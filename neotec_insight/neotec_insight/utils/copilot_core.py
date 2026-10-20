"""Insight Copilot — the parts that need no site (v2.94.0).

The model never writes SQL and never computes a figure. It picks ONE tool
from TOOLS and fills its arguments; `validate_plan` checks every argument
against the tool's schema before anything runs; the server computes the data
with the user's permissions; the model then explains it. A built-in planner
(`rule_plan`) answers common questions instantly, and is the fallback when
AI is off or returns something unusable.
"""
from __future__ import annotations

import datetime as _dt
import re

DIMENSIONS = ("cost_center", "project", "department", "branch")
ROOT_TYPES = ("Income", "Expense", "Asset", "Liability", "Equity")
VIEWS = ("table", "bar", "line", "pie", "number", "text")
RECORD_DOCTYPES = (
    "Sales Invoice", "Purchase Invoice", "Payment Entry", "Journal Entry", "Sales Order",
    "Purchase Order", "Quotation", "Delivery Note", "Purchase Receipt", "Customer", "Supplier",
    "Item", "Project", "Expense Claim", "Asset", "Bank Transaction",
)

# name -> {"desc", "args": {arg: spec}, "view"}; spec = (type, required, extra)
TOOLS: dict[str, dict] = {
    "revenue_expense_trend": {
        "desc": "Income, expense and net profit per month for a period.",
        "args": {}, "view": "line"},
    "kpis": {
        "desc": "Headline figures for a period: revenue, expenses, net profit, margin, cash, receivables, payables.",
        "args": {}, "view": "number"},
    "top_accounts": {
        "desc": "Largest accounts by movement in a period for one root type (e.g. biggest expenses).",
        "args": {"root_type": ("enum", True, ROOT_TYPES), "top_n": ("int", False, (1, 50))},
        "view": "bar"},
    "by_dimension": {
        "desc": "Income or expense split by cost centre, project, department or branch.",
        "args": {"dimension": ("enum", True, DIMENSIONS), "root_type": ("enum", True, ("Income", "Expense")),
                 "top_n": ("int", False, (1, 50))},
        "view": "bar"},
    "top_parties": {
        "desc": "Top customers or suppliers by invoiced amount in a period, or by amount outstanding now.",
        "args": {"party": ("enum", True, ("customer", "supplier")),
                 "metric": ("enum", True, ("invoiced", "outstanding")), "top_n": ("int", False, (1, 50))},
        "view": "bar"},
    "ageing": {
        "desc": "Receivables or payables by age bucket (current, 1-30, 31-60, 61-90, 90+) as at the period end.",
        "args": {"party": ("enum", True, ("customer", "supplier"))}, "view": "bar"},
    "cash_position": {
        "desc": "Cash and bank balances per account as at the period end.",
        "args": {}, "view": "pie"},
    # v2.97.0 — Analysis Pack
    "profit_bridge": {"desc": "Profit bridge: revenue to net profit step by step (cost of sales, expense groups).",
                      "args": {}, "view": "table"},
    "variance_drivers": {"desc": "Which accounts moved profit most against the same period last year.",
                         "args": {}, "view": "bar"},
    "working_capital_cycle": {"desc": "DSO, DPO, DIO and cash conversion cycle month by month.",
                              "args": {}, "view": "line"},
    "gl_anomalies": {"desc": "Manual journal entries worth reviewing: round amounts, weekend or period-end "
                             "postings, unusual size.", "args": {}, "view": "table"},
    "find_records": {
        "desc": "List or summarise documents (invoices, orders, payments, customers, items…) matching a request.",
        "args": {"doctype": ("doctype", True, RECORD_DOCTYPES), "request": ("text", True, 300)},
        "view": "table"},
}

DEFAULT_TOP_N = 10


class PlanError(ValueError):
    pass


def _date(v) -> _dt.date:
    if isinstance(v, _dt.date):
        return v
    return _dt.date.fromisoformat(str(v)[:10])


def validate_plan(plan: dict, today: _dt.date | None = None, allowed_doctypes=None) -> dict:
    """Return a clean plan or raise PlanError. Unknown tools, unknown or
    out-of-range arguments, and bad dates are all refused — nothing the model
    proposes reaches the database unchecked."""
    if not isinstance(plan, dict):
        raise PlanError("plan is not an object")
    tool = plan.get("tool")
    if tool not in TOOLS:
        raise PlanError(f"unknown tool {tool!r}")
    spec = TOOLS[tool]["args"]
    raw = plan.get("args") or {}
    if not isinstance(raw, dict):
        raise PlanError("args is not an object")
    extra = set(raw) - set(spec) - {"from_date", "to_date"}
    if extra:
        raise PlanError(f"unexpected arguments {sorted(extra)}")
    args: dict = {}
    for name, (kind, required, rule) in spec.items():
        v = raw.get(name)
        if v in (None, ""):
            if required:
                raise PlanError(f"missing {name}")
            continue
        if kind == "doctype":
            # v2.95.0 — any document type the user can read (custom apps
            # included); the caller passes that list. Default: the core set.
            pool = list(allowed_doctypes) if allowed_doctypes is not None else list(rule)
            match = next((x for x in pool if str(x).lower() == str(v).lower()), None)
            if match is None:
                raise PlanError(f"{name} {v!r} is not a document type you can read")
            args[name] = match
        elif kind == "enum":
            match = next((x for x in rule if str(x).lower() == str(v).lower()), None)
            if match is None:
                raise PlanError(f"{name} must be one of {list(rule)}")
            args[name] = match
        elif kind == "int":
            try:
                iv = int(v)
            except (TypeError, ValueError):
                raise PlanError(f"{name} must be a whole number") from None
            lo, hi = rule
            args[name] = max(lo, min(hi, iv))
        elif kind == "text":
            s = str(v).strip()
            if not s:
                raise PlanError(f"{name} is empty")
            args[name] = s[:rule]
    today = today or _dt.date.today()
    fd, td = raw.get("from_date"), raw.get("to_date")
    try:
        f = _date(fd) if fd else None
        t = _date(td) if td else None
    except ValueError:
        raise PlanError("dates must be YYYY-MM-DD") from None
    if f and t and f > t:
        f, t = t, f
    if f and t and (t - f).days > 366 * 5:
        raise PlanError("period longer than five years")
    if f:
        args["from_date"] = f.isoformat()
    if t:
        args["to_date"] = t.isoformat()
    view = plan.get("view") if plan.get("view") in VIEWS else TOOLS[tool]["view"]
    title = str(plan.get("title") or "").strip()[:120]
    return {"tool": tool, "args": args, "view": view, "title": title}


# ── built-in planner ─────────────────────────────────────────────────────────

_N = re.compile(r"\b(?:top|first|biggest|largest|best|أكبر|أعلى|أفضل)\s+(\d{1,2})\b|\b(\d{1,2})\s+(?:top|biggest|largest)\b", re.I)


# "top customers", "top 10 customers", "best 5 suppliers", "largest clients" …
_RANKED_PARTY = re.compile(r"\b(top|best|biggest|largest|main|major)\s+(\d{1,2}\s+)?(customers?|clients?|suppliers?|vendors?)\b")


def _top_n(q: str) -> int | None:
    m = _N.search(q)
    return int(m.group(1) or m.group(2)) if m else None


def _has(q: str, *words) -> bool:
    return any(w in q for w in words)


def rule_plan(question: str) -> dict | None:
    """Plan common questions without a model. Returns a raw plan (validate it)
    or None when the question needs the model."""
    q = (question or "").lower()
    n = _top_n(q)
    args_n = {"top_n": n} if n else {}
    supplier = _has(q, "supplier", "vendor", "payable", "مورد", "الموردين", "الدائن")
    party = "supplier" if supplier else "customer"

    if _has(q, "anomal", "unusual journal", "suspicious", "red flag", "second look", "audit the journal",
            "قيود غير عادية", "مشبوه"):
        return {"tool": "gl_anomalies", "args": {}}
    if _has(q, "bridge", "waterfall", "revenue to profit", "from revenue to", "جسر الربح"):
        return {"tool": "profit_bridge", "args": {}}
    if _has(q, "what moved", "why did profit", "why is profit", "profit change", "variance", "drivers",
            "compared to last year", "vs last year", "مقارنة بالعام الماضي", "أسباب تغير الربح"):
        return {"tool": "variance_drivers", "args": {}}
    if _has(q, "working capital", "cash conversion", "dso", "dpo", "dio", "days sales outstanding",
            "رأس المال العامل", "دورة النقد"):
        return {"tool": "working_capital_cycle", "args": {}}
    if _has(q, "ageing", "aging", "overdue bucket", "age bucket", "أعمار", "تعمير"):
        return {"tool": "ageing", "args": {"party": party}}
    if _has(q, "owe", "outstanding", "unpaid", "receivable", "payable", "overdue", "مستحق", "المدينين"):
        return {"tool": "top_parties", "args": {"party": party, "metric": "outstanding", **args_n}}
    if _RANKED_PARTY.search(q) or _has(q, "أفضل العملاء", "أكبر العملاء", "أكبر الموردين", "أفضل الموردين"):
        return {"tool": "top_parties", "args": {"party": party, "metric": "invoiced", **args_n}}
    for dim, words in (("cost_center", ("cost center", "cost centre", "مركز التكلفة", "مراكز التكلفة")),
                       ("project", ("by project", "per project", "projects", "مشروع", "المشاريع")),
                       ("department", ("department", "قسم", "الأقسام")),
                       ("branch", ("branch", "فرع", "الفروع"))):
        if _has(q, *words):
            rt = "Income" if _has(q, "revenue", "income", "sales", "إيراد", "مبيعات") else "Expense"
            return {"tool": "by_dimension", "args": {"dimension": dim, "root_type": rt, **args_n}}
    if _has(q, "cash", "bank balance", "bank account", "liquidity", "نقد", "النقدية", "رصيد البنك"):
        return {"tool": "cash_position", "args": {}}
    if _has(q, "trend", "monthly", "by month", "per month", "over time", "month by month", "شهري", "شهريا", "اتجاه"):
        return {"tool": "revenue_expense_trend", "args": {}}
    if _has(q, "expense", "spend", "cost", "مصروف", "المصاريف", "تكاليف"):
        return {"tool": "top_accounts", "args": {"root_type": "Expense", **args_n}}
    if _has(q, "revenue by account", "income by account", "income account", "sources of revenue", "مصادر الإيراد"):
        return {"tool": "top_accounts", "args": {"root_type": "Income", **args_n}}
    if _has(q, "kpi", "overview", "summary", "how are we doing", "how is the business", "profit", "margin",
            "revenue", "ملخص", "الأداء", "الربح", "الإيرادات"):
        return {"tool": "kpis", "args": {}}
    return None


# ── shaping ──────────────────────────────────────────────────────────────────

def chart_for(view: str, columns: list[dict], rows: list[dict]) -> dict | None:
    """Chart spec from a dataset: first text column = labels, numeric columns =
    series. None for views that are not charts or data that cannot be drawn."""
    if view not in ("bar", "line", "pie") or not rows:
        return None
    label_col = next((c["key"] for c in columns if c.get("type") not in ("number", "currency", "percent")), None)
    num_cols = [c for c in columns if c.get("type") in ("number", "currency")]
    if not label_col or not num_cols:
        return None
    if view == "pie":
        num_cols = num_cols[:1]
    return {"type": view, "labels": [str(r.get(label_col) if r.get(label_col) is not None else "—") for r in rows],
            "series": [{"name": c["label"], "data": [float(r.get(c["key"]) or 0) for r in rows]} for c in num_cols]}


def fmt(v: float) -> str:
    return f"{v:,.2f}"


def plain_summary(tool: str, title: str, columns: list[dict], rows: list[dict]) -> str:
    """A factual one-paragraph answer when no model is available."""
    if not rows:
        return f"**{title}** — no data for this period."
    if tool == "kpis":
        parts = [f"{r['metric']}: **{fmt(r['value'])}**" for r in rows if r.get("value") is not None]
        return f"**{title}** — " + "; ".join(parts) + "."
    label = next((c["key"] for c in columns if c.get("type") not in ("number", "currency", "percent")), None)
    num = next((c for c in columns if c.get("type") in ("number", "currency")), None)
    if not label or not num:
        return f"**{title}** — {len(rows)} rows."
    total = sum(float(r.get(num["key"]) or 0) for r in rows)
    top = max(rows, key=lambda r: abs(float(r.get(num["key"]) or 0)))
    share = (float(top.get(num["key"]) or 0) / total * 100) if total else 0
    return (f"**{title}** — {len(rows)} rows, total {num['label'].lower()} **{fmt(total)}**. "
            f"Largest: **{top.get(label)}** with {fmt(float(top.get(num['key']) or 0))}"
            + (f" ({share:.0f}% of the total)." if total and 0 < share <= 100 else "."))


FOLLOW_UPS = {
    "profit_bridge": ["What moved profit vs last year?", "Expenses by cost centre", "Show the KPIs"],
    "variance_drivers": ["Show the profit bridge", "Biggest expenses this year", "Show the monthly trend"],
    "working_capital_cycle": ["Who owes us the most?", "Receivables ageing", "Cash position"],
    "gl_anomalies": ["Biggest expenses this year", "What moved profit vs last year?", "Show the KPIs"],
    "revenue_expense_trend": ["Which expenses grew the most?", "Show profit by cost centre", "Top 10 customers this year"],
    "kpis": ["Show the monthly trend", "Who owes us the most?", "Biggest expenses this year"],
    "top_accounts": ["Show the monthly trend", "Split expenses by cost centre", "Show the KPIs"],
    "by_dimension": ["Show the monthly trend", "Top 10 expense accounts", "Show the KPIs"],
    "top_parties": ["Show receivables ageing", "Top 10 suppliers by invoiced amount", "Show cash position"],
    "ageing": ["Who owes us the most?", "Show cash position", "Show the KPIs"],
    "cash_position": ["Show payables ageing", "Show the monthly trend", "Who owes us the most?"],
    "find_records": ["Show the KPIs", "Top 10 customers this year", "Show the monthly trend"],
}


def rank_doctypes(question: str, names, k: int = 30) -> list[str]:
    """Document types most likely meant by the question, for the model's
    shortlist: word overlap with the name (singular/plural tolerant), then
    the core set, then the rest alphabetically."""
    words = {w.rstrip("s") for w in re.findall(r"[a-z]{3,}", (question or "").lower())}
    core_set = {d.lower() for d in RECORD_DOCTYPES}

    def score(n: str):
        parts = {w.rstrip("s") for w in re.findall(r"[a-z]{3,}", n.lower())}
        return (-len(parts & words), 0 if n.lower() in core_set else 1, n)
    return sorted(set(names), key=score)[:k]
