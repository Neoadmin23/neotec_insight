from __future__ import annotations

import frappe


def run_daily_report_schedules() -> None:
    """Scheduled hook entry. Iterates enabled Insight Report Schedules and dispatches them.

    Implementation deferred to neotec_insight.neotec_insight.utils.scheduled_reports
    in a follow-up release. Defined here so hooks.py loads cleanly.
    """
    try:
        from neotec_insight.neotec_insight.utils.scheduled_reports import (
            process_scheduled_reports_for_cadence,
        )

        process_scheduled_reports_for_cadence("daily")
    except ImportError:
        return
