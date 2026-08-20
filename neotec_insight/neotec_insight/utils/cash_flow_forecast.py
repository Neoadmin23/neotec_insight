"""Cash Flow Forecast — engine.

Deliberately standalone: no import from utils/execution.py, utils/allocation.py,
utils/fiscal_year.py, or api/report.py. That is a product decision (see
Cash_Flow_Phase2_Spec.md), not an oversight, and it has one real, accepted
cost worth stating up front rather than discovering later: the calendar-month
<-> FY-position conversion below is a SECOND implementation of the same idea
`utils/fiscal_year.py` already has. Two implementations of the same
conversion is exactly the kind of drift that shipped the v2.79.0 allocation-
budget bug — the mitigation here is the same one that caught that bug:
`tests/test_cash_flow_forecast_engine.py` runs the January-start AND
April-start fixtures against this module's own conversion, independently of
whatever `fiscal_year.py` does.

Three-tier attribution, in order:
  1. Direct binding (account [+ direction mode] [+ cost centre / project]
     [+ party]) — see `attribute_binding_monthly`.
  2. Manual override (Insight Cash Flow Override) — a specific voucher,
     tagged by hand, for the transactions Tier 1 genuinely cannot separate.
  3. Whatever neither of the above claims shows up in the reconciliation
     residual — never silently absorbed. See `reconciliation_residual`.
"""

from __future__ import annotations

from typing import Any

import frappe
from frappe.utils import flt, getdate


# ─────────────────────────────────────────────────────────────────────────
# Calendar month <-> FY position. Pure, no frappe calls, deliberately
# duplicated from fiscal_year.py — see module docstring.
# ─────────────────────────────────────────────────────────────────────────

def calendar_to_fy_position(cal_month: int, fy_start_month: int) -> int:
    """0-based FY position for a calendar month (1-12), given the company's
    FY-start month (1-12). fy_start_month=1 (January-start): calendar month 1
    -> position 0. fy_start_month=4 (April-start): calendar month 4 ->
    position 0, calendar month 3 -> position 11 (last month of that FY)."""
    return (cal_month - fy_start_month) % 12


def fy_position_to_calendar(fy_pos: int, fy_start_month: int) -> int:
    """Inverse of calendar_to_fy_position."""
    return ((fy_pos + fy_start_month - 1) % 12) + 1


def fy_position_to_calendar_year(fy_pos: int, fiscal_year: int, fy_start_month: int) -> int:
    """`fiscal_year` is the calendar year the FY STARTS in (this app's
    convention throughout, e.g. execution.py's _allocation_monthly). For a
    January-start company every position falls in that same calendar year.
    For an April-start company, positions 9-11 (Jan/Feb/Mar) fall in
    fiscal_year + 1 — get this wrong and a budget cell saved against
    "fiscal_year-03-01" silently lands in the wrong calendar year even
    though the FY-position math is correct. This is the same shape of bug
    as the original allocation-budget month-shift, one level up: right
    month, wrong year."""
    return fiscal_year + ((fy_start_month - 1 + fy_pos) // 12)


# ─────────────────────────────────────────────────────────────────────────
# Tier 1: direct binding attribution
# ─────────────────────────────────────────────────────────────────────────

def attribute_binding_monthly(
    gl_rows: list[dict],
    direction_mode: str,
    bank_leg_vouchers: set[tuple[str, str]],
    transfer_vouchers: set[tuple[str, str]],
    override_vouchers: set[tuple[str, str]],
    fy_start_month: int,
    months: list[int],
) -> dict[int, float]:
    """Pure aggregation — no DB access — so it's the one function this
    module's tests exercise directly, the same discipline as every other
    engine function in this app.

    gl_rows: raw rows already fetched for ONE binding's account (+ its
        cost_center/project/party filter, applied by the caller in SQL) —
        each a dict with voucher_type, voucher_no, posting_date, debit, credit.
    direction_mode: "Net" | "Debit Only" | "Credit Only" — Debit Only counts
        only rows where debit > credit (money leaving this account); Credit
        Only the reverse. This is what lets 'Riyadh Bank Loan settlement'
        and 'Financing from Riyadh Bank' read the same account as two
        non-overlapping lines instead of one net figure.
    bank_leg_vouchers: set of (voucher_type, voucher_no) that have at least
        one OTHER leg posting to a Bank/Cash account — a row whose voucher
        isn't in this set never moved cash (a pure accrual) and is skipped.
    transfer_vouchers: set of (voucher_type, voucher_no) where the bound
        account ITSELF is also a Bank/Cash account and the voucher's other
        leg is too — an internal transfer between two bank accounts, not a
        cash flow line item. Skipped even if bank_leg_vouchers also
        contains it.
    override_vouchers: set of (voucher_type, voucher_no) already claimed by
        a Tier 2 manual override — skipped here so an overridden voucher is
        never double-counted (once by whatever binding would otherwise have
        matched it, once by the override that explicitly claims it).

    Returns FY-position (0..11) -> amount, matching every other row kind's
    convention in this app.
    """
    monthly = {m: 0.0 for m in months}
    for row in gl_rows:
        key = (row.get("voucher_type"), row.get("voucher_no"))
        if key in transfer_vouchers or key in override_vouchers:
            continue
        if key not in bank_leg_vouchers:
            continue
        debit = flt(row.get("debit"))
        credit = flt(row.get("credit"))
        net = debit - credit
        if direction_mode == "Debit Only":
            if debit <= credit:
                continue
            amt = net  # positive: debit > credit in this branch
        elif direction_mode == "Credit Only":
            if credit <= debit:
                continue
            amt = -net  # positive: credit > debit in this branch, net is negative
        else:  # "Net"
            amt = net
        pd = row.get("posting_date")
        cal_month = pd.month if hasattr(pd, "month") else getdate(pd).month
        pos = calendar_to_fy_position(cal_month, fy_start_month)
        if pos in monthly:
            monthly[pos] = flt(monthly[pos] + amt, 2)
    return monthly


# ─────────────────────────────────────────────────────────────────────────
# Tier 2: manual override attribution
# ─────────────────────────────────────────────────────────────────────────

def attribute_overrides_monthly(
    override_rows: list[dict],
    fy_start_month: int,
    months: list[int],
) -> dict[str, dict[int, float]]:
    """Pure aggregation. override_rows: joined Override + GL rows, each a
    dict with line, posting_date, debit, credit. Returns {line: {fy_pos: amt}}.
    Sign convention: debit - credit, same as a Debit Only/Net binding — a
    line's direction (Cash Out/Cash In) determines how this is displayed,
    not how it's summed here."""
    out: dict[str, dict[int, float]] = {}
    for row in override_rows:
        line = row["line"]
        m = out.setdefault(line, {mo: 0.0 for mo in months})
        pd = row.get("posting_date")
        cal_month = pd.month if hasattr(pd, "month") else getdate(pd).month
        pos = calendar_to_fy_position(cal_month, fy_start_month)
        if pos in m:
            amt = flt(row.get("debit")) - flt(row.get("credit"))
            m[pos] = flt(m[pos] + amt, 2)
    return out


# ─────────────────────────────────────────────────────────────────────────
# Balance carry — the one genuinely new engine capability
# ─────────────────────────────────────────────────────────────────────────

def balance_carry(
    opening_amount: float,
    cash_in_monthly: dict[int, float],
    cash_out_monthly: dict[int, float],
    months: list[int],
) -> dict[int, dict[str, float]]:
    """Pure — no DB access. Seeds months[0]'s opening from `opening_amount`,
    then chains forward: each month's opening is the prior month's closing.
    `months` must be given in period order (FY position, ascending) — this
    function does not sort them, so a caller passing them out of order gets
    a silently wrong rollforward. That's deliberate: sorting defensively
    here would hide a real bug in the caller instead of surfacing it.

    Returns {fy_pos: {"opening": x, "closing": y}}."""
    out: dict[int, dict[str, float]] = {}
    running = flt(opening_amount)
    for m in months:
        opening = running
        closing = flt(opening + cash_in_monthly.get(m, 0.0) - cash_out_monthly.get(m, 0.0), 2)
        out[m] = {"opening": flt(opening, 2), "closing": closing}
        running = closing
    return out


# ─────────────────────────────────────────────────────────────────────────
# Reconciliation residual
# ─────────────────────────────────────────────────────────────────────────

def reconciliation_residual(
    actual_bank_delta: float,
    classified_cash_in_total: float,
    classified_cash_out_total: float,
) -> float:
    """The one number that proves (or disproves) every binding and override
    above is complete and non-overlapping for the period. Zero, within
    rounding, means every line item + the actual bank movement agree.
    Nonzero means something is unmapped, double-mapped, or a transfer was
    misclassified as a real cash flow line — never netted into an existing
    line, always its own visible figure."""
    return flt(actual_bank_delta - (classified_cash_in_total - classified_cash_out_total), 2)


# ─────────────────────────────────────────────────────────────────────────
# DB-facing wrappers — thin, not independently unit-tested (same convention
# as the rest of this app: the pure functions above carry the test burden).
# ─────────────────────────────────────────────────────────────────────────

def resolve_cash_accounts(company: str | None) -> list[str]:
    """This module's OWN definition of 'which accounts are cash' — not
    api/cashflow.py's _cash_accounts(). Two independent definitions is an
    accepted cost of full isolation (see module docstring); if the two ever
    need to agree exactly, that is a product decision to revisit, not a bug
    to silently patch around here."""
    filters = {"account_type": ["in", ["Bank", "Cash"]], "is_group": 0}
    if company:
        filters["company"] = company
    return frappe.get_all("Account", filters=filters, pluck="name")


def fetch_bank_leg_and_transfer_vouchers(
    account: str, company: str | None, from_date, to_date, cash_accounts: list[str]
) -> tuple[set[tuple[str, str]], set[tuple[str, str]]]:
    """For every voucher touching `account` in the period, find whether any
    OTHER leg of that same voucher hit a cash account (-> bank_leg), and
    whether `account` itself is also a cash account while every other leg is
    too (-> transfer, an internal move between bank accounts)."""
    if not cash_accounts:
        return set(), set()
    filters = {
        "account": account,
        "posting_date": ["between", [from_date, to_date]],
        "is_cancelled": 0,
    }
    if company:
        filters["company"] = company
    vouchers = frappe.get_all("GL Entry", filters=filters,
                              fields=["voucher_type", "voucher_no"], distinct=True,
                              limit_page_length=0)
    bank_leg: set[tuple[str, str]] = set()
    transfer: set[tuple[str, str]] = set()
    account_is_cash = account in cash_accounts
    for v in vouchers:
        key = (v["voucher_type"], v["voucher_no"])
        legs = frappe.get_all(
            "GL Entry",
            filters={"voucher_type": v["voucher_type"], "voucher_no": v["voucher_no"], "is_cancelled": 0},
            fields=["account"], limit_page_length=0,
        )
        other_leg_accounts = {l["account"] for l in legs if l["account"] != account}
        if other_leg_accounts & set(cash_accounts):
            bank_leg.add(key)
        if account_is_cash and other_leg_accounts and other_leg_accounts <= set(cash_accounts):
            transfer.add(key)
    return bank_leg, transfer


def fetch_binding_gl_rows(binding: dict, company: str | None, from_date, to_date) -> list[dict]:
    """GL Entries for one binding's account, filtered by its cost_center/
    project/party if set."""
    filters = {
        "account": binding["account"],
        "posting_date": ["between", [from_date, to_date]],
        "is_cancelled": 0,
    }
    if company:
        filters["company"] = company
    if binding.get("cost_center"):
        filters["cost_center"] = binding["cost_center"]
    if binding.get("project"):
        filters["project"] = binding["project"]
    if binding.get("party_type") and binding.get("party"):
        filters["party_type"] = binding["party_type"]
        filters["party"] = binding["party"]
    return frappe.get_all(
        "GL Entry", filters=filters,
        fields=["voucher_type", "voucher_no", "posting_date", "debit", "credit"],
        limit_page_length=0,
    )
