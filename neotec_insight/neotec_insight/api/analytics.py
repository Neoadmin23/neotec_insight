"""Management analytics: financial ratios, rolling-12 trends, the fragility
(concentration) radar and the multi-year trend.

v2.92.0 — moved unchanged out of api/report.py, which re-exports every
name so the `api.report.<name>` endpoint addresses keep working."""

import json
import frappe
from frappe.utils import cint
from neotec_insight.neotec_insight.utils.execution import execute_report, load_flag_to_accounts
from neotec_insight.neotec_insight.api.report_shared import _require_read, _resolve_report_doc, _sanitise_dimension_filters


# ───────────────────────────────────────────────────────────────────────────
# Financial ratios (v1.9.24) — profitability, liquidity, efficiency
# ───────────────────────────────────────────────────────────────────────────

@frappe.whitelist()
def get_financial_ratios(company: str, fiscal_year: str | int) -> dict:
    """Standard financial ratios for the management dashboard.

    Computed directly from GL, classified by account root_type / account_type,
    so it is independent of any report definition and globally reusable:

      Profitability  — gross margin, EBITDA margin, net margin
      Liquidity      — current ratio, quick ratio, cash ratio
      Efficiency     — DSO, DPO, cash conversion cycle

    P&L figures are the fiscal year's activity; balance-sheet figures are the
    position as of the fiscal year end. All amounts in company currency.
    """
    _require_read()
    fy = cint(fiscal_year)
    # v1.9.59 — use company's fiscal year boundaries.
    from neotec_insight.neotec_insight.utils.fiscal_year import fiscal_year_bounds
    _fy_s, _fy_e = fiscal_year_bounds(company, fy)
    fy_start = _fy_s.isoformat()
    fy_end = _fy_e.isoformat()

    # Account metadata for the company.
    accounts = frappe.get_all(
        "Account",
        filters={"company": company, "is_group": 0},
        fields=["name", "root_type", "account_type"],
    )
    by_root: dict[str, list[str]] = {}
    by_type: dict[str, list[str]] = {}
    for a in accounts:
        by_root.setdefault(a.root_type or "", []).append(a.name)
        by_type.setdefault(a.account_type or "", []).append(a.name)

    def _sum_period(acc_names: list[str], signed: str) -> float:
        """Sum GL activity within the fiscal year for the given accounts.
        signed='credit_minus_debit' for income, 'debit_minus_credit' for expense."""
        if not acc_names:
            return 0.0
        ph = ", ".join(["%s"] * len(acc_names))
        expr = "credit - debit" if signed == "credit_minus_debit" else "debit - credit"
        row = frappe.db.sql(
            f"""SELECT COALESCE(SUM({expr}), 0) FROM `tabGL Entry`
                WHERE is_cancelled = 0 AND company = %s AND account IN ({ph})
                  AND posting_date >= %s AND posting_date <= %s""",
            [company, *acc_names, fy_start, fy_end],
        )
        return float(row[0][0] or 0.0) if row else 0.0

    def _balance_asof(acc_names: list[str], signed: str) -> float:
        """Net balance as of fiscal year end for the given accounts."""
        if not acc_names:
            return 0.0
        ph = ", ".join(["%s"] * len(acc_names))
        expr = "debit - credit" if signed == "debit_minus_credit" else "credit - debit"
        row = frappe.db.sql(
            f"""SELECT COALESCE(SUM({expr}), 0) FROM `tabGL Entry`
                WHERE is_cancelled = 0 AND company = %s AND account IN ({ph})
                  AND posting_date <= %s""",
            [company, *acc_names, fy_end],
        )
        return float(row[0][0] or 0.0) if row else 0.0

    # ── P&L figures (fiscal-year activity) ─────────────────────────────────
    revenue = _sum_period(by_root.get("Income", []), "credit_minus_debit")
    expenses = _sum_period(by_root.get("Expense", []), "debit_minus_credit")
    net_income = revenue - expenses
    # Direct cost = Cost of Goods Sold accounts; gross profit = revenue - COGS.
    cogs = _sum_period(by_type.get("Cost of Goods Sold", []), "debit_minus_credit")
    gross_profit = revenue - cogs
    # EBITDA = net income + depreciation/amortisation + interest (add-backs).
    deprec = _sum_period(by_type.get("Depreciation", []), "debit_minus_credit")
    ebitda = net_income + deprec  # interest add-back omitted; conservative

    # ── Balance-sheet figures (as of fiscal year end) ──────────────────────
    receivables = _balance_asof(by_type.get("Receivable", []), "debit_minus_credit")
    payables = _balance_asof(by_type.get("Payable", []), "credit_minus_debit")
    cash = _balance_asof(
        by_type.get("Bank", []) + by_type.get("Cash", []), "debit_minus_credit"
    )
    inventory = _balance_asof(by_type.get("Stock", []), "debit_minus_credit")
    current_assets = _balance_asof(
        [a.name for a in accounts if (a.account_type or "") in
         ("Receivable", "Bank", "Cash", "Stock") and a.root_type == "Asset"],
        "debit_minus_credit",
    )
    current_liabilities = _balance_asof(
        [a.name for a in accounts if (a.account_type or "") in
         ("Payable", "Tax") and a.root_type == "Liability"],
        "credit_minus_debit",
    )

    def _safe_div(a: float, b: float):
        return (a / b) if b not in (0, 0.0) else None

    # ── Ratios ─────────────────────────────────────────────────────────────
    profitability = [
        {"key": "gross_margin", "label": "Gross Margin",
         "value": _safe_div(gross_profit, revenue), "format": "pct",
         "good": "high", "benchmark": 0.40},
        {"key": "ebitda_margin", "label": "EBITDA Margin",
         "value": _safe_div(ebitda, revenue), "format": "pct",
         "good": "high", "benchmark": 0.15},
        {"key": "net_margin", "label": "Net Margin",
         "value": _safe_div(net_income, revenue), "format": "pct",
         "good": "high", "benchmark": 0.10},
    ]
    liquidity = [
        {"key": "current_ratio", "label": "Current Ratio",
         "value": _safe_div(current_assets, current_liabilities), "format": "ratio",
         "good": "high", "benchmark": 1.5},
        {"key": "quick_ratio", "label": "Quick Ratio",
         "value": _safe_div(current_assets - inventory, current_liabilities), "format": "ratio",
         "good": "high", "benchmark": 1.0},
        {"key": "cash_ratio", "label": "Cash Ratio",
         "value": _safe_div(cash, current_liabilities), "format": "ratio",
         "good": "high", "benchmark": 0.5},
    ]
    # DSO = receivables / revenue * 365 ; DPO = payables / cogs * 365.
    dso = _safe_div(receivables, revenue)
    dso = dso * 365 if dso is not None else None
    dpo = _safe_div(payables, cogs)
    dpo = dpo * 365 if dpo is not None else None
    ccc = (dso - dpo) if (dso is not None and dpo is not None) else None
    efficiency = [
        {"key": "dso", "label": "DSO (days)", "value": dso, "format": "days",
         "good": "low", "benchmark": 45},
        {"key": "dpo", "label": "DPO (days)", "value": dpo, "format": "days",
         "good": "low", "benchmark": 60},
        {"key": "ccc", "label": "Cash Conversion Cycle (days)", "value": ccc,
         "format": "days", "good": "low", "benchmark": 30},
    ]

    return {
        "company": company,
        "fiscal_year": fy,
        "inputs": {
            "revenue": revenue, "cogs": cogs, "gross_profit": gross_profit,
            "ebitda": ebitda, "net_income": net_income,
            "current_assets": current_assets, "current_liabilities": current_liabilities,
            "cash": cash, "inventory": inventory,
            "receivables": receivables, "payables": payables,
        },
        "groups": [
            {"key": "profitability", "label": "Profitability", "ratios": profitability},
            {"key": "liquidity", "label": "Liquidity", "ratios": liquidity},
            {"key": "efficiency", "label": "Efficiency", "ratios": efficiency},
        ],
    }


# ───────────────────────────────────────────────────────────────────────────
# Rolling-12-month trends (v1.9.37)
# ───────────────────────────────────────────────────────────────────────────

@frappe.whitelist()
def get_rolling_12(
    report: str,
    fiscal_year: str | int,
    company: str | None = None,
    cost_center: str | None = None,
    project: str | None = None,
    department: str | None = None,
    branch: str | None = None,
) -> dict:
    """Return rolling-12-month monthly series for each P&L row of a report.

    The window ends at the last completed calendar month of the selected
    fiscal year (or 'now', whichever is earlier — we never project actuals
    into the future). For a historical fiscal year, that's December of that
    year. For the current fiscal year, it's the most recent completed month.

    Composition: 12 monthly values stitched across the end-year and the year
    before it, so rolling-12 always has 12 points regardless of where in the
    fiscal year we are.

    Used by the dashboard's KPI sparklines when 'Rolling-12' is selected.
    """
    _require_read()
    fy = cint(fiscal_year)
    if not fy:
        frappe.throw("fiscal_year is required.")

    doc = _resolve_report_doc(report)
    company = company or doc.company

    # Decide the end month — last completed calendar month within fy that is
    # not in the future. For a historical year, that's Dec; for the current
    # year, it's last month (or current month-1 if today's day <= 1).
    today = frappe.utils.getdate(frappe.utils.nowdate())
    if fy < today.year:
        end_month = 12     # 1-indexed
        end_year = fy
    elif fy == today.year:
        end_month = max(1, today.month - 1)   # last completed month
        end_year = fy
    else:
        # Future fiscal year requested — no actuals to roll. Return empties.
        end_month = 12
        end_year = fy

    # Run the engine for two years and stitch the last 12 months ending at
    # (end_year, end_month).
    definition = json.loads(doc.definition_json or "{}")
    flag_to_accounts = load_flag_to_accounts(doc.name)

    def _run_year(y: int):
        try:
            return execute_report(
                report_def=definition,
                fiscal_year=y,
                month_from=0,
                month_to=11,
                segment="total",
                cost_center=cost_center,
                project=project,
                department=department,
                branch=branch,
                company=company,
                flag_to_accounts=flag_to_accounts,
            )
        except Exception:
            return {"rows": []}

    cur_year = _run_year(end_year)
    prev_year = _run_year(end_year - 1)

    # Build {row_key: {month_0..11: amount}} for both years.
    def _by_key(rows):
        out = {}
        for r in rows or []:
            k = r.get("key")
            if not k:
                continue
            out[k] = r.get("monthly") or {}
        return out

    cur_by_key = _by_key(cur_year.get("rows"))
    prev_by_key = _by_key(prev_year.get("rows"))

    # 12-point series: iterate backwards from (end_year, end_month) for 12.
    series_keys = []  # list of (year, month_idx) — month_idx is 0-based
    y, m = end_year, end_month - 1   # m is 0-based
    for _step in range(12):
        series_keys.append((y, m))
        m -= 1
        if m < 0:
            m = 11
            y -= 1
    series_keys.reverse()    # oldest first

    # Compose the rows: for each P&L row, the 12 stitched values + labels.
    all_keys = sorted(set(list(cur_by_key.keys()) + list(prev_by_key.keys())))
    out_rows = []
    for k in all_keys:
        series = []
        for (yy, mm) in series_keys:
            book = cur_by_key if yy == end_year else prev_by_key
            val = book.get(k, {}).get(mm) or 0.0
            try:
                val = float(val)
            except Exception:
                val = 0.0
            series.append(val)
        out_rows.append({"key": k, "series": series})

    # Month labels for tooltips/legends (e.g. 'Dec-25').
    MN = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    months_label = [
        {"month": mm, "year": yy, "label": f"{MN[mm]}-{str(yy)[2:]}"}
        for (yy, mm) in series_keys
    ]

    return {
        "report": doc.name,
        "end_year": end_year,
        "end_month": end_month,
        "fiscal_year": fy,
        "months": months_label,
        "rows": out_rows,
    }


# ───────────────────────────────────────────────────────────────────────────
# Fragility radar (v1.9.41) — Section 3 of the CFO Briefing
# ───────────────────────────────────────────────────────────────────────────
# Concentration risk: where the business is exposed to a single party, a
# single bucket, or a single dimension that could hurt cash if it broke.
#
# Metrics computed:
#   - Customer concentration (top-5, HHI) — from submitted Sales Invoices
#   - Supplier concentration (top-5, HHI) — from submitted Purchase Invoices
#   - Receivables ageing skew (% in 90+ days) — from outstanding SI
#   - Branch concentration (top-5) — from submitted SI when branch is tagged
#
# All metrics are computed for a chosen fiscal year. Status thresholds are
# stated in the response itself (visible to users in the UI help text)
# so a reviewer can audit our judgement.


def _hhi(shares: list[float]) -> float:
    """Herfindahl-Hirschman Index — concentration measure used by US DOJ/FTC
    merger guidelines. Each share is a percentage (0..100). HHI of 10000 =
    one party has 100% share. < 1500 = low concentration, > 2500 = high.
    """
    return sum(s * s for s in shares)


@frappe.whitelist()
def get_fragility_radar(
    company: str,
    fiscal_year: str | int,
    top_n: int = 5,
) -> dict:
    _require_read()  # v2.77.0 — returns customer revenue concentration
    """Compute the fragility/concentration radar for a company+fiscal year.

    Returns a dict with four sub-blocks (some may be empty if data isn't
    tagged in this bench — e.g. no branches → branch block is omitted).
    Each block has the same shape:
      {
        "metric": <name>,
        "status": "green" | "amber" | "red",
        "headline": <plain-English sentence — only what the data supports>,
        "details": <metric-specific table>,
      }
    """
    fy = cint(fiscal_year)
    if not company:
        frappe.throw("company is required.")
    if not fy:
        frappe.throw("fiscal_year is required.")
    top_n = max(3, min(20, cint(top_n) or 5))

    today = frappe.utils.getdate(frappe.utils.nowdate())

    # v1.9.59 — use company FY boundaries instead of YEAR(posting_date) = fy.
    from neotec_insight.neotec_insight.utils.fiscal_year import fiscal_year_bounds
    _fy_s, _fy_e = fiscal_year_bounds(company, fy)
    fy_start_d = _fy_s.isoformat()
    fy_end_d = _fy_e.isoformat()

    # ── Customer concentration ────────────────────────────────────────────
    # Aggregate submitted Sales Invoices by customer for the fiscal year.
    si_rows = frappe.db.sql(
        """SELECT customer, SUM(base_grand_total) AS amt
           FROM `tabSales Invoice`
           WHERE company = %s AND docstatus = 1
             AND posting_date BETWEEN %s AND %s
           GROUP BY customer
           ORDER BY amt DESC""",
        [company, fy_start_d, fy_end_d],
        as_dict=True,
    )
    cust_total = sum(float(r["amt"] or 0) for r in si_rows)
    customer_block = _concentration_block(
        items=si_rows, item_key="customer", item_label="customer",
        total=cust_total, top_n=top_n,
        metric_label="Customer concentration",
        denom_label="of revenue",
    )

    # ── Supplier concentration ────────────────────────────────────────────
    pi_rows = frappe.db.sql(
        """SELECT supplier, SUM(base_grand_total) AS amt
           FROM `tabPurchase Invoice`
           WHERE company = %s AND docstatus = 1
             AND posting_date BETWEEN %s AND %s
           GROUP BY supplier
           ORDER BY amt DESC""",
        [company, fy_start_d, fy_end_d],
        as_dict=True,
    )
    supp_total = sum(float(r["amt"] or 0) for r in pi_rows)
    supplier_block = _concentration_block(
        items=pi_rows, item_key="supplier", item_label="supplier",
        total=supp_total, top_n=top_n,
        metric_label="Supplier concentration",
        denom_label="of purchases",
    )

    # ── Receivables ageing skew ───────────────────────────────────────────
    # Reuse the same logic as get_liquidity: bucket outstanding SI by days
    # past due. The "skew" is the share in 90+ buckets.
    ar_rows = frappe.db.sql(
        """SELECT name, outstanding_amount, due_date
           FROM `tabSales Invoice`
           WHERE company = %s AND docstatus = 1 AND outstanding_amount > 0""",
        [company],
        as_dict=True,
    )
    buckets = {"not_due": 0.0, "0_30": 0.0, "30_60": 0.0, "60_90": 0.0, "90_180": 0.0, "180_plus": 0.0}
    for r in ar_rows:
        amt = float(r.get("outstanding_amount") or 0)
        if amt <= 0:
            continue
        due = r.get("due_date")
        if not due:
            buckets["0_30"] += amt
            continue
        days = (today - frappe.utils.getdate(due)).days
        if days <= 0:
            buckets["not_due"] += amt
        elif days <= 30:
            buckets["0_30"] += amt
        elif days <= 60:
            buckets["30_60"] += amt
        elif days <= 90:
            buckets["60_90"] += amt
        elif days <= 180:
            buckets["90_180"] += amt
        else:
            buckets["180_plus"] += amt

    ar_total = sum(buckets.values())
    aged_90plus = buckets["90_180"] + buckets["180_plus"]
    aged_pct = (aged_90plus / ar_total) if ar_total > 0 else 0.0

    # Status thresholds for AR skew — these are commonly cited credit-risk
    # rules of thumb (not regulatory like HHI, but defensible practice).
    if ar_total == 0:
        ar_status = "green"
        ar_headline = "No outstanding receivables."
    elif aged_pct < 0.10:
        ar_status = "green"
        ar_headline = f"Receivables are healthy: only {aged_pct * 100:.0f}% of outstanding AR is over 90 days overdue."
    elif aged_pct < 0.25:
        ar_status = "amber"
        ar_headline = f"Receivables ageing is concerning: {aged_pct * 100:.0f}% of outstanding AR is over 90 days overdue."
    else:
        ar_status = "red"
        ar_headline = f"Receivables ageing is poor: {aged_pct * 100:.0f}% of outstanding AR is over 90 days overdue. Collection risk is material."

    ageing_block = {
        "metric": "Receivables ageing skew",
        "status": ar_status,
        "headline": ar_headline,
        "details": {
            "total_outstanding": ar_total,
            "aged_90plus_amount": aged_90plus,
            "aged_90plus_share": aged_pct,
            "buckets": buckets,
            "threshold_explainer": "Green < 10% in 90+; Amber 10-25%; Red > 25%. A high share in 90+ buckets indicates collection risk.",
        },
    }

    # ── Branch concentration (conditional) ────────────────────────────────
    branch_block = None
    try:
        if frappe.db.has_column("Sales Invoice", "branch"):
            br_rows = frappe.db.sql(
                """SELECT branch, SUM(base_grand_total) AS amt
                   FROM `tabSales Invoice`
                   WHERE company = %s AND docstatus = 1
                     AND posting_date BETWEEN %s AND %s
                     AND branch IS NOT NULL AND branch != ''
                   GROUP BY branch
                   ORDER BY amt DESC""",
                [company, fy_start_d, fy_end_d],
                as_dict=True,
            )
            br_total = sum(float(r["amt"] or 0) for r in br_rows)
            if br_total > 0 and len(br_rows) >= 2:
                # Only worth showing if there are at least 2 branches — otherwise
                # "100% concentration" isn't a useful insight.
                branch_block = _concentration_block(
                    items=br_rows, item_key="branch", item_label="branch",
                    total=br_total, top_n=top_n,
                    metric_label="Branch concentration",
                    denom_label="of revenue",
                )
    except Exception:
        branch_block = None  # branch column or query failed — skip silently

    blocks = [customer_block, supplier_block, ageing_block]
    if branch_block:
        blocks.insert(2, branch_block)  # branch between supplier and ageing

    return {
        "company": company,
        "fiscal_year": fy,
        "blocks": [b for b in blocks if b],
    }


def _concentration_block(items, item_key, item_label, total, top_n, metric_label, denom_label):
    """Shared logic for customer/supplier/branch concentration blocks.

    Computes:
      - Top-N share (sum of top-N as % of total)
      - HHI (in 0..10000 scale, standard convention)
      - Top-3 share (used for the headline threshold)
      - Status: green/amber/red

    Thresholds (cited in the response):
      Top-3 < 30%: Green. 30-50%: Amber. >50%: Red.
      HHI < 1500: low concentration; 1500-2500 moderate; >2500 high (US DOJ).
    """
    if total == 0 or not items:
        return {
            "metric": metric_label,
            "status": "green",
            "headline": f"No {item_label} activity recorded — no concentration risk.",
            "details": {
                "total": 0,
                "top_n": [],
                "top_3_share": 0,
                "top_n_share": 0,
                "hhi": 0,
                "threshold_explainer": "Status uses top-3 share: <30% Green, 30-50% Amber, >50% Red. HHI shown for reference (DOJ standard).",
            },
        }
    shares = [(float(r["amt"] or 0) / total) * 100 for r in items]
    hhi = _hhi(shares)
    top_3 = sum(shares[:3])
    top_n_share = sum(shares[:top_n])

    if top_3 < 30:
        status = "green"
        headline = f"Top 3 {item_label}s are {top_3:.0f}% {denom_label} — within the comfort zone."
    elif top_3 < 50:
        status = "amber"
        headline = f"Top 3 {item_label}s are {top_3:.0f}% {denom_label} — meaningful concentration, worth monitoring."
    else:
        status = "red"
        headline = f"Top 3 {item_label}s are {top_3:.0f}% {denom_label} — high concentration. Losing one would materially affect the business."

    return {
        "metric": metric_label,
        "status": status,
        "headline": headline,
        "details": {
            "total": total,
            "top_n": [
                {"name": r.get(item_key) or "(unspecified)", "amount": float(r["amt"] or 0), "share": shares[i]}
                for i, r in enumerate(items[:top_n])
            ],
            "top_3_share": top_3,
            "top_n_share": top_n_share,
            "hhi": hhi,
            "threshold_explainer": "Status uses top-3 share: <30% Green, 30-50% Amber, >50% Red. HHI is the Herfindahl-Hirschman Index (DOJ standard): <1500 low, 1500-2500 moderate, >2500 high.",
        },
    }


# ───────────────────────────────────────────────────────────────────────────
# Multi-year trend (v1.9.47) — Section 6 of the CFO Briefing
# ───────────────────────────────────────────────────────────────────────────
# Runs execute_report once per year for N years. Consistency with the rest
# of the app matters more than raw speed: same engine → same numbers → no
# divergence risk between trend view and per-year P&L.
#
# Honest scope: P&L rows only. Balance sheet trend is a separate engine
# path and a separate release if needed.


@frappe.whitelist()
def get_multi_year_trend(
    report: str,
    end_year: int,
    years: int = 5,
    breakdown: str = "total",
    company: str | None = None,
    dimension_filters: dict | str | None = None,
) -> dict:
    """Multi-year trend of the report's KPI rows.

    Runs execute_report for each year [end_year - years + 1 .. end_year] and
    returns annual totals per row. When breakdown='branch', additionally
    runs per-branch for each year.

    v1.9.52 — accepts dimension_filters dict for custom Accounting Dimensions.
    Forwarded to execute_report; sanitised against the discovered dimension
    set to prevent unsafe column injection.

    Returns:
      years: list of fiscal years in chronological order
      rows: { row_key: { label, values: [year_total, ...], yoy: [...] } }
      branches: only present when breakdown='branch' —
        { branch_name: { row_key: { values: [...] } } }
      kpi_rows: list of row keys that are recognised as the headline KPIs
        (a UI hint; full row list is also returned)
      synthesis: a single auditable sentence summarising the headline trend
    """
    _require_read()
    end_year = cint(end_year)
    if not end_year:
        frappe.throw("end_year is required.")
    years = max(2, min(10, cint(years) or 5))
    breakdown = (breakdown or "total").strip().lower()
    if breakdown not in ("total", "branch"):
        breakdown = "total"

    doc = _resolve_report_doc(report)
    company = company or doc.company
    definition = json.loads(doc.definition_json or "{}")
    flag_to_accounts = load_flag_to_accounts(doc.name)
    dim_filters_safe = _sanitise_dimension_filters(dimension_filters)

    year_list = list(range(end_year - years + 1, end_year + 1))

    # ── Total-by-year pass ────────────────────────────────────────────────
    rows_by_key: dict[str, dict] = {}
    for y in year_list:
        try:
            res = execute_report(
                report_def=definition,
                fiscal_year=y,
                month_from=0,
                month_to=11,
                segment="total",
                company=company,
                flag_to_accounts=flag_to_accounts,
                dimension_filters=dim_filters_safe or None,
            )
        except Exception:
            res = {"rows": []}
        for r in (res.get("rows") or []):
            k = r.get("key")
            if not k:
                continue
            if k not in rows_by_key:
                rows_by_key[k] = {
                    "key": k,
                    "label": r.get("label") or k,
                    "values": [0.0] * len(year_list),
                }
            year_idx = year_list.index(y)
            monthly = r.get("monthly") or {}
            total = sum(float(monthly.get(m) or 0) for m in range(12))
            rows_by_key[k]["values"][year_idx] = total

    # Compute Y/Y growth for each row's values (None for first year).
    for v in rows_by_key.values():
        vals = v["values"]
        yoy = [None]
        for i in range(1, len(vals)):
            prev = vals[i - 1]
            if prev == 0:
                yoy.append(None)
            else:
                yoy.append((vals[i] - prev) / abs(prev))
        v["yoy"] = yoy

    # Add a synthesised gross_margin_pct row (no engine run needed).
    rev = rows_by_key.get("total_revenue")
    gp = rows_by_key.get("gross_profit")
    if rev and gp:
        margin_vals = []
        for i in range(len(year_list)):
            r = rev["values"][i]
            g = gp["values"][i]
            margin_vals.append((g / r) if r != 0 else 0.0)
        margin_yoy = [None]
        for i in range(1, len(margin_vals)):
            prev = margin_vals[i - 1]
            margin_yoy.append((margin_vals[i] - prev) if prev != 0 or margin_vals[i] != 0 else None)
        rows_by_key["gross_margin_pct"] = {
            "key": "gross_margin_pct",
            "label": "Gross Margin %",
            "values": margin_vals,
            "yoy": margin_yoy,
            "is_ratio": True,
        }

    # ── Branch breakdown pass (only if requested) ────────────────────────
    branches_data: dict = {}
    if breakdown == "branch" and frappe.db.has_column("GL Entry", "branch"):
        # Discover distinct branches that actually appear in this company's
        # GL for the year range. This is more accurate than listing every
        # Branch doctype record — some may have no activity.
        first_year = year_list[0]
        last_year = year_list[-1]
        # v1.9.59 — use company FY bounds for the multi-year discovery window.
        from neotec_insight.neotec_insight.utils.fiscal_year import fiscal_year_bounds
        _start_d, _ = fiscal_year_bounds(company, first_year)
        _, _end_d = fiscal_year_bounds(company, last_year)
        try:
            br_rows = frappe.db.sql(
                """SELECT DISTINCT branch
                   FROM `tabGL Entry`
                   WHERE company = %s
                     AND branch IS NOT NULL AND branch != ''
                     AND posting_date BETWEEN %s AND %s""",
                [company, _start_d.isoformat(), _end_d.isoformat()],
                as_dict=True,
            )
            branches_list = [r["branch"] for r in br_rows]
        except Exception:
            branches_list = []

        for br in branches_list:
            br_rows_by_key: dict[str, list[float]] = {}
            for y in year_list:
                try:
                    res = execute_report(
                        report_def=definition,
                        fiscal_year=y,
                        month_from=0,
                        month_to=11,
                        segment="total",
                        branch=br,
                        company=company,
                        flag_to_accounts=flag_to_accounts,
                        dimension_filters=dim_filters_safe or None,
                    )
                except Exception:
                    res = {"rows": []}
                for r in (res.get("rows") or []):
                    k = r.get("key")
                    if not k:
                        continue
                    if k not in br_rows_by_key:
                        br_rows_by_key[k] = [0.0] * len(year_list)
                    year_idx = year_list.index(y)
                    monthly = r.get("monthly") or {}
                    br_rows_by_key[k][year_idx] = sum(float(monthly.get(m) or 0) for m in range(12))
            branches_data[br] = br_rows_by_key

    # ── Synthesis sentence (revenue-led, honest fallback if data is thin) ─
    synthesis = _build_trend_synthesis(rows_by_key, year_list)

    return {
        "report": doc.name,
        "company": company,
        "years": year_list,
        "rows": list(rows_by_key.values()),
        "kpi_rows": ["total_revenue", "gross_profit", "ebitda", "net_income", "gross_margin_pct"],
        "branches": branches_data,
        "synthesis": synthesis,
        "breakdown": breakdown,
    }


def _build_trend_synthesis(rows_by_key: dict, year_list: list) -> str:
    """One sentence summarising the revenue trend. Honest with the data:
    if data is missing or signal is weak, returns a plain factual statement
    rather than a fabricated narrative.
    """
    rev = rows_by_key.get("total_revenue")
    if not rev or len(year_list) < 2:
        return ""
    vals = rev["values"]
    if vals[0] == 0 or vals[-1] == 0:
        return ""
    years_span = year_list[-1] - year_list[0]
    if years_span < 1:
        return ""
    cagr = (vals[-1] / vals[0]) ** (1.0 / years_span) - 1.0
    # Find best year.
    best_year_idx = 0
    best_yoy = -999.0
    yoy = rev.get("yoy") or []
    for i in range(1, len(yoy)):
        if yoy[i] is not None and yoy[i] > best_yoy:
            best_yoy = yoy[i]
            best_year_idx = i
    cagr_str = f"{cagr * 100:.0f}%"
    if abs(cagr) < 0.01:
        return f"Revenue has been broadly flat across the {years_span + 1}-year window."
    direction = "grown" if cagr > 0 else "declined"
    if best_yoy > 0:
        return f"Revenue has {direction} at a {cagr_str} CAGR over {years_span + 1} years; the strongest year was FY{year_list[best_year_idx]}."
    return f"Revenue has {direction} at a {cagr_str} CAGR over {years_span + 1} years."
