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

import datetime
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
# Internal transfers — surfaced, never silently vanished. classify_voucher_leg
# already excludes a transfer leg from any binding's Actual; this section is
# what lets a user actually SEE the transfers that got excluded, per the
# same "never absorbed silently" principle as the reconciliation residual.
# ─────────────────────────────────────────────────────────────────────────

def build_transfer_log(
    raw_transfer_rows: list[dict],
    fy_start_month: int,
    months: list[int],
) -> list[dict]:
    """Pure. raw_transfer_rows: one row per (voucher, cash-account leg) where
    that leg was classified as a transfer — each a dict with voucher_type,
    voucher_no, account, posting_date, debit, credit. Groups the two (or
    more, if a transfer somehow touches more than two cash accounts) legs of
    each voucher into one entry: from_account (the credit/source leg),
    to_account (the debit/destination leg), amount moved, and — the KSA
    case — a fee, if the voucher's total cash legs don't net to zero (the
    destination received less than the source sent)."""
    by_voucher: dict[tuple[str, str], list[dict]] = {}
    for row in raw_transfer_rows:
        key = (row["voucher_type"], row["voucher_no"])
        by_voucher.setdefault(key, []).append(row)

    log = []
    for (vtype, vno), legs in by_voucher.items():
        credit_legs = [l for l in legs if flt(l.get("credit")) > flt(l.get("debit"))]
        debit_legs = [l for l in legs if flt(l.get("debit")) > flt(l.get("credit"))]
        sent = flt(sum(flt(l["credit"]) - flt(l["debit"]) for l in credit_legs), 2)
        received = flt(sum(flt(l["debit"]) - flt(l["credit"]) for l in debit_legs), 2)
        fee = flt(sent - received, 2)
        pd = legs[0].get("posting_date")
        cal_month = pd.month if hasattr(pd, "month") else getdate(pd).month
        pos = calendar_to_fy_position(cal_month, fy_start_month)
        log.append({
            "voucher_type": vtype, "voucher_no": vno,
            "from_accounts": [l["account"] for l in credit_legs],
            "to_accounts": [l["account"] for l in debit_legs],
            "amount_sent": sent, "amount_received": received, "fee": fee,
            "fy_position": pos if pos in months else None,
        })
    return log


def bank_breakdown_monthly(
    gl_rows: list[dict],
    direction_mode: str,
    bank_leg_vouchers: set[tuple[str, str]],
    transfer_vouchers: set[tuple[str, str]],
    override_vouchers: set[tuple[str, str]],
    voucher_cash_legs: dict[tuple[str, str], list[str]],
    fy_start_month: int,
    months: list[int],
) -> dict[int, dict[str, float]]:
    """Pure. Same filtering as attribute_binding_monthly, but keyed by
    (fy_position -> {bank_account: amount}) instead of a single summed
    figure — this is what backs "click a number, see which bank accounts
    fed it." voucher_cash_legs: {(voucher_type, voucher_no): [bank account
    names that were this voucher's cash leg]} — a voucher can have more than
    one (a split payment across two banks), in which case its amount is
    attributed evenly across them; exact per-bank amounts aren't knowable
    from the non-cash leg's row alone when the cash side is split."""
    out: dict[int, dict[str, float]] = {m: {} for m in months}
    for row in gl_rows:
        key = (row.get("voucher_type"), row.get("voucher_no"))
        if key in transfer_vouchers or key in override_vouchers or key not in bank_leg_vouchers:
            continue
        debit = flt(row.get("debit"))
        credit = flt(row.get("credit"))
        net = debit - credit
        if direction_mode == "Debit Only":
            if debit <= credit:
                continue
            amt = net
        elif direction_mode == "Credit Only":
            if credit <= debit:
                continue
            amt = -net
        else:
            amt = net
        pd = row.get("posting_date")
        cal_month = pd.month if hasattr(pd, "month") else getdate(pd).month
        pos = calendar_to_fy_position(cal_month, fy_start_month)
        if pos not in out:
            continue
        banks = voucher_cash_legs.get(key) or ["(unattributed)"]
        share = flt(amt / len(banks), 2)
        for b in banks:
            out[pos][b] = flt(out[pos].get(b, 0.0) + share, 2)
    return out


# ─────────────────────────────────────────────────────────────────────────
# DB-facing wrappers — thin, not independently unit-tested (same convention
# as the rest of this app: the pure functions above carry the test burden).
# ─────────────────────────────────────────────────────────────────────────

def parse_fy_start_month(year_start_date) -> int:
    """Pure. Given a Company.year_start_date value (a date/datetime object,
    an ISO string, or None/missing), extract the calendar month (1-12) the
    company's fiscal year starts on, defaulting to January if missing or
    unparseable.

    This is the parsing half of resolve_company_fy_start_month below, split
    out specifically so it's testable without a DB — the whole-function
    version (querying Company.year_start_date) is what actually shipped
    wrong in v2.86.0/v2.86.1: an earlier version queried a `company` column
    on the Fiscal Year doctype that doesn't exist, and raised
    'Unknown column company in WHERE' the first time it ran against a real
    site. The unit suite never caught it because nothing in
    tests/test_cash_flow_forecast_engine.py touched frappe.db.get_value at
    all — every test used the pure functions, which is exactly why THIS
    function exists now: so the part of this logic that can be pure, is."""
    if not year_start_date:
        return 1
    try:
        if isinstance(year_start_date, str):
            year_start_date = datetime.date.fromisoformat(year_start_date[:10])
        m = int(year_start_date.month)
        return m if 1 <= m <= 12 else 1
    except Exception:
        return 1


def resolve_company_fy_start_month(company: str | None) -> int:
    """DB-facing wrapper — reads Company.year_start_date, the proven source
    of truth in this app (see utils/fiscal_year.py's own docstring). NOT a
    `company` filter on the Fiscal Year doctype, which has no such column
    (that was v2.86.0/v2.86.1's bug; fixed in v2.86.2 by copying this query
    shape from fiscal_year.py).

    v2.86.3 — copying the query wasn't enough; fiscal_year.py wraps it in
    try/except and this didn't, so when THIS query also hit a schema this
    site doesn't have (year_start_date isn't a column here either — the
    exact next error reported), it crashed the same way the first one did.
    fiscal_year.py's own get_company_fy_start_month silently falls back to
    January on the identical failure, which is why nothing else in the app
    visibly errors on this site — every other report has likely been
    treating every company as January-start this whole time, silently. That
    is worth this site's owner knowing about directly; it is not something
    to guess a fix for here. This function now matches that same fallback
    behaviour rather than being the one place that crashes instead of
    defaulting."""
    if not company:
        return 1
    try:
        return parse_fy_start_month(frappe.get_value("Company", company, "year_start_date"))
    except Exception:
        return 1


def resolve_cash_accounts(company: str | None, restrict_to: list[str] | None = None) -> list[str]:
    """This module's OWN definition of 'which accounts are cash' — not
    api/cashflow.py's _cash_accounts(). Two independent definitions is an
    accepted cost of full isolation (see module docstring); if the two ever
    need to agree exactly, that is a product decision to revisit, not a bug
    to silently patch around here.

    `restrict_to`: when given, narrows to this specific subset (still
    validated against the real Bank/Cash account list, so a stale or
    mistyped name in a saved filter can't silently expand scope to
    'everything' by matching nothing and falling through) — the "By default,
    select all; when the user wants, they can see one particular bank"
    behaviour."""
    filters = {"account_type": ["in", ["Bank", "Cash"]], "is_group": 0}
    if company:
        filters["company"] = company
    all_cash = frappe.get_all("Account", filters=filters, pluck="name")
    if restrict_to:
        wanted = set(restrict_to)
        return [a for a in all_cash if a in wanted]
    return all_cash


def list_bank_accounts_for_ui(company: str | None) -> list[dict]:
    """Account name + display label, for the bank-account multi-select.
    Kept separate from resolve_cash_accounts (which only ever needs bare
    names for filtering) so the UI-facing shape doesn't leak into the
    filtering logic."""
    filters = {"account_type": ["in", ["Bank", "Cash"]], "is_group": 0}
    if company:
        filters["company"] = company
    return frappe.get_all("Account", filters=filters,
                          fields=["name", "account_name", "account_type"],
                          order_by="account_type asc, account_name asc")


def classify_voucher_leg(account: str, other_leg_accounts: set[str], cash_accounts: list[str]) -> tuple[bool, bool]:
    """Pure — given one voucher's OTHER leg accounts (not including `account`
    itself), decide whether this leg (a) ever moved cash at all (either
    `account` IS itself a cash account, in which case this row is the cash
    leg by definition, or some OTHER leg hit a cash account), and (b) is
    itself part of an internal transfer between two cash accounts, not a
    real cash flow line item.

    Returns (is_bank_leg, is_transfer).

    Most lines bind to an Expense, Payable, Receivable, or other Liability
    account — not the bank account itself — so is_bank_leg usually depends
    on an OTHER leg being cash. But nothing stops a line from binding
    directly to a bank/cash account (e.g. a 'Cash on Hand movements' line),
    and for that row the cash movement IS this leg, regardless of what the
    other leg is — checking only the other legs would wrongly exclude it as
    a pure accrual with no bank leg at all.

    A transfer is any leg where `account` is a cash account AND at least one
    OTHER leg is also a cash account — not only when EVERY other leg is. A
    KSA inter-bank transfer routinely carries a third leg (the SARIE /
    transfer fee, posted to a Bank Charges expense account); requiring every
    other leg to be cash would miss that case, and the transfer money would
    then get counted twice — once leaving the source bank, once arriving at
    the destination — as if it were two unrelated real cash movements. The
    fee leg is untouched by this rule: when the fee account's OWN binding is
    classified, `account` (the fee account) isn't itself a cash account, so
    is_transfer is always False for it, and it's correctly counted as real
    spend if bound to a Bank Charges line."""
    cash_set = set(cash_accounts)
    account_is_cash = account in cash_set
    is_bank_leg = account_is_cash or bool(other_leg_accounts & cash_set)
    is_transfer = account_is_cash and bool(other_leg_accounts & cash_set)
    return is_bank_leg, is_transfer


def fetch_all_transfer_legs(company: str | None, from_date, to_date, cash_accounts: list[str]) -> list[dict]:
    """Every GL Entry leg, across the whole cash-accounts set, belonging to
    a voucher that touches 2+ DISTINCT cash accounts — i.e. every leg of
    every internal transfer in the period, source and destination together,
    for build_transfer_log to pair up. One query for the whole run, not
    per-binding, unlike fetch_bank_leg_and_transfer_vouchers below."""
    if not cash_accounts:
        return []
    filters = {"account": ["in", cash_accounts], "posting_date": ["between", [from_date, to_date]],
               "is_cancelled": 0}
    if company:
        filters["company"] = company
    rows = frappe.get_all("GL Entry", filters=filters,
                          fields=["voucher_type", "voucher_no", "account", "posting_date", "debit", "credit"],
                          limit_page_length=0)
    by_voucher: dict[tuple[str, str], list[dict]] = {}
    for r in rows:
        by_voucher.setdefault((r["voucher_type"], r["voucher_no"]), []).append(r)
    out = []
    for legs in by_voucher.values():
        if len({l["account"] for l in legs}) >= 2:
            out.extend(legs)
    return out


def fetch_voucher_cash_legs(company: str | None, from_date, to_date,
                            cash_accounts: list[str]) -> dict[tuple[str, str], list[str]]:
    """{(voucher_type, voucher_no): [cash account names that are legs of
    this voucher]} for the whole period — one query, reused for every
    binding's bank-breakdown rather than re-fetched per binding."""
    if not cash_accounts:
        return {}
    filters = {"account": ["in", cash_accounts], "posting_date": ["between", [from_date, to_date]],
               "is_cancelled": 0}
    if company:
        filters["company"] = company
    rows = frappe.get_all("GL Entry", filters=filters,
                          fields=["voucher_type", "voucher_no", "account"], limit_page_length=0)
    out: dict[tuple[str, str], list[str]] = {}
    for r in rows:
        key = (r["voucher_type"], r["voucher_no"])
        bucket = out.setdefault(key, [])
        if r["account"] not in bucket:
            bucket.append(r["account"])
    return out


def fetch_bank_leg_and_transfer_vouchers(
    account: str, company: str | None, from_date, to_date, cash_accounts: list[str]
) -> tuple[set[tuple[str, str]], set[tuple[str, str]]]:
    """For every voucher touching `account` in the period, find whether any
    OTHER leg of that same voucher hit a cash account (-> bank_leg), and
    whether `account` itself is also a cash account with at least one other
    cash leg (-> transfer). Classification itself is `classify_voucher_leg`
    above; this function only fetches."""
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
    for v in vouchers:
        key = (v["voucher_type"], v["voucher_no"])
        legs = frappe.get_all(
            "GL Entry",
            filters={"voucher_type": v["voucher_type"], "voucher_no": v["voucher_no"], "is_cancelled": 0},
            fields=["account"], limit_page_length=0,
        )
        other_leg_accounts = {l["account"] for l in legs if l["account"] != account}
        is_bank_leg, is_transfer = classify_voucher_leg(account, other_leg_accounts, cash_accounts)
        if is_bank_leg:
            bank_leg.add(key)
        if is_transfer:
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
