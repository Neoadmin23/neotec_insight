from __future__ import annotations

import frappe
from frappe.model.document import Document


class InsightEquityMovementType(Document):
    """A configurable equity-movement-type lookup.

    Replaces the hardcoded Select options that previously lived on
    Insight Equity Movement.movement_type (pre-v1.9.51).

    Key behavioural rule: exactly ONE movement type should have
    is_opening_balance = 1. The Statement of Shareholder's Equity uses
    this flag (not the name) to identify which entry anchors a
    component's opening figure. We don't strictly enforce uniqueness
    here — a deployment in transition might temporarily have zero or
    two openings — but the validation warns clearly when this is wrong.
    """

    def validate(self) -> None:
        if self.is_opening_balance:
            # Warn if another type also has the flag.
            others = frappe.db.get_all(
                "Insight Equity Movement Type",
                filters={
                    "is_opening_balance": 1,
                    "name": ["!=", self.name or ""],
                },
                pluck="name",
            )
            if others:
                # Soft warning rather than hard throw — gives the admin a
                # chance to be in a transitional state (renaming the opening
                # marker) without being blocked.
                frappe.msgprint(
                    f"Note: '{others[0]}' is also marked as the opening balance type. "
                    "The Statement of Shareholder's Equity needs exactly one type with "
                    "this flag — uncheck the flag on the other type to fix.",
                    indicator="orange",
                    title="Multiple opening balance types",
                )

    def on_trash(self) -> None:
        used = frappe.db.count(
            "Insight Equity Movement",
            filters={"movement_type": self.name},
        )
        if used:
            frappe.throw(
                f"Cannot delete '{self.name}' — {used} equity movement entries "
                "still reference it. Reassign or delete those entries first."
            )
        # If we're deleting the opening-balance type, warn loudly.
        if self.is_opening_balance:
            frappe.msgprint(
                "You just deleted the Opening Balance movement type. "
                "The Statement of Shareholder's Equity cannot identify openings "
                "until another type is created and marked as Opening Balance.",
                indicator="red",
                title="Opening Balance type deleted",
            )
