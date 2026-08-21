"""Cash Flow Forecast API (v2.86.0).

Fully separate from the P&L/report engine, by design — see
Cash_Flow_Phase2_Spec.md. This module does not import anything from
api/report.py, utils/execution.py, utils/allocation.py, or
utils/fiscal_year.py. The one exception, stated plainly rather than
smuggled in: `utils/cash_flow_forecast.py` is this feature's OWN engine
module, imported here and nowhere else in the app.

Three screens this backs:
  - Line Setup: CRUD on Insight Cash Flow Line (+ its Bindings child table)
    and Insight Cash Flow Override.
  - Budget grid: bulk load/save of Insight Cash Flow Budget, same "blank vs
    zero" contract as the allocation Budget grid, reimplemented standalone.
  - Statement: `run()` — the monthly Actual (via the engine's Tier 1 + Tier 2
    attribution), Budget, balance rollforward, and reconciliation residual.
"""

from __future__ import annotations

import json

import frappe
from frappe import _
from frappe.utils import flt, getdate, get_first_day, get_last_day, add_days

from neotec_insight.neotec_insight.utils.cash_flow_forecast import (
    attribute_binding_monthly,
    attribute_overrides_monthly,
    balance_carry,
    bank_breakdown_monthly,
    build_transfer_log,
    calendar_to_fy_position,
    fetch_all_transfer_legs,
    fetch_bank_leg_and_transfer_vouchers,
    fetch_binding_gl_rows,
    fetch_voucher_cash_legs,
    fy_position_to_calendar,
    fy_position_to_calendar_year,
    list_bank_accounts_for_ui,
    reconciliation_residual,
    resolve_cash_accounts,
    resolve_company_fy_start_month,
)

MONTH_LABELS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _require_read():
    if not frappe.has_permission("Insight Cash Flow Line", "read"):
        frappe.throw(_("Not permitted."), frappe.PermissionError)


def _require_write():
    if not frappe.has_permission("Insight Cash Flow Line", "write"):
        frappe.throw(_("Not permitted."), frappe.PermissionError)


@frappe.whitelist()
def list_companies():
    """Backs the company dropdown — auto-select when there's exactly one,
    a real dropdown when there's more than one, per the customer's request
    rather than the free-text field this shipped with in v2.86.0."""
    _require_read()
    return frappe.get_all("Company", fields=["name", "default_currency"],
                          order_by="name asc", limit_page_length=0)


@frappe.whitelist()
def list_bank_accounts(company: str | None = None):
    """Backs the bank-account multi-select. Default is 'select all' — this
    endpoint just lists what's available; run() only narrows when
    bank_accounts is explicitly passed."""
    _require_read()
    return list_bank_accounts_for_ui(company)


# ─────────────────────────────────────────────────────────────────────────
# Line Setup
# ─────────────────────────────────────────────────────────────────────────

@frappe.whitelist()
def list_lines(include_inactive: bool = False):
    _require_read()
    filters = {} if include_inactive else {"is_active": 1}
    names = frappe.get_all("Insight Cash Flow Line", filters=filters,
                           order_by="section asc, sort_key asc", pluck="name")
    return [frappe.get_doc("Insight Cash Flow Line", n).as_dict() for n in names]


_LINE_EDITABLE_FIELDS = ["label", "direction", "section", "sort_key", "is_active",
                        "dimension_field", "description", "bindings"]


@frappe.whitelist()
def save_line(line: str | dict):
    """Only ever writes the fields a person can actually edit on this
    screen — never `doc.update(data)` with the whole payload. The frontend's
    `editing` state originates from list_lines()'s doc.as_dict(), which
    includes every metadata field (modified, owner, creation, docstatus…),
    and every edit afterwards just spreads that same object. A blind
    `.update(data)` overwrites the freshly-fetched doc's real `modified`
    with whatever stale value was sitting in that state — Frappe's own
    optimistic-lock check then correctly rejects the save as a conflict,
    even when there wasn't really one: 'Document has been modified after
    you have opened it,' on a save that's the very first attempt in that
    session. Restricting to a named field whitelist closes this at the
    source rather than working around the symptom."""
    _require_write()
    data = json.loads(line) if isinstance(line, str) else line
    name = data.get("name")
    if name and frappe.db.exists("Insight Cash Flow Line", name):
        doc = frappe.get_doc("Insight Cash Flow Line", name)
    else:
        doc = frappe.new_doc("Insight Cash Flow Line")
    for f in _LINE_EDITABLE_FIELDS:
        if f in data:
            doc.set(f, data[f])
    doc.save()
    return doc.as_dict()


@frappe.whitelist()
def delete_line(name: str):
    _require_write()
    if frappe.db.exists("Insight Cash Flow Budget", {"line": name}):
        frappe.throw(_("{0} has budget entries against it. Deactivate it instead of deleting, "
                       "so past runs keep their history.").format(name))
    frappe.delete_doc("Insight Cash Flow Line", name)
    return {"ok": True}


@frappe.whitelist()
def list_overrides(line: str | None = None):
    _require_read()
    filters = {"line": line} if line else {}
    return frappe.get_all("Insight Cash Flow Override", filters=filters,
                          fields=["name", "line", "voucher_type", "voucher_no", "note",
                                  "created_by_user", "created_on"],
                          order_by="created_on desc", limit_page_length=0)


@frappe.whitelist()
def save_override(line: str, voucher_type: str, voucher_no: str, note: str):
    _require_write()
    doc = frappe.get_doc({
        "doctype": "Insight Cash Flow Override",
        "line": line, "voucher_type": voucher_type, "voucher_no": voucher_no, "note": note,
    })
    doc.insert()
    return doc.as_dict()


@frappe.whitelist()
def delete_override(name: str):
    _require_write()
    frappe.delete_doc("Insight Cash Flow Override", name)
    return {"ok": True}


# ─────────────────────────────────────────────────────────────────────────
# Budget grid — same "blank vs zero" contract as the allocation grid,
# reimplemented standalone rather than shared.
# ─────────────────────────────────────────────────────────────────────────

@frappe.whitelist()
def get_budget_grid(fiscal_year: int, company: str | None = None):
    """Grid is keyed by calendar month (1-12) for display, same convention
    as the allocation Budget grid — the FY-position math only matters for
    which CALENDAR YEAR each cell's date actually falls in."""
    _require_read()
    fy = int(fiscal_year)
    fy_start_month = resolve_company_fy_start_month(company)
    year_for_month = {}
    for cal_m in range(1, 13):
        pos = calendar_to_fy_position(cal_m, fy_start_month)
        year_for_month[cal_m] = fy_position_to_calendar_year(pos, fy, fy_start_month)
    date_ranges = [f"{year_for_month[m]}-{m:02d}-01" for m in range(1, 13)]
    rows = frappe.get_all(
        "Insight Cash Flow Budget",
        filters={"period_month": ["in", date_ranges]},
        fields=["line", "period_month", "budget_amount"], limit_page_length=0,
    )
    grid: dict[str, dict[int, float]] = {}
    for r in rows:
        pm = getdate(r["period_month"])
        grid.setdefault(r["line"], {})[pm.month] = flt(r["budget_amount"])
    return grid


@frappe.whitelist()
def save_budget_grid(fiscal_year: int, cells: str | dict, company: str | None = None):
    """cells: {line: {month(1-12): amount}}. A month key simply absent from
    a line's dict is left untouched — blank, not zero, same contract as the
    allocation grid. Nothing is deleted here; only inserted or updated.

    `fiscal_year` is the calendar year the FY starts in (this app's
    convention). For an April-start company, cells for Jan/Feb/Mar belong to
    fiscal_year + 1 calendar-wise even though they're "months 10-12 of
    FY{fiscal_year}" — get this wrong and budget entered against "March"
    silently saves under the wrong year's March, invisible until someone
    runs the FOLLOWING year and finds March's budget already populated (or
    THIS year's March missing). Same bug shape as the original allocation-
    budget month-shift, one level up: right month, wrong year."""
    _require_write()
    fy = int(fiscal_year)
    fy_start_month = resolve_company_fy_start_month(company)
    data = json.loads(cells) if isinstance(cells, str) else cells
    for line, months in data.items():
        if not frappe.db.exists("Insight Cash Flow Line", line):
            frappe.throw(_("Unknown line: {0}").format(line))
        for m_str, amount in (months or {}).items():
            m = int(m_str)
            if m < 1 or m > 12:
                continue
            pos = calendar_to_fy_position(m, fy_start_month)
            year = fy_position_to_calendar_year(pos, fy, fy_start_month)
            period_month = f"{year}-{m:02d}-01"
            existing = frappe.db.exists(
                "Insight Cash Flow Budget", {"line": line, "period_month": period_month})
            if existing:
                frappe.db.set_value("Insight Cash Flow Budget", existing, "budget_amount", flt(amount))
            else:
                frappe.get_doc({
                    "doctype": "Insight Cash Flow Budget", "line": line,
                    "period_month": period_month, "budget_amount": flt(amount),
                }).insert()
    return {"ok": True}


# ─────────────────────────────────────────────────────────────────────────
# Run — the statement itself
# ─────────────────────────────────────────────────────────────────────────



@frappe.whitelist()
def run(fiscal_year: int, company: str | None = None, bank_accounts: str | list | None = None):
    """bank_accounts: optional list of specific Bank/Cash account names to
    restrict to — default (None or empty) is every cash account for the
    company, matching the "select all by default, narrow to one bank when
    the user wants" behaviour."""
    _require_read()
    fy = int(fiscal_year)
    fy_start_month = resolve_company_fy_start_month(company)
    months = list(range(12))
    restrict = json.loads(bank_accounts) if isinstance(bank_accounts, str) else bank_accounts
    cash_accounts = resolve_cash_accounts(company, restrict_to=restrict or None)

    lines = frappe.get_all("Insight Cash Flow Line", filters={"is_active": 1},
                           order_by="section asc, sort_key asc",
                           fields=["name", "label", "direction", "section"])
    budget = get_budget_grid(fy, company)

    # Overrides for the whole FY, joined against GL so their amounts are
    # real — fetched once, attributed per line below, and their voucher
    # keys withheld from every Tier 1 binding query so nothing double-counts.
    override_rows = frappe.get_all(
        "Insight Cash Flow Override",
        fields=["line", "voucher_type", "voucher_no"], limit_page_length=0)
    override_vouchers = {(r["voucher_type"], r["voucher_no"]) for r in override_rows}
    override_gl = []
    for r in override_rows:
        gl = frappe.get_all(
            "GL Entry",
            filters={"voucher_type": r["voucher_type"], "voucher_no": r["voucher_no"], "is_cancelled": 0},
            fields=["posting_date", "debit", "credit"], limit_page_length=0)
        for g in gl:
            override_gl.append({"line": r["line"], **g})
    override_monthly = attribute_overrides_monthly(override_gl, fy_start_month, months)

    if fy_start_month == 1:
        from_date, to_date = f"{fy}-01-01", f"{fy}-12-31"
    else:
        # Same bug class as the budget grid above: the FY's date range does
        # not sit inside one calendar year for a non-January-start company.
        from_date = f"{fy}-{fy_start_month:02d}-01"
        end_year, end_month = fy + 1, fy_start_month - 1
        to_date = get_last_day(f"{end_year}-{end_month:02d}-01")

    result_lines = []
    cash_in_total = {m: 0.0 for m in months}
    cash_out_total = {m: 0.0 for m in months}

    # Fetched once for the whole run, reused per binding below — which bank
    # account(s) are the cash leg of every voucher in the period. Backs the
    # "click a number, see which bank accounts fed it" drill-down.
    voucher_cash_legs = fetch_voucher_cash_legs(company, from_date, to_date, cash_accounts)

    for line in lines:
        # v2.86.6 — a flat frappe.get_all can't nest a Table MultiSelect
        # field's own child rows (cost_centers is itself a child table of
        # the binding row). frappe.get_doc() fetches the whole document
        # tree correctly, so that's what unpacks the multi-select here —
        # not a raw join this module would otherwise have to hand-write.
        line_doc = frappe.get_doc("Insight Cash Flow Line", line["name"])
        bindings = [
            {
                "account": b.account,
                "direction_mode": b.direction_mode,
                "cost_centers": [row.cost_center for row in (b.cost_centers or [])],
                "project": b.project,
                "party_type": b.party_type,
                "party": b.party,
            }
            for b in (line_doc.bindings or [])
        ]
        monthly = {m: 0.0 for m in months}
        by_bank: dict[int, dict[str, float]] = {m: {} for m in months}
        for b in bindings:
            gl_rows = fetch_binding_gl_rows(b, company, from_date, to_date)
            bank_leg, transfer = fetch_bank_leg_and_transfer_vouchers(
                b["account"], company, from_date, to_date, cash_accounts)
            per_binding = attribute_binding_monthly(
                gl_rows, b.get("direction_mode") or "Net", bank_leg, transfer,
                override_vouchers, fy_start_month, months)
            for m in months:
                monthly[m] = flt(monthly[m] + per_binding[m], 2)
            binding_bank_breakdown = bank_breakdown_monthly(
                gl_rows, b.get("direction_mode") or "Net", bank_leg, transfer,
                override_vouchers, voucher_cash_legs, fy_start_month, months)
            for m in months:
                for bank, amt in binding_bank_breakdown[m].items():
                    by_bank[m][bank] = flt(by_bank[m].get(bank, 0.0) + amt, 2)
        for m, amt in override_monthly.get(line["name"], {}).items():
            monthly[m] = flt(monthly[m] + amt, 2)

        line_budget = budget.get(line["name"], {})
        budget_monthly = {m: 0.0 for m in months}
        for m in months:
            cal_m = fy_position_to_calendar(m, fy_start_month)
            if cal_m in line_budget:
                budget_monthly[m] = line_budget[cal_m]

        result_lines.append({
            "line": line["name"], "label": line["label"], "direction": line["direction"],
            "section": line["section"], "actual": monthly, "budget": budget_monthly,
            "binding_count": len(bindings), "by_bank": by_bank,
        })
        target = cash_in_total if line["direction"] == "Cash In" else cash_out_total
        for m in months:
            target[m] = flt(target[m] + monthly[m], 2)

    # Opening balance + rollforward.
    settings = frappe.get_single("Insight Cash Flow Settings")
    if settings.opening_balance_mode == "Manual Override":
        opening = flt(settings.opening_balance_override)
    else:
        opening = _cash_balance_as_of(company, cash_accounts, get_first_day(from_date))
    rollforward = balance_carry(opening, cash_in_total, cash_out_total, months)

    # Reconciliation residual, per month — checked against the LEDGER's own
    # cash-account balance delta, independently of the classified lines.
    # Using rollforward's own closing-minus-opening here would be tautological
    # (balance_carry derives closing FROM cash_in_total/cash_out_total, so it
    # would equal them by construction and the residual would always read
    # zero, silently defeating the one check this whole feature exists to
    # provide) — the actual ledger balance at each month's boundary is a
    # second, independent number, fetched fresh.
    residuals = {}
    for m in months:
        cal_m = fy_position_to_calendar(m, fy_start_month)
        cal_year = fy_position_to_calendar_year(m, fy, fy_start_month)
        month_start_date = f"{cal_year}-{cal_m:02d}-01"
        month_end_date = get_last_day(month_start_date)
        bal_start = _cash_balance_as_of(company, cash_accounts, add_days(month_start_date, -1))
        bal_end = _cash_balance_as_of(company, cash_accounts, month_end_date)
        actual_delta = flt(bal_end - bal_start, 2)
        residuals[m] = reconciliation_residual(actual_delta, cash_in_total[m], cash_out_total[m])

    # Internal transfers — surfaced, not silently excluded. Answers "how do
    # we control internal bank transfers": here, visibly, with the fee (the
    # KSA SARIE case) broken out as its own figure rather than folded into
    # either the source or destination amount.
    transfer_legs = fetch_all_transfer_legs(company, from_date, to_date, cash_accounts)
    transfer_log = build_transfer_log(transfer_legs, fy_start_month, months)

    return {
        "fiscal_year": fy, "fy_start_month": fy_start_month, "company": company,
        "cash_accounts": cash_accounts,
        "lines": result_lines,
        "cash_in_total": cash_in_total, "cash_out_total": cash_out_total,
        "rollforward": rollforward, "residuals": residuals,
        "residual_tolerance_pct": flt(settings.residual_tolerance_pct or 0.5),
        "month_labels": [MONTH_LABELS[fy_position_to_calendar(m, fy_start_month) - 1] for m in months],
        "transfers": transfer_log,
    }


def _cash_balance_as_of(company: str | None, cash_accounts: list[str], upto_date) -> float:
    if not cash_accounts:
        return 0.0
    filters = {"account": ["in", cash_accounts], "posting_date": ["<=", upto_date], "is_cancelled": 0}
    if company:
        filters["company"] = company
    rows = frappe.get_all("GL Entry", filters=filters, fields=["debit", "credit"], limit_page_length=0)
    return flt(sum(flt(r["debit"]) - flt(r["credit"]) for r in rows), 2)
