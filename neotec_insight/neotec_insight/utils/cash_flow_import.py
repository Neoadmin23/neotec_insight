"""Cash Flow Forecast — historical import engine.

Same isolation boundary as the rest of Cash Flow Forecast — no import from
utils/map_importer.py or the P&L engine, even though the shape of this
feature (upload an .xlsx, parse with openpyxl, apply to app data) matches
that existing feature's own established pattern. Following the pattern is
not the same as sharing the code.

Two pure concerns, both testable without a live site:
  1. parse_classified_history_sheet — read the "Data" sheet shape a real
     customer's manual process produces (Month/Posting Date/Account/
     Transaction Type/Class/New Class/Debit/Credit/Balance/Voucher Type/
     Voucher No/Against Account/Project/Cost Center/Remarks) into plain rows.
  2. match_lines_to_rows — resolve each row's "New Class" label to a real
     Insight Cash Flow Line, or report it as unmatched so the user knows
     exactly which categories still need a Line created before import can
     claim them.
"""

from __future__ import annotations

import io

import openpyxl


# Column headers this parser recognizes, matched case-insensitively and
# stripped — not fixed column positions, since a real customer's workbook
# (verified against the one this feature was built from) has trailing
# spaces on several headers ("Transaction Type ", "Class ") and there's no
# reason to assume every future export keeps columns in exactly the same
# order.
_HEADER_ALIASES = {
    "posting_date": {"posting date"},
    "account": {"account"},
    "transaction_type": {"transaction type"},
    "class": {"class"},
    "new_class": {"new class"},
    "debit": {"debit (sar)", "debit"},
    "credit": {"credit (sar)", "credit"},
    "voucher_type": {"voucher type"},
    "voucher_no": {"voucher no", "voucher no."},
    "against_account": {"against account"},
    "project": {"project"},
    "cost_center": {"cost center", "cost centre"},
    "remarks": {"remarks"},
}


def _normalize_header(h) -> str:
    return str(h or "").strip().lower()


def _find_header_row(ws, max_scan_rows: int = 10) -> tuple[int, dict[str, int]] | None:
    """Scans the first `max_scan_rows` rows for one that contains BOTH a
    'voucher no' and a 'new class' column — the two fields this import
    cannot function without — rather than assuming a fixed row number.
    Returns (row_number, {field_key: column_index}) or None if not found."""
    for row_idx in range(1, max_scan_rows + 1):
        col_map: dict[str, int] = {}
        for col_idx in range(1, ws.max_column + 1):
            header = _normalize_header(ws.cell(row=row_idx, column=col_idx).value)
            if not header:
                continue
            for field, aliases in _HEADER_ALIASES.items():
                if header in aliases and field not in col_map:
                    col_map[field] = col_idx
        if "voucher_no" in col_map and "new_class" in col_map:
            return row_idx, col_map
    return None


def parse_classified_history_sheet(file_bytes: bytes, sheet_name: str | None = None) -> dict:
    """Returns {rows, header_row, columns_found, sheet_used, warnings}.
    `rows`: [{new_class, voucher_type, voucher_no, remarks, debit, credit,
    against_account, cost_center, project}, ...] — only rows with both a
    voucher_no and a new_class are kept; anything else is silently not a
    classified transaction row (could be a blank line, a totals row, etc.)
    and isn't reported as an error."""
    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
    warnings: list[str] = []

    if sheet_name and sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        sheet_used = sheet_name
    else:
        ws = wb.worksheets[0]
        sheet_used = ws.title
        if sheet_name:
            warnings.append(f"Sheet '{sheet_name}' not found — used '{sheet_used}' instead.")

    found = _find_header_row(ws)
    if not found:
        return {"rows": [], "header_row": None, "columns_found": {}, "sheet_used": sheet_used,
                "warnings": warnings + ["Could not find a header row with both "
                                       "'Voucher No' and 'New Class' columns."]}
    header_row, col_map = found

    missing = [f for f in ("remarks", "debit", "credit", "voucher_type") if f not in col_map]
    if missing:
        warnings.append(f"Columns not found, treated as blank: {', '.join(missing)}.")

    def _cell(row_idx, field):
        col = col_map.get(field)
        return ws.cell(row=row_idx, column=col).value if col else None

    rows = []
    for r in range(header_row + 1, ws.max_row + 1):
        new_class = _cell(r, "new_class")
        voucher_no = _cell(r, "voucher_no")
        if not new_class or not voucher_no:
            continue
        rows.append({
            "new_class": str(new_class).strip(),
            "voucher_type": str(_cell(r, "voucher_type") or "").strip(),
            "voucher_no": str(voucher_no).strip(),
            "remarks": str(_cell(r, "remarks") or "").strip(),
            "debit": _cell(r, "debit") or 0,
            "credit": _cell(r, "credit") or 0,
            "against_account": str(_cell(r, "against_account") or "").strip(),
            "cost_center": str(_cell(r, "cost_center") or "").strip(),
            "project": str(_cell(r, "project") or "").strip(),
        })

    return {"rows": rows, "header_row": header_row, "columns_found": col_map,
            "sheet_used": sheet_used, "warnings": warnings}


def match_lines_to_rows(rows: list[dict], available_lines: list[dict]) -> dict:
    """Pure. available_lines: [{name, label}] — the app's real Insight Cash
    Flow Line records. Matches each row's new_class (normalized: stripped,
    lowercased) against a line's label the same way. A "New Class" label
    with no matching Line is NOT silently dropped — it's reported by name
    and count, so the user knows exactly which categories need a Line
    created before those rows can be imported, the same "never silently
    absorbed" discipline as the reconciliation residual."""
    label_to_name = {(l["label"] or "").strip().lower(): l["name"] for l in available_lines}

    matched = []
    unmatched_counts: dict[str, int] = {}
    for row in rows:
        key = row["new_class"].strip().lower()
        line_name = label_to_name.get(key)
        if line_name:
            matched.append({**row, "line": line_name})
        else:
            unmatched_counts[row["new_class"]] = unmatched_counts.get(row["new_class"], 0) + 1

    return {
        "matched": matched,
        "unmatched_labels": dict(sorted(unmatched_counts.items(), key=lambda kv: -kv[1])),
        "matched_count": len(matched),
        "unmatched_count": sum(unmatched_counts.values()),
    }
