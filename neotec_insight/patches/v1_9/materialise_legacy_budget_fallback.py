"""Convert the legacy in-memory FY-1 × 1.10 budget fallback into real
Insight Budget Cell documents (v1.9.56).

Before v1.9.56, _load_budget computed a fallback budget at run time when
no Insight Budget Cell existed for a (report, fy, row, month). The fallback
was: basis = the same row's actual value in (fy - 1), budget = round(basis × 1.10).

That violated the principle "every BUDGET value must come from a document."
This patch finds every (report, fiscal_year, dimension book) tuple that
would have produced fallback values and materialises them as real
Insight Budget Cell documents. After the patch:
  - Existing reports continue to show the same numbers they did before.
  - Those numbers are now real documents the user can edit, audit, or delete.
  - The runtime fallback in _load_budget is removed.

Migration strategy:
  For every Insight Budget Book where (report, fiscal_year) currently has
  ZERO cells, but a basis-year run (fy - 1) would have produced non-zero
  actuals, we generate cells equivalent to the old FY-1 × 1.10 logic.

  Books that already have ANY cells are left untouched — admins have
  customised them, and we don't want to clobber that.

Audit log:
  Every conversion writes one line to the "Neotec Insight: migration"
  error log so admins can review afterwards which books were populated
  and by how many cells.

Idempotent — re-running does nothing because the second pass finds cells
already populated and skips. (Frappe's Patch Log prevents re-execution
anyway, but the inner logic is also self-protecting.)
"""
from __future__ import annotations

import frappe


def execute() -> None:
    if not frappe.db.exists("DocType", "Insight Budget Book"):
        return
    if not frappe.db.exists("DocType", "Insight Budget Cell"):
        return

    # Use the actual function from the api module — single source of truth.
    # We deliberately reuse derive_basis_year_monthly so any future change
    # to how basis values are read is reflected here automatically.
    from neotec_insight.neotec_insight.api.report import (
        _derive_basis_year_monthly,
        _resolve_report_doc,
    )

    # v1.9.59 defensive guard — `custom_dimension_fieldname` is a v1.9.52
    # addition to Insight Budget Book. On sites whose DocType was created
    # at an older release and the schema sync hasn't yet run during this
    # migrate pass, the column may not exist in `tabInsight Budget Book`
    # even though our DocType JSON declares it. Querying it directly
    # crashes with "Unknown column 'custom_dimension_fieldname' in 'field
    # list'". We check first and degrade gracefully — books that pre-date
    # the field can't have a custom-dimension scope anyway, so the field
    # is None for all of them and including it would yield no additional
    # information. The schema sync later in this same migrate pass will
    # add the column; the next patch run picks it up correctly.
    try:
        bb_cols = set(frappe.db.get_table_columns("Insight Budget Book"))
    except Exception:
        bb_cols = set()
    has_cd_field = "custom_dimension_fieldname" in bb_cols

    fields_to_select = ["name", "report", "fiscal_year", "dimension_type", "dimension_value"]
    if has_cd_field:
        fields_to_select.append("custom_dimension_fieldname")

    books = frappe.get_all(
        "Insight Budget Book",
        fields=fields_to_select,
        limit_page_length=0,
    )
    if not books:
        return

    populated = 0
    skipped = 0

    # v1.9.59 hotfix-2 — `frappe.log_error()` API treats the first
    # positional arg as the title in current Frappe versions, and titles
    # have a 140-char limit. Long migration audit messages were being
    # passed as the title and triggering CharacterLengthExceededError on
    # every successful conversion, which then cascaded through the
    # exception handler. Two changes here:
    #   1. Always pass title= and message= as keyword args with a short,
    #      fixed title that fits within Frappe's 140-char limit.
    #   2. Wrap log_error in a defensive try/except — audit logging must
    #      never abort the migration. If logging fails, we silently
    #      continue; the next migrate pass remains idempotent.
    def _log(message: str) -> None:
        try:
            frappe.log_error(
                message=message,
                title="Neotec Insight migration v1.9.56",
            )
        except Exception:
            pass  # audit log is best-effort; never block migration

    for book in books:
        try:
            existing = frappe.db.count("Insight Budget Cell", {"book": book["name"]})
            if existing > 0:
                skipped += 1
                continue

            report_name = book["report"]
            fy = int(book["fiscal_year"])
            try:
                report_doc = _resolve_report_doc(report_name)
            except Exception:
                skipped += 1
                continue

            import json as _json
            definition = _json.loads(report_doc.definition_json or "{}")
            rows = definition.get("rows", []) or []

            # Dimension scope on the book.
            dt = book.get("dimension_type") or "total"
            dv = book.get("dimension_value") or ""
            cc = dv if dt == "cost_center" else None
            pj = dv if dt == "project" else None
            dp = dv if dt == "department" else None
            br = dv if dt == "branch" else None
            cd_field = book.get("custom_dimension_fieldname") if dt == "custom" else None
            cd_value = dv if dt == "custom" else None

            basis_by_row = _derive_basis_year_monthly(
                report=report_name,
                basis_year=fy - 1,
                rows=rows,
                company=report_doc.company,
                cost_center=cc, project=pj, department=dp, branch=br,
                custom_dimension_fieldname=cd_field,
                custom_dimension_value=cd_value,
            )

            # Generate cells using the legacy logic: × 1.10, rounded.
            cells_to_write = []
            for r in rows:
                if r.get("kind") != "source":
                    continue
                rk = r.get("key")
                if not rk:
                    continue
                basis_monthly = basis_by_row.get(rk, {})
                for m in range(12):
                    v = basis_monthly.get(m, basis_monthly.get(str(m), 0.0))
                    try:
                        derived = round(float(v) * 1.10)
                    except (TypeError, ValueError):
                        derived = 0
                    if derived == 0 and float(v or 0) == 0:
                        continue
                    cells_to_write.append((rk, m, float(derived)))

            if not cells_to_write:
                skipped += 1
                continue

            for (rk, m, amt) in cells_to_write:
                cell = frappe.new_doc("Insight Budget Cell")
                cell.book = book["name"]
                cell.report = report_name
                cell.fiscal_year = fy
                cell.row_key = rk
                cell.month = m
                cell.amount = amt
                cell.segment = "total"
                cell.insert(ignore_permissions=True)

            populated += 1
            _log(
                f"populated book '{book['name']}' "
                f"(report={report_name}, FY={fy}, scope={dt}:{dv}) "
                f"with {len(cells_to_write)} cells derived from FY-1 × 1.10"
            )
        except Exception as e:
            skipped += 1
            _log(f"failed on book '{book.get('name')}': {e}")

    frappe.db.commit()
    _log(
        f"summary: {populated} books populated, "
        f"{skipped} books skipped (already had cells, no basis data, or failed)."
    )
