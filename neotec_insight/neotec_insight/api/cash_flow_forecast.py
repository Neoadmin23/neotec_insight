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

import base64
import json

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, get_first_day, get_last_day, add_days

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
    list_binding_transactions,
    reconciliation_residual,
    resolve_cash_accounts,
    resolve_company_fy_start_month,
)
from neotec_insight.neotec_insight.utils.cash_flow_import import (
    match_lines_to_rows,
    parse_classified_history_sheet,
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
    # Same failure mode as mine_rules' min_purity, but silent instead of a
    # crash — which is worse, not better. The frontend always sends an
    # explicit 0/1, which arrives here as the STRING "0" (this site does
    # not auto-cast whitelisted-method params to their type hint — see
    # v2.87.2). `if "0":` is True in Python (a non-empty string), so every
    # call showed inactive lines regardless of what the caller asked for,
    # with no error to ever surface it.
    filters = {} if cint(include_inactive) else {"is_active": 1}
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
# Historical import — bring in a workbook of already-classified transactions
# (the same shape a customer's manual Excel process produces) as Overrides,
# rather than re-classifying the same history one row at a time through the
# Classification Queue. Two steps, deliberately: preview never writes
# anything; commit writes only what preview already showed the user.
# ─────────────────────────────────────────────────────────────────────────

def _resolve_available_lines() -> list[dict]:
    return frappe.get_all("Insight Cash Flow Line", filters={"is_active": 1},
                          fields=["name", "label"], limit_page_length=0)


@frappe.whitelist()
def preview_classified_history_import(file_base64: str, sheet_name: str | None = None):
    """Parses and matches only — writes nothing. Returns matched/unmatched
    counts and, per unmatched category, how many rows it affects, so the
    user knows exactly which Lines to create (or which typo to fix) before
    committing anything."""
    _require_read()
    try:
        file_bytes = base64.b64decode(file_base64)
    except Exception:
        frappe.throw(_("file_base64 must be a base64-encoded .xlsx file."))
    parsed = parse_classified_history_sheet(file_bytes, sheet_name=sheet_name)
    match = match_lines_to_rows(parsed["rows"], _resolve_available_lines())

    # Already-overridden vouchers are counted separately from "matched" —
    # they have a Line, but importing them again would just hit the
    # doctype's own one-voucher-one-line uniqueness check and do nothing,
    # so the preview should say so up front rather than let the user think
    # committing will create that many new records.
    existing = {(o["voucher_type"], o["voucher_no"])
               for o in frappe.get_all("Insight Cash Flow Override",
                                       fields=["voucher_type", "voucher_no"], limit_page_length=0)}
    already_classified = sum(1 for r in match["matched"] if (r["voucher_type"], r["voucher_no"]) in existing)
    new_count = match["matched_count"] - already_classified

    return {
        "sheet_used": parsed["sheet_used"], "header_row": parsed["header_row"],
        "warnings": parsed["warnings"],
        "total_rows": len(parsed["rows"]),
        "matched_count": match["matched_count"],
        "already_classified_count": already_classified,
        "new_count": new_count,
        "unmatched_count": match["unmatched_count"],
        "unmatched_labels": match["unmatched_labels"],
    }


@frappe.whitelist()
def commit_classified_history_import(file_base64: str, sheet_name: str | None = None):
    """Writes an Insight Cash Flow Override per matched row whose voucher
    isn't already classified — same parse+match as preview, run again
    rather than trusting client-held state, so what gets written is always
    based on the current Line list, not a stale preview from a minute ago
    if a Line was renamed in between."""
    _require_write()
    try:
        file_bytes = base64.b64decode(file_base64)
    except Exception:
        frappe.throw(_("file_base64 must be a base64-encoded .xlsx file."))
    parsed = parse_classified_history_sheet(file_bytes, sheet_name=sheet_name)
    match = match_lines_to_rows(parsed["rows"], _resolve_available_lines())

    created = 0
    skipped_already_classified = 0
    errors: list[str] = []
    for row in match["matched"]:
        if frappe.db.exists("Insight Cash Flow Override",
                            {"voucher_type": row["voucher_type"], "voucher_no": row["voucher_no"]}):
            skipped_already_classified += 1
            continue
        try:
            frappe.get_doc({
                "doctype": "Insight Cash Flow Override",
                "line": row["line"], "voucher_type": row["voucher_type"], "voucher_no": row["voucher_no"],
                "note": f"Imported from historical workbook — original remarks: {row['remarks']}"[:500],
                "decision_kind": "Manual",
            }).insert()
            created += 1
        except Exception as e:
            if len(errors) < 60:
                errors.append(f"{row['voucher_type']} {row['voucher_no']}: {e}")

    return {
        "created": created,
        "skipped_already_classified": skipped_already_classified,
        "unmatched_count": match["unmatched_count"],
        "unmatched_labels": match["unmatched_labels"],
        "errors": errors,
    }


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



def _fy_date_range(fy: int, fy_start_month: int) -> tuple[str, str]:
    """Shared by run() and list_line_transactions() — extracted here (this
    is its 3rd call site across the two API files, counting the near-
    identical copy in api/cash_flow_classification.py) since duplicating a
    6-line date computation a third time is the actual risk this function
    closes, not a style preference. Not imported by cash_flow_classification.py
    or vice versa — both files independently need the same logic and
    importing between them would create the kind of cross-file coupling
    this feature's isolation was built to avoid; small enough duplication
    to accept once, not a third time."""
    if fy_start_month == 1:
        return f"{fy}-01-01", f"{fy}-12-31"
    from_date = f"{fy}-{fy_start_month:02d}-01"
    end_year, end_month = fy + 1, fy_start_month - 1
    return from_date, get_last_day(f"{end_year}-{end_month:02d}-01")


@frappe.whitelist()
def list_line_transactions(fiscal_year: int, line: str, month_index: int, company: str | None = None,
                           bank_accounts: str | list | None = None):
    """The individual transactions behind one line's Actual figure for one
    month — the app equivalent of a row in the customer's own Excel Data
    sheet, with an Open action per transaction attached by the frontend.

    month_index: 0-11, FY position — the SAME index the Statement view's
    month_labels array already uses, not a calendar month. The caller
    already has this from whichever cell was clicked; no conversion needed
    on either side."""
    _require_read()
    fy = int(fiscal_year)
    target_month = int(month_index)
    fy_start_month = resolve_company_fy_start_month(company)
    restrict = json.loads(bank_accounts) if isinstance(bank_accounts, str) else bank_accounts
    cash_accounts = resolve_cash_accounts(company, restrict_to=restrict or None)
    from_date, to_date = _fy_date_range(fy, fy_start_month)

    if not frappe.db.exists("Insight Cash Flow Line", line):
        frappe.throw(_("Unknown line: {0}").format(line))
    line_doc = frappe.get_doc("Insight Cash Flow Line", line)
    bindings = [
        {
            "account": b.account,
            "direction_mode": b.direction_mode,
            "cost_centers": [row.cost_center for row in (b.cost_centers or [])],
            "project": b.project, "party_type": b.party_type, "party": b.party,
        }
        for b in (line_doc.bindings or [])
    ]

    override_vouchers = {
        (o["voucher_type"], o["voucher_no"])
        for o in frappe.get_all("Insight Cash Flow Override", filters={"line": line},
                                fields=["voucher_type", "voucher_no"], limit_page_length=0)
    }

    transactions: list[dict] = []
    for b in bindings:
        gl_rows = fetch_binding_gl_rows(b, company, from_date, to_date)
        bank_leg, transfer = fetch_bank_leg_and_transfer_vouchers(
            b["account"], company, from_date, to_date, cash_accounts)
        rows = list_binding_transactions(
            gl_rows, b.get("direction_mode") or "Net", bank_leg, transfer,
            override_vouchers, fy_start_month, target_month)
        transactions.extend(rows)

    # Overrides claiming this line, in the same target month — a manually
    # tagged voucher never went through a binding's own gl_rows fetch, so
    # it needs its own pass here to appear in the drill-down at all. An
    # override tags the WHOLE voucher, not one specific leg the way a
    # binding does, so there's no single "the" account to prefer — this
    # takes whichever leg GL Entry returns first per voucher, same
    # simplification the pre-v2.87.6 enrichment step already made.
    for o in frappe.get_all("Insight Cash Flow Override", filters={"line": line},
                            fields=["voucher_type", "voucher_no"], limit_page_length=0):
        gl = frappe.get_all(
            "GL Entry",
            filters={"voucher_type": o["voucher_type"], "voucher_no": o["voucher_no"], "is_cancelled": 0},
            fields=["posting_date", "debit", "credit", "account", "cost_center",
                   "project", "remarks", "against"],
            limit_page_length=0)
        for g in gl:
            pd = g["posting_date"]
            cal_month = pd.month if hasattr(pd, "month") else getdate(pd).month
            pos = calendar_to_fy_position(cal_month, fy_start_month)
            if pos == target_month:
                transactions.append({
                    "voucher_type": o["voucher_type"], "voucher_no": o["voucher_no"],
                    "posting_date": str(pd), "amount": flt(g["debit"]) - flt(g["credit"]),
                    "account": g.get("account") or "", "cost_center": g.get("cost_center") or "",
                    "project": g.get("project") or "", "remarks": g.get("remarks") or "",
                    "against_account": g.get("against") or "",
                })

    # v2.87.6 — binding-sourced rows already carry account/cost_center/
    # remarks/against_account from list_binding_transactions (which gets
    # them from fetch_binding_gl_rows' now-widened field list), and
    # override-sourced rows get them from the widened query directly above
    # — the separate per-voucher "enrichment" re-query this used to need is
    # gone. It was also a real, if minor, correctness risk: for a
    # multi-leg voucher it could return whichever leg GL Entry happened to
    # list first, not necessarily the one this specific binding matched.
    transactions.sort(key=lambda t: t["posting_date"])
    return {"line": line, "month_index": target_month, "transactions": transactions,
            "total": flt(sum(t["amount"] for t in transactions), 2)}


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

    from_date, to_date = _fy_date_range(fy, fy_start_month)

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
