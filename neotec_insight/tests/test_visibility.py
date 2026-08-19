"""Row visibility — the rule that decides what a reader of a P&L sees.

This shipped wrong once: the check was applied to every row kind with a shared
default, and rows vanished from consolidated runs of reports nobody had edited.
The first test below is the one that would have caught it, and it is the reason
this file exists.

No frappe import — `is_row_hidden` takes plain values so the decision can be
tested without a site.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "neotec_insight" / "utils"))

import ast  # noqa: E402


def _load_is_row_hidden():
    """Read the function out of execution.py without importing the module.

    execution.py imports frappe at module level; the decision function does not
    depend on it. Compiling just that function keeps the test runnable anywhere,
    which is what makes it cheap enough to always run.
    """
    src = (Path(__file__).resolve().parents[1] / "neotec_insight" / "utils" / "execution.py").read_text()
    tree = ast.parse(src)
    fn = next(n for n in tree.body
              if isinstance(n, ast.FunctionDef) and n.name == "is_row_hidden")
    ns: dict = {}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), "execution.py", "exec"), ns)
    return ns["is_row_hidden"]


is_row_hidden = _load_is_row_hidden()


class TestUntouchedReportsAreUnchanged(unittest.TestCase):
    """THE regression guard. A report nobody has edited carries no `show_when`
    on its rows. Every non-allocation row must still render consolidated, or an
    upgrade silently blanks live management accounts."""

    KINDS = ["section", "source", "formula", "spacer", "header"]

    def test_no_show_when_renders_consolidated(self):
        for kind in self.KINDS:
            with self.subTest(kind=kind):
                self.assertFalse(is_row_hidden({"label": "Revenue"}, kind, None))

    def test_no_show_when_renders_for_one_cost_centre(self):
        for kind in self.KINDS:
            with self.subTest(kind=kind):
                self.assertFalse(is_row_hidden({"label": "Revenue"}, kind, "Akaria"))

    def test_allocation_still_hides_itself_consolidated(self):
        """v2.62.1 behaviour, preserved: a pool shown consolidated sits beside
        expenses that already contain it and double-counts to anyone scanning."""
        self.assertTrue(is_row_hidden({"label": "GMO"}, "allocation", None))

    def test_allocation_shows_for_one_cost_centre(self):
        self.assertFalse(is_row_hidden({"label": "GMO"}, "allocation", "Akaria"))


class TestExplicitSetting(unittest.TestCase):
    def test_opt_in_hides_a_formula_row_consolidated(self):
        """The case this feature exists for: the before-allocation line, which
        duplicates net income when credit-back leaves the total unchanged."""
        row = {"label": "NOI (Before Allocation)", "show_when": "cost_center"}
        self.assertTrue(is_row_hidden(row, "formula", None))
        self.assertFalse(is_row_hidden(row, "formula", "Akaria"))

    def test_always_overrides_the_allocation_default(self):
        row = {"label": "GMO", "show_when": "always"}
        self.assertFalse(is_row_hidden(row, "allocation", None))


class TestBadValuesFallBackSafely(unittest.TestCase):
    """A stored value that is not one of the two known options must fall back to
    the per-kind default rather than hiding the row. Garbage in a definition
    should never blank a line of a filed-adjacent report."""

    def test_unknown_value_on_a_formula_row_still_shows(self):
        for bad in ("", None, "yes", "COST_CENTER", 1, [], {}):
            with self.subTest(value=bad):
                self.assertFalse(is_row_hidden({"show_when": bad}, "formula", None))

    def test_unknown_value_on_an_allocation_row_keeps_its_default(self):
        self.assertTrue(is_row_hidden({"show_when": "wat"}, "allocation", None))


class TestMultiSelectIsConsolidated(unittest.TestCase):
    """Several cost centres selected is not 'a single cost centre'. An
    allocation across three centres is as misleading as one across all of them."""

    def test_none_means_consolidated(self):
        self.assertTrue(is_row_hidden({}, "allocation", None))

    def test_a_single_name_means_selected(self):
        self.assertFalse(is_row_hidden({}, "allocation", "Akaria"))


class TestCcApplies(unittest.TestCase):
    """v2.76.1 — two allocation rows for different pools, both defaulting to
    'cost_center' visibility, used to print side by side as identical bare
    0.000s the moment ANY single cost centre was selected — correct numbers,
    indistinguishable from a row whose pool actually touches that centre.
    `cc_applies` is the caller's answer to "does this rule's pool touch the
    selected centre at all," computed in `_allocation_monthly`; this file
    only tests how `is_row_hidden` acts on that answer.
    """

    def test_irrelevant_pool_is_hidden_even_with_a_cost_centre_selected(self):
        # Default show_when for an allocation row is 'cost_center'. A single
        # centre IS selected, but this rule's pool doesn't touch it.
        self.assertTrue(is_row_hidden({}, "allocation", "Akaria", cc_applies=False))

    def test_relevant_pool_still_shows(self):
        self.assertFalse(is_row_hidden({}, "allocation", "Akaria", cc_applies=True))

    def test_default_cc_applies_is_true_for_old_call_sites(self):
        """Every call site written before v2.76.1 passes 3 positional args.
        Omitting cc_applies must reproduce the old behaviour exactly, or
        every existing caller changes behaviour on upgrade with no code
        change of its own."""
        self.assertFalse(is_row_hidden({}, "allocation", "Akaria"))

    def test_explicit_always_is_never_touched_by_cc_applies(self):
        """cc_applies only ever narrows the default cost-centre rule. It must
        never override a row the definition explicitly marked 'Always' —
        that would turn a refinement into a silent behaviour change for
        anyone who opted out of hiding."""
        row = {"show_when": "always"}
        self.assertFalse(is_row_hidden(row, "allocation", "Akaria", cc_applies=False))

    def test_consolidated_is_unaffected_by_cc_applies(self):
        """Consolidated (no single cost centre), 'applies' is defined as True
        by `_allocation_monthly` itself — but even if a caller got that
        wrong, is_row_hidden must not use cc_applies to decide the
        consolidated case; only single-cost-centre selection does."""
        self.assertTrue(is_row_hidden({}, "allocation", None, cc_applies=False))
        self.assertTrue(is_row_hidden({}, "allocation", None, cc_applies=True))

    def test_non_allocation_kinds_are_unaffected(self):
        """cc_applies is an allocation-specific signal. A source or formula
        row explicitly set to 'cost_center' should behave exactly as before,
        regardless of what a caller happens to pass for cc_applies."""
        row = {"show_when": "cost_center"}
        self.assertFalse(is_row_hidden(row, "formula", "Akaria", cc_applies=False))
        self.assertTrue(is_row_hidden(row, "formula", None, cc_applies=False))


class TestExcludeFromFormulas(unittest.TestCase):
    """v2.81.0 — 'cost_center_exclude' hides on the same condition as
    'cost_center' and additionally removes the value from the arithmetic.

    `is_row_hidden` must treat the two identically; only the caller differs.
    If it did not, the row would stay visible while its value was zeroed —
    a printed line reading 0.000 with no explanation, which is worse than
    either behaviour on its own."""

    def test_hides_consolidated_like_cost_center(self):
        row = {"label": "Segment-only line", "show_when": "cost_center_exclude"}
        self.assertTrue(is_row_hidden(row, "formula", None))

    def test_shows_for_a_single_cost_centre(self):
        row = {"label": "Segment-only line", "show_when": "cost_center_exclude"}
        self.assertFalse(is_row_hidden(row, "formula", "Akaria"))

    def test_applies_to_every_row_kind(self):
        row = {"show_when": "cost_center_exclude"}
        for kind in ("source", "formula", "allocation", "section"):
            with self.subTest(kind=kind):
                self.assertTrue(is_row_hidden(row, kind, None))

    def test_existing_values_are_unaffected(self):
        """The new option must not change what 'cost_center' or 'always' do —
        every report saved before this release keeps its behaviour exactly."""
        self.assertTrue(is_row_hidden({"show_when": "cost_center"}, "formula", None))
        self.assertFalse(is_row_hidden({"show_when": "always"}, "allocation", None))
        self.assertFalse(is_row_hidden({}, "formula", None))

    def test_unknown_value_still_falls_back_safely(self):
        """A typo must not silently exclude a row from the totals."""
        self.assertFalse(is_row_hidden({"show_when": "cost_center_exclud"}, "formula", None))


if __name__ == "__main__":
    unittest.main(verbosity=2)
