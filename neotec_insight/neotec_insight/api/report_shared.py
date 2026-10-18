"""Helpers shared by api/report.py and the modules split out of it (v2.92.0).

Permission gates, report resolution and accounting-dimension discovery.
Moved unchanged from report.py, which imports them back, so every caller
and every `api.report.<name>` reference keeps working. This module must not
import api/report.py — that is what keeps the split free of import cycles."""

import json
import frappe
from frappe import _


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
    """Finance read gate — one policy for every module (utils/access.py, v2.90.0)."""
    from neotec_insight.neotec_insight.utils.access import require_finance_read
    require_finance_read(_("You are not permitted to view financial reports."))


def _resolve_report_doc(report: str, ptype: str = "read"):
    """Load a report the user may read. v2.90.0 — loading is not
    authorising: the report's own permission rules (inactive reports for
    managers only) and its company's User Permissions are now checked here,
    for every path that resolves a report."""
    from neotec_insight.neotec_insight.utils.access import check_report
    name = frappe.db.get_value("Insight Report Definition", {"slug": report}, "name") or report
    doc = frappe.get_doc("Insight Report Definition", name)
    check_report(doc, ptype)
    return doc


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
