"""v2.90.0 — Insight Report Schedule gains run_as. Existing schedules run as
the user who created them until someone saves them again."""
import frappe


def execute():
    frappe.reload_doc("neotec_insight", "doctype", "insight_report_schedule", force=True)
    for s in frappe.get_all("Insight Report Schedule", fields=["name", "owner", "run_as"]):
        if not s.get("run_as"):
            frappe.db.set_value("Insight Report Schedule", s["name"], "run_as", s["owner"],
                                update_modified=False)
