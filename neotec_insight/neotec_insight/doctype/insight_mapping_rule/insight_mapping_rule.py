from __future__ import annotations

import frappe
from frappe.model.document import Document


class InsightMappingRule(Document):
    def validate(self) -> None:
        if not self.prefix or not self.prefix.strip():
            frappe.throw("Code prefix is required.")
        self.prefix = self.prefix.strip()
        if not self.prefix.isdigit():
            frappe.throw("Code prefix must be digits only.")
