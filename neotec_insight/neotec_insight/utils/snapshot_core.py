"""Pure helpers for automatic backup snapshots (v2.89.0). No frappe import,
so every rule here is unit-tested without a site."""

from __future__ import annotations

import base64
import gzip
import hashlib
import json
from datetime import date, datetime

# How many of each kind to keep. Pinned snapshots are never removed.
RETENTION: dict[str, int] = {
    "scheduled": 30,     # a month of dailies
    "pre_migrate": 20,   # the last twenty upgrades
    "post_migrate": 10,
    "pre_restore": 20,
    "pre_change": 60,    # bulk edits are frequent; each is small (scoped)
    "manual": 50,
}

ALLOCATION_ENTRY = "Insight Allocation Entry"


def _default(o):
    if isinstance(o, (datetime, date)):
        return o.isoformat(sep=" ") if isinstance(o, datetime) else o.isoformat()
    return str(o)


def dumps(obj) -> str:
    return json.dumps(obj, default=_default, sort_keys=True, ensure_ascii=False)


def fingerprint(data: dict) -> str:
    """SHA-256 of the data part only, so the export time and version do not
    make two identical backups look different."""
    return hashlib.sha256(dumps(data or {}).encode("utf-8")).hexdigest()


def encode(bundle: dict) -> tuple[str, float]:
    raw = dumps(bundle).encode("utf-8")
    packed = base64.b64encode(gzip.compress(raw, compresslevel=6)).decode("ascii")
    return packed, round(len(packed) / 1024.0, 1)


def decode(packed: str) -> dict:
    return json.loads(gzip.decompress(base64.b64decode(packed)).decode("utf-8"))


def build_index(bundle: dict) -> dict:
    """Small lookups stored beside the payload: allocation entries per rule and
    per rule-year, so the recovery screen can point at the right snapshot
    without unpacking each one."""
    per_rule: dict[str, int] = {}
    per_rule_year: dict[str, dict[str, int]] = {}
    for e in (bundle.get("data") or {}).get(ALLOCATION_ENTRY) or []:
        r = e.get("rule") or ""
        per_rule[r] = per_rule.get(r, 0) + 1
        y = str(e.get("period_month") or "")[:4]
        per_rule_year.setdefault(r, {})
        per_rule_year[r][y] = per_rule_year[r].get(y, 0) + 1
    return {"allocation_entries_by_rule": per_rule,
            "allocation_entries_by_rule_year": per_rule_year}


def to_prune(rows: list[dict], retention: dict[str, int] | None = None) -> list[str]:
    """Names to delete. rows: [{name, snapshot_type, pinned, creation}], any
    order. Keeps the newest N of each type (N from retention, unknown types
    keep 50) and every pinned one."""
    retention = retention or RETENTION
    by_type: dict[str, list[dict]] = {}
    for r in rows:
        by_type.setdefault(r.get("snapshot_type") or "manual", []).append(r)
    out = []
    for kind, items in by_type.items():
        items = sorted(items, key=lambda r: str(r.get("creation") or ""), reverse=True)
        keep = retention.get(kind, 50)
        unpinned = [r for r in items if not r.get("pinned")]
        out.extend(r["name"] for r in unpinned[keep:])
    return sorted(out)


def alert_text(drops: list[dict]) -> str:
    """One line per doctype whose count fell, for the snapshot's alert field."""
    return "\n".join(f"{d['doctype']}: {d['before']} → {d['after']} ({d['lost']} gone)"
                     for d in drops)


def entries_for_rule(bundle: dict, rule: str, year: int | None = None) -> list[dict]:
    rows = (bundle.get("data") or {}).get(ALLOCATION_ENTRY) or []
    out = [r for r in rows if r.get("rule") == rule]
    if year:
        out = [r for r in out if str(r.get("period_month") or "")[:4] == str(year)]
    return out
