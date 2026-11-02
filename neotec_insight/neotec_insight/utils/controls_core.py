"""Insight Controls — continuous finance checks (v3.5.0). Pure: no site needed.

A control rule finds issues; each issue is one Finding that lives across runs:
  Open → Acknowledged (someone is on it) → Fixed (resolved) | Dismissed (accepted)
The runner never raises the same issue twice: it keys every finding, keeps
first_seen / last_seen, closes findings whose cause disappeared (for rules that
can tell) or whose document was cancelled, reopens a Fixed finding that comes
back, and never reopens one a person Dismissed.
"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict

OPEN, ACK, FIXED, DISMISSED = "Open", "Acknowledged", "Fixed", "Dismissed"
ACTIVE = (OPEN, ACK)

# code → settings. self_closing: absence on a later run means it was fixed.
CATALOGUE: dict[str, dict] = {
    "DUP_PURCHASE_INVOICE": {
        "title": "Possible duplicate purchase invoice", "severity": "High", "threshold": 3, "lookback_days": 90,
        "threshold_label": "Days apart for the same supplier and amount",
        "description": "Submitted purchase invoices from one supplier with the same bill number, or with the "
                       "same total within the threshold days.", "self_closing": False},
    "DUP_SALES_INVOICE": {
        "title": "Possible duplicate sales invoice", "severity": "Medium", "threshold": 2, "lookback_days": 90,
        "threshold_label": "Days apart for the same customer and amount",
        "description": "Submitted sales invoices to one customer with the same total within the threshold days.",
        "self_closing": False},
    "NEGATIVE_STOCK": {
        "title": "Negative stock", "severity": "High", "threshold": 0, "lookback_days": 0,
        "threshold_label": "Not used",
        "description": "An item whose quantity in a warehouse is below zero. Closes itself once corrected.",
        "self_closing": True},
    "ROUND_CASH_JOURNAL": {
        "title": "Large round cash/bank journal without reference", "severity": "Medium", "threshold": 50000,
        "lookback_days": 60, "threshold_label": "Smallest round amount to report",
        "description": "A journal entry moving a large round amount through a cash or bank account with no "
                       "cheque/reference number.", "self_closing": False},
    "RATE_OUTLIER": {
        "title": "Stock received at an unusual rate", "severity": "Medium", "threshold": 50, "lookback_days": 30,
        "threshold_label": "Percent away from the item's average incoming rate (previous 90 days)",
        "description": "Stock received in a warehouse at a rate far above or below the item's recent average.",
        "self_closing": False},
    "DUP_STOCK_ENTRY": {
        "title": "Possible duplicate stock entry", "severity": "Medium", "threshold": 0, "lookback_days": 60,
        "threshold_label": "Not used",
        "description": "Submitted stock entries of the same type, date, warehouses and value.",
        "self_closing": False},
    "UNALLOCATED_PAYMENT": {
        "title": "Payment not allocated to any invoice", "severity": "Low", "threshold": 30, "lookback_days": 365,
        "threshold_label": "Days a payment may stay unallocated",
        "description": "A payment or receipt with an unallocated amount older than the threshold days.",
        "self_closing": True},
}


def plan(existing: dict[str, str], found: set[str], self_closing: bool,
         gone: set[str] | None = None) -> dict[str, list[str]]:
    """What to do with this rule's findings after a run.

    existing  key → current status of stored findings
    found     keys detected in this run
    gone      keys whose document was cancelled or deleted
    → {"create": [...], "touch": [...], "reopen": [...], "close": [...]}"""
    gone = gone or set()
    out = {"create": [], "touch": [], "reopen": [], "close": []}
    for key in sorted(found):
        st = existing.get(key)
        if st is None:
            out["create"].append(key)
        elif st == FIXED:
            (out["reopen"] if self_closing else out["touch"]).append(key)
        else:                                   # Open, Acknowledged, Dismissed: keep the person's decision
            out["touch"].append(key)
    for key, st in sorted(existing.items()):
        if key in found or st not in ACTIVE:
            continue
        if self_closing or key in gone:
            out["close"].append(key)
    return out


def _d(x) -> dt.date:
    return x if isinstance(x, dt.date) and not isinstance(x, dt.datetime) else dt.date.fromisoformat(str(x)[:10])


def same_amount_within(rows: list[dict], party_key: str, days: int) -> list[tuple[dict, dict]]:
    """Pairs of documents for one party with the same amount, at most `days`
    apart. Each later document is paired with its nearest earlier twin."""
    by: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        by[(r[party_key], round(float(r["amount"]), 2))].append(r)
    pairs = []
    for group in by.values():
        group.sort(key=lambda r: (_d(r["posting_date"]), r["name"]))
        for i in range(1, len(group)):
            a, b = group[i - 1], group[i]
            if (_d(b["posting_date"]) - _d(a["posting_date"])).days <= days:
                pairs.append((a, b))
    return pairs


def same_bill_no(rows: list[dict]) -> list[tuple[dict, dict]]:
    by: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        bill = (r.get("bill_no") or "").strip().lower()
        if bill:
            by[(r["supplier"], bill)].append(r)
    pairs = []
    for group in by.values():
        group.sort(key=lambda r: (_d(r["posting_date"]), r["name"]))
        pairs += [(group[0], g) for g in group[1:]]
    return pairs


def rate_outliers(receipts: list[dict], averages: dict[tuple, float], pct: float) -> list[dict]:
    """Receipts whose rate differs from (item, warehouse) average by more than pct %."""
    out = []
    for r in receipts:
        avg = averages.get((r["item_code"], r["warehouse"]))
        rate = float(r.get("incoming_rate") or 0)
        if not avg or not rate:
            continue
        diff = (rate - avg) / avg * 100
        if abs(diff) > pct:
            out.append({**r, "average": round(avg, 4), "diff_pct": round(diff, 1)})
    return out


def pair_key(rule: str, a: str, b: str) -> str:
    """Order-independent key so A↔B and B↔A are one finding."""
    x, y = sorted((a, b))
    return f"{rule}:{x}:{y}"
