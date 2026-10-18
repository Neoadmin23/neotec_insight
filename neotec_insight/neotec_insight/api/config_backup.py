"""Backup → Export / Import endpoints.

v2.91.0 — moved out of api/report.py unchanged. report.py re-exports these
names, so the endpoints keep their `api.report.<name>` addresses."""

import frappe
from frappe.utils import cint

from neotec_insight.neotec_insight.utils.config_backup_registry import (
    EXCLUDED_FROM_CONFIG_BACKUP,
    compute_coverage,
)


def _require_read() -> None:
    """Finance read gate — one policy for every module (utils/access.py)."""
    from neotec_insight.neotec_insight.utils.access import require_finance_read
    require_finance_read()


@frappe.whitelist()
def export_configuration(sections=None):
    """Bundle everything Insight holds into one portable JSON object.

    v2.89.0 — what goes in is discovered from the installed doctypes of the
    module (utils/backup_engine.py), not from a hand-kept list: a doctype added
    by a future release is in the backup without anyone remembering to add it.
    The registry (utils/config_backup_registry.py) only gives areas their
    labels and order. Data areas (VAT adjustments, payment orders, bank slips,
    cash-flow classifications, allocation head counts) are included too.

    `sections` (optional): JSON list of doctypes to include; omitted = all.
    """
    frappe.only_for("System Manager")
    from neotec_insight.neotec_insight.utils.backup_engine import build_bundle
    sel = None
    if sections:
        try:
            sel = frappe.parse_json(sections) if isinstance(sections, str) else sections
        except Exception:
            sel = None
    return build_bundle(sel or None)


@frappe.whitelist()
def config_areas():
    """Backup areas for the checkbox list, grouped, each with its doctypes,
    kind (config/data) and whether it was added automatically."""
    _require_read()
    from neotec_insight.neotec_insight.utils.backup_engine import live_plan
    areas: dict[str, dict] = {}
    for entry in live_plan():
        a = areas.setdefault(entry["area"], {"label": entry["area"], "doctypes": [],
                                             "kind": entry.get("kind", "config"),
                                             "auto": False})
        a["doctypes"].append(entry["doctype"])
        a["auto"] = a["auto"] or bool(entry.get("auto"))
    return list(areas.values())


@frappe.whitelist()
def check_config_backup_coverage():
    """Every Insight doctype (module='Neotec Insight', not a child table)
    against the registry. Since v2.89.0 an unlabelled doctype is still backed
    up (under "Other (added automatically)"); `unaccounted` lists those so they
    can be given a proper label, and `plan` is exactly what an export carries."""
    frappe.only_for("System Manager")
    from neotec_insight.neotec_insight.utils.backup_engine import live_plan
    live = frappe.get_all("DocType", filters={"module": "Neotec Insight", "istable": 0}, pluck="name")
    out = compute_coverage(live)
    out["plan"] = [{k: e.get(k) for k in ("doctype", "area", "kind", "auto", "is_single")}
                   for e in live_plan()]
    out["excluded_reasons"] = {k: v for k, v in EXCLUDED_FROM_CONFIG_BACKUP.items()}
    return out


@frappe.whitelist()
def config_section_counts():
    """Record count per backed-up doctype, for the backup selector."""
    _require_read()
    from neotec_insight.neotec_insight.utils.backup_engine import current_counts
    return current_counts()


def _app_version():
    try:
        import neotec_insight
        return getattr(neotec_insight, "__version__", "")
    except Exception:
        return ""


@frappe.whitelist(methods=["POST"])
def import_configuration(payload=None, mode="replace", sections=None, allow_partial=0,
                         allow_without_snapshot=0):
    """Restore a bundle produced by export_configuration (or a snapshot).

    v2.89.0:
    * A snapshot of the current state is taken first, so any restore can be
      undone from Backup > Snapshots.
    * All-or-nothing: if any record fails, nothing is changed (rolled back) and
      the errors are returned. allow_partial=1 keeps what succeeded instead.
    * replace updates matching records in place and deletes only records of
      the bundle's own doctypes that the bundle does not hold; doctypes the
      bundle does not carry are never touched.
    * Doctypes this site does not have are skipped and listed, not fatal.
    """
    frappe.only_for("System Manager")
    from neotec_insight.neotec_insight.utils.backup_engine import bundle_from_json, restore_bundle
    from neotec_insight.neotec_insight.utils import snapshots
    payload = bundle_from_json(payload)
    if not payload or "data" not in payload:
        frappe.throw("Invalid configuration bundle.")
    sel = frappe.parse_json(sections) if isinstance(sections, str) and sections else sections
    # v2.90.0 — no restore without a complete pre-restore snapshot, unless a
    # System Manager explicitly accepts that (e.g. the snapshot store itself
    # is what is broken).
    pre = snapshots.guard("pre_restore", label=f"Before {mode} restore of a v{payload.get('version') or '?'} bundle",
                          required=not cint(allow_without_snapshot))
    out = restore_bundle(payload, mode=mode, sections=sel or None, atomic=not cint(allow_partial))
    out["pre_restore_snapshot"] = pre
    return out
