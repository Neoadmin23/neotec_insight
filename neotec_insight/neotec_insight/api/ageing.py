# Copyright (c) 2026, Neotec Integrated Solution
# AR / AP Ageing (v2.30.0) — GL-based with FIFO open-item allocation.
#
# Why GL and not invoice.outstanding_amount: the client's real ledgers carry
# unallocated payments and advances (party rows with no invoice at all), and
# an as-of date must reconstruct history. So per party we walk the Receivable/
# Payable GL rows up to the as-of date in date order: invoicing rows open
# items, payment rows knock the OLDEST open items off first (FIFO), and
# whatever remains — open invoices aged by their due/posting date, or a
# leftover advance as a negative bucket — is distributed into the slabs.
#
# Slabs are user-defined boundaries (e.g. 30,60,90,120 days → 0–30, 31–60,
# 61–90, 91–120, 121+) in DAYS or CALENDAR MONTHS.
from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import flt, getdate

from .health import _default_company


def _parse_slabs(raw):
    try:
        vals = sorted({int(x) for x in str(raw or "").replace("،", ",").split(",") if str(x).strip()})
        vals = [v for v in vals if v > 0][:8]
        return vals or [30, 60, 90, 120]
    except Exception:
        return [30, 60, 90, 120]


def _slab_labels(bounds, mode):
    unit_en = "Days" if mode == "days" else "Months"
    unit_ar = "يوم" if mode == "days" else "شهر"
    labels = []
    prev = 0
    for b in bounds:
        labels.append({"key": f"b{b}", "en": f"{prev}\u2013{b} {unit_en}", "ar": f"{prev}\u2013{b} {unit_ar}"})
        prev = b + 1
    labels.append({"key": "over", "en": f"{bounds[-1] + 1}+ {unit_en}", "ar": f"+{bounds[-1] + 1} {unit_ar}"})
    return labels


def _age(as_of, d, mode):
    if mode == "months":
        return (as_of.year - d.year) * 12 + (as_of.month - d.month) - (1 if as_of.day < d.day else 0)
    return (as_of - d).days


def _slab_index(age, bounds):
    for i, b in enumerate(bounds):
        if age <= b:
            return i
    return len(bounds)


@frappe.whitelist()
def ar_ap_ageing(company=None, as_of=None, party_type="Customer", based_on="due",
                 mode="days", slabs="30,60,90,120", top_n=0, exclude_parties=None,
                 allocation="actual", include_parties=None):
    company = company or _default_company()
    if not frappe.has_permission("GL Entry", "read"):
        frappe.throw(_("Not permitted."))
    as_of = getdate(as_of or frappe.utils.nowdate())
    party_type = "Supplier" if party_type == "Supplier" else "Customer"
    acc_type = "Receivable" if party_type == "Customer" else "Payable"
    mode = "months" if mode == "months" else "days"
    bounds = _parse_slabs(slabs)
    n_slabs = len(bounds) + 1

    rows = frappe.db.sql(
        """SELECT gle.party, gle.posting_date, gle.due_date,
                  gle.voucher_no, gle.against_voucher,
                  gle.debit, gle.credit
           FROM `tabGL Entry` gle
           JOIN `tabAccount` a ON a.name = gle.account
           WHERE gle.company = %(c)s AND gle.is_cancelled = 0
             AND a.account_type = %(t)s
             AND gle.party_type = %(pt)s AND IFNULL(gle.party, '') != ''
             AND gle.posting_date <= %(d)s
           ORDER BY gle.party, gle.posting_date, gle.creation""",
        {"c": company, "t": acc_type, "pt": party_type, "d": as_of}, as_dict=True)

    # For AR an invoice is a DEBIT and a payment a CREDIT; AP is the mirror.
    inv_key, pay_key = ("debit", "credit") if party_type == "Customer" else ("credit", "debit")

    allocation = "fifo" if allocation == "fifo" else "actual"
    parties = {}
    for r in rows:
        p = parties.setdefault(r["party"], {"invoiced": 0.0, "paid": 0.0, "open": [], "bills": {}})
        inv_amt = flt(r[inv_key])
        pay_amt = flt(r[pay_key])
        base_date = getdate(r["due_date"]) if (based_on == "due" and r.get("due_date")) else getdate(r["posting_date"])
        if inv_amt > 0.005:
            p["invoiced"] += inv_amt
            item = {"date": base_date, "remaining": inv_amt}
            p["open"].append(item)
            # actual mode addresses items by invoice number (v2.33.0)
            if r.get("voucher_no") in p["bills"]:
                p["bills"][r["voucher_no"]]["remaining"] += inv_amt
            else:
                p["bills"][r["voucher_no"]] = item
        if pay_amt > 0.005:
            p["paid"] += pay_amt
            against = r.get("against_voucher") or ""
            if allocation == "actual":
                # Follow the ledger: reduce exactly the referenced invoice;
                # no reference (or self-reference) → unallocated advance aged
                # by its own date. Identical logic to the Bill-wise view, so
                # summary and detail can never disagree.
                if against and against != r.get("voucher_no") and against in p["bills"]:
                    p["bills"][against]["remaining"] = flt(p["bills"][against]["remaining"] - pay_amt, 6)
                else:
                    p["open"].append({"date": getdate(r["posting_date"]), "remaining": -pay_amt})
            else:
                left = pay_amt
                for item in p["open"]:                  # FIFO knock-off
                    if left <= 0.005:
                        break
                    if item["remaining"] <= 0.005:
                        continue
                    take = min(item["remaining"], left)
                    item["remaining"] -= take
                    left -= take
                if left > 0.005:                         # unallocated → advance
                    p["open"].append({"date": getdate(r["posting_date"]), "remaining": -left})

    party_names = {}
    if parties:
        label_field = "customer_name" if party_type == "Customer" else "supplier_name"
        for x in frappe.get_all(party_type, filters={"name": ["in", list(parties)]},
                                fields=["name", label_field], limit_page_length=0):
            party_names[x["name"]] = x.get(label_field) or x["name"]

    # v2.32.0 — parties under dispute / legal hold can be kept OFF the report.
    import json as _json
    if isinstance(exclude_parties, str):
        try:
            exclude_parties = _json.loads(exclude_parties or "[]")
        except Exception:
            exclude_parties = []
    excl = set(exclude_parties or [])
    if isinstance(include_parties, str):
        try:
            include_parties = _json.loads(include_parties or "[]")
        except Exception:
            include_parties = []
    incl = set(include_parties or [])   # empty = all parties

    out_rows = []
    for party, p in parties.items():
        if party in excl or (incl and party not in incl):
            continue
        buckets = [0.0] * n_slabs
        for item in p["open"]:
            if abs(item["remaining"]) < 0.005:
                continue
            buckets[_slab_index(_age(as_of, item["date"], mode), bounds)] += item["remaining"]
        outstanding = flt(p["invoiced"] - p["paid"], 2)
        if abs(outstanding) < 0.01 and all(abs(b) < 0.01 for b in buckets):
            continue
        out_rows.append({"party": party, "label": party_names.get(party, party),
                         "invoiced": flt(p["invoiced"], 2), "paid": flt(p["paid"], 2),
                         "outstanding": outstanding,
                         "buckets": [flt(b, 2) for b in buckets]})
    out_rows.sort(key=lambda r: -r["outstanding"])

    top_n = int(top_n or 0)
    others = None
    if top_n > 0 and len(out_rows) > top_n:
        rest = out_rows[top_n:]
        others = {"party": "", "label": _("Others ({0} parties)").format(len(rest)),
                  "invoiced": flt(sum(r["invoiced"] for r in rest), 2),
                  "paid": flt(sum(r["paid"] for r in rest), 2),
                  "outstanding": flt(sum(r["outstanding"] for r in rest), 2),
                  "buckets": [flt(sum(r["buckets"][i] for r in rest), 2) for i in range(n_slabs)]}
        out_rows = out_rows[:top_n]

    shown = out_rows + ([others] if others else [])
    total = {"invoiced": flt(sum(r["invoiced"] for r in shown), 2),
             "paid": flt(sum(r["paid"] for r in shown), 2),
             "outstanding": flt(sum(r["outstanding"] for r in shown), 2),
             "buckets": [flt(sum(r["buckets"][i] for r in shown), 2) for i in range(n_slabs)]}

    return {
        "company": company, "as_of": str(as_of), "party_type": party_type,
        "based_on": based_on, "mode": mode, "bounds": bounds, "allocation": allocation,
        "labels": _slab_labels(bounds, mode),
        "currency": frappe.db.get_value("Company", company, "default_currency") or "SAR",
        "rows": out_rows, "others": others, "total": total,
        "excluded_parties": sorted(excl),
        "notes": [
            ("GL-based with ACTUAL allocation: each payment settles exactly the invoice it was applied to (Payment Entry references); payments not applied to any invoice appear as negative amounts aged by their own date."
             if allocation == "actual" else
             "GL-based with FIFO allocation: payments settle the oldest open items first; unallocated payments/advances appear as negative amounts aged by their own date."),
            "Ageing base: " + ("due date (falling back to posting date)" if based_on == "due" else "posting date") + ".",
        ],
    }


@frappe.whitelist()
def list_parties(company=None, party_type="Customer", query=""):
    """Party search for exclusions and the Bill-wise selection."""
    party_type = "Supplier" if party_type == "Supplier" else "Customer"
    if not frappe.has_permission(party_type, "read"):
        return []
    label = "customer_name" if party_type == "Customer" else "supplier_name"
    flt_ = {}
    if (query or "").strip():
        flt_[label] = ["like", f"%{query.strip()}%"]
    return frappe.get_all(party_type, filters=flt_, fields=["name", f"{label} as label"],
                          order_by=label, limit_page_length=30)


@frappe.whitelist()
def billwise(company=None, party_type="Customer", parties=None, as_of=None,
             based_on="due", mode="days", slabs="30,60,90,120"):
    """Bill-wise analysis (v2.32.0) — per-party document statement in the
    classic ledger-analysis layout: every invoice and payment row in date
    order, payments allocated FIFO with the AGAINST VOUCHER shown, per-bill
    remaining balance, running cumulative party balance, a party subtotal —
    and the still-open bills aged into the user's slabs. Multiple parties
    render stacked one below the other (same concept as the multi-ledger GL)."""
    import json as _json
    company = company or _default_company()
    if not frappe.has_permission("GL Entry", "read"):
        frappe.throw(_("Not permitted."))
    as_of = getdate(as_of or frappe.utils.nowdate())
    party_type = "Supplier" if party_type == "Supplier" else "Customer"
    acc_type = "Receivable" if party_type == "Customer" else "Payable"
    if isinstance(parties, str):
        parties = _json.loads(parties or "[]")
    parties = [p for p in (parties or []) if p][:20]
    if not parties:
        # v2.34.3 — no selection = the TOP 20 parties by absolute outstanding,
        # so the button always produces a useful report instead of a refusal.
        top = frappe.db.sql(
            """SELECT gle.party,
                      ABS(SUM(gle.debit - gle.credit)) AS outs
               FROM `tabGL Entry` gle
               JOIN `tabAccount` a ON a.name = gle.account
               WHERE gle.company = %(c)s AND gle.is_cancelled = 0
                 AND a.account_type = %(t)s AND gle.party_type = %(pt)s
                 AND IFNULL(gle.party, '') != '' AND gle.posting_date <= %(d)s
               GROUP BY gle.party
               HAVING ABS(SUM(gle.debit - gle.credit)) > 0.01
               ORDER BY outs DESC LIMIT 20""",
            {"c": company, "t": acc_type, "pt": party_type, "d": as_of}, as_dict=True)
        parties = [r["party"] for r in top]
    if not parties:
        frappe.throw(_("No parties with an outstanding balance as of this date."))
    mode = "months" if mode == "months" else "days"
    bounds = _parse_slabs(slabs)
    labels = _slab_labels(bounds, mode)

    rows = frappe.db.sql(
        """SELECT gle.party, gle.posting_date, gle.due_date, gle.voucher_type,
                  gle.voucher_no, gle.against_voucher_type, gle.against_voucher,
                  gle.debit, gle.credit
           FROM `tabGL Entry` gle
           JOIN `tabAccount` a ON a.name = gle.account
           WHERE gle.company = %(c)s AND gle.is_cancelled = 0
             AND a.account_type = %(t)s AND gle.party_type = %(pt)s
             AND gle.party IN %(p)s AND gle.posting_date <= %(d)s
           ORDER BY gle.party, gle.posting_date, gle.creation""",
        {"c": company, "t": acc_type, "pt": party_type, "p": tuple(parties),
         "d": as_of}, as_dict=True)

    inv_key, pay_key = ("debit", "credit") if party_type == "Customer" else ("credit", "debit")
    label_field = "customer_name" if party_type == "Customer" else "supplier_name"
    names = {x["name"]: x.get(label_field) or x["name"]
             for x in frappe.get_all(party_type, filters={"name": ["in", parties]},
                                     fields=["name", label_field], limit_page_length=0)}

    by_party = {}
    for r in rows:
        by_party.setdefault(r["party"], []).append(r)

    blocks = []
    for party in parties:
        entries = by_party.get(party, [])
        # v2.32.1 — ACTUAL allocation, not FIFO: a payment/credit-note row that
        # carries against_voucher was explicitly applied to that invoice (this
        # is exactly what ERPNext writes from Payment Entry references). Rows
        # WITHOUT an against_voucher were never applied to any invoice — they
        # are listed separately at the bottom of the party as UNALLOCATED
        # receipts (AR) / payments (AP).
        bills = {}       # voucher_no -> {"date","base_date","amount","remaining"}
        bill_order = []
        unallocated = []
        out = []
        cum = 0.0
        for r in entries:
            inv_amt = flt(r[inv_key])
            pay_amt = flt(r[pay_key])
            base_date = getdate(r["due_date"]) if (based_on == "due" and r.get("due_date")) else getdate(r["posting_date"])
            against = r.get("against_voucher") or ""
            self_ref = against == r["voucher_no"]

            if inv_amt > 0.005:
                cum = flt(cum + inv_amt, 2)
                if r["voucher_no"] in bills:
                    bills[r["voucher_no"]]["amount"] = flt(bills[r["voucher_no"]]["amount"] + inv_amt, 2)
                    bills[r["voucher_no"]]["remaining"] = flt(bills[r["voucher_no"]]["remaining"] + inv_amt, 2)
                else:
                    bills[r["voucher_no"]] = {"date": getdate(r["posting_date"]),
                                              "base_date": base_date,
                                              "amount": inv_amt, "remaining": inv_amt}
                    bill_order.append(r["voucher_no"])
                out.append({"posting_date": str(r["posting_date"]),
                            "voucher_type": r["voucher_type"], "voucher_no": r["voucher_no"],
                            "amount": inv_amt, "allocated": None, "against": "",
                            "bill_balance": bills[r["voucher_no"]]["remaining"],
                            "cumulative": cum})

            if pay_amt > 0.005:
                cum = flt(cum - pay_amt, 2)
                if against and not self_ref:
                    b = bills.get(against)
                    rem = None
                    if b:
                        b["remaining"] = flt(b["remaining"] - pay_amt, 2)
                        rem = b["remaining"]
                    out.append({"posting_date": str(r["posting_date"]),
                                "voucher_type": r["voucher_type"], "voucher_no": r["voucher_no"],
                                "amount": -pay_amt, "allocated": pay_amt, "against": against,
                                "bill_balance": rem, "cumulative": cum})
                else:
                    # Received/paid but applied to NOTHING — the client's ask:
                    # keep it out of the bill flow, list it separately below.
                    out.append({"posting_date": str(r["posting_date"]),
                                "voucher_type": r["voucher_type"], "voucher_no": r["voucher_no"],
                                "amount": -pay_amt, "allocated": None,
                                "against": _("(unallocated)"),
                                "bill_balance": None, "cumulative": cum})
                    unallocated.append({"posting_date": str(r["posting_date"]),
                                        "voucher_type": r["voucher_type"],
                                        "voucher_no": r["voucher_no"],
                                        "amount": flt(pay_amt, 2)})

        still_open = []
        buckets = [0.0] * (len(bounds) + 1)
        for vno in bill_order:
            b = bills[vno]
            if abs(b["remaining"]) < 0.005:
                continue
            age = _age(as_of, b["base_date"], mode)
            si = _slab_index(age, bounds)
            buckets[si] += b["remaining"]
            still_open.append({"bill": vno, "date": str(b["date"]),
                               "amount": flt(b["amount"], 2),
                               "remaining": flt(b["remaining"], 2),
                               "age": age, "slab": labels[si]["en"]})
        still_open.sort(key=lambda x: -abs(x["remaining"]))
        unalloc_total = flt(sum(u["amount"] for u in unallocated), 2)
        blocks.append({"party": party, "label": names.get(party, party),
                       "rows": out, "balance": flt(cum, 2),
                       "open_bills": still_open,
                       "unallocated": unallocated,
                       "unallocated_total": unalloc_total,
                       "buckets": [flt(x, 2) for x in buckets]})

    return {"company": company, "as_of": str(as_of), "party_type": party_type,
            "based_on": based_on, "mode": mode, "labels": labels,
            "currency": frappe.db.get_value("Company", company, "default_currency") or "SAR",
            "blocks": blocks}


@frappe.whitelist()
def party_tree(party_type="Customer"):
    """Customers/Suppliers grouped for the multi-select tree picker."""
    party_type = "Supplier" if party_type == "Supplier" else "Customer"
    if not frappe.has_permission(party_type, "read"):
        return []
    label = "customer_name" if party_type == "Customer" else "supplier_name"
    group_f = "customer_group" if party_type == "Customer" else "supplier_group"
    rows = frappe.get_all(party_type, fields=["name", f"{label} as label", f"{group_f} as grp"],
                          order_by=f"{group_f}, {label}", limit_page_length=2000)
    groups = {}
    for r in rows:
        groups.setdefault(r.get("grp") or _("Others"), []).append(
            {"name": r["name"], "label": r.get("label") or r["name"]})
    return [{"group": g, "parties": p} for g, p in groups.items()]
