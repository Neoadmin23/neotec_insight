"""Liquidity: cash movement, ageing, forward projection, and the sensitivity
stress test built on it (v1.9.21, v1.9.39).

v2.92.0 — moved unchanged out of api/report.py, which re-exports every
name so the `api.report.<name>` endpoint addresses keep working."""

import frappe
from frappe.utils import cint
from neotec_insight.neotec_insight.api.report_shared import _require_read, _sanitise_dimension_filters


# ───────────────────────────────────────────────────────────────────────────
# Liquidity (v1.9.21) — cash movement + receivables ageing
# ───────────────────────────────────────────────────────────────────────────

@frappe.whitelist()
def get_liquidity(
    company: str,
    fiscal_year: str | int,
    projection_months: str | int = 6,
    projection_baseline: str = "committed",
    collection_mode: str = "best_case",
    collection_schedule: str | dict | None = None,
    payment_schedule: str | dict | None = None,
    dimension_filters: dict | str | None = None,
) -> dict:
    """Liquidity block for the dashboard.

    Two parts, both company-scoped:

    1. cash_monthly — month-by-month movement across the company's Cash + Bank
       accounts (account_type IN ('Bank','Cash')). For each month: opening
       balance, inflow (debits), outflow (credits), closing. Opening of the
       first month is the cash balance as of the day before the fiscal year
       starts; each subsequent month opens where the prior closed.

    2. receivables_ageing — outstanding customer balance bucketed by how long
       past the due date, using ERPNext's standard Accounts Receivable data
       (the Sales Invoice ledger). Buckets: 0-30, 30-60, 60-90, 90-180, 180+.

    v1.9.52 — dimension_filters is applied to the CASH GL queries so the
    cash bucket reflects the active custom-dimension scope. AR/AP ageing
    reads invoice totals and is NOT filtered by custom dimensions in this
    release — per-invoice dimension filtering needs separate design.

    No P&L mappings involved — this reads GL and Sales Invoice directly, so it
    is independent of any report definition and globally reusable.
    """
    _require_read()
    fy = cint(fiscal_year)
    # v1.9.59 — use company's fiscal year boundaries, not hardcoded Jan-Dec.
    from neotec_insight.neotec_insight.utils.fiscal_year import fiscal_year_bounds
    _fy_s, _fy_e = fiscal_year_bounds(company, fy)
    fy_start = _fy_s.isoformat()
    fy_end = _fy_e.isoformat()
    # v1.9.52 — sanitise once, build a SQL fragment + extra params we can
    # splice into the cash GL queries below.
    dim_filters_safe = _sanitise_dimension_filters(dimension_filters)
    dim_sql = ""
    dim_params: list = []
    if dim_filters_safe:
        try:
            gl_cols_existing = set(frappe.db.get_table_columns("GL Entry"))
        except Exception:
            gl_cols_existing = set()
        for fld, val in dim_filters_safe.items():
            if not val:
                continue
            if fld not in gl_cols_existing:
                continue
            # fld came from the sanitiser whitelist, so backticks are safe.
            # v1.9.58 — value may be a list (multi-select). Expand to IN(...)
            # parameterised, preserving the existing scalar fallback.
            if isinstance(val, list):
                placeholders = ", ".join(["%s"] * len(val))
                dim_sql += f" AND `{fld}` IN ({placeholders})"
                dim_params.extend(val)
            else:
                dim_sql += f" AND `{fld}` = %s"
                dim_params.append(val)

    # ── 1. Cash + Bank accounts for the company ────────────────────────────
    cash_accounts = frappe.get_all(
        "Account",
        filters={
            "company": company,
            "account_type": ["in", ["Bank", "Cash"]],
            "is_group": 0,
        },
        pluck="name",
    )

    cash_monthly: list[dict] = []
    opening_total = 0.0
    if cash_accounts:
        ph = ", ".join(["%s"] * len(cash_accounts))

        # Opening: net (debit - credit) on cash accounts strictly before FY start.
        opening_row = frappe.db.sql(
            f"""
            SELECT COALESCE(SUM(debit - credit), 0) AS bal
            FROM `tabGL Entry`
            WHERE is_cancelled = 0
              AND company = %s
              AND account IN ({ph})
              AND posting_date < %s
              {dim_sql}
            """,
            [company, *cash_accounts, fy_start, *dim_params],
        )
        opening_total = float(opening_row[0][0] or 0.0) if opening_row else 0.0

        # Per-month inflow (debit) and outflow (credit) within the fiscal year.
        month_rows = frappe.db.sql(
            f"""
            SELECT MONTH(posting_date) AS m,
                   COALESCE(SUM(debit), 0)  AS inflow,
                   COALESCE(SUM(credit), 0) AS outflow
            FROM `tabGL Entry`
            WHERE is_cancelled = 0
              AND company = %s
              AND account IN ({ph})
              AND posting_date >= %s AND posting_date <= %s
              {dim_sql}
            GROUP BY MONTH(posting_date)
            """,
            [company, *cash_accounts, fy_start, fy_end, *dim_params],
            as_dict=True,
        )
        by_month = {int(r["m"]): r for r in month_rows}

        # v1.9.59 — iterate in FY order. fy_month_idx 0..11 = the company's
        # 1st through 12th month. The output `month` field uses the
        # FY-month index so downstream consumers can plot inflow/outflow
        # in fiscal-year order. The `cal_month` field carries the absolute
        # calendar position for any consumer that needs the date.
        from neotec_insight.neotec_insight.utils.fiscal_year import calendar_month_for_fy_month
        running = opening_total
        for fy_m in range(12):
            cal_m = calendar_month_for_fy_month(company, fy_m)
            r = by_month.get(cal_m)
            inflow = float(r["inflow"]) if r else 0.0
            outflow = float(r["outflow"]) if r else 0.0
            opening = running
            closing = opening + inflow - outflow
            cash_monthly.append({
                "month": fy_m,            # FY-month index (0..11 in fiscal-year order)
                "cal_month": cal_m - 1,   # calendar 0-indexed (Jan=0) — for label rendering
                "opening": opening,
                "inflow": inflow,
                "outflow": outflow,
                "closing": closing,
            })
            running = closing

    # ── 2. Receivables ageing ──────────────────────────────────────────────
    # Outstanding Sales Invoices for the company, bucketed by days past due.
    buckets = [
        {"key": "b0_30", "label": "0-30", "min": 0, "max": 30},
        {"key": "b30_60", "label": "30-60", "min": 30, "max": 60},
        {"key": "b60_90", "label": "60-90", "min": 60, "max": 90},
        {"key": "b90_180", "label": "90-180", "min": 90, "max": 180},
        {"key": "b180_plus", "label": "180+", "min": 180, "max": None},
    ]
    ageing = {b["key"]: 0.0 for b in buckets}
    ageing["not_due"] = 0.0
    receivables_total = 0.0

    invoices = frappe.get_all(
        "Sales Invoice",
        filters={
            "company": company,
            "docstatus": 1,
            "outstanding_amount": [">", 0],
        },
        fields=["name", "outstanding_amount", "due_date"],
    )
    today = frappe.utils.getdate(frappe.utils.nowdate())
    for inv in invoices:
        amt = float(inv.get("outstanding_amount") or 0.0)
        if amt <= 0:
            continue
        receivables_total += amt
        due = inv.get("due_date")
        if not due:
            ageing["b0_30"] += amt
            continue
        days_overdue = (today - frappe.utils.getdate(due)).days
        if days_overdue <= 0:
            ageing["not_due"] += amt
            continue
        placed = False
        for b in buckets:
            lo, hi = b["min"], b["max"]
            if days_overdue > lo and (hi is None or days_overdue <= hi):
                ageing[b["key"]] += amt
                placed = True
                break
        if not placed:
            ageing["b180_plus"] += amt

    # ── 3. Forward cash projection ─────────────────────────────────────────
    # Project cash for the next 6 months from today, using COMMITTED items
    # only: outstanding Sales Invoices land as inflow in the month of their
    # due date; outstanding Purchase Invoices land as outflow likewise.
    # Overdue items (due date already passed) are assumed to land in month 1,
    # the soonest projected month — a standard, conservative AR/AP forecast.
    PROJECT_MONTHS = max(1, min(12, cint(projection_months) or 6))
    proj_inflow = [0.0] * PROJECT_MONTHS
    proj_outflow = [0.0] * PROJECT_MONTHS

    def _months_ahead(due_date) -> int:
        """0-based index of the projected month a due date falls in.
        Past-due or due this month -> 0; clamps to the last projected month."""
        d = frappe.utils.getdate(due_date)
        idx = (d.year - today.year) * 12 + (d.month - today.month)
        if idx < 0:
            idx = 0
        if idx > PROJECT_MONTHS - 1:
            idx = PROJECT_MONTHS - 1
        return idx

    # Receivables -> projected inflow.
    # Two modes:
    #   best_case (default kept for back-compat): place each invoice's full
    #     outstanding amount in the month of its due date (or month 1 if overdue).
    #     This is what every committed-items forecast does — but it implicitly
    #     assumes 100% collection, which is unrealistic for aged receivables.
    #   realistic: apply a collection-likelihood haircut by ageing bucket, and
    #     spread the expected collections over multiple months — the older the
    #     bucket, the smaller the share and the longer the tail.
    #
    # The user can override every bucket's percentage and month weights via the
    # collection_schedule parameter. Defaults are sensible Big-Four-style values.
    cmode = (collection_mode or "best_case").lower()
    uncollectible_estimate = 0.0
    collection_used: dict = {}

    if cmode == "realistic":
        # Default schedule — (collection_pct, [weights across months 1..N]).
        # Weights need not be normalised — they're normalised below.
        DEFAULT_SCHEDULE = {
            "not_due":    {"pct": 1.00, "weights": [0.5, 0.5]},
            "b0_30":      {"pct": 0.90, "weights": [0.6, 0.4]},
            "b30_60":     {"pct": 0.75, "weights": [0.4, 0.4, 0.2]},
            "b60_90":     {"pct": 0.60, "weights": [0.3, 0.3, 0.2, 0.2]},
            "b90_180":    {"pct": 0.40, "weights": [0.2, 0.2, 0.2, 0.2, 0.2]},
            "b180_plus":  {"pct": 0.20, "weights": [0.15, 0.15, 0.15, 0.15, 0.2, 0.2]},
        }
        # Merge user overrides on top of the defaults — partial overrides are fine.
        schedule = {k: dict(v) for k, v in DEFAULT_SCHEDULE.items()}
        if collection_schedule:
            if isinstance(collection_schedule, str):
                try:
                    import json as _json
                    user_sched = _json.loads(collection_schedule)
                except Exception:
                    user_sched = {}
            else:
                user_sched = collection_schedule or {}
            for k, v in (user_sched or {}).items():
                if k in schedule and isinstance(v, dict):
                    if "pct" in v:
                        try:
                            schedule[k]["pct"] = max(0.0, min(1.0, float(v["pct"])))
                        except Exception:
                            pass
                    if "weights" in v and isinstance(v["weights"], list) and v["weights"]:
                        try:
                            schedule[k]["weights"] = [max(0.0, float(x)) for x in v["weights"]]
                        except Exception:
                            pass

        # Bucket totals already computed above in the receivables ageing pass.
        bucket_amounts = {
            "not_due":   ageing["not_due"],
            "b0_30":     ageing["b0_30"],
            "b30_60":    ageing["b30_60"],
            "b60_90":    ageing["b60_90"],
            "b90_180":   ageing["b90_180"],
            "b180_plus": ageing["b180_plus"],
        }
        for bkey, amt in bucket_amounts.items():
            if amt <= 0:
                continue
            sch = schedule[bkey]
            pct = sch["pct"]
            expected = amt * pct
            uncollectible_estimate += amt - expected
            weights = sch["weights"] or [1.0]
            # Clip weights to the projection horizon. Anything past the horizon
            # collapses into the final projected month — so the realistic
            # estimate stays whole within the chosen window.
            if len(weights) > PROJECT_MONTHS:
                head = weights[: PROJECT_MONTHS - 1]
                tail = sum(weights[PROJECT_MONTHS - 1:])
                weights = head + [tail]
            wsum = sum(weights) or 1.0
            for i, w in enumerate(weights):
                proj_inflow[i] += expected * (w / wsum)
            collection_used[bkey] = {
                "pct": pct,
                "weights": sch["weights"],
                "amount": amt,
                "expected": expected,
            }
    else:
        # Best-case mode — original behaviour, 100% by due date.
        for inv in invoices:
            amt = float(inv.get("outstanding_amount") or 0.0)
            if amt <= 0:
                continue
            due = inv.get("due_date")
            idx = _months_ahead(due) if due else 0
            proj_inflow[idx] += amt

    # Payables -> projected outflow, from outstanding Purchase Invoices.
    # First pass: build ageing buckets (independent of projection mode).
    payables_total = 0.0
    pay_ageing = {b["key"]: 0.0 for b in buckets}
    pay_ageing["not_due"] = 0.0
    pinvoices = frappe.get_all(
        "Purchase Invoice",
        filters={
            "company": company,
            "docstatus": 1,
            "outstanding_amount": [">", 0],
        },
        fields=["name", "outstanding_amount", "due_date"],
    )
    for pinv in pinvoices:
        amt = float(pinv.get("outstanding_amount") or 0.0)
        if amt <= 0:
            continue
        payables_total += amt
        due = pinv.get("due_date")
        # Ageing bucket by days past due.
        if not due:
            pay_ageing["b0_30"] += amt
        else:
            days_overdue = (today - frappe.utils.getdate(due)).days
            if days_overdue <= 0:
                pay_ageing["not_due"] += amt
            else:
                placed = False
                for b in buckets:
                    lo, hi = b["min"], b["max"]
                    if days_overdue > lo and (hi is None or days_overdue <= hi):
                        pay_ageing[b["key"]] += amt
                        placed = True
                        break
                if not placed:
                    pay_ageing["b180_plus"] += amt

    # Second pass: place payables on the projected outflow line.
    # Best case (default) keeps the original behaviour — pay each invoice in
    # the month of its due date (overdue → month 1). Realistic uses a
    # payment-timing schedule per ageing bucket: 100% pays (no haircut, you
    # still owe the money), but older overdue pays sooner and the spread
    # across months reflects supplier-pressure reality. Users can override
    # the per-bucket spread via the payment_schedule parameter.
    payment_used: dict = {}
    if cmode == "realistic":
        # Revised defaults (v1.9.31): older overdue is given longer tails, not
        # forced into month 1. The instinct "older = pays sooner" holds for
        # moderately overdue (0-60 days, supplier pressure), but for very aged
        # payables (90+) it's the opposite — they've sat that long *because*
        # they can't be cleared in a month, so they dribble out over time.
        DEFAULT_PAY_SCHEDULE = {
            "not_due":   {"weights": [0.5, 0.5]},
            "b0_30":     {"weights": [0.7, 0.3]},
            "b30_60":    {"weights": [0.6, 0.3, 0.1]},
            "b60_90":    {"weights": [0.5, 0.3, 0.2]},
            "b90_180":   {"weights": [0.3, 0.25, 0.2, 0.15, 0.1]},
            "b180_plus": {"weights": [0.2, 0.2, 0.2, 0.15, 0.15, 0.1]},
        }
        pay_sched = {k: dict(v) for k, v in DEFAULT_PAY_SCHEDULE.items()}
        if payment_schedule:
            if isinstance(payment_schedule, str):
                try:
                    import json as _json
                    user_psched = _json.loads(payment_schedule)
                except Exception:
                    user_psched = {}
            else:
                user_psched = payment_schedule or {}
            for k, v in (user_psched or {}).items():
                if k in pay_sched and isinstance(v, dict):
                    if "weights" in v and isinstance(v["weights"], list) and v["weights"]:
                        try:
                            pay_sched[k]["weights"] = [max(0.0, float(x)) for x in v["weights"]]
                        except Exception:
                            pass

        bucket_pay_amounts = {
            "not_due":   pay_ageing["not_due"],
            "b0_30":     pay_ageing["b0_30"],
            "b30_60":    pay_ageing["b30_60"],
            "b60_90":    pay_ageing["b60_90"],
            "b90_180":   pay_ageing["b90_180"],
            "b180_plus": pay_ageing["b180_plus"],
        }
        for bkey, amt in bucket_pay_amounts.items():
            if amt <= 0:
                continue
            weights = pay_sched[bkey]["weights"] or [1.0]
            if len(weights) > PROJECT_MONTHS:
                head = weights[: PROJECT_MONTHS - 1]
                tail = sum(weights[PROJECT_MONTHS - 1:])
                weights = head + [tail]
            wsum = sum(weights) or 1.0
            for i, w in enumerate(weights):
                proj_outflow[i] += amt * (w / wsum)
            payment_used[bkey] = {
                "weights": pay_sched[bkey]["weights"],
                "amount": amt,
            }
    else:
        # Best-case mode — original behaviour: pay on due date, overdue in month 1.
        for pinv in pinvoices:
            amt = float(pinv.get("outstanding_amount") or 0.0)
            if amt <= 0:
                continue
            due = pinv.get("due_date")
            idx = _months_ahead(due) if due else 0
            proj_outflow[idx] += amt

    # ── Expense baseline ──────────────────────────────────────────────────
    # Layer a recurring-expense estimate on top of committed payables — so
    # months with no invoiced expenses still show a realistic outflow.
    # Honest by design: baseline is EXPENSE-only (never adds to inflow), and
    # we NET committed payables against the baseline so we don't double-count.
    expense_accounts = [
        a.name for a in frappe.get_all(
            "Account",
            filters={"company": company, "is_group": 0, "root_type": "Expense"},
            fields=["name"],
        )
    ]
    baseline_mode = (projection_baseline or "committed").lower()
    baseline_out = [0.0] * PROJECT_MONTHS

    def _month_expense(yr: int, mo: int) -> float:
        """Total expense (debit-credit on Expense accounts) for a calendar month."""
        if not expense_accounts:
            return 0.0
        # First day of next month, exclusive.
        if mo == 12:
            end = f"{yr + 1}-01-01"
        else:
            end = f"{yr}-{mo + 1:02d}-01"
        start = f"{yr}-{mo:02d}-01"
        ph = ", ".join(["%s"] * len(expense_accounts))
        row = frappe.db.sql(
            f"""SELECT COALESCE(SUM(debit - credit), 0) FROM `tabGL Entry`
                WHERE is_cancelled = 0 AND company = %s AND account IN ({ph})
                  AND posting_date >= %s AND posting_date < %s""",
            [company, *expense_accounts, start, end],
        )
        return float(row[0][0] or 0.0) if row else 0.0

    if baseline_mode == "prior_year" and expense_accounts:
        # Same calendar month, one year earlier — honours seasonality.
        for i in range(PROJECT_MONTHS):
            mo = ((today.month - 1 + i) % 12) + 1
            yr = today.year + (today.month - 1 + i) // 12 - 1
            baseline_out[i] = _month_expense(yr, mo)
    elif baseline_mode == "trailing_3m" and expense_accounts:
        # Mean of the last 3 fully-completed months — picks up the current run-rate.
        totals = []
        for k in range(1, 4):
            # k months back from the current month start.
            y, m = today.year, today.month - k
            while m <= 0:
                m += 12
                y -= 1
            totals.append(_month_expense(y, m))
        avg = sum(totals) / 3 if totals else 0.0
        for i in range(PROJECT_MONTHS):
            baseline_out[i] = avg
    # 'committed' baseline_mode leaves baseline_out at zeros.

    # Build the projected cash line, starting from current cash on hand.
    current_cash = (cash_monthly[-1]["closing"] if cash_monthly else opening_total)
    projection: list[dict] = []
    running = current_cash
    base_month = today.month  # 1-12
    base_year = today.year
    for i in range(PROJECT_MONTHS):
        mnum = (base_month - 1 + i) % 12          # 0-indexed month
        yr = base_year + (base_month - 1 + i) // 12
        opening = running
        committed_in = proj_inflow[i]
        committed_out = proj_outflow[i]
        # Net the baseline against committed so we never double-count payables
        # already invoiced for the month.
        baseline_excess = max(0.0, baseline_out[i] - committed_out)
        total_out = committed_out + baseline_excess
        closing = opening + committed_in - total_out
        projection.append({
            "month": mnum,
            "year": yr,
            "opening": opening,
            "expected_in": committed_in,
            "expected_out": total_out,
            "committed_out": committed_out,
            "baseline_out": baseline_excess,
            "closing": closing,
        })
        running = closing

    return {
        "company": company,
        "fiscal_year": fy,
        "cash_accounts_count": len(cash_accounts),
        "cash_opening": opening_total,
        "cash_monthly": cash_monthly,
        "receivables": {
            "total": receivables_total,
            "not_due": ageing["not_due"],
            "buckets": [
                {"key": b["key"], "label": b["label"], "amount": ageing[b["key"]]}
                for b in buckets
            ],
        },
        "payables": {
            "total": payables_total,
            "not_due": pay_ageing["not_due"],
            "buckets": [
                {"key": b["key"], "label": b["label"], "amount": pay_ageing[b["key"]]}
                for b in buckets
            ],
        },
        "projection": {
            "months": PROJECT_MONTHS,
            "baseline": baseline_mode,
            "collection_mode": cmode,
            "collection_schedule": collection_used,
            "payment_schedule": payment_used,
            "uncollectible_estimate": uncollectible_estimate,
            "current_cash": current_cash,
            "payables_total": payables_total,
            "rows": projection,
        },
    }


# ───────────────────────────────────────────────────────────────────────────
# Sensitivity analysis (v1.9.39) — "the CFO's stress test"
# ───────────────────────────────────────────────────────────────────────────
# Wraps get_liquidity and perturbs the projection by three real-world levers:
# collection-speed delay, revenue level, and cost inflation. Output: stressed
# projection + a tornado-ready scenario set for each lever.
#
# Design choice: NOT persisted. Sensitivity is exploratory — you don't save
# what-if runs. If a user wants to commit a stressed scenario as a forecast,
# that's a budget-revision feature, not this one.


def _apply_stress_to_projection(
    base: dict,
    stress_collection_days: int,
    stress_revenue_pct: float,
    stress_cost_pct: float,
) -> dict:
    """Apply three stress levers to a base liquidity projection.

    Mechanics (all transparent, intentionally simple — sensitivity output
    should not pretend to be more sophisticated than its inputs):

    - stress_collection_days: shift expected_in to later months. Positive
      days = slower collection. Implementation: shift a fraction of each
      month's inflow to next month, proportional to days/30.
    - stress_revenue_pct: scale expected_in. -10 = 10% lower inflows.
    - stress_cost_pct: scale projected outflows (committed + baseline).
      +5 = 5% higher outflows.
    """
    proj = base.get("projection") or {}
    rows = list(proj.get("rows") or [])
    if not rows:
        return base

    months = len(rows)
    days = max(-30, min(30, int(stress_collection_days or 0)))
    rev_mult = 1.0 + (max(-50.0, min(50.0, float(stress_revenue_pct or 0))) / 100.0)
    cost_mult = 1.0 + (max(-50.0, min(50.0, float(stress_cost_pct or 0))) / 100.0)

    # Step 1: scale revenue (expected_in) and cost (expected_out) per month.
    expected_in = [float(r.get("expected_in") or 0) * rev_mult for r in rows]
    expected_out = [float(r.get("expected_out") or 0) * cost_mult for r in rows]

    # Step 2: collection delay — shift a fraction of each month's inflow
    # forward by (days/30) months. Positive days = slower = push later.
    if days != 0:
        shift_frac = abs(days) / 30.0  # 30 days = 100% shift to next month
        shift_frac = min(1.0, shift_frac)
        direction = 1 if days > 0 else -1  # +1 = later, -1 = earlier
        shifted = [0.0] * months
        for i in range(months):
            keep = expected_in[i] * (1.0 - shift_frac)
            shift = expected_in[i] * shift_frac
            shifted[i] += keep
            target = i + direction
            if 0 <= target < months:
                shifted[target] += shift
            else:
                # Pushed off the horizon — treat as deferred/lost in window.
                # For honesty, push to last month (acknowledges cash arrives
                # eventually but outside the window we can see).
                shifted[months - 1] += shift if direction > 0 else 0
        expected_in = shifted

    # Step 3: recompute running cash. Opening month 0 = current_cash.
    current_cash = float(proj.get("current_cash") or 0)
    new_rows = []
    opening = current_cash
    for i, r in enumerate(rows):
        closing = opening + expected_in[i] - expected_out[i]
        new_rows.append({
            **r,
            "opening": opening,
            "expected_in": expected_in[i],
            "expected_out": expected_out[i],
            "closing": closing,
        })
        opening = closing

    # Step 4: refresh derived metrics.
    closings = [r["closing"] for r in new_rows]
    low_point = min(closings) if closings else 0.0
    low_idx = closings.index(low_point) if closings else 0
    goes_negative = low_point < 0
    ending = closings[-1] if closings else current_cash

    new_proj = {
        **proj,
        "rows": new_rows,
        "stress": {
            "collection_days": days,
            "revenue_pct": stress_revenue_pct,
            "cost_pct": stress_cost_pct,
        },
        "stressed_summary": {
            "low_point": low_point,
            "low_point_month_idx": low_idx,
            "goes_negative": goes_negative,
            "ending_cash": ending,
            "delta_vs_base_ending": ending - float(proj.get("rows", [{}])[-1].get("closing", 0) if proj.get("rows") else 0),
        },
    }
    return {**base, "projection": new_proj}


@frappe.whitelist()
def get_sensitivity_scenario(
    company: str,
    fiscal_year: str | int,
    projection_months: str | int = 6,
    projection_baseline: str = "committed",
    collection_mode: str = "best_case",
    collection_schedule: str | dict | None = None,
    payment_schedule: str | dict | None = None,
    stress_collection_days: int = 0,
    stress_revenue_pct: float = 0,
    stress_cost_pct: float = 0,
) -> dict:
    """Run a single stressed projection.

    Returns the same shape as get_liquidity, but with `projection.stress`
    and `projection.stressed_summary` populated.
    """
    _require_read()
    base = get_liquidity(
        company=company,
        fiscal_year=fiscal_year,
        projection_months=projection_months,
        projection_baseline=projection_baseline,
        collection_mode=collection_mode,
        collection_schedule=collection_schedule,
        payment_schedule=payment_schedule,
    )
    return _apply_stress_to_projection(
        base,
        int(stress_collection_days or 0),
        float(stress_revenue_pct or 0),
        float(stress_cost_pct or 0),
    )


@frappe.whitelist()
def get_sensitivity_tornado(
    company: str,
    fiscal_year: str | int,
    projection_months: str | int = 6,
    projection_baseline: str = "committed",
    collection_mode: str = "best_case",
    collection_schedule: str | dict | None = None,
    payment_schedule: str | dict | None = None,
) -> dict:
    """Compute a tornado dataset — each lever's impact on ending cash.

    For each of the three levers, runs a standardised mild/moderate/severe
    stress and reports the ending-cash delta vs base case. The lever with
    the largest range is what the CFO should worry about most. This is the
    real value of sensitivity analysis: ranking the risks.

    Standardised stresses (one-sided, downside only — that's what stress
    tests measure):
      collection_days: +10 (mild), +20 (moderate), +30 (severe)
      revenue_pct:     -5  (mild), -10 (moderate), -20 (severe)
      cost_pct:        +3  (mild), +7  (moderate), +12 (severe)
    """
    _require_read()
    base = get_liquidity(
        company=company,
        fiscal_year=fiscal_year,
        projection_months=projection_months,
        projection_baseline=projection_baseline,
        collection_mode=collection_mode,
        collection_schedule=collection_schedule,
        payment_schedule=payment_schedule,
    )
    proj_rows = (base.get("projection") or {}).get("rows") or []
    base_ending = float(proj_rows[-1].get("closing") if proj_rows else 0)

    LEVERS = [
        {"key": "collection_days", "label": "Collection delay (days)",
         "stresses": [("mild", 10, 0, 0), ("moderate", 20, 0, 0), ("severe", 30, 0, 0)]},
        {"key": "revenue_pct", "label": "Revenue shortfall",
         "stresses": [("mild", 0, -5, 0), ("moderate", 0, -10, 0), ("severe", 0, -20, 0)]},
        {"key": "cost_pct", "label": "Cost inflation",
         "stresses": [("mild", 0, 0, 3), ("moderate", 0, 0, 7), ("severe", 0, 0, 12)]},
    ]

    results = []
    for lev in LEVERS:
        scenarios = []
        for (name, d, r, c) in lev["stresses"]:
            stressed = _apply_stress_to_projection(base, d, r, c)
            srows = (stressed.get("projection") or {}).get("rows") or []
            ending = float(srows[-1].get("closing") if srows else 0)
            low_point = min((float(rr.get("closing") or 0) for rr in srows), default=0)
            scenarios.append({
                "name": name,
                "label": {"mild": "Mild", "moderate": "Moderate", "severe": "Severe"}[name],
                "ending_cash": ending,
                "low_point": low_point,
                "delta": ending - base_ending,
                "goes_negative": low_point < 0,
            })
        # Range = severe ending - base ending (always negative since downside-only).
        range_magnitude = abs(scenarios[-1]["delta"])
        results.append({
            "lever": lev["key"],
            "label": lev["label"],
            "scenarios": scenarios,
            "range_magnitude": range_magnitude,
        })

    # Sort by impact — the lever the CFO should focus on first.
    results.sort(key=lambda x: x["range_magnitude"], reverse=True)

    return {
        "base_ending_cash": base_ending,
        "levers": results,
        "currency": base.get("currency", ""),
        "horizon_months": int(projection_months),
    }
