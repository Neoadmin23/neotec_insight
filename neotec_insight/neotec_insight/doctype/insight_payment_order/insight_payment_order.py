from __future__ import annotations

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, getdate


class InsightPaymentOrder(Document):
    def validate(self):
        self._invoice_is_real()
        self._company_matches()
        self._not_before_the_invoice()
        self._not_over_ordered()

    def _invoice_is_real(self):
        docstatus, company = frappe.db.get_value(
            "Sales Invoice", self.sales_invoice, ["docstatus", "company"]) or (None, None)
        if docstatus is None:
            frappe.throw(_("Sales Invoice {0} does not exist.").format(self.sales_invoice))
        if docstatus != 1:
            frappe.throw(_("Sales Invoice {0} is not submitted. A payment order against a draft or "
                           "cancelled invoice would move a tax point onto a document that carries no tax.")
                         .format(self.sales_invoice))
        self._invoice_company = company

    def _company_matches(self):
        if getattr(self, "_invoice_company", None) and self.company != self._invoice_company:
            frappe.throw(_("{0} belongs to {1}, not {2}.").format(
                self.sales_invoice, self._invoice_company, self.company))

    def _not_before_the_invoice(self):
        posting = frappe.db.get_value("Sales Invoice", self.sales_invoice, "posting_date")
        if posting and getdate(self.order_date) < getdate(posting):
            frappe.throw(_("The payment order is dated {0}, before the invoice on {1}. That would "
                           "pull the supply into a quarter earlier than the invoice itself.")
                         .format(self.order_date, posting))

    def _not_over_ordered(self):
        """Part orders must not exceed the invoice between them.

        Over-ordering is usually a duplicate entry, and a duplicate silently
        turns a part order into a full one — which releases the whole invoice
        into a quarter on the strength of an order that never covered it.
        """
        if not flt(self.amount):
            return
        grand = flt(frappe.db.get_value("Sales Invoice", self.sales_invoice, "base_grand_total"))
        others = frappe.get_all("Insight Payment Order",
                                filters={"sales_invoice": self.sales_invoice,
                                         "name": ["!=", self.name or ""]},
                                pluck="amount") or []
        total = flt(self.amount) + sum(flt(a) for a in others)
        if grand and total > abs(grand) + 0.01:
            frappe.throw(_("Payment orders against {0} would total {1}, more than the invoice at {2}.")
                         .format(self.sales_invoice, total, abs(grand)))
