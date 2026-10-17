"""One access policy for every Insight API (v2.90.0).

Ten modules had their own `_require_read`, and they had drifted: some
checked the HR-only rule, some did not; the People service accepted anyone
with ledger access. Every module now delegates here.

Capabilities
  finance  — read ledger-derived figures. GL Entry read, and not a People-only
             (HR) user.
  people   — read headcount, wages, payroll, end-of-service. HR authority
             (Salary Slip read where HRMS is installed, otherwise Employee
             read with an HR role) or an approved executive role. Ledger
             access alone does NOT grant it.

Scope
  company  — every request to an Insight endpoint that names a company is
             checked against the companies the user may see (User
             Permissions apply). A restricted user who names no company gets
             their only company filled in, or is asked to choose one.
             Enforced once, centrally, in `enforce_request_scope` (a
             before_request hook), so an endpoint cannot forget it.
  report   — a named report must be readable by the user (its own
             has_permission rules: active, owner, manager).
  file     — an uploaded file is read only if Frappe's File permission allows
             it (private files: owner / the attached document's readers).
"""

from __future__ import annotations

import inspect

import frappe
from frappe import _

HR_ROLES = {"Insight HR", "HR Manager", "HR User"}
EXEC_ROLES = {"Insight CFO", "Insight CEO", "System Manager"}
# Same exemption set the People-only rule has always used (report.py
# _check_hr_only): a user with an HR role and none of these is People-only.
FINANCE_ROLES = {"System Manager", "Accounts Manager", "Insight CFO", "Insight CEO", "Insight Group Viewer"}
API_PREFIX = "neotec_insight.neotec_insight.api."


def _roles(user=None) -> set[str]:
    return set(frappe.get_roles(user or frappe.session.user))


def is_admin(user=None) -> bool:
    user = user or frappe.session.user
    return user == "Administrator" or "System Manager" in _roles(user)


def is_people_only(user=None) -> bool:
    roles = _roles(user)
    return bool(roles & HR_ROLES) and not (roles & FINANCE_ROLES)


# ── capabilities ─────────────────────────────────────────────────────────────

def require_finance_read(message: str | None = None) -> None:
    if frappe.session.user == "Administrator":
        return
    if not frappe.has_permission("GL Entry", "read"):
        frappe.throw(message or _("You are not permitted to view financial data."),
                     frappe.PermissionError)
    if is_people_only():
        frappe.throw(_("Your access is limited to the People workspace."), frappe.PermissionError)


def can_read_people(user=None) -> bool:
    user = user or frappe.session.user
    if user == "Administrator":
        return True
    roles = _roles(user)
    if roles & EXEC_ROLES:
        return True
    try:
        if frappe.db.exists("DocType", "Salary Slip"):
            return bool(frappe.has_permission("Salary Slip", "read", user=user))
    except Exception:
        pass
    return bool(roles & HR_ROLES) and bool(frappe.has_permission("Employee", "read", user=user))


def require_people_read() -> None:
    if not can_read_people():
        frappe.throw(_("Headcount, wages and end-of-service need HR or payroll access."),
                     frappe.PermissionError)


# ── company scope ────────────────────────────────────────────────────────────

def allowed_companies() -> list[str]:
    """Companies the user may see, User Permissions applied. Request-cached."""
    cache = getattr(frappe.local, "insight_allowed_companies", None)
    if cache is not None:
        return cache
    if frappe.session.user == "Administrator":
        out = frappe.get_all("Company", pluck="name", limit_page_length=0)
    else:
        out = frappe.get_list("Company", pluck="name", limit_page_length=0)
    frappe.local.insight_allowed_companies = out
    return out


def is_company_restricted() -> bool:
    if frappe.session.user == "Administrator":
        return False
    return len(allowed_companies()) < frappe.db.count("Company")


def check_company(company: str | None) -> None:
    if not company or frappe.session.user == "Administrator":
        return
    if company not in allowed_companies():
        frappe.throw(_("You do not have access to company {0}.").format(company), frappe.PermissionError)


def check_companies(companies) -> None:
    if isinstance(companies, str):
        try:
            companies = frappe.parse_json(companies)
        except Exception:
            companies = [c.strip() for c in companies.split(",")]
    for c in companies or []:
        check_company(c)


def scoped_company(company: str | None) -> str | None:
    """The company to use for a query: the one asked for (checked), or for a
    restricted user with exactly one company, that one."""
    if company:
        check_company(company)
        return company
    if is_company_restricted():
        allowed = allowed_companies()
        if len(allowed) == 1:
            return allowed[0]
        frappe.throw(_("Choose a company."), frappe.PermissionError)
    return None


# ── report and file ──────────────────────────────────────────────────────────

RD = "Insight Report Definition"


def resolve_report_name(report: str | None) -> str | None:
    if not report:
        return None
    if frappe.db.exists(RD, report):
        return report
    return frappe.db.get_value(RD, {"slug": report}, "name")


def check_report(report, ptype: str = "read") -> None:
    """report: a name/slug or a Document."""
    if frappe.session.user == "Administrator":
        return
    doc = report if hasattr(report, "doctype") else None
    if doc is None:
        name = resolve_report_name(report)
        if not name:
            return                       # the endpoint will report "not found"
        doc = frappe.get_doc(RD, name)
    if not frappe.has_permission(RD, ptype, doc=doc):
        frappe.throw(_("You do not have access to report {0}.").format(doc.name), frappe.PermissionError)
    if getattr(doc, "company", None):
        check_company(doc.company)


def check_file(file_doc) -> None:
    """Refuse to read an uploaded file the user may not read through Frappe."""
    if frappe.session.user == "Administrator":
        return
    if not frappe.has_permission("File", "read", doc=file_doc):
        frappe.throw(_("You do not have access to this file."), frappe.PermissionError)


# ── central enforcement ──────────────────────────────────────────────────────

COMPANY_ARGS = ("company",)
COMPANIES_ARGS = ("companies",)
REPORT_ARGS = ("report", "report_name", "report_slug", "pool_report")
DIMENSION_ARGS = {"cost_center": "Cost Center", "project": "Project", "department": "Department",
                  "branch": "Branch"}


def check_link(doctype: str, name: str) -> None:
    """A single named dimension record must be readable (User Permissions)."""
    if frappe.session.user == "Administrator":
        return
    try:
        if not frappe.db.exists(doctype, name):
            return
    except Exception:
        return
    if not frappe.has_permission(doctype, "read", doc=name):
        frappe.throw(_("You do not have access to {0} {1}.").format(_(doctype), name), frappe.PermissionError)


def _target_method() -> str | None:
    try:
        path = frappe.request.path or ""
    except Exception:
        return None
    if path.startswith("/api/method/"):
        return path[len("/api/method/"):].strip("/")
    if path.startswith("/api/v2/method/"):
        return path[len("/api/v2/method/"):].strip("/")
    return None


def enforce_request_scope() -> None:
    """before_request hook: company and report scope for every Insight call."""
    method = _target_method()
    if not method or not method.startswith(API_PREFIX):
        return
    if frappe.session.user in ("Guest", "Administrator"):
        return
    form = frappe.local.form_dict or {}
    for k in COMPANY_ARGS:
        if form.get(k):
            check_company(form.get(k))
    for k in COMPANIES_ARGS:
        if form.get(k):
            check_companies(form.get(k))
    for k in REPORT_ARGS:
        if form.get(k) and isinstance(form.get(k), str):
            check_report(form.get(k))
    for k, dt in DIMENSION_ARGS.items():
        v = form.get(k)
        if v and isinstance(v, str) and not v.startswith(("[", "{")):
            check_link(dt, v)
    # A restricted user who names no company: fill in their only company for
    # endpoints that take one, so a query never runs across all companies.
    if not form.get("company") and is_company_restricted():
        try:
            fn = frappe.get_attr(method)
            params = inspect.signature(inspect.unwrap(fn)).parameters
        except Exception:
            # Cannot tell whether this endpoint takes a company. Fail closed
            # for a company-restricted user rather than let it run unscoped.
            frappe.throw(_("Choose a company."), frappe.PermissionError)
        if "company" in params:
            allowed = allowed_companies()
            if len(allowed) == 1:
                frappe.local.form_dict["company"] = allowed[0]
            else:
                frappe.throw(_("Choose a company."), frappe.PermissionError)


# ── uploaded files (v2.90.0) ─────────────────────────────────────────────────

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
STATEMENT_EXTS = {".csv", ".tsv", ".txt", ".xlsx", ".xls", ".pdf"}
SLIP_EXTS = {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".heic", ".xlsx", ".xls"}


def readable_file(file_url: str, allowed_exts: set[str] | None = None,
                  max_bytes: int = MAX_UPLOAD_BYTES):
    """(File doc, absolute path) for an upload the user may read.

    The file must be a File record the user can read through Frappe (owner,
    or a reader of the document it is attached to, or public), of an allowed
    type and size, and resolve to a path inside this site's files folders.
    A parser is never pointed at a path the caller made up."""
    import os
    if not file_url:
        frappe.throw(_("A file is required."))
    name = frappe.db.get_value("File", {"file_url": file_url}, "name")
    if not name:
        frappe.throw(_("File not found."), frappe.DoesNotExistError)
    file_doc = frappe.get_doc("File", name)
    check_file(file_doc)
    ext = os.path.splitext(file_doc.file_name or file_url)[1].lower()
    if allowed_exts and ext not in allowed_exts:
        frappe.throw(_("Files of type {0} cannot be read here.").format(ext or "?"))
    path = os.path.realpath(file_doc.get_full_path())
    roots = [os.path.realpath(frappe.get_site_path(p, "files")) for p in ("public", "private")]
    if not any(path.startswith(r + os.sep) for r in roots):
        frappe.throw(_("File is outside the site's file storage."), frappe.PermissionError)
    try:
        size = os.path.getsize(path)
    except OSError:
        frappe.throw(_("File not found on disk."), frappe.DoesNotExistError)
    if size > max_bytes:
        frappe.throw(_("File is too large ({0} MB; the limit is {1} MB).")
                     .format(round(size / 1048576, 1), max_bytes // 1048576))
    return file_doc, path
