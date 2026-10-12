"""Force-reload doctypes whose JSON changed in 2.88.x.

Frappe re-syncs a doctype on migrate only when the JSON `modified` is newer
than the one in the database. 2.88.1 (VAT Adjustment: Reclassify + target_box)
and 2.88.5 (Allocation Rule: excluded_ccs) changed JSON without bumping it, so
a site could keep the old definition. reload_doc(force=True) removes the
dependence on timestamps."""
import frappe


def execute():
    for dt in ("insight_allocation_excluded_cc", "insight_allocation_rule",
               "insight_allocation_entry", "insight_vat_adjustment"):
        frappe.reload_doc("neotec_insight", "doctype", dt, force=True)
