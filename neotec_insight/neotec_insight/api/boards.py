"""Dashboard designer (v2.99.0): saved boards and the data behind each visual.

A visual's source is an Analysis Pack analysis or a saved Studio report. Each
runs with the viewer's own permissions; page filters (company, period, cost
centre) are applied to a Studio report only where its document has the field.
"""
from __future__ import annotations

import json

import frappe
from frappe import _

from neotec_insight.neotec_insight.utils.access import require_finance_read

DT = "Insight Board"
VISUALS = ("kpi", "bar", "line", "area", "pie", "waterfall", "table", "pivot")
MAX_VISUALS = 30
DATE_FIELDS = ("posting_date", "transaction_date", "date", "creation")


def _roles(text: str | None) -> set[str]:
    return {r.strip() for r in (text or "").split(",") if r.strip()}


def _can_view(doc) -> bool:
    user = frappe.session.user
    roles = set(frappe.get_roles(user))
    return (user in ("Administrator", doc.owner) or "System Manager" in roles
            or bool(_roles(doc.shared_roles) & roles))


def _can_edit(doc) -> bool:
    user = frappe.session.user
    return user in ("Administrator", doc.owner) or "System Manager" in frappe.get_roles(user)


def clean_layout(layout) -> list[dict]:
    """Keep only well-formed visuals inside the 12-column grid."""
    out = []
    for v in (layout or [])[:MAX_VISUALS]:
        if not isinstance(v, dict) or v.get("type") not in VISUALS:
            continue
        src = v.get("source") or {}
        if src.get("kind") not in ("analysis", "studio") or not (src.get("key") or src.get("report")):
            continue
        w = max(2, min(12, int(v.get("w") or 4)))
        out.append({"id": str(v.get("id") or len(out))[:40], "title": str(v.get("title") or "")[:120],
                    "type": v["type"], "source": {k: src.get(k) for k in ("kind", "key", "report")},
                    "x": max(0, min(12 - w, int(v.get("x") or 0))), "y": max(0, int(v.get("y") or 0)),
                    "w": w, "h": max(2, min(12, int(v.get("h") or 3))),
                    "options": {k: v.get("options", {}).get(k) for k in ("row", "column", "value", "measure")
                                if isinstance(v.get("options"), dict)}})
    return out


@frappe.whitelist()
def list_boards():
    require_finance_read()
    out = []
    for r in frappe.get_all(DT, fields=["name", "title", "owner", "shared_roles", "modified"],
                            order_by="modified desc", limit_page_length=200):
        if _can_view(frappe._dict(r)):
            out.append({"name": r["name"], "title": r["title"], "owner": r["owner"],
                        "mine": r["owner"] == frappe.session.user, "modified": str(r["modified"])[:16]})
    return out


@frappe.whitelist()
def get_board(name=None):
    require_finance_read()
    doc = frappe.get_doc(DT, name)
    if not _can_view(doc):
        frappe.throw(_("This dashboard is not shared with you."), frappe.PermissionError)
    return {"name": doc.name, "title": doc.title, "shared_roles": doc.shared_roles or "",
            "layout": json.loads(doc.layout_json or "[]"), "filters": json.loads(doc.filters_json or "{}"),
            "can_edit": _can_edit(doc), "owner": doc.owner}


@frappe.whitelist(methods=["POST"])
def save_board(name=None, title=None, layout=None, filters=None, shared_roles=None):
    require_finance_read()
    layout = clean_layout(json.loads(layout) if isinstance(layout, str) else layout)
    filters = json.loads(filters) if isinstance(filters, str) else (filters or {})
    filters = {k: str(filters.get(k) or "")[:140] for k in ("company", "from_date", "to_date", "cost_center")}
    if name and frappe.db.exists(DT, name):
        doc = frappe.get_doc(DT, name)
        if not _can_edit(doc):
            frappe.throw(_("Only the owner can change this dashboard."), frappe.PermissionError)
    else:
        doc = frappe.new_doc(DT)
    doc.title = (title or "").strip()[:140] or _("Untitled dashboard")
    doc.layout_json = json.dumps(layout)
    doc.filters_json = json.dumps(filters)
    doc.shared_roles = ", ".join(sorted(_roles(shared_roles)))[:500]
    doc.save(ignore_permissions=True)
    return {"name": doc.name}


@frappe.whitelist(methods=["POST"])
def delete_board(name=None):
    doc = frappe.get_doc(DT, name)
    if not _can_edit(doc):
        frappe.throw(_("Only the owner can delete this dashboard."), frappe.PermissionError)
    frappe.delete_doc(DT, name, ignore_permissions=True)
    return {"ok": True}


def _studio_dataset(report: str, f: dict) -> dict:
    from neotec_insight.neotec_insight.api import studio
    cfg = json.loads(frappe.db.get_value("Studio Report", report, "config_json") or "{}")
    dt = cfg.get("doctype")
    if not dt:
        frappe.throw(_("Studio report {0} not found.").format(report))
    meta = frappe.get_meta(dt)
    extra = []
    if f.get("company") and meta.get_field("company"):
        extra.append({"field": "company", "op": "=", "value": f["company"]})
    if f.get("cost_center") and meta.get_field("cost_center"):
        extra.append({"field": "cost_center", "op": "=", "value": f["cost_center"]})
    date_field = next((d for d in DATE_FIELDS if d == "creation" or meta.get_field(d)), None)
    if date_field and f.get("from_date") and f.get("to_date"):
        extra.append({"field": date_field, "op": "between", "value": [f["from_date"], f["to_date"]]})
    cfg["filters"] = [x for x in (cfg.get("filters") or []) if not x.get("ask")] + extra
    res = studio.run_query(cfg)
    cols, rows = studio.flat_result(res)           # v2.99.1 — grouped reports too
    return {"key": report, "title": report, "figures": [], "columns": cols, "rows": rows,
            "chart": None, "notes": [], "applied": [x["field"] for x in extra]}


@frappe.whitelist()
def run_visual(source=None, filters=None):
    """Data for one visual, in the Analysis Pack dataset shape."""
    require_finance_read()
    src = json.loads(source) if isinstance(source, str) else (source or {})
    f = json.loads(filters) if isinstance(filters, str) else (filters or {})
    if src.get("kind") == "analysis":
        from neotec_insight.neotec_insight.api.analysis_pack import compute
        d = compute(src.get("key"), f.get("company") or None, f.get("from_date") or None, f.get("to_date") or None)
        d["applied"] = ["company", "period"]
        return d
    if src.get("kind") == "studio":
        return _studio_dataset(src.get("report"), f)
    frappe.throw(_("Unknown visual source."))


@frappe.whitelist()
def sources():
    """What a visual can show: the analyses and the Studio reports."""
    require_finance_read()
    from neotec_insight.neotec_insight.api.analysis_pack import list_analyses
    return {"analyses": list_analyses(),
            "studio": frappe.get_all("Studio Report", fields=["name", "title"], order_by="title asc",
                                     limit_page_length=500)}
