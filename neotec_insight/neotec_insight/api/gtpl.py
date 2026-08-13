# Copyright (c) 2026, Neotec Integrated Solution
# GTPL deferral — database layer.
#
# Fetches invoices, payments and customer groups, hands them to the decision core
# in utils/gtpl_core.py, and writes the result out as Insight VAT Adjustment rows.
# No decision that moves a filed figure belongs in this file.
from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import flt

from ..utils.gtpl_core import (  # noqa: F401  — re-exported for callers
    TOLERANCE,
    TRIGGERS,
    box_figures,
    classify_invoice,
    is_government,
    order_release,
    pick_rule,
    plan_period,
    released_on,
    sales_box,
)

def active_rule(company: str, as_of: str) -> dict | None:
    """The GTPL rule governing a period ending `as_of`, with its child tables."""
    names = frappe.get_all("Insight GTPL Rule",
                           filters={"company": company},
                           fields=["name", "effective_from", "is_active"],
                           limit_page_length=0)
    picked = pick_rule(names, as_of)
    if not picked:
        return None
    doc = frappe.get_doc("Insight GTPL Rule", picked["name"])
    return {
        "name": doc.name,
        "company": doc.company,
        "effective_from": str(doc.effective_from),
        "is_active": doc.is_active,
        "target_box": doc.target_box,
        "trigger_basis": doc.trigger_basis,
        "order_date_field": doc.order_date_field,
        "credit_note_presentation": doc.credit_note_presentation,
        "customer_groups": [{"customer_group": r.customer_group} for r in doc.customer_groups],
        "customer_overrides": [{"customer": r.customer, "treatment": r.treatment,
                                "reason": r.reason} for r in doc.customer_overrides],
    }


def _group_descendants(group_names: set[str]) -> set[str]:
    """Every customer group at or beneath the named ones.

    Customer Group is a nested set. Expanding by lft/rgt means a rule naming
    `Government` keeps working the day someone adds `Ministries` under it — the
    failure that would otherwise be silent and only visible as VAT declared a
    quarter early.
    """
    if not group_names:
        return set()
    out = set(group_names)
    bounds = frappe.get_all("Customer Group",
                            filters={"name": ["in", list(group_names)]},
                            fields=["lft", "rgt"], limit_page_length=0)
    for b in bounds:
        if b.get("lft") is None or b.get("rgt") is None:
            continue
        out |= set(frappe.get_all("Customer Group",
                                  filters={"lft": [">=", b["lft"]], "rgt": ["<=", b["rgt"]]},
                                  pluck="name", limit_page_length=0))
    return out


def _ancestry(group: str | None, parent_of: dict[str, str]) -> list[str]:
    """Group, then its parents, root last. Guarded against a cyclic tree."""
    path, seen, cur = [], set(), group
    while cur and cur not in seen:
        path.append(cur)
        seen.add(cur)
        cur = parent_of.get(cur)
    return path


def _scope(rule: dict) -> tuple[set[str], set[str], set[str]]:
    """(government customers, expanded government groups, forced-out customers)."""
    named = {g["customer_group"] for g in rule.get("customer_groups") or []}
    groups = _group_descendants(named)
    forced_in = {r["customer"] for r in rule.get("customer_overrides") or []
                 if r.get("treatment") == "Government"}
    forced_out = {r["customer"] for r in rule.get("customer_overrides") or []
                  if r.get("treatment") != "Government"}
    in_groups = set(frappe.get_all("Customer",
                                   filters={"customer_group": ["in", list(groups)]} if groups else {"name": ["is", "not set"]},
                                   pluck="name", limit_page_length=0)) if groups else set()
    return (in_groups | forced_in) - forced_out, groups, forced_out


_SI_FIELDS = ["name", "posting_date", "customer", "customer_group", "base_net_total",
              "base_total_taxes_and_charges", "base_grand_total", "is_return", "return_against"]


def _candidates(company: str, rule: dict, from_date: str, to_date: str) -> list[dict]:
    """Every invoice that could affect this period's government boxes.

    Government invoices are fetched WITHOUT a lower date bound. A deferral has no
    statutory expiry — an invoice raised three years ago and settled this quarter
    belongs in this quarter's return — so a lookback window would silently drop
    exactly the supplies this feature exists to catch. The population is small
    because it is restricted to government customers.

    Ungrouped customers are fetched for the period only. They cannot be deferred
    (their scope is unknown, not governmental); they are here to be counted and
    shown, and an unbounded history of them would be noise.
    """
    gov_customers, _groups, _out = _scope(rule)
    rows: list[dict] = []
    extra = [f for f in [(rule.get("order_date_field") or "").strip()] if f]
    fields = _SI_FIELDS + [f for f in extra if frappe.db.has_column("Sales Invoice", f)]

    if gov_customers:
        rows += frappe.get_all("Sales Invoice",
                               filters={"company": company, "docstatus": 1,
                                        "customer": ["in", list(gov_customers)],
                                        "posting_date": ["<=", to_date]},
                               fields=fields, order_by="posting_date asc",
                               limit_page_length=0)

    ungrouped = frappe.get_all("Sales Invoice",
                               filters={"company": company, "docstatus": 1,
                                        "posting_date": ["between", [from_date, to_date]],
                                        "customer_group": ["in", ["", None]]},
                               fields=fields, limit_page_length=0)
    seen = {r["name"] for r in rows}
    rows += [r for r in ungrouped if r["name"] not in seen]
    return rows


def _allocations(invoice_names: list[str]) -> dict[str, list[dict]]:
    """Payment allocations per invoice, from Payment Entries and Journal Entries.

    Both routes matter: ERPNext settles by Payment Entry, but a receipt booked as
    a Journal Entry against the receivable is equally a payment and equally
    triggers the tax point. Counting only Payment Entries would leave
    JE-settled government invoices deferred forever.
    """
    if not invoice_names:
        return {}
    out: dict[str, list[dict]] = {}
    for row in frappe.get_all("Payment Entry Reference",
                              filters={"reference_doctype": "Sales Invoice",
                                       "reference_name": ["in", invoice_names],
                                       "docstatus": 1},
                              fields=["reference_name", "allocated_amount", "parent"],
                              limit_page_length=0):
        date = frappe.db.get_value("Payment Entry", row["parent"], "posting_date")
        out.setdefault(row["reference_name"], []).append(
            {"posting_date": str(date), "allocated_amount": flt(row["allocated_amount"])})

    for row in frappe.get_all("Journal Entry Account",
                              filters={"reference_type": "Sales Invoice",
                                       "reference_name": ["in", invoice_names],
                                       "docstatus": 1},
                              fields=["reference_name", "debit_in_account_currency",
                                      "credit_in_account_currency", "parent"],
                              limit_page_length=0):
        date = frappe.db.get_value("Journal Entry", row["parent"], "posting_date")
        amount = flt(row["credit_in_account_currency"]) - flt(row["debit_in_account_currency"])
        if amount <= 0:
            continue  # a debit against the invoice increases it; not a settlement
        out.setdefault(row["reference_name"], []).append(
            {"posting_date": str(date), "allocated_amount": amount})
    return out


def _payment_orders(invoice_names: list[str]) -> dict[str, list[dict]]:
    """Payment orders (أمر الدفع) per invoice.

    Recorded rather than derived: ERPNext has no concept of a government payment
    order, and the order reference is what ZATCA asks for when it queries why a
    supply was declared in a given quarter.
    """
    if not invoice_names:
        return {}
    out: dict[str, list[dict]] = {}
    for row in frappe.get_all("Insight Payment Order",
                              filters={"sales_invoice": ["in", invoice_names]},
                              fields=["name", "sales_invoice", "order_date", "amount",
                                      "order_reference"],
                              limit_page_length=0):
        out.setdefault(row["sales_invoice"], []).append({
            "name": row["name"],
            "order_date": str(row["order_date"]),
            "amount": flt(row.get("amount")),
            "order_reference": row.get("order_reference"),
        })
    return out


def _group_paths(customers: set[str]) -> tuple[dict[str, str], dict[str, list[str]]]:
    if not customers:
        return {}, {}
    groups = {c["name"]: c.get("customer_group")
              for c in frappe.get_all("Customer", filters={"name": ["in", list(customers)]},
                                      fields=["name", "customer_group"], limit_page_length=0)}
    parent_of = {g["name"]: g.get("parent_customer_group")
                 for g in frappe.get_all("Customer Group",
                                         fields=["name", "parent_customer_group"],
                                         limit_page_length=0)}
    paths = {c: _ancestry(g, parent_of) for c, g in groups.items()}
    return groups, paths


def build_plan(company: str, from_date: str, to_date: str) -> dict:
    """Full GTPL plan for a period: fetch, classify, summarise."""
    rule = active_rule(company, to_date)
    if not rule:
        return {"rule": None, "rows": [], "to_include": [], "to_exclude": [],
                "flagged": [], "scope_unknown": [], "ungrouped_customers": [],
                "totals": {}, "target_box": None}
    invoices = _candidates(company, rule, from_date, to_date)
    names = [i["name"] for i in invoices]
    allocations = _allocations(names)
    orders = _payment_orders(names)
    groups, paths = _group_paths({i["customer"] for i in invoices if i.get("customer")})
    plan = plan_period(invoices, rule, from_date, to_date, allocations, groups, paths, orders)
    plan["rule"] = rule
    plan["target_box"] = rule.get("target_box")
    return plan


@frappe.whitelist()
def gtpl_preview(company=None, from_date=None, to_date=None):
    """What the rule would do to this period. Read-only; changes nothing."""
    from .health import _default_company
    company = company or _default_company()
    if not (company and from_date and to_date):
        frappe.throw(_("company, from_date and to_date are required."))
    if not frappe.has_permission("Sales Invoice", "read"):
        frappe.throw(_("Not permitted."))
    return build_plan(company, from_date, to_date)


@frappe.whitelist()
def apply_gtpl_adjustments(company=None, from_date=None, to_date=None):
    """Write the plan out as Insight VAT Adjustment rows.

    Deliberately NOT automatic. The engine proposes and a person applies, because
    the result is a filed tax return. Two guarantees:

      * A hand-written adjustment is never overwritten. If a preparer has already
        ruled on a voucher for this period, their judgement stands and the row is
        reported back as skipped.
      * Part-paid and unknown-scope supplies are never written at all — they are
        returned for a human to decide.
    """
    from .health import _default_company
    from .vat import _period_adjustments
    company = company or _default_company()
    if not frappe.has_permission("Insight VAT Adjustment", "write"):
        frappe.throw(_("Not permitted."))
    if not (company and from_date and to_date):
        frappe.throw(_("company, from_date and to_date are required."))

    plan = build_plan(company, from_date, to_date)
    if not plan.get("rule"):
        frappe.throw(_("No active GTPL rule applies to a period ending {0}.").format(to_date))

    include, exclude = _period_adjustments(company, from_date, to_date, "Sales Invoice")
    existing = set(include) | set(exclude)
    written, skipped = [], []

    for row in plan["to_exclude"] + plan["to_include"]:
        if row["voucher_no"] in existing:
            skipped.append({"voucher_no": row["voucher_no"],
                            "why": _("a manual adjustment already exists for this period")})
            continue
        frappe.get_doc({
            "doctype": "Insight VAT Adjustment",
            "company": company, "from_date": from_date, "to_date": to_date,
            "voucher_type": "Sales Invoice", "voucher_no": row["voucher_no"],
            "action": row["action"],
            "reason": "GTPL {0} ({1}): {2}".format(
                plan["rule"]["name"], plan["rule"]["trigger_basis"], row["why"]),
        }).insert(ignore_permissions=True)
        written.append({"voucher_no": row["voucher_no"], "action": row["action"]})

    frappe.db.commit()
    return {"ok": True, "written": written, "skipped": skipped,
            "flagged": plan["flagged"], "scope_unknown": plan["scope_unknown"],
            "target_box": plan.get("target_box")}


def account_movement(company: str, account: str, from_date: str, to_date: str) -> float:
    """Net credit movement on an account over the period, in company currency.

    Credit-positive because the accounts this is used for are VAT liabilities:
    a period that raises output VAT shows a positive number, which reads the way
    an accountant expects rather than inverted.
    """
    if not account:
        return 0.0
    rows = frappe.get_all("GL Entry",
                          filters={"company": company, "account": account,
                                   "posting_date": ["between", [from_date, to_date]],
                                   "is_cancelled": 0},
                          fields=["sum(credit) as cr", "sum(debit) as dr"])
    if not rows:
        return 0.0
    return flt(rows[0].get("cr")) - flt(rows[0].get("dr"))


def ledger_check(company: str, rule: dict, from_date: str, to_date: str) -> list[dict]:
    """Compare the register against the ledger accounts named on the rule.

    This is the reconciliation a preparer otherwise does by hand — pulling the
    Output VAT balance for the quarter and satisfying themselves it agrees with
    what the return declares. It reports rather than corrects: a gap is
    information, and Insight has no business deciding which of the two numbers
    is the wrong one.

    Returns [] when the rule names no accounts, so the block simply does not
    appear rather than showing zeros that look like a passing check.
    """
    out = []
    for field, label in (("output_vat_account", "Output VAT"),
                         ("deferred_vat_account", "Deferred Output VAT")):
        account = (rule or {}).get(field)
        if account:
            out.append({"field": field, "label": label, "account": account,
                        "movement": round(account_movement(company, account, from_date, to_date), 2)})
    return out
