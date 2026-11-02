"""Insight Controls — checks, runner and endpoints (v3.5.0).

Each check returns findings as dicts: key, title, reference/related document,
amount, details. The runner applies controls_core.plan() so every issue is one
Finding across runs. Checks use plain SQL (identical on Frappe v15 and v16).
"""
from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import add_days, flt, now_datetime, nowdate

from neotec_insight.neotec_insight.utils import controls_core as core

RULE, FIND = "Insight Control Rule", "Insight Control Finding"


def _since(rule) -> str:
    return str(add_days(nowdate(), -int(rule.get("lookback_days") or 90)))


# ── checks ───────────────────────────────────────────────────────────────────

def c_dup_purchase_invoice(company, rule):
    rows = frappe.db.sql(
        """select name, supplier, bill_no, posting_date, grand_total as amount from `tabPurchase Invoice`
           where company = %s and docstatus = 1 and is_return = 0 and posting_date >= %s""",
        (company, _since(rule)), as_dict=True)
    out, seen = [], set()
    for a, b in core.same_bill_no(rows):
        k = core.pair_key(rule["name"], a["name"], b["name"])
        seen.add(k)
        out.append({"key": k, "title": _("Same bill number {0} from {1}").format(b["bill_no"], b["supplier"]),
                    "reference_doctype": "Purchase Invoice", "reference_name": b["name"],
                    "related_doctype": "Purchase Invoice", "related_name": a["name"], "amount": b["amount"],
                    "details": _("{0} and {1} carry the same supplier bill number.").format(a["name"], b["name"])})
    for a, b in core.same_amount_within([r for r in rows if r["amount"]], "supplier", int(rule.get("threshold") or 3)):
        k = core.pair_key(rule["name"], a["name"], b["name"])
        if k in seen:
            continue
        out.append({"key": k, "title": _("Same amount from {0} within {1} days").format(b["supplier"], int(rule.get("threshold") or 3)),
                    "reference_doctype": "Purchase Invoice", "reference_name": b["name"],
                    "related_doctype": "Purchase Invoice", "related_name": a["name"], "amount": b["amount"],
                    "details": _("{0} ({1}) and {2} ({3}) — {4:,.2f} each.").format(
                        a["name"], a["posting_date"], b["name"], b["posting_date"], flt(b["amount"]))})
    return out


def c_dup_sales_invoice(company, rule):
    rows = frappe.db.sql(
        """select name, customer, posting_date, grand_total as amount from `tabSales Invoice`
           where company = %s and docstatus = 1 and is_return = 0 and posting_date >= %s and grand_total > 0""",
        (company, _since(rule)), as_dict=True)
    days = int(rule.get("threshold") or 2)
    return [{"key": core.pair_key(rule["name"], a["name"], b["name"]),
             "title": _("Same amount to {0} within {1} days").format(b["customer"], days),
             "reference_doctype": "Sales Invoice", "reference_name": b["name"],
             "related_doctype": "Sales Invoice", "related_name": a["name"], "amount": b["amount"],
             "details": _("{0} ({1}) and {2} ({3}) — {4:,.2f} each. Recurring billing can be legitimate.").format(
                 a["name"], a["posting_date"], b["name"], b["posting_date"], flt(b["amount"]))}
            for a, b in core.same_amount_within(rows, "customer", days)]


def c_negative_stock(company, rule):
    rows = frappe.db.sql(
        """select b.item_code, b.warehouse, b.actual_qty from `tabBin` b
           join `tabWarehouse` w on w.name = b.warehouse
           where w.company = %s and b.actual_qty < 0""", (company,), as_dict=True)
    return [{"key": f"{rule['name']}:{r['item_code']}:{r['warehouse']}",
             "title": _("{0} is negative in {1}").format(r["item_code"], r["warehouse"]),
             "reference_doctype": "Item", "reference_name": r["item_code"],
             "related_doctype": "Warehouse", "related_name": r["warehouse"], "amount": flt(r["actual_qty"]),
             "details": _("Quantity on hand: {0}.").format(flt(r["actual_qty"]))} for r in rows]


def c_round_cash_journal(company, rule):
    floor = flt(rule.get("threshold") or 50000)
    rows = frappe.db.sql(
        """select je.name, je.posting_date, jea.account, (jea.debit + jea.credit) as amount
           from `tabJournal Entry` je
           join `tabJournal Entry Account` jea on jea.parent = je.name
           join `tabAccount` a on a.name = jea.account
           where je.company = %s and je.docstatus = 1 and je.posting_date >= %s
             and a.account_type in ('Cash', 'Bank') and ifnull(je.cheque_no, '') = ''
             and (jea.debit + jea.credit) >= %s""", (company, _since(rule), floor), as_dict=True)
    out = []
    for r in rows:
        amt = flt(r["amount"])
        if amt % 1000 == 0:
            out.append({"key": f"{rule['name']}:{r['name']}:{r['account']}",
                        "title": _("{0:,.0f} through {1} without a reference").format(amt, r["account"]),
                        "reference_doctype": "Journal Entry", "reference_name": r["name"], "amount": amt,
                        "details": _("Posted {0}; no cheque or reference number.").format(r["posting_date"])})
    return out


def c_rate_outlier(company, rule):
    since = _since(rule)
    receipts = frappe.db.sql(
        """select sle.voucher_type, sle.voucher_no, sle.item_code, sle.warehouse, sle.incoming_rate, sle.posting_date
           from `tabStock Ledger Entry` sle
           where sle.company = %s and sle.is_cancelled = 0 and sle.actual_qty > 0 and sle.incoming_rate > 0
             and sle.posting_date >= %s and sle.voucher_type in ('Purchase Receipt', 'Purchase Invoice')""",
        (company, since), as_dict=True)
    if not receipts:
        return []
    hist = frappe.db.sql(
        """select item_code, warehouse, avg(incoming_rate) as avg_rate from `tabStock Ledger Entry`
           where company = %s and is_cancelled = 0 and actual_qty > 0 and incoming_rate > 0
             and posting_date < %s and posting_date >= %s
           group by item_code, warehouse""", (company, since, str(add_days(since, -90))), as_dict=True)
    averages = {(h["item_code"], h["warehouse"]): flt(h["avg_rate"]) for h in hist}
    return [{"key": f"{rule['name']}:{o['voucher_no']}:{o['item_code']}:{o['warehouse']}",
             "title": _("{0} received {1:+.0f}% off its usual rate").format(o["item_code"], o["diff_pct"]),
             "reference_doctype": o["voucher_type"], "reference_name": o["voucher_no"],
             "related_doctype": "Item", "related_name": o["item_code"], "amount": flt(o["incoming_rate"]),
             "details": _("Rate {0:,.4f} against a 90-day average of {1:,.4f} in {2}.").format(
                 flt(o["incoming_rate"]), o["average"], o["warehouse"])}
            for o in core.rate_outliers(receipts, averages, flt(rule.get("threshold") or 50))]


def c_dup_stock_entry(company, rule):
    rows = frappe.db.sql(
        """select name, stock_entry_type, posting_date, ifnull(from_warehouse, '') as fw,
                  ifnull(to_warehouse, '') as tw, total_outgoing_value, total_incoming_value
           from `tabStock Entry` where company = %s and docstatus = 1 and posting_date >= %s
           order by posting_date, name""", (company, _since(rule)), as_dict=True)
    groups: dict[tuple, list] = {}
    for r in rows:
        sig = (r["stock_entry_type"], str(r["posting_date"]), r["fw"], r["tw"],
               round(flt(r["total_outgoing_value"]), 2), round(flt(r["total_incoming_value"]), 2))
        groups.setdefault(sig, []).append(r)
    out = []
    for sig, g in groups.items():
        if len(g) < 2 or not (sig[4] or sig[5]):
            continue
        for later in g[1:]:
            out.append({"key": core.pair_key(rule["name"], g[0]["name"], later["name"]),
                        "title": _("{0} on {1} entered twice?").format(sig[0], sig[1]),
                        "reference_doctype": "Stock Entry", "reference_name": later["name"],
                        "related_doctype": "Stock Entry", "related_name": g[0]["name"],
                        "amount": sig[4] or sig[5],
                        "details": _("Same type, date, warehouses and value as {0}.").format(g[0]["name"])})
    return out


def c_unallocated_payment(company, rule):
    cutoff = str(add_days(nowdate(), -int(rule.get("threshold") or 30)))
    rows = frappe.db.sql(
        """select name, party_type, party, posting_date, unallocated_amount from `tabPayment Entry`
           where company = %s and docstatus = 1 and unallocated_amount > 0.005
             and posting_date <= %s and posting_date >= %s""",
        (company, cutoff, _since(rule)), as_dict=True)
    return [{"key": f"{rule['name']}:{r['name']}",
             "title": _("{0:,.2f} from {1} not allocated since {2}").format(flt(r["unallocated_amount"]), r["party"], r["posting_date"]),
             "reference_doctype": "Payment Entry", "reference_name": r["name"],
             "related_doctype": r["party_type"], "related_name": r["party"], "amount": flt(r["unallocated_amount"]),
             "details": _("Allocate it to the invoices it pays, or mark it as an advance.")} for r in rows]


CHECKS = {
    "DUP_PURCHASE_INVOICE": c_dup_purchase_invoice, "DUP_SALES_INVOICE": c_dup_sales_invoice,
    "NEGATIVE_STOCK": c_negative_stock, "ROUND_CASH_JOURNAL": c_round_cash_journal,
    "RATE_OUTLIER": c_rate_outlier, "DUP_STOCK_ENTRY": c_dup_stock_entry,
    "UNALLOCATED_PAYMENT": c_unallocated_payment,
}


# ── rules & runner ───────────────────────────────────────────────────────────

def seed_rules() -> None:
    """Create missing rules from the catalogue; never overwrite edited settings
    (the read-only texts are refreshed)."""
    for code, spec in core.CATALOGUE.items():
        if frappe.db.exists(RULE, code):
            frappe.db.set_value(RULE, code, {"rule_title": spec["title"], "threshold_label": spec["threshold_label"],
                                             "description": spec["description"]}, update_modified=False)
            continue
        frappe.get_doc({"doctype": RULE, "rule_code": code, "rule_title": spec["title"], "enabled": 1,
                        "severity": spec["severity"], "threshold": spec["threshold"],
                        "threshold_label": spec["threshold_label"], "lookback_days": spec["lookback_days"],
                        "description": spec["description"]}).insert(ignore_permissions=True)


def _doc_gone(doctype, name) -> bool:
    if not doctype or not name:
        return False
    try:
        ds = frappe.db.get_value(doctype, name, "docstatus")
    except Exception:
        return False
    return ds is None or ds == 2


def _apply(company, rule, findings) -> list[str]:
    """Store one rule's results for a company; returns names of new findings."""
    now = now_datetime()
    by_key = {f["key"]: f for f in findings}
    rows = frappe.get_all(FIND, filters={"rule": rule["name"], "company": company},
                          fields=["name", "finding_key", "status", "reference_doctype", "reference_name",
                                  "related_doctype", "related_name"], limit_page_length=0, order_by="creation asc")
    existing = {r["finding_key"]: r["status"] for r in rows}
    gone = {r["finding_key"] for r in rows if r["status"] in core.ACTIVE and r["finding_key"] not in by_key
            and (_doc_gone(r["reference_doctype"], r["reference_name"]) or _doc_gone(r["related_doctype"], r["related_name"]))}
    p = core.plan(existing, set(by_key), core.CATALOGUE.get(rule["name"], {}).get("self_closing", False), gone)
    names = {r["finding_key"]: r["name"] for r in rows}
    new = []
    for k in p["create"]:
        f = by_key[k]
        doc = frappe.get_doc({"doctype": FIND, "finding_key": k, "title": str(f["title"])[:140], "rule": rule["name"],
                              "severity": rule["severity"], "status": core.OPEN, "company": company,
                              "reference_doctype": f.get("reference_doctype"), "reference_name": f.get("reference_name"),
                              "related_doctype": f.get("related_doctype"), "related_name": f.get("related_name"),
                              "amount": f.get("amount"), "details": f.get("details"),
                              "first_seen": now, "last_seen": now}).insert(ignore_permissions=True)
        new.append(doc.name)
    for k in p["touch"]:
        frappe.db.set_value(FIND, names[k], "last_seen", now, update_modified=False)
    for k in p["reopen"]:
        frappe.db.set_value(FIND, names[k], {"status": core.OPEN, "last_seen": now, "resolved_by": None,
                                             "resolved_on": None})
    for k in p["close"]:
        frappe.db.set_value(FIND, names[k], {"status": core.FIXED, "resolved_by": "Insight Controls",
                                             "resolved_on": now})
    return new


def run_all(companies=None) -> dict:
    seed_rules()
    rules = frappe.get_all(RULE, filters={"enabled": 1}, fields=["name", "severity", "threshold", "lookback_days"],
                           order_by="name asc", limit_page_length=0)
    companies = companies or frappe.get_all("Company", pluck="name", order_by="name asc")
    summary = {"new": 0, "errors": [], "high_new": []}
    for company in companies:
        for rule in rules:
            check = CHECKS.get(rule["name"])
            if not check:
                continue
            try:
                new = _apply(company, rule, check(company, rule))
                summary["new"] += len(new)
                if rule["severity"] == "High":
                    summary["high_new"] += new
                frappe.db.commit()
            except Exception as e:
                frappe.db.rollback()
                summary["errors"].append(f"{rule['name']} / {company}: {str(e)[:200]}")
                frappe.log_error(title=f"Insight Controls: {rule['name']} failed")
    _notify(summary["high_new"])
    return summary


def _notify(names: list[str]) -> None:
    if not names:
        return
    try:
        users = {r["parent"] for r in frappe.get_all("Has Role", filters={"role": "Accounts Manager", "parenttype": "User"},
                                                      fields=["parent"], limit_page_length=0, order_by="creation asc")}
        for u in users:
            if u in ("Administrator", "Guest") or not frappe.db.get_value("User", u, "enabled"):
                continue
            frappe.get_doc({"doctype": "Notification Log", "for_user": u, "type": "Alert", "document_type": FIND,
                            "document_name": names[0],
                            "subject": _("Insight Controls: {0} new high-severity finding(s)").format(len(names))}
                           ).insert(ignore_permissions=True)
    except Exception:
        frappe.log_error(title="Insight Controls: notification failed")


def daily() -> None:
    run_all()


# ── endpoints ────────────────────────────────────────────────────────────────

def _require_manager():
    roles = set(frappe.get_roles())
    if not ({"System Manager", "Accounts Manager"} & roles) and frappe.session.user != "Administrator":
        frappe.throw(_("Only Accounts Managers can change controls."), frappe.PermissionError)


@frappe.whitelist(methods=["POST"])
def run_now(company=None):
    _require_manager()
    from neotec_insight.neotec_insight.utils.access import check_company
    if company:
        check_company(company)
    return run_all([company] if company else None)


@frappe.whitelist()
def findings(company=None, status="Active", severity=None, rule=None):
    from neotec_insight.neotec_insight.utils.access import check_company, require_finance_read
    require_finance_read()
    flt_ = {}
    if company:
        check_company(company)
        flt_["company"] = company
    if status == "Active":
        flt_["status"] = ["in", list(core.ACTIVE)]
    elif status:
        flt_["status"] = status
    if severity:
        flt_["severity"] = severity
    if rule:
        flt_["rule"] = rule
    rows = frappe.get_list(FIND, filters=flt_, fields=["name", "title", "rule", "severity", "status", "company",
                                                         "reference_doctype", "reference_name", "related_doctype",
                                                         "related_name", "amount", "details", "note", "first_seen",
                                                         "last_seen", "resolved_by"],
                           order_by="last_seen desc", limit_page_length=500)
    counts: dict[str, int] = {}
    for r in frappe.get_list(FIND, filters={k: v for k, v in flt_.items() if k != "severity"}, fields=["severity"],
                             limit_page_length=0, order_by="creation asc"):
        counts[r["severity"]] = counts.get(r["severity"], 0) + 1
    return {"findings": rows, "counts": counts,
            "rules": frappe.get_all(RULE, fields=["name", "rule_title", "enabled", "severity", "threshold",
                                                  "threshold_label", "lookback_days", "description"],
                                    order_by="name asc", limit_page_length=0)}


@frappe.whitelist(methods=["POST"])
def set_status(names=None, status=None, note=None):
    import json
    _require_manager()
    if status not in (core.OPEN, core.ACK, core.FIXED, core.DISMISSED):
        frappe.throw(_("Unknown status."))
    names = json.loads(names) if isinstance(names, str) else (names or [])
    for n in names[:500]:
        values = {"status": status}
        if note:
            values["note"] = str(note)[:1000]
        if status in (core.FIXED, core.DISMISSED):
            values.update({"resolved_by": frappe.session.user, "resolved_on": now_datetime()})
        else:
            values.update({"resolved_by": None, "resolved_on": None})
        frappe.db.set_value(FIND, n, values)
    return {"updated": len(names[:500])}


@frappe.whitelist(methods=["POST"])
def save_rule(name=None, enabled=None, severity=None, threshold=None, lookback_days=None):
    _require_manager()
    if not frappe.db.exists(RULE, name):
        frappe.throw(_("Unknown rule."))
    values = {}
    if enabled is not None:
        values["enabled"] = 1 if str(enabled) in ("1", "true", "True") else 0
    if severity in ("High", "Medium", "Low"):
        values["severity"] = severity
    if threshold not in (None, ""):
        values["threshold"] = flt(threshold)
    if lookback_days not in (None, ""):
        values["lookback_days"] = max(0, int(flt(lookback_days)))
    frappe.db.set_value(RULE, name, values)
    return {"ok": True}
