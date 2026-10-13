"""Allocation API (v2.58.0).

Three jobs: list the rules, load and save the driver grid, and run the
allocation report. The grid is the "optional tool to make the entry of head
count and the sales count" — deliberately a bulk upsert of one rule-year at a
time, because that is how the numbers actually arrive (a column of head counts
for a month, or a whole year pasted from the old workbook).
"""

from __future__ import annotations

import json
import re

import frappe
from frappe import _
from frappe.utils import flt

from neotec_insight.neotec_insight.utils.fiscal_year import calendar_month_for_fy_month
from neotec_insight.neotec_insight.utils.allocation import (
    compute,
    month_end,
    month_start,
)

MONTH_LABELS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _guard(write: bool = False):
    dt = "Insight Allocation Entry"
    if not frappe.has_permission(dt, "write" if write else "read"):
        frappe.throw(_("Not permitted."), frappe.PermissionError)




@frappe.whitelist()
def list_rules(company=None):
    _guard()
    filters = {"is_active": 1}
    if company:
        filters["company"] = company
    return frappe.get_all(
        "Insight Allocation Rule", filters=filters,
        fields=["name", "title", "company", "driver_label", "pool_mode", "pool_report",
                "pool_row_key", "pool_cost_center", "pool_flag", "pool_sign",
                "formula", "credit_back", "credit_cost_center",
                "driver_source", "count_as_of", "cc_from"],
        order_by="title asc", limit_page_length=0,
    )


def classify_rules_state(company, rules, orphans, deleted, error=None):
    """Why the rule picker is empty. Pure.

    rules: [{name, company, is_active}]; orphans: [{rule, entries}] (entries whose
    rule no longer exists); deleted: [{deleted_name, owner, creation, restored}]."""
    if error:
        return "read_error"
    if rules:
        active_here = [r for r in rules if r.get("is_active") and (not company or r.get("company") == company)]
        if active_here:
            return "ok"
        if any(r.get("is_active") for r in rules):
            return "other_company"
        return "inactive_only"
    if any(not d.get("restored") for d in deleted or []):
        return "deleted"
    if orphans:
        return "orphan_entries"
    return "none_exist"


@frappe.whitelist()
def rules_diagnostic(company=None):
    """Why the rule picker is empty: every rule on the site, however filtered."""
    _guard()
    out = {"company": company, "rules": [], "orphans": [], "deleted": [], "error": None,
           "excluded_doctype": bool(frappe.db.exists("DocType", "Insight Allocation Excluded CC"))}
    try:
        out["rules"] = frappe.get_all(
            "Insight Allocation Rule", fields=["name", "company", "is_active"],
            limit_page_length=0)
    except Exception as e:
        out["error"] = str(e)[:300]
    try:
        known = {r["name"] for r in out["rules"]}
        for r in frappe.db.sql(
                "select `rule`, count(*) from `tabInsight Allocation Entry` group by `rule`"):
            if r[0] and r[0] not in known:
                out["orphans"].append({"rule": r[0], "entries": int(r[1])})
    except Exception:
        pass
    try:
        out["deleted"] = [
            {"name": d["name"], "deleted_name": d["deleted_name"], "owner": d["owner"],
             "creation": str(d["creation"])[:16], "restored": int(d.get("restored") or 0)}
            for d in frappe.get_all(
                "Deleted Document", filters={"deleted_doctype": "Insight Allocation Rule"},
                fields=["name", "deleted_name", "owner", "creation", "restored"],
                order_by="creation desc", limit_page_length=20)]
    except Exception:
        pass
    out["reason"] = classify_rules_state(company, out["rules"], out["orphans"], out["deleted"], out["error"])
    return out


def classify_entries_state(own, orphans, deleted):
    """What to tell someone whose rule has no head-count entries. Pure.

    own: entries attached to this rule (any year); orphans: [{rule, entries}]
    entries whose rule no longer exists; deleted: [{rule, entries}] from the
    Deleted Document log that are not yet restored."""
    if own:
        return "ok"
    if any(d.get("entries") for d in deleted or []):
        return "deleted"
    if any(o.get("entries") for o in orphans or []):
        return "orphans"
    return "none"


@frappe.whitelist()
def entries_diagnostic(rule, year=None):
    """Where a rule's head-count entries went, when its grid comes back empty."""
    _guard()
    out = {"rule": rule, "own": 0, "orphans": [], "other_rules": [], "deleted": [], "error": None}
    try:
        out["own"] = frappe.db.count("Insight Allocation Entry", {"rule": rule})
        known = {r["name"] for r in frappe.get_all("Insight Allocation Rule", fields=["name"],
                                                   limit_page_length=0)}
        for r in frappe.db.sql(
                "select `rule`, count(*) from `tabInsight Allocation Entry` group by `rule`"):
            if not r[0] or r[0] == rule:
                continue
            (out["other_rules"] if r[0] in known else out["orphans"]).append(
                {"rule": r[0], "entries": int(r[1])})
    except Exception as e:
        out["error"] = str(e)[:300]
    try:
        by_rule = {}
        for d in frappe.get_all(
                "Deleted Document",
                # v2.89.1 — only rows mentioning this rule. The unfiltered read
                # pulled up to 5,000 full JSON documents every time the popup
                # opened.
                filters={"deleted_doctype": "Insight Allocation Entry", "restored": 0,
                         "data": ["like", f"%{rule}%"]},
                fields=["name", "data", "owner", "creation"], limit_page_length=5000):
            try:
                rn = (json.loads(d["data"]) or {}).get("rule")
            except Exception:
                continue
            g = by_rule.setdefault(rn, {"rule": rn, "entries": 0, "owner": d["owner"],
                                        "creation": str(d["creation"])[:16]})
            g["entries"] += 1
        out["deleted"] = sorted(by_rule.values(), key=lambda g: -g["entries"])
    except Exception:
        pass
    out["state"] = classify_entries_state(out["own"], out["orphans"], out["deleted"])
    return out


@frappe.whitelist()
def reattach_entries(from_rule, to_rule):
    """Point orphaned entries (their rule no longer exists) at an existing rule."""
    frappe.only_for("System Manager")
    if frappe.db.exists("Insight Allocation Rule", from_rule):
        frappe.throw(_("{0} still exists as a rule; only entries of a missing rule can be moved.").format(from_rule))
    if not frappe.db.exists("Insight Allocation Rule", to_rule):
        frappe.throw(_("Target rule {0} does not exist.").format(to_rule))
    moved = skipped = 0
    for e in frappe.get_all("Insight Allocation Entry", filters={"rule": from_rule},
                            fields=["name", "cost_center", "period_month"], limit_page_length=0):
        if frappe.db.exists("Insight Allocation Entry", {
                "rule": to_rule, "cost_center": e["cost_center"], "period_month": e["period_month"]}):
            skipped += 1
            continue
        frappe.db.set_value("Insight Allocation Entry", e["name"], "rule", to_rule)
        moved += 1
    frappe.db.commit()
    return {"ok": True, "moved": moved, "skipped": skipped}


@frappe.whitelist()
def restore_deleted_entries(rule):
    """Restore deleted head-count entries of one rule from the Deleted Document log."""
    frappe.only_for("System Manager")
    if not frappe.db.exists("Insight Allocation Rule", rule):
        frappe.throw(_("Rule {0} does not exist; restore or create it first.").format(rule))
    from frappe.core.doctype.deleted_document.deleted_document import restore
    restored = failed = 0
    errors = []
    for d in frappe.get_all(
            "Deleted Document",
            filters={"deleted_doctype": "Insight Allocation Entry", "restored": 0,
                     "data": ["like", f"%{rule}%"]},
            fields=["name", "data"], limit_page_length=5000):
        try:
            if (json.loads(d["data"]) or {}).get("rule") != rule:
                continue
            restore(d["name"], alert=False)
            restored += 1
        except Exception as e:
            failed += 1
            if len(errors) < 5:
                errors.append(str(e)[:160])
    frappe.db.commit()
    return {"ok": True, "restored": restored, "failed": failed, "errors": errors}


def parse_head_count_table(text):
    """Rows of (label, {month: value}) from pasted text. Pure.

    One cost centre per line: the label, then up to 12 month columns (Jan..Dec),
    separated by tabs (a copy from Excel or a table), commas, or runs of 2+
    spaces. Blank cells are skipped, a header or YTD column is ignored."""
    rows = []
    for line in (text or "").replace("\ufeff", "").splitlines():
        if not line.strip():
            continue
        if "\t" in line:
            parts = line.split("\t")
        elif line.count(",") >= 3:
            parts = line.split(",")
        else:
            parts = re.split(r"\s{2,}", line.strip())
        parts = [p.strip() for p in parts]
        label, months = parts[0], {}
        for i, v in enumerate(parts[1:13]):
            if v == "":
                continue
            try:
                months[i + 1] = float(v.replace(",", ""))
            except ValueError:
                pass
        if label and months:
            rows.append((label, months))
    return rows


def _norm_cc(s):
    return re.sub(r"\s*-\s*", " - ", re.sub(r"\s+", " ", (s or "").strip().lower()))


def match_cost_centre(label, options):
    """Cost Center name for a pasted label, or None if absent or ambiguous. Pure.

    options: [{name, cost_center_name}]. Matches the label against the display
    name, the full name, and the full name without its company suffix, ignoring
    case and spacing around hyphens ("Sales- IRSAA" = "Sales - IRSAA")."""
    want = _norm_cc(label)
    hits = set()
    for o in options:
        full = _norm_cc(o.get("name"))
        cands = {_norm_cc(o.get("cost_center_name")), full, full.rsplit(" - ", 1)[0]}
        if want in cands:
            hits.add(o["name"])
    return next(iter(hits)) if len(hits) == 1 else None


@frappe.whitelist()
def import_head_counts(rule, year, text, overwrite=0, dry_run=1, basis="head_count"):
    """Write head counts pasted from a table. Cost centres are matched by name.

    basis "amount" writes the Amount table instead (a figure taken out of the
    pool before the head-count split), e.g. a fixed monthly charge.
    Existing frozen entries are never touched; other existing entries are only
    replaced when overwrite is set. dry_run reports what would happen."""
    from neotec_insight.neotec_insight.utils.allocation import month_start
    _guard(write=True)
    year = int(year)
    if basis not in ("head_count", "amount"):
        frappe.throw(_("basis must be head_count or amount."))
    vfield = "driver_value" if basis == "head_count" else "amount"
    company = frappe.db.get_value("Insight Allocation Rule", rule, "company")
    options = frappe.get_all("Cost Center", filters={"is_group": 0, "company": company},
                             fields=["name", "cost_center_name"], limit_page_length=0)
    out = {"created": 0, "updated": 0, "skipped_existing": 0, "skipped_frozen": 0,
           "unmatched": [], "matched": [], "dry_run": bool(int(dry_run))}
    if int(overwrite) and not out["dry_run"]:
        from neotec_insight.neotec_insight.utils import snapshots
        snapshots.guard("pre_change", sections=["Insight Allocation Rule", "Insight Allocation Entry"],
                        label=f"Before paste-overwrite {rule} {year}")
    for label, months in parse_head_count_table(text):
        cc = match_cost_centre(label, options)
        if not cc:
            out["unmatched"].append(label)
            continue
        out["matched"].append({"label": label, "cost_center": cc})
        for m, v in months.items():
            ms = month_start(year, m)
            ex = frappe.db.get_value("Insight Allocation Entry",
                                     {"rule": rule, "cost_center": cc, "period_month": ms},
                                     ["name", "is_frozen"], as_dict=True)
            if ex:
                if ex["is_frozen"]:
                    out["skipped_frozen"] += 1
                elif int(overwrite):
                    if not out["dry_run"]:
                        frappe.db.set_value("Insight Allocation Entry", ex["name"],
                                            {vfield: v, "basis": basis})
                    out["updated"] += 1
                else:
                    out["skipped_existing"] += 1
                continue
            if not out["dry_run"]:
                e = frappe.new_doc("Insight Allocation Entry")
                e.update({"rule": rule, "company": company, "cost_center": cc,
                          "period_month": ms, "basis": basis, vfield: v,
                          "source": "manual"})
                e.insert()
            out["created"] += 1
    if not out["dry_run"]:
        remember_layout(rule, add={m["cost_center"]: basis for m in out["matched"]})
        frappe.db.commit()
    return out


@frappe.whitelist()
def list_cost_centers(company=None):
    """Leaf cost centres — the report spreads across these, horizontally."""
    _guard()
    filters = {"is_group": 0}
    if company:
        filters["company"] = company
    rows = frappe.get_all("Cost Center", filters=filters,
                          fields=["name", "cost_center_name"],
                          order_by="cost_center_name asc", limit_page_length=0)
    return [{"name": r["name"], "label": r.get("cost_center_name") or r["name"]} for r in rows]


@frappe.whitelist()
def get_grid(rule, year, company=None):
    """The driver grid for one rule and year: cost centres × months.

    Every cost centre that has any entry in the year is returned for all
    twelve months, so the editor renders a complete rectangle rather than a
    ragged one — a blank cell and a zero mean different things and the user
    needs to see which is which.
    """
    _guard()
    year = int(year)
    doc = frappe.get_doc("Insight Allocation Rule", rule)
    company = company or doc.company

    rows = frappe.get_all(
        "Insight Allocation Entry",
        filters={"rule": rule,
                 "period_month": ["between", [month_start(year, 1), month_end(year, 12)]]},
        fields=["name", "cost_center", "period_month", "basis", "driver_value", "amount",
                "budget_amount", "manual_pool", "is_frozen"],
        limit_page_length=0,
    )
    cells: dict[str, dict] = {}
    basis_by_cc: dict[str, str] = {}
    pool: dict[int, float] = {}
    # v2.88.5 — cost centres opted out of this rule are not part of the grid.
    excluded = sorted({d.cost_center for d in (getattr(doc, "excluded_ccs", None) or []) if d.cost_center})
    for r in rows:
        if r["cost_center"] in excluded:
            continue
        pm = r["period_month"]
        m = pm.month if hasattr(pm, "month") else int(str(pm)[5:7])
        cells.setdefault(r["cost_center"], {})[m] = {
            "driver": flt(r.get("driver_value")),
            "amount": flt(r.get("amount")),
            "budget": flt(r.get("budget_amount")),
        }
        basis_by_cc[r["cost_center"]] = r.get("basis") or "head_count"
        if r.get("manual_pool"):
            pool[m] = flt(r["manual_pool"])

    # v2.89.2 — cost centres the rule is set up with keep their row even in a
    # month, year or situation with no stored figures (a new year, or after a
    # loss), so the grid shows the full layout to type into or paste over.
    layout_only = []
    for cc, b in read_layout(rule).items():
        if cc in excluded or cc in cells:
            continue
        cells[cc] = {}
        basis_by_cc.setdefault(cc, b or "head_count")
        layout_only.append(cc)

    return {
        "rule": rule,
        "title": doc.title,
        "layout_only": sorted(layout_only),
        "driver_label": doc.driver_label or "Driver",
        "pool_mode": doc.pool_mode,
        "pool_flag": doc.pool_flag,
        "company": company,
        "year": year,
        "months": MONTH_LABELS,
        "cost_centers": sorted(cells.keys()),
        "excluded": excluded,
        "cells": cells,
        "basis": basis_by_cc,
        "manual_pool": pool,
        # v2.89.0 — echoed back by save_grid so a stale grid cannot be saved.
        "entry_count": len(rows),
        "frozen": sorted({f"{r['cost_center']}|{_month_of(r['period_month'])}" for r in rows
                          if r.get("is_frozen")}),
    }


@frappe.whitelist()
def save_grid(rule, year, company=None, cells=None, manual_pool=None, basis=None, remove=None,
              loaded_count=None, confirm_bulk=0):
    """Bulk upsert of one rule-year, under the rules in utils/allocation_guard.py.

    v2.89.0 — a save can no longer make figures disappear unnoticed:
      * refused if the rule-year changed since the grid was loaded
        (`loaded_count` = entry_count from get_grid) — "Reload" first;
      * frozen months are never changed or deleted, only reported;
      * more than one row's worth of blank-cell deletions needs confirm_bulk=1;
      * a scoped snapshot of the rule's entries is taken before anything is
        deleted or a captured figure is overridden;
      * every deletion goes through the Deleted Document log and every change
        is kept in the entry's version history.

    Rows whose value and pool are all empty are deleted rather than stored as
    zeros — an absent head count and a head count of zero are different facts.
    """
    from neotec_insight.neotec_insight.utils.allocation_guard import (
        is_stale, needs_bulk_confirmation, plan_grid_save,
    )
    _guard(write=True)
    year = int(year)
    doc = frappe.get_doc("Insight Allocation Rule", rule)
    company = company or doc.company

    def _j(v, default):
        return json.loads(v or json.dumps(default)) if isinstance(v, str) else (v or default)
    cells, manual_pool = _j(cells, {}), _j(manual_pool, {})
    basis, remove = _j(basis, {}), _j(remove, [])

    existing = {}
    for r in frappe.get_all(
            "Insight Allocation Entry",
            filters={"rule": rule,
                     "period_month": ["between", [month_start(year, 1), month_end(year, 12)]]},
            fields=["name", "cost_center", "period_month", "basis", "driver_value", "amount",
                    "budget_amount", "manual_pool", "is_frozen", "source", "captured_on",
                    "captured_value"],
            limit_page_length=0):
        pm = r["period_month"]
        m = pm.month if hasattr(pm, "month") else int(str(pm)[5:7])
        existing[(r["cost_center"], m)] = r

    if is_stale(loaded_count, len(existing)):
        frappe.throw(_("GRID_STALE: {0} {1} now holds {2} stored entries, but this grid was loaded "
                       "with {3}. Nothing was saved. Press Reload to see the current figures, then "
                       "enter your changes again.").format(rule, year, len(existing), loaded_count),
                     title=_("Figures changed since the grid was loaded"))

    plan = plan_grid_save(existing, cells, basis, manual_pool, remove)
    c = plan["counts"]
    if needs_bulk_confirmation(c) and not int(confirm_bulk or 0):
        frappe.throw(_("BULK_DELETE: this save would delete {0} stored entries because their "
                       "cells are blank. Nothing was saved. Confirm to go ahead.").format(c["blank_delete"]),
                     title=_("Confirm deletion"))

    snapshot = None
    if c["delete"] or c["overrides"]:
        from neotec_insight.neotec_insight.utils import snapshots
        snapshot = snapshots.guard(
            "pre_change", sections=["Insight Allocation Rule", "Insight Allocation Entry"],
            label=f"Before saving {rule} {year}: {c['delete']} deleted, {c['overrides']} overridden")

    for o in plan["ops"]:
        if o["op"] == "delete":
            frappe.delete_doc("Insight Allocation Entry", o["name"], ignore_permissions=False, force=1)
        elif o["op"] in ("insert", "update"):
            if o["op"] == "update":
                e = frappe.get_doc("Insight Allocation Entry", o["name"])
            else:
                e = frappe.new_doc("Insight Allocation Entry")
                e.rule = rule
                e.cost_center = o["cc"]
                e.period_month = month_start(year, o["m"])
                e.source = "manual"
            e.company = company
            e.update(o["values"])
            e.save()

    remember_layout(rule, add={cc: (basis or {}).get(cc) or "head_count" for cc in (cells or {})
                               if cc not in (remove or [])},
                    drop=remove)
    frappe.db.commit()
    skipped = sorted({f"{o['cc']} · {MONTH_LABELS[o['m'] - 1]}" for o in plan["ops"]
                      if o["op"] == "skip_frozen"})
    return {"ok": True, "written": c["insert"] + c["update"], "deleted": c["delete"],
            "unchanged": c["unchanged"], "skipped_frozen": c["skip_frozen"],
            "skipped_frozen_cells": skipped[:40], "overrides": c["overrides"],
            "snapshot": snapshot}


def _month_of(pm) -> int:
    return pm.month if hasattr(pm, "month") else int(str(pm)[5:7])


def _pool_from_report_row(rule_doc, company, year):
    """Pool by calendar month (see _pool_with_status). Signature kept for the
    P&L engine, which calls this to evaluate allocation rows."""
    return _pool_with_status(rule_doc, company, year)[0]


def _pool_with_status(rule_doc, company, year):
    """(pool_by_month, status). status = {"state", "message", ...}.

    v2.89.2 — this used to return {} on every failure, and the screen showed
    "no pool" in every month with no reason. Each failure now has a state:
      not_configured · report_missing · error · row_missing · relinked ·
      zero · ok · recursion
    row_missing carries `candidates` (the report's rows) so the screen can
    offer to pick the new one.

    The pool as the report already reports it.

    This is the number on screen when the P&L is run with one cost centre
    selected — `Total Expenses` for GMO, say. Reading the report rather than
    re-deriving it from accounts means the allocation cannot drift from the
    statement it is spread out of: remap an account and both move together.
    """
    from neotec_insight.neotec_insight.utils.execution import (
        execute_report, load_flag_mappings, load_flag_to_accounts,
    )

    def st(state, message, **kw):
        return {"state": state, "message": message, "report": rule_doc.pool_report,
                "row_key": rule_doc.pool_row_key, "cost_center": rule_doc.pool_cost_center, **kw}

    if not (rule_doc.pool_report and rule_doc.pool_row_key):
        return {}, st("not_configured", "The rule has no source report or row set.")
    if not frappe.db.exists("Insight Report Definition", rule_doc.pool_report):
        return {}, st("report_missing",
                      f"Report {rule_doc.pool_report} does not exist on this site.")

    # ── Recursion guard (v2.60.2) ────────────────────────────────────────
    # The pool is a row of a report, and that same report is where the
    # allocation rows live. Running it to read the pool therefore evaluates
    # the allocation rows, which run this function again — unbounded
    # recursion until the worker dies.
    #
    # The guard also fixes the accounting, not just the crash: while a pool
    # is being read, allocation rows evaluate to zero, so the pool is the
    # cost centre's expense *before* any allocation. That is the only figure
    # it can be — allocating a number that already contains allocations is
    # circular by definition.
    inflight = frappe.flags.setdefault("ni_allocation_pool_inflight", set())
    token = (rule_doc.pool_report, rule_doc.pool_cost_center or "", int(year))
    if token in inflight:
        return {}, st("recursion", "Pool read while the same report is being evaluated.")
    inflight.add(token)
    try:
        rd = frappe.get_doc("Insight Report Definition", rule_doc.pool_report)
        report_def = json.loads(rd.definition_json or "{}")
        res = execute_report(
            report_def=report_def,
            fiscal_year=int(year),
            # v2.62.0 — month indices here are 0-based and in *fiscal year
            # order*, not calendar months. Passing 1..12 read every figure
            # one slot late: January's allocation was February's expense.
            month_from=0,
            month_to=11,
            company=company,
            # One cost centre, exactly as the operator selects it on screen.
            cost_center=rule_doc.pool_cost_center or None,
            flag_to_accounts=load_flag_to_accounts(rule_doc.pool_report),
            flag_to_mappings=load_flag_mappings(rule_doc.pool_report),
        ) or {}
    except Exception as e:
        frappe.log_error(f"allocation pool report failed: {e}", "Neotec Insight: allocation")
        return {}, st("error", f"Running {rule_doc.pool_report} failed: {str(e)[:300]}")
    finally:
        inflight.discard(token)

    key = rule_doc.pool_row_key
    rows = res.get("rows") or []
    hit = next((r for r in rows if r.get("key") == key or r.get("label") == key), None)
    relinked = None
    label = getattr(rule_doc, "pool_row_label", None)
    if hit is None and label:
        # The report was rebuilt and the row has a new key; same label.
        hit = next((r for r in rows if (r.get("label") or "").strip() == label.strip()), None)
        if hit is not None and hit.get("key"):
            relinked = hit["key"]
            try:
                frappe.db.set_value("Insight Allocation Rule", rule_doc.name, "pool_row_key",
                                    relinked, update_modified=False)
            except Exception:
                pass
    if hit is None:
        # v2.89.4 — each candidate carries its January and year figures (in
        # calendar months, with the rule's sign), so the right row can be
        # recognised by its numbers. Rows with figures come first.
        sign = -1.0 if (rule_doc.pool_sign or "normal") == "invert" else 1.0
        cands = []
        for r in rows:
            if not r.get("key"):
                continue
            by_cal = {}
            for k, v in (r.get("monthly") or {}).items():
                try:
                    by_cal[calendar_month_for_fy_month(company, int(k))] = sign * flt(v)
                except Exception:
                    pass
            cands.append({"key": r.get("key"), "label": r.get("label") or r.get("key"),
                          "jan": flt(by_cal.get(1), 2), "ytd": flt(sum(by_cal.values()), 2)})
        cands = sorted(cands, key=lambda c: 0 if (c["ytd"] or c["jan"]) else 1)[:400]
        return {}, st("row_missing",
                      f"Row {key} is not in report {rule_doc.pool_report} any more "
                      "(the report was probably rebuilt or re-imported, which gives rows new keys). "
                      "Pick the row that holds the pool.", candidates=cands)
    row = hit
    monthly = row.get("monthly") or {}
    # Translate fiscal-year position into calendar month, because the
    # driver entries are keyed by real dates. For a January-start
    # company this is idx+1; for an April-start one it is not, and
    # assuming otherwise would silently misalign every figure.
    out = {}
    for k, v in monthly.items():
        cal = calendar_month_for_fy_month(company, int(k))
        out[cal] = flt(v)
    if (rule_doc.pool_sign or "normal") == "invert":
        out = {m: -v for m, v in out.items()}
    if not any(abs(v) > 0.005 for v in out.values()):
        return out, st("zero",
                       f"Row “{row.get('label') or key}” of {rule_doc.pool_report} is zero in every "
                       f"month of {year} for cost centre {rule_doc.pool_cost_center or '(all)'}. "
                       "Check that the cost centre and year are right and that the report "
                       "shows figures for them.", row_label=row.get("label"))
    if relinked:
        return out, st("relinked", f"Row found again by its label “{label}”; key updated to {relinked}.")
    return out, st("ok", "")


@frappe.whitelist()
def list_report_rows(report):
    """Rows of a report, for picking which one carries the pool."""
    _guard()
    try:
        rd = frappe.get_doc("Insight Report Definition", report)
        rows = (json.loads(rd.definition_json or "{}") or {}).get("rows") or []
    except Exception:
        return []
    return [{"key": r.get("key"), "label": r.get("label") or r.get("key"),
             "kind": r.get("kind")} for r in rows if r.get("key")]


def _pool_from_flag(rule_doc, company, year):
    """Monthly pool totals for a flag-sourced rule, straight from the GL.

    Account flags are defined per report, not globally, so the flag is
    resolved against the report named on the rule. That keeps one source of
    truth: the same account map that feeds the P&L feeds the allocation, and
    a remapped account cannot make the two disagree.
    """
    from neotec_insight.neotec_insight.utils.execution import (
        _fetch_monthly_for_accounts, load_flag_to_accounts,
    )

    if not rule_doc.pool_report:
        return {}
    try:
        accounts = (load_flag_to_accounts(rule_doc.pool_report) or {}).get(rule_doc.pool_flag) or []
    except Exception:
        accounts = []
    if not accounts:
        return {}
    try:
        monthly = _fetch_monthly_for_accounts(
            accounts=accounts, fiscal_year=year, months=list(range(1, 13)),
            cost_center=None, project=None, department=None, branch=None,
            company=company, row_scope=None, dimension_filters=None,
            fy_start_month_override=None, period_mode=None,
            period_from_date=None, period_to_date=None,
        ) or {}
    except Exception as e:
        frappe.log_error(f"allocation pool fetch failed: {e}", "Neotec Insight: allocation")
        return {}
    if (rule_doc.pool_sign or "normal") == "invert":
        monthly = {m: -v for m, v in monthly.items()}
    return monthly


@frappe.whitelist()
def run(rule, year, company=None):
    """The allocation report: cost centres across, months down."""
    _guard()
    year = int(year)
    doc = frappe.get_doc("Insight Allocation Rule", rule)
    company = company or doc.company

    mode = doc.pool_mode or "report_row"
    pool_by_month = None
    pool_status = {"state": "ok", "message": ""}
    if mode == "report_row":
        pool_by_month, pool_status = _pool_with_status(doc, company, year)
    elif mode == "flag" and doc.pool_flag:
        pool_by_month = _pool_from_flag(doc, company, year)
        if not any(abs(flt(v)) > 0.005 for v in (pool_by_month or {}).values()):
            pool_status = {"state": "zero", "message":
                           f"Flag {doc.pool_flag} returns no figures for {year}."}

    res = compute(rule, company, year, pool_by_month=pool_by_month)
    res["pool_status"] = pool_status
    res["month_labels"] = MONTH_LABELS

    cc_labels = {}
    for cc in res["cost_centers"]:
        cc_labels[cc] = frappe.db.get_value("Cost Center", cc, "cost_center_name") or cc
    res["cost_center_labels"] = cc_labels

    # Year totals per cost centre, so the report can carry a YTD column
    # without the client re-adding twelve floats and rounding differently.
    res["pool_source"] = (
        f"{doc.pool_report} · {doc.pool_row_key} · {doc.pool_cost_center}"
        if mode == "report_row" else
        (f"flag {doc.pool_flag}" if mode == "flag" else "manual")
    )
    ytd = {cc: 0.0 for cc in res["cost_centers"]}
    for m in res["months"]:
        for cc, v in res["by_month"][m]["allocation"].items():
            ytd[cc] = flt(ytd.get(cc, 0.0) + v, 2)
    res["ytd"] = ytd
    res["ytd_pool"] = flt(sum(res["by_month"][m]["pool"] for m in res["months"]), 2)
    res["ytd_residual"] = flt(sum(res["by_month"][m]["residual"] for m in res["months"]), 2)

    # ── Budget (v2.78.0) ────────────────────────────────────────────────────
    # Read straight from the stored entries and NEVER derived. A budget for an
    # allocation is a decision someone signed off; re-deriving it from the
    # actual driver would produce a figure nobody agreed to, and it would move
    # every time the driver moved — which is exactly what a budget must not do.
    #
    # Carried alongside the computed actuals rather than inside `by_month`, so
    # the split between "derived" and "entered" stays legible: everything in
    # `by_month` is calculated, everything here was typed by a person.
    budget: dict[int, dict[str, float]] = {m: {} for m in res["months"]}
    budget_ytd: dict[str, float] = {cc: 0.0 for cc in res["cost_centers"]}
    for r in frappe.get_all(
            "Insight Allocation Entry",
            filters={"rule": rule,
                     "period_month": ["between", [month_start(year, 1), month_end(year, 12)]]},
            fields=["cost_center", "period_month", "budget_amount"],
            limit_page_length=0):
        b = flt(r.get("budget_amount"))
        if not b:
            continue
        pm = r["period_month"]
        m = pm.month if hasattr(pm, "month") else int(str(pm)[5:7])
        cc = r["cost_center"]
        if m in budget:
            budget[m][cc] = flt(budget[m].get(cc, 0.0) + b, 2)
        budget_ytd[cc] = flt(budget_ytd.get(cc, 0.0) + b, 2)
    res["budget"] = budget
    res["budget_ytd"] = budget_ytd
    res["budget_total"] = flt(sum(budget_ytd.values()), 2)

    # Variance, computed here so screen and every export agree on the sign.
    # Actual over budget is POSITIVE — an allocation is a cost, so spending
    # more than budgeted reads as a positive overrun rather than a negative.
    res["variance_ytd"] = {cc: flt(ytd.get(cc, 0.0) - budget_ytd.get(cc, 0.0), 2)
                           for cc in res["cost_centers"]}
    return res


# ── Driver capture (v2.63.0) ────────────────────────────────────────────────


@frappe.whitelist()
def capture_preview(rule, year, months=None):
    """What a capture would do. Writes nothing."""
    _guard()
    from neotec_insight.neotec_insight.utils import driver_capture
    if isinstance(months, str):
        months = json.loads(months or "null")
    return driver_capture.preview(rule, int(year), months)


@frappe.whitelist()
def capture_status(rule, year):
    """Which months are empty, manual, captured or frozen — for the picker."""
    _guard()
    from neotec_insight.neotec_insight.utils import driver_capture
    return driver_capture.month_status(rule, int(year))


@frappe.whitelist()
def capture_commit(rule, year, months=None, freeze=0, refresh_drift=0):
    """Accept a capture. Frozen and overridden rows are left alone, unless
    refresh_drift is set to re-open and rewrite frozen rows that differ."""
    _guard(write=True)
    from neotec_insight.neotec_insight.utils import driver_capture
    if isinstance(months, str):
        months = json.loads(months or "null")
    # v2.89.0 — a capture rewrites figures; keep the rule's entries as they were.
    from neotec_insight.neotec_insight.utils import snapshots
    snapshots.guard("pre_change", sections=["Insight Allocation Rule", "Insight Allocation Entry"],
                    label=f"Before capture {rule} {year}")
    res = driver_capture.commit(rule, int(year), months, freeze=bool(int(freeze or 0)),
                                refresh_drift=bool(int(refresh_drift or 0)))
    _layout_from_year(rule, int(year))
    return res


@frappe.whitelist()
def set_cc_excluded(rule, cost_center, excluded=1, reason=None):
    """Take a cost centre out of (or put it back into) a rule's allocation."""
    _guard(write=True)
    doc = frappe.get_doc("Insight Allocation Rule", rule)
    if not frappe.get_meta("Insight Allocation Rule").get_field("excluded_ccs"):
        frappe.throw(_("The Excluded Cost Centres field is not installed yet. Run Migrate Database."))
    keep = [d for d in (getattr(doc, "excluded_ccs", None) or []) if d.cost_center != cost_center]
    if int(excluded):
        keep.append(frappe._dict(cost_center=cost_center, reason=(reason or "")[:140]))
    doc.set("excluded_ccs", [])
    for d in keep:
        doc.append("excluded_ccs", {"cost_center": d.cost_center, "reason": d.get("reason") or ""})
    doc.save()
    frappe.db.commit()
    return {"ok": True, "excluded": [d.cost_center for d in doc.excluded_ccs]}


@frappe.whitelist()
def get_evidence(rule, cost_center, year, month):
    """The people (or leads) behind one cell.

    Read back from the snapshot taken at capture time rather than re-counted,
    so the list always reconciles to the number it supports — including
    employees who have since transferred or been deleted.
    """
    _guard()
    from neotec_insight.neotec_insight.utils.allocation import month_start
    name = frappe.db.exists("Insight Allocation Entry", {
        "rule": rule, "cost_center": cost_center,
        "period_month": month_start(int(year), int(month))})
    if not name:
        return {"available": False, "reason": "no_entry"}
    doc = frappe.get_doc("Insight Allocation Entry", name)
    if not doc.evidence_json:
        return {
            "available": False,
            "reason": "manual",
            "value": flt(doc.driver_value) or flt(doc.amount),
            "basis": doc.basis,
            "modified_by": doc.modified_by,
            "modified": str(doc.modified),
        }
    try:
        ev = json.loads(doc.evidence_json)
    except Exception:
        return {"available": False, "reason": "unreadable"}
    ev["available"] = True
    ev["stored_value"] = flt(doc.driver_value)
    ev["is_override"] = int(doc.is_override or 0)
    ev["is_frozen"] = int(doc.is_frozen or 0)
    # The whole point of a snapshot is that it still adds up. Say so, or say
    # loudly that it does not.
    ev["reconciles"] = abs(len(ev.get("members") or []) - flt(doc.driver_value)) < 0.0001
    return ev


@frappe.whitelist()
def freeze_months(rule, year, months, frozen=1):
    """Close (or reopen) months so later captures report drift, not changes."""
    _guard(write=True)
    from neotec_insight.neotec_insight.utils.allocation import month_start
    if isinstance(months, str):
        months = json.loads(months or "[]")
    n = 0
    for m in months or []:
        for e in frappe.get_all("Insight Allocation Entry",
                                filters={"rule": rule, "period_month": month_start(int(year), int(m))},
                                fields=["name"], limit_page_length=0):
            frappe.db.set_value("Insight Allocation Entry", e["name"], "is_frozen",
                                1 if int(frozen) else 0)
            n += 1
    frappe.db.commit()
    return {"ok": True, "updated": n}


@frappe.whitelist()
def unassigned_employees(rule, year, months=None):
    """Pre-flight: who has no cost centre, and what it distorts."""
    _guard()
    from neotec_insight.neotec_insight.utils import driver_capture
    if isinstance(months, str):
        months = json.loads(months or "null")
    return driver_capture.unassigned_report(rule, int(year), months)


# ── v2.89.0: recover a rule's entries from an automatic snapshot ─────────────

@frappe.whitelist()
def snapshot_candidates(rule, year=None):
    """Automatic snapshots that hold entries for this rule (and year), newest
    first, read from each snapshot's small index — nothing is unpacked."""
    _guard()
    from neotec_insight.neotec_insight.utils import snapshots
    if not snapshots._store_ready():
        return {"ready": False, "rows": []}
    out = []
    for s in frappe.get_all(snapshots.DT,
                            fields=["name", "snapshot_type", "label", "creation", "index_json",
                                    "app_version", "alert"],
                            order_by="creation desc", limit_page_length=200):
        try:
            idx = json.loads(s.get("index_json") or "{}")
        except Exception:
            continue
        n_all = int((idx.get("allocation_entries_by_rule") or {}).get(rule) or 0)
        n_year = int(((idx.get("allocation_entries_by_rule_year") or {}).get(rule) or {})
                     .get(str(year), 0)) if year else n_all
        if n_all:
            out.append({"name": s["name"], "type": s["snapshot_type"], "label": s["label"],
                        "creation": str(s["creation"])[:16], "version": s["app_version"],
                        "entries": n_all, "entries_year": n_year, "alert": s.get("alert") or ""})
    return {"ready": True, "rows": out}


@frappe.whitelist(methods=["POST"])
def restore_from_snapshot(rule, snapshot, year=None, overwrite=0, dry_run=0):
    """Bring one rule's entries back from a snapshot.

    Missing entries (by rule + cost centre + month) are inserted; existing ones
    are kept unless overwrite is set, and frozen ones are never touched. If the
    rule itself is missing it is restored from the snapshot first."""
    _guard(write=True)
    from neotec_insight.neotec_insight.utils import snapshots
    from neotec_insight.neotec_insight.utils.snapshot_core import entries_for_rule
    bundle = snapshots.load(snapshot)
    year = int(year) if year else None
    rows = entries_for_rule(bundle, rule, year)
    out = {"inserted": 0, "updated": 0, "kept": 0, "skipped_frozen": 0, "rule_restored": False,
           "in_snapshot": len(rows), "dry_run": bool(int(dry_run))}

    if not frappe.db.exists("Insight Allocation Rule", rule):
        src = next((r for r in (bundle.get("data") or {}).get("Insight Allocation Rule") or []
                    if r.get("name") == rule), None)
        if not src:
            frappe.throw(_("Rule {0} is neither on this site nor in the snapshot.").format(rule))
        if not out["dry_run"]:
            d = dict(src, doctype="Insight Allocation Rule")
            doc = frappe.get_doc(d)
            doc.flags.ignore_links = True
            doc.insert(ignore_permissions=True, set_name=rule)
        out["rule_restored"] = True

    if int(overwrite) and not out["dry_run"]:
        snapshots.guard("pre_change", sections=["Insight Allocation Rule", "Insight Allocation Entry"],
                        label=f"Before restoring {rule} from {snapshot}")

    for r in rows:
        key = {"rule": rule, "cost_center": r.get("cost_center"), "period_month": r.get("period_month")}
        ex = frappe.db.get_value("Insight Allocation Entry", key, ["name", "is_frozen"], as_dict=True)
        vals = {k: v for k, v in r.items() if k not in ("name", "doctype")}
        if ex:
            if ex["is_frozen"]:
                out["skipped_frozen"] += 1
            elif int(overwrite):
                if not out["dry_run"]:
                    e = frappe.get_doc("Insight Allocation Entry", ex["name"])
                    e.update(vals)
                    e.flags.ignore_links = True
                    e.save()
                out["updated"] += 1
            else:
                out["kept"] += 1
            continue
        if not out["dry_run"]:
            e = frappe.get_doc(dict(vals, doctype="Insight Allocation Entry"))
            e.flags.ignore_links = True
            e.insert(set_name=r.get("name") if r.get("name") and not frappe.db.exists(
                "Insight Allocation Entry", r.get("name")) else None)
        out["inserted"] += 1
    if not out["dry_run"]:
        remember_layout(rule, add={r.get("cost_center"): r.get("basis") or "head_count" for r in rows})
        frappe.db.commit()
    return out


# ── v2.89.2: pool row picker and a cost-centre layout that outlives figures ──

@frappe.whitelist(methods=["POST"])
def set_pool_row(rule, key):
    """Point the rule at a different row of its pool report, and remember the
    row's label so a later rebuild of the report can be followed by label."""
    _guard(write=True)
    doc = frappe.get_doc("Insight Allocation Rule", rule)
    rows = list_report_rows(doc.pool_report) if doc.pool_report else []
    hit = next((r for r in rows if r["key"] == key), None)
    if not hit:
        frappe.throw(_("Row {0} is not in report {1}.").format(key, doc.pool_report))
    vals = {"pool_row_key": key}
    if frappe.get_meta("Insight Allocation Rule").get_field("pool_row_label"):
        vals["pool_row_label"] = hit["label"]
    frappe.db.set_value("Insight Allocation Rule", rule, vals)
    frappe.db.commit()
    return {"ok": True, "key": key, "label": hit["label"]}


def _has_layout_field() -> bool:
    try:
        return bool(frappe.get_meta("Insight Allocation Rule").get_field("layout_json"))
    except Exception:
        return False


def read_layout(rule) -> dict:
    """{cost_center: basis} remembered for the rule. On first use it is seeded
    from every entry the rule has ever had (any year)."""
    if not _has_layout_field():
        return {}
    raw = frappe.db.get_value("Insight Allocation Rule", rule, "layout_json")
    if raw:
        try:
            return dict(json.loads(raw) or {})
        except Exception:
            return {}
    seeded = {}
    for r in frappe.get_all("Insight Allocation Entry", filters={"rule": rule},
                            fields=["cost_center", "basis", "period_month"],
                            order_by="period_month asc", limit_page_length=0):
        seeded[r["cost_center"]] = r.get("basis") or "head_count"
    if seeded:
        write_layout(rule, seeded)
    return seeded


def write_layout(rule, layout: dict):
    if _has_layout_field():
        frappe.db.set_value("Insight Allocation Rule", rule, "layout_json",
                            json.dumps(dict(sorted(layout.items()))), update_modified=False)


def remember_layout(rule, add: dict | None = None, drop=None):
    """Add cost centres (with their basis) and drop removed ones."""
    if not _has_layout_field():
        return
    lay = read_layout(rule)
    before = dict(lay)
    for cc, b in (add or {}).items():
        if cc:
            lay[cc] = b or "head_count"
    for cc in (drop or []):
        lay.pop(cc, None)
    if lay != before:
        write_layout(rule, lay)


def _layout_from_year(rule, year):
    from neotec_insight.neotec_insight.utils.allocation import month_end, month_start
    add = {r["cost_center"]: r.get("basis") or "head_count" for r in frappe.get_all(
        "Insight Allocation Entry",
        filters={"rule": rule, "period_month": ["between", [month_start(year, 1), month_end(year, 12)]]},
        fields=["cost_center", "basis"], limit_page_length=0)}
    remember_layout(rule, add=add)
    frappe.db.commit()


# ── v2.89.3: one-click diagnosis of the whole chain ──────────────────────────

def _year_of(pm) -> int:
    return pm.year if hasattr(pm, "year") else int(str(pm)[:4])


@frappe.whitelist()
def diagnose(rule, year, company=None):
    """Every link from stored figures to an allocated result, checked in order.

    Each check is {key, ok: True|False|None (info), title, detail, fix?}.
    `fix` names an action the screen can offer (move_year, pick_row,
    snapshots, paste)."""
    _guard()
    year = int(year)
    checks = []

    def add(key, ok, title, detail="", **kw):
        checks.append({"key": key, "ok": ok, "title": title, "detail": detail, **kw})

    if not frappe.db.exists("Insight Allocation Rule", rule):
        add("rule", False, f"Rule {rule} does not exist", fix="snapshots")
        return {"checks": checks}
    doc = frappe.get_doc("Insight Allocation Rule", rule)
    add("rule", bool(doc.is_active), f"Rule {rule}",
        f"Company {doc.company} · {'active' if doc.is_active else 'INACTIVE — it is not run'}")
    if company and doc.company and company != doc.company:
        add("company", None, "Screen company differs from the rule's company",
            f"Screen: {company} · rule: {doc.company}. The rule's own company is used.")

    rows = frappe.get_all("Insight Allocation Entry", filters={"rule": rule},
                          fields=["name", "cost_center", "period_month", "basis", "driver_value",
                                  "amount", "company", "is_frozen"], limit_page_length=0)
    by_year: dict[int, int] = {}
    for r in rows:
        by_year[_year_of(r["period_month"])] = by_year.get(_year_of(r["period_month"]), 0) + 1
    this = [r for r in rows if _year_of(r["period_month"]) == year]
    others = {y: n for y, n in sorted(by_year.items()) if y != year}
    if this:
        hc = [r for r in this if (r.get("basis") or "head_count") == "head_count"]
        am = [r for r in this if r.get("basis") == "amount"]
        ccs = sorted({r["cost_center"] for r in this})
        add("entries", True, f"{len(this)} figures stored for {year}",
            f"{len(ccs)} cost centres · {len(hc)} head-count cells (total "
            f"{sum(flt(r['driver_value']) for r in hc):g}) · {len(am)} amount cells (total "
            f"{sum(flt(r['amount']) for r in am):,.0f})")
    else:
        add("entries", False, f"No figures stored for {year}",
            "Nothing to allocate until head counts are entered, pasted or restored.",
            fix="paste")
    odd = {y: n for y, n in others.items() if y < 2000 or y > 2100}
    if odd:
        add("odd_years", False, "Figures stored under an impossible year",
            "; ".join(f"{n} under year {y}" for y, n in odd.items())
            + ". These are 2026-style figures saved while the Year box was half typed.",
            fix="move_year", years=sorted(odd))
    if others and not odd:
        add("other_years", None, "Figures in other years",
            "; ".join(f"{y}: {n}" for y, n in others.items()),
            fix="move_year" if not this else None, years=sorted(others))
    stamped = sorted({r.get("company") or "(blank)" for r in this if r.get("company") != doc.company})
    if stamped:
        add("entry_company", None, "Some figures carry another company",
            f"{', '.join(stamped)} — counted anyway (fixed in v2.89.3).")
    skip = {d.cost_center for d in (doc.get("excluded_ccs") or [])}
    hidden = sorted({r["cost_center"] for r in this if r["cost_center"] in skip})
    if hidden:
        add("excluded", None, "Figures on excluded cost centres are ignored",
            ", ".join(hidden))

    pool, st = ({}, {"state": "ok", "message": ""})
    if (doc.pool_mode or "report_row") == "report_row":
        pool, st = _pool_with_status(doc, doc.company, year)
    elif doc.pool_mode == "flag" and doc.pool_flag:
        pool = _pool_from_flag(doc, doc.company, year)
    total_pool = sum(flt(v) for v in (pool or {}).values())
    if doc.pool_mode == "manual":
        add("pool", None, "Pool is entered by hand on the grid")
    elif st["state"] in ("ok", "relinked") and total_pool:
        months = sum(1 for v in pool.values() if abs(flt(v)) > 0.005)
        add("pool", True, f"Pool found: {total_pool:,.0f} for {year}",
            f"{months} months with a pool · {doc.pool_report} · {doc.pool_row_key} · "
            f"{doc.pool_cost_center or ''}")
    else:
        add("pool", False, "Pool not found", st.get("message") or "Zero in every month.",
            fix="pick_row" if st.get("state") == "row_missing" else None,
            candidates=st.get("candidates"))
        # v2.89.5 — the row usually went missing because the report itself was
        # replaced by an older version. If an earlier version still holds the
        # row, offer to put that version back rather than pick another row.
        if st.get("state") == "row_missing" and "System Manager" in frappe.get_roles():
            try:
                from neotec_insight.neotec_insight.api.recovery import _collect_versions
                best = next((v for v in _collect_versions(doc.pool_report)
                             if doc.pool_row_key in v["summary"]["has_pool_rows"]), None)
                if best:
                    sm = best["summary"]
                    add("report_version", False,
                        f"An earlier version of {doc.pool_report} still has row {doc.pool_row_key}",
                        f"{best['source']} · {best['when']} · {sm['rows']} rows"
                        + (f" · allocation rows: {', '.join(sm['allocation_rows'])}" if sm["allocation_rows"] else "")
                        + (f" · {sm['last_formula']}" if sm["last_formula"] else ""),
                        fix="restore_report", report=doc.pool_report, version_id=best["id"])
            except Exception:
                pass

    if this and total_pool:
        try:
            from neotec_insight.neotec_insight.utils.allocation import compute
            res = compute(rule, doc.company, year, pool_by_month=pool)
            # "charged": what moved to the receiving cost centres. The plain sum
            # of `allocation` nets to zero when the source is credited back.
            alloc = sum(flt(res["by_month"][m].get("charged")) for m in res["months"])
            empty = [MONTH_LABELS[m - 1] for m in res["months"]
                     if res["by_month"][m].get("pool") and not res["by_month"][m]["allocation"]]
            add("result", alloc != 0, f"Allocated {alloc:,.0f} across {len(res['cost_centers'])} cost centres",
                ("Months with a pool but no head count: " + ", ".join(empty)) if empty else "")
        except Exception as e:
            add("result", False, "The allocation could not be computed", str(e)[:300])

    try:
        from neotec_insight.neotec_insight.utils import snapshots
        if snapshots._store_ready():
            n = 0
            for s in frappe.get_all(snapshots.DT, fields=["index_json"], limit_page_length=200):
                idx = json.loads(s.get("index_json") or "{}")
                if ((idx.get("allocation_entries_by_rule_year") or {}).get(rule) or {}).get(str(year)):
                    n += 1
            add("snapshots", None, f"{n} snapshots hold {year} figures for this rule",
                fix="snapshots" if n and not this else None)
    except Exception:
        pass
    deleted = frappe.db.count("Deleted Document", {"deleted_doctype": "Insight Allocation Entry",
                                                   "restored": 0, "data": ["like", f"%{rule}%"]})
    if deleted:
        add("deleted", None, f"{deleted} deleted entries of this rule can be restored",
            "Use Find my data.", fix="snapshots")
    return {"checks": checks, "rule": rule, "year": year}


@frappe.whitelist(methods=["POST"])
def move_entries_year(rule, from_year, to_year, overwrite=0):
    """Move a rule's figures from one year to another, month for month.
    Months already filled in the target are kept unless overwrite is set;
    frozen target months are never touched. Snapshot first."""
    _guard(write=True)
    from neotec_insight.neotec_insight.utils import snapshots
    from neotec_insight.neotec_insight.utils.allocation import month_start
    fy, ty = int(from_year), int(to_year)
    snapshots.guard("pre_change", sections=["Insight Allocation Rule", "Insight Allocation Entry"],
                    label=f"Before moving {rule} {fy} → {ty}")
    out = {"moved": 0, "kept_target": 0, "replaced": 0}
    src = frappe.get_all("Insight Allocation Entry",
                         filters={"rule": rule, "period_month": ["between", [
                             f"{fy:04d}-01-01", f"{fy:04d}-12-31"]]},
                         fields=["name", "cost_center", "period_month", "basis"],
                         limit_page_length=0)
    for r in src:
        pm = r["period_month"]
        m = pm.month if hasattr(pm, "month") else int(str(pm)[5:7])
        target = month_start(ty, m)
        ex = frappe.db.get_value("Insight Allocation Entry",
                                 {"rule": rule, "cost_center": r["cost_center"], "period_month": target},
                                 ["name", "is_frozen"], as_dict=True)
        if ex:
            if ex["is_frozen"] or not int(overwrite):
                out["kept_target"] += 1
                continue
            frappe.delete_doc("Insight Allocation Entry", ex["name"], force=1)
            out["replaced"] += 1
        e = frappe.get_doc("Insight Allocation Entry", r["name"])
        e.period_month = target
        e.save()
        out["moved"] += 1
    remember_layout(rule, add={r["cost_center"]: r.get("basis") or "head_count" for r in src})
    frappe.db.commit()
    return out
