# Copyright (c) 2026, Neotec Integrated Solution
# VAT settings — one read for everything that governs how a return is produced.
#
# This module deliberately OWNS no configuration. VAT accounts are resolved from
# Classification tags, deferral from Insight GTPL Rule, per-voucher overrides
# from Insight VAT Adjustment. Each already has a home, and a settings screen
# that introduced a second place to set the same thing would guarantee the two
# eventually disagree — with no way to tell which one produced a filed number.
#
# What was actually missing was VISIBILITY: no single screen showed which
# accounts the engine had resolved, whether it was obeying tags or guessing, or
# which rule governed a given quarter. That is what this provides.
from __future__ import annotations

import json

import frappe
from frappe import _
from frappe.utils import flt, getdate

from .classify import tag_map
from .health import _default_company
from .vat import STANDARD_RATE, _vat_accounts


def _require_read() -> None:
    """Refuse a financial read from a user with no ledger access.

    `@frappe.whitelist()` requires a login, not a role, so without this these
    endpoints were callable over `/api/method/...` by any authenticated user,
    including portal users with no business seeing the ledger. Reading GL Entry
    is the right test: ERPNext already restricts it to the accounts roles, so
    this inherits the site's own configuration rather than inventing a second
    permission model.
    """
    if not frappe.has_permission("GL Entry", "read"):
        frappe.throw(
            _("You are not permitted to view financial data."),
            frappe.PermissionError,
        )


def _account_labels(names: list[str]) -> list[dict]:
    if not names:
        return []
    rows = frappe.get_all("Account", filters={"name": ["in", names]},
                          fields=["name", "account_name", "account_number", "account_type"],
                          limit_page_length=0)
    return sorted(rows, key=lambda r: (r.get("account_number") or "", r["name"]))


@frappe.whitelist()
def vat_settings(company=None):
    """Everything governing this company's VAT return, in one read."""
    company = company or _default_company()
    if not company:
        frappe.throw(_("No company found."))
    if not frappe.has_permission("Account", "read"):
        frappe.throw(_("Not permitted."))

    out_names, in_names, clearing_names = _vat_accounts(company)
    tags = tag_map(company)
    tagged_out = [a for a, t in tags.items() if t == "output_vat"]
    tagged_in = [a for a, t in tags.items() if t == "input_vat"]

    # STRICT means a tag exists on that side, so heuristics are off entirely and
    # the account list is exactly what was tagged. Showing which mode is in force
    # matters more than the list: an untagged company is being GUESSED at, and
    # nothing previously said so.
    accounts = {
        "output": {"mode": "tagged" if tagged_out else "heuristic",
                   "accounts": _account_labels(list(out_names)),
                   "tagged_count": len(tagged_out)},
        "input": {"mode": "tagged" if tagged_in else "heuristic",
                  "accounts": _account_labels(list(in_names)),
                  "tagged_count": len(tagged_in)},
        # v2.87.8 — accounts recognized as VAT clearing/reconciliation, not a
        # real Output or Input VAT liability/asset in their own right. A
        # voucher touching one of these is excluded from the non-invoice VAT
        # figure on BOTH sides, the same as one touching Output+Input VAT
        # directly — a quarter-end closing entry done as two separate JEs
        # (one per VAT side, each paired with an account from this list) is
        # otherwise invisible to that exclusion. Shown here for the same
        # reason the heuristic Output/Input lists are: so it's checkable,
        # not asserted.
        "clearing": {"accounts": _account_labels(list(clearing_names))},
    }

    rules = frappe.get_all(
        "Insight GTPL Rule", filters={"company": company},
        fields=["name", "effective_from", "is_active", "target_box", "trigger_basis",
                "credit_note_presentation", "order_date_field",
                "output_vat_account", "deferred_vat_account", "notes"],
        order_by="effective_from desc", limit_page_length=0)
    for r in rules:
        r["effective_from"] = str(r["effective_from"])
        doc = frappe.get_doc("Insight GTPL Rule", r["name"])
        r["customer_groups"] = [g.customer_group for g in doc.customer_groups]
        r["customer_overrides"] = [{"customer": o.customer, "treatment": o.treatment,
                                    "reason": o.reason} for o in doc.customer_overrides]

    adj = frappe.get_all("Insight VAT Adjustment", filters={"company": company},
                         fields=["name", "from_date", "to_date", "voucher_type",
                                 "voucher_no", "action", "reason"],
                         order_by="to_date desc, voucher_no asc", limit_page_length=200)
    for a in adj:
        a["from_date"] = str(a["from_date"]); a["to_date"] = str(a["to_date"])

    return {
        "company": company,
        "standard_rate": STANDARD_RATE,
        "accounts": accounts,
        "gtpl_rules": rules,
        "adjustments": adj,
        "customer_groups": frappe.get_all("Customer Group", pluck="name", limit_page_length=0),
        "can_write": bool(frappe.has_permission("Insight GTPL Rule", "write")),
    }


@frappe.whitelist()
def save_gtpl_rule(company=None, payload=None):
    """Create or update a GTPL rule.

    Editing a rule that has already governed a filed quarter silently restates
    it. The doctype cannot tell the difference, so this endpoint does not try to
    guess either — it saves what it is given and the UI warns. What it will not
    do is let two rules share an effective date, which the controller rejects,
    because an ambiguous resolution changes a filed figure with no way to see it.
    """
    company = company or _default_company()
    if not frappe.has_permission("Insight GTPL Rule", "write"):
        frappe.throw(_("Not permitted."))
    data = json.loads(payload) if isinstance(payload, str) else (payload or {})
    if not data.get("effective_from"):
        frappe.throw(_("An effective-from date is required."))

    name = data.get("name")
    doc = frappe.get_doc("Insight GTPL Rule", name) if name else frappe.new_doc("Insight GTPL Rule")
    doc.company = company
    doc.effective_from = getdate(data["effective_from"])
    doc.is_active = 1 if data.get("is_active", 1) else 0
    # Blank is meaningful: defer, but declare into box 1 rather than a split line.
    doc.target_box = (data.get("target_box") or "").strip() or None
    doc.trigger_basis = data.get("trigger_basis") or "receipt_only"
    doc.credit_note_presentation = data.get("credit_note_presentation") or "gross_with_adjustment"
    doc.order_date_field = (data.get("order_date_field") or "").strip()
    doc.output_vat_account = data.get("output_vat_account") or None
    doc.deferred_vat_account = data.get("deferred_vat_account") or None
    doc.notes = data.get("notes") or None

    doc.set("customer_groups", [])
    for g in data.get("customer_groups") or []:
        doc.append("customer_groups", {"customer_group": g})
    doc.set("customer_overrides", [])
    for o in data.get("customer_overrides") or []:
        if not o.get("customer"):
            continue
        doc.append("customer_overrides", {
            "customer": o["customer"],
            "treatment": o.get("treatment") or "Government",
            "reason": o.get("reason") or "",
        })

    doc.save()
    frappe.db.commit()
    return {"ok": True, "name": doc.name}


@frappe.whitelist()
def set_gtpl_rule_active(name=None, active=1):
    """Activate or deactivate a rule without deleting it.

    Deactivating is preferred over deleting: a deleted rule takes the record of
    what governed a filed quarter with it, and `pick_rule` already skips inactive
    rules, so the effect on future periods is the same.
    """
    if not frappe.has_permission("Insight GTPL Rule", "write"):
        frappe.throw(_("Not permitted."))
    doc = frappe.get_doc("Insight GTPL Rule", name)
    doc.is_active = 1 if int(active or 0) else 0
    doc.save()
    frappe.db.commit()
    return {"ok": True, "name": doc.name, "is_active": doc.is_active}


@frappe.whitelist()
def delete_gtpl_rule(name=None):
    if not frappe.has_permission("Insight GTPL Rule", "delete"):
        frappe.throw(_("Not permitted."))
    frappe.delete_doc("Insight GTPL Rule", name)
    frappe.db.commit()
    return {"ok": True}


@frappe.whitelist()
def payment_orders(company=None, limit=200):
    """Recorded payment orders, newest first, with the invoice they release."""
    company = company or _default_company()
    if not frappe.has_permission("Insight Payment Order", "read"):
        frappe.throw(_("Not permitted."))
    rows = frappe.get_all("Insight Payment Order", filters={"company": company},
                          fields=["name", "sales_invoice", "customer", "order_date",
                                  "order_reference", "amount", "notes"],
                          order_by="order_date desc", limit_page_length=int(limit or 200))
    for r in rows:
        r["order_date"] = str(r["order_date"])
        r["posting_date"] = str(frappe.db.get_value("Sales Invoice", r["sales_invoice"], "posting_date") or "")
        r["grand_total"] = abs(flt(frappe.db.get_value(
            "Sales Invoice", r["sales_invoice"], "base_grand_total")))
    return rows


@frappe.whitelist()
def save_payment_order(company=None, payload=None):
    company = company or _default_company()
    if not frappe.has_permission("Insight Payment Order", "write"):
        frappe.throw(_("Not permitted."))
    data = json.loads(payload) if isinstance(payload, str) else (payload or {})
    if not data.get("sales_invoice") or not data.get("order_date"):
        frappe.throw(_("An invoice and an order date are required."))

    name = data.get("name")
    doc = frappe.get_doc("Insight Payment Order", name) if name else frappe.new_doc("Insight Payment Order")
    doc.company = company
    doc.sales_invoice = data["sales_invoice"]
    doc.order_date = getdate(data["order_date"])
    doc.order_reference = (data.get("order_reference") or "").strip() or None
    doc.amount = flt(data.get("amount")) or 0
    doc.notes = data.get("notes") or None
    doc.save()
    frappe.db.commit()

    # Tell the caller whether this order will actually do anything. Recording an
    # order under a receipt_only rule changes nothing, and silently accepting it
    # would leave someone believing they had moved a supply into a quarter.
    rule = None
    try:
        from .gtpl import active_rule
        rule = active_rule(company, str(doc.order_date))
    except Exception:
        pass
    basis = (rule or {}).get("trigger_basis")
    inert = basis not in ("order_only", "earlier_of_receipt_or_order")
    return {"ok": True, "name": doc.name, "basis": basis, "inert": inert}


@frappe.whitelist()
def delete_payment_order(name=None):
    if not frappe.has_permission("Insight Payment Order", "delete"):
        frappe.throw(_("Not permitted."))
    frappe.delete_doc("Insight Payment Order", name)
    frappe.db.commit()
    return {"ok": True}


@frappe.whitelist()
def deferred_invoices(company=None, as_of=None):
    """Government invoices not yet released — the candidates for a payment order.

    Answers the question a preparer actually has when recording one: which
    supplies are still sitting outside the return, and how long have they been
    there.
    """
    company = company or _default_company()
    if not frappe.has_permission("Sales Invoice", "read"):
        frappe.throw(_("Not permitted."))
    from .gtpl import build_plan
    as_of = as_of or frappe.utils.today()
    year_start = str(getdate(as_of).replace(month=1, day=1))
    plan = build_plan(company, year_start, as_of)
    rows = [r for r in plan.get("rows", [])
            if r["state"] in ("deferred", "still_deferred", "partial")]
    rows.sort(key=lambda r: r.get("posting_date") or "")
    return {"rows": rows, "rule": plan.get("rule")}


# ─────────────────────────────────────────────────────────────────────────────
# Link and tree pickers.
#
# Deliberately an ALLOW-LIST rather than a generic doctype browser. A whitelisted
# endpoint that reads any doctype by name is a data-exposure hole regardless of
# what the UI happens to ask it for, so only the four the VAT screens need are
# reachable, each with its own field list.
# ─────────────────────────────────────────────────────────────────────────────

_LINKABLE = {
    "Account": {
        "tree": True, "parent": "parent_account", "by_company": True,
        "fields": ["name", "account_name", "account_number", "is_group", "root_type"],
        "search": ["name", "account_name", "account_number"],
    },
    # v2.86.6 — added for the Cash Flow Forecast Cost Center multi-select.
    # Same tree shape as Account (parent_cost_center / is_group), reused
    # rather than duplicated — this endpoint is generic UI plumbing, not
    # P&L/report-engine logic, so it's shared across features on purpose.
    "Cost Center": {
        "tree": True, "parent": "parent_cost_center", "by_company": True,
        "fields": ["name", "cost_center_name", "is_group"],
        "search": ["name", "cost_center_name"],
    },
    "Customer Group": {
        "tree": True, "parent": "parent_customer_group", "by_company": False,
        "fields": ["name", "customer_group_name", "is_group"],
        "search": ["name", "customer_group_name"],
    },
    "Customer": {
        "tree": False, "by_company": False,
        "fields": ["name", "customer_name", "customer_group"],
        "search": ["name", "customer_name"],
    },
    "Sales Invoice": {
        "tree": False, "by_company": True, "extra": {"docstatus": 1},
        "fields": ["name", "customer", "posting_date", "base_grand_total"],
        "search": ["name", "customer"],
    },
}


def _link_label(doctype: str, row: dict) -> dict:
    if doctype == "Account":
        return {"value": row["name"],
                "label": row.get("account_name") or row["name"],
                "code": row.get("account_number") or "",
                "meta": row.get("root_type") or "",
                "is_group": bool(row.get("is_group"))}
    if doctype == "Customer Group":
        return {"value": row["name"], "label": row.get("customer_group_name") or row["name"],
                "code": "", "meta": "", "is_group": bool(row.get("is_group"))}
    if doctype == "Cost Center":
        return {"value": row["name"], "label": row.get("cost_center_name") or row["name"],
                "code": "", "meta": "", "is_group": bool(row.get("is_group"))}
    if doctype == "Customer":
        return {"value": row["name"], "label": row.get("customer_name") or row["name"],
                "code": "", "meta": row.get("customer_group") or "", "is_group": False}
    return {"value": row["name"], "label": row.get("customer") or row["name"],
            "code": str(row.get("posting_date") or ""),
            "meta": f"{abs(flt(row.get('base_grand_total'))):,.2f}", "is_group": False}


@frappe.whitelist()
def link_options(doctype=None, company=None, parent=None, query=None, limit=50):
    """Children of a tree node, or flat search results.

    Tree doctypes answer BOTH ways on purpose. A chart of accounts is navigable
    by drilling when you know roughly where a thing lives, and searchable when
    you know its name — forcing either one alone makes the other case tedious.
    Group nodes come back marked so the UI can show them as branches without
    letting anyone pick a node that cannot hold a balance.
    """
    spec = _LINKABLE.get(doctype)
    if not spec:
        frappe.throw(_("{0} is not available for lookup here.").format(doctype))
    if not frappe.has_permission(doctype, "read"):
        frappe.throw(_("Not permitted."))

    filters = dict(spec.get("extra") or {})
    if spec["by_company"]:
        filters["company"] = company or _default_company()

    q = (query or "").strip()
    if q:
        or_filters = [[f, "like", f"%{q}%"] for f in spec["search"]]
        rows = frappe.get_all(doctype, filters=filters, or_filters=or_filters,
                              fields=spec["fields"], limit_page_length=int(limit or 50),
                              order_by="name asc")
    elif spec["tree"]:
        filters[spec["parent"]] = parent or ""
        rows = frappe.get_all(doctype, filters=filters, fields=spec["fields"],
                              limit_page_length=0, order_by="name asc")
    else:
        rows = frappe.get_all(doctype, filters=filters, fields=spec["fields"],
                              limit_page_length=int(limit or 50), order_by="modified desc")

    return {"doctype": doctype, "tree": spec["tree"], "searched": bool(q),
            "options": [_link_label(doctype, r) for r in rows]}


@frappe.whitelist()
def exclude_from_vat(company=None, account=None, restore=0):
    """Tag an account 'Not VAT', or clear that tag.

    This is how a bank account called "…(IRSAA VAT)" gets out of the input VAT
    list. It excludes the ONE account and does NOT switch the side to strict
    mode — only an output_vat/input_vat tag does that — so a company can prune
    false positives one at a time without having to tag its whole chart in a
    single sitting.
    """
    _require_read()
    from .classify import save_classification
    company = company or _default_company()
    if not account:
        frappe.throw(_("An account is required."))
    save_classification(company=company, changes=json.dumps({account: "" if int(restore or 0) else "not_vat"}))
    return {"ok": True, "account": account, "excluded": not int(restore or 0)}
