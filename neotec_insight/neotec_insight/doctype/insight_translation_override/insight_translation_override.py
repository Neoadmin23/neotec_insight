# Copyright (c) 2026, Neotec and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class InsightTranslationOverride(Document):
    def validate(self):
        self.source_doctype = (self.source_doctype or "").strip()
        self.source_name = (self.source_name or "").strip()
        self.arabic = (self.arabic or "").strip()
        if not self.source_doctype or not self.source_name:
            frappe.throw("Source DocType and Source Name are required.")
        # Enforce one override per (source_doctype, source_name).
        dup = frappe.db.get_value(
            "Insight Translation Override",
            {"source_doctype": self.source_doctype, "source_name": self.source_name,
             "name": ["!=", self.name]},
            "name",
        )
        if dup:
            frappe.throw(f"A translation override for {self.source_name} already exists.")
