from __future__ import annotations

import frappe
from frappe.model.document import Document


class InsightBudgetCell(Document):
    def validate(self) -> None:
        if self.month is None or not (0 <= int(self.month) <= 11):
            frappe.throw("Month must be 0-11 (0=Jan, 11=Dec).")
        if not self.row_key or not self.row_key.strip():
            frappe.throw("Row key is required.")
        self.row_key = self.row_key.strip()
        self.segment = (self.segment or "total").strip()
        # Sync report and fiscal_year from book if available — guards against drift.
        if self.book:
            book = frappe.db.get_value(
                "Insight Budget Book", self.book, ["report", "fiscal_year"], as_dict=True
            )
            if book:
                if not self.report:
                    self.report = book.report
                if not self.fiscal_year:
                    self.fiscal_year = book.fiscal_year
                if self.report != book.report:
                    frappe.throw(
                        f"Cell report '{self.report}' doesn't match book report '{book.report}'."
                    )
                if int(self.fiscal_year) != int(book.fiscal_year):
                    frappe.throw(
                        f"Cell fiscal_year '{self.fiscal_year}' doesn't match book fiscal_year '{book.fiscal_year}'."
                    )
