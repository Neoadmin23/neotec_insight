"""Backup > Snapshots endpoints (v2.89.0). System Manager only."""

from __future__ import annotations

import json

import frappe
from frappe import _
from frappe.utils import cint

from neotec_insight.neotec_insight.utils import snapshots
from neotec_insight.neotec_insight.utils.backup_engine import (
    current_counts, live_plan, restore_bundle,
)

DT = snapshots.DT
LIST_FIELDS = ["name", "snapshot_type", "label", "app_version", "pinned", "record_count",
               "size_kb", "sections", "alert", "creation", "fingerprint"]


def _sm():
    frappe.only_for("System Manager")


def _ready():
    if not snapshots._store_ready():
        frappe.throw(_("The snapshot store is not installed yet. Run Migrate Database."))


@frappe.whitelist()
def list_snapshots(limit=80):
    _sm()
    if not snapshots._store_ready():
        return {"ready": False, "rows": []}
    rows = frappe.get_all(DT, fields=LIST_FIELDS, order_by="creation desc",
                          limit_page_length=cint(limit) or 80)
    for r in rows:
        r["creation"] = str(r["creation"])[:19]
        r["scoped"] = bool(r.get("sections"))
    return {"ready": True, "rows": rows}


@frappe.whitelist(methods=["POST"])
def take_snapshot(label=None):
    _sm()
    _ready()
    return snapshots.take("manual", label=(label or "").strip() or None)


@frappe.whitelist()
def snapshot_bundle(name):
    """The full bundle, in Backup > Export format, for download."""
    _sm()
    _ready()
    return snapshots.load(name)


@frappe.whitelist()
def compare_snapshot(name):
    """Record counts in the snapshot against the site now, per doctype."""
    _sm()
    _ready()
    then = json.loads(frappe.db.get_value(DT, name, "counts_json") or "{}")
    now = current_counts(live_plan())
    rows = []
    for dt in sorted(set(then) | set(now)):
        a, b = int(then.get(dt) or 0), int(now.get(dt) or 0)
        rows.append({"doctype": dt, "snapshot": a, "now": b, "diff": b - a})
    return {"name": name, "rows": rows}


@frappe.whitelist(methods=["POST"])
def restore_snapshot(name, mode="merge", sections=None, allow_partial=0, allow_without_snapshot=0):
    """Restore a snapshot. A pre-restore snapshot is taken first, and the
    restore is all-or-nothing unless allow_partial is set."""
    _sm()
    _ready()
    bundle = snapshots.load(name)
    sel = frappe.parse_json(sections) if isinstance(sections, str) and sections else sections
    pre = snapshots.guard("pre_restore", label=f"Before restoring {name} ({mode})",
                          required=not cint(allow_without_snapshot))
    out = restore_bundle(bundle, mode=mode, sections=sel or None, atomic=not cint(allow_partial))
    out["pre_restore_snapshot"] = pre
    return out


@frappe.whitelist(methods=["POST"])
def pin_snapshot(name, pinned=1):
    _sm()
    _ready()
    frappe.db.set_value(DT, name, "pinned", 1 if cint(pinned) else 0)
    frappe.db.commit()
    return {"ok": True}


@frappe.whitelist(methods=["POST"])
def delete_snapshot(name):
    _sm()
    _ready()
    frappe.delete_doc(DT, name, ignore_permissions=True, delete_permanently=True)
    frappe.db.commit()
    return {"ok": True}
