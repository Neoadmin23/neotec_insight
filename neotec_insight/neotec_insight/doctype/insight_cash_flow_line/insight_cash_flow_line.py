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
        #
        # v2.86.6 — cost_centers is a Table MultiSelect (one binding, several
        # cost centres, mapped once) rather than a single Link. "Set" now
        # means "at least one row", not "a truthy scalar".
        # v2.86.6 — no longer Cash In only. Cash Out lines that are
        # project/department-specific (Kafaa Project Allowance, Municipalities
        # Project Per Diem) need the same dimension check Collection always
        # had — a blank dimension value on a shared-account binding would
        # silently read as "every cost centre", regardless of which
        # direction the line prints on.
        if self.dimension_field:
            for b in self.bindings or []:
                if self.dimension_field == "Cost Center":
                    if not (b.get("cost_centers") or []):
                        frappe.throw(
                            _("Binding on {0} needs at least one Cost Center \u2014 this line's "
                              "dimension is set to Cost Center.")
                            .format(b.get("account") or _("(no account)"))
                        )
                elif self.dimension_field == "Project" and not b.get("project"):
                    frappe.throw(
                        _("Binding on {0} needs a Project \u2014 this line's dimension is set to Project.")
                        .format(b.get("account") or _("(no account)"))
                    )

        seen = set()
        for b in self.bindings or []:
            cc_key = tuple(sorted(row.get("cost_center") for row in (b.get("cost_centers") or [])))
            key = (b.get("account"), b.get("direction_mode"), cc_key,
                   b.get("project"), b.get("party_type"), b.get("party"))
            if key in seen:
                frappe.throw(_("This line has the same binding ({0}) more than once \u2014 "
                              "same account, same direction mode, same cost centres.")
                             .format(b.get("account") or _("(no account)")))
            seen.add(key)
