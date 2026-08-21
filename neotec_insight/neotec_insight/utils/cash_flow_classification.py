"""Cash Flow Classification engine (v2.87.0).

Phase B of Cash_Flow_Classification_Final_Verdict_and_Build_Spec.docx.
Standalone, same isolation boundary as the rest of Cash Flow Forecast — no
import from api/report.py, utils/execution.py, utils/allocation.py, or
utils/fiscal_year.py.

The decision cascade this module implements, in order:
  1. Account Binding (already built, api/cash_flow_forecast.py) — deterministic,
     tried first, not touched by this module at all.
  2. A learned rule (this module) — phrase match against an Active rule,
     confidence from the rule's own rolling precision.
  3. A human, via the Classification Queue — whatever neither of the above
     resolves confidently.
  4. The reconciliation residual (already built) — runs regardless of which
     tier answered, and is the final proof the whole thing is complete.

Governance, enforced by the doctype controller, not this module: a rule is
mined here as a Candidate. It is never Active until a human approves it.
"""

from __future__ import annotations

from typing import Any

import frappe
from frappe.utils import flt


# ─────────────────────────────────────────────────────────────────────────
# Column E — Transaction Type. Structural, not learned: a bank-side debit
# means cash arrived, a credit means it left. Verified against 2,209 real
# rows at 98.0% agreement (2,164/2,209) before this was written — the 45
# exceptions (reversals, opening entries, contra postings) are NOT modeled
# here. Encoding those needs a human inspecting each one, per the build
# spec's own instruction ("must be analyzed and encoded... rather than
# ignored") — this function deliberately does the 98% case only and leaves
# the rest to whoever reviews the Queue, rather than guessing at a pattern
# for exceptions nobody has actually characterized yet.
# ─────────────────────────────────────────────────────────────────────────

def infer_transaction_type(debit: float, credit: float) -> str:
    """Pure. The one structural rule for Column E — no learning, no rules,
    no exceptions layer (see module docstring for why)."""
    return "Cash In" if flt(debit) > flt(credit) else "Cash Out"


# ─────────────────────────────────────────────────────────────────────────
# Column G — the primary classification target. Rule matching + scoring.
# ─────────────────────────────────────────────────────────────────────────

def pattern_matches(pattern: str, transaction: dict, match_field: str) -> bool:
    """Pure. `pattern` is already normalized (lowercase, stripped) at rule
    save time — see InsightCashFlowClassificationRule; this function does
    not re-normalize, so a caller passing raw mixed-case text will simply
    never match, which is a cheap, safe failure mode (a rule silently never
    firing) rather than a confusing partial-match one."""
    remarks = (transaction.get("remarks") or "").lower()
    against = (transaction.get("against_account") or "").lower()
    if match_field == "Remarks":
        haystack = remarks
    elif match_field == "Against Account":
        haystack = against
    else:  # "Remarks + Against Account"
        haystack = remarks + " " + against
    return pattern in haystack


def score_candidates(transaction: dict, active_rules: list[dict]) -> list[dict]:
    """Pure. active_rules: [{name, pattern, match_field, target_line,
    rolling_precision}]. Returns one candidate per matching rule — every
    match, not just the best one, since resolve_classification below needs
    to see all of them to detect a conflict between rules pointing at
    different lines."""
    out = []
    for rule in active_rules:
        if pattern_matches(rule["pattern"], transaction, rule["match_field"]):
            out.append({
                "rule": rule["name"],
                "target_line": rule["target_line"],
                "confidence": flt(rule.get("rolling_precision") or rule.get("historical_precision") or 0),
            })
    return out


def resolve_classification(
    candidates: list[dict],
    high_threshold: float = 95.0,
    medium_threshold: float = 70.0,
) -> dict:
    """Pure. The single place tier assignment happens — High / Medium / Low
    / Conflict, matching the build spec's table exactly.

    Conflict overrides confidence, always. Two rules pointing at different
    lines are a disagreement regardless of which one scores higher — the
    build spec is explicit that a numeric score must not silently win a
    disagreement ("Override numeric score and require review when important
    signals disagree"). A rule with 99% confidence does not get to
    steamroll one with 60% if they disagree about the answer; both go to a
    human, together, so the human sees the actual disagreement instead of
    an falsely-confident single suggestion."""
    if not candidates:
        return {"tier": "none", "target_line": None, "confidence": None, "rule": None, "alternatives": []}

    distinct_lines = {c["target_line"] for c in candidates}
    if len(distinct_lines) > 1:
        ranked = sorted(candidates, key=lambda c: -c["confidence"])
        return {"tier": "conflict", "target_line": None, "confidence": None, "rule": None,
                "alternatives": ranked[:3]}

    best = max(candidates, key=lambda c: c["confidence"])
    if best["confidence"] >= high_threshold:
        tier = "high"
    elif best["confidence"] >= medium_threshold:
        tier = "medium"
    else:
        tier = "low"
    # "rule" is the winning candidate's own rule name — the frontend needs
    # this to record provenance on confirm and to know which rule to flag
    # on reject. Omitting it here (leaving only target_line/confidence) was
    # a real gap: without it, a Queue confirmation could never credit or
    # correct the rule that actually produced the suggestion.
    return {"tier": tier, "target_line": best["target_line"], "confidence": best["confidence"],
            "rule": best["rule"], "alternatives": [c for c in candidates if c is not best]}


def classify_transaction(transaction: dict, active_rules: list[dict],
                         high_threshold: float = 95.0, medium_threshold: float = 70.0) -> dict:
    """Pure. The whole rule-tier decision for one transaction, in one call —
    score then resolve. Account Binding is NOT part of this function; by
    the time a transaction reaches here it has already failed to match any
    binding (see api/cash_flow_forecast.py's queue builder)."""
    candidates = score_candidates(transaction, active_rules)
    return resolve_classification(candidates, high_threshold, medium_threshold)


# ─────────────────────────────────────────────────────────────────────────
# Rule mining — Candidate generation from confirmed history. Produces
# Candidates only; nothing here ever sets a rule's status to Active. Phrase-
# based (2-4 word n-grams), Remarks-only by default: the build spec and this
# module's own earlier backtest agree that Against Account alone, or mixed
# in blindly, is not reliably safe — a reviewer can widen match_field on a
# specific candidate by hand if the evidence supports it, but mining never
# does that automatically.
# ─────────────────────────────────────────────────────────────────────────

_STOP = {"the", "a", "an", "of", "to", "for", "and", "or", "in", "on", "at",
        "from", "with", "is", "was", "payment", "paid", "received", "part", "amount"}


def _ngrams(text: str, n_range: tuple[int, int] = (2, 4)) -> set[str]:
    import re
    words = [w for w in re.findall(r"[a-z']+", text) if w not in _STOP and len(w) > 2]
    out = set()
    for n in range(n_range[0], n_range[1] + 1):
        for i in range(len(words) - n + 1):
            out.add(" ".join(words[i:i + n]))
    return out


def mine_candidate_rules(
    labeled_rows: list[dict],
    min_support: int = 3,
    min_purity: float = 0.95,
    max_samples: int = 3,
) -> list[dict]:
    """Pure. labeled_rows: [{remarks, target_line}] — confirmed history
    (from Insight Cash Flow Override, joined back to its voucher's remarks).
    Returns candidate rule dicts, NOT saved documents — the caller decides
    whether/how to persist them as actual Candidate-status rule records.

    min_purity=0.95 matches this module's own verified backtest ("strict"
    configuration): on a voucher-grouped, leakage-safe split of 2,209 real
    rows, this threshold produced 96.8-100% precision on what it actually
    classified. That backtest is the reason min_purity defaults here rather
    than to something looser — the looser version tested at the same time
    produced meaningfully more wrong answers for a modest coverage gain,
    and precision is the number this whole design refuses to compromise."""
    from collections import Counter, defaultdict

    class_phrase_counts: dict[str, Counter] = defaultdict(Counter)
    global_phrase_counts: Counter = Counter()
    phrase_samples: dict[tuple[str, str], list[str]] = defaultdict(list)

    for row in labeled_rows:
        remark = (row.get("remarks") or "").lower()
        cls = row.get("target_line")
        if not cls:
            continue
        phrases = _ngrams(remark)
        for p in phrases:
            class_phrase_counts[cls][p] += 1
            global_phrase_counts[p] += 1
            key = (cls, p)
            if len(phrase_samples[key]) < max_samples:
                phrase_samples[key].append(row.get("remarks") or "")

    out = []
    for cls, counts in class_phrase_counts.items():
        for phrase, count in counts.items():
            if count < min_support:
                continue
            purity = count / global_phrase_counts[phrase]
            if purity < min_purity:
                continue
            out.append({
                "pattern": phrase,
                "match_field": "Remarks",
                "target_line": cls,
                "historical_support": count,
                "historical_precision": round(purity * 100, 1),
                "sample_transactions": phrase_samples[(cls, phrase)],
            })
    out.sort(key=lambda r: (-r["historical_precision"], -r["historical_support"]))
    return out


# ─────────────────────────────────────────────────────────────────────────
# Rule monitoring — every accountant decision updates the rule that
# produced (or failed to produce) the suggestion they responded to.
# ─────────────────────────────────────────────────────────────────────────

def update_rule_stats(current: dict, decision: str) -> dict:
    """Pure. current: {times_suggested, times_confirmed, times_corrected}.
    decision: "confirmed" | "corrected" | "suggested" (suggested increments
    only the shown-count, for rules that get surfaced but the accountant
    hasn't acted on yet — e.g. a batch-suggest that's still pending).

    rolling_precision is confirmed / (confirmed + corrected) — deliberately
    NOT including times_suggested in that denominator, since a suggestion
    the accountant hasn't responded to yet is neither a win nor a loss for
    the rule and must not dilute its precision either direction."""
    out = dict(current)
    out.setdefault("times_suggested", 0)
    out.setdefault("times_confirmed", 0)
    out.setdefault("times_corrected", 0)
    if decision == "suggested":
        out["times_suggested"] += 1
    elif decision == "confirmed":
        out["times_confirmed"] += 1
    elif decision == "corrected":
        out["times_corrected"] += 1
    denom = out["times_confirmed"] + out["times_corrected"]
    out["rolling_precision"] = round(out["times_confirmed"] / denom * 100, 1) if denom else None
    return out
