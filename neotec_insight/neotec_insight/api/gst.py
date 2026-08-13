# Copyright (c) 2026, Neotec Integrated Solution
# India GST (v2.36.0) — GSTR-3B-style summary assembled from the GL tax-head
# accounts (Output/Input CGST, SGST/UTGST, IGST, Cess) plus invoice registers.
# Same philosophy as the ZATCA return: GL is the source of truth, invoices
# provide the taxable-value detail, and detection is name-based with the
# ERPNext India chart conventions ("Output Tax CGST - ...").
from __future__ import annotations

import re

import frappe
from frappe import _
from frappe.utils import flt

from .health import _default_company

HEADS = ["cgst", "sgst", "igst", "cess"]
_HEAD_RX = {
    "cgst": re.compile(r"\bcgst\b", re.I),
    "sgst": re.compile(r"\b(sgst|utgst)\b", re.I),
    "igst": re.compile(r"\bigst\b", re.I),
    "cess": re.compile(r"\bcess\b", re.I),
}
_OUT_RX = re.compile(r"output|sales", re.I)
_IN_RX = re.compile(r"input|itc|purchase", re.I)


def _gst_accounts(company):
    """{('output'|'input', head): [accounts]} by name convention."""
    accts = frappe.get_all("Account", filters={"company": company, "is_group": 0},
                           fields=["name", "account_name", "root_type"],
                           limit_page_length=0)
    out = {}
    for a in accts:
        text = f"{a.get('account_name') or ''} {a['name']}"
        head = next((h for h, rx in _HEAD_RX.items() if rx.search(text)), None)
        if not head:
            continue
        if _OUT_RX.search(text) or (a["root_type"] == "Liability" and not _IN_RX.search(text)):
            side = "output"
        elif _IN_RX.search(text) or a["root_type"] == "Asset":
            side = "input"
        else:
            continue
        out.setdefault((side, head), []).append(a["name"])
    return out


def _gl_sum(company, accounts, from_date, to_date, credit_positive):
    if not accounts:
        return 0.0
    r = frappe.db.sql(
        """SELECT COALESCE(SUM(credit - debit), 0) AS c
           FROM `tabGL Entry`
           WHERE company=%(co)s AND is_cancelled=0 AND account IN %(a)s
             AND posting_date BETWEEN %(f)s AND %(t)s""",
        {"co": company, "a": tuple(accounts), "f": from_date, "t": to_date},
        as_dict=True)[0]
    v = flt(r["c"], 2)
    return v if credit_positive else flt(-v, 2)


@frappe.whitelist()
def gst_summary(company=None, from_date=None, to_date=None):
    company = company or _default_company()
    if not frappe.has_permission("GL Entry", "read"):
        frappe.throw(_("Not permitted."))
    if not from_date or not to_date:
        frappe.throw(_("from_date and to_date are required."))
    acc = _gst_accounts(company)

    output = {h: _gl_sum(company, acc.get(("output", h), []), from_date, to_date, True) for h in HEADS}
    itc = {h: _gl_sum(company, acc.get(("input", h), []), from_date, to_date, False) for h in HEADS}

    # Taxable values from invoices (3.1a outward / inward for context)
    si = frappe.db.sql(
        """SELECT COALESCE(SUM(base_net_total),0) n FROM `tabSales Invoice`
           WHERE company=%(c)s AND docstatus=1
             AND posting_date BETWEEN %(f)s AND %(t)s""",
        {"c": company, "f": from_date, "t": to_date}, as_dict=True)[0]
    pi = frappe.db.sql(
        """SELECT COALESCE(SUM(base_net_total),0) n FROM `tabPurchase Invoice`
           WHERE company=%(c)s AND docstatus=1
             AND posting_date BETWEEN %(f)s AND %(t)s""",
        {"c": company, "f": from_date, "t": to_date}, as_dict=True)[0]

    total_out = flt(sum(output.values()), 2)
    total_itc = flt(sum(itc.values()), 2)
    net = {h: flt(output[h] - itc[h], 2) for h in HEADS}

    return {
        "company": company, "from_date": from_date, "to_date": to_date,
        "currency": frappe.db.get_value("Company", company, "default_currency") or "INR",
        "gstin": frappe.db.get_value("Company", company, "tax_id") or "",
        "outward_taxable_value": flt(si["n"], 2),
        "inward_taxable_value": flt(pi["n"], 2),
        "output": output, "itc": itc, "net": net,
        "total_output": total_out, "total_itc": total_itc,
        "net_payable": flt(total_out - total_itc, 2),
        "accounts": {f"{s}_{h}": acc.get((s, h), []) for s in ("output", "input") for h in HEADS},
        "note": _("Detected from GL tax-head accounts by the ERPNext India naming convention (Output/Input · CGST/SGST/IGST/Cess). Reconcile with your GSTR filings; e-invoicing and returns filing remain with the India Compliance app."),
    }


@frappe.whitelist()
def gst_head_drill(company=None, from_date=None, to_date=None, side="output", head="cgst"):
    company = company or _default_company()
    if not frappe.has_permission("GL Entry", "read"):
        frappe.throw(_("Not permitted."))
    side = "input" if side == "input" else "output"
    head = head if head in HEADS else "cgst"
    accounts = _gst_accounts(company).get((side, head), [])
    if not accounts:
        return {"rows": []}
    rows = frappe.db.sql(
        """SELECT posting_date, voucher_type, voucher_no, party,
                  debit, credit, account
           FROM `tabGL Entry`
           WHERE company=%(c)s AND is_cancelled=0 AND account IN %(a)s
             AND posting_date BETWEEN %(f)s AND %(t)s
           ORDER BY posting_date, creation""",
        {"c": company, "a": tuple(accounts), "f": from_date, "t": to_date},
        as_dict=True)
    sign = 1 if side == "output" else -1
    out = [{"posting_date": str(r["posting_date"]), "voucher_type": r["voucher_type"],
            "voucher_no": r["voucher_no"], "party": r.get("party") or "",
            "amount": flt(sign * (flt(r["credit"]) - flt(r["debit"])), 2),
            "account": r["account"]} for r in rows]
    return {"rows": out, "total": flt(sum(r["amount"] for r in out), 2)}
