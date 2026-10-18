"""Company brand: logo and letterhead details for the screen and printouts.

v2.91.0 — moved out of api/report.py unchanged. report.py re-exports these
names, so the endpoints keep their `api.report.<name>` addresses."""

import frappe
from frappe import _


def _abs_file_url(url: str) -> str:
    """Site-absolute URL for a Frappe /files path; pass anything else through."""
    if not url:
        return ""
    if url.startswith("/"):
        try:
            return frappe.utils.get_url().rstrip("/") + url
        except Exception:
            return url
    return url


@frappe.whitelist()
def company_brand(company: str | None = None) -> dict:
    """Name, logo and statutory identifiers for the header brand block.

    Falls back to the global default company so the header is never blank on
    first paint, and never raises — the shell must render even if the Company
    doctype is missing or the user cannot read it.
    """
    out = {
        "company": "", "label": "", "logo": "", "logo_source": "",
        "tax_id": "", "can_edit": 0,
    }
    try:
        if not frappe.db.exists("DocType", "Company"):
            return out
        if not company:
            company = frappe.defaults.get_user_default("Company") \
                or frappe.defaults.get_global_default("company") or ""
        if not company:
            rows = frappe.get_all("Company", fields=["name"], order_by="company_name asc",
                                  limit_page_length=1)
            company = rows[0]["name"] if rows else ""
        if not company:
            return out

        fields = ["company_name", "tax_id"]
        try:
            if frappe.get_meta("Company").get_field("company_logo"):
                fields.append("company_logo")
        except Exception:
            pass
        c = frappe.db.get_value("Company", company, fields, as_dict=True) or {}

        out["company"] = company
        out["label"] = c.get("company_name") or company
        out["tax_id"] = c.get("tax_id") or ""
        logo = c.get("company_logo") or ""
        if logo:
            out["logo"] = _abs_file_url(logo)
            out["logo_source"] = "company"
        else:
            # Provision #2: the site's default Letter Head image, when the
            # Company master itself carries no logo.
            try:
                lh = frappe.db.get_value("Letter Head", {"disabled": 0, "is_default": 1},
                                         ["image"], as_dict=True)
                if lh and lh.get("image"):
                    out["logo"] = _abs_file_url(lh["image"])
                    out["logo_source"] = "letterhead"
            except Exception:
                pass
        try:
            out["can_edit"] = 1 if frappe.has_permission("Company", "write", company) else 0
        except Exception:
            out["can_edit"] = 0
    except Exception as e:
        frappe.log_error(f"company_brand failed: {e}", "Neotec Insight: brand")
    return out


@frappe.whitelist(methods=["POST"])
def set_company_logo(company: str, logo_url: str = "") -> dict:
    """Write an uploaded logo back to the Company master.

    This is the 'provision for logo if not available in company master':
    the operator uploads once from the shell header and every report — screen,
    Print, PDF and Excel — resolves the same image afterwards. An empty
    `logo_url` clears it.
    """
    if not company:
        frappe.throw(_("Select a company first."))
    if not frappe.db.exists("Company", company):
        frappe.throw(_("Company {0} not found.").format(company))
    if not frappe.has_permission("Company", "write", company):
        frappe.throw(_("Not permitted to change the company logo."))
    try:
        if not frappe.get_meta("Company").get_field("company_logo"):
            frappe.throw(_("This site's Company master has no logo field."))
    except frappe.ValidationError:
        raise
    except Exception:
        pass

    url = (logo_url or "").strip()
    if url and not (url.startswith("/files/") or url.startswith("/private/files/")):
        frappe.throw(_("Upload the logo as a file first."))

    frappe.db.set_value("Company", company, "company_logo", url or None)
    frappe.db.commit()
    frappe.clear_document_cache("Company", company)
    return {"ok": True, "logo": _abs_file_url(url)}
