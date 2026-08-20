from __future__ import annotations

import frappe
from frappe import _
from frappe.model.document import Document


class InsightCashFlowLine(Document):
    def validate(self):
        self._validate_bindings()

    def _validate_bindings(self):
        # A Cash In line with a dimension_field set needs every binding to
        # actually carry that dimension's value — a blank one would silently
        # read as "every cost centre", which for a Cash In line sharing one
        # receivables account across every department is exactly the
        # overlap the reconciliation residual exists to catch, except this
        # is the one case that's cheap to catch here instead, before a run.
        if self.direction == "Cash In" and self.dimension_field:
            fieldname = "cost_center" if self.dimension_field == "Cost Center" else "project"
            for b in self.bindings or []:
                if not b.get(fieldname):
                    frappe.throw(
                        _("Binding on {0} needs a {1} \u2014 this line's dimension is set to {1}.")
                        .format(b.get("account") or _("(no account)"), self.dimension_field)
                    )

        seen = set()
        for b in self.bindings or []:
            key = (b.get("account"), b.get("direction_mode"), b.get("cost_center"),
                   b.get("project"), b.get("party_type"), b.get("party"))
            if key in seen:
                frappe.throw(_("This line has the same binding ({0}) more than once.")
                             .format(b.get("account") or _("(no account)")))
            seen.add(key)
