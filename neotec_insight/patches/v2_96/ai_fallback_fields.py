"""v2.96.1 — Insight AI Settings gains the optional fallback model."""
import frappe


def execute():
    frappe.reload_doc("neotec_insight", "doctype", "insight_ai_settings", force=True)
