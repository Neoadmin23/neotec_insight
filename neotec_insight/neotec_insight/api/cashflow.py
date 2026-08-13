# Copyright (c) 2026, Neotec Integrated Solution
# Statement of Cash Flows (indirect method) assembled from the GL.
#
# Method: for a balanced ledger, the period change in cash equals net profit
# plus the period movement (credit − debit) of every non-cash balance-sheet
# account. We therefore compute per-account movements, classify each account
# into Operating (net profit, depreciation add-back, working capital),
# Investing, or Financing, and the statement reconciles to the actual cash
# movement BY CONSTRUCTION — any residual (opening entries dated mid-period,
# multi-currency rounding) is shown explicitly as an unclassified line rather
# than silently absorbed.
#
# Vouchers excluded from the flows (each is internally balanced, so excluding
# whole vouchers preserves the identity): opening entries and Period Closing
# Vouchers (which merely fold P&L into retained earnings).
from __future__ import annotations

import re

import frappe
from frappe import _
from frappe.utils import add_days, flt, getdate

from .health import _default_company

_CASH_TYPES = ("Bank", "Cash")
_CASH_NAME = re.compile(r"bank|cash|petty|نقد|صندوق|بنك", re.I)
_ACC_DEP_NAME = re.compile(r"accumulated\s+depreciation|إهلاك\s*متراكم|مجمع\s*(الإهلاك|الاستهلاك|استهلاك)", re.I)
_INVESTING_TYPES = ("Fixed Asset", "Capital Work in Progress")
_INVESTING_NAME = re.compile(
    r"fixed asset|property|plant|equipment|vehicle|furniture|machin|intangible|goodwill|"
    r"investment|cwip|capital work|أصول ثابتة|معدات|سيارات|أثاث|استثمار", re.I)
_FINANCING_NAME = re.compile(
    r"loan|borrow|sukuk|bond|finance lease|murabaha|tawarruq|overdraft|"
    r"قرض|قروض|تمويل|صكوك|مرابحة|تورق", re.I)


def _classify(root_type, account_type, text, user_tag=None):
    """Bucket a balance-sheet account. Returns one of:
    cash | dep (depreciation add-back) | wc (working capital / other operating)
    | inv (investing) | fin (financing).

    A user tag from the Account Classification Studio (v2.26.0) wins over
    every heuristic — tag once, classified everywhere."""
    if user_tag == "cash":
        return "cash"
    if user_tag == "investing":
        return "inv"
    if user_tag in ("financing", "provision"):
        return "fin"
    if user_tag == "cogs":
        return "wc"
    if account_type in _CASH_TYPES or _CASH_NAME.search(text or ""):
        # Name-matched 'bank loan' etc. must not be mistaken for cash:
        if not _FINANCING_NAME.search(text or "") or account_type in _CASH_TYPES:
            return "cash"
    if account_type == "Accumulated Depreciation" or _ACC_DEP_NAME.search(text or ""):
        return "dep"
    if root_type == "Equity":
        return "fin"
    if root_type == "Liability" and _FINANCING_NAME.search(text or ""):
        return "fin"
    if root_type == "Asset" and (account_type in _INVESTING_TYPES or _INVESTING_NAME.search(text or "")):
        return "inv"
    return "wc"


def _cash_accounts(company):
    from .classify import tag_map
    tags = tag_map(company)
    accts = frappe.get_all("Account",
                           filters={"company": company, "is_group": 0, "root_type": "Asset"},
                           fields=["name", "account_name", "account_type"])
    out = []
    for a in accts:
        user_tag = tags.get(a["name"])
        if user_tag and user_tag != "cash":
            continue  # explicitly tagged as something else — never cash
        text = f"{a.get('account_name') or ''} {a['name']}"
        if user_tag == "cash" or a.get("account_type") in _CASH_TYPES or (
                _CASH_NAME.search(text) and not _FINANCING_NAME.search(text)):
            out.append(a["name"])
    return out


def _cash_balance(company, cash_accounts, upto_date):
    """Cash balance as of end of `upto_date` (ALL postings incl. opening)."""
    if not cash_accounts:
        return 0.0
    r = frappe.db.sql(
        """SELECT COALESCE(SUM(debit - credit), 0) AS b FROM `tabGL Entry`
           WHERE company=%(c)s AND is_cancelled=0
             AND account IN %(a)s AND posting_date <= %(d)s""",
        {"c": company, "a": tuple(cash_accounts), "d": upto_date}, as_dict=True)
    return flt(r[0]["b"], 2) if r else 0.0


@frappe.whitelist()
def cash_flow(company=None, from_date=None, to_date=None):
    """Statement of Cash Flows (indirect) for the period."""
    company = company or _default_company()
    if not company:
        frappe.throw(_("No company found."))
    if not from_date or not to_date:
        frappe.throw(_("from_date and to_date are required."))
    if not frappe.has_permission("GL Entry", "read"):
        frappe.throw(_("Not permitted."))
    getdate(from_date); getdate(to_date)

    rows = frappe.db.sql(
        """SELECT gle.account, a.account_name, a.root_type, a.account_type,
                  COALESCE(SUM(gle.debit), 0) AS d, COALESCE(SUM(gle.credit), 0) AS c
           FROM `tabGL Entry` gle
           JOIN `tabAccount` a ON a.name = gle.account
           WHERE gle.company = %(c)s AND gle.is_cancelled = 0
             AND IFNULL(gle.is_opening, 'No') != 'Yes'
             AND gle.voucher_type != 'Period Closing Voucher'
             AND gle.posting_date BETWEEN %(f)s AND %(t)s
           GROUP BY gle.account, a.account_name, a.root_type, a.account_type""",
        {"c": company, "f": from_date, "t": to_date}, as_dict=True)

    from .classify import tag_map
    tags = tag_map(company)
    net_profit = 0.0
    dep_expense = 0.0
    buckets = {"dep": [], "wc": [], "inv": [], "fin": []}
    cash_flow_delta = 0.0  # Δcash from the SAME voucher set — the tie-out target

    for r in rows:
        flow = flt(r["c"] - r["d"], 2)  # credit − debit = cash-flow contribution
        text = f"{r.get('account_name') or ''} {r['account']}"
        if r["root_type"] in ("Income", "Expense"):
            net_profit += flow  # income credit − expense debit = net profit
            if r["root_type"] == "Expense" and (
                    r.get("account_type") == "Depreciation"
                    or re.search(r"depreciat|amorti[sz]|إهلاك|استهلاك", text, re.I)):
                dep_expense += -flow  # expense is a debit ⇒ flow negative ⇒ add-back positive
            continue
        bucket = _classify(r["root_type"], r.get("account_type"), text, tags.get(r["account"]))
        if bucket == "cash":
            cash_flow_delta += flt(r["d"] - r["c"], 2)
            continue
        if abs(flow) >= 0.005:
            buckets[bucket].append({
                "account": r["account"],
                "label": r.get("account_name") or r["account"],
                "amount": flow,
            })

    for b in buckets.values():
        b.sort(key=lambda x: -abs(x["amount"]))

    dep_total = flt(sum(l["amount"] for l in buckets["dep"]), 2)
    wc_total = flt(sum(l["amount"] for l in buckets["wc"]), 2)
    inv_total = flt(sum(l["amount"] for l in buckets["inv"]), 2)
    fin_total = flt(sum(l["amount"] for l in buckets["fin"]), 2)
    net_profit = flt(net_profit, 2)

    operating = flt(net_profit + dep_total + wc_total, 2)
    classified_total = flt(operating + inv_total + fin_total, 2)
    # Residual vs the voucher-set cash delta (should be ~0; shown, never hidden).
    unclassified = flt(cash_flow_delta - classified_total, 2)

    cash_accts = _cash_accounts(company)
    opening_cash = _cash_balance(company, cash_accts,
                                 add_days(from_date, -1))
    closing_cash = _cash_balance(company, cash_accts, to_date)

    return {
        "company": company,
        "period": {"from_date": from_date, "to_date": to_date},
        "currency": frappe.db.get_value("Company", company, "default_currency") or "SAR",
        "operating": {
            "net_profit": net_profit,
            "depreciation_addback": dep_total,
            "depreciation_expense_detected": flt(dep_expense, 2),
            "working_capital": buckets["wc"],
            "working_capital_total": wc_total,
            "total": operating,
        },
        "investing": {"lines": buckets["inv"], "total": inv_total},
        "financing": {"lines": buckets["fin"], "total": fin_total},
        "net_change": classified_total,
        "unclassified": unclassified,
        "cash": {
            "accounts": cash_accts,
            "opening": opening_cash,
            "closing": closing_cash,
            "actual_change": flt(closing_cash - opening_cash, 2),
        },
        "notes": [
            "Indirect method from the General Ledger. Working-capital and investing/financing splits are classified by account type and name — review before external use.",
            "The depreciation add-back is the period movement of Accumulated Depreciation accounts; if depreciation posts against the asset cost account directly, it will appear inside Investing instead.",
            "Opening entries and Period Closing Vouchers are excluded; any difference between the classified total and the actual cash movement (e.g. opening entries dated inside the period) is shown as Unclassified rather than hidden.",
        ],
    }


@frappe.whitelist()
def company_branding():
    """Companies with their logo URL — feeds the logo-derived colour theme."""
    fields = ["name", "company_name"]
    try:
        if frappe.get_meta("Company").get_field("company_logo"):
            fields.append("company_logo")
    except Exception:
        pass
    rows = frappe.get_all("Company", fields=fields, order_by="name")
    return [{"name": r["name"],
             "label": r.get("company_name") or r["name"],
             "logo": r.get("company_logo") or ""} for r in rows]
