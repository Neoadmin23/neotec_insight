from __future__ import annotations

import re

import frappe
from frappe.model.document import Document

DIM_TYPE_LABEL = {
    "total": "Total Company",
    "cost_center": "Cost Center",
    "department": "Department",
    "project": "Project",
}


def auto_label(fiscal_year: int, dimension_type: str, dimension_value: str | None) -> str:
    """Build the canonical book label.

    Format:
      FY{year} · Total Company                       (when dimension_type == 'total')
      FY{year} · {Dimension Type}: {Dimension Value}  (otherwise)
    """
    fy_part = f"FY{int(fiscal_year)}"
    if dimension_type == "total":
        return f"{fy_part} · Total Company"
    dim_label = DIM_TYPE_LABEL.get(dimension_type, dimension_type.title())
    value = (dimension_value or "").strip() or "(unset)"
    return f"{fy_part} · {dim_label}: {value}"


def auto_slug(report: str, fiscal_year: int, dimension_type: str, dimension_value: str | None) -> str:
    """Stable slug; never depends on the human label.

    Examples:
      fy26-cc-nisa-mssp
      fy26-total
      fy26-proj-prj-atlas
    """
    fy = f"fy{int(fiscal_year) % 100:02d}"
    type_short = {"cost_center": "cc", "department": "dept", "project": "proj", "total": "total"}.get(dimension_type, dimension_type)
    parts = [fy, type_short]
    if dimension_type != "total":
        slug_val = re.sub(r"[^a-z0-9]+", "-", (dimension_value or "").lower()).strip("-")
        if slug_val:
            parts.append(slug_val)
    base = "-".join(parts)
    report_slug = re.sub(r"[^a-z0-9]+", "-", (report or "").lower()).strip("-")
    if report_slug:
        return f"{report_slug}-{base}"
    return base


class InsightBudgetBook(Document):
    def before_validate(self) -> None:
        if not self.dimension_type:
            self.dimension_type = "total"
        if self.dimension_type == "total":
            self.dimension_value = None
        if self.dimension_value:
            self.dimension_value = self.dimension_value.strip()

        # Always auto-set slug from structured fields. Slug never changes after
        # creation (Frappe protects naming-rule fields from changing once stored).
        if not self.slug and self.report and self.fiscal_year is not None:
            self.slug = auto_slug(self.report, self.fiscal_year, self.dimension_type, self.dimension_value)

        # Auto-label unless the user has explicitly overridden it.
        if not self.label_is_custom and self.fiscal_year is not None:
            self.label = auto_label(self.fiscal_year, self.dimension_type, self.dimension_value)

        if not self.status:
            self.status = "draft"

    def validate(self) -> None:
        if self.dimension_type not in DIM_TYPE_LABEL:
            frappe.throw(f"Invalid dimension_type '{self.dimension_type}'.")
        if self.dimension_type != "total" and not (self.dimension_value or "").strip():
            frappe.throw(f"Dimension value is required when dimension_type = '{self.dimension_type}'.")

        # Enforce uniqueness on (report, fiscal_year, dimension_type, dimension_value)
        # — name is unique by virtue of slug, but a friendlier error is helpful.
        existing = frappe.db.get_value(
            "Insight Budget Book",
            {
                "report": self.report,
                "fiscal_year": self.fiscal_year,
                "dimension_type": self.dimension_type,
                "dimension_value": self.dimension_value or "",
            },
            "name",
        )
        if existing and existing != self.name:
            frappe.throw(
                f"A book already exists for this report, fiscal year, and dimension. "
                f"Edit '{existing}' instead of creating a duplicate."
            )

    def can_user_edit(self, user: str | None = None) -> bool:
        user = user or frappe.session.user
        if user == "Administrator":
            return True
        if self.status in {"approved", "locked"}:
            return _is_finance_user(user)
        if self.owner_user == user:
            return True
        return _is_finance_user(user)


def _is_finance_user(user: str) -> bool:
    return bool(set(frappe.get_roles(user)) & {"System Manager", "Accounts Manager"})
