from __future__ import annotations

import json

import frappe
from frappe import _



def _require_read() -> None:
    """Refuse a financial read from a user with no ledger access.

    `@frappe.whitelist()` requires a login, not a role, so without this these
    endpoints were callable over `/api/method/...` by any authenticated user,
    including portal users with no business seeing the ledger. Reading GL Entry
    is the right test: ERPNext already restricts it to the accounts roles, so
    this inherits the site's own configuration rather than inventing a second
    permission model.
    """
    if not frappe.has_permission("GL Entry", "read"):
        frappe.throw(
            _("You are not permitted to view financial data."),
            frappe.PermissionError,
        )


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


@frappe.whitelist()
def list_dashboards() -> list[dict]:
    _require_read()
    return frappe.get_all(
        "Insight Dashboard",
        fields=["name", "dashboard_name", "slug", "description", "is_active", "modified"],
        order_by="modified desc",
        limit_page_length=0,
    )


@frappe.whitelist()
def get_dashboard(dashboard: str) -> dict:
    _require_read()
    name = frappe.db.get_value("Insight Dashboard", {"slug": dashboard}, "name") or dashboard
    doc = frappe.get_doc("Insight Dashboard", name)
    return {
        "name": doc.name,
        "dashboard_name": doc.dashboard_name,
        "slug": doc.slug,
        "description": doc.description,
        "is_active": doc.is_active,
        "tiles": json.loads(doc.tiles_json or "[]"),
        "run_snapshots": json.loads(doc.run_snapshots_json or "[]"),
    }


@frappe.whitelist(methods=["POST"])
def save_dashboard(payload: str | dict) -> dict:
    _require_write("Insight Dashboard")
    data = frappe.parse_json(payload)
    if not isinstance(data, dict):
        frappe.throw("Payload must be an object.")
    name = data.get("name")
    if name:
        doc = frappe.get_doc("Insight Dashboard", name)
    else:
        doc = frappe.new_doc("Insight Dashboard")
        doc.slug = data.get("slug") or frappe.scrub(data.get("dashboard_name") or "untitled")
    doc.dashboard_name = data.get("dashboard_name") or doc.dashboard_name or "Untitled"
    doc.description = data.get("description") or doc.description
    doc.is_active = 1 if data.get("is_active", 1) else 0
    doc.tiles_json = json.dumps(data.get("tiles") or [], indent=2, sort_keys=True)
    doc.run_snapshots_json = json.dumps(data.get("run_snapshots") or [], indent=2, sort_keys=True)
    if doc.is_new():
        doc.insert()
    else:
        doc.save()
    return {"name": doc.name, "slug": doc.slug}


@frappe.whitelist(methods=["POST"])
def delete_dashboard(name: str) -> dict:
    _require_read()
    frappe.delete_doc("Insight Dashboard", name)
    return {"deleted": True}
