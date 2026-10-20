"""Config backup registry — the ONE place that decides which Insight
doctypes are portable configuration (exported/restored by
export_configuration / import_configuration) versus site-specific
transactional history (deliberately excluded, with a reason) versus not yet
triaged at all (a real gap).

Before this file existed, the same decision was made independently in three
places — export_configuration's own hardcoded dict, config_section_counts'
_IMPORT_ORDER, and the frontend's AREAS array — and could silently drift out
of sync, or all three could simply never be told about a new doctype. A
coverage audit against the real doctype list (see check_config_backup_
coverage in api/report.py) found seven doctypes already missing before this
feature's own Cash Flow Forecast doctypes were even added — this file, and
compute_coverage below, are the fix: one registry, one place to check it
against what's actually installed, and a test that fails if a future
doctype goes untriaged.

Standalone, no frappe import — compute_coverage is pure and testable
without a site; api/report.py owns the DB-facing discovery query
(frappe.get_all("DocType", filters={"module": "Neotec Insight", ...})) and
the whitelisted endpoint that exposes this to the UI.
"""

from __future__ import annotations

# Portable configuration — exported/restored as a unit. `area` groups
# doctypes under one friendly checkbox in the backup UI; several doctypes
# can share an area (e.g. the three Equity doctypes). `is_single`: Frappe
# Single doctypes are stored and restored differently (frappe.get_doc(dt)
# with no name, not frappe.get_all + per-record loop).
CONFIG_REGISTRY: list[dict] = [
    {"doctype": "Insight Report Definition", "area": "Report definitions & rows"},
    {"doctype": "Account Flag Mapping", "area": "Account map (flag mappings)"},
    {"doctype": "Insight Mapping Rule", "area": "Mapping rules"},
    {"doctype": "Insight Budget Book", "area": "Budgets"},
    {"doctype": "Insight Budget Cell", "area": "Budgets"},
    {"doctype": "Insight Equity Component", "area": "Equity setup & movements"},
    {"doctype": "Insight Equity Movement Type", "area": "Equity setup & movements"},
    {"doctype": "Insight Equity Movement", "area": "Equity setup & movements"},
    {"doctype": "Insight Dashboard", "area": "Dashboards"},
    {"doctype": "Insight Variance Note", "area": "Variance notes"},
    {"doctype": "Insight Quick Link", "area": "Quick links"},
    {"doctype": "Insight AI Settings", "area": "AI settings", "is_single": True},

    # v2.87.3 — added from the first real coverage audit. High confidence:
    # inferred from names AND the code that builds/uses each one, same as
    # everything above.
    {"doctype": "Insight Account Tag", "area": "Account classification tags"},
    # v2.88.11 — rules AND their driver values travel together. The head counts
    # (manual and captured, with frozen flags and evidence) are figures people
    # key in and report from; a backup that kept the rule but not the numbers
    # restored an empty allocation. Rule first: entries link to it.
    {"doctype": "Insight Allocation Rule", "area": "Allocation rules & driver values"},
    {"doctype": "Insight Allocation Entry", "area": "Allocation rules & driver values"},
    {"doctype": "Insight GTPL Rule", "area": "GTPL rules"},
    {"doctype": "Insight Menu Settings", "area": "Menu layout", "is_single": True},
    {"doctype": "Insight Report Schedule", "area": "Report schedules"},
    {"doctype": "Insight Translation Override", "area": "Translation overrides"},
    {"doctype": "Insight Dataset", "area": "Saved datasets"},
    {"doctype": "Insight EBITDA Addback", "area": "EBITDA addbacks"},
    {"doctype": "Insight Export Pack", "area": "Export packs"},
    {"doctype": "Studio Report", "area": "Studio reports"},

    # Cash Flow Forecast (v2.86.0+) — config half only. Insight Cash Flow
    # Line's own Bindings (and their Cost Centers multi-select) are a child
    # table and travel with it automatically; nothing separate to register.
    {"doctype": "Insight Cash Flow Line", "area": "Cash Flow Forecast — lines & bindings"},
    {"doctype": "Insight Cash Flow Budget", "area": "Cash Flow Forecast — budget"},
    {"doctype": "Insight Cash Flow Classification Rule",
     "area": "Cash Flow Forecast — classification rules"},
    {"doctype": "Insight Cash Flow Settings", "area": "Cash Flow Forecast — settings", "is_single": True},
]

# v2.89.0 — the four doctypes that used to be listed here as "transactional,
# not portable" are now backed up too, as data areas: the request was that
# everything the app holds is part of the backup, and a restore onto the SAME
# site (the case that actually happens after a loss) needs them. They are
# registered with kind="data" so the UI can label them and a sandbox export
# can leave them out with one click.
#
# The only thing excluded is the snapshot store itself: a snapshot containing
# every earlier snapshot would grow without bound.
EXCLUDED_FROM_CONFIG_BACKUP: dict[str, str] = {
    "Insight Backup Snapshot":
        "The automatic snapshot store itself. Each snapshot already holds a full "
        "bundle; nesting them would double the size of every backup, every day.",
}

# Data areas (v2.89.0). Kept separate from CONFIG_REGISTRY's list literal so
# the config/data split stays obvious when reading either one.
DATA_REGISTRY: list[dict] = [
    {"doctype": "Insight VAT Adjustment", "area": "Data — VAT adjustments", "kind": "data"},
    {"doctype": "Insight Payment Order", "area": "Data — payment orders", "kind": "data"},
    {"doctype": "Insight Bank Slip", "area": "Data — bank slips", "kind": "data"},
    {"doctype": "Insight Cash Flow Override", "area": "Data — cash flow classifications", "kind": "data"},
]
CONFIG_REGISTRY.extend(DATA_REGISTRY)

# v2.95.0 — Copilot's outside data sources. The connection's password lives in
# Frappe's encrypted password store, not on the record, so a backup carries
# host/database/allowlist settings but never the credential.
CONFIG_REGISTRY.extend([
    {"doctype": "Insight Data Source", "area": "Copilot — outside data sources"},
    {"doctype": "Insight Source Report", "area": "Copilot — outside data sources"},
])

# Anything in the module that is in neither list is NOT a gap any more: the
# export discovers it and includes it under this area, so a doctype added by
# a future release is in the backup the day it ships, before anyone edits this
# file. Registering it here only gives it a friendlier label and a fixed place
# in the restore order.
AUTO_AREA = "Other (added automatically)"


# Doctypes whose record names are random hashes, so a merge must recognise an
# existing record by what it means rather than by name.
NATURAL_KEYS: dict[str, tuple[str, ...]] = {
    "Insight Allocation Entry": ("rule", "cost_center", "period_month"),
}


def natural_key(doctype: str, record: dict) -> dict | None:
    """Filters identifying the same record under a different name. Pure."""
    fields = NATURAL_KEYS.get(doctype)
    if not fields or any(not record.get(f) for f in fields):
        return None
    return {f: str(record[f]) if f == "period_month" else record[f] for f in fields}


def compute_coverage(all_doctypes: list[str], registry: list[dict] | None = None,
                     excluded: dict | None = None) -> dict:
    """Pure. Compares the live set of Insight doctypes against what's
    registered for config backup and what's explicitly excluded. Anything
    in neither bucket is a genuine, un-triaged gap — this is the number
    that should be zero, and the test that enforces it."""
    registry = CONFIG_REGISTRY if registry is None else registry
    excluded = EXCLUDED_FROM_CONFIG_BACKUP if excluded is None else excluded

    registered_names = {r["doctype"] for r in registry}
    excluded_names = set(excluded.keys())
    live = set(all_doctypes)

    overlap = registered_names & excluded_names
    unaccounted = sorted(dt for dt in live if dt not in registered_names and dt not in excluded_names)

    return {
        "total_live_doctypes": len(live),
        "registered": sorted(registered_names & live),
        "excluded": {dt: excluded[dt] for dt in sorted(excluded_names & live)},
        "unaccounted": unaccounted,
        "registered_but_not_live": sorted(registered_names - live),
        "conflicting_registered_and_excluded": sorted(overlap),
        "fully_covered": not unaccounted and not overlap,
    }


def order_by_dependencies(names: list[str], deps: dict[str, set] | None) -> list[str]:
    """Stable topological order: a doctype comes after every doctype it links
    to (Link / Table fields, including links held in its child tables). Pure.

    Ties keep the incoming order, so the hand-written registry order is kept
    wherever it is already consistent. Self-links and cycles cannot be ordered;
    the remaining members of a cycle keep their incoming order (the restore
    inserts them with link validation off, so either order works)."""
    deps = deps or {}
    present = set(names)
    need = {n: {d for d in deps.get(n, ()) if d in present and d != n} for n in names}
    out: list[str] = []
    placed: set[str] = set()
    pending = list(names)
    while pending:
        progressed = False
        for n in list(pending):
            if need[n] <= placed:
                out.append(n)
                placed.add(n)
                pending.remove(n)
                progressed = True
                break
        if not progressed:          # cycle: take the earliest one as-is
            n = pending.pop(0)
            out.append(n)
            placed.add(n)
    return out


def build_plan(live: list[dict], deps: dict[str, set] | None = None,
               registry: list[dict] | None = None,
               excluded: dict | None = None) -> list[dict]:
    """Everything the backup carries, in restore order. Pure.

    live: [{"name", "is_single"}] for every non-child doctype of the module, as
    installed (or as found in the doctype folder). Registered doctypes keep
    their area and kind; unregistered, unexcluded ones are included under
    AUTO_AREA with auto=True. Registered doctypes that are not installed are
    dropped (an old registry line for a retired doctype must not break the
    export)."""
    registry = CONFIG_REGISTRY if registry is None else registry
    excluded = EXCLUDED_FROM_CONFIG_BACKUP if excluded is None else excluded
    by_name = {d["name"]: d for d in live}
    plan: list[dict] = []
    seen: set[str] = set()
    for e in registry:
        dt = e["doctype"]
        if dt in by_name and dt not in excluded and dt not in seen:
            plan.append({"kind": "config", **e,
                         "is_single": bool(e.get("is_single") or by_name[dt].get("is_single"))})
            seen.add(dt)
    for d in sorted(live, key=lambda x: x["name"]):
        dt = d["name"]
        if dt in seen or dt in excluded:
            continue
        plan.append({"doctype": dt, "area": AUTO_AREA, "kind": "config",
                     "is_single": bool(d.get("is_single")), "auto": True})
        seen.add(dt)
    singles = [p for p in plan if p["is_single"]]
    multi = [p for p in plan if not p["is_single"]]
    order = order_by_dependencies([p["doctype"] for p in multi], deps)
    pos = {dt: i for i, dt in enumerate(order)}
    multi.sort(key=lambda p: pos[p["doctype"]])
    return multi + singles


def count_drops(before: dict, after: dict, min_lost: int = 5,
                ratio: float = 0.5) -> list[dict]:
    """Doctypes whose record count fell sharply between two backups. Pure.

    A fall is reported when at least `min_lost` records went AND either the
    table emptied or it lost at least `ratio` of its rows. Small edits (a
    deleted dashboard) stay quiet; a table going from 84 rows to 0 does not."""
    out = []
    for dt, b in (before or {}).items():
        b = int(b or 0)
        a = int((after or {}).get(dt) or 0)
        lost = b - a
        if lost >= min_lost and (a == 0 or lost >= ratio * b):
            out.append({"doctype": dt, "before": b, "after": a, "lost": lost})
    return sorted(out, key=lambda r: -r["lost"])


def doctypes_to_clear(order: list[str], data: dict | None) -> list[str]:
    """Doctypes a replace-restore may delete from: only those the bundle carries,
    in restore order (the caller walks it in reverse). Pure. A bundle made
    before an area existed must never wipe that area (v2.88.9)."""
    return [dt for dt in order if dt in (data or {})]
