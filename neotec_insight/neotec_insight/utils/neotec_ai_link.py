"""Neotec AI connection (v3.8.0).

When Neotec AI 4 is installed on the site and Insight AI Settings → AI Engine is "Neotec AI", every Insight
model call (Copilot, Studio's AI builder, AI analysis, outside-database questions, the CFO brief narrative and
the bank-slip reader) goes through Neotec AI's gateway: its providers and routes, fallback, residency, masking
and usage ledger. Insight's own connection settings stay as they are and are used whenever the engine is
"Insight built-in" or Neotec AI is not installed.

This module also offers Insight Copilot's read-only finance, HR and compliance tools to Neotec AI coworkers
(`neotec_ai_tools` hook), and the one-click switch shown in Neotec AI's setup (`neotec_ai_integrations`).
"""
from __future__ import annotations

import frappe

ENGINE_FIELD = "ai_engine"
HUB = "Neotec AI"
BUILT_IN = "Insight built-in"
APP = "neotec_insight"

# Which Neotec AI coworker gets each Copilot tool on first install (granted once; admins can remove it).
HR_TOOLS = {"headcount", "people_cost", "saudization", "compliance_overview"}


def hub_installed() -> bool:
    try:
        return "neotec_ai" in frappe.get_installed_apps()
    except Exception:
        return False


def routed(s=None) -> bool:
    """True when Insight should use the Neotec AI gateway for this call."""
    try:
        s = s or frappe.get_single("Insight AI Settings")
        if (getattr(s, ENGINE_FIELD, None) or BUILT_IN) != HUB or not hub_installed():
            return False
        from neotec_ai.platform import is_available
        return is_available()
    except Exception:
        return False


def chat(system: str, user: str, *, feature: str, temperature: float = 0.2, max_tokens: int | None = None,
         timeout: int | None = None, json_mode: bool = False, images=None) -> str:
    from neotec_ai.platform import complete
    res = complete(system, user, app=APP, feature=feature, temperature=temperature, max_tokens=max_tokens,
                   timeout=timeout, json_mode=json_mode, images=images,
                   purpose="Vision" if images else None)
    return (res.text or "").strip()


# ── integration switch ───────────────────────────────────────────────────────

def integration() -> dict:
    return {"app": APP, "label": "Neotec Insight",
            "description": "Copilot, Studio AI, analysis, CFO brief and bank-slip reading use Neotec AI.",
            "settings_doctype": "Insight AI Settings", "field": ENGINE_FIELD, "on_value": HUB,
            "off_value": BUILT_IN}


# ── coworker tools ───────────────────────────────────────────────────────────

def schema_for(spec: dict) -> dict:
    """Copilot tool spec -> JSON-schema properties (with __required__) for a Neotec AI tool. Pure."""
    props: dict = {
        "company": {"type": "string", "description": "Company; default is the user's default company"},
        "from_date": {"type": "string", "description": "YYYY-MM-DD; default: start of the current fiscal year"},
        "to_date": {"type": "string", "description": "YYYY-MM-DD; default: today"},
    }
    required = []
    for name, (kind, req, rule) in spec["args"].items():
        if kind == "enum":
            props[name] = {"type": "string", "enum": [str(x) for x in rule]}
        elif kind == "int":
            props[name] = {"type": "integer", "minimum": rule[0], "maximum": rule[1]}
        elif kind == "doctype":
            props[name] = {"type": "string", "description": "Document type, e.g. " + ", ".join(list(rule)[:6])}
        else:
            props[name] = {"type": "string", "description": f"Up to {rule} characters"}
        if req:
            required.append(name)
    props["__required__"] = required
    return props


def _runner(tool_name: str):
    def run(company=None, from_date=None, to_date=None, **args):
        from neotec_insight.neotec_insight.api import copilot
        from neotec_insight.neotec_insight.utils import copilot_core as core
        from neotec_insight.neotec_insight.utils.access import require_finance_read

        require_finance_read()
        raw = {"tool": tool_name, "args": {k: v for k, v in {**args, "from_date": from_date,
                                                              "to_date": to_date}.items() if v not in (None, "")}}
        plan = core.validate_plan(raw, allowed_doctypes=(copilot.readable_doctypes()
                                                         if tool_name == "find_records" else None))
        company = copilot._company(company)
        a = plan["args"]
        f, t = copilot._period(company, a.pop("from_date", None), a.pop("to_date", None))
        result = copilot.RUNNERS[tool_name](company, f, t, **a)
        cols, rows, title = result[0], result[1], result[2]
        rows = [dict(r) for r in rows[:100]]
        return {"title": title, "company": company, "from_date": f, "to_date": t,
                # v3.8.1 — Copilot columns are {"key", "label"}; keep both for the coworker
                "columns": [(c.get("key") or c.get("fieldname")) if isinstance(c, dict) else c for c in cols],
                "column_labels": [(c.get("label") or c.get("key") or "") if isinstance(c, dict) else str(c) for c in cols],
                "rows": rows, "row_count": len(result[1])}
    run.__name__ = f"insight_{tool_name}"
    return run


def get_tools() -> list[dict]:
    from neotec_insight.neotec_insight.utils import copilot_core as core

    tools = []
    for name, spec in core.TOOLS.items():
        hr = name in HR_TOOLS
        tools.append({
            "name": f"insight_{name}", "label": "Insight: " + name.replace("_", " ").capitalize(),
            "description": spec["desc"] + " Read-only; follows the user's company scope and permissions.",
            "category": "Finance", "parameters": schema_for(spec), "fn": _runner(name),
            "is_write": False, "risk": "Low",
            "grant_to": {"HR & Labor Law Advisor": "Default"} if hr else {"Finance Analyst": "Default"},
        })
    return tools
