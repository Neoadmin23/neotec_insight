"""AI design of dashboards and financial statements (v3.0.0).

The model proposes; the server checks everything; nothing is saved until the
user reviews it.

Dashboards — visuals over Analysis Pack analyses and Studio reports. Each
source must exist and be readable; types and sizes are cleaned and the visuals
packed into the 12-column grid. Returned as an unsaved draft for the Designer.

Statements — sections, account rows, formulas and allocation rows, with the
accounts behind each account row. Keys must be unique; formulas must parse and
refer only to rows above them; accounts must be this company's postable
accounts; allocation rows must name real rules; the definition must pass the
report schema. Active accounts the statement leaves out are listed. Saved as an
INACTIVE report with its account mapping, to refine and switch on in
Reports → Rows.
"""
from __future__ import annotations

import json
import re

import frappe
from frappe import _
from frappe.utils import add_months, flt, nowdate

from neotec_insight.neotec_insight.utils.access import require_finance_read

SIZES = {"small": (3, 2), "medium": (6, 4), "large": (12, 5), "wide": (12, 4), "tall": (6, 6)}
KEY_RE = re.compile(r"^[a-z][a-z0-9_]{1,40}$")
KINDS = ("section", "source", "formula", "allocation")
MAX_ROWS = 120


def _json(text):
    from neotec_insight.neotec_insight.api.copilot import _json as j
    return j(text)


def _chat(system, user):
    from neotec_insight.neotec_insight.api.copilot import _chat as c
    return c(system, user, temperature=0.1)


def _ai_ready():
    from neotec_insight.neotec_insight.api.copilot import _ai_ready as r
    return r()


# ── dashboards ───────────────────────────────────────────────────────────────

CFO_STARTER = {"title": "CFO overview", "visuals": [
    {"title": "Return on equity", "type": "kpi", "source": {"kind": "analysis", "key": "dupont"}, "size": "small"},
    {"title": "Net margin", "type": "kpi", "source": {"kind": "analysis", "key": "profit_bridge"}, "size": "small"},
    {"title": "Cash conversion cycle", "type": "kpi", "source": {"kind": "analysis", "key": "working_capital_cycle"}, "size": "small"},
    {"title": "Customers making 80%", "type": "kpi", "source": {"kind": "analysis", "key": "revenue_concentration"}, "size": "small"},
    {"title": "Profit bridge", "type": "waterfall", "source": {"kind": "analysis", "key": "profit_bridge"}, "size": "medium"},
    {"title": "Working-capital cycle", "type": "line", "source": {"kind": "analysis", "key": "working_capital_cycle"}, "size": "medium"},
    {"title": "What moved profit", "type": "bar", "source": {"kind": "analysis", "key": "variance_drivers"}, "size": "medium"},
    {"title": "Top customers", "type": "bar", "source": {"kind": "analysis", "key": "revenue_concentration"}, "size": "medium"},
    {"title": "Journals worth a second look", "type": "table", "source": {"kind": "analysis", "key": "gl_anomalies"}, "size": "wide"},
]}


def pack(visuals: list[dict]) -> list[dict]:
    """Left-to-right, top-to-bottom flow into 12 columns. Pure."""
    x = y = row_h = 0
    out = []
    for i, v in enumerate(visuals):
        w, h = SIZES.get(v.get("size") or "", (int(v.get("w") or 6), int(v.get("h") or 4)))
        w, h = max(2, min(12, w)), max(2, min(12, h))
        if x + w > 12:
            x, y, row_h = 0, y + row_h, 0
        out.append({**{k: v[k] for k in ("title", "type", "source") if k in v}, "id": f"ai{i}",
                    "x": x, "y": y, "w": w, "h": h, "options": {}})
        x += w
        row_h = max(row_h, h)
    return out


def check_board(draft: dict, analyses: set[str], reports: set[str]) -> tuple[dict, list[str]]:
    """Keep only visuals whose source exists and whose type is known."""
    from neotec_insight.neotec_insight.api.boards import VISUALS
    notes, kept = [], []
    for v in (draft.get("visuals") or [])[:30]:
        if not isinstance(v, dict):
            continue
        src = v.get("source") or {}
        ok_src = (src.get("kind") == "analysis" and src.get("key") in analyses) or \
                 (src.get("kind") == "studio" and src.get("report") in reports)
        if v.get("type") not in VISUALS:
            notes.append(_("Left out “{0}”: unknown visual type {1}.").format(v.get("title"), v.get("type")))
        elif not ok_src:
            notes.append(_("Left out “{0}”: its data source does not exist.").format(v.get("title")))
        else:
            kept.append({"title": str(v.get("title") or "")[:120], "type": v["type"],
                         "source": {"kind": src["kind"], "key": src.get("key"), "report": src.get("report")},
                         "size": v.get("size")})
    return {"title": str(draft.get("title") or _("New dashboard"))[:140], "layout": pack(kept)}, notes


BOARD_SYSTEM = (
    "You design business dashboards. Reply with ONLY JSON: {\"title\": str, \"visuals\": [{\"title\": str, "
    "\"type\": one of kpi|bar|line|area|pie|waterfall|table|pivot, \"source\": {\"kind\": \"analysis\", \"key\": KEY} "
    "or {\"kind\": \"studio\", \"report\": NAME}, \"size\": small|medium|large|wide|tall}]}. Use only the sources "
    "listed. Lead with 3-4 KPI visuals (size small), then charts (medium), then detail tables (wide). Pick the "
    "visual type that suits each source: waterfall only for profit_bridge, line for trends over months, bar for "
    "rankings, table for lists. 6 to 12 visuals.")


def design_board(prompt: str) -> dict:
    from neotec_insight.neotec_insight.api.analysis_pack import ANALYSES
    analyses = {k: (t, d) for k, t, d in ANALYSES}
    reports = {r["name"]: r["title"] for r in frappe.get_all("Studio Report", fields=["name", "title"],
                                                            order_by="modified desc", limit_page_length=80)}
    if not _ai_ready():
        board, notes = check_board(CFO_STARTER, set(analyses), set(reports))
        return {**board, "notes": [_("AI is off — this is the built-in CFO starter. Turn AI on to design from a "
                                     "description.")] + notes, "source": "template"}
    user = ("Analyses (kind analysis):\n" + "\n".join(f"- {k}: {t} — {d}" for k, (t, d) in analyses.items())
            + "\nStudio reports (kind studio):\n" + ("\n".join(f"- {n}: {t}" for n, t in reports.items()) or "- none")
            + f"\n\nRequest: {prompt}\nJSON only.")
    draft = _json(_chat(BOARD_SYSTEM, user)) or {}
    board, notes = check_board(draft, set(analyses), set(reports))
    if not board["layout"]:
        frappe.throw(_("The AI's draft had no usable visuals. Try describing the dashboard differently."))
    return {**board, "notes": notes, "source": "ai"}


# ── statements ───────────────────────────────────────────────────────────────

def _accounts(company) -> list[dict]:
    """Postable P&L and balance-sheet accounts with their last-12-months activity."""
    since = str(add_months(nowdate(), -12))
    rows = frappe.db.sql(
        """select a.name, a.account_name, a.root_type, ifnull(a.account_type, '') as account_type,
                  a.parent_account as parent,
                  coalesce(sum(abs(gle.debit - gle.credit)), 0) as activity
           from `tabAccount` a left join `tabGL Entry` gle
             on gle.account = a.name and gle.is_cancelled = 0 and gle.posting_date >= %s
           where a.company = %s and a.is_group = 0 and ifnull(a.disabled, 0) = 0
           group by a.name, a.account_name, a.root_type, a.account_type, a.parent_account""",
        (since, company), as_dict=True)
    return sorted(rows, key=lambda r: -flt(r["activity"]))


def check_statement(draft: dict, accounts: list[dict], rules: set[str], statement_type: str):
    """(definition, mappings, errors, warnings, coverage). Pure apart from the
    formula validator (which raises frappe.ValidationError)."""
    from neotec_insight.neotec_insight.utils.formula import validate_formula_expression
    by_name = {a["name"]: a for a in accounts}
    by_label = {}
    for a in accounts:
        by_label.setdefault(a["account_name"].strip().lower(), a["name"])
    errors, warnings, rows, mappings, seen_keys, flags, mapped = [], [], [], [], [], set(), {}
    raw = draft.get("rows") if isinstance(draft, dict) else None
    if not isinstance(raw, list) or not raw:
        return None, [], [_("The draft has no rows.")], [], {}
    for i, r in enumerate(raw[:MAX_ROWS]):
        if not isinstance(r, dict):
            errors.append(_("Row {0} is not an object.").format(i + 1))
            continue
        key, kind, label = str(r.get("key") or ""), r.get("kind"), str(r.get("label") or "").strip()[:120]
        where = f"“{label or key}”"
        if kind not in KINDS:
            errors.append(_("{0}: unknown row kind {1}.").format(where, kind))
            continue
        if not KEY_RE.match(key):
            errors.append(_("{0}: key {1} must be lower-case letters, digits and underscores.").format(where, key))
            continue
        if key in seen_keys:
            errors.append(_("{0}: key {1} is used twice.").format(where, key))
            continue
        if not label:
            errors.append(_("Row {0} has no label.").format(key))
            continue
        row = {"key": key, "kind": kind, "label": label}
        if kind == "source":
            flag = label
            n = 2
            while flag in flags:
                flag, n = f"{label} ({n})", n + 1
            flags.add(flag)
            row.update({"flag": flag, "accounts": [], "sign": "invert" if r.get("sign") == "invert" else "normal"})
            for acc in r.get("accounts") or []:
                name = acc if acc in by_name else by_label.get(str(acc).strip().lower())
                if not name:
                    warnings.append(_("{0}: account {1} does not exist in this company — left out.").format(where, acc))
                elif name in mapped:
                    warnings.append(_("{0}: {1} is already in “{2}” — kept there only.").format(where, name, mapped[name]))
                else:
                    mapped[name] = label
                    mappings.append({"account": name, "flag": flag})
            if not any(m["flag"] == flag for m in mappings):
                warnings.append(_("{0} has no accounts yet — map them in Reports → Account map.").format(where))
        elif kind == "formula":
            formula = str(r.get("formula") or "").strip()
            try:
                validate_formula_expression(formula, set(seen_keys), row_key=key)
            except Exception as e:
                errors.append(_("{0}: formula “{1}” — {2}").format(where, formula, str(e)[:200]))
                continue
            row["formula"] = formula
        elif kind == "allocation":
            rule = r.get("allocation_rule")
            if rule not in rules:
                errors.append(_("{0}: allocation rule {1} does not exist.").format(where, rule))
                continue
            row["allocation_rule"] = rule
        rows.append(row)
        seen_keys.append(key)
    relevant = ("Income", "Expense") if statement_type == "pnl" else ("Asset", "Liability", "Equity")
    missing = [a for a in accounts if a["root_type"] in relevant and flt(a["activity"]) > 0 and a["name"] not in mapped]
    total = sum(flt(a["activity"]) for a in accounts if a["root_type"] in relevant) or 0
    left = sum(flt(a["activity"]) for a in missing)
    coverage = {"mapped_accounts": len(mapped), "missing_active": len(missing),
                "missing_share": round(left / total * 100, 1) if total else 0,
                "missing_top": [{"account": a["name"], "activity": round(flt(a["activity"]), 2)} for a in missing[:10]]}
    if missing:
        warnings.append(_("{0} active accounts ({1}% of activity) are not in the statement.").format(
            len(missing), coverage["missing_share"]))
    definition = {"comparison": draft.get("comparison") or {}, "rows": rows}
    if not errors:
        try:
            from neotec_insight.neotec_insight.utils.schema import validate_report_definition_schema
            validate_report_definition_schema(definition, report_type=statement_type)
        except Exception as e:
            errors.append(_("Report schema: {0}").format(str(e)[:300]))
    return definition, mappings, errors, warnings, coverage


STATEMENT_SYSTEM = (
    "You design financial statement layouts for ERPNext. Reply with ONLY JSON: {\"title\": str, \"rows\": [...]}. "
    "Each row is one of: {\"key\", \"kind\": \"section\", \"label\"}; {\"key\", \"kind\": \"source\", \"label\", "
    "\"accounts\": [account names from the list]}; {\"key\", \"kind\": \"formula\", \"label\", \"formula\": "
    "expression using keys of rows ABOVE it with + - * / ( ) and IF/ROUND/ABS/MIN/MAX/SUM}; {\"key\", \"kind\": "
    "\"allocation\", \"label\", \"allocation_rule\": a rule name from the list}. Keys: lower_snake_case, unique. "
    "Put every listed active account in exactly one source row. Totals and subtotals are formula rows. "
    "Revenue and income accounts give positive figures; expense rows are positive too, so profit = revenue - costs.")


def design_statement(prompt: str, statement_type: str = "pnl", company: str | None = None) -> dict:
    from neotec_insight.neotec_insight.api.copilot import _company
    if not _ai_ready():
        frappe.throw(_("Designing a statement needs AI. Turn it on in Insight AI Settings."))
    company = _company(company)
    statement_type = statement_type if statement_type in ("pnl", "balance_sheet") else "pnl"
    roots = ("Income", "Expense") if statement_type == "pnl" else ("Asset", "Liability", "Equity")
    accounts = [a for a in _accounts(company) if a["root_type"] in roots][:400]
    rules = {r["name"] for r in frappe.get_all("Insight Allocation Rule", filters={"company": company},
                                               fields=["name"], limit_page_length=0)}
    acc_text = "\n".join(f"- {a['name']} | {a['root_type']} | {a['account_type'] or '-'} | parent {a['parent']} | "
                         f"activity {flt(a['activity']):,.0f}" for a in accounts)
    user = (f"Statement: {'profit and loss' if statement_type == 'pnl' else 'balance sheet'} for {company}.\n"
            f"Accounts (name | root | type | parent | 12-month activity):\n{acc_text}\n"
            f"Allocation rules: {', '.join(sorted(rules)) or 'none'}\n\nRequest: {prompt}\nJSON only.")
    draft = _json(_chat(STATEMENT_SYSTEM, user)) or {}
    definition, mappings, errors, warnings, coverage = check_statement(draft, accounts, rules, statement_type)
    if errors:      # one corrected attempt, with the server's reasons
        retry = _json(_chat(STATEMENT_SYSTEM, user + "\n\nYour previous draft was refused:\n- "
                            + "\n- ".join(errors[:15]) + "\nReturn a corrected full draft.")) or {}
        d2 = check_statement(retry, accounts, rules, statement_type)
        if not d2[2] or len(d2[2]) < len(errors):
            draft, (definition, mappings, errors, warnings, coverage) = retry, d2
    return {"title": str(draft.get("title") or _("Management statement"))[:140], "statement_type": statement_type,
            "company": company, "definition": definition, "mappings": mappings, "errors": errors,
            "warnings": warnings, "coverage": coverage}


# ── endpoints ────────────────────────────────────────────────────────────────

@frappe.whitelist(methods=["POST"])
def start_design(kind=None, prompt=None, statement_type="pnl", company=None):
    """Queue a design as a background AI job (the model can be slow)."""
    require_finance_read()
    prompt = (prompt or "").strip()[:1000]
    if not prompt:
        frappe.throw(_("Describe what you want."))
    from neotec_insight.neotec_insight.utils import ai_jobs
    if kind == "board":
        return ai_jobs.start("neotec_insight.neotec_insight.api.ai_design.design_board", {"prompt": prompt})
    if kind == "statement":
        from neotec_insight.neotec_insight.doctype.insight_report_definition.insight_report_definition import can_manage
        if not can_manage():
            frappe.throw(_("Only report managers can design statements."), frappe.PermissionError)
        return ai_jobs.start("neotec_insight.neotec_insight.api.ai_design.design_statement",
                             {"prompt": prompt, "statement_type": statement_type, "company": company})
    frappe.throw(_("Unknown design kind."))


@frappe.whitelist(methods=["POST"])
def save_statement(draft=None):
    """Re-check a reviewed statement draft and save it as an INACTIVE report."""
    require_finance_read()
    from neotec_insight.neotec_insight.doctype.insight_report_definition.insight_report_definition import can_manage
    if not can_manage():
        frappe.throw(_("Only report managers can create reports."), frappe.PermissionError)
    d = json.loads(draft) if isinstance(draft, str) else (draft or {})
    company = d.get("company")
    from neotec_insight.neotec_insight.utils.access import check_company
    check_company(company)
    st = d.get("statement_type") if d.get("statement_type") in ("pnl", "balance_sheet") else "pnl"
    roots = ("Income", "Expense") if st == "pnl" else ("Asset", "Liability", "Equity")
    accounts = [a for a in _accounts(company) if a["root_type"] in roots]
    rules = {r["name"] for r in frappe.get_all("Insight Allocation Rule", filters={"company": company},
                                               fields=["name"], limit_page_length=0)}
    raw_rows = [dict(r, accounts=[m["account"] for m in d.get("mappings") or [] if m.get("flag") == r.get("flag")])
                for r in (d.get("definition") or {}).get("rows") or []]
    definition, mappings, errors, _w, _c = check_statement({"rows": raw_rows}, accounts, rules, st)
    if errors:
        frappe.throw(_("The draft no longer passes the checks: {0}").format("; ".join(errors[:5])))
    base = frappe.scrub(d.get("title") or "ai statement").replace("_", "-")[:50] or "ai-statement"
    slug, n = base, 2
    while frappe.db.exists("Insight Report Definition", slug):
        slug, n = f"{base}-{n}", n + 1
    doc = frappe.get_doc({"doctype": "Insight Report Definition", "slug": slug,
                          "report_name": (d.get("title") or slug)[:140], "report_type": st, "version": 1,
                          "is_active": 0, "company": company, "definition_json": json.dumps(definition),
                          "column_schema_json": "[]", "filter_schema_json": "[]",
                          "description": _("Designed with AI — review in Reports → Rows, then activate.")})
    doc.insert()
    for m in mappings:
        frappe.get_doc({"doctype": "Account Flag Mapping", "report": doc.name, "account": m["account"],
                        "flag": m["flag"], "source": "auto-suggested"}).insert()
    return {"name": doc.name, "rows": len(definition["rows"]), "mappings": len(mappings)}
