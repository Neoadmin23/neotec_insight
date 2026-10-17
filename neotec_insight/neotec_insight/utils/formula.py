from __future__ import annotations

import ast
import operator

import frappe

BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
}

UNARY_OPS = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}

COMPARE_OPS = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
}

SUPPORTED_FUNCTIONS = {"IF", "ROUND", "ABS", "MIN", "MAX", "SUM"}


def evaluate_row_formula(formula: str, values: dict[str, float]) -> float:
    tree = _parse(formula)
    out = float(_eval(tree.body, values))
    if out != out or out in (float("inf"), float("-inf")):
        raise FormulaError("result is not a finite number")
    return out


def validate_formula_expression(formula: str, available_row_keys: set[str], *, row_key: str) -> None:
    tree = _parse(formula)
    _validate(tree.body, available_row_keys, row_key=row_key)


def _parse(formula: str) -> ast.Expression:
    try:
        return ast.parse(formula, mode="eval")
    except SyntaxError as exc:
        raise frappe.ValidationError(f"Invalid formula syntax: {formula}") from exc


def _eval(node, values):
    if isinstance(node, ast.BinOp):
        op = BIN_OPS.get(type(node.op))
        if not op:
            _err(node)
        left, right = _eval(node.left, values), _eval(node.right, values)
        if isinstance(node.op, ast.Div) and right == 0:
            raise FormulaError("division by zero")
        return op(left, right)
    if isinstance(node, ast.UnaryOp):
        op = UNARY_OPS.get(type(node.op))
        if not op:
            _err(node)
        return op(_eval(node.operand, values))
    if isinstance(node, ast.Name):
        return float(values.get(node.id, 0))
    if isinstance(node, ast.Compare):
        left = _eval(node.left, values)
        for op, comparator in zip(node.ops, node.comparators):
            op_fn = COMPARE_OPS.get(type(op))
            if not op_fn:
                _err(node)
            right = _eval(comparator, values)
            if not op_fn(left, right):
                return 0.0
            left = right
        return 1.0
    if isinstance(node, ast.Call):
        return float(_eval_call(node, values))
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)
    _err(node)


class FormulaError(ArithmeticError):
    """A formula that is valid but cannot be computed for these values
    (division by zero, non-finite result). Raised so the caller can report it
    instead of passing it off as a genuine zero."""


ARITY = {"IF": (3, 3), "ROUND": (1, 2), "ABS": (1, 1), "MIN": (1, None), "MAX": (1, None),
         "SUM": (0, None)}


def _eval_call(node, values):
    if not isinstance(node.func, ast.Name) or node.keywords:
        _err(node)
    fn = node.func.id.upper()
    if fn == "IF":
        # v2.90.0 — lazy, like a spreadsheet: only the chosen branch is
        # evaluated. IF(x<>0, y/x, 0) — the standard division guard — used to
        # evaluate y/x anyway, fail, and come out as 0 for the whole row.
        if len(node.args) != 3:
            _err(node)
        cond = _eval(node.args[0], values)
        return _eval(node.args[1] if bool(cond) else node.args[2], values)
    args = [_eval(a, values) for a in node.args]
    if fn == "ROUND":
        if len(args) not in {1, 2}:
            _err(node)
        precision = int(args[1]) if len(args) == 2 else 0
        return round(args[0], precision)
    if fn == "ABS":
        if len(args) != 1:
            _err(node)
        return abs(args[0])
    if fn == "MIN":
        if not args:
            _err(node)
        return min(args)
    if fn == "MAX":
        if not args:
            _err(node)
        return max(args)
    if fn == "SUM":
        return sum(args)
    _err(node)


def _validate(node, keys, *, row_key):
    if isinstance(node, ast.BinOp):
        if type(node.op) not in BIN_OPS:
            _err(node)
        _validate(node.left, keys, row_key=row_key)
        _validate(node.right, keys, row_key=row_key)
        return
    if isinstance(node, ast.UnaryOp):
        if type(node.op) not in UNARY_OPS:
            _err(node)
        _validate(node.operand, keys, row_key=row_key)
        return
    if isinstance(node, ast.Name):
        if node.id not in keys:
            raise frappe.ValidationError(
                f"Formula row '{row_key}' references unknown row key '{node.id}'."
            )
        return
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return
    if isinstance(node, ast.Compare):
        if any(type(op) not in COMPARE_OPS for op in node.ops):
            _err(node)
        _validate(node.left, keys, row_key=row_key)
        for c in node.comparators:
            _validate(c, keys, row_key=row_key)
        return
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
            and node.func.id.upper() in SUPPORTED_FUNCTIONS:
        fn = node.func.id.upper()
        lo, hi = ARITY[fn]
        n = len(node.args)
        if node.keywords or n < lo or (hi is not None and n > hi):
            want = f"{lo}" if lo == hi else (f"{lo} or more" if hi is None else f"{lo}–{hi}")
            raise frappe.ValidationError(
                f"Formula row '{row_key}': {fn} takes {want} argument(s), got {n}"
                + (" and keyword arguments are not allowed" if node.keywords else "") + ".")
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id.upper() not in SUPPORTED_FUNCTIONS:
            raise frappe.ValidationError(
                f"Formula row '{row_key}' uses unsupported function."
            )
        for a in node.args:
            _validate(a, keys, row_key=row_key)
        return
    _err(node)


def _err(node):
    raise frappe.ValidationError(f"Unsupported formula expression: {ast.dump(node)}")
