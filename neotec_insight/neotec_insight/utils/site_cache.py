"""Site-safe caching (v2.90.0).

Replaces process-wide dictionaries keyed only by company name. Those were
shared by every site a worker served (two sites with a company of the same
name read each other's fiscal calendar) and an edit cleared only the worker
that handled it (others kept the old calendar or account list until restart).

Two layers:
  * request-scoped memo on frappe.local — gone at the end of the request/job;
  * Frappe's shared cache (Redis), which Frappe namespaces per site, with a
    TTL so nothing can stay wrong for long even if an invalidation is missed.
Invalidation deletes the shared key, so every worker of the site sees it.
"""
from __future__ import annotations

import frappe

PREFIX = "neotec_insight:"
DEFAULT_TTL = 600


def _redis():
    c = getattr(frappe, "cache", None)
    if c is None:
        return None
    try:
        return c() if callable(c) and not hasattr(c, "get_value") else c
    except Exception:
        return None


def _local() -> dict:
    try:
        store = getattr(frappe.local, "neotec_insight_cache", None)
        if store is None:
            store = {}
            frappe.local.neotec_insight_cache = store
        return store
    except Exception:
        return {}


def cached(key: str, compute, ttl: int = DEFAULT_TTL):
    local = _local()
    if key in local:
        return local[key]
    r = _redis()
    val = None
    if r is not None:
        try:
            val = r.get_value(PREFIX + key)
        except Exception:
            val = None
    if val is None:
        val = compute()
        if r is not None:
            try:
                r.set_value(PREFIX + key, val, expires_in_sec=ttl)
            except Exception:
                pass
    local[key] = val
    return val


def invalidate(*keys: str) -> None:
    local = _local()
    r = _redis()
    for k in keys:
        local.pop(k, None)
        if r is not None:
            try:
                r.delete_value(PREFIX + k)
            except Exception:
                pass
