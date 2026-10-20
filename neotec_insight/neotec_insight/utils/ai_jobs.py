"""Background jobs for AI calls (v2.95.1).

A model can take longer to answer than the web gateway lets a request live
(Frappe Cloud returns 504 Gateway Timeout). So AI work runs as a background
job: the screen gets a job id at once and polls `ai.ai_job_status` until the
result is ready.

* The job runs as the user who started it — the same permission checks apply
  inside the job as in a normal request.
* Only that user can read the job's status or result.
* State lives in the site's shared cache for an hour.
* If background workers are unavailable, the work runs inline instead, so the
  feature still works (with the old timeout risk) rather than failing.
"""
from __future__ import annotations

import time

import frappe
from frappe import _

TTL = 3600
PREFIX = "neotec_insight:ai_job:"
ALLOWED = {
    "neotec_insight.neotec_insight.api.health.health_ai_analysis",
    "neotec_insight.neotec_insight.api.copilot.ask",
}


def _cache():
    c = frappe.cache
    return c() if callable(c) and not hasattr(c, "get_value") else c


def _put(job_id: str, state: dict) -> None:
    _cache().set_value(PREFIX + job_id, state, expires_in_sec=TTL)


def _get(job_id: str) -> dict | None:
    return _cache().get_value(PREFIX + job_id)


def _message(e: Exception) -> str:
    msg = str(e) or type(e).__name__
    try:
        msgs = frappe.local.message_log or []
        if msgs:
            last = msgs[-1]
            msg = (frappe.parse_json(last).get("message") if isinstance(last, str) else last.get("message")) or msg
    except Exception:
        pass
    return frappe.utils.strip_html(msg)[:500] if hasattr(frappe.utils, "strip_html") else msg[:500]


def start(method: str, kwargs: dict) -> dict:
    """Queue `method(**kwargs)` as the current user; return {job_id}."""
    if method not in ALLOWED:
        frappe.throw(_("Not an AI job."))
    job_id = frappe.generate_hash(length=20)
    owner = frappe.session.user
    _put(job_id, {"status": "queued", "owner": owner, "started": time.time()})
    try:
        frappe.enqueue("neotec_insight.neotec_insight.utils.ai_jobs.execute", queue="long", timeout=900,
                       job_key=job_id, method=method, kwargs=kwargs, owner=owner)
    except Exception:
        # No queue available: run now so the feature still answers.
        execute(job_key=job_id, method=method, kwargs=kwargs, owner=owner)
    return {"job_id": job_id}


def execute(job_key: str, method: str, kwargs: dict, owner: str) -> None:
    if method not in ALLOWED:
        return
    state = _get(job_key) or {"owner": owner}
    state.update({"status": "running", "owner": owner})
    _put(job_key, state)
    previous = frappe.session.user
    try:
        frappe.set_user(owner)
        result = frappe.get_attr(method)(**(kwargs or {}))
        state.update({"status": "done", "result": result, "finished": time.time()})
    except Exception as e:
        frappe.db.rollback()
        state.update({"status": "error", "error": _message(e), "finished": time.time()})
    finally:
        frappe.set_user(previous)
    _put(job_key, state)


def status(job_id: str) -> dict:
    state = _get(job_id or "")
    if not state:
        return {"status": "missing"}
    if state.get("owner") != frappe.session.user and frappe.session.user != "Administrator":
        frappe.throw(_("Not your job."), frappe.PermissionError)
    out = {"status": state["status"]}
    if state["status"] == "done":
        out["result"] = state.get("result")
    elif state["status"] == "error":
        out["error"] = state.get("error")
    out["elapsed"] = round(time.time() - float(state.get("started") or time.time()), 1)
    return out
