"""Tests for the v2.88.1 'Reclassify' VAT Adjustment — a per-voucher box
override, built for the real reported symptom: a Terraco purchase invoice
from a reverse-charge import supplier (Terraco UAE, invoices ACC-PINV-2026-
00392/00396/00397) landing in box 7 'Standard rated domestic purchases'
instead of box 9 'Imports subject to VAT (reverse charge)', because box
classification is driven entirely by the Purchase Invoice's own Tax
Category field (`_classify_purchase`), which was never set (or was cleared)
on these invoices — a different mechanism from VAT control account tagging,
and one with no existing per-voucher correction before this.

api/vat.py has relative imports (..utils.gtpl_core, .health) that make a
full module load impractical outside the real app package, so the pure
pieces are extracted via AST — the same technique test_vat_clearing.py
already uses for _VAT_CLEARING — and the two DB-facing functions that
thread a forced box through (`_period_adjustments`, `_apply_adjustments`)
are exercised against a minimal fake `frappe.get_all`, the same
fake-frappe pattern used throughout this app's test suite
(test_cash_flow_forecast_engine.py).
"""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1] / "neotec_insight" / "api" / "vat.py"


class _FakeFrappe:
    """Backs the subset of `frappe` that `_period_adjustments` and
    `_apply_adjustments` actually call: `get_all`."""

    def __init__(self, get_all_impl):
        self.get_all = get_all_impl


def _load_pure_members(*names):
    """Extract selected module-level assignments / function defs from
    vat.py by AST, without importing the whole frappe-dependent module.
    Extracted members share one namespace so a function's free variables
    (e.g. `_is_vat_like` referencing `_NEVER_VAT_TYPES`/`_NOT_VAT`, or
    `_apply_adjustments` calling `_period_adjustments` and `frappe.get_all`)
    resolve against whatever else was pulled into the same call."""
    tree = ast.parse(APP_ROOT.read_text())
    ns: dict = {"re": re}
    wanted = set(names)
    found = set()
    for node in tree.body:
        target_names: set = set()
        if isinstance(node, ast.Assign):
            target_names = {t.id for t in node.targets if isinstance(t, ast.Name)}
        elif isinstance(node, ast.FunctionDef):
            target_names = {node.name}
        if target_names & wanted:
            mod = ast.Module(body=[node], type_ignores=[])
            ast.fix_missing_locations(mod)
            exec(compile(mod, "<extract>", "exec"), ns)
            found |= (target_names & wanted)
    missing = wanted - found
    if missing:
        raise AssertionError(f"Could not find {missing} as module-level in vat.py")
    return ns


class TestIsVatLikeAnyRootType(unittest.TestCase):
    """_is_vat_like is the shared test behind BOTH `_vat_accounts` (which
    additionally restricts by root_type) and `suggest_vat_accounts` (which
    deliberately does not) — this is the part that decides 'does this
    account's name/type plausibly mean VAT' independent of that
    restriction."""

    def setUp(self):
        ns = _load_pure_members("_NEVER_VAT_TYPES", "_NOT_VAT", "_is_vat_like")
        self.is_vat_like = ns["_is_vat_like"]

    def test_reverse_charge_liability_account_matches(self):
        """The exact shape of the account this feature exists for: a
        reverse-charge VAT control account booked as a Liability — which
        `_vat_accounts`'s Input-side heuristic (Asset only) would never
        find on its own; only a tag ever found it."""
        a = {"name": "21205001 - VAT 15% Reverse Charge", "account_name": "VAT 15% Reverse Charge",
             "account_type": "", "root_type": "Liability"}
        self.assertTrue(self.is_vat_like(a))

    def test_arabic_vat_wording_matches(self):
        a = {"name": "ضريبة القيمة المضافة", "account_name": "ضريبة القيمة المضافة",
             "account_type": "", "root_type": "Asset"}
        self.assertTrue(self.is_vat_like(a))

    def test_tax_type_matches_even_with_unrelated_name(self):
        a = {"name": "Some Tax Account", "account_name": "Some Tax Account",
             "account_type": "Tax", "root_type": "Liability"}
        self.assertTrue(self.is_vat_like(a))

    def test_bank_account_never_matches_even_with_vat_in_name(self):
        """The real contamination case _NEVER_VAT_TYPES exists for: a bank
        account literally named '...VAT...'."""
        a = {"name": "Bank Saudi Hollandi (IRSAA VAT)", "account_name": "Bank Saudi Hollandi (IRSAA VAT)",
             "account_type": "Bank", "root_type": "Asset"}
        self.assertFalse(self.is_vat_like(a))

    def test_wht_account_does_not_match(self):
        a = {"name": "WHT Withholding Tax Payable", "account_name": "WHT Withholding Tax Payable",
             "account_type": "", "root_type": "Liability"}
        self.assertFalse(self.is_vat_like(a))

    def test_unrelated_liability_account_does_not_match(self):
        a = {"name": "Accrued Payroll", "account_name": "Accrued Payroll",
             "account_type": "", "root_type": "Liability"}
        self.assertFalse(self.is_vat_like(a))


class TestReclassifyBoxLists(unittest.TestCase):
    """The box choices save_vat_adjustment/vat_reclassify_boxes accept —
    deliberately excludes box1_2 (GTPL-governed) and the box6/box12
    system totals, since Reclassify is only for the per-voucher category
    split ordinarily decided by tax_category."""

    def setUp(self):
        ns = _load_pure_members("_SALES_RECLASSIFY_BOXES", "_PURCHASE_RECLASSIFY_BOXES")
        self.sales_boxes = ns["_SALES_RECLASSIFY_BOXES"]
        self.purchase_boxes = ns["_PURCHASE_RECLASSIFY_BOXES"]

    def test_purchase_boxes_include_reverse_charge(self):
        self.assertIn("box9", self.purchase_boxes)
        self.assertIn("reverse charge", self.purchase_boxes["box9"].lower())

    def test_purchase_boxes_exclude_system_totals(self):
        self.assertNotIn("box12", self.purchase_boxes)

    def test_sales_boxes_exclude_government_and_system_total(self):
        self.assertNotIn("box1_2", self.sales_boxes)
        self.assertNotIn("box6", self.sales_boxes)

    def test_sales_boxes_cover_the_five_category_boxes(self):
        self.assertEqual(set(self.sales_boxes), {"box1", "box2", "box3", "box4", "box5"})

    def test_purchase_boxes_cover_the_five_category_boxes(self):
        self.assertEqual(set(self.purchase_boxes), {"box7", "box8", "box9", "box10", "box11"})


class TestPeriodAdjustmentsThreeWaySplit(unittest.TestCase):
    """_period_adjustments now returns (include, exclude, reclassify) —
    every caller was updated for the 3-tuple; this proves the split itself
    is correct, independent of any caller."""

    def setUp(self):
        self.ns = _load_pure_members("_period_adjustments")

    def _rows(self, rows):
        def get_all(doctype, filters=None, fields=None, limit_page_length=0):
            assert doctype == "Insight VAT Adjustment"
            return rows
        self.ns["frappe"] = _FakeFrappe(get_all)

    def test_splits_include_exclude_reclassify(self):
        self._rows([
            {"voucher_no": "SINV-1", "action": "Include", "reason": "paid this quarter", "target_box": None},
            {"voucher_no": "SINV-2", "action": "Exclude", "reason": "unpaid", "target_box": None},
            {"voucher_no": "PINV-9", "action": "Reclassify", "reason": "RCM import", "target_box": "box9"},
        ])
        include, exclude, reclassify = self.ns["_period_adjustments"]("ACME", "2026-01-01", "2026-03-31", "Purchase Invoice")
        self.assertEqual(include, {"SINV-1": "paid this quarter"})
        self.assertEqual(exclude, {"SINV-2": "unpaid"})
        self.assertEqual(reclassify, {"PINV-9": "box9"})

    def test_reclassify_with_no_target_box_is_dropped(self):
        """Defensive: a row somehow saved without a target_box must not
        silently become a falsy-but-present key that later code might
        mistake for 'in the dict, so force it'."""
        self._rows([
            {"voucher_no": "PINV-1", "action": "Reclassify", "reason": "x", "target_box": None},
        ])
        _include, _exclude, reclassify = self.ns["_period_adjustments"]("ACME", "2026-01-01", "2026-03-31", "Purchase Invoice")
        self.assertEqual(reclassify, {})


class TestApplyAdjustmentsStampsForceBox(unittest.TestCase):
    """_apply_adjustments is where the Reclassify decision actually reaches
    the invoice row that _purchase_breakdown/_sales_breakdown read
    `_force_box` off of."""

    def setUp(self):
        self.ns = _load_pure_members("_period_adjustments", "_apply_adjustments")

    def _wire(self, adjustment_rows, extra_invoice_rows=None):
        def get_all(doctype, filters=None, fields=None, limit_page_length=0):
            if doctype == "Insight VAT Adjustment":
                return adjustment_rows
            return extra_invoice_rows or []
        self.ns["frappe"] = _FakeFrappe(get_all)

    def test_reclassified_voucher_is_stamped_force_box(self):
        self._wire([
            {"voucher_no": "PINV-9", "action": "Reclassify", "reason": "RCM import, Tax Category was never set",
             "target_box": "box9"},
        ])
        invoices = [{"name": "PINV-9", "base_net_total": 100.0, "base_total_taxes_and_charges": 15.0}]
        kept, removed = self.ns["_apply_adjustments"](
            invoices, "Purchase Invoice", "ACME", "2026-08-01", "2026-08-31",
            ["name", "base_net_total", "base_total_taxes_and_charges"])
        self.assertEqual(removed, [])
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["_force_box"], "box9")

    def test_untouched_voucher_has_no_force_box(self):
        self._wire([
            {"voucher_no": "PINV-9", "action": "Reclassify", "reason": "x", "target_box": "box9"},
        ])
        invoices = [{"name": "PINV-OTHER", "base_net_total": 50.0, "base_total_taxes_and_charges": 7.5}]
        kept, _removed = self.ns["_apply_adjustments"](
            invoices, "Purchase Invoice", "ACME", "2026-08-01", "2026-08-31",
            ["name", "base_net_total", "base_total_taxes_and_charges"])
        self.assertNotIn("_force_box", kept[0])

    def test_reclassify_and_exclude_are_independent_for_different_vouchers(self):
        """Timing (Include/Exclude) and box (Reclassify) are separate
        questions about separate vouchers here — both must be honoured in
        the same period without one clobbering the other."""
        self._wire([
            {"voucher_no": "PINV-9", "action": "Reclassify", "reason": "RCM import", "target_box": "box9"},
            {"voucher_no": "PINV-5", "action": "Exclude", "reason": "unpaid", "target_box": None},
        ])
        invoices = [
            {"name": "PINV-9", "base_net_total": 100.0, "base_total_taxes_and_charges": 15.0},
            {"name": "PINV-5", "base_net_total": 200.0, "base_total_taxes_and_charges": 30.0},
        ]
        kept, removed = self.ns["_apply_adjustments"](
            invoices, "Purchase Invoice", "ACME", "2026-08-01", "2026-08-31",
            ["name", "base_net_total", "base_total_taxes_and_charges"])
        self.assertEqual([r["name"] for r in removed], ["PINV-5"])
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["name"], "PINV-9")
        self.assertEqual(kept[0]["_force_box"], "box9")

    def test_a_single_voucher_can_carry_both_a_reclassify_and_an_include(self):
        """A voucher pulled in from another period (Include) can ALSO need
        its box corrected (Reclassify) — both apply to the same voucher at
        once, which is exactly why save_vat_adjustment dedupes per-action
        rather than per-voucher."""
        self._wire(
            [
                {"voucher_no": "PINV-9", "action": "Include", "reason": "paid this quarter", "target_box": None},
                {"voucher_no": "PINV-9", "action": "Reclassify", "reason": "RCM import", "target_box": "box9"},
            ],
            extra_invoice_rows=[{"name": "PINV-9", "base_net_total": 100.0, "base_total_taxes_and_charges": 15.0}],
        )
        kept, removed = self.ns["_apply_adjustments"](
            [],  # PINV-9 is out-of-period, so it isn't in the base query result
            "Purchase Invoice", "ACME", "2026-08-01", "2026-08-31",
            ["name", "base_net_total", "base_total_taxes_and_charges"])
        self.assertEqual(removed, [])
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["name"], "PINV-9")
        self.assertEqual(kept[0]["_adj"], "in")
        self.assertEqual(kept[0]["_force_box"], "box9")


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestRealTerracoReverseChargeInvoices(unittest.TestCase):
    """The three real invoices from the customer's Terraco site: blank Tax
    Category, template 'KSA VAT RC 15% - T', template row rate 0 so ERPNext
    recorded VAT = 0. Previously landed in box 10 with no VAT."""

    def setUp(self):
        ns = _load_pure_members("STANDARD_RATE", "_PURCHASE_CATEGORY_RULES", "_classify",
                                "_RC_TEMPLATE", "_classify_purchase", "_rc_self_assessed")
        ns["flt"] = lambda v, p=None: round(float(v or 0), p) if p is not None else float(v or 0)
        self.ns = ns

    def inv(self, net, **kw):
        d = {"name": "ACC-PINV-2026-00392", "tax_category": "", "taxes_and_charges": "KSA VAT RC 15% - T",
             "base_net_total": net, "base_total_taxes_and_charges": 0}
        d.update(kw)
        return d

    def test_template_alone_routes_to_box9(self):
        self.assertEqual(self.ns["_classify_purchase"](self.inv(11664.75)), "box9")

    def test_self_assessed_vat_is_15_percent_of_net(self):
        pi = self.inv(11664.75)
        self.assertEqual(self.ns["_rc_self_assessed"]("box9", pi), 1749.71)
        pi = self.inv(90083.41915)
        self.assertEqual(self.ns["_rc_self_assessed"]("box9", pi), 13512.51)

    def test_recorded_vat_is_left_alone(self):
        pi = self.inv(1000, base_total_taxes_and_charges=150)
        self.assertEqual(self.ns["_rc_self_assessed"]("box9", pi), 0.0)

    def test_other_boxes_never_self_assess(self):
        self.assertEqual(self.ns["_rc_self_assessed"]("box10", self.inv(1000)), 0.0)

    def test_plain_zero_vat_purchase_still_box10(self):
        pi = self.inv(500, taxes_and_charges="KSA VAT 15% - T")
        pi["taxes_and_charges"] = ""
        self.assertEqual(self.ns["_classify_purchase"](pi), "box10")

    def test_ordinary_template_not_mistaken_for_rc(self):
        pi = self.inv(500, taxes_and_charges="KSA VAT 15% - T", base_total_taxes_and_charges=75)
        self.assertEqual(self.ns["_classify_purchase"](pi), "box7")

    def test_tax_category_still_wins(self):
        pi = self.inv(500, tax_category="Imports customs", taxes_and_charges="")
        self.assertEqual(self.ns["_classify_purchase"](pi), "box8")
