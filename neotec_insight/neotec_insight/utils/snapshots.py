"""Automatic backup snapshots (v2.89.0).

The allocation head counts were lost with no backup to restore from, because
the only backup was a file someone had to remember to download — and the one
that existed predated the head counts joining it. This makes the backup
automatic and keeps several generations of it on the site itself:

* daily (skipped when nothing changed since the last one),
* before every migrate (so an upgrade can always be undone),
* before every restore (so a restore can always be undone),
* before bulk changes that delete or overwrite allocation figures.

Every snapshot also compares record counts with the previous full snapshot.
A table that suddenly empties raises an alert on the snapshot and an Error
Log — the loss is noticed the day it happens, not weeks later.

Snapshots live in the site database (Insight Backup Snapshot), so the Frappe
Cloud site backups carry them too. Nothing here may ever raise into a
migrate or a scheduler run: every entry point catches and logs.
"""

from __future__ import annotations

import json

import frappe

from neotec_insight.neotec_insight.utils.backup_engine import (
    app_version, build_bundle, current_counts, live_plan,
)
from neotec_insight.neotec_insight.utils.config_backup_registry import count_drops
from neotec_insight.neotec_insight.utils.snapshot_core import (
    alert_text, build_index, decode, encode, fingerprint, to_prune,
)

DT = "Insight Backup Snapshot"
FILE_PREFIX = "insight-snapshot-"


def _store_ready() -> bool:
    try:
        return bool(frappe.db.table_exists(DT))
    except Exception:
        return False


def _previous_full(kind: str | None = None) -> dict | None:
    filters = {"sections": ["is", "not set"]}
    if kind:
        filters["snapshot_type"] = kind
    rows = frappe.get_all(DT, filters=filters,
                          fields=["name", "fingerprint", "counts_json", "creation"],
                          order_by="creation desc", limit_page_length=1)
    return rows[0] if rows else None


def take(kind: str = "manual", label: str | None = None, sections=None,
         dedupe: bool = False, commit: bool = True) -> dict:
    """Take a snapshot. Returns {name, skipped, alert, records}."""
    sections = sorted(sections) if sections else None
    bundle = build_bundle(sections)
    fp = fingerprint(bundle["data"])
    records = sum(bundle["counts"].values())
    label = label or {
        "scheduled": "Daily", "pre_migrate": f"Before migrate (code v{app_version()})",
        "post_migrate": "After migrate", "pre_restore": "Before restore",
        "pre_change": "Before bulk change", "manual": "Manual",
    }.get(kind, kind)

    if not _store_ready():
        # First migrate onto 2.89.0: the snapshot table does not exist yet.
        # Keep the copy as a private file; after_migrate adopts it.
        return _store_as_file(kind, label, bundle, commit)

    if dedupe:
        prev = (_previous_full(kind) if not sections else
                (frappe.get_all(DT, filters={"snapshot_type": kind, "sections": json.dumps(sections)},
                                fields=["name", "fingerprint"], order_by="creation desc",
                                limit_page_length=1) or [None])[0])
        if prev and prev.get("fingerprint") == fp:
            return {"name": prev["name"], "skipped": True, "alert": "", "records": records}

    alert = ""
    if not sections:
        prev = _previous_full()
        if prev and prev.get("counts_json"):
            try:
                drops = count_drops(json.loads(prev["counts_json"]), bundle["counts"])
            except Exception:
                drops = []
            if drops:
                alert = alert_text(drops)
                frappe.log_error(title="Insight: records disappeared since the last snapshot",
                                 message=f"Compared with snapshot {prev['name']}:\n{alert}")

    packed, size_kb = encode(bundle)
    doc = frappe.get_doc({
        "doctype": DT, "snapshot_type": kind, "label": label[:140],
        "app_version": app_version(), "record_count": records, "size_kb": size_kb,
        "fingerprint": fp, "sections": json.dumps(sections) if sections else "",
        "alert": alert, "counts_json": json.dumps(bundle["counts"]),
        "index_json": json.dumps(build_index(bundle)), "payload": packed,
    })
    doc.flags.ignore_permissions = True
    doc.insert(ignore_permissions=True)
    prune()
    if commit:
        frappe.db.commit()
    return {"name": doc.name, "skipped": False, "alert": alert, "records": records}


def _store_as_file(kind, label, bundle, commit) -> dict:
    packed, _ = encode(bundle)
    stamp = frappe.utils.now_datetime().strftime("%Y%m%d-%H%M%S")
    f = frappe.get_doc({
        "doctype": "File", "file_name": f"{FILE_PREFIX}{kind}-{stamp}.txt",
        "is_private": 1, "content": json.dumps({"kind": kind, "label": label, "payload": packed}),
    })
    f.flags.ignore_permissions = True
    f.insert(ignore_permissions=True)
    if commit:
        frappe.db.commit()
    return {"name": f.name, "skipped": False, "alert": "", "as_file": True,
            "records": sum(bundle["counts"].values())}


def adopt_pending_files() -> int:
    """Turn snapshot files written before the table existed into snapshots."""
    if not _store_ready():
        return 0
    n = 0
    for f in frappe.get_all("File", filters={"file_name": ["like", f"{FILE_PREFIX}%"],
                                             "attached_to_doctype": ["in", ["", None]]},
                            fields=["name"], limit_page_length=50):
        try:
            fd = frappe.get_doc("File", f["name"])
            wrap = json.loads(fd.get_content())
            bundle = decode(wrap["payload"])
            doc = frappe.get_doc({
                "doctype": DT, "snapshot_type": wrap.get("kind") or "pre_migrate",
                "label": (wrap.get("label") or "")[:140], "app_version": bundle.get("version", ""),
                "record_count": sum((bundle.get("counts") or {}).values()),
                "size_kb": round(len(wrap["payload"]) / 1024.0, 1),
                "fingerprint": fingerprint(bundle.get("data")), "sections": "",
                "counts_json": json.dumps(bundle.get("counts") or {}),
                "index_json": json.dumps(build_index(bundle)), "payload": wrap["payload"],
            })
            doc.insert(ignore_permissions=True)
            fd.db_set({"attached_to_doctype": DT, "attached_to_name": doc.name})
            n += 1
        except Exception:
            frappe.log_error(title="Insight: could not adopt a snapshot file",
                             message=frappe.get_traceback())
    if n:
        frappe.db.commit()
    return n


def prune() -> list[str]:
    rows = frappe.get_all(DT, fields=["name", "snapshot_type", "pinned", "creation"],
                          limit_page_length=0)
    gone = to_prune(rows)
    for nm in gone:
        frappe.delete_doc(DT, nm, ignore_permissions=True, force=1, delete_permanently=True)
    return gone


def load(name: str) -> dict:
    return decode(frappe.db.get_value(DT, name, "payload"))


# ── hook entry points: never raise ───────────────────────────────────────────

def _safe(fn, title):
    try:
        return fn()
    except Exception:
        try:
            frappe.db.rollback()
            frappe.log_error(title=title, message=frappe.get_traceback())
        except Exception:
            pass
        return None


def daily():
    _safe(lambda: take("scheduled", dedupe=True), "Insight: daily snapshot failed")


def before_migrate():
    _safe(lambda: take("pre_migrate"), "Insight: pre-migrate snapshot failed")


def after_migrate():
    """Adopt file snapshots, then check whether the migrate lost records."""
    _safe(adopt_pending_files, "Insight: snapshot adoption failed")

    def check():
        prev = _previous_full("pre_migrate")
        if not prev or not prev.get("counts_json"):
            return
        drops = count_drops(json.loads(prev["counts_json"]), current_counts(live_plan()))
        if drops:
            take("post_migrate", label="After migrate — records missing", dedupe=False)
            frappe.log_error(title="Insight: records missing after migrate",
                             message=f"Compared with {prev['name']} (taken just before):\n"
                                     + alert_text(drops)
                                     + "\n\nRestore from Insight > Backup > Snapshots.")
    _safe(check, "Insight: post-migrate check failed")

    def first():
        if _store_ready() and not _previous_full():
            take("manual", label=f"First snapshot (v{app_version()})")
    _safe(first, "Insight: first snapshot failed")


def guard(kind: str, sections=None, label: str | None = None):
    """Snapshot before a risky write. Returns the snapshot name, or None if it
    could not be taken (the write still proceeds; the failure is logged)."""
    r = _safe(lambda: take(kind, label=label, sections=sections, dedupe=True, commit=True),
              f"Insight: {kind} snapshot failed")
    return (r or {}).get("name")
