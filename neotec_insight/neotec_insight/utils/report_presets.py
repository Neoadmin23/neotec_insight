from __future__ import annotations

import json
import os

import frappe

PRESETS_DIR = os.path.join(os.path.dirname(__file__), "..", "report_presets")


def sync_default_app_report_presets() -> None:
    """Ensure all app-shipped presets exist as Insight Report Definitions.

    Only inserts if the slug isn't present. Safe across re-migrations.
    """
    if not os.path.isdir(PRESETS_DIR):
        return
    for entry in sorted(os.listdir(PRESETS_DIR)):
        if not entry.endswith(".json"):
            continue
        path = os.path.join(PRESETS_DIR, entry)
        try:
            with open(path, encoding="utf-8") as fh:
                preset = json.load(fh)
        except Exception:
            continue
        slug = preset.get("slug")
        if not slug:
            continue
        if frappe.db.exists("Insight Report Definition", {"slug": slug}):
            continue
        # v2.89.5 — a preset is installed once. If this site had the report
        # and it was deleted, re-creating the stock preset under the same name
        # silently replaced a customised report with the shipped original:
        # rows added since (Total Expenses, allocation rows) vanished and
        # nothing said so. Leave it missing and say where to restore it from.
        if _was_deleted(slug):
            frappe.log_error(
                title=f"Insight: report {slug} was deleted — preset not re-created",
                message=(f"The report '{slug}' existed on this site and was deleted. The stock "
                         "preset was NOT installed over it, so a customised version is not "
                         "hidden. Restore it from Insight > Backup > Recover > Report versions."))
            continue
        doc = frappe.new_doc("Insight Report Definition")
        doc.report_name = preset.get("report_name") or slug
        doc.slug = slug
        doc.description = preset.get("description") or ""
        doc.is_active = 1
        doc.seed_source = "app-preset"
        doc.report_type = preset.get("report_type") or "pnl"
        doc.comparison_mode = preset.get("comparison_mode") or "vs_budget"
        doc.prior_years = preset.get("prior_years") or 1
        doc.primary_budget_axis = preset.get("primary_budget_axis") or "cost_center"
        doc.definition_json = json.dumps(preset.get("definition", {}), indent=2, sort_keys=True)
        doc.column_schema_json = json.dumps(preset.get("columns", []), indent=2, sort_keys=True)
        doc.filter_schema_json = json.dumps(preset.get("filters", []), indent=2, sort_keys=True)
        doc.insert(ignore_permissions=True)


def _was_deleted(slug: str) -> bool:
    try:
        return bool(frappe.db.exists("Deleted Document", {
            "deleted_doctype": "Insight Report Definition", "deleted_name": slug}))
    except Exception:
        return False
