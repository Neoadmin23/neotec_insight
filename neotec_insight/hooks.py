from __future__ import annotations

app_name = "neotec_insight"
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
after_migrate = ["neotec_insight.install.after_migrate"]

# v1.9.59 — when an admin changes a Company's year_start_date in ERPNext,
# Insight's per-company FY cache must invalidate or subsequent report runs
# will use the stale fiscal year orientation. Listening to on_update covers
# both create and edit paths cleanly.
doc_events = {
    "Company": {
        "on_update": "neotec_insight.neotec_insight.utils.fiscal_year.clear_company_fy_cache_on_company_save",
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
