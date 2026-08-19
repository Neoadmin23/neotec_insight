# Copyright (c) 2026, Neotec and contributors
# -----------------------------------------------------------------------------
# Reconciliation matcher (Milestone 2, part 2) — Insight's own engine.
#
# For each unreconciled Bank Transaction, propose candidate vouchers (Payment
# Entries + Journal Entries) ranked in confidence passes:
#   1. exact reference   — statement REF == PE.reference_no / JE.cheque_no
#   2. cited invoice     — incoming line names a Sales Invoice (SINV …)
#   3. amount + date     — same amount/direction within an adjustable window,
#                          IBAN bonus, ranked by date closeness
# PROPOSE-ONLY: nothing is reconciled until confirm_match() is called.
# -----------------------------------------------------------------------------
from __future__ import annotations
import re
from datetime import timedelta
from typing import Any

import frappe
from frappe import _
from frappe.utils import flt, getdate

_SINV_RE = re.compile(r"\bS?INV[\s/_-]*\d{4}[\s/_-]*\d{3,6}\b", re.I)
_IBAN_RE = re.compile(r"\bSA\d{22}\b")
_FEE_RE = re.compile(r"FEE\s*[:]?\s*([\d,]+\.\d+)\s*SAR.{0,40}?VAT\s*AMOUNT\s*([\d,]+\.\d+)", re.I | re.S)


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


def _fee_vat(bt: dict) -> tuple[float, float]:
    m = _FEE_RE.search(bt.get("description") or "")
    if m:
        return flt(m.group(1).replace(",", "")), flt(m.group(2).replace(",", ""))
    return 0.0, 0.0


def _is_fee_line(bt: dict) -> bool:
    desc = (bt.get("description") or "").upper()
    if "FEE" in desc and "VAT AMOUNT" in desc:
        return True
    return bool(re.search(r"\bFEES?\b|COMMISSION", desc))


def _amt(bt: dict) -> float:
    return flt(bt.get("deposit")) or flt(bt.get("withdrawal"))


def _direction(bt: dict) -> str:
    return "Incoming" if flt(bt.get("deposit")) > 0 else "Outgoing"


def _pe_amount(pe: dict) -> float:
    if pe.get("payment_type") == "Receive":
        return flt(pe.get("received_amount")) or flt(pe.get("paid_amount"))
    return flt(pe.get("paid_amount")) or flt(pe.get("received_amount"))


def _norm_ref(s: str | None) -> str:
    return re.sub(r"[\s-]", "", (s or "")).upper()


def _candidates(bt: dict, company: str, gl_account: str,
                wb: int, wa: int) -> list[dict]:
    amount = _amt(bt)
    direction = _direction(bt)
    ref = _norm_ref(bt.get("reference_number"))
    out: list[dict] = []
    seen: set[tuple] = set()

    def add(vt, v, passname, score, why):
        key = (vt, v["name"])
        if key in seen:
            return
        seen.add(key)
        va = _pe_amount(v) if vt == "Payment Entry" else flt(v.get("total_debit"))
        out.append({
            "voucher_type": vt, "voucher_name": v["name"],
            "pass": passname, "score": score, "why": why,
            "amount": va, "amount_matches": abs(va - amount) < 0.02,
            "date": str(v.get("posting_date") or ""),
            "party": v.get("party") or v.get("pay_to_recd_from") or "",
            "reference_no": v.get("reference_no") or v.get("cheque_no") or "",
        })

    # ---- Pass 1: exact reference ----
    if ref:
        for pe in _pe_pool(company, clearance_open=True, reference_no=bt.get("reference_number")):
            add("Payment Entry", pe, "reference", 100,
                "Statement reference equals Payment Entry Reference No")
        for je in _je_pool(company, gl_account, cheque_no=bt.get("reference_number")):
            add("Journal Entry", je, "reference", 98,
                "Statement reference equals Journal Entry Cheque/Ref No")

    # ---- Pass 2: cited Sales Invoice (incoming) ----
    if direction == "Incoming":
        m = _SINV_RE.search(bt.get("description") or "")
        if m:
            inv = m.group(0)
            inv_like = "%" + re.sub(r"[\s_-]+", "%", inv) + "%"
            rows = frappe.get_all(
                "Payment Entry Reference",
                filters={"reference_doctype": "Sales Invoice", "reference_name": ["like", inv_like]},
                fields=["parent"], limit_page_length=20)
            for r in rows:
                pe = _pe_by_name(r["parent"], company)
                if pe:
                    add("Payment Entry", pe, "invoice", 90,
                        f"Incoming line cites {inv}, matched to its Payment Entry")

    # ---- Pass 3: amount + date window (+ IBAN bonus) ----
    bdate = getdate(bt.get("date"))
    d_from = bdate - timedelta(days=wb)
    d_to = bdate + timedelta(days=wa)
    ptype = "Receive" if direction == "Incoming" else "Pay"
    ibans = set(_IBAN_RE.findall(bt.get("description") or ""))
    for pe in _pe_pool(company, clearance_open=True, payment_type=ptype,
                       date_from=d_from, date_to=d_to):
        if abs(_pe_amount(pe) - amount) < 0.02:
            days = abs((getdate(pe["posting_date"]) - bdate).days)
            bonus = 0
            why = f"Same amount, {days}d from statement date"
            pe_ref = " ".join(str(pe.get(k) or "") for k in ("reference_no", "party"))
            if ibans and any(ib in pe_ref for ib in ibans):
                bonus = 15
                why += ", beneficiary IBAN matches"
            add("Payment Entry", pe, "amount_date", max(40, 75 - days * 3 + bonus), why)
    for je in _je_pool(company, gl_account, date_from=d_from, date_to=d_to):
        if abs(flt(je.get("total_debit")) - amount) < 0.02:
            days = abs((getdate(je["posting_date"]) - bdate).days)
            add("Journal Entry", je, "amount_date", max(38, 70 - days * 3),
                f"Journal Entry, same amount, {days}d from statement date")

    out.sort(key=lambda c: c["score"], reverse=True)

    # Fee lines (bank charge + VAT) share the transfer's reference, so the
    # reference pass points them at the transfer's entry with a mismatched
    # amount. Lead instead with a "book as bank charge + VAT" action.
    if _is_fee_line(bt):
        fee, vat = _fee_vat(bt)
        if not fee and not vat:
            fee, vat = round(amount / 1.15, 2), round(amount - amount / 1.15, 2)
        charge = {
            "voucher_type": "Bank Charge", "voucher_name": None,
            "pass": "bank_charge", "score": 95,
            "why": "Bank fee — book charge to expense and VAT to input VAT",
            "amount": amount, "amount_matches": True, "date": str(bt.get("date") or ""),
            "party": "", "reference_no": bt.get("reference_number") or "",
            "fee": fee, "vat": vat,
        }
        # keep any amount-correct alternative, but lead with the split
        out = [charge] + [c for c in out if c["amount_matches"]]
        return out[:6]
    return out[:6]


# ---- voucher pools ---------------------------------------------------------

_PE_FIELDS = ["name", "payment_type", "posting_date", "paid_amount", "received_amount",
              "party_type", "party", "reference_no", "reference_date", "clearance_date"]


def _pe_pool(company, clearance_open=False, reference_no=None, payment_type=None,
             date_from=None, date_to=None):
    filters = {"company": company, "docstatus": 1}
    if reference_no:
        filters["reference_no"] = reference_no
    if payment_type:
        filters["payment_type"] = payment_type
    if date_from and date_to:
        filters["posting_date"] = ["between", [date_from, date_to]]
    if clearance_open:
        filters["clearance_date"] = ["in", [None, ""]]
    return frappe.get_all("Payment Entry", filters=filters, fields=_PE_FIELDS,
                          limit_page_length=50)


def _pe_by_name(name, company):
    rows = frappe.get_all("Payment Entry", filters={"name": name, "company": company, "docstatus": 1},
                          fields=_PE_FIELDS, limit_page_length=1)
    return rows[0] if rows else None


_JE_FIELDS = ["name", "posting_date", "cheque_no", "total_debit", "total_credit",
              "clearance_date", "user_remark"]


def _je_pool(company, gl_account, cheque_no=None, date_from=None, date_to=None):
    filters = {"company": company, "docstatus": 1, "clearance_date": ["in", [None, ""]]}
    if cheque_no:
        filters["cheque_no"] = cheque_no
    if date_from and date_to:
        filters["posting_date"] = ["between", [date_from, date_to]]
    # only JEs that actually touch this bank GL account
    je_names = frappe.get_all("Journal Entry Account",
                              filters={"account": gl_account, "parenttype": "Journal Entry"},
                              fields=["parent"], limit_page_length=400)
    names = {r["parent"] for r in je_names}
    if not names:
        return []
    filters["name"] = ["in", list(names)]
    return frappe.get_all("Journal Entry", filters=filters, fields=_JE_FIELDS,
                          limit_page_length=50)


# ---- whitelisted endpoints -------------------------------------------------


def _require_write(doctype: str) -> None:
    """Refuse a write from a user who lacks permission on the doctype.

    Frappe checks permissions inside `doc.save()`, but these endpoints use
    `ignore_permissions=True`, `frappe.db.set_value` or raw SQL — all of which
    bypass that check. Whitelisted and unguarded, they were callable directly
    over `/api/method/...` by any authenticated user, including a read-only
    one. The guard restores the check the bypass removed.
    """
    if not frappe.has_permission(doctype, "write"):
        frappe.throw(
            _("You are not permitted to modify {0}.").format(_(doctype)),
            frappe.PermissionError,
        )




@frappe.whitelist()
def find_matches(bank_account: str, from_date: str | None = None, to_date: str | None = None,
                 window_before: int = 3, window_after: int = 7, limit: int = 100) -> dict:
    _require_write("Insight Bank Slip")
    company, gl_account = frappe.db.get_value("Bank Account", bank_account, ["company", "account"])
    filters = {"bank_account": bank_account, "docstatus": ["<", 2],
               "status": ["not in", ["Reconciled", "Settled"]]}
    if from_date and to_date:
        filters["date"] = ["between", [from_date, to_date]]
    bts = frappe.get_all(
        "Bank Transaction", filters=filters,
        fields=["name", "date", "deposit", "withdrawal", "reference_number",
                "description", "status", "docstatus", "unallocated_amount"],
        order_by="date desc", limit_page_length=int(limit))
    wb, wa = int(window_before), int(window_after)
    txns, counts = [], {"reference": 0, "invoice": 0, "amount_date": 0, "none": 0}
    for bt in bts:
        cands = _candidates(bt, company, gl_account, wb, wa)
        top = cands[0]["pass"] if cands else "none"
        counts[top] = counts.get(top, 0) + 1
        txns.append({
            "name": bt["name"], "date": str(bt["date"]), "amount": _amt(bt),
            "direction": _direction(bt), "reference": bt.get("reference_number"),
            "description": (bt.get("description") or "")[:160],
            "draft": bt.get("docstatus") == 0,
            "candidates": cands,
        })
    return {"bank_account": bank_account, "company": company, "gl_account": gl_account,
            "window_before": wb, "window_after": wa,
            "transaction_count": len(txns), "pass_counts": counts, "transactions": txns}


# `voucher_type` arrives from the caller and `frappe.db.set_value` performs no
# permission or doctype check at all — without this list a whitelisted endpoint
# would write a clearance_date onto ANY doctype having that column, named by
# whoever called it. There is no legitimate fifth value.
_CLEARABLE = {"Payment Entry", "Journal Entry", "Sales Invoice", "Purchase Invoice"}


def _set_clearance(voucher_type: str, voucher_name: str, date) -> None:
    """Write the bank statement's value date onto the voucher as its clearance
    date, so ERPNext and our report agree on when it cleared."""
    if not date:
        return
    if voucher_type not in _CLEARABLE:
        frappe.throw(_("{0} cannot carry a clearance date.").format(_(voucher_type)),
                     frappe.PermissionError)
    _require_write(voucher_type)
    try:
        if frappe.db.has_column(voucher_type, "clearance_date"):
            frappe.db.set_value(voucher_type, voucher_name, "clearance_date", date,
                                update_modified=False)
    except Exception:
        frappe.log_error(title="reconcile: set clearance_date failed",
                         message=frappe.get_traceback())


@frappe.whitelist()
def confirm_match(bank_transaction: str, voucher_type: str, voucher_name: str,
                  allocated_amount: float | None = None) -> dict:
    """Reconcile one Bank Transaction with a chosen voucher. Submits the Bank
    Transaction first if it was imported as a draft.

    v2.77.0 — guarded; it submits a Bank Transaction and stamps a clearance
    date on a caller-named voucher.
    """
    _require_write("Bank Transaction")
    bt = frappe.get_doc("Bank Transaction", bank_transaction)
    if bt.docstatus == 0:
        bt.submit()
        bt.reload()
    amt = flt(allocated_amount) if allocated_amount else (
        flt(bt.unallocated_amount) or flt(bt.deposit) or flt(bt.withdrawal))
    bt.append("payment_entries", {
        "payment_document": voucher_type,
        "payment_entry": voucher_name,
        "allocated_amount": amt,
    })
    bt.save()
    bt.reload()
    _set_clearance(voucher_type, voucher_name, bt.date)
    return {"ok": True, "status": bt.status,
            "unallocated_amount": flt(bt.unallocated_amount)}


@frappe.whitelist()
def unmatch(bank_transaction: str) -> dict:
    """Remove all allocations from a Bank Transaction (undo a confirm)."""
    _require_write("Bank Transaction")
    bt = frappe.get_doc("Bank Transaction", bank_transaction)
    bt.payment_entries = []
    bt.save()
    return {"ok": True, "status": bt.status}


# ---- bank-charge split + settings ------------------------------------------

@frappe.whitelist()
def get_reconcile_settings() -> dict:
    _require_read()
    s = frappe.get_single("Insight AI Settings")
    return {
        "bank_charges_account": getattr(s, "bank_charges_account", None),
        "input_vat_account": getattr(s, "input_vat_account", None),
    }


@frappe.whitelist()
def set_reconcile_settings(bank_charges_account: str | None = None,
                           input_vat_account: str | None = None) -> dict:
    _require_read()
    s = frappe.get_single("Insight AI Settings")
    if bank_charges_account is not None:
        s.bank_charges_account = bank_charges_account or None
    if input_vat_account is not None:
        s.input_vat_account = input_vat_account or None
    s.save(ignore_permissions=False)
    return {"ok": True}


@frappe.whitelist()
def book_bank_charge(bank_transaction: str, bank_charges_account: str,
                     input_vat_account: str | None = None,
                     fee: float | None = None, vat: float | None = None) -> dict:
    """Create a Journal Entry that books a bank fee — charge to expense, VAT to
    recoverable input VAT, credit the bank — then reconcile the fee line to it.
    Propose-only elsewhere; this is an explicit user action."""
    _require_read()
    bt = frappe.get_doc("Bank Transaction", bank_transaction)
    if bt.docstatus == 0:
        bt.submit(); bt.reload()
    company, gl_account = frappe.db.get_value(
        "Bank Account", bt.bank_account, ["company", "account"])
    total = flt(bt.withdrawal) or flt(bt.deposit)
    f, v = _fee_vat({"description": bt.description})
    fee = flt(fee) if fee is not None else (f or round(total / 1.15, 2))
    vat = flt(vat) if vat is not None else (v or round(total - fee, 2))
    # keep the legs equal to the bank movement
    if round(fee + vat, 2) != round(total, 2):
        vat = round(total - fee, 2)

    je = frappe.new_doc("Journal Entry")
    je.voucher_type = "Bank Entry"
    je.company = company
    je.posting_date = bt.date
    je.cheque_no = bt.reference_number
    je.cheque_date = bt.date
    je.user_remark = f"Bank charge — {bt.description or ''}"[:140]
    je.append("accounts", {"account": bank_charges_account, "debit_in_account_currency": fee})
    if vat and input_vat_account:
        je.append("accounts", {"account": input_vat_account, "debit_in_account_currency": vat})
    elif vat:
        # no VAT account given -> fold into the charge
        je.accounts[0].debit_in_account_currency = round(fee + vat, 2)
    je.append("accounts", {"account": gl_account, "credit_in_account_currency": round(total, 2)})
    je.flags.ignore_mandatory = True
    je.insert(); je.submit()

    bt.append("payment_entries", {
        "payment_document": "Journal Entry", "payment_entry": je.name,
        "allocated_amount": total})
    bt.save(); bt.reload()
    _set_clearance("Journal Entry", je.name, bt.date)
    return {"ok": True, "journal_entry": je.name, "fee": fee, "vat": vat,
            "status": bt.status}


@frappe.whitelist()
def backfill_clearance(bank_account: str) -> dict:
    """Stamp clearance_date (= bank value date) on vouchers already reconciled to
    this account's bank transactions but missing it."""
    _require_read()
    bts = frappe.get_all(
        "Bank Transaction",
        filters={"bank_account": bank_account, "status": ["in", ["Reconciled", "Settled"]], "docstatus": 1},
        fields=["name", "date"])
    n = 0
    for bt in bts:
        for a in frappe.get_all("Bank Transaction Payments", filters={"parent": bt["name"]},
                                fields=["payment_document", "payment_entry"]):
            if not frappe.db.get_value(a["payment_document"], a["payment_entry"], "clearance_date"):
                _set_clearance(a["payment_document"], a["payment_entry"], bt["date"])
                n += 1
    return {"ok": True, "stamped": n}


# ---- reconciled view + CFO summary -----------------------------------------

@frappe.whitelist()
def list_reconciled(bank_account: str, limit: int = 100) -> dict:
    _require_read()
    bts = frappe.get_all(
        "Bank Transaction",
        filters={"bank_account": bank_account, "status": ["in", ["Reconciled", "Settled"]],
                 "docstatus": 1},
        fields=["name", "date", "deposit", "withdrawal", "reference_number", "description"],
        order_by="date desc", limit_page_length=int(limit))
    out = []
    for bt in bts:
        allocs = frappe.get_all(
            "Bank Transaction Payments", filters={"parent": bt["name"]},
            fields=["payment_document", "payment_entry", "allocated_amount"])
        out.append({
            "name": bt["name"], "date": str(bt["date"]), "amount": _amt(bt),
            "direction": _direction(bt), "reference": bt.get("reference_number"),
            "vouchers": [{"type": a["payment_document"], "name": a["payment_entry"],
                          "amount": flt(a["allocated_amount"])} for a in allocs],
        })
    return {"bank_account": bank_account, "count": len(out), "transactions": out}


@frappe.whitelist()
def reconciliation_summary(bank_account: str, from_date: str | None = None,
                           to_date: str | None = None) -> dict:
    """Compact status for the CFO/CEO views: reconciled vs open, bank charges +
    recoverable VAT for the period, and the cleared-balance gap."""
    _require_read()
    base = {"bank_account": bank_account, "docstatus": 1}
    if from_date and to_date:
        base["date"] = ["between", [from_date, to_date]]

    def _agg(extra):
        f = dict(base, **extra)
        rows = frappe.get_all("Bank Transaction", filters=f,
                              fields=["deposit", "withdrawal", "description"])
        return rows

    recon = _agg({"status": ["in", ["Reconciled", "Settled"]]})
    openn = _agg({"status": ["not in", ["Reconciled", "Settled"]]})
    allr = recon + openn
    fees = vat = 0.0
    for r in allr:
        f, v = _fee_vat(r)
        fees += f; vat += v
    return {
        "bank_account": bank_account,
        "reconciled_count": len(recon),
        "reconciled_value": round(sum(_amt(r) for r in recon), 2),
        "open_count": len(openn),
        "open_value": round(sum(_amt(r) for r in openn), 2),
        "reconciled_pct": round(100 * len(recon) / max(1, len(allr)), 1),
        "bank_charges": round(fees, 2),
        "recoverable_input_vat": round(vat, 2),
        "incoming_value": round(sum(_amt(r) for r in allr if flt(r.get("deposit")) > 0), 2),
        "outgoing_value": round(sum(_amt(r) for r in allr if flt(r.get("withdrawal")) > 0), 2),
    }


@frappe.whitelist()
def reconciliation_report(bank_account: str, from_date: str | None = None,
                          to_date: str | None = None, include_open: int = 1) -> dict:
    """Document-style reconciliation report: every bank line with the voucher it
    was reconciled to, document numbers and dates (value date, document date,
    cleared date)."""
    _require_read()
    company, gl_account = frappe.db.get_value("Bank Account", bank_account, ["company", "account"])
    filters = {"bank_account": bank_account, "docstatus": 1}
    if from_date and to_date:
        filters["date"] = ["between", [from_date, to_date]]
    bts = frappe.get_all(
        "Bank Transaction", filters=filters,
        fields=["name", "date", "deposit", "withdrawal", "reference_number",
                "description", "status"],
        order_by="date asc, creation asc", limit_page_length=5000)

    rows = []
    for bt in bts:
        base = {
            "bank_transaction": bt["name"], "value_date": str(bt["date"]),
            "bank_reference": bt.get("reference_number") or "",
            "description": (bt.get("description") or "")[:80],
            "amount": _amt(bt), "direction": _direction(bt),
        }
        allocs = frappe.get_all(
            "Bank Transaction Payments", filters={"parent": bt["name"]},
            fields=["payment_document", "payment_entry", "allocated_amount"])
        if bt.get("status") in ("Reconciled", "Settled") and allocs:
            for a in allocs:
                vt, vn = a["payment_document"], a["payment_entry"]
                if vt == "Payment Entry":
                    vd = frappe.db.get_value(
                        "Payment Entry", vn,
                        ["posting_date", "reference_no", "party", "clearance_date"], as_dict=True) or {}
                    vdate, clr, party = vd.get("posting_date"), vd.get("clearance_date"), vd.get("party")
                else:
                    vd = frappe.db.get_value(
                        "Journal Entry", vn,
                        ["posting_date", "cheque_no", "clearance_date"], as_dict=True) or {}
                    vdate, clr, party = vd.get("posting_date"), vd.get("clearance_date"), ""
                rows.append({**base, "reconciled": True,
                             "voucher_type": vt, "voucher_no": vn,
                             "voucher_date": str(vdate or ""),
                             # Cleared date is when it cleared on the bank statement —
                             # i.e. the imported line's value date — falling back to
                             # the voucher's clearance_date if present.
                             "cleared_date": str(bt["date"] or clr or ""),
                             "party": party or "", "allocated": flt(a["allocated_amount"])})
        elif int(include_open):
            rows.append({**base, "reconciled": False, "voucher_type": "", "voucher_no": "",
                         "voucher_date": "", "cleared_date": "", "party": "", "allocated": 0.0})

    recon = [r for r in rows if r["reconciled"]]
    return {
        "bank_account": bank_account, "company": company,
        "from_date": from_date, "to_date": to_date,
        "row_count": len(rows), "reconciled_count": len(recon),
        "reconciled_value": round(sum(r["allocated"] for r in recon), 2),
        "rows": rows,
    }


@frappe.whitelist()
def get_print_header() -> dict:
    _require_read()
    s = frappe.get_single("Insight AI Settings")
    name = getattr(s, "print_org_name", None)
    if not name:
        # sensible default from the default company
        company = frappe.defaults.get_global_default("company")
        name = company or "Company"
    return {
        "org_name": name,
        "org_address": getattr(s, "print_org_address", None) or "",
        "logo_url": getattr(s, "print_logo_url", None) or "",
    }


@frappe.whitelist()
def set_print_header(org_name: str | None = None, org_address: str | None = None,
                     logo_url: str | None = None) -> dict:
    _require_read()
    s = frappe.get_single("Insight AI Settings")
    if org_name is not None:
        s.print_org_name = org_name or None
    if org_address is not None:
        s.print_org_address = org_address or None
    if logo_url is not None:
        s.print_logo_url = logo_url or None
    s.save(ignore_permissions=False)
    return {"ok": True}


# ---- balance bridge (book-to-bank reconciliation statement) -----------------

def _gl_bal(company, account, as_of):
    rows = frappe.db.sql(
        """select sum(debit)-sum(credit) from `tabGL Entry`
           where company=%s and account=%s and posting_date<=%s and is_cancelled=0""",
        (company, account, as_of))
    return flt(rows[0][0]) if rows and rows[0][0] is not None else 0.0


def _uncleared_vouchers(company, gl_account, as_of, direction):
    """Vouchers booked to the bank GL but not yet cleared on the statement.
    direction 'out' = payments issued/unpresented; 'in' = deposits in transit."""
    total = 0.0
    if direction == "out":
        pes = frappe.get_all("Payment Entry",
                             filters={"company": company, "docstatus": 1, "payment_type": "Pay",
                                      "paid_from": gl_account, "clearance_date": ["in", [None, ""]],
                                      "posting_date": ["<=", as_of]},
                             fields=["paid_amount"], limit_page_length=10000)
        total += sum(flt(p["paid_amount"]) for p in pes)
        rows = frappe.db.sql(
            """select sum(ja.credit_in_account_currency) from `tabJournal Entry Account` ja
               join `tabJournal Entry` je on je.name=ja.parent
               where je.company=%s and je.docstatus=1 and ja.account=%s
                 and (je.clearance_date is null or je.clearance_date='')
                 and je.posting_date<=%s and ja.credit_in_account_currency>0""",
            (company, gl_account, as_of))
        total += flt(rows[0][0]) if rows and rows[0][0] else 0.0
    else:
        pes = frappe.get_all("Payment Entry",
                             filters={"company": company, "docstatus": 1, "payment_type": "Receive",
                                      "paid_to": gl_account, "clearance_date": ["in", [None, ""]],
                                      "posting_date": ["<=", as_of]},
                             fields=["received_amount"], limit_page_length=10000)
        total += sum(flt(p["received_amount"]) for p in pes)
        rows = frappe.db.sql(
            """select sum(ja.debit_in_account_currency) from `tabJournal Entry Account` ja
               join `tabJournal Entry` je on je.name=ja.parent
               where je.company=%s and je.docstatus=1 and ja.account=%s
                 and (je.clearance_date is null or je.clearance_date='')
                 and je.posting_date<=%s and ja.debit_in_account_currency>0""",
            (company, gl_account, as_of))
        total += flt(rows[0][0]) if rows and rows[0][0] else 0.0
    return round(total, 2)


def _unreconciled_bank(bank_account, as_of):
    rows = frappe.get_all("Bank Transaction",
                          filters={"bank_account": bank_account, "docstatus": 1,
                                   "status": ["not in", ["Reconciled", "Settled"]],
                                   "date": ["<=", as_of]},
                          fields=["deposit", "withdrawal"], limit_page_length=10000)
    return {"credits": round(sum(flt(r["deposit"]) for r in rows), 2),
            "charges": round(sum(flt(r["withdrawal"]) for r in rows), 2),
            "count": len(rows)}


@frappe.whitelist()
def reconciliation_bridge(bank_account: str, as_of: str | None = None,
                          statement_balance: float | None = None) -> dict:
    """Book-to-bank balance bridge. Starts from the GL (book) balance, adds back
    un-presented payments, removes deposits in transit, and adjusts for bank-side
    items not yet in the books, to arrive at the expected bank statement balance.
    If the actual statement balance is given, returns the difference (≈0 when
    fully reconciled)."""
    _require_read()
    as_of = as_of or nowdate()
    company, gl_account = frappe.db.get_value("Bank Account", bank_account, ["company", "account"])
    book = round(_gl_bal(company, gl_account, as_of), 2)
    op = _uncleared_vouchers(company, gl_account, as_of, "out")     # outstanding payments
    dit = _uncleared_vouchers(company, gl_account, as_of, "in")     # deposits in transit
    bank_side = _unreconciled_bank(bank_account, as_of)
    bc, ic = bank_side["charges"], bank_side["credits"]
    expected = round(book + op - dit + ic - bc, 2)
    out = {
        "bank_account": bank_account, "company": company, "as_of": str(as_of),
        "gl_account": gl_account,
        "book_balance": book,
        "outstanding_payments": op,
        "deposits_in_transit": dit,
        "bank_credits_unbooked": ic,
        "bank_charges_unbooked": bc,
        "unreconciled_bank_count": bank_side["count"],
        "expected_bank_balance": expected,
        "fully_reconciled": (op == 0 and dit == 0 and bc == 0 and ic == 0),
    }
    if statement_balance not in (None, ""):
        sb = flt(statement_balance)
        out["statement_balance"] = round(sb, 2)
        out["difference"] = round(sb - expected, 2)
    return out
