"""Fix the Investment Holding preset's comparison_mode (v1.9.54).

The v1.9.49 Income Statement — Investment Holding preset was shipped with
comparison_mode = 'vs_prior_year', which is NOT a value the engine
recognises. The engine accepts only 'actuals_only' and 'vs_budget'; anything
else falls through to "no budget comparison columns rendered."

For this preset the behaviour was effectively the same as 'actuals_only'
(prior years still loaded via the separate `prior_years` field), but the
mode label was wrong and confusing in the UI.

Two places carry the mode for the same report:
  - The DocType field `comparison_mode` (read by run_report)
  - The nested JSON at definition_json["comparison"]["mode"] (read by the
    frontend RowsTab editor)
Both need to match. This patch updates both.

Match strategy — match by BOTH slug AND current stale value. If an admin
has already manually fixed the mode, or set it to something else
deliberately (e.g. 'vs_budget'), we leave it alone. Only the original
broken combination is replaced.

Idempotent — re-running does nothing if the patch already ran (Frappe
tracks via tabPatch Log) and even if it somehow ran twice, the second
pass would find no rows matching the stale value and exit cleanly.
"""
from __future__ import annotations

import json

import frappe

_SLUG = "income-statement-investment-holding"
_STALE_MODE = "vs_prior_year"
_CORRECT_MODE = "actuals_only"


def execute() -> None:
    # The DocType might not exist on a fresh bench where this patch runs
    # before the app is fully migrated. Defensive: bail quietly.
    if not frappe.db.exists("DocType", "Insight Report Definition"):
        return

    rows = frappe.get_all(
        "Insight Report Definition",
        filters={"slug": _SLUG, "comparison_mode": _STALE_MODE},
        fields=["name", "definition_json"],
    )
    if not rows:
        # Nothing to fix — either the preset was never installed on this
        # bench, an admin already corrected it, or the slug doesn't exist.
        # Either way, the patch's job is done.
        return

    fixed = 0
    for row in rows:
        try:
            # Field update first.
            frappe.db.set_value(
                "Insight Report Definition",
                row["name"],
                "comparison_mode",
                _CORRECT_MODE,
                update_modified=False,  # don't bump version for an admin correction
            )

            # Nested JSON update — parse, fix in place, re-serialise. We
            # only touch the `comparison.mode` key; everything else in the
            # definition (rows, columns, filters) is preserved exactly.
            raw = row.get("definition_json") or "{}"
            try:
                d = json.loads(raw)
            except Exception:
                # Corrupt JSON — skip the nested fix but keep the field fix.
                # Better to half-fix than to throw and roll back.
                continue
            comp = d.get("comparison") if isinstance(d, dict) else None
            if isinstance(comp, dict) and comp.get("mode") == _STALE_MODE:
                comp["mode"] = _CORRECT_MODE
                frappe.db.set_value(
                    "Insight Report Definition",
                    row["name"],
                    "definition_json",
                    json.dumps(d, indent=2, sort_keys=True),
                    update_modified=False,
                )
            fixed += 1
        except Exception as e:
            # Per-row resilience: one bad row shouldn't abort the migration.
            frappe.log_error(
                f"v1.9.54 patch could not fix '{row.get('name')}': {e}",
                "Neotec Insight: patch",
            )

    if fixed:
        # Single commit at the end. If anything failed, the per-row try
        # already swallowed it — we commit the successes.
        frappe.db.commit()
