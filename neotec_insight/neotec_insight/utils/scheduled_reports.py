# Copyright (c) 2026, Neotec Integrated Solution
# Scheduled distribution of Studio reports (v2.24.0) — fills the stub that
# tasks.run_daily_report_schedules has been calling since it was deferred.
#
# The daily scheduler hook runs once a day; each enabled Insight Report
# Schedule decides whether it is due (Daily always; Weekly when today matches
# the weekday; Monthly when today matches the day-of-month, clamped for short
# months). Delivery: email with an XLSX/CSV attachment, and optionally a
# WhatsApp text summary (report title + grand totals) via the WhatsApp
# Business Cloud API when site_config provides `whatsapp_token` and
# `whatsapp_phone_id` — silently skipped when not configured.
from __future__ import annotations

import calendar
import csv
import io
import json

import frappe
from frappe.utils import flt, get_url, now_datetime, nowdate


def process_scheduled_reports_for_cadence(cadence: str) -> None:
    """Entry point called from tasks.run_daily_report_schedules."""
    if cadence != "daily":
        return
    schedules = frappe.get_all("Insight Report Schedule",
                               filters={"enabled": 1}, pluck="name")
    for name in schedules:
        try:
            doc = frappe.get_doc("Insight Report Schedule", name)
            if not _is_due(doc):
                continue
            # Long queue — a heavy report must never delay the scheduler tick.
            frappe.enqueue(
                "neotec_insight.neotec_insight.utils.scheduled_reports._dispatch_by_name",
                queue="long", schedule_name=name, job_id=f"insight-sched-{name}",
                deduplicate=True,
            )
        except Exception:
            frappe.log_error(title="Insight schedule enqueue failed",
                             message=f"{name}: {frappe.get_traceback()}")


def _is_due(doc) -> bool:
    today = frappe.utils.getdate(nowdate())
    if doc.frequency == "Daily":
        return True
    if doc.frequency == "Weekly":
        return calendar.day_name[today.weekday()] == (doc.weekday or "Sunday")
    if doc.frequency == "Monthly":
        target = int(doc.day_of_month or 1)
        last = calendar.monthrange(today.year, today.month)[1]
        return today.day == min(max(target, 1), last)
    return False


def _dispatch_by_name(schedule_name: str) -> None:
    dispatch_schedule(frappe.get_doc("Insight Report Schedule", schedule_name))


def dispatch_schedule(doc) -> None:
    """Run the report and deliver it. Status is recorded on the schedule."""
    try:
        report = frappe.get_doc("Studio Report", doc.report)
        config = json.loads(report.config_json or "{}")
        # v2.90.0 — run with the permissions of the person who saved the
        # schedule, not as Administrator. The old way let anyone able to save
        # a schedule send data their own access did not cover (another
        # company, another doctype). If that person is disabled or has lost
        # access, the run fails and says so.
        run_as = getattr(doc, "run_as", None) or doc.owner
        if not run_as or not frappe.db.get_value("User", run_as, "enabled"):
            raise frappe.PermissionError(f"schedule owner {run_as or '?'} is disabled or missing")
        from neotec_insight.neotec_insight.api.studio import run_query
        previous = frappe.session.user
        frappe.set_user(run_as)
        try:
            result = run_query(config)
        finally:
            frappe.set_user(previous)

        fname, fcontent = _render_attachment(report.title or doc.report, result,
                                             (doc.file_format or "XLSX").upper())
        subject = doc.subject or f"{report.title} — {nowdate()}"
        sent_to = []

        recipients = [r.strip() for r in (doc.recipients or "").replace(";", ",").split(",") if r.strip()]
        if recipients and fcontent is not None:
            frappe.sendmail(
                recipients=recipients,
                subject=subject,
                message=_email_body(report.title, result),
                attachments=[{"fname": fname, "fcontent": fcontent}],
            )
            sent_to.append(f"email:{len(recipients)}")

        numbers = [n.strip() for n in (doc.whatsapp_numbers or "").replace(";", ",").split(",") if n.strip()]
        if numbers:
            ok = _send_whatsapp_summary(numbers, subject, result)
            if ok:
                sent_to.append(f"whatsapp:{ok}")

        doc.db_set("last_run", now_datetime(), update_modified=False)
        doc.db_set("last_status", "OK — " + (", ".join(sent_to) or "no recipients configured"),
                   update_modified=False)
        frappe.db.commit()
    except Exception:
        frappe.log_error(title="Insight schedule dispatch failed",
                         message=f"{doc.name}: {frappe.get_traceback()}")
        try:
            doc.db_set("last_run", now_datetime(), update_modified=False)
            doc.db_set("last_status", "FAILED — see Error Log", update_modified=False)
            frappe.db.commit()
        except Exception:
            pass


def _flatten_rows(result):
    cols = [c for c in (result.get("columns") or [])]
    if result.get("groups") is not None:
        rows = []
        for g in result["groups"]:
            rows.extend(g.get("rows") or (g.get("sales_rows") or []) + (g.get("return_rows") or []))
        return cols, rows
    return cols, result.get("rows") or []


def _render_attachment(title, result, file_format):
    cols, rows = _flatten_rows(result)
    if not cols:
        return None, None
    headers = [c.get("label") or c.get("field") for c in cols]
    fields = [c.get("field") for c in cols]
    if file_format == "CSV":
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(headers)
        for r in rows:
            w.writerow([r.get(f, "") for f in fields])
        return f"{frappe.scrub(title)}-{nowdate()}.csv", ("\ufeff" + buf.getvalue()).encode("utf-8")
    # XLSX via openpyxl (bundled with Frappe)
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = (title or "Report")[:31]
    ws.append(headers)
    for r in rows:
        ws.append([r.get(f) for f in fields])
    out = io.BytesIO()
    wb.save(out)
    return f"{frappe.scrub(title)}-{nowdate()}.xlsx", out.getvalue()


def _totals_text(result, max_items=4):
    cols = {c["field"]: (c.get("label") or c["field"])
            for c in (result.get("columns") or []) if c.get("numeric")}
    grand = result.get("grand_total") or {}
    parts = []
    for f, label in cols.items():
        if f in grand and len(parts) < max_items:
            parts.append(f"{label}: {flt(grand[f], 2):,.2f}")
    return " · ".join(parts)


def _email_body(title, result):
    totals = _totals_text(result)
    link = get_url("/insight")
    return (f"<p>Scheduled Insight report: <b>{frappe.utils.escape_html(title or '')}</b></p>"
            + (f"<p>{frappe.utils.escape_html(totals)}</p>" if totals else "")
            + f"<p>Rows: {result.get('row_count', 0)} · "
              f"<a href='{link}'>Open Neotec Insight</a></p>")


def _send_whatsapp_summary(numbers, subject, result) -> int:
    """Text summary via WhatsApp Business Cloud API. Requires site_config keys
    `whatsapp_token` and `whatsapp_phone_id`; returns count sent (0 = skipped)."""
    token = frappe.conf.get("whatsapp_token")
    phone_id = frappe.conf.get("whatsapp_phone_id")
    if not token or not phone_id:
        return 0
    import requests
    body = subject
    totals = _totals_text(result)
    if totals:
        body += "\n" + totals
    body += f"\nRows: {result.get('row_count', 0)} — {get_url('/insight')}"
    sent = 0
    for num in numbers:
        try:
            resp = requests.post(
                f"https://graph.facebook.com/v19.0/{phone_id}/messages",
                headers={"Authorization": f"Bearer {token}",
                         "Content-Type": "application/json"},
                json={"messaging_product": "whatsapp", "to": num.lstrip("+"),
                      "type": "text", "text": {"body": body[:4000]}},
                timeout=20)
            if resp.ok:
                sent += 1
            else:
                frappe.log_error(title="Insight WhatsApp send failed",
                                 message=f"{num}: {resp.status_code} {resp.text[:500]}")
        except Exception:
            frappe.log_error(title="Insight WhatsApp send failed",
                             message=f"{num}: {frappe.get_traceback()}")
    return sent


# ── v2.90.0: who a schedule runs as, and where it may send ───────────────────

def preflight_as_current_user(report: str) -> None:
    """Run the saved report once, small, as the current user: refuses a
    schedule whose data the person saving it cannot read."""
    from neotec_insight.neotec_insight.api.studio import run_query
    config = json.loads(frappe.db.get_value("Studio Report", report, "config_json") or "{}")
    config["limit"] = 1
    run_query(config)


def check_recipients(recipients: str, whatsapp_numbers: str) -> None:
    """Recipient policy.

    * Site config `insight_schedule_allowed_domains` (list) — when set, every
      email recipient must be at one of those domains.
    * Otherwise, a user who is not a System Manager may only send to people
      who have an enabled user account on this site.
    * WhatsApp delivery leaves the site's mail controls entirely: System
      Manager only.
    """
    from frappe import _
    is_sm = "System Manager" in frappe.get_roles()
    emails = [r.strip() for r in (recipients or "").replace(";", ",").split(",") if r.strip()]
    allowed = frappe.conf.get("insight_schedule_allowed_domains") or []
    if allowed:
        allowed = {d.lower().lstrip("@") for d in allowed}
        bad = [e for e in emails if e.rsplit("@", 1)[-1].lower() not in allowed]
        if bad:
            frappe.throw(_("These recipients are outside the allowed domains: {0}").format(", ".join(bad)))
    elif not is_sm:
        bad = [e for e in emails if not frappe.db.get_value("User", {"email": e, "enabled": 1}, "name")]
        if bad:
            frappe.throw(_("Scheduled reports can only be sent to users of this site: {0}. "
                           "A System Manager can send outside.").format(", ".join(bad)))
    if (whatsapp_numbers or "").strip() and not is_sm:
        frappe.throw(_("Only a System Manager can send scheduled reports by WhatsApp."))
