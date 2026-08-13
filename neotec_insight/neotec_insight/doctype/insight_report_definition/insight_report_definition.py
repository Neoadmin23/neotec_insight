from __future__ import annotations

import json

import frappe
from frappe.model.document import Document

from neotec_insight.neotec_insight.utils.schema import (
    validate_column_schema,
    validate_filter_schema,
    validate_report_definition_schema,
)


class InsightReportDefinition(Document):
    def before_validate(self) -> None:
        if not self.slug and self.report_name:
            self.slug = frappe.scrub(self.report_name)
        self.definition_json = _normalize_json(self.definition_json, default={})
        self.column_schema_json = _normalize_json(self.column_schema_json, default=[])
        self.filter_schema_json = _normalize_json(self.filter_schema_json, default=[])
        if self.prior_years is None:
            self.prior_years = 1
        self.prior_years = max(0, min(int(self.prior_years), 5))

    def validate(self) -> None:
        definition = json.loads(self.definition_json)
        column_schema = json.loads(self.column_schema_json)
        filter_schema = json.loads(self.filter_schema_json)
        report_type = getattr(self, "report_type", "pnl") or "pnl"
        validate_report_definition_schema(definition, report_type=report_type)
        validate_column_schema(column_schema)
        validate_filter_schema(filter_schema)


CAPABILITY_VIEW = "view"
CAPABILITY_EXECUTE = "execute"
CAPABILITY_EDIT = "edit"
CAPABILITY_MANAGE = "manage"
ALL_CAPABILITIES = {CAPABILITY_VIEW, CAPABILITY_EXECUTE, CAPABILITY_EDIT, CAPABILITY_MANAGE}


def get_permission_query_conditions(user: str | None = None) -> str | None:
    user = user or frappe.session.user
    if can_manage(user):
        return None
    return "`tabInsight Report Definition`.`is_active` = 1"


def has_permission(doc, ptype: str = "read", user: str | None = None) -> bool:
    user = user or frappe.session.user
    if ptype == "read":
        return has_capability(doc, CAPABILITY_VIEW, user=user)
    if ptype == "write":
        return has_capability(doc, CAPABILITY_EDIT, user=user)
    return can_manage(user)


def can_manage(user: str | None = None) -> bool:
    user = user or frappe.session.user
    if user == "Administrator":
        return True
    roles = set(frappe.get_roles(user))
    return bool(roles & {"System Manager", "Accounts Manager"})


def has_capability(doc, capability: str, user: str | None = None) -> bool:
    user = user or frappe.session.user
    if can_manage(user):
        return True
    if capability not in ALL_CAPABILITIES:
        return False
    if getattr(doc, "owner", None) == user:
        return True
    if bool(getattr(doc, "is_active", 0)) and capability in {CAPABILITY_VIEW, CAPABILITY_EXECUTE}:
        return True
    return False


def _normalize_json(raw, *, default):
    if raw:
        try:
            parsed = json.loads(raw) if isinstance(raw, str) else raw
        except json.JSONDecodeError as exc:
            frappe.throw(f"Invalid JSON in field: {exc}")
    else:
        parsed = default
    return json.dumps(parsed, indent=2, sort_keys=True)
