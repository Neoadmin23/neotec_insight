"""External data sources for Insight Copilot (v2.95.0).

Who may use a source: System Managers, plus the roles listed on the source.
What a question becomes: one SELECT written by the model from the source's
table structure, checked by utils/sql_guard (one read-only statement, allowed
tables only), capped, run in a read-only session, and shown to the user
alongside the answer.
"""
from __future__ import annotations

import json

import frappe
from frappe import _
from frappe.utils import now_datetime

from neotec_insight.neotec_insight.utils import copilot_core as core
from neotec_insight.neotec_insight.utils import external_sources as ext
from neotec_insight.neotec_insight.utils import sql_guard

DS = "Insight Data Source"
SR = "Insight Source Report"


def _can_use(src) -> bool:
    user = frappe.session.user
    roles = set(frappe.get_roles(user))
    if user == "Administrator" or "System Manager" in roles:
        return True
    allowed = {r.strip() for r in (src.allowed_roles or "").split(",") if r.strip()}
    return bool(allowed & roles)


def get_source(name: str):
    if not name or not frappe.db.exists(DS, name):
        frappe.throw(_("Data source {0} not found.").format(name or "?"))
    src = frappe.get_doc(DS, name)
    if not src.enabled:
        frappe.throw(_("Data source {0} is disabled.").format(name))
    if not _can_use(src):
        frappe.throw(_("You don't have access to data source {0}.").format(name), frappe.PermissionError)
    return src


def _password(src) -> str:
    try:
        return src.get_password("password", raise_exception=False) or ""
    except Exception:
        return ""


@frappe.whitelist()
def list_sources():
    """Sources the current user may ask about."""
    out = []
    for r in frappe.get_all(DS, filters={"enabled": 1}, fields=["name", "db_type", "allowed_roles"],
                            order_by="name asc", limit_page_length=0):
        if _can_use(frappe._dict(r)):
            out.append({"name": r["name"], "db_type": r["db_type"]})
    return out


@frappe.whitelist(methods=["POST"])
def test_source(name):
    """Connect, read the table structure, store it (System Manager)."""
    frappe.only_for("System Manager")
    src = frappe.get_doc(DS, name)
    try:
        schema = ext.introspect(src, _password(src))
    except Exception as e:
        msg = _("Could not connect: {0}").format(str(e)[:300])
        frappe.db.set_value(DS, name, "status", msg)
        frappe.db.commit()
        return {"ok": False, "message": msg}
    cols = sum(len(c) for c in schema.values())
    msg = _("Connected. {0} tables, {1} columns readable.").format(len(schema), cols)
    if not src.allowed_tables:
        msg += " " + _("No table allowlist is set: every table this user can see is open to questions.")
    frappe.db.set_value(DS, name, {"schema_json": json.dumps(schema), "schema_read_on": now_datetime(),
                                   "status": msg})
    frappe.db.commit()
    return {"ok": True, "message": msg, "tables": len(schema), "columns": cols}


SQL_SYSTEM = (
    "You write ONE read-only SQL query for a business question. Reply with ONLY JSON: "
    '{"sql": "...", "view": "table|bar|line|pie", "title": "short title"}. Rules: a single SELECT '
    "(WITH is fine); only the tables and columns listed; join tables with JOIN … ON, never commas; no "
    "comments; no INSERT/UPDATE/DELETE/DDL or server functions; aggregate (SUM/COUNT/AVG with GROUP BY) "
    "when the question asks for totals, rankings or trends; put a text/label column first and numeric "
    "columns after it; order rankings descending. If the tables cannot answer the question, reply "
    '{"sql": null}.')


def _model_sql(chat, question: str, d: str, schema: dict, feedback: str = "") -> dict:
    dialect_name = {"mysql": "MySQL/MariaDB", "postgres": "PostgreSQL", "mssql": "SQL Server (T-SQL)"}[d]
    user = (f"Dialect: {dialect_name}\nTables:\n{ext.schema_text(schema)}\n\nQuestion: {question}\n"
            + (f"\nYour previous query was refused: {feedback}. Write a corrected query.\n" if feedback else "")
            + "JSON only.")
    from neotec_insight.neotec_insight.api.copilot import _json
    return _json(chat(SQL_SYSTEM, user)) or {}


def ask_source(question: str, source: str, chat) -> dict:
    """Plan → check → run for an external source. `chat` is the model call."""
    src = get_source(source)
    schema = ext.load_schema(src)
    if not schema:
        schema = ext.introspect(src, _password(src))
        frappe.db.set_value(DS, src.name, {"schema_json": json.dumps(schema), "schema_read_on": now_datetime()})
    if not schema:
        frappe.throw(_("No readable tables in {0}. Check the allowlist and the database user.").format(src.name))
    d = ext.dialect(src.db_type)
    plan = _model_sql(chat, question, d, schema)
    if not plan.get("sql"):
        return {"ok": False, "answer": _("Those tables don't seem to hold what the question asks about."),
                "suggestions": []}
    feedback, result = "", None
    for attempt in range(2):
        try:
            result = ext.run(src, _password(src), plan["sql"])
            break
        except sql_guard.SqlRejected as e:
            feedback = str(e)
            if attempt == 0:
                plan = _model_sql(chat, question, d, schema, feedback) or {}
                if not plan.get("sql"):
                    break
    if result is None:
        return {"ok": False, "answer": _("I couldn't write a safe query for that ({0}). Try rephrasing, or ask "
                                         "about the tables directly.").format(feedback or _("no query")),
                "suggestions": []}
    view = plan.get("view") if plan.get("view") in ("table", "bar", "line", "pie") else "table"
    title = str(plan.get("title") or src.name)[:120]
    return {"ok": True, "title": title, "view": view, "columns": result["columns"], "rows": result["rows"],
            "sql": result["sql"], "chart": core.chart_for(view, result["columns"], result["rows"]),
            "source_name": src.name}


@frappe.whitelist(methods=["POST"])
def save_source_report(title=None, source=None, sql=None, question=None, view="table"):
    src = get_source(source)
    clean = sql_guard.check(sql, sql_guard.parse_allowlist(src.allowed_tables))
    title = (title or "").strip()
    if not title:
        frappe.throw(_("Give the report a title."))
    doc = frappe.get_doc(SR, title) if frappe.db.exists(SR, title) else frappe.new_doc(SR)
    doc.update({"title": title, "source": src.name, "sql": clean, "question": (question or "")[:500],
                "view": view if view in ("table", "bar", "line", "pie") else "table"})
    doc.save(ignore_permissions=True)
    frappe.db.commit()
    return {"name": doc.name}


@frappe.whitelist()
def list_source_reports():
    out = []
    for r in frappe.get_all(SR, fields=["name", "source", "view", "question"], order_by="modified desc",
                            limit_page_length=100):
        try:
            if frappe.db.get_value(DS, r["source"], "enabled") and _can_use(frappe.get_doc(DS, r["source"])):
                out.append(r)
        except Exception:
            continue
    return out


@frappe.whitelist()
def run_source_report(name=None):
    rep = frappe.get_doc(SR, name)
    src = get_source(rep.source)
    result = ext.run(src, _password(src), rep.sql)          # re-checked on every run
    return {"ok": True, "title": rep.name, "view": rep.view, "columns": result["columns"],
            "rows": result["rows"], "sql": result["sql"],
            "chart": core.chart_for(rep.view, result["columns"], result["rows"]),
            "answer": core.plain_summary("source", rep.name, result["columns"], result["rows"]),
            "suggestions": [], "source_name": src.name}
