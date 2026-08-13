"""Driver capture — counting head count from Employee records (v2.63.0).

Two things make this harder than a `SELECT COUNT(*)`:

**Point in time.** The People tab counts `status = "Active"`, which is a
snapshot of today. A head count that moves 12 → 11 → 8 across the year only
exists if each month is counted as of *that* month: joined by then, not yet
left. Everything here works from `date_of_joining` and `relieving_date`, not
from current status.

**History does not stay still.** `payroll_cost_center` holds one current
value with no history, so an employee who transfers in September would, on a
naive recount, appear to have been in the new cost centre all along — silently
rewriting July. Transfers are therefore replayed backwards from Employee
Transfer records where they exist, and an accepted month is frozen so that
later edits surface as drift rather than as a changed number.

Evidence is snapshotted, never re-queried. A list rebuilt at viewing time
would omit a since-deleted employee and stop reconciling to the count it is
supposed to support — which is exactly the moment an auditor is looking at it.
"""

from __future__ import annotations

import json
from datetime import date, datetime

import frappe
from frappe import _
from frappe.utils import flt, getdate

from neotec_insight.neotec_insight.utils.allocation import month_end, month_start


def _has(dt: str) -> bool:
    try:
        return bool(frappe.db.exists("DocType", dt))
    except Exception:
        return False


def _field(dt: str, fn: str) -> bool:
    try:
        return bool(frappe.get_meta(dt).get_field(fn))
    except Exception:
        return False


def _transfers_before(company: str, cutoff: str) -> dict[str, str]:
    """Cost centre an employee sat in *before* transfers on or after `cutoff`.

    Employee Transfer records carry a transfer date and the property changes
    made, so a past month can be reconstructed properly rather than assuming
    today's cost centre always applied. Returns {employee: previous_cc} for
    transfers that happened after the month being counted.
    """
    out: dict[str, str] = {}
    if not _has("Employee Transfer"):
        return out
    try:
        rows = frappe.get_all(
            "Employee Transfer",
            filters={"transfer_date": [">=", cutoff], "docstatus": ["<", 2]},
            fields=["name", "employee"], limit_page_length=0,
        )
        for r in rows:
            try:
                doc = frappe.get_doc("Employee Transfer", r["name"])
            except Exception:
                continue
            for d in (getattr(doc, "transfer_details", None) or []):
                if getattr(d, "property", None) in ("Payroll Cost Center", "payroll_cost_center"):
                    # Earliest transfer wins: walking back from today, the
                    # first change after the month is the state it left.
                    out.setdefault(r["employee"], getattr(d, "current", None) or "")
    except Exception as e:
        frappe.log_error(f"transfer replay failed: {e}", "Neotec Insight: capture")
    return out


def headcount_for_month(
    company: str,
    year: int,
    month: int,
    *,
    as_of: str = "month_end",
    cc_from: str = "payroll_cost_center",
    dept_map: dict[str, str] | None = None,
    include_status: str = "Active",
) -> dict:
    """Employees active in one month, grouped by cost centre, with evidence.

    Returns {"counts": {cc: n}, "evidence": {cc: [members]},
             "unassigned": [members], "reconstructed": bool}.
    """
    dept_map = dept_map or {}
    ms, me = month_start(year, month), month_end(year, month)
    asof = me if as_of == "month_end" else ms

    out = {"counts": {}, "evidence": {}, "unassigned": [], "reconstructed": False,
           "as_of": asof, "notes": [], "undated_inactive": []}
    if not _has("Employee"):
        out["notes"].append(_("Employee doctype is not installed."))
        return out

    fields = ["name", "employee_name", "date_of_joining", "status"]
    for f in ("relieving_date", "department", "payroll_cost_center", "designation"):
        if _field("Employee", f):
            fields.append(f)

    try:
        rows = frappe.get_all(
            "Employee",
            filters={
                "company": company,
                "date_of_joining": ["<=", asof],
            },
            fields=fields, limit_page_length=0,
        )
    except Exception as e:
        frappe.log_error(f"headcount query failed: {e}", "Neotec Insight: capture")
        out["notes"].append(_("Could not read Employee records."))
        return out

    prev_cc = _transfers_before(company, ms)
    if prev_cc:
        out["reconstructed"] = True

    wanted = {x.strip().lower() for x in (include_status or "Active").split(",") if x.strip()} \
        or {"active"}

    for r in rows:
        rel = r.get("relieving_date")
        status = (r.get("status") or "").strip()

        if rel:
            # A dated departure is the reliable case: they count in every
            # month up to the date they left, whatever their status says
            # today. This is what keeps a past month stable when someone
            # leaves later.
            if getdate(rel) < getdate(asof):
                continue
        elif status.lower() not in wanted:
            # Not a counted status and no leaving date, so there is nothing
            # to say when they stopped. Counting them would inflate every
            # month including months they may not have worked; excluding
            # them silently would hide a data gap. Excluded and listed.
            out["undated_inactive"].append({
                "id": r["name"],
                "name": r.get("employee_name") or r["name"],
                "status": status,
                "department": r.get("department") or "",
                "joined": str(r.get("date_of_joining") or ""),
            })
            continue

        cc = ""
        if cc_from == "payroll_cost_center":
            cc = prev_cc.get(r["name"]) or (r.get("payroll_cost_center") or "")
        if not cc:
            cc = dept_map.get(r.get("department") or "", "")

        member = {
            "id": r["name"],
            "name": r.get("employee_name") or r["name"],
            "department": r.get("department") or "",
            "designation": r.get("designation") or "",
            "joined": str(r.get("date_of_joining") or ""),
            "left": str(rel or ""),
            "cost_center": cc,
            "via": "transfer_replay" if r["name"] in prev_cc else cc_from,
        }
        if not cc:
            out["unassigned"].append(member)
            continue
        out["counts"][cc] = out["counts"].get(cc, 0) + 1
        out["evidence"].setdefault(cc, []).append(member)

    return out


def _lead_assignments() -> dict[str, dict]:
    """First assignment per lead, from ToDo.

    Frappe records an assignment as a ToDo against the document, so that is
    where both the fact of assignment and its date live. `lead_owner` is not
    a substitute: it defaults to whoever created the record, so on this site
    nearly every lead has one whether or not anyone was ever given it.

    The *earliest* assignment is used deliberately. Re-assigning a lead in
    May should not drag it out of the March it entered the pipeline in —
    same principle as not letting a September transfer rewrite July.

    ToDo.creation is when the assignment was made; ToDo.date is the due date
    on it, which is a different thing and would place leads in the wrong
    month.
    """
    out: dict[str, dict] = {}
    if not _has("ToDo"):
        return out
    try:
        rows = frappe.get_all(
            "ToDo",
            filters={"reference_type": "Lead", "status": ["!=", "Cancelled"]},
            fields=["reference_name", "allocated_to", "assigned_by", "creation", "status"],
            order_by="creation asc", limit_page_length=0,
        )
    except Exception as e:
        frappe.log_error(f"lead assignment lookup failed: {e}", "Neotec Insight: capture")
        return out
    for r in rows:
        ref = r.get("reference_name")
        if ref and ref not in out:      # ordered ascending, so first wins
            out[ref] = {
                "assigned_to": r.get("allocated_to") or "",
                "assigned_by": r.get("assigned_by") or "",
                "assigned_on": str(r.get("creation") or "")[:19],
                "status": r.get("status") or "",
            }
    return out


def leadcount_for_month(
    company: str,
    year: int,
    month: int,
    *,
    cc_field: str = "business_line",
    date_field: str = "assignment",
    exclude_status: str = "",
    include_disabled: bool = False,
    split_units: bool = False,
    assigned_only: bool = True,
) -> dict:
    """Leads placed in one month, grouped by cost centre, with evidence.

    Lead carries a direct Link to Cost Center — `business_line` on this site —
    so no mapping is needed and no inference is involved. `business_division`
    is fetched from it and is the same thing one level up, which is why the
    values already read as allocation columns.

    Which date counts is a real decision, not a detail: a lead received in
    March and qualified in May belongs to different months under different
    rules, and that moves real money between cost centres. It is therefore
    configured on the rule rather than assumed here.
    """
    ms, me = month_start(year, month), month_end(year, month)
    out = {"counts": {}, "evidence": {}, "unassigned": [], "reconstructed": False,
           "as_of": me, "notes": []}
    if not _has("Lead"):
        out["notes"].append(_("Lead doctype is not installed."))
        return out

    fields = ["name", "lead_name", "company_name", "status", "lead_owner", "creation"]
    for f in (cc_field, date_field, "source", "custom_business_line_", "disabled",
              "qualification_status", "quoted_value"):
        if f and f not in fields and _field("Lead", f):
            fields.append(f)

    # The chosen date may be unset on older records, so the window is widened
    # and the row filtered in Python against the effective date. Filtering in
    # SQL on a nullable custom field would silently drop exactly those rows.
    filters = {"company": company} if _field("Lead", "company") else {}
    try:
        rows = frappe.get_all("Lead", filters=filters, fields=fields, limit_page_length=0)
    except Exception as e:
        frappe.log_error(f"lead query failed: {e}", "Neotec Insight: capture")
        out["notes"].append(_("Could not read Lead records."))
        return out

    skip = {x.strip().lower() for x in (exclude_status or "").split(",") if x.strip()}
    assigns = _lead_assignments() if (assigned_only or date_field == "assignment") else {}
    unassigned_n = 0

    for r in rows:
        if not include_disabled and r.get("disabled"):
            continue
        if skip and (r.get("status") or "").lower() in skip:
            continue

        asg = assigns.get(r["name"])
        if assigned_only and not asg:
            # Never handed to anyone. It is a record, not a workload, so it
            # does not belong in a driver that spreads the cost of chasing.
            unassigned_n += 1
            continue

        if date_field == "assignment":
            raw = asg.get("assigned_on") if asg else None
        else:
            raw = r.get(date_field) or r.get("creation")
        if not raw:
            continue
        eff = str(raw)[:10]
        if not (ms <= eff <= me):
            continue

        targets = []
        if split_units and _has("Lead Business Unit"):
            try:
                for u in frappe.get_all("Lead Business Unit",
                                        filters={"parent": r["name"]},
                                        fields=["*"], limit_page_length=0):
                    cc = u.get(cc_field) or u.get("business_line") or u.get("cost_center")
                    if cc:
                        targets.append(cc)
            except Exception:
                targets = []
        if not targets:
            cc = r.get(cc_field) or ""
            targets = [cc] if cc else []

        member = {
            "id": r["name"],
            "name": r.get("company_name") or r.get("lead_name") or r["name"],
            "department": r.get("custom_business_line_") or "",
            "designation": r.get("status") or "",
            "joined": eff,
            "left": "",
            "cost_center": targets[0] if targets else "",
            "owner": (asg or {}).get("assigned_to") or r.get("lead_owner") or "",
            "assigned_on": (asg or {}).get("assigned_on") or "",
            "assigned_by": (asg or {}).get("assigned_by") or "",
            "source": r.get("source") or "",
            "via": date_field,
        }
        if not targets:
            out["unassigned"].append(member)
            continue
        for cc in targets:
            out["counts"][cc] = out["counts"].get(cc, 0) + 1
            out["evidence"].setdefault(cc, []).append({**member, "cost_center": cc})

    if assigned_only and unassigned_n:
        out["notes"].append(
            _("{0} leads skipped because nobody has been assigned to them.").format(unassigned_n))
    if out["undated_inactive"]:
        out["notes"].append(
            _("{0} employees excluded: not Active and no relieving date recorded.").format(
                len(out["undated_inactive"])))
    return out


def _evidence_blob(members: list[dict], meta: dict) -> str:
    """Compact, self-describing snapshot. Small enough to sit on the row."""
    return json.dumps({
        "captured_on": meta.get("captured_on"),
        "captured_by": meta.get("captured_by"),
        "as_of": meta.get("as_of"),
        "source": meta.get("source"),
        "count": len(members),
        "members": members,
    }, default=str)


def _rule_config(rule) -> dict:
    dept_map = {}
    for d in (getattr(rule, "dept_map", None) or []):
        if getattr(d, "department", None) and getattr(d, "cost_center", None):
            dept_map[d.department] = d.cost_center
    return {
        "as_of": getattr(rule, "count_as_of", None) or "month_end",
        "cc_from": getattr(rule, "cc_from", None) or "payroll_cost_center",
        "dept_map": dept_map,
        "include_status": getattr(rule, "include_status", None) or "Active",
    }


def _lead_config(rule) -> dict:
    return {
        "cc_field": getattr(rule, "lead_cc_field", None) or "business_line",
        "date_field": getattr(rule, "lead_date_field", None) or "assignment",
        "exclude_status": getattr(rule, "lead_exclude_status", None) or "",
        "include_disabled": bool(getattr(rule, "lead_include_disabled", 0)),
        "split_units": bool(getattr(rule, "lead_split_units", 0)),
        "assigned_only": bool(getattr(rule, "lead_assigned_only", 1)),
    }


def _count_for_month(rule, year: int, month: int) -> dict:
    """Dispatch to whichever source the rule uses."""
    src = getattr(rule, "driver_source", None) or "manual"
    if src == "crm_leads":
        return leadcount_for_month(rule.company, year, month, **_lead_config(rule))
    return headcount_for_month(rule.company, year, month, **_rule_config(rule))


def preview(rule_name: str, year: int, months: list[int] | None = None) -> dict:
    """What a capture would write, and what it would leave alone.

    Nothing is saved. Frozen months are compared rather than proposed, so a
    closed period shows drift instead of a pending change.
    """
    rule = frappe.get_doc("Insight Allocation Rule", rule_name)
    cfg = _rule_config(rule)
    company = rule.company
    months = months or list(range(1, 13))

    existing = {}
    for e in frappe.get_all(
        "Insight Allocation Entry",
        filters={"rule": rule_name,
                 "period_month": ["between", [month_start(year, 1), month_end(year, 12)]]},
        fields=["name", "cost_center", "period_month", "basis", "driver_value",
                "source", "is_override", "is_frozen", "captured_value"],
        limit_page_length=0,
    ):
        pm = e["period_month"]
        m = pm.month if hasattr(pm, "month") else int(str(pm)[5:7])
        existing[(e["cost_center"], m)] = e

    # Months already carrying values from before capture existed are the ones
    # most at risk: they were entered by hand, they have been reported, and a
    # capture that disagrees would rewrite history. Any such month that is not
    # yet frozen is flagged so the caller can lock it before running.
    unprotected = sorted({
        m for (cc, m) in existing
        if not existing[(cc, m)].get("is_frozen")
        and (existing[(cc, m)].get("source") or "manual") == "manual"
    })

    out_months = []
    for m in months:
        hc = _count_for_month(rule, year, m)
        rows = []
        seen = set(hc["counts"].keys())
        # Cost centres already on the rule but with nobody counted this month
        # still need a row, or a headcount that fell to zero would look like
        # no data rather than like zero.
        for (cc, mm) in existing:
            if mm == m and existing[(cc, mm)].get("basis") == "head_count":
                seen.add(cc)
        for cc in sorted(seen):
            ex = existing.get((cc, m))
            counted = hc["counts"].get(cc, 0)
            stored = flt(ex.get("driver_value")) if ex else None
            frozen = bool(ex and ex.get("is_frozen"))
            override = bool(ex and ex.get("is_override"))
            if frozen:
                action = "drift" if stored is not None and abs(stored - counted) > 0.0001 else "frozen"
            elif override:
                action = "kept"
            elif stored is None:
                action = "write"
            elif abs(stored - counted) > 0.0001:
                action = "update"
            else:
                action = "unchanged"
            rows.append({
                "cost_center": cc, "counted": counted, "stored": stored,
                "delta": None if stored is None else round(counted - stored, 4),
                "action": action, "frozen": frozen, "override": override,
                "members": hc["evidence"].get(cc, []),
            })
        out_months.append({
            "month": m, "as_of": hc["as_of"], "rows": rows,
            "unassigned": hc["unassigned"], "notes": hc["notes"],
            "reconstructed": hc["reconstructed"],
        })

    return {
        "rule": rule_name, "title": rule.title, "company": company, "year": year,
        "driver_source": getattr(rule, "driver_source", "manual"),
        "unprotected_manual_months": unprotected,
        "config": {k: v for k, v in cfg.items() if k != "dept_map"},
        "months": out_months,
    }


def commit(rule_name: str, year: int, months: list[int] | None = None,
           freeze: bool = False) -> dict:
    """Write what preview proposed. Never touches frozen or overridden rows."""
    rule = frappe.get_doc("Insight Allocation Rule", rule_name)
    cfg = _rule_config(rule)
    pv = preview(rule_name, year, months)
    now = frappe.utils.now()
    user = frappe.session.user
    written = skipped = drifted = 0

    for mblock in pv["months"]:
        m = mblock["month"]
        for row in mblock["rows"]:
            if row["action"] in ("frozen", "kept", "unchanged"):
                skipped += 1
                continue
            if row["action"] == "drift":
                drifted += 1
                continue
            name = frappe.db.exists("Insight Allocation Entry", {
                "rule": rule_name, "cost_center": row["cost_center"],
                "period_month": month_start(year, m)})
            doc = (frappe.get_doc("Insight Allocation Entry", name) if name
                   else frappe.new_doc("Insight Allocation Entry"))
            if not name:
                doc.rule = rule_name
                doc.cost_center = row["cost_center"]
                doc.period_month = month_start(year, m)
                doc.basis = "head_count"
            doc.company = rule.company
            doc.driver_value = row["counted"]
            doc.captured_value = row["counted"]
            doc.source = ("reconstructed" if mblock["reconstructed"]
                          else (getattr(rule, "driver_source", None) or "employee_headcount"))
            doc.captured_on = now
            doc.captured_by = user
            doc.is_override = 0
            doc.evidence_json = _evidence_blob(row["members"], {
                "captured_on": now, "captured_by": user,
                "as_of": mblock["as_of"], "source": doc.source,
            })
            if freeze:
                doc.is_frozen = 1
            doc.save()
            written += 1

    frappe.db.commit()
    return {"ok": True, "written": written, "skipped": skipped, "drifted": drifted}


def unassigned_report(rule_name: str, year: int, months: list[int] | None = None) -> dict:
    """Everyone who would fall out of the count, and what that costs.

    An employee with no cost centre does not raise an error — they simply
    leave the denominator, every remaining cost centre takes a slightly
    larger share, and the total still ties to the pool. Nothing looks wrong.
    That makes this list the single most useful pre-flight check there is, so
    it is available on its own rather than only inside a capture preview.

    `distortion_pct` is the honest way to state the impact: with 3 of 40
    people unassigned, every assigned cost centre is carrying about 8% more
    than it should.
    """
    rule = frappe.get_doc("Insight Allocation Rule", rule_name)
    cfg = _rule_config(rule)
    months = months or list(range(1, 13))

    people: dict[str, dict] = {}
    inactive: dict[str, dict] = {}
    per_month = []
    for m in months:
        hc = _count_for_month(rule, year, m)
        counted = sum(hc["counts"].values())
        missing = len(hc["unassigned"])
        total = counted + missing
        per_month.append({
            "month": m,
            "counted": counted,
            "missing": missing,
            "total": total,
            # How mucheach assigned cost centre is inflated by the absentees.
            "distortion_pct": round((missing / counted * 100.0), 2) if counted else 0.0,
        })
        for u in hc["unassigned"]:
            rec = people.setdefault(u["id"], {**u, "months": []})
            rec["months"].append(m)
        for u in hc.get("undated_inactive") or []:
            inactive.setdefault(u["id"], u)

    rows = sorted(people.values(), key=lambda r: (r.get("department") or "", r["name"]))
    worst = max((p["distortion_pct"] for p in per_month), default=0.0)
    return {
        "rule": rule_name,
        "company": rule.company,
        "year": year,
        "cc_from": cfg["cc_from"],
        "has_dept_map": bool(cfg["dept_map"]),
        "people": rows,
        "per_month": per_month,
        "total_people": len(rows),
        "worst_distortion_pct": worst,
        # Not an error, but the same class of problem: a record that cannot
        # be placed in time. Listing them is how a relieving date gets added.
        "undated_inactive": sorted(inactive.values(), key=lambda r: r["name"]),
    }
