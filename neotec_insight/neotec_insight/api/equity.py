"""Statement of changes in equity: components, movement types and movements.

v2.92.0 — moved unchanged out of api/report.py, which re-exports every
name so the `api.report.<name>` endpoint addresses keep working."""

import frappe
from frappe.utils import cint
from neotec_insight.neotec_insight.api.report_shared import _check_edit_permission, _require_read


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
    from neotec_insight.neotec_insight.utils.compat import with_aliases
    return with_aliases(frappe.get_all(
        "Insight Equity Component",
        fields=["name", "display_order", "is_seeded", "description"],
        order_by="display_order asc, name asc",
    ), {"value": "name", "label": "name"})


@frappe.whitelist()
def list_equity_movement_types() -> list[dict]:
    """Public endpoint — used by the frontend to populate the movement-type
    dropdown. Includes the is_opening_balance flag so the UI can hint or
    auto-fill behaviour on a per-type basis.
    """
    _require_read()
    from neotec_insight.neotec_insight.utils.compat import with_aliases
    return with_aliases(frappe.get_all(
        "Insight Equity Movement Type",
        fields=["name", "display_order", "is_opening_balance", "default_sign", "is_seeded", "description"],
        order_by="display_order asc, name asc",
    ), {"value": "name", "label": "name"})


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
