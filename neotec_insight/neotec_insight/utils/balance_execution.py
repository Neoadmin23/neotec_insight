"""Balance-based execution engine for Trial Balance and Balance Sheet.

The P&L engine in `execution.py` computes period activity (debit/credit sums
within a date range) on flow accounts. These reports are different:

* Trial Balance shows opening + period + closing per account.
* Balance Sheet shows closing-balance-as-of a date on stock accounts.

Both share the underlying primitive: given an account and a date range, what
are the debit/credit totals from GL Entry? We expose that as a single SQL
query that's then aggregated in different ways for each report.
"""
from __future__ import annotations

from typing import Any

import frappe
from frappe.utils import flt, getdate


# ─── Pivot-by validation (v1.9.57) ───────────────────────────────────────
#
# The Trial Balance and Balance Sheet pivot engines used to hardcode the
# four native dimensions (cost_center, department, project, branch) as the
# only valid pivot_by values. That worked when Insight only knew about the
# natives. After v1.9.52 introduced custom Accounting Dimensions support,
# the P&L pivot was widened to accept any configured dimension; this engine
# wasn't, which is why custom dimensions didn't appear in the TB/BS pivot
# dropdowns even though they were configured in ERPNext.
#
# This single helper centralises the check so all three call sites
# (line ~1106 for the P&L equivalent in this file, ~1284 for Trial Balance,
# ~1402 for Balance Sheet) use identical logic.


def _validate_pivot_by(pivot_by: str) -> None:
    """Reject pivot_by values that aren't on the dimension whitelist —
    natives ∪ discovered custom Accounting Dimensions. Lazy import of the
    report.py helper avoids any circular-import surprise at module load
    time (report.py already imports from this file).

    Defensive: if the helper can't be imported for any reason, fall back
    to the legacy native-only whitelist so the engine remains usable.
    """
    valid: set[str]
    try:
        from neotec_insight.neotec_insight.api.report import _all_valid_dimension_fieldnames
        valid = _all_valid_dimension_fieldnames()
    except Exception:
        valid = {"cost_center", "department", "project", "branch"}
    if pivot_by not in valid:
        frappe.throw(f"Invalid pivot_by '{pivot_by}'.")


# ─── Common helpers ──────────────────────────────────────────────────────


def _apply_in_clause(conds: list[str], params: dict, sql_col: str, val, pkey_prefix: str) -> None:
    """Append a parameterised WHERE clause for either scalar or list value.

    v1.9.58 — when val is a list (multi-select), expands to IN(...);
    when scalar, falls back to single equality. Empty values do nothing.

    The sql_col argument is interpolated directly with backticks — the
    caller must have validated it against the whitelist; passing user
    input here would be a SQL injection vector. We trust the caller.
    """
    if val is None or val == "" or val == []:
        return
    if isinstance(val, list):
        # Drop blanks but preserve order for reproducibility in cache keys
        vals = [v for v in val if v is not None and str(v).strip()]
        if not vals:
            return
        placeholders = ", ".join(f"%({pkey_prefix}_{i})s" for i in range(len(vals)))
        conds.append(f"g.`{sql_col}` IN ({placeholders})")
        for i, v in enumerate(vals):
            params[f"{pkey_prefix}_{i}"] = v
    else:
        pkey = pkey_prefix
        conds.append(f"g.`{sql_col}` = %({pkey})s")
        params[pkey] = val


def _apply_extra_gl_filters(
    conds: list[str],
    params: dict,
    *,
    finance_book: str | None = None,
    dimension_filters: dict | None = None,
) -> None:
    """Append finance-book and custom-dimension WHERE clauses in place.

    finance_book: GL Entry has a built-in `finance_book` column.
    dimension_filters: {gl_column_name: value} for custom Accounting
        Dimensions. v1.9.58 — value may be a list (multi-select), in which
        case the clause becomes IN(...). Each column is verified to exist
        before being used, so a dimension that hasn't migrated yet is
        silently skipped rather than crashing the query.
    """
    if finance_book:
        conds.append("g.finance_book = %(finance_book)s")
        params["finance_book"] = finance_book
    if dimension_filters:
        for i, (col, val) in enumerate(dimension_filters.items()):
            if val is None or val == "" or val == []:
                continue
            if not _gl_entry_has_column(col):
                continue
            _apply_in_clause(conds, params, col, val, f"dim{i}")


def _gl_sums_per_account(
    *,
    company: str,
    from_date: str | None,
    to_date: str | None,
    cost_center: str | list | None = None,
    project: str | list | None = None,
    accounts: list[str] | None = None,
    finance_book: str | None = None,
    dimension_filters: dict | None = None,
) -> dict[str, dict[str, float]]:
    """One SQL query: SUM(debit), SUM(credit) per account in a date window.

    Returns {account_name: {"debit": float, "credit": float}}.

    Filters all gl entries to company. Excludes cancelled entries.
    Optional dimension filters narrow further.
    `accounts` filter limits the query to a specific account set (used for
    party-drill queries where we already know the control account).

    Pass `from_date=None` to mean "since the beginning of time" — used for
    opening-balance queries.
    """
    conds = ["g.company = %(company)s", "g.is_cancelled = 0"]
    params: dict[str, Any] = {"company": company}

    if from_date:
        conds.append("g.posting_date >= %(from_date)s")
        params["from_date"] = from_date
    if to_date:
        conds.append("g.posting_date <= %(to_date)s")
        params["to_date"] = to_date
    # v1.9.58 — native dims now accept lists for multi-select.
    _apply_in_clause(conds, params, "cost_center", cost_center, "cost_center")
    _apply_in_clause(conds, params, "project", project, "project")
    _apply_extra_gl_filters(conds, params, finance_book=finance_book, dimension_filters=dimension_filters)
    if accounts:
        placeholders = ", ".join(f"%(a{i})s" for i in range(len(accounts)))
        conds.append(f"g.account IN ({placeholders})")
        for i, a in enumerate(accounts):
            params[f"a{i}"] = a

    sql = f"""
        SELECT g.account AS account,
               SUM(g.debit) AS debit,
               SUM(g.credit) AS credit
        FROM `tabGL Entry` g
        WHERE {' AND '.join(conds)}
        GROUP BY g.account
    """
    rows = frappe.db.sql(sql, params, as_dict=True)
    out: dict[str, dict[str, float]] = {}
    for r in rows:
        out[r["account"]] = {"debit": flt(r["debit"]), "credit": flt(r["credit"])}
    return out


def _load_chart_of_accounts(company: str) -> list[dict]:
    """Return every Account for a company with the structural fields we need:
    name, parent_account, is_group, account_type, root_type, lft, rgt, disabled.
    """
    return frappe.get_all(
        "Account",
        filters={"company": company, "disabled": 0},
        fields=[
            "name", "account_number", "account_name", "parent_account",
            "is_group", "account_type", "root_type", "lft", "rgt",
        ],
        order_by="lft asc",
        limit_page_length=0,
    )


# ─── Trial Balance ───────────────────────────────────────────────────────


def run_trial_balance_engine(
    *,
    company: str,
    fiscal_year_start: str,
    as_of_date: str,
    from_date: str | None = None,
    cost_center: str | list | None = None,
    project: str | list | None = None,
    root_types: list[str] | None = None,
    finance_book: str | None = None,
    dimension_filters: dict | None = None,
    show_group_accounts: bool = True,
    show_zero_values: bool = False,
    presentation_currency: str | None = None,
) -> dict[str, Any]:
    """Run a trial balance for `company`.

    Period window (v1.9.16): if `from_date` is given, the period runs
    from_date → as_of_date and Opening is the balance as of the day BEFORE
    from_date. If `from_date` is omitted, behaviour is unchanged — period is
    fiscal_year_start → as_of_date with Opening before the fiscal year.

    `as_of_date` is the period's TO date in both cases.

    Result structure:
        {
            "accounts": [
                {
                    "name": "...", "label": "...", "code": "...",
                    "parent": "...", "is_group": 0/1,
                    "account_type": "...", "root_type": "Asset",
                    "depth": 2,
                    "lft": ..., "rgt": ...,
                    "opening_debit": float, "opening_credit": float,
                    "period_debit": float, "period_credit": float,
                    "closing_debit": float, "closing_credit": float,
                    "has_parties": bool,    # true for Receivable/Payable
                },
                ...
            ],
            "totals": {opening_debit, opening_credit, period_debit, period_credit, closing_debit, closing_credit}
        }

    Sign convention: standard accounting. Opening/closing values are *gross*
    (debit and credit reported separately, both positive) so the user can
    see the natural side of each account. The closing balance equals
    opening + period activity by side, with cross-side offsets applied so a
    debit-natural account with net credit activity shows up on the credit side.
    """
    coa = _load_chart_of_accounts(company)
    if root_types:
        coa = [a for a in coa if a.get("root_type") in root_types]
    coa_by_name = {a["name"]: a for a in coa}

    # Compute depth for indentation. Walk parent chain. Memoize.
    depth_cache: dict[str, int] = {}
    def _depth(acc_name: str) -> int:
        if acc_name in depth_cache:
            return depth_cache[acc_name]
        a = coa_by_name.get(acc_name)
        if not a or not a.get("parent_account"):
            depth_cache[acc_name] = 0
            return 0
        d = _depth(a["parent_account"]) + 1
        depth_cache[acc_name] = d
        return d

    # Period window. The period STARTS at `period_start` — the explicit
    # from_date if given, else the fiscal-year start. Opening is everything
    # before that.
    period_start = from_date or fiscal_year_start
    opening_to = _day_before(period_start)
    opening_sums = _gl_sums_per_account(
        company=company, from_date=None, to_date=opening_to,
        cost_center=cost_center, project=project,
        finance_book=finance_book, dimension_filters=dimension_filters,
    )

    # Period activity: period_start through as_of_date (the TO date).
    period_sums = _gl_sums_per_account(
        company=company, from_date=period_start, to_date=as_of_date,
        cost_center=cost_center, project=project,
        finance_book=finance_book, dimension_filters=dimension_filters,
    )

    # First pass: leaf account rows.
    leaf_rows: dict[str, dict] = {}
    for a in coa:
        if a.get("is_group"):
            continue
        op = opening_sums.get(a["name"], {"debit": 0.0, "credit": 0.0})
        pd = period_sums.get(a["name"], {"debit": 0.0, "credit": 0.0})
        op_dr, op_cr = op["debit"], op["credit"]
        pd_dr, pd_cr = pd["debit"], pd["credit"]
        # Closing: net the two sides, then redisplay on the natural side.
        net_dr = (op_dr + pd_dr) - (op_cr + pd_cr)
        if net_dr >= 0:
            cl_dr, cl_cr = net_dr, 0.0
        else:
            cl_dr, cl_cr = 0.0, -net_dr
        # Opening shown on its natural side too.
        op_net = op_dr - op_cr
        if op_net >= 0:
            op_dr_disp, op_cr_disp = op_net, 0.0
        else:
            op_dr_disp, op_cr_disp = 0.0, -op_net

        leaf_rows[a["name"]] = {
            "name": a["name"],
            "label": a.get("account_name") or a["name"],
            "code": a.get("account_number") or "",
            "parent": a.get("parent_account") or "",
            "is_group": 0,
            "account_type": a.get("account_type") or "",
            "root_type": a.get("root_type") or "",
            "depth": _depth(a["name"]),
            "lft": a.get("lft") or 0,
            "rgt": a.get("rgt") or 0,
            "opening_debit": round(op_dr_disp, 2),
            "opening_credit": round(op_cr_disp, 2),
            "period_debit": round(pd_dr, 2),
            "period_credit": round(pd_cr, 2),
            "closing_debit": round(cl_dr, 2),
            "closing_credit": round(cl_cr, 2),
            "has_parties": (a.get("account_type") or "") in {"Receivable", "Payable"},
        }

    # Second pass: group rows roll up their descendants. We walk all groups
    # and sum every leaf inside (lft <= leaf.lft < rgt). Cheap because we
    # already have leaves in memory.
    group_rows: dict[str, dict] = {}
    for a in coa:
        if not a.get("is_group"):
            continue
        agg = {"opening_debit": 0.0, "opening_credit": 0.0,
               "period_debit": 0.0, "period_credit": 0.0,
               "closing_debit": 0.0, "closing_credit": 0.0}
        for leaf in leaf_rows.values():
            if leaf["lft"] >= a["lft"] and leaf["rgt"] <= a["rgt"]:
                for k in agg:
                    agg[k] += leaf[k]
        # Group rows show GROSS debit AND credit per side — the sum of each
        # leaf's (already per-account-netted) balance, WITHOUT re-netting the
        # group. Re-netting made the top-level group lines fail to sum to the
        # column totals whenever a group held contra accounts (e.g. accumulated
        # depreciation under Assets, or a debit-balance item under Liabilities):
        # the contra is netted away in the group but counted gross in the total.
        # Keeping group rows gross makes them reconcile exactly —
        # Σ(top-level groups, debit) == total debit, and likewise credit.

        group_rows[a["name"]] = {
            "name": a["name"],
            "label": a.get("account_name") or a["name"],
            "code": a.get("account_number") or "",
            "parent": a.get("parent_account") or "",
            "is_group": 1,
            "account_type": a.get("account_type") or "",
            "root_type": a.get("root_type") or "",
            "depth": _depth(a["name"]),
            "lft": a.get("lft") or 0,
            "rgt": a.get("rgt") or 0,
            "has_parties": False,
            **{k: round(v, 2) for k, v in agg.items()},
        }

    # Merge in tree pre-order, but sort siblings by account_number (then lft as
    # a stable fallback) so roots and children read 1, 2, 3, 4, 5, 6 instead of
    # whatever order the nested-set tree happened to be built in.
    by_parent: dict[str, list[dict]] = {}
    node_for: dict[str, dict] = {}
    for a in coa:
        n = a["name"]
        row = leaf_rows.get(n) or group_rows.get(n)
        if row is None:
            continue
        node_for[n] = row
        by_parent.setdefault(a.get("parent_account") or "", []).append(a)

    def _sib_key(a: dict):
        code = (a.get("account_number") or "").strip()
        # accounts without a number sort after numbered ones, then by lft
        return (code == "", code, a.get("lft") or 0)

    accounts: list[dict] = []

    def _walk(parent_name: str) -> None:
        for a in sorted(by_parent.get(parent_name, []), key=_sib_key):
            n = a["name"]
            if n in node_for:
                accounts.append(node_for[n])
            _walk(n)

    _walk("")
    # Safety net: append any node not reached via the parent walk (defensive
    # against orphaned parent links) so nothing silently drops from totals.
    seen = {id(r) for r in accounts}
    for a in coa:
        r = node_for.get(a["name"])
        if r is not None and id(r) not in seen:
            accounts.append(r)
            seen.add(id(r))

    # Currency conversion (single-rate, as-of-date). Applied before totals so
    # everything stays internally consistent. rate=1.0 when no conversion.
    company_currency = get_company_currency(company)
    conversion_rate = 1.0
    rate_missing = False
    if presentation_currency and presentation_currency != company_currency:
        conversion_rate = get_conversion_rate(company_currency, presentation_currency, as_of_date)
        if conversion_rate == 1.0:
            # Could be a genuine 1:1 peg, but more likely no rate on file.
            rate_missing = True
    if conversion_rate != 1.0:
        amount_keys = ("opening_debit", "opening_credit", "period_debit",
                       "period_credit", "closing_debit", "closing_credit")
        for r in accounts:
            for k in amount_keys:
                r[k] = round(r[k] * conversion_rate, 2)

    # Totals = sum of leaf rows (groups would double-count).
    totals = {
        "opening_debit": round(sum(r["opening_debit"] for r in accounts if not r["is_group"]), 2),
        "opening_credit": round(sum(r["opening_credit"] for r in accounts if not r["is_group"]), 2),
        "period_debit": round(sum(r["period_debit"] for r in accounts if not r["is_group"]), 2),
        "period_credit": round(sum(r["period_credit"] for r in accounts if not r["is_group"]), 2),
        "closing_debit": round(sum(r["closing_debit"] for r in accounts if not r["is_group"]), 2),
        "closing_credit": round(sum(r["closing_credit"] for r in accounts if not r["is_group"]), 2),
    }

    # Optional row filters. Applied last so totals always reflect the full
    # ledger regardless of what's hidden from view.
    if not show_zero_values:
        amount_keys = ("opening_debit", "opening_credit", "period_debit",
                       "period_credit", "closing_debit", "closing_credit")
        accounts = [
            r for r in accounts
            if any(abs(r[k]) > 0.005 for k in amount_keys)
        ]
    if not show_group_accounts:
        accounts = [r for r in accounts if not r["is_group"]]

    return {
        "accounts": accounts,
        "totals": totals,
        "currency": {
            "company_currency": company_currency,
            "presentation_currency": presentation_currency or company_currency,
            "conversion_rate": conversion_rate,
            "rate_missing": rate_missing,
            "as_of_date": as_of_date,
        },
    }


def run_trial_balance_parties_engine(
    *,
    company: str,
    account: str,
    fiscal_year_start: str,
    as_of_date: str,
    cost_center: str | list | None = None,
    project: str | list | None = None,
    presentation_currency: str | None = None,
) -> list[dict]:
    """Per-party breakdown for one control account (Receivable or Payable).

    Returns a list of {party_type, party, party_name, opening_debit,
    opening_credit, period_debit, period_credit, closing_debit, closing_credit}.

    Opens a separate query keyed by party. We don't pre-load parties because
    a company can have thousands; lazy-load only when the user expands.
    """
    # Opening: pre-FY-start sums grouped by party for this single account.
    opening_to = _day_before(fiscal_year_start)
    op_sums = _gl_sums_per_party(
        company=company, account=account,
        from_date=None, to_date=opening_to,
        cost_center=cost_center, project=project,
    )
    pd_sums = _gl_sums_per_party(
        company=company, account=account,
        from_date=fiscal_year_start, to_date=as_of_date,
        cost_center=cost_center, project=project,
    )

    party_keys = set(op_sums.keys()) | set(pd_sums.keys())
    party_names = _party_name_map(party_keys)

    out: list[dict] = []
    for (party_type, party) in party_keys:
        op = op_sums.get((party_type, party), {"debit": 0.0, "credit": 0.0})
        pd = pd_sums.get((party_type, party), {"debit": 0.0, "credit": 0.0})
        op_dr, op_cr = op["debit"], op["credit"]
        pd_dr, pd_cr = pd["debit"], pd["credit"]
        net_dr = (op_dr + pd_dr) - (op_cr + pd_cr)
        if net_dr >= 0:
            cl_dr, cl_cr = net_dr, 0.0
        else:
            cl_dr, cl_cr = 0.0, -net_dr
        op_net = op_dr - op_cr
        if op_net >= 0:
            op_dr_disp, op_cr_disp = op_net, 0.0
        else:
            op_dr_disp, op_cr_disp = 0.0, -op_net

        out.append({
            "party_type": party_type or "",
            "party": party or "",
            "party_name": party_names.get((party_type, party)) or party or "(unnamed)",
            "opening_debit": round(op_dr_disp, 2),
            "opening_credit": round(op_cr_disp, 2),
            "period_debit": round(pd_dr, 2),
            "period_credit": round(pd_cr, 2),
            "closing_debit": round(cl_dr, 2),
            "closing_credit": round(cl_cr, 2),
        })
    # Sort by party name for stable display.
    out.sort(key=lambda r: (r["party_name"] or "").lower())

    # Single-rate currency conversion to match the parent report.
    company_currency = get_company_currency(company)
    if presentation_currency and presentation_currency != company_currency:
        rate = get_conversion_rate(company_currency, presentation_currency, as_of_date)
        if rate != 1.0:
            keys = ("opening_debit", "opening_credit", "period_debit",
                    "period_credit", "closing_debit", "closing_credit")
            for r in out:
                for k in keys:
                    r[k] = round(r[k] * rate, 2)
    return out


def _gl_sums_per_party(
    *, company: str, account: str,
    from_date: str | None, to_date: str | None,
    cost_center: str | list | None = None, project: str | list | None = None,
) -> dict[tuple[str, str], dict[str, float]]:
    conds = ["g.company = %(company)s", "g.is_cancelled = 0", "g.account = %(account)s"]
    params: dict[str, Any] = {"company": company, "account": account}
    if from_date:
        conds.append("g.posting_date >= %(from_date)s"); params["from_date"] = from_date
    if to_date:
        conds.append("g.posting_date <= %(to_date)s"); params["to_date"] = to_date
    # v1.9.58 — native dims now accept lists for multi-select.
    _apply_in_clause(conds, params, "cost_center", cost_center, "cost_center")
    _apply_in_clause(conds, params, "project", project, "project")
    sql = f"""
        SELECT g.party_type AS party_type, g.party AS party,
               SUM(g.debit) AS debit, SUM(g.credit) AS credit
        FROM `tabGL Entry` g
        WHERE {' AND '.join(conds)}
        GROUP BY g.party_type, g.party
    """
    rows = frappe.db.sql(sql, params, as_dict=True)
    out: dict[tuple[str, str], dict[str, float]] = {}
    for r in rows:
        key = (r.get("party_type") or "", r.get("party") or "")
        out[key] = {"debit": flt(r["debit"]), "credit": flt(r["credit"])}
    return out


def _party_name_map(keys: set[tuple[str, str]]) -> dict[tuple[str, str], str]:
    """Fetch human names for each (party_type, party). Customers and Suppliers
    have *_name fields; other types fall back to the party ID itself."""
    out: dict[tuple[str, str], str] = {}
    customers = [p for (pt, p) in keys if pt == "Customer" and p]
    suppliers = [p for (pt, p) in keys if pt == "Supplier" and p]
    if customers:
        try:
            rows = frappe.get_all("Customer",
                filters={"name": ["in", customers]},
                fields=["name", "customer_name"],
                limit_page_length=0,
            )
            for r in rows:
                out[("Customer", r["name"])] = r.get("customer_name") or r["name"]
        except Exception:
            pass
    if suppliers:
        try:
            rows = frappe.get_all("Supplier",
                filters={"name": ["in", suppliers]},
                fields=["name", "supplier_name"],
                limit_page_length=0,
            )
            for r in rows:
                out[("Supplier", r["name"])] = r.get("supplier_name") or r["name"]
        except Exception:
            pass
    return out


# ─── Balance Sheet ───────────────────────────────────────────────────────


def run_balance_sheet_engine(
    *,
    company: str,
    as_of_date: str,
    prior_as_of_date: str | None = None,
    cost_center: str | list | None = None,
    project: str | list | None = None,
    finance_book: str | None = None,
    dimension_filters: dict | None = None,
    show_group_accounts: bool = True,
    show_zero_values: bool = False,
    show_unclosed_pl: bool = True,
    presentation_currency: str | None = None,
) -> dict[str, Any]:
    """Return a balance sheet at `as_of_date`, optionally with a prior-period
    comparison column.

    Result:
        {
            "accounts": [{name, label, code, parent, is_group, account_type,
                          root_type, depth, lft, rgt, current, prior?}, ...],
            "sections": {
                "asset": {current, prior?},
                "liability": {current, prior?},
                "equity": {current, prior?},
                "lia_plus_eq": {current, prior?},
                "diff": {current, prior?},   # asset - (liability + equity)
            }
        }

    Only Asset, Liability, Equity accounts are included (root_type filter).
    Income/Expense are excluded — those net into Retained Earnings, which the
    consumer's Equity section already includes as a posting account if their
    bookkeeping closed the year. If their year isn't closed, the implicit
    "current year earnings" gets surfaced as a synthetic equity line.

    Sign convention: balances shown signed. Assets positive (debit-natural),
    Liabilities and Equity also positive (credit-natural, but we present the
    magnitude). The diff line shows Asset - (Liability + Equity), which should
    equal zero in a balanced ledger.
    """
    coa = _load_chart_of_accounts(company)
    coa = [a for a in coa if (a.get("root_type") or "") in {"Asset", "Liability", "Equity"}]

    coa_by_name = {a["name"]: a for a in coa}
    depth_cache: dict[str, int] = {}
    def _depth(acc_name: str) -> int:
        if acc_name in depth_cache: return depth_cache[acc_name]
        a = coa_by_name.get(acc_name)
        if not a or not a.get("parent_account"):
            depth_cache[acc_name] = 0
            return 0
        d = _depth(a["parent_account"]) + 1
        depth_cache[acc_name] = d
        return d

    def _balances_as_of(dt: str) -> dict[str, float]:
        sums = _gl_sums_per_account(
            company=company, from_date=None, to_date=dt,
            cost_center=cost_center, project=project,
            finance_book=finance_book, dimension_filters=dimension_filters,
        )
        return {acc: flt(s["debit"]) - flt(s["credit"]) for acc, s in sums.items()}

    cur_bal = _balances_as_of(as_of_date)
    prior_bal = _balances_as_of(prior_as_of_date) if prior_as_of_date else {}

    # Compute current-year earnings (only matters for unclosed books).
    # Income - Expense from FY-start through as_of_date.
    # We treat as_of_date's year as the current FY for this purpose.
    fy_start = _fiscal_year_start_for(as_of_date)
    cy_earn_sums = _gl_sums_per_account(
        company=company, from_date=fy_start, to_date=as_of_date,
        cost_center=cost_center, project=project,
        finance_book=finance_book, dimension_filters=dimension_filters,
    )
    cy_income = 0.0
    cy_expense = 0.0
    income_accts = frappe.get_all("Account",
        filters={"company": company, "root_type": "Income", "is_group": 0},
        pluck="name", limit_page_length=0,
    )
    expense_accts = frappe.get_all("Account",
        filters={"company": company, "root_type": "Expense", "is_group": 0},
        pluck="name", limit_page_length=0,
    )
    for a in income_accts:
        s = cy_earn_sums.get(a, {"debit": 0.0, "credit": 0.0})
        cy_income += flt(s["credit"]) - flt(s["debit"])
    for a in expense_accts:
        s = cy_earn_sums.get(a, {"debit": 0.0, "credit": 0.0})
        cy_expense += flt(s["debit"]) - flt(s["credit"])
    cy_earnings = cy_income - cy_expense

    # The "Show unclosed fiscal year's P&L balances" toggle. When off, we
    # don't fold the current-year net into Equity — matching ERPNext's
    # behavior where closed books already have it posted to retained earnings.
    if not show_unclosed_pl:
        cy_earnings = 0.0

    # Same for prior date.
    prior_cy_earnings = 0.0
    if prior_as_of_date:
        py_fy_start = _fiscal_year_start_for(prior_as_of_date)
        py_sums = _gl_sums_per_account(
            company=company, from_date=py_fy_start, to_date=prior_as_of_date,
            cost_center=cost_center, project=project,
            finance_book=finance_book, dimension_filters=dimension_filters,
        )
        for a in income_accts:
            s = py_sums.get(a, {"debit": 0.0, "credit": 0.0})
            prior_cy_earnings += flt(s["credit"]) - flt(s["debit"])
        for a in expense_accts:
            s = py_sums.get(a, {"debit": 0.0, "credit": 0.0})
            prior_cy_earnings -= flt(s["debit"]) - flt(s["credit"])
        if not show_unclosed_pl:
            prior_cy_earnings = 0.0

    # Build rows.
    accounts: list[dict] = []
    leaf_currents: dict[str, float] = {}
    leaf_priors: dict[str, float] = {}
    for a in coa:
        if a.get("is_group"):
            continue
        rt = a.get("root_type")
        cur_raw = cur_bal.get(a["name"], 0.0)
        # For Liability/Equity, display the credit-side magnitude (positive).
        cur = cur_raw if rt == "Asset" else -cur_raw
        leaf_currents[a["name"]] = cur

        prior = None
        if prior_as_of_date:
            prior_raw = prior_bal.get(a["name"], 0.0)
            prior = prior_raw if rt == "Asset" else -prior_raw
            leaf_priors[a["name"]] = prior

        accounts.append({
            "name": a["name"],
            "label": a.get("account_name") or a["name"],
            "code": a.get("account_number") or "",
            "parent": a.get("parent_account") or "",
            "is_group": 0,
            "account_type": a.get("account_type") or "",
            "root_type": rt or "",
            "depth": _depth(a["name"]),
            "lft": a.get("lft") or 0,
            "rgt": a.get("rgt") or 0,
            "has_parties": (a.get("account_type") or "") in {"Receivable", "Payable"},
            "current": round(cur, 2),
            "prior": round(prior, 2) if prior is not None else None,
        })

    # Group rows: sum descendants. lft/rgt comparison handles arbitrary depth.
    for a in coa:
        if not a.get("is_group"):
            continue
        cur_sum = 0.0
        prior_sum = 0.0
        for leaf in coa:
            if leaf.get("is_group"):
                continue
            if leaf["lft"] >= a["lft"] and leaf["rgt"] <= a["rgt"]:
                cur_sum += leaf_currents.get(leaf["name"], 0.0)
                if prior_as_of_date:
                    prior_sum += leaf_priors.get(leaf["name"], 0.0)
        accounts.append({
            "name": a["name"],
            "label": a.get("account_name") or a["name"],
            "code": a.get("account_number") or "",
            "parent": a.get("parent_account") or "",
            "is_group": 1,
            "account_type": a.get("account_type") or "",
            "root_type": a.get("root_type") or "",
            "depth": _depth(a["name"]),
            "lft": a.get("lft") or 0,
            "rgt": a.get("rgt") or 0,
            "has_parties": False,
            "current": round(cur_sum, 2),
            "prior": round(prior_sum, 2) if prior_as_of_date else None,
        })

    # Reorder by lft so we get a tree-natural display.
    accounts.sort(key=lambda r: r["lft"])

    # Currency conversion (single-rate). Convert account values and the
    # current-year-earnings figures before computing section totals so
    # everything is consistent in the presentation currency.
    company_currency = get_company_currency(company)
    conversion_rate = 1.0
    rate_missing = False
    if presentation_currency and presentation_currency != company_currency:
        conversion_rate = get_conversion_rate(company_currency, presentation_currency, as_of_date)
        if conversion_rate == 1.0:
            rate_missing = True
    if conversion_rate != 1.0:
        for r in accounts:
            r["current"] = round(r["current"] * conversion_rate, 2)
            if r.get("prior") is not None:
                r["prior"] = round(r["prior"] * conversion_rate, 2)
        cy_earnings = cy_earnings * conversion_rate
        prior_cy_earnings = prior_cy_earnings * conversion_rate

    # Section totals.
    def _section_sum(rt: str, key: str) -> float:
        return round(sum(
            r[key] or 0.0 for r in accounts
            if not r["is_group"] and r["root_type"] == rt
        ), 2)

    cur_asset = _section_sum("Asset", "current")
    cur_liab = _section_sum("Liability", "current")
    cur_eq = _section_sum("Equity", "current") + cy_earnings
    sections = {
        "asset": {"current": cur_asset, "prior": (_section_sum("Asset", "prior") if prior_as_of_date else None)},
        "liability": {"current": cur_liab, "prior": (_section_sum("Liability", "prior") if prior_as_of_date else None)},
        "equity": {
            "current": round(cur_eq, 2),
            "prior": round(_section_sum("Equity", "prior") + prior_cy_earnings, 2) if prior_as_of_date else None,
        },
        "current_year_earnings": {
            "current": round(cy_earnings, 2),
            "prior": round(prior_cy_earnings, 2) if prior_as_of_date else None,
        },
    }
    sections["lia_plus_eq"] = {
        "current": round(sections["liability"]["current"] + sections["equity"]["current"], 2),
        "prior": (round((sections["liability"]["prior"] or 0) + (sections["equity"]["prior"] or 0), 2)
                  if prior_as_of_date else None),
    }
    sections["diff"] = {
        "current": round(sections["asset"]["current"] - sections["lia_plus_eq"]["current"], 2),
        "prior": (round((sections["asset"]["prior"] or 0) - (sections["lia_plus_eq"]["prior"] or 0), 2)
                  if prior_as_of_date else None),
    }

    # Optional row filters — applied after sections so totals stay complete.
    if not show_zero_values:
        accounts = [
            r for r in accounts
            if abs(r["current"]) > 0.005 or (r.get("prior") is not None and abs(r["prior"]) > 0.005)
        ]
    if not show_group_accounts:
        accounts = [r for r in accounts if not r["is_group"]]

    return {
        "accounts": accounts,
        "sections": sections,
        "currency": {
            "company_currency": company_currency,
            "presentation_currency": presentation_currency or company_currency,
            "conversion_rate": conversion_rate,
            "rate_missing": rate_missing,
            "as_of_date": as_of_date,
        },
    }


# ─── Date helpers ────────────────────────────────────────────────────────


def _day_before(date_str: str) -> str:
    """Return the calendar day before `date_str` as 'YYYY-MM-DD'."""
    from datetime import timedelta
    d = getdate(date_str)
    return (d - timedelta(days=1)).strftime("%Y-%m-%d")


def _fiscal_year_start_for(date_str: str) -> str:
    """Look up the Fiscal Year covering `date_str` and return its start date.
    Falls back to Jan 1 of the same calendar year if no Fiscal Year matches.
    """
    d = getdate(date_str)
    try:
        fy = frappe.get_all(
            "Fiscal Year",
            filters={
                "year_start_date": ["<=", date_str],
                "year_end_date": [">=", date_str],
            },
            fields=["year_start_date"],
            limit_page_length=1,
        )
        if fy:
            return str(fy[0]["year_start_date"])
    except Exception:
        pass
    return f"{d.year}-01-01"


# ─── Accounting dimensions (generic discovery) ───────────────────────────


def list_accounting_dimensions() -> list[dict]:
    """Discover the custom Accounting Dimensions defined on this bench.

    ERPNext lets each site define its own Accounting Dimensions (e.g.
    "Business Division", "Employee Cost Center"). Each one adds a column to
    GL Entry whose fieldname is the dimension's document_type slugified.

    Returns a list of {label, fieldname, document_type} so the frontend can
    render a filter dropdown per dimension, and the engines can apply them
    as GL Entry filters — without anything being hardcoded per customer.

    Cost Center and Project are ERPNext built-ins, not Accounting Dimension
    records, so they're handled separately and not returned here.
    """
    try:
        rows = frappe.get_all(
            "Accounting Dimension",
            filters={"disabled": 0},
            fields=["document_type", "label"],
            limit_page_length=0,
        )
    except Exception:
        # Older ERPNext or missing doctype — no custom dimensions.
        return []

    out: list[dict] = []
    for r in rows:
        doc_type = r.get("document_type")
        if not doc_type:
            continue
        # The GL Entry column for a dimension is the doctype name, lowercased
        # with spaces → underscores. This matches ERPNext's own convention.
        fieldname = doc_type.lower().replace(" ", "_")
        out.append({
            "label": r.get("label") or doc_type,
            "fieldname": fieldname,
            "document_type": doc_type,
        })
    return out


def _gl_entry_has_column(fieldname: str) -> bool:
    """True if GL Entry actually has the given column. Custom dimensions add
    columns, but a freshly-added dimension may not have migrated yet — guard
    against querying a column that isn't there."""
    try:
        cols = frappe.db.get_table_columns("GL Entry")
        return fieldname in cols
    except Exception:
        return False


# ─── Currency conversion (single-rate) ───────────────────────────────────


def get_company_currency(company: str) -> str:
    """Return the default currency of a company (e.g. 'SAR')."""
    try:
        return frappe.db.get_value("Company", company, "default_currency") or ""
    except Exception:
        return ""


def get_conversion_rate(from_currency: str, to_currency: str, as_of_date: str) -> float:
    """Single-rate FX lookup: how many `to_currency` units per 1 `from_currency`.

    Looks up ERPNext's Currency Exchange records for the most recent rate on
    or before `as_of_date`. Returns 1.0 when the currencies match or no rate
    is found (caller should surface a clear "no rate" note in that case).

    This is the single-rate model: every figure in the report converts at the
    one as-of-date closing rate. Not technically exact for flow accounts
    (which strictly want period-average rates) but transparent and labelled.
    """
    if not from_currency or not to_currency or from_currency == to_currency:
        return 1.0
    # Most recent Currency Exchange on or before the date.
    try:
        rows = frappe.get_all(
            "Currency Exchange",
            filters={
                "from_currency": from_currency,
                "to_currency": to_currency,
                "date": ["<=", as_of_date],
            },
            fields=["exchange_rate"],
            order_by="date desc",
            limit_page_length=1,
        )
        if rows and rows[0].get("exchange_rate"):
            return flt(rows[0]["exchange_rate"])
    except Exception:
        pass
    # Try the inverse pair and reciprocate.
    try:
        rows = frappe.get_all(
            "Currency Exchange",
            filters={
                "from_currency": to_currency,
                "to_currency": from_currency,
                "date": ["<=", as_of_date],
            },
            fields=["exchange_rate"],
            order_by="date desc",
            limit_page_length=1,
        )
        if rows and rows[0].get("exchange_rate"):
            r = flt(rows[0]["exchange_rate"])
            if r:
                return 1.0 / r
    except Exception:
        pass
    return 1.0  # caller decides how to flag a missing rate


# ─── Profit & Loss Statement (chart-of-accounts based) ───────────────────


def run_pnl_statement_engine(
    *,
    company: str,
    from_date: str,
    to_date: str,
    cost_center: str | list | None = None,
    project: str | list | None = None,
    finance_book: str | None = None,
    dimension_filters: dict | None = None,
    show_group_accounts: bool = True,
    show_zero_values: bool = False,
    presentation_currency: str | None = None,
) -> dict[str, Any]:
    """Chart-of-accounts Profit & Loss for a date range.

    Reads Income and Expense accounts straight from the chart of accounts in
    tree order (no flag mapping). Period activity only — debit/credit net
    within [from_date, to_date].

    Sign convention:
      Income  shown positive  (credit-natural → credit minus debit)
      Expense shown positive  (debit-natural  → debit minus credit)
      Net Profit = Total Income - Total Expense

    Result:
        {
            "accounts": [{name, label, code, parent, is_group, root_type,
                          account_type, depth, lft, rgt, amount}, ...],
            "summary": {
                "total_income": float,
                "total_expense": float,
                "net_profit": float,     # positive = profit, negative = loss
                "is_loss": bool,
            },
            "currency": {...},
        }
    """
    coa = _load_chart_of_accounts(company)
    coa = [a for a in coa if (a.get("root_type") or "") in {"Income", "Expense"}]
    coa_by_name = {a["name"]: a for a in coa}

    depth_cache: dict[str, int] = {}
    def _depth(acc_name: str) -> int:
        if acc_name in depth_cache:
            return depth_cache[acc_name]
        a = coa_by_name.get(acc_name)
        if not a or not a.get("parent_account"):
            depth_cache[acc_name] = 0
            return 0
        d = _depth(a["parent_account"]) + 1
        depth_cache[acc_name] = d
        return d

    # Period activity for every account in the window.
    period_sums = _gl_sums_per_account(
        company=company, from_date=from_date, to_date=to_date,
        cost_center=cost_center, project=project,
        finance_book=finance_book, dimension_filters=dimension_filters,
    )

    # Leaf rows. Income = credit - debit; Expense = debit - credit. Both
    # presented as positive when they're "normal" for that account type.
    leaf_amount: dict[str, float] = {}
    leaf_rows: dict[str, dict] = {}
    for a in coa:
        if a.get("is_group"):
            continue
        s = period_sums.get(a["name"], {"debit": 0.0, "credit": 0.0})
        rt = a.get("root_type")
        if rt == "Income":
            amt = flt(s["credit"]) - flt(s["debit"])
        else:  # Expense
            amt = flt(s["debit"]) - flt(s["credit"])
        leaf_amount[a["name"]] = amt
        leaf_rows[a["name"]] = {
            "name": a["name"],
            "label": a.get("account_name") or a["name"],
            "code": a.get("account_number") or "",
            "parent": a.get("parent_account") or "",
            "is_group": 0,
            "root_type": rt or "",
            "account_type": a.get("account_type") or "",
            "depth": _depth(a["name"]),
            "lft": a.get("lft") or 0,
            "rgt": a.get("rgt") or 0,
            "amount": round(amt, 2),
        }

    # Group rows: sum descendant leaves by lft/rgt range.
    group_rows: dict[str, dict] = {}
    for a in coa:
        if not a.get("is_group"):
            continue
        total = 0.0
        for leaf in coa:
            if leaf.get("is_group"):
                continue
            if leaf["lft"] >= a["lft"] and leaf["rgt"] <= a["rgt"]:
                total += leaf_amount.get(leaf["name"], 0.0)
        group_rows[a["name"]] = {
            "name": a["name"],
            "label": a.get("account_name") or a["name"],
            "code": a.get("account_number") or "",
            "parent": a.get("parent_account") or "",
            "is_group": 1,
            "root_type": a.get("root_type") or "",
            "account_type": a.get("account_type") or "",
            "depth": _depth(a["name"]),
            "lft": a.get("lft") or 0,
            "rgt": a.get("rgt") or 0,
            "amount": round(total, 2),
        }

    accounts: list[dict] = []
    for a in coa:
        n = a["name"]
        if n in leaf_rows:
            accounts.append(leaf_rows[n])
        elif n in group_rows:
            accounts.append(group_rows[n])

    # Currency conversion (single-rate, as-of to_date).
    company_currency = get_company_currency(company)
    conversion_rate = 1.0
    rate_missing = False
    if presentation_currency and presentation_currency != company_currency:
        conversion_rate = get_conversion_rate(company_currency, presentation_currency, to_date)
        if conversion_rate == 1.0:
            rate_missing = True
    if conversion_rate != 1.0:
        for r in accounts:
            r["amount"] = round(r["amount"] * conversion_rate, 2)

    # Summary — sum leaf rows by root type.
    total_income = round(sum(
        r["amount"] for r in accounts if not r["is_group"] and r["root_type"] == "Income"
    ), 2)
    total_expense = round(sum(
        r["amount"] for r in accounts if not r["is_group"] and r["root_type"] == "Expense"
    ), 2)
    net_profit = round(total_income - total_expense, 2)

    # Row filters — after summary so totals stay complete.
    if not show_zero_values:
        accounts = [r for r in accounts if abs(r["amount"]) > 0.005]
    if not show_group_accounts:
        accounts = [r for r in accounts if not r["is_group"]]

    return {
        "accounts": accounts,
        "summary": {
            "total_income": total_income,
            "total_expense": total_expense,
            "net_profit": net_profit,
            "is_loss": net_profit < 0,
        },
        "currency": {
            "company_currency": company_currency,
            "presentation_currency": presentation_currency or company_currency,
            "conversion_rate": conversion_rate,
            "rate_missing": rate_missing,
            "as_of_date": to_date,
        },
    }


def run_pnl_statement_pivot_engine(
    *,
    company: str,
    from_date: str,
    to_date: str,
    pivot_by: str,
    finance_book: str | None = None,
    show_group_accounts: bool = True,
    show_zero_values: bool = False,
    presentation_currency: str | None = None,
) -> dict[str, Any]:
    """Dimension-pivot variant of the CoA P&L: account tree as rows, one
    column per dimension value (cost center / department / project) plus a
    Total column.

    pivot_by: 'cost_center' | 'department' | 'project'.

    Returns:
        {
            "accounts": [{...account meta..., by_dim: {dim: amount}, total: amount}],
            "dimensions": [{name, label}, ...],
            "summary": {by_dim totals + total income/expense/net per dimension},
            "currency": {...},
        }
    """
    _validate_pivot_by(pivot_by)

    coa = _load_chart_of_accounts(company)
    coa = [a for a in coa if (a.get("root_type") or "") in {"Income", "Expense"}]
    coa_by_name = {a["name"]: a for a in coa}

    depth_cache: dict[str, int] = {}
    def _depth(acc_name: str) -> int:
        if acc_name in depth_cache:
            return depth_cache[acc_name]
        a = coa_by_name.get(acc_name)
        if not a or not a.get("parent_account"):
            depth_cache[acc_name] = 0
            return 0
        d = _depth(a["parent_account"]) + 1
        depth_cache[acc_name] = d
        return d

    # Discover dimension values present in the GL for this window. We query
    # the dimension column directly and group by it, so empty dimensions
    # don't produce columns.
    dim_col = pivot_by  # cost_center / department / project are all GL columns
    if not _gl_entry_has_column(dim_col):
        frappe.throw(f"GL Entry has no '{dim_col}' column on this bench.")

    # One SQL: net amount per (account, dimension_value).
    conds = [
        "g.company = %(company)s", "g.is_cancelled = 0",
        "g.posting_date >= %(from_date)s", "g.posting_date <= %(to_date)s",
    ]
    params: dict[str, Any] = {"company": company, "from_date": from_date, "to_date": to_date}
    if finance_book:
        conds.append("g.finance_book = %(finance_book)s")
        params["finance_book"] = finance_book

    sql = f"""
        SELECT g.account AS account,
               g.`{dim_col}` AS dim_value,
               SUM(g.debit) AS debit,
               SUM(g.credit) AS credit
        FROM `tabGL Entry` g
        WHERE {' AND '.join(conds)}
        GROUP BY g.account, g.`{dim_col}`
    """
    rows = frappe.db.sql(sql, params, as_dict=True)

    # account → {dim_value → net amount (signed by root type)}
    root_by_account = {a["name"]: a.get("root_type") for a in coa}
    per_account: dict[str, dict[str, float]] = {}
    dim_values_seen: set[str] = set()
    for r in rows:
        acc = r["account"]
        rt = root_by_account.get(acc)
        if rt not in {"Income", "Expense"}:
            continue
        dv = r["dim_value"] or "(Unassigned)"
        dim_values_seen.add(dv)
        if rt == "Income":
            amt = flt(r["credit"]) - flt(r["debit"])
        else:
            amt = flt(r["debit"]) - flt(r["credit"])
        per_account.setdefault(acc, {})[dv] = per_account.get(acc, {}).get(dv, 0.0) + amt

    dim_values = sorted(dim_values_seen)

    # Currency conversion.
    company_currency = get_company_currency(company)
    conversion_rate = 1.0
    rate_missing = False
    if presentation_currency and presentation_currency != company_currency:
        conversion_rate = get_conversion_rate(company_currency, presentation_currency, to_date)
        if conversion_rate == 1.0:
            rate_missing = True

    # Leaf rows with per-dimension amounts.
    leaf_by_dim: dict[str, dict[str, float]] = {}
    for a in coa:
        if a.get("is_group"):
            continue
        dims = per_account.get(a["name"], {})
        if conversion_rate != 1.0:
            dims = {k: v * conversion_rate for k, v in dims.items()}
        leaf_by_dim[a["name"]] = dims

    # Build rows including group rollups.
    out_rows: list[dict] = []
    for a in coa:
        meta = {
            "name": a["name"],
            "label": a.get("account_name") or a["name"],
            "code": a.get("account_number") or "",
            "parent": a.get("parent_account") or "",
            "is_group": 1 if a.get("is_group") else 0,
            "root_type": a.get("root_type") or "",
            "depth": _depth(a["name"]),
            "lft": a.get("lft") or 0,
            "rgt": a.get("rgt") or 0,
        }
        by_dim: dict[str, float] = {}
        if a.get("is_group"):
            for leaf in coa:
                if leaf.get("is_group"):
                    continue
                if leaf["lft"] >= a["lft"] and leaf["rgt"] <= a["rgt"]:
                    for dv, amt in leaf_by_dim.get(leaf["name"], {}).items():
                        by_dim[dv] = by_dim.get(dv, 0.0) + amt
        else:
            by_dim = dict(leaf_by_dim.get(a["name"], {}))
        meta["by_dim"] = {dv: round(by_dim.get(dv, 0.0), 2) for dv in dim_values}
        meta["total"] = round(sum(by_dim.values()), 2)
        out_rows.append(meta)

    # Summary per dimension.
    def _sum(rt: str, dv: str | None) -> float:
        total = 0.0
        for r in out_rows:
            if r["is_group"] or r["root_type"] != rt:
                continue
            total += r["total"] if dv is None else r["by_dim"].get(dv, 0.0)
        return round(total, 2)

    summary = {"by_dim": {}, "total": {}}
    for dv in dim_values:
        inc = _sum("Income", dv)
        exp = _sum("Expense", dv)
        summary["by_dim"][dv] = {
            "total_income": inc, "total_expense": exp,
            "net_profit": round(inc - exp, 2),
        }
    t_inc = _sum("Income", None)
    t_exp = _sum("Expense", None)
    summary["total"] = {
        "total_income": t_inc, "total_expense": t_exp,
        "net_profit": round(t_inc - t_exp, 2), "is_loss": (t_inc - t_exp) < 0,
    }

    # Row filters.
    if not show_zero_values:
        out_rows = [r for r in out_rows if abs(r["total"]) > 0.005]
    if not show_group_accounts:
        out_rows = [r for r in out_rows if not r["is_group"]]

    return {
        "accounts": out_rows,
        "dimensions": [{"name": dv, "label": dv} for dv in dim_values],
        "summary": summary,
        "currency": {
            "company_currency": company_currency,
            "presentation_currency": presentation_currency or company_currency,
            "conversion_rate": conversion_rate,
            "rate_missing": rate_missing,
            "as_of_date": to_date,
        },
    }


_COMBO_SEP = "\u241f"  # unit-separator: safe composite key for (dim1, dim2)
_MAX_COMBO_COLS = 60


def run_pnl_statement_combo_pivot_engine(
    *,
    company: str,
    from_date: str,
    to_date: str,
    dim1: str,
    dim2: str,
    finance_book: str | None = None,
    show_group_accounts: bool = True,
    show_zero_values: bool = False,
    presentation_currency: str | None = None,
) -> dict[str, Any]:
    """Combo (two-dimension) variant of the CoA P&L pivot.

    v1.9.93 — returns the SAME shape as run_pnl_statement_pivot_engine (account
    tree rows + Total + one column per "dimension"), except each column is a
    composite (dim1 value × dim2 value). This lets the Combo view reuse the
    exact statement-tree renderer, Excel export and Print of the Dimension view
    — only the column set differs (the presentation), not the structure.
    """
    _validate_pivot_by(dim1)
    _validate_pivot_by(dim2)
    if dim1 == dim2:
        frappe.throw("Combo view requires two different dimensions.")
    for d in (dim1, dim2):
        if not _gl_entry_has_column(d):
            frappe.throw(f"GL Entry has no '{d}' column on this bench.")

    coa = _load_chart_of_accounts(company)
    coa = [a for a in coa if (a.get("root_type") or "") in {"Income", "Expense"}]
    coa_by_name = {a["name"]: a for a in coa}

    depth_cache: dict[str, int] = {}
    def _depth(acc_name: str) -> int:
        if acc_name in depth_cache:
            return depth_cache[acc_name]
        a = coa_by_name.get(acc_name)
        if not a or not a.get("parent_account"):
            depth_cache[acc_name] = 0
            return 0
        d = _depth(a["parent_account"]) + 1
        depth_cache[acc_name] = d
        return d

    conds = [
        "g.company = %(company)s", "g.is_cancelled = 0",
        "g.posting_date >= %(from_date)s", "g.posting_date <= %(to_date)s",
    ]
    params: dict[str, Any] = {"company": company, "from_date": from_date, "to_date": to_date}
    if finance_book:
        conds.append("g.finance_book = %(finance_book)s")
        params["finance_book"] = finance_book

    sql = f"""
        SELECT g.account AS account,
               g.`{dim1}` AS d1, g.`{dim2}` AS d2,
               SUM(g.debit) AS debit, SUM(g.credit) AS credit
        FROM `tabGL Entry` g
        WHERE {' AND '.join(conds)}
        GROUP BY g.account, g.`{dim1}`, g.`{dim2}`
    """
    rows = frappe.db.sql(sql, params, as_dict=True)

    root_by_account = {a["name"]: a.get("root_type") for a in coa}
    per_account: dict[str, dict[str, float]] = {}
    combo_seen: dict[str, tuple[str, str]] = {}
    for r in rows:
        acc = r["account"]
        rt = root_by_account.get(acc)
        if rt not in {"Income", "Expense"}:
            continue
        d1 = r["d1"] or "(Unassigned)"
        d2 = r["d2"] or "(Unassigned)"
        key = f"{d1}{_COMBO_SEP}{d2}"
        combo_seen[key] = (d1, d2)
        amt = (flt(r["credit"]) - flt(r["debit"])) if rt == "Income" else (flt(r["debit"]) - flt(r["credit"]))
        per_account.setdefault(acc, {})[key] = per_account.get(acc, {}).get(key, 0.0) + amt

    # Order columns by absolute magnitude so the most material combinations
    # appear first, then cap to keep the grid readable.
    col_weight: dict[str, float] = {}
    for accmap in per_account.values():
        for k, v in accmap.items():
            col_weight[k] = col_weight.get(k, 0.0) + abs(v)
    combo_keys = sorted(combo_seen.keys(), key=lambda k: (-col_weight.get(k, 0.0), k))
    truncated = len(combo_keys) > _MAX_COMBO_COLS
    combo_keys = combo_keys[:_MAX_COMBO_COLS]

    company_currency = get_company_currency(company)
    conversion_rate = 1.0
    rate_missing = False
    if presentation_currency and presentation_currency != company_currency:
        conversion_rate = get_conversion_rate(company_currency, presentation_currency, to_date)
        if conversion_rate == 1.0:
            rate_missing = True

    leaf_by_combo: dict[str, dict[str, float]] = {}
    for a in coa:
        if a.get("is_group"):
            continue
        dims = per_account.get(a["name"], {})
        if conversion_rate != 1.0:
            dims = {k: v * conversion_rate for k, v in dims.items()}
        leaf_by_combo[a["name"]] = dims

    out_rows: list[dict] = []
    for a in coa:
        meta = {
            "name": a["name"], "label": a.get("account_name") or a["name"],
            "code": a.get("account_number") or "", "parent": a.get("parent_account") or "",
            "is_group": 1 if a.get("is_group") else 0, "root_type": a.get("root_type") or "",
            "depth": _depth(a["name"]), "lft": a.get("lft") or 0, "rgt": a.get("rgt") or 0,
        }
        by_combo: dict[str, float] = {}
        if a.get("is_group"):
            for leaf in coa:
                if leaf.get("is_group"):
                    continue
                if leaf["lft"] >= a["lft"] and leaf["rgt"] <= a["rgt"]:
                    for k, amt in leaf_by_combo.get(leaf["name"], {}).items():
                        by_combo[k] = by_combo.get(k, 0.0) + amt
        else:
            by_combo = dict(leaf_by_combo.get(a["name"], {}))
        meta["by_dim"] = {k: round(by_combo.get(k, 0.0), 2) for k in combo_keys}
        meta["total"] = round(sum(by_combo.get(k, 0.0) for k in combo_keys), 2)
        out_rows.append(meta)

    def _sum(rt: str, key: str | None) -> float:
        total = 0.0
        for r in out_rows:
            if r["is_group"] or r["root_type"] != rt:
                continue
            total += r["total"] if key is None else r["by_dim"].get(key, 0.0)
        return round(total, 2)

    summary = {"by_dim": {}, "total": {}}
    for k in combo_keys:
        inc = _sum("Income", k)
        exp = _sum("Expense", k)
        summary["by_dim"][k] = {"total_income": inc, "total_expense": exp, "net_profit": round(inc - exp, 2)}
    t_inc = _sum("Income", None)
    t_exp = _sum("Expense", None)
    summary["total"] = {
        "total_income": t_inc, "total_expense": t_exp,
        "net_profit": round(t_inc - t_exp, 2), "is_loss": (t_inc - t_exp) < 0,
    }

    if not show_zero_values:
        out_rows = [r for r in out_rows if abs(r["total"]) > 0.005]
    if not show_group_accounts:
        out_rows = [r for r in out_rows if not r["is_group"]]

    dimensions = [{"name": k, "label": f"{combo_seen[k][0]} / {combo_seen[k][1]}"} for k in combo_keys]

    return {
        "accounts": out_rows,
        "dimensions": dimensions,
        "summary": summary,
        "combo": {"dim1": dim1, "dim2": dim2, "truncated": truncated, "shown": len(combo_keys)},
        "currency": {
            "company_currency": company_currency,
            "presentation_currency": presentation_currency or company_currency,
            "conversion_rate": conversion_rate,
            "rate_missing": rate_missing,
            "as_of_date": to_date,
        },
    }


# ─── Trial Balance / Balance Sheet dimension pivots (v1.9.2) ─────────────


def run_trial_balance_pivot_engine(
    *,
    company: str,
    fiscal_year_start: str,
    as_of_date: str,
    pivot_by: str,
    finance_book: str | None = None,
    show_group_accounts: bool = True,
    show_zero_values: bool = False,
    presentation_currency: str | None = None,
) -> dict[str, Any]:
    """Trial Balance pivoted by dimension. One CLOSING BALANCE per dimension
    value (the six Opening/Period/Closing columns collapse to a single net
    closing figure — that's the only readable layout for a dimensioned TB).

    Closing balance = all GL activity up to as_of_date, netted (debit minus
    credit), displayed signed (debit-positive).
    """
    _validate_pivot_by(pivot_by)
    dim_col = pivot_by
    if not _gl_entry_has_column(dim_col):
        frappe.throw(f"GL Entry has no '{dim_col}' column on this bench.")

    coa = _load_chart_of_accounts(company)
    coa_by_name = {a["name"]: a for a in coa}
    depth_cache: dict[str, int] = {}
    def _depth(n: str) -> int:
        if n in depth_cache:
            return depth_cache[n]
        a = coa_by_name.get(n)
        if not a or not a.get("parent_account"):
            depth_cache[n] = 0
            return 0
        d = _depth(a["parent_account"]) + 1
        depth_cache[n] = d
        return d

    # Net closing per (account, dimension value) — all activity up to as_of.
    conds = [
        "g.company = %(company)s", "g.is_cancelled = 0",
        "g.posting_date <= %(as_of)s",
    ]
    params: dict[str, Any] = {"company": company, "as_of": as_of_date}
    if finance_book:
        conds.append("g.finance_book = %(finance_book)s")
        params["finance_book"] = finance_book
    sql = f"""
        SELECT g.account AS account, g.`{dim_col}` AS dim_value,
               SUM(g.debit) AS debit, SUM(g.credit) AS credit
        FROM `tabGL Entry` g
        WHERE {' AND '.join(conds)}
        GROUP BY g.account, g.`{dim_col}`
    """
    rows = frappe.db.sql(sql, params, as_dict=True)

    per_account: dict[str, dict[str, float]] = {}
    dim_values_seen: set[str] = set()
    for r in rows:
        dv = r["dim_value"] or "(Unassigned)"
        dim_values_seen.add(dv)
        net = flt(r["debit"]) - flt(r["credit"])
        per_account.setdefault(r["account"], {})[dv] = \
            per_account.get(r["account"], {}).get(dv, 0.0) + net
    dim_values = sorted(dim_values_seen)

    company_currency = get_company_currency(company)
    conversion_rate = 1.0
    rate_missing = False
    if presentation_currency and presentation_currency != company_currency:
        conversion_rate = get_conversion_rate(company_currency, presentation_currency, as_of_date)
        if conversion_rate == 1.0:
            rate_missing = True

    leaf_by_dim: dict[str, dict[str, float]] = {}
    for a in coa:
        if a.get("is_group"):
            continue
        dims = per_account.get(a["name"], {})
        if conversion_rate != 1.0:
            dims = {k: v * conversion_rate for k, v in dims.items()}
        leaf_by_dim[a["name"]] = dims

    out_rows: list[dict] = []
    for a in coa:
        meta = {
            "name": a["name"], "label": a.get("account_name") or a["name"],
            "code": a.get("account_number") or "", "parent": a.get("parent_account") or "",
            "is_group": 1 if a.get("is_group") else 0,
            "root_type": a.get("root_type") or "", "depth": _depth(a["name"]),
            "lft": a.get("lft") or 0, "rgt": a.get("rgt") or 0,
        }
        by_dim: dict[str, float] = {}
        if a.get("is_group"):
            for leaf in coa:
                if leaf.get("is_group"):
                    continue
                if leaf["lft"] >= a["lft"] and leaf["rgt"] <= a["rgt"]:
                    for dv, amt in leaf_by_dim.get(leaf["name"], {}).items():
                        by_dim[dv] = by_dim.get(dv, 0.0) + amt
        else:
            by_dim = dict(leaf_by_dim.get(a["name"], {}))
        meta["by_dim"] = {dv: round(by_dim.get(dv, 0.0), 2) for dv in dim_values}
        meta["total"] = round(sum(by_dim.values()), 2)
        out_rows.append(meta)

    if not show_zero_values:
        out_rows = [r for r in out_rows if abs(r["total"]) > 0.005]
    if not show_group_accounts:
        out_rows = [r for r in out_rows if not r["is_group"]]

    return {
        "accounts": out_rows,
        "dimensions": [{"name": dv, "label": dv} for dv in dim_values],
        "currency": {
            "company_currency": company_currency,
            "presentation_currency": presentation_currency or company_currency,
            "conversion_rate": conversion_rate, "rate_missing": rate_missing,
            "as_of_date": as_of_date,
        },
    }


def run_balance_sheet_pivot_engine(
    *,
    company: str,
    as_of_date: str,
    pivot_by: str,
    finance_book: str | None = None,
    show_group_accounts: bool = True,
    show_zero_values: bool = False,
    presentation_currency: str | None = None,
) -> dict[str, Any]:
    """Balance Sheet pivoted by dimension. One balance column per dimension
    value, for Asset / Liability / Equity accounts. Asset shown debit-positive,
    Liability / Equity shown credit-positive (magnitude)."""
    _validate_pivot_by(pivot_by)
    dim_col = pivot_by
    if not _gl_entry_has_column(dim_col):
        frappe.throw(f"GL Entry has no '{dim_col}' column on this bench.")

    coa = _load_chart_of_accounts(company)
    coa = [a for a in coa if (a.get("root_type") or "") in {"Asset", "Liability", "Equity"}]
    coa_by_name = {a["name"]: a for a in coa}
    depth_cache: dict[str, int] = {}
    def _depth(n: str) -> int:
        if n in depth_cache:
            return depth_cache[n]
        a = coa_by_name.get(n)
        if not a or not a.get("parent_account"):
            depth_cache[n] = 0
            return 0
        d = _depth(a["parent_account"]) + 1
        depth_cache[n] = d
        return d

    conds = [
        "g.company = %(company)s", "g.is_cancelled = 0",
        "g.posting_date <= %(as_of)s",
    ]
    params: dict[str, Any] = {"company": company, "as_of": as_of_date}
    if finance_book:
        conds.append("g.finance_book = %(finance_book)s")
        params["finance_book"] = finance_book
    sql = f"""
        SELECT g.account AS account, g.`{dim_col}` AS dim_value,
               SUM(g.debit) AS debit, SUM(g.credit) AS credit
        FROM `tabGL Entry` g
        WHERE {' AND '.join(conds)}
        GROUP BY g.account, g.`{dim_col}`
    """
    rows = frappe.db.sql(sql, params, as_dict=True)

    root_by_account = {a["name"]: a.get("root_type") for a in coa}
    per_account: dict[str, dict[str, float]] = {}
    dim_values_seen: set[str] = set()
    for r in rows:
        rt = root_by_account.get(r["account"])
        if rt not in {"Asset", "Liability", "Equity"}:
            continue
        dv = r["dim_value"] or "(Unassigned)"
        dim_values_seen.add(dv)
        net = flt(r["debit"]) - flt(r["credit"])
        # Asset = debit-positive; Liability/Equity = credit-positive.
        val = net if rt == "Asset" else -net
        per_account.setdefault(r["account"], {})[dv] = \
            per_account.get(r["account"], {}).get(dv, 0.0) + val
    dim_values = sorted(dim_values_seen)

    company_currency = get_company_currency(company)
    conversion_rate = 1.0
    rate_missing = False
    if presentation_currency and presentation_currency != company_currency:
        conversion_rate = get_conversion_rate(company_currency, presentation_currency, as_of_date)
        if conversion_rate == 1.0:
            rate_missing = True

    leaf_by_dim: dict[str, dict[str, float]] = {}
    for a in coa:
        if a.get("is_group"):
            continue
        dims = per_account.get(a["name"], {})
        if conversion_rate != 1.0:
            dims = {k: v * conversion_rate for k, v in dims.items()}
        leaf_by_dim[a["name"]] = dims

    out_rows: list[dict] = []
    for a in coa:
        meta = {
            "name": a["name"], "label": a.get("account_name") or a["name"],
            "code": a.get("account_number") or "", "parent": a.get("parent_account") or "",
            "is_group": 1 if a.get("is_group") else 0,
            "root_type": a.get("root_type") or "", "depth": _depth(a["name"]),
            "lft": a.get("lft") or 0, "rgt": a.get("rgt") or 0,
        }
        by_dim: dict[str, float] = {}
        if a.get("is_group"):
            for leaf in coa:
                if leaf.get("is_group"):
                    continue
                if leaf["lft"] >= a["lft"] and leaf["rgt"] <= a["rgt"]:
                    for dv, amt in leaf_by_dim.get(leaf["name"], {}).items():
                        by_dim[dv] = by_dim.get(dv, 0.0) + amt
        else:
            by_dim = dict(leaf_by_dim.get(a["name"], {}))
        meta["by_dim"] = {dv: round(by_dim.get(dv, 0.0), 2) for dv in dim_values}
        meta["total"] = round(sum(by_dim.values()), 2)
        out_rows.append(meta)

    if not show_zero_values:
        out_rows = [r for r in out_rows if abs(r["total"]) > 0.005]
    if not show_group_accounts:
        out_rows = [r for r in out_rows if not r["is_group"]]

    return {
        "accounts": out_rows,
        "dimensions": [{"name": dv, "label": dv} for dv in dim_values],
        "currency": {
            "company_currency": company_currency,
            "presentation_currency": presentation_currency or company_currency,
            "conversion_rate": conversion_rate, "rate_missing": rate_missing,
            "as_of_date": as_of_date,
        },
    }


def run_balance_sheet_combo_pivot_engine(
    *,
    company: str,
    as_of_date: str,
    dim1: str,
    dim2: str,
    finance_book: str | None = None,
    show_group_accounts: bool = True,
    show_zero_values: bool = False,
    presentation_currency: str | None = None,
) -> dict[str, Any]:
    """Combo (two-dimension) Balance Sheet pivot. Same account-tree shape as
    run_balance_sheet_pivot_engine, with one closing-balance column per
    (dim1 × dim2) combination — so the Combo view reuses the Dimension view's
    statement tree, Excel and Print. Asset debit-positive, Liability/Equity
    credit-positive (magnitude)."""
    _validate_pivot_by(dim1)
    _validate_pivot_by(dim2)
    if dim1 == dim2:
        frappe.throw("Combo view requires two different dimensions.")
    for d in (dim1, dim2):
        if not _gl_entry_has_column(d):
            frappe.throw(f"GL Entry has no '{d}' column on this bench.")

    coa = _load_chart_of_accounts(company)
    coa = [a for a in coa if (a.get("root_type") or "") in {"Asset", "Liability", "Equity"}]
    coa_by_name = {a["name"]: a for a in coa}
    depth_cache: dict[str, int] = {}
    def _depth(n: str) -> int:
        if n in depth_cache:
            return depth_cache[n]
        a = coa_by_name.get(n)
        if not a or not a.get("parent_account"):
            depth_cache[n] = 0
            return 0
        d = _depth(a["parent_account"]) + 1
        depth_cache[n] = d
        return d

    conds = ["g.company = %(company)s", "g.is_cancelled = 0", "g.posting_date <= %(as_of)s"]
    params: dict[str, Any] = {"company": company, "as_of": as_of_date}
    if finance_book:
        conds.append("g.finance_book = %(finance_book)s")
        params["finance_book"] = finance_book
    sql = f"""
        SELECT g.account AS account, g.`{dim1}` AS d1, g.`{dim2}` AS d2,
               SUM(g.debit) AS debit, SUM(g.credit) AS credit
        FROM `tabGL Entry` g
        WHERE {' AND '.join(conds)}
        GROUP BY g.account, g.`{dim1}`, g.`{dim2}`
    """
    rows = frappe.db.sql(sql, params, as_dict=True)

    root_by_account = {a["name"]: a.get("root_type") for a in coa}
    per_account: dict[str, dict[str, float]] = {}
    combo_seen: dict[str, tuple[str, str]] = {}
    for r in rows:
        rt = root_by_account.get(r["account"])
        if rt not in {"Asset", "Liability", "Equity"}:
            continue
        d1 = r["d1"] or "(Unassigned)"
        d2 = r["d2"] or "(Unassigned)"
        key = f"{d1}{_COMBO_SEP}{d2}"
        combo_seen[key] = (d1, d2)
        net = flt(r["debit"]) - flt(r["credit"])
        val = net if rt == "Asset" else -net
        per_account.setdefault(r["account"], {})[key] = per_account.get(r["account"], {}).get(key, 0.0) + val

    col_weight: dict[str, float] = {}
    for accmap in per_account.values():
        for k, v in accmap.items():
            col_weight[k] = col_weight.get(k, 0.0) + abs(v)
    combo_keys = sorted(combo_seen.keys(), key=lambda k: (-col_weight.get(k, 0.0), k))
    truncated = len(combo_keys) > _MAX_COMBO_COLS
    combo_keys = combo_keys[:_MAX_COMBO_COLS]

    company_currency = get_company_currency(company)
    conversion_rate = 1.0
    rate_missing = False
    if presentation_currency and presentation_currency != company_currency:
        conversion_rate = get_conversion_rate(company_currency, presentation_currency, as_of_date)
        if conversion_rate == 1.0:
            rate_missing = True

    leaf_by_combo: dict[str, dict[str, float]] = {}
    for a in coa:
        if a.get("is_group"):
            continue
        dims = per_account.get(a["name"], {})
        if conversion_rate != 1.0:
            dims = {k: v * conversion_rate for k, v in dims.items()}
        leaf_by_combo[a["name"]] = dims

    out_rows: list[dict] = []
    for a in coa:
        meta = {
            "name": a["name"], "label": a.get("account_name") or a["name"],
            "code": a.get("account_number") or "", "parent": a.get("parent_account") or "",
            "is_group": 1 if a.get("is_group") else 0,
            "root_type": a.get("root_type") or "", "depth": _depth(a["name"]),
            "lft": a.get("lft") or 0, "rgt": a.get("rgt") or 0,
        }
        by_combo: dict[str, float] = {}
        if a.get("is_group"):
            for leaf in coa:
                if leaf.get("is_group"):
                    continue
                if leaf["lft"] >= a["lft"] and leaf["rgt"] <= a["rgt"]:
                    for k, amt in leaf_by_combo.get(leaf["name"], {}).items():
                        by_combo[k] = by_combo.get(k, 0.0) + amt
        else:
            by_combo = dict(leaf_by_combo.get(a["name"], {}))
        meta["by_dim"] = {k: round(by_combo.get(k, 0.0), 2) for k in combo_keys}
        meta["total"] = round(sum(by_combo.get(k, 0.0) for k in combo_keys), 2)
        out_rows.append(meta)

    if not show_zero_values:
        out_rows = [r for r in out_rows if abs(r["total"]) > 0.005]
    if not show_group_accounts:
        out_rows = [r for r in out_rows if not r["is_group"]]

    dimensions = [{"name": k, "label": f"{combo_seen[k][0]} / {combo_seen[k][1]}"} for k in combo_keys]
    return {
        "accounts": out_rows,
        "dimensions": dimensions,
        "combo": {"dim1": dim1, "dim2": dim2, "truncated": truncated, "shown": len(combo_keys)},
        "currency": {
            "company_currency": company_currency,
            "presentation_currency": presentation_currency or company_currency,
            "conversion_rate": conversion_rate, "rate_missing": rate_missing,
            "as_of_date": as_of_date,
        },
    }


# ─── Combo view for balance reports (v1.9.63) ────────────────────────────


def run_balance_combo_engine(
    *,
    company: str,
    as_of_date: str,
    from_date: str | None,
    dim1: str,
    dim2: str,
    coa: list[dict],
    root_types: list[str] | None = None,
    cost_center: str | list | None = None,
    project: str | list | None = None,
    department: str | list | None = None,
    branch: str | list | None = None,
    finance_book: str | None = None,
    dimension_filters: dict | None = None,
    show_zero_values: int = 0,
) -> dict:
    """Combo view for Trial Balance and Balance Sheet.

    v1.9.63 — emits closing balance per (account × dim1 × dim2) as of
    `as_of_date`. Sign convention follows the underlying balance type:
    Asset/Expense shown debit-positive, Liability/Equity/Income shown
    credit-positive (matches what TB/BS columns show in the period view).

    `root_types` constrains which accounts contribute. None = all root
    types (Trial Balance). ['Asset','Liability','Equity'] = Balance Sheet.

    Like the P&L combo, empty tuples are dropped by default. Native and
    custom dimension filters apply on top of the pivot dimensions.

    Returns the same shape as execute_combo_report so the frontend
    renders both via a single component.
    """
    _validate_pivot_by(dim1)
    _validate_pivot_by(dim2)
    if not _gl_entry_has_column(dim1) or not _gl_entry_has_column(dim2):
        frappe.throw(
            f"Combo view requires both dimensions to exist as GL Entry columns. "
            f"Missing: {[d for d in (dim1, dim2) if not _gl_entry_has_column(d)]}"
        )

    # Constrain accounts by root_type if specified (BS = Asset/Liability/Equity).
    if root_types:
        accounts = [a for a in coa if (a.get("root_type") or "") in set(root_types)]
    else:
        accounts = list(coa)
    # Only leaf accounts contribute (groups are aggregates of their leaves).
    leaf_names = [a["name"] for a in accounts if not a.get("is_group")]
    if not leaf_names:
        return {"view": "combo", "dimensions_picked": [dim1, dim2], "rows": []}

    # Index account → root_type so we can sign-correct each row.
    acc_root: dict[str, str] = {a["name"]: (a.get("root_type") or "") for a in coa}

    # Build WHERE clauses defensively. dim1 and dim2 are validated against
    # the dimension whitelist above, so backtick injection is safe.
    placeholders = ", ".join(f"%(a{i})s" for i in range(len(leaf_names)))
    conds = [
        "g.is_cancelled = 0",
        f"g.account IN ({placeholders})",
        "g.posting_date <= %(as_of_date)s",
        "g.company = %(company)s",
    ]
    params: dict = {"as_of_date": as_of_date, "company": company}
    for i, a in enumerate(leaf_names):
        params[f"a{i}"] = a
    if from_date:
        # Caller wants activity from a specific start — narrow the range.
        # Used by some TB variants for "period activity" semantics.
        conds.append("g.posting_date >= %(from_date)s")
        params["from_date"] = from_date

    _apply_in_clause(conds, params, "cost_center", cost_center, "cost_center")
    _apply_in_clause(conds, params, "project", project, "project")
    _apply_extra_gl_filters(
        conds, params,
        finance_book=finance_book,
        dimension_filters=dimension_filters,
    )

    # The query: SUM(debit - credit) grouped by (account, dim1, dim2).
    # Debit-positive raw sum; we sign-flip below per root_type so display
    # matches TB/BS convention.
    sql = f"""
        SELECT g.account AS account,
               g.`{dim1}` AS d1,
               g.`{dim2}` AS d2,
               SUM(g.debit - g.credit) AS bal
        FROM `tabGL Entry` g
        WHERE {' AND '.join(conds)}
        GROUP BY g.account, g.`{dim1}`, g.`{dim2}`
    """
    rows = frappe.db.sql(sql, params, as_dict=True)

    # Aggregate by (root_type, dim1, dim2) — one combo tuple per
    # (account, dim1, dim2). For TB/BS the "row" is the account itself.
    acc_label: dict[str, str] = {a["name"]: (a.get("account_name") or a["name"]) for a in coa}

    rows_out: list[dict] = []
    for r in rows:
        acc = r["account"]
        rt = acc_root.get(acc, "")
        # Sign convention: Asset/Expense are debit-positive (use raw bal).
        # Liability/Equity/Income are credit-positive (flip sign).
        raw = flt(r.get("bal") or 0)
        if rt in ("Liability", "Equity", "Income"):
            value = -raw
        else:
            value = raw
        # v1.9.63 — drop empty tuples (closing balance of zero).
        if value == 0 and not show_zero_values:
            continue
        d1 = r.get("d1") or "(Unassigned)"
        d2 = r.get("d2") or "(Unassigned)"
        rows_out.append({
            "row_key": acc,
            "row_label": acc_label.get(acc, acc),
            "tuple": {dim1: d1, dim2: d2},
            "value": value,
        })

    # Sort outer dim → inner dim → account.
    rows_out.sort(key=lambda x: (x["tuple"][dim1], x["tuple"][dim2], x["row_label"]))

    return {
        "view": "combo",
        "dimensions_picked": [dim1, dim2],
        "rows": rows_out,
    }


# ─── Multi-period closing balance (v1.9.63) ──────────────────────────────


def run_multi_period_balance(
    *,
    company: str,
    period_boundaries: list[tuple[str, str]],
    accounts: list[str],
    cost_center: str | list | None = None,
    project: str | list | None = None,
    department: str | list | None = None,
    branch: str | list | None = None,
    finance_book: str | None = None,
    dimension_filters: dict | None = None,
) -> dict[str, dict[str, float]]:
    """Cumulative closing balance per account at each period boundary.

    v1.9.63 — for multi-period TB/BS. `period_boundaries` is a list of
    (period_key, end_date_iso) tuples — one per period to compute, in
    chronological order. Returns {account: {period_key: signed_balance}}.

    Implementation: a single SQL query with SUM(CASE WHEN ...) per
    boundary. This is dramatically faster than N separate queries — the
    database scans GL Entry once and produces all cumulative balances in
    a single pass per account.

    Sign convention is debit-positive (raw debit - credit). Callers
    sign-flip per root_type at the row level. This matches the rest of
    the balance engine.
    """
    if not accounts or not period_boundaries:
        return {}

    placeholders = ", ".join(f"%(a{i})s" for i in range(len(accounts)))
    conds = [
        "g.is_cancelled = 0",
        f"g.account IN ({placeholders})",
        "g.company = %(company)s",
    ]
    params: dict = {"company": company}
    for i, a in enumerate(accounts):
        params[f"a{i}"] = a

    _apply_in_clause(conds, params, "cost_center", cost_center, "cost_center")
    _apply_in_clause(conds, params, "project", project, "project")
    _apply_extra_gl_filters(
        conds, params,
        finance_book=finance_book,
        dimension_filters=dimension_filters,
    )

    # Build one SUM(CASE WHEN posting_date <= 'X' THEN debit-credit ELSE 0 END)
    # per period. The CASE evaluates against each row once; aggregation is
    # cheap. This pattern scales to 12+ periods on tables with millions of
    # rows without becoming the slow path.
    case_clauses = []
    for i, (period_key, end_date) in enumerate(period_boundaries):
        pkey = f"p_end_{i}"
        params[pkey] = end_date
        # period_key is safe — caller controls it (m0, m1, q1, ...).
        case_clauses.append(
            f"SUM(CASE WHEN g.posting_date <= %({pkey})s THEN (g.debit - g.credit) ELSE 0 END) AS `{period_key}`"
        )

    select_clause = "g.account AS account,\n               " + ",\n               ".join(case_clauses)
    sql = f"""
        SELECT {select_clause}
        FROM `tabGL Entry` g
        WHERE {' AND '.join(conds)}
        GROUP BY g.account
    """
    rows = frappe.db.sql(sql, params, as_dict=True)

    out: dict[str, dict[str, float]] = {}
    for r in rows:
        acc = r["account"]
        per_period: dict[str, float] = {}
        for period_key, _ in period_boundaries:
            per_period[period_key] = flt(r.get(period_key) or 0)
        out[acc] = per_period
    return out
