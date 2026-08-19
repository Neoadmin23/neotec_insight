from __future__ import annotations

import frappe

from neotec_insight.neotec_insight.utils.formula import validate_formula_expression

# v2.62.1 — 'allocation' was added to the engine and the Rows editor but not
# here, so every save of a report containing one was rejected by validate().
ALLOWED_ROW_KINDS = {"section", "source", "formula", "allocation"}
ALLOWED_AGGREGATIONS = {"sum", "count", "avg"}
ALLOWED_SIGN_MODES = {"normal", "invert"}
ALLOWED_FILTER_OPS = {"=", "!=", ">", ">=", "<", "<=", "like", "in", "between"}
ALLOWED_COMPARISON_MODES = {"actuals_only", "vs_budget"}


def validate_report_definition_schema(definition: dict, report_type: str = "pnl") -> None:
    if not isinstance(definition, dict):
        _err("Definition JSON must be a JSON object.")

    # Trial Balance and Balance Sheet compute their rows at run time from the
    # chart of accounts — their stored definition legitimately has an empty
    # 'rows' array. Only the P&L report type requires defined rows.
    rows_optional = report_type in {"trial_balance", "balance_sheet", "pnl_statement"}

    rows = definition.get("rows")
    if not isinstance(rows, list):
        _err("Definition 'rows' must be an array.")
    if not rows:
        if rows_optional:
            return  # nothing further to validate for a balance-based report
        _err("Definition must contain a non-empty 'rows' array.")

    seen_keys: set[str] = set()
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            _err(f"Row {index} must be a JSON object.")
        kind = row.get("kind")
        if kind not in ALLOWED_ROW_KINDS:
            _err(f"Row {index} has invalid kind '{kind}'. Allowed: {sorted(ALLOWED_ROW_KINDS)}.")
        key = _require_str(row, "key", label=f"row {index}")
        _require_str(row, "label", label=f"row {index}")
        # A row key like 'alc_0yavc' is an internal id the user never chose and
        # cannot see in the editor. Errors quote the visible label and position
        # so the offending row can actually be found; the key stays for support.
        where = f"row {index} — “{row.get('label')}”"
        if key in seen_keys:
            _err(f"Duplicate row key '{key}'.")
        seen_keys.add(key)

        if kind == "source":
            accounts = row.get("accounts") or []
            if not isinstance(accounts, list):
                _err(f"Source {where} must have 'accounts' as a list. (key: {key})")
            for a in accounts:
                if not isinstance(a, str) or not a.strip():
                    _err(f"Source {where} has an invalid account entry. (key: {key})")
            sign = row.get("sign", "normal")
            if sign not in ALLOWED_SIGN_MODES:
                _err(f"Source {where} has invalid sign '{sign}'. (key: {key})")

        if kind == "formula":
            formula = _require_str(row, "formula", label=f"formula row '{key}'")
            validate_formula_expression(formula, available_row_keys=seen_keys, row_key=key)

        # show_when is valid on any kind (v2.76.0). Absent means "use the
        # per-kind default" — it is deliberately NOT defaulted here, so
        # execution.py stays the single place that decides.
        visible = row.get("show_when")
        if visible is not None and visible not in {"cost_center", "cost_center_exclude", "always"}:
            _err(f"{where} has invalid show_when '{visible}'. (key: {key})")

        if kind == "allocation":
            rule = row.get("allocation_rule")
            if not isinstance(rule, str) or not rule.strip():
                _err(f"Allocation {where} has no allocation rule selected. "
                     f"Pick one in the row editor, or delete the row. "
                     f"If the list is empty, no Insight Allocation Rule exists yet for this company — "
                     f"create one first, then add this row. (key: {key})")
            sign = row.get("sign", "normal")
            if sign not in ALLOWED_SIGN_MODES:
                _err(f"Allocation {where} has invalid sign '{sign}'. (key: {key})")

    comparison = definition.get("comparison")
    if comparison is not None:
        if not isinstance(comparison, dict):
            _err("comparison must be an object.")
        mode = comparison.get("mode", "vs_budget")
        if mode not in ALLOWED_COMPARISON_MODES:
            _err(f"comparison.mode must be one of {sorted(ALLOWED_COMPARISON_MODES)}.")
        py = comparison.get("prior_years", 1)
        if not isinstance(py, int) or py < 0 or py > 5:
            _err("comparison.prior_years must be an integer 0-5.")


def validate_column_schema(schema) -> None:
    if not isinstance(schema, list):
        _err("Column Schema must be an array.")


def validate_filter_schema(schema) -> None:
    if not isinstance(schema, list):
        _err("Filter Schema must be an array.")
    for index, fd in enumerate(schema, start=1):
        if not isinstance(fd, dict):
            _err(f"Filter {index} must be a JSON object.")
        _require_str(fd, "key", label=f"filter {index}")
        _require_str(fd, "label", label=f"filter {index}")
        op = fd.get("operator", "=")
        if op not in ALLOWED_FILTER_OPS:
            _err(f"Filter {index} has invalid operator '{op}'.")


def _require_str(obj, field, *, label):
    val = obj.get(field)
    if not isinstance(val, str) or not val.strip():
        _err(f"{label}: '{field}' is required and must be a non-empty string.")
    return val.strip()


def _err(msg):
    raise frappe.ValidationError(msg)
