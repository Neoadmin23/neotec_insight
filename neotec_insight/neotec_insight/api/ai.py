# Copyright (c) 2026, Neotec Integrated Solution and contributors
"""Neotec AI — additive AI layer over the existing Neotec Insight reports.

This module does NOT change any report logic. It reads the live numbers
(General Ledger + the existing report engine) and sends them as context to an
OpenAI-compatible endpoint (NeoNexus / Ollama / LM Studio / OpenAI), configured
in the "Insight AI Settings" single DocType.

Whitelisted methods:
  - ai_bootstrap()       → AI status + report/company lists for the panel
  - financial_snapshot() → rich, real financial context for a company + period
  - ask_ai()             → answer a question grounded in the live figures
  - list_models()        → discover models from the endpoint
"""
from __future__ import annotations

import json

import frappe
from frappe import _
from frappe.utils import flt, getdate, nowdate

DEFAULT_SYSTEM = (
    "You are Neotec AI, a precise, bilingual (English/Arabic) financial analyst "
    "embedded in the Neotec Insight reporting app for a Saudi/GCC business. You "
    "are given REAL figures (in the company's currency) pulled live from the "
    "General Ledger and the report engine for a specific company and posting-date "
    "period. Ground every statement in those figures and quote numbers exactly. "
    "Be concise and structured: short paragraphs and bullet lists. Surface the "
    "most decision-useful insights — profitability, margins, variance, "
    "concentration, liquidity, anomalies — and flag risks. The JSON may include "
    "monthly_trend (income/expense/net by month), prior_year and yoy deltas, "
    "budget (authored budget per report row), and receivables_payables (AR/AP "
    "outstanding with approximate ageing) — use them when relevant. If asked "
    "something the data does not cover, say so rather than inventing. When the "
    "user writes in Arabic, answer fully in Arabic."
)


def _settings():
    return frappe.get_single("Insight AI Settings")


def _default_company():
    return (
        frappe.defaults.get_user_default("Company")
        or frappe.db.get_single_value("Global Defaults", "default_company")
        or (frappe.get_all("Company", limit=1, pluck="name") or [None])[0]
    )


def _ar_label_field(source_doctype):
    """Configured Arabic-name field for a master DocType, or None."""
    try:
        s = _settings()
        for row in (s.arabic_label_sources or []):
            if row.enabled and row.source_doctype == source_doctype and row.label_field:
                if frappe.db.has_column(source_doctype, row.label_field):
                    return row.label_field
    except Exception:
        pass
    return None



def _require_read() -> None:
    """Refuse a financial read from a user with no ledger access.

    `@frappe.whitelist()` requires a login, not a role — so without this every
    report endpoint was callable over `/api/method/...` by any authenticated
    user, including Website and portal users who have no business seeing the
    ledger. Reading GL Entry is the right test: ERPNext already restricts it
    to the accounts roles, so this inherits whatever the site has configured
    rather than inventing a second permission model.
    """
    if not frappe.has_permission("GL Entry", "read"):
        frappe.throw(
            _("You are not permitted to view financial reports."),
            frappe.PermissionError,
        )


@frappe.whitelist()
def arabic_labels(source_doctype=None, names=None):
    """Return {record_name: arabic_label} for the given master records.

    Uses the field configured for `source_doctype` in Insight AI Settings →
    Arabic Label Sources. Falls back to the record name when no field is
    configured or the value is empty. The selectable value never changes;
    this only supplies display labels.
    """
    if isinstance(names, str):
        try:
            names = json.loads(names)
        except Exception:
            names = [names]
    names = [n for n in (names or []) if n]
    if not source_doctype or not names:
        return {}
    field = _ar_label_field(source_doctype)
    out = {}
    if field:
        for r in frappe.get_all(source_doctype, filters={"name": ["in", names]},
                                fields=["name", field]):
            val = r.get(field)
            if val:
                out[r["name"]] = val
    # User-saved translation overrides take precedence over the source field.
    for o in frappe.get_all("Insight Translation Override",
                            filters={"source_doctype": source_doctype,
                                     "source_name": ["in", names]},
                            fields=["source_name", "arabic"]):
        if o.get("arabic"):
            out[o["source_name"]] = o["arabic"]
    for n in names:
        out.setdefault(n, n)
    return out


@frappe.whitelist()
def save_translation_override(source_doctype=None, source_name=None, arabic=None, english=None):
    """Create or update a user Arabic-translation override for a master record.
    An empty `arabic` reverts to the source/our label (deletes the override)."""
    source_doctype = (source_doctype or "").strip()
    source_name = (source_name or "").strip()
    arabic = (arabic or "").strip()
    if not source_doctype or not source_name:
        frappe.throw("source_doctype and source_name are required.")
    if not frappe.has_permission("Insight Translation Override", "write"):
        frappe.throw("You are not permitted to edit translations.", frappe.PermissionError)
    if not arabic:
        return delete_translation_override(source_doctype, source_name)
    existing = frappe.db.get_value(
        "Insight Translation Override",
        {"source_doctype": source_doctype, "source_name": source_name}, "name")
    if existing:
        doc = frappe.get_doc("Insight Translation Override", existing)
        doc.arabic = arabic
        if english:
            doc.english = english
        doc.save()
    else:
        frappe.get_doc({
            "doctype": "Insight Translation Override",
            "source_doctype": source_doctype, "source_name": source_name,
            "arabic": arabic, "english": english or "",
        }).insert()
    frappe.db.commit()
    return {"source_name": source_name, "arabic": arabic, "saved": True}


@frappe.whitelist()
def delete_translation_override(source_doctype=None, source_name=None):
    """Remove a translation override, reverting to the source/our label."""
    source_doctype = (source_doctype or "").strip()
    source_name = (source_name or "").strip()
    name = frappe.db.get_value(
        "Insight Translation Override",
        {"source_doctype": source_doctype, "source_name": source_name}, "name")
    if name:
        if not frappe.has_permission("Insight Translation Override", "delete"):
            frappe.throw("You are not permitted to edit translations.", frappe.PermissionError)
        frappe.delete_doc("Insight Translation Override", name)
        frappe.db.commit()
    return {"source_name": source_name, "arabic": "", "deleted": bool(name)}


# Map the dimension "kind" used by the UI to its ERPNext master DocType.
_DIM_DOCTYPE = {
    "company": "Company", "cost_center": "Cost Center", "project": "Project",
    "department": "Department", "branch": "Branch", "employee": "Employee",
    "supplier": "Supplier", "customer": "Customer",
}


@frappe.whitelist()
def dimension_options(kind=None, company=None):
    """Options for a dimension/company selector, each with an Arabic label.

    Returns [{name, label, label_ar}] where label_ar comes from the configured
    Arabic field on that master (empty string if none). The front-end shows
    label_ar when Arabic is active, but always submits `name` for filtering.
    """
    _require_read()
    dt = _DIM_DOCTYPE.get(kind)
    if not dt or not frappe.db.exists("DocType", dt):
        return []
    filters = {}
    if company and frappe.db.has_column(dt, "company"):
        filters["company"] = company
    if frappe.db.has_column(dt, "is_group"):
        filters["is_group"] = 0
    if frappe.db.has_column(dt, "disabled"):
        filters["disabled"] = 0
    field = _ar_label_field(dt)
    name_label = ("cost_center_name" if dt == "Cost Center"
                  else "project_name" if dt == "Project"
                  else "department_name" if dt == "Department"
                  else "employee_name" if dt == "Employee"
                  else "company_name" if dt == "Company"
                  else "supplier_name" if dt == "Supplier"
                  else "customer_name" if dt == "Customer"
                  else None)
    fields = ["name"]
    if name_label and frappe.db.has_column(dt, name_label):
        fields.append(name_label)
    if field and field not in fields:
        fields.append(field)
    rows = frappe.get_all(dt, filters=filters, fields=fields, order_by="name asc", limit=500)
    out = []
    for r in rows:
        out.append({
            "name": r["name"],
            "label": (r.get(name_label) if name_label else None) or r["name"],
            "label_ar": (r.get(field) if field else "") or "",
        })
    return out


@frappe.whitelist()
def ai_bootstrap():
    """Status + lists the Ask-Neotec-AI panel initialises with."""
    _require_read()
    s = _settings()
    reports = frappe.get_all(
        "Insight Report Definition",
        filters={"is_active": 1},
        fields=["name", "report_name", "slug", "report_type", "is_default", "company"],
        order_by="is_default desc, report_name asc",
    )
    companies = frappe.get_all("Company", pluck="name", order_by="name asc")
    return {
        "enabled": bool(s.ai_enabled),
        "model": s.ai_model or "llama3",
        "endpoint_set": bool(s.ai_endpoint),
        "default_language": s.default_language or "en",
        "reports": reports,
        "companies": companies,
        "default_company": _default_company(),
    }


def _dim_sql(dims, params):
    """Build an AND fragment for cost_center/project/department/branch filters.
    Mutates `params` with the bound values; returns the SQL string."""
    frag = ""
    for col in ("cost_center", "project", "department", "branch"):
        val = (dims or {}).get(col)
        if val:
            frag += f" AND gle.{col} = %({col})s"
            params[col] = val
    return frag


def _gl_by_root(company, start, end, as_of=False, dims=None):
    """Net (debit-credit) grouped by account root_type.
    as_of=True → cumulative up to `end` (balance items); else period start..end."""
    cond = "gle.is_cancelled = 0 AND gle.company = %(company)s AND gle.posting_date <= %(end)s"
    params = {"company": company, "end": end}
    if not as_of:
        cond += " AND gle.posting_date >= %(start)s"
        params["start"] = start
    cond += _dim_sql(dims, params)
    rows = frappe.db.sql(
        f"""SELECT a.root_type AS root_type, SUM(gle.debit - gle.credit) AS net
            FROM `tabGL Entry` gle JOIN `tabAccount` a ON a.name = gle.account
            WHERE {cond} GROUP BY a.root_type""",
        params, as_dict=True,
    )
    return {(r.root_type or "Unknown"): flt(r.net) for r in rows}


def _monthly_trend(company, start, end, dims=None):
    """Income / expense / net profit by calendar month across the period."""
    params = {"company": company, "s": start, "e": end}
    dim = _dim_sql(dims, params)
    rows = frappe.db.sql(
        f"""SELECT DATE_FORMAT(gle.posting_date, '%%Y-%%m') AS ym, a.root_type AS rt,
                  SUM(gle.debit - gle.credit) AS net
           FROM `tabGL Entry` gle JOIN `tabAccount` a ON a.name = gle.account
           WHERE gle.is_cancelled = 0 AND gle.company = %(company)s
             AND gle.posting_date BETWEEN %(s)s AND %(e)s
             AND a.root_type IN ('Income','Expense'){dim}
           GROUP BY ym, rt ORDER BY ym""",
        params, as_dict=True,
    )
    months = {}
    for r in rows:
        m = months.setdefault(r.ym, {"month": r.ym, "income": 0.0, "expense": 0.0})
        if r.rt == "Income":
            m["income"] += -flt(r.net)
        else:
            m["expense"] += flt(r.net)
    out = list(months.values())
    for m in out:
        m["net_profit"] = m["income"] - m["expense"]
    return out


def _quarterly(monthly):
    """Roll a monthly_trend list up into calendar quarters."""
    q = {}
    for m in monthly or []:
        try:
            y, mo = m["month"].split("-")
            key = f"{y}-Q{(int(mo) - 1) // 3 + 1}"
        except Exception:
            continue
        b = q.setdefault(key, {"quarter": key, "income": 0.0, "expense": 0.0})
        b["income"] += m["income"]
        b["expense"] += m["expense"]
    out = sorted(q.values(), key=lambda x: x["quarter"])
    for b in out:
        b["net_profit"] = b["income"] - b["expense"]
    return out


def _prior_year(company, start, end, dims=None):
    """Same window shifted back one year — period P&L + balances as-of."""
    from frappe.utils import add_to_date
    ps, pe = add_to_date(getdate(start), years=-1), add_to_date(getdate(end), years=-1)
    per = _gl_by_root(company, ps, pe, as_of=False, dims=dims)
    asof = _gl_by_root(company, ps, pe, as_of=True, dims=dims)
    inc = -flt(per.get("Income", 0.0))
    exp = flt(per.get("Expense", 0.0))
    return {
        "period": {"from": str(ps), "to": str(pe)},
        "total_income": inc, "total_expense": exp, "net_profit": inc - exp,
        "total_assets": flt(asof.get("Asset", 0.0)),
        "total_liabilities": -flt(asof.get("Liability", 0.0)),
        "total_equity": -flt(asof.get("Equity", 0.0)),
    }


def _budget_by_line(report_name, fiscal_year):
    """User-authored budget for a report + fiscal year, summed per row."""
    if not frappe.db.table_exists("Insight Budget Cell"):
        return None
    rows = frappe.db.sql(
        """SELECT row_key, SUM(amount) AS budget
           FROM `tabInsight Budget Cell`
           WHERE report = %(r)s AND fiscal_year = %(fy)s
           GROUP BY row_key ORDER BY ABS(SUM(amount)) DESC LIMIT 25""",
        {"r": report_name, "fy": fiscal_year}, as_dict=True,
    )
    if not rows:
        return None
    return {
        "fiscal_year": fiscal_year,
        "total_budget": sum(flt(r.budget) for r in rows),
        "by_row": [{"row_key": r.row_key, "budget": flt(r.budget)} for r in rows],
    }


def _invoice_ageing(company, as_of):
    """Due-date-accurate AR/AP ageing from open invoices (the standard ERPNext way)."""
    def _age(doctype):
        if not frappe.db.exists("DocType", doctype):
            return None
        rows = frappe.db.sql(
            f"""SELECT
                    CASE
                      WHEN DATEDIFF(%(e)s, COALESCE(due_date, posting_date)) <= 0 THEN 'not_due'
                      WHEN DATEDIFF(%(e)s, COALESCE(due_date, posting_date)) <= 30 THEN '0-30'
                      WHEN DATEDIFF(%(e)s, COALESCE(due_date, posting_date)) <= 60 THEN '31-60'
                      WHEN DATEDIFF(%(e)s, COALESCE(due_date, posting_date)) <= 90 THEN '61-90'
                      ELSE '90+' END AS bucket,
                    SUM(outstanding_amount) AS amt
                FROM `tab{doctype}`
                WHERE docstatus = 1 AND company = %(company)s
                  AND posting_date <= %(e)s AND outstanding_amount > 0
                GROUP BY bucket""",
            {"company": company, "e": as_of}, as_dict=True,
        )
        buckets = {"not_due": 0.0, "0-30": 0.0, "31-60": 0.0, "61-90": 0.0, "90+": 0.0}
        for r in rows:
            buckets[r.bucket] = buckets.get(r.bucket, 0.0) + flt(r.amt)
        return {"total": sum(buckets.values()), "ageing_by_due_date": buckets}

    ar = _age("Sales Invoice")
    ap = _age("Purchase Invoice")
    if ar is None and ap is None:
        return None
    return {
        "receivables": ar or {"total": 0.0, "ageing_by_due_date": {}},
        "payables": ap or {"total": 0.0, "ageing_by_due_date": {}},
        "basis": "invoice due date (open invoices, docstatus=1)",
    }


def _receivables_payables(company, end):
    """Prefer due-date invoice ageing; fall back to a GL posting-date approximation."""
    inv = None
    try:
        inv = _invoice_ageing(company, end)
    except Exception:
        frappe.log_error(frappe.get_traceback(), "Neotec AI · invoice ageing")
    if inv:
        return inv

    def _bucketed(acc_type, sign):
        rows = frappe.db.sql(
            """SELECT
                   CASE
                     WHEN DATEDIFF(%(e)s, gle.posting_date) <= 30 THEN '0-30'
                     WHEN DATEDIFF(%(e)s, gle.posting_date) <= 60 THEN '31-60'
                     WHEN DATEDIFF(%(e)s, gle.posting_date) <= 90 THEN '61-90'
                     ELSE '90+' END AS bucket,
                   SUM(gle.debit - gle.credit) AS net
               FROM `tabGL Entry` gle JOIN `tabAccount` a ON a.name = gle.account
               WHERE gle.is_cancelled = 0 AND gle.company = %(company)s
                 AND gle.posting_date <= %(e)s AND a.account_type = %(at)s
               GROUP BY bucket""",
            {"company": company, "e": end, "at": acc_type}, as_dict=True,
        )
        buckets = {"0-30": 0.0, "31-60": 0.0, "61-90": 0.0, "90+": 0.0}
        for r in rows:
            buckets[r.bucket] = buckets.get(r.bucket, 0.0) + sign * flt(r.net)
        return {"total": sum(buckets.values()), "ageing_by_posting_date": buckets}

    return {
        "receivables": _bucketed("Receivable", 1),
        "payables": _bucketed("Payable", -1),
        "basis": "GL posting date (approximate — invoices unavailable)",
    }


@frappe.whitelist()
def financial_snapshot(company=None, from_date=None, to_date=None, report=None,
                       cost_center=None, project=None, department=None, branch=None):
    """A compact but rich, REAL financial picture for company + period.
    Optional dimension filters scope every GL-based figure."""
    _require_read()
    company = company or _default_company()
    to_date = to_date or nowdate()
    from_date = from_date or f"{getdate(to_date).year}-01-01"
    if not company:
        return {"error": "No company found."}

    dims = {k: v for k, v in {
        "cost_center": cost_center, "project": project,
        "department": department, "branch": branch,
    }.items() if v}

    period = _gl_by_root(company, from_date, to_date, as_of=False, dims=dims)
    asof = _gl_by_root(company, from_date, to_date, as_of=True, dims=dims)

    income = -flt(period.get("Income", 0.0))          # credit-natured
    expense = flt(period.get("Expense", 0.0))           # debit-natured
    net_profit = income - expense
    assets = flt(asof.get("Asset", 0.0))
    liabilities = -flt(asof.get("Liability", 0.0))
    equity = -flt(asof.get("Equity", 0.0))

    # Top movers in the P&L period
    _p = {"company": company, "s": from_date, "e": to_date}
    _d = _dim_sql(dims, _p)
    top = frappe.db.sql(
        f"""SELECT a.account_name AS account, a.root_type AS root_type,
                  SUM(gle.debit - gle.credit) AS net
           FROM `tabGL Entry` gle JOIN `tabAccount` a ON a.name = gle.account
           WHERE gle.is_cancelled = 0 AND gle.company = %(company)s
             AND gle.posting_date BETWEEN %(s)s AND %(e)s
             AND a.root_type IN ('Income','Expense'){_d}
           GROUP BY gle.account ORDER BY ABS(SUM(gle.debit - gle.credit)) DESC LIMIT 12""",
        _p, as_dict=True,
    )
    income_lines = [{"account": r.account, "amount": -flt(r.net)} for r in top if r.root_type == "Income"][:6]
    expense_lines = [{"account": r.account, "amount": flt(r.net)} for r in top if r.root_type == "Expense"][:6]

    # By cost center (P&L net for the period)
    _p2 = {"company": company, "s": from_date, "e": to_date}
    _d2 = _dim_sql(dims, _p2)
    by_cc = frappe.db.sql(
        f"""SELECT COALESCE(NULLIF(gle.cost_center,''),'(none)') AS cost_center,
                  SUM(gle.debit - gle.credit) AS net
           FROM `tabGL Entry` gle JOIN `tabAccount` a ON a.name = gle.account
           WHERE gle.is_cancelled = 0 AND gle.company = %(company)s
             AND gle.posting_date BETWEEN %(s)s AND %(e)s
             AND a.root_type IN ('Income','Expense'){_d2}
           GROUP BY gle.cost_center ORDER BY ABS(SUM(gle.debit - gle.credit)) DESC LIMIT 8""",
        _p2, as_dict=True,
    )
    cost_centers = [{"cost_center": r.cost_center, "pnl_net": -flt(r.net)} for r in by_cc]

    snap = {
        "company": company,
        "filters": dims or None,
        "period": {"from": str(from_date), "to": str(to_date), "basis": "posting_date"},
        "currency": frappe.db.get_value("Company", company, "default_currency"),
        "pnl": {
            "total_income": income,
            "total_expense": expense,
            "net_profit": net_profit,
            "net_margin_pct": round((net_profit / income * 100), 1) if income else None,
            "top_income_accounts": income_lines,
            "top_expense_accounts": expense_lines,
        },
        "balance_as_of": {
            "as_of": str(to_date),
            "total_assets": assets,
            "total_liabilities": liabilities,
            "total_equity": equity,
            "current_ratio_hint": None,
        },
        "by_cost_center_pnl": cost_centers,
    }

    # Best-effort: attach the SELECTED report's own engine output (exact lines).
    if report:
        snap["report"] = _try_report_engine(report, company, from_date, to_date)

    # ── widened context (each block guarded so one failure can't break the rest) ──
    try:
        trend = _monthly_trend(company, from_date, to_date, dims=dims)
        snap["monthly_trend"] = trend
        snap["quarterly_trend"] = _quarterly(trend)
    except Exception:
        frappe.log_error(frappe.get_traceback(), "Neotec AI · trend")
    try:
        py = _prior_year(company, from_date, to_date, dims=dims)
        snap["prior_year"] = py
        # convenience deltas the model can quote directly
        snap["yoy"] = {
            "income_delta": snap["pnl"]["total_income"] - py["total_income"],
            "expense_delta": snap["pnl"]["total_expense"] - py["total_expense"],
            "net_profit_delta": snap["pnl"]["net_profit"] - py["net_profit"],
        }
    except Exception:
        frappe.log_error(frappe.get_traceback(), "Neotec AI · prior_year")
    try:
        if report:
            name = frappe.db.get_value("Insight Report Definition", {"slug": report}) or report
            fy = frappe.db.get_value("Insight Report Definition", name, "fiscal_year") \
                if frappe.db.has_column("Insight Report Definition", "fiscal_year") else None
            b = _budget_by_line(name, fy or getdate(to_date).year)
            if b:
                snap["budget"] = b
    except Exception:
        frappe.log_error(frappe.get_traceback(), "Neotec AI · budget")
    try:
        snap["receivables_payables"] = _receivables_payables(company, to_date)
    except Exception:
        frappe.log_error(frappe.get_traceback(), "Neotec AI · ar_ap")

    return snap


def _try_report_engine(report, company, from_date, to_date):
    """Call the existing engine for the selected report — never raises."""
    name = frappe.db.get_value("Insight Report Definition", {"slug": report}) or report
    rtype = frappe.db.get_value("Insight Report Definition", name, "report_type") or "pnl"
    fy = getdate(to_date).year
    import importlib
    rep = importlib.import_module("neotec_insight.neotec_insight.api.report")
    attempts = {
        "trial_balance": [
            lambda: rep.run_trial_balance(report=name, company=company, fiscal_year=fy,
                                          as_of_date=str(to_date), from_date=str(from_date), use_cache=0),
        ],
        "balance_sheet": [
            lambda: rep.run_balance_sheet(report=name, company=company,
                                          as_of_date=str(to_date), use_cache=0),
        ],
        "pnl_statement": [
            lambda: rep.run_pnl_statement(report=name, company=company,
                                          from_date=str(from_date), to_date=str(to_date), use_cache=0),
        ],
        "pnl": [
            lambda: rep.run_report(report=name, company=company, fiscal_year=fy,
                                   month_from=0, month_to=11, use_cache=0),
        ],
    }
    for fn in attempts.get(rtype, attempts["pnl"]):
        try:
            return {"name": name, "type": rtype, "engine": fn()}
        except Exception:
            continue
    return {"name": name, "type": rtype, "engine": None, "note": "engine call skipped"}


def _trim(obj, limit=16000):
    try:
        s = json.dumps(obj, ensure_ascii=False, default=str)
    except Exception:
        s = str(obj)
    return s if len(s) <= limit else s[:limit] + " …(truncated)"


@frappe.whitelist()
def ask_ai(question=None, company=None, from_date=None, to_date=None, report=None, lang="en",
           context="", cost_center=None, project=None, department=None, branch=None):
    """Answer a question grounded in the live financial figures."""
    _require_read()
    s = _settings()
    if not s.ai_enabled or not s.ai_endpoint:
        return {"text": _("Neotec AI is not configured. Enable it and set a reachable AI Endpoint in "
                          "Insight AI Settings."), "ok": False}
    if not question:
        return {"text": _("No question provided."), "ok": False}

    if not context:
        try:
            context = _trim(financial_snapshot(
                company, from_date, to_date, report,
                cost_center=cost_center, project=project,
                department=department, branch=branch,
            ))
        except Exception:
            frappe.log_error(frappe.get_traceback(), "Neotec AI · snapshot")
            context = "{}"

    system_prompt = s.ai_system_prompt or DEFAULT_SYSTEM
    user_msg = (
        f"Company: {company or _default_company()}\n"
        f"Language: {lang}\n\n"
        f"LIVE FINANCIAL DATA (JSON):\n{context}\n\n"
        f"Question: {question}"
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_msg},
    ]

    headers = {"Content-Type": "application/json"}
    api_key = s.get_password("ai_api_key") if s.ai_api_key else None
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    endpoint = s.ai_endpoint.rstrip("/") + "/chat/completions"
    payload = {"model": s.ai_model or "llama3", "messages": messages, "stream": False, "temperature": 0.2}

    import requests
    try:
        resp = requests.post(endpoint, headers=headers, json=payload, timeout=120)
        resp.raise_for_status()
        data = resp.json()
        text = data.get("choices", [{}])[0].get("message", {}).get("content", "") if isinstance(data, dict) else ""
        if not text:
            return {"text": _("The AI endpoint returned an empty response."), "ok": False}
        return {"text": text, "ok": True}
    except requests.exceptions.RequestException as e:
        # endpoint unreachable / DNS / timeout — expected when misconfigured; don't spam Error Log
        frappe.logger("neotec_insight").warning(f"AI endpoint unreachable: {e}")
        return {"text": _("The AI endpoint could not be reached at {0}. Check Insight AI Settings → "
                          "AI Endpoint. Note: 'host.docker.internal' only works when Frappe and the model "
                          "run in Docker on the same machine; on Frappe Cloud use a publicly reachable "
                          "HTTPS URL, or turn AI off.").format(s.ai_endpoint), "ok": False}
    except Exception as e:  # noqa: BLE001
        frappe.log_error(frappe.get_traceback(), "Neotec AI · ask_ai")
        return {"text": _("AI request failed: {0}").format(str(e)[:200]), "ok": False}


@frappe.whitelist()
def list_models():
    s = _settings()
    if not s.ai_endpoint:
        return {"models": []}
    import requests
    headers = {}
    api_key = s.get_password("ai_api_key") if s.ai_api_key else None
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        resp = requests.get(s.ai_endpoint.rstrip("/") + "/models", headers=headers, timeout=8)
        resp.raise_for_status()
        data = resp.json()
        items = data.get("data", data) if isinstance(data, dict) else data
        models = [m.get("id") for m in items if isinstance(m, dict) and m.get("id")]
        return {"models": models}
    except Exception:
        return {"models": []}
