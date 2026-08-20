from __future__ import annotations

import frappe
from frappe import _
from frappe.model.document import Document


class InsightCashFlowBudget(Document):
    def validate(self):
        dupe = frappe.db.exists(
            "Insight Cash Flow Budget",
            {"line": self.line, "period_month": self.period_month, "name": ["!=", self.name or ""]},
        )
        if dupe:
            frappe.throw(_("{0} already has a budget for {1}.").format(self.line, self.period_month))
