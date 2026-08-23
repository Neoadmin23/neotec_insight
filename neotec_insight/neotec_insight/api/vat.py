# Copyright (c) 2026, Neotec Integrated Solution
# VAT Return (KSA / ZATCA) — assembles the official 16-box VAT return from the
# company's GL and tax invoices for a chosen period. This is a PREPARATION aid:
# figures must be reviewed against source documents before filing on Fatoora.
#
# ZATCA VAT return structure (3 columns per line: Amount = taxable base,
# Adjustment, VAT Amount):
#   Output VAT (sales)            Boxes 1–6
#     1  Standard-rated domestic sales (15%)
#     2  Private healthcare / education / first-house to citizens (0% to seller)
#     3  Zero-rated domestic sales
#     4  Exports (0%, outside GCC)
#     5  Exempt sales
#     6  Total sales (system)
#   Input VAT (purchases)         Boxes 7–12
#     7  Standard-rated domestic purchases (15%)
#     8  Imports — VAT paid at customs
#     9  Imports — VAT via reverse-charge mechanism
#     10 Zero-rated purchases
#     11 Exempt purchases
#     12 Total purchases (system)
#   Net VAT (system)              Boxes 13–16
#     13 Total VAT due for the period (Box 6 VAT − Box 12 VAT)
#     14 Corrections from previous period (±SAR 5,000)
#     15 VAT credit carried forward
#     16 Net VAT due / (reclaimable)
from __future__ import annotations

import re

import frappe
from frappe import _
from frappe.utils import flt, getdate

from ..utils.gtpl_core import sales_box
from .health import _default_company

STANDARD_RATE = 15.0

# Maps a tax-category / template name to a return box by keyword.
_SALES_CATEGORY_RULES = [
    (r"export", "box4"),
    (r"exempt|معف", "box5"),
    (r"health|medic|educat|citizen|first.?house|صح|تعليم", "box2"),
    (r"zero|0\s*%|صفر", "box3"),
]
_PURCHASE_CATEGORY_RULES = [
    (r"reverse|rcm|عكس", "box9"),
    (r"import|customs|جمار|استيراد", "box8"),
    (r"exempt|معف", "box11"),
    (r"zero|0\s*%|صفر", "box10"),
]


# Tax-named accounts that are NOT VAT — never auto-detected (v2.27.1). These
# were the source of real contamination: WHT accruals landed in Output VAT and
# the quarterly ZATCA Sadad settlement appeared as huge negative output.
_NOT_VAT = re.compile(
    r"wht|withhold|استقطاع|zakat|زكاة|income tax|ضريبة الدخل|"
    r"settle|settlement|تسوية|sadad|سداد|excise|انتقائية|الانتقائية", re.I)

# A VAT-adjacent account excluded from being its OWN Output/Input VAT box
# specifically because its name matches settlement/reconciliation — NOT
# because it's a different tax entirely (WHT/Zakat/Sadad, which _NOT_VAT also
# excludes, but which are irrelevant here). This is the account a quarter-end
# closing JE moves VAT INTO — real, observed in IRSAA's own Q2 2026 ledger:
# ACC-JV-2026-01038 (30-06-2026) debits Output VAT 157,109.07 against
# '21204002 - VAT Reconciliation'; ACC-JV-2026-01035, same date, debits that
# same account against Input VAT 30,595.85. Two separate JEs, each touching
# only ONE VAT side plus this clearing account — neither is caught by the
# existing settlement_clause below, which only excludes a voucher touching
# BOTH an output AND an input VAT account directly. Both are, in substance,
# the exact "quarter-end JE that nets VAT into a clearing account" the
# existing exclusion already exists to catch; they just do it as a pair
# instead of one combined entry. Without this, the clearing account being
# correctly excluded from being counted AS Output/Input VAT — the whole
# point of _NOT_VAT's settlement keywords — left its ledger entries fully
# exposed to _non_invoice_vat, which does not itself know a clearing
# account when it sees one.
_VAT_CLEARING = re.compile(
    r"(vat|ضريبة|ضريبه).{0,20}(settle|reconcil|تسوية|مقاصة)|"
    r"(settle|reconcil|تسوية|مقاصة).{0,20}(vat|ضريبة|ضريبه)", re.I)

# A VAT control account is never one of these, whatever it is called. The name
# heuristic alone matched a BANK account called "Bank Saudi Hollandi (IRSAA VAT)"
# and a supplier control account called "C/A - IRSAA VAT Consultancy Co." — both
# then fed the non-invoice VAT lines, and a bank account carries every payment
# the company makes. Root type is not enough to catch this: a bank is an Asset
# like input VAT, and a supplier control is a Liability like output VAT.
_NEVER_VAT_TYPES = {
    "Bank", "Cash", "Receivable", "Payable", "Stock", "Fixed Asset",
    "Accumulated Depreciation", "Capital Work in Progress", "Equity",
    "Cost of Goods Sold", "Stock Received But Not Billed", "Depreciation",
    "Chargeable", "Temporary",
}


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


def _vat_accounts(company):
    """Output (Liability) and input (Asset) VAT control accounts.

    Resolution order (same philosophy as the Classification Studio):
      1. USER TAGS — accounts tagged Output VAT / Input VAT. Tagging ANY
         account on a side switches that side to STRICT mode: only tagged
         accounts count, heuristics off. Accounts tagged anything else
         (including 'Not VAT') are always excluded.
      2. HEURISTICS — account_type='Tax' or a VAT-ish name ('vat', 'ضريبة'),
         MINUS tax-named non-VAT accounts (WHT/withholding/استقطاع, zakat,
         income tax, settlement/تسوية/Sadad). The old matcher counted every
         'ضريبة' account as VAT, which pulled WHT journals into Output VAT
         and the ZATCA payment JE in as negative output.
    """
    from .classify import tag_map
    tags = tag_map(company)
    tagged_out = [a for a, t in tags.items() if t == "output_vat"]
    tagged_in = [a for a, t in tags.items() if t == "input_vat"]
    excluded = {a for a, t in tags.items() if t not in ("output_vat", "input_vat")}

    accts = frappe.get_all("Account",
                           filters={"company": company, "is_group": 0},
                           fields=["name", "account_name", "account_type", "root_type"])

    def is_vat(a):
        text = f"{a.get('account_name') or ''} {a.get('name') or ''}"
        if a.get("account_type") in _NEVER_VAT_TYPES:
            return False
        if _NOT_VAT.search(text):
            return False
        if a.get("account_type") == "Tax":
            return True
        low = text.lower()
        return any(k in low for k in ("vat", "ضريبة", "ضريبه"))

    output = tagged_out if tagged_out else [
        a["name"] for a in accts
        if a["root_type"] == "Liability" and a["name"] not in excluded and is_vat(a)]
    input_ = tagged_in if tagged_in else [
        a["name"] for a in accts
        if a["root_type"] == "Asset" and a["name"] not in excluded and is_vat(a)]

    # v2.87.8 — accounts that ARE VAT-adjacent by name but are excluded from
    # output/input specifically by the settlement/reconciliation half of
    # _NOT_VAT, not by the WHT/Zakat half. See _VAT_CLEARING's own comment
    # for the real ledger case this closes. Computed regardless of tagging
    # mode — a clearing account is a clearing account whether or not the
    # site has gone through and explicitly tagged its VAT control accounts.
    clearing = [a["name"] for a in accts
               if _VAT_CLEARING.search(f"{a.get('account_name') or ''} {a.get('name') or ''}")]
    return output, input_, clearing


# Vouchers that must NEVER feed the non-invoice VAT lines: invoices are already
# counted per-box from the invoice tables, and closing vouchers are bookkeeping.
_INVOICE_VOUCHERS = ("Sales Invoice", "Purchase Invoice", "POS Invoice", "Period Closing Voucher")


def _non_invoice_vat(company, accounts, opposite_accounts, from_date, to_date, debit_positive):
    """VAT posted to `accounts` by documents OTHER than invoices, computed directly
    from the GL (never by subtraction).

    Returns (total, {voucher_type: amount}).

    Excluded on purpose:
      * Sales/Purchase/POS Invoices — their VAT is already in boxes 1–11 from the
        invoice tables; counting their GL rows too would double-count.
      * Opening entries and Period Closing Vouchers.
      * SETTLEMENT / CLEARING vouchers — any voucher that touches BOTH the input and
        the output VAT accounts (the classic quarter-end Journal Entry that nets
        input against output into 'VAT Payable'). Those move VAT between control
        accounts; they do not create input or output tax, so including them zeroes
        out the period and corrupts the return.

    This replaces the old approach of `GL total − invoice total`, which broke in two
    real-world cases: (1) a settlement JE inside the period drove the GL total to ~0,
    turning the difference into a large NEGATIVE 'other input VAT' that cancelled
    box 7; (2) invoices posting VAT to an account outside the detected set made the
    subtraction meaningless."""
    if not accounts:
        return 0.0, {}
    params = {
        "c": company, "a": tuple(accounts), "f": from_date, "t": to_date,
        "inv": _INVOICE_VOUCHERS,
    }
    settlement_clause = ""
    if opposite_accounts:
        params["opp"] = tuple(opposite_accounts)
        settlement_clause = """
             AND NOT EXISTS (
                 SELECT 1 FROM `tabGL Entry` x
                 WHERE x.voucher_type = gle.voucher_type
                   AND x.voucher_no = gle.voucher_no
                   AND x.is_cancelled = 0
                   AND x.account IN %(opp)s)"""
    expr = "SUM(gle.debit - gle.credit)" if debit_positive else "SUM(gle.credit - gle.debit)"
    rows = frappe.db.sql(
        f"""SELECT gle.voucher_type, COALESCE({expr}, 0) AS net
            FROM `tabGL Entry` gle
            WHERE gle.company = %(c)s AND gle.is_cancelled = 0
              AND IFNULL(gle.is_opening, 'No') != 'Yes'
              AND gle.account IN %(a)s
              AND gle.posting_date BETWEEN %(f)s AND %(t)s
              AND gle.voucher_type NOT IN %(inv)s{settlement_clause}
            GROUP BY gle.voucher_type""",
        params, as_dict=True)
    breakdown = {r["voucher_type"]: flt(r["net"], 2) for r in rows if abs(flt(r["net"])) >= 0.005}
    return flt(sum(breakdown.values()), 2), breakdown


def _non_invoice_vouchers(company, accounts, opposite_accounts, from_date, to_date, debit_positive):
    """Per-voucher rows behind the non-invoice VAT figure — for box drill-down."""
    if not accounts:
        return []
    params = {
        "c": company, "a": tuple(accounts), "f": from_date, "t": to_date,
        "inv": _INVOICE_VOUCHERS,
    }
    settlement_clause = ""
    if opposite_accounts:
        params["opp"] = tuple(opposite_accounts)
        settlement_clause = """
             AND NOT EXISTS (
                 SELECT 1 FROM `tabGL Entry` x
                 WHERE x.voucher_type = gle.voucher_type
                   AND x.voucher_no = gle.voucher_no
                   AND x.is_cancelled = 0
                   AND x.account IN %(opp)s)"""
    expr = "SUM(gle.debit - gle.credit)" if debit_positive else "SUM(gle.credit - gle.debit)"
    rows = frappe.db.sql(
        f"""SELECT gle.voucher_type, gle.voucher_no, MIN(gle.posting_date) AS posting_date,
                   MAX(gle.party) AS party, COALESCE({expr}, 0) AS vat
            FROM `tabGL Entry` gle
            WHERE gle.company = %(c)s AND gle.is_cancelled = 0
              AND IFNULL(gle.is_opening, 'No') != 'Yes'
              AND gle.account IN %(a)s
              AND gle.posting_date BETWEEN %(f)s AND %(t)s
              AND gle.voucher_type NOT IN %(inv)s{settlement_clause}
            GROUP BY gle.voucher_type, gle.voucher_no
            HAVING ABS(COALESCE({expr}, 0)) >= 0.005
            ORDER BY posting_date ASC""",
        params, as_dict=True)
    rate = STANDARD_RATE / 100.0
    out = []
    for r in rows:
        vat = flt(r["vat"], 2)
        out.append({
            "name": r["voucher_no"],
            "doctype": r["voucher_type"],
            "posting_date": str(r["posting_date"]),
            "supplier": r.get("party") or "",
            "customer": r.get("party") or "",
            "tax_category": "Non-invoice (GL)",
            "base_net_total": flt(vat / rate, 2),
            "base_total_taxes_and_charges": vat,
        })
    return out


def _classify(category, rules, default_box):
    name = (category or "").lower()
    for pattern, box in rules:
        if re.search(pattern, name):
            return box
    return default_box


def _is_foreign(party_country):
    if not party_country:
        return False
    return party_country.strip().lower() not in ("saudi arabia", "ksa", "السعودية", "المملكة العربية السعودية")


def _customer_country(customer, cache):
    """Resolve the customer's COUNTRY from their address (not the sales territory).

    Territory is a sales-hierarchy label and is unreliable for export determination;
    the place of supply is driven by the customer's address country. Results are
    cached per request to avoid one query per invoice."""
    if not customer:
        return None
    if customer in cache:
        return cache[customer]
    country = None
    addr = frappe.db.get_value("Customer", customer, "customer_primary_address")
    if addr:
        country = frappe.db.get_value("Address", addr, "country")
    if not country:
        link = frappe.get_all(
            "Dynamic Link",
            filters={"link_doctype": "Customer", "link_name": customer, "parenttype": "Address"},
            fields=["parent"], limit=1)
        if link:
            country = frappe.db.get_value("Address", link[0]["parent"], "country")
    cache[customer] = country
    return country


def _classify_sales(si, country_cache):
    """Return the output box (box1..box5) for one Sales Invoice.

    Rule of thumb: any non-zero output VAT ⇒ standard-rated (box1), ALWAYS — exports,
    zero-rated and exempt supplies all carry 0% VAT by definition, so a category
    label that says 'export' on a 15% invoice is a data error, not an export.
    The magnitude (abs) is used so credit notes / returns — which carry NEGATIVE VAT —
    net back into the same box as the original standard-rated supply (box1) rather than
    leaking into zero-rated/export. Only genuinely 0% sales are split by category /
    customer country."""
    vat = flt(si.get("base_total_taxes_and_charges"))
    if abs(vat) > 0.005:
        return "box1"
    cat = si.get("tax_category")
    if cat:
        # 0% sale with an explicit category: export / exempt / zero / healthcare-education.
        return _classify(cat, _SALES_CATEGORY_RULES, "box3")
    # 0% sale, no category: export only when the customer's country is clearly outside
    # KSA. Unknown/blank country → zero-rated domestic (box3), the conservative default,
    # because zero-rating an export requires documentary proof of export.
    country = _customer_country(si.get("customer"), country_cache)
    return "box4" if _is_foreign(country) else "box3"


def _classify_purchase(pi):
    """Return the input box (box7..box11) for one Purchase Invoice.

    Imports (customs-paid) and reverse-charge purchases legitimately carry VAT but
    belong in box8/box9, so category rules run regardless of the VAT amount. The
    magnitude (abs) is used so debit notes / returns net back into the standard-rated
    box (box7) instead of zero-rated."""
    cat = pi.get("tax_category")
    vat = flt(pi.get("base_total_taxes_and_charges"))
    default = "box7" if abs(vat) > 0.005 else "box10"
    if cat:
        return _classify(cat, _PURCHASE_CATEGORY_RULES, default)
    return default


def _government_customers(company, as_of):
    """Customers whose standard-rated sales belong in the government box.

    Resolved from the GTPL rule in force at the PERIOD END, so a return re-run
    years later splits its boxes the way it was filed. Returns (customers, rule);
    no rule means no government line is emitted at all, rather than an empty box
    appearing on the return of every company that has never heard of GTPL.

    Imported lazily because gtpl reaches back into this module for the period
    adjustments. A module-level import in both directions is a cycle waiting for
    someone to reorder an import statement.
    """
    try:
        from .gtpl import _scope, active_rule
    except Exception:
        return set(), None
    rule = active_rule(company, as_of)
    if not rule:
        return set(), None
    customers, _groups, _forced_out = _scope(rule)
    return customers, rule


def _sales_breakdown(company, from_date, to_date, government=None, split=True):
    """Accumulate sales taxable base + VAT into output boxes.

    Returns/credit notes (is_return=1) are routed into each box's `adjustment`
    bucket — per the ZATCA 3-column layout (Amount / Adjustment / VAT) credit
    notes are adjustments to output tax, not negative supplies. Their VAT still
    nets into the box VAT so the box figure is the true net.

    Standard-rated supplies to government entities split off into `box1_2`. ONLY
    box1 reroutes: the ZATCA line is standard-rated government sales, so a
    zero-rated or exported supply to a ministry stays in its own box.

    WHICH invoices fall in this period is decided upstream by the period
    adjustments; this function only decides which box they land in. Those two
    concerns stay apart deliberately — the deferral engine can be changed without
    touching the box split, and vice versa."""
    government = government or set()
    boxes = {b: {"amount": 0.0, "adjustment": 0.0, "vat": 0.0}
             for b in ("box1", "box1_2", "box2", "box3", "box4", "box5")}
    country_cache: dict = {}
    _si_fields = ["name", "base_net_total", "base_total_taxes_and_charges", "tax_category",
                  "customer", "is_return"]
    invoices = frappe.get_all(
        "Sales Invoice",
        filters={"company": company, "docstatus": 1, "posting_date": ["between", [from_date, to_date]]},
        fields=_si_fields)
    invoices, _ = _apply_adjustments(invoices, "Sales Invoice", company, from_date, to_date, _si_fields)
    for si in invoices:
        box = sales_box(_classify_sales(si, country_cache), si.get("customer"), government, split)
        net = flt(si["base_net_total"])
        if si.get("is_return"):
            boxes[box]["adjustment"] += net
        else:
            boxes[box]["amount"] += net
        boxes[box]["vat"] += flt(si["base_total_taxes_and_charges"])
    return boxes


def _purchase_breakdown(company, from_date, to_date):
    boxes = {b: {"amount": 0.0, "adjustment": 0.0, "vat": 0.0}
             for b in ("box7", "box8", "box9", "box10", "box11")}
    _pi_fields = ["name", "base_net_total", "base_total_taxes_and_charges", "tax_category",
                  "supplier", "is_return"]
    invoices = frappe.get_all(
        "Purchase Invoice",
        filters={"company": company, "docstatus": 1, "posting_date": ["between", [from_date, to_date]]},
        fields=_pi_fields)
    invoices, _ = _apply_adjustments(invoices, "Purchase Invoice", company, from_date, to_date, _pi_fields)
    for pi in invoices:
        box = _classify_purchase(pi)
        net = flt(pi["base_net_total"])
        if pi.get("is_return"):
            boxes[box]["adjustment"] += net
        else:
            boxes[box]["amount"] += net
        boxes[box]["vat"] += flt(pi["base_total_taxes_and_charges"])
    return boxes


@frappe.whitelist()
def vat_return(company=None, from_date=None, to_date=None):
    """Assemble the ZATCA 16-box VAT return for the period."""
    company = company or _default_company()
    if not company:
        frappe.throw("No company found.")
    if not from_date or not to_date:
        frappe.throw("from_date and to_date are required.")
    if not frappe.has_permission("GL Entry", "read"):
        frappe.throw("Not permitted.")
    getdate(from_date); getdate(to_date)

    out_accts, in_accts, clearing_accts = _vat_accounts(company)

    government, gtpl_rule = _government_customers(company, to_date)
    gov_split = bool((gtpl_rule or {}).get("target_box") or "")
    sales = _sales_breakdown(company, from_date, to_date, government, gov_split)
    purch = _purchase_breakdown(company, from_date, to_date)

    # Non-invoice VAT (Payment Entries, Journal Entries, custom Expenses Entry apps,
    # …) is CLUBBED INTO the same lines as invoices — box 7 for input, box 1 for
    # output — instead of a separate 'other' line, with the taxable base derived
    # from the VAT at the standard rate so Amount and VAT stay consistent.
    #
    # opposite_accounts includes clearing_accts alongside the literal other
    # VAT side — a voucher touching Output VAT plus a recognized VAT-
    # clearing/reconciliation account is excluded here exactly like one
    # touching Output VAT plus Input VAT directly; see _VAT_CLEARING for the
    # real ledger case (a quarter-end close done as two separate JEs, one per
    # VAT side, each only ever touching ONE VAT account plus the clearing
    # account — invisible to the input+output pairing check alone).
    rate = STANDARD_RATE / 100.0
    other_input, input_sources = _non_invoice_vat(
        company, in_accts, out_accts + clearing_accts, from_date, to_date, debit_positive=True)
    other_output, output_sources = _non_invoice_vat(
        company, out_accts, in_accts + clearing_accts, from_date, to_date, debit_positive=False)
    if abs(other_input) >= 0.01:
        purch["box7"]["vat"] += other_input
        purch["box7"]["amount"] += other_input / rate
    if abs(other_output) >= 0.01:
        sales["box1"]["vat"] += other_output
        sales["box1"]["amount"] += other_output / rate

    def line(num, key, label, src, editable_amount=False, zero_vat=False):
        d = src.get(key, {"amount": 0.0, "adjustment": 0.0, "vat": 0.0})
        return {
            "box": num, "label": label,
            "amount": round(d["amount"], 2),
            "adjustment": round(d.get("adjustment", 0.0), 2),
            "vat": 0.0 if zero_vat else round(d["vat"], 2),
            "zero_vat": zero_vat,
        }

    # The government line is emitted ONLY when a GTPL rule is in force. Without
    # one the return keeps its previous shape exactly, so nothing changes for a
    # company that has no government supplies. Its box NUMBER comes from the rule
    # rather than a constant — ZATCA has renumbered this return before, and the
    # number that was correct when a quarter was filed is the number it should
    # still show when re-run.
    sales_lines = [
        line(1, "box1", "Standard rated sales", sales),
        line(2, "box2", "Private healthcare / education to citizens", sales, zero_vat=True),
        line(3, "box3", "Zero-rated domestic sales", sales, zero_vat=True),
        line(4, "box4", "Exports", sales, zero_vat=True),
        line(5, "box5", "Exempt sales", sales, zero_vat=True),
    ]
    # A rule with no target box defers WITHOUT splitting the line: released
    # supplies join ordinary standard-rated sales in box 1. Some filers disclose
    # the split and some do not — the deferral is the tax treatment, the box is
    # only presentation, and forcing a 1.2 line onto a filer who does not use one
    # produces a return that does not match what they submit.
    if gtpl_rule and (gtpl_rule.get("target_box") or "").strip():
        sales_lines.insert(1, line(gtpl_rule["target_box"], "box1_2",
                                   "Standard rated sales to government entities", sales))
    purchase_lines = [
        line(7, "box7", "Standard rated domestic purchases", purch),
        line(8, "box8", "Imports subject to VAT (paid at customs)", purch),
        line(9, "box9", "Imports subject to VAT (reverse charge)", purch),
        line(10, "box10", "Zero-rated purchases", purch, zero_vat=True),
        line(11, "box11", "Exempt purchases", purch, zero_vat=True),
    ]

    # Totals are the SUM OF THE LINES — the breakdown always reconciles to box 6/12
    # by construction. (The previous 'authoritative GL total' was corrupted by
    # quarter-end settlement JEs, which once drove box 12 to ~0 and produced a large
    # negative 'other input VAT' line that cancelled box 7.)
    sales_amount = round(sum(l["amount"] for l in sales_lines), 2)
    sales_adjustment = round(sum(l["adjustment"] for l in sales_lines), 2)
    sales_vat = round(sum(l["vat"] for l in sales_lines), 2)
    purch_amount = round(sum(l["amount"] for l in purchase_lines), 2)
    purch_adjustment = round(sum(l["adjustment"] for l in purchase_lines), 2)
    purch_vat = round(sum(l["vat"] for l in purchase_lines), 2)

    box6 = {"box": 6, "label": "Total sales", "amount": sales_amount, "adjustment": sales_adjustment, "vat": round(sales_vat, 2), "system": True}
    box12 = {"box": 12, "label": "Total purchases", "amount": purch_amount, "adjustment": purch_adjustment, "vat": round(purch_vat, 2), "system": True}

    box13 = round(sales_vat - purch_vat, 2)
    net = {
        "box13": box13,            # total VAT due this period
        "box14": 0.0,              # corrections (user-entered)
        "box15": 0.0,              # credit carried forward (user-entered)
        "box16": box13,            # net VAT due / (reclaimable)
    }

    return {
        "company": company,
        "tax_id": frappe.db.get_value("Company", company, "tax_id"),
        "period": {"from_date": from_date, "to_date": to_date},
        "currency": frappe.db.get_value("Company", company, "default_currency") or "SAR",
        "standard_rate": STANDARD_RATE,
        "accounts": {"output_vat": out_accts, "input_vat": in_accts},
        "gtpl": ({"rule": gtpl_rule["name"], "box": gtpl_rule.get("target_box"),
                  "basis": gtpl_rule.get("trigger_basis"),
                  "customers": len(government)} if gtpl_rule else None),
        "sales_lines": sales_lines,
        "box6": box6,
        "purchase_lines": purchase_lines,
        "box12": box12,
        "net": net,
        # Non-invoice VAT that was clubbed into box 1 / box 7, split by document type
        # (e.g. {'Expenses Entry': 259.99, 'Payment Entry': 1200.0}) — shown in the UI
        # so the preparer can see exactly what beyond invoices is inside those boxes.
        "adjustments": _adjustment_summary(company, from_date, to_date),
        "non_invoice": {
            "input": {"total": other_input, "sources": input_sources},
            "output": {"total": other_output, "sources": output_sources},
        },
        "notes": [
            "Box 7 includes input VAT posted straight to the GL VAT accounts by non-invoice documents (Payment Entries, Journal Entries, Expenses Entries); its taxable base is derived from the VAT at 15%, so verify the net amounts against source documents.",
            "Quarter-end VAT settlement Journal Entries (vouchers touching both the input and output VAT accounts) are excluded — they move VAT between control accounts and are not input or output tax.",
            "If a Payment Entry carries VAT on an advance that is later invoiced, ensure the VAT is not posted twice (once on the payment and again on the Purchase Invoice).",
            "Sales/purchase categories are inferred from each invoice's Tax Category, or the VAT rate when none is set — review the split before filing.",
            "Boxes 14 (corrections) and 15 (credit carried forward) are entered manually.",
            "Standard-rated sales to government entities are split into their own box under the active GTPL rule. Which invoices fall in this period is governed by the period adjustments — run the deferral register to see what was carried forward and why.",
        ],
    }


@frappe.whitelist()
def vat_box_drill(company=None, from_date=None, to_date=None, box=None):
    """Return the invoices behind a specific sales/purchase box for verification.

    Uses the SAME per-invoice classifier as the summary totals, then filters to the
    requested box, so the drill list reconciles with the box figure. Boxes 6 and 12
    are the period totals and return every sales / purchase invoice respectively."""
    _require_read()
    company = company or _default_company()
    if not (company and from_date and to_date and box):
        frappe.throw("company, from_date, to_date and box are required.")
    box = str(box)
    government, gtpl_rule = _government_customers(company, to_date)
    gov_box = str((gtpl_rule or {}).get("target_box") or "")
    gov_split = bool(gov_box)
    is_sales = box in ("1", "2", "3", "4", "5", "6") or (gov_split and box == gov_box)
    doctype = "Sales Invoice" if is_sales else "Purchase Invoice"
    party_field = "customer" if is_sales else "supplier"
    fields = ["name", "posting_date", party_field, "base_net_total",
              "base_total_taxes_and_charges", "tax_category", "is_return"]
    rows = frappe.get_all(
        doctype,
        filters={"company": company, "docstatus": 1, "posting_date": ["between", [from_date, to_date]]},
        fields=fields,
        order_by="posting_date asc", limit_page_length=0)

    # Box 6 / 12 are system totals → no per-box filtering.
    if box in ("6", "12"):
        return {"doctype": doctype, "rows": rows}

    # v2.32.0 — the drill obeys the period adjustments exactly like the boxes:
    # excluded invoices leave the main list and are returned separately so the
    # UI can show them under their own heading; included out-of-period ones
    # join the list flagged _adj='in'.
    dt_for_adj = "Sales Invoice" if is_sales else "Purchase Invoice"
    rows, removed = _apply_adjustments(rows, dt_for_adj, company, from_date, to_date, fields)
    # The government box carries a rule-defined NUMBER ("1.2") but a fixed
    # internal KEY, so the drill cannot simply prefix the number with "box".
    target = "box1_2" if (gov_split and box == gov_box) else "box" + box
    country_cache: dict = {}

    def _sales_box(r):
        return sales_box(_classify_sales(r, country_cache), r.get("customer"), government, gov_split)

    if is_sales:
        filtered = [r for r in rows if _sales_box(r) == target]
        excluded = [r for r in removed if _sales_box(r) == target]
    else:
        filtered = [r for r in rows if _classify_purchase(r) == target]
        excluded = [r for r in removed if _classify_purchase(r) == target]

    # Boxes 1 and 7 also carry non-invoice VAT (clubbed in by vat_return) — append
    # those vouchers, each tagged with its own doctype, so the drill reconciles
    # with the box figure.
    if box in ("1", "7"):
        out_accts, in_accts, clearing_accts = _vat_accounts(company)
        if box == "7":
            filtered += _non_invoice_vouchers(
                company, in_accts, out_accts + clearing_accts, from_date, to_date, debit_positive=True)
        else:
            filtered += _non_invoice_vouchers(
                company, out_accts, in_accts + clearing_accts, from_date, to_date, debit_positive=False)
    return {"doctype": doctype, "rows": filtered, "excluded": excluded}


# ────────────────────────────────────────────────────────────────────────────
# Period adjustments (v2.29.0) — include an out-of-period invoice in this
# return (e.g. a government invoice whose VAT falls due when PAID) or exclude
# an in-period invoice (unpaid, deferred to the payment quarter). Every
# adjustment carries a mandatory reason and is stored per return period —
# the audit trail the accountant's coloured Excel rows were approximating.
# ────────────────────────────────────────────────────────────────────────────

def _period_adjustments(company, from_date, to_date, voucher_type):
    rows = frappe.get_all("Insight VAT Adjustment",
                          filters={"company": company, "from_date": from_date,
                                   "to_date": to_date, "voucher_type": voucher_type},
                          fields=["voucher_no", "action", "reason"], limit_page_length=0)
    include = {r["voucher_no"]: r["reason"] for r in rows if r["action"] == "Include"}
    exclude = {r["voucher_no"]: r["reason"] for r in rows if r["action"] == "Exclude"}
    return include, exclude


def _apply_adjustments(invoices, doctype, company, from_date, to_date, fields):
    """Drop excluded invoices; append included out-of-period ones. Each
    appended row is tagged _adj='in'; excluded rows are returned separately
    so registers can show them (red) without counting them."""
    include, exclude = _period_adjustments(company, from_date, to_date, doctype)
    kept, removed = [], []
    for inv in invoices:
        if inv["name"] in exclude:
            inv["_adj"] = "out"
            inv["_adj_reason"] = exclude[inv["name"]]
            removed.append(inv)
        else:
            kept.append(inv)
    extra_names = [n for n in include if n not in {i["name"] for i in invoices}]
    if extra_names:
        extra = frappe.get_all(doctype, filters={"name": ["in", extra_names], "docstatus": 1},
                               fields=fields, limit_page_length=0)
        for inv in extra:
            inv["_adj"] = "in"
            inv["_adj_reason"] = include[inv["name"]]
            kept.append(inv)
    return kept, removed


@frappe.whitelist()
def list_vat_adjustments(company=None, from_date=None, to_date=None):
    _require_read()
    company = company or _default_company()
    return frappe.get_all("Insight VAT Adjustment",
                          filters={"company": company, "from_date": from_date, "to_date": to_date},
                          fields=["name", "voucher_type", "voucher_no", "action", "reason",
                                  "owner", "creation"],
                          order_by="creation desc", limit_page_length=200)


@frappe.whitelist()
def save_vat_adjustment(company=None, from_date=None, to_date=None,
                        voucher_type=None, voucher_no=None, action=None, reason=None):
    if not frappe.has_permission("Insight VAT Adjustment", "write"):
        frappe.throw(_("Not permitted."))
    company = company or _default_company()
    if voucher_type not in ("Sales Invoice", "Purchase Invoice"):
        frappe.throw(_("Voucher type must be Sales Invoice or Purchase Invoice."))
    if not frappe.db.exists(voucher_type, {"name": voucher_no, "company": company, "docstatus": 1}):
        frappe.throw(_("{0} {1} not found (must be submitted, same company).").format(_(voucher_type), voucher_no))
    if not (reason or "").strip():
        frappe.throw(_("A reason is required — it is the audit trail."))
    posting = frappe.db.get_value(voucher_type, voucher_no, "posting_date")
    in_period = str(from_date) <= str(posting) <= str(to_date)
    if action == "Exclude" and not in_period:
        frappe.throw(_("{0} is dated {1} — outside this period, nothing to exclude.").format(voucher_no, posting))
    if action == "Include" and in_period:
        frappe.throw(_("{0} is dated {1} — already inside this period.").format(voucher_no, posting))
    existing = frappe.get_all("Insight VAT Adjustment",
                              filters={"company": company, "from_date": from_date, "to_date": to_date,
                                       "voucher_type": voucher_type, "voucher_no": voucher_no},
                              pluck="name")
    for n in existing:
        frappe.delete_doc("Insight VAT Adjustment", n, ignore_permissions=True)
    frappe.get_doc({"doctype": "Insight VAT Adjustment", "company": company,
                    "from_date": from_date, "to_date": to_date,
                    "voucher_type": voucher_type, "voucher_no": voucher_no,
                    "action": action, "reason": reason.strip()}).insert(ignore_permissions=True)
    frappe.db.commit()
    return {"ok": True}


@frappe.whitelist()
def delete_vat_adjustment(name=None):
    if not frappe.has_permission("Insight VAT Adjustment", "delete"):
        frappe.throw(_("Not permitted."))
    if name and frappe.db.exists("Insight VAT Adjustment", name):
        frappe.delete_doc("Insight VAT Adjustment", name)
        frappe.db.commit()
    return {"ok": True}


@frappe.whitelist()
def find_vouchers(company=None, voucher_type=None, query=None):
    """Invoice search for the adjustments panel."""
    _require_read()
    company = company or _default_company()
    if voucher_type not in ("Sales Invoice", "Purchase Invoice"):
        return []
    if not _can_read_doc(voucher_type):
        return []
    party = "customer_name" if voucher_type == "Sales Invoice" else "supplier_name"
    return frappe.get_all(voucher_type,
                          filters={"company": company, "docstatus": 1,
                                   "name": ["like", f"%{(query or '').strip()}%"]},
                          fields=["name", "posting_date", party + " as party",
                                  "base_net_total", "base_total_taxes_and_charges"],
                          order_by="posting_date desc", limit_page_length=12)


def _can_read_doc(dt):
    try:
        return frappe.has_permission(dt, "read")
    except Exception:
        return False


def _adjustment_summary(company, from_date, to_date):
    """Included / excluded vouchers with their VAT figures — for the return
    header chip and the pack reconciliation block."""
    out = {"included": [], "excluded": [], "net_vat_effect": 0.0}
    for vtype in ("Sales Invoice", "Purchase Invoice"):
        include, exclude = _period_adjustments(company, from_date, to_date, vtype)
        for names, kind in ((include, "included"), (exclude, "excluded")):
            if not names:
                continue
            rows = frappe.get_all(vtype, filters={"name": ["in", list(names)], "docstatus": 1},
                                  fields=["name", "posting_date", "base_net_total",
                                          "base_total_taxes_and_charges"], limit_page_length=0)
            for r in rows:
                vat = flt(r["base_total_taxes_and_charges"], 2)
                sign = 1 if vtype == "Sales Invoice" else -1  # effect on net VAT due
                eff = vat * sign * (1 if kind == "included" else -1)
                out[kind].append({"voucher_type": vtype, "voucher_no": r["name"],
                                  "posting_date": str(r["posting_date"]),
                                  "net": flt(r["base_net_total"], 2), "vat": vat,
                                  "reason": names[r["name"]]})
                out["net_vat_effect"] += eff
    out["net_vat_effect"] = flt(out["net_vat_effect"], 2)
    return out


@frappe.whitelist()
def clear_vat_adjustment(company=None, from_date=None, to_date=None,
                         voucher_type=None, voucher_no=None):
    """Remove the adjustment on a voucher for this period — the drill's
    checkbox 're-include' path."""
    if not frappe.has_permission("Insight VAT Adjustment", "delete"):
        frappe.throw(_("Not permitted."))
    company = company or _default_company()
    for n in frappe.get_all("Insight VAT Adjustment",
                            filters={"company": company, "from_date": from_date,
                                     "to_date": to_date, "voucher_type": voucher_type,
                                     "voucher_no": voucher_no}, pluck="name"):
        frappe.delete_doc("Insight VAT Adjustment", n, ignore_permissions=True)
    frappe.db.commit()
    return {"ok": True}
