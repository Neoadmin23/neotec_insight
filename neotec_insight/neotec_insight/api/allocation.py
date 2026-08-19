"""Allocation API (v2.58.0).

Three jobs: list the rules, load and save the driver grid, and run the
allocation report. The grid is the "optional tool to make the entry of head
count and the sales count" — deliberately a bulk upsert of one rule-year at a
time, because that is how the numbers actually arrive (a column of head counts
for a month, or a whole year pasted from the old workbook).
"""

from __future__ import annotations

import json

import frappe
from frappe import _
from frappe.utils import flt

from neotec_insight.neotec_insight.utils.fiscal_year import calendar_month_for_fy_month
from neotec_insight.neotec_insight.utils.allocation import (
    compute,
    month_end,
    month_start,
)

MONTH_LABELS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _guard(write: bool = False):
    dt = "Insight Allocation Entry"
    if not frappe.has_permission(dt, "write" if write else "read"):
        frappe.throw(_("Not permitted."), frappe.PermissionError)




@frappe.whitelist()
def list_rules(company=None):
    _guard()
    filters = {"is_active": 1}
    if company:
        filters["company"] = company
    return frappe.get_all(
        "Insight Allocation Rule", filters=filters,
        fields=["name", "title", "company", "driver_label", "pool_mode", "pool_report",
                "pool_row_key", "pool_cost_center", "pool_flag", "pool_sign",
                "formula", "credit_back", "credit_cost_center",
                "driver_source", "count_as_of", "cc_from"],
        order_by="title asc", limit_page_length=0,
    )


@frappe.whitelist()
def list_cost_centers(company=None):
    """Leaf cost centres — the report spreads across these, horizontally."""
    _guard()
    filters = {"is_group": 0}
    if company:
        filters["company"] = company
    rows = frappe.get_all("Cost Center", filters=filters,
                          fields=["name", "cost_center_name"],
                          order_by="cost_center_name asc", limit_page_length=0)
    return [{"name": r["name"], "label": r.get("cost_center_name") or r["name"]} for r in rows]


@frappe.whitelist()
def get_grid(rule, year, company=None):
    """The driver grid for one rule and year: cost centres × months.

    Every cost centre that has any entry in the year is returned for all
    twelve months, so the editor renders a complete rectangle rather than a
    ragged one — a blank cell and a zero mean different things and the user
    needs to see which is which.
    """
    _guard()
    year = int(year)
    doc = frappe.get_doc("Insight Allocation Rule", rule)
    company = company or doc.company

    rows = frappe.get_all(
        "Insight Allocation Entry",
        filters={"rule": rule,
                 "period_month": ["between", [month_start(year, 1), month_end(year, 12)]]},
        fields=["name", "cost_center", "period_month", "basis", "driver_value", "amount",
                "budget_amount", "manual_pool"],
        limit_page_length=0,
    )
    cells: dict[str, dict] = {}
    basis_by_cc: dict[str, str] = {}
    pool: dict[int, float] = {}
    for r in rows:
        pm = r["period_month"]
        m = pm.month if hasattr(pm, "month") else int(str(pm)[5:7])
        cells.setdefault(r["cost_center"], {})[m] = {
            "driver": flt(r.get("driver_value")),
            "amount": flt(r.get("amount")),
            "budget": flt(r.get("budget_amount")),
        }
        basis_by_cc[r["cost_center"]] = r.get("basis") or "head_count"
        if r.get("manual_pool"):
            pool[m] = flt(r["manual_pool"])

    return {
        "rule": rule,
        "title": doc.title,
        "driver_label": doc.driver_label or "Driver",
        "pool_mode": doc.pool_mode,
        "pool_flag": doc.pool_flag,
        "company": company,
        "year": year,
        "months": MONTH_LABELS,
        "cost_centers": sorted(cells.keys()),
        "cells": cells,
        "basis": basis_by_cc,
        "manual_pool": pool,
    }


@frappe.whitelist()
def save_grid(rule, year, company=None, cells=None, manual_pool=None, basis=None, remove=None):
    """Bulk upsert of one rule-year.

    Rows whose value and pool are all empty are deleted rather than stored as
    zeros — an absent head count and a head count of zero are different facts,
    and only the second should contribute a zero share.
    """
    _guard(write=True)
    year = int(year)
    doc = frappe.get_doc("Insight Allocation Rule", rule)
    company = company or doc.company

    if isinstance(cells, str):
        cells = json.loads(cells or "{}")
    if isinstance(manual_pool, str):
        manual_pool = json.loads(manual_pool or "{}")
    if isinstance(basis, str):
        basis = json.loads(basis or "{}")
    if isinstance(remove, str):
        remove = json.loads(remove or "[]")
    remove = remove or []
    cells = cells or {}
    manual_pool = manual_pool or {}
    basis = basis or {}

    existing = {
        (r["cost_center"], (r["period_month"].month if hasattr(r["period_month"], "month")
                            else int(str(r["period_month"])[5:7]))): r["name"]
        for r in frappe.get_all(
            "Insight Allocation Entry",
            filters={"rule": rule,
                     "period_month": ["between", [month_start(year, 1), month_end(year, 12)]]},
            fields=["name", "cost_center", "period_month"], limit_page_length=0)
    }

    written = deleted = 0

    # v2.60.1 — explicit removal. Emptying a row cannot express "delete this
    # cost centre": a zero head count is a real value that must stay in the
    # table and contribute a zero share, so there is no set of cell values
    # that means "gone". The client names the rows to drop.
    for cc in remove:
        for (e_cc, _m), name in list(existing.items()):
            if e_cc == cc:
                frappe.delete_doc("Insight Allocation Entry", name,
                                  ignore_permissions=False, force=1)
                deleted += 1
        cells.pop(cc, None)

    for cc, months in cells.items():
        for m_str, vals in (months or {}).items():
            m = int(m_str)
            if not 1 <= m <= 12:
                continue
            cc_basis = basis.get(cc) or "head_count"
            # Only the field matching the basis is stored. A head-count cost
            # centre cannot carry an amount, which is what stops a calculated
            # figure being pasted back in as an input.
            driver = flt((vals or {}).get("driver")) if cc_basis == "head_count" else 0.0
            amount = flt((vals or {}).get("amount")) if cc_basis == "amount" else 0.0
            # Budget is stored for EVERY basis, unlike driver/amount which are
            # mutually exclusive. It is an independent input — what the business
            # agreed to spend — not an alternative way of expressing the driver,
            # so a head-count cost centre carries one too.
            budget = flt((vals or {}).get("budget"))
            pool = flt(manual_pool.get(str(m), manual_pool.get(m, 0)))
            key = (cc, m)
            blank = not driver and not amount and not pool and not budget
            if blank:
                if key in existing:
                    frappe.delete_doc("Insight Allocation Entry", existing[key],
                                      ignore_permissions=False, force=1)
                    deleted += 1
                continue
            if key in existing:
                e = frappe.get_doc("Insight Allocation Entry", existing[key])
            else:
                e = frappe.new_doc("Insight Allocation Entry")
                e.rule = rule
                e.cost_center = cc
                e.period_month = month_start(year, m)
            e.company = company
            e.basis = cc_basis
            e.driver_value = driver
            e.amount = amount
            e.budget_amount = budget
            e.manual_pool = pool
            e.save()
            written += 1

    frappe.db.commit()
    return {"ok": True, "written": written, "deleted": deleted}


def _pool_from_report_row(rule_doc, company, year):
    """The pool as the report already reports it.

    This is the number on screen when the P&L is run with one cost centre
    selected — `Total Expenses` for GMO, say. Reading the report rather than
    re-deriving it from accounts means the allocation cannot drift from the
    statement it is spread out of: remap an account and both move together.
    """
    from neotec_insight.neotec_insight.utils.execution import (
        execute_report, load_flag_mappings, load_flag_to_accounts,
    )

    if not (rule_doc.pool_report and rule_doc.pool_row_key):
        return {}

    # ── Recursion guard (v2.60.2) ────────────────────────────────────────
    # The pool is a row of a report, and that same report is where the
    # allocation rows live. Running it to read the pool therefore evaluates
    # the allocation rows, which run this function again — unbounded
    # recursion until the worker dies.
    #
    # The guard also fixes the accounting, not just the crash: while a pool
    # is being read, allocation rows evaluate to zero, so the pool is the
    # cost centre's expense *before* any allocation. That is the only figure
    # it can be — allocating a number that already contains allocations is
    # circular by definition.
    inflight = frappe.flags.setdefault("ni_allocation_pool_inflight", set())
    token = (rule_doc.pool_report, rule_doc.pool_cost_center or "", int(year))
    if token in inflight:
        return {}
    inflight.add(token)
    try:
        rd = frappe.get_doc("Insight Report Definition", rule_doc.pool_report)
        report_def = json.loads(rd.definition_json or "{}")
        res = execute_report(
            report_def=report_def,
            fiscal_year=int(year),
            # v2.62.0 — month indices here are 0-based and in *fiscal year
            # order*, not calendar months. Passing 1..12 read every figure
            # one slot late: January's allocation was February's expense.
            month_from=0,
            month_to=11,
            company=company,
            # One cost centre, exactly as the operator selects it on screen.
            cost_center=rule_doc.pool_cost_center or None,
            flag_to_accounts=load_flag_to_accounts(rule_doc.pool_report),
            flag_to_mappings=load_flag_mappings(rule_doc.pool_report),
        ) or {}
    except Exception as e:
        frappe.log_error(f"allocation pool report failed: {e}", "Neotec Insight: allocation")
        return {}
    finally:
        inflight.discard(token)

    key = rule_doc.pool_row_key
    for row in (res.get("rows") or []):
        if row.get("key") == key or row.get("label") == key:
            monthly = row.get("monthly") or {}
            # Translate fiscal-year position into calendar month, because the
            # driver entries are keyed by real dates. For a January-start
            # company this is idx+1; for an April-start one it is not, and
            # assuming otherwise would silently misalign every figure.
            out = {}
            for k, v in monthly.items():
                cal = calendar_month_for_fy_month(company, int(k))
                out[cal] = flt(v)
            if (rule_doc.pool_sign or "normal") == "invert":
                out = {m: -v for m, v in out.items()}
            return out
    return {}


@frappe.whitelist()
def list_report_rows(report):
    """Rows of a report, for picking which one carries the pool."""
    _guard()
    try:
        rd = frappe.get_doc("Insight Report Definition", report)
        rows = (json.loads(rd.definition_json or "{}") or {}).get("rows") or []
    except Exception:
        return []
    return [{"key": r.get("key"), "label": r.get("label") or r.get("key"),
             "kind": r.get("kind")} for r in rows if r.get("key")]


def _pool_from_flag(rule_doc, company, year):
    """Monthly pool totals for a flag-sourced rule, straight from the GL.

    Account flags are defined per report, not globally, so the flag is
    resolved against the report named on the rule. That keeps one source of
    truth: the same account map that feeds the P&L feeds the allocation, and
    a remapped account cannot make the two disagree.
    """
    from neotec_insight.neotec_insight.utils.execution import (
        _fetch_monthly_for_accounts, load_flag_to_accounts,
    )

    if not rule_doc.pool_report:
        return {}
    try:
        accounts = (load_flag_to_accounts(rule_doc.pool_report) or {}).get(rule_doc.pool_flag) or []
    except Exception:
        accounts = []
    if not accounts:
        return {}
    try:
        monthly = _fetch_monthly_for_accounts(
            accounts=accounts, fiscal_year=year, months=list(range(1, 13)),
            cost_center=None, project=None, department=None, branch=None,
            company=company, row_scope=None, dimension_filters=None,
            fy_start_month_override=None, period_mode=None,
            period_from_date=None, period_to_date=None,
        ) or {}
    except Exception as e:
        frappe.log_error(f"allocation pool fetch failed: {e}", "Neotec Insight: allocation")
        return {}
    if (rule_doc.pool_sign or "normal") == "invert":
        monthly = {m: -v for m, v in monthly.items()}
    return monthly


@frappe.whitelist()
def run(rule, year, company=None):
    """The allocation report: cost centres across, months down."""
    _guard()
    year = int(year)
    doc = frappe.get_doc("Insight Allocation Rule", rule)
    company = company or doc.company

    mode = doc.pool_mode or "report_row"
    pool_by_month = None
    if mode == "report_row":
        pool_by_month = _pool_from_report_row(doc, company, year)
    elif mode == "flag" and doc.pool_flag:
        pool_by_month = _pool_from_flag(doc, company, year)

    res = compute(rule, company, year, pool_by_month=pool_by_month)
    res["month_labels"] = MONTH_LABELS

    cc_labels = {}
    for cc in res["cost_centers"]:
        cc_labels[cc] = frappe.db.get_value("Cost Center", cc, "cost_center_name") or cc
    res["cost_center_labels"] = cc_labels

    # Year totals per cost centre, so the report can carry a YTD column
    # without the client re-adding twelve floats and rounding differently.
    res["pool_source"] = (
        f"{doc.pool_report} · {doc.pool_row_key} · {doc.pool_cost_center}"
        if mode == "report_row" else
        (f"flag {doc.pool_flag}" if mode == "flag" else "manual")
    )
    ytd = {cc: 0.0 for cc in res["cost_centers"]}
    for m in res["months"]:
        for cc, v in res["by_month"][m]["allocation"].items():
            ytd[cc] = flt(ytd.get(cc, 0.0) + v, 2)
    res["ytd"] = ytd
    res["ytd_pool"] = flt(sum(res["by_month"][m]["pool"] for m in res["months"]), 2)
    res["ytd_residual"] = flt(sum(res["by_month"][m]["residual"] for m in res["months"]), 2)

    # ── Budget (v2.78.0) ────────────────────────────────────────────────────
    # Read straight from the stored entries and NEVER derived. A budget for an
    # allocation is a decision someone signed off; re-deriving it from the
    # actual driver would produce a figure nobody agreed to, and it would move
    # every time the driver moved — which is exactly what a budget must not do.
    #
    # Carried alongside the computed actuals rather than inside `by_month`, so
    # the split between "derived" and "entered" stays legible: everything in
    # `by_month` is calculated, everything here was typed by a person.
    budget: dict[int, dict[str, float]] = {m: {} for m in res["months"]}
    budget_ytd: dict[str, float] = {cc: 0.0 for cc in res["cost_centers"]}
    for r in frappe.get_all(
            "Insight Allocation Entry",
            filters={"rule": rule,
                     "period_month": ["between", [month_start(year, 1), month_end(year, 12)]]},
            fields=["cost_center", "period_month", "budget_amount"],
            limit_page_length=0):
        b = flt(r.get("budget_amount"))
        if not b:
            continue
        pm = r["period_month"]
        m = pm.month if hasattr(pm, "month") else int(str(pm)[5:7])
        cc = r["cost_center"]
        if m in budget:
            budget[m][cc] = flt(budget[m].get(cc, 0.0) + b, 2)
        budget_ytd[cc] = flt(budget_ytd.get(cc, 0.0) + b, 2)
    res["budget"] = budget
    res["budget_ytd"] = budget_ytd
    res["budget_total"] = flt(sum(budget_ytd.values()), 2)

    # Variance, computed here so screen and every export agree on the sign.
    # Actual over budget is POSITIVE — an allocation is a cost, so spending
    # more than budgeted reads as a positive overrun rather than a negative.
    res["variance_ytd"] = {cc: flt(ytd.get(cc, 0.0) - budget_ytd.get(cc, 0.0), 2)
                           for cc in res["cost_centers"]}
    return res


# ── Driver capture (v2.63.0) ────────────────────────────────────────────────


@frappe.whitelist()
def capture_preview(rule, year, months=None):
    """What a capture would do. Writes nothing."""
    _guard()
    from neotec_insight.neotec_insight.utils import driver_capture
    if isinstance(months, str):
        months = json.loads(months or "null")
    return driver_capture.preview(rule, int(year), months)


@frappe.whitelist()
def capture_commit(rule, year, months=None, freeze=0):
    """Accept a capture. Frozen and overridden rows are left alone."""
    _guard(write=True)
    from neotec_insight.neotec_insight.utils import driver_capture
    if isinstance(months, str):
        months = json.loads(months or "null")
    return driver_capture.commit(rule, int(year), months, freeze=bool(int(freeze or 0)))


@frappe.whitelist()
def get_evidence(rule, cost_center, year, month):
    """The people (or leads) behind one cell.

    Read back from the snapshot taken at capture time rather than re-counted,
    so the list always reconciles to the number it supports — including
    employees who have since transferred or been deleted.
    """
    _guard()
    from neotec_insight.neotec_insight.utils.allocation import month_start
    name = frappe.db.exists("Insight Allocation Entry", {
        "rule": rule, "cost_center": cost_center,
        "period_month": month_start(int(year), int(month))})
    if not name:
        return {"available": False, "reason": "no_entry"}
    doc = frappe.get_doc("Insight Allocation Entry", name)
    if not doc.evidence_json:
        return {
            "available": False,
            "reason": "manual",
            "value": flt(doc.driver_value) or flt(doc.amount),
            "basis": doc.basis,
            "modified_by": doc.modified_by,
            "modified": str(doc.modified),
        }
    try:
        ev = json.loads(doc.evidence_json)
    except Exception:
        return {"available": False, "reason": "unreadable"}
    ev["available"] = True
    ev["stored_value"] = flt(doc.driver_value)
    ev["is_override"] = int(doc.is_override or 0)
    ev["is_frozen"] = int(doc.is_frozen or 0)
    # The whole point of a snapshot is that it still adds up. Say so, or say
    # loudly that it does not.
    ev["reconciles"] = abs(len(ev.get("members") or []) - flt(doc.driver_value)) < 0.0001
    return ev


@frappe.whitelist()
def freeze_months(rule, year, months, frozen=1):
    """Close (or reopen) months so later captures report drift, not changes."""
    _guard(write=True)
    from neotec_insight.neotec_insight.utils.allocation import month_start
    if isinstance(months, str):
        months = json.loads(months or "[]")
    n = 0
    for m in months or []:
        for e in frappe.get_all("Insight Allocation Entry",
                                filters={"rule": rule, "period_month": month_start(int(year), int(m))},
                                fields=["name"], limit_page_length=0):
            frappe.db.set_value("Insight Allocation Entry", e["name"], "is_frozen",
                                1 if int(frozen) else 0)
            n += 1
    frappe.db.commit()
    return {"ok": True, "updated": n}


@frappe.whitelist()
def unassigned_employees(rule, year, months=None):
    """Pre-flight: who has no cost centre, and what it distorts."""
    _guard()
    from neotec_insight.neotec_insight.utils import driver_capture
    if isinstance(months, str):
        months = json.loads(months or "null")
    return driver_capture.unassigned_report(rule, int(year), months)
