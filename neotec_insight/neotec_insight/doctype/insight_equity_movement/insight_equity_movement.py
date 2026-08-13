from __future__ import annotations

import frappe
from frappe.model.document import Document


class InsightEquityMovement(Document):
    """Single equity-movement entry for the Statement of Shareholder's Equity.

    Each row represents one change to one equity component for one period.
    The Statement of Shareholder's Equity report rolls movements up by
    component, showing Beginning + Movements = Ending for each.

    v1.9.51 — component and movement_type are now LINKS to configurable
    DocTypes (Insight Equity Component, Insight Equity Movement Type). The
    "opening balance" rule is now identified by the is_opening_balance flag
    on the linked Movement Type, not by a hardcoded name comparison.

    Validation rules:
      - Opening Balance must be non-negative.
      - Other movements may be signed.
      - At most one Opening Balance per (company, fiscal_year, period,
        component) — multiple openings make no sense.
    """

    def validate(self) -> None:
        if not self.movement_type:
            return
        # Look up the type's flag rather than comparing the name.
        is_opening = bool(frappe.db.get_value(
            "Insight Equity Movement Type",
            self.movement_type,
            "is_opening_balance",
        ))
        if not is_opening:
            return
        # Opening balance rules.
        if self.amount is None or float(self.amount) < 0:
            frappe.throw("Opening balance must be a non-negative amount.")
        # Find all opening-balance type names so we catch duplicates regardless
        # of whether the admin renamed the opening type.
        opening_type_names = frappe.get_all(
            "Insight Equity Movement Type",
            filters={"is_opening_balance": 1},
            pluck="name",
        )
        if not opening_type_names:
            return
        existing = frappe.db.exists(
            "Insight Equity Movement",
            {
                "company": self.company,
                "fiscal_year": self.fiscal_year,
                "period": self.period,
                "component": self.component,
                "movement_type": ["in", opening_type_names],
                "name": ["!=", self.name or ""],
            },
        )
        if existing:
            frappe.throw(
                f"An Opening Balance already exists for {self.component} "
                f"in {self.company} {self.period} {self.fiscal_year}. "
                "Edit that one instead of creating a duplicate."
            )
