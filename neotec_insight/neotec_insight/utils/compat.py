"""Frappe v15 / v16 compatibility (v2.93.0).

One branch runs on both. Everything that differs between the two lives here,
so a future v17 change is made in one place.

* Desk moved from /app to /desk in v16. Links are built with `desk_url`, and
  stored desk paths (Quick Links) are translated with `to_desk_path`, so a
  link saved on one version still works after an upgrade.
"""
from __future__ import annotations

import re

import frappe


def frappe_major() -> int:
    """15, 16, … from frappe.__version__ ("16.0.0", "16.0.0-dev", "15.122.0")."""
    try:
        return int(re.match(r"\d+", str(getattr(frappe, "__version__", "15"))).group(0))
    except Exception:
        return 15


def desk_prefix() -> str:
    return "/desk" if frappe_major() >= 16 else "/app"


def desk_url(path: str = "") -> str:
    """desk_url("query-report/General Ledger") -> "/desk/query-report/General Ledger" on v16."""
    path = (path or "").lstrip("/")
    return desk_prefix() + ("/" + path if path else "")


def to_desk_path(url: str) -> str:
    """Translate a stored desk path to this version's prefix; leave anything
    else (external URLs, /insight, /files) untouched."""
    if not url:
        return url
    for old in ("/app", "/desk"):
        if url == old or url.startswith(old + "/") or url.startswith(old + "?"):
            return desk_prefix() + url[len(old):]
    return url


def with_aliases(rows: list, aliases: dict[str, str]) -> list:
    """Select plain fields, then copy each to its alias in Python:
    with_aliases(rows, {"label": "customer_name"}). Replaces "field as alias"
    strings in get_all fields, which Frappe v16's query builder parses more
    strictly — this form behaves the same on v15 and v16."""
    for r in rows or []:
        for alias, src in aliases.items():
            r[alias] = r.get(src)
    return rows
