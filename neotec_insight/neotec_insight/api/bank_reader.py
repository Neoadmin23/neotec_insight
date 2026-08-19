# Copyright (c) 2026, Neotec and contributors
# -----------------------------------------------------------------------------
# Bank slip reader — Milestone 1.
#
# Pipeline:  uploaded file  ->  _extract_text  ->  structure_slip  ->  Insight
# Bank Slip (status=Extracted)  ->  stage_draft_payment_entry (draft, unsubmitted).
#
# Structuring is LLM-first (Ollama, local / open-source / data-sovereign) with a
# deterministic heuristic fallback so the reader still works when the model is
# offline. Nothing is auto-posted: every slip stages a DRAFT entry for a human
# to review and submit.
# -----------------------------------------------------------------------------
from __future__ import annotations
import json
import re
import unicodedata
from typing import Any

import frappe
from frappe import _
from frappe.utils import flt, getdate

# ---- config -----------------------------------------------------------------

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


def _ollama_config() -> dict:
    """Ollama endpoint + models. Override via site_config.json or Insight AI
    Settings without code changes."""
    cfg = {
        "url": frappe.conf.get("insight_ollama_url") or "http://localhost:11434",
        "text_model": frappe.conf.get("insight_ollama_text_model") or "qwen2.5",
        "vision_model": frappe.conf.get("insight_ollama_vision_model") or "qwen2.5vl",
        "timeout": int(frappe.conf.get("insight_ollama_timeout") or 120),
    }
    try:
        s = frappe.get_single("Insight AI Settings")
        cfg["url"] = (getattr(s, "ollama_url", None) or cfg["url"]).rstrip("/")
        cfg["text_model"] = getattr(s, "ollama_text_model", None) or cfg["text_model"]
        cfg["vision_model"] = getattr(s, "ollama_vision_model", None) or cfg["vision_model"]
    except Exception:
        cfg["url"] = cfg["url"].rstrip("/")
    return cfg


# ---- normalized schema ------------------------------------------------------

_SCHEMA_KEYS = (
    "bank", "channel", "direction", "currency", "amount", "fee", "vat",
    "converted_amount", "converted_currency", "conversion_rate",
    "source_account", "counterparty_name", "counterparty_account",
    "counterparty_bank", "value_date", "processing_date",
    "bank_reference", "transaction_id", "references", "purpose", "purpose_group",
)

_EXTRACTION_PROMPT = (
    "You read a single bank transfer / payment slip and return ONE JSON object. "
    "Return ONLY the JSON, no prose, no markdown. Use null for anything not present. "
    "The slip may be Arabic, English, or both. Schema:\n"
    "{"
    '"bank":str,"channel":str,"direction":"Outgoing"|"Incoming",'
    '"currency":str,"amount":number,"fee":number,"vat":number,'
    '"converted_amount":number,"converted_currency":str,"conversion_rate":number,'
    '"source_account":str,"counterparty_name":str,"counterparty_account":str,'
    '"counterparty_bank":str,"value_date":"YYYY-MM-DD","processing_date":"YYYY-MM-DD",'
    '"bank_reference":str,"transaction_id":str,"references":[str],'
    '"purpose":str,"purpose_group":str,"description":str'
    "}\n"
    "amount = the principal transferred (exclude fee/VAT). direction is Outgoing "
    "if money leaves the account holder. bank_reference = the single most unique "
    "transfer reference (e.g. FT/REF/transaction number)."
)


# ---- text extraction --------------------------------------------------------

def _extract_text_pdf(path: str) -> str:
    import fitz  # PyMuPDF
    doc = fitz.open(path)
    return "\n".join(p.get_text() for p in doc)


def _extract_text_excel(path: str) -> str:
    """Flatten an .xlsx/.xls slip to label:value text for the structurer.
    (Multi-row bank *statements* are handled by reconciliation, not here.)"""
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    lines = []
    for ws in wb.worksheets:
        for row in ws.iter_rows(values_only=True):
            cells = [str(c) for c in row if c not in (None, "")]
            if cells:
                lines.append("\t".join(cells))
    return "\n".join(lines)


def _ollama_structure_vision(image_path: str) -> dict[str, Any]:
    """Read a scanned/image slip directly with the local vision model
    (Qwen2.5-VL) — OCR + structure in one call, Arabic + English."""
    import base64
    import requests
    cfg = _ollama_config()
    with open(image_path, "rb") as fh:
        b64 = base64.b64encode(fh.read()).decode()
    r = requests.post(
        f"{cfg['url']}/api/chat",
        json={
            "model": cfg["vision_model"],
            "format": "json",
            "stream": False,
            "options": {"temperature": 0},
            "messages": [
                {"role": "system", "content": _EXTRACTION_PROMPT + _direction_hint()},
                {"role": "user", "content": "Read this bank slip.", "images": [b64]},
            ],
        },
        timeout=cfg["timeout"],
    )
    r.raise_for_status()
    return json.loads(r.json().get("message", {}).get("content", "{}"))


def _extract_text(path: str, content_type: str | None = None) -> tuple[str, str]:
    """Return (text, channel). channel is 'PDF Text', 'Excel', or 'Vision'."""
    lower = (path or "").lower()
    if lower.endswith(".pdf"):
        text = _extract_text_pdf(path)
        if len(text.strip()) >= 40:          # digital PDF with a real text layer
            return text, "PDF Text"
        return "", "Vision"                  # scanned PDF -> vision
    if lower.endswith((".xlsx", ".xls", ".xlsm")):
        return _extract_text_excel(path), "Excel"
    # images -> vision model handles OCR + structure in one shot
    return "", "Vision"


# ---- structuring ------------------------------------------------------------

def _direction_hint() -> str:
    ids = sorted(_company_account_ids())
    tokens = _company_name_tokens()
    return (
        "\nDIRECTION: 'our company' is identified by these account numbers/IBANs: "
        + (", ".join(ids) if ids else "(none on file)")
        + "; and these name tokens: " + ", ".join(tokens) + ". "
        "If our company is the beneficiary/recipient of the transfer, direction='Incoming'. "
        "If our company is the payer / the account the statement belongs to, direction='Outgoing'."
    )


def _ollama_structure_text(text: str) -> dict[str, Any]:
    import requests
    cfg = _ollama_config()
    r = requests.post(
        f"{cfg['url']}/api/chat",
        json={
            "model": cfg["text_model"],
            "format": "json",
            "stream": False,
            "options": {"temperature": 0},
            "messages": [
                {"role": "system", "content": _EXTRACTION_PROMPT + _direction_hint()},
                {"role": "user", "content": text},
            ],
        },
        timeout=cfg["timeout"],
    )
    r.raise_for_status()
    content = r.json().get("message", {}).get("content", "{}")
    return json.loads(content)


_NUM = r"([0-9][0-9,]*\.?[0-9]*)"

def _num(s: str) -> float:
    return flt((s or "").replace(",", "")) if s else 0.0

def _find(rx: str, text: str, flags=re.I) -> str | None:
    m = re.search(rx, text, flags)
    return m.group(1).strip() if m else None

def _norm_date(s: str | None) -> str | None:
    if not s:
        return None
    s = s.strip().split()[0]
    for sep in ("/", "-"):
        if sep in s:
            a, b, c = (s.split(sep) + ["", ""])[:3]
            if len(a) == 4:                    # YYYY-MM-DD
                return f"{a}-{b}-{c}"
            if len(c) == 4:                    # DD/MM/YYYY (KSA convention)
                return f"{c}-{b}-{a}"
    return None


# A "money" value: digit groups with a decimal part (e.g. 12,000.00 / 6.75).
# Requiring the decimal point keeps IBANs, account numbers and VAT-registration
# numbers (long bare integers) from being mistaken for amounts.
_MONEY = r"([0-9]{1,3}(?:,[0-9]{3})*\.[0-9]{2}|[0-9]{1,9}\.[0-9]{2})"

def _money_after(label: str, text: str) -> float:
    """First money value appearing within ~40 chars after a label (same or next
    line)."""
    m = re.search(r"(?:" + label + r")[^0-9A-Za-z]{0,40}?" + _MONEY, text, re.I)
    return _num(m.group(1)) if m else 0.0


def _heuristic_structure(text: str) -> dict[str, Any]:
    """Generic, label-based best-effort parse. The LLM is the primary path; this
    keeps the reader functional offline and seeds the review screen. It is
    deliberately conservative — it would rather leave a field null than guess."""
    out: dict[str, Any] = {k: None for k in _SCHEMA_KEYS}
    out["references"] = []
    out["direction"] = "Outgoing"
    out["currency"] = _find(r"\b(SAR|AED|USD|EUR|GBP)\b", text) or "SAR"

    # --- amounts (decimal-anchored, label-preferred) ---
    out["amount"] = (
        _money_after(r"\bAmount\b(?!\s*Group)", text)
        or _money_after(r"Debit|ﻣﺪﻳﻦ", text)
        or _num((re.search(_MONEY + r"\s*(?:SAR|AED)", text) or [None, None])[1] if re.search(_MONEY + r"\s*(?:SAR|AED)", text) else None)
    )
    out["fee"] = _money_after(r"Base Fee|ﺍﻟﺮﺳﻮﻡ", text) or _money_after(r"(?<!VAT )\bFee\b", text)
    out["vat"] = _money_after(r"VAT(?:\s*Fee)?|ﺿﺮﻳﺒﺔ", text)
    out["converted_amount"] = _money_after(r"Converted Amount", text)
    tot = _money_after(r"Total Amount", text)
    # fee/vat sanity: must be smaller than the principal
    if out["fee"] and out["amount"] and out["fee"] >= out["amount"]:
        out["fee"] = 0.0
    if out["vat"] and out["amount"] and out["vat"] >= out["amount"]:
        out["vat"] = 0.0
    # back-fill fee from total when only total is shown
    if tot and out["amount"] and not out["fee"] and not out["vat"]:
        out["fee"] = round(tot - out["amount"], 2) if tot > out["amount"] else 0.0

    # --- accounts (label-first, IBAN fallback) ---
    ibans = re.findall(r"\b([A-Z]{2}[0-9]{18,24})\b", text)
    out["source_account"] = (
        _find(r"Account From\s*\n?\s*([A-Z0-9]{8,})", text)
        or (ibans[0] if ibans else None)
    )
    out["counterparty_account"] = (
        _find(r"Beneficiary Account\s*\n?\s*([A-Z0-9]{8,})", text)
        or next((i for i in ibans if i != out["source_account"]), None)
    )
    out["counterparty_name"] = _find(r"Beneficiary Name\s*\n?\s*([^\n]+)", text)
    out["counterparty_bank"] = _find(r"Beneficiary Bank(?:\s*ID)?\s*\n?\s*([^\n]+)", text)

    # --- references ---
    out["transaction_id"] = _find(r"Transaction ID\s*\n?\s*([A-Z0-9]{6,})", text)
    out["bank_reference"] = (
        _find(r"Ref\.?\s*#\s*\n?\s*([A-Z0-9]{6,})", text)
        or _find(r"\bREF\s+([A-Z0-9]{6,})", text)
        or out["transaction_id"]
    )
    refs = re.findall(r"\b(?:REF\s*)?(FT[A-Z0-9]{6,}|TBC[0-9]{6,}|NCBK[A-Z0-9]{4,}|[0-9]{11,})\b", text)
    out["references"] = list(dict.fromkeys(refs))[:8]
    if not out["bank_reference"] and out["references"]:
        out["bank_reference"] = out["references"][0]

    # Description / narration — the free-text the user reads to pick the account.
    # Conservative: the value on the line right after a standalone label. The
    # full slip text is always shown in the UI, so this need not be exhaustive.
    desc_bits = []
    for lbl in ("Narration", "Detail", "Description", "Remarks"):
        m = re.search(r"(?<![A-Za-z])" + lbl + r"\s*\n+([^\n]{3,200})", text)
        if m:
            desc_bits.append(m.group(1).strip())
    out["description"] = (" | ".join(dict.fromkeys(desc_bits))[:600]) or None
    out["purpose"] = _find(r"Purpose\s*\n?\s*([^\n]+)", text)
    out["purpose_group"] = _find(r"Purpose Group\s*\n?\s*([^\n]+)", text)

    for d in re.findall(r"\b(\d{2}[/-]\d{2}[/-]\d{4})\b", text):
        out["value_date"] = _norm_date(d)
        break

    # --- bank name (plain text scan) ---
    low = text.lower()
    if "alinma" in low or "ﺍﻻﻧﻤﺎﺀ" in text:
        out["bank"] = "Alinma"
    elif "snb" in low or "saudi national bank" in low:
        out["bank"] = "SNB"
    elif "riyad bank" in low or "ﺍﻟﺮﻳﺎﺽ" in text:
        out["bank"] = "Riyad Bank"
    return out


def structure_slip(text: str, channel: str, path: str | None = None) -> tuple[dict[str, Any], str, float]:
    """Return (data, method, confidence)."""
    if channel in ("PDF Text", "Excel") and text.strip():
        try:
            data = _ollama_structure_text(text)
            return data, channel, 0.9
        except Exception:
            frappe.log_error(title="bank_reader: Ollama text structuring failed",
                             message=frappe.get_traceback())
            return _heuristic_structure(text), channel, 0.55
    if channel == "Vision" and path:
        # Image or scanned PDF -> vision model (OCR + structure in one call).
        try:
            data = _ollama_structure_vision(path)
            data.setdefault("references", [])
            return data, "Vision", 0.85
        except Exception:
            frappe.log_error(title="bank_reader: Ollama vision structuring failed",
                             message=frappe.get_traceback())
            return _heuristic_structure(text), "Vision", 0.3
    return _heuristic_structure(text), channel, 0.5


# ---- direction classifier (inward / outward) --------------------------------
# Anchors on the company's own identity: account numbers + IBANs read from
# ERPNext Bank Account records (company accounts only), plus name tokens
# (IRSAA / ارساء). Arabic is NFKC-normalized so presentation forms still match.
# Rule: if the company is the recipient (beneficiary / "الى") -> Incoming;
# if the company is present but not the recipient -> Outgoing; else unconfirmed.

def _acc_norm(s: str | None) -> str:
    return re.sub(r"[\s-]", "", (s or "")).upper()


def _norm_ar(s: str | None) -> str:
    return unicodedata.normalize("NFKC", s or "")


def _company_account_ids() -> set[str]:
    ids: set[str] = set()
    try:
        for r in frappe.get_all("Bank Account", filters={"is_company_account": 1},
                                fields=["bank_account_no", "iban"]):
            if r.get("bank_account_no"):
                ids.add(_acc_norm(r["bank_account_no"]))
            if r.get("iban"):
                ids.add(_acc_norm(r["iban"]))
    except Exception:
        pass
    return ids


def _company_name_tokens() -> list[str]:
    tokens = ["IRSAA", "ارساء", "إرساء"]
    try:
        s = frappe.get_single("Insight AI Settings")
        extra = getattr(s, "company_aliases", "") or ""
        tokens += [x.strip() for x in re.split(r"[,\n]", extra) if x.strip()]
    except Exception:
        pass
    return tokens


def _name_has_company(text: str, tokens: list[str]) -> bool:
    n = _norm_ar(text).upper()
    for tok in tokens:
        tk = _norm_ar(tok).upper()
        if not tk:
            continue
        if re.search(r"[A-Z]", tk):                      # latin token -> word-ish boundary
            if re.search(r"(?<![A-Z])" + re.escape(tk) + r"(?![A-Z])", n):
                return True
        elif tk in n:                                    # arabic token -> substring (post-NFKC)
            return True
    return False


def _accts_in(text: str) -> set[str]:
    return {_acc_norm(a) for a in re.findall(r"SA\d{22}|\b\d{10,18}\b", text)}


def _company_is_recipient(t: str, data: dict, ids: set[str], tokens: list[str]) -> bool:
    if _acc_norm(data.get("counterparty_account")) in ids or _name_has_company(data.get("counterparty_name") or "", tokens):
        return True
    # Arabic 'to' — proximity on either side (RTL extraction reorders text).
    for m in re.finditer(r"(الى|إلى)", t):
        w = t[max(0, m.start() - 60): m.end() + 60]
        if (_accts_in(w) & ids) or _name_has_company(w, tokens):
            return True
    # English markers — only AFTER (LTR), so a holder account before "Beneficiary"
    # can't false-trigger.
    for m in re.finditer(r"(beneficiary|in favou?r of)", t, re.I):
        w = t[m.end(): m.end() + 70]
        if (_accts_in(w) & ids) or _name_has_company(w, tokens):
            return True
    return False


def classify_direction(text: str, data: dict) -> tuple[str | None, str]:
    """Return (direction, basis). direction is 'Incoming', 'Outgoing', or None
    when the company can't be identified on the slip (left for the user to set)."""
    ids = _company_account_ids()
    tokens = _company_name_tokens()
    t = _norm_ar(text)
    present = bool(_accts_in(t) & ids) or _name_has_company(t, tokens)
    if not present:
        return None, "Company not identified on the slip — set direction manually."
    if _company_is_recipient(t, data, ids, tokens):
        return "Incoming", "Company is the beneficiary/recipient."
    return "Outgoing", "Company is the payer / account holder."



def _slip_from_data(data: dict, *, company: str, file_url: str, raw_text: str,
                    method: str, confidence: float) -> "frappe.Document":
    refs = data.get("references") or []
    doc = frappe.new_doc("Insight Bank Slip")
    doc.company = company
    doc.status = "Extracted"
    doc.extraction_method = method
    doc.extraction_confidence = round((confidence or 0) * 100, 1)
    doc.source_file = file_url
    doc.raw_text = (raw_text or "")[:140000]
    for fld in ("bank", "channel", "direction", "currency", "source_account",
                "counterparty_name", "counterparty_account", "counterparty_bank",
                "bank_reference", "transaction_id", "purpose", "purpose_group",
                "description", "direction_basis", "converted_currency"):
        if data.get(fld):
            doc.set(fld, data.get(fld))
    for fld in ("amount", "fee", "vat", "converted_amount", "conversion_rate"):
        if data.get(fld) not in (None, ""):
            doc.set(fld, flt(data.get(fld)))
    for fld in ("value_date", "processing_date"):
        if data.get(fld):
            try:
                doc.set(fld, getdate(data.get(fld)))
            except Exception:
                pass
    if refs:
        doc.references_json = json.dumps(refs, ensure_ascii=False)
    doc.insert(ignore_permissions=False)
    return doc



def _require_write(doctype: str) -> None:
    """Refuse a write from a user who lacks permission on the doctype.

    Frappe checks permissions inside `doc.save()`, but these endpoints use
    `ignore_permissions=True`, `frappe.db.set_value` or raw SQL — all of which
    bypass that check. Whitelisted and unguarded, they were callable directly
    over `/api/method/...` by any authenticated user, including a read-only
    one. The guard restores the check the bypass removed.
    """
    if not frappe.has_permission(doctype, "write"):
        frappe.throw(
            _("You are not permitted to modify {0}.").format(_(doctype)),
            frappe.PermissionError,
        )


@frappe.whitelist()
def read_slip(file_url: str, company: str | None = None) -> dict:
    """Read an uploaded slip (PDF/image) into a staged Insight Bank Slip.

    Returns {slip, data, method, confidence}. Status is 'Extracted' — nothing is
    posted. The caller reviews, then calls stage_draft_payment_entry()."""
    _require_write("Insight Bank Slip")
    if not file_url:
        frappe.throw("file_url is required.")
    company = company or frappe.defaults.get_user_default("Company") \
        or frappe.db.get_single_value("Global Defaults", "default_company")

    file_doc = frappe.get_doc("File", {"file_url": file_url})
    path = file_doc.get_full_path()

    text, channel = _extract_text(path)
    data, method, confidence = structure_slip(text, channel, path=path)

    # Authoritative inward/outward classification from the company's own identity.
    # (Text path; for vision the model returns direction in `data`.)
    if text.strip():
        direction, basis = classify_direction(text, data)
        if direction:
            data["direction"] = direction
        data["direction_basis"] = basis

    # Duplicate guard — a slip with the same bank reference already staged?
    ref = (data.get("bank_reference") or "").strip()
    if ref:
        existing = frappe.db.get_value(
            "Insight Bank Slip", {"bank_reference": ref, "company": company},
            ["name", "status", "payment_entry"], as_dict=True)
        if existing:
            # Refresh direction under the current classifier — a slip read before
            # the classifier shipped may carry a stale direction. Don't override a
            # manual correction.
            if text.strip() and data.get("direction"):
                prior_basis = frappe.db.get_value(
                    "Insight Bank Slip", existing.name, "direction_basis") or ""
                if "manual" not in prior_basis.lower():
                    frappe.db.set_value("Insight Bank Slip", existing.name, {
                        "direction": data["direction"],
                        "direction_basis": data.get("direction_basis"),
                    })
            return {"slip": existing.name, "data": data, "method": method,
                    "confidence": confidence, "duplicate": True,
                    "payment_entry": existing.payment_entry, "status": existing.status}

    slip = _slip_from_data(data, company=company, file_url=file_url,
                           raw_text=text, method=method, confidence=confidence)
    return {"slip": slip.name, "data": data, "method": method,
            "confidence": confidence, "total_amount": slip.total_amount}


def _resolve_bank_account(slip) -> str | None:
    """Match the slip's source account to an ERPNext Bank Account record."""
    acc = (slip.source_account or "").strip()
    if not acc:
        return None
    for field in ("iban", "bank_account_no"):
        hit = frappe.db.get_value("Bank Account", {field: acc}, "name")
        if hit:
            return hit
    return None


def _resolve_supplier(name: str | None) -> str | None:
    if not name:
        return None
    return frappe.db.get_value("Supplier", {"supplier_name": name}, "name") \
        or frappe.db.get_value("Supplier", {"name": name}, "name")


@frappe.whitelist()
def stage_draft_payment_entry(slip: str) -> dict:
    """Create a DRAFT (unsubmitted) Payment Entry from a staged slip, writing the
    bank reference so reconciliation can later auto-match it. Never submits.

    v2.77.0 — guarded. The Payment Entry inserts with ignore_permissions=False
    so Frappe checks that one, but this also mutates the Insight Bank Slip via
    `db_set`, which checks nothing.
    """
    _require_write("Payment Entry")
    _require_write("Insight Bank Slip")
    s = frappe.get_doc("Insight Bank Slip", slip)
    if s.payment_entry:
        return {"payment_entry": s.payment_entry, "already": True}

    pe = frappe.new_doc("Payment Entry")
    pe.company = s.company
    pe.payment_type = "Pay" if (s.direction or "Outgoing") == "Outgoing" else "Receive"
    pe.posting_date = s.value_date or frappe.utils.today()
    pe.paid_amount = flt(s.amount)
    pe.received_amount = flt(s.amount)
    pe.reference_no = s.bank_reference or s.transaction_id or s.name
    pe.reference_date = s.value_date or frappe.utils.today()
    pe.remarks = (s.purpose or "") + (f" · {s.counterparty_name}" if s.counterparty_name else "")

    bank_account = s.source_account_link or _resolve_bank_account(s)
    if bank_account:
        pe.bank_account = bank_account
        gl = frappe.db.get_value("Bank Account", bank_account, "account")
        if gl:
            # Pay -> money leaves the bank (paid_from); Receive -> into bank (paid_to).
            if pe.payment_type == "Pay":
                pe.paid_from = gl
            else:
                pe.paid_to = gl
    # The account the user chose to book against (expense/income), used for the
    # other leg when there's no party.
    if s.suggested_account:
        if pe.payment_type == "Pay":
            pe.paid_to = s.suggested_account
        else:
            pe.paid_from = s.suggested_account
    # User's explicit party choice (from the review dropdown) wins; fall back to
    # a name match only as a pre-selected suggestion.
    if s.party_type and s.party:
        pe.party_type = s.party_type
        pe.party = s.party
    else:
        supplier = _resolve_supplier(s.counterparty_name)
        if supplier and pe.payment_type == "Pay":
            pe.party_type = "Supplier"
            pe.party = supplier

    # Exchange rates — ERPNext makes source_exchange_rate mandatory and this
    # check runs even with ignore_mandatory. For a same-currency payment in a
    # same-currency company the rate is 1.0; otherwise use the slip's own rate
    # or look one up.
    company_currency = frappe.get_cached_value("Company", s.company, "default_currency")
    slip_currency = s.currency or company_currency
    pe.paid_from_account_currency = slip_currency
    pe.paid_to_account_currency = slip_currency
    rate = 1.0
    if slip_currency and company_currency and slip_currency != company_currency:
        if flt(s.conversion_rate):
            rate = flt(s.conversion_rate)
        else:
            try:
                from erpnext.setup.utils import get_exchange_rate
                rate = flt(get_exchange_rate(slip_currency, company_currency, pe.posting_date)) or 1.0
            except Exception:
                rate = 1.0
    pe.source_exchange_rate = rate
    pe.target_exchange_rate = rate

    # Insert as a DRAFT for human completion/approval — do NOT submit.
    pe.flags.ignore_mandatory = True
    pe.insert(ignore_permissions=False)

    s.db_set("payment_entry", pe.name)
    s.db_set("status", "Posted")
    return {"payment_entry": pe.name, "draft": True,
            "needs": [f for f in ("party", "paid_from", "paid_to")
                      if not pe.get(f)]}


# ---- pickers (whitelisted): models + parties --------------------------------

@frappe.whitelist()
def list_ollama_models() -> dict:
    """Return the models actually pulled on the configured Ollama host, so the
    UI can show a dropdown instead of a typed guess."""
    import requests
    cfg = _ollama_config()
    try:
        r = requests.get(f"{cfg['url']}/api/tags", timeout=10)
        r.raise_for_status()
        models = []
        for m in r.json().get("models", []):
            det = m.get("details", {}) or {}
            models.append({
                "name": m.get("name"),
                "size_gb": round((m.get("size") or 0) / 1e9, 2),
                "family": det.get("family"),
                "parameter_size": det.get("parameter_size"),
                "modified": m.get("modified_at"),
            })
        models.sort(key=lambda x: x["name"] or "")
        return {"ok": True, "url": cfg["url"], "models": models,
                "selected": {"text": cfg["text_model"], "vision": cfg["vision_model"]}}
    except Exception as e:
        return {"ok": False, "url": cfg["url"], "models": [], "error": str(e)}


_PARTY_FIELDS = {
    "Supplier": ["name", "supplier_name", "supplier_group", "supplier_type",
                 "tax_id", "country", "default_currency", "default_price_list",
                 "mobile_no", "email_id", "disabled"],
    "Customer": ["name", "customer_name", "customer_group", "customer_type",
                 "tax_id", "territory", "default_currency", "default_price_list",
                 "mobile_no", "email_id", "disabled"],
}


@frappe.whitelist()
def search_parties(party_type: str, txt: str = "", limit: int = 20) -> list[dict]:
    """Rich supplier/customer search for the review dropdown — returns the full
    identifying detail (name, tax id, group, currency, contact) so the user can
    pick the right party with confidence rather than relying on a name match."""
    _require_read()
    if party_type not in _PARTY_FIELDS:
        frappe.throw("party_type must be 'Supplier' or 'Customer'.")
    fields = _PARTY_FIELDS[party_type]
    namefield = "supplier_name" if party_type == "Supplier" else "customer_name"
    filters = []
    or_filters = []
    if txt:
        like = f"%{txt}%"
        or_filters = [["name", "like", like], [namefield, "like", like],
                      ["tax_id", "like", like]]
    rows = frappe.get_list(
        party_type, fields=fields, filters=filters, or_filters=or_filters,
        limit_page_length=int(limit), order_by="modified desc",
    )
    return rows


@frappe.whitelist()
def set_slip_party(slip: str, party_type: str, party: str) -> dict:
    """Persist the user's party choice on the staged slip before posting."""
    _require_read()
    s = frappe.get_doc("Insight Bank Slip", slip)
    s.party_type = party_type
    s.party = party
    s.save(ignore_permissions=False)
    return {"ok": True, "party_type": party_type, "party": party}


# ---- embed in Payment Entry / Journal Entry forms --------------------------

def _map_to_payment_entry(data: dict) -> dict:
    direction = data.get("direction") or "Outgoing"
    remarks = (data.get("purpose") or "")
    if data.get("counterparty_name"):
        remarks = (remarks + f" · {data['counterparty_name']}").strip(" ·")
    return {
        "payment_type": "Pay" if direction == "Outgoing" else "Receive",
        "posting_date": data.get("value_date"),
        "reference_no": data.get("bank_reference") or data.get("transaction_id"),
        "reference_date": data.get("value_date"),
        "paid_amount": flt(data.get("amount")) or None,
        "received_amount": flt(data.get("amount")) or None,
        "remarks": remarks or None,
    }


def _map_to_journal_entry(data: dict) -> dict:
    remarks = (data.get("purpose") or "")
    if data.get("counterparty_name"):
        remarks = (remarks + f" · {data['counterparty_name']}").strip(" ·")
    return {
        "posting_date": data.get("value_date"),
        "cheque_no": data.get("bank_reference") or data.get("transaction_id"),
        "cheque_date": data.get("value_date"),
        "user_remark": remarks or None,
        "total_amount_hint": flt(data.get("amount")) + flt(data.get("fee")) + flt(data.get("vat")),
    }


@frappe.whitelist()
def read_slip_into(file_url: str, target_doctype: str | None = None,
                   company: str | None = None) -> dict:
    """Read a slip and return the field map for the document the user is in
    (Payment Entry or Journal Entry). Also stages an Insight Bank Slip for the
    audit trail and the reconciliation reference. The form's client script
    applies the returned `field_map`; the user reviews and submits."""
    _require_read()
    res = read_slip(file_url=file_url, company=company)
    data = res["data"]
    if target_doctype == "Journal Entry":
        field_map = _map_to_journal_entry(data)
    else:
        field_map = _map_to_payment_entry(data)
    return {
        "slip": res["slip"],
        "data": data,
        "field_map": {k: v for k, v in field_map.items() if v not in (None, "")},
        "method": res["method"],
        "confidence": res["confidence"],
        "counterparty_name": data.get("counterparty_name"),
        "counterparty_account": data.get("counterparty_account"),
        "suggested_party_search": data.get("counterparty_name"),
    }


@frappe.whitelist()
def list_slips(limit: int = 25, status: str | None = None) -> list[dict]:
    """Recent staged slips for the Insight Bank tab."""
    _require_read()
    filters = {"status": status} if status else {}
    return frappe.get_list(
        "Insight Bank Slip",
        fields=["name", "bank", "direction", "amount", "currency", "total_amount",
                "status", "value_date", "counterparty_name", "bank_reference",
                "payment_entry", "extraction_confidence", "extraction_method",
                "party_type", "party"],
        filters=filters, order_by="creation desc", limit_page_length=int(limit),
    )


# ---- account pickers (whitelisted) -----------------------------------------

@frappe.whitelist()
def search_accounts(company: str, txt: str = "", root_type: str | None = None,
                    limit: int = 20) -> list[dict]:
    """Leaf GL accounts for the 'book against' dropdown. The user reads the slip
    description and picks the expense/income account."""
    _require_read()
    filters = {"company": company, "is_group": 0}
    if root_type:
        filters["root_type"] = root_type
    or_filters = None
    if txt:
        like = f"%{txt}%"
        or_filters = [["name", "like", like], ["account_name", "like", like],
                      ["account_number", "like", like]]
    return frappe.get_list(
        "Account",
        fields=["name", "account_name", "account_number", "root_type",
                "account_type", "account_currency"],
        filters=filters, or_filters=or_filters,
        limit_page_length=int(limit), order_by="account_number, account_name")


@frappe.whitelist()
def search_bank_accounts(company: str = "", txt: str = "", limit: int = 20) -> list[dict]:
    """Bank Account records (with their GL account) for the source/paid-from
    dropdown. Company optional (statements don't carry one)."""
    _require_read()
    filters = {"company": company} if company else {}
    or_filters = None
    if txt:
        like = f"%{txt}%"
        or_filters = [["name", "like", like], ["account_name", "like", like],
                      ["bank", "like", like], ["iban", "like", like],
                      ["bank_account_no", "like", like]]
    return frappe.get_list(
        "Bank Account",
        fields=["name", "account_name", "bank", "account", "iban",
                "bank_account_no", "company", "is_company_account"],
        filters=filters, or_filters=or_filters,
        limit_page_length=int(limit), order_by="modified desc")


@frappe.whitelist()
def set_slip_accounts(slip: str, bank_account: str | None = None,
                      account: str | None = None) -> dict:
    """Persist the user's account choices on the slip before staging."""
    _require_read()
    s = frappe.get_doc("Insight Bank Slip", slip)
    if bank_account is not None:
        s.source_account_link = bank_account or None
    if account is not None:
        s.suggested_account = account or None
    s.save(ignore_permissions=False)
    return {"ok": True, "source_account_link": s.source_account_link,
            "suggested_account": s.suggested_account}


@frappe.whitelist()
def get_slip(slip: str) -> dict:
    """Full slip detail for the review panel (incl. description + raw text)."""
    _require_read()
    s = frappe.get_doc("Insight Bank Slip", slip)
    return {
        "name": s.name, "company": s.company, "bank": s.bank, "direction": s.direction,
        "amount": s.amount, "currency": s.currency, "fee": s.fee, "vat": s.vat,
        "total_amount": s.total_amount, "status": s.status,
        "value_date": str(s.value_date or ""), "counterparty_name": s.counterparty_name,
        "counterparty_account": s.counterparty_account, "bank_reference": s.bank_reference,
        "transaction_id": s.transaction_id, "purpose": s.purpose,
        "description": s.description, "raw_text": s.raw_text,
        "extraction_method": s.extraction_method, "extraction_confidence": s.extraction_confidence,
        "party_type": s.party_type, "party": s.party,
        "direction_basis": s.direction_basis,
        "source_account": s.source_account, "source_account_link": s.source_account_link,
        "suggested_account": s.suggested_account, "payment_entry": s.payment_entry,
    }


@frappe.whitelist()
def set_slip_direction(slip: str, direction: str) -> dict:
    """Manual inward/outward correction (also useful when the company couldn't be
    auto-identified)."""
    _require_read()
    if direction not in ("Incoming", "Outgoing"):
        frappe.throw("direction must be 'Incoming' or 'Outgoing'.")
    s = frappe.get_doc("Insight Bank Slip", slip)
    s.direction = direction
    s.direction_basis = "Set manually by user."
    s.save(ignore_permissions=False)
    return {"ok": True, "direction": direction}
