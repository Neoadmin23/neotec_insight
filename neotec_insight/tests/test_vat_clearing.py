"""Tests for api/vat.py's _VAT_CLEARING pattern — the fix for a real bug
found in IRSAA's own Q2 2026 ledger: a quarter-end VAT close done as two
separate Journal Entries (one per VAT side, each paired with a
'VAT Reconciliation' clearing account) was invisible to the existing
settlement-exclusion check, which only catches a single voucher touching
both Output AND Input VAT directly. Real JE numbers: ACC-JV-2026-01038
(Output VAT side, 157,109.07 SAR) and ACC-JV-2026-01035 (Input VAT side,
30,595.85 SAR), both dated 30-06-2026, both against
'21204002 - VAT Reconciliation حساب التسوية لضريبة القيمة المضافة - IRSAA'.

api/vat.py has relative imports (..utils.gtpl_core, .health) that make a
full module load impractical outside the real app package — extracted via
AST instead, the same surgical technique used elsewhere in this app for
isolated pure logic living inside an otherwise frappe-dependent file.
"""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1] / "neotec_insight" / "api" / "vat.py"


def _extract_pattern(varname: str) -> re.Pattern:
    """Finds `varname = re.compile(...)` at module level in vat.py and
    evaluates just that one assignment — not the whole module, which would
    need frappe and this app's package context to import."""
    tree = ast.parse(APP_ROOT.read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == varname for t in node.targets):
            ns: dict = {"re": re}
            exec(compile(ast.Expression(node.value), "<extract>", "eval"), ns)
            return eval(compile(ast.Expression(node.value), "<extract>", "eval"), ns)
    raise AssertionError(f"{varname} not found as a module-level re.compile(...) in vat.py")


class TestVatClearingPattern(unittest.TestCase):
    """The exact real account name from IRSAA's ledger, plus the cases this
    must NOT match — a settlement/reconciliation account with no VAT in its
    name (a different clearing account entirely), and a VAT account with no
    settlement/reconciliation wording (a real Output/Input VAT account,
    which must keep being counted normally)."""

    def setUp(self):
        self.pattern = _extract_pattern("_VAT_CLEARING")

    def test_matches_the_real_irsaa_clearing_account(self):
        name = "21204002 - VAT Reconciliation حساب التسوية لضريبة القيمة المضافة - IRSAA"
        self.assertTrue(self.pattern.search(name))

    def test_matches_arabic_only_wording(self):
        self.assertTrue(self.pattern.search("حساب تسوية ضريبة القيمة المضافة"))

    def test_matches_english_settlement_wording_too(self):
        self.assertTrue(self.pattern.search("VAT Settlement Account"))

    def test_does_not_match_a_real_output_vat_account(self):
        """The account this whole fix protects from being wrongly swept in —
        a real Output VAT control account must never match this pattern."""
        self.assertIsNone(self.pattern.search("21204004 - Output VAT - 15% - IRSAA"))

    def test_does_not_match_a_real_input_vat_account(self):
        self.assertIsNone(self.pattern.search("11305002 - Input Vat - 15%"))

    def test_does_not_match_a_non_vat_settlement_account(self):
        """A settlement/clearing account with nothing VAT about it — e.g. a
        general bank reconciliation account — must not be swept in just
        because it says 'reconciliation'."""
        self.assertIsNone(self.pattern.search("Bank Reconciliation Clearing Account"))

    def test_does_not_match_zakat_or_wht_accounts(self):
        """Different tax types entirely — _NOT_VAT already excludes these
        from being counted AS VAT; _VAT_CLEARING must not treat them as a
        VAT-clearing account either, since they aren't one."""
        self.assertIsNone(self.pattern.search("Zakat Payable"))
        self.assertIsNone(self.pattern.search("WHT Withholding Tax Payable"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
