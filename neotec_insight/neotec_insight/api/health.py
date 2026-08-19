# Copyright (c) 2026, Neotec Integrated Solution
# Financial Health of the Firm — executive ratio analysis computed from the GL.
# Isolated module; does not touch the report engine.
from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import flt, getdate

# ERPNext account-type conventions used to classify balances.
NON_CURRENT_ASSET_TYPES = {"Fixed Asset", "Capital Work in Progress"}
COGS_TYPES = {"Cost of Goods Sold", "Stock Adjustment"}

# EBIT/EBITDA add-backs. ERPNext has account_type='Depreciation' but NO type for
# interest/financing cost — so depreciation prefers the account_type and only
# falls back to keywords, while interest is matched by a bilingual,
# Islamic-finance-aware keyword set (always Expense-constrained). English-only
# "%interest%" silently missed Arabic charts and KSA Islamic-finance accounts
# (Murabaha/Tawarruq/Ijara/"bank profit"), collapsing EBITDA to net income.
# Extend these for site-specific chart wording.
INTEREST_KEYWORDS = [
    # English / conventional
    "interest", "finance cost", "financing cost", "finance charge",
    "financing charge", "finance expense", "financing expense",
    # Islamic finance (common in KSA / GCC charts)
    "murabaha", "tawarruq", "ijara", "ijarah", "profit on financing",
    "financing profit", "bank profit", "profit paid to bank",
    # Arabic
    "\u0641\u0627\u0626\u062f\u0629", "\u0641\u0648\u0627\u0626\u062f",
    "\u062a\u0643\u0627\u0644\u064a\u0641 \u062a\u0645\u0648\u064a\u0644",
    "\u062a\u0643\u0644\u0641\u0629 \u062a\u0645\u0648\u064a\u0644",
    "\u0645\u0631\u0627\u0628\u062d\u0629", "\u062a\u0648\u0631\u0642",
    "\u0625\u062c\u0627\u0631\u0629", "\u0623\u0631\u0628\u0627\u062d \u062a\u0645\u0648\u064a\u0644",
    "\u0639\u0645\u0648\u0644\u0627\u062a \u062a\u0645\u0648\u064a\u0644",
]
DEPRECIATION_KEYWORDS = [
    "deprecia", "amorti",                       # depreciation / amortisation
    "\u0625\u0647\u0644\u0627\u0643", "\u0627\u0633\u062a\u0647\u0644\u0627\u0643",  # AR depreciation
    "\u0625\u0637\u0641\u0627\u0621",            # AR amortization
]


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


def _default_company():
    return (frappe.defaults.get_user_default("Company")
            or frappe.defaults.get_global_default("company")
            or (frappe.get_all("Company", limit=1, pluck="name") or [None])[0])


def _metrics(company, fy_start, fy_end):
    """Aggregate GL into the building blocks for ratios.
    Balance-sheet items are cumulative up to fy_end; P&L items cover fy_start..fy_end.
    Sign convention: Asset/Expense are debit-positive; Liability/Equity/Income credit-positive.
    """
    rows = frappe.db.sql(
        """
        SELECT a.root_type AS rt, a.account_type AS at,
               SUM(g.debit - g.credit) AS cum,
               SUM(CASE WHEN g.posting_date >= %(pfrom)s THEN g.debit - g.credit ELSE 0 END) AS per
        FROM `tabGL Entry` g
        JOIN `tabAccount` a ON a.name = g.account
        WHERE g.company = %(company)s AND g.is_cancelled = 0 AND g.posting_date <= %(upto)s
        GROUP BY a.root_type, a.account_type
        """,
        {"company": company, "pfrom": fy_start, "upto": fy_end}, as_dict=True,
    )

    m = dict(assets=0.0, liabilities=0.0, equity=0.0, revenue=0.0, expenses=0.0,
             cogs=0.0, current_assets=0.0, non_current_assets=0.0, inventory=0.0,
             cash=0.0, receivables=0.0, payables=0.0)
    for r in rows:
        rt, at = r.rt, (r.at or "")
        cum, per = flt(r.cum), flt(r.per)
        if rt == "Asset":
            m["assets"] += cum
            if at in NON_CURRENT_ASSET_TYPES:
                m["non_current_assets"] += cum
            if at == "Stock":
                m["inventory"] += cum
            if at in ("Bank", "Cash"):
                m["cash"] += cum
            if at == "Receivable":
                m["receivables"] += cum
        elif rt == "Liability":
            if at == "Equity":
                # Equity accounts (Capital, Retained Earnings) sometimes sit under a
                # Liability root group — classify them as Equity, not debt.
                m["equity"] += -cum
            else:
                m["liabilities"] += -cum
                if at == "Payable":
                    m["payables"] += -cum
        elif rt == "Equity":
            m["equity"] += -cum
        elif rt == "Income":
            m["revenue"] += -per
        elif rt == "Expense":
            m["expenses"] += per
            if at in COGS_TYPES:
                m["cogs"] += per

    # v2.26.0 — Classification Studio: accounts the user tagged as COGS count
    # into cost of sales even when their account_type is blank (the common
    # real-world case: direct-cost accounts left untyped in the CoA). Accounts
    # already typed COGS/Stock Adjustment are excluded here to avoid double
    # counting with the account_type grouping above.
    try:
        from .classify import tagged_accounts
        cogs_tagged = tagged_accounts(company, "cogs")
        if cogs_tagged:
            extra = frappe.db.sql(
                """SELECT COALESCE(SUM(g.debit - g.credit), 0) AS per
                   FROM `tabGL Entry` g JOIN `tabAccount` a ON a.name = g.account
                   WHERE g.company = %(company)s AND g.is_cancelled = 0
                     AND g.account IN %(accts)s
                     AND a.account_type NOT IN ('Cost of Goods Sold', 'Stock Adjustment')
                     AND g.posting_date BETWEEN %(pfrom)s AND %(upto)s""",
                {"company": company, "accts": tuple(cogs_tagged),
                 "pfrom": fy_start, "upto": fy_end}, as_dict=True)
            m["cogs"] += flt(extra[0]["per"]) if extra else 0.0
    except Exception:
        pass  # classification is an enhancement layer — never break the metrics

    m["current_assets"] = m["assets"] - m["non_current_assets"]
    # Long-term debt isn't reliably typed in ERPNext, so current liabilities ≈
    # total liabilities (common SME simplification; documented in the UI).
    m["current_liabilities"] = m["liabilities"]
    m["net_income"] = m["revenue"] - m["expenses"]
    m["gross_profit"] = m["revenue"] - m["cogs"]

    # ── Interest & depreciation add-backs for EBIT/EBITDA ───────────────────
    # Precedence, most authoritative first:
    #   1. Explicit per-account tags (Insight EBITDA Addback) — user-set, exact,
    #      independent of name or account_type.
    #   2. Depreciation only: account_type == 'Depreciation' (structural).
    #   3. Keyword fallback (bilingual, Islamic-finance aware) — used ONLY when
    #      the user has tagged/typed nothing in that category, so once a chart is
    #      curated, heuristic name-matching can no longer introduce noise.
    expense_accounts = frappe.get_all(
        "Account",
        filters={"company": company, "is_group": 0, "root_type": "Expense"},
        fields=["name", "account_name"],
    )
    tags = frappe.get_all(
        "Insight EBITDA Addback",
        filters={"company": company},
        fields=["account", "category"],
    )
    interest_explicit = {t["account"] for t in tags if t["category"] == "Interest"}
    deprec_explicit = {t["account"] for t in tags if t["category"] == "Depreciation"}
    typed_deprec = set(frappe.get_all(
        "Account",
        filters={"company": company, "is_group": 0, "root_type": "Expense", "account_type": "Depreciation"},
        pluck="name",
    ))

    def _kw_match(keywords: list[str]) -> set:
        kws = [k.lower() for k in keywords]
        return {a["name"] for a in expense_accounts
                if a.get("account_name") and any(k in a["account_name"].lower() for k in kws)}

    interest_set = set(interest_explicit) or _kw_match(INTEREST_KEYWORDS)
    deprec_set = (set(deprec_explicit) | typed_deprec) or _kw_match(DEPRECIATION_KEYWORDS)
    # An account contributes to exactly one add-back. If it somehow lands in both
    # buckets (e.g. tagged Interest yet account_type='Depreciation'), interest wins
    # — matching the precedence shown on the EBITDA add-backs management screen.
    deprec_set = deprec_set - interest_set

    def _sum_account_set(accounts) -> float:
        accounts = list(accounts)
        if not accounts:
            return 0.0
        ph = ", ".join(["%s"] * len(accounts))
        v = frappe.db.sql(
            f"""SELECT SUM(CASE WHEN g.posting_date >= %s THEN g.debit - g.credit ELSE 0 END)
                FROM `tabGL Entry` g
                WHERE g.company = %s AND g.is_cancelled = 0 AND g.posting_date <= %s
                  AND g.account IN ({ph})""",
            [fy_start, company, fy_end, *accounts])
        return flt(v[0][0]) if v and v[0] else 0.0

    m["interest"] = _sum_account_set(interest_set)
    m["depreciation"] = _sum_account_set(deprec_set)
    m["ebit"] = m["net_income"] + m["interest"]
    m["ebitda"] = m["ebit"] + m["depreciation"]
    return m


def _safe(n, d):
    n, d = flt(n), flt(d)
    return (n / d) if d else None


def _kpi(label, value, fmt, bands, higher_better=True, note="", detail=None, warn=""):
    """bands = (green_threshold, amber_threshold). Returns a KPI dict with status."""
    status, score = "na", None
    if value is not None:
        g, a = bands
        if higher_better:
            status = "green" if value >= g else "amber" if value >= a else "red"
        else:
            status = "green" if value <= g else "amber" if value <= a else "red"
        score = {"green": 100, "amber": 65, "red": 30}[status]
    return {"label": label, "value": value, "fmt": fmt, "status": status,
            "score": score, "note": note, "detail": detail, "warn": warn}


def _D(formula, parts):
    """Build an evidence block. parts = list of (label, value, bucket|None)."""
    return {"formula": formula,
            "parts": [{"label": p[0], "value": (round(flt(p[1]), 2) if p[1] is not None else None),
                       "bucket": (p[2] if len(p) > 2 else None)} for p in parts]}


def _ratios(m):
    ca, cl = m["current_assets"], m["current_liabilities"]
    rev, ni = m["revenue"], m["net_income"]
    inv, cogs = m["inventory"], m["cogs"]
    ar, ap = m["receivables"], m["payables"]

    # ── Data-quality flags (point at likely account-type misclassification) ──
    cogs_zero = round(flt(cogs), 2) == 0
    recv_zero = round(flt(ar), 2) == 0
    pay_zero = round(flt(ap), 2) == 0
    gm = (_safe(m["gross_profit"], rev) or 0) * 100 if rev else None
    dso = (_safe(ar, rev) or 0) * 365 if rev else None
    inv_days = (_safe(inv, cogs) or 0) * 365 if cogs else None
    pay_days = (_safe(ap, cogs) or 0) * 365 if cogs else None
    ccc = (dso + inv_days - pay_days) if (dso is not None and inv_days is not None and pay_days is not None) else None

    W_COGS = "No 'Cost of Goods Sold' accounts found — classify your cost-of-sales accounts for this to be meaningful."
    gm_warn = (W_COGS if (cogs_zero and rev) else
               ("Margin looks high — some cost-of-sales accounts may not be typed 'Cost of Goods Sold'." if (gm is not None and gm > 60) else ""))
    dso_warn = ("No 'Receivable' accounts found — classify your AR control account." if (recv_zero and rev) else
                ("Unusually high — verify 'Receivable' classification and that the period's revenue is complete." if (dso is not None and dso > 180) else ""))
    invd_warn = (W_COGS if cogs_zero else ("Inventory is zero — verify 'Stock' account classification." if round(flt(inv), 2) == 0 else ""))
    payd_warn = (W_COGS if cogs_zero else
                 ("Negative — the Payable balance is a net debit; verify 'Payable' classification." if (pay_days is not None and pay_days < 0) else
                  ("No 'Payable' accounts found — classify your AP control account." if pay_zero else "")))
    ccc_warn = W_COGS if cogs_zero else ""
    int_warn = "No interest / financing-cost accounts detected for this period (conventional or Islamic-finance)." if round(flt(m["interest"]), 2) == 0 else ""

    liquidity = [
        _kpi("Current Ratio", _safe(ca, cl), "x", (1.5, 1.0),
             detail=_D("Current Assets ÷ Current Liabilities",
                       [("Current Assets", ca, "current_assets"), ("Current Liabilities", cl, "liabilities")])),
        _kpi("Quick Ratio", _safe(ca - inv, cl), "x", (1.0, 0.7),
             detail=_D("(Current Assets − Inventory) ÷ Current Liabilities",
                       [("Current Assets", ca, "current_assets"), ("Inventory", inv, "inventory"), ("Current Liabilities", cl, "liabilities")])),
        _kpi("Working Capital", (ca - cl) if (ca or cl) else None, "money", (0.0001, 0.0),
             detail=_D("Current Assets − Current Liabilities",
                       [("Current Assets", ca, "current_assets"), ("Current Liabilities", cl, "liabilities")])),
    ]
    profitability = [
        _kpi("Gross Profit Margin", gm, "pct", (30, 15), warn=gm_warn,
             detail=_D("(Revenue − COGS) ÷ Revenue × 100",
                       [("Revenue", rev, "revenue"), ("Cost of Goods Sold", cogs, "cogs"), ("Gross Profit", m["gross_profit"], None)])),
        _kpi("Net Profit Margin", (_safe(ni, rev) or 0) * 100 if rev else None, "pct", (10, 0),
             detail=_D("Net Income ÷ Revenue × 100",
                       [("Net Income", ni, None), ("Revenue", rev, "revenue")])),
        _kpi("Return on Assets (ROA)", (_safe(ni, m["assets"]) or 0) * 100 if m["assets"] else None, "pct", (5, 0),
             detail=_D("Net Income ÷ Total Assets × 100",
                       [("Net Income", ni, None), ("Total Assets", m["assets"], "assets")])),
        _kpi("Return on Equity (ROE)", (_safe(ni, m["equity"]) or 0) * 100 if m["equity"] else None, "pct", (10, 0),
             detail=_D("Net Income ÷ Equity × 100",
                       [("Net Income", ni, None), ("Equity", m["equity"], "equity")])),
    ]
    efficiency = [
        _kpi("Days Sales Outstanding (DSO)", dso, "days", (45, 75), higher_better=False, warn=dso_warn,
             detail=_D("Receivables ÷ Revenue × 365", [("Receivables", ar, "receivables"), ("Revenue", rev, "revenue")])),
        _kpi("Inventory Days", inv_days, "days", (60, 120), higher_better=False, warn=invd_warn,
             detail=_D("Inventory ÷ COGS × 365", [("Inventory", inv, "inventory"), ("Cost of Goods Sold", cogs, "cogs")])),
        _kpi("Payable Days", pay_days, "days", (90, 120), higher_better=False, note="Higher can aid cash flow", warn=payd_warn,
             detail=_D("Payables ÷ COGS × 365", [("Payables", ap, "payables"), ("Cost of Goods Sold", cogs, "cogs")])),
        _kpi("Cash Conversion Cycle", ccc, "days", (60, 120), higher_better=False, warn=ccc_warn,
             detail=_D("DSO + Inventory Days − Payable Days",
                       [("DSO", dso, None), ("Inventory Days", inv_days, None), ("Payable Days", pay_days, None)])),
        _kpi("Asset Turnover", _safe(rev, m["assets"]), "x", (1.0, 0.5),
             detail=_D("Revenue ÷ Total Assets", [("Revenue", rev, "revenue"), ("Total Assets", m["assets"], "assets")])),
    ]
    stability = [
        _kpi("Debt-to-Equity", _safe(m["liabilities"], m["equity"]), "x", (1.0, 1.5), higher_better=False,
             detail=_D("Total Liabilities ÷ Equity", [("Total Liabilities", m["liabilities"], "liabilities"), ("Equity", m["equity"], "equity")])),
        _kpi("Debt Ratio", _safe(m["liabilities"], m["assets"]), "x", (0.5, 0.7), higher_better=False,
             detail=_D("Total Liabilities ÷ Total Assets", [("Total Liabilities", m["liabilities"], "liabilities"), ("Total Assets", m["assets"], "assets")])),
        _kpi("Interest Coverage", _safe(m["ebit"], m["interest"]) if m["interest"] else None, "x", (3, 1.5), warn=int_warn,
             detail=_D("EBIT ÷ Interest Expense", [("EBIT", m["ebit"], None), ("Interest Expense", m["interest"], None)])),
    ]
    cashflow = [
        _kpi("Cash & Equivalents", m["cash"] if (m["cash"] or m["assets"]) else None, "money", (0.0001, 0.0),
             detail=_D("Sum of Bank + Cash account balances", [("Bank & Cash", m["cash"], "cash")])),
        _kpi("EBITDA Margin", (_safe(m["ebitda"], rev) or 0) * 100 if rev else None, "pct", (15, 5),
             detail=_D("(Net Income + Interest + Depreciation) ÷ Revenue × 100",
                       [("Net Income", ni, None), ("Interest", m["interest"], None), ("Depreciation", m["depreciation"], None), ("Revenue", rev, "revenue")])),
        _kpi("Operating Cash Flow Ratio (approx)", _safe(m["net_income"] + m["depreciation"], cl), "x", (1.0, 0.5),
             note="Approximation: (Net income + Depreciation) / Current liabilities",
             detail=_D("(Net Income + Depreciation) ÷ Current Liabilities",
                       [("Net Income", ni, None), ("Depreciation", m["depreciation"], None), ("Current Liabilities", cl, "liabilities")])),
    ]

    def _sec(name, q, kpis):
        scores = [k["score"] for k in kpis if k["score"] is not None]
        return {"name": name, "question": q, "kpis": kpis,
                "score": round(sum(scores) / len(scores)) if scores else None}

    return [
        _sec("Liquidity Health", "Can we pay our bills?", liquidity),
        _sec("Profitability Health", "Are we making money?", profitability),
        _sec("Operational Efficiency", "How efficiently do we operate?", efficiency),
        _sec("Financial Stability", "How risky is the business?", stability),
        _sec("Cash Flow Health", "Are we generating real cash?", cashflow),
    ]


def _classify(score):
    if score is None:
        return "—"
    if score >= 90: return "Excellent"
    if score >= 80: return "Very Good"
    if score >= 70: return "Good"
    if score >= 60: return "Fair"
    return "Needs Attention"


def _summary(sections, overall):
    """Deterministic executive commentary from the computed flags."""
    strong = [s["name"] for s in sections if s["score"] and s["score"] >= 80]
    weak = [s["name"] for s in sections if s["score"] and s["score"] < 70]
    reds = [k["label"] for s in sections for k in s["kpis"] if k["status"] == "red"]
    ambers = [k["label"] for s in sections for k in s["kpis"] if k["status"] == "amber"]
    parts = []
    parts.append(_("The firm's overall financial health scores {0}/100 ({1}).").format(overall if overall is not None else "—", _(_classify(overall))))
    if strong:
        parts.append(_("Strengths are evident in {0}.").format(", ".join(_(s) for s in strong)))
    if reds:
        parts.append(_("Immediate attention is needed on: {0}.").format(", ".join(_(r) for r in reds)))
    elif ambers:
        parts.append(_("Watch areas include: {0}.").format(", ".join(_(a) for a in ambers[:4])))
    if weak:
        parts.append(_("The weakest area is {0}; focus improvement efforts there.").format(_(weak[0])))
    if not reds and not ambers and overall and overall >= 80:
        parts.append(_("All key indicators are within healthy targets."))
    return " ".join(parts)


_BUCKETS = {
    # bucket: (root_type|None, account_types set|None, exclude_types set|None, sign, period)
    "assets": ("Asset", None, None, 1, False),
    "current_assets": ("Asset", None, NON_CURRENT_ASSET_TYPES, 1, False),
    "fixed_assets": ("Asset", NON_CURRENT_ASSET_TYPES, None, 1, False),
    "receivables": ("Asset", {"Receivable"}, None, 1, False),
    "inventory": ("Asset", {"Stock"}, None, 1, False),
    "cash": ("Asset", {"Bank", "Cash"}, None, 1, False),
    "liabilities": ("Liability", None, None, -1, False),
    "payables": ("Liability", {"Payable"}, None, -1, False),
    "equity": ("Equity", None, None, -1, False),
    "revenue": ("Income", None, None, -1, True),
    "expenses": ("Expense", None, None, 1, True),
    "cogs": ("Expense", COGS_TYPES, None, 1, True),
}


@frappe.whitelist()
def health_breakdown(company=None, bucket=None, to_date=None):
    """List the individual accounts (with balances) that make up a building
    block — the evidence behind a ratio."""
    company = company or _default_company()
    if not company or bucket not in _BUCKETS:
        return {"rows": [], "total": 0}
    if not frappe.has_permission("GL Entry", "read"):
        frappe.throw(_("Not permitted."))

    fys = frappe.get_all("Fiscal Year", fields=["name", "year_start_date", "year_end_date"],
                         order_by="year_end_date desc", limit_page_length=8)
    if to_date:
        fys = [f for f in fys if getdate(f["year_end_date"]) >= getdate(to_date)] or fys
    if not fys:
        return {"rows": [], "total": 0}
    cur = fys[0]

    rt, types, excl, sign, period = _BUCKETS[bucket]
    conds = ["g.company=%(company)s", "g.is_cancelled=0", "g.posting_date<=%(upto)s"]
    params = {"company": company, "upto": cur["year_end_date"], "pfrom": cur["year_start_date"]}
    if bucket == "equity":
        # Equity may sit under an Equity root OR be typed Equity under a Liability root.
        conds.append("(a.root_type='Equity' OR a.account_type='Equity')")
    elif bucket == "liabilities":
        # Exclude equity-typed accounts that live under the Liability root.
        conds.append("a.root_type='Liability'")
        conds.append("(a.account_type IS NULL OR a.account_type<>'Equity')")
    else:
        if rt:
            conds.append("a.root_type=%(rt)s"); params["rt"] = rt
        if types:
            conds.append("a.account_type IN %(types)s"); params["types"] = tuple(types)
        if excl:
            conds.append("(a.account_type IS NULL OR a.account_type NOT IN %(excl)s)"); params["excl"] = tuple(excl)
    val = ("SUM(CASE WHEN g.posting_date>=%(pfrom)s THEN g.debit-g.credit ELSE 0 END)"
           if period else "SUM(g.debit-g.credit)")
    rows = frappe.db.sql(
        f"""SELECT g.account AS account, a.account_type AS account_type, ({val})*{sign} AS value
            FROM `tabGL Entry` g JOIN `tabAccount` a ON a.name=g.account
            WHERE {' AND '.join(conds)}
            GROUP BY g.account, a.account_type
            HAVING ROUND(value,2) <> 0
            ORDER BY ABS(value) DESC""",
        params, as_dict=True)
    total = round(sum(flt(r["value"]) for r in rows), 2)
    return {"rows": [{"account": r["account"], "account_type": r["account_type"],
                      "value": round(flt(r["value"]), 2)} for r in rows], "total": total}


@frappe.whitelist()
def financial_health(company=None, to_date=None, years=3, fiscal_year=None):
    """Full Financial Health report: current-period ratios by section, overall
    score, executive summary, and a multi-year trend of headline ratios."""
    company = company or _default_company()
    if not company:
        frappe.throw(_("No company found."))
    if not frappe.has_permission("GL Entry", "read"):
        frappe.throw(_("Not permitted."))

    all_fys = frappe.get_all("Fiscal Year", fields=["name", "year_start_date", "year_end_date"],
                             order_by="year_start_date desc", limit_page_length=20)
    if not all_fys:
        frappe.throw(_("No Fiscal Year defined."))

    current = None
    if fiscal_year:
        current = next((f for f in all_fys if f["name"] == fiscal_year), None)
    if current is None and to_date:
        current = next((f for f in all_fys if getdate(f["year_start_date"]) <= getdate(to_date) <= getdate(f["year_end_date"])), None)
    if current is None:
        current = all_fys[0]

    # Trend = current FY + prior fiscal years (oldest → newest)
    older = [f for f in all_fys if getdate(f["year_end_date"]) <= getdate(current["year_end_date"])]
    window = older[:max(int(years or 3), 1)]
    window = list(reversed(window))

    m = _metrics(company, current["year_start_date"], current["year_end_date"])
    sections = _ratios(m)
    sec_scores = [s["score"] for s in sections if s["score"] is not None]
    overall = round(sum(sec_scores) / len(sec_scores)) if sec_scores else None

    trend = []
    for fy in window:
        mm = _metrics(company, fy["year_start_date"], fy["year_end_date"])
        ca, cl, rev = mm["current_assets"], mm["current_liabilities"], mm["revenue"]
        trend.append({
            "year": fy["name"],
            "current_ratio": _safe(ca, cl),
            "net_margin": (_safe(mm["net_income"], rev) or 0) * 100 if rev else None,
            "roe": (_safe(mm["net_income"], mm["equity"]) or 0) * 100 if mm["equity"] else None,
            "dso": (_safe(mm["receivables"], rev) or 0) * 365 if rev else None,
            "debt_equity": _safe(mm["liabilities"], mm["equity"]),
        })

    return {
        "company": company,
        "fiscal_year": current["name"],
        "fiscal_years": [f["name"] for f in all_fys],
        "period": {"from": str(current["year_start_date"]), "to": str(current["year_end_date"]), "label": current["name"]},
        "overall_score": overall,
        "classification": _classify(overall),
        "sections": sections,
        "summary": _summary(sections, overall),
        "trend": trend,
        "raw": {k: round(flt(v), 2) for k, v in m.items()},
    }


@frappe.whitelist()
def health_ai_analysis(company=None, fiscal_year=None, lang="en"):
    """Deeper, AI-written analysis grounded in the computed ratios."""
    _require_read()
    company = company or _default_company()
    data = financial_health(company=company, fiscal_year=fiscal_year)
    ctx = {
        "company": data["company"], "period": data["period"],
        "overall_score": data["overall_score"], "classification": data["classification"],
        "sections": [{"name": s["name"], "score": s["score"],
                      "kpis": [{"label": k["label"], "value": k["value"], "status": k["status"]} for k in s["kpis"]]}
                     for s in data["sections"]],
        "raw": data["raw"], "trend": data["trend"],
    }
    import json
    from neotec_insight.neotec_insight.api.ai import ask_ai
    question = (
        "You are a senior CFO and financial analyst advising an SME owner. Based on the financial "
        "health data provided, write a clear, insightful analysis with these sections, using short "
        "paragraphs and bullet points:\n"
        "1) Overall verdict (2-3 sentences).\n"
        "2) Key strengths (reference the specific ratios and their values).\n"
        "3) Top risks / red flags, with the most likely root causes.\n"
        "4) Benchmark commentary versus healthy targets (Current Ratio >1.5, Quick >1.0, Net Margin growing, "
        "DSO <45, Debt/Equity <1.5, ROE >10%, Interest Coverage >3x).\n"
        "5) A prioritized 90-day action plan (bullet points, most impactful first).\n"
        "Be concrete and practical. If a figure looks implausible (e.g. very high DSO or ~100% gross "
        "margin), note that it likely indicates an account-type misclassification to verify."
    )
    return ask_ai(question=question, company=company, context=json.dumps(ctx, default=str), lang=lang)
