"""Saved dashboards: list, open, save and delete (v1.7).

v2.92.0 — moved unchanged out of api/report.py, which re-exports every
name so the `api.report.<name>` endpoint addresses keep working."""

import frappe
from neotec_insight.neotec_insight.api.report_shared import _require_read, _require_write


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
