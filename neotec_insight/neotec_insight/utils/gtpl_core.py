# Copyright (c) 2026, Neotec Integrated Solution
# GTPL deferral — output VAT on supplies to government entities under the Saudi
# Government Tenders and Procurement Law (نظام المنافسات والمشتريات الحكومية)
# falls due when the supply is PAID, not when it is invoiced. The invoice
# therefore belongs in the return of the quarter it was settled in, not the
# quarter it was raised in.
#
# Observed in production across two filed IRSAA returns (Q1 and Q2 2025):
# government invoices raised in one quarter were held out of that quarter's
# return entirely and declared in full in the next, with credit notes shown
# separately in the Adjustment column. The lag is a consequence of the payment
# date, not a fixed offset — this module derives it rather than assuming it.
#
# DESIGN: every decision that affects a filed figure lives in a pure function
# below the `PURE CORE` banner, taking plain dicts and returning plain dicts.
# Nothing there imports frappe. The thin wrappers underneath fetch rows and
# hand them to the core. That split exists so the decisions can be tested
# against real filed returns without a site, and so a reviewer can read the
# whole of what moves a number in one screen.
#
# Insight NEVER posts an accounting entry. This module proposes; the preparer
# disposes. Its output is Insight VAT Adjustment rows, the same include/exclude
# mechanism an accountant would otherwise key by hand.
from __future__ import annotations

from typing import Any

TOLERANCE = 0.01  # SAR — payment allocations vs grand total

TRIGGERS = ("earlier_of_receipt_or_order", "receipt_only", "order_only", "invoice_date")

# This module is deliberately free of frappe, of the database and of the clock.
# Everything that moves a filed figure lives here and nowhere else, so it can be
# tested against real filed returns without a site. api/gtpl.py fetches rows and
# calls in; it decides nothing.


def pick_rule(rules: list[dict], as_of: str) -> dict | None:
    """The active rule governing a period ending `as_of`.

    The newest rule effective on or before the period END wins. Rules are dated
    and superseded rather than edited, so re-running a return filed two years ago
    resolves the rule that was in force then and reproduces the figures that were
    filed. That property is the whole reason rules are a doctype and not a
    Single: a settings page that can be edited in place cannot reproduce history.
    """
    candidates = [
        r for r in rules
        if r.get("is_active") and str(r.get("effective_from") or "") <= str(as_of)
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda r: str(r["effective_from"]))


def is_government(customer: str, customer_group: str | None, rule: dict,
                  group_path: list[str] | None = None) -> tuple[bool | None, str]:
    """Whether a customer is a government contracting authority under this rule.

    An explicit per-customer row wins over the group test in BOTH directions.
    The negative direction is not decoration: a sovereign investment fund can sit
    in a group that is otherwise governmental while being invoiced commercially,
    and getting that wrong defers VAT that was actually due.

    `group_path` is the customer's group followed by its ancestors, so a rule
    naming `Government` also captures `Ministries` and `Authorities` if anyone
    nests them under it later. ERPNext customer groups are a nested set; a flat
    membership test would silently stop matching on the day someone adds a child.

    Returns (verdict, why). The verdict is None — NOT False — when the customer
    has no group at all and no override. Unknown scope and out of scope are
    different facts: a government customer with a blank group would otherwise be
    treated as commercial and have its VAT declared a quarter early, with nothing
    in the output to say so. The reason travels with the verdict so the register
    can show why each invoice was treated as it was.
    """
    for row in rule.get("customer_overrides") or []:
        if row.get("customer") == customer:
            gov = row.get("treatment") == "Government"
            return gov, f"override: {row.get('reason') or 'no reason given'}"
    groups = {g.get("customer_group") for g in rule.get("customer_groups") or []}
    path = [g for g in (group_path or ([customer_group] if customer_group else [])) if g]
    if not path:
        return None, "customer has no customer group — GTPL scope cannot be determined"
    hit = next((g for g in path if g in groups), None)
    if hit:
        via = f"customer group {path[0]}" if hit == path[0] else f"customer group {path[0]} (under {hit})"
        return True, via
    return False, f"customer group {path[0]} is not governmental"


def order_release(invoice: dict, rule: dict, payment_orders: list[dict] | None,
                  as_of: str) -> tuple[str | None, float]:
    """(date, fraction) the payment orders release, if any.

    Under the Government Tenders and Procurement Law the أمر الدفع is itself a
    tax point: the supply enters the return of the quarter containing the order
    date, whether or not the money has arrived. A contract milestone can carry
    several orders, which is why these are records rather than one date field on
    the invoice.

    An order with NO amount covers the invoice in full — the ordinary case, and
    the one a preparer should not have to key a figure for. Amounts are only for
    part orders, and a part-ordered invoice returns a fraction with no date, so
    it lands in `partial` and waits for a person. Releasing a whole invoice on a
    part order would declare tax that is not yet due.

    Falls back to a date field on the invoice for sites that already carry one.
    """
    dated = [o for o in (payment_orders or []) if str(o.get("order_date") or "") <= str(as_of)]
    if dated:
        earliest = min(str(o["order_date"]) for o in dated)
        if all(not o.get("amount") for o in dated):
            return earliest, 1.0
        grand = abs(float(invoice.get("base_grand_total") or 0.0))
        covered = sum(abs(float(o.get("amount") or 0.0)) for o in dated)
        if grand <= TOLERANCE or covered >= grand - TOLERANCE:
            return earliest, 1.0
        return None, (covered / grand if grand else 0.0)

    field = (rule.get("order_date_field") or "").strip()
    if field and invoice.get(field) and str(invoice[field]) <= str(as_of):
        return str(invoice[field]), 1.0
    return None, 0.0


def released_on(
    invoice: dict,
    rule: dict,
    allocations: list[dict],
    as_of: str,
    payment_orders: list[dict] | None = None,
) -> tuple[str | None, float]:
    """Date the invoice's output VAT became declarable, and the fraction released.

    `allocations` are payment allocations against this invoice, each
    {"posting_date": "YYYY-MM-DD", "allocated_amount": float}. They are used
    rather than the invoice's current `outstanding_amount` because outstanding is
    a live figure: it tells you what is unpaid TODAY, not what was unpaid at the
    end of a quarter you are re-filing. Re-running Q1 2025 next year has to give
    the answer Q1 2025 gave.

    Returns (release_date | None, fraction 0.0–1.0).
    """
    basis = rule.get("trigger_basis") or "receipt_only"
    if basis == "invoice_date":
        return str(invoice.get("posting_date")), 1.0

    order_date, order_fraction = (None, 0.0)
    if basis in ("order_only", "earlier_of_receipt_or_order"):
        order_date, order_fraction = order_release(invoice, rule, payment_orders, as_of)

    if basis == "order_only":
        # No fall-back to receipt. `order_only` says the tax point IS the order;
        # settling on receipt instead would declare tax in the wrong quarter, and
        # an unreleased invoice is visible in the register as carried-forward
        # whereas a silent basis switch is visible nowhere.
        return order_date, order_fraction

    grand = abs(float(invoice.get("base_grand_total") or 0.0))
    if grand <= TOLERANCE:
        # A zero-value invoice has nothing to defer.
        return str(invoice.get("posting_date")), 1.0

    paid = 0.0
    crossed = None
    for alloc in sorted(allocations, key=lambda a: str(a.get("posting_date") or "")):
        if str(alloc.get("posting_date") or "") > str(as_of):
            break
        paid += abs(float(alloc.get("allocated_amount") or 0.0))
        if crossed is None and paid >= grand - TOLERANCE:
            crossed = str(alloc.get("posting_date"))

    fraction = min(paid / grand, 1.0) if grand else 0.0

    if basis == "earlier_of_receipt_or_order" and order_date:
        if crossed is None or order_date < crossed:
            return order_date, 1.0
    if basis == "earlier_of_receipt_or_order" and crossed is None:
        # Neither route has released it. Report whichever is further along so a
        # part order shows as part-settled rather than as untouched.
        return None, max(fraction, order_fraction)

    return crossed, fraction


def classify_invoice(
    invoice: dict,
    rule: dict,
    period_from: str,
    period_to: str,
    allocations: list[dict],
    customer_group: str | None = None,
    credit_notes: list[dict] | None = None,
    group_path: list[str] | None = None,
    payment_orders: list[dict] | None = None,
) -> dict:
    """Decide what this one invoice does to this one return period.

    `state` is one of:
      not_government  — outside the rule's scope, normal treatment
      credit_note     — a return; declared when issued, never deferred
      in_period       — raised and released this period, already counted
      deferred        — raised this period, not yet due → EXCLUDE
      released        — raised earlier, became due this period → INCLUDE
      still_deferred  — raised earlier, still not due → no action
      already_counted — raised earlier, became due in an EARLIER period → no action
      partial         — part-paid at period end → FLAGGED, never auto-adjusted
    """
    gov, why = is_government(invoice.get("customer"), customer_group, rule, group_path)
    if gov is None:
        # Ungrouped customer. Treated as out of scope for the figures — the same
        # outcome as before — but surfaced as its own state so a government
        # customer that was never grouped shows up as a question rather than
        # vanishing into the commercial population.
        return {"state": "scope_unknown", "action": None, "why": why}
    if not gov:
        return {"state": "not_government", "action": None, "why": why}

    posting = str(invoice.get("posting_date"))
    in_period = period_from <= posting <= period_to

    # Credit notes are declared in the period they are ISSUED. They reduce tax
    # already declared, so deferring them would defer a reduction the taxpayer is
    # entitled to now. This reproduces ACC-SRET-2025-00016 in IRSAA's Q2 filing:
    # issued in Q2, shown in Q2's Adjustment column, against an invoice released
    # into Q2's Amount column in the same return.
    if invoice.get("is_return"):
        return {
            "state": "credit_note",
            "action": None if in_period else "Include",
            "why": f"credit note, declared when issued ({why})",
            "release_date": posting,
        }

    # A credit note against a deferred supply RESOLVES it. The supply will never
    # be paid, so a payment-based trigger would defer it forever — the invoice
    # would sit undeclared indefinitely while its credit note reduced tax that
    # had never been declared.
    #
    # Under the ZATCA three-column layout the resolution is disclosed gross: the
    # invoice enters Amount and the credit note enters Adjustment, in the credit
    # note's period, netting to nil VAT. That is exactly IRSAA's filed Q2 2025 —
    # ACC-SINV-2025-00133 (1,177,115) in Amount against ACC-SRET-2025-00016 in
    # Adjustment — and it is why Box 1.2 was filed at 5,390,429 gross rather than
    # 4,213,314 net. Both presentations carry identical VAT; the rule chooses
    # which pair of figures is disclosed.
    cancelling = [
        cn for cn in (credit_notes or [])
        if period_from <= str(cn.get("posting_date")) <= period_to
    ]
    if cancelling and (rule.get("credit_note_presentation") or "gross_with_adjustment") == "gross_with_adjustment":
        if not in_period:
            return {
                "state": "released_by_credit_note",
                "action": "Include",
                "release_date": str(min(str(cn.get("posting_date")) for cn in cancelling)),
                "why": f"cancelled by {cancelling[0].get('name')} — disclosed gross against its adjustment ({why})",
            }

    release_date, fraction = released_on(invoice, rule, allocations, period_to, payment_orders)

    if release_date is None:
        if fraction > 0:
            return {
                "state": "partial",
                "action": None,
                "fraction": round(fraction, 6),
                "why": f"{fraction:.1%} settled at {period_to} — part-paid supplies are not auto-adjusted ({why})",
            }
        return {
            "state": "deferred" if in_period else "still_deferred",
            "action": "Exclude" if in_period else None,
            "why": f"unpaid at {period_to} ({why})",
        }

    released_in_period = period_from <= release_date <= period_to

    if in_period:
        # Raised and settled inside the same period — already in the return.
        return {"state": "in_period", "action": None, "release_date": release_date, "why": why}

    if released_in_period:
        return {
            "state": "released",
            "action": "Include",
            "release_date": release_date,
            "why": f"settled {release_date} ({why})",
        }

    if release_date < period_from:
        # Declared in an earlier return. Re-including it here would restate tax
        # already paid — the specific failure this branch exists to prevent.
        return {
            "state": "already_counted",
            "action": None,
            "release_date": release_date,
            "why": f"already declared in the period containing {release_date}",
        }

    return {"state": "still_deferred", "action": None, "why": f"settles after {period_to} ({why})"}


def plan_period(
    invoices: list[dict],
    rule: dict,
    period_from: str,
    period_to: str,
    allocations_by_invoice: dict[str, list[dict]],
    groups_by_customer: dict[str, str] | None = None,
    group_paths: dict[str, list[str]] | None = None,
    orders_by_invoice: dict[str, list[dict]] | None = None,
) -> dict:
    """Classify every candidate invoice and summarise what the period should do."""
    groups_by_customer = groups_by_customer or {}
    group_paths = group_paths or {}
    orders_by_invoice = orders_by_invoice or {}
    notes_against: dict[str, list[dict]] = {}
    for inv in invoices:
        if inv.get("is_return") and inv.get("return_against"):
            notes_against.setdefault(inv["return_against"], []).append(inv)
    rows: list[dict] = []
    for inv in invoices:
        verdict = classify_invoice(
            inv,
            rule,
            period_from,
            period_to,
            allocations_by_invoice.get(inv["name"], []),
            groups_by_customer.get(inv.get("customer")),
            notes_against.get(inv["name"]),
            group_paths.get(inv.get("customer")),
            orders_by_invoice.get(inv["name"]),
        )
        verdict["voucher_no"] = inv["name"]
        verdict["posting_date"] = str(inv.get("posting_date"))
        verdict["customer"] = inv.get("customer")
        verdict["net"] = round(float(inv.get("base_net_total") or 0.0), 2)
        verdict["vat"] = round(float(inv.get("base_total_taxes_and_charges") or 0.0), 2)
        rows.append(verdict)

    def total(state_set, key):
        return round(sum(r[key] for r in rows if r["state"] in state_set), 2)

    return {
        "rows": rows,
        "to_exclude": [r for r in rows if r["action"] == "Exclude"],
        "to_include": [r for r in rows if r["action"] == "Include"],
        "flagged": [r for r in rows if r["state"] == "partial"],
        "scope_unknown": [r for r in rows if r["state"] == "scope_unknown"],
        "ungrouped_customers": sorted({
            r["customer"] for r in rows if r["state"] == "scope_unknown" and r.get("customer")
        }),
        "totals": {
            "released_net": total({"released", "released_by_credit_note"}, "net"),
            "released_vat": total({"released", "released_by_credit_note"}, "vat"),
            "deferred_net": total({"deferred"}, "net"),
            "deferred_vat": total({"deferred"}, "vat"),
            "still_deferred_net": total({"still_deferred"}, "net"),
            "still_deferred_vat": total({"still_deferred"}, "vat"),
            "credit_note_net": total({"credit_note"}, "net"),
            "credit_note_vat": total({"credit_note"}, "vat"),
        },
    }


def box_figures(plan: dict, standard_rate: float = 15.0) -> dict:
    """The three figures the target box is filed with, from a classified plan.

    Amount is what this return declares; Adjustment is the credit notes issued in
    it; VAT is summed off the invoices rather than recomputed from the base.

    `vat` and `implied_vat` are both returned instead of picking one. They differ
    when invoices carry rounding, or when a non-standard rate has crept into a
    government supply — and that difference is exactly the thing a preparer needs
    to see before signing. Silently choosing either figure would hide it.
    """
    def total(states, key):
        return round(sum(r[key] for r in plan.get("rows", []) if r["state"] in states), 2)

    amount = total({"released", "released_by_credit_note"}, "net")
    adjustment = round(abs(total({"credit_note"}, "net")), 2)
    vat = round(total({"released", "released_by_credit_note"}, "vat")
                + total({"credit_note"}, "vat"), 2)
    base = round(amount - adjustment, 2)
    implied = round(base * standard_rate / 100.0, 2)
    return {
        "amount": amount,
        "adjustment": adjustment,
        "base": base,
        "vat": vat,
        "implied_vat": implied,
        "variance": round(vat - implied, 2),
    }


def sales_box(base_box: str, customer: str | None, government: set,
              split: bool = True) -> str:
    """Route one already-classified sales invoice to its final output box.

    Standard-rated supplies to government entities move to `box1_2`; everything
    else keeps the box the ordinary classifier gave it. ONLY box1 reroutes — the
    ZATCA line is standard-rated government sales, so a zero-rated or exported
    supply to a ministry belongs in its own box, not this one.

    This exists as one function because two call sites need the answer — the box
    totals and the drill-down behind them — and if they ever disagree the drill
    stops reconciling with the figure it is supposed to explain. That is a silent
    failure: both screens render fine and only the sum is wrong.

    `split` is False when the rule names no target box: the supplies are still
    deferred until due, but on release they join ordinary standard-rated sales.
    Routing them to box1_2 anyway would drop them from the return entirely, since
    no 1.2 line is rendered — silently understating the tax.
    """
    if split and base_box == "box1" and customer and customer in (government or set()):
        return "box1_2"
    return base_box
