from __future__ import annotations

import re

import frappe
from frappe.model.document import Document


def slugify(label: str) -> str:
    """Reduce a label to a stable URL-safe slug. Suffix with a counter if
    the base slug is already taken (handled at insert time by Frappe's
    unique constraint catching the duplicate)."""
    s = re.sub(r"[^a-zA-Z0-9]+", "-", (label or "").strip().lower()).strip("-")
    return s or "dashboard"


class InsightDashboard(Document):
    def before_validate(self) -> None:
        if not self.slug and self.label:
            self.slug = slugify(self.label)
        if not self.owner_user:
            self.owner_user = frappe.session.user

    def can_user_edit(self, user: str | None = None) -> bool:
        user = user or frappe.session.user
        if user == "Administrator":
            return True
        if self.owner_user == user:
            return True
        # Finance roles can edit shared dashboards but not other users' private ones.
        if self.is_shared and _is_finance_user(user):
            return True
        return False

    def can_user_view(self, user: str | None = None) -> bool:
        user = user or frappe.session.user
        if user == "Administrator":
            return True
        if self.owner_user == user:
            return True
        if self.is_shared:
            return True
        return _is_finance_user(user)


def _is_finance_user(user: str) -> bool:
    return bool(set(frappe.get_roles(user)) & {"System Manager", "Accounts Manager"})
