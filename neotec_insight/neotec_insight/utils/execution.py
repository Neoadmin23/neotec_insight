from __future__ import annotations

import json
from typing import Any

import frappe
from frappe.utils import flt

from neotec_insight.neotec_insight.utils.formula import evaluate_row_formula

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


_IE_CACHE: dict = {}


def _income_expense_accounts(company):
    key = company or "__all__"
    if key not in _IE_CACHE:
        flt = {"root_type": ["in", ["Income", "Expense"]], "is_group": 0}
        if company:
            flt["company"] = company
        _IE_CACHE[key] = frappe.get_all("Account", filters=flt, pluck="name", limit_page_length=0)
    return _IE_CACHE[key]


def execute_report(
    *,
    report_def: dict,
    fiscal_year: int,
    month_from: int,
    month_to: int,
    segment: str = "total",
    cost_center: str | list | None = None,
    project: str | list | None = None,
    department: str | list | None = None,
    branch: str | list | None = None,
    company: str | None = None,
    account_to_flag: dict[str, str] | None = None,
    flag_to_accounts: dict[str, list[str]] | None = None,
    flag_to_mappings: dict[str, list[dict]] | None = None,
    dimension_filters: dict | None = None,
    fy_start_month_override: int | None = None,
    # v1.9.65 — date-range mode plumbing.
    period_mode: str = "fiscal_year",
    period_from_date: str | None = None,
    period_to_date: str | None = None,
) -> dict[str, Any]:
    """Run a report for one fiscal year.

    Returns {rows: [{key, kind, label, monthly: {0..11: float}}, ...]}.

    For each source row we look up the bound accounts (from Account Flag
    Mapping under this report, matched by flag) and run a SUM(credit - debit)
    GROUP BY MONTH against GL Entry.

    `flag_to_mappings` (v1.9.5) carries per-mapping-row dimension scopes — when
    supplied it takes precedence over `flag_to_accounts`, so the same account
    can feed different rows scoped to different departments / cost centers /
    projects. `flag_to_accounts` is kept for backward compatibility.

    `dimension_filters` (v1.9.52) is an optional dict of GL Entry column → value
    for ARBITRARY accounting dimensions (custom ones like vehicle, region,
    salesperson). Every column name must already have been validated against
    the discovered dimension set by the caller — execution.py treats keys as
    trusted and uses them directly in WHERE clauses, guarded by a table-column
    check so unknown columns silently no-op rather than throw SQL errors.
    """
    rows = report_def.get("rows", [])
    flag_to_accounts = flag_to_accounts or {}
    flag_to_mappings = flag_to_mappings or {}
    dimension_filters = dimension_filters or {}

    # v1.9.58 — accept both scalar (legacy single-select) and list
    # (multi-select) for the native dimension params. Normalise once at the
    # boundary so all downstream code can assume "list of strings, or None
    # for no filter."
    def _norm(v):
        if v is None or v == "":
            return None
        if isinstance(v, list):
            cleaned = [str(x).strip() for x in v if x is not None and str(x).strip()]
            return cleaned or None
        s = str(v).strip()
        return [s] if s else None

    cost_center = _norm(cost_center)
    project = _norm(project)
    department = _norm(department)
    branch = _norm(branch)

    months = list(range(month_from, month_to + 1))
    ctx: dict[str, dict[int, float]] = {}
    out: list[dict] = []

    for row in rows:
        kind = row.get("kind")
        key = row.get("key")
        monthly: dict[int, float] = {m: 0.0 for m in months}
        # Default: this row's visibility is governed purely by show_when /
        # single-cost-centre-selected, same as every kind before v2.76.1.
        # Only the allocation branch below can set this False, when a single
        # cost centre IS selected and this rule's pool does not touch it.
        cc_applies = True

        if kind == "source":
            flag = row.get("flag") or row.get("label")
            accounts = flag_to_accounts.get(flag, [])
            if accounts:
                monthly = _fetch_monthly_for_accounts(
                    accounts=accounts,
                    fiscal_year=fiscal_year,
                    months=months,
                    cost_center=cost_center,
                    project=project,
                    department=department,
                    branch=branch,
                    company=company,
                    row_scope=row.get("dimension_scope"),
                    dimension_filters=dimension_filters,
                    fy_start_month_override=fy_start_month_override,
                    period_mode=period_mode,
                    period_from_date=period_from_date,
                    period_to_date=period_to_date,
                )
                if row.get("sign") == "invert":
                    monthly = {m: -v for m, v in monthly.items()}
            ctx[key] = monthly
        elif kind == "allocation":
            # v2.58.0 — a row that pulls its monthly figures from an
            # Allocation Rule. It lands in `ctx` exactly like a flag-sourced
            # row, so a formula row can reference it by key with no special
            # syntax: the P&L's "GMO Allocation" line is just another term.
            #
            # The pool is taken from this report's own flag map, so the
            # allocation and the expense lines it spreads can never be
            # reading different accounts.
            monthly, cc_applies = _allocation_monthly(
                rule_name=row.get("allocation_rule"),
                fiscal_year=fiscal_year,
                months=months,
                company=company,
                cost_center=_single_cost_center(cost_center),
                flag_to_accounts=flag_to_accounts,
                fy_start_month_override=fy_start_month_override,
                period_mode=period_mode,
                period_from_date=period_from_date,
                period_to_date=period_to_date,
            )
            if row.get("sign") == "invert":
                monthly = {m: -v for m, v in monthly.items()}
            ctx[key] = monthly
            # v2.62.1 — an allocation row is meaningful for one cost centre.
            # Run consolidated, it would show the whole pool sitting beside
            # expenses that already contain it, which reads as a real charge
            # and double-counts to anyone scanning the column. Default is to
            # hide it unless a single cost centre is selected; the value still
            # lands in `ctx`, so formulas referencing it keep working either
            # way and only the display is suppressed.
            #
        elif kind == "formula":
            formula = row.get("formula") or "0"
            for m in months:
                sub = {k: v.get(m, 0.0) for k, v in ctx.items()}
                try:
                    monthly[m] = evaluate_row_formula(formula, sub)
                except Exception:
                    monthly[m] = 0.0
            ctx[key] = monthly
        else:
            ctx[key] = monthly

        # ── Visibility (v2.76.0) ──────────────────────────────────────────
        # Applies to EVERY row kind, not only allocation.
        #
        # The case that needs it: with credit-back on, an allocation moves cost
        # between cost centres and leaves the company total unchanged. Run
        # consolidated, the allocation rows hide themselves and the report
        # prints "before allocation" and "after allocation" as the same figure,
        # with the allocation that explains the gap invisible between them.
        # Arithmetically right, reads as a mistake.
        #
        # THE DEFAULT DIFFERS BY KIND AND MUST. Allocation rows keep defaulting
        # to 'cost_center' (a pool shown consolidated reads as a real charge and
        # double-counts); every other kind defaults to 'always'. A shared
        # default would blank rows in every existing report on upgrade — the
        # exact failure this shipped with once before, so
        # tests/test_visibility.py asserts an untouched report keeps all its
        # rows consolidated.
        #
        # Hidden rows still land in `ctx`, so formulas referencing them keep
        # working; only the display is suppressed.
        #
        # v2.76.1 — `cc_applies` additionally hides an allocation row when a
        # single cost centre IS selected but that centre is not one this
        # rule's pool touches at all (not a driver, not the credit target).
        # Two unrelated allocation rows both defaulting to "cost_center"
        # visibility used to print side by side as identical bare 0.000s —
        # correct numbers, indistinguishable from each other and from a
        # relevant row also reading zero that month. Only refines the
        # already-hidden-unless-selected default; an explicit "Always" is
        # left alone; deliberately unselected cost centres.
        if is_row_hidden(row, kind, _single_cost_center(cost_center), cc_applies):
            row = {**row, "hidden": 1}
            # v2.81.0 — 'cost_center_exclude' additionally removes the row from
            # the ARITHMETIC, not just the display.
            #
            # The distinction is real. 'cost_center' hides a line while its
            # value keeps feeding every formula referencing it, which is right
            # for a before/after-allocation line: the reader should not see a
            # duplicated figure, but net income must still be computed from it.
            # It is wrong for a line that only means something for one cost
            # centre — there, a hidden row silently inflating a total is the
            # worst of both, because the evidence for the total is invisible.
            #
            # Zeroing `ctx` rather than deleting the key on purpose: a formula
            # naming a missing key raises and takes down the report, whereas a
            # zero contributes nothing and leaves every other row computable.
            if row.get("show_when") == "cost_center_exclude":
                ctx[key] = {m: 0.0 for m in months}
                monthly = {m: 0.0 for m in months}
                row = {**row, "excluded_from_totals": 1}

        out.append({**row, "monthly": monthly})

    return {"rows": out, "months": months}



def is_row_hidden(row: dict, kind: str, single_cost_center, cc_applies: bool = True) -> bool:
    """Whether this row is suppressed for the current cost-centre selection.

    Pulled out of the row loop so it can be tested without a site. It decides
    what a reader of a management P&L does and does not see, and it has already
    shipped wrong once — the check was applied to every kind with a shared
    default, which blanked rows in existing reports.

    An explicit `show_when` always wins. Absent, allocation rows default to
    'cost_center' and every other kind to 'always', which is what makes an
    untouched report render identically before and after upgrade.

    `cc_applies` (v2.76.1, default True so every caller before this version
    behaves exactly as before) only ever REFINES a row that is already
    subject to the cost-centre rule — it can turn a visible allocation row
    invisible when a single cost centre is selected but this rule's pool
    doesn't touch it, never the reverse. An explicit "Always" is left alone
    on purpose: `cc_applies` narrows the default, it does not override a
    deliberate choice the row's owner made.
    """
    explicit = row.get("show_when")
    # 'cost_center_exclude' hides on the same condition as 'cost_center'; it
    # differs only in what happens to the value, which the caller handles.
    if explicit == "cost_center_exclude":
        explicit = "cost_center"
    # Membership-tested as a string: an unhashable value in a stored definition
    # (a list or dict, however it got there) would otherwise raise TypeError and
    # take down the whole report run rather than this one row.
    if not isinstance(explicit, str) or explicit not in {"cost_center", "always"}:
        explicit = "cost_center" if kind == "allocation" else "always"
    if explicit == "always":
        return False
    if not single_cost_center:
        return True
    # cc_applies is a signal computed only for allocation rows (see
    # _allocation_monthly): whether THIS rule's pool touches the selected
    # cost centre at all. It must never affect a source/formula row that a
    # user explicitly set to 'cost_center' — that setting means exactly what
    # it always meant, unrelated to any allocation rule.
    return kind == "allocation" and not cc_applies


def _single_cost_center(cost_center) -> str | None:
    """The one cost centre in play, or None for consolidated/multi-select."""
    if isinstance(cost_center, str) and cost_center.strip():
        return cost_center.strip()
    if isinstance(cost_center, list) and len(cost_center) == 1:
        return str(cost_center[0])
    return None


def _allocation_monthly(
    *,
    rule_name: str | None,
    fiscal_year: int,
    months: list[int],
    company: str | None,
    cost_center: str | None,
    flag_to_accounts: dict[str, list[str]] | None,
    fy_start_month_override=None,
    period_mode=None,
    period_from_date=None,
    period_to_date=None,
) -> tuple[dict[int, float], bool]:
    """Monthly allocation for one rule, from the point of view of the
    report's current cost centre filter.

    With a cost centre selected this is that centre's share. With none it is
    the whole pool being spread — a consolidated statement should show the
    total under allocation, not a blank.

    Returns `(monthly, applies)`. `applies` is False only when a single cost
    centre IS selected and that centre is not one this rule's pool actually
    touches (not a driver cost centre and not the credit-back target) — the
    case that made two unrelated allocation rows print the identical bare
    0.000 side by side and read as the same figure rather than as "this pool
    has nothing to do with the cost centre you picked." Consolidated (no
    single cost centre) always applies: showing the whole pool is the point.

    Never raises: a missing or misconfigured rule yields zeros with
    `applies=True` — fail open, so a broken rule's row still surfaces
    (logged separately) rather than silently vanishing from the statement.
    """
    zero = {m: 0.0 for m in months}
    if not rule_name:
        return zero, True
    # While a pool is being read from this very report, allocation rows must
    # evaluate to zero — otherwise the pool would contain the allocations it
    # is meant to produce, and the report would recurse into itself.
    if frappe.flags.get("ni_allocation_pool_inflight"):
        return zero, True
    try:
        from neotec_insight.neotec_insight.utils.allocation import (
            allocation_for_cost_center, compute,
        )
        from neotec_insight.neotec_insight.utils.fiscal_year import (
            calendar_month_for_fy_month, get_company_fy_start_month,
        )

        rule = frappe.get_doc("Insight Allocation Rule", rule_name)
        co = company or rule.company

        # ── Month bases ────────────────────────────────────────────────
        # `months` here are 0-based indices in fiscal-year order. Allocation
        # entries are stored against real dates, so they are keyed by
        # calendar month. Feeding fiscal indices straight into compute()
        # looked up month 0 (which never exists, hence a blank January) and
        # shifted every other figure a month late.
        #
        # For a January-start company the two bases differ by one; for an
        # April-start company they differ by three and cross a year boundary,
        # so the calendar *year* has to travel with the month.
        fy_start = get_company_fy_start_month(co, override=fy_start_month_override)
        cal_of: dict[int, tuple[int, int]] = {}
        for idx in months:
            cal_of[idx] = (
                int(fiscal_year) + ((fy_start - 1 + int(idx)) // 12),
                calendar_month_for_fy_month(co, int(idx), fy_start_month_override),
            )

        pool_by_month = None
        if (rule.pool_mode or "report_row") == "report_row":
            from neotec_insight.neotec_insight.api.allocation import _pool_from_report_row
            pool_by_month = _pool_from_report_row(rule, co, int(fiscal_year))
        elif (rule.pool_mode or "") == "flag" and rule.pool_flag:
            accounts = (flag_to_accounts or {}).get(rule.pool_flag) or []
            if accounts:
                raw = _fetch_monthly_for_accounts(
                    accounts=accounts, fiscal_year=fiscal_year, months=months,
                    cost_center=None, project=None, department=None, branch=None,
                    company=co, row_scope=None, dimension_filters=None,
                    fy_start_month_override=fy_start_month_override,
                    period_mode=period_mode,
                    period_from_date=period_from_date,
                    period_to_date=period_to_date,
                )
                if (rule.pool_sign or "normal") == "invert":
                    raw = {m: -v for m, v in raw.items()}
                # Same translation: this returns fiscal indices too.
                pool_by_month = {cal_of[i][1]: v for i, v in raw.items() if i in cal_of}

        # compute() works one calendar year at a time. A fiscal year that
        # straddles two calendar years therefore needs both, merged back onto
        # the fiscal index the report is asking for.
        by_year: dict[int, list[int]] = {}
        for idx, (cy, cm) in cal_of.items():
            by_year.setdefault(cy, []).append(cm)

        out = dict(zero)
        applies = not cost_center  # consolidated always "applies"
        for cy, cal_months in by_year.items():
            res = compute(rule_name, co, cy,
                          pool_by_month=pool_by_month, months=sorted(set(cal_months)))
            if cost_center and cost_center in (res.get("cost_centers") or []):
                applies = True
            got = allocation_for_cost_center(res, cost_center)
            for idx, (idx_year, idx_month) in cal_of.items():
                if idx_year == cy:
                    out[idx] = float(got.get(idx_month, 0.0))

        if not cost_center:
            # Consolidated: the credit nets the charges to zero, which would
            # read as "no allocation happened". Show the pool being spread.
            for idx, (cy, cm) in cal_of.items():
                blk = None
                try:
                    blk = compute(rule_name, co, cy, pool_by_month=pool_by_month,
                                  months=[cm])["by_month"].get(cm)
                except Exception:
                    blk = None
                if blk:
                    out[idx] = float(blk.get("charged", 0.0))
        return out, applies
    except Exception as e:
        frappe.log_error(f"allocation row failed for {rule_name}: {e}",
                         "Neotec Insight: allocation")
        return zero, True


def _fetch_monthly_for_mappings(
    *,
    mappings: list[dict],
    fiscal_year: int,
    months: list[int],
    cost_center: list | str | None,
    project: list | str | None,
    department: list | str | None,
    branch: list | str | None,
    company: str | None,
    fy_start_month_override: int | None = None,
) -> dict[int, float]:
    """Sum GL activity for a flag whose mapping rows may each carry their own
    per-row dimension scope.

    `mappings` is a list of {account, dimension_filters} where dimension_filters
    is a list of {dimension_type, dimension_value} pairs (possibly empty).

    Rows are grouped by their filter signature so accounts sharing the same
    scope still resolve in one query — then one query runs per distinct scope.
    Results are summed into a single monthly map for the flag.

    The report-level cost_center / project / department filters (from the Run
    tab) are ANDed on top of every query, so a user can still narrow the whole
    report while per-row scopes segregate the accounts.
    """
    from frappe.query_builder import DocType
    from frappe.query_builder.functions import Sum, Extract

    # Map a dimension_type label to the GL Entry column. Built-ins plus any
    # custom Accounting Dimension (column = doctype name slugified).
    def _col_for(dim_type: str) -> str | None:
        t = (dim_type or "").strip().lower()
        if t in ("cost center", "cost_center"):
            return "cost_center"
        if t == "project":
            return "project"
        if t == "department":
            return "department"
        # Custom dimension — slugify the type name.
        slug = t.replace(" ", "_")
        return slug or None

    # Group mapping rows by filter signature.
    groups: dict[str, dict] = {}
    for m in mappings:
        acct = m.get("account")
        if not acct:
            continue
        filters = m.get("dimension_filters") or []
        # Canonical signature: sorted (col, value) pairs.
        norm: list[tuple[str, str]] = []
        for f in filters:
            col = _col_for(f.get("dimension_type"))
            val = (f.get("dimension_value") or "").strip()
            if col and val:
                norm.append((col, val))
        norm.sort()
        sig = json.dumps(norm)
        g = groups.setdefault(sig, {"filters": norm, "accounts": []})
        g["accounts"].append(acct)

    gl_cols_cache: set[str] | None = None
    def _gl_has(col: str) -> bool:
        nonlocal gl_cols_cache
        if gl_cols_cache is None:
            try:
                gl_cols_cache = set(frappe.db.get_table_columns("GL Entry"))
            except Exception:
                gl_cols_cache = set()
        return col in gl_cols_cache

    monthly = {m: 0.0 for m in months}
    for g in groups.values():
        accounts = g["accounts"]
        if not accounts:
            continue
        gl = DocType("GL Entry")
        # v1.9.59 — date filter is now absolute BETWEEN(start, end), derived
        # from the company's fiscal year boundaries. The `months` list is
        # FY-month indices (0..11 in FY order). We compute the calendar
        # date range for those FY months and let SQL filter on absolute
        # date. The bucketing back to FY-month happens in Python below.
        from neotec_insight.neotec_insight.utils.fiscal_year import (
            fy_month_range_to_date_range,
            fy_month_for_calendar_month,
        )
        date_start, date_end = fy_month_range_to_date_range(
            company, fiscal_year, min(months), max(months),
            fy_start_month_override=fy_start_month_override,
        )
        # v2.43.0 — closed fiscal years: the Period Closing Voucher reverses
        # every P&L account into retained earnings on the last day, so a
        # full-year sum telescopes to ~0 (the Years-tab zeros). Exclude PCV
        # rows on Income/Expense accounts — exactly what ERPNext's own
        # financial statements do. Balance-sheet accounts keep PCV lines
        # (retained earnings must include them).
        _ie = _income_expense_accounts(company)
        q = (
            frappe.qb.from_(gl)
            .select(
                Extract("MONTH", gl.posting_date).as_("cal_month"),
                Sum(gl.credit - gl.debit).as_("amount"),
            )
            .where(gl.is_cancelled == 0)
            .where(~((gl.voucher_type == "Period Closing Voucher") & (gl.account.isin(_ie or ["__none__"]))))
            .where(gl.account.isin(accounts))
            .where(gl.posting_date.between(date_start, date_end))
            .groupby(Extract("MONTH", gl.posting_date))
        )
        # Per-row dimension scope.
        for (col, val) in g["filters"]:
            if not _gl_has(col):
                # Dimension column not present on this bench — skip the filter
                # rather than crash. The row simply won't be scoped.
                continue
            q = q.where(gl[col] == val)
        # Report-level filters on top.
        # v1.9.58 — each native dim is a list (or None) after normalisation
        # in execute_report. Use .isin() so multi-select expands to IN(...).
        # Single-element lists work identically to scalar equality.
        def _apply(qq, col_attr, vals):
            if vals is None:
                return qq
            if isinstance(vals, list):
                if not vals:
                    return qq
                return qq.where(col_attr.isin(vals))
            return qq.where(col_attr == vals)
        q = _apply(q, gl.cost_center, cost_center)
        q = _apply(q, gl.project, project)
        q = _apply(q, gl.department, department)
        q = _apply(q, gl.branch, branch)
        if company:
            q = q.where(gl.company == company)

        for row in q.run(as_dict=True):
            # v1.9.59 — cal_month is 1..12 from SQL. Convert to FY-month
            # index (0..11) per the company's fiscal year orientation.
            cal_m = int(row.get("cal_month") or 0)
            if cal_m < 1 or cal_m > 12:
                continue
            idx = fy_month_for_calendar_month(company, cal_m, fy_start_month_override=fy_start_month_override)
            if idx in monthly:
                monthly[idx] = monthly[idx] + flt(row.get("amount") or 0.0)
    return monthly


def _fetch_monthly_for_accounts(
    *,
    accounts: list[str],
    fiscal_year: int,
    months: list[int],
    cost_center: str | None,
    project: list | str | None,
    department: list | str | None,
    branch: list | str | None,
    company: str | None,
    row_scope: dict | None = None,
    dimension_filters: dict | None = None,
    fy_start_month_override: int | None = None,
    period_mode: str = "fiscal_year",
    period_from_date: str | None = None,
    period_to_date: str | None = None,
) -> dict[int, float]:
    """One SUM/GROUP BY query against GL Entry, returns {fy_month_idx: amount}.

    v1.9.59 — fy_month_idx is 0-indexed in the company's fiscal year order:
    0 = first month of the company's FY (e.g. April for an Indian co),
    11 = last month. NOT calendar month. The SQL filter uses an absolute
    posting_date BETWEEN range derived from the company's fiscal year
    boundaries, then results are bucketed by FY position in Python.

    `row_scope` (v1.9.6) is the source row's optional dimension scope:
    {"dimension_type": "Department", "dimension_values": ["MSSP", "MDR"]}.
    When present, the query is filtered to that dimension IN those values —
    OR within one dimension — so a single row can club several departments.

    `dimension_filters` (v1.9.52) is a dict of GL Entry column → value for
    arbitrary custom accounting dimensions. v1.9.58: value may also be a
    list, in which case the query uses an IN(...) clause. Each key must
    already be a validated, safe column name (the API layer sanitises).
    Unknown columns are silently dropped here via a table-column existence
    check, defensive against a stale validation set vs a recently-changed
    schema.

    v1.9.65 — period_mode='date_range' uses period_from_date/period_to_date
    directly instead of deriving from FY. Bucketing into FY months still
    happens but the resulting layout may have months outside the picked
    span (which will be zero). For pure date-range queries the caller
    should ignore the per-month structure and sum the values.
    """
    from frappe.query_builder import DocType
    from frappe.query_builder.functions import Sum, Extract
    from neotec_insight.neotec_insight.utils.fiscal_year import (
        resolve_date_bounds,
        fy_month_for_calendar_month,
    )

    gl = DocType("GL Entry")
    # v1.9.65 — single resolver respects period_mode.
    date_start, date_end = resolve_date_bounds(
        company, fiscal_year, min(months), max(months),
        fy_start_month_override=fy_start_month_override,
        period_mode=period_mode,
        period_from_date=period_from_date,
        period_to_date=period_to_date,
    )
    _ie = _income_expense_accounts(company)
    q = (
        frappe.qb.from_(gl)
        .select(
            Extract("MONTH", gl.posting_date).as_("cal_month"),
            Sum(gl.credit - gl.debit).as_("amount"),
        )
        .where(gl.is_cancelled == 0)
        # v2.43.1 — PCV exclusion on the MAIN sums path too (v2.43.0 patched
        # only the first query block; this one feeds priors and totals).
        .where(~((gl.voucher_type == "Period Closing Voucher") & (gl.account.isin(_ie or ["__none__"]))))
        .where(gl.account.isin(accounts))
        .where(gl.posting_date.between(date_start, date_end))
        .groupby(Extract("MONTH", gl.posting_date))
    )

    # v1.9.58 — native dim filters: accept scalar (legacy) or list (multi).
    def _apply(qq, col_attr, vals):
        if vals is None:
            return qq
        if isinstance(vals, list):
            if not vals:
                return qq
            # v1.9.66 — "__BLANK__" sentinel = entries with NO value for this
            # dimension (NULL or empty). Combine with any real values via OR.
            real = [v for v in vals if str(v).strip() != "__BLANK__"]
            include_blank = len(real) != len(vals)
            if include_blank and real:
                return qq.where(col_attr.isin(real) | col_attr.isnull() | (col_attr == ""))
            if include_blank:
                return qq.where(col_attr.isnull() | (col_attr == ""))
            return qq.where(col_attr.isin(real))
        if vals == "":
            return qq
        return qq.where(col_attr == vals)
    q = _apply(q, gl.cost_center, cost_center)
    q = _apply(q, gl.project, project)
    q = _apply(q, gl.department, department)
    q = _apply(q, gl.branch, branch)
    if company:
        q = q.where(gl.company == company)

    # v1.9.52 + v1.9.58 — custom accounting-dimension filters. Values may
    # be scalar (legacy) or list (multi-select). Defensive table-column
    # check before applying.
    if dimension_filters:
        try:
            gl_cols_existing = set(frappe.db.get_table_columns("GL Entry"))
        except Exception:
            gl_cols_existing = set()
        for fld, val in dimension_filters.items():
            if val is None or val == "" or val == []:
                continue
            if fld in ("cost_center", "project", "department", "branch", "company"):
                continue  # natives already handled
            if fld not in gl_cols_existing:
                continue
            if isinstance(val, list):
                if not val:
                    continue
                q = q.where(gl[fld].isin(val))
            else:
                q = q.where(gl[fld] == val)

    # Row-level dimension scope: dimension IN (values).
    if row_scope and isinstance(row_scope, dict):
        dim_type = (row_scope.get("dimension_type") or "").strip().lower()
        raw_vals = row_scope.get("dimension_values") or []
        # v1.9.65: a "__BLANK__" sentinel means "entries with NO value for this
        # dimension" (NULL or empty) — lets a row capture un-tagged postings.
        include_blank = any(str(v).strip() == "__BLANK__" for v in raw_vals)
        dim_values = [v for v in raw_vals if v and str(v).strip() != "__BLANK__"]
        if dim_type and (dim_values or include_blank):
            col = {
                "department": "department",
                "cost center": "cost_center",
                "cost_center": "cost_center",
                "project": "project",
            }.get(dim_type, dim_type.replace(" ", "_"))
            # Guard: only filter on a column that actually exists on GL Entry.
            try:
                gl_cols = set(frappe.db.get_table_columns("GL Entry"))
            except Exception:
                gl_cols = set()
            if col in gl_cols:
                if include_blank and dim_values:
                    q = q.where(gl[col].isin(dim_values) | gl[col].isnull() | (gl[col] == ""))
                elif include_blank:
                    q = q.where(gl[col].isnull() | (gl[col] == ""))
                else:
                    q = q.where(gl[col].isin(dim_values))

    monthly = {m: 0.0 for m in months}
    for row in q.run(as_dict=True):
        # v1.9.59 — bucket by FY-month, not calendar month.
        cal_m = int(row.get("cal_month") or 0)
        if cal_m < 1 or cal_m > 12:
            continue
        idx = fy_month_for_calendar_month(company, cal_m, fy_start_month_override=fy_start_month_override)
        if idx in monthly:
            monthly[idx] = monthly[idx] + flt(row.get("amount") or 0.0)
    return monthly


def load_flag_to_accounts(report_name: str) -> dict[str, list[str]]:
    """Build a {flag: [leaf_account, ...]} lookup from Account Flag Mapping.

    Group bindings (is_group_binding=1) are expanded to all leaf accounts
    under the group's lft/rgt range. Leaves added to the chart of accounts
    later get included automatically on the next run — no remap needed.
    """
    rows = frappe.get_all(
        "Account Flag Mapping",
        filters={"report": report_name},
        fields=["account", "flag", "is_group_binding"],
        limit_page_length=0,
    )
    if not rows:
        return {}

    # First pass: collect group accounts we'll need to resolve, alongside
    # the direct leaf bindings we can use as-is.
    out: dict[str, list[str]] = {}
    group_accounts_by_flag: dict[str, list[str]] = {}

    for r in rows:
        flag = (r.get("flag") or "").strip()
        if not flag:
            continue
        if r.get("is_group_binding"):
            group_accounts_by_flag.setdefault(flag, []).append(r.get("account"))
        else:
            out.setdefault(flag, []).append(r.get("account"))

    if not group_accounts_by_flag:
        return out

    # Resolve every group binding in one SQL: fetch the lft/rgt of each group,
    # then one IN-query against Account to find leaves whose lft falls in any
    # of those ranges. This is cheap even for hundreds of groups.
    all_groups = list({a for accs in group_accounts_by_flag.values() for a in accs})
    group_meta = frappe.get_all(
        "Account",
        filters={"name": ["in", all_groups]},
        fields=["name", "lft", "rgt", "company"],
        limit_page_length=0,
    )
    meta_by_name = {g["name"]: g for g in group_meta}

    # Collect every leaf under each group, keyed by group.
    leaves_by_group: dict[str, list[str]] = {}
    for g in group_meta:
        leaves = frappe.get_all(
            "Account",
            filters={
                "is_group": 0,
                "lft": [">=", g["lft"]],
                "rgt": ["<=", g["rgt"]],
                "company": g["company"],
            },
            pluck="name",
            limit_page_length=0,
        )
        leaves_by_group[g["name"]] = leaves

    # Stitch leaves back into the per-flag map. Deduplicate — a leaf may be
    # under more than one bound group (rare but possible with custom trees).
    for flag, groups in group_accounts_by_flag.items():
        bucket = set(out.get(flag) or [])
        for g in groups:
            for leaf in leaves_by_group.get(g, []):
                bucket.add(leaf)
        if bucket:
            out[flag] = sorted(bucket)

    return out


def flag_binding_meta(
    report_name: str,
    flag_to_accounts: dict[str, list[str]] | None = None,
    report_rows: list[dict] | None = None,
) -> dict[str, dict]:
    """Per-flag binding metadata, so the report view can explain — at read time —
    why a row's number is what it is.

    For each flag (report row) it reports:
      - is_group:        the row carries at least one GROUP binding, so it
                         auto-includes new leaves added under that group.
      - resolved_count:  how many leaf accounts the flag currently resolves to.
      - direct_count:    how many of those were bound individually (static).
      - group_codes:     the account codes of the bound group(s) (for tooltip).
      - new_count /      leaves that joined the chart AFTER the row was set up
        new_accounts:    and are now being swept in automatically. Derived with
                         NO stored snapshot — a resolved leaf is "new" when its
                         Account.creation is later than the earliest group-binding
                         mapping for that flag, and it wasn't bound directly.
      - has_binding:     at least one Account Flag Mapping row exists for this
                         flag (group or direct), whether or not it currently
                         resolves to anything.
      - missing_accounts / missing_count:
                         directly-bound accounts that no longer exist in the
                         chart of accounts (deleted or renamed out from under
                         the mapping). These stay in `flag_to_accounts` — a
                         name the SQL `IN` clause simply won't match — so the
                         row quietly loses whatever they used to contribute,
                         with nothing on screen to say so.

    A row with resolved_count == 0 sums to zero in ~10ms and looks exactly
    like a row with genuinely no activity — the two are indistinguishable on
    the statement itself, which is what makes a missing mapping look like a
    reporting engine bug rather than a configuration gap. `report_rows`
    (every row from the definition) lets this function report on a flag that
    has NO Account Flag Mapping at all, not only ones present in the mapping
    table — the case that used to disappear entirely from this response.

    This is the read-time companion to the Map screen's bind-time badges: it makes
    the dynamic group behaviour visible on the statement itself, and surfaces leaves
    that silently joined a row since setup (the case most likely to move a number
    without an obvious cause)."""
    if flag_to_accounts is None:
        flag_to_accounts = load_flag_to_accounts(report_name)

    # Every flag a "source" row in the definition actually reads from — so a
    # row with zero Account Flag Mapping rows still gets an entry below,
    # instead of vanishing from this response the way it used to.
    all_flags: set[str] = set()
    for r in (report_rows or []):
        if r.get("kind") == "source":
            f = (r.get("flag") or r.get("label") or "").strip()
            if f:
                all_flags.add(f)

    maps = frappe.get_all(
        "Account Flag Mapping",
        filters={"report": report_name},
        fields=["account", "flag", "is_group_binding", "creation"],
        limit_page_length=0,
    )
    if not maps:
        # No mapping rows at all — still report every source-row flag as
        # unbound rather than returning nothing. A caller with no report_rows
        # (older call sites) keeps the old behaviour of an empty dict.
        return {f: _unbound_binding_meta() for f in all_flags}

    group_binding_accts: dict[str, list[str]] = {}
    earliest_group_creation: dict[str, str] = {}
    direct_accts: dict[str, set] = {}
    for m in maps:
        flag = (m.get("flag") or "").strip()
        if not flag:
            continue
        if m.get("is_group_binding"):
            group_binding_accts.setdefault(flag, []).append(m.get("account"))
            c = str(m.get("creation") or "")
            if c and (flag not in earliest_group_creation or c < earliest_group_creation[flag]):
                earliest_group_creation[flag] = c
        else:
            direct_accts.setdefault(flag, set()).add(m.get("account"))

    # One query for the creation + display code of every resolved leaf and bound group.
    all_leaves = {a for accs in flag_to_accounts.values() for a in accs}
    all_groups = {a for accs in group_binding_accts.values() for a in accs}
    need = list(all_leaves | all_groups)
    acct_info: dict[str, dict] = {}
    if need:
        for a in frappe.get_all(
            "Account",
            filters={"name": ["in", need]},
            fields=["name", "account_number", "account_name", "creation"],
            limit_page_length=0,
        ):
            acct_info[a["name"]] = a

    def _code(name: str) -> str:
        info = acct_info.get(name) or {}
        return info.get("account_number") or info.get("account_name") or name

    MAX_NEW = 25
    out: dict[str, dict] = {}
    flags = set(group_binding_accts) | set(direct_accts) | set(flag_to_accounts) | all_flags
    for flag in flags:
        leaves = flag_to_accounts.get(flag, [])
        is_group = flag in group_binding_accts
        direct = direct_accts.get(flag, set())
        new_accounts: list[dict] = []
        cutoff = earliest_group_creation.get(flag)
        if is_group and cutoff:
            for leaf in leaves:
                if leaf in direct:
                    continue
                info = acct_info.get(leaf)
                if info and str(info.get("creation") or "") > cutoff:
                    new_accounts.append({"code": _code(leaf), "name": info.get("account_name") or leaf})
        new_accounts.sort(key=lambda x: x["code"])
        # Direct bindings that no longer resolve to a live Account — the
        # mapping row still exists and still feeds the SQL `IN (...)`, it
        # just can never match anything again. Silent otherwise.
        missing = sorted(a for a in direct if a and a not in acct_info)
        out[flag] = {
            "is_group": is_group,
            "resolved_count": len(leaves),
            "direct_count": len(direct),
            "group_codes": sorted({_code(g) for g in group_binding_accts.get(flag, [])}),
            "new_count": len(new_accounts),
            "new_accounts": new_accounts[:MAX_NEW],
            "new_truncated": len(new_accounts) > MAX_NEW,
            "has_binding": bool(is_group or direct),
            "missing_accounts": missing,
            "missing_count": len(missing),
        }
    return out


def _unbound_binding_meta() -> dict:
    """Shape returned for a source-row flag with no Account Flag Mapping at
    all. Kept in sync with the fields `flag_binding_meta` fills in above so
    the frontend never has to special-case "no mapping rows in the report"
    versus "mapping rows exist but resolve to nothing"."""
    return {
        "is_group": False,
        "resolved_count": 0,
        "direct_count": 0,
        "group_codes": [],
        "new_count": 0,
        "new_accounts": [],
        "new_truncated": False,
        "has_binding": False,
        "missing_accounts": [],
        "missing_count": 0,
    }


def load_flag_mappings(report_name: str) -> dict[str, list[dict]]:
    """Build {flag: [{account, dimension_filters}, ...]} from Account Flag Mapping.

    Unlike load_flag_to_accounts (which returns a flat account list per flag),
    this preserves each mapping ROW — so the same account can appear more than
    once under a flag with different per-row dimension scopes, and so each
    row's scope travels with its account into the engine.

    Group bindings expand to leaves; every resolved leaf inherits the binding
    row's dimension_filters.

    dimension_filters is a list of {dimension_type, dimension_value} dicts
    (empty list = whole company).
    """
    rows = frappe.get_all(
        "Account Flag Mapping",
        filters={"report": report_name},
        fields=["account", "flag", "is_group_binding", "dimension_filters_json"],
        limit_page_length=0,
    )
    if not rows:
        return {}

    def _parse_filters(raw) -> list[dict]:
        if not raw:
            return []
        try:
            parsed = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(parsed, list):
                out = []
                for p in parsed:
                    if isinstance(p, dict) and p.get("dimension_type") and p.get("dimension_value"):
                        out.append({
                            "dimension_type": p["dimension_type"],
                            "dimension_value": p["dimension_value"],
                        })
                return out
        except Exception:
            pass
        return []

    out: dict[str, list[dict]] = {}
    group_rows: list[dict] = []  # rows needing leaf resolution

    for r in rows:
        flag = (r.get("flag") or "").strip()
        if not flag:
            continue
        filters = _parse_filters(r.get("dimension_filters_json"))
        if r.get("is_group_binding"):
            group_rows.append({"flag": flag, "account": r.get("account"), "filters": filters})
        else:
            out.setdefault(flag, []).append({
                "account": r.get("account"),
                "dimension_filters": filters,
            })

    if group_rows:
        all_groups = list({g["account"] for g in group_rows if g["account"]})
        group_meta = frappe.get_all(
            "Account",
            filters={"name": ["in", all_groups]},
            fields=["name", "lft", "rgt", "company"],
            limit_page_length=0,
        )
        leaves_by_group: dict[str, list[str]] = {}
        for g in group_meta:
            leaves_by_group[g["name"]] = frappe.get_all(
                "Account",
                filters={
                    "is_group": 0,
                    "lft": [">=", g["lft"]],
                    "rgt": ["<=", g["rgt"]],
                    "company": g["company"],
                },
                pluck="name",
                limit_page_length=0,
            )
        for gr in group_rows:
            leaves = leaves_by_group.get(gr["account"], [])
            for leaf in leaves:
                out.setdefault(gr["flag"], []).append({
                    "account": leaf,
                    "dimension_filters": gr["filters"],
                })

    return out


# ─── Combo view (v1.9.63) ────────────────────────────────────────────────


def execute_combo_report(
    *,
    report_def: dict,
    fiscal_year: int,
    month_from: int,
    month_to: int,
    dim1: str,
    dim2: str,
    cost_center: str | list | None = None,
    project: str | list | None = None,
    department: str | list | None = None,
    branch: str | list | None = None,
    company: str | None = None,
    flag_to_accounts: dict[str, list[str]] | None = None,
    dimension_filters: dict | None = None,
    fy_start_month_override: int | None = None,
    period_mode: str = "fiscal_year",
    period_from_date: str | None = None,
    period_to_date: str | None = None,
) -> dict:
    """Run a report in combo mode — one row per (row × dim1 × dim2 tuple).

    v1.9.63 — combo view for P&L reports. The engine returns a flat list
    of {row_key, row_label, tuple, value} records. Frontend decides
    whether to render as flat table (Format A) or hierarchical tree
    (Format B).

    Algorithm:
      1. For each source row in the report definition, collect the GL
         account leaves it references (via flag_to_accounts).
      2. Issue one SQL query per row, grouped by (dim1, dim2), that sums
         signed (credit-debit) over the date range — same sign convention
         as execute_report so values match the period view.
      3. Emit one tuple record per non-zero (dim1, dim2) combination.

    Empty tuples are dropped (the user-confirmed default). The frontend
    has no toggle for "show empty" in v1.9.63 — adding that would require
    enumerating all dim values, which is significantly more expensive.

    Performance: each row gets one SQL query with GROUP BY (dim1, dim2).
    For a typical 30-row report and dimension columns with 10-50 distinct
    values each, this is fast — the database does the aggregation.

    Returns:
        {
            "view": "combo",
            "dimensions_picked": [dim1, dim2],
            "rows": [
                {"row_key", "row_label", "tuple": {dim1: v, dim2: v}, "value"},
                ...
            ],
        }
    """
    from frappe.query_builder import DocType
    from frappe.query_builder.functions import Sum
    from neotec_insight.neotec_insight.utils.fiscal_year import (
        resolve_date_bounds,
    )

    flag_to_accounts = flag_to_accounts or {}

    # Normalise native dim filters — same pattern as execute_report.
    def _norm(v):
        if v is None or v == "":
            return None
        if isinstance(v, list):
            cleaned = [str(x).strip() for x in v if x is not None and str(x).strip()]
            return cleaned or None
        s = str(v).strip()
        return [s] if s else None

    cost_center = _norm(cost_center)
    project = _norm(project)
    department = _norm(department)
    branch = _norm(branch)

    # v1.9.65 — single resolver respects period_mode.
    date_start, date_end = resolve_date_bounds(
        company, fiscal_year, month_from, month_to,
        fy_start_month_override=fy_start_month_override,
        period_mode=period_mode,
        period_from_date=period_from_date,
        period_to_date=period_to_date,
    )

    # Existence check — protect against pivoting on a column that doesn't
    # exist in `tabGL Entry` (e.g. a custom dimension not yet migrated).
    try:
        gl_cols = set(frappe.db.get_table_columns("GL Entry"))
    except Exception:
        gl_cols = set()
    if dim1 not in gl_cols or dim2 not in gl_cols:
        frappe.throw(
            f"Combo view requires both dimensions to exist as GL Entry columns. "
            f"Missing: {[d for d in (dim1, dim2) if d not in gl_cols]}"
        )

    rows_out: list[dict] = []

    for row in report_def.get("rows", []):
        kind = row.get("kind")
        if kind != "source":
            # Combo view skips formula and section rows. They derive from
            # source rows and would double-count if included as separate
            # tuples. Frontend can still compute formula totals if needed.
            continue
        flag = row.get("flag") or row.get("label")
        accounts = flag_to_accounts.get(flag, [])
        if not accounts:
            continue

        gl = DocType("GL Entry")
        # Sign convention: SUM(credit - debit). Same as execute_report,
        # which then negates expense-side accounts via the report
        # definition. For combo, we keep raw (credit - debit) and let the
        # frontend handle sign on its end based on row metadata.
        _ie2 = _income_expense_accounts(company)
        q = (
            frappe.qb.from_(gl)
            .select(
                gl[dim1].as_("d1"),
                gl[dim2].as_("d2"),
                Sum(gl.credit - gl.debit).as_("amount"),
            )
            .where(gl.is_cancelled == 0)
            .where(~((gl.voucher_type == "Period Closing Voucher") & (gl.account.isin(_ie2 or ["__none__"]))))
            .where(gl.account.isin(accounts))
            .where(gl.posting_date.between(date_start, date_end))
            .groupby(gl[dim1], gl[dim2])
        )

        # Apply native and custom dimension filters — identical pattern
        # to execute_report for consistency.
        def _apply(qq, col_attr, vals):
            if vals is None:
                return qq
            if isinstance(vals, list):
                if not vals:
                    return qq
                return qq.where(col_attr.isin(vals))
            return qq.where(col_attr == vals)
        q = _apply(q, gl.cost_center, cost_center)
        q = _apply(q, gl.project, project)
        q = _apply(q, gl.department, department)
        q = _apply(q, gl.branch, branch)
        if company:
            q = q.where(gl.company == company)
        if dimension_filters:
            for col, val in dimension_filters.items():
                if val is None or val == "" or val == []:
                    continue
                if col not in gl_cols:
                    continue
                if col in (dim1, dim2):
                    # Don't double-filter the columns being pivoted on.
                    continue
                if isinstance(val, list):
                    if not val:
                        continue
                    q = q.where(gl[col].isin(val))
                else:
                    q = q.where(gl[col] == val)

        # Sign flip — accumulator rows in the report definition may
        # specify sign='reverse' for cost-side accounts (so values
        # display as positive). We honour it here so combo values match
        # what the user sees in the period view.
        sign = -1 if (row.get("sign") == "reverse") else 1

        for r in q.run(as_dict=True):
            d1_val = r.get("d1") or "(Unassigned)"
            d2_val = r.get("d2") or "(Unassigned)"
            amt = sign * flt(r.get("amount") or 0)
            # v1.9.63 — hide empty tuples by default (user-confirmed).
            if amt == 0:
                continue
            rows_out.append({
                "row_key": row.get("key"),
                "row_label": row.get("label"),
                "tuple": {dim1: d1_val, dim2: d2_val},
                "value": amt,
            })

    # Sort: outer dim, then inner dim, then row order. Keeps both flat
    # (Format A) and hierarchical (Format B) presentations sensible.
    row_order = {r.get("key"): i for i, r in enumerate(report_def.get("rows", []))}
    rows_out.sort(key=lambda x: (
        x["tuple"][dim1],
        x["tuple"][dim2],
        row_order.get(x["row_key"], 9999),
    ))

    return {
        "view": "combo",
        "dimensions_picked": [dim1, dim2],
        "rows": rows_out,
    }
