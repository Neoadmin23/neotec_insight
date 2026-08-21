"""Cash Flow Classification API (v2.87.0) — Phase B/C/D of the build spec.

Same isolation boundary as the rest of Cash Flow Forecast. Imports from
api/cash_flow_forecast.py are within-feature (both files are this one
isolated feature), not a P&L/report-engine dependency — reusing
_require_read/_require_write and the account/GL-fetch helpers already
built there rather than duplicating them.
"""

from __future__ import annotations

import json

import frappe
from frappe import _
from frappe.utils import cint, flt, get_last_day

from neotec_insight.neotec_insight.api.cash_flow_forecast import (
    _require_read,
    _require_write,
)
from neotec_insight.neotec_insight.utils.cash_flow_forecast import (
    fetch_all_transfer_legs,
    fetch_bank_leg_and_transfer_vouchers,
    fetch_binding_gl_rows,
    resolve_cash_accounts,
    resolve_company_fy_start_month,
)
from neotec_insight.neotec_insight.utils.cash_flow_classification import (
    classify_transaction,
    infer_transaction_type,
    mine_candidate_rules,
    update_rule_stats,
)


def _fy_date_range(fiscal_year: int, company: str | None) -> tuple[str, str]:
    """Same logic as run()'s date range in cash_flow_forecast.py — a fiscal
    year does not sit inside one calendar year for a non-January-start
    company. Not imported from there (it's inlined in that function, not a
    standalone helper) — small enough that duplicating it here beats adding
    a cross-file dependency for four lines, but noted so nobody 'fixes' one
    copy without the other if this logic ever needs to change."""
    fy_start_month = resolve_company_fy_start_month(company)
    if fy_start_month == 1:
        return f"{fiscal_year}-01-01", f"{fiscal_year}-12-31"
    from_date = f"{fiscal_year}-{fy_start_month:02d}-01"
    end_year, end_month = fiscal_year + 1, fy_start_month - 1
    return from_date, get_last_day(f"{end_year}-{end_month:02d}-01")


# ─────────────────────────────────────────────────────────────────────────
# Rule governance (Phase D) — Candidate → Under Review → Approved → Active.
# The doctype controller enforces which transitions are legal; this layer
# just calls .save() and lets it validate.
# ─────────────────────────────────────────────────────────────────────────

@frappe.whitelist()
def list_rules(status: str | None = None):
    _require_read()
    filters = {"status": status} if status else {}
    return frappe.get_all(
        "Insight Cash Flow Classification Rule", filters=filters,
        fields=["name", "pattern", "match_field", "target_line", "status",
                "historical_support", "historical_precision", "rolling_precision",
                "times_suggested", "times_confirmed", "times_corrected",
                "sample_transactions", "created_by_user", "created_on",
                "approved_by_user", "approved_on"],
        order_by="modified desc", limit_page_length=0)


@frappe.whitelist()
def set_rule_status(name: str, status: str):
    """The only way a rule moves through its lifecycle. Never called
    automatically by mining or by the Queue — always a deliberate action a
    person took, which is the whole point of the governance model."""
    _require_write()
    doc = frappe.get_doc("Insight Cash Flow Classification Rule", name)
    doc.status = status
    doc.save()
    return doc.as_dict()


@frappe.whitelist()
def mine_rules(min_support: int = 3, min_purity: float = 95.0):
    """Generates Candidates from confirmed Override history — never
    anything higher-status than Candidate, and never overwrites an existing
    non-Retired rule with the same pattern/field/target (mining is additive
    and idempotent-ish: re-running it after new confirmations won't
    duplicate what a reviewer already acted on)."""
    _require_write()
    # Python type hints are not enforced at runtime, and this site does not
    # auto-cast whitelisted-method parameters to their annotation — both
    # arrive here as strings over HTTP regardless of the `int`/`float`
    # above. Confirmed by the actual production crash: min_purity / 100 on
    # an uncast string raised "unsupported operand type(s) for /: 'str' and
    # 'int'" the first time this endpoint was called. Every other numeric
    # parameter in this API surface is now checked (see the v2.87.2
    # changelog entry) rather than assuming a type hint did its job.
    min_support = cint(min_support)
    min_purity = flt(min_purity)
    overrides = frappe.get_all(
        "Insight Cash Flow Override", fields=["line", "voucher_type", "voucher_no"],
        limit_page_length=0)
    labeled_rows = []
    for o in overrides:
        gl = frappe.get_all(
            "GL Entry",
            filters={"voucher_type": o["voucher_type"], "voucher_no": o["voucher_no"], "is_cancelled": 0},
            fields=["remarks"], limit_page_length=1)
        labeled_rows.append({"remarks": (gl[0]["remarks"] if gl else "") or "", "target_line": o["line"]})

    candidates = mine_candidate_rules(labeled_rows, min_support=min_support, min_purity=min_purity / 100)
    created = 0
    for c in candidates:
        exists = frappe.db.exists(
            "Insight Cash Flow Classification Rule",
            {"pattern": c["pattern"], "match_field": c["match_field"],
             "target_line": c["target_line"], "status": ["!=", "Retired"]})
        if exists:
            continue
        frappe.get_doc({
            "doctype": "Insight Cash Flow Classification Rule",
            "pattern": c["pattern"], "match_field": c["match_field"], "target_line": c["target_line"],
            "historical_support": c["historical_support"], "historical_precision": c["historical_precision"],
            "sample_transactions": "\n".join(c["sample_transactions"]),
        }).insert()
        created += 1
    return {"mined": len(candidates), "created": created, "skipped_existing": len(candidates) - created}


def _update_rule(rule_name: str, decision: str) -> None:
    rule = frappe.get_doc("Insight Cash Flow Classification Rule", rule_name)
    current = {"times_suggested": rule.times_suggested or 0, "times_confirmed": rule.times_confirmed or 0,
               "times_corrected": rule.times_corrected or 0}
    updated = update_rule_stats(current, decision)
    rule.times_suggested = updated["times_suggested"]
    rule.times_confirmed = updated["times_confirmed"]
    rule.times_corrected = updated["times_corrected"]
    rule.rolling_precision = updated["rolling_precision"]
    rule.last_used_on = frappe.utils.now_datetime()
    rule.save()


# ─────────────────────────────────────────────────────────────────────────
# The Classification Queue (Phase C) — every real cash-leg transaction no
# Account Binding and no existing Override already claims.
# ─────────────────────────────────────────────────────────────────────────

def _claimed_voucher_keys(company: str | None, from_date, to_date,
                          cash_accounts: list[str]) -> set[tuple[str, str]]:
    """Every (voucher_type, voucher_no) already accounted for by an active
    Line Binding or an existing Override. What's left after subtracting
    this from every real cash-leg voucher in the period is the Queue."""
    claimed: set[tuple[str, str]] = set()
    lines = frappe.get_all("Insight Cash Flow Line", filters={"is_active": 1}, pluck="name")
    for line_name in lines:
        line_doc = frappe.get_doc("Insight Cash Flow Line", line_name)
        for b in (line_doc.bindings or []):
            binding = {
                "account": b.account,
                "cost_centers": [row.cost_center for row in (b.cost_centers or [])],
                "project": b.project, "party_type": b.party_type, "party": b.party,
            }
            for row in fetch_binding_gl_rows(binding, company, from_date, to_date):
                claimed.add((row["voucher_type"], row["voucher_no"]))
    for o in frappe.get_all("Insight Cash Flow Override", fields=["voucher_type", "voucher_no"],
                            limit_page_length=0):
        claimed.add((o["voucher_type"], o["voucher_no"]))
    return claimed


@frappe.whitelist()
def list_unclassified_transactions(fiscal_year: int, company: str | None = None,
                                   bank_accounts: str | list | None = None, limit: int = 200):
    _require_read()
    fy = int(fiscal_year)
    restrict = json.loads(bank_accounts) if isinstance(bank_accounts, str) else bank_accounts
    cash_accounts = resolve_cash_accounts(company, restrict_to=restrict or None)
    from_date, to_date = _fy_date_range(fy, company)

    claimed = _claimed_voucher_keys(company, from_date, to_date, cash_accounts)

    filters = {"account": ["in", cash_accounts], "posting_date": ["between", [from_date, to_date]],
               "is_cancelled": 0}
    if company:
        filters["company"] = company
    all_rows = frappe.get_all(
        "GL Entry", filters=filters,
        # GL Entry's real column is "against" (Data, comma-separated other-
        # side accounts/parties) — labeled "Against Account" in the UI,
        # which is exactly how "against_account" ended up requested here
        # instead of the real fieldname and crashed the first time this ran
        # against a live site. Same bug shape as v2.86.2/v2.86.3: a label
        # mistaken for a fieldname. Fetched as "against" here; renamed to
        # "against_account" below for every downstream consumer (the
        # engine's transaction dict, the Queue's output, the frontend's
        # QueueRow type) — none of which needed to change, since they only
        # ever cared about the key name, not where it came from.
        fields=["voucher_type", "voucher_no", "account", "posting_date", "debit", "credit",
                "against", "remarks", "cost_center"],
        limit_page_length=0)

    # One representative row per voucher — the largest-magnitude cash leg,
    # for a voucher that touches more than one cash account (a split payment).
    by_voucher: dict[tuple[str, str], dict] = {}
    for r in all_rows:
        key = (r["voucher_type"], r["voucher_no"])
        if key in claimed:
            continue
        existing = by_voucher.get(key)
        magnitude = abs(flt(r["debit"]) - flt(r["credit"]))
        if not existing or magnitude > abs(flt(existing["debit"]) - flt(existing["credit"])):
            by_voucher[key] = r

    # Internal transfers are excluded from the Queue the same way they're
    # excluded from every line's Actual — they're not unclassified spend,
    # they're money that moved bank to bank.
    transfer_legs = fetch_all_transfer_legs(company, from_date, to_date, cash_accounts)
    for leg in transfer_legs:
        by_voucher.pop((leg["voucher_type"], leg["voucher_no"]), None)

    active_rules = frappe.get_all(
        "Insight Cash Flow Classification Rule", filters={"status": "Active"},
        fields=["name", "pattern", "match_field", "target_line", "rolling_precision", "historical_precision"],
        limit_page_length=0)

    out = []
    for key, r in list(by_voucher.items())[: int(limit)]:
        against_account = r.get("against") or ""
        txn = {"remarks": r.get("remarks") or "", "against_account": against_account}
        suggestion = classify_transaction(txn, active_rules)
        out.append({
            "voucher_type": r["voucher_type"], "voucher_no": r["voucher_no"],
            "account": r["account"], "posting_date": str(r["posting_date"]),
            "debit": flt(r["debit"]), "credit": flt(r["credit"]),
            "against_account": against_account, "remarks": r.get("remarks"),
            "cost_center": r.get("cost_center"),
            "inferred_type": infer_transaction_type(r["debit"], r["credit"]),
            "suggestion": suggestion,
        })
    # Highest-confidence suggestions first — the batch-confirm candidates
    # surface at the top, transactions with no suggestion at all sink down.
    out.sort(key=lambda t: -(t["suggestion"].get("confidence") or -1))
    return {"total_unclassified": len(by_voucher), "shown": len(out), "transactions": out}


@frappe.whitelist()
def confirm_classification(voucher_type: str, voucher_no: str, line: str, note: str | None = None,
                           suggested_by_rule: str | None = None, suggested_line: str | None = None,
                           confidence: float | None = None):
    """Writes an Insight Cash Flow Override — the single source of truth
    for a confirmed classification, whatever tier produced it. Rule
    provenance is recorded so the rule that got it right (or wrong) can be
    told which."""
    _require_write()
    # confidence is stored on the doc, not used in arithmetic here, so
    # Frappe's own DocField coercion (a documented, reliable behavior,
    # unlike whitelisted-method parameter type hints — see mine_rules)
    # would likely have handled a string value fine on save. Cast anyway,
    # cheaply, rather than rely on that distinction holding forever.
    confidence = flt(confidence) if confidence is not None else None
    changed = bool(suggested_by_rule and suggested_line and suggested_line != line)
    decision_kind = "Manual"
    if suggested_by_rule:
        decision_kind = "Rule Suggested — Changed" if changed else "Rule Suggested — Confirmed"
    doc = frappe.get_doc({
        "doctype": "Insight Cash Flow Override",
        "line": line, "voucher_type": voucher_type, "voucher_no": voucher_no,
        "note": note or "", "decision_kind": decision_kind,
        "suggested_by_rule": suggested_by_rule,
        "suggested_line": suggested_line if changed else None,
        "confidence_at_decision": confidence,
    })
    doc.insert()
    if suggested_by_rule:
        _update_rule(suggested_by_rule, "corrected" if changed else "confirmed")
    return {"ok": True, "override": doc.name}


@frappe.whitelist()
def reject_suggestion(suggested_by_rule: str):
    """The suggestion was wrong and the accountant has no better answer
    ready right now — no Override is created (the transaction stays in the
    Queue for manual classification), but the rule still learns it missed."""
    _require_write()
    _update_rule(suggested_by_rule, "corrected")
    return {"ok": True}


@frappe.whitelist()
def batch_confirm(items: str | list):
    """items: [{voucher_type, voucher_no, line, suggested_by_rule,
    suggested_line, confidence}, ...] — restricted by the caller (the
    frontend) to same-rule, high-confidence rows only, per the build spec's
    'Allow batch-confirm only for high-confidence rows from the same
    approved rule, with visible totals.' This endpoint does not itself
    enforce that restriction — it trusts what it's given and writes an
    Override per item, same as confirm_classification called in a loop."""
    _require_write()
    rows = json.loads(items) if isinstance(items, str) else items
    results = []
    for it in rows:
        results.append(confirm_classification(
            it["voucher_type"], it["voucher_no"], it["line"], it.get("note"),
            it.get("suggested_by_rule"), it.get("suggested_line"), it.get("confidence")))
    return {"ok": True, "count": len(results)}
