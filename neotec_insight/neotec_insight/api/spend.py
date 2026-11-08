"""Expense Audit (v3.7.0) — company spending from imported bank statements.
Steps 1–2: clean, remove internal moves, unmask middlemen, first look.
Every number is computed from the statement lines (utils/spend_core.py)."""
from __future__ import annotations

from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import flt, getdate

from neotec_insight.neotec_insight.utils import spend_core as core

CARD_WORDS = ("credit card", "card payment", "بطاقة", "visa payment", "mastercard payment")


@frappe.whitelist()
def audit(company=None, from_date=None, to_date=None):
    from neotec_insight.neotec_insight.utils.access import check_company, require_finance_read
    require_finance_read()
    company = company or frappe.defaults.get_user_default("Company")
    check_company(company)
    f = getdate(from_date) if from_date else None
    t = getdate(to_date) if to_date else None
    stmts = frappe.get_list("Insight Bank Statement", filters={"company": company},
                            fields=["name", "bank_account", "gl_account"], limit_page_length=0, order_by="from_date asc")
    lines, seen = [], set()
    for st in stmts:
        cur = frappe.db.get_value("Account", st["gl_account"], "account_currency") if st["gl_account"] else None
        for ln in frappe.get_doc("Insight Bank Statement", st["name"]).get("lines") or []:
            d = getdate(ln["posting_date"])
            if (f and d < f) or (t and d > t) or ln["line_key"] in seen:     # overlapping statements count once
                continue
            seen.add(ln["line_key"])
            lines.append({"date": d, "account": st["bank_account"], "details": ln["details"] or "",
                          "reference": ln["reference"] or "", "type": ln["txn_type"] or "",
                          "credit": flt(ln["credit"]), "debit": flt(ln["debit"]), "currency": cur or "SAR"})
    if not lines:
        frappe.throw(_("No bank statement lines for this company and period. Import statements in Operations → Reconcile."))
    comp = frappe.get_doc("Company", company)
    own = (comp.company_name, comp.name, getattr(comp, "abbr", ""))
    rows = core.classify(sorted(lines, key=lambda x: (x["date"], x["account"])), own_names=own)

    accounts = defaultdict(lambda: {"count": 0, "first": None, "last": None, "currency": ""})
    for r in rows:
        a = accounts[r["account"]]
        a["count"] += 1
        a["currency"] = r["currency"]
        a["first"] = min(a["first"] or r["date"], r["date"])
        a["last"] = max(a["last"] or r["date"], r["date"])
    bank = {b["name"]: b["bank"] for b in frappe.get_all("Bank Account", fields=["name", "bank"], limit_page_length=0,
                                                          order_by="name asc")}
    internal = [r for r in rows if r["kind"] == "internal"]
    cards = [r for r in rows if r["kind"] == "spend" and any(w in r["details"].lower() for w in CARD_WORDS)]
    middle: dict[tuple, dict] = {}
    for r in rows:
        if r["kind"] == "spend" and r["via"]:
            k = (r["via"], round(r["debit"], 2), r["date"].day)
            m = middle.setdefault(k, {"via": r["via"], "amount": round(r["debit"], 2), "day": r["date"].day, "count": 0})
            m["count"] += 1
    spend_total = round(sum(r["debit"] for r in rows if r["kind"] == "spend"), 2)
    in_total = round(sum(r["credit"] for r in rows if r["kind"] in ("income", "refund")), 2)
    return {
        "company": company, "from_date": str(min(r["date"] for r in rows)), "to_date": str(max(r["date"] for r in rows)),
        "accounts": [{"account": k, "bank": bank.get(k) or "", "currency": v["currency"], "first": str(v["first"]),
                      "last": str(v["last"]), "count": v["count"]} for k, v in sorted(accounts.items())],
        "lines": len(rows), "money_in": in_total, "money_out": spend_total, "left": round(in_total - spend_total, 2),
        "internal": {"count": len(internal), "total": round(sum(r["amount"] for r in internal), 2)},
        "card_payments": [{"date": str(r["date"]), "amount": r["debit"], "text": r["details"][:120]} for r in cards],
        "monthly": core.monthly(rows), "centres": core.centres(rows), "recurring": core.recurring(rows),
        "middlemen": sorted(middle.values(), key=lambda m: (-m["count"], -m["amount"])),
        "rows": [{"date": str(r["date"]), "account": r["account"], "merchant": r["merchant"], "via": r["via"] or "",
                  "centre": r["centre"], "kind": r["kind"], "amount": r["amount"],
                  "direction": "out" if r["debit"] else "in", "currency": r["currency"]} for r in rows],
    }
