# Copyright (c) 2026, Neotec Integrated Solution
# Frontend-managed navigation (v2.25.0). The SPA ships a DEFAULT menu catalog;
# admins can rearrange it from the Menu Setup modal and the result is stored
# here site-wide as JSON. The frontend MERGES saved config with its catalog on
# boot, so tabs added by future app versions still appear even under an old
# saved layout.
from __future__ import annotations

import json

import frappe
from frappe import _


@frappe.whitelist()
def get_menu():
    """Saved menu configuration, or {} when the default layout applies."""
    try:
        raw = frappe.db.get_single_value("Insight Menu Settings", "menu_json")
        return json.loads(raw) if raw else {}
    except Exception:
        return {}


@frappe.whitelist()
def save_menu(menu=None):
    """Persist the menu layout (site-wide). Write permission on the Single
    DocType gates this — System Manager / Accounts Manager by default."""
    if not frappe.has_permission("Insight Menu Settings", "write"):
        frappe.throw(_("Not permitted to change the menu."))
    if isinstance(menu, str):
        menu = json.loads(menu or "{}")
    sections = menu.get("sections") or []
    # Structural validation only — the frontend owns semantics; unknown tabs
    # are dropped at merge time, so stale config can never break the shell.
    clean = []
    for s in sections[:20]:
        tabs = [{"ws": str(tb.get("ws") or ""), "hidden": 1 if tb.get("hidden") else 0}
                for tb in (s.get("tabs") or [])[:20] if tb.get("ws")]
        clean.append({"key": str(s.get("key") or "")[:40],
                      "label": str(s.get("label") or "")[:60], "tabs": tabs})
    doc = frappe.get_single("Insight Menu Settings")
    doc.menu_json = json.dumps({"sections": clean})
    doc.save(ignore_permissions=True)
    frappe.db.commit()
    return {"ok": True}


@frappe.whitelist()
def reset_menu():
    if not frappe.has_permission("Insight Menu Settings", "write"):
        frappe.throw(_("Not permitted to change the menu."))
    doc = frappe.get_single("Insight Menu Settings")
    doc.menu_json = ""
    doc.save(ignore_permissions=True)
    frappe.db.commit()
    return {"ok": True}


# ── Brand Kit (v2.55.0) ─────────────────────────────────────────────────────
# The print header/footer setup used to live only in the browser's
# localStorage, which meant every operator configured the letterhead again on
# every machine and the Excel/PDF/Print output drifted between them. It is now
# stored site-wide, keyed by company, so one setup serves every report menu.

_BRAND_KEYS = {
    "showCompany", "showPeriod", "footerText", "showPageNumbers", "showTimestamp",
    "paper", "orientation", "logoUrl", "logoPos", "logoHeightMm", "centered",
    "titleSizePt", "bodySizePx", "accent", "borderPreset", "gridLines",
    "align", "headerText", "pageNoPos", "printColors", "browserChrome",
    "companyName", "companyNameAr", "vatNo", "crNo", "title", "subtitle",
}


def _clean_brand(brand: dict) -> dict:
    """Keep only known keys, and only scalar/short values.

    The payload is user-supplied and ends up inside generated print markup,
    so anything unrecognised is dropped rather than trusted.
    """
    out = {}
    for k, v in (brand or {}).items():
        if k not in _BRAND_KEYS:
            continue
        if k == "align" and isinstance(v, dict):
            out[k] = {
                str(ak)[:20]: str(av)[:10]
                for ak, av in list(v.items())[:20]
                if str(av) in ("left", "center", "right", "hide")
            }
        elif isinstance(v, bool) or isinstance(v, int) or isinstance(v, float):
            out[k] = v
        elif isinstance(v, str):
            out[k] = v[:500]
    return out


@frappe.whitelist()
def app_version():
    """The INSTALLED python version of this app.

    Exists so the frontend can compare it against `__APP_VERSION__`, which is
    baked into the JS bundle at build time. The two are read from different
    places and can disagree: a deploy that ships the Python but serves a cached
    or stale asset bundle leaves an old UI talking to a new backend.

    That disagreement is not theoretical — it hid a fixed bug for a full round
    of testing, because Frappe's own "Installed Apps" reports the Python
    version while the screen reports the bundle, and nothing compared them.
    Deliberately unguarded: it carries no data, and a version banner must
    render for whoever is looking at the broken screen.
    """
    try:
        import neotec_insight
        return {"backend": getattr(neotec_insight, "__version__", None)}
    except Exception:
        return {"backend": None}


@frappe.whitelist()
def get_brand():
    """Saved Brand Kit, as {company_or_'default': {...}}. Empty when unset."""
    try:
        raw = frappe.db.get_single_value("Insight Menu Settings", "brand_json")
        data = json.loads(raw) if raw else {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


@frappe.whitelist()
def save_brand(company=None, brand=None):
    """Persist one company's Brand Kit. `company` empty means the fallback."""
    if not frappe.has_permission("Insight Menu Settings", "write"):
        frappe.throw(_("Not permitted to change the print setup."))
    if isinstance(brand, str):
        brand = json.loads(brand or "{}")
    if not isinstance(brand, dict):
        frappe.throw(_("Invalid print setup payload."))
    key = str(company or "default")[:140]

    doc = frappe.get_single("Insight Menu Settings")
    try:
        store = json.loads(doc.brand_json) if doc.brand_json else {}
        if not isinstance(store, dict):
            store = {}
    except Exception:
        store = {}
    # Cap the number of stored companies so a runaway client cannot grow the
    # Single row without bound.
    if key not in store and len(store) >= 60:
        frappe.throw(_("Too many saved print setups. Remove some first."))
    store[key] = _clean_brand(brand)
    doc.brand_json = json.dumps(store)
    doc.save(ignore_permissions=True)
    frappe.db.commit()
    return {"ok": True}
