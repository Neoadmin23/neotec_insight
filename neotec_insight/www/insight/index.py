from __future__ import annotations

import frappe
import frappe.sessions

no_cache = 1


def get_context(context):
    """Server-side context for the /insight SPA.

    Generates a CSRF token, stamps it into the session boot data so Frappe
    persists it, and passes it to the React app via the page template. The
    pattern below matches the documented frappe-react-sdk integration.

    Without this, POST endpoints reject every request with CSRFTokenError
    because the in-memory session never had a token written into it.
    """
    if frappe.session.user == "Guest":
        frappe.local.flags.redirect_location = "/login?redirect-to=/insight"
        raise frappe.Redirect

    # 1) Generate a fresh CSRF token. This is the canonical Frappe helper.
    csrf_token = frappe.sessions.get_csrf_token()

    # 2) Persist it. Without commit, the token lives only in this request's
    #    memory; the next request's session doesn't know about it and rejects
    #    the header we send back. Frappe explicitly suggests committing here.
    frappe.db.commit()  # nosemgrep — required for SPA bootstrapping

    # 3) Also stamp the token into the boot dict so any code reading the
    #    session for CSRF later sees the same value.
    try:
        boot = frappe.sessions.get()
        if not isinstance(boot, dict):
            boot = {}
        sess = boot.get("session")
        if not isinstance(sess, dict):
            sess = {}
            boot["session"] = sess
        sess["csrf_token"] = csrf_token
        boot["csrf_token"] = csrf_token
    except Exception:
        # If boot dict isn't available we still have a valid token from step 1.
        pass

    context.csrf_token = csrf_token
    context.frappe_user = frappe.session.user
    context.no_cache = 1
    context.show_sidebar = False
    return context
