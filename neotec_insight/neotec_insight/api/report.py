from __future__ import annotations

import base64
import calendar
from datetime import date
import hashlib
import io
import json
import re
import time
from typing import Any

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate

from neotec_insight.neotec_insight.utils.execution import (
    execute_report,
    load_flag_to_accounts,
    flag_binding_meta,
)
from neotec_insight.neotec_insight.utils.mapping_rules import (
    autosuggest_unmapped_for_report,
    suggest_flag_for_code,
)
from neotec_insight.neotec_insight.utils.map_importer import (
    apply_map_to_report,
    read_map_sheet,
)
from neotec_insight.neotec_insight.utils.report_structure_importer import (
    import_report_structure,
)
from neotec_insight.neotec_insight.utils.config_backup_registry import (
    CONFIG_REGISTRY,
    EXCLUDED_FROM_CONFIG_BACKUP,
    compute_coverage,
)

EXECUTION_CACHE_TTL_SECONDS = 300



def _require_write(doctype: str) -> None:
    """Refuse a write from a user who lacks permission on the doctype.

    Frappe checks permissions inside `doc.save()`, but these endpoints use
    `ignore_permissions=True`, `frappe.db.set_value` or raw SQL — all of which
    bypass that check. Whitelisted and unguarded, they were callable directly
    over `/api/method/...` by any authenticated user, including a read-only
    one. The guard restores the check the bypass removed.
    """
    if not frappe.has_permission(doctype, "write"):
        frappe.throw(
            _("You are not permitted to modify {0}.").format(_(doctype)),
            frappe.PermissionError,
        )



def _require_read() -> None:
    """Refuse a financial read from a user with no ledger access.

    `@frappe.whitelist()` requires a login, not a role — so without this every
    report endpoint was callable over `/api/method/...` by any authenticated
    user, including Website and portal users who have no business seeing the
    ledger. Reading GL Entry is the right test: ERPNext already restricts it
    to the accounts roles, so this inherits whatever the site has configured
    rather than inventing a second permission model.
    """
    if not frappe.has_permission("GL Entry", "read"):
        frappe.throw(
            _("You are not permitted to view financial reports."),
            frappe.PermissionError,
        )
    # v2.84.0 — a People-only user may hold GL Entry read through an HR role
    # bundle, which would otherwise let them straight into the ledger. The
    # People endpoints live in cfo.py and do not pass through here, so they
    # keep working.
    _check_hr_only(allow=False)


@frappe.whitelist()
def list_quick_links() -> list[dict]:
    """Return the enabled Insight Quick Links for the header menu.

    Ordered by sort_order then label. Used by the 'Back to ERP' dropdown in
    the Insight header — these are the user-configurable links (the fixed
    'Back to ERP Desk' item is hardcoded in the frontend).
    """
    rows = frappe.get_all(
        "Insight Quick Link",
        filters={"enabled": 1},
        fields=["label", "url", "icon", "open_in_new_tab", "sort_order"],
        order_by="sort_order asc, label asc",
    )
    return rows


@frappe.whitelist()
def get_csrf():
    """Return a freshly-generated CSRF token for SPA clients.

    Whitelisted so GET works without a token, but auth still required —
    Guests get rejected upstream. The frontend retries failed POSTs after
    fetching here. Follows the documented frappe-react-sdk pattern: generate,
    commit, stamp into boot, return.
    """
    import frappe.sessions

    token = frappe.sessions.get_csrf_token() or ""
    try:
        frappe.db.commit()  # nosemgrep — required so the new token sticks
    except Exception:
        pass

    try:
        boot = frappe.sessions.get()
        if isinstance(boot, dict):
            sess = boot.get("session")
            if not isinstance(sess, dict):
                sess = {}
                boot["session"] = sess
            sess["csrf_token"] = token
            boot["csrf_token"] = token
    except Exception:
        pass

    return {"csrf_token": token, "user": frappe.session.user}


# ─── ERPNext master lookups ──────────────────────────────────────────────
#
# These endpoints power the filter dropdowns on the Run tab. They return the
# real masters (Company, Cost Center, Project, Department) from the user's
# ERPNext database, not hardcoded values. All return tuple-friendly shapes
# {name, label} so the frontend can render labels independently of IDs.


def _safe_columns(table: str) -> set[str]:
    """Return the set of column names on a table.

    Defensive helper: some Frappe versions don't have `frappe.db.has_column`,
    some return wrong values for child tables, and on a fresh install certain
    tables don't exist yet. This wraps the SQL DESCRIBE in a try/except and
    returns an empty set on any failure — callers can then treat "column
    absent" as the safe default.
    """
    try:
        cols = frappe.db.sql(f"SHOW COLUMNS FROM `{table}`", as_dict=True)
        return {c["Field"] for c in cols}
    except Exception:
        return set()


@frappe.whitelist()
def list_companies() -> list[dict]:
    """Return all companies the user can see. Used by the Company dropdown."""
    _require_read()
    try:
        if not frappe.db.exists("DocType", "Company"):
            return []
        rows = frappe.get_all(
            "Company",
            fields=["name", "company_name", "abbr", "default_currency", "country"],
            order_by="company_name asc",
            limit_page_length=0,
        )
        return [
            {
                "name": r["name"],
                "label": r.get("company_name") or r["name"],
                "abbr": r.get("abbr") or "",
                "currency": r.get("default_currency") or "",
                "country": r.get("country") or "",
            }
            for r in rows
        ]
    except Exception as e:
        frappe.log_error(f"list_companies failed: {e}", "Neotec Insight: masters")
        return []


@frappe.whitelist()
def list_cost_centers(company: str | None = None, search: str = "", limit: int = 100) -> list[dict]:
    """Return Cost Centers, filtered by company and optional search term.

    Only returns non-group (leaf) cost centers. Returns [] on any error so
    the frontend dropdown never gets a 500.
    """
    _require_read()
    try:
        if not frappe.db.exists("DocType", "Cost Center"):
            return []
        cols = _safe_columns("tabCost Center")
        filters: dict = {}
        if "is_group" in cols:
            filters["is_group"] = 0
        if company and "company" in cols:
            filters["company"] = company

        or_filters: list = []
        if search and search.strip():
            s = search.strip()
            if "cost_center_name" in cols:
                or_filters.append(["cost_center_name", "like", f"%{s}%"])
            or_filters.append(["name", "like", f"%{s}%"])

        # Build the field list dynamically — never select a column that doesn't exist.
        fields = ["name"]
        for f in ("cost_center_name", "parent_cost_center", "company", "is_group"):
            if f in cols:
                fields.append(f)

        rows = frappe.get_all(
            "Cost Center",
            filters=filters,
            or_filters=or_filters,
            fields=fields,
            order_by=("cost_center_name asc" if "cost_center_name" in cols else "name asc"),
            limit_page_length=cint(limit) or 100,
        )
        return [
            {
                "name": r["name"],
                "label": r.get("cost_center_name") or r["name"],
                "parent": r.get("parent_cost_center") or "",
                "company": r.get("company") or "",
            }
            for r in rows
        ]
    except Exception as e:
        frappe.log_error(f"list_cost_centers failed: {e}", "Neotec Insight: masters")
        return []


@frappe.whitelist()
def list_projects(company: str | None = None, search: str = "", limit: int = 100, status: str | None = None) -> list[dict]:
    """Return Projects, filtered by company, search term, and status."""
    _require_read()
    try:
        if not frappe.db.exists("DocType", "Project"):
            return []
        cols = _safe_columns("tabProject")
        filters: dict = {}
        if company and "company" in cols:
            filters["company"] = company
        if "status" in cols:
            if status is None:
                filters["status"] = "Open"
            elif status:
                filters["status"] = status

        or_filters: list = []
        if search and search.strip():
            s = search.strip()
            if "project_name" in cols:
                or_filters.append(["project_name", "like", f"%{s}%"])
            or_filters.append(["name", "like", f"%{s}%"])

        fields = ["name"]
        for f in ("project_name", "status", "company"):
            if f in cols:
                fields.append(f)

        rows = frappe.get_all(
            "Project",
            filters=filters,
            or_filters=or_filters,
            fields=fields,
            order_by=("project_name asc" if "project_name" in cols else "name asc"),
            limit_page_length=cint(limit) or 100,
        )
        return [
            {
                "name": r["name"],
                "label": r.get("project_name") or r["name"],
                "status": r.get("status") or "",
                "company": r.get("company") or "",
            }
            for r in rows
        ]
    except Exception as e:
        frappe.log_error(f"list_projects failed: {e}", "Neotec Insight: masters")
        return []


@frappe.whitelist()
def list_departments(company: str | None = None, search: str = "", limit: int = 100) -> list[dict]:
    """Return Departments.

    Department doctype varies between Frappe HR (`is_group`, `company` present)
    and Frappe core (sometimes neither column exists). We probe columns first
    and only filter by what actually exists. Everything wraps in a top-level
    try/except so a missing column or table never 500s the endpoint.
    """
    _require_read()
    try:
        if not frappe.db.exists("DocType", "Department"):
            return []
        cols = _safe_columns("tabDepartment")
        if not cols:
            # Table doesn't physically exist yet — DocType registered but never migrated.
            return []

        filters: dict = {}
        if "is_group" in cols:
            filters["is_group"] = 0
        if "disabled" in cols:
            filters["disabled"] = 0
        if company and "company" in cols:
            filters["company"] = company

        or_filters: list = []
        if search and search.strip():
            s = search.strip()
            if "department_name" in cols:
                or_filters.append(["department_name", "like", f"%{s}%"])
            or_filters.append(["name", "like", f"%{s}%"])

        fields = ["name"]
        for f in ("department_name", "parent_department", "company"):
            if f in cols:
                fields.append(f)

        rows = frappe.get_all(
            "Department",
            filters=filters,
            or_filters=or_filters,
            fields=fields,
            order_by=("department_name asc" if "department_name" in cols else "name asc"),
            limit_page_length=cint(limit) or 100,
        )
        return [
            {
                "name": r["name"],
                "label": r.get("department_name") or r["name"],
                "parent": r.get("parent_department") or "",
                "company": r.get("company") or "",
            }
            for r in rows
        ]
    except Exception as e:
        frappe.log_error(f"list_departments failed: {e}", "Neotec Insight: masters")
        return []


@frappe.whitelist()
def list_branches(search: str = "", limit: int = 100) -> list[dict]:
    """Return Branches.

    Branch is an ERPNext doctype used as an accounting dimension. Like
    Department, it varies by ERPNext version — the table may not exist on
    older benches. We probe columns and fall back gracefully.
    """
    _require_read()
    try:
        if not frappe.db.exists("DocType", "Branch"):
            return []
        cols = _safe_columns("tabBranch")
        if not cols:
            return []

        or_filters: list = []
        if search and search.strip():
            s = search.strip()
            if "branch" in cols:
                or_filters.append(["branch", "like", f"%{s}%"])
            or_filters.append(["name", "like", f"%{s}%"])

        fields = ["name"]
        if "branch" in cols:
            fields.append("branch")

        rows = frappe.get_all(
            "Branch",
            or_filters=or_filters,
            fields=fields,
            order_by=("branch asc" if "branch" in cols else "name asc"),
            limit_page_length=cint(limit) or 100,
        )
        return [
            {
                "name": r["name"],
                "label": r.get("branch") or r["name"],
            }
            for r in rows
        ]
    except Exception as e:
        frappe.log_error(f"list_branches failed: {e}", "Neotec Insight: masters")
        return []


@frappe.whitelist()
def list_fiscal_years(limit: int = 12) -> list[dict]:
    """Return Fiscal Years from ERPNext, most-recent first."""
    _require_read()
    try:
        if not frappe.db.exists("DocType", "Fiscal Year"):
            return []
        rows = frappe.get_all(
            "Fiscal Year",
            fields=["name", "year_start_date", "year_end_date", "disabled"],
            filters={"disabled": 0},
            order_by="year_start_date desc",
            limit_page_length=cint(limit) or 12,
        )
        out = []
        for r in rows:
            year_int = None
            if r.get("year_end_date"):
                try:
                    year_int = int(str(r["year_end_date"])[:4])
                except Exception:
                    pass
            if year_int is None and r.get("year_start_date"):
                try:
                    year_int = int(str(r["year_start_date"])[:4])
                except Exception:
                    pass
            out.append({
                "name": r["name"],
                "year_int": year_int,
                "year_start_date": str(r.get("year_start_date") or ""),
                "year_end_date": str(r.get("year_end_date") or ""),
            })
        return out
    except Exception as e:
        frappe.log_error(f"list_fiscal_years failed: {e}", "Neotec Insight: masters")
        return []


@frappe.whitelist()
def list_reports(include_disabled: int = 0) -> list[dict]:
    """List all visible reports for the user's picker.

    By default, only `is_active = 1` reports are returned. Admins can pass
    `include_disabled=1` to see the full list (used by the Insight Report
    Definition desk view, not the chat UI).

    Sort order: default report first (so the frontend can use the first item
    as its default selection), then most recently modified.
    """
    _require_read()
    filters = {}
    if not cint(include_disabled):
        filters["is_active"] = 1
    rows = frappe.get_all(
        "Insight Report Definition",
        filters=filters,
        fields=[
            "name",
            "report_name",
            "slug",
            "description",
            "is_active",
            "is_default",
            "presentation_format",
            "version",
            "company",
            "report_type",
            "comparison_mode",
            "prior_years",
            "default_expand",
            "hide_zero_accounts",
            "hide_group_accounts",
            "modified",
        ],
        order_by="is_default desc, modified desc",
    )
    return rows


@frappe.whitelist()
def get_report(report: str) -> dict:
    _require_read()
    doc = _resolve_report_doc(report)
    return _serialize_report(doc)


@frappe.whitelist(methods=["POST"])
def save_report(payload: str | dict) -> dict:
    _check_edit_permission()
    data = frappe.parse_json(payload)
    if not isinstance(data, dict):
        frappe.throw("Payload must be an object.")
    name = data.get("name")
    if name:
        doc = frappe.get_doc("Insight Report Definition", name)
    else:
        doc = frappe.new_doc("Insight Report Definition")
        doc.slug = data.get("slug") or frappe.scrub(data.get("report_name") or "")
    doc.report_name = data.get("report_name") or doc.report_name
    doc.description = data.get("description") or doc.description
    doc.is_active = cint(data.get("is_active", 1))
    doc.is_default = cint(data.get("is_default", 0))
    # v1.9.48 — presentation format (vertical | t_account). Defaults to
    # vertical to preserve existing behaviour. Constrained to known values.
    pf = (data.get("presentation_format") or "vertical").strip().lower()
    if pf not in ("vertical", "t_account"):
        pf = "vertical"
    doc.presentation_format = pf
    # v1.9.53 — optional default Letter Head for print/export.
    plh = (data.get("print_letter_head") or "").strip()
    if plh and frappe.db.exists("Letter Head", plh):
        doc.print_letter_head = plh
    elif not plh:
        doc.print_letter_head = ""
    # If a stale value comes through (Letter Head deleted), drop it silently
    # rather than throwing — the next save fixes it.
    doc.company = data.get("company") or doc.company
    doc.comparison_mode = data.get("comparison_mode") or "vs_budget"
    doc.prior_years = cint(data.get("prior_years", 1))
    doc.definition_json = json.dumps(data.get("definition") or {})
    doc.column_schema_json = json.dumps(data.get("columns") or [])
    doc.filter_schema_json = json.dumps(data.get("filters") or [])
    doc.version = cint(doc.version or 0) + 1
    if doc.is_new():
        doc.insert()
    else:
        doc.save()
    _bump_cache_gen(doc.name)
    return _serialize_report(doc)


@frappe.whitelist()
def run_report_dimension_pivot(
    report: str,
    fiscal_year: int,
    month_from: int = 0,
    month_to: int = 11,
    pivot_by: str = "cost_center",
    company: str | None = None,
    include_unassigned: int = 1,
    use_cache: int = 1,
) -> dict:
    """Run a P&L pivoted by dimension instead of by period.

    Reuses the existing execution engine — for each dimension value, runs
    `execute_report` with that value as the filter, then sums months into a
    single number per (row × dimension_value). Same flags, same formulas,
    same row definitions as the period view.

    `pivot_by` is one of: cost_center, department, project — OR any custom
    Accounting Dimension fieldname configured in this bench (validated
    server-side against the discovered dimension set).
    `company` filters which dimension values to include (and which GL entries
    each value pulls from).

    Returns:
        {
            "report": {name, report_name, slug},
            "filters": {fiscal_year, month_from, month_to, pivot_by, company},
            "dimensions": [{name, label, total}, ...]   # the column headers
            "rows": [
                {key, kind, label, formula?, by_dim: {dim_name: amount}, total: amount}
            ],
            "performance": {execution_ms, cache_hit},
        }

    The Total column is computed as the sum across all visible dimension
    values (not a separate query). When include_unassigned=1, GL entries
    with NO value for the chosen dimension get bucketed as "(Unassigned)".
    """
    _require_read()
    doc = _resolve_report_doc(report)
    fy = cint(fiscal_year)
    mf = max(0, min(cint(month_from), 11))
    mt = max(mf, min(cint(month_to), 11))
    pivot_by = (pivot_by or "cost_center").strip()
    # v1.9.52 — accept any fieldname in the valid dimension set (natives +
    # configured custom Accounting Dimensions).
    valid_pivots = _all_valid_dimension_fieldnames()
    if pivot_by not in valid_pivots:
        frappe.throw(
            f"Invalid pivot_by '{pivot_by}'. Use one of the native dimensions "
            "(cost_center, department, project, branch) or a configured "
            "Accounting Dimension fieldname."
        )

    cache_key = _execution_cache_key(
        report_name=doc.name,
        user=frappe.session.user,
        fiscal_year=fy,
        month_from=mf,
        month_to=mt,
        pivot_by=pivot_by,
        company=company,
        kind="dim_pivot",
    )
    if cint(use_cache):
        cached = frappe.cache().get_value(cache_key)
        if isinstance(cached, dict):
            cached["performance"] = {**cached.get("performance", {}), "cache_hit": True}
            return cached

    started = time.perf_counter()
    flag_to_accounts = load_flag_to_accounts(doc.name)
    definition = json.loads(doc.definition_json or "{}")

    # Resolve which dimension values to include. Fetch from the same masters
    # endpoints the frontend uses so the list matches.
    dim_values = _list_dimension_values(pivot_by, company)

    # Run the report once per dimension value. Each call is one round-trip but
    # the queries are narrow (small GL slice for that one cost center / project).
    # For very large dimension lists this could be slow; we cap at 100 to be safe.
    if len(dim_values) > 100:
        dim_values = dim_values[:100]

    months = list(range(mf, mt + 1))

    def _run_one(dim_filter: dict) -> dict:
        # Split native vs custom dimension filter routing. Natives go to the
        # engine's named params; customs go through dimension_filters dict.
        native = {k: v for k, v in dim_filter.items() if k in ("cost_center", "project", "department", "branch")}
        custom = {k: v for k, v in dim_filter.items() if k not in native}
        return execute_report(
            report_def=definition,
            fiscal_year=fy,
            month_from=mf,
            month_to=mt,
            segment="total",
            cost_center=native.get("cost_center"),
            project=native.get("project"),
            department=native.get("department"),
            branch=native.get("branch"),
            company=company,
            flag_to_accounts=flag_to_accounts,
            dimension_filters=custom or None,
        )

    # Per-dimension execution. Result shape: {dim_name: {row_key: total_amount}}
    by_dim: dict[str, dict[str, float]] = {}
    for dv in dim_values:
        result = _run_one({pivot_by: dv["name"]})
        per_row: dict[str, float] = {}
        for r in result.get("rows", []):
            mvals = r.get("monthly") or {}
            total = sum(float(mvals.get(m, 0.0) or 0.0) for m in months)
            per_row[r["key"]] = round(total, 2)
        by_dim[dv["name"]] = per_row

    # The "Total" column. Run once with NO dimension filter — that's the true
    # company-wide number, not the sum of visible dim values (which would
    # exclude GL entries that have no dimension set at all).
    total_result = _run_one({})
    total_per_row: dict[str, float] = {}
    for r in total_result.get("rows", []):
        mvals = r.get("monthly") or {}
        total_per_row[r["key"]] = round(sum(float(mvals.get(m, 0.0) or 0.0) for m in months), 2)

    # Build the output rows in the same order as the report definition.
    out_rows: list[dict] = []
    for row in definition.get("rows", []):
        out_rows.append({
            "key": row.get("key"),
            "kind": row.get("kind"),
            "label": row.get("label"),
            "formula": row.get("formula"),
            "flag": row.get("flag"),
            "by_dim": {dv["name"]: by_dim.get(dv["name"], {}).get(row["key"], 0.0) for dv in dim_values},
            "total": total_per_row.get(row.get("key"), 0.0),
        })

    # Column metadata — labels + per-column total (sum of all data rows under that column).
    # Helps the frontend show a "column total" without re-summing.
    dim_meta = []
    for dv in dim_values:
        # Use the first source row's "Revenue"-equivalent if it exists, else 0
        col_total = 0.0
        for r in out_rows:
            if r.get("key") in {"total_revenue", "revenue"}:
                col_total = r["by_dim"].get(dv["name"], 0.0)
                break
        dim_meta.append({
            "name": dv["name"],
            "label": dv.get("label") or dv["name"],
            "company": dv.get("company") or "",
            "revenue": col_total,
        })

    payload = {
        "report": {"name": doc.name, "report_name": doc.report_name, "slug": doc.slug},
        "filters": {
            "fiscal_year": fy,
            "month_from": mf,
            "month_to": mt,
            "pivot_by": pivot_by,
            "company": company,
        },
        "dimensions": dim_meta,
        "rows": out_rows,
        "binding_meta": flag_binding_meta(doc.name, flag_to_accounts, definition.get("rows", [])),
        "performance": {"execution_ms": int((time.perf_counter() - started) * 1000), "cache_hit": False},
    }
    frappe.cache().set_value(cache_key, payload, expires_in_sec=EXECUTION_CACHE_TTL_SECONDS)
    return payload


def _list_dimension_values(pivot_by: str, company: str | None) -> list[dict]:
    """Return the list of dimension values for the pivot, mirroring the
    masters endpoints used by the Run tab filters. Pass an empty list when
    the underlying DocType is missing on this bench (e.g. no Frappe HR).

    v1.9.52 — supports custom Accounting Dimensions: pivot_by may be any
    discovered fieldname; we look up the dimension's DocType and read values
    via list_dimension_values (defensive on label column).
    """
    if pivot_by == "cost_center":
        return list_cost_centers(company=company, limit=200)
    if pivot_by == "project":
        return list_projects(company=company, limit=200, status="")
    if pivot_by == "department":
        return list_departments(company=company, limit=200)
    if pivot_by == "branch":
        return list_branches(limit=200)
    # Custom Accounting Dimension — look up by fieldname and pull values.
    try:
        return list_dimension_values(fieldname=pivot_by, limit=200)
    except Exception:
        return []


@frappe.whitelist()
def run_trial_balance(
    report: str,
    company: str,
    fiscal_year: str | int,
    as_of_date: str,
    from_date: str | None = None,
    cost_center: str | list | None = None,
    project: str | list | None = None,
    department: str | list | None = None,
    branch: str | list | None = None,
    root_types: str | list[str] | None = None,
    finance_book: str | None = None,
    dimension_filters: str | dict | None = None,
    show_group_accounts: int = 1,
    show_zero_values: int = 0,
    presentation_currency: str | None = None,
    use_cache: int = 1,
) -> dict:
    """Run a trial balance.

    Args:
        report: slug or name of an Insight Report Definition.
        company: ERPNext Company.
        fiscal_year: Fiscal Year doctype name or year as int.
        as_of_date: end of the period.
        cost_center / project / department: built-in dimension filters.
        root_types: optional list of root types to include.
        finance_book: optional Finance Book filter.
        dimension_filters: JSON object {gl_column: value} for custom
            Accounting Dimensions discovered at runtime.
        show_group_accounts: 1 to include group rollup rows.
        show_zero_values: 1 to keep all-zero rows.
        presentation_currency: convert all figures to this currency at the
            as-of-date single rate. Empty = company currency, no conversion.
        use_cache: 1 to use the 5-minute execution cache.
    """
    _require_read()
    from neotec_insight.neotec_insight.utils.balance_execution import run_trial_balance_engine

    doc = _resolve_report_doc(report)
    if not company:
        frappe.throw("company is required.")
    if not as_of_date:
        frappe.throw("as_of_date is required (YYYY-MM-DD).")

    fy_start = _resolve_fiscal_year_start(fiscal_year, company)
    rt_list = _parse_root_types(root_types)
    dim_filters = _parse_dimension_filters(dimension_filters)
    # v1.9.58 — normalise native dim params (multi-select).
    cost_center = _normalise_dim_param(cost_center)
    project = _normalise_dim_param(project)
    department = _normalise_dim_param(department)
    branch = _normalise_dim_param(branch)
    # `department` is itself a custom dimension on most ERPNext setups, but it
    # also frequently exists as a built-in GL column — fold it into the
    # dimension filter dict so the engine handles it uniformly.
    if department:
        dim_filters = {**dim_filters, "department": department}
    if branch:
        dim_filters = {**dim_filters, "branch": branch}

    cache_key = _execution_cache_key(
        report_name=doc.name, user=frappe.session.user,
        company=company, fy_start=fy_start, as_of_date=as_of_date,
        from_date=from_date or "",
        cost_center=sorted(cost_center) if cost_center else cost_center,
        project=sorted(project) if project else project,
        root_types=",".join(rt_list) if rt_list else "",
        finance_book=finance_book or "",
        dims=json.dumps(dim_filters, sort_keys=True, default=str),
        sga=cint(show_group_accounts), szv=cint(show_zero_values),
        pc=presentation_currency or "",
        kind="trial_balance",
    )
    if cint(use_cache):
        cached = frappe.cache().get_value(cache_key)
        if isinstance(cached, dict):
            cached["performance"] = {**cached.get("performance", {}), "cache_hit": True}
            return cached

    started = time.perf_counter()
    try:
        result = run_trial_balance_engine(
            company=company, fiscal_year_start=fy_start,
            as_of_date=as_of_date, from_date=from_date or None,
            cost_center=cost_center,
            project=project, root_types=rt_list,
            finance_book=finance_book, dimension_filters=dim_filters,
            show_group_accounts=bool(cint(show_group_accounts)),
            show_zero_values=bool(cint(show_zero_values)),
            presentation_currency=presentation_currency or None,
        )
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "Trial balance failed")
        frappe.throw(f"Trial balance failed: {e}")

    payload = {
        "report": {
            "name": doc.name, "report_name": doc.report_name,
            "slug": doc.slug, "report_type": getattr(doc, "report_type", "trial_balance"),
        },
        "filters": {
            "company": company, "fiscal_year": fiscal_year,
            "fiscal_year_start": fy_start, "as_of_date": as_of_date,
            "from_date": from_date,
            "cost_center": cost_center, "project": project, "department": department,
            "root_types": rt_list, "finance_book": finance_book,
            "dimension_filters": dim_filters,
            "show_group_accounts": cint(show_group_accounts),
            "show_zero_values": cint(show_zero_values),
            "presentation_currency": presentation_currency or None,
        },
        "result": result,
        "performance": {"execution_ms": int((time.perf_counter() - started) * 1000), "cache_hit": False},
    }
    frappe.cache().set_value(cache_key, payload, expires_in_sec=EXECUTION_CACHE_TTL_SECONDS)
    return payload


def _parse_dimension_filters(raw: str | dict | None) -> dict:
    """Parse the dimension_filters argument into a clean {column: value} dict."""
    if not raw:
        return {}
    if isinstance(raw, str):
        try:
            parsed = frappe.parse_json(raw)
        except Exception:
            return {}
    else:
        parsed = raw
    if not isinstance(parsed, dict):
        return {}
    return {str(k): v for k, v in parsed.items() if v}


@frappe.whitelist()
def list_report_filter_options(company: str | None = None) -> dict:
    """One call that returns everything the TB/BS filter strip needs:
    custom accounting dimensions, finance books, and currencies.

    Bundled into a single endpoint so the frontend filter strip can populate
    in one round-trip rather than three.
    """
    _require_read()
    from neotec_insight.neotec_insight.utils.balance_execution import (
        list_accounting_dimensions, get_company_currency,
    )

    # Custom accounting dimensions (Business Division, etc.) discovered live.
    dimensions = list_accounting_dimensions()
    # For each dimension, fetch its selectable values so the UI can show a
    # dropdown. The dimension's document_type is the source doctype.
    for d in dimensions:
        try:
            vals = frappe.get_all(
                d["document_type"],
                fields=["name"],
                limit_page_length=200,
                order_by="name asc",
            )
            d["options"] = [v["name"] for v in vals]
        except Exception:
            d["options"] = []

    # Finance Books.
    try:
        finance_books = frappe.get_all(
            "Finance Book", fields=["name"], limit_page_length=0, order_by="name asc",
        )
        finance_books = [fb["name"] for fb in finance_books]
    except Exception:
        finance_books = []

    # Currencies — enabled ones only.
    try:
        currencies = frappe.get_all(
            "Currency",
            filters={"enabled": 1},
            fields=["name", "currency_name", "symbol"],
            limit_page_length=0,
            order_by="name asc",
        )
    except Exception:
        currencies = []

    company_currency = get_company_currency(company) if company else ""

    # Default decimal precision — read from System Settings' float_precision.
    # The report header lets the user override per view; this is just the
    # starting value. Falls back to 2 if unset.
    try:
        float_precision = cint(frappe.db.get_single_value("System Settings", "float_precision")) or 2
    except Exception:
        float_precision = 2

    # v1.9.59 — surface the company's FY start month so the From/To month
    # dropdowns in the filter strip can label themselves in fiscal-year
    # order (Apr-Mar for India, Jan-Dec for KSA, etc.) before any report
    # has been run.
    from neotec_insight.neotec_insight.utils.fiscal_year import get_company_fy_start_month
    fy_start_month = get_company_fy_start_month(company) if company else 1

    # v1.9.60 — reporting calendar catalogue for the group-reporting toggle.
    # "local" reflects the company's configured FY; "group" lets a Saudi
    # subsidiary render its books as Apr-Mar for parent-company reporting
    # without changing Company.year_start_date. Hardcoded two-option list
    # for now; later releases may expose this as a configurable DocType
    # for clients with more complex group structures.
    reporting_calendars = [
        {
            "key": "local",
            "label": "Local",
            "start_month": fy_start_month,
            "override": None,  # null = use company's configured calendar
        },
    ]
    # Only add the alternate option when it differs from local — no point
    # offering "Group: Apr-Mar" when local IS Apr-Mar.
    if fy_start_month != 4:
        reporting_calendars.append({
            "key": "group_apr_mar",
            "label": "Group (Apr–Mar)",
            "start_month": 4,
            "override": 4,
        })
    if fy_start_month != 1:
        reporting_calendars.append({
            "key": "local_jan_dec",
            "label": "Calendar (Jan–Dec)",
            "start_month": 1,
            "override": 1,
        })

    return {
        "dimensions": dimensions,
        "finance_books": finance_books,
        "currencies": currencies,
        "company_currency": company_currency,
        "float_precision": float_precision,
        "fy_start_month": fy_start_month,
        "reporting_calendars": reporting_calendars,
    }


@frappe.whitelist()
def run_trial_balance_parties(
    report: str,
    account: str,
    company: str,
    fiscal_year: str | int,
    as_of_date: str,
    cost_center: str | None = None,
    project: str | None = None,
    presentation_currency: str | None = None,
    use_cache: int = 1,
) -> dict:
    """Per-party breakdown for one Receivable/Payable account in a trial balance.

    Lazy-loaded: the frontend only calls this when the user clicks the +
    on a specific control account row.
    """
    _require_read()
    from neotec_insight.neotec_insight.utils.balance_execution import run_trial_balance_parties_engine

    doc = _resolve_report_doc(report)
    if not company or not account:
        frappe.throw("company and account are required.")

    fy_start = _resolve_fiscal_year_start(fiscal_year, company)
    cache_key = _execution_cache_key(
        report_name=doc.name, user=frappe.session.user,
        company=company, fy_start=fy_start, as_of_date=as_of_date,
        cost_center=cost_center, project=project, account=account,
        pc=presentation_currency or "",
        kind="trial_balance_parties",
    )
    if cint(use_cache):
        cached = frappe.cache().get_value(cache_key)
        if isinstance(cached, dict):
            cached["performance"] = {**cached.get("performance", {}), "cache_hit": True}
            return cached

    started = time.perf_counter()
    try:
        parties = run_trial_balance_parties_engine(
            company=company, account=account,
            fiscal_year_start=fy_start, as_of_date=as_of_date,
            cost_center=cost_center, project=project,
            presentation_currency=presentation_currency or None,
        )
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "Trial balance parties failed")
        frappe.throw(f"Per-party breakdown failed: {e}")

    payload = {
        "report": {
            "name": doc.name, "report_name": doc.report_name,
            "slug": doc.slug, "report_type": getattr(doc, "report_type", "trial_balance"),
        },
        "filters": {
            "company": company, "account": account,
            "fiscal_year": fiscal_year, "fiscal_year_start": fy_start,
            "as_of_date": as_of_date,
            "cost_center": cost_center, "project": project,
            "presentation_currency": presentation_currency or None,
        },
        "result": {"parties": parties},
        "performance": {"execution_ms": int((time.perf_counter() - started) * 1000), "cache_hit": False},
    }
    frappe.cache().set_value(cache_key, payload, expires_in_sec=EXECUTION_CACHE_TTL_SECONDS)
    return payload


@frappe.whitelist()
def run_balance_sheet(
    report: str,
    company: str,
    as_of_date: str,
    prior_as_of_date: str | None = None,
    cost_center: str | list | None = None,
    project: str | list | None = None,
    department: str | list | None = None,
    branch: str | list | None = None,
    finance_book: str | None = None,
    dimension_filters: str | dict | None = None,
    show_group_accounts: int = 1,
    show_zero_values: int = 0,
    show_unclosed_pl: int = 1,
    presentation_currency: str | None = None,
    use_cache: int = 1,
) -> dict:
    """Run a balance sheet. Optional `prior_as_of_date` adds a comparison column."""
    _require_read()
    from neotec_insight.neotec_insight.utils.balance_execution import run_balance_sheet_engine

    doc = _resolve_report_doc(report)
    if not company:
        frappe.throw("company is required.")
    if not as_of_date:
        frappe.throw("as_of_date is required (YYYY-MM-DD).")

    dim_filters = _parse_dimension_filters(dimension_filters)
    # v1.9.58 — normalise native dim params.
    cost_center = _normalise_dim_param(cost_center)
    project = _normalise_dim_param(project)
    department = _normalise_dim_param(department)
    branch = _normalise_dim_param(branch)
    if department:
        dim_filters = {**dim_filters, "department": department}
    if branch:
        dim_filters = {**dim_filters, "branch": branch}

    cache_key = _execution_cache_key(
        report_name=doc.name, user=frappe.session.user,
        company=company, as_of_date=as_of_date,
        prior_as_of_date=prior_as_of_date or "",
        cost_center=sorted(cost_center) if cost_center else cost_center,
        project=sorted(project) if project else project,
        finance_book=finance_book or "",
        dims=json.dumps(dim_filters, sort_keys=True, default=str),
        sga=cint(show_group_accounts), szv=cint(show_zero_values),
        sup=cint(show_unclosed_pl), pc=presentation_currency or "",
        kind="balance_sheet",
    )
    if cint(use_cache):
        cached = frappe.cache().get_value(cache_key)
        if isinstance(cached, dict):
            cached["performance"] = {**cached.get("performance", {}), "cache_hit": True}
            return cached

    started = time.perf_counter()
    try:
        result = run_balance_sheet_engine(
            company=company, as_of_date=as_of_date,
            prior_as_of_date=prior_as_of_date,
            cost_center=cost_center, project=project,
            finance_book=finance_book, dimension_filters=dim_filters,
            show_group_accounts=bool(cint(show_group_accounts)),
            show_zero_values=bool(cint(show_zero_values)),
            show_unclosed_pl=bool(cint(show_unclosed_pl)),
            presentation_currency=presentation_currency or None,
        )
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "Balance sheet failed")
        frappe.throw(f"Balance sheet failed: {e}")

    payload = {
        "report": {
            "name": doc.name, "report_name": doc.report_name,
            "slug": doc.slug, "report_type": getattr(doc, "report_type", "balance_sheet"),
        },
        "filters": {
            "company": company, "as_of_date": as_of_date,
            "prior_as_of_date": prior_as_of_date,
            "cost_center": cost_center, "project": project, "department": department,
            "finance_book": finance_book, "dimension_filters": dim_filters,
            "show_group_accounts": cint(show_group_accounts),
            "show_zero_values": cint(show_zero_values),
            "show_unclosed_pl": cint(show_unclosed_pl),
            "presentation_currency": presentation_currency or None,
        },
        "result": result,
        "performance": {"execution_ms": int((time.perf_counter() - started) * 1000), "cache_hit": False},
    }
    frappe.cache().set_value(cache_key, payload, expires_in_sec=EXECUTION_CACHE_TTL_SECONDS)
    return payload


@frappe.whitelist()
def run_trial_balance_pivot(
    report: str,
    company: str,
    fiscal_year: str | int,
    as_of_date: str,
    pivot_by: str = "cost_center",
    finance_book: str | None = None,
    show_group_accounts: int = 1,
    show_zero_values: int = 0,
    presentation_currency: str | None = None,
    use_cache: int = 1,
) -> dict:
    """Trial Balance pivoted by dimension — one closing balance per dimension."""
    _require_read()
    from neotec_insight.neotec_insight.utils.balance_execution import run_trial_balance_pivot_engine

    doc = _resolve_report_doc(report)
    if not company or not as_of_date:
        frappe.throw("company and as_of_date are required.")
    fy_start = _resolve_fiscal_year_start(fiscal_year, company)
    pivot_by = (pivot_by or "cost_center").strip()

    cache_key = _execution_cache_key(
        report_name=doc.name, user=frappe.session.user,
        company=company, fy_start=fy_start, as_of_date=as_of_date,
        pivot_by=pivot_by, finance_book=finance_book or "",
        sga=cint(show_group_accounts), szv=cint(show_zero_values),
        pc=presentation_currency or "", kind="trial_balance_pivot",
    )
    if cint(use_cache):
        cached = frappe.cache().get_value(cache_key)
        if isinstance(cached, dict):
            cached["performance"] = {**cached.get("performance", {}), "cache_hit": True}
            return cached

    started = time.perf_counter()
    try:
        result = run_trial_balance_pivot_engine(
            company=company, fiscal_year_start=fy_start, as_of_date=as_of_date,
            pivot_by=pivot_by, finance_book=finance_book,
            show_group_accounts=bool(cint(show_group_accounts)),
            show_zero_values=bool(cint(show_zero_values)),
            presentation_currency=presentation_currency or None,
        )
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "Trial balance pivot failed")
        frappe.throw(f"Trial balance pivot failed: {e}")

    payload = {
        "report": {
            "name": doc.name, "report_name": doc.report_name,
            "slug": doc.slug, "report_type": getattr(doc, "report_type", "trial_balance"),
        },
        "filters": {
            "company": company, "fiscal_year": fiscal_year,
            "fiscal_year_start": fy_start, "as_of_date": as_of_date,
            "pivot_by": pivot_by, "finance_book": finance_book,
            "show_group_accounts": cint(show_group_accounts),
            "show_zero_values": cint(show_zero_values),
            "presentation_currency": presentation_currency or None,
        },
        "result": result,
        "performance": {"execution_ms": int((time.perf_counter() - started) * 1000), "cache_hit": False},
    }
    frappe.cache().set_value(cache_key, payload, expires_in_sec=EXECUTION_CACHE_TTL_SECONDS)
    return payload


@frappe.whitelist()
def run_balance_sheet_pivot(
    report: str,
    company: str,
    as_of_date: str,
    pivot_by: str = "cost_center",
    finance_book: str | None = None,
    show_group_accounts: int = 1,
    show_zero_values: int = 0,
    presentation_currency: str | None = None,
    use_cache: int = 1,
) -> dict:
    """Balance Sheet pivoted by dimension — one balance column per dimension."""
    _require_read()
    from neotec_insight.neotec_insight.utils.balance_execution import run_balance_sheet_pivot_engine

    doc = _resolve_report_doc(report)
    if not company or not as_of_date:
        frappe.throw("company and as_of_date are required.")
    pivot_by = (pivot_by or "cost_center").strip()

    cache_key = _execution_cache_key(
        report_name=doc.name, user=frappe.session.user,
        company=company, as_of_date=as_of_date,
        pivot_by=pivot_by, finance_book=finance_book or "",
        sga=cint(show_group_accounts), szv=cint(show_zero_values),
        pc=presentation_currency or "", kind="balance_sheet_pivot",
    )
    if cint(use_cache):
        cached = frappe.cache().get_value(cache_key)
        if isinstance(cached, dict):
            cached["performance"] = {**cached.get("performance", {}), "cache_hit": True}
            return cached

    started = time.perf_counter()
    try:
        result = run_balance_sheet_pivot_engine(
            company=company, as_of_date=as_of_date,
            pivot_by=pivot_by, finance_book=finance_book,
            show_group_accounts=bool(cint(show_group_accounts)),
            show_zero_values=bool(cint(show_zero_values)),
            presentation_currency=presentation_currency or None,
        )
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "Balance sheet pivot failed")
        frappe.throw(f"Balance sheet pivot failed: {e}")

    payload = {
        "report": {
            "name": doc.name, "report_name": doc.report_name,
            "slug": doc.slug, "report_type": getattr(doc, "report_type", "balance_sheet"),
        },
        "filters": {
            "company": company, "as_of_date": as_of_date,
            "pivot_by": pivot_by, "finance_book": finance_book,
            "show_group_accounts": cint(show_group_accounts),
            "show_zero_values": cint(show_zero_values),
            "presentation_currency": presentation_currency or None,
        },
        "result": result,
        "performance": {"execution_ms": int((time.perf_counter() - started) * 1000), "cache_hit": False},
    }
    frappe.cache().set_value(cache_key, payload, expires_in_sec=EXECUTION_CACHE_TTL_SECONDS)
    return payload


@frappe.whitelist()
def run_balance_sheet_combo_pivot(
    report: str,
    company: str,
    as_of_date: str,
    dim1: str = "cost_center",
    dim2: str = "project",
    finance_book: str | None = None,
    show_group_accounts: int = 1,
    show_zero_values: int = 0,
    presentation_currency: str | None = None,
    use_cache: int = 1,
) -> dict:
    """Combo (two-dimension) Balance Sheet pivot — same account-tree structure
    as the dimension pivot, one column per (dim1 × dim2). Lets the Combo view
    reuse the Dimension view's renderer, Excel and Print."""
    _require_read()
    from neotec_insight.neotec_insight.utils.balance_execution import run_balance_sheet_combo_pivot_engine

    doc = _resolve_report_doc(report)
    if not company or not as_of_date:
        frappe.throw("company and as_of_date are required.")
    dim1 = (dim1 or "cost_center").strip()
    dim2 = (dim2 or "project").strip()

    cache_key = _execution_cache_key(
        report_name=doc.name, user=frappe.session.user,
        company=company, as_of_date=as_of_date,
        dim1=dim1, dim2=dim2, finance_book=finance_book or "",
        sga=cint(show_group_accounts), szv=cint(show_zero_values),
        pc=presentation_currency or "", kind="balance_sheet_combo_pivot",
    )
    if cint(use_cache):
        cached = frappe.cache().get_value(cache_key)
        if isinstance(cached, dict):
            cached["performance"] = {**cached.get("performance", {}), "cache_hit": True}
            return cached

    started = time.perf_counter()
    try:
        result = run_balance_sheet_combo_pivot_engine(
            company=company, as_of_date=as_of_date,
            dim1=dim1, dim2=dim2, finance_book=finance_book,
            show_group_accounts=bool(cint(show_group_accounts)),
            show_zero_values=bool(cint(show_zero_values)),
            presentation_currency=presentation_currency or None,
        )
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "Balance sheet combo pivot failed")
        frappe.throw(f"Balance sheet combo failed: {e}")

    payload = {
        "report": {
            "name": doc.name, "report_name": doc.report_name,
            "slug": doc.slug, "report_type": getattr(doc, "report_type", "balance_sheet"),
        },
        "filters": {
            "company": company, "as_of_date": as_of_date,
            "dim1": dim1, "dim2": dim2, "finance_book": finance_book,
            "show_group_accounts": cint(show_group_accounts),
            "show_zero_values": cint(show_zero_values),
            "presentation_currency": presentation_currency or None,
        },
        "result": result,
        "performance": {"execution_ms": int((time.perf_counter() - started) * 1000), "cache_hit": False},
    }
    frappe.cache().set_value(cache_key, payload, expires_in_sec=EXECUTION_CACHE_TTL_SECONDS)
    return payload


@frappe.whitelist()
def run_pnl_statement(
    report: str,
    company: str,
    from_date: str,
    to_date: str,
    cost_center: str | None = None,
    project: str | None = None,
    department: str | None = None,
    branch: str | None = None,
    finance_book: str | None = None,
    dimension_filters: str | dict | None = None,
    show_group_accounts: int = 1,
    show_zero_values: int = 0,
    presentation_currency: str | None = None,
    use_cache: int = 1,
) -> dict:
    """Run the chart-of-accounts Profit & Loss Statement for a date range."""
    _require_read()
    from neotec_insight.neotec_insight.utils.balance_execution import run_pnl_statement_engine

    doc = _resolve_report_doc(report)
    if not company:
        frappe.throw("company is required.")
    if not from_date or not to_date:
        frappe.throw("from_date and to_date are required (YYYY-MM-DD).")

    dim_filters = _parse_dimension_filters(dimension_filters)
    if department:
        dim_filters = {**dim_filters, "department": department}
    if branch:
        dim_filters = {**dim_filters, "branch": branch}

    cache_key = _execution_cache_key(
        report_name=doc.name, user=frappe.session.user,
        company=company, from_date=from_date, to_date=to_date,
        cost_center=cost_center, project=project,
        finance_book=finance_book or "",
        dims=json.dumps(dim_filters, sort_keys=True),
        sga=cint(show_group_accounts), szv=cint(show_zero_values),
        pc=presentation_currency or "",
        kind="pnl_statement",
    )
    if cint(use_cache):
        cached = frappe.cache().get_value(cache_key)
        if isinstance(cached, dict):
            cached["performance"] = {**cached.get("performance", {}), "cache_hit": True}
            return cached

    started = time.perf_counter()
    try:
        result = run_pnl_statement_engine(
            company=company, from_date=from_date, to_date=to_date,
            cost_center=cost_center, project=project,
            finance_book=finance_book, dimension_filters=dim_filters,
            show_group_accounts=bool(cint(show_group_accounts)),
            show_zero_values=bool(cint(show_zero_values)),
            presentation_currency=presentation_currency or None,
        )
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "P&L statement failed")
        frappe.throw(f"Profit & Loss Statement failed: {e}")

    payload = {
        "report": {
            "name": doc.name, "report_name": doc.report_name,
            "slug": doc.slug, "report_type": getattr(doc, "report_type", "pnl_statement"),
        },
        "filters": {
            "company": company, "from_date": from_date, "to_date": to_date,
            "cost_center": cost_center, "project": project, "department": department,
            "finance_book": finance_book, "dimension_filters": dim_filters,
            "show_group_accounts": cint(show_group_accounts),
            "show_zero_values": cint(show_zero_values),
            "presentation_currency": presentation_currency or None,
        },
        "result": result,
        "performance": {"execution_ms": int((time.perf_counter() - started) * 1000), "cache_hit": False},
    }
    frappe.cache().set_value(cache_key, payload, expires_in_sec=EXECUTION_CACHE_TTL_SECONDS)
    return payload


# ── P&L Statement by period (v2.65.0) ──────────────────────────────────────


def _pnl_period_slices(from_date: str, to_date: str, granularity: str) -> list[dict]:
    """Column definitions for a granularity, plus the months each rolls up.

    P&L figures are *flows*, so a quarter is the sum of its months. The engine
    is therefore run once per calendar month in range and every other column —
    quarter, half, YTD — is derived by addition. Twelve queries at worst,
    whatever combination of columns is asked for; re-running the engine per
    column would have been up to twenty-eight.

    Month slices are clamped to the requested range, so a report from 15 Feb
    to 20 Aug gives a part-month first and last column rather than silently
    widening to whole months.
    """
    fd, td = getdate(from_date), getdate(to_date)
    if td < fd:
        return []

    months: list[dict] = []
    y, m = fd.year, fd.month
    while (y, m) <= (td.year, td.month):
        first = date(y, m, 1)
        last = date(y, m, calendar.monthrange(y, m)[1])
        months.append({
            "key": f"{y:04d}-{m:02d}",
            "label": f"{calendar.month_abbr[m]} {y}",
            "from": str(max(first, fd)),
            "to": str(min(last, td)),
            "y": y, "m": m,
        })
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)

    g = (granularity or "month").lower()
    want_month = "month" in g
    want_quarter = "quarter" in g
    want_half = "half" in g
    # 'total' is what the client sends for the classic single-column report.
    # Without this alias it fell through to the monthly default and returned
    # twelve columns to a caller that asked for one.
    want_ytd = g in ("ytd", "total") or "ytd" in g
    if not any([want_month, want_quarter, want_half, want_ytd]):
        want_month = True

    cols: list[dict] = []
    if want_month:
        for mo in months:
            # from/to travel with the column. They were previously left on the
            # internal month list only, so the caller — which runs the engine
            # per month column — raised KeyError: 'from'.
            cols.append({"key": mo["key"], "label": mo["label"], "months": [mo["key"]],
                         "kind": "month", "from": mo["from"], "to": mo["to"]})

    def _bucket(size: int, kind: str, namer):
        seen: dict[tuple, list] = {}
        for mo in months:
            idx = (mo["m"] - 1) // size
            seen.setdefault((mo["y"], idx), []).append(mo["key"])
        for (yy, idx), keys in seen.items():
            cols.append({"key": f"{kind}-{yy}-{idx + 1}", "label": namer(yy, idx),
                         "months": keys, "kind": kind})

    if want_quarter:
        _bucket(3, "quarter", lambda yy, i: f"Q{i + 1} {yy}")
    if want_half:
        _bucket(6, "half", lambda yy, i: f"H{i + 1} {yy}")

    # A total column always closes the report — a statement whose columns do
    # not add to the period figure invites the reader to add them up by hand.
    if want_ytd or len(cols) != 1:
        cols.append({"key": "total", "label": "Total",
                     "months": [mo["key"] for mo in months], "kind": "total"})

    # Interleave rather than grouping by kind: a quarter column belongs
    # immediately after the last month it covers, which is how a comparative
    # statement is read. Sorting by (last month covered, kind) gives
    # Jan Feb Mar Q1 Apr May Jun Q2 H1 Total.
    order = {"month": 0, "quarter": 1, "half": 2, "total": 3}
    cols.sort(key=lambda c: (c["months"][-1] if c["months"] else "",
                             order.get(c["kind"], 9)))
    return cols


@frappe.whitelist()
def run_pnl_statement_periods(
    report: str,
    company: str,
    from_date: str,
    to_date: str,
    granularity: str = "month",
    cost_center: str | None = None,
    project: str | None = None,
    department: str | None = None,
    branch: str | None = None,
    finance_book: str | None = None,
    dimension_filters: str | dict | None = None,
    show_group_accounts: int = 1,
    show_zero_values: int = 0,
    presentation_currency: str | None = None,
    use_cache: int = 1,
) -> dict:
    """Profit & Loss Statement split into period columns.

    Same accounts and the same tree as the single-column report; each account
    carries an amount per column instead of one figure.
    """
    _require_read()
    started = time.perf_counter()
    cols = _pnl_period_slices(from_date, to_date, granularity)
    if not cols:
        frappe.throw("from_date must not be after to_date.")

    month_keys = sorted({k for c in cols for k in c["months"]})
    if len(month_keys) > 36:
        frappe.throw("That range spans too many months. Narrow it to three years or less.")

    # One engine run per month; everything else is addition.
    by_month: dict[str, dict] = {}
    base = None
    # Always run every calendar month in range, whatever columns were asked
    # for — quarters and halves are sums of these. Built with an explicit
    # month granularity so the set does not depend on the requested one.
    month_slices = [c for c in _pnl_period_slices(from_date, to_date, "month")
                    if c["kind"] == "month"]
    for sl in month_slices:
        run = run_pnl_statement(
            report=report, company=company, from_date=sl["from"], to_date=sl["to"],
            cost_center=cost_center, project=project, department=department, branch=branch,
            finance_book=finance_book, dimension_filters=dimension_filters,
            show_group_accounts=show_group_accounts, show_zero_values=1,
            presentation_currency=presentation_currency, use_cache=use_cache,
        )
        by_month[sl["key"]] = run["result"]
        if base is None:
            base = run

    if base is None:
        frappe.throw("No periods in range.")

    # Union of accounts across months, keeping the first month's tree order so
    # an account that only transacts in December still appears in its proper
    # place rather than being appended at the end.
    order: list[str] = []
    meta: dict[str, dict] = {}
    for mk in sorted(by_month):
        for a in by_month[mk].get("accounts", []):
            if a["name"] not in meta:
                meta[a["name"]] = a
                order.append(a["name"])

    accounts = []
    for name in order:
        a = meta[name]
        amounts = {}
        for c in cols:
            amounts[c["key"]] = flt(sum(
                next((x["amount"] for x in by_month.get(mk, {}).get("accounts", [])
                      if x["name"] == name), 0.0)
                for mk in c["months"]), 2)
        if not cint(show_zero_values) and not any(abs(v) > 0.005 for v in amounts.values()):
            continue
        accounts.append({**a, "amounts": amounts})

    def _sum(field):
        out = {}
        for c in cols:
            out[c["key"]] = flt(sum(
                (by_month.get(mk, {}).get("summary", {}) or {}).get(field, 0.0)
                for mk in c["months"]), 2)
        return out

    income, expense = _sum("total_income"), _sum("total_expense")
    net = {k: flt(income.get(k, 0.0) - expense.get(k, 0.0), 2) for k in income}

    return {
        "report": base["report"],
        "filters": {**base["filters"], "from_date": from_date, "to_date": to_date,
                    "granularity": granularity},
        "columns": cols,
        "result": {
            "accounts": accounts,
            "currency": base["result"].get("currency"),
            "summary": {"total_income": income, "total_expense": expense, "net_profit": net},
        },
        "performance": {"execution_ms": int((time.perf_counter() - started) * 1000),
                        "months_run": len(by_month)},
    }


@frappe.whitelist()
def run_pnl_statement_pivot(
    report: str,
    company: str,
    from_date: str,
    to_date: str,
    pivot_by: str = "cost_center",
    finance_book: str | None = None,
    show_group_accounts: int = 1,
    show_zero_values: int = 0,
    presentation_currency: str | None = None,
    use_cache: int = 1,
) -> dict:
    """Dimension-pivot variant of the CoA P&L — account tree rows, one column
    per cost center / department / project."""
    _require_read()
    from neotec_insight.neotec_insight.utils.balance_execution import run_pnl_statement_pivot_engine

    doc = _resolve_report_doc(report)
    if not company:
        frappe.throw("company is required.")
    if not from_date or not to_date:
        frappe.throw("from_date and to_date are required.")
    pivot_by = (pivot_by or "cost_center").strip()

    cache_key = _execution_cache_key(
        report_name=doc.name, user=frappe.session.user,
        company=company, from_date=from_date, to_date=to_date,
        pivot_by=pivot_by, finance_book=finance_book or "",
        sga=cint(show_group_accounts), szv=cint(show_zero_values),
        pc=presentation_currency or "",
        kind="pnl_statement_pivot",
    )
    if cint(use_cache):
        cached = frappe.cache().get_value(cache_key)
        if isinstance(cached, dict):
            cached["performance"] = {**cached.get("performance", {}), "cache_hit": True}
            return cached

    started = time.perf_counter()
    try:
        result = run_pnl_statement_pivot_engine(
            company=company, from_date=from_date, to_date=to_date,
            pivot_by=pivot_by, finance_book=finance_book,
            show_group_accounts=bool(cint(show_group_accounts)),
            show_zero_values=bool(cint(show_zero_values)),
            presentation_currency=presentation_currency or None,
        )
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "P&L statement pivot failed")
        frappe.throw(f"Profit & Loss pivot failed: {e}")

    payload = {
        "report": {
            "name": doc.name, "report_name": doc.report_name,
            "slug": doc.slug, "report_type": getattr(doc, "report_type", "pnl_statement"),
        },
        "filters": {
            "company": company, "from_date": from_date, "to_date": to_date,
            "pivot_by": pivot_by, "finance_book": finance_book,
            "show_group_accounts": cint(show_group_accounts),
            "show_zero_values": cint(show_zero_values),
            "presentation_currency": presentation_currency or None,
        },
        "result": result,
        "performance": {"execution_ms": int((time.perf_counter() - started) * 1000), "cache_hit": False},
    }
    frappe.cache().set_value(cache_key, payload, expires_in_sec=EXECUTION_CACHE_TTL_SECONDS)
    return payload


@frappe.whitelist()
def run_pnl_statement_combo_pivot(
    report: str,
    company: str,
    from_date: str,
    to_date: str,
    dim1: str = "cost_center",
    dim2: str = "project",
    finance_book: str | None = None,
    show_group_accounts: int = 1,
    show_zero_values: int = 0,
    presentation_currency: str | None = None,
    use_cache: int = 1,
) -> dict:
    """Combo (two-dimension) P&L statement pivot. Same account-tree structure
    as the dimension pivot, with one column per (dim1 × dim2) combination, so
    the Combo view shares the Dimension view's renderer, Excel and Print."""
    _require_read()
    from neotec_insight.neotec_insight.utils.balance_execution import run_pnl_statement_combo_pivot_engine

    doc = _resolve_report_doc(report)
    if not company:
        frappe.throw("company is required.")
    if not from_date or not to_date:
        frappe.throw("from_date and to_date are required.")
    dim1 = (dim1 or "cost_center").strip()
    dim2 = (dim2 or "project").strip()

    cache_key = _execution_cache_key(
        report_name=doc.name, user=frappe.session.user,
        company=company, from_date=from_date, to_date=to_date,
        dim1=dim1, dim2=dim2, finance_book=finance_book or "",
        sga=cint(show_group_accounts), szv=cint(show_zero_values),
        pc=presentation_currency or "",
        kind="pnl_statement_combo_pivot",
    )
    if cint(use_cache):
        cached = frappe.cache().get_value(cache_key)
        if isinstance(cached, dict):
            cached["performance"] = {**cached.get("performance", {}), "cache_hit": True}
            return cached

    started = time.perf_counter()
    try:
        result = run_pnl_statement_combo_pivot_engine(
            company=company, from_date=from_date, to_date=to_date,
            dim1=dim1, dim2=dim2, finance_book=finance_book,
            show_group_accounts=bool(cint(show_group_accounts)),
            show_zero_values=bool(cint(show_zero_values)),
            presentation_currency=presentation_currency or None,
        )
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "P&L statement combo pivot failed")
        frappe.throw(f"Profit & Loss combo failed: {e}")

    payload = {
        "report": {
            "name": doc.name, "report_name": doc.report_name,
            "slug": doc.slug, "report_type": getattr(doc, "report_type", "pnl_statement"),
        },
        "filters": {
            "company": company, "from_date": from_date, "to_date": to_date,
            "dim1": dim1, "dim2": dim2, "finance_book": finance_book,
            "show_group_accounts": cint(show_group_accounts),
            "show_zero_values": cint(show_zero_values),
            "presentation_currency": presentation_currency or None,
        },
        "result": result,
        "performance": {"execution_ms": int((time.perf_counter() - started) * 1000), "cache_hit": False},
    }
    frappe.cache().set_value(cache_key, payload, expires_in_sec=EXECUTION_CACHE_TTL_SECONDS)
    return payload


def _resolve_fiscal_year_start(fiscal_year: str | int, company: str | None = None) -> str:
    """Accept either a Fiscal Year DocType name ('FY-2026') or an integer
    year (2026). Returns the start date as 'YYYY-MM-DD'.

    v1.9.59 — when a `company` is supplied, prefer that company's
    fiscal_year_bounds so non-Jan-start companies (India: Apr-start, etc.)
    get correct boundaries. Without `company`, falls back to the legacy
    behaviour for backward compatibility with old callers.
    """
    if company:
        from neotec_insight.neotec_insight.utils.fiscal_year import fiscal_year_bounds
        try:
            s, _ = fiscal_year_bounds(company, fiscal_year)
            return s.isoformat()
        except Exception:
            pass  # fall through to legacy resolution
    if isinstance(fiscal_year, int) or (isinstance(fiscal_year, str) and fiscal_year.isdigit()):
        # Treat as a calendar year. Try to find a matching Fiscal Year.
        yr = int(fiscal_year)
        try:
            rows = frappe.get_all(
                "Fiscal Year",
                filters={"year_start_date": [">=", f"{yr - 1}-12-31"], "year_start_date": ["<=", f"{yr}-12-31"]},
                fields=["year_start_date"], limit_page_length=1,
            )
            if rows:
                return str(rows[0]["year_start_date"])
        except Exception:
            pass
        return f"{yr}-01-01"
    # Treat as a Fiscal Year name.
    try:
        d = frappe.db.get_value("Fiscal Year", fiscal_year, "year_start_date")
        if d:
            return str(d)
    except Exception:
        pass
    return f"{fiscal_year}-01-01"


def _parse_root_types(root_types: str | list[str] | None) -> list[str]:
    if not root_types:
        return []
    if isinstance(root_types, str):
        parsed = frappe.parse_json(root_types) if root_types.startswith("[") else [s.strip() for s in root_types.split(",")]
        if isinstance(parsed, list):
            return [str(s) for s in parsed if s]
        return []
    if isinstance(root_types, list):
        return [str(s) for s in root_types if s]
    return []


@frappe.whitelist()
def run_report_row_drill(
    report: str,
    row_key: str,
    fiscal_year: int,
    month_from: int = 0,
    month_to: int = 11,
    cost_center: str | list | None = None,
    project: str | list | None = None,
    department: str | list | None = None,
    branch: str | list | None = None,
    company: str | None = None,
    period_mode: str = "fiscal_year",
    period_from_date: str | None = None,
    period_to_date: str | None = None,
    use_cache: int = 1,
) -> dict:
    """Per-account monthly breakdown for one P&L source row.

    Returns the accounts mapped to this row's flag, each with their own
    {0..11: amount} monthly map for actuals. Group bindings are expanded to
    leaves via the same lft/rgt resolution used by the main engine.

    Per-account budget is NOT returned because Insight Budget Cells are
    keyed by row_key, not by account — budget lives at the P&L line level,
    not the chart-of-accounts level. The frontend should show empty cells
    in the Budget column for drill rows.

    Returns:
        {
            "row_key": "...",
            "flag": "...",                  # the flag bound to this row
            "accounts": [
                {
                    "account": "11200002 - ...", "account_code": "11200002",
                    "account_name": "...", "monthly": {0: 1000.0, 1: 1500.0, ...},
                    "is_group_binding_leaf": false,
                    "parent_group": null,    # populated if this account came from a group binding
                }, ...
            ],
            "totals": {0: ..., 11: ...},     # sum across accounts; should match parent row's monthly
            "performance": {execution_ms, cache_hit},
        }
    """
    _require_read()
    doc = _resolve_report_doc(report)
    if not row_key:
        frappe.throw("row_key is required.")

    # Resolve the row's flag from the report definition.
    definition = json.loads(doc.definition_json or "{}")
    target_row = None
    for r in definition.get("rows", []):
        if r.get("key") == row_key:
            target_row = r
            break
    if not target_row:
        frappe.throw(f"Row '{row_key}' not found in report '{doc.name}'.")
    if target_row.get("kind") != "source":
        frappe.throw(f"Row '{row_key}' is a {target_row.get('kind')} row — only source rows can be drilled.")
    flag = (target_row.get("flag") or target_row.get("label") or "").strip()
    if not flag:
        frappe.throw(f"Row '{row_key}' has no flag — nothing to drill.")

    fy = cint(fiscal_year)
    mf = max(0, min(cint(month_from), 11))
    mt = max(mf, min(cint(month_to), 11))

    # v1.9.58 — multi-select native dims. Normalise then sort for cache key.
    cost_center = _normalise_dim_param(cost_center)
    project = _normalise_dim_param(project)
    department = _normalise_dim_param(department)
    branch = _normalise_dim_param(branch)

    cache_key = _execution_cache_key(
        report_name=doc.name, user=frappe.session.user,
        row_key=row_key, fy=fy, mf=mf, mt=mt,
        cost_center=sorted(cost_center) if cost_center else cost_center,
        project=sorted(project) if project else project,
        department=sorted(department) if department else department,
        branch=sorted(branch) if branch else branch,
        company=company, kind="row_drill",
        period_mode=period_mode,
        period_from_date=period_from_date if period_mode == "date_range" else None,
        period_to_date=period_to_date if period_mode == "date_range" else None,
        hide_zero=cint(getattr(doc, "hide_zero_accounts", 0)),
        hide_group=cint(getattr(doc, "hide_group_accounts", 0)),
    )
    if cint(use_cache):
        cached = frappe.cache().get_value(cache_key)
        if isinstance(cached, dict):
            cached["performance"] = {**cached.get("performance", {}), "cache_hit": True}
            return cached

    started = time.perf_counter()

    # Get the accounts bound to this flag. load_flag_to_accounts already
    # resolves group bindings to their leaves, so we get a flat list.
    flag_map = load_flag_to_accounts(doc.name)
    accounts = flag_map.get(flag) or []
    if not accounts:
        payload = {
            "row_key": row_key, "flag": flag, "accounts": [], "totals": {m: 0.0 for m in range(mf, mt + 1)},
            "performance": {"execution_ms": int((time.perf_counter() - started) * 1000), "cache_hit": False},
        }
        frappe.cache().set_value(cache_key, payload, expires_in_sec=EXECUTION_CACHE_TTL_SECONDS)
        return payload

    # Also fetch the raw mapping rows so we can mark which accounts came from
    # a group binding (purely for UI hinting — we never group-display them).
    raw_mappings = frappe.get_all(
        "Account Flag Mapping",
        filters={"report": doc.name, "flag": flag},
        fields=["account", "is_group_binding"],
        limit_page_length=0,
    )
    group_bind_parents: set[str] = set()
    for m in raw_mappings:
        if m.get("is_group_binding"):
            group_bind_parents.add(m["account"])

    # Pull account metadata in one query.
    acc_meta = frappe.get_all(
        "Account",
        filters={"name": ["in", accounts]},
        fields=["name", "account_number", "account_name", "parent_account", "is_group"],
        limit_page_length=0,
    )
    acc_meta_by_name = {a["name"]: a for a in acc_meta}

    # Query GL Entry once for all accounts in this drill, grouped by
    # (account, month). Reuses the company filter from cost_center if needed.
    months_in_window = list(range(mf, mt + 1))

    # We need a company filter; if the caller didn't supply one, infer from
    # the report's company field (fallback to the accounts' company).
    effective_company = company or doc.company
    if not effective_company and acc_meta:
        effective_company = acc_meta[0].get("company") or None

    per_account_monthly = _gl_sums_per_account_month(
        company=effective_company,
        fiscal_year=fy,
        months=months_in_window,
        cost_center=cost_center, project=project,
        department=department, branch=branch,
        accounts=accounts,
        row_scope=target_row.get("dimension_scope"),
        period_mode=period_mode,
        period_from_date=period_from_date,
        period_to_date=period_to_date,
    )

    # v2.34.0 — prior-year actuals per account, so drill rows carry the FY-1
    # comparison column and growth %. Budget stays row-level by design
    # (Insight Budget Cells key on row_key, not account).
    try:
        per_account_prev = _gl_sums_per_account_month(
            company=effective_company,
            fiscal_year=fy - 1,
            months=months_in_window,
            cost_center=cost_center, project=project,
            department=department, branch=branch,
            accounts=accounts,
            row_scope=target_row.get("dimension_scope"),
            period_mode="fiscal_year",
        )
    except Exception:
        per_account_prev = {}

    # Build the response.
    rows = []
    totals = {m: 0.0 for m in months_in_window}
    for acc_name in accounts:
        meta = acc_meta_by_name.get(acc_name) or {}
        monthly = per_account_monthly.get(acc_name) or {}
        for m in months_in_window:
            v = float(monthly.get(m) or 0.0)
            totals[m] = totals[m] + v
        # Find which group (if any) this leaf came from. Walk the parent chain
        # and stop at the first parent that matches a group_bind_parents entry.
        parent_group = None
        cur_parent = meta.get("parent_account")
        guard = 0
        while cur_parent and guard < 20:
            if cur_parent in group_bind_parents:
                parent_group = cur_parent
                break
            cur_parent = frappe.db.get_value("Account", cur_parent, "parent_account")
            guard += 1
        rows.append({
            "account": acc_name,
            "account_code": meta.get("account_number") or "",
            "account_name": meta.get("account_name") or acc_name,
            "monthly": {m: round(float(monthly.get(m) or 0.0), 2) for m in months_in_window},
            "monthly_prev": {m: round(float((per_account_prev.get(acc_name) or {}).get(m) or 0.0), 2) for m in months_in_window},
            "is_group_binding_leaf": parent_group is not None,
            "parent_group": parent_group,
            "is_group": bool(meta.get("is_group")),
        })

    # v1.9.92 — apply the report's display defaults so the drill matches what
    # the definition asks for. Group accounts hold no GL postings and zero
    # rows contribute nothing, so filtering them never changes the parent
    # total — it only removes noise.
    if cint(getattr(doc, "hide_group_accounts", 0)):
        rows = [r for r in rows if not r["is_group"]]
    if cint(getattr(doc, "hide_zero_accounts", 0)):
        rows = [r for r in rows if any(abs(float(v)) > 0.005 for v in r["monthly"].values())]

    # Sort by account_code to give a stable, scannable order.
    rows.sort(key=lambda r: r["account_code"] or r["account"])

    payload = {
        "row_key": row_key,
        "flag": flag,
        "accounts": rows,
        "totals": {m: round(v, 2) for m, v in totals.items()},
        "performance": {"execution_ms": int((time.perf_counter() - started) * 1000), "cache_hit": False},
    }
    frappe.cache().set_value(cache_key, payload, expires_in_sec=EXECUTION_CACHE_TTL_SECONDS)
    return payload


@frappe.whitelist()
def gl_drill_entries(
    report,
    row_key=None,
    account=None,
    fiscal_year=None,
    month_from=0,
    month_to=11,
    cost_center=None,
    project=None,
    department=None,
    branch=None,
    company=None,
    period_mode="fiscal_year",
    period_from_date=None,
    period_to_date=None,
    limit=500,
):
    """Raw GL Entry rows behind one Insight row (or one account within it).

    Applies the EXACT population that produced the displayed value: the row's
    bound accounts (or a single clicked account), the row's dimension scope,
    the global dimension filters, and the resolved date bounds (honouring
    date_range). Returns the entries, their net total (credit-debit, which
    reconciles with the figure on screen), a filter summary, and a deep link
    to ERPNext's General Ledger report with the same filters pre-applied.
    """
    _require_read()
    from neotec_insight.neotec_insight.utils.fiscal_year import resolve_date_bounds
    from urllib.parse import urlencode

    doc = _resolve_report_doc(report)
    definition = json.loads(doc.definition_json or "{}")
    target_row = None
    for r in definition.get("rows", []):
        if r.get("key") == row_key:
            target_row = r
            break
    row_scope = (target_row or {}).get("dimension_scope") or {}
    flag = ((target_row or {}).get("flag") or (target_row or {}).get("label") or "").strip()

    flag_map = load_flag_to_accounts(doc.name)
    bound_accounts = flag_map.get(flag) or []
    accounts = [account] if account else list(bound_accounts)
    if not accounts:
        return {"entries": [], "total": 0.0, "count": 0, "shown": 0, "filters": {}, "gl_report_url": ""}

    cost_center = _normalise_dim_param(cost_center)
    project = _normalise_dim_param(project)
    department = _normalise_dim_param(department)
    branch = _normalise_dim_param(branch)

    fy = cint(fiscal_year)
    mf = max(0, min(cint(month_from), 11))
    mt = max(mf, min(cint(month_to), 11))
    eff_company = company or doc.company

    ds, de = resolve_date_bounds(
        eff_company, fy, mf, mt,
        period_mode=period_mode,
        period_from_date=period_from_date,
        period_to_date=period_to_date,
    )

    filters = {
        "is_cancelled": 0,
        "account": ["in", accounts],
        "posting_date": ["between", [str(ds), str(de)]],
    }
    if eff_company:
        filters["company"] = eff_company

    def _add(col, val):
        if val in (None, "", []):
            return
        if isinstance(val, list):
            vals = [v for v in val if v]
            if vals:
                filters[col] = ["in", vals]
        else:
            filters[col] = val

    _add("cost_center", cost_center)
    _add("project", project)
    _add("department", department)
    _add("branch", branch)

    # Row-level dimension scope (e.g. Department IN [Assessment]).
    dim_col, dim_vals = None, []
    or_filters = None
    if isinstance(row_scope, dict):
        dt = (row_scope.get("dimension_type") or "").strip().lower()
        raw_vals = row_scope.get("dimension_values") or []
        include_blank = any(str(v).strip() == "__BLANK__" for v in raw_vals)
        dim_vals = [v for v in raw_vals if v and str(v).strip() != "__BLANK__"]
        dim_col = {
            "department": "department", "cost center": "cost_center",
            "cost_center": "cost_center", "project": "project", "branch": "branch",
        }.get(dt, dt.replace(" ", "_") if dt else None)
        try:
            if dim_col and frappe.db.has_column("GL Entry", dim_col):
                if include_blank:
                    or_filters = []
                    if dim_vals:
                        or_filters.append([dim_col, "in", dim_vals])
                    or_filters.append([dim_col, "is", "not set"])
                    or_filters.append([dim_col, "=", ""])
                elif dim_vals:
                    filters[dim_col] = ["in", dim_vals]
        except Exception:
            pass

    fields = ["posting_date", "account", "debit", "credit", "voucher_type",
              "voucher_no", "against", "party_type", "party", "cost_center",
              "project", "remarks"]
    if frappe.db.has_column("GL Entry", "department"):
        fields.append("department")

    entries = frappe.get_all(
        "GL Entry", filters=filters, or_filters=or_filters, fields=fields,
        order_by="posting_date asc, creation asc",
        limit_page_length=cint(limit) or 500,
    )
    total = sum(float(e.get("credit") or 0) - float(e.get("debit") or 0) for e in entries)
    try:
        cnt = frappe.db.count("GL Entry", filters=filters) if not or_filters else len(entries)
    except Exception:
        cnt = len(entries)

    # Deep link to ERPNext General Ledger query report (same filters).
    gl_params = {"company": eff_company or "", "from_date": str(ds), "to_date": str(de)}
    if account:
        gl_params["account"] = account
    if isinstance(cost_center, str) and cost_center:
        gl_params["cost_center"] = cost_center
    if isinstance(project, str) and project:
        gl_params["project"] = project
    gl_report_url = "/app/query-report/General Ledger?" + urlencode(gl_params)

    return {
        "entries": entries,
        "total": total,
        "count": cnt,
        "shown": len(entries),
        "filters": {
            "accounts": accounts,
            "date_from": str(ds),
            "date_to": str(de),
            "scope_dimension": dim_col if (dim_col and dim_vals) else None,
            "scope_values": dim_vals if (dim_col and dim_vals) else [],
            "company": eff_company,
        },
        "gl_report_url": gl_report_url,
    }


@frappe.whitelist()
def export_configuration(sections=None):
    """Bundle Insight configuration into one portable JSON object.

    Every doctype in CONFIG_REGISTRY (utils/config_backup_registry.py) —
    that ONE list is now the single source of truth this function, import_
    configuration, and config_section_counts all read from, replacing what
    used to be three independently hardcoded lists that could silently
    drift out of sync, or all three simply never learn about a new
    doctype. A coverage audit (see check_config_backup_coverage below)
    found seven pre-existing doctypes unregistered in any of them before
    this fix, on top of Cash Flow Forecast's own doctypes never having been
    added either.

    Carry the file to another site and restore it with import_configuration.
    Account/department names are kept as-is (intended for the SAME company
    / chart).

    `sections` (optional): JSON list of doctype keys to include. When
    omitted, every area is exported. Useful for partial backups from a test
    environment.
    """
    frappe.only_for("System Manager")
    if not frappe.has_permission("Insight Report Definition", "read"):
        frappe.throw("Not permitted to export Insight configuration.")

    VOLATILE = {"creation", "modified", "modified_by", "owner", "docstatus", "idx",
                "lft", "rgt", "_user_tags", "_comments", "_assign", "_liked_by",
                "parent", "parentfield", "parenttype", "_seen"}

    def _clean(doc):
        if isinstance(doc, dict):
            out = {}
            for k, v in doc.items():
                if k in VOLATILE:
                    continue
                if isinstance(v, list):
                    out[k] = [_clean(x) for x in v]
                else:
                    out[k] = v
            # child rows: drop their name too (parent insert re-creates)
            return out
        return doc

    def _dump(dt):
        rows = []
        for r in frappe.get_all(dt, pluck="name"):
            d = frappe.get_doc(dt, r).as_dict()
            rows.append(_clean(d))
        return rows

    data = {}
    for entry in CONFIG_REGISTRY:
        dt = entry["doctype"]
        if entry.get("is_single"):
            try:
                data[dt] = [_clean(frappe.get_doc(dt).as_dict())]
            except Exception:
                data[dt] = []
        else:
            data[dt] = _dump(dt)

    # optional section filter (partial backup)
    sel = None
    if sections:
        try:
            sel = set(frappe.parse_json(sections) if isinstance(sections, str) else sections)
        except Exception:
            sel = None
    if sel:
        data = {k: v for k, v in data.items() if k in sel}

    counts = {k: len(v) for k, v in data.items()}
    return {
        "app": "neotec_insight",
        "version": _app_version(),
        "exported_at": frappe.utils.now(),
        "site": frappe.local.site,
        "sections": sorted(data.keys()),
        "counts": counts,
        "data": data,
    }


@frappe.whitelist()
def config_areas():
    """The registry, grouped by area, for the backup UI's checkbox list —
    replaces the frontend's own previously-hardcoded AREAS array. Adding a
    doctype to CONFIG_REGISTRY now makes it appear here with no frontend
    change needed."""
    _require_read()
    areas: dict[str, list[str]] = {}
    for entry in CONFIG_REGISTRY:
        areas.setdefault(entry["area"], []).append(entry["doctype"])
    return [{"label": label, "doctypes": doctypes} for label, doctypes in areas.items()]


@frappe.whitelist()
def check_config_backup_coverage():
    """Live diagnostic: every Insight doctype (module='Neotec Insight',
    not a child table) against CONFIG_REGISTRY and EXCLUDED_FROM_CONFIG_
    BACKUP. `unaccounted` should always be empty — the same check runs as
    tests/test_config_backup_registry.py against the doctype/ folder
    directly, so a real gap should already have been caught before this
    ever reaches a live site; this endpoint is for confirming that on an
    actual installed site, including any custom doctypes a specific
    deployment might have added outside this app's own source."""
    frappe.only_for("System Manager")
    live = frappe.get_all("DocType", filters={"module": "Neotec Insight", "istable": 0}, pluck="name")
    return compute_coverage(live)


@frappe.whitelist()
def config_section_counts():
    """Record count per Insight configuration area, for the backup selector."""
    _require_read()
    out = {}
    for entry in CONFIG_REGISTRY:
        dt = entry["doctype"]
        try:
            if entry.get("is_single"):
                out[dt] = 1 if frappe.db.exists(dt, dt) else 0
            else:
                out[dt] = frappe.db.count(dt)
        except Exception:
            out[dt] = 0
    return out


# Non-Single doctypes, in registry order — parents/targets before the
# records that link to them, same ordering guarantee _IMPORT_ORDER used to
# provide as its own separately-maintained list. Derived from CONFIG_
# REGISTRY now so there's exactly one place to add a new doctype, not two.
_IMPORT_ORDER = [e["doctype"] for e in CONFIG_REGISTRY if not e.get("is_single")]
_SINGLE_DOCTYPES = [e["doctype"] for e in CONFIG_REGISTRY if e.get("is_single")]


def _app_version():
    try:
        import neotec_insight
        return getattr(neotec_insight, "__version__", "")
    except Exception:
        return ""


@frappe.whitelist()
def import_configuration(payload=None, mode="replace"):
    """Restore an Insight configuration bundle produced by export_configuration.

    mode='replace' (default) clears existing Insight config first (children
    before parents) then inserts the bundle — an exact restore onto an empty
    or stale site. mode='merge' inserts/updates without clearing. Each record
    is wrapped in its own try/except so one bad link never aborts the whole
    restore; a per-doctype summary of inserted/updated/failed is returned.
    """
    frappe.only_for("System Manager")
    if isinstance(payload, str):
        payload = json.loads(payload)
    if not payload or "data" not in payload:
        frappe.throw("Invalid configuration bundle.")
    data = payload["data"]

    summary = {"inserted": {}, "failed": {}, "errors": [], "from_version": payload.get("version", "")}

    # 1) Replace mode: clear existing config (children/dependents first).
    if mode == "replace":
        for dt in reversed(_IMPORT_ORDER):
            try:
                for nm in frappe.get_all(dt, pluck="name"):
                    frappe.delete_doc(dt, nm, force=1, ignore_permissions=True)
            except Exception as e:
                summary["errors"].append(f"clear {dt}: {e}")

    # 2) Insert records in dependency order.
    for dt in _IMPORT_ORDER:
        ins = 0
        for raw in (data.get(dt) or []):
            try:
                d = dict(raw)
                d["doctype"] = dt
                if mode == "merge" and d.get("name") and frappe.db.exists(dt, d["name"]):
                    existing = frappe.get_doc(dt, d["name"])
                    existing.update({k: v for k, v in d.items() if k not in ("doctype", "name")})
                    existing.save(ignore_permissions=True)
                else:
                    doc = frappe.get_doc(d)
                    doc.insert(ignore_permissions=True, set_name=d.get("name"))
                ins += 1
            except Exception as e:
                summary["failed"][dt] = summary["failed"].get(dt, 0) + 1
                if len(summary["errors"]) < 60:
                    summary["errors"].append(f"{dt} [{raw.get('name', '?')}]: {e}")
        summary["inserted"][dt] = ins

    # 3) Single doctypes — every one in the registry, not just AI Settings.
    # The old version of this function only ever handled AI Settings by
    # name; Insight Menu Settings and Insight Cash Flow Settings (both
    # Singles) would have silently never restored, the same class of
    # silent gap the registry itself exists to close.
    for dt in _SINGLE_DOCTYPES:
        rows = data.get(dt) or []
        if not rows:
            continue
        try:
            doc = frappe.get_doc(dt)
            src = dict(rows[0])
            for k in ("doctype", "name"):
                src.pop(k, None)
            doc.update(src)
            doc.save(ignore_permissions=True)
            summary["inserted"][dt] = 1
        except Exception as e:
            summary["errors"].append(f"{dt}: {e}")

    frappe.db.commit()
    return summary



# ── GL: document description + party grouping (v2.56.0) ─────────────────────


_DOCFIELD_SKIP = {"Section Break", "Column Break", "Tab Break", "HTML", "Button",
                  "Image", "Fold", "Heading", "Table", "Table MultiSelect",
                  "Signature", "Password", "Attach Image"}


@frappe.whitelist()
def voucher_field_options(voucher_types=None):
    """Fields that can be pulled from each source document onto ledger rows.

    Discovered from the live DocType meta rather than hard-coded, so custom
    fields — which is where site-specific narration usually lives — are offered
    alongside the standard ones. Layout elements and child tables are dropped:
    they carry no scalar value a ledger cell could hold.
    """
    types = _normalise_dim_param(voucher_types) or []
    out = {}
    for dt in types:
        if not frappe.db.exists("DocType", dt) or not frappe.has_permission(dt, "read"):
            continue
        try:
            meta = frappe.get_meta(dt)
        except Exception:
            continue
        fields = []
        for df in meta.fields:
            if df.fieldtype in _DOCFIELD_SKIP or not df.fieldname:
                continue
            fields.append({"fieldname": df.fieldname,
                           "label": df.label or df.fieldname,
                           "fieldtype": df.fieldtype,
                           "custom": bool(getattr(df, "is_custom_field", 0))})
        fields.sort(key=lambda f: (not f["custom"], f["label"].lower()))
        out[dt] = fields
    return out


def _gl_voucher_types(where: str, params: dict, from_date, to_date) -> list[str]:
    """Distinct voucher types in the window, ignoring exclusions."""
    try:
        rows = frappe.db.sql(
            f"""SELECT DISTINCT g.voucher_type AS vt FROM `tabGL Entry` g
                WHERE {where} AND g.posting_date BETWEEN %(fd)s AND %(td)s""",
            {**params, "fd": from_date, "td": to_date}, as_dict=True)
    except Exception:
        return []
    return sorted({r["vt"] for r in rows if r.get("vt")})


def _gl_doc_fields(tx_rows: list[dict], wanted: dict) -> dict[tuple, dict]:
    """Requested source-document fields, keyed by (voucher_type, voucher_no).

    One query per voucher type, never per row — the same batching as
    `_gl_descriptions`, which this generalises. A ledger over a quarter can
    carry thousands of rows across a handful of doctypes; per-row fetching
    would make the feature unusable at exactly the sizes it is wanted for.

    Fields that do not exist on the doctype are dropped rather than throwing:
    a saved column set outliving a customisation should degrade to a blank
    column, not break the report.
    """
    if not wanted:
        return {}
    by_type: dict[str, set] = {}
    for r in tx_rows:
        vt, vno = r.get("voucher_type"), r.get("voucher_no")
        if vt and vno and vt in wanted:
            by_type.setdefault(vt, set()).add(vno)

    out: dict[tuple, dict] = {}
    for vt, vnos in by_type.items():
        names = [f for f in (wanted.get(vt) or []) if f]
        if not names:
            continue
        try:
            if not frappe.has_permission(vt, "read"):
                continue
            live = [f for f in names if frappe.db.has_column(vt, f)]
            if not live:
                continue
            rows = frappe.get_all(vt, filters={"name": ["in", list(vnos)]},
                                  fields=["name"] + live, limit_page_length=0)
        except Exception:
            continue
        for row in rows:
            out[(vt, row["name"])] = {f"{vt}::{f}": row.get(f) for f in live}
    return out


def _gl_descriptions(tx_rows: list[dict]) -> dict[tuple, str]:
    """The narration a human typed on the source voucher, per (type, no).

    GL Entry's own `remarks` is machine-written — "Amount SAR 172.5 to X
    Transaction…" — which is what the Particular column shows. The narration
    the accountant actually entered lives on the source document under a
    different fieldname for almost every doctype, so we look up the right one
    per type and batch the fetch. One query per voucher type, never per row.
    """
    # Preference order per doctype; the first field that exists and is
    # non-empty wins. `user_remark` is the Journal Entry's typed narration,
    # distinct from its auto-built `remark`.
    CANDIDATES = {
        "Journal Entry": ["user_remark", "remark"],
        "Payment Entry": ["remarks"],
        "Sales Invoice": ["remarks"],
        "Purchase Invoice": ["remarks"],
        "Purchase Receipt": ["remarks"],
        "Delivery Note": ["remarks"],
        "Stock Entry": ["remarks"],
        "Expense Claim": ["remark"],
    }
    by_type: dict[str, set] = {}
    for r in tx_rows:
        vt, vno = r.get("voucher_type"), r.get("voucher_no")
        if vt and vno:
            by_type.setdefault(vt, set()).add(vno)

    out: dict[tuple, str] = {}
    for vt, vnos in by_type.items():
        try:
            if not frappe.db.exists("DocType", vt):
                continue
            meta = frappe.get_meta(vt)
            fields = [f for f in CANDIDATES.get(vt, ["remarks", "remark", "user_remark"])
                      if meta.get_field(f)]
            if not fields:
                continue
            names = list(vnos)
            CH = 900
            for i in range(0, len(names), CH):
                chunk = names[i:i + CH]
                rows = frappe.get_all(vt, filters={"name": ["in", chunk]},
                                      fields=["name"] + fields, limit_page_length=0)
                for row in rows:
                    val = ""
                    for f in fields:
                        v = (row.get(f) or "").strip()
                        if v:
                            val = v
                            break
                    if val:
                        # Narration fields are Text/Small Text and some sites
                        # store HTML in them; the ledger cell wants plain text.
                        val = re.sub(r"<[^>]+>", " ", val)
                        val = re.sub(r"\s+", " ", val).strip()
                        out[(vt, row["name"])] = val[:500]
        except Exception:
            # A description is a nicety. Never let it take the ledger down.
            continue
    return out


def _gl_regroup_by_party(blocks: list[dict], base_where: str, params: dict,
                         from_date: str) -> list[dict]:
    """Re-bucket the ledger by party instead of by account.

    Filtering by a supplier and then reading a block headed "Creditors" tells
    you nothing you didn't already type. When a party filter is active the
    party is the subject of the report, so it becomes the block heading and
    the account moves into the rows.

    Running balances and sub-totals are recomputed per party — carrying the
    per-account running balance across would be meaningless in the new order.
    """
    opening: dict[str, float] = {}
    try:
        op_rows = frappe.db.sql(
            f"""SELECT g.party_type AS party_type, g.party AS party,
                       SUM(g.debit - g.credit) AS bal
                FROM `tabGL Entry` g
                WHERE {base_where} AND g.posting_date < %(fd)s
                  AND g.party IS NOT NULL AND g.party != ''
                GROUP BY g.party_type, g.party""",
            {**params, "fd": from_date}, as_dict=True,
        )
        for r in op_rows:
            opening[r["party"] or ""] = float(r["bal"] or 0.0)
    except Exception:
        opening = {}

    buckets: dict[str, list] = {}
    for b in blocks:
        for tx in b.get("transactions", []):
            # Entries with no party (the bank leg of a payment, say) would
            # otherwise vanish; they group under an explicit bucket instead.
            key = tx.get("party") or ""
            buckets.setdefault(key, []).append(tx)

    out: list[dict] = []
    for party, txns in buckets.items():
        txns.sort(key=lambda r: (str(r.get("posting_date") or ""), str(r.get("voucher_no") or "")))
        running = opening.get(party, 0.0)
        deb = cred = 0.0
        for tx in txns:
            d = float(tx.get("debit") or 0.0)
            c = float(tx.get("credit") or 0.0)
            running += d - c
            deb += d
            cred += c
            tx["balance_raw"] = running
        ptype = next((t.get("party_type") for t in txns if t.get("party_type")), "")
        out.append({
            "account": party or _("Without party"),
            "account_number": "",
            "account_name": party or _("Without party"),
            "party": party,
            "party_type": ptype,
            "group_kind": "party",
            "root_type": "",
            "opening_raw": opening.get(party, 0.0),
            "transactions": txns,
            "sub_total": {"debit": deb, "credit": cred},
            "closing_raw": running,
        })
    out.sort(key=lambda b: (b["account"] == _("Without party"), b["account"]))
    return out



@frappe.whitelist()
def party_control_accounts(company=None, party_type="Supplier"):
    """The receivable/payable control accounts a party ledger should span.

    A Supplier Ledger shouldn't make the user hunt for "Creditors" in the
    account tree — the account set is implied by the party type. Returns leaf
    accounts of the matching account_type so the ledger can preselect them,
    while leaving the picker available for sites with several control
    accounts (retention payable, related-party payable, and so on).
    """
    _require_read()
    atype = "Receivable" if str(party_type or "").strip().lower() == "customer" else "Payable"
    company = company or frappe.defaults.get_user_default("Company") \
        or frappe.defaults.get_global_default("company") or None
    filters = {"account_type": atype, "is_group": 0}
    if company:
        filters["company"] = company
    try:
        rows = frappe.get_all("Account", filters=filters,
                              fields=["name", "account_number", "account_name"],
                              order_by="account_number asc, account_name asc",
                              limit_page_length=0)
    except Exception:
        rows = []
    return [{"name": r["name"],
             "label": ((r.get("account_number") + " - ") if r.get("account_number") else "")
                      + (r.get("account_name") or r["name"])}
            for r in rows]


@frappe.whitelist()
def list_parties(party_type="Supplier", search=None, company=None, limit=50):
    """Type-ahead source for the party ledger's Supplier/Customer picker."""
    _require_read()
    dt = "Customer" if str(party_type or "").strip().lower() == "customer" else "Supplier"
    name_field = "customer_name" if dt == "Customer" else "supplier_name"
    filters = {}
    if search:
        filters["name"] = ["like", f"%{search}%"]
    try:
        rows = frappe.get_all(dt, filters=filters, fields=["name", name_field],
                              order_by=f"{name_field} asc",
                              limit_page_length=min(int(limit or 50), 200))
    except Exception:
        return []
    return [{"name": r["name"], "label": r.get(name_field) or r["name"]} for r in rows]


@frappe.whitelist()
def general_ledger(
    company=None,
    accounts=None,
    from_date=None,
    to_date=None,
    cost_center=None,
    project=None,
    department=None,
    branch=None,
    supplier=None,
    customer=None,
    show_without_transactions=0,
    show_zero_closing=0,
    show_only_opening=0,
    split_by_against=1,
    report=None,
    limit=0,
    group_by=None,
    with_description=0,
    exclude_voucher_types=None,
    exclude_vouchers=None,
    doc_fields=None,
):
    """ERPNext-style grouped General Ledger.

    Returns one block per (leaf) account: opening balance, the transactions in
    the window with a running balance, a period sub-total, and the closing
    balance — plus a final report total. Mirrors the attached Excel layout.

    Balance sign convention matches the Excel: raw = Σ(debit − credit) from the
    opening forward; displayed as abs(raw) with a 'Dr' suffix when raw ≥ 0 and
    'Cr' when raw < 0.

    Options (all default off):
      - show_without_transactions: include accounts with no entries in the window
      - show_zero_closing: keep accounts whose closing balance is exactly 0
      - show_only_opening: include accounts that have only an opening balance
    """
    _require_read()
    if isinstance(accounts, str):
        try:
            accounts = json.loads(accounts)
        except Exception:
            accounts = [accounts] if accounts else []
    accounts = [a for a in (accounts or []) if a]
    if not accounts:
        frappe.throw("Select at least one account.")
    if not from_date or not to_date:
        frappe.throw("from_date and to_date are required.")

    eff_company = company
    if not eff_company and report:
        try:
            eff_company = _resolve_report_doc(report).company
        except Exception:
            eff_company = None

    cost_center = _normalise_dim_param(cost_center)
    project = _normalise_dim_param(project)
    department = _normalise_dim_param(department)
    branch = _normalise_dim_param(branch)

    # Expand any group accounts to their leaves (via lft/rgt), like ERPNext.
    meta_rows = frappe.get_all(
        "Account", filters={"name": ["in", accounts]},
        fields=["name", "account_number", "account_name", "root_type", "is_group", "lft", "rgt", "company"],
    )
    leaf_accounts: list[str] = []
    acc_meta: dict[str, dict] = {}
    for m in meta_rows:
        if m.get("is_group"):
            leaves = frappe.get_all(
                "Account",
                filters={"lft": [">", m["lft"]], "rgt": ["<", m["rgt"]], "is_group": 0,
                         **({"company": m["company"]} if m.get("company") else {})},
                fields=["name", "account_number", "account_name", "root_type", "company"],
            )
            for lf in leaves:
                acc_meta[lf["name"]] = lf
                leaf_accounts.append(lf["name"])
        else:
            acc_meta[m["name"]] = m
            leaf_accounts.append(m["name"])
    leaf_accounts = list(dict.fromkeys(leaf_accounts))  # de-dup, keep order
    if not leaf_accounts:
        return {"accounts": [], "report_total": {"debit": 0.0, "credit": 0.0, "balance_raw": 0.0},
                "filters": {"from_date": from_date, "to_date": to_date, "company": eff_company}}

    # ── Common WHERE fragments ───────────────────────────────────────────
    base_conds = ["g.is_cancelled = 0", "g.account IN %(accs)s"]
    params: dict[str, Any] = {"accs": tuple(leaf_accounts)}
    if eff_company:
        base_conds.append("g.company = %(company)s")
        params["company"] = eff_company

    def _dim(col, val):
        if val in (None, "", []):
            return
        if isinstance(val, list):
            include_blank = any(str(v).strip() == "__BLANK__" for v in val)
            vals = [v for v in val if v and str(v).strip() != "__BLANK__"]
            ors = []
            if vals:
                ph = ", ".join(f"%({col}_{i})s" for i in range(len(vals)))
                ors.append(f"g.`{col}` IN ({ph})")
                for i, v in enumerate(vals):
                    params[f"{col}_{i}"] = v
            if include_blank:
                ors.append(f"(g.`{col}` IS NULL OR g.`{col}` = '')")
            if ors:
                base_conds.append("(" + " OR ".join(ors) + ")")
        else:
            base_conds.append(f"g.`{col}` = %({col})s")
            params[col] = val

    _dim("cost_center", cost_center)
    _dim("project", project)
    _dim("department", department)
    _dim("branch", branch)

    # Party (Supplier / Customer) filter. A GL Entry's party is identified by
    # party_type + party. If both supplier and customer are given we OR the
    # two party clauses (a single entry can only be one or the other).
    supplier = _normalise_dim_param(supplier)
    customer = _normalise_dim_param(customer)
    party_ors = []
    for ptype, pval in (("Supplier", supplier), ("Customer", customer)):
        vals = pval if isinstance(pval, list) else ([pval] if pval else [])
        vals = [v for v in vals if v]
        if vals:
            ph = ", ".join(f"%(p_{ptype}_{i})s" for i in range(len(vals)))
            party_ors.append(f"(g.party_type = %(pt_{ptype})s AND g.party IN ({ph}))")
            params[f"pt_{ptype}"] = ptype
            for i, v in enumerate(vals):
                params[f"p_{ptype}_{i}"] = v
    if party_ors:
        base_conds.append("(" + " OR ".join(party_ors) + ")")

    # ── Exclusions (v2.74.0) — by voucher type, or by individual voucher.
    #
    # Applied to the OPENING balance as well as the window, deliberately. An
    # exclusion that only hit the window would leave closing = a real opening
    # plus a filtered movement, which is a figure that describes nothing. Both
    # ends filtered gives a coherent "as if these documents did not exist"
    # ledger that foots against itself.
    #
    # It does NOT foot against the account's true balance any more, and it
    # cannot — that is inherent to excluding documents, not a defect. The
    # response flags it so the screen and every export can say so.
    ex_types = _normalise_dim_param(exclude_voucher_types) or []
    ex_vouchers = _normalise_dim_param(exclude_vouchers) or []
    # Kept before the exclusions are appended: the picker must offer every type
    # the window CONTAINS, not every type that survived filtering. Reading the
    # list off the filtered rows would make an exclusion irreversible — the type
    # you just hid would disappear from the control that hid it.
    where_before_exclusions = " AND ".join(base_conds)

    if ex_types:
        ph = ", ".join(f"%(xvt_{i})s" for i in range(len(ex_types)))
        base_conds.append(f"g.voucher_type NOT IN ({ph})")
        for i, v in enumerate(ex_types):
            params[f"xvt_{i}"] = v
    if ex_vouchers:
        ph = ", ".join(f"%(xvn_{i})s" for i in range(len(ex_vouchers)))
        base_conds.append(f"g.voucher_no NOT IN ({ph})")
        for i, v in enumerate(ex_vouchers):
            params[f"xvn_{i}"] = v

    base_where = " AND ".join(base_conds)

    # ── Opening balances (everything strictly before from_date) ──────────
    opening: dict[str, float] = {a: 0.0 for a in leaf_accounts}
    op_rows = frappe.db.sql(
        f"""SELECT g.account AS account, SUM(g.debit - g.credit) AS bal
            FROM `tabGL Entry` g
            WHERE {base_where} AND g.posting_date < %(fd)s
            GROUP BY g.account""",
        {**params, "fd": from_date}, as_dict=True,
    )
    for r in op_rows:
        opening[r["account"]] = float(r["bal"] or 0.0)

    # ── In-window transactions ───────────────────────────────────────────
    has_dept = frappe.db.has_column("GL Entry", "department")
    dept_sel = ", g.department AS department" if has_dept else ""
    tx_rows = frappe.db.sql(
        f"""SELECT g.account AS account, g.posting_date AS posting_date,
                   g.voucher_type AS voucher_type, g.voucher_no AS voucher_no,
                   g.against AS against, g.party_type AS party_type, g.party AS party,
                   g.cost_center AS cost_center, g.project AS project,
                   g.debit AS debit, g.credit AS credit, g.remarks AS remarks{dept_sel}
            FROM `tabGL Entry` g
            WHERE {base_where} AND g.posting_date BETWEEN %(fd)s AND %(td)s
            ORDER BY g.account ASC, g.posting_date ASC, g.creation ASC""",
        {**params, "fd": from_date, "td": to_date}, as_dict=True,
    )
    tx_by_account: dict[str, list] = {}
    for r in tx_rows:
        tx_by_account.setdefault(r["account"], []).append(r)

    # ── Contra legs: for each voucher, the OTHER accounts it touched, so we
    # can list each affected account on its own row (invoice-wise) instead of
    # cramming them into one "against" cell. (v1.9.65 feature add.)
    contra_map: dict[tuple, list] = {}
    if int(split_by_against or 0):
        vno_list = list({r["voucher_no"] for r in tx_rows if r.get("voucher_no")})
        leaf_set = set(leaf_accounts)
        leg_rows: list[dict] = []
        CH = 900
        for i in range(0, len(vno_list), CH):
            chunk = vno_list[i:i + CH]
            ph = ", ".join(["%s"] * len(chunk))
            args: list = list(chunk)
            comp_clause = ""
            if eff_company:
                comp_clause = " AND company = %s"
                args.append(eff_company)
            leg_rows += frappe.db.sql(
                f"""SELECT voucher_type, voucher_no, account, party, debit, credit
                    FROM `tabGL Entry`
                    WHERE is_cancelled = 0 AND voucher_no IN ({ph}){comp_clause}
                    ORDER BY creation ASC""",
                tuple(args), as_dict=True,
            )
        # account display names for contra accounts
        contra_accts = list({l["account"] for l in leg_rows if l["account"] not in leaf_set})
        name_map = {}
        if contra_accts:
            for a in frappe.get_all("Account", filters={"name": ["in", contra_accts]},
                                    fields=["name", "account_number", "account_name"]):
                name_map[a["name"]] = a
        for l in leg_rows:
            if l["account"] in leaf_set:
                continue
            key = (l["voucher_type"] or "", l["voucher_no"] or "")
            am = name_map.get(l["account"], {})
            num = am.get("account_number")
            disp = (f"{num} - {am.get('account_name')}" if num else (am.get("account_name") or l["account"]))
            contra_map.setdefault(key, []).append({
                "account": l["account"], "display": disp, "party": l.get("party") or "",
                "debit": float(l.get("debit") or 0.0), "credit": float(l.get("credit") or 0.0),
            })

    # ── Assemble per-account blocks ──────────────────────────────────────
    blocks = []
    tot_debit = tot_credit = 0.0
    for acc in leaf_accounts:
        m = acc_meta.get(acc, {})
        op = opening.get(acc, 0.0)
        txns = tx_by_account.get(acc, [])
        running = op
        per_debit = per_credit = 0.0
        out_txns = []
        for tr in txns:
            d = float(tr.get("debit") or 0.0)
            c = float(tr.get("credit") or 0.0)
            vt = tr.get("voucher_type") or ""
            vno = tr.get("voucher_no") or ""
            # v2.56.0 — the row carries its own account. When the ledger is
            # grouped by party the account is no longer implied by the block
            # heading, so it has to travel with the transaction.
            am = acc_meta.get(acc, {})
            common = {
                "posting_date": str(tr.get("posting_date") or ""),
                "voucher_type": vt,
                "voucher_no": vno,
                "account": acc,
                "account_label": (f"{am.get('account_number')} - " if am.get("account_number") else "")
                                 + (am.get("account_name") or acc),
                "party": tr.get("party") or "",
                "party_type": tr.get("party_type") or "",
                "cost_center": tr.get("cost_center") or "",
                "project": tr.get("project") or "",
                "department": tr.get("department") or "",
                "remarks": tr.get("remarks") or "",
                "description": "",
            }
            legs = contra_map.get((vt, vno), []) if int(split_by_against or 0) else []
            if legs:
                # Split this voucher into one row per other account. The portion
                # attributable to each contra leg = (its credit − its debit),
                # which maps to this account's movement; portions sum to d − c
                # for a balanced voucher, so the running balance stays exact.
                emitted = 0.0
                for leg in legs:
                    portion = leg["credit"] - leg["debit"]
                    rdeb = portion if portion > 0 else 0.0
                    rcred = -portion if portion < 0 else 0.0
                    running += rdeb - rcred
                    per_debit += rdeb
                    per_credit += rcred
                    emitted += rdeb - rcred
                    out_txns.append({
                        **common,
                        "against": leg["display"],
                        "contra_party": leg["party"],
                        "debit": rdeb, "credit": rcred,
                        "balance_raw": running,
                    })
                # Residual guard: if legs didn't balance to the bank movement
                # (e.g. multi-bank voucher), add a correcting line so totals tie.
                resid = (d - c) - emitted
                if abs(resid) >= 0.005:
                    rdeb = resid if resid > 0 else 0.0
                    rcred = -resid if resid < 0 else 0.0
                    running += rdeb - rcred
                    per_debit += rdeb
                    per_credit += rcred
                    out_txns.append({
                        **common, "against": tr.get("against") or "",
                        "debit": rdeb, "credit": rcred, "balance_raw": running,
                    })
            else:
                running += d - c
                per_debit += d
                per_credit += c
                out_txns.append({
                    **common,
                    "against": tr.get("against") or "",
                    "debit": d, "credit": c,
                    "balance_raw": running,
                })
        closing = op + (per_debit - per_credit)

        # Visibility rules.
        has_txn = len(out_txns) > 0
        if not has_txn:
            if op != 0.0:
                if not (show_without_transactions or show_only_opening):
                    continue
            else:
                if not show_without_transactions:
                    continue
        if show_only_opening and has_txn:
            # show_only_opening narrows to opening-only accounts when set alone,
            # but we still honour accounts WITH transactions unless the user also
            # cleared the default set. Keep transactional accounts visible.
            pass
        if abs(closing) < 0.005 and not show_zero_closing:
            # hide zero-closing accounts unless explicitly requested
            if not (show_only_opening and op != 0.0 and not has_txn):
                continue

        blocks.append({
            "account": acc,
            "account_number": m.get("account_number") or "",
            "account_name": m.get("account_name") or acc,
            "root_type": m.get("root_type") or "",
            "opening_raw": op,
            "transactions": out_txns,
            "sub_total": {"debit": per_debit, "credit": per_credit},
            "closing_raw": closing,
        })
        tot_debit += per_debit
        tot_credit += per_credit

    # ── Description (v2.56.0) — opt-in, because it costs one query per
    # voucher type and most runs don't display the column.
    if int(with_description or 0):
        desc = _gl_descriptions(tx_rows)
        if desc:
            for b in blocks:
                for tx in b["transactions"]:
                    tx["description"] = desc.get((tx.get("voucher_type"), tx.get("voucher_no")), "")

    # ── Source-document fields (v2.74.0) — the same batching, generalised.
    # Values land in tx["doc"] under "Doctype::fieldname" keys so two doctypes
    # can contribute a field of the same name without colliding.
    want = doc_fields
    if isinstance(want, str):
        try:
            want = json.loads(want)
        except Exception:
            want = None
    if isinstance(want, dict) and want:
        docmap = _gl_doc_fields(tx_rows, want)
        for b in blocks:
            for tx in b["transactions"]:
                tx["doc"] = docmap.get((tx.get("voucher_type"), tx.get("voucher_no")), {})

    # ── Grouping (v2.56.0). 'party' is the default when a supplier or
    # customer filter is active — that filter says the party is the subject
    # of the report, so it heads the block and the account moves into the
    # rows. 'account' forces the classic layout back.
    # v2.57.0 — grouping is explicit. An earlier build flipped to party
    # grouping automatically whenever a party filter was present, which
    # silently changed the behaviour of the existing General Ledger. The
    # Supplier and Customer ledgers ask for party grouping by name; the
    # account-grouped ledger is left exactly as it was.
    has_party_filter = bool(party_ors)
    gb = (group_by or "").strip().lower()
    if gb not in ("account", "party"):
        gb = "account"
    if gb == "party":
        blocks = _gl_regroup_by_party(blocks, base_where, params, from_date)

    return {
        "accounts": blocks,
        "report_total": {
            "debit": tot_debit, "credit": tot_credit,
            "balance_raw": sum(b["closing_raw"] for b in blocks),
        },
        "filters": {
            "from_date": from_date, "to_date": to_date, "company": eff_company,
            "account_count": len(blocks),
            "group_by": gb,
            "has_party_filter": 1 if has_party_filter else 0,
            # What the user excluded, echoed back so every export can print it.
            # A ledger with documents removed must say so on its face — a
            # printed statement that silently omits credit notes is the kind of
            # artefact that gets handed to an auditor and misread.
            "excluded_voucher_types": ex_types,
            "excluded_vouchers": ex_vouchers,
            "is_filtered": 1 if (ex_types or ex_vouchers) else 0,
            # Voucher types actually present in the window, so the exclusion
            # picker offers what this ledger contains rather than a generic list.
            "voucher_types": _gl_voucher_types(where_before_exclusions, params, from_date, to_date),
        },
    }


def _gl_sums_per_account_month(
    *,
    company: str | None,
    fiscal_year: int,
    months: list[int],
    cost_center: str | list | None,
    project: str | list | None,
    accounts: list[str],
    department: str | list | None = None,
    branch: str | list | None = None,
    row_scope: dict | None = None,
    period_mode: str = "fiscal_year",
    period_from_date: str | None = None,
    period_to_date: str | None = None,
    fy_start_month_override: int | None = None,
) -> dict[str, dict[int, float]]:
    """SUM(credit - debit) per (account, month) for one fiscal year.

    Returns {account_name: {month_int: amount}}. month_int is 0-indexed
    (0=Jan, 11=Dec) to match the rest of the codebase.

    `row_scope` (v1.9.10) is the source row's dimension scope
    {"dimension_type","dimension_values"}. It MUST be applied here so the
    drilldown reconciles with the parent row total — the main engine's
    _fetch_monthly_for_accounts applies the same scope, and a drilldown that
    ignored it would show unscoped (whole-company) account figures that don't
    sum to the scoped parent.

    v1.9.58 — native dim params accept lists (multi-select) and expand to
    IN(...) clauses. Scalar fallback preserved for backward compat.
    """
    if not accounts:
        return {}
    # v1.9.67 — use the SAME date resolver as the main engine so the drill
    # reconciles with the parent row in EVERY period mode (fiscal_year AND
    # date_range). Previously this hardcoded YEAR(posting_date)=fiscal_year +
    # a month window, which ignored date_range entirely — making expanded
    # account values span the wrong period and exceed their own parent total.
    from neotec_insight.neotec_insight.utils.fiscal_year import resolve_date_bounds
    _ds, _de = resolve_date_bounds(
        company, fiscal_year, min(months), max(months),
        fy_start_month_override=fy_start_month_override,
        period_mode=period_mode,
        period_from_date=period_from_date,
        period_to_date=period_to_date,
    )
    placeholders = ", ".join(f"%(a{i})s" for i in range(len(accounts)))
    conds = [
        "g.is_cancelled = 0",
        f"g.account IN ({placeholders})",
        "g.posting_date BETWEEN %(ds)s AND %(de)s",
        # v2.43.1 — closed years: drill accounts are P&L accounts; exclude
        # the Period Closing Voucher reversal so prior-year columns survive.
        "g.voucher_type != 'Period Closing Voucher'",
    ]
    params: dict[str, Any] = {"ds": _ds, "de": _de}
    for i, a in enumerate(accounts):
        params[f"a{i}"] = a
    if company:
        conds.append("g.company = %(company)s")
        params["company"] = company

    # v1.9.58 — native dim filters: scalar or list. Apply in one helper.
    def _apply_native(col: str, val) -> None:
        if val is None or val == "" or val == []:
            return
        if isinstance(val, list):
            include_blank = any(str(v).strip() == "__BLANK__" for v in val)
            vals = [v for v in val if v and str(v).strip() != "__BLANK__"]
            ors = []
            if vals:
                ph = ", ".join(f"%({col}_{i})s" for i in range(len(vals)))
                ors.append(f"g.`{col}` IN ({ph})")
                for i, v in enumerate(vals):
                    params[f"{col}_{i}"] = v
            if include_blank:
                ors.append(f"(g.`{col}` IS NULL OR g.`{col}` = '')")
            if ors:
                conds.append("(" + " OR ".join(ors) + ")")
        else:
            conds.append(f"g.`{col}` = %({col})s")
            params[col] = val
    _apply_native("cost_center", cost_center)
    _apply_native("project", project)
    _apply_native("department", department)
    _apply_native("branch", branch)

    # Row-level dimension scope — dimension IN (values). Same logic as the
    # main engine, so parent row and drilldown reconcile.
    if row_scope and isinstance(row_scope, dict):
        dim_type = (row_scope.get("dimension_type") or "").strip().lower()
        raw_vals = row_scope.get("dimension_values") or []
        include_blank = any(str(v).strip() == "__BLANK__" for v in raw_vals)
        dim_values = [v for v in raw_vals if v and str(v).strip() != "__BLANK__"]
        if dim_type and (dim_values or include_blank):
            col = {
                "department": "department",
                "cost center": "cost_center",
                "cost_center": "cost_center",
                "project": "project",
            }.get(dim_type, dim_type.replace(" ", "_"))
            try:
                gl_cols = set(frappe.db.get_table_columns("GL Entry"))
            except Exception:
                gl_cols = set()
            if col in gl_cols:
                ors = []
                if dim_values:
                    vph = ", ".join(f"%(sv{i})s" for i in range(len(dim_values)))
                    ors.append(f"g.`{col}` IN ({vph})")
                    for i, v in enumerate(dim_values):
                        params[f"sv{i}"] = v
                if include_blank:
                    ors.append(f"(g.`{col}` IS NULL OR g.`{col}` = '')")
                if ors:
                    conds.append("(" + " OR ".join(ors) + ")")

    sql = f"""
        SELECT g.account AS account,
               MONTH(g.posting_date) AS month_1based,
               SUM(g.credit - g.debit) AS amount
        FROM `tabGL Entry` g
        WHERE {' AND '.join(conds)}
        GROUP BY g.account, MONTH(g.posting_date)
    """
    rows = frappe.db.sql(sql, params, as_dict=True)
    out: dict[str, dict[int, float]] = {}
    for r in rows:
        acc = r["account"]
        m = int(r["month_1based"]) - 1
        out.setdefault(acc, {})[m] = float(r["amount"] or 0.0)
    return out


@frappe.whitelist()
def run_report(
    report: str,
    fiscal_year: int,
    month_from: int = 0,
    month_to: int = 11,
    segment: str = "total",
    cost_center: str | list | None = None,
    project: str | list | None = None,
    department: str | list | None = None,
    branch: str | list | None = None,
    prior_years: int = 1,
    comparison_mode: str = "vs_budget",
    granularity: str = "month_quarter",
    compare_to_book: str | None = None,
    sel_from=None,
    sel_to=None,
    use_cache: int = 1,
    dimension_filters: dict | str | None = None,
    fy_start_month_override: int | None = None,
    # v1.9.65 — period mode and date-range bounds.
    period_mode: str = "fiscal_year",
    period_from_date: str | None = None,
    period_to_date: str | None = None,
) -> dict:
    _require_read()
    from neotec_insight.neotec_insight.utils.periods import build_period_groups

    doc = _resolve_report_doc(report)
    fy = cint(fiscal_year)
    mf = max(0, min(cint(month_from), 11))
    mt = max(mf, min(cint(month_to), 11))
    py = max(0, min(cint(prior_years), 5))
    # v1.9.52 — sanitise custom-dimension filters. Whitelist-only; anything
    # not on the configured set is silently dropped before SQL.
    dim_filters_safe = _sanitise_dimension_filters(dimension_filters)
    # v1.9.58 — normalise native dim params. Frontend sends arrays (multi-
    # select) or scalars (legacy/integration); either works.
    cost_center = _normalise_dim_param(cost_center)
    project = _normalise_dim_param(project)
    department = _normalise_dim_param(department)
    branch = _normalise_dim_param(branch)

    # v1.9.60 — validate the reporting-calendar override. Frontend sends
    # this when the user has picked "Group: Apr-Mar" (or similar) to view
    # the company's books through a different fiscal calendar without
    # changing Company.year_start_date. Accept ints 1-12; coerce anything
    # invalid back to None so the company's configured calendar prevails.
    fy_override = None
    if fy_start_month_override is not None:
        try:
            _m = int(fy_start_month_override)
            if 1 <= _m <= 12:
                fy_override = _m
        except (TypeError, ValueError):
            pass

    # v1.9.65 — validate period_mode. Two values: 'fiscal_year' (default,
    # preserves all prior behavior) or 'date_range'. When in date_range
    # mode, period_from_date + period_to_date take over and the FY-based
    # bounds are ignored. Invalid mode silently coerced back to fiscal_year.
    pm = period_mode if period_mode in ("fiscal_year", "date_range") else "fiscal_year"
    pfd = period_from_date if pm == "date_range" else None
    ptd = period_to_date if pm == "date_range" else None

    cache_key = _execution_cache_key(
        report_name=doc.name,
        user=frappe.session.user,
        fiscal_year=fy,
        month_from=mf,
        month_to=mt,
        segment=segment,
        cost_center=sorted(cost_center) if cost_center else cost_center,
        project=sorted(project) if project else project,
        department=sorted(department) if department else department,
        branch=sorted(branch) if branch else branch,
        prior_years=py,
        comparison_mode=comparison_mode,
        granularity=granularity,
        sel_from=sel_from,
        sel_to=sel_to,
        compare_to_book=compare_to_book,
        dimension_filters=dim_filters_safe,
        # v1.9.60 — override must be part of the cache key so the same
        # (report, fy, dim filters) computed for "local Jan-Dec" doesn't
        # collide with the "group Apr-Mar" view of the same span.
        fy_override=fy_override,
        # v1.9.65 — period mode + dates also part of the key so date-
        # range runs don't collide with FY runs for the same span.
        period_mode=pm,
        period_from_date=pfd,
        period_to_date=ptd,
    )
    if cint(use_cache):
        cached = frappe.cache().get_value(cache_key)
        if isinstance(cached, dict):
            cached["cache_hit"] = True
            return cached

    started = time.perf_counter()
    flag_to_accounts = load_flag_to_accounts(doc.name)
    definition = json.loads(doc.definition_json or "{}")

    current = execute_report(
        report_def=definition,
        fiscal_year=fy,
        month_from=mf,
        month_to=mt,
        segment=segment,
        cost_center=cost_center,
        project=project,
        department=department,
        branch=branch,
        company=doc.company,
        flag_to_accounts=flag_to_accounts,
        dimension_filters=dim_filters_safe,
        fy_start_month_override=fy_override,
        period_mode=pm,
        period_from_date=pfd,
        period_to_date=ptd,
    )

    priors = []
    # v1.9.65 — prior years only make sense in fiscal_year mode. In
    # date_range mode the concept of "prior year" doesn't have a clean
    # mapping (which year does "prior" mean when the range is Mar-May?).
    # Skip prior-year computation entirely in date_range mode; the
    # response just has the current span and no comparatives.
    if pm == "fiscal_year":
        for offset in range(1, py + 1):
            pry = fy - offset
            # v2.42.2 — the error message Noor asked for: resolve and RETURN
            # the exact date window each prior year queried, and log loudly if
            # a prior computes empty while the current year has data.
            try:
                from neotec_insight.neotec_insight.utils.fiscal_year import fy_month_range_to_date_range
                _pds, _pde = fy_month_range_to_date_range(doc.company, pry, mf, mt,
                                                          fy_start_month_override=fy_override)
                _pdbg = {"from": str(_pds), "to": str(_pde)}
            except Exception as _e:
                _pdbg = {"error": str(_e)}
                frappe.log_error(frappe.get_traceback(),
                                 f"Insight priors: date resolution failed fy={pry}")
            priors.append(
                {
                    "fiscal_year": pry,
                    "debug": _pdbg,
                    "rows": execute_report(
                        report_def=definition,
                        fiscal_year=pry,
                        month_from=mf,
                        month_to=mt,
                        segment=segment,
                        cost_center=cost_center,
                        project=project,
                        department=department,
                        branch=branch,
                        company=doc.company,
                        flag_to_accounts=flag_to_accounts,
                        dimension_filters=dim_filters_safe,
                        fy_start_month_override=fy_override,
                    )["rows"],
                }
            )
        for pr in priors:
            try:
                tot = 0.0
                for r in pr.get("rows") or []:
                    for v in (r.get("monthly") or {}).values():
                        tot += abs(float(v or 0))
                pr["debug"]["rows_abs_sum"] = round(tot, 2)
                if tot < 1:
                    frappe.log_error(
                        f"prior fy={pr['fiscal_year']} dates={pr['debug']} mf={mf} mt={mt} "
                        f"segment={segment} company={doc.company} override={fy_override}",
                        "Insight priors: EMPTY prior-year result")
            except Exception:
                pass

    # v1.9.66 — date-range comparative: compute ONE prior period that is the
    # SAME span shifted back exactly one year (e.g. 01-Apr-2025→31-Mar-2026
    # compares to 01-Apr-2024→31-Mar-2025). Only when the caller asked for at
    # least one prior year. This is purely additive — fiscal_year mode is
    # untouched above.
    elif pm == "date_range" and pfd and ptd and py >= 1:
        from frappe.utils import add_to_date, getdate
        try:
            ppfd = str(add_to_date(getdate(pfd), years=-1))
            pptd = str(add_to_date(getdate(ptd), years=-1))
            priors.append(
                {
                    "fiscal_year": getdate(ppfd).year,
                    "period_from_date": ppfd,
                    "period_to_date": pptd,
                    "rows": execute_report(
                        report_def=definition,
                        fiscal_year=getdate(ppfd).year,
                        month_from=mf,
                        month_to=mt,
                        segment=segment,
                        cost_center=cost_center,
                        project=project,
                        department=department,
                        branch=branch,
                        company=doc.company,
                        flag_to_accounts=flag_to_accounts,
                        dimension_filters=dim_filters_safe,
                        fy_start_month_override=fy_override,
                        period_mode="date_range",
                        period_from_date=ppfd,
                        period_to_date=pptd,
                    )["rows"],
                }
            )
        except Exception:
            frappe.log_error(frappe.get_traceback(), "Neotec Insight · date-range prior")

    budget = None
    if comparison_mode == "vs_budget" and pm == "fiscal_year":
        budget = _load_budget(
            report=doc.name,
            fiscal_year=fy,
            month_from=mf,
            month_to=mt,
            book_slug=compare_to_book,
            cost_center=cost_center,
            project=project,
            department=department,
            branch=branch,
            rows=definition.get("rows", []),
            prior_year_rows=priors[0]["rows"] if priors else None,
            company=doc.company,
            fy_start_month_override=fy_override,
        )

    # v1.9.59/v1.9.60 — pass company AND optional override so month labels
    # honour either the company's configured FY (default) or the
    # group-reporting override calendar when active.
    period_groups = build_period_groups(mf, mt, granularity, company=doc.company, fy_start_month_override=fy_override, sel_from=cint(sel_from) if sel_from is not None and str(sel_from) != '' else None, sel_to=cint(sel_to) if sel_to is not None and str(sel_to) != '' else None)

    # v1.9.59 — surface FY orientation so the frontend can label tabular
    # columns in the company's fiscal-year order (Apr-Mar for India,
    # Jan-Dec for KSA, etc.) and display the canonical FY label.
    # v1.9.60 — when override is active, surface that orientation instead
    # of the company's configured one so the UI reflects what was rendered.
    from neotec_insight.neotec_insight.utils.fiscal_year import (
        get_company_fy_start_month, format_fy_label,
    )
    _fy_start_month = get_company_fy_start_month(doc.company, override=fy_override)
    _fy_label = format_fy_label(doc.company, fy, fy_start_month_override=fy_override)

    payload = {
        "report": {"name": doc.name, "report_name": doc.report_name, "slug": doc.slug},
        "filters": {
            "fiscal_year": fy,
            "month_from": mf,
            "month_to": mt,
            "segment": segment,
            "company": doc.company or None,
            "cost_center": cost_center,
            "project": project,
            "department": department,
            "branch": branch,
            "dimension_filters": dim_filters_safe,
            "prior_years": py,
            "comparison_mode": comparison_mode,
            "granularity": period_groups["granularity"],
            "sel_from": cint(sel_from) if sel_from is not None and str(sel_from) != "" else None,
            "sel_to": cint(sel_to) if sel_to is not None and str(sel_to) != "" else None,
            "compare_to_book": compare_to_book,
            "compare_to_book_resolved": (budget or {}).get("book") if budget else None,
            "fy_start_month": _fy_start_month,    # effective FY start month (override or company config)
            "fy_label": _fy_label,                # "FY 2025" or "FY 2024-25", reflects effective calendar
            # v1.9.60 — surface the override so the frontend can render
            # the calendar-mode toggle in its active state on subsequent
            # page loads. None = local calendar (company config).
            "fy_start_month_override": fy_override,
            # v1.9.65 — period mode echo so the frontend can render its
            # active selector state and exports can use the right header.
            "period_mode": pm,
            "period_from_date": pfd,
            "period_to_date": ptd,
        },
        "current": current,
        "priors": priors,
        "budget": budget,
        "binding_meta": flag_binding_meta(doc.name, flag_to_accounts, definition.get("rows", [])),
        "period_groups": period_groups["groups"],
        "performance": {"execution_ms": int((time.perf_counter() - started) * 1000), "cache_hit": False},
    }
    if cint(use_cache):
        frappe.cache().set_value(cache_key, payload, expires_in_sec=EXECUTION_CACHE_TTL_SECONDS)
    return payload


@frappe.whitelist()
def report_integrity(
    report: str,
    fiscal_year: int,
    month_from: int = 0,
    month_to: int = 11,
    period_mode: str = "fiscal_year",
    period_from_date: str | None = None,
    period_to_date: str | None = None,
    fy_start_month_override: int | None = None,
) -> dict:
    """Read-only integrity & coverage audit for a report over the chosen period.

    Surfaces unmapped active accounts (coverage gaps), double-count risk, orphan
    mappings, empty rows and accounts that auto-joined live group rows. See
    neotec_insight.utils.integrity for the finding definitions. Writes nothing.
    """
    _require_write("Insight Report Definition")
    from neotec_insight.neotec_insight.utils.integrity import analyze_report_integrity

    doc = _resolve_report_doc(report)
    fy = cint(fiscal_year)
    mf = max(0, min(cint(month_from), 11))
    mt = max(mf, min(cint(month_to), 11))
    fy_override = None
    if fy_start_month_override is not None:
        try:
            _m = int(fy_start_month_override)
            if 1 <= _m <= 12:
                fy_override = _m
        except (TypeError, ValueError):
            pass
    pm = period_mode if period_mode in ("fiscal_year", "date_range") else "fiscal_year"
    return analyze_report_integrity(
        report_name=doc.name,
        fiscal_year=fy,
        month_from=mf,
        month_to=mt,
        period_mode=pm,
        period_from_date=period_from_date if pm == "date_range" else None,
        period_to_date=period_to_date if pm == "date_range" else None,
        fy_start_month_override=fy_override,
    )


def _allocation_budget_monthly(*, rule: str, fiscal_year: int, months: list[int],
                               cost_center=None, company: str | None = None,
                               fy_start_month_override: int | None = None) -> tuple[dict, dict]:
    """Budget for one allocation row, from the rule's own entries.

    Returns (monthly, has_cell). `has_cell` is True only where a budget was
    actually entered, so a month with no budget shows blank rather than a zero —
    the same distinction the Budget Book makes between "budgeted nil" and "not
    budgeted".

    `cost_center` may be a single name, a list, or None. None means consolidated
    and sums every cost centre on the rule.

    `months` is FY-month position (0..11), the same convention every other
    row in this report uses — NOT the calendar month. `Insight Allocation
    Entry.period_month` is a real calendar date, so every entry read here
    must be converted from calendar month to FY position before it can be
    used as a key into `monthly`/`has_cell`. Skipping that conversion (as an
    earlier version of this function did) puts a calendar-January entry
    under key 1 rather than key 0 for a January-start company — FY position 1
    is February, so every entered month printed one column late on the
    statement.
    """
    from neotec_insight.neotec_insight.utils.allocation import month_end, month_start
    from neotec_insight.neotec_insight.utils.fiscal_year import fy_month_for_calendar_month

    ccs = None
    if isinstance(cost_center, str) and cost_center.strip():
        ccs = [cost_center]
    elif isinstance(cost_center, (list, tuple)) and cost_center:
        ccs = [c for c in cost_center if c]

    filters = {"rule": rule,
               "period_month": ["between", [month_start(fiscal_year, 1),
                                            month_end(fiscal_year, 12)]]}
    if ccs:
        filters["cost_center"] = ["in", ccs]

    monthly = {m: 0.0 for m in months}
    has_cell = {m: False for m in months}
    try:
        entries = frappe.get_all("Insight Allocation Entry", filters=filters,
                                 fields=["period_month", "budget_amount"],
                                 limit_page_length=0)
    except Exception:
        # A missing rule or a pre-upgrade table must not take down the whole
        # P&L — the row simply carries no budget, same as any unbudgeted line.
        return monthly, has_cell

    for e in entries:
        amt = flt(e.get("budget_amount"))
        if not amt:
            continue
        pm = e["period_month"]
        cal_month = pm.month if hasattr(pm, "month") else int(str(pm)[5:7])
        m = fy_month_for_calendar_month(company, cal_month, fy_start_month_override)
        if m in monthly:
            monthly[m] = flt(monthly[m] + amt, 2)
            has_cell[m] = True
    return monthly, has_cell


def _load_budget(
    *,
    report: str,
    fiscal_year: int,
    month_from: int,
    month_to: int,
    book_slug: str | None,
    rows: list[dict],
    prior_year_rows: list[dict] | None,
    cost_center: str | None = None,
    project: str | None = None,
    department: str | None = None,
    branch: str | None = None,
    company: str | None = None,
    fy_start_month_override: int | None = None,
) -> dict:
    """Load the budget for a report by resolving a Budget Book.

    The book selection algorithm:

    1. If `book_slug` was passed, use that exact book.
    2. Otherwise infer from the dimension filters on the active run:
         cost_center → look for FY's cost_center book matching that value
         project    → same for project, department → same for department
         (nothing) → Total Company book
    3. If the resolved book is the report's Total book AND the report has a
       primary axis, sum every book on that axis instead of reading Total cells
       directly. Lets users budget per-CC and see a roll-up automatically.
    """
    months = list(range(month_from, month_to + 1))

    report_doc = frappe.get_doc("Insight Report Definition", report)
    company = company or report_doc.company
    primary_axis = (getattr(report_doc, "primary_budget_axis", None) or "none").strip()

    # Step 1/2: resolve the active book.
    active_book = _resolve_active_book(
        report=report,
        fiscal_year=fiscal_year,
        book_slug=book_slug,
        cost_center=cost_center,
        project=project,
        department=department,
    )

    # Step 3 (v2.35.1 — priority FIXED): the Total book's OWN cells always win
    # when they exist; the automatic roll-up of primary-axis books applies
    # ONLY when the Total book is empty. Previously the roll-up ran first, so
    # creating a single cost-center book silently replaced the Total budget
    # the user had entered — the "budget not refreshing" bug.
    by_row: dict[str, dict[int, float]] = {}
    budget_source = None
    if active_book and active_book.dimension_type == "total" and primary_axis != "none":
        cells = frappe.get_all(
            "Insight Budget Cell",
            filters={"book": active_book.name},
            fields=["row_key", "month", "amount"],
            limit_page_length=0,
        ) if active_book.name else []
        if cells:
            for c in cells:
                by_row.setdefault(c["row_key"], {})[int(c["month"])] = float(c["amount"] or 0.0)
            budget_source = "book"
        else:
            contributing = frappe.get_all(
                "Insight Budget Book",
                filters={
                    "report": report,
                    "fiscal_year": fiscal_year,
                    "dimension_type": primary_axis,
                },
                pluck="name",
                limit_page_length=0,
            )
            if contributing:
                cells = frappe.get_all(
                    "Insight Budget Cell",
                    filters={"book": ["in", contributing]},
                    fields=["row_key", "month", "amount"],
                    limit_page_length=0,
                )
                for c in cells:
                    m = int(c["month"])
                    by_row.setdefault(c["row_key"], {}).setdefault(m, 0.0)
                    by_row[c["row_key"]][m] += float(c["amount"] or 0.0)
                budget_source = {"rollup": primary_axis, "books": len(contributing)}
    elif active_book:
        cells = frappe.get_all(
            "Insight Budget Cell",
            filters={"book": active_book.name},
            fields=["row_key", "month", "amount"],
            limit_page_length=0,
        )
        for c in cells:
            by_row.setdefault(c["row_key"], {})[int(c["month"])] = float(c["amount"] or 0.0)
        budget_source = "book"
    # else: no book at all → by_row stays empty, all rows fall back to FY-1 × 1.10.

    # v1.9.56 — the FY-1 × 1.10 in-memory fallback is REMOVED. Every value in
    # a BUDGET column now comes from a real Insight Budget Cell document. If
    # no cell exists for a (row, month), the budget for that cell is left at
    # 0.0 — the frontend renders this as '—' (em dash), not as a number, so
    # the user can tell that no budget was entered. Admins can populate
    # cells via manual entry, the Derive action, or the Copy action.
    out_rows = []
    ctx: dict[str, dict[int, float]] = {}
    has_cell_by_key: dict[str, dict[int, bool]] = {}
    from neotec_insight.neotec_insight.utils.formula import evaluate_row_formula

    for row in rows:
        key = row.get("key")
        kind = row.get("kind")
        monthly: dict[int, float] = {m: 0.0 for m in months}
        # has_cell[m] tracks whether a real budget cell exists for (row, m).
        # Source rows: True iff the (row, m) cell is stored.
        # Formula rows: True iff at least one source row contributing to
        # the formula has a cell at m. Conservative: if no source rows have
        # cells, the formula result is meaningless and we mark it empty.
        has_cell: dict[int, bool] = {m: False for m in months}

        # ── Allocation rows (v2.79.0) ───────────────────────────────────────
        # An allocation row's budget does NOT live in a Budget Book. It is
        # entered per cost centre per month on the rule itself, because that is
        # the grain the business budgets an allocation at — a single Budget Cell
        # against the P&L line could not express "27,382 to Financial & Admin
        # and nothing to the others."
        #
        # So it is read from Insight Allocation Entry and filtered to the SAME
        # cost centre the run is filtered to. Consolidated, every cost centre's
        # budget sums, which matches the actual: consolidated, the row shows the
        # whole pool spread across all of them.
        if kind == "allocation" and row.get("allocation_rule"):
            monthly, has_cell = _allocation_budget_monthly(
                rule=row["allocation_rule"], fiscal_year=fiscal_year,
                months=months, cost_center=cost_center,
                company=company, fy_start_month_override=fy_start_month_override)
            # v2.85.0 — the same visibility rule the actuals obey.
            #
            # `_load_budget` had no notion of show_when, so a row hidden on the
            # actual side still carried a budget. On a consolidated P&L that
            # printed as Actual 0 against a real Budget figure — which reads as
            # "budgeted and not spent" rather than "not applicable at this
            # level", and the % Achieved beside it was computed from it.
            from neotec_insight.neotec_insight.utils.execution import is_row_hidden
            single_cc = cost_center if isinstance(cost_center, str) and cost_center.strip() else None
            if isinstance(cost_center, (list, tuple)) and len(cost_center) == 1:
                single_cc = cost_center[0]
            if is_row_hidden(row, "allocation", single_cc):
                monthly = {m: 0.0 for m in months}
                has_cell = {m: False for m in months}
            ctx[key] = monthly
            has_cell_by_key[key] = has_cell
            out_rows.append({**row, "monthly": monthly, "has_cell": has_cell})
            continue

        if kind == "source":
            stored = by_row.get(key, {})
            for m in months:
                if m in stored:
                    monthly[m] = stored[m]
                    has_cell[m] = True
        elif kind == "formula":
            formula = row.get("formula") or "0"
            for m in months:
                sub = {k: v.get(m, 0.0) for k, v in ctx.items()}
                try:
                    monthly[m] = evaluate_row_formula(formula, sub)
                except Exception:
                    monthly[m] = 0.0
                # has_cell[m] = True iff any prior source row had a real
                # cell at m. Defensible heuristic — formula results aren't
                # entered, they're computed, so they "exist" iff their
                # inputs do.
                has_cell[m] = any(hc.get(m, False) for hc in has_cell_by_key.values())

        ctx[key] = monthly
        has_cell_by_key[key] = has_cell
        out_rows.append({**row, "monthly": monthly, "has_cell": has_cell})

    return {
        "rows": out_rows,
        "months": months,
        "book": _book_summary(active_book) if active_book else None,
        "source": budget_source,
    }


def _resolve_active_book(
    *,
    report: str,
    fiscal_year: int,
    book_slug: str | None,
    cost_center: str | None = None,
    project: str | None = None,
    department: str | None = None,
):
    """Return the Insight Budget Book document chosen for this run.

    Resolution order:
      explicit book_slug → cost_center filter → project filter → department filter → Total book.
    Auto-creates the Total book the first time someone runs a report without
    one — so a fresh install can render Actual vs Budget without setup.
    """
    if book_slug:
        name = frappe.db.get_value("Insight Budget Book", {"slug": book_slug}, "name")
        if name:
            return frappe.get_doc("Insight Budget Book", name)

    candidates = []
    if cost_center:
        candidates.append(("cost_center", cost_center))
    if project:
        candidates.append(("project", project))
    if department:
        candidates.append(("department", department))

    for dim_type, dim_value in candidates:
        name = frappe.db.get_value(
            "Insight Budget Book",
            {
                "report": report,
                "fiscal_year": fiscal_year,
                "dimension_type": dim_type,
                "dimension_value": dim_value,
            },
            "name",
        )
        if name:
            return frappe.get_doc("Insight Budget Book", name)

    # Fall back to the Total book; auto-create if missing.
    total_name = frappe.db.get_value(
        "Insight Budget Book",
        {
            "report": report,
            "fiscal_year": fiscal_year,
            "dimension_type": "total",
        },
        "name",
    )
    if total_name:
        return frappe.get_doc("Insight Budget Book", total_name)

    # Auto-create on first read so the UI always has a book to attach to.
    from neotec_insight.neotec_insight.doctype.insight_budget_book.insight_budget_book import (
        auto_label,
        auto_slug,
    )
    book = frappe.new_doc("Insight Budget Book")
    book.report = report
    book.fiscal_year = fiscal_year
    book.dimension_type = "total"
    book.dimension_value = None
    book.label = auto_label(fiscal_year, "total", None)
    book.slug = auto_slug(report, fiscal_year, "total", None)
    book.status = "approved"
    book.is_primary_axis_book = 1
    book.insert(ignore_permissions=True)
    frappe.db.commit()
    return book


def _book_summary(book) -> dict:
    return {
        "name": book.name,
        "slug": book.slug,
        "label": book.label,
        "dimension_type": book.dimension_type,
        "dimension_value": book.dimension_value or None,
        "fiscal_year": int(book.fiscal_year),
        "status": book.status,
        "owner_user": book.owner_user or None,
        "is_primary_axis_book": int(book.is_primary_axis_book or 0),
    }


@frappe.whitelist(methods=["POST"])
def save_budget_cells(book: str, cells: str | list) -> dict:
    """Bulk save budget cells against a Budget Book.

    `book` is the book name (or slug). Each cell only needs row_key, month,
    amount; report and fiscal_year are inherited from the book.
    """
    _require_write("Insight Budget Book")
    _check_edit_permission()
    book_doc = _resolve_book_doc(book)
    if not book_doc.can_user_edit():
        frappe.throw(f"You don't have permission to edit book '{book_doc.label}' (status: {book_doc.status}).")

    parsed = frappe.parse_json(cells)
    if not isinstance(parsed, list):
        frappe.throw("cells must be a list.")
    written = 0
    deleted = 0
    for c in parsed:
        if not isinstance(c, dict):
            continue
        row_key = str(c.get("row_key") or "").strip()
        if not row_key:
            continue
        month = cint(c.get("month"))
        if not 0 <= month <= 11:
            continue
        amount = float(c.get("amount") or 0.0)
        filters = {
            "book": book_doc.name,
            "row_key": row_key,
            "month": month,
        }
        existing = frappe.db.get_value("Insight Budget Cell", filters, "name")
        if amount == 0.0 and existing:
            # Treat zero as a delete to keep the table compact.
            frappe.delete_doc("Insight Budget Cell", existing, ignore_permissions=True, force=True)
            deleted += 1
            continue
        if existing:
            d = frappe.get_doc("Insight Budget Cell", existing)
            d.amount = amount
            d.save(ignore_permissions=True)
        else:
            d = frappe.new_doc("Insight Budget Cell")
            d.book = book_doc.name
            d.report = book_doc.report
            d.fiscal_year = book_doc.fiscal_year
            d.month = month
            d.row_key = row_key
            d.segment = book_doc.dimension_value or "total"  # legacy mirror
            d.amount = amount
            d.insert(ignore_permissions=True)
        written += 1
    _bump_cache_gen(book_doc.report)
    return {"written": written, "deleted": deleted, "book": _book_summary(book_doc)}


def _resolve_book_doc(book: str):
    """Accept either a Frappe doc name or a slug."""
    if not book or not book.strip():
        frappe.throw("book is required.")
    book = book.strip()
    if frappe.db.exists("Insight Budget Book", book):
        return frappe.get_doc("Insight Budget Book", book)
    name = frappe.db.get_value("Insight Budget Book", {"slug": book}, "name")
    if not name:
        frappe.throw(f"Budget Book '{book}' not found.")
    return frappe.get_doc("Insight Budget Book", name)





@frappe.whitelist()
def list_account_mappings(report: str) -> list[dict]:
    _require_read()
    doc = _resolve_report_doc(report)
    rows = frappe.get_all(
        "Account Flag Mapping",
        filters={"report": doc.name},
        fields=["name", "account", "account_code", "account_name", "flag",
                "source", "auto_suggested", "is_group_binding",
                "dimension_filters_json", "scope_summary"],
        limit_page_length=0,
        order_by="account_code asc",
    )
    # Parse the JSON scope into a list the frontend can render directly.
    for r in rows:
        raw = r.get("dimension_filters_json")
        try:
            r["dimension_filters"] = json.loads(raw) if raw else []
        except Exception:
            r["dimension_filters"] = []
    return rows


@frappe.whitelist(methods=["POST"])
def save_account_mapping(
    report: str,
    account: str,
    flag: str,
    is_group_binding: int = 0,
    dimension_filters: str | list | None = None,
    mapping_name: str | None = None,
) -> dict:
    """Create or update one Account Flag Mapping row, scope included.

    `mapping_name` — when given, updates that specific row (needed because one
    account can now have several rows under different flags/scopes). When
    omitted, creates a new row.

    `dimension_filters` — JSON array (or list) of {dimension_type, dimension_value}.
    Empty / null = whole company.
    """
    _require_write("Insight Report Definition")
    doc = _resolve_report_doc(report)
    if not account or not (flag or "").strip():
        frappe.throw("account and flag are required.")

    # Normalize the filters argument to a JSON string.
    if dimension_filters is None:
        filters_json = ""
    elif isinstance(dimension_filters, str):
        filters_json = dimension_filters
    else:
        filters_json = json.dumps(dimension_filters)

    if mapping_name:
        d = frappe.get_doc("Account Flag Mapping", mapping_name)
        if d.report != doc.name:
            frappe.throw("Mapping does not belong to this report.")
    else:
        d = frappe.new_doc("Account Flag Mapping")
        d.report = doc.name
        d.account = account
    d.account = account
    d.flag = flag.strip()
    d.is_group_binding = cint(is_group_binding)
    d.dimension_filters_json = filters_json
    d.source = "manual"
    d.auto_suggested = 0
    if mapping_name:
        d.save(ignore_permissions=True)
    else:
        d.insert(ignore_permissions=True)
    _bump_cache_gen(doc.name)
    return {"saved": True, "name": d.name, "scope_summary": d.scope_summary or ""}


@frappe.whitelist(methods=["POST"])
def delete_account_mapping(report: str, mapping_name: str) -> dict:
    """Delete one Account Flag Mapping row by name."""
    _require_write("Insight Report Definition")
    doc = _resolve_report_doc(report)
    if not mapping_name:
        frappe.throw("mapping_name is required.")
    existing = frappe.db.get_value("Account Flag Mapping", mapping_name, "report")
    if existing != doc.name:
        frappe.throw("Mapping does not belong to this report.")
    frappe.delete_doc("Account Flag Mapping", mapping_name, ignore_permissions=True)
    _bump_cache_gen(doc.name)
    return {"deleted": True}


@frappe.whitelist()
def list_available_accounts(
    report: str, search: str = "", limit: int = 50, include_groups: int = 1
) -> list[dict]:
    """Return Frappe Accounts the user could map to this report.

    Behavior:
      - Searches across account_number, account_name, and the auto-generated
        Account `name` (which is "{code} - {name} - {abbr}") so a user pasting
        any of those finds the row.
      - Filters by the report's company when set. If that filter yields zero
        results AND the user typed a search term, falls back to a no-company
        search — better to over-show than to leave them stuck.
      - Returns up to `limit` rows. Group accounts included by default for
        bulk-binding via expand_account_group.
    """
    _require_read()
    doc = _resolve_report_doc(report)
    s = (search or "").strip()
    lim = cint(limit) or 50

    def _query(company_filter: str | None) -> list[dict]:
        filters: dict = {}
        if company_filter:
            filters["company"] = company_filter
        if not cint(include_groups):
            filters["is_group"] = 0

        or_filters: list = []
        if s:
            or_filters = [
                ["account_number", "like", f"%{s}%"],
                ["account_name", "like", f"%{s}%"],
                ["name", "like", f"%{s}%"],
            ]

        return frappe.get_all(
            "Account",
            filters=filters,
            or_filters=or_filters,
            fields=[
                "name", "account_number", "account_name", "root_type",
                "account_type", "is_group", "lft", "rgt", "company",
            ],
            order_by="account_number asc, account_name asc",
            limit_page_length=lim,
        )

    rows = _query(doc.company if doc.company else None)

    # Fallback: if a company is set on the report and the user has a search
    # term but got nothing, retry without the company filter. Often the
    # account exists but on a sibling company, or the report's `company`
    # field is stale. We still annotate which company each row belongs to
    # so the frontend can show it.
    if not rows and s and doc.company:
        rows = _query(None)

    # Look up which accounts are already mapped on this report — but don't
    # filter them out. Earlier behavior was to hide them ("you can't map an
    # already-mapped account again"), which was wrong: the user often wants
    # to *change* the flag, not blindly add a new one. We annotate instead.
    existing_mappings = frappe.get_all(
        "Account Flag Mapping",
        filters={"report": doc.name},
        fields=["name", "account", "flag", "source", "is_group_binding"],
        limit_page_length=0,
    )
    by_account: dict[str, dict] = {m["account"]: m for m in existing_mappings}

    for r in rows:
        m = by_account.get(r["name"])
        if m:
            r["existing_mapping"] = {
                "name": m["name"],
                "flag": m.get("flag") or "",
                "source": m.get("source") or "",
                "is_group_binding": int(m.get("is_group_binding") or 0),
            }
        else:
            r["existing_mapping"] = None

    return rows


@frappe.whitelist()
def expand_account_group(account: str) -> list[dict]:
    """Return all non-group descendants of a group account.

    Uses Frappe's nested-set columns (lft/rgt) for a single-query lookup.
    """
    _require_read()
    parent = frappe.db.get_value(
        "Account", account, ["name", "is_group", "lft", "rgt", "company"], as_dict=True
    )
    if not parent:
        frappe.throw(f"Account '{account}' not found.")
    if not parent.is_group:
        return [
            {
                "name": parent.name,
                "account_number": frappe.db.get_value("Account", parent.name, "account_number"),
                "account_name": frappe.db.get_value("Account", parent.name, "account_name"),
            }
        ]
    return frappe.get_all(
        "Account",
        filters={
            "is_group": 0,
            "lft": [">", parent.lft],
            "rgt": ["<", parent.rgt],
            "company": parent.company,
        },
        fields=["name", "account_number", "account_name"],
        order_by="account_number asc",
        limit_page_length=0,
    )


@frappe.whitelist(methods=["POST"])
def bulk_set_account_flags(report: str, items: str | list) -> dict:
    """Bulk-create Account Flag Mappings. items = [{account, flag, bind_as_group?}, ...].

    Behavior depends on whether the account is a group and how the caller
    wants groups handled:

      - Leaf account: always create one mapping row.
      - Group account, bind_as_group=1: create ONE row with is_group_binding=1.
        At runtime, the execution engine resolves the group to its leaves
        using the Account tree's lft/rgt — so leaves added later are included
        automatically.
      - Group account, bind_as_group=0 or absent: expand to leaves now (the
        v1.6 behavior). Each leaf gets its own mapping row.

    Existing mappings on the same (report, account) are overwritten.
    """
    _require_write("Insight Report Definition")
    doc = _resolve_report_doc(report)
    parsed = frappe.parse_json(items)
    if not isinstance(parsed, list):
        frappe.throw("items must be a list.")

    created = 0
    skipped = 0
    expanded_total = 0
    group_bindings = 0
    warnings: list[str] = []

    for it in parsed:
        if not isinstance(it, dict):
            continue
        account = (it.get("account") or "").strip()
        flag = (it.get("flag") or "").strip()
        bind_as_group = bool(it.get("bind_as_group"))
        if not account or not flag:
            skipped += 1
            continue

        is_group = frappe.db.get_value("Account", account, "is_group")
        if is_group is None:
            warnings.append(f"Account '{account}' not found.")
            skipped += 1
            continue

        if is_group and bind_as_group:
            # Single mapping row that the engine resolves at runtime. New leaves
            # added under this group later are auto-included.
            _upsert_mapping(doc.name, account, flag, is_group_binding=True)
            created += 1
            group_bindings += 1
            continue

        # Either it's a leaf (treat as one row) or the caller asked us to
        # expand-now. Walk the leaves and upsert each one as a non-group binding.
        leaves = expand_account_group(account) if is_group else [{"name": account}]
        if not leaves:
            warnings.append(f"Group '{account}' has no leaf accounts under it.")
            skipped += 1
            continue
        if is_group:
            expanded_total += len(leaves)

        for leaf in leaves:
            _upsert_mapping(doc.name, leaf["name"], flag, is_group_binding=False)
            created += 1

    _bump_cache_gen(doc.name)
    return {
        "created": created, "skipped": skipped,
        "expanded_total": expanded_total, "group_bindings": group_bindings,
        "warnings": warnings,
    }


def _upsert_mapping(report: str, account: str, flag: str, is_group_binding: bool = False) -> None:
    """Upsert one Account Flag Mapping row.

    Keyed on (report, account, FLAG) — so binding an account to a *different*
    flag creates a NEW row rather than overwriting the existing binding. This
    is what lets one account feed several report rows at once. Re-binding the
    same account to the same flag is idempotent (updates that one row).
    """
    existing = frappe.db.get_value(
        "Account Flag Mapping",
        {"report": report, "account": account, "flag": flag},
        "name",
    )
    if existing:
        d = frappe.get_doc("Account Flag Mapping", existing)
        d.flag = flag
        d.source = "manual"
        d.auto_suggested = 0
        d.is_group_binding = 1 if is_group_binding else 0
        d.save(ignore_permissions=True)
    else:
        d = frappe.new_doc("Account Flag Mapping")
        d.report = report
        d.account = account
        d.flag = flag
        d.source = "manual"
        d.is_group_binding = 1 if is_group_binding else 0
        d.insert(ignore_permissions=True)


@frappe.whitelist()
def account_tree(company=None):
    """Return the full chart of accounts for a company as a flat, tree-ordered list.

    Read-only. Used by the General Ledger account picker to render an expandable
    group/parent → child tree and to resolve a group selection to its leaf
    accounts (via the nested-set lft/rgt range). Ordered by lft so the caller can
    build the hierarchy directly."""
    _require_read()
    company = company or _default_company()
    if not company:
        return []
    return frappe.get_all(
        "Account",
        filters={"company": company, "disabled": 0},
        fields=["name", "account_number", "account_name", "is_group",
                "parent_account", "lft", "rgt", "root_type"],
        order_by="lft asc",
        limit_page_length=0,
    )


_COGS_ACCOUNT_TYPES = {"Cost of Goods Sold", "Stock Adjustment"}


@frappe.whitelist()
def pl_hierarchy(
    report=None,
    company=None,
    fiscal_year=None,
    month_from=0,
    month_to=11,
    primary_dim="cost_center",
    secondary_dim="",
    period_mode="fiscal_year",
    period_from_date=None,
    period_to_date=None,
    fy_start_month_override=None,
    cost_center=None,
    project=None,
    finance_book=None,
    dimension_filters=None,
):
    """Hierarchical P&L drill-down. Read-only.

    Builds: primary dimension (e.g. Cost Center) → secondary dimension (e.g.
    Intercompany) → P&L section (Revenue / Cost of Sales / Operating Expenses)
    → account, with Gross Profit, Net Profit and margins computed at every node,
    plus grand totals and each primary node's revenue share.

    Dimension fieldnames are validated against the bench's known dimensions
    before entering SQL, so a custom 'Intercompany' Accounting Dimension works
    the same as the native cost_center/project."""
    _require_read()
    from frappe.utils import flt
    from neotec_insight.neotec_insight.utils.fiscal_year import resolve_date_bounds

    company = company or _default_company()
    if not company:
        frappe.throw("company is required.")

    valid = _all_valid_dimension_fieldnames()
    pfield = (primary_dim or "cost_center").strip()
    sfield = (secondary_dim or "").strip()
    if pfield not in valid:
        frappe.throw(f"Invalid primary dimension '{pfield}'.")
    if sfield and sfield not in valid:
        frappe.throw(f"Invalid secondary dimension '{sfield}'.")
    if sfield and sfield == pfield:
        frappe.throw("Primary and secondary dimensions must differ.")

    start, end = resolve_date_bounds(
        company, cint(fiscal_year) if fiscal_year else None, cint(month_from), cint(month_to),
        fy_start_month_override=cint(fy_start_month_override) if fy_start_month_override else None,
        period_mode=period_mode if period_mode in ("fiscal_year", "date_range") else "fiscal_year",
        period_from_date=period_from_date, period_to_date=period_to_date,
    )

    conds = ["g.company = %(company)s", "g.is_cancelled = 0",
             "g.voucher_type != 'Period Closing Voucher'",
             "g.posting_date BETWEEN %(start)s AND %(end)s",
             "a.root_type IN ('Income', 'Expense')"]
    params = {"company": company, "start": start, "end": end}

    def _in(field, vals, key):
        vals = _normalise_dim_param(vals)
        if not vals:
            return
        ph = ", ".join([f"%({key}{i})s" for i in range(len(vals))])
        conds.append(f"g.`{field}` IN ({ph})")
        for i, v in enumerate(vals):
            params[f"{key}{i}"] = v

    _in("cost_center", cost_center, "cc")
    _in("project", project, "pr")
    if finance_book:
        conds.append("g.finance_book = %(fb)s")
        params["fb"] = finance_book
    for i, (k, v) in enumerate(_parse_dimension_filters(dimension_filters).items()):
        if k in valid:
            _in(k, v, f"df{i}_")

    sel_p = f"g.`{pfield}`"
    sel_s = f"g.`{sfield}`" if sfield else "''"
    rows = frappe.db.sql(
        f"""SELECT g.account AS account, {sel_p} AS p_val, {sel_s} AS s_val,
                   SUM(g.debit) AS debit, SUM(g.credit) AS credit
            FROM `tabGL Entry` g JOIN `tabAccount` a ON a.name = g.account
            WHERE {' AND '.join(conds)}
            GROUP BY g.account, p_val, s_val""",
        params, as_dict=True)

    # Account metadata for classification + display.
    accts = {a for r in rows for a in [r["account"]]}
    ainfo = {}
    if accts:
        for a in frappe.get_all("Account", filters={"name": ["in", list(accts)]},
                                fields=["name", "account_number", "account_name", "root_type", "account_type"],
                                limit_page_length=0):
            ainfo[a["name"]] = a

    # v2.38.0 — ENGINE UNIFICATION. When a report definition is given, the
    # drill's Revenue / Cost of Sales buckets come from THAT definition:
    # the gross-profit formula's operands are expanded to source rows, and
    # their Account Flag Mappings (group bindings expanded to descendants)
    # become the account sets. Result: P&L Drill subtotals and KPIs can never
    # disagree with the Standard layout again. Heuristics remain only as the
    # fallback for unmapped accounts / no report given.
    rev_set, cogs_set = set(), set()
    if report:
        try:
            _doc = _resolve_report_doc(report)
            _defn = json.loads(_doc.definition_json or "{}")
            _rows = _defn.get("rows") or []
            _bykey = {r.get("key"): r for r in _rows if r.get("key")}

            def _expand(token, seen=None):
                seen = seen or set()
                if token in seen:
                    return set()
                seen.add(token)
                r = _bykey.get(token)
                if not r:
                    return set()
                if r.get("kind") == "source":
                    return {token}
                out = set()
                for t in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", r.get("formula") or ""):
                    out |= _expand(t, seen)
                return out

            gp = next((r for r in _rows if r.get("kind") == "formula" and (
                (r.get("key") or "") == "gross_profit"
                or re.search(r"gross\s*profit|مجمل", r.get("label") or "", re.I))), None)
            rev_keys, cogs_keys = set(), set()
            if gp:
                m = re.match(r"\s*(.+?)\s*-\s*(.+)\s*$", gp.get("formula") or "")
                if m:
                    for t in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", m.group(1)):
                        rev_keys |= _expand(t)
                    for t in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", m.group(2)):
                        cogs_keys |= _expand(t)
            flags_rev = {(_bykey[k].get("flag") or _bykey[k].get("label") or "").strip()
                         for k in rev_keys}
            flags_cogs = {(_bykey[k].get("flag") or _bykey[k].get("label") or "").strip()
                          for k in cogs_keys}
            maps = frappe.get_all("Account Flag Mapping", filters={"report": _doc.name},
                                  fields=["account", "flag", "is_group_binding"],
                                  limit_page_length=0)

            def _leafs(acc):
                lr = frappe.db.get_value("Account", acc, ["lft", "rgt"], as_dict=True)
                if not lr:
                    return {acc}
                return set(frappe.get_all("Account",
                                          filters={"lft": [">=", lr.lft], "rgt": ["<=", lr.rgt],
                                                   "is_group": 0},
                                          pluck="name", limit_page_length=0)) or {acc}

            for mp in maps:
                target = _leafs(mp["account"]) if mp.get("is_group_binding") else {mp["account"]}
                if (mp.get("flag") or "").strip() in flags_cogs:
                    cogs_set |= target
                elif (mp.get("flag") or "").strip() in flags_rev:
                    rev_set |= target
        except Exception:
            rev_set, cogs_set = set(), set()
            frappe.log_error(frappe.get_traceback(),
                             "pl_hierarchy: definition buckets failed — heuristic fallback")

    buckets_source = ("definition" if (rev_set or cogs_set) else
                      ("heuristic" if report else "heuristic (no report)"))

    def _section(info):
        name = info.get("name")
        if name in cogs_set:
            return "cogs"
        if name in rev_set:
            return "revenue"
        if info.get("root_type") == "Income":
            return "revenue"
        return "cogs" if info.get("account_type") in _COGS_ACCOUNT_TYPES else "opex"

    def _signed(info, debit, credit):
        # P&L natural amount: revenue is credit-positive, cost is debit-positive.
        return (credit - debit) if info.get("root_type") == "Income" else (debit - credit)

    # nest[p][s][section][account] = amount
    nest: dict = {}
    for r in rows:
        info = ainfo.get(r["account"]) or {}
        if not info:
            continue
        sec = _section(info)
        amt = flt(_signed(info, flt(r["debit"]), flt(r["credit"])))
        p = (r.get("p_val") or "").strip() or "(Unassigned)"
        s = (r.get("s_val") or "").strip() or "(Unassigned)"
        nest.setdefault(p, {}).setdefault(s, {}).setdefault(sec, {}).setdefault(r["account"], 0.0)
        nest[p][s][sec][r["account"]] += amt

    def _acct_label(name):
        info = ainfo.get(name) or {}
        code = info.get("account_number")
        nm = info.get("account_name") or name
        return {"account": name, "code": code or "", "label": (f"{code} · {nm}" if code else nm), "amount": 0.0}

    def _margins(rev, gp, np_):
        return (round(100 * gp / rev, 1) if rev else 0.0, round(100 * np_ / rev, 1) if rev else 0.0)

    SECTION_LABELS = {"revenue": "Revenue", "cogs": "Cost of Sales", "opex": "Operating Expenses"}

    def _build_secondary(secmap):
        rev = sum(secmap.get("revenue", {}).values())
        cogs = sum(secmap.get("cogs", {}).values())
        opex = sum(secmap.get("opex", {}).values())
        gp = rev - cogs
        npf = gp - opex
        gm, nm = _margins(rev, gp, npf)
        sections = []
        for key in ("revenue", "cogs", "opex"):
            accts_map = secmap.get(key, {})
            items = []
            for acc, amt in sorted(accts_map.items(), key=lambda kv: -abs(kv[1])):
                row = _acct_label(acc)
                row["amount"] = round(amt, 2)
                items.append(row)
            sections.append({"section": key, "label": SECTION_LABELS[key],
                             "amount": round(sum(accts_map.values()), 2), "accounts": items})
        return {
            "revenue": round(rev, 2), "cogs": round(cogs, 2), "gross_profit": round(gp, 2),
            "opex": round(opex, 2), "net_profit": round(npf, 2),
            "gross_margin": gm, "net_margin": nm, "sections": sections,
        }

    grand = {"revenue": 0.0, "cogs": 0.0, "opex": 0.0}
    tree = []
    for p, smap in nest.items():
        children = []
        p_rev = p_cogs = p_opex = 0.0
        for s, secmap in smap.items():
            node = _build_secondary(secmap)
            node["key"] = s
            node["label"] = s
            children.append(node)
            p_rev += node["revenue"]; p_cogs += node["cogs"]; p_opex += node["opex"]
        children.sort(key=lambda c: -c["net_profit"])
        p_gp = p_rev - p_cogs
        p_np = p_gp - p_opex
        gm, nm = _margins(p_rev, p_gp, p_np)
        grand["revenue"] += p_rev; grand["cogs"] += p_cogs; grand["opex"] += p_opex
        tree.append({
            "key": p, "label": p,
            "revenue": round(p_rev, 2), "cogs": round(p_cogs, 2), "gross_profit": round(p_gp, 2),
            "opex": round(p_opex, 2), "net_profit": round(p_np, 2),
            "gross_margin": gm, "net_margin": nm, "children": children,
        })
    tree.sort(key=lambda n: -n["net_profit"])

    g_rev = grand["revenue"]; g_gp = g_rev - grand["cogs"]; g_np = g_gp - grand["opex"]
    g_gm, g_nm = _margins(g_rev, g_gp, g_np)
    for n in tree:
        n["rev_share"] = round(100 * n["revenue"] / g_rev, 1) if g_rev else 0.0

    def _dim_label(field):
        if field == "cost_center":
            return "Cost Center"
        if field == "project":
            return "Project"
        try:
            from neotec_insight.neotec_insight.utils.dimensions import list_accounting_dimensions  # type: ignore
            for d in list_accounting_dimensions():
                if d.get("fieldname") == field:
                    return d.get("label") or field
        except Exception:
            pass
        return field.replace("_", " ").title()

    return {
        "view": "pl_hierarchy",
        "buckets_source": buckets_source,
        "buckets_mapped": {"revenue_accounts": len(rev_set), "cogs_accounts": len(cogs_set)},
        "primary_dim": {"fieldname": pfield, "label": _dim_label(pfield)},
        "secondary_dim": {"fieldname": sfield, "label": _dim_label(sfield)} if sfield else None,
        "period": {"from": str(start), "to": str(end), "fiscal_year": fiscal_year},
        "currency": frappe.get_cached_value("Company", company, "default_currency"),
        "tree": tree,
        "grand": {
            "revenue": round(g_rev, 2), "cogs": round(grand["cogs"], 2), "gross_profit": round(g_gp, 2),
            "opex": round(grand["opex"], 2), "net_profit": round(g_np, 2),
            "gross_margin": g_gm, "net_margin": g_nm,
        },
    }


@frappe.whitelist()
def list_accounts_for_flag(report: str, flag: str) -> list[dict]:
    """Return the accounts currently mapped to a specific flag on this report."""
    _require_read()
    doc = _resolve_report_doc(report)
    return frappe.get_all(
        "Account Flag Mapping",
        filters={"report": doc.name, "flag": flag},
        fields=["name", "account", "account_code", "account_name", "source"],
        order_by="account_code asc",
        limit_page_length=0,
    )


@frappe.whitelist()
def list_existing_flags(report: str) -> list[str]:
    """Return the set of P&L flags already in use across this report.

    Combines flags declared on source rows in the definition with flags found on
    existing Account Flag Mapping rows. Useful for populating the dropdown when
    a user adds an account manually.
    """
    _require_read()
    doc = _resolve_report_doc(report)
    definition = json.loads(doc.definition_json or "{}")
    flags: set[str] = set()
    for row in definition.get("rows", []):
        if row.get("kind") == "source":
            flag = (row.get("flag") or row.get("label") or "").strip()
            if flag:
                flags.add(flag)
    for m in frappe.get_all(
        "Account Flag Mapping",
        filters={"report": doc.name},
        fields=["flag"],
        limit_page_length=0,
    ):
        flag = (m.get("flag") or "").strip()
        if flag:
            flags.add(flag)
    return sorted(flags)


@frappe.whitelist(methods=["POST"])
def set_account_flag(report: str, account: str, flag: str | None) -> dict:
    _require_write("Insight Report Definition")
    doc = _resolve_report_doc(report)
    existing = frappe.db.get_value(
        "Account Flag Mapping",
        {"report": doc.name, "account": account},
        "name",
    )
    if not (flag or "").strip():
        if existing:
            frappe.delete_doc("Account Flag Mapping", existing, ignore_permissions=True)
        _bump_cache_gen(doc.name)
        return {"deleted": True}
    if existing:
        d = frappe.get_doc("Account Flag Mapping", existing)
        d.flag = flag.strip()
        d.source = "manual"
        d.auto_suggested = 0
        d.save(ignore_permissions=True)
    else:
        d = frappe.new_doc("Account Flag Mapping")
        d.report = doc.name
        d.account = account
        d.flag = flag.strip()
        d.source = "manual"
        d.insert(ignore_permissions=True)
    _bump_cache_gen(doc.name)
    return {"saved": True, "account": account, "flag": flag.strip()}


@frappe.whitelist(methods=["POST"])
def autosuggest_mappings(report: str) -> dict:
    _require_read()
    doc = _resolve_report_doc(report)
    created = autosuggest_unmapped_for_report(doc.name)
    _bump_cache_gen(doc.name)
    return {"created": created}


@frappe.whitelist(methods=["POST"])
def import_map_sheet(
    report: str,
    file_base64: str,
    sheet_name: str = "MAP",
    account_col: int = 2,
    flag_col: int = 3,
    header_rows: int = 4,
    replace: int = 1,
) -> dict:
    """Read a MAP sheet from an uploaded .xlsx, write Account Flag Mappings.

    The file content is sent base64-encoded in the request body so it can be passed
    through the standard Frappe REST envelope without multipart handling.
    """
    _require_read()
    doc = _resolve_report_doc(report)
    try:
        file_bytes = base64.b64decode(file_base64)
    except Exception:
        frappe.throw("file_base64 must be a base64-encoded .xlsx file.")
    parsed = read_map_sheet(
        file_bytes,
        sheet_name=sheet_name,
        account_col=cint(account_col),
        flag_col=cint(flag_col),
        header_rows=cint(header_rows),
    )
    result = apply_map_to_report(
        report=doc.name,
        parsed=parsed,
        replace=bool(cint(replace)),
        company=doc.company,
    )
    _bump_cache_gen(doc.name)
    result["sheet_used"] = parsed.get("sheet_used")
    result["flags_found"] = parsed.get("flags", {})
    return result


@frappe.whitelist(methods=["POST"])
def import_report_structure_from_excel(
    file_base64: str,
    sheet_name: str = "P&L",
    label_col: int = 2,
    data_col_start: int = 4,
    use_flags_from_sheet: str = "MAP",
    create_report: int = 0,
    report_name: str | None = None,
    report_slug: str | None = None,
) -> dict:
    """Read a report sheet, infer the row tree, return a preview spec.

    If create_report is truthy, also create an Insight Report Definition.
    """
    _require_write("Insight Report Definition")
    try:
        file_bytes = base64.b64decode(file_base64)
    except Exception:
        frappe.throw("file_base64 must be a base64-encoded .xlsx file.")

    flags_set = set()
    if use_flags_from_sheet:
        try:
            map_parsed = read_map_sheet(file_bytes, sheet_name=use_flags_from_sheet)
            flags_set = set(map_parsed.get("flags", {}).keys())
        except Exception:
            flags_set = set()

    parsed = import_report_structure(
        file_bytes,
        sheet_name=sheet_name,
        label_col=cint(label_col),
        data_col_start=cint(data_col_start),
        flags_in_map_sheet=sorted(flags_set),
    )

    if not cint(create_report):
        return parsed

    if not report_name:
        report_name = "Imported P&L"
    slug = (report_slug or frappe.scrub(report_name)).strip()
    if frappe.db.exists("Insight Report Definition", {"slug": slug}):
        frappe.throw(f"A report with slug '{slug}' already exists.")

    doc = frappe.new_doc("Insight Report Definition")
    doc.report_name = report_name
    doc.slug = slug
    doc.description = f"Imported from {parsed.get('sheet_used')}"
    doc.is_active = 1
    doc.seed_source = "excel-template"
    doc.comparison_mode = "vs_budget"
    doc.prior_years = 1
    definition = {
        "rows": [
            {
                **{k: v for k, v in r.items() if k != "raw_excel_formula" and k != "source_row_idx"},
            }
            for r in parsed["rows"]
        ],
        "comparison": {"mode": "vs_budget", "prior_years": 1},
    }
    doc.definition_json = json.dumps(definition, indent=2, sort_keys=True)
    doc.column_schema_json = json.dumps([], indent=2)
    doc.filter_schema_json = json.dumps([], indent=2)
    doc.insert(ignore_permissions=True)
    parsed["created_report"] = doc.name
    return parsed


@frappe.whitelist(methods=["POST"])
def suggest_flag(code: str) -> dict:
    _require_read()
    return {"flag": suggest_flag_for_code(code)}


@frappe.whitelist()
def list_mapping_rules() -> list[dict]:
    _require_read()
    return frappe.get_all(
        "Insight Mapping Rule",
        fields=["name", "prefix", "flag", "priority", "is_active"],
        order_by="priority asc, prefix desc",
        limit_page_length=0,
    )


@frappe.whitelist(methods=["POST"])
def save_mapping_rule(name: str | None, prefix: str, flag: str, priority: int = 100, is_active: int = 1) -> dict:
    _check_edit_permission()
    if name:
        doc = frappe.get_doc("Insight Mapping Rule", name)
    else:
        doc = frappe.new_doc("Insight Mapping Rule")
    doc.prefix = prefix
    doc.flag = flag
    doc.priority = cint(priority)
    doc.is_active = cint(is_active)
    if doc.is_new():
        doc.insert()
    else:
        doc.save()
    return {"name": doc.name}


@frappe.whitelist(methods=["POST"])
def delete_mapping_rule(name: str) -> dict:
    _require_read()
    frappe.delete_doc("Insight Mapping Rule", name)
    return {"deleted": True}


def _resolve_report_doc(report: str):
    name = frappe.db.get_value("Insight Report Definition", {"slug": report}, "name") or report
    return frappe.get_doc("Insight Report Definition", name)


def _serialize_report(doc) -> dict:
    return {
        "name": doc.name,
        "report_name": doc.report_name,
        "slug": doc.slug,
        "description": doc.description,
        "is_active": doc.is_active,
        "is_default": getattr(doc, "is_default", 0),
        # v1.9.48 — exposed so the frontend knows whether to render
        # T-account or vertical. Defaults to vertical for backward compat.
        "presentation_format": getattr(doc, "presentation_format", "vertical") or "vertical",
        "print_letter_head": getattr(doc, "print_letter_head", "") or "",
        "version": doc.version,
        "company": doc.company,
        "report_type": getattr(doc, "report_type", "pnl") or "pnl",
        "comparison_mode": doc.comparison_mode,
        "prior_years": doc.prior_years,
        # v1.9.92 — display defaults (drill-down behaviour + initial expand).
        "default_expand": (getattr(doc, "default_expand", "Collapsed") or "Collapsed"),
        "hide_zero_accounts": cint(getattr(doc, "hide_zero_accounts", 0)),
        "hide_group_accounts": cint(getattr(doc, "hide_group_accounts", 0)),
        "definition": json.loads(doc.definition_json or "{}"),
        "columns": json.loads(doc.column_schema_json or "[]"),
        "filters": json.loads(doc.filter_schema_json or "[]"),
    }


def _execution_cache_key(**parts) -> str:
    gen = _get_cache_gen(parts["report_name"])
    # v2.38.5 — the app version salts every key: deploying a new build
    # invalidates all cached payloads. A stale pre-fix payload survived four
    # deploys in Redis and replayed the quarter-frame bug — never again.
    from neotec_insight import __version__ as _appv
    parts["_appv"] = _appv
    payload = json.dumps(parts, sort_keys=True, separators=(",", ":"))
    sig = hashlib.sha1(payload.encode("utf-8")).hexdigest()
    return f"neotec_insight:exec:{parts['report_name']}:g{gen}:{sig}"


@frappe.whitelist()
def list_budget_books(report: str, fiscal_year: int | None = None) -> list[dict]:
    """List all budget books for a report (optionally one fiscal year)."""
    _require_read()
    doc = _resolve_report_doc(report)
    filters: dict = {"report": doc.name}
    if fiscal_year is not None and str(fiscal_year).strip():
        filters["fiscal_year"] = cint(fiscal_year)
    rows = frappe.get_all(
        "Insight Budget Book",
        filters=filters,
        fields=[
            "name", "slug", "label", "fiscal_year", "dimension_type", "dimension_value",
            "owner_user", "status", "is_primary_axis_book", "label_is_custom",
        ],
        order_by="fiscal_year desc, dimension_type asc, dimension_value asc, label asc",
        limit_page_length=0,
    )
    # Mark whether the current user can edit each book.
    me = frappe.session.user
    is_finance = bool(set(frappe.get_roles(me)) & {"System Manager", "Accounts Manager"})
    for r in rows:
        r["can_edit"] = (
            r["status"] not in {"approved", "locked"} and (r["owner_user"] == me or is_finance)
        ) or (is_finance and r["status"] in {"approved", "locked"})
    return rows


@frappe.whitelist()
def get_budget_book(book: str) -> dict:
    """Return book metadata + every cell on it grouped by (row_key, month)."""
    _require_read()
    book_doc = _resolve_book_doc(book)
    cells = frappe.get_all(
        "Insight Budget Cell",
        filters={"book": book_doc.name},
        fields=["row_key", "month", "amount"],
        limit_page_length=0,
    )
    grid: dict[str, dict[int, float]] = {}
    for c in cells:
        grid.setdefault(c["row_key"], {})[int(c["month"])] = float(c["amount"] or 0.0)
    return {
        "book": _book_summary(book_doc),
        "cells": grid,
    }


@frappe.whitelist(methods=["POST"])
def create_budget_book(payload: str | dict) -> dict:
    """Create a new Budget Book. payload = {report, fiscal_year, dimension_type, dimension_value, label?, label_is_custom?, owner_user?, description?}."""
    _require_write("Insight Budget Book")
    data = frappe.parse_json(payload) if isinstance(payload, str) else (payload or {})
    if not isinstance(data, dict):
        frappe.throw("payload must be an object.")
    doc = _resolve_report_doc(data.get("report"))

    from neotec_insight.neotec_insight.doctype.insight_budget_book.insight_budget_book import (
        auto_label as _auto_label,
        auto_slug as _auto_slug,
    )

    fy = cint(data.get("fiscal_year"))
    dim_type = (data.get("dimension_type") or "total").strip()
    dim_value = (data.get("dimension_value") or "").strip() or None
    # v2.35.0 — books on ANY Accounting Dimension (Branch, Business Division…)
    cd_field = (data.get("custom_dimension_fieldname") or "").strip() or None
    if dim_type == "custom":
        if not cd_field:
            frappe.throw("Pick the accounting dimension for a custom-dimension book.")
        valid = {d["fieldname"] for d in list_accounting_dimensions()}
        if cd_field not in valid:
            frappe.throw(f"Unknown accounting dimension: {cd_field}")
    label_custom = bool(data.get("label_is_custom"))
    label = (data.get("label") or "").strip()
    if not label_custom or not label:
        label = _auto_label(fy, dim_type, dim_value)

    book = frappe.new_doc("Insight Budget Book")
    book.report = doc.name
    book.fiscal_year = fy
    book.dimension_type = dim_type
    book.dimension_value = dim_value
    if dim_type == "custom":
        book.custom_dimension_fieldname = cd_field
    book.label = label
    book.slug = _auto_slug(doc.name, fy, dim_type, dim_value)
    book.label_is_custom = 1 if label_custom else 0
    book.owner_user = (data.get("owner_user") or "").strip() or None
    book.description = (data.get("description") or "").strip() or None
    book.status = "draft"
    book.insert(ignore_permissions=True)
    frappe.db.commit()
    return _book_summary(book)


@frappe.whitelist(methods=["POST"])
def update_budget_book(book: str, payload: str | dict) -> dict:
    """Update a book's label, owner, description, or status.

    Status transitions follow draft → submitted → approved (or back to draft).
    Locked books can only be unlocked by System Manager.
    """
    _require_write("Insight Budget Book")
    data = frappe.parse_json(payload) if isinstance(payload, str) else (payload or {})
    if not isinstance(data, dict):
        frappe.throw("payload must be an object.")
    book_doc = _resolve_book_doc(book)

    me = frappe.session.user
    is_finance = bool(set(frappe.get_roles(me)) & {"System Manager", "Accounts Manager"})

    if "status" in data:
        new_status = (data["status"] or "").strip()
        valid = {"draft", "submitted", "approved", "locked"}
        if new_status not in valid:
            frappe.throw(f"Invalid status '{new_status}'.")
        # Owners can submit their own draft; only finance can approve/lock/unlock.
        if new_status in {"approved", "locked"} and not is_finance:
            frappe.throw("Only Accounts Managers can approve or lock a book.")
        if book_doc.status == "locked" and not is_finance:
            frappe.throw("Locked books can only be changed by Accounts Managers.")
        book_doc.status = new_status

    if "label" in data:
        new_label = (data["label"] or "").strip()
        if new_label:
            book_doc.label = new_label
            book_doc.label_is_custom = 1
    if data.get("revert_label_to_auto"):
        book_doc.label_is_custom = 0  # before_validate will regenerate

    if "owner_user" in data:
        book_doc.owner_user = (data["owner_user"] or "").strip() or None
    if "description" in data:
        book_doc.description = (data["description"] or "").strip() or None

    book_doc.save(ignore_permissions=True)
    _bump_cache_gen(book_doc.report)
    return _book_summary(book_doc)


@frappe.whitelist(methods=["POST"])
def delete_budget_book(book: str) -> dict:
    """Delete a book and its cells. Locked or approved books need finance role."""
    _require_write("Insight Budget Book")
    book_doc = _resolve_book_doc(book)
    me = frappe.session.user
    is_finance = bool(set(frappe.get_roles(me)) & {"System Manager", "Accounts Manager"})
    if book_doc.status in {"approved", "locked"} and not is_finance:
        frappe.throw(f"Book '{book_doc.label}' is {book_doc.status}; only Accounts Managers can delete it.")
    report = book_doc.report
    # Cells delete via cascading: do it explicitly so the link survives integrity checks.
    frappe.db.sql(
        "DELETE FROM `tabInsight Budget Cell` WHERE book = %s", (book_doc.name,)
    )
    frappe.delete_doc("Insight Budget Book", book_doc.name, ignore_permissions=True, force=True)
    _bump_cache_gen(report)
    return {"deleted": book_doc.name}


@frappe.whitelist(methods=["POST"])
def rollup_to_total(report: str, fiscal_year: int) -> dict:
    """Sum all primary-axis books for (report, fiscal_year) into the Total book.

    Idempotent: clears the Total book's existing cells first, then writes the
    sum. The Total book is created if it doesn't exist.
    """
    _require_write("Insight Budget Book")
    doc = _resolve_report_doc(report)
    fy = cint(fiscal_year)
    primary_axis = (getattr(doc, "primary_budget_axis", None) or "none").strip()
    if primary_axis == "none":
        frappe.throw(
            "This report has no primary budget axis. Set one in the report definition "
            "(e.g. 'cost_center') before rolling up."
        )

    contributing = frappe.get_all(
        "Insight Budget Book",
        filters={"report": doc.name, "fiscal_year": fy, "dimension_type": primary_axis},
        pluck="name",
        limit_page_length=0,
    )
    if not contributing:
        frappe.throw(
            f"No {primary_axis} books exist for {doc.report_name} FY{fy}. "
            f"Create at least one before rolling up."
        )

    # Sum per (row_key, month).
    sums: dict[tuple, float] = {}
    cells = frappe.get_all(
        "Insight Budget Cell",
        filters={"book": ["in", contributing]},
        fields=["row_key", "month", "amount"],
        limit_page_length=0,
    )
    for c in cells:
        key = (c["row_key"], int(c["month"]))
        sums[key] = sums.get(key, 0.0) + float(c["amount"] or 0.0)

    # Find or create the Total book.
    total_name = frappe.db.get_value(
        "Insight Budget Book",
        {"report": doc.name, "fiscal_year": fy, "dimension_type": "total"},
        "name",
    )
    if total_name:
        total = frappe.get_doc("Insight Budget Book", total_name)
    else:
        from neotec_insight.neotec_insight.doctype.insight_budget_book.insight_budget_book import (
            auto_label as _auto_label,
            auto_slug as _auto_slug,
        )
        total = frappe.new_doc("Insight Budget Book")
        total.report = doc.name
        total.fiscal_year = fy
        total.dimension_type = "total"
        total.dimension_value = None
        total.label = _auto_label(fy, "total", None)
        total.slug = _auto_slug(doc.name, fy, "total", None)
        total.status = "approved"
        total.is_primary_axis_book = 1
        total.insert(ignore_permissions=True)

    # Reset and rewrite.
    frappe.db.sql(
        "DELETE FROM `tabInsight Budget Cell` WHERE book = %s", (total.name,)
    )
    inserted = 0
    for (row_key, month), amount in sums.items():
        if amount == 0.0:
            continue
        d = frappe.new_doc("Insight Budget Cell")
        d.book = total.name
        d.report = doc.name
        d.fiscal_year = fy
        d.month = month
        d.row_key = row_key
        d.segment = "total"
        d.amount = amount
        d.insert(ignore_permissions=True)
        inserted += 1

    total.is_primary_axis_book = 1
    if total.status not in {"approved", "locked"}:
        total.status = "approved"
    total.save(ignore_permissions=True)
    _bump_cache_gen(doc.name)
    return {
        "book": _book_summary(total),
        "contributing_books": len(contributing),
        "cells_written": inserted,
        "primary_axis": primary_axis,
    }


# ─── Saved Dashboards (v1.7) ─────────────────────────────────────────────


@frappe.whitelist()
def list_dashboards(report: str | None = None) -> list[dict]:
    """Return dashboards visible to the current user.

    A dashboard is visible if:
      - It's shared (is_shared=1), OR
      - The current user is the owner_user, OR
      - The current user has a finance role (System Manager / Accounts Manager).

    Optionally filter by `report` (the report slug or DocType name).
    """
    _require_read()
    me = frappe.session.user
    is_finance = bool(set(frappe.get_roles(me)) & {"System Manager", "Accounts Manager"})

    filters: dict = {}
    if report:
        # Accept either slug or name.
        if frappe.db.exists("Insight Report Definition", report):
            filters["report"] = report
        else:
            by_slug = frappe.db.get_value("Insight Report Definition", {"slug": report}, "name")
            if by_slug:
                filters["report"] = by_slug

    rows = frappe.get_all(
        "Insight Dashboard",
        filters=filters,
        fields=["name", "label", "slug", "report", "owner_user", "is_shared", "description", "modified"],
        order_by="modified desc",
        limit_page_length=0,
    )
    out = []
    for r in rows:
        if r["owner_user"] == me or r["is_shared"] or is_finance:
            r["can_edit"] = (r["owner_user"] == me) or is_finance or (r["is_shared"] and is_finance)
            r["is_mine"] = (r["owner_user"] == me)
            out.append(r)
    return out


@frappe.whitelist()
def get_dashboard(dashboard: str) -> dict:
    """Return a dashboard's full content (tiles + filters)."""
    _require_read()
    doc = _resolve_dashboard_doc(dashboard)
    if not doc.can_user_view():
        frappe.throw(f"You don't have permission to view dashboard '{doc.label}'.")
    return {
        "name": doc.name,
        "label": doc.label,
        "slug": doc.slug,
        "report": doc.report,
        "owner_user": doc.owner_user,
        "is_shared": int(doc.is_shared or 0),
        "description": doc.description or "",
        "tiles": frappe.parse_json(doc.tiles_json or "[]"),
        "filters": frappe.parse_json(doc.filters_json or "{}"),
    }


@frappe.whitelist(methods=["POST"])
def save_dashboard(payload: str | dict) -> dict:
    """Create or update a dashboard.

    payload = {
      name?           # if present, update; else create
      label
      report
      tiles           # array of tile configs
      filters         # snapshot of run filters
      is_shared?
      description?
    }
    """
    _require_write("Insight Dashboard")
    data = frappe.parse_json(payload) if isinstance(payload, str) else (payload or {})
    if not isinstance(data, dict):
        frappe.throw("payload must be an object.")

    label = (data.get("label") or "").strip()
    if not label:
        frappe.throw("Dashboard label is required.")
    report = (data.get("report") or "").strip()
    if not report:
        frappe.throw("Dashboard report is required.")
    # Normalize report ref.
    if not frappe.db.exists("Insight Report Definition", report):
        by_slug = frappe.db.get_value("Insight Report Definition", {"slug": report}, "name")
        if by_slug:
            report = by_slug
        else:
            frappe.throw(f"Report '{report}' not found.")

    tiles = data.get("tiles") or []
    filters = data.get("filters") or {}
    is_shared = 1 if data.get("is_shared") else 0
    description = (data.get("description") or "").strip() or None

    existing_name = (data.get("name") or "").strip()
    if existing_name:
        doc = _resolve_dashboard_doc(existing_name)
        if not doc.can_user_edit():
            frappe.throw(f"You don't have permission to edit dashboard '{doc.label}'.")
        doc.label = label
        doc.report = report
        doc.is_shared = is_shared
        doc.description = description
        doc.tiles_json = frappe.as_json(tiles)
        doc.filters_json = frappe.as_json(filters)
        doc.save(ignore_permissions=True)
    else:
        # Insert. Slug is auto-derived from the label; on collision Frappe will
        # raise DuplicateEntryError — handle by suffixing.
        from neotec_insight.neotec_insight.doctype.insight_dashboard.insight_dashboard import slugify
        slug_base = slugify(label)
        slug = slug_base
        n = 2
        while frappe.db.exists("Insight Dashboard", slug):
            slug = f"{slug_base}-{n}"
            n += 1
            if n > 50:
                frappe.throw("Could not generate a unique slug — try a different label.")
        doc = frappe.new_doc("Insight Dashboard")
        doc.label = label
        doc.slug = slug
        doc.report = report
        doc.is_shared = is_shared
        doc.description = description
        doc.owner_user = frappe.session.user
        doc.tiles_json = frappe.as_json(tiles)
        doc.filters_json = frappe.as_json(filters)
        doc.insert(ignore_permissions=True)

    return {
        "name": doc.name,
        "label": doc.label,
        "slug": doc.slug,
        "owner_user": doc.owner_user,
        "is_shared": int(doc.is_shared or 0),
    }


@frappe.whitelist(methods=["POST"])
def delete_dashboard(dashboard: str) -> dict:
    """Delete a dashboard. Owner can always delete; other users need a finance role."""
    _require_write("Insight Dashboard")
    doc = _resolve_dashboard_doc(dashboard)
    me = frappe.session.user
    is_finance = bool(set(frappe.get_roles(me)) & {"System Manager", "Accounts Manager"})
    if doc.owner_user != me and not is_finance:
        frappe.throw(f"Only the owner or an Accounts Manager can delete '{doc.label}'.")
    name = doc.name
    label = doc.label
    frappe.delete_doc("Insight Dashboard", name, ignore_permissions=True, force=True)
    return {"deleted": name, "label": label}


def _resolve_dashboard_doc(dashboard: str):
    """Accept either DocType name or slug."""
    if not dashboard or not dashboard.strip():
        frappe.throw("dashboard is required.")
    dashboard = dashboard.strip()
    if frappe.db.exists("Insight Dashboard", dashboard):
        return frappe.get_doc("Insight Dashboard", dashboard)
    name = frappe.db.get_value("Insight Dashboard", {"slug": dashboard}, "name")
    if not name:
        frappe.throw(f"Dashboard '{dashboard}' not found.")
    return frappe.get_doc("Insight Dashboard", name)


def _cache_gen_key(name: str) -> str:
    return f"neotec_insight:gen:{name}"


def _get_cache_gen(name: str) -> int:
    return cint(frappe.cache().get_value(_cache_gen_key(name)) or 1) or 1


def _bump_cache_gen(name: str) -> int:
    nxt = _get_cache_gen(name) + 1
    frappe.cache().set_value(_cache_gen_key(name), nxt)
    return nxt


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
    expense_accounts = by_root_for_projection = [
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
# Variance commentary (v1.9.36) — per (report, row_key, fiscal_year)
# ───────────────────────────────────────────────────────────────────────────

@frappe.whitelist()
def list_variance_notes(report: str, fiscal_year: str | int) -> list[dict]:
    """Return all commentary notes for a report+fiscal year.

    Used by the dashboard variance panel and the Management Pack export to
    decorate rows with the user's 'why' explanation.
    """
    _require_read()
    fy = cint(fiscal_year)
    if not report or not fy:
        return []
    # Resolve the report doc name (caller may pass slug).
    doc = _resolve_report_doc(report)
    rows = frappe.get_all(
        "Insight Variance Note",
        filters={"report": doc.name, "fiscal_year": fy},
        fields=["name", "row_key", "commentary", "modified", "modified_by"],
        limit_page_length=0,
    )
    out = []
    for r in rows:
        commentary = (r.get("commentary") or "").strip()
        if not commentary:
            continue
        out.append({
            "name": r["name"],
            "row_key": r["row_key"],
            "commentary": commentary,
            "modified": str(r.get("modified") or ""),
            "modified_by": r.get("modified_by") or "",
        })
    return out


@frappe.whitelist(methods=["POST"])
def save_variance_note(
    report: str,
    row_key: str,
    fiscal_year: str | int,
    commentary: str = "",
) -> dict:
    """Upsert (or delete) a variance note for one row of a report+year.

    Empty/whitespace commentary deletes the note — keeps storage clean and
    matches the UX: clearing the textarea removes the row's commentary entirely.
    """
    _require_read()
    fy = cint(fiscal_year)
    if not report or not row_key or not fy:
        frappe.throw("report, row_key and fiscal_year are required.")
    doc = _resolve_report_doc(report)
    row_key = (row_key or "").strip()
    text = (commentary or "").strip()

    existing = frappe.get_all(
        "Insight Variance Note",
        filters={"report": doc.name, "row_key": row_key, "fiscal_year": fy},
        pluck="name",
        limit_page_length=1,
    )

    if not text:
        # Empty -> delete if present.
        if existing:
            frappe.delete_doc("Insight Variance Note", existing[0], ignore_permissions=False)
            frappe.db.commit()
        return {"deleted": True, "row_key": row_key}

    if existing:
        note = frappe.get_doc("Insight Variance Note", existing[0])
        note.commentary = text
        note.save(ignore_permissions=False)
    else:
        note = frappe.new_doc("Insight Variance Note")
        note.report = doc.name
        note.row_key = row_key
        note.fiscal_year = fy
        note.commentary = text
        note.insert(ignore_permissions=False)
    frappe.db.commit()
    return {
        "name": note.name,
        "row_key": row_key,
        "commentary": text,
        "modified": str(note.modified or ""),
        "modified_by": note.modified_by or "",
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
    for _ in range(12):
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
# Multi-company group view (v1.9.38) — Layer 1: aggregate + currency translate
# ───────────────────────────────────────────────────────────────────────────
# Honest scope: aggregates across selected companies; translates each
# company's amounts to a chosen presentation currency using ERPNext's
# Currency Exchange rates (period-average for P&L). Does NOT eliminate
# intercompany transactions — that's Layer 2 (a later release). Banner in
# the UI states this clearly so nobody mistakes it for statutory consolidation.

_GROUP_VIEW_ROLES = {"System Manager", "Accounts Manager", "Insight Group Viewer"}

# v1.9.45 — Insight role tier. 'Edit' permits writes to report definitions
# and related admin actions; 'Full' is the union of view + edit. System
# Manager and Accounts Manager are implicit members of both sets.
_INSIGHT_EDIT_ROLES = {"System Manager", "Accounts Manager", "Insight CFO"}
_INSIGHT_VIEW_FULL_ROLES = {"System Manager", "Accounts Manager", "Insight CFO", "Insight CEO"}


def _user_roles() -> set:
    return set(frappe.get_roles(frappe.session.user) or [])


def _can_edit_insight() -> bool:
    """True if the current user can edit report definitions and admin data.
    Administrator always allowed; otherwise must have an edit role.
    """
    if frappe.session.user == "Administrator":
        return True
    return bool(_user_roles() & _INSIGHT_EDIT_ROLES)


def _check_edit_permission() -> None:
    """Raise PermissionError unless the user is allowed to edit Insight data."""
    if not _can_edit_insight():
        frappe.throw(
            "You don't have permission to edit Insight reports. "
            "Ask an administrator to assign the 'Insight CFO' role.",
            frappe.PermissionError,
        )


@frappe.whitelist()
def insight_get_access_profile() -> dict:
    """Return the current user's access profile.

    Frontend reads this once on load to know which tabs and affordances
    to render. Does not throw — returns a profile even for anonymous users.

    Returns:
      role_tier:    'admin' | 'cfo' | 'ceo' | 'group_viewer' | 'hr' | 'basic'
      can_edit:     bool — can save/edit report definitions
      can_see_group: bool — Group tab visibility
    """
    if frappe.session.user == "Administrator":
        return {
            "role_tier": "admin",
            "can_edit": True,
            "can_see_group": True,
            "user": "Administrator",
        }
    roles = _user_roles()
    # Determine the tier — most-privileged wins.
    if roles & {"System Manager", "Accounts Manager"}:
        tier = "admin"
    elif "Insight CFO" in roles:
        tier = "cfo"
    elif "Insight CEO" in roles:
        tier = "ceo"
    elif "Insight Group Viewer" in roles:
        tier = "group_viewer"
    elif roles & _HR_ROLES:
        # v2.84.0 — an HR user sees People and nothing else. Placed LAST among
        # the named tiers on purpose: it is the most restrictive, so anyone who
        # also holds a finance role keeps that wider access rather than being
        # narrowed to People by holding both.
        tier = "hr"
    else:
        tier = "basic"
    return {
        "role_tier": tier,
        "can_edit": bool(roles & _INSIGHT_EDIT_ROLES),
        "can_see_group": bool(roles & _GROUP_VIEW_ROLES),
        # True only for a user whose ONLY Insight access is People. The UI hides
        # every other workspace for these users; `_check_hr_only` enforces the
        # same restriction server-side, because hiding a tab does not stop
        # anyone calling the endpoint behind it.
        "hr_only": tier == "hr",
        "user": frappe.session.user,
    }


_HR_ROLES = {"Insight HR", "HR Manager", "HR User"}


def _check_hr_only(*, allow: bool) -> None:
    """Refuse a non-People request from a People-only user.

    An HR user is given Insight to read headcount, payroll accruals and
    end-of-service — not the P&L those figures roll into. Hiding the other tabs
    in the UI is presentation; this is the part that actually holds, since a
    hidden tab's endpoint is still one HTTP call away.
    """
    if allow or frappe.session.user == "Administrator":
        return
    roles = _user_roles()
    if roles & (_INSIGHT_EDIT_ROLES | _GROUP_VIEW_ROLES | {"Insight CFO", "Insight CEO"}):
        return
    if roles & _HR_ROLES:
        frappe.throw(
            _("Your access is limited to the People workspace."),
            frappe.PermissionError,
        )


def _check_group_view_access() -> None:
    """Raise PermissionError unless the current user has a group-view role.

    Defence in depth: the UI hides the Group tab from users without these
    roles, AND every backend endpoint enforces the same check so the data
    can't be reached by direct API call. Administrator bypasses (consistent
    with Frappe convention).
    """
    if frappe.session.user == "Administrator":
        return
    user_roles = set(frappe.get_roles(frappe.session.user) or [])
    if not (user_roles & _GROUP_VIEW_ROLES):
        frappe.throw(
            "You don't have access to the group-view feature. "
            "Ask an administrator to assign you the 'Insight Group Viewer' role.",
            frappe.PermissionError,
        )


@frappe.whitelist()
def insight_has_group_access() -> dict:
    """Lightweight check for the frontend so it knows whether to show the Group tab.

    Returns {has_access: bool} — never throws. The Group tab in the UI
    queries this once on load and only renders itself when true.
    """
    if frappe.session.user == "Administrator":
        return {"has_access": True}
    user_roles = set(frappe.get_roles(frappe.session.user) or [])
    return {"has_access": bool(user_roles & _GROUP_VIEW_ROLES)}


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


# ───────────────────────────────────────────────────────────────────────────
# Statement of Shareholder's Equity (v1.9.49)
# ───────────────────────────────────────────────────────────────────────────
# Rolls Insight Equity Movement entries into a Beginning + Movements = Ending
# table per component. No automatic GL derivation — entries are explicit.
# This is the right design for equity movements because categorising a
# posting as 'transfer to statutory reserve' versus 'capital increase' is
# a judgement that ERPNext can't make from chart-of-accounts naming alone.

# v1.9.51 — Equity components and movement types are now configurable
# DocTypes (Insight Equity Component, Insight Equity Movement Type). The
# helpers below read them at query time. There are NO hardcoded names or
# orders in this module anymore.


def _list_equity_components_ordered() -> list[str]:
    """Return component names in admin-configured display order."""
    rows = frappe.get_all(
        "Insight Equity Component",
        fields=["name"],
        order_by="display_order asc, name asc",
    )
    return [r["name"] for r in rows]


def _opening_movement_type_names() -> set[str]:
    """Return the set of movement-type names flagged as the opening balance.

    Plural because in a transitional state the admin might have zero or
    multiple — the validator warns but doesn't block. We accept any
    flagged type as an opening marker so the report doesn't crash.
    """
    rows = frappe.get_all(
        "Insight Equity Movement Type",
        filters={"is_opening_balance": 1},
        pluck="name",
    )
    return set(rows)


@frappe.whitelist()
def list_equity_components() -> list[dict]:
    """Public endpoint — used by the frontend to populate the component
    dropdown. Returns components in admin-configured display order.
    """
    _require_read()
    return frappe.get_all(
        "Insight Equity Component",
        fields=["name as value", "name as label", "display_order", "is_seeded", "description"],
        order_by="display_order asc, name asc",
    )


@frappe.whitelist()
def list_equity_movement_types() -> list[dict]:
    """Public endpoint — used by the frontend to populate the movement-type
    dropdown. Includes the is_opening_balance flag so the UI can hint or
    auto-fill behaviour on a per-type basis.
    """
    _require_read()
    return frappe.get_all(
        "Insight Equity Movement Type",
        fields=["name as value", "name as label", "display_order", "is_opening_balance", "default_sign", "is_seeded", "description"],
        order_by="display_order asc, name asc",
    )


@frappe.whitelist()
def get_equity_movement(
    company: str,
    fiscal_year: str | int,
    period: str = "FY",
    report: str | None = None,
) -> dict:
    """Statement of Shareholder's Equity for one company / fiscal_year / period.

    Returns:
      components: ordered list of equity-component blocks, each containing:
        component, beginning, movements [{type, amount, narration}, ...],
        movements_total, ending_derived (beginning + movements_total)
      total_beginning: sum of all components' beginning balances
      total_ending: sum of all components' ending balances
      missing_components: configured components that have no entries
      opening_type_status: 'ok' | 'none' | 'multiple' — diagnostic on the
        is_opening_balance flag state (lets the UI show a clear warning if
        the admin's configuration is incomplete).
    """
    _require_read()
    fy = cint(fiscal_year)
    if not company:
        frappe.throw("company is required.")
    if not fy:
        frappe.throw("fiscal_year is required.")
    if period not in ("FY", "Q1", "Q2", "Q3", "Q4", "H1", "H2"):
        period = "FY"

    # Read configuration (not constants).
    component_order = _list_equity_components_ordered()
    opening_types = _opening_movement_type_names()
    opening_type_status = (
        "ok" if len(opening_types) == 1
        else "multiple" if len(opening_types) > 1
        else "none"
    )

    filters = {"company": company, "fiscal_year": fy, "period": period}
    if report:
        # If a specific report is named, include movements tagged to that
        # report PLUS untagged ones (the global pool). This means a finance
        # team can have report-specific overrides without losing the
        # company-wide baseline movements.
        rows = frappe.get_all(
            "Insight Equity Movement",
            filters={**filters, "report": ["in", ["", report]]},
            fields=["name", "component", "movement_type", "amount", "narration"],
            order_by="creation asc",
        )
    else:
        rows = frappe.get_all(
            "Insight Equity Movement",
            filters=filters,
            fields=["name", "component", "movement_type", "amount", "narration"],
            order_by="creation asc",
        )

    # Bucket by component preserving insertion order within each.
    by_component: dict[str, dict] = {}
    for r in rows:
        c = r["component"]
        if c not in by_component:
            by_component[c] = {"component": c, "beginning": 0.0, "movements": [], "movements_total": 0.0}
        amt = float(r.get("amount") or 0)
        # Identify opening by flag, not by name.
        if r["movement_type"] in opening_types:
            # Last-write-wins for opening (validation prevents duplicates,
            # but a corrupt dataset shouldn't crash the report).
            by_component[c]["beginning"] = amt
        else:
            by_component[c]["movements"].append({
                "type": r["movement_type"],
                "amount": amt,
                "narration": r.get("narration") or "",
            })
            by_component[c]["movements_total"] += amt

    # Compute ending and list in admin-configured order. Components with no
    # data are omitted from the output (the frontend can show a hint via
    # missing_components if useful).
    components_out: list[dict] = []
    component_order_set = set(component_order)
    for c in component_order:
        if c in by_component:
            blk = by_component[c]
            blk["ending_derived"] = blk["beginning"] + blk["movements_total"]
            components_out.append(blk)
    # Append any non-configured components (e.g. an old reference to a
    # component that was renamed or deleted) AFTER the configured ordering,
    # so we never silently drop data. Frontend can flag these visually.
    for c, blk in by_component.items():
        if c not in component_order_set:
            blk["ending_derived"] = blk["beginning"] + blk["movements_total"]
            blk["unconfigured"] = True
            components_out.append(blk)

    total_beginning = sum(b["beginning"] for b in components_out)
    total_movements = sum(b["movements_total"] for b in components_out)
    total_ending = total_beginning + total_movements

    missing = [c for c in component_order if c not in by_component]

    return {
        "company": company,
        "fiscal_year": fy,
        "period": period,
        "components": components_out,
        "total_beginning": total_beginning,
        "total_movements": total_movements,
        "total_ending": total_ending,
        "missing_components": missing,
        "opening_type_status": opening_type_status,
    }


@frappe.whitelist(methods=["POST"])
def save_equity_movement(payload: str | dict) -> dict:
    """Create or update an Insight Equity Movement entry."""
    _check_edit_permission()
    data = frappe.parse_json(payload)
    if not isinstance(data, dict):
        frappe.throw("Payload must be an object.")
    name = data.get("name")
    if name:
        doc = frappe.get_doc("Insight Equity Movement", name)
    else:
        doc = frappe.new_doc("Insight Equity Movement")
    for fld in ("company", "fiscal_year", "period", "component", "movement_type", "narration", "report"):
        if fld in data:
            doc.set(fld, data.get(fld))
    if "amount" in data:
        try:
            doc.amount = float(data.get("amount") or 0)
        except Exception:
            frappe.throw("amount must be a number.")
    if doc.is_new():
        doc.insert(ignore_permissions=False)
    else:
        doc.save(ignore_permissions=False)
    return {"name": doc.name}


@frappe.whitelist(methods=["POST"])
def delete_equity_movement(name: str) -> dict:
    _check_edit_permission()
    if not name:
        frappe.throw("name is required.")
    if not frappe.db.exists("Insight Equity Movement", name):
        return {"deleted": False, "reason": "not_found"}
    frappe.delete_doc("Insight Equity Movement", name, ignore_permissions=False)
    return {"deleted": True}


# ───────────────────────────────────────────────────────────────────────────
# Accounting Dimensions integration (v1.9.52)
# ───────────────────────────────────────────────────────────────────────────
# ERPNext supports user-defined GL dimensions via the 'Accounting Dimension'
# DocType. The admin declares any DocType (Vehicle, Region, Salesperson, etc.)
# as a GL dimension; ERPNext adds a column to GL Entry on save.
#
# These helpers expose those dimensions in Insight:
#   1. Discovery — list the active accounting dimensions, deduplicating
#      against the four we already plumb natively.
#   2. Validation — every fieldname that flows into a SQL query is verified
#      against the discovered set first. NEVER trust caller-supplied keys.
#   3. Value lookup — a generic lister that reads the underlying DocType
#      defensively (we don't know its label column in advance).

# Native dimensions we already plumb. The discovery endpoint excludes these
# to avoid double-rendering them as both built-in and custom filters.
_NATIVE_DIMENSION_FIELDS = {"cost_center", "project", "department", "branch"}
_NATIVE_DIMENSION_DOCTYPES = {"Cost Center", "Project", "Department", "Branch"}


def _discover_accounting_dimensions_raw() -> list[dict]:
    """Return the raw active accounting-dimension rows from this bench.

    Defensive: the DocType may not exist on very old ERPNext benches; if so
    we return an empty list rather than throwing.
    """
    if not frappe.db.exists("DocType", "Accounting Dimension"):
        return []
    try:
        rows = frappe.get_all(
            "Accounting Dimension",
            filters={"disabled": 0},
            fields=["name", "document_type", "fieldname", "label"],
            order_by="document_type asc",
        )
    except Exception as e:
        frappe.log_error(f"Accounting Dimension discovery failed: {e}", "Neotec Insight: dimensions")
        return []
    return rows


def _accounting_dimension_fieldnames() -> set[str]:
    """The set of valid fieldnames an admin has configured (active only),
    excluding the native ones. Used to validate caller-supplied filter keys
    against actual GL Entry columns.
    """
    rows = _discover_accounting_dimensions_raw()
    out = set()
    for r in rows:
        fn = (r.get("fieldname") or "").strip()
        dt = (r.get("document_type") or "").strip()
        if not fn:
            continue
        # Dedup against natives — both directions.
        if fn in _NATIVE_DIMENSION_FIELDS:
            continue
        if dt in _NATIVE_DIMENSION_DOCTYPES:
            continue
        out.add(fn)
    return out


def _all_valid_dimension_fieldnames() -> set[str]:
    """The full set of fieldnames safe to inject into GL queries — natives
    plus discovered custom dimensions. Use this to validate every filter
    key before it enters SQL.
    """
    return _NATIVE_DIMENSION_FIELDS | _accounting_dimension_fieldnames()


def _normalise_dim_param(val) -> list[str] | None:
    """Normalise a dimension filter parameter to a list of strings (or None).

    Accepted inputs and their meanings:
      None         → no filter on this dimension
      ""           → no filter (empty single-select)
      "CC-001"     → single-value filter (backward compat)
      ["CC-A","CC-B"] → multi-value filter (v1.9.58)
      '["CC-A","CC-B"]' → JSON-encoded list (multi-value, transport)
      "CC-A,CC-B"  → comma-separated string (multi-value, transport)

    Returns None when the caller meant "no filter" (empty/None/empty list).
    Returns a list of one or more non-empty strings otherwise.

    Defensive: any unparseable input is treated as "no filter" — we'd
    rather under-filter than throw, since this runs on every report load
    and a transient bad value shouldn't break the page.

    Why accept all these shapes: the multi-select UI sends arrays, prior
    code paths send scalar strings, internal call sites that forward
    filters (priors loop, group dashboard, multi-year trend) might pass
    whatever they received. Normalising at the boundary means every
    consumer below sees the same shape.
    """
    if val is None:
        return None
    # JSON-encoded list (common when the frontend's framework serialises args).
    if isinstance(val, str):
        s = val.strip()
        if not s:
            return None
        if s.startswith("[") and s.endswith("]"):
            try:
                parsed = json.loads(s)
                if isinstance(parsed, list):
                    val = parsed
                else:
                    val = [s]  # bizarre case — JSON parsed but not a list
            except Exception:
                val = [s]
        elif "," in s:
            # Comma-separated form — split and trim.
            val = [t.strip() for t in s.split(",")]
        else:
            val = [s]
    if not isinstance(val, list):
        # Anything weird (int, dict) — fail soft as "no filter."
        return None
    out = [str(v).strip() for v in val if v is not None and str(v).strip()]
    return out if out else None


def _dim_sql_clause(column: str, values: list[str] | None) -> tuple[str, list]:
    """Build a parameterised SQL fragment for an IN clause.

    Returns (sql_fragment, params). When values is None or empty, returns
    ("", []) — caller should append nothing.

    Column name is interpolated directly via backticks. Caller MUST have
    validated `column` against the dimension whitelist; otherwise this is
    a SQL injection vector. We trust the caller because in practice this
    is only called with hardcoded native column names or values from
    _sanitise_dimension_filters which already validates.
    """
    if not values:
        return ("", [])
    placeholders = ", ".join(["%s"] * len(values))
    return (f" AND `{column}` IN ({placeholders})", list(values))


@frappe.whitelist()
def list_accounting_dimensions() -> list[dict]:
    """Public endpoint — returns the active custom accounting dimensions
    available on this bench, EXCLUDING the four natively-supported ones
    (cost_center, project, department, branch).

    Each row: {fieldname, label, document_type}.
    Frontend uses fieldname as the filter key and document_type as the
    DocType to read values from.
    """
    _require_read()
    rows = _discover_accounting_dimensions_raw()
    out: list[dict] = []
    for r in rows:
        fn = (r.get("fieldname") or "").strip()
        dt = (r.get("document_type") or "").strip()
        if not fn or not dt:
            continue
        if fn in _NATIVE_DIMENSION_FIELDS:
            continue
        if dt in _NATIVE_DIMENSION_DOCTYPES:
            continue
        out.append({
            "fieldname": fn,
            "label": (r.get("label") or dt or fn).strip(),
            "document_type": dt,
        })
    return out


@frappe.whitelist()
def list_dimension_values(fieldname: str, search: str = "", limit: int = 100) -> list[dict]:
    """Lazily fetch values for a custom accounting dimension.

    Security: the fieldname must match an active discovered dimension. We
    never reflect the caller's fieldname into SQL — we use it to look up
    the dimension's target DocType, then query that DocType through
    Frappe's ORM (which sanitises everything).

    Returns: list of {name, label}. Falls back to {name, name} if no
    obvious label column exists.
    """
    _require_read()
    fieldname = (fieldname or "").strip()
    if not fieldname:
        return []

    # Look up the dimension by fieldname — this is the security check.
    dims = _discover_accounting_dimensions_raw()
    target_doctype = None
    for d in dims:
        if (d.get("fieldname") or "").strip() == fieldname:
            target_doctype = (d.get("document_type") or "").strip()
            break
    if not target_doctype:
        return []  # Caller asked for a dimension that isn't configured. Quietly empty.

    if not frappe.db.exists("DocType", target_doctype):
        return []
    try:
        # Probe columns to find a sensible label field. Try common patterns.
        table = f"tab{target_doctype}"
        cols = _safe_columns(table)
        if not cols:
            return []

        # Candidate label columns, in priority order.
        label_field = None
        candidates = [
            target_doctype.lower().replace(" ", "_") + "_name",  # vehicle_name
            target_doctype.lower().replace(" ", "_"),            # vehicle
            "title", "label", "description", "full_name",
        ]
        for c in candidates:
            if c in cols and c != "name":  # don't pick 'name' as alt; we always include it
                label_field = c
                break

        or_filters: list = []
        if search and search.strip():
            s = search.strip()
            if label_field:
                or_filters.append([label_field, "like", f"%{s}%"])
            or_filters.append(["name", "like", f"%{s}%"])

        fields = ["name"]
        if label_field:
            fields.append(label_field)

        rows = frappe.get_all(
            target_doctype,
            or_filters=or_filters,
            fields=fields,
            order_by=(f"{label_field} asc" if label_field else "name asc"),
            limit_page_length=cint(limit) or 100,
        )
        return [
            {
                "name": r["name"],
                "label": (r.get(label_field) if label_field else None) or r["name"],
            }
            for r in rows
        ]
    except Exception as e:
        frappe.log_error(
            f"list_dimension_values({fieldname} → {target_doctype}) failed: {e}",
            "Neotec Insight: dimensions",
        )
        return []


def _sanitise_dimension_filters(raw: dict | str | None) -> dict:
    """Caller-supplied dimension_filters dict → safe dict.

    Validates every key against the union of native + discovered custom
    dimensions. Anything not on the whitelist is silently dropped — we
    never let a caller-controlled string become a SQL column name.

    Accepts either a dict or a JSON string (some callers pass strings).
    """
    if raw is None:
        return {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            return {}
    if not isinstance(raw, dict):
        return {}

    valid = _all_valid_dimension_fieldnames()
    out: dict = {}
    for k, v in raw.items():
        if not isinstance(k, str):
            continue
        if k not in valid:
            # Silently drop. We log at most one warning to avoid spam.
            continue
        # v1.9.58 — value may be scalar or list (multi-select).
        # Normalise to either a non-empty string or a non-empty list.
        if v is None or v == "" or v == []:
            continue
        if isinstance(v, list):
            cleaned = [str(x).strip() for x in v if x is not None and str(x).strip()]
            if not cleaned:
                continue
            out[k] = cleaned
        else:
            out[k] = v
    return out


# ───────────────────────────────────────────────────────────────────────────
# Letter Head integration for print/export (v1.9.53)
# ───────────────────────────────────────────────────────────────────────────
# Goal: every print/export carries the company's corporate identity. We read
# from ERPNext's built-in Letter Head DocType — single source of truth, same
# letterhead the admin uses for invoices. We deliberately do NOT build our
# own letterhead editor; the Frappe desk's existing one is better and
# already in the admin's workflow.
#
# Selection at the moment of export: the user clicks Print / Export → a
# small picker opens with the default pre-selected → they confirm or change.
# Selection precedence for the DEFAULT:
#   1. report.print_letter_head (if/when an admin pre-configures it later)
#   2. company.default_letter_head
#   3. The system-default Letter Head (is_default = 1)
#   4. None — exports proceed without letterhead.


@frappe.whitelist()
def list_letter_heads() -> list[dict]:
    """List all enabled Letter Heads on this bench.

    Returns minimal metadata only; the full payload (HTML + structured
    fields) comes from get_letterhead() on demand to avoid bloating the
    list response. Defensive: if Letter Head doctype is missing on a very
    old bench, returns an empty list.
    """
    if not frappe.db.exists("DocType", "Letter Head"):
        return []
    try:
        rows = frappe.get_all(
            "Letter Head",
            filters={"disabled": 0},
            fields=["name", "letter_head_name", "is_default"],
            order_by="is_default desc, letter_head_name asc",
        )
    except Exception as e:
        frappe.log_error(f"list_letter_heads failed: {e}", "Neotec Insight: letterhead")
        return []
    return [
        {
            "name": r["name"],
            "label": r.get("letter_head_name") or r["name"],
            "is_default": cint(r.get("is_default") or 0),
        }
        for r in rows
    ]


@frappe.whitelist()
def resolve_letterhead(report: str | None = None, company: str | None = None) -> dict:
    """Run the default-precedence chain and return the suggested Letter Head
    name. The caller (frontend) uses this to pre-select the dropdown when
    the export picker opens.

    Returns: {name, source} where source explains which step in the chain
    won — useful for the UI to label "Default for this company" etc.
    """
    if not frappe.db.exists("DocType", "Letter Head"):
        return {"name": "", "source": "none"}

    # 1. Per-report override.
    if report:
        try:
            doc = _resolve_report_doc(report)
            pl = getattr(doc, "print_letter_head", None)
            if pl and frappe.db.exists("Letter Head", pl):
                return {"name": pl, "source": "report"}
        except Exception:
            pass  # Report didn't resolve — fall through to next step.

    # 2. Company default.
    if company:
        try:
            cdl = frappe.db.get_value("Company", company, "default_letter_head")
            if cdl and frappe.db.exists("Letter Head", cdl):
                return {"name": cdl, "source": "company"}
        except Exception:
            pass

    # 3. System default.
    try:
        sd = frappe.db.get_value("Letter Head", {"is_default": 1, "disabled": 0}, "name")
        if sd:
            return {"name": sd, "source": "system"}
    except Exception:
        pass

    return {"name": "", "source": "none"}


def _extract_logo_from_html(html: str) -> str | None:
    """Best-effort extract the first <img src="..."> from Letter Head HTML.

    Returns the absolute URL (or path) if found, else None. Deliberately
    naive — Letter Head HTML is admin-controlled but unstructured; we don't
    try to parse arbitrary HTML, just pick the first image. If the admin's
    Letter Head doesn't have an <img> at all (text-only letterheads), we
    return None and the Excel export falls back to text-only.
    """
    if not html:
        return None
    import re
    m = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', html, re.IGNORECASE)
    if not m:
        return None
    src = m.group(1).strip()
    if not src:
        return None
    # If it's a relative path, prefix with the site URL so external clients
    # can resolve it. Frappe stores logos under /files/... typically.
    if src.startswith("/"):
        try:
            base = frappe.utils.get_url()
            return base.rstrip("/") + src
        except Exception:
            return src
    return src


def _company_contact_fields(company: str | None) -> dict:
    """Pull the company's contact details from ERPNext's Company doc.

    These feed the Excel/CSV exports where rendering arbitrary letterhead
    HTML doesn't work. We return what's reliably available; missing fields
    are returned as empty strings, never None, so the consumer doesn't
    have to null-check.
    """
    out = {
        "company_name": "",
        "address_lines": [],
        "phone": "",
        "email": "",
        "website": "",
        "tax_id": "",
        "company_logo": "",
    }
    if not company:
        return out
    try:
        c = frappe.db.get_value(
            "Company",
            company,
            ["company_name", "phone_no", "email", "website", "tax_id", "company_logo"],
            as_dict=True,
        )
    except Exception:
        c = None
    if c:
        out["company_name"] = c.get("company_name") or company
        out["phone"] = c.get("phone_no") or ""
        out["email"] = c.get("email") or ""
        out["website"] = c.get("website") or ""
        out["tax_id"] = c.get("tax_id") or ""
        logo = c.get("company_logo") or ""
        if logo and logo.startswith("/"):
            try:
                out["company_logo"] = frappe.utils.get_url().rstrip("/") + logo
            except Exception:
                out["company_logo"] = logo
        else:
            out["company_logo"] = logo
    # Address — pull the company's primary address if present. Defensive
    # because Address doctype might not have a row linked.
    try:
        addr_links = frappe.get_all(
            "Dynamic Link",
            filters={"link_doctype": "Company", "link_name": company, "parenttype": "Address"},
            fields=["parent"],
            limit_page_length=1,
        )
        if addr_links:
            addr = frappe.db.get_value(
                "Address",
                addr_links[0]["parent"],
                ["address_line1", "address_line2", "city", "state", "pincode", "country"],
                as_dict=True,
            )
            if addr:
                lines = []
                if addr.get("address_line1"): lines.append(addr["address_line1"])
                if addr.get("address_line2"): lines.append(addr["address_line2"])
                csp = ", ".join(filter(None, [addr.get("city"), addr.get("state"), addr.get("pincode")]))
                if csp: lines.append(csp)
                if addr.get("country"): lines.append(addr["country"])
                out["address_lines"] = lines
    except Exception:
        pass  # Address is optional; an empty list is fine.
    return out


@frappe.whitelist()
def get_letterhead(name: str = "", company: str | None = None) -> dict:
    """Return the full Letter Head payload for embedding in exports.

    Two formats served from one call:
      - HTML (header_html, footer_html) for PDF and Print — used as-is by
        embedding into the export's page template.
      - Structured fields (company_name, address_lines, logo_url, contact
        info) for Excel and CSV — where HTML can't render.

    `name` is the Letter Head document name. If empty, returns the
    structured company info with no HTML (the Excel/CSV path still works;
    the PDF path renders without letterhead, which is the honest default
    when the admin chose "Without letterhead").
    """
    _require_read()
    company_fields = _company_contact_fields(company)

    out = {
        "name": "",
        "label": "",
        "header_html": "",
        "footer_html": "",
        "logo_url": company_fields["company_logo"],
        **company_fields,
    }

    if not name or not frappe.db.exists("Letter Head", name):
        return out

    try:
        lh = frappe.db.get_value(
            "Letter Head",
            name,
            ["letter_head_name", "content", "footer", "image"],
            as_dict=True,
        )
    except Exception:
        return out
    if not lh:
        return out

    out["name"] = name
    out["label"] = lh.get("letter_head_name") or name
    out["header_html"] = lh.get("content") or ""
    out["footer_html"] = lh.get("footer") or ""

    # Letter Head's own image takes precedence over the Company's logo for
    # the Excel/CSV path — the admin chose this specific letterhead, so
    # its image is what they want.
    lh_image = lh.get("image") or ""
    if lh_image:
        if lh_image.startswith("/"):
            try:
                out["logo_url"] = frappe.utils.get_url().rstrip("/") + lh_image
            except Exception:
                out["logo_url"] = lh_image
        else:
            out["logo_url"] = lh_image
    else:
        # No explicit image field — try to extract from header HTML.
        extracted = _extract_logo_from_html(out["header_html"])
        if extracted:
            out["logo_url"] = extracted

    return out


# ───────────────────────────────────────────────────────────────────────────
# Budget derivation + copy (v1.9.56)
# ───────────────────────────────────────────────────────────────────────────
# Every budget number visible in Insight comes from a real Insight Budget
# Cell document — no more in-memory FY-1 × 1.10 silent computation. These
# endpoints let admins generate / duplicate cells in bulk.


def _derive_basis_year_monthly(
    *,
    report: str,
    basis_year: int,
    rows: list[dict],
    company: str | None,
    cost_center: str | None = None,
    project: str | None = None,
    department: str | None = None,
    branch: str | None = None,
    custom_dimension_fieldname: str | None = None,
    custom_dimension_value: str | None = None,
) -> dict[str, dict[int, float]]:
    """Fetch actual GL monthly amounts for the basis year, with the same
    dimension scope the book carries. Returns {row_key: {month: amount}}.

    Uses execute_report against the basis year. Cells are derived from the
    actuals — not from a prior budget — so the derivation honestly
    reflects what happened in that year.
    """
    doc = _resolve_report_doc(report)
    definition = json.loads(doc.definition_json or "{}")
    flag_to_accounts = load_flag_to_accounts(doc.name)
    dim_filters_safe: dict = {}
    if custom_dimension_fieldname and custom_dimension_value:
        dim_filters_safe = _sanitise_dimension_filters({custom_dimension_fieldname: custom_dimension_value})

    try:
        res = execute_report(
            report_def=definition,
            fiscal_year=basis_year,
            month_from=0,
            month_to=11,
            segment="total",
            cost_center=cost_center,
            project=project,
            department=department,
            branch=branch,
            company=company,
            flag_to_accounts=flag_to_accounts,
            dimension_filters=dim_filters_safe or None,
        )
    except Exception as e:
        frappe.log_error(f"derive_basis_year failed: {e}", "Neotec Insight: budget")
        return {}

    out: dict[str, dict[int, float]] = {}
    for r in (res.get("rows") or []):
        k = r.get("key")
        if not k:
            continue
        # Only source rows have meaningful basis values. Formula rows are
        # computed; deriving them from actuals would double-count if we
        # also derive their inputs. The formula will re-compute at run time.
        if r.get("kind") != "source":
            continue
        out[k] = r.get("monthly") or {}
    return out


def _parse_growth_overrides(raw: str | dict | None) -> dict[str, float]:
    """Parse per-row growth overrides. Accepts a dict or a JSON string.
    Returns {row_key: growth_pct}. Invalid entries silently dropped.
    """
    if not raw:
        return {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            return {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, float] = {}
    for k, v in raw.items():
        if not isinstance(k, str):
            continue
        try:
            out[k] = float(v)
        except (TypeError, ValueError):
            continue
    return out


def _resolve_row_growth_pct(
    row: dict,
    rows: list[dict],
    overrides: dict[str, float],
    default_growth_pct: float,
) -> float:
    """Resolve the growth % for a source row. Precedence:
       1. Source row's own override (most specific).
       2. The growth % of the most recent section ABOVE this row, if set.
       3. The book's default growth %.

    The cascade is implemented by walking `rows` from the source row backward
    to find the nearest section header. This means section overrides apply
    to every source row beneath them — until the next section, or until a
    source-row override wins.
    """
    key = row.get("key")
    if key in overrides:
        return overrides[key]
    # Walk back to find the most recent section above this row.
    try:
        idx = next(i for i, r in enumerate(rows) if r.get("key") == key)
    except StopIteration:
        return default_growth_pct
    for r in rows[idx - 1::-1]:
        if r.get("kind") == "section":
            sec_key = r.get("key")
            if sec_key and sec_key in overrides:
                return overrides[sec_key]
            break
    return default_growth_pct


@frappe.whitelist(methods=["POST"])
def derive_budget_cells(
    book: str,
    basis_offset: int | str = 1,
    default_growth_pct: float | str = 10.0,
    row_overrides: str | dict | None = None,
    preview: int = 0,
) -> dict:
    """Generate Insight Budget Cell documents for a book by applying a
    growth % to the basis year's actuals.

    Args:
      book: Insight Budget Book name.
      basis_offset: how many years back to read actuals from (1=FY-1, max 5).
      default_growth_pct: base growth (e.g. 10 means × 1.10). Negative OK.
      row_overrides: optional dict {row_key: growth_pct} for per-row /
                     per-section overrides. Section keys cascade to their
                     source rows unless the source row has its own override.
      preview: when 1, returns the proposed cells WITHOUT creating them.
               Lets the UI show a confirmation table before commit.

    Returns:
      {
        "preview": <bool>,
        "cells_created": <int>,
        "cells_replaced": <int>,    # existing cells overwritten
        "basis_year": <int>,
        "growth_summary": [{row_key, label, growth_pct, source}, ...],
        "preview_cells": [{row_key, month, amount}, ...] (only when preview=1)
      }
    """
    _require_write("Insight Budget Book")
    _check_edit_permission()
    book_doc = frappe.get_doc("Insight Budget Book", book)
    report_name = book_doc.report
    report_doc = _resolve_report_doc(report_name)
    fy = cint(book_doc.fiscal_year)

    basis_offset_i = max(1, min(5, cint(basis_offset)))
    basis_year = fy - basis_offset_i
    growth_default = float(default_growth_pct or 0)
    overrides = _parse_growth_overrides(row_overrides)

    definition = json.loads(report_doc.definition_json or "{}")
    rows = definition.get("rows", []) or []

    # Read basis-year actuals with the book's dimension scope honoured.
    cc = book_doc.dimension_value if book_doc.dimension_type == "cost_center" else None
    pj = book_doc.dimension_value if book_doc.dimension_type == "project" else None
    dp = book_doc.dimension_value if book_doc.dimension_type == "department" else None
    br = book_doc.dimension_value if book_doc.dimension_type == "branch" else None
    cd_field = book_doc.custom_dimension_fieldname if book_doc.dimension_type == "custom" else None
    cd_value = book_doc.dimension_value if book_doc.dimension_type == "custom" else None

    basis_by_row = _derive_basis_year_monthly(
        report=report_name,
        basis_year=basis_year,
        rows=rows,
        company=report_doc.company,
        cost_center=cc,
        project=pj,
        department=dp,
        branch=br,
        custom_dimension_fieldname=cd_field,
        custom_dimension_value=cd_value,
    )

    growth_summary: list[dict] = []
    preview_cells: list[dict] = []
    cells_to_write: list[tuple[str, int, float]] = []  # (row_key, month, amount)

    for row in rows:
        if row.get("kind") != "source":
            continue
        rk = row.get("key")
        if not rk:
            continue
        # Determine which growth % applies.
        g_pct = _resolve_row_growth_pct(row, rows, overrides, growth_default)
        # Provenance: source = "row_override" | "section_override" | "default"
        if rk in overrides:
            src = "row_override"
        else:
            src = "default"
            try:
                idx = next(i for i, r in enumerate(rows) if r.get("key") == rk)
                for r in rows[idx - 1::-1]:
                    if r.get("kind") == "section":
                        if r.get("key") in overrides:
                            src = "section_override"
                        break
            except StopIteration:
                pass
        growth_summary.append({
            "row_key": rk,
            "label": row.get("label", rk),
            "growth_pct": g_pct,
            "source": src,
        })
        basis_monthly = basis_by_row.get(rk, {})
        multiplier = 1.0 + (g_pct / 100.0)
        for m in range(12):
            # basis_monthly keys may be ints or stringified ints depending
            # on serialisation; check both. We round to the nearest integer
            # — same precision the legacy fallback used — so derived
            # numbers don't look spuriously precise.
            v = basis_monthly.get(m, basis_monthly.get(str(m), 0.0))
            try:
                derived = round(float(v) * multiplier)
            except (TypeError, ValueError):
                derived = 0
            if derived == 0 and float(v or 0) == 0:
                continue  # don't write cells for zero basis × any growth = 0
            cells_to_write.append((rk, m, float(derived)))
            preview_cells.append({"row_key": rk, "month": m, "amount": float(derived)})

    if cint(preview):
        return {
            "preview": True,
            "cells_created": 0,
            "cells_replaced": 0,
            "basis_year": basis_year,
            "growth_summary": growth_summary,
            "preview_cells": preview_cells,
        }

    # Actual write — replace existing cells on this book for the affected
    # (row, month) tuples. Pre-existing manual entries on rows/months not in
    # the derivation are preserved (e.g. if a row has zero basis, we skip
    # it; if the admin had manually entered something for it, that stays).
    affected_pairs = [(rk, m) for (rk, m, _) in cells_to_write]
    replaced = 0
    if affected_pairs:
        existing = frappe.get_all(
            "Insight Budget Cell",
            filters={"book": book_doc.name},
            fields=["name", "row_key", "month"],
            limit_page_length=0,
        )
        idx = {(e["row_key"], int(e["month"])): e["name"] for e in existing}
        for (rk, m, amt) in cells_to_write:
            existing_name = idx.get((rk, m))
            if existing_name:
                frappe.db.set_value("Insight Budget Cell", existing_name, "amount", amt, update_modified=False)
                replaced += 1
            else:
                cell = frappe.new_doc("Insight Budget Cell")
                cell.book = book_doc.name
                cell.report = report_name
                cell.fiscal_year = fy
                cell.row_key = rk
                cell.month = m
                cell.amount = amt
                # The legacy schema has segment for backward compat.
                cell.segment = "total"
                cell.insert(ignore_permissions=False)
    created = len(cells_to_write) - replaced

    # Persist the derivation config + audit fields on the book so the user
    # can re-derive later with the same settings.
    book_doc.derive_basis_offset = basis_offset_i
    book_doc.derive_default_growth_pct = growth_default
    book_doc.derive_overrides_json = json.dumps(overrides) if overrides else ""
    book_doc.derive_last_run_at = frappe.utils.now()
    book_doc.derive_last_run_count = len(cells_to_write)
    book_doc.save(ignore_permissions=False)
    frappe.db.commit()

    return {
        "preview": False,
        "cells_created": created,
        "cells_replaced": replaced,
        "basis_year": basis_year,
        "growth_summary": growth_summary,
    }


@frappe.whitelist(methods=["POST"])
def copy_budget_book(source_book: str, target_fiscal_year: int | str, target_label: str | None = None) -> dict:
    """Duplicate a budget book to a different fiscal year, copying all cells.

    Same report, same dimension scope, new fiscal year. If a book already
    exists for (report, target_fy, dimension), the copy is refused — the
    admin should derive into the existing book or pick a different target.

    Returns: {name: <new_book_name>, cells_copied: <int>}
    """
    _check_edit_permission()
    src = frappe.get_doc("Insight Budget Book", source_book)
    target_fy = cint(target_fiscal_year)
    if not target_fy:
        frappe.throw("target_fiscal_year is required.")
    if target_fy == cint(src.fiscal_year):
        frappe.throw("Target fiscal year must differ from the source book's year.")

    # Check for an existing book on the same (report, target_fy, dimension).
    conflict_filters = {
        "report": src.report,
        "fiscal_year": target_fy,
        "dimension_type": src.dimension_type,
        "dimension_value": src.dimension_value or "",
    }
    if src.dimension_type == "custom":
        conflict_filters["custom_dimension_fieldname"] = src.custom_dimension_fieldname or ""
    conflict = frappe.db.exists("Insight Budget Book", conflict_filters)
    if conflict:
        frappe.throw(
            f"A book already exists for FY{target_fy} on this scope ({conflict}). "
            "Edit or derive into that book instead, or delete it first."
        )

    # Build the new book.
    new = frappe.new_doc("Insight Budget Book")
    new.report = src.report
    new.fiscal_year = target_fy
    new.dimension_type = src.dimension_type
    new.dimension_value = src.dimension_value
    new.custom_dimension_fieldname = src.custom_dimension_fieldname
    new.label = target_label or f"{src.label} — FY{target_fy}"
    new.label_is_custom = 1
    new.status = "draft"
    new.is_primary_axis_book = src.is_primary_axis_book
    new.derive_basis_offset = src.derive_basis_offset
    new.derive_default_growth_pct = src.derive_default_growth_pct
    new.derive_overrides_json = src.derive_overrides_json
    new.insert(ignore_permissions=False)

    # Copy cells.
    cells = frappe.get_all(
        "Insight Budget Cell",
        filters={"book": src.name},
        fields=["row_key", "month", "amount", "segment"],
        limit_page_length=0,
    )
    for c in cells:
        cc = frappe.new_doc("Insight Budget Cell")
        cc.book = new.name
        cc.report = src.report
        cc.fiscal_year = target_fy
        cc.row_key = c["row_key"]
        cc.month = c["month"]
        cc.amount = c["amount"]
        cc.segment = c.get("segment") or "total"
        cc.insert(ignore_permissions=False)
    frappe.db.commit()

    return {"name": new.name, "cells_copied": len(cells)}


# ─── Combo view dispatcher (v1.9.63) ─────────────────────────────────────


@frappe.whitelist()
def run_combo_report(
    report: str,
    dim1: str,
    dim2: str,
    fiscal_year: int | None = None,
    month_from: int = 0,
    month_to: int = 11,
    company: str | None = None,
    as_of_date: str | None = None,
    from_date: str | None = None,
    cost_center: str | list | None = None,
    project: str | list | None = None,
    department: str | list | None = None,
    branch: str | list | None = None,
    finance_book: str | None = None,
    dimension_filters: str | dict | None = None,
    fy_start_month_override: int | None = None,
    period_mode: str = "fiscal_year",
    period_from_date: str | None = None,
    period_to_date: str | None = None,
    use_cache: int = 1,
) -> dict:
    """v1.9.63 — combo view dispatcher.

    Takes a report name + two dimensions, detects the report type, and
    dispatches to the appropriate engine path. P&L reports use the
    flow-based combo (sum of activity over date range). TB and BS use
    the balance-based combo (closing balance as of date).

    Frontend doesn't need to know which engine ran — same response shape
    in all cases. The two dimensions are validated against the
    discovered dimension whitelist before any SQL runs.

    Returns:
        {
            "report": {name, report_name, slug},
            "view": "combo",
            "dimensions_picked": [dim1, dim2],
            "rows": [{row_key, row_label, tuple, value}, ...],
            "filters": {...},  # echoed so frontend can render header
            "performance": {execution_ms, cache_hit}
        }
    """
    _require_read()
    doc = _resolve_report_doc(report)
    report_type = (getattr(doc, "report_type", "pnl") or "pnl").lower()

    # Validate both dimensions against the configured set. Reject anything
    # not on the whitelist before any SQL gets built — this is the
    # injection-safety boundary.
    valid_dims = _all_valid_dimension_fieldnames()
    for d in (dim1, dim2):
        if not d or d not in valid_dims:
            frappe.throw(f"Invalid dimension '{d}' for combo view.")
    if dim1 == dim2:
        frappe.throw("Combo view requires two different dimensions.")

    # Sanitise native and custom dim filters at the boundary.
    cc = _normalise_dim_param(cost_center)
    pj = _normalise_dim_param(project)
    dp = _normalise_dim_param(department)
    br = _normalise_dim_param(branch)
    dim_filters_safe = _sanitise_dimension_filters(dimension_filters)
    # Resolve fy_start override (1-12, else None).
    fy_override = None
    if fy_start_month_override is not None:
        try:
            _m = int(fy_start_month_override)
            if 1 <= _m <= 12:
                fy_override = _m
        except (TypeError, ValueError):
            pass

    # v1.9.65 — period mode for combo too. Date-range combo means
    # "sum activity in this date range, grouped by (dim1, dim2)" — no FY
    # involved. Only applies to P&L combo path; balance combo uses
    # as_of_date which is already date-driven.
    pm = period_mode if period_mode in ("fiscal_year", "date_range") else "fiscal_year"
    pfd = period_from_date if pm == "date_range" else None
    ptd = period_to_date if pm == "date_range" else None

    cache_key = _execution_cache_key(
        report_name=doc.name, user=frappe.session.user,
        kind="combo", report_type=report_type,
        dim1=dim1, dim2=dim2,
        fiscal_year=fiscal_year, month_from=month_from, month_to=month_to,
        company=company or doc.company or "",
        as_of_date=as_of_date or "", from_date=from_date or "",
        cost_center=sorted(cc) if cc else cc,
        project=sorted(pj) if pj else pj,
        department=sorted(dp) if dp else dp,
        branch=sorted(br) if br else br,
        finance_book=finance_book or "",
        dim_filters=json.dumps(dim_filters_safe, sort_keys=True, default=str),
        fy_override=fy_override,
        period_mode=pm,
        period_from_date=pfd,
        period_to_date=ptd,
    )
    if cint(use_cache):
        cached = frappe.cache().get_value(cache_key)
        if isinstance(cached, dict):
            cached["cache_hit"] = True
            return cached

    started = time.perf_counter()

    if report_type in ("trial_balance", "balance_sheet", "pnl_statement"):
        from neotec_insight.neotec_insight.utils.balance_execution import (
            run_balance_combo_engine, _load_chart_of_accounts,
        )
        eff_company = company or doc.company
        if not eff_company:
            frappe.throw("company is required for balance-based combo.")
        coa = _load_chart_of_accounts(eff_company)
        root_types = None
        combo_from = from_date
        combo_asof = as_of_date
        if report_type == "balance_sheet":
            root_types = ["Asset", "Liability", "Equity"]
        elif report_type == "pnl_statement":
            # The dedicated P&L statement has no flag-based row tree, so it
            # uses the account-based engine like TB/BS — but as *period flow*
            # over the chosen date range (Income/Expense only).
            root_types = ["Income", "Expense"]
            combo_from = pfd or from_date
            combo_asof = ptd or as_of_date
        if not combo_asof:
            frappe.throw("as_of_date (or period_to_date) is required for combo.")
        # Dept/Branch fold into dimension_filters for balance engine —
        # matches how run_trial_balance / run_balance_sheet construct it.
        dim_filters_eff = dict(dim_filters_safe)
        if dp:
            dim_filters_eff["department"] = dp
        if br:
            dim_filters_eff["branch"] = br
        result = run_balance_combo_engine(
            company=eff_company,
            as_of_date=combo_asof,
            from_date=combo_from,
            dim1=dim1, dim2=dim2,
            coa=coa,
            root_types=root_types,
            cost_center=cc,
            project=pj,
            finance_book=finance_book,
            dimension_filters=dim_filters_eff,
        )
    else:
        # P&L family — pnl, pnl_statement, or anything else.
        from neotec_insight.neotec_insight.utils.execution import execute_combo_report
        fy = cint(fiscal_year)
        # v1.9.65 — in date_range mode FY is irrelevant. In fiscal_year
        # mode it's required.
        if pm == "fiscal_year" and not fy:
            frappe.throw("fiscal_year is required for P&L combo in fiscal_year mode.")
        if pm == "date_range" and not (pfd and ptd):
            frappe.throw("period_from_date and period_to_date are required in date_range mode.")
        mf = max(0, min(cint(month_from), 11))
        mt = max(mf, min(cint(month_to), 11))
        flag_to_accounts = load_flag_to_accounts(doc.name)
        definition = json.loads(doc.definition_json or "{}")
        result = execute_combo_report(
            report_def=definition,
            fiscal_year=fy,
            month_from=mf, month_to=mt,
            dim1=dim1, dim2=dim2,
            cost_center=cc, project=pj,
            department=dp, branch=br,
            company=company or doc.company,
            flag_to_accounts=flag_to_accounts,
            dimension_filters=dim_filters_safe,
            fy_start_month_override=fy_override,
            period_mode=pm,
            period_from_date=pfd,
            period_to_date=ptd,
        )

    elapsed_ms = int((time.perf_counter() - started) * 1000)

    payload = {
        "report": {
            "name": doc.name,
            "report_name": doc.report_name,
            "slug": doc.slug,
            "report_type": report_type,
        },
        "view": result["view"],
        "dimensions_picked": result["dimensions_picked"],
        "rows": result["rows"],
        "filters": {
            "dim1": dim1, "dim2": dim2,
            "fiscal_year": fiscal_year,
            "month_from": month_from, "month_to": month_to,
            "company": company or doc.company,
            "as_of_date": as_of_date,
            "from_date": from_date,
            "cost_center": cc, "project": pj,
            "department": dp, "branch": br,
            "finance_book": finance_book,
            "dimension_filters": dim_filters_safe,
            "fy_start_month_override": fy_override,
            "period_mode": pm,
            "period_from_date": pfd,
            "period_to_date": ptd,
        },
        "performance": {
            "execution_ms": elapsed_ms,
            "cache_hit": False,
        },
    }

    if cint(use_cache):
        frappe.cache().set_value(cache_key, payload, expires_in_sec=900)
    return payload


# ─── Multi-period TB/BS (v1.9.63) ────────────────────────────────────────


@frappe.whitelist()
def run_trial_balance_multi_period(
    report: str,
    company: str,
    fiscal_year: str | int,
    granularity: str = "quarter",
    cost_center: str | list | None = None,
    project: str | list | None = None,
    department: str | list | None = None,
    branch: str | list | None = None,
    root_types: str | list[str] | None = None,
    finance_book: str | None = None,
    dimension_filters: str | dict | None = None,
    show_group_accounts: int = 1,
    show_zero_values: int = 0,
    presentation_currency: str | None = None,
    fy_start_month_override: int | None = None,
    use_cache: int = 1,
) -> dict:
    """Trial Balance with multiple period columns instead of one as-of date.

    v1.9.63 — for evolution-over-time views. `granularity` controls the
    column structure:
      - 'month'   → 12 columns, one closing balance per month-end
      - 'quarter' → 4 columns (Q1/Q2/Q3/Q4 closing)
      - 'half'    → 2 columns (H1/H2 closing)
      - 'year'    → 1 column (year-end closing)

    All closing balances are cumulative from time zero to the period
    boundary — they reflect the account's position AT that moment in time,
    not the period's activity. For period-activity views, use the
    standard run_trial_balance endpoint.

    The response shape:
        {
            "report": {...},
            "filters": {...},
            "periods": [
                {"key": "m1", "label": "Jan", "end_date": "2025-01-31"},
                ...
            ],
            "accounts": [
                {
                    "name": "1100 - Cash",
                    "account_name": "Cash",
                    "root_type": "Asset",
                    "balances": {"m1": 12000.0, "m2": 15300.0, ...}
                },
                ...
            ],
            "performance": {...}
        }
    """
    _require_read()
    from neotec_insight.neotec_insight.utils.balance_execution import (
        run_multi_period_balance, _load_chart_of_accounts,
    )
    from neotec_insight.neotec_insight.utils.fiscal_year import (
        fiscal_year_bounds, fy_month_range_to_date_range,
        format_fy_label, get_company_fy_start_month,
    )

    doc = _resolve_report_doc(report)
    if not company:
        frappe.throw("company is required.")

    granularity = (granularity or "quarter").strip().lower()
    if granularity not in ("month", "quarter", "half", "year"):
        frappe.throw(f"Invalid granularity '{granularity}'. Use month/quarter/half/year.")

    fy = cint(fiscal_year)

    # Normalise dim filters.
    cc = _normalise_dim_param(cost_center)
    pj = _normalise_dim_param(project)
    dp = _normalise_dim_param(department)
    br = _normalise_dim_param(branch)
    dim_filters_safe = _sanitise_dimension_filters(dimension_filters)
    rt_list = _parse_root_types(root_types)
    # Fold dept/branch into the dim_filters payload — consistent with
    # the single-period TB endpoint.
    if dp:
        dim_filters_safe = {**dim_filters_safe, "department": dp}
    if br:
        dim_filters_safe = {**dim_filters_safe, "branch": br}

    fy_override = None
    if fy_start_month_override is not None:
        try:
            _m = int(fy_start_month_override)
            if 1 <= _m <= 12:
                fy_override = _m
        except (TypeError, ValueError):
            pass

    cache_key = _execution_cache_key(
        report_name=doc.name, user=frappe.session.user,
        kind="tb_multi_period", company=company, fy=fy,
        granularity=granularity,
        cost_center=sorted(cc) if cc else cc,
        project=sorted(pj) if pj else pj,
        root_types=",".join(rt_list) if rt_list else "",
        finance_book=finance_book or "",
        dim_filters=json.dumps(dim_filters_safe, sort_keys=True, default=str),
        sga=cint(show_group_accounts), szv=cint(show_zero_values),
        pc=presentation_currency or "",
        fy_override=fy_override,
    )
    if cint(use_cache):
        cached = frappe.cache().get_value(cache_key)
        if isinstance(cached, dict):
            cached["cache_hit"] = True
            return cached

    started = time.perf_counter()

    # Build the period boundary list. Each period is (key, end_date_iso).
    # End dates are computed via fy_month_range_to_date_range which honours
    # company FY orientation and any override.
    periods: list[dict] = []
    if granularity == "month":
        # 12 month-end boundaries.
        for m in range(12):
            _, end_d = fy_month_range_to_date_range(
                company, fy, 0, m, fy_start_month_override=fy_override
            )
            # Label — use the company-aware month abbreviation.
            from neotec_insight.neotec_insight.utils.periods import _month_label_for_fy_index
            label = _month_label_for_fy_index(company, m, fy_start_month_override=fy_override)
            periods.append({"key": f"m{m}", "label": label, "end_date": end_d.isoformat()})
    elif granularity == "quarter":
        for q in range(4):
            last_month = (q + 1) * 3 - 1  # 2, 5, 8, 11
            _, end_d = fy_month_range_to_date_range(
                company, fy, 0, last_month, fy_start_month_override=fy_override
            )
            periods.append({"key": f"q{q + 1}", "label": f"Q{q + 1}", "end_date": end_d.isoformat()})
    elif granularity == "half":
        for h in range(2):
            last_month = (h + 1) * 6 - 1  # 5, 11
            _, end_d = fy_month_range_to_date_range(
                company, fy, 0, last_month, fy_start_month_override=fy_override
            )
            periods.append({"key": f"h{h + 1}", "label": f"H{h + 1}", "end_date": end_d.isoformat()})
    else:  # year
        _, end_d = fy_month_range_to_date_range(
            company, fy, 0, 11, fy_start_month_override=fy_override
        )
        periods.append({"key": "y", "label": "Year-end", "end_date": end_d.isoformat()})

    # Load chart of accounts; constrain by root_types if BS or specified.
    coa = _load_chart_of_accounts(company)
    if rt_list:
        coa = [a for a in coa if (a.get("root_type") or "") in set(rt_list)]
    leaf_accounts = [a for a in coa if not a.get("is_group")]
    leaf_names = [a["name"] for a in leaf_accounts]

    # One query, all period boundaries — the SUM(CASE WHEN...) trick.
    boundaries = [(p["key"], p["end_date"]) for p in periods]
    raw = run_multi_period_balance(
        company=company,
        period_boundaries=boundaries,
        accounts=leaf_names,
        cost_center=cc, project=pj,
        finance_book=finance_book,
        dimension_filters=dim_filters_safe,
    )

    # Sign-correct per root_type — Asset/Expense debit-positive (raw),
    # Liability/Equity/Income credit-positive (flip).
    accounts_out: list[dict] = []
    for a in leaf_accounts:
        acc_name = a["name"]
        rt = a.get("root_type") or ""
        bal = raw.get(acc_name, {})
        # Skip all-zero accounts when show_zero_values is off.
        if not show_zero_values and not any(v for v in bal.values()):
            continue
        flip = -1 if rt in ("Liability", "Equity", "Income") else 1
        signed = {pk: flip * flt(v) for pk, v in bal.items()}
        accounts_out.append({
            "name": acc_name,
            "account_name": a.get("account_name") or acc_name,
            "root_type": rt,
            "account_currency": a.get("account_currency"),
            "balances": signed,
        })

    # Group totals — optional, computed by aggregating leaf children.
    if cint(show_group_accounts):
        group_accounts = [a for a in coa if a.get("is_group")]
        # Naive aggregation: any account whose `parent_account` is the
        # group sums into it. For deeper hierarchies the frontend
        # computes group rollups; we just emit group rows so they're
        # available. The leaves we computed above are the source.
        leaf_by_parent: dict[str, list[str]] = {}
        for a in leaf_accounts:
            p = a.get("parent_account") or ""
            leaf_by_parent.setdefault(p, []).append(a["name"])
        # Build a recursive descendant set per group.
        def descendants_of(name: str) -> list[str]:
            out: list[str] = list(leaf_by_parent.get(name, []))
            for g in group_accounts:
                if g.get("parent_account") == name:
                    out.extend(descendants_of(g["name"]))
            return out
        for g in group_accounts:
            kids = descendants_of(g["name"])
            if not kids:
                continue
            agg: dict[str, float] = {}
            for pk in (p["key"] for p in periods):
                agg[pk] = sum(raw.get(k, {}).get(pk, 0.0) for k in kids)
            if not show_zero_values and not any(agg.values()):
                continue
            rt = g.get("root_type") or ""
            flip = -1 if rt in ("Liability", "Equity", "Income") else 1
            signed = {pk: flip * v for pk, v in agg.items()}
            accounts_out.append({
                "name": g["name"],
                "account_name": g.get("account_name") or g["name"],
                "root_type": rt,
                "account_currency": g.get("account_currency"),
                "is_group": 1,
                "balances": signed,
            })

    payload = {
        "report": {
            "name": doc.name,
            "report_name": doc.report_name,
            "slug": doc.slug,
            "report_type": getattr(doc, "report_type", "trial_balance"),
        },
        "filters": {
            "company": company,
            "fiscal_year": fy,
            "granularity": granularity,
            "cost_center": cc, "project": pj,
            "department": dp, "branch": br,
            "root_types": rt_list,
            "finance_book": finance_book,
            "dimension_filters": dim_filters_safe,
            "show_group_accounts": cint(show_group_accounts),
            "show_zero_values": cint(show_zero_values),
            "presentation_currency": presentation_currency,
            "fy_start_month_override": fy_override,
            "fy_label": format_fy_label(company, fy, fy_start_month_override=fy_override),
            "fy_start_month": get_company_fy_start_month(company, override=fy_override),
        },
        "periods": periods,
        "accounts": accounts_out,
        "performance": {
            "execution_ms": int((time.perf_counter() - started) * 1000),
            "cache_hit": False,
        },
    }

    if cint(use_cache):
        frappe.cache().set_value(cache_key, payload, expires_in_sec=900)
    return payload


@frappe.whitelist()
def run_balance_sheet_multi_period(
    report: str,
    company: str,
    fiscal_year: str | int,
    granularity: str = "quarter",
    cost_center: str | list | None = None,
    project: str | list | None = None,
    department: str | list | None = None,
    branch: str | list | None = None,
    finance_book: str | None = None,
    dimension_filters: str | dict | None = None,
    show_group_accounts: int = 1,
    show_zero_values: int = 0,
    presentation_currency: str | None = None,
    fy_start_month_override: int | None = None,
    use_cache: int = 1,
) -> dict:
    """Balance Sheet with multiple period columns.

    v1.9.63 — same engine as run_trial_balance_multi_period but root_types
    constrained to Asset / Liability / Equity. Delegated so callers can
    use the more semantic endpoint name and we have one place to extend
    if BS-specific behaviour diverges later (e.g. unclosed-P&L roll-in).
    """
    _require_read()
    return run_trial_balance_multi_period(
        report=report,
        company=company,
        fiscal_year=fiscal_year,
        granularity=granularity,
        cost_center=cost_center, project=project,
        department=department, branch=branch,
        root_types=["Asset", "Liability", "Equity"],
        finance_book=finance_book,
        dimension_filters=dimension_filters,
        show_group_accounts=show_group_accounts,
        show_zero_values=show_zero_values,
        presentation_currency=presentation_currency,
        fy_start_month_override=fy_start_month_override,
        use_cache=use_cache,
    )


@frappe.whitelist()
def create_taccount_variant(report: str | None = None):
    """v2.36.1 — one-click T-format copy of an existing vertical P&L.

    Clones the report definition, sets presentation_format='t_account' and
    auto-classifies every row's t_side from the vertical structure:
    revenue sections → Trading credit ("Other …" income → P&L credit),
    cost-of-sales sections → Trading debit, remaining expense sections →
    P&L debit, the gross-profit formula → GP balancer, the net-profit/income
    formula → NP balancer, other total formulas excluded (the T view computes
    its own side totals). The copy is fully editable afterwards — the
    heuristics are a starting point, not a cage."""
    import json as _json
    import re as _re

    if not report or not frappe.db.exists("Insight Report Definition", report):
        frappe.throw(_("Report not found."))
    if not frappe.has_permission("Insight Report Definition", "create"):
        frappe.throw(_("Not permitted."))
    src = frappe.get_doc("Insight Report Definition", report)
    definition = _json.loads(src.definition_json or "{}")
    rows = definition.get("rows") or []

    REV = _re.compile(r"revenue|sales|income|turnover|إيراد", _re.I)
    DIRECT = _re.compile(r"cost of|cogs|direct|purchase|consum|تكلفة", _re.I)
    OTHER = _re.compile(r"other|misc|أخرى", _re.I)
    GP = _re.compile(r"gross\s*profit|مجمل", _re.I)
    NP = _re.compile(r"net\s*(profit|income|loss)|صافي", _re.I)

    section_mode = None  # 'trading_cr' | 'trading_dr' | 'pl_dr'
    for row in rows:
        kind = row.get("kind")
        label = row.get("label") or ""
        if kind == "section":
            if DIRECT.search(label):
                section_mode = "trading_dr"
            elif REV.search(label):
                section_mode = "trading_cr"
            else:
                section_mode = "pl_dr"
            row["t_side"] = ""
        elif kind == "source":
            if section_mode == "trading_cr":
                row["t_side"] = "credit_pl" if OTHER.search(label) else "credit_trading"
            elif section_mode == "trading_dr":
                row["t_side"] = "debit_trading"
            else:
                row["t_side"] = "credit_pl" if REV.search(label) and OTHER.search(label) else "debit_pl"
        elif kind == "formula":
            key = row.get("key") or ""
            if GP.search(label) or key == "gross_profit":
                row["t_side"] = "gp_balancer"
            elif NP.search(label) or key in ("net_profit", "net_income"):
                row["t_side"] = "np_balancer"
            else:
                row["t_side"] = ""

    new = frappe.copy_doc(src)
    new.report_name = f"{src.report_name} (T-Format)"
    base_slug = f"{src.slug or frappe.scrub(src.report_name)}-t"
    slug = base_slug
    i = 2
    while frappe.db.exists("Insight Report Definition", {"slug": slug}):
        slug = f"{base_slug}{i}"
        i += 1
    new.slug = slug
    new.presentation_format = "t_account"
    new.definition_json = _json.dumps(definition)
    new.insert(ignore_permissions=True)
    frappe.db.commit()
    mapped = sum(1 for r in rows if r.get("t_side"))
    return {"name": new.name, "report_name": new.report_name, "slug": new.slug,
            "rows_classified": mapped, "rows_total": len(rows)}


@frappe.whitelist()
def coverage_check(report: str, company: str | None = None, fiscal_year: int | None = None):
    """v2.44.0 — Noor's checkup: every Income/Expense leaf in the chart vs the
    accounts actually mapped into this report's rows. Unmapped accounts are
    exactly the mismatch between the native P&L and the Insight report —
    returned with their type and FY value so they can be assigned to a row."""
    doc = _resolve_report_doc(report)
    company = company or doc.company
    fy = cint(fiscal_year) if fiscal_year else None
    if not frappe.has_permission("GL Entry", "read"):
        frappe.throw(_("Not permitted."))

    coa = frappe.get_all("Account",
                         filters={"company": company, "is_group": 0,
                                  "root_type": ["in", ["Income", "Expense"]]},
                         fields=["name", "account_name", "account_number", "root_type"],
                         limit_page_length=0)

    # accounts mapped into ANY row of this report (group bindings expanded)
    mapped: set[str] = set()
    lr = {a["name"]: a for a in frappe.get_all("Account",
                                               filters={"company": company},
                                               fields=["name", "lft", "rgt", "is_group"],
                                               limit_page_length=0)}
    for m in frappe.get_all("Account Flag Mapping", filters={"report": doc.name},
                            fields=["account", "is_group_binding"], limit_page_length=0):
        acc = m["account"]
        if m.get("is_group_binding") and acc in lr:
            l, r = lr[acc]["lft"], lr[acc]["rgt"]
            for n, info in lr.items():
                if not info["is_group"] and info["lft"] > l and info["rgt"] < r:
                    mapped.add(n)
        else:
            mapped.add(acc)

    missing = [a for a in coa if a["name"] not in mapped]
    values: dict[str, float] = {}
    if missing and fy:
        try:
            from neotec_insight.neotec_insight.utils.fiscal_year import fy_month_range_to_date_range
            ds, de = fy_month_range_to_date_range(company, fy, 0, 11)
            rows = frappe.db.sql(
                """SELECT account, SUM(credit - debit) AS amt
                   FROM `tabGL Entry`
                   WHERE company=%(c)s AND is_cancelled=0
                     AND voucher_type != 'Period Closing Voucher'
                     AND account IN %(a)s
                     AND posting_date BETWEEN %(f)s AND %(t)s
                   GROUP BY account""",
                {"c": company, "a": tuple(x["name"] for x in missing), "f": ds, "t": de},
                as_dict=True)
            values = {r["account"]: flt(r["amt"], 2) for r in rows}
        except Exception:
            frappe.log_error(frappe.get_traceback(), "coverage_check value pass failed")

    out = []
    for a in missing:
        v = values.get(a["name"], 0.0)
        out.append({"account": a["name"], "account_name": a["account_name"],
                    "account_number": a.get("account_number") or "",
                    "root_type": a["root_type"],
                    "amount": v if a["root_type"] == "Income" else flt(-v, 2)})
    out.sort(key=lambda x: (x["root_type"], -abs(x["amount"])))
    rows_opts = [{"key": r.get("key"), "label": r.get("label"), "flag": r.get("flag")}
                 for r in json.loads(doc.definition_json or "{}").get("rows", [])
                 if r.get("kind") == "source"]
    return {"company": company, "total_pl_accounts": len(coa), "mapped": len(coa) - len(missing),
            "missing": out, "missing_value_income": flt(sum(x["amount"] for x in out if x["root_type"] == "Income"), 2),
            "missing_value_expense": flt(sum(x["amount"] for x in out if x["root_type"] == "Expense"), 2),
            "rows": rows_opts}


# ── Company brand (v2.55.0) ─────────────────────────────────────────────────
# The shell header shows the company the operator is actually reporting on —
# its name and its logo — so a printed pack and the screen it came from can be
# matched at a glance. ERPNext's Company master carries `company_logo`, but a
# freshly-provisioned site usually has none; `set_company_logo` is the
# provision for that, writing an uploaded file back to the master so every
# report picks it up without per-machine configuration.


def _abs_file_url(url: str) -> str:
    """Site-absolute URL for a Frappe /files path; pass anything else through."""
    if not url:
        return ""
    if url.startswith("/"):
        try:
            return frappe.utils.get_url().rstrip("/") + url
        except Exception:
            return url
    return url


@frappe.whitelist()
def company_brand(company: str | None = None) -> dict:
    """Name, logo and statutory identifiers for the header brand block.

    Falls back to the global default company so the header is never blank on
    first paint, and never raises — the shell must render even if the Company
    doctype is missing or the user cannot read it.
    """
    out = {
        "company": "", "label": "", "logo": "", "logo_source": "",
        "tax_id": "", "can_edit": 0,
    }
    try:
        if not frappe.db.exists("DocType", "Company"):
            return out
        if not company:
            company = frappe.defaults.get_user_default("Company") \
                or frappe.defaults.get_global_default("company") or ""
        if not company:
            rows = frappe.get_all("Company", fields=["name"], order_by="company_name asc",
                                  limit_page_length=1)
            company = rows[0]["name"] if rows else ""
        if not company:
            return out

        fields = ["company_name", "tax_id"]
        try:
            if frappe.get_meta("Company").get_field("company_logo"):
                fields.append("company_logo")
        except Exception:
            pass
        c = frappe.db.get_value("Company", company, fields, as_dict=True) or {}

        out["company"] = company
        out["label"] = c.get("company_name") or company
        out["tax_id"] = c.get("tax_id") or ""
        logo = c.get("company_logo") or ""
        if logo:
            out["logo"] = _abs_file_url(logo)
            out["logo_source"] = "company"
        else:
            # Provision #2: the site's default Letter Head image, when the
            # Company master itself carries no logo.
            try:
                lh = frappe.db.get_value("Letter Head", {"disabled": 0, "is_default": 1},
                                         ["image"], as_dict=True)
                if lh and lh.get("image"):
                    out["logo"] = _abs_file_url(lh["image"])
                    out["logo_source"] = "letterhead"
            except Exception:
                pass
        try:
            out["can_edit"] = 1 if frappe.has_permission("Company", "write", company) else 0
        except Exception:
            out["can_edit"] = 0
    except Exception as e:
        frappe.log_error(f"company_brand failed: {e}", "Neotec Insight: brand")
    return out


@frappe.whitelist()
def set_company_logo(company: str, logo_url: str = "") -> dict:
    """Write an uploaded logo back to the Company master.

    This is the 'provision for logo if not available in company master':
    the operator uploads once from the shell header and every report — screen,
    Print, PDF and Excel — resolves the same image afterwards. An empty
    `logo_url` clears it.
    """
    if not company:
        frappe.throw(_("Select a company first."))
    if not frappe.db.exists("Company", company):
        frappe.throw(_("Company {0} not found.").format(company))
    if not frappe.has_permission("Company", "write", company):
        frappe.throw(_("Not permitted to change the company logo."))
    try:
        if not frappe.get_meta("Company").get_field("company_logo"):
            frappe.throw(_("This site's Company master has no logo field."))
    except frappe.ValidationError:
        raise
    except Exception:
        pass

    url = (logo_url or "").strip()
    if url and not (url.startswith("/files/") or url.startswith("/private/files/")):
        frappe.throw(_("Upload the logo as a file first."))

    frappe.db.set_value("Company", company, "company_logo", url or None)
    frappe.db.commit()
    frappe.clear_document_cache("Company", company)
    return {"ok": True, "logo": _abs_file_url(url)}
