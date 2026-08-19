# v2.54.0 — render a print document to PDF on the server.
#
# The browser can already produce a PDF through Print → Save as PDF, but that
# depends on the operator picking the right destination and leaves the
# browser's own header/footer under their control. This renders the same
# document Insight builds for printing and hands back a file, so a PDF is one
# click and identical on every machine.
#
# The client posts the HTML it already assembled rather than the server
# rebuilding it: the letterhead, themes, column selection and bidi handling all
# live in the SPA, and duplicating them in Python would guarantee the two
# drift. That means the payload is user-supplied markup, so it is sanitised
# before it reaches wkhtmltopdf and local file access is disabled — the
# renderer must not be usable to read the server's filesystem.

import re

import frappe
from frappe import _
from frappe.utils.pdf import get_pdf

# Anything that can execute or reach back into the host is stripped. The print
# documents are static markup — tables, inline CSS and an <img> for the logo —
# so this costs nothing legitimate.
_STRIP_TAGS = ("script", "iframe", "object", "embed", "applet", "link", "meta", "base", "form")
_MAX_HTML_BYTES = 12 * 1024 * 1024


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


def _sanitise(html: str) -> str:
	for tag in _STRIP_TAGS:
		html = re.sub(
			r"<%s\b[^>]*>.*?</%s\s*>" % (tag, tag), "", html, flags=re.I | re.S
		)
		html = re.sub(r"<%s\b[^>]*/?>" % tag, "", html, flags=re.I)
	# Event handlers and javascript: URLs.
	html = re.sub(r"\son\w+\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)", "", html, flags=re.I)
	html = re.sub(r"(href|src)\s*=\s*([\"']?)\s*javascript:[^\"'>]*\2", r"\1=\2#\2", html, flags=re.I)
	return html


@frappe.whitelist()
def render_pdf(html: str, filename: str = "report", orientation: str = "Landscape",
               page_size: str = "A4"):
	"""Render an Insight print document to PDF and return it as a download."""
	_require_read()
	if not html or not html.strip():
		frappe.throw(_("Nothing to render."))
	if len(html.encode("utf-8")) > _MAX_HTML_BYTES:
		frappe.throw(_("This report is too large to render server-side. Use Print instead."))

	orientation = "Portrait" if str(orientation).lower().startswith("p") else "Landscape"
	page_size = (page_size or "A4").upper()
	if page_size not in ("A4", "A3", "LETTER", "LEGAL"):
		page_size = "A4"

	options = {
		"orientation": orientation,
		"page-size": "A4" if page_size == "A4" else page_size.title(),
		"margin-top": "0mm",
		"margin-bottom": "0mm",
		"margin-left": "0mm",
		"margin-right": "0mm",
		"encoding": "UTF-8",
		"print-media-type": None,
		"disable-local-file-access": None,
		"disable-javascript": None,
		"load-error-handling": "ignore",
		"load-media-error-handling": "ignore",
	}

	try:
		content = get_pdf(_sanitise(html), options=options)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "neotec_insight.render_pdf failed")
		frappe.throw(_("PDF rendering failed on the server. Use Print → Save as PDF instead."))

	safe = re.sub(r"[^A-Za-z0-9._\- ]+", "_", filename or "report").strip() or "report"
	frappe.local.response.filename = "%s.pdf" % safe[:120]
	frappe.local.response.filecontent = content
	frappe.local.response.type = "download"
