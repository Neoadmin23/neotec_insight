# Copyright (c) 2026, Neotec and contributors
import frappe
from frappe.model.document import Document
from frappe.utils import flt


class InsightBankSlip(Document):
    def validate(self):
        # Total the bank actually moved = amount + fee + VAT (fees are often
        # billed on the same statement line as the transfer).
        self.total_amount = flt(self.amount) + flt(self.fee) + flt(self.vat)
        # Normalise the primary reference for reliable matching.
        if self.bank_reference:
            self.bank_reference = self.bank_reference.strip()
