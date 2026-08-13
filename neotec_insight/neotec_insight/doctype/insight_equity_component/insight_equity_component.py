from __future__ import annotations

import frappe
from frappe.model.document import Document


class InsightEquityComponent(Document):
    """A configurable equity-component lookup used by the Statement of
    Shareholder's Equity.

    Replaces the hardcoded Select options that previously lived on
    Insight Equity Movement.component (pre-v1.9.51). The component list is
    now editable: admins can rename, reorder, add, or delete components.

    On delete, we soft-check whether any Insight Equity Movement rows
    reference this component. If they do, the delete is blocked with a
    clear message — Frappe's built-in link integrity also enforces this,
    but a friendly message is better than a cryptic SQL error.
    """

    def on_trash(self) -> None:
        used = frappe.db.count(
            "Insight Equity Movement",
            filters={"component": self.name},
        )
        if used:
            frappe.throw(
                f"Cannot delete '{self.name}' — {used} equity movement entries "
                "still reference it. Reassign or delete those entries first."
            )
