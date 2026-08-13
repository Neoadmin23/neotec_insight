# Copyright (c) 2026, Neotec Integrated Solution
# Zakat base estimation (v2.24.0) — the equity (indirect) method used in KSA
# ZATCA practice, assembled from the GL:
#
#   Zakat base = Equity (capital, reserves, retained earnings)
#              + Adjusted net profit for the zakat year
#              + Long-term financing sources (loans, provisions held ≥ 1 year)
#              − Deductions (net fixed assets, CWIP, long-term investments)
#   …with the base floored at the adjusted net profit (a positive profit is
#   zakatable even when the equity-method base nets below it).
#
#   Zakat due = base × 2.5%       (Hijri year)
#             = base × 2.577683%  (Gregorian year — 2.5% × 365.25/354.37)
#
# This is a PREPARATION ESTIMATE for review with the zakat advisor — item
# adjustments (disallowed provisions, pre-incorporation losses, investments in
# zakat-paying entities, …) are the advisor's call; every component here is
# expandable to account level so those adjustments are easy to apply.
from __future__ import annotations

import re

import frappe
from frappe import _
from frappe.utils import flt, getdate

from .health import _default_company
from .cashflow import _INVESTING_NAME, _INVESTING_TYPES, _FINANCING_NAME

HIJRI_RATE = 0.025
GREGORIAN_RATE = 0.02577683

_ACC_DEP = re.compile(r"accumulated\s+depreciation|إهلاك\s*متراكم|مجمع\s*(الإهلاك|الاستهلاك|استهلاك)", re.I)
_PROVISION = re.compile(r"provision|end of service|eosb|gratuity|مخصص|مكافأة نهاية الخدمة", re.I)


def _gl_balances(company, as_of):
    """Per-account (credit − debit) balance as of a date — natural sign for
    Equity/Liability; assets therefore come out negative."""
    return frappe.db.sql(
        """SELECT gle.account, a.account_name, a.root_type, a.account_type,
                  COALESCE(SUM(gle.credit - gle.debit), 0) AS bal
           FROM `tabGL Entry` gle
           JOIN `tabAccount` a ON a.name = gle.account
           WHERE gle.company = %(c)s AND gle.is_cancelled = 0
             AND gle.posting_date <= %(d)s
           GROUP BY gle.account, a.account_name, a.root_type, a.account_type
           HAVING ABS(bal) >= 0.005""",
        {"c": company, "d": as_of}, as_dict=True)


def _period_profit(company, from_date, to_date):
    r = frappe.db.sql(
        """SELECT COALESCE(SUM(gle.credit - gle.debit), 0) AS p
           FROM `tabGL Entry` gle
           JOIN `tabAccount` a ON a.name = gle.account
           WHERE gle.company = %(c)s AND gle.is_cancelled = 0
             AND gle.voucher_type != 'Period Closing Voucher'
             AND a.root_type IN ('Income', 'Expense')
             AND gle.posting_date BETWEEN %(f)s AND %(t)s""",
        {"c": company, "f": from_date, "t": to_date}, as_dict=True)
    return flt(r[0]["p"], 2) if r else 0.0


@frappe.whitelist()
def zakat_estimate(company=None, from_date=None, to_date=None, calendar="hijri"):
    """Zakat base + due for the zakat year ending `to_date`."""
    company = company or _default_company()
    if not company:
        frappe.throw(_("No company found."))
    if not from_date or not to_date:
        frappe.throw(_("from_date and to_date (the zakat year) are required."))
    if not frappe.has_permission("GL Entry", "read"):
        frappe.throw(_("Not permitted."))
    getdate(from_date); getdate(to_date)

    from .classify import tag_map
    tags = tag_map(company)
    balances = _gl_balances(company, to_date)
    profit = _period_profit(company, from_date, to_date)

    equity_lines, financing_lines, deduction_lines = [], [], []
    for b in balances:
        text = f"{b.get('account_name') or ''} {b['account']}"
        bal = flt(b["bal"], 2)
        line = {"account": b["account"],
                "label": b.get("account_name") or b["account"], "amount": bal}
        user_tag = tags.get(b["account"])
        if b["root_type"] == "Equity":
            equity_lines.append(line)
        elif user_tag in ("financing", "provision") and b["root_type"] == "Liability":
            financing_lines.append(line)
        elif user_tag == "investing" and b["root_type"] == "Asset":
            deduction_lines.append({**line, "amount": flt(-bal, 2)})
        elif user_tag:
            pass  # explicitly tagged as something else — heuristics stay out
        elif b["root_type"] == "Liability" and (
                _FINANCING_NAME.search(text) or _PROVISION.search(text)):
            # Long-term funding sources added to the base (advisor may exclude
            # loans held < 354 days — that judgement stays with the advisor).
            financing_lines.append(line)
        elif b["root_type"] == "Asset" and (
                b.get("account_type") in _INVESTING_TYPES
                or b.get("account_type") == "Accumulated Depreciation"
                or _INVESTING_NAME.search(text) or _ACC_DEP.search(text)):
            # Asset balances arrive negative (credit − debit); a deduction line
            # is shown as the positive net asset value.
            deduction_lines.append({**line, "amount": flt(-bal, 2)})

    for lst in (equity_lines, financing_lines, deduction_lines):
        lst.sort(key=lambda x: -abs(x["amount"]))

    equity_total = flt(sum(l["amount"] for l in equity_lines), 2)
    financing_total = flt(sum(l["amount"] for l in financing_lines), 2)
    deductions_total = flt(sum(l["amount"] for l in deduction_lines), 2)

    # Equity balances as of to_date already contain the year's profit (unless a
    # PCV moved it — either way it lives in Equity/P&L balances). To show the
    # classic presentation without double counting we compute:
    #   base = (equity incl. profit) + financing − deductions
    # and DISPLAY equity-excluding-profit + profit as separate lines.
    base_raw = flt(equity_total + financing_total - deductions_total, 2)
    equity_ex_profit = flt(equity_total - profit, 2)
    base = max(base_raw, flt(profit, 2))  # ZATCA floor: not less than adjusted profit
    floored = base != base_raw

    rate = GREGORIAN_RATE if (calendar or "").lower().startswith("greg") else HIJRI_RATE
    zakat_due = flt(base * rate, 2)

    return {
        "company": company,
        "period": {"from_date": from_date, "to_date": to_date},
        "currency": frappe.db.get_value("Company", company, "default_currency") or "SAR",
        "calendar": "gregorian" if rate == GREGORIAN_RATE else "hijri",
        "rate_pct": flt(rate * 100, 6),
        "components": {
            "equity_excl_profit": equity_ex_profit,
            "equity_lines": equity_lines,
            "net_profit": profit,
            "financing": financing_lines,
            "financing_total": financing_total,
            "deductions": deduction_lines,
            "deductions_total": deductions_total,
        },
        "base_raw": base_raw,
        "base": base,
        "floored_at_profit": floored,
        "zakat_due": zakat_due,
        "notes": [
            "Estimation by the equity (indirect) method for preparation purposes — the final zakat base requires advisor adjustments (disallowed provisions, investments in zakat-paying entities, funding held under a full year, statutory items).",
            "Long-term funding and deduction lines are classified by account type and name; expand each section and review the account list before relying on the figure.",
            "The base is floored at the adjusted net profit per ZATCA practice; the raw equity-method base is shown alongside when the floor applies.",
        ],
    }
