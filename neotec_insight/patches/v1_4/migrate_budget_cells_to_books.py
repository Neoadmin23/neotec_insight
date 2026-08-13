"""Migrate v1.3.x budget cells onto Insight Budget Books.

Before v1.4 the Budget Cell had only (report, fy, month, row_key, segment).
After v1.4 each cell links to a Book. This patch creates a default book per
unique (report, fy, segment) tuple and back-fills the `book` field.

Idempotent — safe to re-run.
"""
from __future__ import annotations

import frappe

from neotec_insight.neotec_insight.doctype.insight_budget_book.insight_budget_book import (
    auto_label,
    auto_slug,
)


def execute() -> None:
    cells_without_book = frappe.get_all(
        "Insight Budget Cell",
        filters={"book": ["in", [None, ""]]},
        fields=["name", "report", "fiscal_year", "segment"],
        limit_page_length=0,
    )
    if not cells_without_book:
        return

    # Group by (report, fy, segment) so we create exactly one book per cluster.
    clusters: dict[tuple, list[str]] = {}
    for c in cells_without_book:
        key = (c["report"], int(c["fiscal_year"]), (c.get("segment") or "total").strip())
        clusters.setdefault(key, []).append(c["name"])

    created = 0
    attached = 0
    for (report, fy, segment), cell_names in clusters.items():
        # Map legacy segment values onto dimension_type / dimension_value.
        # Pre-v1.4 the segment string held things like 'total', 'irsaa', 'nii'.
        # We treat any non-'total' segment as a cost_center dimension by default
        # — that matches the legacy convention from the prototype.
        if segment in {"", "total"}:
            dim_type, dim_value = "total", None
        else:
            dim_type, dim_value = "cost_center", segment

        slug = auto_slug(report, fy, dim_type, dim_value)
        existing_book = frappe.db.get_value("Insight Budget Book", {"slug": slug}, "name")
        if not existing_book:
            book = frappe.new_doc("Insight Budget Book")
            book.report = report
            book.fiscal_year = fy
            book.dimension_type = dim_type
            book.dimension_value = dim_value
            book.label = auto_label(fy, dim_type, dim_value)
            book.slug = slug
            book.status = "approved"  # legacy data is already in use
            book.label_is_custom = 0
            book.insert(ignore_permissions=True)
            existing_book = book.name
            created += 1

        # Bulk update — one SQL per cluster instead of per cell.
        frappe.db.sql(
            """UPDATE `tabInsight Budget Cell`
               SET book = %s
               WHERE name IN ({placeholders})""".format(
                placeholders=", ".join(["%s"] * len(cell_names))
            ),
            (existing_book, *cell_names),
        )
        attached += len(cell_names)

    frappe.db.commit()
    print(f"[neotec_insight] v1_4 migration: created {created} budget books, attached {attached} cells.")
