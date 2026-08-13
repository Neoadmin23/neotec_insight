from __future__ import annotations

import frappe
from frappe.model.document import Document


class InsightEBITDAAddback(Document):
    def validate(self) -> None:
        if not self.account or not self.company:
            frappe.throw("Both Company and Account are required.")

        acct = frappe.db.get_value(
            "Account", self.account, ["company", "is_group", "root_type"], as_dict=True
        )
        if not acct:
            frappe.throw(f"Account {self.account} does not exist.")
        if acct.company and acct.company != self.company:
            frappe.throw(f"Account {self.account} does not belong to {self.company}.")
        if acct.is_group:
            frappe.throw("Pick a leaf account, not a group.")
        # Add-backs are expense items; flagging a non-expense account is almost
        # always a mistake and would distort EBIT/EBITDA with the wrong sign.
        if acct.root_type and acct.root_type != "Expense":
            frappe.throw(
                f"{self.account} is a {acct.root_type} account. EBITDA add-backs "
                "(interest / depreciation) must be Expense accounts."
            )

        # One classification per (company, account).
        dup = frappe.db.exists(
            "Insight EBITDA Addback",
            {"company": self.company, "account": self.account, "name": ["!=", self.name]},
        )
        if dup:
            frappe.throw(
                f"{self.account} is already classified as an EBITDA add-back. "
                "Edit the existing entry instead of adding a second one."
            )
