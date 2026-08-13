from __future__ import annotations

import frappe

DEFAULT_RULES: list[dict] = [
    {"prefix": "311", "flag": "Revenue", "priority": 100},
    {"prefix": "312", "flag": "Other Revenue", "priority": 100},
    {"prefix": "41101", "flag": "Cost of revenue for service and solutions", "priority": 50},
    {"prefix": "411", "flag": "Cost of revenue for service and solutions", "priority": 100},
    {"prefix": "42101", "flag": "Total Employee Cost", "priority": 50},
    {"prefix": "42102", "flag": "Total Employee Cost", "priority": 50},
    {"prefix": "42103", "flag": "Kitchen / Stationary / Computer and other supplies", "priority": 50},
    {"prefix": "42104", "flag": "Hotels / Accommodation and Travel Expenses", "priority": 50},
    {"prefix": "42203", "flag": "Hotels / Accommodation and Travel Expenses", "priority": 50},
    {"prefix": "42204", "flag": "Utilities", "priority": 50},
    {"prefix": "42205", "flag": "Government related", "priority": 50},
    {"prefix": "42206", "flag": "Repairs / Maintenance and other services", "priority": 50},
    {"prefix": "42207", "flag": "Kitchen / Stationary / Computer and other supplies", "priority": 50},
    {"prefix": "42208", "flag": "Subscriptions & Server Hosting", "priority": 50},
    {"prefix": "42301", "flag": "Bank Charges", "priority": 50},
    {"prefix": "42302", "flag": "Consultation and Audit Fee", "priority": 50},
    {"prefix": "43101", "flag": "Depreciation And Amortization", "priority": 50},
    {"prefix": "43102", "flag": "Zakat and Other Charges", "priority": 50},
]


def seed_default_mapping_rules() -> None:
    """Seed Insight Mapping Rule with default code-prefix rules.

    Idempotent — skips any rule whose (prefix, flag) already exists.
    """
    for rule in DEFAULT_RULES:
        existing = frappe.db.exists(
            "Insight Mapping Rule",
            {"prefix": rule["prefix"], "flag": rule["flag"]},
        )
        if existing:
            continue
        doc = frappe.new_doc("Insight Mapping Rule")
        doc.prefix = rule["prefix"]
        doc.flag = rule["flag"]
        doc.priority = rule.get("priority", 100)
        doc.is_active = 1
        doc.insert(ignore_permissions=True)


def suggest_flag_for_code(code: str) -> str | None:
    """Given an account code, return the best-matching flag by prefix.

    Picks the longest matching prefix among active rules; ties broken by priority.
    """
    if not code:
        return None
    code = str(code).strip()
    rules = frappe.get_all(
        "Insight Mapping Rule",
        filters={"is_active": 1},
        fields=["prefix", "flag", "priority"],
    )
    best = None
    best_len = -1
    best_prio = 1_000_000
    for r in rules:
        prefix = (r.get("prefix") or "").strip()
        if not prefix or not code.startswith(prefix):
            continue
        plen = len(prefix)
        prio = int(r.get("priority") or 100)
        if plen > best_len or (plen == best_len and prio < best_prio):
            best = r.get("flag")
            best_len = plen
            best_prio = prio
    return best


def autosuggest_unmapped_for_report(report_name: str) -> int:
    """Apply mapping rules to every unmapped Account on the report's company chart.

    Returns the count of newly created mappings.
    """
    report = frappe.get_doc("Insight Report Definition", report_name)
    company = report.company
    filters = {"is_group": 0}
    if company:
        filters["company"] = company
    accounts = frappe.get_all(
        "Account",
        filters=filters,
        fields=["name", "account_number", "account_name"],
    )
    already = {
        m["account"]
        for m in frappe.get_all(
            "Account Flag Mapping",
            filters={"report": report_name},
            fields=["account"],
        )
    }
    created = 0
    for acc in accounts:
        if acc["name"] in already:
            continue
        code = acc.get("account_number")
        if not code:
            continue
        flag = suggest_flag_for_code(code)
        if not flag:
            continue
        doc = frappe.new_doc("Account Flag Mapping")
        doc.report = report_name
        doc.account = acc["name"]
        doc.flag = flag
        doc.auto_suggested = 1
        doc.source = "auto-suggested"
        doc.insert(ignore_permissions=True)
        created += 1
    return created
