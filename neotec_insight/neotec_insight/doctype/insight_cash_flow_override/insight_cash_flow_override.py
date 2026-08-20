from __future__ import annotations

import frappe
from frappe import _
from frappe.model.document import Document


class InsightCashFlowOverride(Document):
    def validate(self):
        dupe = frappe.db.exists(
            "Insight Cash Flow Override",
            {
                "voucher_type": self.voucher_type,
                "voucher_no": self.voucher_no,
                "name": ["!=", self.name or ""],
            },
        )
        if dupe:
            frappe.throw(
                _("{0} {1} is already overridden to a line ({2}). One voucher, one line.")
                .format(_(self.voucher_type), self.voucher_no, dupe)
            )

    def before_insert(self):
        self.created_by_user = frappe.session.user
        self.created_on = frappe.utils.now_datetime()
