from __future__ import annotations

import frappe
from frappe import _
from frappe.model.document import Document


class InsightAllocationEntry(Document):
	def validate(self):
		# One driver value per cost centre per month per rule. A duplicate
		# would double the numerator and quietly skew every other cost
		# centre's share, so it is refused rather than summed.
		dupe = frappe.db.exists(
			"Insight Allocation Entry",
			{
				"rule": self.rule,
				"cost_center": self.cost_center,
				"period_month": self.period_month,
				"name": ["!=", self.name or ""],
			},
		)
		if dupe:
			frappe.throw(
				_("{0} already has a value for {1} in {2}.").format(
					self.rule, self.cost_center, self.period_month
				)
			)
