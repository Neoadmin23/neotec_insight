from __future__ import annotations

import frappe
from frappe import _
from frappe.model.document import Document

# Only these transitions are allowed — enforced here, not left to the UI to
# get right. A rule cannot skip from Candidate straight to Active; a human
# approval step is not optional, per the build spec's rule governance
# section: "Rules may be mined automatically, but they must never be
# silently deployed."
_ALLOWED_TRANSITIONS = {
    "Candidate": {"Under Review", "Retired"},
    "Under Review": {"Approved", "Candidate", "Retired"},
    "Approved": {"Active", "Under Review", "Retired"},
    "Active": {"Suspended", "Retired"},
    "Suspended": {"Active", "Retired"},
    "Retired": set(),
}


class InsightCashFlowClassificationRule(Document):
    def validate(self):
        self._validate_transition()
        if self.status == "Active" and not self.approved_by_user:
            frappe.throw(_("A rule cannot become Active without having been Approved by someone first."))

    def _validate_transition(self):
        if self.is_new():
            if self.status not in ("Candidate", ""):
                frappe.throw(_("A new rule must start as Candidate."))
            return
        old_status = frappe.db.get_value(self.doctype, self.name, "status")
        if old_status == self.status:
            return
        allowed = _ALLOWED_TRANSITIONS.get(old_status, set())
        if self.status not in allowed:
            frappe.throw(
                _("Cannot move a rule from {0} to {1} directly.").format(old_status, self.status))
        if self.status == "Approved":
            self.approved_by_user = frappe.session.user
            self.approved_on = frappe.utils.now_datetime()

    def before_insert(self):
        self.created_by_user = frappe.session.user
        self.created_on = frappe.utils.now_datetime()
        self.status = "Candidate"
