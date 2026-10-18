"""Variance commentary per report row and fiscal year (v1.9.36).

v2.92.0 — moved unchanged out of api/report.py, which re-exports every
name so the `api.report.<name>` endpoint addresses keep working."""

import frappe
from frappe.utils import cint
from neotec_insight.neotec_insight.api.report_shared import _require_read, _resolve_report_doc


# ───────────────────────────────────────────────────────────────────────────
# Variance commentary (v1.9.36) — per (report, row_key, fiscal_year)
# ───────────────────────────────────────────────────────────────────────────

@frappe.whitelist()
def list_variance_notes(report: str, fiscal_year: str | int) -> list[dict]:
    """Return all commentary notes for a report+fiscal year.

    Used by the dashboard variance panel and the Management Pack export to
    decorate rows with the user's 'why' explanation.
    """
    _require_read()
    fy = cint(fiscal_year)
    if not report or not fy:
        return []
    # Resolve the report doc name (caller may pass slug).
    doc = _resolve_report_doc(report)
    rows = frappe.get_all(
        "Insight Variance Note",
        filters={"report": doc.name, "fiscal_year": fy},
        fields=["name", "row_key", "commentary", "modified", "modified_by"],
        limit_page_length=0,
    )
    out = []
    for r in rows:
        commentary = (r.get("commentary") or "").strip()
        if not commentary:
            continue
        out.append({
            "name": r["name"],
            "row_key": r["row_key"],
            "commentary": commentary,
            "modified": str(r.get("modified") or ""),
            "modified_by": r.get("modified_by") or "",
        })
    return out


@frappe.whitelist(methods=["POST"])
def save_variance_note(
    report: str,
    row_key: str,
    fiscal_year: str | int,
    commentary: str = "",
) -> dict:
    """Upsert (or delete) a variance note for one row of a report+year.

    Empty/whitespace commentary deletes the note — keeps storage clean and
    matches the UX: clearing the textarea removes the row's commentary entirely.
    """
    _require_read()
    fy = cint(fiscal_year)
    if not report or not row_key or not fy:
        frappe.throw("report, row_key and fiscal_year are required.")
    doc = _resolve_report_doc(report)
    row_key = (row_key or "").strip()
    text = (commentary or "").strip()

    existing = frappe.get_all(
        "Insight Variance Note",
        filters={"report": doc.name, "row_key": row_key, "fiscal_year": fy},
        pluck="name",
        limit_page_length=1,
        order_by="modified desc",
    )

    if not text:
        # Empty -> delete if present.
        if existing:
            frappe.delete_doc("Insight Variance Note", existing[0], ignore_permissions=False)
            frappe.db.commit()
        return {"deleted": True, "row_key": row_key}

    if existing:
        note = frappe.get_doc("Insight Variance Note", existing[0])
        note.commentary = text
        note.save(ignore_permissions=False)
    else:
        note = frappe.new_doc("Insight Variance Note")
        note.report = doc.name
        note.row_key = row_key
        note.fiscal_year = fy
        note.commentary = text
        note.insert(ignore_permissions=False)
    frappe.db.commit()
    return {
        "name": note.name,
        "row_key": row_key,
        "commentary": text,
        "modified": str(note.modified or ""),
        "modified_by": note.modified_by or "",
    }
