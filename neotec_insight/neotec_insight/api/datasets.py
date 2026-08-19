# Copyright (c) 2026, Neotec Integrated Solution
# Semantic layer (v2.24.0) — 'Insight Dataset' is a governed model over a base
# DocType: named MEASURES (field + aggregation, defined once) and named
# DIMENSIONS, plus base filters baked into every query. Reports and dashboards
# built on a dataset all agree on what "Net Revenue" means, because the
# definition lives in ONE place.
#
# Query execution goes through frappe.get_list (user permissions enforced,
# child tables via parent_doctype) and every fieldname is validated against
# the DocType meta before it is embedded in an aggregate expression.
from __future__ import annotations

import json

import frappe
from frappe import _
from frappe.utils import flt

from .studio import _can_read, _child_parent, _coerce_filters

_AGGS = {"sum", "avg", "min", "max", "count"}


def _require_read() -> None:
    """Refuse a financial read from a user with no ledger access.

    `@frappe.whitelist()` requires a login, not a role, so without this these
    endpoints were callable over `/api/method/...` by any authenticated user,
    including portal users with no business seeing the ledger. Reading GL Entry
    is the right test: ERPNext already restricts it to the accounts roles, so
    this inherits the site's own configuration rather than inventing a second
    permission model.
    """
    if not frappe.has_permission("GL Entry", "read"):
        frappe.throw(
            _("You are not permitted to view financial data."),
            frappe.PermissionError,
        )


def _load(slug):
    if not slug or not frappe.db.exists("Insight Dataset", slug):
        frappe.throw(_("Dataset not found."))
    doc = frappe.get_doc("Insight Dataset", slug)
    cfg = json.loads(doc.config_json or "{}")
    return doc, cfg


def _validated(doctype, cfg):
    """Return (measures, dimensions, base_filters) with every fieldname checked
    against meta — anything unknown is dropped, never passed to SQL."""
    meta = frappe.get_meta(doctype)
    ftype = {f.fieldname: f.fieldtype for f in meta.fields}
    numeric = {fn for fn, ft in ftype.items()
               if ft in ("Currency", "Float", "Int", "Percent", "Duration")}
    measures = []
    for m in (cfg.get("measures") or []):
        field, agg = m.get("field"), (m.get("agg") or "sum").lower()
        key = (m.get("key") or "").strip()
        if agg not in _AGGS or not key or not key.isidentifier():
            continue
        if agg == "count":
            field = field if field in ftype else "name"
        elif field not in numeric:
            continue
        measures.append({"key": key, "field": field, "agg": agg,
                         "label": m.get("label") or key})
    dimensions = [{"field": d.get("field"), "label": d.get("label") or d.get("field")}
                  for d in (cfg.get("dimensions") or [])
                  if d.get("field") in ftype or d.get("field") in ("name", "owner")]
    return measures, dimensions, (cfg.get("filters") or [])


@frappe.whitelist()
def list_datasets():
    _require_read()
    rows = frappe.get_all("Insight Dataset",
                          fields=["slug", "title", "base_doctype", "description", "modified"],
                          order_by="modified desc", limit_page_length=200)
    return [r for r in rows if _can_read(r["base_doctype"])]


@frappe.whitelist()
def save_dataset(dataset=None):
    """Create/update a dataset. config = { measures, dimensions, filters }."""
    if isinstance(dataset, str):
        dataset = json.loads(dataset or "{}")
    title = (dataset.get("title") or "").strip()
    doctype = dataset.get("base_doctype")
    if not title:
        frappe.throw(_("Give the dataset a title."))
    if not doctype or not _can_read(doctype):
        frappe.throw(_("Pick a base document you can read."))
    if not frappe.has_permission("Insight Dataset", "write"):
        frappe.throw(_("Not permitted to save datasets."))
    cfg = dataset.get("config") or {}
    measures, dimensions, base_filters = _validated(doctype, cfg)
    if not measures:
        frappe.throw(_("A dataset needs at least one valid measure."))
    slug = frappe.scrub(title)
    if frappe.db.exists("Insight Dataset", slug):
        doc = frappe.get_doc("Insight Dataset", slug)
    else:
        doc = frappe.new_doc("Insight Dataset")
        doc.slug = slug
    doc.title = title
    doc.base_doctype = doctype
    doc.description = dataset.get("description") or ""
    doc.config_json = json.dumps({"measures": measures, "dimensions": dimensions,
                                  "filters": base_filters})
    doc.save(ignore_permissions=True)
    frappe.db.commit()
    return {"slug": doc.slug, "title": doc.title}


@frappe.whitelist()
def load_dataset(slug=None):
    _require_read()
    doc, cfg = _load(slug)
    if not _can_read(doc.base_doctype):
        frappe.throw(_("You don't have permission to read {0}.").format(doc.base_doctype))
    measures, dimensions, base_filters = _validated(doc.base_doctype, cfg)
    return {"slug": doc.slug, "title": doc.title, "base_doctype": doc.base_doctype,
            "description": doc.description,
            "config": {"measures": measures, "dimensions": dimensions, "filters": base_filters}}


@frappe.whitelist()
def delete_dataset(slug=None):
    if not frappe.has_permission("Insight Dataset", "delete"):
        frappe.throw(_("Not permitted."))
    if slug and frappe.db.exists("Insight Dataset", slug):
        frappe.delete_doc("Insight Dataset", slug)
        frappe.db.commit()
    return {"ok": True}


@frappe.whitelist()
def run_dataset(slug=None, dimension=None, measures=None, filters=None, limit=200):
    """Aggregate the dataset's measures, optionally split by one of its
    dimensions, with extra ad-hoc filters on top of the baked-in base filters.

    Only measure keys and dimension fields DEFINED ON THE DATASET are honoured
    — this is what makes it a governed semantic layer rather than a raw query."""
    _require_read()
    doc, cfg = _load(slug)
    doctype = doc.base_doctype
    if not _can_read(doctype):
        frappe.throw(_("You don't have permission to read {0}.").format(doctype))
    all_measures, all_dims, base_filters = _validated(doctype, cfg)

    if isinstance(measures, str):
        measures = json.loads(measures or "[]")
    picked_keys = set(measures or [m["key"] for m in all_measures])
    use_measures = [m for m in all_measures if m["key"] in picked_keys] or all_measures

    dim = None
    if dimension:
        dim = next((d for d in all_dims if d["field"] == dimension), None)

    if isinstance(filters, str):
        filters = json.loads(filters or "[]")
    flt_list = _coerce_filters(doctype, list(base_filters) + list(filters or []))

    kwargs = {}
    meta = frappe.get_meta(doctype)
    if meta.istable:
        cp = _child_parent(doctype)
        if cp:
            flt_list.append([doctype, "parenttype", "=", cp])
            kwargs["parent_doctype"] = cp

    agg_fields = [f"{m['agg']}(`{m['field']}`) as `{m['key']}`" for m in use_measures]
    fields = ([dim["field"]] if dim else []) + agg_fields
    rows = frappe.get_list(doctype, filters=flt_list, fields=fields,
                           group_by=dim["field"] if dim else None,
                           limit_page_length=min(int(limit or 200), 1000), **kwargs)

    out_rows = []
    for r in rows:
        row = {"key": (r.get(dim["field"]) if dim else _("Total")) or "—"}
        for m in use_measures:
            row[m["key"]] = flt(r.get(m["key"]) or 0, 2)
        out_rows.append(row)
    if dim:
        out_rows.sort(key=lambda x: -abs(x.get(use_measures[0]["key"], 0)))

    return {
        "dataset": doc.slug, "title": doc.title, "base_doctype": doctype,
        "dimension": dim, "measures": use_measures, "rows": out_rows,
        "totals": {m["key"]: flt(sum(r.get(m["key"], 0) for r in out_rows), 2)
                   for m in use_measures},
    }


@frappe.whitelist()
def preview_dataset(base_doctype=None, config=None, dimension=None, limit=50):
    """Run an UNSAVED dataset definition — the wizard's live preview, so the
    user sees real numbers before committing the model. Same validation and
    permission path as run_dataset."""
    _require_read()
    if not base_doctype or not _can_read(base_doctype):
        frappe.throw(_("Pick a base document you can read."))
    if isinstance(config, str):
        config = json.loads(config or "{}")
    measures, dims, base_filters = _validated(base_doctype, config or {})
    if not measures:
        frappe.throw(_("Pick at least one valid measure."))
    dim = next((d for d in dims if d["field"] == dimension), None) if dimension else None
    flt_list = _coerce_filters(base_doctype, base_filters)
    kwargs = {}
    meta = frappe.get_meta(base_doctype)
    if meta.istable:
        cp = _child_parent(base_doctype)
        if cp:
            flt_list.append([base_doctype, "parenttype", "=", cp])
            kwargs["parent_doctype"] = cp
    agg_fields = [f"{m['agg']}(`{m['field']}`) as `{m['key']}`" for m in measures]
    fields = ([dim["field"]] if dim else []) + agg_fields
    rows = frappe.get_list(base_doctype, filters=flt_list, fields=fields,
                           group_by=dim["field"] if dim else None,
                           limit_page_length=min(int(limit or 50), 200), **kwargs)
    out_rows = []
    for r in rows:
        row = {"key": (r.get(dim["field"]) if dim else _("Total")) or "—"}
        for m in measures:
            row[m["key"]] = flt(r.get(m["key"]) or 0, 2)
        out_rows.append(row)
    if dim:
        out_rows.sort(key=lambda x: -abs(x.get(measures[0]["key"], 0)))
    return {"dimension": dim, "measures": measures, "rows": out_rows,
            "totals": {m["key"]: flt(sum(r.get(m["key"], 0) for r in out_rows), 2)
                       for m in measures}}


# ── Report schedules (distribution) ─────────────────────────────────────────

@frappe.whitelist()
def list_schedules(report=None):
    _require_read()
    flt_ = {"report": report} if report else {}
    return frappe.get_all("Insight Report Schedule", filters=flt_,
                          fields=["name", "report", "enabled", "frequency", "weekday",
                                  "day_of_month", "recipients", "whatsapp_numbers",
                                  "file_format", "subject", "last_run", "last_status"],
                          order_by="modified desc", limit_page_length=100)


@frappe.whitelist()
def save_schedule(schedule=None):
    if isinstance(schedule, str):
        schedule = json.loads(schedule or "{}")
    if not frappe.has_permission("Insight Report Schedule", "write"):
        frappe.throw(_("Not permitted."))
    report = schedule.get("report")
    if not report or not frappe.db.exists("Studio Report", report):
        frappe.throw(_("Pick a saved Studio report to schedule."))
    name = schedule.get("name")
    doc = frappe.get_doc("Insight Report Schedule", name) if name and \
        frappe.db.exists("Insight Report Schedule", name) else \
        frappe.new_doc("Insight Report Schedule")
    doc.report = report
    doc.enabled = 1 if schedule.get("enabled", 1) else 0
    doc.frequency = schedule.get("frequency") or "Daily"
    doc.weekday = schedule.get("weekday") or "Sunday"
    doc.day_of_month = int(schedule.get("day_of_month") or 1)
    doc.recipients = schedule.get("recipients") or ""
    doc.whatsapp_numbers = schedule.get("whatsapp_numbers") or ""
    doc.file_format = schedule.get("file_format") or "XLSX"
    doc.subject = schedule.get("subject") or ""
    doc.save(ignore_permissions=True)
    frappe.db.commit()
    return {"name": doc.name}


@frappe.whitelist()
def delete_schedule(name=None):
    if not frappe.has_permission("Insight Report Schedule", "delete"):
        frappe.throw(_("Not permitted."))
    if name and frappe.db.exists("Insight Report Schedule", name):
        frappe.delete_doc("Insight Report Schedule", name)
        frappe.db.commit()
    return {"ok": True}


@frappe.whitelist()
def run_schedule_now(name=None):
    """Manual trigger — same code path as the scheduler, for testing."""
    from neotec_insight.neotec_insight.utils.scheduled_reports import dispatch_schedule
    if not name or not frappe.db.exists("Insight Report Schedule", name):
        frappe.throw(_("Schedule not found."))
    if not frappe.has_permission("Insight Report Schedule", "write"):
        frappe.throw(_("Not permitted."))
    dispatch_schedule(frappe.get_doc("Insight Report Schedule", name))
    return {"ok": True}
