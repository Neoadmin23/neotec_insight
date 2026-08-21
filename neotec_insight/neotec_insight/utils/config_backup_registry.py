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
    {"doctype": "Insight Allocation Rule", "area": "Allocation rules"},
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

# Deliberately NOT portable config — real, dated activity tied to this
# site's actual transactions. A reason is required for every entry; "not
# obviously configuration" is not a reason, an actual justification is.
EXCLUDED_FROM_CONFIG_BACKUP: dict[str, str] = {
    "Insight Allocation Entry":
        "Posted allocation results and driver figures tied to specific historical "
        "periods on this site — not portable config.",
    "Insight Cash Flow Override":
        "Confirmed classification decisions tied to specific real vouchers on this "
        "site — not portable config, same treatment as Insight Allocation Entry above.",
    "Insight Bank Slip":
        "Staged/reconciled bank statement records tied to this site's real bank activity.",
    "Insight Payment Order":
        "LOWER CONFIDENCE — inferred from the name only, not verified against how it's "
        "actually used. Appears to be a generated payment run tied to a specific period "
        "rather than reusable setup. Flagged for the app owner to confirm, not asserted.",
    "Insight VAT Adjustment":
        "LOWER CONFIDENCE — inferred from the name only, not verified against how it's "
        "actually used. Appears to be a specific period's adjustment entry rather than "
        "reusable setup. Flagged for the app owner to confirm, not asserted.",
}


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
