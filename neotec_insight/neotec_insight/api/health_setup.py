# Copyright (c) 2026, Neotec Integrated Solution
# Account Setup Advisor — reads the Chart of Accounts and suggests the account-type
# corrections that make the Financial Health ratios accurate. Optionally applies them.
from __future__ import annotations

import json
import re

import frappe
from frappe import _

from .health import _default_company

_TRADE = r"trade|customer|عملاء|تجاري"
# Each recommended type must be compatible with the account's root type.
_EXPECTED_ROOT = {
    "Receivable": "Asset", "Current Asset": "Asset", "Bank": "Asset", "Cash": "Asset",
    "Stock": "Asset", "Fixed Asset": "Asset",
    "Payable": "Liability", "Current Liability": "Liability",
    "Cost of Goods Sold": "Expense", "Expense Account": "Expense", "Depreciation": "Expense",
    "Income Account": "Income",
    "Tax": None,  # can be Asset or Liability
}


def _suggest(acc):
    """Return (recommended_type, priority, reason) or None."""
    name = (acc.get("account_name") or acc.get("name") or "")
    ln = name.lower()
    root = acc.get("root_type") or ""
    typ = acc.get("account_type") or ""
    num = str(acc.get("account_number") or "")

    # 1) An expense wrongly typed as Payable/Receivable (drags AP/AR the wrong way).
    if root == "Expense" and typ in ("Payable", "Receivable"):
        return ("Expense Account", "High",
                _("Expense account wrongly typed “{0}”. Its balance is pulled into {0}s and breaks Payable/Receivable ratios.").format(typ))

    # 2) Current assets mis-typed as Fixed Asset (understate liquidity).
    if typ == "Fixed Asset":
        if re.search(r"input vat|input tax", ln):
            return ("Tax", "High", _("Input VAT is a recoverable current asset, not a fixed asset."))
        if re.search(r"receivable|مدين", ln):
            if re.search(_TRADE, ln):
                return ("Receivable", "High", _("Trade receivables drive DSO and current assets — must be “Receivable”."))
            return ("Current Asset", "High", _("Receivable wrongly typed Fixed Asset — understates Current & Quick Ratio."))
        if re.search(r"prepaid|prepayment|advance|deposit|دفعات مقدمة|متداول", ln):
            return ("Current Asset", "Medium", _("Prepayments / other current assets are current, not fixed."))
        if re.search(r"\bcash\b|\bbank\b|نقد", ln):
            return ("Current Asset", "Low", _("Cash/Bank wrongly typed Fixed Asset (leaves should be Bank/Cash)."))

    # 3) Cost-of-sales sitting in operating expenses (inflates gross margin).
    if root == "Expense" and typ in ("Expense Account", "") and re.search(
            r"cost of revenue|cost of operation|cost of sales|cost of services|direct cost|تكاليف الإيرادات|التكلفة التشغيلية", ln):
        return ("Cost of Goods Sold", "Review",
                _("Looks like a direct cost of services. Type as COGS so Gross Margin is meaningful — only if it is a true cost of sales."))

    return None


@frappe.whitelist()
def scan_account_types(company=None):
    """Scan the Chart of Accounts and return suggested account-type corrections."""
    company = company or _default_company()
    if not company:
        frappe.throw(_("No company found."))
    if not frappe.has_permission("Account", "read"):
        frappe.throw(_("Not permitted."))

    accs = frappe.get_all("Account", filters={"company": company},
                          fields=["name", "account_name", "account_number", "root_type", "account_type", "is_group"])
    can_write = bool(frappe.has_permission("Account", "write"))
    order = {"High": 0, "Medium": 1, "Review": 2, "Low": 3}
    out = []
    for a in accs:
        s = _suggest(a)
        if not s:
            continue
        rec, pri, why = s
        # Validate root compatibility; ERPNext validates the rest on save.
        exp = _EXPECTED_ROOT.get(rec)
        applicable = (exp is None or exp == a.get("root_type"))
        out.append({
            "account": a["name"], "account_number": a.get("account_number") or "",
            "root_type": a.get("root_type"), "current_type": a.get("account_type") or "",
            "recommended_type": rec, "priority": pri, "reason": why,
            "is_group": bool(a.get("is_group")), "applicable": applicable,
        })
    out.sort(key=lambda x: (order.get(x["priority"], 9), x["account_number"]))
    return {"company": company, "can_write": can_write, "suggestions": out}


@frappe.whitelist()
def apply_account_types(changes=None, company=None):
    """Apply selected account-type changes. changes = [{account, account_type}, ...]."""
    if isinstance(changes, str):
        changes = json.loads(changes or "[]")
    changes = changes or []
    if not frappe.has_permission("Account", "write"):
        frappe.throw(_("You do not have permission to edit accounts."))

    applied, failed = [], []
    for ch in changes:
        acct, new_type = ch.get("account"), ch.get("account_type")
        if not acct or not new_type:
            continue
        try:
            doc = frappe.get_doc("Account", acct)
            exp = _EXPECTED_ROOT.get(new_type)
            if exp and exp != doc.root_type:
                raise frappe.ValidationError(_("“{0}” is not valid for a {1} account.").format(new_type, doc.root_type))
            doc.account_type = new_type
            doc.save()
            applied.append(acct)
        except Exception as e:
            failed.append({"account": acct, "error": str(e)})
    if applied:
        frappe.db.commit()
    return {"applied": applied, "failed": failed, "applied_count": len(applied), "failed_count": len(failed)}


# ─────────────────────────────────────────────────────────────────────────────
# EBITDA add-back tags (v1.9.98)
# Let users tag any Expense account as an interest/financing or depreciation
# add-back, so EBIT/EBITDA are exact regardless of account naming language or
# missing account_type. Mirrors the precedence used in health._metrics.
# ─────────────────────────────────────────────────────────────────────────────
from .health import INTEREST_KEYWORDS, DEPRECIATION_KEYWORDS  # noqa: E402


def _kw_hits(expense_accounts, keywords):
    kws = [k.lower() for k in keywords]
    return {a["name"] for a in expense_accounts
            if a.get("account_name") and any(k in a["account_name"].lower() for k in kws)}


@frappe.whitelist()
def list_ebitda_addbacks(company=None):
    """Return every Expense account with how it is currently counted toward
    EBIT/EBITDA, and the source of that decision — so the user can see and
    override exactly what the engine uses."""
    company = company or _default_company()
    if not company:
        frappe.throw(_("No company found."))
    if not frappe.has_permission("Account", "read"):
        frappe.throw(_("Not permitted."))

    expense_accounts = frappe.get_all(
        "Account",
        filters={"company": company, "is_group": 0, "root_type": "Expense"},
        fields=["name", "account_number", "account_name", "account_type"],
        order_by="account_number asc, account_name asc",
    )
    tags = frappe.get_all(
        "Insight EBITDA Addback",
        filters={"company": company},
        fields=["account", "category"],
    )
    tag_by_acct = {t["account"]: t["category"] for t in tags}
    interest_explicit = {a for a, c in tag_by_acct.items() if c == "Interest"}
    deprec_explicit = {a for a, c in tag_by_acct.items() if c == "Depreciation"}
    typed_deprec = {a["name"] for a in expense_accounts if (a.get("account_type") == "Depreciation")}

    # Same precedence as the engine: explicit tags (or typed deprec) suppress
    # keyword fallback per-category.
    interest_set = set(interest_explicit) or _kw_hits(expense_accounts, INTEREST_KEYWORDS)
    deprec_set = (set(deprec_explicit) | typed_deprec) or _kw_hits(expense_accounts, DEPRECIATION_KEYWORDS)

    rows = []
    for a in expense_accounts:
        name = a["name"]
        if name in interest_set:
            counted, src = "Interest", ("manual" if name in interest_explicit else "keyword")
        elif name in deprec_set:
            counted = "Depreciation"
            src = "manual" if name in deprec_explicit else ("type" if name in typed_deprec else "keyword")
        else:
            counted, src = "", ""
        rows.append({
            "account": name,
            "code": a.get("account_number") or "",
            "name": a.get("account_name") or name,
            "account_type": a.get("account_type") or "",
            "tag": tag_by_acct.get(name, ""),     # the user's explicit tag, if any
            "counted_as": counted,                # what the engine counts it as now
            "source": src,                        # manual | type | keyword | ''
        })
    # Counted accounts first, then by code.
    rows.sort(key=lambda r: (0 if r["counted_as"] else 1, r["code"], r["name"]))

    return {
        "company": company,
        "can_write": bool(frappe.has_permission("Insight EBITDA Addback", "create")),
        "interest_curated": bool(interest_explicit),
        "depreciation_curated": bool(deprec_explicit or typed_deprec),
        "accounts": rows,
    }


@frappe.whitelist()
def save_ebitda_addbacks(company=None, changes=None):
    """Upsert/clear explicit add-back tags. changes = [{account, category}, ...]
    where category is 'Interest', 'Depreciation', or '' (remove)."""
    company = company or _default_company()
    if isinstance(changes, str):
        changes = json.loads(changes or "[]")
    changes = changes or []
    if not frappe.has_permission("Insight EBITDA Addback", "create"):
        frappe.throw(_("You do not have permission to manage EBITDA add-backs."))

    saved, removed, failed = 0, 0, []
    for ch in changes:
        acct = (ch.get("account") or "").strip()
        cat = (ch.get("category") or "").strip()
        if not acct:
            continue
        try:
            existing = frappe.db.get_value(
                "Insight EBITDA Addback", {"company": company, "account": acct}, "name"
            )
            if cat in ("Interest", "Depreciation"):
                if existing:
                    doc = frappe.get_doc("Insight EBITDA Addback", existing)
                    if doc.category != cat:
                        doc.category = cat
                        doc.save()
                else:
                    frappe.get_doc({
                        "doctype": "Insight EBITDA Addback",
                        "company": company, "account": acct, "category": cat,
                    }).insert()
                saved += 1
            else:
                if existing:
                    frappe.delete_doc("Insight EBITDA Addback", existing)
                    removed += 1
        except Exception as e:
            failed.append({"account": acct, "error": str(e)})

    if saved or removed:
        frappe.db.commit()
    return {"saved": saved, "removed": removed, "failed": failed, "failed_count": len(failed)}
