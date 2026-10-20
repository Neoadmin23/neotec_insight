"""Analysis Pack — the most useful analyses of the GL, P&L and balance sheet
(v2.97.0). Calculations: utils/analysis_core.py. Every analysis returns the
standard dataset shape so the screen, the Copilot and dashboards share it."""
from __future__ import annotations

import datetime as _dt

import frappe
from frappe import _
from frappe.utils import add_days, add_months, flt, getdate

from neotec_insight.neotec_insight.utils import analysis_core as ac

ANALYSES = [
    ("profit_bridge", "Profit bridge", "How revenue turns into net profit, cost group by cost group."),
    ("variance_drivers", "What moved profit", "The accounts that changed profit most against the comparison period."),
    ("common_size_pl", "Common-size P&L", "Every P&L line as a share of revenue, this period against the comparison."),
    ("common_size_bs", "Common-size balance sheet", "The balance sheet's structure as a share of total assets."),
    ("working_capital_cycle", "Working-capital cycle", "Days to collect, to pay and to sell stock, month by month."),
    ("dupont", "Return on equity (DuPont)", "ROE split into margin, asset turnover and leverage."),
    ("revenue_concentration", "Customer concentration", "How much revenue depends on a few customers."),
    ("expense_pareto", "Expense Pareto", "How few accounts make up most of the costs."),
    ("gl_anomalies", "Journal entries worth a second look", "Manual journals with round amounts, weekend or "
                                                           "period-end postings, or unusual size."),
]
KEYS = {k for k, _t, _d in ANALYSES}


def _context(company, from_date, to_date, compare):
    from neotec_insight.neotec_insight.api.copilot import _company, _period
    from neotec_insight.neotec_insight.utils.access import require_finance_read
    require_finance_read()
    company = _company(company)
    f, t = _period(company, from_date, to_date)
    fd, td = getdate(f), getdate(t)
    if compare == "prior_period":
        span = (td - fd).days + 1
        cf, ct = fd - _dt.timedelta(days=span), fd - _dt.timedelta(days=1)
    else:
        cf, ct = getdate(add_months(fd, -12)), getdate(add_months(td, -12))
    return company, fd, td, cf, ct


def _metrics(company, f, t):
    from neotec_insight.neotec_insight.api.health import _metrics as m
    return m(company, str(f), str(t))


def _by_account(company, f, t, root_types=("Income", "Expense")):
    return frappe.db.sql(
        """select gle.account as account, a.root_type as root_type, a.parent_account as parent,
                  ifnull(a.account_type, '') as account_type,
                  sum(case when a.root_type = 'Income' then gle.credit - gle.debit
                           else gle.debit - gle.credit end) as amount
           from `tabGL Entry` gle join `tabAccount` a on a.name = gle.account
           where gle.company = %s and gle.is_cancelled = 0 and gle.posting_date between %s and %s
             and a.root_type in %s
           group by gle.account, a.root_type, a.parent_account, a.account_type""",
        (company, f, t, tuple(root_types)), as_dict=True)


def _group_name(parent: str | None) -> str:
    return (parent or _("Other")).rsplit(" - ", 1)[0]


def _opex_groups(rows):
    groups: dict[str, float] = {}
    for r in rows:
        if r["root_type"] == "Expense" and r["account_type"] != "Cost of Goods Sold":
            g = _group_name(r["parent"])
            groups[g] = groups.get(g, 0.0) + flt(r["amount"])
    return groups


# ── analyses ─────────────────────────────────────────────────────────────────

def a_profit_bridge(company, f, t, cf, ct):
    m = _metrics(company, f, t)
    groups = _opex_groups(_by_account(company, f, t))
    return ac.profit_bridge(m["revenue"], m["cogs"], [(k, v) for k, v in groups.items() if abs(v) > 0.005])


def a_variance_drivers(company, f, t, cf, ct):
    now = {r["account"]: r for r in _by_account(company, f, t)}
    then = {r["account"]: r for r in _by_account(company, cf, ct)}
    accounts = [{"account": a, "root_type": (now.get(a) or then.get(a))["root_type"],
                 "now": flt((now.get(a) or {}).get("amount")), "then": flt((then.get(a) or {}).get("amount"))}
                for a in set(now) | set(then)]
    return ac.variance_drivers(accounts)


def a_common_size_pl(company, f, t, cf, ct):
    mn, mt = _metrics(company, f, t), _metrics(company, cf, ct)
    gn, gt = _opex_groups(_by_account(company, f, t)), _opex_groups(_by_account(company, cf, ct))
    lines = [{"line": _("Revenue"), "now": mn["revenue"], "then": mt["revenue"]},
             {"line": _("Cost of sales"), "now": mn["cogs"], "then": mt["cogs"]},
             {"line": _("Gross profit"), "now": mn["gross_profit"], "then": mt["gross_profit"]}]
    for g in sorted(set(gn) | set(gt), key=lambda k: -abs(gn.get(k, 0))):
        lines.append({"line": g, "now": gn.get(g, 0.0), "then": gt.get(g, 0.0)})
    lines.append({"line": _("Net profit"), "now": mn["net_income"], "then": mt["net_income"]})
    return ac.common_size(lines, mn["revenue"], mt["revenue"], "common_size_pl", _("Common-size P&L"),
                          _("revenue"))


def a_common_size_bs(company, f, t, cf, ct):
    def bs(at):
        m = _metrics(company, at, at)
        equity = m["assets"] - m["liabilities"]           # includes profit not yet closed
        return m, equity
    (mn, en), (mt, et) = bs(t), bs(ct)
    def lines_for(m, e):
        return {_("Current assets"): m["current_assets"], _("Non-current assets"): m["non_current_assets"],
                _("Current liabilities"): m["current_liabilities"],
                _("Non-current liabilities"): m["liabilities"] - m["current_liabilities"],
                _("Equity"): e, _("of which cash"): m["cash"], _("of which receivables"): m["receivables"],
                _("of which inventory"): m["inventory"]}
    ln, lt = lines_for(mn, en), lines_for(mt, et)
    lines = [{"line": k, "now": ln[k], "then": lt[k]} for k in ln]
    out = ac.common_size(lines, mn["assets"], mt["assets"], "common_size_bs", _("Common-size balance sheet"),
                         _("total assets"))
    out["notes"].append(_("Comparison: balance sheet at {0}.").format(ct))
    return out


def a_working_capital_cycle(company, f, t, cf, ct):
    points = []
    for me in ac.month_ends(getdate(add_months(t, -11)).replace(day=1), t):
        m = _metrics(company, add_days(me, -364), me)
        points.append({"month": me.strftime("%Y-%m"), "receivables": m["receivables"], "payables": m["payables"],
                       "inventory": m["inventory"], "revenue_12m": m["revenue"], "cogs_12m": m["cogs"]})
    return ac.working_capital_cycle(points)


def a_dupont(company, f, t, cf, ct):
    def calc(a, b):
        m = _metrics(company, a, b)
        return ac.dupont(m["net_income"], m["revenue"], m["assets"], m["assets"] - m["liabilities"])
    return ac.dupont_dataset(calc(f, t), calc(cf, ct))


def a_revenue_concentration(company, f, t, cf, ct):
    if not frappe.has_permission("Sales Invoice", "read"):
        frappe.throw(_("You don't have access to Sales Invoices."), frappe.PermissionError)
    rows = frappe.db.sql(
        """select customer, sum(base_net_total) as amount from `tabSales Invoice`
           where company = %s and docstatus = 1 and posting_date between %s and %s
           group by customer""", (company, f, t), as_dict=True)
    return ac.pareto([(r["customer"], flt(r["amount"])) for r in rows], "revenue_concentration",
                     _("Customer concentration"), _("Customers"))


def a_expense_pareto(company, f, t, cf, ct):
    rows = [r for r in _by_account(company, f, t, ("Expense",))]
    return ac.pareto([(r["account"], flt(r["amount"])) for r in rows], "expense_pareto", _("Expense Pareto"),
                     _("Accounts"))


def a_gl_anomalies(company, f, t, cf, ct):
    if not frappe.has_permission("Journal Entry", "read"):
        frappe.throw(_("You don't have access to Journal Entries."), frappe.PermissionError)
    sql = """select voucher_no, posting_date, account, (debit - credit) as amount
             from `tabGL Entry` where company = %s and is_cancelled = 0 and voucher_type = 'Journal Entry'
               and posting_date between %s and %s order by posting_date desc limit %s"""
    entries = frappe.db.sql(sql, (company, f, t, 5000), as_dict=True)
    history = frappe.db.sql(sql, (company, add_days(t, -365), t, 20000), as_dict=True)
    ends = {str(d) for d in ac.month_ends(getdate(f), getdate(t))}
    return ac.gl_anomalies(entries, history, ends)


RUN = {
    "profit_bridge": a_profit_bridge, "variance_drivers": a_variance_drivers, "common_size_pl": a_common_size_pl,
    "common_size_bs": a_common_size_bs, "working_capital_cycle": a_working_capital_cycle, "dupont": a_dupont,
    "revenue_concentration": a_revenue_concentration, "expense_pareto": a_expense_pareto,
    "gl_anomalies": a_gl_anomalies,
}


def compute(key, company=None, from_date=None, to_date=None, compare="prior_year") -> dict:
    if key not in KEYS:
        frappe.throw(_("Unknown analysis."))
    company, f, t, cf, ct = _context(company, from_date, to_date, compare)
    out = RUN[key](company, f, t, cf, ct)
    out.update({"company": company, "from_date": str(f), "to_date": str(t),
                "compare_from": str(cf), "compare_to": str(ct), "compare": compare})
    return out


@frappe.whitelist()
def list_analyses():
    return [{"key": k, "title": _(t), "description": _(d)} for k, t, d in ANALYSES]


@frappe.whitelist()
def run_analysis(key=None, company=None, from_date=None, to_date=None, compare="prior_year"):
    return compute(key, company, from_date, to_date,
                   compare if compare in ("prior_year", "prior_period") else "prior_year")
