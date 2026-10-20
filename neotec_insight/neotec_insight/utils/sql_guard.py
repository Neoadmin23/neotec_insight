"""Checks every query sent to an external database (v2.95.0). Pure.

The real protection is the database account: connect with a user that can
only SELECT the tables you want reported on. This guard is the second line,
so that a confused or manipulated model cannot even *send* anything else:

  * exactly one statement, and it must be a SELECT (or WITH … SELECT);
  * no comments (they can hide a second statement);
  * no write, schema, permission, session, file or delay keywords;
  * every table it reads is on the source's allowlist;
  * the result is capped by wrapping it in an outer SELECT with a row limit.
Anything the guard cannot read with confidence is refused, not guessed at.
"""
from __future__ import annotations

import re

DIALECTS = ("mysql", "postgres", "mssql")

# Only one SELECT/WITH statement can reach the database, so the danger is
# what can act from *inside* a SELECT: data-changing CTEs (WITH x AS (DELETE…)),
# SELECT … INTO, row locks (FOR UPDATE / FOR SHARE), and server functions that
# sleep, read files, call out, or run SQL given as text (query_to_xml, dblink,
# OPENROWSET …). Ordinary words that are only names or harmless functions
# (open, close, start, replace, fetch) are not listed, so normal reporting
# columns are not refused.
FORBIDDEN = re.compile(
    r"\b(insert|update|delete|merge|upsert|into|drop|alter|create|truncate|grant|revoke|call|exec|"
    r"execute|copy|outfile|dumpfile|load_file|load\s+data|lock|share\s+mode|reconfigure|shutdown|kill|"
    r"sleep|benchmark|pg_sleep\w*|waitfor|xp_\w+|sp_\w+|openrowset|opendatasource|openquery|openxml|bulk|"
    r"dblink\w*|lo_\w+|pg_read_\w+|pg_stat_file|pg_ls_dir|pg_terminate_backend|pg_cancel_backend|"
    r"set_config|query_to_xml\w*|cursor_to_xml|table_to_xml\w*|sys_exec|sys_eval|nextval|setval)\b",
    re.I)

_STRING = re.compile(r"'(?:[^'\\]|\\.|'')*'")
_QUOTED_ID = re.compile(r'"([^"]+)"|`([^`]+)`|\[([^\]]+)\]')
_TABLE_REF = re.compile(r"\b(?:from|join)\s+((?:[\w$]+\.)?[\w$]+)", re.I)
# FROM a, b — the second table would escape the FROM/JOIN scan; refused.
_COMMA_JOIN = re.compile(r"\bfrom\s+[\w$.]+(?:\s+(?:as\s+)?(?!where\b|group\b|order\b|join\b|inner\b|left\b|"
                         r"right\b|full\b|cross\b|on\b|limit\b|union\b|having\b)[\w$]+)?\s*,", re.I)
_CTE_NAME = re.compile(r"(?:\bwith\b|,)\s*([\w$]+)\s+as\s*\(", re.I)


class SqlRejected(ValueError):
    pass


def _strip_strings(sql: str) -> str:
    return _STRING.sub("''", sql)


def _unquote_identifiers(sql: str) -> str:
    return _QUOTED_ID.sub(lambda m: next(g for g in m.groups() if g is not None), sql)


def tables_read(sql: str) -> set[str]:
    """Lower-cased table names after FROM/JOIN, minus CTE names."""
    s = _unquote_identifiers(_strip_strings(sql))
    ctes = {c.lower() for c in _CTE_NAME.findall(s)}
    out = set()
    for t in _TABLE_REF.findall(s):
        name = t.lower()
        if name in ctes or name.split(".")[-1] in ctes:
            continue
        out.add(name)
    return out


def check(sql: str, allowed_tables: set[str] | None) -> str:
    """Return the cleaned statement or raise SqlRejected."""
    if not sql or not str(sql).strip():
        raise SqlRejected("empty query")
    s = str(sql).strip()
    if len(s) > 8000:
        raise SqlRejected("query too long")
    if "--" in _strip_strings(s) or "/*" in s or "#" in _strip_strings(s):
        raise SqlRejected("comments are not allowed")
    s = s.rstrip().rstrip(";").rstrip()
    body = _strip_strings(s)
    if ";" in body:
        raise SqlRejected("only one statement is allowed")
    first = re.match(r"\s*(\w+)", body)
    if not first or first.group(1).lower() not in ("select", "with"):
        raise SqlRejected("only SELECT queries are allowed")
    m = FORBIDDEN.search(body)
    if m:
        raise SqlRejected(f"'{m.group(0)}' is not allowed")
    if _COMMA_JOIN.search(_unquote_identifiers(body)):
        raise SqlRejected("list tables with JOIN, not commas")
    tables = tables_read(s)
    if not tables:
        raise SqlRejected("could not tell which tables the query reads")
    if allowed_tables is not None:
        allow = {a.lower() for a in allowed_tables}
        bare = {a.split(".")[-1] for a in allow}
        bad = sorted(t for t in tables if t not in allow and t.split(".")[-1] not in bare)
        if bad:
            raise SqlRejected(f"table(s) not allowed for this source: {', '.join(bad)}")
    return s


def with_limit(sql: str, dialect: str, limit: int) -> str:
    """Cap rows without trusting the inner query's own LIMIT/TOP."""
    limit = max(1, min(int(limit), 10000))
    if dialect == "mssql":
        return f"SELECT TOP {limit} * FROM ({sql}) AS insight_q"
    return f"SELECT * FROM ({sql}) AS insight_q LIMIT {limit}"


def parse_allowlist(text: str | None) -> set[str] | None:
    """'a, b\\nschema.c' -> {'a','b','schema.c'}; blank -> None (every table the user can read)."""
    items = {x.strip().lower() for x in re.split(r"[,\n]", text or "") if x.strip()}
    return items or None
