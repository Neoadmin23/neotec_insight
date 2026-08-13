from __future__ import annotations

import json

import frappe
from frappe.model.document import Document


class AccountFlagMapping(Document):
    def validate(self) -> None:
        if not self.flag or not self.flag.strip():
            frappe.throw("P&L Classification (flag) cannot be empty.")
        self.flag = self.flag.strip()
        self._normalize_dimension_filters()

    def _normalize_dimension_filters(self) -> None:
        """Parse dimension_filters_json, drop empty pairs, rebuild canonical
        JSON and the human-readable scope_summary.

        Stored shape: JSON array of {"dimension_type","dimension_value"}.
        Empty array / null = whole company (no filter).
        """
        raw = self.dimension_filters_json
        pairs: list[dict] = []
        if raw:
            try:
                parsed = json.loads(raw) if isinstance(raw, str) else raw
                if isinstance(parsed, list):
                    for p in parsed:
                        if not isinstance(p, dict):
                            continue
                        dt = (p.get("dimension_type") or "").strip()
                        dv = (p.get("dimension_value") or "").strip()
                        if dt and dv:
                            pairs.append({"dimension_type": dt, "dimension_value": dv})
            except Exception:
                pairs = []
        self.dimension_filters_json = json.dumps(pairs) if pairs else ""
        if not pairs:
            self.scope_summary = ""
        else:
            self.scope_summary = " · ".join(
                f"{p['dimension_type']}: {p['dimension_value']}" for p in pairs
            )
