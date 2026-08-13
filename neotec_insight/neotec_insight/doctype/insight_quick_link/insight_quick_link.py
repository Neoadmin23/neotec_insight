from __future__ import annotations

import frappe
from frappe.model.document import Document


class InsightQuickLink(Document):
    def validate(self) -> None:
        if not self.label or not self.label.strip():
            frappe.throw("Label is required.")
        self.label = self.label.strip()

        if not self.url or not self.url.strip():
            frappe.throw("URL is required.")
        self.url = self.url.strip()

        # Accept either an ERPNext desk path ("/app/...") or a full URL.
        if not (self.url.startswith("/") or self.url.startswith("http://") or self.url.startswith("https://")):
            frappe.throw(
                "URL must start with '/' for a desk path (e.g. /app/general-ledger) "
                "or with http:// or https:// for an external link."
            )

        if self.icon:
            # Strip a leading 'ti ti-' or 'ti-' if the user pasted the full class.
            self.icon = self.icon.strip().replace("ti ti-", "").replace("ti-", "").strip()

        if self.sort_order is None:
            self.sort_order = 10
