from __future__ import annotations

import frappe

from neotec_insight.neotec_insight.utils.report_presets import (
    sync_default_app_report_presets,
)
from neotec_insight.neotec_insight.utils.mapping_rules import (
    seed_default_mapping_rules,
)


def after_install() -> None:
    _sync_defaults()


def after_migrate() -> None:
    _sync_defaults()


def _seed_export_packs() -> None:
    from neotec_insight.neotec_insight.api.packs import seed_default_export_packs
    seed_default_export_packs()


def _sync_defaults() -> None:
    frappe.flags.in_patch = True
    try:
        # Each seed is isolated: a failure is logged and skipped, never fatal.
        # after_migrate must not be able to abort a site migrate (which would
        # block the whole bench's deploy, not just this app).
        for step in (
            seed_default_mapping_rules,
            sync_default_app_report_presets,
            seed_default_quick_links,
            seed_insight_roles,
            seed_navbar_link,
            seed_insight_workspace,
            seed_equity_components_and_types,
            _seed_export_packs,
        ):
            try:
                step()
                frappe.db.commit()
            except Exception:
                frappe.db.rollback()
                try:
                    frappe.log_error(
                        title="neotec_insight after_migrate seed failed",
                        message=f"{step.__name__}: {frappe.get_traceback()}",
                    )
                except Exception:
                    pass
    finally:
        frappe.flags.in_patch = False


# Seed payloads for the configurable equity DocTypes (v1.9.51). Display
# orders are spaced by 10 so admins can insert new components/types between
# existing ones without renumbering.
_SEED_EQUITY_COMPONENTS = [
    {"component_name": "Paid-up Share Capital",                  "display_order": 10},
    {"component_name": "Statutory Reserve",                      "display_order": 20},
    {"component_name": "Reserve for Probable Liabilities",       "display_order": 30},
    {"component_name": "Retained Earnings",                      "display_order": 40},
    {"component_name": "Unrealized Gains/(Losses) from AFS",     "display_order": 50},
    {"component_name": "Minority Interest",                      "display_order": 60},
    {"component_name": "Other",                                  "display_order": 70},
]

_SEED_EQUITY_MOVEMENT_TYPES = [
    {"type_name": "Beginning Balance",                "display_order": 10, "is_opening_balance": 1, "default_sign": "Either"},
    {"type_name": "Net Income for the Period",        "display_order": 20, "is_opening_balance": 0, "default_sign": "Either"},
    {"type_name": "Transfer In from Retained Earnings", "display_order": 30, "is_opening_balance": 0, "default_sign": "Increase only"},
    {"type_name": "Transfer Out to Reserve",          "display_order": 40, "is_opening_balance": 0, "default_sign": "Decrease only"},
    {"type_name": "Transfer Out to Capital",          "display_order": 50, "is_opening_balance": 0, "default_sign": "Decrease only"},
    {"type_name": "Capital Increase/(Decrease)",      "display_order": 60, "is_opening_balance": 0, "default_sign": "Either"},
    {"type_name": "Dividend Paid",                    "display_order": 70, "is_opening_balance": 0, "default_sign": "Decrease only"},
    {"type_name": "Unrealized Gain/(Loss) on AFS",    "display_order": 80, "is_opening_balance": 0, "default_sign": "Either"},
    {"type_name": "Disposal Movement (Realised)",     "display_order": 90, "is_opening_balance": 0, "default_sign": "Either"},
    {"type_name": "Absorption of Losses",             "display_order": 100, "is_opening_balance": 0, "default_sign": "Either"},
    {"type_name": "Other Movement",                   "display_order": 110, "is_opening_balance": 0, "default_sign": "Either"},
]


def seed_equity_components_and_types() -> None:
    """Seed the configurable equity components and movement types (v1.9.51).

    Idempotent on the name field: if a row with this name already exists,
    we skip it entirely (don't overwrite display_order, flags, or anything
    the admin may have customised). If the admin renamed a seeded row, the
    seed won't recreate it under the original name — that's intentional;
    rename = deliberate change.

    The is_seeded flag is set on insertion so admins can identify which
    rows came from the seed vs which they created themselves.
    """
    # Components
    for c in _SEED_EQUITY_COMPONENTS:
        if frappe.db.exists("Insight Equity Component", c["component_name"]):
            continue
        try:
            doc = frappe.new_doc("Insight Equity Component")
            doc.component_name = c["component_name"]
            doc.display_order = c["display_order"]
            doc.is_seeded = 1
            doc.insert(ignore_permissions=True)
        except Exception as e:
            frappe.log_error(
                f"Could not seed Insight Equity Component '{c['component_name']}': {e}",
                "Neotec Insight: install",
            )

    # Movement types
    for t in _SEED_EQUITY_MOVEMENT_TYPES:
        if frappe.db.exists("Insight Equity Movement Type", t["type_name"]):
            continue
        try:
            doc = frappe.new_doc("Insight Equity Movement Type")
            doc.type_name = t["type_name"]
            doc.display_order = t["display_order"]
            doc.is_opening_balance = t["is_opening_balance"]
            doc.default_sign = t["default_sign"]
            doc.is_seeded = 1
            doc.insert(ignore_permissions=True)
        except Exception as e:
            frappe.log_error(
                f"Could not seed Insight Equity Movement Type '{t['type_name']}': {e}",
                "Neotec Insight: install",
            )


def seed_insight_workspace() -> None:
    """Create a 'Neotec Insight' workspace in the Frappe desk that serves as
    a launch tile / shortcut to /insight (v1.9.56).

    The workspace appears in the desk's sidebar and on the home page. It's
    a deliberate, prominent navigation surface — finance users land in
    ERPNext, see the Insight tile, click it, and arrive in /insight.

    Idempotent — checks if a workspace named 'Neotec Insight' already
    exists. If yes, we update only its title/icon/shortcut link to keep
    it healed, but we don't reset admin customisations of other fields.
    """
    if not frappe.db.exists("DocType", "Workspace"):
        return
    name = "Neotec Insight"
    try:
        if frappe.db.exists("Workspace", name):
            # Heal — make sure the shortcut still points at /insight.
            try:
                ws = frappe.get_doc("Workspace", name)
                # Look for an existing shortcut to /insight, add if missing.
                has = False
                for sc in (ws.shortcuts or []):
                    if (sc.link_to or "").strip() == "/insight":
                        has = True
                        break
                if not has:
                    ws.append("shortcuts", {
                        "type": "URL",
                        "label": "Open Neotec Insight",
                        "url": "/insight",
                        "color": "Blue",
                    })
                    ws.save(ignore_permissions=True)
            except Exception as e:
                frappe.log_error(f"Could not heal Neotec Insight workspace: {e}", "Neotec Insight: install")
            return

        ws = frappe.new_doc("Workspace")
        ws.name = name
        ws.title = name
        ws.label = name
        ws.public = 1
        ws.module = "Neotec Insight"
        ws.icon = "chart-line"
        # category may not be a valid field on all Frappe versions — set defensively.
        try: ws.category = "Modules"
        except Exception: pass
        ws.append("shortcuts", {
            "type": "URL",
            "label": "Open Neotec Insight",
            "url": "/insight",
            "color": "Blue",
            "doc_view": "",
        })
        # Content (the workspace's main body). Minimal — a header + a hint to
        # click the shortcut. We deliberately don't try to embed Insight UI
        # inside the workspace; the URL link is cleaner.
        try:
            import json as _json
            ws.content = _json.dumps([
                {"id": "neotec_header", "type": "header", "data": {
                    "text": "<h3>Neotec Insight — financial analytics workspace</h3>",
                    "col": 12
                }},
                {"id": "neotec_para", "type": "paragraph", "data": {
                    "text": (
                        "Click <b>Open Neotec Insight</b> in the shortcuts to launch the "
                        "reporting and budgeting workspace. Reports, dashboards, the CFO "
                        "Briefing, multi-company group view, equity statements, and budget "
                        "books all live there."
                    ),
                    "col": 12
                }},
            ])
        except Exception:
            pass
        ws.insert(ignore_permissions=True)
    except Exception as e:
        frappe.log_error(f"Could not seed Neotec Insight workspace: {e}", "Neotec Insight: install")


def seed_navbar_link() -> None:
    """Add a 'Neotec Insight' link to the desk's top-right Settings dropdown.

    Idempotent — checks for an existing entry pointing to /insight before
    adding. Quietly skips if Navbar Settings isn't a doctype on this bench
    (e.g. very old Frappe versions).
    """
    if not frappe.db.exists("DocType", "Navbar Settings"):
        return
    try:
        ns = frappe.get_doc("Navbar Settings", "Navbar Settings")
    except Exception:
        return

    # v1.9.44 — heal any previously mis-seeded item. v1.9.43 wrote the entry
    # with item_type="Route", which makes Frappe rewrite '/insight' to
    # '/app/insight' (a desk route) and fall through to the workspace page.
    # The correct type for an external/absolute path is "URL".
    healed = False
    for item in (ns.settings_dropdown or []):
        if (item.action or "").strip() == "/insight":
            if (item.item_type or "") != "URL":
                item.item_type = "URL"
                healed = True
            # Entry exists (whether we just healed it or not). Save if needed
            # and return — don't append a duplicate.
            if healed:
                try:
                    ns.save(ignore_permissions=True)
                except Exception:
                    frappe.log_error("Could not heal navbar item type for /insight", "Neotec Insight: install")
            return

    # No existing entry — add a fresh one with the correct type.
    ns.append("settings_dropdown", {
        "item_label": "Neotec Insight",
        "item_type": "URL",
        "action": "/insight",
        "is_standard": 1,
    })
    try:
        ns.save(ignore_permissions=True)
    except Exception:
        # Failing to write the navbar should not break migrate — log and skip.
        frappe.log_error("Could not seed navbar shortcut to /insight", "Neotec Insight: install")


def seed_insight_roles() -> None:
    """Seed the Insight role tier.

    Three roles in total, all created idempotently:

    - 'Insight Group Viewer' (v1.9.38): gates the Group multi-company tab.
      Independent of CFO/CEO — explicit signal "this user works at the
      group level."
    - 'Insight CFO' (v1.9.45): full access including edit on report
      definitions. Backend role-check helpers honour this.
    - 'Insight CEO' (v1.9.45): full view access, no edit. The frontend
      hides edit affordances; the backend save endpoints reject writes
      from users with only this role.

    System Manager and Accounts Manager bypass all role checks (preserves
    current admin behaviour). Users without any Insight role retain
    today's basic access — the new roles GRANT edit, they do not RESTRICT
    view, so existing users are not locked out on the next migrate.
    """
    for role_name in ("Insight Group Viewer", "Insight CFO", "Insight CEO"):
        if frappe.db.exists("Role", role_name):
            continue
        role = frappe.new_doc("Role")
        role.role_name = role_name
        role.desk_access = 1
        role.disabled = 0
        role.insert(ignore_permissions=True)


def seed_default_quick_links() -> None:
    """Seed two sample Insight Quick Links on first run.

    Only seeds when the table is completely empty — so it never overwrites or
    re-creates links the user has edited or deleted. The user fully owns these
    records after the first seed.
    """
    if frappe.db.count("Insight Quick Link") > 0:
        return
    samples = [
        {"label": "General Ledger", "url": "/app/general-ledger", "icon": "book", "sort_order": 10},
        {"label": "Chart of Accounts", "url": "/app/account", "icon": "list-tree", "sort_order": 20},
    ]
    for s in samples:
        if frappe.db.exists("Insight Quick Link", s["label"]):
            continue
        doc = frappe.new_doc("Insight Quick Link")
        doc.label = s["label"]
        doc.url = s["url"]
        doc.icon = s["icon"]
        doc.sort_order = s["sort_order"]
        doc.open_in_new_tab = 1
        doc.enabled = 1
        doc.insert(ignore_permissions=True)
