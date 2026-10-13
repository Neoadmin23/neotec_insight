"""Backup → Recover (v2.89.5).

Bring back what was deleted or overwritten, from the three places Frappe and
Insight keep it:

* Version history — every save of a Report Definition (track_changes) keeps
  the previous definition;
* the Deleted Document log — every record removed with delete_doc;
* automatic snapshots (from v2.89.0).

Report definitions get their own view because a report is rewritten in
place: its earlier versions are listed with what tells them apart (rows,
allocation rows, the rows allocation rules read their pool from, the last
formula) and one can be put back over the current one.
"""

from __future__ import annotations

import hashlib
import json

import frappe
from frappe import _
from frappe.utils import cint

RD = "Insight Report Definition"
MODULE = "Neotec Insight"
VOLATILE = {"creation", "modified", "modified_by", "owner", "docstatus", "idx", "doctype",
            "_user_tags", "_comments", "_assign", "_liked_by", "_seen", "name"}
CARRY = ("definition_json", "column_schema_json", "filter_schema_json", "report_type",
         "comparison_mode", "prior_years", "primary_budget_axis", "presentation_format",
         "default_expand", "hide_zero_accounts", "hide_group_accounts", "description")


def _sm():
    frappe.only_for("System Manager")


def _insight_doctypes() -> list[str]:
    return frappe.get_all("DocType", filters={"module": MODULE, "istable": 0}, pluck="name")


# ── pure: describe a definition ──────────────────────────────────────────────

def summarize_definition(defn, pool_keys=()) -> dict:
    """What tells two versions of a report apart. Pure."""
    if isinstance(defn, str):
        try:
            defn = json.loads(defn or "{}")
        except Exception:
            defn = {}
    rows = (defn or {}).get("rows") or []
    keys = {r.get("key") for r in rows}
    alloc = [r.get("label") or r.get("key") for r in rows
             if r.get("kind") == "allocation" or str(r.get("key", "")).startswith("alc_")]
    last_formula = next((f"{r.get('label')}: {r.get('formula')}" for r in reversed(rows)
                         if r.get("kind") == "formula" and r.get("formula")), "")
    return {"rows": len(rows), "allocation_rows": alloc,
            "has_pool_rows": sorted(k for k in pool_keys if k in keys),
            "missing_pool_rows": sorted(k for k in pool_keys if k and k not in keys),
            "last_formula": last_formula,
            "tail": [r.get("label") or r.get("key") for r in rows[-6:]]}


def _fp(text: str) -> str:
    try:
        text = json.dumps(json.loads(text or "{}"), sort_keys=True)
    except Exception:
        pass
    return hashlib.sha1((text or "").encode("utf-8")).hexdigest()[:12]


def rank_versions(cands: list[dict]) -> list[dict]:
    """Versions holding every pool row first, then more allocation rows, then
    newest. Pure."""
    newest_first = sorted(cands, key=lambda c: str(c.get("when") or ""), reverse=True)
    return sorted(newest_first, key=lambda c: (
        -len(c["summary"]["has_pool_rows"]), len(c["summary"]["missing_pool_rows"]),
        -len(c["summary"]["allocation_rows"])))


# ── report versions ──────────────────────────────────────────────────────────

def _pool_keys(report: str) -> list[str]:
    return sorted({r for r in frappe.get_all(
        "Insight Allocation Rule", filters={"pool_report": report}, pluck="pool_row_key") if r})


def _collect_versions(report: str) -> list[dict]:
    pool_keys = _pool_keys(report)
    current = frappe.db.get_value(RD, report, "definition_json") if frappe.db.exists(RD, report) else None
    cur_fp = _fp(current) if current else None
    seen: dict[str, dict] = {}

    def add(source, ref, when, who, fields: dict):
        text = fields.get("definition_json")
        if not text:
            return
        fp = _fp(text)
        if fp == cur_fp or fp in seen:
            return
        seen[fp] = {"id": fp, "source": source, "ref": ref, "when": str(when or "")[:19],
                    "who": who or "", "fields": fields,
                    "summary": summarize_definition(text, pool_keys)}

    # Version history: each change keeps old and new values.
    for v in frappe.get_all("Version", filters={"ref_doctype": RD, "docname": report},
                            fields=["name", "data", "creation", "owner"],
                            order_by="creation desc", limit_page_length=300):
        try:
            data = json.loads(v["data"] or "{}")
        except Exception:
            continue
        for ch in data.get("changed") or []:
            if len(ch) >= 3 and ch[0] == "definition_json":
                add("version (before change)", v["name"], v["creation"], v["owner"],
                    {"definition_json": ch[1]})
                add("version (after change)", v["name"], v["creation"], v["owner"],
                    {"definition_json": ch[2]})

    # Deleted copies of this report.
    for d in frappe.get_all("Deleted Document",
                            filters={"deleted_doctype": RD, "data": ["like", f"%{report}%"]},
                            fields=["name", "data", "creation", "owner", "deleted_name"],
                            order_by="creation desc", limit_page_length=100):
        try:
            data = json.loads(d["data"] or "{}")
        except Exception:
            continue
        if data.get("name") != report and data.get("slug") != report:
            continue
        add("deleted copy", d["name"], d["creation"], d["owner"],
            {k: data.get(k) for k in CARRY if data.get(k) is not None})

    # Automatic snapshots.
    try:
        from neotec_insight.neotec_insight.utils import snapshots
        if snapshots._store_ready():
            for s in frappe.get_all(snapshots.DT, fields=["name", "creation"],
                                    filters={"sections": ["is", "not set"]},
                                    order_by="creation desc", limit_page_length=15):
                try:
                    b = snapshots.load(s["name"])
                except Exception:
                    continue
                for r in (b.get("data") or {}).get(RD) or []:
                    if r.get("name") == report:
                        add("snapshot", s["name"], s["creation"], "",
                            {k: r.get(k) for k in CARRY if r.get(k) is not None})
    except Exception:
        pass
    return rank_versions(list(seen.values()))


@frappe.whitelist()
def report_versions(report):
    """Earlier versions of a report, best candidate first."""
    _sm()
    cands = _collect_versions(report)
    cur = frappe.db.get_value(RD, report, ["definition_json", "seed_source", "creation"], as_dict=True)
    return {
        "report": report,
        "current": ({"summary": summarize_definition(cur["definition_json"], _pool_keys(report)),
                     "seed_source": cur.get("seed_source"), "created": str(cur.get("creation"))[:19]}
                    if cur else None),
        "versions": [{k: v for k, v in c.items() if k != "fields"} for c in cands],
    }


@frappe.whitelist(methods=["POST"])
def restore_report_version(report, version_id):
    """Put an earlier version back over the current report. Snapshot first."""
    _sm()
    cands = {c["id"]: c for c in _collect_versions(report)}
    c = cands.get(version_id)
    if not c:
        frappe.throw(_("That version is no longer available. Reload the list."))
    from neotec_insight.neotec_insight.utils import snapshots
    pre = snapshots.guard("pre_restore", sections=[RD], label=f"Before restoring report {report}")
    fields = {k: v for k, v in c["fields"].items() if v is not None}
    if frappe.db.exists(RD, report):
        doc = frappe.get_doc(RD, report)
        doc.update(fields)
        doc.flags.ignore_links = True
        doc.save(ignore_permissions=True)
    else:
        doc = frappe.get_doc(dict(fields, doctype=RD, slug=report, report_name=report))
        doc.flags.ignore_links = True
        doc.insert(ignore_permissions=True, set_name=report)
    frappe.db.commit()
    return {"ok": True, "from": c["source"], "when": c["when"], "summary": c["summary"],
            "pre_restore_snapshot": pre}


# ── deleted records of any Insight doctype ───────────────────────────────────

def _title(data: dict) -> str:
    for k in ("title", "report_name", "label", "rule", "slug", "flag", "account", "cost_center"):
        if data.get(k):
            extra = data.get("period_month") or data.get("company") or ""
            return f"{data[k]}{' · ' + str(extra)[:10] if extra else ''}"
    return data.get("name") or ""


@frappe.whitelist()
def deleted_summary():
    """Deleted, not yet restored, Insight records per doctype."""
    _sm()
    dts = _insight_doctypes()
    if not dts:
        return []
    rows = frappe.get_all("Deleted Document",
                          filters={"deleted_doctype": ["in", dts], "restored": 0},
                          fields=["deleted_doctype", "count(name) as n", "min(creation) as first",
                                  "max(creation) as last"],
                          group_by="deleted_doctype", order_by="deleted_doctype asc")
    return [{"doctype": r["deleted_doctype"], "count": r["n"],
             "first": str(r["first"])[:16], "last": str(r["last"])[:16]} for r in rows]


@frappe.whitelist()
def deleted_list(doctype, limit=500):
    _sm()
    if doctype not in _insight_doctypes():
        frappe.throw(_("Not an Insight doctype."))
    out = []
    for d in frappe.get_all("Deleted Document",
                            filters={"deleted_doctype": doctype, "restored": 0},
                            fields=["name", "deleted_name", "data", "creation", "owner"],
                            order_by="creation desc", limit_page_length=cint(limit) or 500):
        try:
            data = json.loads(d["data"] or "{}")
        except Exception:
            data = {}
        out.append({"name": d["name"], "deleted_name": d["deleted_name"],
                    "when": str(d["creation"])[:16], "who": d["owner"], "title": _title(data),
                    "exists_now": bool(frappe.db.exists(doctype, d["deleted_name"]))})
    return out


@frappe.whitelist(methods=["POST"])
def restore_deleted(names, on_conflict="skip"):
    """Restore deleted Insight records. A record whose name is in use again is
    skipped, or with on_conflict=replace written over the current one."""
    _sm()
    names = frappe.parse_json(names) if isinstance(names, str) else (names or [])
    from neotec_insight.neotec_insight.utils import snapshots
    dts = set(_insight_doctypes())
    touched = sorted({frappe.db.get_value("Deleted Document", n, "deleted_doctype") for n in names} & dts)
    pre = snapshots.guard("pre_restore", sections=touched or None, label="Before restoring deleted records")
    out = {"restored": 0, "replaced": 0, "skipped": 0, "errors": [], "pre_restore_snapshot": pre}
    for n in names:
        d = frappe.db.get_value("Deleted Document", n, ["deleted_doctype", "deleted_name", "data", "restored"],
                                as_dict=True)
        if not d or d["restored"] or d["deleted_doctype"] not in dts:
            out["skipped"] += 1
            continue
        try:
            data = json.loads(d["data"] or "{}")
            dt = d["deleted_doctype"]
            if frappe.db.exists(dt, d["deleted_name"]):
                if on_conflict != "replace":
                    out["skipped"] += 1
                    continue
                doc = frappe.get_doc(dt, d["deleted_name"])
                doc.update({k: v for k, v in data.items() if k not in VOLATILE})
                doc.flags.ignore_links = True
                doc.save(ignore_permissions=True)
                out["replaced"] += 1
            else:
                doc = frappe.get_doc(dict({k: v for k, v in data.items() if k not in VOLATILE}, doctype=dt))
                doc.flags.ignore_links = True
                doc.insert(ignore_permissions=True, set_name=d["deleted_name"])
                out["restored"] += 1
            frappe.db.set_value("Deleted Document", n, {"restored": 1, "new_name": d["deleted_name"]})
        except Exception as e:
            out["errors"].append(f"{d['deleted_doctype']} {d['deleted_name']}: {str(e)[:200]}")
    frappe.db.commit()
    return out


# ── account mappings of one report (v2.89.6) ─────────────────────────────────

AFM = "Account Flag Mapping"
MAP_FIELDS = ("report", "account", "is_group_binding", "account_code", "account_name", "flag",
              "dimension_filters_json", "scope_summary", "auto_suggested", "source")


def _deleted_mappings(report: str) -> list[dict]:
    out = []
    for d in frappe.get_all("Deleted Document",
                            filters={"deleted_doctype": AFM, "restored": 0,
                                     "data": ["like", f"%{report}%"]},
                            fields=["name", "data", "creation"], order_by="creation desc",
                            limit_page_length=0):
        try:
            data = json.loads(d["data"] or "{}")
        except Exception:
            continue
        if data.get("report") == report:
            out.append({"dd": d["name"], "when": str(d["creation"])[:16], "data": data})
    return out


def _snapshot_mappings(name: str, report: str) -> list[dict]:
    from neotec_insight.neotec_insight.utils import snapshots
    b = snapshots.load(name)
    return [m for m in (b.get("data") or {}).get(AFM) or [] if m.get("report") == report]


@frappe.whitelist()
def mapping_sources(report):
    """Where this report's account mappings can be brought back from."""
    _sm()
    current = frappe.db.count(AFM, {"report": report})
    sources = []
    dl = _deleted_mappings(report)
    if dl:
        sources.append({"kind": "deleted", "ref": "", "count": len(dl),
                        "when": f"{dl[-1]['when']} → {dl[0]['when']}",
                        "label": "Deleted Document log"})
    try:
        from neotec_insight.neotec_insight.utils import snapshots
        if snapshots._store_ready():
            for s in frappe.get_all(snapshots.DT, fields=["name", "creation", "index_json", "label"],
                                    order_by="creation desc", limit_page_length=60):
                idx = json.loads(s.get("index_json") or "{}")
                if "mappings_by_report" in idx:
                    n = int((idx["mappings_by_report"] or {}).get(report) or 0)
                else:                      # snapshot made before the index carried it
                    try:
                        n = len(_snapshot_mappings(s["name"], report))
                    except Exception:
                        n = 0
                if n:
                    sources.append({"kind": "snapshot", "ref": s["name"], "count": n,
                                    "when": str(s["creation"])[:16], "label": s.get("label") or ""})
    except Exception:
        pass
    return {"report": report, "current": current, "sources": sources}


@frappe.whitelist(methods=["POST"])
def restore_mappings(report, kind, ref=None):
    """Bring a report's account mappings back. Adds what is missing; an account
    already mapped on this report is left as it is. Never deletes."""
    _sm()
    from neotec_insight.neotec_insight.utils import snapshots
    pre = snapshots.guard("pre_restore", sections=[AFM], label=f"Before restoring mappings of {report}")
    if kind == "deleted":
        items = [(x["dd"], x["data"]) for x in _deleted_mappings(report)]
    elif kind == "snapshot" and ref:
        items = [(None, m) for m in _snapshot_mappings(ref, report)]
    else:
        frappe.throw(_("Unknown source."))
    mapped = {(m["account"], m["flag"]) for m in frappe.get_all(
        AFM, filters={"report": report}, fields=["account", "flag"], limit_page_length=0)}
    mapped_accounts = {a for a, _f in mapped}
    out = {"restored": 0, "already_mapped": 0, "errors": [], "pre_restore_snapshot": pre}
    for dd, data in items:
        acct = data.get("account")
        if acct in mapped_accounts:
            out["already_mapped"] += 1
            if dd:
                frappe.db.set_value("Deleted Document", dd, "restored", 1)
            continue
        try:
            doc = frappe.get_doc(dict({k: data.get(k) for k in MAP_FIELDS if k in data}, doctype=AFM))
            doc.flags.ignore_links = True
            name = data.get("name")
            doc.insert(ignore_permissions=True,
                       set_name=name if name and not frappe.db.exists(AFM, name) else None)
            mapped_accounts.add(acct)
            out["restored"] += 1
            if dd:
                frappe.db.set_value("Deleted Document", dd, {"restored": 1, "new_name": doc.name})
        except Exception as e:
            out["errors"].append(f"{acct}: {str(e)[:160]}")
    frappe.db.commit()
    out["now"] = frappe.db.count(AFM, {"report": report})
    return out
