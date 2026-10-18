"""Multi-company group view: aggregation and currency translation
(Layer 1 — intercompany is not eliminated) (v1.9.38).

v2.92.0 — moved unchanged out of api/report.py, which re-exports every
name so the `api.report.<name>` endpoint addresses keep working."""

import json
import frappe
from frappe.utils import cint
from neotec_insight.neotec_insight.utils.execution import execute_report, load_flag_to_accounts
from neotec_insight.neotec_insight.api.report_shared import _check_group_view_access, _require_read, _resolve_report_doc, _sanitise_dimension_filters


@frappe.whitelist()
def list_group_companies() -> list[dict]:
    """Return all enabled companies in the bench, with their default currency.
    Used by the group-view multi-select picker on the dashboard.
    """
    _require_read()
    _check_group_view_access()
    rows = frappe.get_all(
        "Company",
        fields=["name", "company_name", "default_currency", "is_group", "parent_company"],
        order_by="company_name asc",
        limit_page_length=0,
    )
    return [
        {
            "name": r["name"],
            "label": r.get("company_name") or r["name"],
            "currency": r.get("default_currency") or "",
            "is_group": int(r.get("is_group") or 0),
            "parent_company": r.get("parent_company") or "",
        }
        for r in rows
    ]


def _avg_rate_for_month(from_currency: str, to_currency: str, year: int, month_idx: int) -> float:
    """Average Currency Exchange rate for a calendar month.

    Reads daily rates from `tabCurrency Exchange` and averages them. Returns 1.0
    if currencies are identical or no rates can be found (with a caller-side
    warning expected). month_idx is 0-based; we convert to 1-based for SQL.
    """
    if not from_currency or not to_currency or from_currency == to_currency:
        return 1.0
    m1 = month_idx + 1
    # Last day of the month — use Python rather than SQL to avoid DB-specific syntax.
    if m1 == 12:
        next_first = f"{year + 1}-01-01"
    else:
        next_first = f"{year}-{m1 + 1:02d}-01"
    first = f"{year}-{m1:02d}-01"
    rows = frappe.db.sql(
        """SELECT exchange_rate FROM `tabCurrency Exchange`
           WHERE from_currency = %s AND to_currency = %s
             AND date >= %s AND date < %s""",
        [from_currency, to_currency, first, next_first],
    )
    if rows:
        rates = [float(r[0] or 0) for r in rows if r[0]]
        if rates:
            return sum(rates) / len(rates)
    # Fallback 1: most recent rate before this month.
    prev = frappe.db.sql(
        """SELECT exchange_rate FROM `tabCurrency Exchange`
           WHERE from_currency = %s AND to_currency = %s AND date < %s
           ORDER BY date DESC LIMIT 1""",
        [from_currency, to_currency, first],
    )
    if prev and prev[0][0]:
        return float(prev[0][0])
    # Fallback 2: inverse pair (the customer might only maintain one direction).
    inv = frappe.db.sql(
        """SELECT exchange_rate FROM `tabCurrency Exchange`
           WHERE from_currency = %s AND to_currency = %s AND date < %s
           ORDER BY date DESC LIMIT 1""",
        [to_currency, from_currency, next_first],
    )
    if inv and inv[0][0] and float(inv[0][0]) > 0:
        return 1.0 / float(inv[0][0])
    # No rate at all — return 1.0 and let the UI flag the gap.
    return 1.0


@frappe.whitelist()
def get_group_dashboard(
    report: str,
    fiscal_year: str | int,
    companies: str | list,
    presentation_currency: str | None = None,
    dimension_filters: dict | str | None = None,
) -> dict:
    """Aggregate one P&L definition across multiple companies for one fiscal year.

    Returns:
      - group: KPI totals in presentation_currency (revenue, gross_profit,
        ebitda, net_income; monthly series stitched for sparklines)
      - by_company: each company in its OWN currency, with the same KPI set
      - meta: which rate dates were used, gaps in FX data, banner text

    v1.9.52 — dimension_filters is applied to each per-company execute_report
    call, sanitised against the discovered dimension set.

    Layer 1: aggregation only. Does NOT eliminate intercompany.
    """
    _require_read()
    _check_group_view_access()
    fy = cint(fiscal_year)
    if isinstance(companies, str):
        try:
            companies = json.loads(companies)
        except Exception:
            companies = [c.strip() for c in companies.split(",") if c.strip()]
    if not companies or not isinstance(companies, list):
        frappe.throw("companies must be a non-empty list of company names.")
    if len(companies) > 50:
        frappe.throw("Too many companies — please limit to 50 per request.")

    doc = _resolve_report_doc(report)
    definition = json.loads(doc.definition_json or "{}")
    flag_to_accounts = load_flag_to_accounts(doc.name)
    dim_filters_safe = _sanitise_dimension_filters(dimension_filters)

    # Resolve each company's native currency.
    company_meta = {}
    for c in companies:
        cur = frappe.db.get_value("Company", c, "default_currency") or ""
        company_meta[c] = {"currency": cur, "name": c}

    # Pick presentation currency: default to the first company's currency
    # if not specified (a sensible default — usually the holding company is
    # listed first or already passed in).
    pres_cur = presentation_currency or company_meta[companies[0]]["currency"] or ""

    KPI_KEYS = ["total_revenue", "gross_profit", "ebitda", "net_income"]
    months_all = list(range(12))

    # Per-company native run.
    by_company: list[dict] = []
    # Group totals in presentation currency: keyed by KPI key, then by month.
    group_monthly: dict[str, list[float]] = {k: [0.0] * 12 for k in KPI_KEYS}
    # FX gap tracker — months where we fell back to a default rate.
    fx_gaps: list[dict] = []

    for c in companies:
        cur = company_meta[c]["currency"] or pres_cur

        try:
            res = execute_report(
                report_def=definition,
                fiscal_year=fy,
                month_from=0,
                month_to=11,
                segment="total",
                company=c,
                flag_to_accounts=flag_to_accounts,
                dimension_filters=dim_filters_safe or None,
            )
        except Exception as e:
            frappe.log_error(f"group_dashboard exec failed for {c}: {e}", "Neotec Insight: group")
            res = {"rows": []}

        rows_by_key = {r.get("key"): r for r in (res.get("rows") or [])}
        per_company_kpis = {}

        for k in KPI_KEYS:
            row = rows_by_key.get(k)
            monthly_native = []
            for m in months_all:
                v = float((row or {}).get("monthly", {}).get(m) or 0.0)
                monthly_native.append(v)
            per_company_kpis[k] = {
                "monthly_native": monthly_native,
                "total_native": sum(monthly_native),
            }

        # Translate to presentation currency, monthly average rates.
        per_company_pres = {}
        if cur == pres_cur:
            rates = [1.0] * 12
        else:
            rates = []
            has_gap = False
            for m in months_all:
                r = _avg_rate_for_month(cur, pres_cur, fy, m)
                rates.append(r)
                if r == 1.0 and cur != pres_cur:
                    has_gap = True
            if has_gap:
                fx_gaps.append({"company": c, "from": cur, "to": pres_cur})

        for k in KPI_KEYS:
            monthly_pres = [
                per_company_kpis[k]["monthly_native"][m] * rates[m]
                for m in months_all
            ]
            per_company_pres[k] = {
                "monthly_pres": monthly_pres,
                "total_pres": sum(monthly_pres),
            }
            for m in months_all:
                group_monthly[k][m] += monthly_pres[m]

        by_company.append({
            "company": c,
            "currency": cur,
            "kpis": {
                k: {
                    "native_total": per_company_kpis[k]["total_native"],
                    "native_monthly": per_company_kpis[k]["monthly_native"],
                    "presentation_total": per_company_pres[k]["total_pres"],
                }
                for k in KPI_KEYS
            },
            "rates_used": rates if cur != pres_cur else None,
        })

    group_totals = {
        k: {"total": sum(group_monthly[k]), "monthly": group_monthly[k]}
        for k in KPI_KEYS
    }

    return {
        "report": doc.name,
        "fiscal_year": fy,
        "companies": companies,
        "presentation_currency": pres_cur,
        "group": group_totals,
        "by_company": by_company,
        "fx_gaps": fx_gaps,
        "notes": {
            "layer": "Layer 1 — aggregation only",
            "intercompany": "Not eliminated. Intercompany transactions are included in both revenue and expense.",
            "rate_convention": "Period average rate per month, from ERPNext Currency Exchange.",
            "comparatives": "Prior-year figures use prior-year rates (no retranslation).",
        },
    }
