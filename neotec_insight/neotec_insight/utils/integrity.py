"""Report Integrity & Coverage engine (v1.9.97).

Read-only. Audits a report's account mappings against the live chart of accounts
and the GL for the selected period, and returns actionable findings. This is the
capability that takes the product past plain BI: instead of only presenting
numbers, it *proves the numbers are complete* and points at the exact accounts
or rows that threaten that completeness.

Findings produced
-----------------
- coverage_gap   (high)   active accounts in the report's root types that are
                          mapped to NO row — so their balances are silently
                          missing from the statement.
- double_count   (medium) one account feeding more than one row — its amount is
                          counted more than once unless the rows' scopes differ.
- orphan_mapping (medium) a mapping whose flag is not a row in the current
                          report definition — dead mapping, included in nothing.
- empty_row      (low)    a source row that resolves to accounts but has zero GL
                          movement in the period — often a mis-mapping.
- new_member     (info)   accounts that auto-joined a live group-bound row after
                          the row was set up (companion to the run-time badge).

Nothing here writes. Every query is a SELECT.
"""
from __future__ import annotations

import json

import frappe
from frappe.utils import flt

from .execution import load_flag_to_accounts, flag_binding_meta
from .fiscal_year import resolve_date_bounds


def _account_codes(names: list[str]) -> dict[str, dict]:
    if not names:
        return {}
    out = {}
    for a in frappe.get_all(
        "Account",
        filters={"name": ["in", list(names)]},
        fields=["name", "account_number", "account_name", "root_type", "is_group"],
        limit_page_length=0,
    ):
        out[a["name"]] = a
    return out


def _code(info: dict | None, fallback: str) -> str:
    info = info or {}
    return info.get("account_number") or info.get("account_name") or fallback


def analyze_report_integrity(
    report_name: str,
    fiscal_year: int,
    month_from: int = 0,
    month_to: int = 11,
    period_mode: str = "fiscal_year",
    period_from_date: str | None = None,
    period_to_date: str | None = None,
    fy_start_month_override: int | None = None,
) -> dict:
    doc = frappe.get_doc("Insight Report Definition", report_name)
    company = doc.company
    definition = json.loads(doc.definition_json or "{}")
    rows = definition.get("rows", [])

    # Flags actually present as rows in the current definition (source rows).
    row_flags = set()
    source_row_by_flag: dict[str, dict] = {}
    for r in rows:
        if r.get("kind") == "source":
            f = (r.get("flag") or r.get("label") or "").strip()
            if f:
                row_flags.add(f)
                source_row_by_flag.setdefault(f, r)

    flag_to_accounts = load_flag_to_accounts(report_name)
    mapped_accounts = {a for accs in flag_to_accounts.values() for a in accs}

    # Determine the report's covered root types from what's already mapped, so the
    # coverage check adapts to a P&L (Income/Expense) vs a Balance Sheet
    # (Asset/Liability/Equity) without being told which it is.
    mapped_info = _account_codes(list(mapped_accounts))
    covered_root_types = sorted({i.get("root_type") for i in mapped_info.values() if i.get("root_type")})

    # The universe: every active leaf account of those root types for the company.
    universe_info: dict[str, dict] = {}
    if covered_root_types:
        for a in frappe.get_all(
            "Account",
            filters={
                "company": company,
                "is_group": 0,
                "disabled": 0,
                "root_type": ["in", covered_root_types],
            },
            fields=["name", "account_number", "account_name", "root_type"],
            limit_page_length=0,
        ):
            universe_info[a["name"]] = a

    # One GL aggregate over the union of (universe ∪ mapped), for the period.
    union = list(set(universe_info.keys()) | mapped_accounts)
    net_by_acct: dict[str, float] = {}
    move_by_acct: dict[str, float] = {}
    if union:
        from frappe.query_builder import DocType
        from frappe.query_builder.functions import Sum

        gl = DocType("GL Entry")
        date_start, date_end = resolve_date_bounds(
            company, fiscal_year, month_from, month_to,
            fy_start_month_override=fy_start_month_override,
            period_mode=period_mode,
            period_from_date=period_from_date,
            period_to_date=period_to_date,
        )
        # Chunk the IN() list to stay well under any param limits on large charts.
        for i in range(0, len(union), 900):
            chunk = union[i:i + 900]
            q = (
                frappe.qb.from_(gl)
                .select(
                    gl.account.as_("account"),
                    Sum(gl.credit - gl.debit).as_("net"),
                    Sum(gl.debit + gl.credit).as_("movement"),
                )
                .where(gl.is_cancelled == 0)
                .where(gl.account.isin(chunk))
                .where(gl.posting_date.between(date_start, date_end))
                .groupby(gl.account)
            )
            if company:
                q = q.where(gl.company == company)
            for r in q.run(as_dict=True):
                net_by_acct[r["account"]] = flt(r.get("net"))
                move_by_acct[r["account"]] = flt(r.get("movement"))

    def _active(acct: str) -> bool:
        return abs(move_by_acct.get(acct, 0.0)) > 0.005

    findings: list[dict] = []

    # ── coverage_gap ────────────────────────────────────────────────────────
    gap_items = []
    for acct, info in universe_info.items():
        if acct in mapped_accounts:
            continue
        if not _active(acct):
            continue
        gap_items.append({
            "account": acct,
            "code": _code(info, acct),
            "name": info.get("account_name") or acct,
            "root_type": info.get("root_type") or "",
            "amount": round(net_by_acct.get(acct, 0.0), 2),
        })
    gap_items.sort(key=lambda x: abs(x["amount"]), reverse=True)

    # Coverage headline.
    active_universe = [a for a in universe_info if _active(a)]
    total_active = len(active_universe)
    covered_active = len([a for a in active_universe if a in mapped_accounts])
    coverage_pct = round(100.0 * covered_active / total_active, 1) if total_active else 100.0

    if gap_items:
        findings.append({
            "id": "coverage_gap",
            "severity": "high",
            "title": "Unmapped accounts with activity",
            "detail": (
                f"{len(gap_items)} account(s) of type "
                f"{', '.join(covered_root_types)} have GL activity this period but "
                "are not mapped to any report row, so their balances are missing "
                "from the statement."
            ),
            "fix": "Open the Map tab and assign each account to a row (or bind its parent group).",
            "count": len(gap_items),
            "items": gap_items,
        })

    # ── double_count ────────────────────────────────────────────────────────
    maps = frappe.get_all(
        "Account Flag Mapping",
        filters={"report": report_name},
        fields=["account", "flag", "is_group_binding", "dimension_filters_json"],
        limit_page_length=0,
    )
    flags_by_acct: dict[str, list[dict]] = {}
    for m in maps:
        flags_by_acct.setdefault(m["account"], []).append(m)
    dup_items = []
    for acct, ms in flags_by_acct.items():
        distinct_flags = sorted({(m.get("flag") or "").strip() for m in ms if m.get("flag")})
        if len(distinct_flags) > 1:
            # If every binding carries a non-empty, distinct dimension scope the
            # double-count may be intentional; flag it but mark scoped=True.
            scoped = all((m.get("dimension_filters_json") or "").strip() not in ("", "[]", "null") for m in ms)
            info = _account_codes([acct]).get(acct)
            dup_items.append({
                "account": acct,
                "code": _code(info, acct),
                "name": (info or {}).get("account_name") or acct,
                "flags": distinct_flags,
                "scoped": scoped,
            })
    if dup_items:
        unscoped = [d for d in dup_items if not d["scoped"]]
        findings.append({
            "id": "double_count",
            "severity": "medium" if unscoped else "low",
            "title": "Accounts feeding more than one row",
            "detail": (
                f"{len(dup_items)} account(s) are bound to multiple rows. Unless the "
                "rows carry non-overlapping dimension scopes, the amount is counted "
                "more than once."
            ),
            "fix": "Give each row a distinct dimension scope, or remove the extra binding.",
            "count": len(dup_items),
            "items": dup_items,
        })

    # ── orphan_mapping ──────────────────────────────────────────────────────
    orphan_items = []
    seen = set()
    for m in maps:
        f = (m.get("flag") or "").strip()
        if f and f not in row_flags and f not in seen:
            seen.add(f)
            cnt = len([x for x in maps if (x.get("flag") or "").strip() == f])
            orphan_items.append({"flag": f, "mappings": cnt})
    if orphan_items:
        findings.append({
            "id": "orphan_mapping",
            "severity": "medium",
            "title": "Mappings pointing at no row",
            "detail": (
                f"{len(orphan_items)} flag(s) are mapped to accounts but have no "
                "matching row in this report — those accounts contribute to nothing."
            ),
            "fix": "Add a source row for the flag, re-point the mappings, or delete them.",
            "count": len(orphan_items),
            "items": orphan_items,
        })

    # ── empty_row ───────────────────────────────────────────────────────────
    empty_items = []
    for flag, accts in flag_to_accounts.items():
        if flag not in row_flags or not accts:
            continue
        movement = sum(abs(move_by_acct.get(a, 0.0)) for a in accts)
        if movement <= 0.005:
            srow = source_row_by_flag.get(flag) or {}
            empty_items.append({
                "flag": flag,
                "row_label": srow.get("label") or flag,
                "resolved_count": len(accts),
            })
    if empty_items:
        findings.append({
            "id": "empty_row",
            "severity": "low",
            "title": "Rows with accounts but no activity",
            "detail": (
                f"{len(empty_items)} row(s) resolve to accounts that had no GL "
                "movement this period. Expected for some lines; a surprise here "
                "usually means the wrong accounts are mapped."
            ),
            "fix": "Confirm the row's accounts are the intended ones on the Map tab.",
            "count": len(empty_items),
            "items": empty_items,
        })

    # ── new_member (companion to the run-time badge) ────────────────────────
    meta = flag_binding_meta(report_name, flag_to_accounts)
    new_items = []
    for flag, mm in meta.items():
        if flag in row_flags and mm.get("new_count"):
            srow = source_row_by_flag.get(flag) or {}
            new_items.append({
                "flag": flag,
                "row_label": srow.get("label") or flag,
                "new_count": mm["new_count"],
                "new_accounts": mm.get("new_accounts") or [],
            })
    if new_items:
        total_new = sum(x["new_count"] for x in new_items)
        findings.append({
            "id": "new_member",
            "severity": "info",
            "title": "Accounts auto-joined since setup",
            "detail": (
                f"{total_new} account(s) have flowed into live group-bound rows since "
                "those rows were configured. Included automatically — review that they "
                "belong."
            ),
            "fix": "Verify the new accounts belong on the row; no action needed if they do.",
            "count": total_new,
            "items": new_items,
        })

    severity_rank = {"high": 0, "medium": 1, "low": 2, "info": 3}
    findings.sort(key=lambda f: severity_rank.get(f["severity"], 9))
    by_sev: dict[str, int] = {}
    for f in findings:
        by_sev[f["severity"]] = by_sev.get(f["severity"], 0) + 1

    return {
        "report": {"name": doc.name, "report_name": doc.report_name},
        "company": company,
        "period": {
            "fiscal_year": fiscal_year, "month_from": month_from, "month_to": month_to,
            "period_mode": period_mode, "period_from_date": period_from_date, "period_to_date": period_to_date,
        },
        "coverage": {
            "covered_root_types": covered_root_types,
            "total_active_accounts": total_active,
            "covered_active_accounts": covered_active,
            "coverage_pct": coverage_pct,
            "unmapped_active_accounts": total_active - covered_active,
        },
        "summary": {"total_findings": len(findings), "by_severity": by_sev},
        "findings": findings,
    }
