"""Nitaqat-style Saudization estimate (v3.1.0). Pure: no site needed.

Every weighting and threshold comes from Insight Saudization Rules, which the
client verifies in Qiwa — nothing here is presented as the official band."""
from __future__ import annotations

import math

BANDS = ("platinum", "high_green", "medium_green", "low_green")
BAND_LABEL = {"platinum": "Platinum", "high_green": "High Green", "medium_green": "Medium Green",
              "low_green": "Low Green", "red": "Red"}


def _norm(v) -> str:
    return str(v or "").strip().lower()


def _list(text) -> set[str]:
    return {_norm(x) for x in str(text or "").replace(",", "\n").split("\n") if _norm(x)}


def classify(emp: dict, rules: dict) -> tuple[str, float, str]:
    """(category, weight in the Saudi count, note) for one active employee."""
    nat = _norm(emp.get("nationality"))
    if not nat:
        return "Nationality missing", 0.0, "not counted as Saudi until nationality is filled in"
    saudi = nat in _list(rules.get("saudi_values")) or any(s in nat for s in ("saudi", "سعود"))
    gcc = nat in _list(rules.get("counted_as_saudi_values"))
    if not (saudi or gcc):
        return "Non-Saudi", 0.0, ""
    base = "Saudi" if saudi else "Counted as Saudi"
    if rules.get("disability_field") and emp.get("disabled"):
        return f"{base} — disability", float(rules.get("disabled_weight") or 4), ""
    weight, note = 1.0, ""
    if _norm(emp.get("employment_type")) in _list(rules.get("part_time_values")):
        return f"{base} — part-time", float(rules.get("part_time_weight") or 0.5), ""
    if (rules.get("salary_source") or "Not used") != "Not used":
        wage = emp.get("wage")
        full, floor_ = float(rules.get("min_wage_full") or 0), float(rules.get("min_wage_floor") or 0)
        if wage is None:
            note = "wage unknown — counted fully; check"
        elif floor_ and wage < floor_:
            return f"{base} — below wage floor", 0.0, ""
        elif full and wage < full:
            return f"{base} — below full-count wage", float(rules.get("weight_below_full") or 0.5), ""
    return base, weight, note


def band_for(pct: float, rules: dict) -> str:
    for b in BANDS:
        th = rules.get(b)
        if th not in (None, "", 0) and pct >= float(th):
            return b
    return "red" if any(rules.get(b) for b in BANDS) else "unknown"


def saudis_needed(weighted: float, total: int, threshold_pct: float) -> int:
    """Full-weight Saudis to hire so (S+n)/(T+n) reaches the threshold."""
    th = threshold_pct / 100.0
    if th >= 1:
        return -1
    if total and weighted / total >= th:
        return 0
    return max(0, math.ceil((th * total - weighted) / (1 - th) - 1e-9))


def estimate(employees: list[dict], rules: dict) -> dict:
    cats: dict[str, dict] = {}
    notes = []
    weighted = 0.0
    for e in employees:
        cat, w, note = classify(e, rules)
        c = cats.setdefault(cat, {"category": cat, "employees": 0, "weight": w, "weighted": 0.0})
        c["employees"] += 1
        c["weighted"] += w
        weighted += w
        if note:
            notes.append(f"{e.get('name')}: {note}")
    total = len(employees)
    pct = round(weighted / total * 100, 2) if total else 0.0
    band = band_for(pct, rules)
    nxt = None
    order = ["red", "low_green", "medium_green", "high_green", "platinum"]
    if band in order and band != "platinum":
        for b in order[order.index(band) + 1:]:
            if rules.get(b):
                nxt = {"band": b, "label": BAND_LABEL[b], "threshold": float(rules[b]),
                       "saudis_needed": saudis_needed(weighted, total, float(rules[b]))}
                break
    return {"total": total, "weighted_saudis": round(weighted, 2), "pct": pct, "band": band,
            "band_label": BAND_LABEL.get(band, "Not set (enter thresholds)"), "next": nxt,
            "categories": sorted(cats.values(), key=lambda c: -c["employees"]), "notes": notes[:30],
            "plain_saudi": sum(c["employees"] for k, c in cats.items() if k.startswith("Saudi"))}
