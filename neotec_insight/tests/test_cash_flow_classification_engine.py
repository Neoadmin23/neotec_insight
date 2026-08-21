"""Cash Flow Classification engine — tests for the pure functions in
utils/cash_flow_classification.py.

Same isolation and testing discipline as test_cash_flow_forecast_engine.py:
standalone fake-frappe, pure functions carry the test burden, DB-facing
wrappers stay thin and untested by this file.
"""

from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]


def _load_engine():
    fake_frappe = types.ModuleType("frappe")
    fake_utils = types.ModuleType("frappe.utils")
    fake_utils.flt = lambda v, precision=None: (
        round(float(v or 0), precision) if precision is not None else float(v or 0)
    )
    fake_frappe.utils = fake_utils
    sys.modules["frappe"] = fake_frappe
    sys.modules["frappe.utils"] = fake_utils

    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "cash_flow_classification_engine_under_test",
        APP_ROOT / "neotec_insight" / "utils" / "cash_flow_classification.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestInferTransactionType(unittest.TestCase):
    """The verified 98.0% structural rule for Column E."""

    def setUp(self):
        self.eng = _load_engine()

    def test_debit_greater_is_cash_in(self):
        self.assertEqual(self.eng.infer_transaction_type(100, 0), "Cash In")

    def test_credit_greater_is_cash_out(self):
        self.assertEqual(self.eng.infer_transaction_type(0, 100), "Cash Out")

    def test_equal_defaults_to_cash_out(self):
        """A zero-net row (shouldn't really occur, but must not crash or
        return an invalid value) falls to the same branch as a real credit,
        which is the more conservative of the two on a cash-out-heavy book."""
        self.assertEqual(self.eng.infer_transaction_type(50, 50), "Cash Out")


class TestPatternMatches(unittest.TestCase):
    def setUp(self):
        self.eng = _load_engine()

    def test_remarks_only(self):
        txn = {"remarks": "gosi payment for jan", "against_account": "irrelevant"}
        self.assertTrue(self.eng.pattern_matches("gosi", txn, "Remarks"))

    def test_against_account_only(self):
        txn = {"remarks": "irrelevant", "against_account": "hr-emp-555"}
        self.assertTrue(self.eng.pattern_matches("hr-emp-555", txn, "Against Account"))

    def test_remarks_pattern_not_checked_against_against_account_field(self):
        txn = {"remarks": "irrelevant text", "against_account": "gosi authority"}
        self.assertFalse(self.eng.pattern_matches("gosi", txn, "Remarks"))

    def test_combined_field_checks_both(self):
        txn = {"remarks": "settlement", "against_account": "riyadh bank loan"}
        self.assertTrue(self.eng.pattern_matches("riyadh bank", txn, "Remarks + Against Account"))

    def test_no_match(self):
        txn = {"remarks": "payroll payment", "against_account": "hr-emp-1"}
        self.assertFalse(self.eng.pattern_matches("gosi", txn, "Remarks"))


class TestScoreCandidates(unittest.TestCase):
    def setUp(self):
        self.eng = _load_engine()

    def test_single_matching_rule(self):
        txn = {"remarks": "gosi payment for jan", "against_account": ""}
        rules = [{"name": "R1", "pattern": "gosi", "match_field": "Remarks",
                  "target_line": "GOSI Payment", "rolling_precision": 98.0}]
        candidates = self.eng.score_candidates(txn, rules)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["target_line"], "GOSI Payment")
        self.assertEqual(candidates[0]["confidence"], 98.0)

    def test_no_matching_rule(self):
        txn = {"remarks": "something else entirely", "against_account": ""}
        rules = [{"name": "R1", "pattern": "gosi", "match_field": "Remarks",
                  "target_line": "GOSI Payment", "rolling_precision": 98.0}]
        self.assertEqual(self.eng.score_candidates(txn, rules), [])

    def test_multiple_rules_matching_same_transaction(self):
        txn = {"remarks": "current account to mr. medhat", "against_account": ""}
        rules = [
            {"name": "R1", "pattern": "current account to mr", "match_field": "Remarks",
             "target_line": "Mr. Medhat C/A", "rolling_precision": 60.0},
            {"name": "R2", "pattern": "mr. medhat", "match_field": "Remarks",
             "target_line": "Mr. Medhat Management Fee", "rolling_precision": 55.0},
        ]
        candidates = self.eng.score_candidates(txn, rules)
        self.assertEqual(len(candidates), 2)

    def test_falls_back_to_historical_precision_when_no_rolling_precision_yet(self):
        """A freshly-activated rule has no confirmed/corrected decisions yet
        — rolling_precision is None until the first one — so scoring must
        fall back to the mining-time historical_precision rather than
        treating a brand-new rule as zero confidence."""
        txn = {"remarks": "gosi payment", "against_account": ""}
        rules = [{"name": "R1", "pattern": "gosi", "match_field": "Remarks",
                  "target_line": "GOSI Payment", "rolling_precision": None,
                  "historical_precision": 97.0}]
        candidates = self.eng.score_candidates(txn, rules)
        self.assertEqual(candidates[0]["confidence"], 97.0)


class TestResolveClassification(unittest.TestCase):
    """The tier assignment — High / Medium / Low / Conflict — and
    specifically that Conflict always wins over confidence, per the build
    spec's explicit instruction not to let a numeric score silently
    override a real disagreement between rules."""

    def setUp(self):
        self.eng = _load_engine()

    def test_no_candidates_is_none_tier(self):
        result = self.eng.resolve_classification([])
        self.assertEqual(result["tier"], "none")
        self.assertIsNone(result["target_line"])
        self.assertIsNone(result["rule"])

    def test_single_high_confidence_candidate(self):
        candidates = [{"rule": "R1", "target_line": "GOSI Payment", "confidence": 98.0}]
        result = self.eng.resolve_classification(candidates)
        self.assertEqual(result["tier"], "high")
        self.assertEqual(result["target_line"], "GOSI Payment")
        self.assertEqual(result["confidence"], 98.0)
        self.assertEqual(result["rule"], "R1", "the frontend needs to know which rule to credit/correct")

    def test_medium_confidence_candidate(self):
        candidates = [{"rule": "R1", "target_line": "Bank Charge", "confidence": 80.0}]
        result = self.eng.resolve_classification(candidates)
        self.assertEqual(result["tier"], "medium")

    def test_low_confidence_candidate(self):
        candidates = [{"rule": "R1", "target_line": "Petty Cash", "confidence": 50.0}]
        result = self.eng.resolve_classification(candidates)
        self.assertEqual(result["tier"], "low")

    def test_agreeing_rules_use_the_higher_confidence(self):
        candidates = [
            {"rule": "R1", "target_line": "GOSI Payment", "confidence": 96.0},
            {"rule": "R2", "target_line": "GOSI Payment", "confidence": 99.0},
        ]
        result = self.eng.resolve_classification(candidates)
        self.assertEqual(result["tier"], "high")
        self.assertEqual(result["confidence"], 99.0)

    def test_conflicting_rules_are_flagged_conflict_not_averaged_or_majority(self):
        candidates = [
            {"rule": "R1", "target_line": "Mr. Medhat C/A", "confidence": 60.0},
            {"rule": "R2", "target_line": "Mr. Medhat Management Fee", "confidence": 55.0},
        ]
        result = self.eng.resolve_classification(candidates)
        self.assertEqual(result["tier"], "conflict")
        self.assertIsNone(result["target_line"])
        self.assertIsNone(result["rule"])
        self.assertEqual(len(result["alternatives"]), 2)

    def test_high_confidence_does_not_beat_a_conflict(self):
        """The exact case the build spec calls out: a numeric score must
        never silently win a disagreement, no matter how confident one
        side is."""
        candidates = [
            {"rule": "R1", "target_line": "Category A", "confidence": 99.9},
            {"rule": "R2", "target_line": "Category B", "confidence": 51.0},
        ]
        result = self.eng.resolve_classification(candidates)
        self.assertEqual(result["tier"], "conflict")

    def test_custom_thresholds_are_respected(self):
        candidates = [{"rule": "R1", "target_line": "X", "confidence": 90.0}]
        result = self.eng.resolve_classification(candidates, high_threshold=91.0, medium_threshold=50.0)
        self.assertEqual(result["tier"], "medium")


class TestClassifyTransaction(unittest.TestCase):
    """The end-to-end pure decision for one transaction, score + resolve
    together — the function the API layer actually calls per row."""

    def setUp(self):
        self.eng = _load_engine()

    def test_full_pipeline_high_confidence(self):
        txn = {"remarks": "gosi payment to dec'25", "against_account": ""}
        rules = [{"name": "R1", "pattern": "gosi", "match_field": "Remarks",
                  "target_line": "GOSI Payment", "rolling_precision": 98.0}]
        result = self.eng.classify_transaction(txn, rules)
        self.assertEqual(result["tier"], "high")
        self.assertEqual(result["target_line"], "GOSI Payment")

    def test_full_pipeline_no_match_is_none_tier(self):
        txn = {"remarks": "totally unrelated text", "against_account": ""}
        rules = [{"name": "R1", "pattern": "gosi", "match_field": "Remarks",
                  "target_line": "GOSI Payment", "rolling_precision": 98.0}]
        result = self.eng.classify_transaction(txn, rules)
        self.assertEqual(result["tier"], "none")


class TestMineCandidateRules(unittest.TestCase):
    """The rule miner — the exact backtested configuration (2-4 word
    phrases, min_purity=0.95) that produced 96.8-100% precision on the
    real, leakage-safe, voucher-grouped backtest."""

    def setUp(self):
        self.eng = _load_engine()

    def test_finds_a_clean_pattern(self):
        rows = (
            [{"remarks": f"gosi payment authority transfer month {i}", "target_line": "GOSI Payment"}
             for i in range(5)]
            + [{"remarks": "unrelated bank service charge deduction", "target_line": "Bank Charge"}] * 5
        )
        rules = self.eng.mine_candidate_rules(rows, min_support=3, min_purity=0.95)
        patterns = [r["pattern"] for r in rules]
        self.assertTrue(any("gosi authority" in p for p in patterns))

    def test_below_min_support_is_not_mined(self):
        rows = [{"remarks": "rare phrase here", "target_line": "X"}] * 2  # only 2, below default min_support=3
        rules = self.eng.mine_candidate_rules(rows, min_support=3, min_purity=0.95)
        self.assertEqual(rules, [])

    def test_impure_phrase_shared_across_classes_is_not_mined(self):
        """A phrase that shows up under two different target lines roughly
        evenly must not be mined as if it cleanly predicts either one —
        this is the Mr. Medhat case from the real data."""
        rows = (
            [{"remarks": "current account to mr medhat", "target_line": "Mr. Medhat C/A"}] * 5
            + [{"remarks": "current account to mr medhat", "target_line": "Mr. Medhat Management Fee"}] * 5
        )
        rules = self.eng.mine_candidate_rules(rows, min_support=3, min_purity=0.95)
        patterns = [r["pattern"] for r in rules]
        self.assertNotIn("current account", patterns)

    def test_mined_rules_default_to_remarks_only(self):
        """Against Account is never auto-selected by mining — a reviewer
        must widen match_field by hand if the evidence supports it."""
        rows = [{"remarks": "vat authority transfer for quarter one", "target_line": "VAT Payment"}
                for _ in range(5)]
        rules = self.eng.mine_candidate_rules(rows, min_support=3, min_purity=0.95)
        self.assertTrue(rules)
        self.assertTrue(all(r["match_field"] == "Remarks" for r in rules))

    def test_samples_are_capped(self):
        rows = [{"remarks": f"zakat payment run {i}", "target_line": "ZAKAT Payment"} for i in range(20)]
        rules = self.eng.mine_candidate_rules(rows, min_support=3, min_purity=0.95, max_samples=3)
        for r in rules:
            self.assertLessEqual(len(r["sample_transactions"]), 3)


class TestUpdateRuleStats(unittest.TestCase):
    def setUp(self):
        self.eng = _load_engine()

    def test_confirmed_increments_and_computes_precision(self):
        current = {"times_suggested": 5, "times_confirmed": 4, "times_corrected": 0}
        updated = self.eng.update_rule_stats(current, "confirmed")
        self.assertEqual(updated["times_confirmed"], 5)
        self.assertEqual(updated["rolling_precision"], 100.0)

    def test_corrected_lowers_precision(self):
        current = {"times_suggested": 10, "times_confirmed": 9, "times_corrected": 0}
        updated = self.eng.update_rule_stats(current, "corrected")
        self.assertEqual(updated["times_corrected"], 1)
        self.assertAlmostEqual(updated["rolling_precision"], 90.0, places=1)

    def test_suggested_does_not_affect_precision(self):
        """A shown-but-not-yet-acted-on suggestion must not move precision
        in either direction — it isn't evidence of anything yet."""
        current = {"times_suggested": 3, "times_confirmed": 3, "times_corrected": 0}
        before = self.eng.update_rule_stats(current, "confirmed")["rolling_precision"]
        updated = self.eng.update_rule_stats(dict(current, times_confirmed=3), "suggested")
        self.assertEqual(updated["times_suggested"], 4)
        # precision computed from confirmed/corrected only, unaffected by the suggest
        self.assertEqual(updated["rolling_precision"], before)

    def test_no_decisions_yet_gives_none_not_zero(self):
        """A brand-new rule with zero confirmations and zero corrections has
        an UNDEFINED rolling precision, not a zero one — those mean very
        different things (no evidence yet, vs. proven wrong every time)."""
        current = {"times_suggested": 1, "times_confirmed": 0, "times_corrected": 0}
        updated = self.eng.update_rule_stats(current, "suggested")
        self.assertIsNone(updated["rolling_precision"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
