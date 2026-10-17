"""Backup engine (v2.89.0) — the one implementation behind Backup > Export,
Backup > Import, and the automatic snapshots.

Why this exists
---------------
Up to 2.88.x the export walked a hand-maintained list. Every time a feature
added a doctype, the backup silently left it out until someone noticed —
Allocation Entries were missing for months, so restoring a backup brought
back the allocation rule with an empty head-count table. Now:

* What to back up is DISCOVERED from the installed DocTypes of the module
  (plus the doctype folder as a fallback). The registry only labels and
  orders; anything unlabelled is included under "Other (added automatically)".
  A new feature is in the backup the day it ships.
* Restore order is derived from the Link/Table fields, so a record is never
  inserted before what it points to.
* A replace-restore is all-or-nothing: a snapshot is taken first, and if any
  record fails, the whole restore is rolled back instead of leaving the site
  with the old data deleted and only part of the new data inserted.
"""

from __future__ import annotations

import json

import frappe

from neotec_insight.neotec_insight.utils.config_backup_registry import (
    build_plan,
    doctypes_to_clear,
    natural_key,
)

MODULE = "Neotec Insight"
BUNDLE_FORMAT = 2

VOLATILE = {"creation", "modified", "modified_by", "owner", "docstatus", "idx",
            "lft", "rgt", "_user_tags", "_comments", "_assign", "_liked_by",
            "parent", "parentfield", "parenttype", "_seen"}


def app_version() -> str:
    try:
        import neotec_insight
        return getattr(neotec_insight, "__version__", "")
    except Exception:
        return ""


# ── discovery ────────────────────────────────────────────────────────────────

def _live_doctypes() -> list[dict]:
    """Every non-child doctype of the module as installed on this site."""
    rows = frappe.get_all("DocType", filters={"module": MODULE, "istable": 0},
                          fields=["name", "issingle"], limit_page_length=0)
    return [{"name": r["name"], "is_single": bool(r.get("issingle"))} for r in rows]


def _dependencies(names: list[str]) -> dict[str, set]:
    """doctype -> module doctypes it links to, directly or through child tables."""
    wanted = set(names)
    out: dict[str, set] = {}
    for dt in names:
        targets: set[str] = set()
        try:
            meta = frappe.get_meta(dt)
            for df in meta.fields:
                if df.fieldtype == "Link" and df.options in wanted:
                    targets.add(df.options)
                elif df.fieldtype in ("Table", "Table MultiSelect") and df.options:
                    for cdf in frappe.get_meta(df.options).fields:
                        if cdf.fieldtype == "Link" and cdf.options in wanted:
                            targets.add(cdf.options)
        except Exception:
            pass
        out[dt] = targets
    return out


def live_plan() -> list[dict]:
    """The backup plan for this site: registry labels + auto-discovered doctypes,
    in dependency order. Falls back to the static registry if discovery fails."""
    try:
        live = _live_doctypes()
        if live:
            return build_plan(live, _dependencies([d["name"] for d in live]))
    except Exception:
        frappe.log_error(title="Insight backup: doctype discovery failed",
                         message=frappe.get_traceback())
    from neotec_insight.neotec_insight.utils.config_backup_registry import CONFIG_REGISTRY
    return [{"kind": "config", **e} for e in CONFIG_REGISTRY]


# ── export ───────────────────────────────────────────────────────────────────

def _clean(doc):
    if isinstance(doc, dict):
        return {k: ([_clean(x) for x in v] if isinstance(v, list) else v)
                for k, v in doc.items() if k not in VOLATILE}
    return doc


def _table_exists(dt: str) -> bool:
    try:
        return bool(frappe.db.table_exists(dt))
    except Exception:
        return True


def _bulk_rows(dt: str) -> list[dict]:
    """All records of one doctype with their child tables, in 1 + (number of
    table fields) queries — not one query per record.

    v2.89.1 — v2.89.0 loaded each record with frappe.get_doc, which is one
    query per record plus one per child table. That was tolerable for a
    manual export but not once snapshots made the export run daily, before
    every restore and before bulk changes: a site with tens of thousands of
    budget cells kept a database connection busy for minutes, and other
    requests queued behind it."""
    rows = frappe.get_all(dt, fields=["*"], limit_page_length=0, order_by="creation asc")
    out = {r["name"]: _clean(dict(r)) for r in rows}
    try:
        tables = [df for df in frappe.get_meta(dt).fields
                  if df.fieldtype in ("Table", "Table MultiSelect") and df.options]
    except Exception:
        tables = []
    for df in tables:
        for rec in out.values():
            rec[df.fieldname] = []
        if not out:
            continue
        for c in frappe.get_all(df.options, fields=["*"],
                                filters={"parenttype": dt, "parentfield": df.fieldname},
                                limit_page_length=0, order_by="idx asc"):
            parent = out.get(c.get("parent"))
            if parent is not None:
                row = _clean(dict(c))
                row["doctype"] = df.options
                parent[df.fieldname].append(row)
    return list(out.values())


def build_bundle(sections=None, plan: list[dict] | None = None) -> dict:
    """The backup bundle. `sections` = list of doctypes to include (None = all).
    Never raises for one bad doctype: it is recorded under `errors` instead, so
    a broken area can never stop the rest being saved."""
    plan = plan or live_plan()
    sel = set(sections) if sections else None
    data: dict[str, list] = {}
    errors: list[str] = []
    for entry in plan:
        dt = entry["doctype"]
        if sel is not None and dt not in sel:
            continue
        try:
            if entry.get("is_single"):
                data[dt] = [_clean(frappe.get_doc(dt).as_dict())]
                continue
            if not _table_exists(dt):
                continue
            data[dt] = _bulk_rows(dt)
        except Exception as e:
            errors.append(f"{dt}: {str(e)[:200]}")
    return {
        "app": "neotec_insight",
        "format": BUNDLE_FORMAT,
        "version": app_version(),
        "exported_at": frappe.utils.now(),
        "site": getattr(frappe.local, "site", ""),
        "sections": sorted(data.keys()),
        "order": [e["doctype"] for e in plan if e["doctype"] in data],
        "counts": {k: len(v) for k, v in data.items()},
        "errors": errors,
        "data": data,
    }


def current_counts(plan: list[dict] | None = None) -> dict[str, int]:
    out = {}
    for entry in plan or live_plan():
        dt = entry["doctype"]
        try:
            out[dt] = (1 if entry.get("is_single") else frappe.db.count(dt))
        except Exception:
            out[dt] = 0
    return out


# ── restore ──────────────────────────────────────────────────────────────────

def _insert(dt: str, d: dict):
    doc = frappe.get_doc(d)
    # A bundle is restored as a whole; records may point at each other in any
    # order (self-links, cycles) and at masters whose names are unchanged on
    # the same site. Link validation would only turn those into failures.
    doc.flags.ignore_links = True
    doc.flags.ignore_permissions = True
    doc.flags.from_backup_restore = True
    doc.insert(ignore_permissions=True, set_name=d.get("name"))


def restore_bundle(payload: dict, mode: str = "merge", sections=None,
                   atomic: bool = True) -> dict:
    """Restore a bundle.

    merge   — insert what is missing, update what exists (matched by natural
              key where names are random, else by name). Nothing is deleted.
    replace — for each doctype the bundle carries, the site ends up holding
              exactly the bundle's records: missing ones inserted, matching
              ones updated in place, the rest deleted. Doctypes the bundle
              does not carry are not touched.

    atomic (default) — if any record fails, everything is rolled back and the
    site is exactly as it was; the summary says so and lists the errors.
    """
    if not payload or "data" not in payload:
        frappe.throw("Invalid configuration bundle.")
    if mode not in ("merge", "replace"):
        frappe.throw("mode must be merge or replace.")
    data = payload["data"] or {}
    if sections:
        keep = set(sections)
        data = {k: v for k, v in data.items() if k in keep}

    plan = live_plan()
    known = {e["doctype"]: e for e in plan}
    order = [e["doctype"] for e in plan if not e.get("is_single") and e["doctype"] in data]
    singles = [e["doctype"] for e in plan if e.get("is_single") and e["doctype"] in data]
    unknown = sorted(k for k in data if k not in known)

    summary = {"mode": mode, "inserted": {}, "updated": {}, "deleted": {}, "failed": {},
               "errors": [], "skipped_unknown": unknown, "rolled_back": False,
               "from_version": payload.get("version", ""), "atomic": bool(atomic)}

    def fail(dt, name, e):
        summary["failed"][dt] = summary["failed"].get(dt, 0) + 1
        if len(summary["errors"]) < 80:
            summary["errors"].append(f"{dt} [{name}]: {str(e)[:240]}")

    def match(dt, d):
        """Existing record this bundle row corresponds to, or None."""
        nk = natural_key(dt, d)
        if nk:
            hit = frappe.db.get_value(dt, nk, "name")
            if hit:
                return hit
        if d.get("name") and frappe.db.exists(dt, d["name"]):
            return d["name"]
        return None

    # Pair every bundle row with the record it updates, before anything is
    # changed, so replace can work out what to delete.
    matched: dict[str, list] = {}
    for dt in order:
        pairs = []
        for raw in (data.get(dt) or []):
            d = dict(raw)
            d["doctype"] = dt
            try:
                pairs.append((d, match(dt, d)))
            except Exception as e:
                fail(dt, d.get("name", "?"), e)
        matched[dt] = pairs

    # replace: records of the bundle's doctypes that the bundle does not hold
    # are removed. Records it DOES hold are updated in place rather than
    # deleted and re-inserted, so their attachments, comments and version
    # history survive the restore.
    if mode == "replace":
        for dt in reversed(doctypes_to_clear(order, data)):
            keep = {name for _d, name in matched.get(dt, []) if name}
            n = 0
            for nm in frappe.get_all(dt, pluck="name", limit_page_length=0):
                if nm in keep:
                    continue
                try:
                    frappe.delete_doc(dt, nm, force=1, ignore_permissions=True,
                                      ignore_on_trash=True)
                    n += 1
                except Exception as e:
                    fail(dt, nm, e)
            summary["deleted"][dt] = n

    for dt in order:
        ins = upd = 0
        for d, existing in matched.get(dt, []):
            try:
                if existing and frappe.db.exists(dt, existing):
                    doc = frappe.get_doc(dt, existing)
                    doc.update({k: v for k, v in d.items() if k not in ("doctype", "name")})
                    doc.flags.ignore_links = True
                    doc.flags.from_backup_restore = True
                    doc.save(ignore_permissions=True)
                    upd += 1
                else:
                    _insert(dt, d)
                    ins += 1
            except Exception as e:
                fail(dt, d.get("name", "?"), e)
        summary["inserted"][dt] = ins
        summary["updated"][dt] = upd

    for dt in singles:
        rows = data.get(dt) or []
        if not rows:
            continue
        try:
            doc = frappe.get_doc(dt)
            src = {k: v for k, v in dict(rows[0]).items() if k not in ("doctype", "name")}
            doc.update(src)
            doc.flags.ignore_links = True
            doc.save(ignore_permissions=True)
            summary["updated"][dt] = 1
        except Exception as e:
            fail(dt, dt, e)

    if summary["failed"] and atomic:
        frappe.db.rollback()
        summary["rolled_back"] = True
        summary["inserted"], summary["updated"], summary["deleted"] = {}, {}, {}
    else:
        frappe.db.commit()
        # v2.90.0 — links are not validated during a restore (records may
        # point at each other in any order). Afterwards, report every link
        # that points at nothing, so a cross-site or partial restore cannot
        # leave silent orphans.
        try:
            summary["orphans"] = find_orphans(order, known)
        except Exception:
            summary["orphans"] = {}
    src_v, here_v = str(payload.get("version") or ""), app_version()
    if src_v and here_v and src_v.split(".")[:2] != here_v.split(".")[:2]:
        summary["version_note"] = (f"This bundle was made by v{src_v}; this site runs v{here_v}. "
                                   "Check the restored areas.")
    return summary


def find_orphans(doctypes: list[str], known: dict) -> dict[str, int]:
    """{"Doctype.field": count} of Link values (to Insight doctypes) that name
    a record which does not exist."""
    out: dict[str, int] = {}
    names_cache: dict[str, set] = {}
    for dt in doctypes:
        for df in frappe.get_meta(dt).fields:
            if df.fieldtype != "Link" or df.options not in known or known[df.options].get("is_single"):
                continue
            vals = {v for v in frappe.get_all(dt, pluck=df.fieldname, distinct=True,
                                               limit_page_length=0) if v}
            if not vals:
                continue
            if df.options not in names_cache:
                names_cache[df.options] = set(frappe.get_all(df.options, pluck="name", limit_page_length=0))
            missing = vals - names_cache[df.options]
            if missing:
                out[f"{dt}.{df.fieldname}"] = len(missing)
    return out


def bundle_from_json(payload) -> dict:
    if isinstance(payload, str):
        payload = json.loads(payload)
    return payload
