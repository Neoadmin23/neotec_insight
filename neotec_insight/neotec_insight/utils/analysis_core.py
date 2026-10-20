"""Analysis Pack — the calculations (v2.97.0). Pure: no site needed.

Every analysis returns the same dataset shape, so the Analysis screen, the
Copilot and the coming dashboard designer can all use it:
  {"key", "title", "figures": [{label, value, format}], "columns", "rows",
   "chart": {type, labels, series} | None, "notes": [str]}
format: "currency" | "percent" | "days" | "ratio" | "number".
"""
from __future__ import annotations

import datetime as _dt
import math
from statistics import median


def pct(part: float, whole: float) -> float | None:
    return round(part / whole * 100, 2) if whole else None


def safe_div(a: float, b: float) -> float | None:
    return a / b if b else None


def dataset(key, title, figures=None, columns=None, rows=None, chart=None, notes=None) -> dict:
    return {"key": key, "title": title, "figures": figures or [], "columns": columns or [],
            "rows": rows or [], "chart": chart, "notes": notes or []}


# 1 ── profit bridge ─────────────────────────────────────────────────────────

def profit_bridge(revenue: float, cogs: float, opex_groups: list[tuple[str, float]],
                  other_income: float = 0.0, max_groups: int = 6) -> dict:
    """Waterfall steps from revenue to net profit. opex_groups: (name, amount>0)."""
    groups = sorted(opex_groups, key=lambda g: -abs(g[1]))
    shown, rest = groups[:max_groups], groups[max_groups:]
    if rest:
        shown.append(("Other expenses", sum(a for _n, a in rest)))
    steps = [("Revenue", revenue, "total"), ("Cost of sales", -cogs, "delta"),
             ("Gross profit", revenue - cogs, "total")]
    running = revenue - cogs
    for name, amt in shown:
        steps.append((name, -amt, "delta"))
        running -= amt
    if other_income:
        steps.append(("Other income", other_income, "delta"))
        running += other_income
    steps.append(("Net profit", running, "total"))
    # floating bars: [base, top]
    base, labels, lows, highs = 0.0, [], [], []
    for name, val, kind in steps:
        labels.append(name)
        if kind == "total":
            lows.append(0.0)
            highs.append(val)
            base = val
        else:
            lows.append(min(base, base + val))
            highs.append(max(base, base + val))
            base += val
    rows = [{"step": n, "amount": round(v, 2), "kind": k,
             "pct_of_revenue": pct(v, revenue)} for n, v, k in steps]
    return dataset(
        "profit_bridge", "Profit bridge",
        figures=[{"label": "Gross margin", "value": pct(revenue - cogs, revenue), "format": "percent"},
                 {"label": "Net margin", "value": pct(running, revenue), "format": "percent"},
                 {"label": "Net profit", "value": round(running, 2), "format": "currency"}],
        columns=[{"key": "step", "label": "Step", "type": "text"},
                 {"key": "amount", "label": "Amount", "type": "currency"},
                 {"key": "pct_of_revenue", "label": "% of revenue", "type": "percent"}],
        rows=rows,
        chart={"type": "waterfall", "labels": labels,
               "series": [{"name": "Amount", "data": [[round(lo, 2), round(hi, 2)] for lo, hi in zip(lows, highs, strict=True)],
                           "kinds": [k for _n, _v, k in steps]}]})


# 2/3 ── common size ─────────────────────────────────────────────────────────

def common_size(lines: list[dict], base_now: float, base_then: float, key: str, title: str,
                base_label: str) -> dict:
    """lines: [{"line", "now", "then"}] -> each as % of the base, with the change."""
    rows = []
    for ln in lines:
        p_now, p_then = pct(ln["now"], base_now), pct(ln["then"], base_then)
        rows.append({"line": ln["line"], "now": round(ln["now"], 2), "now_pct": p_now,
                     "then": round(ln["then"], 2), "then_pct": p_then,
                     "change_pts": round(p_now - p_then, 2) if p_now is not None and p_then is not None else None})
    return dataset(
        key, title,
        columns=[{"key": "line", "label": "Line", "type": "text"},
                 {"key": "now", "label": "This period", "type": "currency"},
                 {"key": "now_pct", "label": f"% of {base_label}", "type": "percent"},
                 {"key": "then", "label": "Comparison", "type": "currency"},
                 {"key": "then_pct", "label": f"% of {base_label} (comparison)", "type": "percent"},
                 {"key": "change_pts", "label": "Change (pts)", "type": "number"}],
        rows=rows,
        chart={"type": "bar", "labels": [r["line"] for r in rows],
               "series": [{"name": "This period %", "data": [r["now_pct"] or 0 for r in rows]},
                          {"name": "Comparison %", "data": [r["then_pct"] or 0 for r in rows]}]})


# 4 ── variance drivers ──────────────────────────────────────────────────────

def variance_drivers(accounts: list[dict], top: int = 10) -> dict:
    """accounts: [{"account", "root_type", "now", "then"}] (Income credit-positive,
    Expense debit-positive). Impact on profit: income up = +, expense up = −."""
    rows = []
    for a in accounts:
        change = a["now"] - a["then"]
        impact = change if a["root_type"] == "Income" else -change
        rows.append({"account": a["account"], "type": a["root_type"], "now": round(a["now"], 2),
                     "then": round(a["then"], 2), "change": round(change, 2),
                     "change_pct": pct(change, abs(a["then"])) if a["then"] else None,
                     "profit_impact": round(impact, 2)})
    rows.sort(key=lambda r: -abs(r["profit_impact"]))
    shown = rows[:top]
    total = sum(r["profit_impact"] for r in rows)
    helped = sum(r["profit_impact"] for r in rows if r["profit_impact"] > 0)
    hurt = sum(r["profit_impact"] for r in rows if r["profit_impact"] < 0)
    return dataset(
        "variance_drivers", "What moved profit",
        figures=[{"label": "Net change in profit", "value": round(total, 2), "format": "currency"},
                 {"label": "Helped profit", "value": round(helped, 2), "format": "currency"},
                 {"label": "Hurt profit", "value": round(hurt, 2), "format": "currency"}],
        columns=[{"key": "account", "label": "Account", "type": "text"},
                 {"key": "type", "label": "Type", "type": "text"},
                 {"key": "now", "label": "This period", "type": "currency"},
                 {"key": "then", "label": "Comparison", "type": "currency"},
                 {"key": "change", "label": "Change", "type": "currency"},
                 {"key": "change_pct", "label": "Change %", "type": "percent"},
                 {"key": "profit_impact", "label": "Effect on profit", "type": "currency"}],
        rows=shown,
        chart={"type": "bar", "labels": [r["account"] for r in shown],
               "series": [{"name": "Effect on profit", "data": [r["profit_impact"] for r in shown]}]})


# 5 ── working-capital cycle ─────────────────────────────────────────────────

def wc_point(receivables, payables, inventory, revenue_12m, cogs_12m) -> dict:
    dso = safe_div(receivables * 365, revenue_12m)
    dpo = safe_div(payables * 365, cogs_12m)
    dio = safe_div(inventory * 365, cogs_12m)
    ccc = (dso or 0) + (dio or 0) - (dpo or 0) if dso is not None else None
    r1 = lambda v: round(v, 1) if v is not None else None   # noqa: E731
    return {"dso": r1(dso), "dpo": r1(dpo), "dio": r1(dio), "ccc": r1(ccc)}


def working_capital_cycle(points: list[dict]) -> dict:
    """points: [{"month", receivables, payables, inventory, revenue_12m, cogs_12m}]."""
    rows = [{"month": p["month"], **wc_point(p["receivables"], p["payables"], p["inventory"],
                                             p["revenue_12m"], p["cogs_12m"])} for p in points]
    last = rows[-1] if rows else {}
    first = rows[0] if rows else {}
    notes = []
    if last.get("ccc") is not None and first.get("ccc") is not None:
        d = last["ccc"] - first["ccc"]
        notes.append(f"Cash conversion cycle {'lengthened' if d > 0 else 'shortened'} by {abs(d):.0f} days over the period.")
    return dataset(
        "working_capital_cycle", "Working-capital cycle",
        figures=[{"label": "DSO", "value": last.get("dso"), "format": "days"},
                 {"label": "DPO", "value": last.get("dpo"), "format": "days"},
                 {"label": "DIO", "value": last.get("dio"), "format": "days"},
                 {"label": "Cash conversion cycle", "value": last.get("ccc"), "format": "days"}],
        columns=[{"key": "month", "label": "Month", "type": "text"},
                 {"key": "dso", "label": "DSO", "type": "number"}, {"key": "dpo", "label": "DPO", "type": "number"},
                 {"key": "dio", "label": "DIO", "type": "number"}, {"key": "ccc", "label": "CCC", "type": "number"}],
        rows=rows,
        chart={"type": "line", "labels": [r["month"] for r in rows],
               "series": [{"name": k.upper(), "data": [r[k] or 0 for r in rows]} for k in ("dso", "dpo", "dio", "ccc")]},
        notes=notes)


# 6 ── DuPont ────────────────────────────────────────────────────────────────

def dupont(net_income, revenue, assets, equity) -> dict:
    margin = safe_div(net_income, revenue)
    turnover = safe_div(revenue, assets)
    multiplier = safe_div(assets, equity)
    roe = margin * turnover * multiplier if None not in (margin, turnover, multiplier) else None
    return {"net_margin": round(margin * 100, 2) if margin is not None else None,
            "asset_turnover": round(turnover, 3) if turnover is not None else None,
            "equity_multiplier": round(multiplier, 3) if multiplier is not None else None,
            "roe": round(roe * 100, 2) if roe is not None else None}


def dupont_dataset(now: dict, then: dict) -> dict:
    labels = [("net_margin", "Net margin %", "percent"), ("asset_turnover", "Asset turnover", "ratio"),
              ("equity_multiplier", "Equity multiplier", "ratio"), ("roe", "Return on equity %", "percent")]
    rows = [{"measure": lab, "now": now[k], "then": then[k], "key": k} for k, lab, _f in labels]
    notes = []
    if now["roe"] is not None and then["roe"] is not None:
        moves = {k: (now[k] or 0) - (then[k] or 0) for k in ("net_margin", "asset_turnover", "equity_multiplier")}
        driver = max(moves, key=lambda k: abs(moves[k]))
        notes.append(f"ROE moved from {then['roe']}% to {now['roe']}%; the biggest change was in "
                     f"{dict((k, lab) for k, lab, _f in labels)[driver].lower()}.")
    return dataset(
        "dupont", "Return on equity (DuPont)",
        figures=[{"label": lab, "value": now[k], "format": f} for k, lab, f in labels],
        columns=[{"key": "measure", "label": "Measure", "type": "text"},
                 {"key": "now", "label": "This period", "type": "number"},
                 {"key": "then", "label": "Comparison", "type": "number"}],
        rows=rows, notes=notes)


# 7/8 ── Pareto ──────────────────────────────────────────────────────────────

def pareto(items: list[tuple[str, float]], key: str, title: str, label: str, top: int = 25) -> dict:
    items = sorted(((n, float(v)) for n, v in items if v and v > 0), key=lambda x: -x[1])
    total = sum(v for _n, v in items)
    rows, cum, n80 = [], 0.0, None
    for i, (n, v) in enumerate(items, 1):
        cum += v
        share = pct(v, total)
        cum_pct = pct(cum, total)
        if n80 is None and cum_pct is not None and cum_pct >= 80:
            n80 = i
        if i <= top:
            rows.append({"name": n, "amount": round(v, 2), "share": share, "cumulative": cum_pct})
    hhi = round(sum((v / total * 100) ** 2 for _n, v in items)) if total else None
    level = None if hhi is None else ("high" if hhi > 2500 else "moderate" if hhi > 1500 else "low")
    return dataset(
        key, title,
        figures=[{"label": f"{label} making 80%", "value": n80, "format": "number"},
                 {"label": f"Total {label.lower()}", "value": len(items), "format": "number"},
                 {"label": "Top 1 share", "value": rows[0]["share"] if rows else None, "format": "percent"},
                 {"label": "Concentration (HHI)", "value": hhi, "format": "number"}],
        columns=[{"key": "name", "label": label[:-1] if label.endswith("s") else label, "type": "text"},
                 {"key": "amount", "label": "Amount", "type": "currency"},
                 {"key": "share", "label": "Share %", "type": "percent"},
                 {"key": "cumulative", "label": "Cumulative %", "type": "percent"}],
        rows=rows,
        chart={"type": "bar", "labels": [r["name"] for r in rows],
               "series": [{"name": "Amount", "data": [r["amount"] for r in rows]}],
               "line": {"name": "Cumulative %", "data": [r["cumulative"] for r in rows]}},
        notes=[f"Concentration is {level} (HHI {hhi}; above 2,500 is high)."] if level else [])


# 9 ── GL anomaly scan ───────────────────────────────────────────────────────

WEEKEND = (4, 5)          # Friday, Saturday (Saudi working week)


def anomaly_reasons(entry: dict, typical: dict[str, float], period_ends: set[str],
                    round_min: float = 10000, size_factor: float = 3.0) -> list[str]:
    """entry: {voucher_no, posting_date, account, amount, voucher_type}."""
    reasons = []
    amt = abs(float(entry["amount"] or 0))
    d = entry["posting_date"]
    d = d if isinstance(d, _dt.date) else _dt.date.fromisoformat(str(d)[:10])
    if amt >= round_min and amt % 1000 == 0:
        reasons.append("Round amount")
    if d.weekday() in WEEKEND:
        reasons.append("Posted on a weekend")
    if str(d) in period_ends:
        reasons.append("Posted on the last day of the period")
    t = typical.get(entry["account"])
    if t and amt > size_factor * t and amt >= round_min:
        reasons.append(f"{amt / t:.0f}× the usual size for this account")
    return reasons


def typical_sizes(rows: list[dict]) -> dict[str, float]:
    by: dict[str, list[float]] = {}
    for r in rows:
        by.setdefault(r["account"], []).append(abs(float(r["amount"] or 0)))
    return {a: median(v) for a, v in by.items() if len(v) >= 5}


def gl_anomalies(entries: list[dict], history: list[dict], period_ends: set[str], top: int = 50) -> dict:
    typ = typical_sizes(history)
    flagged = []
    for e in entries:
        reasons = anomaly_reasons(e, typ, period_ends)
        if reasons:
            flagged.append({"voucher_no": e["voucher_no"], "posting_date": str(e["posting_date"])[:10],
                            "account": e["account"], "amount": round(float(e["amount"]), 2),
                            "reasons": "; ".join(reasons), "score": len(reasons)})
    flagged.sort(key=lambda r: (-r["score"], -abs(r["amount"])))
    counts: dict[str, int] = {}
    for f in flagged:
        for r in f["reasons"].split("; "):
            k = "Unusually large" if "usual size" in r else r
            counts[k] = counts.get(k, 0) + 1
    return dataset(
        "gl_anomalies", "Journal entries worth a second look",
        figures=[{"label": "Manual journal lines checked", "value": len(entries), "format": "number"},
                 {"label": "Flagged", "value": len(flagged), "format": "number"},
                 {"label": "With 2+ reasons", "value": sum(1 for f in flagged if f["score"] >= 2), "format": "number"}],
        columns=[{"key": "voucher_no", "label": "Journal", "type": "text"},
                 {"key": "posting_date", "label": "Date", "type": "text"},
                 {"key": "account", "label": "Account", "type": "text"},
                 {"key": "amount", "label": "Amount", "type": "currency"},
                 {"key": "reasons", "label": "Why", "type": "text"}],
        rows=flagged[:top],
        chart={"type": "bar", "labels": list(counts), "series": [{"name": "Lines", "data": list(counts.values())}]},
        notes=["A flag is a prompt to review, not a finding: round amounts and month-end postings are often legitimate."])


def month_ends(start: _dt.date, end: _dt.date) -> list[_dt.date]:
    out, y, m = [], start.year, start.month
    while (y, m) <= (end.year, end.month):
        last = (_dt.date(y + (m == 12), m % 12 + 1, 1) - _dt.timedelta(days=1))
        out.append(min(last, end))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def finite(v):
    return v if isinstance(v, (int, float)) and math.isfinite(v) else None
