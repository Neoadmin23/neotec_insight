"""AR / AP ageing from ERPNext's Payment Ledger (v3.7.2). Pure.

Mirrors ERPNext's Accounts Receivable / Payable report: every Payment Ledger
Entry belongs to the document it settles (against_voucher). Grouping by that
document gives, per document:
  own        — the document's own entry (an invoice, a journal, an unallocated payment)
  settled by — the other entries against it, by type: payments, returns (notes), journals/others
  outstanding = sum of all its entries
PLE amounts are signed so that a positive amount increases what is owed (to us
for receivables, by us for payables). Each document's outstanding is aged by
its own due date (or posting date)."""
from __future__ import annotations

import datetime as dt
from collections import defaultdict


def _d(x) -> dt.date:
    return x if isinstance(x, dt.date) and not isinstance(x, dt.datetime) else dt.date.fromisoformat(str(x)[:10])


def kind_of(voucher_type: str, invoice_doctype: str) -> str:
    if voucher_type == "Payment Entry":
        return "paid"
    if voucher_type == invoice_doctype:                # a return invoice settling another
        return "note"
    return "adj"


def documents(ple_rows: list[dict], invoice_doctype: str) -> dict[str, list[dict]]:
    """{party: [document]} from Payment Ledger rows (delinked rows excluded upstream)."""
    groups: dict[tuple, dict] = {}
    for r in ple_rows:
        key = (r["party"], r.get("against_voucher_type") or r["voucher_type"],
               r.get("against_voucher_no") or r["voucher_no"])
        g = groups.setdefault(key, {"party": key[0], "doctype": key[1], "name": key[2], "own": 0.0,
                                    "paid": 0.0, "note": 0.0, "adj": 0.0, "posting_date": None, "due_date": None})
        amt = float(r["amount"] or 0)
        if r["voucher_type"] == key[1] and r["voucher_no"] == key[2]:
            g["own"] += amt
            pd = _d(r["posting_date"])
            g["posting_date"] = min(g["posting_date"] or pd, pd)
            if r.get("due_date"):
                g["due_date"] = _d(r["due_date"])
        else:
            g[kind_of(r["voucher_type"], invoice_doctype)] += -amt     # settlements reduce what is owed
    out: dict[str, list[dict]] = defaultdict(list)
    for g in groups.values():
        if g["posting_date"] is None:                  # only settlements seen (document outside the data): keep, dated by them
            g["posting_date"] = min(_d(r["posting_date"]) for r in ple_rows
                                    if (r.get("against_voucher_no") or r["voucher_no"]) == g["name"])
        g["outstanding"] = round(g["own"] - g["paid"] - g["note"] - g["adj"], 2)
        # ERPNext columns: a document's own positive entry is "invoiced"; a negative own entry (an
        # unallocated payment, a credit journal, a return) shows as settled by its own type.
        if g["own"] >= 0:
            g["invoiced"] = round(g["own"], 2)
        else:
            g["invoiced"] = 0.0
            g[kind_of(g["doctype"], invoice_doctype)] += -g["own"]
        for k in ("paid", "note", "adj"):
            g[k] = round(g[k], 2)
        out[g["party"]].append(g)
    for docs in out.values():
        docs.sort(key=lambda g: (g["posting_date"], g["name"]))
    return out


def age_documents(docs: list[dict], as_of, bounds: list[int], based_on: str = "due",
                  mode: str = "days") -> tuple[list[float], float]:
    """Buckets (len(bounds)+1) and the not-yet-due amount; sets 'age' and 'slab_index' on each document."""
    as_of = _d(as_of)
    buckets = [0.0] * (len(bounds) + 1)
    not_due = 0.0
    for g in docs:
        ref = g["due_date"] if based_on == "due" and g.get("due_date") else g["posting_date"]
        age = ((as_of.year - ref.year) * 12 + as_of.month - ref.month - (1 if as_of.day < ref.day else 0)
               if mode == "months" else (as_of - ref).days)
        g["age"] = age
        if age < 0:
            g["slab_index"] = -1
            not_due += g["outstanding"]
            continue
        idx = next((i for i, b in enumerate(bounds) if age <= b), len(bounds))
        g["slab_index"] = idx
        buckets[idx] += g["outstanding"]
    return [round(b, 2) for b in buckets], round(not_due, 2)


def party_rows(docs_by_party: dict[str, list[dict]], as_of, bounds, based_on="due", mode="days",
               open_only: bool = True) -> list[dict]:
    """One row per party: sums of its documents (open documents only by default)."""
    out = []
    for party, docs in docs_by_party.items():
        shown = [g for g in docs if abs(g["outstanding"]) >= 0.005] if open_only else docs
        buckets, not_due = age_documents(shown, as_of, bounds, based_on, mode)
        out.append({"party": party, "docs": shown,
                    "invoiced": round(sum(g["invoiced"] for g in shown), 2),
                    "paid": round(sum(g["paid"] for g in shown), 2),
                    "note": round(sum(g["note"] for g in shown), 2),
                    "adj": round(sum(g["adj"] for g in shown), 2),
                    "outstanding": round(sum(g["outstanding"] for g in shown), 2),
                    "not_due": not_due, "buckets": buckets})
    return out
