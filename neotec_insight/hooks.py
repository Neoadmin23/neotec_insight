from __future__ import annotations

app_name = "neotec_insight"
# v2.90.0 — ERPNext was required in practice (GL Entry, Company, Account) but
# not declared; a fresh install without it now stops with a clear message.
required_apps = ["erpnext"]
app_title = "Neotec Insight"
app_publisher = "Neotec"
app_description = "Financial reporting and BI for ERPNext — map-driven row tree, multi-year comparison, budget, exports, and a separate Visuals workspace."
app_email = "info@neotec.ai"
app_license = "mit"
app_logo_url = "/assets/neotec_insight/images/neotec-insight-icon.svg"

add_to_apps_screen = [
    {
        "name": "neotec_insight",
        "logo": "/assets/neotec_insight/images/neotec-insight-icon.svg",
        "title": "Neotec Insight",
        "route": "/insight",
    }
]

after_install = "neotec_insight.install.after_install"
# v2.89.0 — a snapshot of everything Insight holds is taken before every
# migrate, and after_migrate checks that no table lost its records. Both are
# wrapped so they can never block a deploy.
before_migrate = ["neotec_insight.neotec_insight.utils.snapshots.before_migrate"]
after_migrate = [
    "neotec_insight.install.after_migrate",
    "neotec_insight.neotec_insight.utils.snapshots.after_migrate",
    "neotec_insight.neotec_insight.api.controls.seed_rules",       # v3.5.0
    "neotec_insight.neotec_insight.api.bankrec.seed_formats",      # v3.6.0
]

# v1.9.59 — when an admin changes a Company's year_start_date in ERPNext,
# Insight's per-company FY cache must invalidate or subsequent report runs
# will use the stale fiscal year orientation. Listening to on_update covers
# both create and edit paths cleanly.
# v2.90.0 — company and report scope for every Insight API call, enforced once.
before_request = ["neotec_insight.neotec_insight.utils.access.enforce_request_scope"]

doc_events = {
    "Company": {
        "on_update": "neotec_insight.neotec_insight.utils.fiscal_year.clear_company_fy_cache_on_company_save",
    },
    # v2.90.0 — account lists are cached per site; any change clears them
    # for every worker.
    "Account": {
        "after_insert": "neotec_insight.neotec_insight.utils.execution.invalidate_account_cache",
        "on_update": "neotec_insight.neotec_insight.utils.execution.invalidate_account_cache",
        "on_trash": "neotec_insight.neotec_insight.utils.execution.invalidate_account_cache",
        "after_rename": "neotec_insight.neotec_insight.utils.execution.invalidate_account_cache",
    },
}

permission_query_conditions = {
    "Insight Report Definition": "neotec_insight.neotec_insight.doctype.insight_report_definition.insight_report_definition.get_permission_query_conditions",
}

has_permission = {
    "Insight Report Definition": "neotec_insight.neotec_insight.doctype.insight_report_definition.insight_report_definition.has_permission",
}

scheduler_events = {
    "daily": ["neotec_insight.tasks.run_daily_report_schedules"],
    # v2.89.0 — daily automatic backup snapshot (skipped when unchanged).
    # v2.89.1 — on the long queue, so it never competes with short jobs.
    "daily_long": ["neotec_insight.neotec_insight.utils.snapshots.daily",
                   # v3.5.0 — Insight Controls: continuous finance checks
                   "neotec_insight.neotec_insight.api.controls.daily"],
}

website_route_rules = [
    {"from_route": "/insight/<path:app_path>", "to_route": "insight"},
]

# Embed the bank-slip reader inside the standard accounting documents.
doctype_js = {
    "Payment Entry": "public/js/payment_entry_bank_slip.js",
    "Journal Entry": "public/js/journal_entry_bank_slip.js",
}

fixtures = []

# v3.8.0 — Neotec AI: Copilot's read-only tools for Neotec AI coworkers, and the one-click switch shown in
# Neotec AI's setup. Both are read only when Neotec AI is installed; harmless otherwise.
neotec_ai_tools = ["neotec_insight.neotec_insight.utils.neotec_ai_link.get_tools"]
neotec_ai_integrations = ["neotec_insight.neotec_insight.utils.neotec_ai_link.integration"]
