# Copyright (c) 2026, Neotec and contributors
# -----------------------------------------------------------------------------
# CFO "brain" — Stage 1: the morning brief.
#
# Computes the facts a CFO checks first (cash, runway, receivables, payables,
# VAT, revenue trend, margin, unreconciled bank items), then ranks what needs
# attention into alerts + actions by materiality. Deterministic and auditable;
# an optional local-Ollama pass turns the numbers into a readable narrative
# (no data leaves the server).
# -----------------------------------------------------------------------------
from __future__ import annotations
import json
from typing import Any

import frappe
from frappe import _
from frappe.utils import flt, getdate, nowdate, add_days, add_months, get_first_day, get_last_day


def _require_read() -> None:
    """Refuse a financial read from a user with no ledger access.

    `@frappe.whitelist()` requires a login, not a role, so without this these
    endpoints were callable over `/api/method/...` by any authenticated user,
    including portal users with no business seeing the ledger. Reading GL Entry
    is the right test: ERPNext already restricts it to the accounts roles, so
    this inherits the site's own configuration rather than inventing a second
    permission model.
    """
    if not frappe.has_permission("GL Entry", "read"):
        frappe.throw(
            _("You are not permitted to view financial data."),
            frappe.PermissionError,
        )


def _company(company: str | None) -> str:
    return company or frappe.defaults.get_user_default("company") \
        or frappe.defaults.get_global_default("company")


def _accounts(company, **f):
    f.update({"company": company, "is_group": 0})
    return frappe.get_all("Account", filters=f, pluck="name")


def _gl_balance(company, accounts, as_of, signed="dr"):
    if not accounts:
        return 0.0
    rows = frappe.db.sql(
        """select sum(debit)-sum(credit) from `tabGL Entry`
           where company=%s and account in %s and posting_date<=%s and is_cancelled=0""",
        (company, tuple(accounts), as_of))
    bal = flt(rows[0][0]) if rows and rows[0][0] is not None else 0.0
    return bal if signed == "dr" else -bal


def _gl_movement(company, accounts, d1, d2, signed="dr"):
    if not accounts:
        return 0.0
    rows = frappe.db.sql(
        """select sum(debit)-sum(credit) from `tabGL Entry`
           where company=%s and account in %s and posting_date between %s and %s and is_cancelled=0""",
        (company, tuple(accounts), d1, d2))
    bal = flt(rows[0][0]) if rows and rows[0][0] is not None else 0.0
    return bal if signed == "dr" else -bal


def _cash(company, as_of):
    return round(_gl_balance(company, _accounts(company, account_type=["in", ["Bank", "Cash"]]), as_of), 2)


def _receivables(company, as_of):
    invs = frappe.get_all(
        "Sales Invoice",
        filters={"company": company, "docstatus": 1, "outstanding_amount": [">", 0.01]},
        fields=["customer", "outstanding_amount", "due_date", "posting_date"],
        limit_page_length=5000)
    total = overdue = 0.0
    worst_days = 0
    by_cust: dict[str, float] = {}
    for i in invs:
        o = flt(i["outstanding_amount"]); total += o
        due = getdate(i["due_date"] or i["posting_date"])
        days = (getdate(as_of) - due).days
        if days > 0:
            overdue += o
            worst_days = max(worst_days, days)
            by_cust[i["customer"]] = by_cust.get(i["customer"], 0.0) + o
    top = sorted(by_cust.items(), key=lambda x: -x[1])[:5]
    return {"total": round(total, 2), "overdue": round(overdue, 2),
            "worst_days": worst_days,
            "top_overdue": [{"party": c, "amount": round(a, 2)} for c, a in top]}


def _payables(company, as_of):
    invs = frappe.get_all(
        "Purchase Invoice",
        filters={"company": company, "docstatus": 1, "outstanding_amount": [">", 0.01]},
        fields=["supplier", "outstanding_amount", "due_date", "posting_date"],
        limit_page_length=5000)
    total = due_soon = 0.0
    horizon = add_days(as_of, 14)
    for i in invs:
        o = flt(i["outstanding_amount"]); total += o
        due = getdate(i["due_date"] or i["posting_date"])
        if getdate(as_of) <= due <= getdate(horizon):
            due_soon += o
    return {"total": round(total, 2), "due_soon": round(due_soon, 2)}


def _vat(company, as_of):
    tax_accts = _accounts(company, account_type="Tax")
    if not tax_accts:
        tax_accts = [a for a in _accounts(company) if "VAT" in a.upper()]
    # liability balance on tax accounts (credit-positive)
    net = round(_gl_balance(company, tax_accts, as_of, signed="cr"), 2)
    return {"net_payable": net, "accounts": len(tax_accts)}


def _revenue(company, as_of):
    inc = _accounts(company, root_type="Income")
    this_d1, this_d2 = get_first_day(as_of), as_of
    last_d1, last_d2 = get_first_day(add_months(as_of, -1)), get_last_day(add_months(as_of, -1))
    yoy_d1, yoy_d2 = get_first_day(add_months(as_of, -12)), get_last_day(add_months(as_of, -12))
    cur = _gl_movement(company, inc, this_d1, this_d2, signed="cr")
    prev = _gl_movement(company, inc, last_d1, last_d2, signed="cr")
    yoy = _gl_movement(company, inc, yoy_d1, yoy_d2, signed="cr")
    return {"this_month": round(cur, 2), "last_month": round(prev, 2), "year_ago": round(yoy, 2),
            "mom_pct": round((cur - prev) / prev * 100, 1) if prev else None,
            "yoy_pct": round((cur - yoy) / yoy * 100, 1) if yoy else None}


def _margin(company, as_of):
    inc = _accounts(company, root_type="Income")
    cogs = _accounts(company, account_type=["in", ["Cost of Goods Sold", "Stock Adjustment"]])
    d1 = get_first_day(as_of)
    rev = _gl_movement(company, inc, d1, as_of, signed="cr")
    cost = _gl_movement(company, cogs, d1, as_of, signed="dr")
    gm = round((rev - cost) / rev * 100, 1) if rev else None
    return {"revenue": round(rev, 2), "cogs": round(cost, 2), "gross_margin_pct": gm}


def _runway(company, cash, as_of):
    exp = _accounts(company, root_type="Expense")
    d1 = get_first_day(add_months(as_of, -3))
    out = _gl_movement(company, exp, d1, as_of, signed="dr")
    months = 3.0
    burn = out / months if out else 0.0
    runway = round(cash / burn, 1) if burn > 0 else None
    return {"monthly_burn": round(burn, 2), "runway_months": runway}


def _unreconciled(company):
    bas = frappe.get_all("Bank Account", filters={"company": company, "is_company_account": 1}, pluck="name")
    cnt = val = 0
    for ba in bas:
        rows = frappe.get_all(
            "Bank Transaction",
            filters={"bank_account": ba, "docstatus": 1, "status": ["not in", ["Reconciled", "Settled"]]},
            fields=["deposit", "withdrawal"], limit_page_length=5000)
        cnt += len(rows)
        val += sum(flt(r["deposit"]) or flt(r["withdrawal"]) for r in rows)
    return {"count": cnt, "value": round(val, 2)}


def _has(doctype) -> bool:
    try:
        return bool(frappe.db.exists("DocType", doctype))
    except Exception:
        return False


def _period_range(as_of, period):
    d = getdate(as_of)
    if period == "last_month":
        s, e, lbl = get_first_day(add_months(d, -1)), get_last_day(add_months(d, -1)), "Last month"
    elif period == "this_quarter":
        q0 = (d.month - 1) // 3 * 3 + 1
        s = getdate(f"{d.year}-{q0:02d}-01"); e = get_last_day(add_months(s, 2)); lbl = "This quarter"
    elif period == "last_quarter":
        q0 = (d.month - 1) // 3 * 3 + 1
        thisq = getdate(f"{d.year}-{q0:02d}-01")
        s = add_months(thisq, -3); e = get_last_day(add_months(s, 2)); lbl = "Last quarter"
    elif period == "half_year":
        s, e, lbl = get_first_day(add_months(d, -5)), get_last_day(d), "Half-year (6 mo)"
    elif period == "ytd":
        s, e, lbl = getdate(f"{d.year}-01-01"), d, "Year to date"
    elif period == "last_12m":
        s, e, lbl = get_first_day(add_months(d, -11)), get_last_day(d), "Last 12 months"
    else:
        s, e, lbl = get_first_day(d), get_last_day(d), "This month"
    months = (e.year - s.year) * 12 + (e.month - s.month) + 1
    return s, e, months, lbl


def _defined_monthly(company):
    """Committed monthly payroll from the active Salary Structure Assignment
    (master) — latest assignment per employee."""
    total = 0.0
    per_emp: dict[str, float] = {}
    if _has("Salary Structure Assignment"):
        seen = set()
        for r in frappe.get_all("Salary Structure Assignment",
                                filters={"company": company, "docstatus": 1},
                                fields=["employee", "base", "from_date"],
                                order_by="from_date desc", limit_page_length=20000):
            if r["employee"] in seen:
                continue
            seen.add(r["employee"])
            per_emp[r["employee"]] = flt(r["base"]); total += flt(r["base"])
    return round(total, 2), per_emp


def _additional_salary(company, d1, d2):
    if not _has("Additional Salary"):
        return 0.0
    try:
        rows = frappe.get_all("Additional Salary",
                              filters={"company": company, "docstatus": 1,
                                       "payroll_date": ["between", [d1, d2]]},
                              fields=["amount", "type"], limit_page_length=20000)
    except Exception:
        rows = frappe.get_all("Additional Salary",
                              filters={"company": company, "docstatus": 1,
                                       "payroll_date": ["between", [d1, d2]]},
                              fields=["amount"], limit_page_length=20000)
    total = 0.0
    for r in rows:
        amt = flt(r["amount"])
        total += -amt if str(r.get("type")) == "Deduction" else amt
    return round(total, 2)


def _emp_base_wage(company) -> dict:
    """Latest monthly base wage per active employee (Structure Assignment first,
    else the most recent Salary Slip gross)."""
    wage: dict[str, float] = {}
    if _has("Salary Structure Assignment"):
        for r in frappe.get_all("Salary Structure Assignment",
                                filters={"company": company, "docstatus": 1},
                                fields=["employee", "base", "from_date"],
                                order_by="from_date desc", limit_page_length=5000):
            wage.setdefault(r["employee"], flt(r["base"]))
    if _has("Salary Slip"):
        for r in frappe.get_all("Salary Slip",
                                filters={"company": company, "docstatus": 1},
                                fields=["employee", "gross_pay", "start_date"],
                                order_by="start_date desc", limit_page_length=5000):
            wage.setdefault(r["employee"], flt(r["gross_pay"]))
    return wage


def _payroll(company, as_of):
    """KSA-aware payroll snapshot: headcount, monthly payroll cost, accrued
    (submitted-but-unpaid) salary, and an End-of-Service (EOSB) provision."""
    out = {"available": False, "headcount": 0, "monthly_cost": 0.0,
           "accrued_unpaid": 0.0, "eosb_liability": 0.0, "next_payroll": 0.0,
           "saudi_pct": None}
    if not _has("Employee"):
        return out
    out["available"] = True
    employees = frappe.get_all("Employee", filters={"company": company, "status": "Active"},
                               fields=["name", "date_of_joining"], limit_page_length=10000)
    out["headcount"] = len(employees)

    # Saudization (best-effort; only if a nationality-like field is populated)
    try:
        meta = frappe.get_meta("Employee")
        nat_field = next((f.fieldname for f in meta.fields
                          if f.fieldname in ("nationality", "custom_nationality")), None)
        if nat_field:
            saudi = sum(1 for e in frappe.get_all("Employee",
                        filters={"company": company, "status": "Active"},
                        fields=[nat_field]) if str(e.get(nat_field) or "").strip().lower() in ("saudi", "saudi arabian", "سعودي"))
            out["saudi_pct"] = round(100 * saudi / max(1, len(employees)), 1)
    except Exception:
        pass

    # current-month payroll cost from submitted Salary Slips
    if _has("Salary Slip"):
        d1, d2 = get_first_day(as_of), get_last_day(as_of)
        slips = frappe.get_all("Salary Slip",
                               filters={"company": company, "docstatus": 1,
                                        "start_date": [">=", d1], "end_date": ["<=", d2]},
                               fields=["net_pay", "gross_pay", "status"], limit_page_length=10000)
        out["monthly_cost"] = round(sum(flt(s["gross_pay"]) for s in slips), 2)
        out["accrued_unpaid"] = round(sum(flt(s["net_pay"]) for s in slips
                                          if str(s.get("status")) != "Paid"), 2)
        out["next_payroll"] = out["monthly_cost"] or 0.0

    # EOSB provision (Saudi Labour Law: ½ month/yr for first 5 yrs, 1 month/yr after)
    wage = _emp_base_wage(company)
    eosb = 0.0
    for e in employees:
        if not e.get("date_of_joining"):
            continue
        yrs = (getdate(as_of) - getdate(e["date_of_joining"])).days / 365.0
        w = flt(wage.get(e["name"]))
        if w <= 0:
            continue
        eosb += (0.5 * min(yrs, 5) + 1.0 * max(0.0, yrs - 5)) * w
    out["eosb_liability"] = round(eosb, 2)
    if not out["monthly_cost"] and wage:
        out["monthly_cost"] = round(sum(wage.values()), 2)
    # next payroll is a forward commitment — prefer the defined (master) total
    defined_monthly, _ = _defined_monthly(company)
    out["defined_monthly"] = defined_monthly
    out["next_payroll"] = defined_monthly or out["monthly_cost"]
    return out


def _money(v):
    return f"{flt(v):,.0f}"


def _build_alerts(m) -> list[dict]:
    a = []
    cash, ar, ap, vat, rev, mar, run, unrec = (
        m["cash"], m["receivables"], m["payables"], m["vat"],
        m["revenue"], m["margin"], m["runway"], m["unreconciled"])
    pay = m.get("payroll") or {}

    if cash is not None and cash < 0:
        a.append({"severity": "high", "area": "Cash", "title": "Cash balance is negative",
                  "amount": cash, "action": "Cover the shortfall or pause non-essential payments."})
    if run.get("runway_months") is not None and run["runway_months"] < 3:
        a.append({"severity": "high", "area": "Cash",
                  "title": f"Cash runway is short ({run['runway_months']} months)",
                  "amount": cash, "action": "Accelerate collections and review discretionary spend."})

    if ar["overdue"] > 0:
        sev = "high" if (rev["this_month"] and ar["overdue"] > rev["this_month"]) or ar["worst_days"] > 90 else "medium"
        names = ", ".join(x["party"] for x in ar["top_overdue"][:2])
        a.append({"severity": sev, "area": "Receivables",
                  "title": f"Overdue receivables {_money(ar['overdue'])} (worst {ar['worst_days']}d)",
                  "amount": ar["overdue"],
                  "action": f"Chase {names}." if names else "Follow up with the largest overdue accounts."})

    if vat["net_payable"] > 0:
        a.append({"severity": "medium", "area": "VAT/ZATCA",
                  "title": f"VAT payable building: {_money(vat['net_payable'])}",
                  "amount": vat["net_payable"],
                  "action": "Set funds aside for the next ZATCA return."})

    if ap["due_soon"] > 0:
        a.append({"severity": "medium", "area": "Payables",
                  "title": f"Payables due within 14 days: {_money(ap['due_soon'])}",
                  "amount": ap["due_soon"], "action": "Confirm cash is available to clear them."})

    if rev["mom_pct"] is not None and rev["mom_pct"] <= -10:
        a.append({"severity": "medium", "area": "Revenue",
                  "title": f"Revenue down {abs(rev['mom_pct'])}% vs last month",
                  "amount": rev["this_month"], "action": "Check pipeline and any lost/late billing."})

    if mar["gross_margin_pct"] is not None and mar["gross_margin_pct"] < 0:
        a.append({"severity": "high", "area": "Margin",
                  "title": f"Gross margin negative ({mar['gross_margin_pct']}%)",
                  "amount": mar["revenue"], "action": "Review pricing and direct costs immediately."})

    if pay.get("available"):
        if pay.get("next_payroll", 0) > 0:
            sev = "high" if (cash is not None and cash < pay["next_payroll"]) else "medium"
            a.append({"severity": sev, "area": "Payroll",
                      "title": f"Next payroll ≈ {_money(pay['next_payroll'])}"
                               + ("  — exceeds current cash" if sev == "high" else ""),
                      "amount": pay["next_payroll"],
                      "action": "Ensure cash is in place before the run date." if sev == "high"
                                else "Funded by current cash."})
        if pay.get("accrued_unpaid", 0) > 0:
            a.append({"severity": "medium", "area": "Payroll",
                      "title": f"Accrued unpaid salary: {_money(pay['accrued_unpaid'])}",
                      "amount": pay["accrued_unpaid"],
                      "action": "Submitted salary slips not yet paid — settle or schedule."})

    pl = m.get("people_liabilities") or {}
    if pl.get("total", 0) > 0:
        bits = [f"EOSB {_money(pl['eosb'])}"]
        if pl.get("vacation"):
            bits.append(f"vacation {_money(pl['vacation'])}")
        if pl.get("ticket"):
            bits.append(f"ticket {_money(pl['ticket'])}")
        if pl.get("insurance"):
            bits.append(f"insurance {_money(pl['insurance'])}")
        unset = []
        if not pl.get("ticket_configured"):
            unset.append("ticket")
        if not pl.get("insurance_configured"):
            unset.append("insurance")
        action = " + ".join(bits) + ". Ensure provisioned in the books."
        if unset:
            action += f" ({' & '.join(unset)} source not set in People → Configure sources.)"
        a.append({"severity": "low", "area": "People liabilities",
                  "title": f"People liabilities provision: {_money(pl['total'])}",
                  "amount": pl["total"], "action": action})

    if unrec["count"] > 0:
        a.append({"severity": "low", "area": "Reconciliation",
                  "title": f"{unrec['count']} unreconciled bank items ({_money(unrec['value'])})",
                  "amount": unrec["value"], "action": "Reconcile in the Bank tab."})

    order = {"high": 0, "medium": 1, "low": 2}
    a.sort(key=lambda x: (order[x["severity"]], -abs(flt(x["amount"]))))
    return a


def _narrative(m, alerts) -> str:
    rev, ar, cash = m["revenue"], m["receivables"], m["cash"]
    parts = []
    if cash is not None:
        parts.append(f"Cash on hand is {_money(cash)} SAR")
        if m["runway"].get("runway_months") is not None:
            parts[-1] += f" (~{m['runway']['runway_months']} months runway)"
    if rev["mom_pct"] is not None:
        d = "up" if rev["mom_pct"] >= 0 else "down"
        parts.append(f"month-to-date revenue is {d} {abs(rev['mom_pct'])}% vs last month")
    if ar["overdue"] > 0:
        parts.append(f"{_money(ar['overdue'])} SAR of receivables is overdue")
    base = ". ".join(parts) + "." if parts else "Not enough posted data yet for a full brief."
    tops = [x["title"] for x in alerts[:3]]
    if tops:
        base += " Top attention: " + "; ".join(tops) + "."
    # optional local-Ollama polish
    try:
        cfg = _ollama_cfg()
        if cfg:
            import requests
            facts = json.dumps({"metrics": m, "alerts": alerts[:5]}, default=str)
            r = requests.post(f"{cfg['url']}/api/chat", json={
                "model": cfg["model"], "stream": False, "options": {"temperature": 0.2},
                "messages": [
                    {"role": "system", "content":
                     "You are a precise CFO. In 3 short sentences, summarise the financial "
                     "situation and the single most important action. Use only the numbers "
                     "given. Do not invent figures. Plain text, no markdown."},
                    {"role": "user", "content": facts},
                ]}, timeout=cfg["timeout"])
            r.raise_for_status()
            txt = (r.json().get("message", {}).get("content") or "").strip()
            if txt:
                return txt
    except Exception:
        frappe.log_error(title="cfo: ollama narrative failed", message=frappe.get_traceback())
    return base


def _ollama_cfg():
    conf = frappe.conf or {}
    url = conf.get("ollama_url")
    model = conf.get("ollama_text_model")
    try:
        s = frappe.get_single("Insight AI Settings")
        url = getattr(s, "ollama_url", None) or url
        model = getattr(s, "ollama_text_model", None) or model
    except Exception:
        pass
    if not url:
        return None
    return {"url": url.rstrip("/"), "model": model or "qwen2.5", "timeout": 60}


@frappe.whitelist()
def morning_brief(company: str | None = None, as_of: str | None = None,
                  narrative: int = 1) -> dict:
    _require_read()
    company = _company(company)
    as_of = as_of or nowdate()
    cash = _cash(company, as_of)
    m = {
        "company": company, "as_of": str(as_of),
        "cash": cash,
        "receivables": _receivables(company, as_of),
        "payables": _payables(company, as_of),
        "vat": _vat(company, as_of),
        "revenue": _revenue(company, as_of),
        "margin": _margin(company, as_of),
        "runway": _runway(company, cash, as_of),
        "unreconciled": _unreconciled(company),
        "payroll": _payroll(company, as_of),
    }
    # consolidated people liabilities: EOSB + vacation + ticket + insurance
    pay = m["payroll"]
    if pay.get("available"):
        emps = frappe.get_all("Employee", filters={"company": company, "status": "Active"},
                              fields=["name", "date_of_joining"], limit_page_length=20000)
        prov = _provisions(company, as_of, emps, _emp_base_wage(company)) if emps else {}
        eosb = flt(pay.get("eosb_liability"))
        vac = flt(prov.get("vacation"))
        ticket = flt(prov.get("ticket")) if prov.get("ticket_configured") else 0.0
        ins = flt(prov.get("insurance")) if prov.get("insurance_configured") else 0.0
        m["people_liabilities"] = {
            "eosb": eosb, "vacation": vac, "ticket": ticket, "insurance": ins,
            "ticket_configured": prov.get("ticket_configured", False),
            "insurance_configured": prov.get("insurance_configured", False),
            "total": round(eosb + vac + ticket + ins, 2),
        }
    alerts = _build_alerts(m)
    high = sum(1 for x in alerts if x["severity"] == "high")
    out = {"metrics": m, "alerts": alerts,
           "headline_count": len(alerts), "high_count": high,
           "status": "high" if high else ("medium" if alerts else "calm")}
    if int(narrative):
        out["narrative"] = _narrative(m, alerts)
    return out


@frappe.whitelist()
def hr_summary(company: str | None = None, as_of: str | None = None,
               period: str = "this_month") -> dict:
    """People & payroll view. Point-in-time snapshot (headcount, EOSB) plus a
    period-scoped salary comparison: defined (master) vs processed (actual),
    additional salary, and the variance."""
    _require_read()
    company = _company(company)
    as_of = as_of or nowdate()
    s, e, months, lbl = _period_range(as_of, period)
    pay = _payroll(company, as_of)

    defined_monthly = pay.get("defined_monthly", 0.0)
    defined_for_period = round(defined_monthly * months, 2)
    processed = net = 0.0
    slip_count = 0
    if _has("Salary Slip"):
        slips = frappe.get_all("Salary Slip",
                               filters={"company": company, "docstatus": 1,
                                        "start_date": [">=", s], "end_date": ["<=", e]},
                               fields=["gross_pay", "net_pay"], limit_page_length=50000)
        slip_count = len(slips)
        processed = round(sum(flt(x["gross_pay"]) for x in slips), 2)
        net = round(sum(flt(x["net_pay"]) for x in slips), 2)
    additional = _additional_salary(company, s, e)
    variance = round(processed - defined_for_period, 2)

    runs = []
    if _has("Payroll Entry"):
        run_fields = ["name", "start_date", "end_date", "posting_date",
                      "number_of_employees", "status", "branch", "department",
                      "payroll_frequency"]
        try:
            runs = frappe.get_all("Payroll Entry",
                                  filters={"company": company, "docstatus": 1},
                                  fields=run_fields, order_by="end_date desc, creation desc",
                                  limit_page_length=12)
        except Exception:
            runs = frappe.get_all("Payroll Entry",
                                  filters={"company": company, "docstatus": 1},
                                  fields=["name", "start_date", "end_date", "posting_date",
                                          "number_of_employees", "status"],
                                  order_by="end_date desc", limit_page_length=12)
    depts = []
    emps_full = []
    if _has("Employee"):
        emps_full = frappe.get_all("Employee", filters={"company": company, "status": "Active"},
                                   fields=["name", "department", "date_of_joining"], limit_page_length=20000)
        agg: dict[str, int] = {}
        for r in emps_full:
            agg[r.get("department") or "—"] = agg.get(r.get("department") or "—", 0) + 1
        depts = sorted(({"department": k, "count": v} for k, v in agg.items()),
                       key=lambda x: -x["count"])[:8]

    provisions = {}
    if emps_full:
        provisions = _provisions(company, as_of, emps_full, _emp_base_wage(company))

    return {
        "company": company, "as_of": str(as_of),
        "period": period, "period_label": lbl,
        "from_date": str(s), "to_date": str(e), "months": months,
        "payroll": pay,
        "provisions": provisions,
        "salary": {
            "defined_monthly": defined_monthly,
            "defined_for_period": defined_for_period,
            "processed": processed, "net_paid": net,
            "additional": additional, "variance": variance,
            "slip_count": slip_count,
        },
        "recent_runs": [{**r, "start_date": str(r["start_date"]),
                         "end_date": str(r["end_date"]),
                         "posting_date": str(r.get("posting_date") or "")} for r in runs],
        "departments": depts,
    }


@frappe.whitelist()
def eosb_breakdown(company: str | None = None, as_of: str | None = None,
                   limit: int = 1000) -> dict:
    """Slab-wise End-of-Service provision per Saudi Labour Law: ½ month per year
    for the first 5 years, 1 month per year thereafter, on each active
    employee's latest base wage."""
    _require_read()
    company = _company(company)
    as_of = as_of or nowdate()
    wage = _emp_base_wage(company)
    emps = frappe.get_all("Employee", filters={"company": company, "status": "Active"},
                          fields=["name", "employee_name", "date_of_joining"],
                          limit_page_length=20000)
    rows = []
    s1 = s2 = 0.0
    for ee in emps:
        doj = ee.get("date_of_joining")
        w = flt(wage.get(ee["name"]))
        if not doj or w <= 0:
            continue
        yrs = (getdate(as_of) - getdate(doj)).days / 365.0
        a = 0.5 * min(yrs, 5) * w           # first-5-years slab
        b = 1.0 * max(0.0, yrs - 5) * w     # beyond-5-years slab
        s1 += a; s2 += b
        rows.append({"employee": ee["name"], "employee_name": ee.get("employee_name") or ee["name"],
                     "doj": str(doj), "years": round(yrs, 2), "wage": round(w, 2),
                     "slab1": round(a, 2), "slab2": round(b, 2), "total": round(a + b, 2)})
    rows.sort(key=lambda r: -r["total"])
    return {"company": company, "as_of": str(as_of), "count": len(rows),
            "slab1_total": round(s1, 2), "slab2_total": round(s2, 2),
            "total": round(s1 + s2, 2), "rows": rows[: int(limit)]}


# ---- people provisions: vacation, ticket, insurance ------------------------

def _provision_config():
    try:
        s = frappe.get_single("Insight AI Settings")
        return {
            "vacation_days": int(getattr(s, "vacation_days_per_year", 0) or 0),
            "ticket_source": getattr(s, "ticket_source", None) or "",
            "insurance_source": getattr(s, "insurance_source", None) or "",
        }
    except Exception:
        return {"vacation_days": 0, "ticket_source": "", "insurance_source": ""}


def _source_value(company, source, emp_count):
    """Resolve a configurable provision source to an annual total.
    Format: 'field:<fieldname>' | 'component:<name>' | 'fixed:<amount_per_employee>'."""
    if not source:
        return None, "not set"
    kind, _, val = str(source).partition(":")
    val = val.strip()
    if kind == "fixed":
        return round(flt(val) * emp_count, 2), f"fixed {flt(val):,.0f}/employee × {emp_count}"
    if kind == "field":
        try:
            rows = frappe.get_all("Employee", filters={"company": company, "status": "Active"},
                                  fields=[val], limit_page_length=20000)
            return round(sum(flt(r.get(val)) for r in rows), 2), f"sum of Employee.{val}"
        except Exception:
            return None, f"field {val} not found"
    if kind == "component":
        # latest amount of that salary component per employee × 12 (treat as monthly)
        if not _has("Salary Detail"):
            return None, "no salary data"
        try:
            rows = frappe.db.sql(
                """select sd.amount from `tabSalary Detail` sd
                   join `tabSalary Slip` ss on ss.name=sd.parent
                   where ss.company=%s and ss.docstatus=1 and sd.salary_component=%s
                   order by ss.start_date desc limit 5000""",
                (company, val))
            # average per slip × 12 × headcount-ish; keep simple: sum recent ×12/months
            amt = sum(flt(r[0]) for r in rows[:emp_count]) if rows else 0.0
            return round(amt * 12, 2), f"component '{val}' (annualised ×12)"
        except Exception:
            return None, f"component {val} error"
    return None, "unknown source"


def _provisions(company, as_of, emps, wage):
    cfg = _provision_config()
    n = len(emps)
    # vacation: annual entitlement value (Saudi: 21 days <5y, 30 days >=5y; override if set)
    vac = 0.0
    for e in emps:
        w = flt(wage.get(e["name"]))
        if w <= 0 or not e.get("date_of_joining"):
            continue
        yrs = (getdate(as_of) - getdate(e["date_of_joining"])).days / 365.0
        days = cfg["vacation_days"] or (30 if yrs >= 5 else 21)
        vac += days * (w / 30.0)
    ticket, ticket_basis = _source_value(company, cfg["ticket_source"], n)
    insurance, ins_basis = _source_value(company, cfg["insurance_source"], n)
    return {
        "vacation": round(vac, 2),
        "vacation_basis": f"{cfg['vacation_days'] or '21/30'} days × daily wage, all active staff",
        "ticket": ticket, "ticket_basis": ticket_basis, "ticket_configured": bool(cfg["ticket_source"]),
        "insurance": insurance, "insurance_basis": ins_basis, "insurance_configured": bool(cfg["insurance_source"]),
        "config": cfg,
    }


@frappe.whitelist()
def provision_field_options(company: str | None = None) -> dict:
    """Numeric Employee fields and Salary Components, for the source pickers."""
    _require_read()
    out = {"employee_fields": [], "components": []}
    try:
        meta = frappe.get_meta("Employee")
        out["employee_fields"] = [{"value": f.fieldname, "label": f.label or f.fieldname}
                                  for f in meta.fields if f.fieldtype in ("Currency", "Float", "Int")]
    except Exception:
        pass
    if _has("Salary Component"):
        out["components"] = frappe.get_all("Salary Component", pluck="name", limit_page_length=300)
    return out


@frappe.whitelist()
def get_provision_config() -> dict:
    _require_read()
    return _provision_config()


@frappe.whitelist()
def set_provision_config(vacation_days: int | None = None, ticket_source: str | None = None,
                         insurance_source: str | None = None) -> dict:
    _require_read()
    s = frappe.get_single("Insight AI Settings")
    if vacation_days is not None:
        s.vacation_days_per_year = int(vacation_days or 0)
    if ticket_source is not None:
        s.ticket_source = ticket_source or None
    if insurance_source is not None:
        s.insurance_source = insurance_source or None
    s.save(ignore_permissions=False)
    return {"ok": True}
