# Copyright (c) 2026, Neotec Integrated Solution
# Account Classification Studio (v2.26.0) — tag an account ONCE and every
# report understands it forever. One resolution order everywhere:
#
#   1. User tag (this module)  →  2. ERPNext account_type  →  3. name heuristic
#
# SYSTEM tags are wired into report logic (Health COGS, Cash Flow buckets,
# Zakat components). CUSTOM labels (tag stored as "label:<Name>") are the
# user's own vocabulary — management groupings like "Direct Project Costs" —
# summarised via label_summary and, in a later phase, the Management P&L
# layout designer.
from __future__ import annotations

import json

import frappe
from frappe import _
from frappe.utils import flt

from .health import _default_company

SYSTEM_TAGS = [
    {"key": "cogs", "label": "Cost of Goods Sold",
     "hint": "Feeds Gross Margin, Inventory Days, Payable Days, Cash Conversion Cycle in Financial Health."},
    {"key": "cash", "label": "Cash & Equivalents",
     "hint": "Counted as cash in the Cash Flow statement reconciliation."},
    {"key": "investing", "label": "Investing (long-term asset)",
     "hint": "Cash Flow investing bucket; deducted from the Zakat base."},
    {"key": "financing", "label": "Financing (loan / long-term funding)",
     "hint": "Cash Flow financing bucket; added to the Zakat base."},
    {"key": "provision", "label": "Provision (EOSB etc.)",
     "hint": "Added to the Zakat base as a long-term funding source."},
    {"key": "output_vat", "label": "Output VAT (sales)",
     "hint": "VAT Return: this account's GL feeds Output VAT. Tagging ANY account as Output/Input VAT switches the return to strict mode — only tagged accounts count."},
    {"key": "input_vat", "label": "Input VAT (purchases)",
     "hint": "VAT Return: this account's GL feeds Input VAT (strict mode when tagged)."},
    {"key": "not_vat", "label": "Not VAT (exclude from VAT Return)",
     "hint": "Explicitly keep this account out of VAT detection — WHT, settlement, zakat and other tax-named accounts that are not VAT."},
]
_SYSTEM_KEYS = {t["key"] for t in SYSTEM_TAGS}


def tag_map(company: str) -> dict:
    """{account: tag} of USER tags for a company — the override layer that the
    Cash Flow, Zakat and Health engines consult before their own heuristics."""
    rows = frappe.get_all("Insight Account Tag", filters={"company": company},
                          fields=["account", "tag"], limit_page_length=0)
    return {r["account"]: r["tag"] for r in rows}


def tagged_accounts(company: str, tag: str) -> list:
    return [a for a, t in tag_map(company).items() if t == tag]


@frappe.whitelist()
def get_classification(company=None):
    """Every leaf account with its resolved classification and source —
    exactly the Tagged-by-you / account_type / Auto legend the EBITDA modal
    established, extended to the full tag vocabulary."""
    company = company or _default_company()
    if not frappe.has_permission("Account", "read"):
        frappe.throw(_("Not permitted."))
    tags = tag_map(company)
    accounts = frappe.get_all("Account",
                              filters={"company": company, "is_group": 0},
                              fields=["name", "account_name", "account_number",
                                      "root_type", "account_type"],
                              order_by="root_type, name", limit_page_length=0)
    # account_type values that imply a system tag (layer 2 of the resolution)
    type_implies = {"Cost of Goods Sold": "cogs", "Stock Adjustment": "cogs",
                    "Bank": "cash", "Cash": "cash",
                    "Fixed Asset": "investing", "Capital Work in Progress": "investing"}
    out = []
    for a in accounts:
        user_tag = tags.get(a["name"])
        implied = type_implies.get(a.get("account_type") or "")
        out.append({
            "account": a["name"],
            "label": a.get("account_name") or a["name"],
            "number": a.get("account_number") or "",
            "root_type": a["root_type"],
            "account_type": a.get("account_type") or "",
            "tag": user_tag or "",
            "implied": implied or "",
        })
    custom = sorted({t.split(":", 1)[1] for t in tags.values() if t.startswith("label:")})
    return {"company": company, "accounts": out,
            "system_tags": SYSTEM_TAGS, "custom_labels": custom}


@frappe.whitelist()
def save_classification(company=None, changes=None):
    """changes = {account: tag-or-empty}. Tag is a system key or 'label:<Name>'.
    Empty removes the user tag (resolution falls back to account_type/auto)."""
    company = company or _default_company()
    if not frappe.has_permission("Insight Account Tag", "write"):
        frappe.throw(_("Not permitted to change the classification."))
    if isinstance(changes, str):
        changes = json.loads(changes or "{}")
    for account, tag in (changes or {}).items():
        if not frappe.db.exists("Account", {"name": account, "company": company}):
            continue
        tag = (tag or "").strip()
        if tag and tag not in _SYSTEM_KEYS and not tag.startswith("label:"):
            continue
        existing = frappe.get_all("Insight Account Tag",
                                  filters={"company": company, "account": account},
                                  pluck="name")
        if not tag:
            for name in existing:
                frappe.delete_doc("Insight Account Tag", name, ignore_permissions=True)
            continue
        if existing:
            frappe.db.set_value("Insight Account Tag", existing[0], "tag", tag)
            for name in existing[1:]:
                frappe.delete_doc("Insight Account Tag", name, ignore_permissions=True)
        else:
            frappe.get_doc({"doctype": "Insight Account Tag", "company": company,
                            "account": account, "tag": tag}).insert(ignore_permissions=True)
    frappe.db.commit()
    return {"ok": True}


@frappe.whitelist()
def label_summary(company=None, from_date=None, to_date=None):
    """Period totals per CUSTOM label — management's own vocabulary, on click."""
    company = company or _default_company()
    if not frappe.has_permission("GL Entry", "read"):
        frappe.throw(_("Not permitted."))
    if not from_date or not to_date:
        frappe.throw(_("from_date and to_date are required."))
    labels = {}
    by_label = {}
    for account, tag in tag_map(company).items():
        if tag.startswith("label:"):
            by_label.setdefault(tag.split(":", 1)[1], []).append(account)
    for label, accounts in by_label.items():
        rows = frappe.db.sql(
            """SELECT gle.account, a.account_name, a.root_type,
                      COALESCE(SUM(gle.debit - gle.credit), 0) AS net
               FROM `tabGL Entry` gle JOIN `tabAccount` a ON a.name = gle.account
               WHERE gle.company = %(c)s AND gle.is_cancelled = 0
                 AND gle.account IN %(a)s
                 AND gle.posting_date BETWEEN %(f)s AND %(t)s
               GROUP BY gle.account, a.account_name, a.root_type""",
            {"c": company, "a": tuple(accounts), "f": from_date, "t": to_date},
            as_dict=True)
        # Natural sign per root: expenses/assets debit-positive; income/liab/equity credit-positive.
        lines = []
        for r in rows:
            amt = flt(r["net"] if r["root_type"] in ("Expense", "Asset") else -r["net"], 2)
            lines.append({"account": r["account"],
                          "label": r.get("account_name") or r["account"], "amount": amt})
        lines.sort(key=lambda x: -abs(x["amount"]))
        labels[label] = {"lines": lines, "total": flt(sum(l["amount"] for l in lines), 2)}
    return {"company": company, "labels": labels}
