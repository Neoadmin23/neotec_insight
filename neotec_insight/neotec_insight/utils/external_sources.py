"""Read-only access to outside databases for Insight Copilot (v2.95.0).

Three layers, outermost first:
  1. the database account (documented on the Data Source form: SELECT only);
  2. the session: read-only transaction mode and a statement timeout;
  3. every statement: utils/sql_guard.check (one SELECT, allowed tables only)
     and an outer row limit.
MariaDB/MySQL use pymysql and PostgreSQL psycopg2 — both already installed
with Frappe. SQL Server needs pymssql; it is loaded only if present.
"""
from __future__ import annotations

import datetime
import decimal
import json

from neotec_insight.neotec_insight.utils import sql_guard

DEFAULT_PORT = {"mysql": 3306, "postgres": 5432, "mssql": 1433}
MAX_TABLES, MAX_COLS = 300, 4000


class SourceError(RuntimeError):
    pass


def dialect(db_type: str) -> str:
    t = (db_type or "").lower()
    if "postgres" in t:
        return "postgres"
    if "sql server" in t or "mssql" in t:
        return "mssql"
    return "mysql"


def _connect(src, password: str):
    d = dialect(src.db_type)
    port = int(src.port or DEFAULT_PORT[d])
    timeout = max(3, min(int(src.timeout_seconds or 20), 120))
    if d == "mysql":
        import pymysql
        conn = pymysql.connect(host=src.host, port=port, user=src.username, password=password or "",
                               database=src.database, connect_timeout=10, read_timeout=timeout,
                               write_timeout=timeout, charset="utf8mb4",
                               ssl={"ssl": {}} if src.use_ssl else None,
                               cursorclass=pymysql.cursors.Cursor, autocommit=False)
        cur = conn.cursor()
        for stmt in ("SET SESSION TRANSACTION READ ONLY",
                     f"SET SESSION MAX_EXECUTION_TIME={timeout * 1000}",      # MySQL
                     f"SET SESSION max_statement_time={timeout}"):           # MariaDB
            try:
                cur.execute(stmt)
            except Exception:
                pass
        return conn
    if d == "postgres":
        import psycopg2
        return psycopg2.connect(host=src.host, port=port, user=src.username, password=password or "",
                                dbname=src.database, connect_timeout=10,
                                sslmode="require" if src.use_ssl else "prefer",
                                options=f"-c statement_timeout={timeout * 1000} -c default_transaction_read_only=on")
    try:
        import pymssql
    except ImportError:
        raise SourceError("SQL Server needs the pymssql package on this server. Ask your host to install it, "
                          "or use a MySQL/PostgreSQL replica.") from None
    return pymssql.connect(server=src.host, port=str(port), user=src.username, password=password or "",
                           database=src.database, login_timeout=10, timeout=timeout)


def _plain(v):
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, (datetime.date, datetime.datetime, datetime.time)):
        return v.isoformat()
    if isinstance(v, (bytes, bytearray, memoryview)):
        return "(binary)"
    return v


def _col_type(values) -> str:
    seen = [v for v in values if v is not None]
    if seen and all(isinstance(v, (int, float, decimal.Decimal)) and not isinstance(v, bool) for v in seen):
        return "currency" if any(isinstance(v, (float, decimal.Decimal)) for v in seen) else "number"
    return "text"


def run(src, password: str, sql: str, connect=None) -> dict:
    """Check, cap and run one query. Returns {columns, rows, sql}."""
    allow = sql_guard.parse_allowlist(src.allowed_tables)
    clean = sql_guard.check(sql, allow)
    d = dialect(src.db_type)
    final = sql_guard.with_limit(clean, d, int(src.row_limit or 500))
    conn = (connect or _connect)(src, password)
    try:
        cur = conn.cursor()
        cur.execute(final)
        names = [c[0] for c in (cur.description or [])]
        raw = cur.fetchall() or []
    finally:
        try:
            conn.rollback()        # nothing to keep, ever
        except Exception:
            pass
        conn.close()
    cols = [{"key": n, "label": n, "type": _col_type([r[i] for r in raw])} for i, n in enumerate(names)]
    rows = [{n: _plain(r[i]) for i, n in enumerate(names)} for r in raw]
    return {"columns": cols, "rows": rows, "sql": clean}


_SCHEMA_SQL = {
    "mysql": ("SELECT table_name, column_name, data_type FROM information_schema.columns "
              "WHERE table_schema = DATABASE() ORDER BY table_name, ordinal_position"),
    "postgres": ("SELECT CASE WHEN table_schema = 'public' THEN table_name ELSE table_schema || '.' || table_name END, "
                 "column_name, data_type FROM information_schema.columns "
                 "WHERE table_schema NOT IN ('pg_catalog', 'information_schema') "
                 "ORDER BY table_schema, table_name, ordinal_position"),
    "mssql": ("SELECT CASE WHEN table_schema = 'dbo' THEN table_name ELSE table_schema + '.' + table_name END, "
              "column_name, data_type FROM information_schema.columns ORDER BY table_schema, table_name, ordinal_position"),
}


def introspect(src, password: str, connect=None) -> dict:
    """{table: [[column, type], …]} for the tables the source may read."""
    allow = sql_guard.parse_allowlist(src.allowed_tables)
    bare = {a.split(".")[-1] for a in allow} if allow else None
    conn = (connect or _connect)(src, password)
    try:
        cur = conn.cursor()
        cur.execute(_SCHEMA_SQL[dialect(src.db_type)])
        rows = cur.fetchall() or []
    finally:
        try:
            conn.rollback()
        except Exception:
            pass
        conn.close()
    out: dict[str, list] = {}
    ncols = 0
    for table, col, typ in rows:
        t = str(table)
        if allow is not None and t.lower() not in allow and t.lower().split(".")[-1] not in bare:
            continue
        if t not in out and len(out) >= MAX_TABLES:
            continue
        if ncols >= MAX_COLS:
            break
        out.setdefault(t, []).append([str(col), str(typ)])
        ncols += 1
    return out


def schema_text(schema: dict, limit_chars: int = 12000) -> str:
    """Compact schema for the model: one line per table."""
    lines = [f"{t}({', '.join(f'{c} {ty}' for c, ty in cols)})" for t, cols in schema.items()]
    text = "\n".join(lines)
    return text[:limit_chars]


def load_schema(src) -> dict:
    try:
        return json.loads(src.schema_json or "{}") or {}
    except Exception:
        return {}
