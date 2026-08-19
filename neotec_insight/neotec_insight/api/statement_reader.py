# Copyright (c) 2026, Neotec and contributors
# -----------------------------------------------------------------------------
# Bank / merchant statement reader — Milestone 2 (parser + import).
#
# Parses a statement (CSV first-class; PDF best-effort) into normalized lines,
# summarises them into settlement batches, and creates ERPNext Bank Transactions
# at the batch level (the unit that actually hits the bank account). The
# reconciliation matcher keys off the batch reference + net amount.
# -----------------------------------------------------------------------------
from __future__ import annotations
import csv
import io
import re
import unicodedata
from collections import defaultdict
from typing import Any

import frappe
from frappe import _
from frappe.utils import flt, getdate

# Canonical column -> the header variants seen across exports.
_COLMAP = {
    "txn_date": ["transaction date"],
    "txn_time": ["transaction time"],
    "card_number": ["card number"],
    "amount": ["transaction amount"],
    "fees": ["fees"],
    "vat": ["vat amount"],
    "net_amount": ["net transaction amount"],
    "batch_total": ["batch total amount"],
    "txn_type": ["transaction type"],
    "auth_code": ["authorization code", "authorisation code"],
    "rrn": ["rrn"],
    "card_scheme": ["card scheme"],
    "posting_date": ["posting date dd/mm/yyyy", "posting date"],
    "channel": ["channel"],
    "account_number": ["account number"],
    "terminal_id": ["terminal id"],
    "merchant_id": ["merchant id"],
    "merchant_name": ["merchant name"],
    "batch_reference": ["batch reference number", "batch reference"],
}


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


def _num(v: Any) -> float:
    if v in (None, ""):
        return 0.0
    m = re.search(r"-?\d[\d,]*\.?\d*", str(v))
    if not m:
        return 0.0
    return flt(m.group(0).replace(",", ""))


def _date(v: str | None) -> str | None:
    if not v:
        return None
    v = str(v).strip().split()[0]
    m = re.match(r"(\d{1,4})[/-](\d{1,2})[/-](\d{1,4})", v)
    if not m:
        return None
    a, b, c = m.groups()
    if len(a) == 4:
        return f"{a}-{int(b):02d}-{int(c):02d}"          # YYYY-MM-DD
    return f"{c}-{int(b):02d}-{int(a):02d}"               # DD/MM/YYYY -> ISO


def _resolve_headers(header_row: list[str]) -> dict[int, str]:
    """Map column index -> canonical field using _COLMAP (case/space tolerant)."""
    idx = {}
    norm = [(i, (h or "").strip().lower()) for i, h in enumerate(header_row)]
    for canon, variants in _COLMAP.items():
        for i, h in norm:
            if h in variants:
                idx[i] = canon
                break
    return idx


def parse_csv_text(text: str) -> list[dict]:
    reader = csv.reader(io.StringIO(text))
    rows = [r for r in reader if any((c or "").strip() for c in r)]
    if not rows:
        return []
    headers = _resolve_headers(rows[0])
    out = []
    for r in rows[1:]:
        line = {canon: (r[i] if i < len(r) else "") for i, canon in headers.items()}
        if not (line.get("amount") or line.get("net_amount")):
            continue
        out.append(_normalize_line(line))
    return out


def _normalize_line(line: dict) -> dict:
    return {
        "txn_date": _date(line.get("txn_date")),
        "txn_time": (line.get("txn_time") or "").strip(),
        "card_number": (line.get("card_number") or "").strip(),
        "amount": _num(line.get("amount")),
        "fees": abs(_num(line.get("fees"))),
        "vat": abs(_num(line.get("vat"))),
        "net_amount": _num(line.get("net_amount")),
        "batch_total": _num(line.get("batch_total")),
        "txn_type": (line.get("txn_type") or "").strip(),
        "auth_code": (line.get("auth_code") or "").strip(),
        "rrn": (line.get("rrn") or "").strip(),
        "card_scheme": (line.get("card_scheme") or "").strip(),
        "posting_date": _date(line.get("posting_date")),
        "channel": (line.get("channel") or "").strip(),
        "account_number": (line.get("account_number") or "").strip(),
        "terminal_id": (line.get("terminal_id") or "").strip(),
        "merchant_id": (line.get("merchant_id") or "").strip(),
        "merchant_name": (line.get("merchant_name") or "").strip(),
        "batch_reference": (line.get("batch_reference") or "").strip(),
    }


def summarize_batches(lines: list[dict]) -> list[dict]:
    """Group lines into settlement batches — the deposit unit that hits the bank.
    Net deposit = sum of net amounts (gross - fees - VAT)."""
    groups: dict[tuple, dict] = {}
    order = []
    for ln in lines:
        key = (ln["batch_reference"], ln["posting_date"], ln["account_number"])
        g = groups.get(key)
        if not g:
            g = {
                "batch_reference": ln["batch_reference"],
                "posting_date": ln["posting_date"],
                "account_number": ln["account_number"],
                "terminal_id": ln["terminal_id"],
                "schemes": set(), "count": 0,
                "gross": 0.0, "fees": 0.0, "vat": 0.0, "net": 0.0,
            }
            groups[key] = g
            order.append(key)
        g["count"] += 1
        g["gross"] += ln["amount"]
        g["fees"] += ln["fees"]
        g["vat"] += ln["vat"]
        g["net"] += ln["net_amount"]
        if ln["card_scheme"]:
            g["schemes"].add(ln["card_scheme"])
    result = []
    for key in order:
        g = groups[key]
        g["schemes"] = ", ".join(sorted(g["schemes"]))
        for k in ("gross", "fees", "vat", "net"):
            g[k] = round(g[k], 2)
        result.append(g)
    return result


def _parse_rows(rows: list[list]) -> list[dict]:
    rows = [r for r in rows if any((str(c) if c is not None else "").strip() for c in r)]
    if not rows:
        return []
    headers = _resolve_headers([str(c) if c is not None else "" for c in rows[0]])
    out = []
    for r in rows[1:]:
        line = {canon: (r[i] if i < len(r) else "") for i, canon in headers.items()}
        if not (line.get("amount") or line.get("net_amount")):
            continue
        out.append(_normalize_line(line))
    return out


def _rows_from_path(path: str) -> list[list]:
    lower = (path or "").lower()
    if lower.endswith((".csv", ".tsv")):
        with open(path, encoding="utf-8-sig", errors="replace") as f:
            return [r for r in csv.reader(f)]
    if lower.endswith((".xlsx", ".xls", ".xlsm")):
        import openpyxl
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        rows = []
        for ws in wb.worksheets:
            for row in ws.iter_rows(values_only=True):
                rows.append(list(row))
        return rows
    if lower.endswith(".pdf"):
        frappe.throw("Please export the statement as CSV or Excel — PDF statement "
                     "parsing isn't reliable across bank layouts.")
    frappe.throw("Unsupported statement file type. Use CSV or Excel.")


def _locate_table(rows: list[list]) -> tuple[str | None, int]:
    """Find the real header row (statements have title/summary preamble above it).
    Returns (format, header_index) or (None, -1)."""
    best_fmt, best_score, best_idx = None, 0, -1
    for i, row in enumerate(rows[:60]):
        acc = len(_resolve_headers_for(row, _COLMAP_ACCOUNT))
        mer = len(_resolve_headers_for(row, _COLMAP))
        if acc >= 3 and acc > best_score:
            best_fmt, best_score, best_idx = "account", acc, i
        if mer >= 4 and mer > best_score:
            best_fmt, best_score, best_idx = "merchant", mer, i
    return best_fmt, best_idx


def _account_no_from_preamble(rows: list[list], header_idx: int) -> str | None:
    for row in rows[:header_idx]:
        cells = [str(c).strip() if c is not None else "" for c in row]
        for j, c in enumerate(cells):
            if c.lower().replace(" ", "") in ("accountnumber", "accountno", "account#"):
                for v in cells[j + 1:]:
                    if re.fullmatch(r"\d{6,}", v):
                        return v
    return None


def _read_statement_any(path: str) -> tuple[str, list[dict], str | None]:
    """Return (format, normalized_lines, account_number)."""
    raw = _rows_from_path(path)
    fmt, hidx = _locate_table(raw)
    if not fmt:
        # fall back to treating row 0 as header (clean exports with no preamble)
        if raw:
            fmt = _detect_format(raw[0])
            hidx = 0
        else:
            return "account", [], None
    header = raw[hidx]
    body = [r for r in raw[hidx + 1:] if any((str(c) if c is not None else "").strip() for c in r)]
    if fmt == "account":
        idx = _resolve_headers_for(header, _COLMAP_ACCOUNT)
        lines = []
        for r in body:
            line = {canon: (r[i] if i < len(r) else "") for i, canon in idx.items()}
            if not (_num(line.get("credit")) or _num(line.get("debit"))):
                continue
            lines.append(_normalize_account_line(line))
        return "account", lines, _account_no_from_preamble(raw, hidx)
    idx = _resolve_headers_for(header, _COLMAP)
    lines = []
    for r in body:
        line = {canon: (r[i] if i < len(r) else "") for i, canon in idx.items()}
        if not (line.get("amount") or line.get("net_amount")):
            continue
        lines.append(_normalize_line(line))
    acct = lines[0]["account_number"] if lines else None
    return "merchant", lines, acct


def _read_statement(path: str) -> list[dict]:
    # Back-compat: merchant lines only.
    return _read_statement_any(path)[1]


def _file_path(file_url: str) -> str:
    return frappe.get_doc("File", {"file_url": file_url}).get_full_path()


@frappe.whitelist()
def preview_statement(file_url: str) -> dict:
    """Parse a statement and return a review summary — no Bank Transactions are
    created. Handles both merchant settlement and current-account statements."""
    _require_read()
    fmt, lines, acct_no = _read_statement_any(_file_path(file_url))
    if fmt == "account":
        summary = summarize_account(lines)
        matches = []
        if acct_no:
            matches = frappe.get_list(
                "Bank Account", filters={"bank_account_no": acct_no},
                fields=["name", "account_name", "bank", "company", "account"],
                limit_page_length=5)
        summary.update({
            "format": "account",
            "account_number": acct_no,
            "lines": lines[:200],
            "matched_bank_accounts": matches,
        })
        return summary
    batches = summarize_batches(lines)
    if acct_no is None:
        acct_no = lines[0]["account_number"] if lines else None
    matches = []
    if acct_no:
        matches = frappe.get_list(
            "Bank Account", filters={"bank_account_no": acct_no},
            fields=["name", "account_name", "bank", "company", "account"],
            limit_page_length=5)
    return {
        "format": "merchant",
        "line_count": len(lines),
        "batch_count": len(batches),
        "total_net": round(sum(b["net"] for b in batches), 2),
        "total_gross": round(sum(b["gross"] for b in batches), 2),
        "total_fees": round(sum(b["fees"] for b in batches), 2),
        "total_vat": round(sum(b["vat"] for b in batches), 2),
        "account_number": acct_no,
        "matched_bank_accounts": matches,
        "batches": batches,
        "lines": lines[:200],
    }


@frappe.whitelist()
def import_statement(file_url: str, bank_account: str, level: str = "batch") -> dict:
    """Create ERPNext Bank Transactions from a statement.

    level='batch' (default): one Bank Transaction per settlement batch, deposit =
    net amount (the figure that actually hits the bank). level='transaction':
    one per card transaction. Skips any whose reference already exists for this
    bank account (idempotent re-import)."""
    _require_read()
    if not bank_account:
        frappe.throw("bank_account is required.")
    fmt, lines, _acct = _read_statement_any(_file_path(file_url))
    company = frappe.db.get_value("Bank Account", bank_account, "company")
    created, skipped = [], 0

    def _make(date, deposit, withdrawal, ref, description):
        if ref and frappe.db.exists("Bank Transaction",
                                    {"bank_account": bank_account, "reference_number": ref}):
            return None
        bt = frappe.new_doc("Bank Transaction")
        bt.date = date
        bt.bank_account = bank_account
        bt.company = company
        bt.deposit = flt(deposit)
        bt.withdrawal = flt(withdrawal)
        bt.reference_number = ref
        bt.description = description
        bt.flags.ignore_mandatory = True
        bt.insert(ignore_permissions=False)
        try:
            bt.submit()           # submitted transactions are what the matcher reconciles
        except Exception:
            frappe.log_error(title="statement_reader: Bank Transaction submit failed",
                             message=frappe.get_traceback())
        return bt.name

    if fmt == "account":
        # One Bank Transaction per statement line (deposit=credit, withdrawal=debit).
        for ln in lines:
            desc = f"{ln['txn_type']} · {ln['details'][:100]}"
            if ln.get("invoice_ref"):
                desc += f" · INVOICE {ln['invoice_ref']}"
            if ln.get("counterparty_iban"):
                desc += f" · {ln['counterparty_iban']}"
            name = _make(ln["value_date"], ln["credit"], ln["debit"],
                         ln["reference"] or None, desc[:1000])
            created.append(name) if name else (skipped := skipped + 1)
    elif level == "transaction":
        for ln in lines:
            name = _make(ln["posting_date"] or ln["txn_date"], ln["net_amount"], 0,
                         ln["rrn"], f"{ln['card_scheme']} {ln['txn_type']} {ln['card_number']}")
            created.append(name) if name else (skipped := skipped + 1)
    else:
        for b in summarize_batches(lines):
            desc = f"POS settlement · {b['schemes']} · {b['count']} txns · gross {b['gross']} fees {b['fees']} vat {b['vat']}"
            name = _make(b["posting_date"], b["net"], 0, b["batch_reference"] or None, desc)
            created.append(name) if name else (skipped := skipped + 1)

    created = [c for c in created if c]
    return {"created": created, "created_count": len(created), "skipped": skipped,
            "bank_account": bank_account, "level": ("transaction" if fmt == "account" else level),
            "format": fmt}


# ---- account statements (current-account) -----------------------------------
# Columns seen on a Riyad Bank statement: Value Date | Details | Reference |
# Cheque No | Transaction Type | Credit Amount | Debit Amount | Balance.
_COLMAP_ACCOUNT = {
    "value_date": ["value date", "date", "transaction date"],
    "details": ["details", "narration", "description"],
    "reference": ["reference", "ref", "reference number", "reference no"],
    "cheque_no": ["cheque no", "cheque", "cheque number"],
    "txn_type": ["transaction type", "type"],
    "credit": ["credit amount", "credit", "credit (sar)"],
    "debit": ["debit amount", "debit", "debit (sar)"],
    "balance": ["balance", "running balance"],
}

_FEE_RE = re.compile(r"FEE\s*[:]?\s*([\d,]+\.\d+)\s*SAR.{0,40}?VAT\s*AMOUNT\s*([\d,]+\.\d+)", re.I | re.S)
_INV_RE = re.compile(r"\bS?INV[\s/_-]*\d{4}[\s/_-]*\d{3,6}\b", re.I)
_IBAN_RE = re.compile(r"\bSA\d{22}\b")


def _detect_format(header_row: list) -> str:
    hl = [(str(h) if h is not None else "").strip().lower() for h in header_row]
    if any(("net transaction" in h) or ("batch" in h) for h in hl):
        return "merchant"
    if any("credit" in h for h in hl) and any("debit" in h for h in hl):
        return "account"
    if any("balance" in h for h in hl):
        return "account"
    return "merchant"


def _resolve_headers_for(header_row: list, colmap: dict) -> dict[int, str]:
    idx = {}
    norm = [(i, (str(h) if h is not None else "").strip().lower()) for i, h in enumerate(header_row)]
    for canon, variants in colmap.items():
        for i, h in norm:
            if h in variants and i not in idx:
                idx[i] = canon
                break
    return idx


def _clean_ref(raw: str) -> str:
    for tok in re.split(r"[\s\n]+", (raw or "").strip()):
        tok = tok.strip()
        if tok and tok.upper() != "REF" and re.fullmatch(r"[A-Za-z0-9]{6,}", tok):
            return tok
    return (raw or "").replace("REF", "").strip().split("\n")[0].strip()


def _normalize_account_line(line: dict) -> dict:
    credit = _num(line.get("credit"))
    debit = _num(line.get("debit"))
    direction = "Incoming" if credit > 0 else ("Outgoing" if debit > 0 else None)
    details = re.sub(r"\s*\n\s*", " ", (line.get("details") or "").strip())
    ndetails = unicodedata.normalize("NFKC", details)
    fee = vat = 0.0
    m = _FEE_RE.search(ndetails)
    if m:
        fee = _num(m.group(1))
        vat = _num(m.group(2))
    inv = _INV_RE.search(ndetails)
    iban = _IBAN_RE.search(ndetails)
    return {
        "value_date": _date(line.get("value_date")),
        "details": details[:500],
        "reference": _clean_ref(line.get("reference") or ""),
        "cheque_no": (line.get("cheque_no") or "").strip(),
        "txn_type": re.sub(r"\s*\n\s*", " ", (line.get("txn_type") or "").strip()),
        "credit": credit, "debit": debit,
        "amount": credit if credit > 0 else debit,
        "direction": direction,
        "balance": _num(line.get("balance")),
        "fee": fee, "vat": vat,
        "invoice_ref": inv.group(0) if inv else None,
        "counterparty_iban": iban.group(0) if iban else None,
    }


def _parse_account_rows(rows: list[list]) -> list[dict]:
    rows = [r for r in rows if any((str(c) if c is not None else "").strip() for c in r)]
    if not rows:
        return []
    headers = _resolve_headers_for(rows[0], _COLMAP_ACCOUNT)
    out = []
    for r in rows[1:]:
        line = {canon: (r[i] if i < len(r) else "") for i, canon in headers.items()}
        if not (_num(line.get("credit")) or _num(line.get("debit"))):
            continue
        out.append(_normalize_account_line(line))
    return out


def summarize_account(lines: list[dict]) -> dict:
    incoming = [l for l in lines if l["direction"] == "Incoming"]
    outgoing = [l for l in lines if l["direction"] == "Outgoing"]
    # Group by reference so a transfer and its separate fee line are paired.
    by_ref: dict[str, dict] = {}
    for l in lines:
        ref = l["reference"]
        if not ref:
            continue
        g = by_ref.setdefault(ref, {"reference": ref, "lines": 0, "fee": 0.0, "vat": 0.0})
        g["lines"] += 1
        g["fee"] += l["fee"]
        g["vat"] += l["vat"]
    multi = [g for g in by_ref.values() if g["lines"] > 1]
    # Validate parse against the running balance (each amount == balance delta).
    bal_ok = bal_bad = 0
    for i in range(len(lines) - 1):
        if lines[i]["balance"] and lines[i + 1]["balance"]:
            delta = round(lines[i]["balance"] - lines[i + 1]["balance"], 2)
            if abs(abs(delta) - lines[i]["amount"]) < 0.01:
                bal_ok += 1
            else:
                bal_bad += 1
    return {
        "line_count": len(lines),
        "incoming_count": len(incoming), "outgoing_count": len(outgoing),
        "total_incoming": round(sum(l["amount"] for l in incoming), 2),
        "total_outgoing": round(sum(l["amount"] for l in outgoing), 2),
        "total_fees": round(sum(l["fee"] for l in lines), 2),
        "total_vat": round(sum(l["vat"] for l in lines), 2),
        "with_invoice_ref": sum(1 for l in lines if l["invoice_ref"]),
        "multi_line_refs": len(multi),
        "balance_ok": bal_ok, "balance_mismatch": bal_bad,
    }
