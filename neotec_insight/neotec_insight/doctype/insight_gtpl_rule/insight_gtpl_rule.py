from __future__ import annotations

import frappe
from frappe import _
from frappe.model.document import Document


class InsightGTPLRule(Document):
	def validate(self):
		self._one_rule_per_date()
		self._no_duplicate_overrides()
		self._no_duplicate_groups()
		self._scope_is_not_empty()
		self._order_basis_has_a_field()

	def _one_rule_per_date(self):
		"""Two rules on the same company and date make resolution ambiguous, and an
		ambiguous rule silently changes a filed figure. Fail at entry instead."""
		clash = frappe.get_all(
			"Insight GTPL Rule",
			filters={
				"company": self.company,
				"effective_from": self.effective_from,
				"name": ["!=", self.name or ""],
			},
			pluck="name",
		)
		if clash:
			frappe.throw(
				_("{0} already has a rule effective {1} ({2}). Supersede it with a later date, or edit that rule.").format(
					self.company, self.effective_from, clash[0]
				)
			)

	def _no_duplicate_overrides(self):
		seen = set()
		for row in self.customer_overrides or []:
			if row.customer in seen:
				frappe.throw(_("Customer {0} is listed twice in the overrides.").format(row.customer))
			seen.add(row.customer)

	def _no_duplicate_groups(self):
		seen = set()
		for row in self.customer_groups or []:
			if row.customer_group in seen:
				frappe.throw(_("Customer group {0} is listed twice.").format(row.customer_group))
			seen.add(row.customer_group)

	def _scope_is_not_empty(self):
		"""An active deferral rule that matches nobody defers nothing, which looks
		identical to the rule working correctly. Only the invoice_date basis is
		allowed an empty scope, since that basis defers nothing by design."""
		if not self.is_active or self.trigger_basis == "invoice_date":
			return
		if not (self.customer_groups or self.customer_overrides):
			frappe.throw(
				_("Name at least one government customer group or customer override, otherwise this rule defers nothing.")
			)

	def _order_basis_has_a_field(self):
		"""Warn rather than block: falling back to receipt is a defensible default,
		but it must not happen silently."""
		if self.trigger_basis in ("order_only", "earlier_of_receipt_or_order") and not (self.order_date_field or "").strip():
			frappe.msgprint(
				_("No payment-order date field is set, so the order basis will fall back to payment receipt."),
				indicator="orange",
				title=_("Falling back to receipt"),
			)
