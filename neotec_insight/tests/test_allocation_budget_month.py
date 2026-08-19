"""Allocation row budget — the "January prints under February" bug.

`_allocation_budget_monthly` (api/report.py) reads Insight Allocation Entry's
budget_amount, keyed by a real calendar date (`period_month`), and has to
place each entry into `monthly`, which every other row in the same report
keys by FY-month POSITION (0..11), not calendar month. For a January-start
company these coincide only by accident — 0-indexed FY position 0 is
January, but a calendar month number is 1-indexed, so plugging the raw
calendar month straight in as the dict key put every entry one column late:
January's budget landed under key 1, which is February's slot.

This file exercises the real function body (via AST extraction, so importing
the enormous report.py module — and everything IT imports — isn't required)
against a fake `frappe.get_all` and the real fiscal_year util, across both a
January-start and an April-start company, since a bug that happens to cancel
out for one FY-start convention should not slip back in unnoticed for the
other.
"""

from __future__ import annotations

import ast
import sys
import types
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]  # .../neotec_insight/neotec_insight


class _Date:
    """Minimal stand-in for a Python date — only `.month` is used."""
    def __init__(self, month: int):
        self.month = month


class _FakeFrappe(types.ModuleType):
    def __init__(self):
        super().__init__("frappe")
        self.entries: list[dict] = []  # what get_all("Insight Allocation Entry", ...) returns

    def get_all(self, doctype, filters=None, fields=None, limit_page_length=0):
        if doctype == "Insight Allocation Entry":
            return list(self.entries)
        return []


def _load_allocation_budget_monthly():
    """Extract just _allocation_budget_monthly out of report.py without
    importing the module — report.py pulls in a large chunk of the app at
    import time, none of which this function's logic actually depends on."""
    src = (APP_ROOT / "neotec_insight" / "api" / "report.py").read_text()
    tree = ast.parse(src)
    fn = next(n for n in tree.body
              if isinstance(n, ast.FunctionDef) and n.name == "_allocation_budget_monthly")

    fake_frappe = _FakeFrappe()
    sys.modules["frappe"] = fake_frappe
    fake_utils = types.ModuleType("frappe.utils")
    fake_utils.cint = lambda v: int(v or 0)
    fake_utils.flt = lambda v, precision=None: round(float(v or 0), precision) if precision is not None else float(v or 0)
    sys.modules["frappe.utils"] = fake_utils
    fake_frappe.utils = fake_utils

    # Real fiscal_year module (pure, no frappe.get_all needed when an
    # override is supplied — see get_company_fy_start_month).
    sys.path.insert(0, str(APP_ROOT))
    pkg_root = types.ModuleType("neotec_insight")
    pkg_ni = types.ModuleType("neotec_insight.neotec_insight")
    pkg_utils = types.ModuleType("neotec_insight.neotec_insight.utils")
    sys.modules.setdefault("neotec_insight", pkg_root)
    sys.modules.setdefault("neotec_insight.neotec_insight", pkg_ni)
    sys.modules["neotec_insight.neotec_insight.utils"] = pkg_utils

    import importlib.util
    fy_spec = importlib.util.spec_from_file_location(
        "neotec_insight.neotec_insight.utils.fiscal_year",
        APP_ROOT / "neotec_insight" / "utils" / "fiscal_year.py")
    fy_mod = importlib.util.module_from_spec(fy_spec)
    sys.modules["neotec_insight.neotec_insight.utils.fiscal_year"] = fy_mod
    fy_spec.loader.exec_module(fy_mod)

    alloc_mod = types.ModuleType("neotec_insight.neotec_insight.utils.allocation")
    alloc_mod.month_start = lambda y, m: f"{y:04d}-{m:02d}-01"
    alloc_mod.month_end = lambda y, m: f"{y:04d}-{m:02d}-28"
    sys.modules["neotec_insight.neotec_insight.utils.allocation"] = alloc_mod

    ns: dict = {"flt": fake_utils.flt, "frappe": fake_frappe}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), "report.py", "exec"), ns)
    return ns["_allocation_budget_monthly"], fake_frappe


class TestJanuaryStartCompany(unittest.TestCase):
    """FY-month position == calendar month - 1 here, so a wrong-by-one bug is
    easy to miss by eye (position 1 vs calendar month 1 look almost right)."""

    def setUp(self):
        self.fn, self.frappe = _load_allocation_budget_monthly()

    def test_january_entry_lands_in_fy_position_0_not_1(self):
        self.frappe.entries = [
            {"period_month": _Date(1), "budget_amount": 27382},  # January
        ]
        monthly, has_cell = self.fn(
            rule="R1", fiscal_year=2026, months=list(range(12)),
            company=None, fy_start_month_override=1)
        self.assertEqual(monthly[0], 27382.0, "January must land at FY position 0")
        self.assertTrue(has_cell[0])
        self.assertEqual(monthly[1], 0.0, "February's slot must stay empty")
        self.assertFalse(has_cell[1])

    def test_february_entry_lands_in_fy_position_1_not_2(self):
        self.frappe.entries = [
            {"period_month": _Date(2), "budget_amount": 27000},  # February
        ]
        monthly, has_cell = self.fn(
            rule="R1", fiscal_year=2026, months=list(range(12)),
            company=None, fy_start_month_override=1)
        self.assertEqual(monthly[1], 27000.0)
        self.assertEqual(monthly[2], 0.0, "March's slot must stay empty")

    def test_full_year_round_trips_without_drift(self):
        """Every month entered once must reproduce the exact same sequence
        at FY position = calendar month - 1, with nothing lost or doubled."""
        self.frappe.entries = [
            {"period_month": _Date(cal_m), "budget_amount": cal_m * 1000}
            for cal_m in range(1, 13)
        ]
        monthly, has_cell = self.fn(
            rule="R1", fiscal_year=2026, months=list(range(12)),
            company=None, fy_start_month_override=1)
        for cal_m in range(1, 13):
            self.assertEqual(monthly[cal_m - 1], cal_m * 1000.0, f"calendar month {cal_m}")
            self.assertTrue(has_cell[cal_m - 1])


class TestAprilStartCompany(unittest.TestCase):
    """The bug this test guards against is invisible for a January-start
    company in casual testing (position and calendar month nearly agree) but
    unmistakable for an April-start one, where they're three apart and cross
    a calendar year boundary."""

    def setUp(self):
        self.fn, self.frappe = _load_allocation_budget_monthly()

    def test_april_is_fy_position_0(self):
        self.frappe.entries = [
            {"period_month": _Date(4), "budget_amount": 5000},  # April = FY start
        ]
        monthly, _has_cell = self.fn(
            rule="R1", fiscal_year=2026, months=list(range(12)),
            company=None, fy_start_month_override=4)
        self.assertEqual(monthly[0], 5000.0)

    def test_march_is_fy_position_11_the_last_month(self):
        self.frappe.entries = [
            {"period_month": _Date(3), "budget_amount": 9000},  # March = FY end
        ]
        monthly, _has_cell = self.fn(
            rule="R1", fiscal_year=2026, months=list(range(12)),
            company=None, fy_start_month_override=4)
        self.assertEqual(monthly[11], 9000.0)

    def test_full_year_round_trips_without_drift(self):
        self.frappe.entries = [
            {"period_month": _Date(cal_m), "budget_amount": cal_m * 1000}
            for cal_m in range(1, 13)
        ]
        monthly, has_cell = self.fn(
            rule="R1", fiscal_year=2026, months=list(range(12)),
            company=None, fy_start_month_override=4)
        # FY position = (cal_m - 4) % 12
        for cal_m in range(1, 13):
            pos = (cal_m - 4) % 12
            self.assertEqual(monthly[pos], cal_m * 1000.0, f"calendar month {cal_m}")
            self.assertTrue(has_cell[pos])


class TestDecemberAndReportedSymptom(unittest.TestCase):
    """The three cases that identified the fault in production.

    Ported from the parallel v2.80.1 test file so they run against the REAL
    function body rather than a re-implementation of it. A mirror of the loop
    cannot fail when the original is wrong — which is exactly how the v2.79.1
    test suite reported green while the shipped code was shifting months."""

    def setUp(self):
        self.fn, self.frappe = _load_allocation_budget_monthly()

    def _run(self, entries, start=1):
        self.frappe.entries = entries
        return self.fn(rule="R1", fiscal_year=2026, months=list(range(12)),
                       company=None, fy_start_month_override=start)

    def test_december_is_not_dropped(self):
        """THE tell that located the root cause. Calendar December is 12, which
        falls outside FY positions 0..11 — so keying by calendar month did not
        merely shift December's figure, it lost it. A shift moves a number; only
        an out-of-range key makes one disappear."""
        monthly, has_cell = self._run([
            {"period_month": _Date(12), "budget_amount": 27499},
        ])
        self.assertEqual(monthly[11], 27499.0, "December must land at FY position 11")
        self.assertTrue(has_cell[11])

    def test_reported_symptom_does_not_recur(self):
        """Reproduces the production report exactly: a full year at 27,382 with
        February edited to 27,000. The edited figure surfaced under March, which
        is what made the shift visible at all — twelve identical months had
        concealed it completely."""
        entries = [{"period_month": _Date(m), "budget_amount": 27382} for m in range(1, 13)]
        entries[1] = {"period_month": _Date(2), "budget_amount": 27000}
        monthly, _ = self._run(entries)
        self.assertEqual(monthly[0], 27382.0, "January must not be empty")
        self.assertEqual(monthly[1], 27000.0, "the edited February figure stays in February")
        self.assertEqual(monthly[2], 27382.0, "March must not show February's figure")

    def test_cost_centres_accumulate_within_one_position(self):
        """Consolidated, every cost centre's budget for a month lands on the
        same FY position and must SUM. Overwriting would silently report one
        cost centre's budget as the whole company's."""
        monthly, has_cell = self._run([
            {"period_month": _Date(1), "budget_amount": 100},
            {"period_month": _Date(1), "budget_amount": 250},
            {"period_month": _Date(2), "budget_amount": 7},
        ])
        self.assertEqual(monthly[0], 350.0)
        self.assertEqual(monthly[1], 7.0)
        self.assertTrue(has_cell[0])

    def test_april_start_december_wraps_to_position_8(self):
        """The same December case on an April-start year, where calendar 12 maps
        to position 8 rather than 11 — a conversion that is merely off by a
        constant would still pass the January-start case."""
        monthly, _ = self._run([
            {"period_month": _Date(12), "budget_amount": 27499},
        ], start=4)
        self.assertEqual(monthly[8], 27499.0)


class TestNoBudgetEntered(unittest.TestCase):
    """A rule with nothing entered must come back all-empty, not raise —
    this is read on every P&L run whether or not the rule has a budget."""

    def setUp(self):
        self.fn, self.frappe = _load_allocation_budget_monthly()

    def test_empty_entries_gives_all_false_all_zero(self):
        self.frappe.entries = []
        monthly, has_cell = self.fn(
            rule="R1", fiscal_year=2026, months=list(range(12)),
            company=None, fy_start_month_override=1)
        self.assertTrue(all(v == 0.0 for v in monthly.values()))
        self.assertTrue(all(v is False for v in has_cell.values()))

    def test_zero_budget_amount_does_not_mark_has_cell(self):
        """An explicit 0 and 'nothing entered' both read as not-budgeted here
        — the amount==0 case is filtered by the `if not amt: continue` guard,
        consistent with the has_cell contract used across the file: a cell
        exists only when it carries a nonzero figure."""
        self.frappe.entries = [{"period_month": _Date(1), "budget_amount": 0}]
        monthly, has_cell = self.fn(
            rule="R1", fiscal_year=2026, months=list(range(12)),
            company=None, fy_start_month_override=1)
        self.assertEqual(monthly[0], 0.0)
        self.assertFalse(has_cell[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
