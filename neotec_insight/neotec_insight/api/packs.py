# Copyright (c) 2026, Neotec Integrated Solution
# Export Pack engine (v2.28.0) — client-specific audit workbooks WITHOUT
# client-specific code. A pack is CONFIGURATION: an ordered list of SHEET
# COMPONENTS from the catalog below, each with schema-driven options. The
# frontend designer renders the option UI from the schema, so adding a new
# sheet type here automatically appears in the designer — the next client's
# "different requirement" is a new pack, not a build.
#
# Catalog: vat_return (16-box summary) · sales_register (invoice/item level,
# customer VAT no.) · purchase_register (incl. non-invoice sources) ·
# gl_ledger (any accounts, or the strict-tagged VAT accounts, with running
# balance). Bilingual EN/AR headers.
from __future__ import annotations

import io
import json
import re

import frappe
from frappe import _
from frappe.utils import getdate, flt

from .health import _default_company
from .vat import (STANDARD_RATE, _apply_adjustments, _non_invoice_vouchers,
                  _vat_accounts, vat_return)

# ── Sheet-component catalog (schema drives the designer UI) ─────────────────
SHEET_TYPES = [
    {
        "type": "vat_return",
        "label": "VAT Return (16 boxes)",
        "options": [],
        "columns": [],
    },
    {
        "type": "sales_register",
        "label": "Sales VAT Register",
        "options": [
            {"key": "item_level", "label": "One row per item line", "kind": "check", "default": 1},
            {"key": "include_returns", "label": "Include credit notes (negative rows)", "kind": "check", "default": 1},
        ],
        "columns": [
            ("idx", "#", "م"), ("party", "Customer Name", "اسم العميل"),
            ("tax_id", "VAT No.", "الرقم الضريبي"), ("voucher_no", "Invoice No.", "رقم الفاتورة"),
            ("posting_date", "Issue Date", "تاريخ الإصدار"), ("item", "Product / Description", "المنتج / الوصف"),
            ("qty", "Qty", "الكمية"), ("rate", "Unit Price", "سعر الوحدة"),
            ("net", "Amount", "المبلغ"), ("tax_rate", "Tax Rate", "نسبة الضريبة"),
            ("vat", "VAT Amount", "مبلغ الضريبة"), ("grand", "Grand Total", "الإجمالي"),
            ("outstanding", "Outstanding", "المتبقي"),
        ],
    },
    {
        "type": "purchase_register",
        "label": "Purchases VAT Register",
        "options": [
            {"key": "item_level", "label": "One row per item line", "kind": "check", "default": 1},
            {"key": "include_non_invoice", "label": "Include non-invoice documents (Expenses Entry, Payment Entry…)", "kind": "check", "default": 1},
        ],
        "columns": [
            ("idx", "#", "م"), ("party", "Vendor Name", "اسم المورد"),
            ("tax_id", "VAT No.", "الرقم الضريبي"), ("bill_no", "Invoice No.", "رقم الفاتورة"),
            ("bill_date", "Invoice Date", "تاريخ الفاتورة"), ("posting_date", "Posting Date", "تاريخ القيد"),
            ("item", "Product / Description", "المنتج / الوصف"),
            ("qty", "Qty", "الكمية"), ("rate", "Unit Price", "سعر الوحدة"),
            ("net", "Amount", "المبلغ"), ("tax_rate", "Tax Rate", "نسبة الضريبة"),
            ("vat", "VAT Amount", "مبلغ الضريبة"), ("source", "Source", "المصدر"),
        ],
    },
    {
        "type": "gst_sales_register",
        "label": "GST Sales Register (B2B)",
        "options": [],
        "columns": [
            ("idx", "#", "#"), ("party", "Customer", "Customer"),
            ("gstin", "GSTIN", "GSTIN"), ("voucher_no", "Invoice No.", "Invoice No."),
            ("posting_date", "Invoice Date", "Invoice Date"),
            ("net", "Taxable Value", "Taxable Value"),
            ("cgst", "CGST", "CGST"), ("sgst", "SGST/UTGST", "SGST/UTGST"),
            ("igst", "IGST", "IGST"), ("cess", "Cess", "Cess"),
            ("grand", "Invoice Total", "Invoice Total"),
        ],
    },
    {
        "type": "gst_purchase_register",
        "label": "GST Purchase Register (ITC)",
        "options": [],
        "columns": [
            ("idx", "#", "#"), ("party", "Supplier", "Supplier"),
            ("gstin", "GSTIN", "GSTIN"), ("bill_no", "Bill No.", "Bill No."),
            ("posting_date", "Posting Date", "Posting Date"),
            ("net", "Taxable Value", "Taxable Value"),
            ("cgst", "CGST", "CGST"), ("sgst", "SGST/UTGST", "SGST/UTGST"),
            ("igst", "IGST", "IGST"), ("cess", "Cess", "Cess"),
            ("grand", "Bill Total", "Bill Total"),
        ],
    },
    {
        "type": "gl_ledger",
        "label": "GL Ledger (any accounts)",
        "options": [
            {"key": "accounts_mode", "label": "Accounts", "kind": "select", "default": "output_vat",
             "choices": [["output_vat", "Output VAT accounts (tagged/detected)"],
                          ["input_vat", "Input VAT accounts (tagged/detected)"],
                          ["custom", "Custom account list"]]},
            {"key": "accounts", "label": "Custom accounts (comma-separated)", "kind": "text", "default": ""},
            {"key": "include_opening", "label": "Print the opening balance row", "kind": "check", "default": 1},
        ],
        "columns": [
            ("posting_date", "Posting Date", "تاريخ القيد"), ("account", "Account", "الحساب"),
            ("debit", "Debit", "مدين"), ("credit", "Credit", "دائن"),
            ("balance", "Balance", "الرصيد"), ("voucher_type", "Voucher Type", "نوع المستند"),
            ("voucher_no", "Voucher No", "رقم المستند"), ("against", "Against Account", "الحساب المقابل"),
            ("party", "Party", "الطرف"), ("remarks", "Remarks", "ملاحظات"),
        ],
    },
    {
        "type": "gtpl_register",
        "label": "Government VAT Deferral Register (GTPL)",
        "options": [
            {"key": "show_attention", "label": "Show rows needing a decision (part-paid, ungrouped)", "kind": "check", "default": 1},
            {"key": "show_already_counted", "label": "Show supplies already declared in earlier returns", "kind": "check", "default": 0},
        ],
        "columns": [
            ("idx", "#", "م"), ("voucher_no", "Invoice No.", "رقم الفاتورة"),
            ("posting_date", "Issue Date", "تاريخ الإصدار"),
            ("customer", "Customer", "العميل"),
            ("release_date", "Tax Due Date", "تاريخ استحقاق الضريبة"),
            ("net", "Amount", "المبلغ"), ("vat", "VAT Amount", "مبلغ الضريبة"),
            ("why", "Basis", "الأساس"),
        ],
    },
]
# v3.2.0 — ageing pack (mirrors the client's "AR & AP Ageing" workbook)
_AGEING_OPTS = [
    {"key": "slabs", "label": "Ageing range (days)", "kind": "text", "default": "30,60,90,120"},
    {"key": "based_on", "label": "Age from", "kind": "select", "default": "due",
     "choices": [["due", "Due date"], ["posting", "Posting date"]]},
]
SHEET_TYPES += [
    {"type": "ageing_dashboard", "label": "Ageing Dashboard (AR & AP summary)", "options": _AGEING_OPTS + [
        {"key": "top_ar", "label": "Top customers table (rows; 0 = hide)", "kind": "text", "default": "5"},
        {"key": "top_ap", "label": "Top suppliers table (rows; 0 = hide)", "kind": "text", "default": "0"},
        {"key": "top_by", "label": "Rank top tables by", "kind": "select", "default": "overdue",
         "choices": [["overdue", "Amount due (overdue)"], ["outstanding", "Total outstanding"]]},
        {"key": "header_title", "label": "Header title (blank = standard)", "kind": "text", "default": ""},
        {"key": "header_subtitle", "label": "Header subtitle (blank = company name)", "kind": "text", "default": ""},
        {"key": "color_accent", "label": "Heading colour (hex)", "kind": "text", "default": "57257D"},
        {"key": "color_dark", "label": "Summary bars colour (hex)", "kind": "text", "default": "262626"},
        {"key": "color_text", "label": "Text colour (hex)", "kind": "text", "default": "1A1A2E"},
        {"key": "color_total", "label": "Totals colour (hex)", "kind": "text", "default": "7030A0"},
        {"key": "color_band", "label": "Row banding colour (hex)", "kind": "text", "default": "F7F5FA"},
    ]},
    {"type": "ar_ageing", "label": "Accounts Receivable Ageing", "options": _AGEING_OPTS + [
        {"key": "split_not_due", "label": "Show “Not Yet Due” separately", "kind": "check", "default": 1},
        {"key": "show_zero", "label": "Show parties with a zero balance", "kind": "check", "default": 0},
        {"key": "amounts", "label": "Invoiced / Paid columns cover", "kind": "select", "default": "open",
         "choices": [["open", "Open invoices only"], ["all", "All invoices"]]},
        {"key": "min_balance", "label": "Ignore leftover balances below (exchange / rounding)", "kind": "number", "default": 1},
        {"key": "details", "label": "Add a detail sheet (documents behind each party, clickable)", "kind": "check", "default": 1}]},
    {"type": "ap_ageing", "label": "Accounts Payable Ageing", "options": _AGEING_OPTS + [
        {"key": "split_not_due", "label": "Show “Not Yet Due” separately", "kind": "check", "default": 1},
        {"key": "show_zero", "label": "Show parties with a zero balance", "kind": "check", "default": 0},
        {"key": "amounts", "label": "Invoiced / Paid columns cover", "kind": "select", "default": "open",
         "choices": [["open", "Open invoices only"], ["all", "All invoices"]]},
        {"key": "min_balance", "label": "Ignore leftover balances below (exchange / rounding)", "kind": "number", "default": 1},
        {"key": "details", "label": "Add a detail sheet (documents behind each party, clickable)", "kind": "check", "default": 1}]},
]
_TYPES = {t["type"]: t for t in SHEET_TYPES}


def _require_read() -> None:
    """Finance read gate — one policy for every module (utils/access.py, v2.90.0)."""
    from neotec_insight.neotec_insight.utils.access import require_finance_read
    require_finance_read(_("You are not permitted to view financial data."))


@frappe.whitelist()
def list_sheet_types():
    _require_read()
    return SHEET_TYPES


@frappe.whitelist()
def list_packs():
    _require_read()
    return frappe.get_all("Insight Export Pack",
                          fields=["slug", "title", "description", "modified"],
                          order_by="modified desc", limit_page_length=100)


@frappe.whitelist()
def load_pack(slug=None):
    _require_read()
    if not slug or not frappe.db.exists("Insight Export Pack", slug):
        frappe.throw(_("Pack not found."))
    doc = frappe.get_doc("Insight Export Pack", slug)
    return {"slug": doc.slug, "title": doc.title, "description": doc.description,
            "config": json.loads(doc.config_json or "{}")}


@frappe.whitelist(methods=["POST"])
def save_pack(pack=None):
    if not frappe.has_permission("Insight Export Pack", "write"):
        frappe.throw(_("Not permitted."))
    if isinstance(pack, str):
        pack = json.loads(pack or "{}")
    title = (pack.get("title") or "").strip()
    if not title:
        frappe.throw(_("Give the pack a title."))
    cfg = pack.get("config") or {}
    sheets = [s for s in (cfg.get("sheets") or []) if s.get("type") in _TYPES][:12]
    if not sheets:
        frappe.throw(_("A pack needs at least one sheet."))
    cfg = {"sheets": sheets, "language": cfg.get("language") or "both"}
    slug = pack.get("slug") or frappe.scrub(title)
    if frappe.db.exists("Insight Export Pack", slug):
        doc = frappe.get_doc("Insight Export Pack", slug)
    else:
        doc = frappe.new_doc("Insight Export Pack")
        doc.slug = slug
    doc.title = title
    doc.description = pack.get("description") or ""
    doc.config_json = json.dumps(cfg)
    doc.save(ignore_permissions=True)
    frappe.db.commit()
    return {"slug": doc.slug}


@frappe.whitelist(methods=["POST"])
def delete_pack(slug=None):
    if not frappe.has_permission("Insight Export Pack", "delete"):
        frappe.throw(_("Not permitted."))
    if slug and frappe.db.exists("Insight Export Pack", slug):
        frappe.delete_doc("Insight Export Pack", slug)
        frappe.db.commit()
    return {"ok": True}


def seed_default_export_packs() -> None:
    """Idempotent seed (called from install after_migrate): the 'VAT Breakdown
    (Quarterly)' pack mirroring the live client's manual workbook — return
    summary, item-level sales & purchase registers, and both VAT-account GL
    ledgers. Never overwrites a pack the user has edited."""
    _seed_ageing_pack()
    if frappe.db.exists("Insight Export Pack", "vat_breakdown_quarterly"):
        return
    cfg = {"language": "both", "sheets": [
        {"type": "vat_return", "title": "VAT Return", "options": {}},
        {"type": "sales_register", "title": "Sales VAT", "options": {"item_level": 1, "include_returns": 1}},
        {"type": "purchase_register", "title": "Purchases VAT", "options": {"item_level": 1, "include_non_invoice": 1}},
        {"type": "gl_ledger", "title": "GL - Output VAT", "options": {"accounts_mode": "output_vat"}},
        {"type": "gl_ledger", "title": "GL - Input VAT", "options": {"accounts_mode": "input_vat"}},
    ]}
    frappe.get_doc({
        "doctype": "Insight Export Pack", "slug": "vat_breakdown_quarterly",
        "title": "VAT Breakdown (Quarterly)",
        "description": "Audit pack: 16-box return + item-level sales/purchase VAT registers with party VAT numbers + Output/Input VAT GL ledgers.",
        "config_json": json.dumps(cfg),
    }).insert(ignore_permissions=True)


AGEING_STYLE_DEFAULTS = {"accent": "57257D", "dark": "262626", "text": "1A1A2E", "total": "7030A0",
                         "band": "F7F5FA", "title": "", "subtitle": ""}
_HEX = re.compile(r"^#?[0-9A-Fa-f]{6}$")


def ageing_style(opts: dict) -> dict:
    """Header text and colours for the ageing sheets (v3.3.0). Colours must be
    6-digit hex; anything else falls back to the template's colour."""
    out = dict(AGEING_STYLE_DEFAULTS)
    for k in ("accent", "dark", "text", "total", "band"):
        v = str((opts or {}).get(f"color_{k}") or "").strip()
        if _HEX.match(v):
            out[k] = v.lstrip("#").upper()
    out["title"] = str((opts or {}).get("header_title") or "").strip()[:120]
    out["subtitle"] = str((opts or {}).get("header_subtitle") or "").strip()[:120]
    return out


def _seed_ageing_pack() -> None:
    """v3.2.0 — 'AR & AP Ageing' pack: dashboard + AR + AP sheets, as in the
    client's month-end ageing workbook. Never overwrites an edited pack."""
    if frappe.db.exists("Insight Export Pack", "ar_ap_ageing"):
        return
    cfg = {"language": "en", "sheets": [
        {"type": "ageing_dashboard", "title": "Dashboard", "options": {}},
        {"type": "ar_ageing", "title": "AR Ageing", "options": {}},
        {"type": "ap_ageing", "title": "AP Ageing", "options": {"split_not_due": 1}},
    ]}
    frappe.get_doc({"doctype": "Insight Export Pack", "slug": "ar_ap_ageing", "title": "AR & AP Ageing",
                    "description": "Month-end ageing: executive dashboard + customer and supplier ageing with "
                                   "invoiced, paid, credit/debit notes, outstanding and ageing buckets.",
                    "config_json": json.dumps(cfg)}).insert(ignore_permissions=True)


def _notes_by_party(company, as_of, party_type):
    """Credit notes (customers) / debit notes (suppliers) per party, from the
    GL like the ageing engine — so they can be shown apart from payments."""
    dt, side = ("Sales Invoice", "credit") if party_type == "Customer" else ("Purchase Invoice", "debit")
    rows = frappe.db.sql(
        f"""select gle.party as party, sum(gle.{side}) as amount
            from `tabGL Entry` gle join `tab{dt}` inv on inv.name = gle.voucher_no
            where gle.company = %s and gle.party_type = %s and gle.is_cancelled = 0 and gle.posting_date <= %s
              and gle.voucher_type = %s and inv.is_return = 1
            group by gle.party""", (company, party_type, as_of, dt), as_dict=True)
    return {r["party"]: flt(r["amount"]) for r in rows}


def open_invoice_docs(company, party_type, parties, as_of):
    """{party: {invoice: {base_total, base_outstanding}}} of invoices whose own
    outstanding is not zero — ERPNext's notion of 'open' (v3.7.1). The document
    outstanding is current, so this is used for report dates of today or later
    (None otherwise: the ledger-based method applies)."""
    from frappe.utils import nowdate
    if not parties or getdate(as_of) < getdate(nowdate()):
        return None
    dt, pf = ("Sales Invoice", "customer") if party_type == "Customer" else ("Purchase Invoice", "supplier")
    out: dict[str, dict] = {}
    for d in frappe.get_all(dt, filters={"company": company, "docstatus": 1, "is_return": 0, pf: ["in", parties],
                                         "posting_date": ["<=", as_of], "outstanding_amount": ["!=", 0]},
                            fields=["name", pf, "base_grand_total", "base_rounded_total", "outstanding_amount",
                                    "conversion_rate"], limit_page_length=0, order_by="posting_date asc"):
        total = flt(d.get("base_rounded_total")) or flt(d.get("base_grand_total"))
        out.setdefault(d[pf], {})[d["name"]] = {
            "base_total": total, "base_outstanding": flt(d["outstanding_amount"]) * (flt(d.get("conversion_rate")) or 1)}
    return out


def _has_ple() -> bool:
    try:
        return bool(frappe.db.table_exists("Payment Ledger Entry"))
    except Exception:
        return False


def ledger_rows(company, as_of, party_type, opts):
    """v3.7.2 — AR / AP rows from ERPNext's Payment Ledger, document by document,
    as ERPNext's own Accounts Receivable / Payable report: each payment, return
    and journal counts against the document it was allocated to (including
    exchange gain/loss journals), across every receivable/payable account."""
    from neotec_insight.neotec_insight.api.ageing import _parse_slabs, _slab_labels
    from neotec_insight.neotec_insight.utils import ledger_ageing as la
    bounds = _parse_slabs(opts.get("slabs") or "30,60,90,120")
    mode = opts.get("mode") or "days"
    based_on = opts.get("based_on") or "due"
    inv_dt = "Sales Invoice" if party_type == "Customer" else "Purchase Invoice"
    ple = frappe.db.sql(
        """select ple.party, ple.voucher_type, ple.voucher_no, ple.against_voucher_type, ple.against_voucher_no,
                  ple.posting_date, ple.due_date, ple.amount
           from `tabPayment Ledger Entry` ple
           join `tabAccount` a on a.name = ple.account
           where ple.company = %s and ple.party_type = %s and ple.delinked = 0 and ple.posting_date <= %s
             and a.account_type = %s""",
        (company, party_type, as_of, "Receivable" if party_type == "Customer" else "Payable"), as_dict=True)
    # sign guard: invoices must increase what is owed; normalise if a site stores them the other way
    own_inv = sum(flt(r["amount"]) for r in ple if r["voucher_type"] == inv_dt and r["voucher_no"] == r.get("against_voucher_no"))
    if own_inv < 0:
        ple = [{**r, "amount": -flt(r["amount"])} for r in ple]
    docs = la.documents(ple, inv_dt)
    mb = opts.get("min_balance")
    prs = la.party_rows(docs, as_of, bounds, based_on, mode, open_only=(opts.get("amounts") or "open") == "open",
                        min_balance=1.0 if mb in (None, "") else flt(mb))
    labels = _slab_labels(bounds, mode)
    split = int(opts.get("split_not_due", 1) if opts.get("split_not_due") not in (None, "") else 1)
    name_field = "customer_name" if party_type == "Customer" else "supplier_name"
    names = {r["name"]: r[name_field] for r in frappe.get_all(party_type, filters={"name": ["in", list(docs)]},
                                                               fields=["name", name_field], limit_page_length=0,
                                                               order_by="name asc")} if docs else {}
    rows = []
    for p in prs:
        for g in p["docs"]:
            g["slab"] = labels[g["slab_index"]]["en"] if g.get("slab_index", -1) >= 0 else "Not yet due"
        if not split:
            p["buckets"][0] = round(p["buckets"][0] + p["not_due"], 2)
            p["not_due"] = 0.0
        rows.append({**p, "label": names.get(p["party"]) or p["party"], "mismatch": False})
    rows.sort(key=lambda r: -abs(r["outstanding"]))
    cur = frappe.db.get_value("Company", company, "default_currency") or "SAR"
    return {"rows": rows, "labels": labels, "currency": cur}, rows


def ageing_rows(company, as_of, party_type, opts):
    """Ageing pack rows: the same engine as the Ageing screen, with notes split
    out of 'paid' and (AP) not-yet-due items kept out of the first bucket."""
    if _has_ple() and (opts.get("engine") or "ledger") == "ledger":
        return ledger_rows(company, as_of, party_type, opts)
    from neotec_insight.neotec_insight.api.ageing import ar_ap_ageing
    # v3.3.1 — both sides split "not yet due" (default on), so overdue can be shown
    split = int(opts.get("split_not_due", 1) if opts.get("split_not_due") not in (None, "") else 1)
    data = ar_ap_ageing(company=company, as_of=as_of, party_type=party_type,
                        based_on=opts.get("based_on") or "due", slabs=opts.get("slabs") or "30,60,90,120",
                        mode=opts.get("mode") or "days",
                        split_not_due=split)
    rows = []
    if (opts.get("amounts") or "open") == "open":
        # v3.6.7 — Invoiced / Received from the invoices still open: a fully
        # settled invoice adds nothing to either column. Received = settled on
        # those invoices (payments and notes) + payments not yet allocated, so
        # Balance = Invoiced − Received.
        from neotec_insight.neotec_insight.api.ageing import billwise
        parties = [r.get("party") for r in data["rows"] if r.get("party")]
        bw = billwise(company=company, party_type=party_type, parties=parties, as_of=as_of,
                      based_on=opts.get("based_on") or "due", mode=opts.get("mode") or "days",
                      slabs=opts.get("slabs") or "30,60,90,120", max_parties=len(parties)) if parties else {"blocks": []}
        blocks = {b["party"]: b for b in bw.get("blocks") or []}
        inv_dt = "Sales Invoice" if party_type == "Customer" else "Purchase Invoice"
        docs = open_invoice_docs(company, party_type, parties, as_of)     # v3.7.1 — None for past dates

        def kind(vt):        # v3.6.8 — how a settlement was made
            return "paid" if vt == "Payment Entry" else "note" if vt == inv_dt else "adj"
        for r in data["rows"]:
            b = blocks.get(r.get("party")) or {}
            if docs is not None:
                # v3.7.1 — open = the invoice's own outstanding (in its currency) is not zero, as in
                # ERPNext. Invoices left with only exchange-rate residue in SAR are not open.
                mine = docs.get(r.get("party"), {})
                open_ = set(mine)
                inv = sum(d["base_total"] for d in mine.values())
            else:
                open_ = {x["bill"] for x in b.get("open_bills") or []}
                inv = sum(flt(x.get("amount")) for x in b.get("open_bills") or [])
            split = {"paid": 0.0, "note": 0.0, "adj": 0.0}
            for ln in b.get("rows") or []:
                if ln.get("allocated") and ln.get("against") in open_:
                    split[kind(ln.get("voucher_type"))] += flt(ln["allocated"])
            for u in b.get("unallocated") or []:
                split[kind(u.get("voucher_type"))] += flt(u.get("amount"))
            # whatever is left between Invoiced and the ageing balance — exchange differences,
            # rounding, entries the rows do not explain — is an adjustment, so the row balances
            split["adj"] = inv - split["paid"] - split["note"] - flt(r["outstanding"])
            # v3.6.9 — does the invoice detail add up to the ageing balance?
            detail_bal = sum(flt(x.get("remaining")) for x in b.get("open_bills") or []) \
                - sum(flt(u.get("amount")) for u in b.get("unallocated") or [])
            rows.append({"party": r.get("party"), "label": r["label"], "invoiced": flt(inv, 2),
                         "paid": flt(split["paid"], 2), "note": flt(split["note"], 2), "adj": flt(split["adj"], 2),
                         "outstanding": r["outstanding"], "not_due": r.get("not_due", 0.0), "buckets": r["buckets"],
                         "mismatch": abs(detail_bal - flt(r["outstanding"])) > 0.01})
        return data, rows
    notes = _notes_by_party(company, as_of, party_type)
    for r in data["rows"]:
        note = notes.get(r["party"], 0.0)
        rows.append({"party": r.get("party"), "label": r["label"], "invoiced": r["invoiced"], "paid": flt(r["paid"] - note, 2),
                     "note": flt(note, 2), "adj": 0.0, "outstanding": r["outstanding"], "not_due": r.get("not_due", 0.0),
                     "buckets": r["buckets"]})
    return data, rows


# ── Data builders ────────────────────────────────────────────────────────────

def _pct(v):
    return flt(v, 4)


def _sales_rows(company, from_date, to_date, opts):
    filters = {"company": company, "docstatus": 1,
               "posting_date": ["between", [from_date, to_date]]}
    if not opts.get("include_returns", 1):
        filters["is_return"] = 0
    _fields = ["name", "customer", "customer_name", "tax_id",
               "posting_date", "base_net_total",
               "base_total_taxes_and_charges", "base_grand_total",
               "outstanding_amount", "is_return"]
    invs = frappe.get_all("Sales Invoice", filters=filters, fields=_fields,
                          order_by="posting_date asc, name asc", limit_page_length=0)
    invs, excluded = _apply_adjustments(invs, "Sales Invoice", company, from_date, to_date, _fields)
    invs = invs + excluded  # excluded stay visible (red) but are not totalled
    rows = []
    if opts.get("item_level", 1):
        by_parent = {}
        if invs:
            items = frappe.get_all("Sales Invoice Item",
                                   filters={"parent": ["in", [i["name"] for i in invs]]},
                                   fields=["parent", "item_name", "qty", "base_rate", "base_net_amount"],
                                   parent_doctype="Sales Invoice", order_by="parent, idx",
                                   limit_page_length=0)
            for it in items:
                by_parent.setdefault(it["parent"], []).append(it)
        for inv in invs:
            eff = (inv["base_total_taxes_and_charges"] or 0) / inv["base_net_total"] if inv["base_net_total"] else 0
            for it in by_parent.get(inv["name"], [{"item_name": "", "qty": None, "base_rate": None, "base_net_amount": inv["base_net_total"]}]):
                net = flt(it.get("base_net_amount"), 2)
                rows.append({"_adj": inv.get("_adj"), "_adj_reason": inv.get("_adj_reason"),
                             "party": inv["customer_name"] or inv["customer"],
                             "tax_id": inv.get("tax_id") or "",
                             "voucher_no": inv["name"], "posting_date": inv["posting_date"],
                             "item": it.get("item_name") or "", "qty": it.get("qty"),
                             "rate": flt(it.get("base_rate"), 2) if it.get("base_rate") is not None else None,
                             "net": net, "tax_rate": _pct(eff),
                             "vat": flt(net * eff, 2),
                             "grand": flt(inv["base_grand_total"], 2),
                             "outstanding": flt(inv["outstanding_amount"], 2)})
        return rows
    for inv in invs:
        eff = (inv["base_total_taxes_and_charges"] or 0) / inv["base_net_total"] if inv["base_net_total"] else 0
        rows.append({"_adj": inv.get("_adj"), "_adj_reason": inv.get("_adj_reason"),
                     "party": inv["customer_name"] or inv["customer"], "tax_id": inv.get("tax_id") or "",
                     "voucher_no": inv["name"], "posting_date": inv["posting_date"], "item": "",
                     "qty": None, "rate": None, "net": flt(inv["base_net_total"], 2),
                     "tax_rate": _pct(eff), "vat": flt(inv["base_total_taxes_and_charges"], 2),
                     "grand": flt(inv["base_grand_total"], 2),
                     "outstanding": flt(inv["outstanding_amount"], 2)})
    return rows


def _purchase_rows(company, from_date, to_date, opts):
    _pfields = ["name", "supplier", "supplier_name", "tax_id",
                "bill_no", "bill_date", "posting_date", "base_net_total",
                "base_total_taxes_and_charges"]
    invs = frappe.get_all("Purchase Invoice",
                          filters={"company": company, "docstatus": 1,
                                   "posting_date": ["between", [from_date, to_date]]},
                          fields=_pfields,
                          order_by="posting_date asc, name asc", limit_page_length=0)
    invs, p_excluded = _apply_adjustments(invs, "Purchase Invoice", company, from_date, to_date, _pfields)
    invs = invs + p_excluded
    rows = []
    if opts.get("item_level", 1) and invs:
        items = frappe.get_all("Purchase Invoice Item",
                               filters={"parent": ["in", [i["name"] for i in invs]]},
                               fields=["parent", "item_name", "qty", "base_rate", "base_net_amount"],
                               parent_doctype="Purchase Invoice", order_by="parent, idx",
                               limit_page_length=0)
        by_parent = {}
        for it in items:
            by_parent.setdefault(it["parent"], []).append(it)
        for inv in invs:
            eff = (inv["base_total_taxes_and_charges"] or 0) / inv["base_net_total"] if inv["base_net_total"] else 0
            for it in by_parent.get(inv["name"], [{"item_name": "", "qty": None, "base_rate": None, "base_net_amount": inv["base_net_total"]}]):
                net = flt(it.get("base_net_amount"), 2)
                rows.append({"_adj": inv.get("_adj"), "_adj_reason": inv.get("_adj_reason"),
                             "party": inv["supplier_name"] or inv["supplier"],
                             "tax_id": inv.get("tax_id") or "",
                             "bill_no": inv.get("bill_no") or inv["name"],
                             "bill_date": inv.get("bill_date") or inv["posting_date"],
                             "posting_date": inv["posting_date"],
                             "item": it.get("item_name") or "", "qty": it.get("qty"),
                             "rate": flt(it.get("base_rate"), 2) if it.get("base_rate") is not None else None,
                             "net": net, "tax_rate": _pct(eff), "vat": flt(net * eff, 2),
                             "source": "Purchase Invoice"})
    else:
        for inv in invs:
            eff = (inv["base_total_taxes_and_charges"] or 0) / inv["base_net_total"] if inv["base_net_total"] else 0
            rows.append({"_adj": inv.get("_adj"), "_adj_reason": inv.get("_adj_reason"),
                         "party": inv["supplier_name"] or inv["supplier"], "tax_id": inv.get("tax_id") or "",
                         "bill_no": inv.get("bill_no") or inv["name"],
                         "bill_date": inv.get("bill_date") or inv["posting_date"],
                         "posting_date": inv["posting_date"], "item": "", "qty": None, "rate": None,
                         "net": flt(inv["base_net_total"], 2), "tax_rate": _pct(eff),
                         "vat": flt(inv["base_total_taxes_and_charges"], 2),
                         "source": "Purchase Invoice"})
    if opts.get("include_non_invoice", 1):
        out_accts, in_accts, clearing_accts = _vat_accounts(company)
        rate = STANDARD_RATE / 100.0
        for v in _non_invoice_vouchers(
                company, in_accts, out_accts + clearing_accts, from_date, to_date, debit_positive=True):
            rows.append({"party": v.get("supplier") or "", "tax_id": "",
                         "bill_no": v["name"], "bill_date": v["posting_date"],
                         "posting_date": v["posting_date"], "item": "", "qty": None, "rate": None,
                         "net": flt(v["base_total_taxes_and_charges"] / rate, 2),
                         "tax_rate": _pct(rate), "vat": v["base_total_taxes_and_charges"],
                         "source": v["doctype"]})
        rows.sort(key=lambda r: str(r["posting_date"]))
    return rows


def _gl_rows(company, from_date, to_date, opts):
    mode = (opts.get("accounts_mode") or "output_vat")
    if mode == "custom":
        accounts = [a.strip() for a in str(opts.get("accounts") or "").split(",") if a.strip()]
    else:
        out_accts, in_accts, _clearing_accts = _vat_accounts(company)
        accounts = out_accts if mode == "output_vat" else in_accts
    if not accounts:
        return [], []
    opening = frappe.db.sql(
        """SELECT COALESCE(SUM(debit),0) d, COALESCE(SUM(credit),0) c FROM `tabGL Entry`
           WHERE company=%(c)s AND is_cancelled=0 AND account IN %(a)s
             AND posting_date < %(f)s""",
        {"c": company, "a": tuple(accounts), "f": from_date}, as_dict=True)[0]
    rows = frappe.db.sql(
        """SELECT posting_date, account, debit, credit, voucher_type, voucher_no,
                  against, party, remarks
           FROM `tabGL Entry`
           WHERE company=%(c)s AND is_cancelled=0 AND account IN %(a)s
             AND posting_date BETWEEN %(f)s AND %(t)s
           ORDER BY posting_date, creation""",
        {"c": company, "a": tuple(accounts), "f": from_date, "t": to_date}, as_dict=True)
    bal = flt(opening["d"] - opening["c"], 2)
    out = []
    if int(opts.get("include_opening", 1)):
        out.append({"posting_date": "", "account": _("Opening Balance"),
                    "debit": flt(opening["d"], 2), "credit": flt(opening["c"], 2),
                    "balance": bal, "voucher_type": "", "voucher_no": "",
                    "against": "", "party": "", "remarks": ""})
    for r in rows:
        bal = flt(bal + flt(r["debit"]) - flt(r["credit"]), 2)
        out.append({"posting_date": r["posting_date"], "account": r["account"],
                    "debit": flt(r["debit"], 2), "credit": flt(r["credit"], 2), "balance": bal,
                    "voucher_type": r["voucher_type"] or "", "voucher_no": r["voucher_no"] or "",
                    "against": r.get("against") or "", "party": r.get("party") or "",
                    "remarks": (r.get("remarks") or "")[:120]})
    return out, accounts


def _gst_head_of(account_head):
    from .gst import _HEAD_RX
    for h, rx in _HEAD_RX.items():
        if rx.search(account_head or ""):
            return h
    return None


def _gst_register_rows(company, from_date, to_date, doctype):
    """Per-invoice CGST/SGST/IGST/Cess split read from the invoice taxes
    child table (account_head name convention)."""
    tax_table = ("Sales Taxes and Charges" if doctype == "Sales Invoice"
                 else "Purchase Taxes and Charges")
    party_f = "customer_name" if doctype == "Sales Invoice" else "supplier_name"
    fields = ["name", party_f + " as party", "tax_id", "posting_date",
              "base_net_total", "base_grand_total"]
    if doctype == "Purchase Invoice":
        fields.append("bill_no")
    invs = frappe.get_all(doctype, filters={"company": company, "docstatus": 1,
                          "posting_date": ["between", [from_date, to_date]]},
                          fields=fields, order_by="posting_date asc, name asc",
                          limit_page_length=0)
    heads_by_parent = {}
    if invs:
        taxes = frappe.get_all(tax_table,
                               filters={"parent": ["in", [i["name"] for i in invs]]},
                               fields=["parent", "account_head", "base_tax_amount_after_discount_amount"],
                               parent_doctype=doctype, limit_page_length=0)
        from neotec_insight.neotec_insight.utils.compat import with_aliases
        with_aliases(taxes, {"amt": "base_tax_amount_after_discount_amount"})
        for tx in taxes:
            h = _gst_head_of(tx["account_head"])
            if h:
                d = heads_by_parent.setdefault(tx["parent"], {})
                d[h] = flt(d.get(h, 0) + flt(tx["amt"]), 2)
    rows = []
    for inv in invs:
        h = heads_by_parent.get(inv["name"], {})
        rows.append({"party": inv["party"], "gstin": inv.get("tax_id") or "",
                     "voucher_no": inv["name"], "bill_no": inv.get("bill_no") or inv["name"],
                     "posting_date": inv["posting_date"],
                     "net": flt(inv["base_net_total"], 2),
                     "cgst": flt(h.get("cgst", 0), 2), "sgst": flt(h.get("sgst", 0), 2),
                     "igst": flt(h.get("igst", 0), 2), "cess": flt(h.get("cess", 0), 2),
                     "grand": flt(inv["base_grand_total"], 2)})
    return rows


# ── Workbook generator ───────────────────────────────────────────────────────

@frappe.whitelist()
def generate_pack(slug=None, company=None, from_date=None, to_date=None, letter_head=None, overrides=None):
    from datetime import datetime

    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill, Side, Border
    from openpyxl.utils import get_column_letter

    company = company or _default_company()
    if not from_date or not to_date:
        frappe.throw(_("from_date and to_date are required."))
    if not frappe.has_permission("GL Entry", "read"):
        frappe.throw(_("Not permitted."))
    # v3.2.1 — the Ageing screen downloads this pack directly; create it if the
    # site has not migrated since it was introduced (or it was deleted).
    if slug == "ar_ap_ageing" and not frappe.db.exists("Insight Export Pack", "ar_ap_ageing"):
        _seed_ageing_pack()
    pack = load_pack(slug)
    cfg = pack["config"]
    # v3.2.1 — options chosen on the calling screen (ageing slabs / basis)
    # override the pack's sheet options for this download only.
    if overrides:
        ov = json.loads(overrides) if isinstance(overrides, str) else overrides
        allowed = {k: v for k, v in (ov or {}).items() if k in ("slabs", "based_on", "mode", "show_zero")}
        if allowed.get("based_on") not in (None, "due", "posting"):
            allowed.pop("based_on")
        if allowed.get("mode") not in (None, "days", "months"):
            allowed.pop("mode")
        for sh in cfg.get("sheets") or []:
            if sh.get("type") in ("ar_ageing", "ap_ageing", "ageing_dashboard"):
                sh["options"] = {**(sh.get("options") or {}), **allowed}
    lang = cfg.get("language") or "both"

    def H(en, ar):
        return en if lang == "en" else ar if lang == "ar" else f"{en} / {ar}"

    wb = Workbook()
    wb.remove(wb.active)
    F_HEAD = PatternFill("solid", fgColor="1F4E5F")
    F_TITLE = Font(bold=True, size=14, color="1F4E5F")
    F_TOT = PatternFill("solid", fgColor="EEF7F4")
    thin = Border(bottom=Side(style="thin", color="E0E0E0"))

    def new_sheet(title):
        ws = wb.create_sheet(title=title[:31] or "Sheet")
        ws.sheet_view.showGridLines = False
        return ws

    def head_block(ws, title):
        ws.append([title]); ws["A1"].font = F_TITLE
        ws.append([f"{company} · {from_date} → {to_date}"])
        ws["A2"].font = Font(size=9, color="777777")
        ws.append([])

    def header_row(ws, labels):
        ws.append(labels)
        for c in ws[ws.max_row]:
            c.fill = F_HEAD; c.font = Font(bold=True, color="FFFFFF")

    def autow(ws):
        for j, col in enumerate(ws.columns, start=1):
            w = 10
            for c in list(col)[:60]:
                w = max(w, min(len(str(c.value or "")), 42))
            ws.column_dimensions[get_column_letter(j)].width = w + 2

    def _dcell(ws, v):
        if hasattr(v, "isoformat") or (isinstance(v, str) and len(v) == 10 and v[4:5] == "-"):
            try:
                return datetime.strptime(str(v)[:10], "%Y-%m-%d").date()
            except Exception:
                return v
        return v

    F_INC = PatternFill("solid", fgColor="DFF0D8")   # included from other period
    F_EXC = PatternFill("solid", fgColor="FDECEA")   # excluded / deferred

    def register_sheet(sheet_cfg, rows, spec):
        ws = new_sheet(sheet_cfg.get("title") or spec["label"])
        head_block(ws, sheet_cfg.get("title") or spec["label"])
        cols = spec["columns"]
        chosen = sheet_cfg.get("options", {}).get("columns") or [c[0] for c in cols]
        cols = [c for c in cols if c[0] in chosen]
        header_row(ws, [H(c[1], c[2]) for c in cols])
        num_keys = {"qty", "rate", "net", "vat", "grand", "outstanding", "debit", "credit", "balance",
                    "cgst", "sgst", "igst", "cess"}
        totals = {k: 0.0 for k in ("net", "vat", "grand", "outstanding", "debit", "credit",
                                    "cgst", "sgst", "igst", "cess")}
        recon = {"in": {"vat": 0.0, "net": 0.0, "rows": 0},
                 "out": {"vat": 0.0, "net": 0.0, "rows": 0}}
        for i, r in enumerate(rows, start=1):
            adj = r.get("_adj")
            vals = []
            for (k, _en, _ar) in cols:
                v = i if k == "idx" else r.get(k)
                if k in ("posting_date", "bill_date"):
                    v = _dcell(ws, v)
                vals.append(v)
            ws.append(vals)
            for j, (k, _en, _ar) in enumerate(cols, start=1):
                cell = ws.cell(row=ws.max_row, column=j)
                cell.border = thin
                if adj == "in":
                    cell.fill = F_INC
                elif adj == "out":
                    cell.fill = F_EXC
                    cell.font = Font(color="B3261E")
                if k in ("posting_date", "bill_date"):
                    cell.number_format = "DD-MM-YYYY"
                elif k == "tax_rate":
                    cell.number_format = "0%"
                elif k in num_keys:
                    cell.number_format = "#,##0.00"
                    cell.alignment = Alignment(horizontal="right")
                # Excluded rows are visible but never counted.
                if adj != "out" and k in totals and isinstance(r.get(k), (int, float)):
                    totals[k] += r[k]
            if adj in recon:
                recon[adj]["vat"] += flt(r.get("vat") or 0)
                recon[adj]["net"] += flt(r.get("net") or 0)
                recon[adj]["rows"] += 1
        # Totals row
        vals = []
        for (k, _en, _ar) in cols:
            vals.append(_("TOTAL") if k in ("idx", "party", "posting_date") and not vals else
                        (flt(totals[k], 2) if k in totals else None))
        ws.append(vals)
        for j, (k, _en, _ar) in enumerate(cols, start=1):
            c = ws.cell(row=ws.max_row, column=j)
            c.fill = F_TOT; c.font = Font(bold=True, color="11816F")
            if k in num_keys:
                c.number_format = "#,##0.00"; c.alignment = Alignment(horizontal="right")
        # Reconciliation block (v2.34.0) — a proper labeled mini-table whose
        # arithmetic CHAINS: invoiced-in-period (including rows later
        # deferred) − deferred + included-from-other-periods = VAT for this
        # return. Previous layout dropped numbers under unrelated register
        # columns and mixed a non-chaining figure — confusing, now fixed.
        if recon["in"]["rows"] or recon["out"]["rows"]:
            gross_net = flt(totals.get("net", 0) - recon["in"]["net"] + recon["out"]["net"], 2)
            gross_vat = flt(totals.get("vat", 0) - recon["in"]["vat"] + recon["out"]["vat"], 2)
            ws.append([])
            ws.append([H("Reconciliation", "التسوية"),
                       H("Amount", "المبلغ"), H("VAT", "الضريبة")])
            for c in ws[ws.max_row][:3]:
                c.fill = F_HEAD; c.font = Font(bold=True, color="FFFFFF")
            lines = [(H("Invoiced in period", "فواتير الفترة"), gross_net, gross_vat, False)]
            if recon["out"]["rows"]:
                lines.append((H("Less: deferred to payment period",
                                "ناقص: مؤجلة إلى فترة السداد"),
                              flt(-recon["out"]["net"], 2), flt(-recon["out"]["vat"], 2), False))
            if recon["in"]["rows"]:
                lines.append((H("Add: included from other periods (paid this period)",
                                "زائد: مضافة من فترات أخرى (مسددة خلال الفترة)"),
                              flt(recon["in"]["net"], 2), flt(recon["in"]["vat"], 2), False))
            lines.append((H("VAT for this return", "الضريبة لهذا الإقرار"),
                          flt(totals.get("net", 0), 2), flt(totals.get("vat", 0), 2), True))
            for (label, netv, vatv, last) in lines:
                ws.append([label, netv, vatv])
                for j in (1, 2, 3):
                    c = ws.cell(row=ws.max_row, column=j)
                    c.font = Font(bold=last, italic=not last,
                                  color="11816F" if last else "555555")
                    if j > 1:
                        c.number_format = "#,##0.00"
                        c.alignment = Alignment(horizontal="right")
                    if last:
                        c.fill = F_TOT
        autow(ws)

    def gtpl_sheet(sheet_cfg, spec, opts):
        """The deferral register: which government supplies this return declares,
        which it carries forward, and which need a human before it can be filed.

        Sectioned rather than flat because the three populations answer different
        questions and get summed differently — declared feeds the box Amount,
        credit notes feed its Adjustment, and deferred feeds neither while still
        needing to be visible and to reconcile."""
        from ..utils.gtpl_core import box_figures
        from .gtpl import build_plan, ledger_check

        plan = build_plan(company, from_date, to_date)
        ws = new_sheet(sheet_cfg.get("title") or spec["label"])
        head_block(ws, sheet_cfg.get("title") or spec["label"])

        rule = plan.get("rule")
        if not rule:
            ws.append([_("No active GTPL rule applies to a period ending {0}.").format(to_date)])
            ws[f"A{ws.max_row}"].font = Font(italic=True, color="B3261E")
            ws.append([_("Create an Insight GTPL Rule effective on or before that date.")])
            ws[f"A{ws.max_row}"].font = Font(size=9, color="777777")
            autow(ws)
            return
        ws.append(["{0}: {1} · {2} · {3} {4}".format(
            _("Rule"), rule["name"], rule["trigger_basis"],
            _("target box"), rule.get("target_box") or "—")])
        ws[f"A{ws.max_row}"].font = Font(size=9, color="777777")
        ws.append([])

        cols = spec["columns"]
        show_attention = int(opts.get("show_attention", 1) or 0)
        show_counted = int(opts.get("show_already_counted", 0) or 0)

        sections = [
            (H("Declared in this return", "المصرح به في هذا الإقرار"),
             {"released", "released_by_credit_note"}, F_INC, None),
            (H("Adjustments — credit notes issued this period", "التعديلات — إشعارات دائنة صادرة خلال الفترة"),
             {"credit_note"}, PatternFill("solid", fgColor="FFF6E5"), None),
            (H("Carried forward — not yet due", "مرحّل — لم تستحق بعد"),
             {"deferred", "still_deferred"}, F_EXC, "B3261E"),
        ]
        if show_attention:
            sections.append((H("Needs a decision before filing", "تحتاج قراراً قبل التقديم"),
                             {"partial", "scope_unknown"},
                             PatternFill("solid", fgColor="F0F0F0"), "555555"))
        if show_counted:
            sections.append((H("Already declared in earlier returns", "سبق التصريح بها في إقرارات سابقة"),
                             {"already_counted", "in_period"},
                             PatternFill("solid", fgColor="F7F7F7"), "777777"))

        subtotals = {}
        for (label, states, fill, colour) in sections:
            rows = [r for r in plan["rows"] if r["state"] in states]
            ws.append([label])
            c = ws.cell(row=ws.max_row, column=1)
            c.font = Font(bold=True, color="1F4E5F"); c.fill = fill
            if not rows:
                ws.append([_("None.")])
                ws[f"A{ws.max_row}"].font = Font(italic=True, size=9, color="777777")
                subtotals[label] = (0.0, 0.0)
                ws.append([])
                continue
            header_row(ws, [H(c1, c2) for (_k, c1, c2) in cols])
            net = vat = 0.0
            for i, r in enumerate(rows, start=1):
                ws.append([i if k == "idx" else
                           (_dcell(ws, r.get(k)) if k in ("posting_date", "release_date") else r.get(k))
                           for (k, _e, _a) in cols])
                for j, (k, _e, _a) in enumerate(cols, start=1):
                    cell = ws.cell(row=ws.max_row, column=j)
                    cell.border = thin
                    cell.fill = fill
                    if colour:
                        cell.font = Font(color=colour)
                    if k in ("posting_date", "release_date"):
                        cell.number_format = "DD-MM-YYYY"
                    elif k in ("net", "vat"):
                        cell.number_format = "#,##0.00"
                        cell.alignment = Alignment(horizontal="right")
                net += flt(r.get("net"), 2); vat += flt(r.get("vat"), 2)
            ws.append([None] * (len(cols) - 3) + [H("Subtotal", "المجموع الفرعي"),
                                                  flt(net, 2), flt(vat, 2)])
            for cc in ws[ws.max_row]:
                cc.font = Font(bold=True); cc.fill = F_TOT
            for j in (len(cols) - 1, len(cols)):
                ws.cell(row=ws.max_row, column=j).number_format = "#,##0.00"
            subtotals[label] = (flt(net, 2), flt(vat, 2))
            ws.append([])

        # Reconciliation — the block the accountant's coloured footer was doing by
        # hand, stated as the arithmetic that produces the filed figures.
        fig = box_figures(plan, STANDARD_RATE)
        box = rule.get("target_box") or "1.2"
        amount, adjustment, vat_due = fig["amount"], fig["adjustment"], fig["vat"]

        ws.append([H("Reconciliation to box {0}".format(box),
                     "التسوية مع الخانة {0}".format(box))])
        ws[f"A{ws.max_row}"].font = Font(bold=True, size=12, color="1F4E5F")
        for (lab, val, bold) in (
            (H("Amount (declared this period)", "المبلغ (المصرح به هذه الفترة)"), amount, False),
            (H("Adjustment (credit notes)", "مبلغ التعديل (إشعارات دائنة)"), adjustment, False),
            (H("Taxable base", "الوعاء الضريبي"), fig["base"], False),
            (H("VAT at {0}%".format(int(STANDARD_RATE)), "الضريبة بنسبة {0}%".format(int(STANDARD_RATE))), vat_due, True),
        ):
            ws.append([lab, val])
            cc = ws.cell(row=ws.max_row, column=2)
            cc.number_format = "#,##0.00"; cc.alignment = Alignment(horizontal="right")
            cc.font = Font(bold=bold, color="11816F" if bold else "555555")
            ws.cell(row=ws.max_row, column=1).font = Font(bold=bold, italic=not bold)
            if bold:
                cc.fill = F_TOT; ws.cell(row=ws.max_row, column=1).fill = F_TOT

        # A cross-check, not a restatement: VAT summed off the invoices should equal
        # VAT recomputed from the base. A gap here means rounding on the invoices or
        # a non-standard rate inside a government supply, and the preparer needs to
        # see it rather than have one of the two figures quietly chosen for them.
        if abs(fig["variance"]) >= 0.01:
            ws.append([H("Check: VAT on the invoices differs from {0}% of the base by {1}"
                         .format(int(STANDARD_RATE), fig["variance"]),
                         "تنبيه: فرق بين ضريبة الفواتير و{0}% من الوعاء".format(int(STANDARD_RATE)))])
            ws[f"A{ws.max_row}"].font = Font(size=9, color="B3261E")

        # Ledger cross-check — the pull an accountant otherwise does by hand to
        # satisfy themselves the VAT account agrees with what the return says.
        # Reported, never reconciled away: a gap is information, and nothing here
        # can know which of the two numbers is the wrong one.
        checks = ledger_check(company, rule, from_date, to_date)
        if checks:
            ws.append([])
            ws.append([H("Ledger cross-check", "المطابقة مع دفتر الأستاذ")])
            ws[f"A{ws.max_row}"].font = Font(bold=True, size=12, color="1F4E5F")
            for chk in checks:
                ws.append(["{0} — {1}".format(_(chk["label"]), chk["account"]), chk["movement"]])
                cc = ws.cell(row=ws.max_row, column=2)
                cc.number_format = "#,##0.00"; cc.alignment = Alignment(horizontal="right")
                cc.font = Font(color="555555")
            deferred = subtotals.get(sections[2][0], (0.0, 0.0))[1]
            ws.append([H("Register: VAT carried forward", "السجل: ضريبة مرحّلة"), flt(deferred, 2)])
            cc = ws.cell(row=ws.max_row, column=2)
            cc.number_format = "#,##0.00"; cc.alignment = Alignment(horizontal="right")
            cc.font = Font(color="555555")
            ws.append([H("Compare the deferred account against the carried-forward figure above; "
                         "a difference is a posting the register cannot see.",
                         "قارن حساب الضريبة المؤجلة مع المبلغ المرحّل أعلاه؛ الفرق يعني قيداً لا يظهر في السجل.")])
            ws[f"A{ws.max_row}"].font = Font(size=9, italic=True, color="777777")

        if plan.get("ungrouped_customers"):
            ws.append([])
            ws.append([H("Customers with no customer group — GTPL scope undetermined: ",
                         "عملاء بدون مجموعة — نطاق النظام غير محدد: ")
                       + ", ".join(plan["ungrouped_customers"][:12])
                       + ("…" if len(plan["ungrouped_customers"]) > 12 else "")])
            ws[f"A{ws.max_row}"].font = Font(size=9, color="B3261E")
        autow(ws)

    # ── v3.2.0 — ageing sheets (formulas, so the workbook stays live) ─────────
    # v3.3.0 — header text and colours come from the Dashboard sheet's options
    # (defaults = the client's template); every ageing sheet uses the same style.
    as_of = to_date
    ageing_refs = {}            # sheet type -> dict(name, total_row, first_bucket, labels, has_nd, parties)
    dashboards = []
    detail_jobs = []            # v3.6.6 — per-party document detail, written after the summaries
    F_NUM = "#,##0.00;[Red](#,##0.00);\"-\""
    COMPANY_LABEL = frappe.db.get_value("Company", company, "company_name") or company
    CURRENCY = frappe.db.get_value("Company", company, "default_currency") or "SAR"
    dash_opts = next((sh.get("options") or {} for sh in cfg.get("sheets") or []
                      if sh.get("type") == "ageing_dashboard"), {})
    ST = ageing_style(dash_opts)

    def font(color=None, bold=False, size=9):
        return Font(name="Arial", bold=bold, size=size, color=color or ST["text"])

    def fill(color):
        return PatternFill("solid", fgColor=color)

    def label_text(x):
        if not isinstance(x, dict):
            return str(x)
        en, ar = x.get("en") or x.get("key") or "", x.get("ar") or ""
        return ar if lang == "ar" and ar else f"{en} / {ar}" if lang == "both" and ar else en

    def ageing_sheet(sheet_cfg, stype, opts):
        party_type = "Customer" if stype == "ar_ageing" else "Supplier"
        data, rows = ageing_rows(company, as_of, party_type, opts)
        # v3.6.5 — fully settled parties (outstanding 0) are hidden unless asked for;
        # negative balances (advances / overpayments) always stay.
        if not int(opts.get("show_zero") or 0):
            rows = [r for r in rows if abs(flt(r.get("outstanding"))) >= 0.005]
        title = sheet_cfg.get("title") or ("AR Ageing" if stype == "ar_ageing" else "AP Ageing")
        ws = new_sheet(title)
        heading = ("Accounts Receivable — Ageing Report" if stype == "ar_ageing"
                   else "Accounts Payable — Ageing Report")
        has_nd = int(opts.get("split_not_due", 1) if opts.get("split_not_due") not in (None, "") else 1)
        # v3.2.1 — the engine's slab labels are {"key", "en", "ar"}; write text.
        labels = [label_text(x) for x in data["labels"]]
        cols = ["#", "Party Name", "Invoiced Amount", "Paid Amount",
                "Credit Note" if stype == "ar_ageing" else "Debit Note", "Adjustment", "Outstanding"]
        cols += (["Not Yet Due"] if has_nd else []) + list(labels) + ["Total Due"]
        last = get_column_letter(len(cols))
        ws["B2"] = heading
        ws["B2"].font = font(ST["accent"], True, 16)
        meta = [ST["subtitle"] or COMPANY_LABEL, f"Report Date: {getdate(as_of).strftime('%d %B %Y')}",
                f"Ageing Range: {opts.get('slabs') or '30,60,90,120'} "
                f"({'from due date' if (opts.get('based_on') or 'due') == 'due' else 'from posting date'})",
                f"Currency: {data['currency']}"]
        for k, txt in enumerate(meta):
            r = 4 + k
            ws[f"B{r}"] = txt
            ws[f"B{r}"].font = font(size=8)
            ws.merge_cells(f"B{r}:{last}{r}")
        ws.merge_cells(f"B2:{last}2")
        for j, h in enumerate(cols, 1):
            c = ws.cell(row=9, column=j, value=h)
            c.font = font("FFFFFF", True, 9)
            c.fill = fill(ST["accent"])
            c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        ws.row_dimensions[9].height = 30
        first = r0 = 10
        fb = 8 + (1 if has_nd else 0)                   # first ageing bucket column (1-based)
        parties = []
        mismatched: list[str] = []
        for i, row in enumerate(rows, 1):
            rr = r0 + i - 1
            vals = [i, row["label"], row["invoiced"], row["paid"], row["note"], row.get("adj", 0.0), None]
            vals += ([row["not_due"]] if has_nd else []) + list(row["buckets"]) + [None]
            for j, v in enumerate(vals, 1):
                c = ws.cell(row=rr, column=j, value=v)
                c.font = font("999999", size=8) if j == 1 else font()
                if j >= 3:
                    c.number_format = F_NUM
            ws.cell(row=rr, column=7, value=f"=C{rr}-D{rr}-E{rr}-F{rr}")    # outstanding
            lb = get_column_letter(len(cols) - 1)
            ws.cell(row=rr, column=len(cols), value=f"=SUM(H{rr}:{lb}{rr})")  # total due
            if row.get("mismatch"):
                mismatched.append(row["label"])
                for j in range(1, len(cols) + 1):
                    ws.cell(row=rr, column=j).fill = fill("FFE8C2")
            elif i % 2 == 0:
                for j in range(1, len(cols) + 1):
                    ws.cell(row=rr, column=j).fill = fill(ST["band"])
            parties.append((row["label"], flt(row["outstanding"]), rr, flt(row.get("not_due"))))
        tr = r0 + len(rows)
        ws.cell(row=tr, column=1, value="Total")
        ws.merge_cells(f"A{tr}:B{tr}")
        for j in range(1, len(cols) + 1):
            c = ws.cell(row=tr, column=j)
            if j >= 3:
                L = get_column_letter(j)
                c.value = f"=SUM({L}{first}:{L}{tr - 1})" if rows else 0
                c.number_format = F_NUM
            c.font = font(ST["total"], True, 9)
            c.border = Border(top=Side(style="thin", color=ST["accent"]), bottom=Side(style="double", color=ST["accent"]))
        chk = tr + 2
        ws.cell(row=chk, column=2, value="Check: Outstanding − Total Due (should be 0)").font = font("777777", size=8)
        ws.cell(row=chk, column=len(cols), value=f"=G{tr}-{last}{tr}").number_format = F_NUM
        if (opts.get("amounts") or "open") == "open":
            note = ("Amounts cover open invoices only (fully settled invoices are left out). Settled by: Paid = payment "
                    f"entries; {'Credit' if stype == 'ar_ageing' else 'Debit'} Note = return invoices; Adjustment = journal "
                    "entries and other vouchers. Unallocated items are included by the same rule. "
                    f"Source: Neotec Insight ageing engine. Generated {datetime.now():%d %b %Y %H:%M}.")
        else:
            note = (f"Source: Neotec Insight ageing engine (GL-based, payments matched to the invoices they settle); "
                    f"{'credit' if stype == 'ar_ageing' else 'debit'} notes shown separately from payments. "
                    f"Generated {datetime.now():%d %b %Y %H:%M}.")
        ws.cell(row=chk + 1, column=2, value=note).font = font("777777", size=8)
        ws.merge_cells(start_row=chk + 1, start_column=2, end_row=chk + 1, end_column=len(cols))
        if mismatched:
            warn = ws.cell(row=chk + 2, column=2, value=(
                f"⚠ {len(mismatched)} part{'y' if len(mismatched) == 1 else 'ies'} (highlighted) — the invoice detail does "
                "not add up to the ageing balance; review before sending."))
            warn.font = font("B42318", True, 9)
        ws.freeze_panes = "C10"
        ws.column_dimensions["A"].width = 5
        ws.column_dimensions["B"].width = 42
        for j in range(3, len(cols) + 1):
            ws.column_dimensions[get_column_letter(j)].width = 15
        ageing_refs[stype] = {"name": ws.title, "tr": tr, "fb": fb, "labels": labels, "has_nd": has_nd,
                              "parties": parties}
        if int(opts.get("details", 1) if opts.get("details") not in (None, "") else 1) and rows:
            detail_jobs.append((ws, party_type, [(r.get("party"), r["label"], r0 + i, flt(r["outstanding"]), r.get("docs"))
                                                 for i, r in enumerate(rows)], opts))

    def ageing_dashboard(sheet_cfg, opts):
        ws = new_sheet(sheet_cfg.get("title") or "Dashboard")
        wb.move_sheet(ws, offset=-(len(wb.sheetnames) - 1))
        ws["B2"] = ST["title"] or "AR & AP Ageing Report — Executive Dashboard"
        ws["B2"].font = font(ST["dark"], True, 16)
        ws["B3"] = f"  {ST['subtitle'] or COMPANY_LABEL}   |   As of {getdate(as_of).strftime('%d %B %Y')}   |   Currency: {CURRENCY}"
        ws["B3"].font = font("555555", size=10)
        ws.merge_cells("B3:I3")
        r = 5
        bottom = r
        for (stype, col0, head) in (("ar_ageing", 2, "  Accounts Receivable — Ageing Summary"),
                                    ("ap_ageing", 7, "  Accounts Payable — Ageing Summary")):
            ref_ = ageing_refs.get(stype)
            if not ref_:
                continue
            ref = "'" + ref_["name"].replace("'", "''") + "'"
            span = range(col0, col0 + (4 if col0 == 2 else 3))
            ws.cell(row=r, column=col0, value=head)
            for j in span:
                ws.cell(row=r, column=j).fill = fill(ST["dark"])
                ws.cell(row=r, column=j).font = font("FFFFFF", True, 10)
            lines = ([("Not Yet Due", ref_["fb"] - 1)] if ref_["has_nd"] else []) + \
                [(lab, ref_["fb"] + i) for i, lab in enumerate(ref_["labels"])]
            vcol = col0 + (3 if col0 == 2 else 2)
            for k, (lab, colno) in enumerate(lines):
                rr = r + 1 + k
                ws.cell(row=rr, column=col0, value=lab).font = font(bold=True)
                cc = ws.cell(row=rr, column=vcol, value=f"={ref}!{get_column_letter(colno)}{ref_['tr']}")
                cc.number_format = F_NUM
                cc.font = font(bold=True)
            tot = r + 1 + len(lines)
            ws.cell(row=tot, column=col0, value="Total AR Outstanding" if stype == "ar_ageing" else "Total AP Outstanding")
            ws.cell(row=tot, column=vcol, value=f"={ref}!G{ref_['tr']}").number_format = F_NUM
            for j in span:
                ws.cell(row=tot, column=j).fill = fill(ST["dark"])
                ws.cell(row=tot, column=j).font = font("FFFFFF", True, 10)
            share = tot + 1
            ws.cell(row=share, column=col0, value="Over 90 days, % of total").font = font("777777", size=8)
            over = [get_column_letter(ref_["fb"] + i) for i, lab in enumerate(ref_["labels"])
                    if any(x in lab for x in ("91", "121", "+", "Above", ">"))]
            if over:
                expr = "+".join(f"{ref}!{L}{ref_['tr']}" for L in over)
                sc = ws.cell(row=share, column=vcol, value=f"=IFERROR(({expr})/{ref}!G{ref_['tr']},0)")
                sc.number_format = "0.0%"
                sc.font = font("777777", size=8)
            bottom = max(bottom, share)

        # v3.3.0 — Top N tables: always ranked over ALL parties in the sheet
        # (not the screen's selection); each amount links to that party's
        # Outstanding cell, so names and amounts can never be mismatched.
        r = bottom + 2
        for stype, n_key, head, who in (("ar_ageing", "top_ar", "Top {n} AR Customers by Outstanding Balance", "Customer Name"),
                                        ("ap_ageing", "top_ap", "Top {n} AP Suppliers by Outstanding Balance", "Supplier Name")):
            ref_ = ageing_refs.get(stype)
            n = int(opts.get(n_key) if opts.get(n_key) not in (None, "") else (5 if stype == "ar_ageing" else 0))
            if not ref_ or n <= 0:
                continue
            # v3.3.1 — rank by the amount actually DUE (outstanding − not yet due)
            # by default; values are written (not formulas) so they show in any
            # viewer — the ranking itself is fixed at download time anyway.
            by_due = (opts.get("top_by") or "overdue") != "outstanding"
            items = [(p[0], p[1], p[2], round(p[1] - (p[3] if len(p) > 3 else 0.0), 2)) for p in ref_["parties"]]
            key = 3 if by_due else 1
            ranked = sorted((p for p in items if p[key] > 0.005), key=lambda p: -p[key])[:n]
            head = head.replace("by Outstanding Balance", "by Overdue Balance" if by_due else "by Outstanding Balance")
            ws.cell(row=r, column=2, value="  " + head.format(n=n))
            for j in range(2, 10):
                ws.cell(row=r, column=j).fill = fill(ST["accent"])
                ws.cell(row=r, column=j).font = font("FFFFFF", True, 10)
            hr = r + 1
            heads = ((2, "#"), (3, who), (7, f"Outstanding ({CURRENCY})"),
                     (9, f"Due — overdue ({CURRENCY})")) if by_due else ((2, "#"), (3, who), (9, f"Outstanding ({CURRENCY})"))
            for col, txt in heads:
                ws.cell(row=hr, column=col, value=txt).font = font(ST["accent"], True)
                if col in (7, 9):
                    ws.cell(row=hr, column=col).alignment = Alignment(horizontal="right", wrap_text=True)
            for j in range(2, 10):
                ws.cell(row=hr, column=j).border = Border(bottom=Side(style="thin", color=ST["accent"]))
            for k, (name, outstanding, _prow, due) in enumerate(ranked, 1):
                rr = hr + k
                ws.cell(row=rr, column=2, value=k).font = font("999999", size=8)
                ws.cell(row=rr, column=3, value=name).font = font()
                ws.merge_cells(start_row=rr, start_column=3, end_row=rr, end_column=6 if by_due else 8)
                if by_due:
                    o = ws.cell(row=rr, column=7, value=outstanding)
                    o.number_format = F_NUM
                    o.font = font("777777")
                c = ws.cell(row=rr, column=9, value=due if by_due else outstanding)
                c.number_format = F_NUM
                c.font = font(ST["total"], True)
            if not ranked:
                ws.cell(row=hr + 1, column=3, value="No overdue balances." if by_due else
                        "No outstanding balances.").font = font("777777", size=8)
            r = hr + max(len(ranked), 1) + 2
        ws.cell(row=r, column=2, value="Summary figures are live formulas linked to the AR and AP sheets; the Top tables "
                                         "are values as at the report date. Due = outstanding − not yet due.").font = font("777777", size=8)
        for L, w in (("A", 2), ("B", 22), ("C", 4), ("D", 4), ("E", 18), ("F", 4), ("G", 22), ("H", 4), ("I", 18)):
            ws.column_dimensions[L].width = w

    for sheet_cfg in cfg.get("sheets") or []:
        stype = sheet_cfg.get("type")
        spec = _TYPES.get(stype)
        if not spec:
            continue
        opts = sheet_cfg.get("options") or {}
        if stype == "vat_return":
            data = vat_return(company, from_date, to_date)
            ws = new_sheet(sheet_cfg.get("title") or "VAT Return")
            head_block(ws, sheet_cfg.get("title") or _("VAT Return"))
            header_row(ws, ["#", H("Description", "الوصف"), H("Amount", "المبلغ"),
                            H("Adjustment", "التعديل"), H("VAT Amount", "مبلغ الضريبة")])
            for ln in (data.get("sales_lines") or []) + [data.get("box6") or {}] + \
                      (data.get("purchase_lines") or []) + [data.get("box12") or {}]:
                if not ln:
                    continue
                ws.append([ln.get("box"), ln.get("label"), flt(ln.get("amount"), 2),
                           flt(ln.get("adjustment"), 2), flt(ln.get("vat"), 2)])
                for j in (3, 4, 5):
                    ws.cell(row=ws.max_row, column=j).number_format = "#,##0.00"
            net = data.get("net") or {}
            ws.append([net.get("box"), _("Net VAT due"), None, None, flt(net.get("vat"), 2)])
            for c in ws[ws.max_row]:
                c.fill = F_TOT; c.font = Font(bold=True, color="11816F")
            ws.cell(row=ws.max_row, column=5).number_format = "#,##0.00"
            autow(ws)
        elif stype == "gst_sales_register":
            register_sheet(sheet_cfg, _gst_register_rows(company, from_date, to_date, "Sales Invoice"), spec)
        elif stype == "gst_purchase_register":
            register_sheet(sheet_cfg, _gst_register_rows(company, from_date, to_date, "Purchase Invoice"), spec)
        elif stype == "sales_register":
            register_sheet(sheet_cfg, _sales_rows(company, from_date, to_date, opts), spec)
        elif stype == "purchase_register":
            register_sheet(sheet_cfg, _purchase_rows(company, from_date, to_date, opts), spec)
        elif stype == "gtpl_register":
            gtpl_sheet(sheet_cfg, spec, opts)
        elif stype == "gl_ledger":
            rows, accounts = _gl_rows(company, from_date, to_date, opts)
            spec2 = dict(spec)
            ws_title = sheet_cfg.get("title") or spec["label"]
            register_sheet({**sheet_cfg, "title": ws_title}, rows, spec2)
            wb[wb.sheetnames[-1]].insert_rows(3)
            wb[wb.sheetnames[-1]]["A3"] = _("Accounts: ") + ", ".join(accounts[:6]) + ("…" if len(accounts) > 6 else "")
            wb[wb.sheetnames[-1]]["A3"].font = Font(size=9, color="777777")

        elif stype in ("ar_ageing", "ap_ageing"):
            ageing_sheet(sheet_cfg, stype, opts)
        elif stype == "ageing_dashboard":
            dashboards.append((sheet_cfg, opts))

    # v3.2.0 — dashboards are written last (they point at the AR/AP sheets) and
    # then moved to the front, as in the client's workbook.
    def ageing_detail(summary_ws, party_type, party_rows, opts):
        """v3.6.6 — the documents behind each party's outstanding, linked both ways."""
        from neotec_insight.neotec_insight.api.ageing import billwise
        from openpyxl.worksheet.hyperlink import Hyperlink

        from neotec_insight.neotec_insight.utils.compat import desk_url
        ledger = all(pr[4] is not None for pr in party_rows)          # v3.7.2 — documents from the Payment Ledger
        docs = None if ledger else open_invoice_docs(company, party_type, [p for p, *_x in party_rows if p], as_of)
        bw = {"blocks": []} if ledger else billwise(company=company, party_type=party_type, parties=[p for p, *_x in party_rows if p],
                      as_of=as_of, based_on=opts.get("based_on") or "due", mode=opts.get("mode") or "days",
                      slabs=opts.get("slabs") or "30,60,90,120", max_parties=len(party_rows))
        blocks = {b["party"]: b for b in bw.get("blocks") or []}
        bills = {x["bill"] for b in blocks.values() for x in b.get("open_bills") or []}
        inv_dt = "Sales Invoice" if party_type == "Customer" else "Purchase Invoice"
        is_inv = set(frappe.get_all(inv_dt, filters={"name": ["in", list(bills)]}, pluck="name",
                                    limit_page_length=0, order_by="name asc")) if bills else set()
        base_url = frappe.utils.get_url()

        def link(cell, dt, dn):
            cell.hyperlink = base_url + desk_url(f"{dt.lower().replace(' ', '-')}/{dn}")
            cell.font = font("1F4E9A")

        title = (summary_ws.title[:24] + " Detail")[:31]
        ws = new_sheet(title)
        ws["B2"] = ("Accounts Receivable" if party_type == "Customer" else "Accounts Payable") + " — documents behind each balance"
        ws["B2"].font = font(ST["accent"], True, 14)
        ws["B3"] = f"{ST['subtitle'] or COMPANY_LABEL}   |   As of {getdate(as_of).strftime('%d %B %Y')}"
        ws["B3"].font = font("555555", size=9)
        heads = ["", "Date", "Document", "Type", "Age (days)", "Bucket", "Amount", "Settled", "Outstanding"]
        r = 5
        sref = "'" + summary_ws.title.replace("'", "''") + "'"
        for party, label, srow, p_out, ldocs in party_rows:
            b = blocks.get(party) or {}
            start = r
            hc = ws.cell(row=r, column=2, value=label)
            hc.font = font("FFFFFF", True, 10)
            for j in range(1, 10):
                ws.cell(row=r, column=j).fill = fill(ST["accent"])
            back = ws.cell(row=r, column=9, value="↩ summary")
            back.hyperlink = Hyperlink(ref=back.coordinate, location=f"{sref}!B{srow}")
            back.font = font("FFFFFF", size=8)
            # party name on the summary jumps here
            sc = summary_ws.cell(row=srow, column=2)
            sc.hyperlink = Hyperlink(ref=sc.coordinate, location=f"'{title}'!B{start}")
            sc.font = font("1F4E9A")
            r += 1
            for j, h in enumerate(heads, 1):
                c = ws.cell(row=r, column=j, value=h or None)
                c.font = font(ST["accent"], True, 9)
                c.border = Border(bottom=Side(style="thin", color=ST["accent"]))
            r += 1
            first = r
            bills = b.get("open_bills") or []
            if ldocs is not None:
                bills = [{"bill": g["name"], "date": str(g["posting_date"]), "amount": g["invoiced"],
                          "remaining": g["outstanding"], "age": g.get("age"), "slab": g.get("slab"), "_dt": g["doctype"]}
                         for g in ldocs]
                b = {"unallocated": []}
            elif docs is not None:                    # v3.7.1 — the invoices ERPNext calls open, at their own outstanding
                mine = docs.get(party, {})
                bills = [{**x, "amount": mine[x["bill"]]["base_total"], "remaining": mine[x["bill"]]["base_outstanding"]}
                         for x in bills if x["bill"] in mine]
            for x in bills:
                dt_ = x.get("_dt") or (inv_dt if x["bill"] in is_inv else "Journal Entry")
                vals = [None, x["date"], x["bill"], "Invoice" if dt_ == inv_dt else dt_, x.get("age"),
                        x.get("slab"), x["amount"], None, x["remaining"]]
                for j, v in enumerate(vals, 1):
                    ws.cell(row=r, column=j, value=v).font = font()
                ws.cell(row=r, column=8, value=f"=G{r}-I{r}")
                link(ws.cell(row=r, column=3), dt_, x["bill"])
                r += 1
            for u in b.get("unallocated") or []:
                vals = [None, u.get("posting_date"), u.get("voucher_no"), (u.get("voucher_type") or "Payment") + " (unallocated)",
                        None, None, None, None, -flt(u.get("amount"))]
                for j, v in enumerate(vals, 1):
                    ws.cell(row=r, column=j, value=v).font = font()
                if u.get("voucher_type") and u.get("voucher_no"):
                    link(ws.cell(row=r, column=3), u["voucher_type"], u["voucher_no"])
                r += 1
            shown = sum(flt(x["remaining"]) for x in bills) - sum(flt(u.get("amount")) for u in b.get("unallocated") or [])
            if abs(p_out - shown) > 0.005:          # what the documents do not explain, shown openly
                ws.cell(row=r, column=3, value="Exchange / rounding differences").font = font("777777")
                ws.cell(row=r, column=9, value=round(p_out - shown, 2)).font = font("777777")
                r += 1
            for rr in range(first, r):
                for j in (7, 8, 9):
                    ws.cell(row=rr, column=j).number_format = F_NUM
            tot = ws.cell(row=r, column=9, value=f"=SUM(I{first}:I{max(first, r - 1)})" if r > first else 0)
            tot.number_format = F_NUM
            tot.font = font(ST["total"], True)
            ws.cell(row=r, column=3, value="Party total").font = font(ST["total"], True)
            chk = ws.cell(row=r, column=8, value=f'=IF(ROUND(I{r}-{sref}!G{srow},2)=0,"✓ matches summary","check")')
            chk.font = font("777777", size=8)
            r += 2
        for L, w in (("A", 2), ("B", 12), ("C", 24), ("D", 22), ("E", 10), ("F", 14), ("G", 15), ("H", 15), ("I", 15)):
            ws.column_dimensions[L].width = w
        ws.freeze_panes = "A5"

    for job in detail_jobs:
        ageing_detail(*job)

    for sheet_cfg, opts in dashboards:
        ageing_dashboard(sheet_cfg, opts)

    if not wb.sheetnames:
        frappe.throw(_("The pack produced no sheets — check its configuration."))
    # v3.3.1 — make Excel recalculate every formula when the file opens
    try:
        from openpyxl.workbook.properties import CalcProperties
        wb.calculation = CalcProperties(fullCalcOnLoad=True)
    except Exception:
        pass

    buf = io.BytesIO()
    wb.save(buf)
    frappe.local.response.filename = f"{frappe.scrub(pack['title'])}-{from_date}-to-{to_date}.xlsx"
    frappe.local.response.filecontent = buf.getvalue()
    frappe.local.response.type = "binary"


@frappe.whitelist()
def list_gl_accounts(company=None):
    """Leaf accounts for the pack designer's ledger picker."""
    company = company or _default_company()
    if not frappe.has_permission("Account", "read"):
        return []
    from neotec_insight.neotec_insight.utils.compat import with_aliases
    return with_aliases(frappe.get_all("Account", filters={"company": company, "is_group": 0},
                                       fields=["name", "account_name"],
                                       order_by="name asc", limit_page_length=0),
                        {"label": "account_name"})
