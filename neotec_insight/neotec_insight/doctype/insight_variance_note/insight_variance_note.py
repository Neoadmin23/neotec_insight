from __future__ import annotations

import frappe
from frappe.model.document import Document


class InsightVarianceNote(Document):
    def validate(self) -> None:
        if not self.report or not self.row_key or not self.fiscal_year:
            frappe.throw("report, row_key and fiscal_year are required.")
        self.row_key = (self.row_key or "").strip()
        if self.commentary:
            self.commentary = self.commentary.strip()
