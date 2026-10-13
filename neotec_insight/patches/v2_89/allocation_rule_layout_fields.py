"""v2.89.2 — Insight Allocation Rule gains pool_row_label and layout_json.
Reloaded explicitly so the fields exist even if a timestamp comparison would
skip the sync."""
import frappe


def execute():
    frappe.reload_doc("neotec_insight", "doctype", "insight_allocation_rule", force=True)
