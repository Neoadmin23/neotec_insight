"""Account Flag Mapping visibility — the gap behind the "blank consolidated
P&L" report.

v2.76.0's own CHANGELOG already diagnosed the real cause of that bug: "source
rows bound to no accounts sum to zero, which renders every row at 0.000 in
~10ms." That diagnosis was correct and the fix was never built — a row with
nothing bound was, and until this patch still was, indistinguishable on
screen from a row with genuine zero activity. This file is the regression
guard for the fix: `flag_binding_meta` must say, for every source row, either
"this resolves to N accounts" or "this resolves to nothing, and here is why."

Stubs a minimal `frappe` module rather than requiring a live site, following
the pattern in test_visibility.py and test_gtpl.py: the decision logic here
is what the frontend badge is keyed off, and it should be testable anywhere.
"""

from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path

UTILS_DIR = Path(__file__).resolve().parents[1] / "neotec_insight" / "utils"


class _FakeFrappe(types.ModuleType):
    """Just enough of `frappe` for execution.py to import and for
    flag_binding_meta / load_flag_to_accounts to run against fixture data.
    """

    def __init__(self):
        super().__init__("frappe")
        self.account_flag_mappings: list[dict] = []
        self.accounts: list[dict] = []

        utils_mod = types.ModuleType("frappe.utils")
        utils_mod.flt = lambda v: float(v or 0)
        sys.modules["frappe.utils"] = utils_mod
        self.utils = utils_mod

    def get_all(self, doctype, filters=None, fields=None, limit_page_length=0, pluck=None):
        filters = filters or {}
        if doctype == "Account Flag Mapping":
            rows = [r for r in self.account_flag_mappings
                    if all(r.get(k) == v for k, v in filters.items())]
        elif doctype == "Account":
            names = None
            if "name" in filters and isinstance(filters["name"], list) and filters["name"][0] == "in":
                names = set(filters["name"][1])
            rows = [a for a in self.accounts if names is None or a["name"] in names]
        else:
            rows = []
        if pluck:
            return [r[pluck] for r in rows]
        if fields:
            return [{f: r.get(f) for f in fields} for r in rows]
        return rows


def _load_execution_module():
    """Import execution.py with a fake `frappe` in sys.modules, so the real
    module code (not an AST-extracted fragment) is what gets tested — this
    function has too many DB round trips to extract cleanly, unlike
    is_row_hidden in test_visibility.py."""
    sys.path.insert(0, str(UTILS_DIR.parent.parent.parent))  # app root, harmless if unused
    fake = _FakeFrappe()
    sys.modules["frappe"] = fake
    sys.path.insert(0, str(UTILS_DIR))
    # execution.py does `from neotec_insight.neotec_insight.utils.formula import
    # evaluate_row_formula` — stub the whole dotted path so import succeeds
    # without the real package tree.
    formula_mod = types.ModuleType("formula")
    formula_mod.evaluate_row_formula = lambda formula, ctx: 0.0
    pkg_root = types.ModuleType("neotec_insight")
    pkg_ni = types.ModuleType("neotec_insight.neotec_insight")
    pkg_utils = types.ModuleType("neotec_insight.neotec_insight.utils")
    pkg_utils.formula = formula_mod
    sys.modules.setdefault("neotec_insight", pkg_root)
    sys.modules.setdefault("neotec_insight.neotec_insight", pkg_ni)
    sys.modules["neotec_insight.neotec_insight.utils"] = pkg_utils
    sys.modules["neotec_insight.neotec_insight.utils.formula"] = formula_mod

    import importlib
    import execution as execution_mod  # noqa
    importlib.reload(execution_mod)
    return execution_mod, fake


class TestUnboundRowIsReported(unittest.TestCase):
    """A source row with zero Account Flag Mapping rows must still appear in
    the response, marked unbound — not silently absent."""

    def test_flag_with_no_mapping_at_all_reports_unbound(self):
        execution, fake = _load_execution_module()
        fake.account_flag_mappings = []  # nothing mapped anywhere
        fake.accounts = []

        rows = [{"kind": "source", "flag": "Revenue"},
                {"kind": "source", "flag": "COGS"}]
        meta = execution.flag_binding_meta("Test Report", flag_to_accounts={}, report_rows=rows)

        self.assertIn("Revenue", meta)
        self.assertIn("COGS", meta)
        self.assertEqual(meta["Revenue"]["resolved_count"], 0)
        self.assertFalse(meta["Revenue"]["has_binding"])

    def test_report_with_zero_mapping_rows_still_lists_every_flag(self):
        """Previously `flag_binding_meta` returned {} outright when the
        report had NO Account Flag Mapping rows at all — every source row
        vanished from the response rather than being marked unbound."""
        execution, fake = _load_execution_module()
        fake.account_flag_mappings = []
        rows = [{"kind": "source", "flag": "Revenue"}]
        meta = execution.flag_binding_meta("Test Report", flag_to_accounts={}, report_rows=rows)
        self.assertEqual(set(meta.keys()), {"Revenue"})
        self.assertEqual(meta["Revenue"], execution._unbound_binding_meta())


class TestDeletedAccountIsReported(unittest.TestCase):
    """A row bound directly to an account that was later deleted from the
    chart of accounts must not just silently under-report — it must say so."""

    def test_deleted_direct_account_is_flagged_missing(self):
        execution, fake = _load_execution_module()
        fake.account_flag_mappings = [
            {"report": "Test Report", "account": "4001 - Revenue - CO", "flag": "Revenue", "is_group_binding": 0, "creation": "2024-01-01"},
            {"report": "Test Report", "account": "4002 - Deleted Account - CO", "flag": "Revenue", "is_group_binding": 0, "creation": "2024-01-01"},
        ]
        # 4002 was deleted: it exists in the mapping table but not in Account.
        fake.accounts = [
            {"name": "4001 - Revenue - CO", "account_number": "4001", "account_name": "Revenue", "creation": "2024-01-01"},
        ]

        flag_to_accounts = {"Revenue": ["4001 - Revenue - CO", "4002 - Deleted Account - CO"]}
        rows = [{"kind": "source", "flag": "Revenue"}]
        meta = execution.flag_binding_meta("Test Report", flag_to_accounts=flag_to_accounts, report_rows=rows)

        self.assertEqual(meta["Revenue"]["missing_count"], 1)
        self.assertIn("4002 - Deleted Account - CO", meta["Revenue"]["missing_accounts"])
        # It still resolves (the leaf is still in flag_to_accounts) — the point
        # is that this is visible, not that the query is corrected here. The
        # query fix (dropping dead names) is a separate, riskier change.
        self.assertEqual(meta["Revenue"]["resolved_count"], 2)

    def test_all_direct_accounts_deleted_reports_zero_and_missing(self):
        execution, fake = _load_execution_module()
        fake.account_flag_mappings = [
            {"report": "Test Report", "account": "4009 - Gone - CO", "flag": "Revenue", "is_group_binding": 0, "creation": "2024-01-01"},
        ]
        fake.accounts = []  # the account is gone
        # load_flag_to_accounts would still resolve the stale name (it does
        # not validate existence) — simulate that here directly.
        flag_to_accounts = {"Revenue": ["4009 - Gone - CO"]}
        rows = [{"kind": "source", "flag": "Revenue"}]
        meta = execution.flag_binding_meta("Test Report", flag_to_accounts=flag_to_accounts, report_rows=rows)

        self.assertTrue(meta["Revenue"]["has_binding"])
        self.assertEqual(meta["Revenue"]["missing_count"], 1)


class TestUnboundMetaShapeIsConsistent(unittest.TestCase):
    """The frontend must never have to special-case 'no mapping rows in the
    report' vs 'mapping rows exist but resolve to nothing' — both paths
    through flag_binding_meta must produce the identical shape."""

    def test_unbound_shape_matches_whether_or_not_other_flags_have_mappings(self):
        execution, fake = _load_execution_module()
        # A different flag ("COGS") HAS a mapping, so `maps` is non-empty and
        # the function takes the main code path rather than the early-return
        # path — "Revenue" must still come out identical to the early-return
        # shape.
        fake.account_flag_mappings = [
            {"report": "Test Report", "account": "5001 - COGS - CO", "flag": "COGS", "is_group_binding": 0, "creation": "2024-01-01"},
        ]
        fake.accounts = [
            {"name": "5001 - COGS - CO", "account_number": "5001", "account_name": "COGS", "creation": "2024-01-01"},
        ]
        rows = [{"kind": "source", "flag": "Revenue"}, {"kind": "source", "flag": "COGS"}]
        meta = execution.flag_binding_meta(
            "Test Report", flag_to_accounts={"COGS": ["5001 - COGS - CO"]}, report_rows=rows)

        self.assertEqual(meta["Revenue"], execution._unbound_binding_meta())


if __name__ == "__main__":
    unittest.main(verbosity=2)
