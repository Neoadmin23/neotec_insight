"""Cost pool allocation across cost centres (v2.58.0).

The model, taken from the workbook this replaces:

    distributable(m) = pool(m) - sum(direct(cc, m))
    alloc(cc, m)     = direct(cc, m)                                   if basis is 'amount'
                     = distributable(m) * driver(cc, m) / driver_total(m)  if basis is 'head_count'

which is the workbook's own cell, =+(D14/D$19)*(D$6-D$12), with D14 the cost
centre's head count, D19 the company total, D6 the pool and D12 the directly
entered amounts.

`pool` is the total cost to spread — GMO cost, Sales & Marketing cost —
read from the GL through an account flag, or entered by hand.

A cost centre is on one basis or the other, never both. Most are on
**head count**: no amount is entered for them at all, it is calculated. A few
have no head count — Main on the GMO rule, Audit on Sales & Marketing — and
their **amount** is typed in directly. Those direct amounts come out of the
pool first, and only what is left moves on head count.

`driver` is head count, or leads, or anything else countable. Only the
remainder moves on it.

The identity that matters: **sum over cost centres of alloc(cc, m) == pool(m)**,
exactly. An allocation that doesn't add back to what you started with is
worse than no allocation, so `compute` returns the residual explicitly rather
than trusting it to be zero, and the report surfaces any drift.
"""

from __future__ import annotations

import calendar
from datetime import date

import frappe
from frappe.utils import flt

from neotec_insight.neotec_insight.utils.formula import evaluate_row_formula

DEFAULT_FORMULA = "(pool - amount_total) * (driver / driver_total)"

# The only names a rule's formula may use. Anything else is a typo, and a typo
# that silently evaluates to zero would be worse than one that refuses to run.
FORMULA_VARS = {
    "pool", "amount_total", "distributable", "driver", "driver_total", "month", "year",
}


def month_start(y: int, m: int) -> str:
    return f"{y:04d}-{m:02d}-01"


def month_end(y: int, m: int) -> str:
    return f"{y:04d}-{m:02d}-{calendar.monthrange(y, m)[1]:02d}"


def load_entries(rule: str, company: str | None, year: int) -> dict:
    """Head counts and direct amounts for one rule and calendar year.

    Returns {month: {cost_center: {"basis": s, "driver": f, "amount": f, "pool": f}}}.
    """
    rows = frappe.get_all(
        "Insight Allocation Entry",
        filters={
            "rule": rule,
            "period_month": ["between", [month_start(year, 1), month_end(year, 12)]],
            **({"company": company} if company else {}),
        },
        fields=["cost_center", "period_month", "basis", "driver_value", "amount", "manual_pool"],
        limit_page_length=0,
    )
    out: dict[int, dict] = {}
    for r in rows:
        pm = r["period_month"]
        m = pm.month if isinstance(pm, date) else int(str(pm)[5:7])
        basis = r.get("basis") or "head_count"
        out.setdefault(m, {})[r["cost_center"]] = {
            "basis": basis,
            # Only the field matching the basis is read. Storing both and
            # adding them is how the previous build let a calculated figure
            # be pasted into an input and quietly double-count.
            "driver": flt(r.get("driver_value")) if basis == "head_count" else 0.0,
            "amount": flt(r.get("amount")) if basis == "amount" else 0.0,
            "pool": flt(r.get("manual_pool")),
        }
    return out


def compute(
    rule_name: str,
    company: str | None,
    year: int,
    pool_by_month: dict[int, float] | None = None,
    months: list[int] | None = None,
) -> dict:
    """Allocate one rule's pool across cost centres, month by month.

    `pool_by_month` lets the caller supply the pool it already fetched — the
    report engine has the flag totals in hand and shouldn't re-query for them.
    When omitted, manual pool values from the entries are used.
    """
    months = months or list(range(1, 13))
    rule = frappe.get_doc("Insight Allocation Rule", rule_name)
    expr = (getattr(rule, "formula", "") or "").strip() or DEFAULT_FORMULA
    errors: list[int] = []
    entries = load_entries(rule_name, company or rule.company, year)

    cost_centers: list[str] = []
    for m in months:
        for cc in entries.get(m, {}):
            if cc not in cost_centers:
                cost_centers.append(cc)
    cost_centers.sort()
    # The credited cost centre has no entry of its own — it never appears in
    # either input table — so it has to be added explicitly or its column
    # would be missing from the very report that has to show it.
    if getattr(rule, "credit_back", 0):
        _credit = (getattr(rule, "credit_cost_center", None)
                   or getattr(rule, "pool_cost_center", None))
        if _credit and _credit not in cost_centers:
            cost_centers.append(_credit)

    by_month: dict[int, dict] = {}
    for m in months:
        month_rows = entries.get(m, {})
        direct_total = sum(v["amount"] for v in month_rows.values())
        driver_total = sum(v["driver"] for v in month_rows.values())

        if pool_by_month is not None:
            pool = flt(pool_by_month.get(m, 0.0))
        else:
            # Manual pool is entered once per month; any row of that month
            # carries it, so take the first non-zero rather than summing —
            # summing would multiply the pool by the cost centre count.
            pool = next((v["pool"] for v in month_rows.values() if v["pool"]), 0.0)

        distributable = pool - direct_total
        alloc: dict[str, float] = {}

        # v2.62.0 — a month with no pool allocates nothing.
        #
        # Without this, a direct amount entered for the whole year charges
        # every month regardless of whether anything was spent: 45,000 to
        # Main in each of Jul–Dec on a pool of zero, a matching negative
        # credit, and a 270,000 "unallocated remainder" that is pure
        # artefact. You cannot spread a pool that does not exist.
        if not pool:
            by_month[m] = {
                "pool": 0.0, "direct_total": flt(direct_total, 2),
                "distributable": 0.0, "driver_total": flt(driver_total, 4),
                "allocation": {cc: 0.0 for cc in month_rows},
                "drivers": {cc: v["driver"] for cc, v in month_rows.items()},
                "direct": {cc: v["amount"] for cc, v in month_rows.items()},
                "credit_cost_center": None,
                "allocated": 0.0, "charged": 0.0, "residual": 0.0,
                "unallocated": False, "no_pool": True,
            }
            continue

        for cc, v in month_rows.items():
            if v["basis"] == "amount":
                # Typed in directly. Never runs through the formula: an
                # amount is a fact, not a derivation.
                alloc[cc] = flt(v["amount"], 2)
                continue
            if not driver_total:
                # No driver anywhere this month. Refuse to divide rather than
                # quietly hand the whole pool to one cost centre.
                alloc[cc] = 0.0
                continue
            try:
                alloc[cc] = flt(evaluate_row_formula(expr, {
                    "pool": pool,
                    "amount_total": direct_total,
                    "distributable": distributable,
                    "driver": v["driver"],
                    # The denominator is the driver table's own total — the
                    # sum of exactly the cost centres listed, never a
                    # company-wide head count. Every share is therefore a
                    # share of something that adds to 100%.
                    "driver_total": driver_total,
                    "month": float(m),
                    "year": float(year),
                }), 2)
            except Exception:
                errors.append(m)
                alloc[cc] = 0.0

        # With no driver anywhere in the month the remainder has nowhere to
        # go. That is a data gap, not a zero — say so instead of silently
        # dropping the money.

        # Credit back (v2.60.0). The source cost centre gave this money away;
        # without a matching credit the same riyal is counted twice at company
        # level — once where it was spent, once where it landed. The credit
        # makes the consolidated total identical with or without allocation.
        credit_cc = None
        if getattr(rule, "credit_back", 0):
            credit_cc = (getattr(rule, "credit_cost_center", None)
                         or getattr(rule, "pool_cost_center", None))
            if credit_cc:
                alloc[credit_cc] = flt(alloc.get(credit_cc, 0.0) - sum(alloc.values()), 2)

        # `charged` is what the receiving cost centres carry; `allocated`
        # includes the credit and is the company-level net. They differ by
        # exactly the credit, which is the point.
        charged = sum(v for k, v in alloc.items() if k != credit_cc)
        allocated = sum(alloc.values())
        residual = flt(pool - charged, 2)

        by_month[m] = {
            "pool": flt(pool, 2),
            "credit_cost_center": credit_cc,
            "direct_total": flt(direct_total, 2),
            "distributable": flt(distributable, 2),
            "driver_total": flt(driver_total, 4),
            "allocation": alloc,
            "drivers": {cc: v["driver"] for cc, v in month_rows.items()},
            "direct": {cc: v["amount"] for cc, v in month_rows.items()},
            "allocated": flt(allocated, 2),
            "charged": flt(charged, 2),
            "residual": residual,
            "unallocated": bool(driver_total == 0 and abs(distributable) > 0.005),
            "no_pool": False,
        }

    # Classify each cost centre across the year (v2.58.1).
    #
    # Which basis each cost centre is on, for the report to label its column.
    # Main is on 'amount' and takes exactly what was typed; everyone else is
    # on 'head_count' and takes a calculated share.
    roles: dict[str, str] = {}
    for cc in cost_centers:
        found = {entries.get(m, {}).get(cc, {}).get("basis")
                 for m in months if cc in entries.get(m, {})}
        found.discard(None)
        if not found:
            roles[cc] = "credit"
            continue
        # A cost centre that changes basis mid-year is almost certainly a
        # mistake, so it is reported rather than silently resolved.
        roles[cc] = ("mixed" if len(found) > 1
                     else ("amount" if found == {"amount"} else "head_count"))

    return {
        "rule": rule_name,
        "formula": expr,
        "formula_errors": sorted(set(errors)),
        "credit_back": 1 if getattr(rule, "credit_back", 0) else 0,
        "roles": roles,
        "mixed": [cc for cc, r in roles.items() if r == "mixed"],
        "title": rule.title,
        "driver_label": rule.driver_label or "Driver",
        "pool_mode": rule.pool_mode,
        "pool_flag": rule.pool_flag,
        "company": company or rule.company,
        "year": year,
        "months": months,
        "cost_centers": cost_centers,
        "by_month": by_month,
    }


def allocation_for_cost_center(result: dict, cost_center: str | None) -> dict[int, float]:
    """Collapse a computed result to {month: amount} for one cost centre.

    With no cost centre the whole pool is returned — a consolidated P&L that
    shows an allocation row should show the total being spread, not zero.
    """
    out: dict[int, float] = {}
    for m, d in result.get("by_month", {}).items():
        if cost_center:
            out[m] = flt(d["allocation"].get(cost_center, 0.0), 2)
        else:
            out[m] = flt(d.get("allocated", 0.0), 2)
    return out
