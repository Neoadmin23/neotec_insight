"""ERPNext engine (v3.9.0): run ERPNext's own Trial Balance and return it in
Insight's result shape, so the figures are ERPNext's to the halala (same code,
same rounding, Account Closing Balance openings) while the screen stays Insight's.

Used when the selection fits ERPNext's report (one cost centre / project at
most, no root-type subset); otherwise Insight's engine runs (see `fits`)."""
from __future__ import annotations

import frappe
from frappe.utils import flt


def _single(v):
    if isinstance(v, (list, tuple)):
        return v[0] if len(v) == 1 else (None if not v else False)
    return v or None


def fits(cost_center=None, project=None, root_types=None) -> tuple[bool, str]:
    """Can ERPNext's report answer this selection? (ok, reason when not)"""
    if _single(cost_center) is False:
        return False, "several cost centres"
    if _single(project) is False:
        return False, "several projects"
    if root_types and set(root_types) != {"Asset", "Liability", "Equity", "Income", "Expense"}:
        return False, "a subset of root types"
    try:
        from erpnext.accounts.report.trial_balance import trial_balance  # noqa: F401
    except Exception:
        return False, "ERPNext's report is not available"
    return True, ""


def _fiscal_year(fiscal_year_start, from_date):
    fy = frappe.db.get_value("Fiscal Year", {"year_start_date": fiscal_year_start}, "name")
    if not fy:
        d = from_date or fiscal_year_start
        rows = frappe.db.sql("select name from `tabFiscal Year` where %s between year_start_date and year_end_date "
                             "order by year_start_date desc limit 1", (d,))
        fy = rows[0][0] if rows else None
    return fy


def trial_balance(*, company, fiscal_year_start, as_of_date, from_date=None, cost_center=None, project=None,
                  finance_book=None, dimension_filters=None, show_group_accounts=True, show_zero_values=False,
                  presentation_currency=None, with_pcv_opening=True, pcv_current=True, show_unclosed_pl=False,
                  include_default_fb=True) -> dict:
    from erpnext.accounts.report.trial_balance.trial_balance import execute

    from neotec_insight.neotec_insight.utils.balance_execution import _load_chart_of_accounts
    filters = frappe._dict(
        company=company, fiscal_year=_fiscal_year(fiscal_year_start, from_date),
        from_date=from_date or fiscal_year_start, to_date=as_of_date,
        cost_center=_single(cost_center), project=_single(project), finance_book=finance_book or None,
        presentation_currency=presentation_currency or None,
        with_period_closing_entry_for_opening=int(bool(with_pcv_opening)),
        with_period_closing_entry_for_current_period=int(bool(pcv_current)),
        show_unclosed_fy_pl_balances=int(bool(show_unclosed_pl)),
        include_default_book_entries=int(bool(include_default_fb)),
        show_zero_values=int(bool(show_zero_values)), show_net_values=1)
    for k, v in (dimension_filters or {}).items():
        if v not in (None, "", []):
            filters[k] = v
    out = execute(filters)
    data = out[1] if isinstance(out, (list, tuple)) else []
    coa = {a["name"]: a for a in _load_chart_of_accounts(company)}
    accounts, totals = [], {}
    for r in data or []:
        if not r:
            continue
        acc = r.get("account")
        nums = {"opening_debit": flt(r.get("opening_debit"), 6), "opening_credit": flt(r.get("opening_credit"), 6),
                "period_debit": flt(r.get("debit"), 6), "period_credit": flt(r.get("credit"), 6),
                "closing_debit": flt(r.get("closing_debit"), 6), "closing_credit": flt(r.get("closing_credit"), 6)}
        a = coa.get(acc)
        if not a:                                  # ERPNext's "'Total'" row
            if str(acc).strip("'\" ") == "Total":
                totals = nums
            continue
        if a.get("is_group") and not show_group_accounts:
            continue
        accounts.append({"name": acc, "label": a.get("account_name") or acc, "code": a.get("account_number") or "",
                         "root_type": a.get("root_type") or "", "account_type": a.get("account_type") or "",
                         "is_group": int(a.get("is_group") or 0), "parent": a.get("parent_account"),
                         "lft": a.get("lft"), "rgt": a.get("rgt"), "depth": int(r.get("indent") or 0),
                         "has_parties": (a.get("account_type") in ("Receivable", "Payable")), **nums})
    # v3.9.2 — ERPNext's Total row adds each side gross; its screen shows opening and
    # closing as the sum of the netted top-level accounts. Use the top-level rows
    # (net per account) for those, and ERPNext's own figures for the period.
    tops = [a for a in accounts if not a.get("parent") or a["parent"] not in coa]
    if tops and show_group_accounts:
        for side in ("opening", "closing"):
            net = sum(a[f"{side}_debit"] - a[f"{side}_credit"] for a in tops)
            # v3.9.3 — full precision (6 decimals); the screen rounds for display, as ERPNext
            nets = [round(a[f"{side}_debit"] - a[f"{side}_credit"], 6) for a in tops]
            dr = round(sum(n for n in nets if n > 0), 6)
            cr = round(sum(-n for n in nets if n < 0), 6)
            totals[f"{side}_debit"], totals[f"{side}_credit"] = dr, cr
            _ = net

    cur = frappe.db.get_value("Company", company, "default_currency")
    return {"accounts": accounts, "totals": totals, "engine": "erpnext",
            "currency": {"company_currency": cur, "presentation_currency": presentation_currency or cur,
                         "conversion_rate": 1.0, "rate_missing": False, "as_of_date": as_of_date}}
