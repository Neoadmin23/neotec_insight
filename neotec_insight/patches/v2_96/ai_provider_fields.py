"""v2.96.0 — Insight AI Settings gains provider, token limit, timeout and
Azure API version. Existing setups keep working as OpenAI-compatible."""
import frappe


def execute():
    frappe.reload_doc("neotec_insight", "doctype", "insight_ai_settings", force=True)
    if not frappe.db.get_single_value("Insight AI Settings", "ai_provider"):
        frappe.db.set_single_value("Insight AI Settings", "ai_provider", "OpenAI-compatible")
