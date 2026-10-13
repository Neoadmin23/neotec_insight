"""v2.89.0 — create the snapshot store and turn on change history for
allocation entries, without depending on JSON `modified` timestamps."""
import frappe


def execute():
    for dt in ("insight_backup_snapshot", "insight_allocation_entry"):
        frappe.reload_doc("neotec_insight", "doctype", dt, force=True)
