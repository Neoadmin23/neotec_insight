"""Cash Flow Forecast engine — tests for the pure functions in
utils/cash_flow_forecast.py.

This module is deliberately standalone (no shared code with the P&L/report
engine), which means it re-implements the calendar<->FY-position conversion
that utils/fiscal_year.py already has correctly. That duplication is an
accepted, stated cost (see the module's own docstring) — the mitigation is
running the exact fixture pair that caught the ORIGINAL month-shift bug
(test_allocation_budget_month.py) against THIS module's own conversion,
independently. If this file's Jan-start/Apr-start tests ever pass while
test_allocation_budget_month.py's fail, or vice versa, the two
implementations have drifted — which is the whole reason to test both
separately rather than trust one covers the other.
"""

from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]  # .../neotec_insight/neotec_insight


def _load_engine():
    """Import utils/cash_flow_forecast.py with a minimal fake frappe, the
    same pattern used throughout this app's test suite."""
    fake_frappe = types.ModuleType("frappe")
    fake_utils = types.ModuleType("frappe.utils")
    fake_utils.flt = lambda v, precision=None: (
        round(float(v or 0), precision) if precision is not None else float(v or 0)
    )
    fake_utils.getdate = lambda v: v
    fake_frappe.utils = fake_utils
    sys.modules["frappe"] = fake_frappe
    sys.modules["frappe.utils"] = fake_utils

    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "cash_flow_forecast_engine_under_test",
        APP_ROOT / "neotec_insight" / "utils" / "cash_flow_forecast.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Date:
    def __init__(self, month):
        self.month = month


class TestCalendarFyPositionJanuaryStart(unittest.TestCase):
    def setUp(self):
        self.eng = _load_engine()

    def test_january_is_position_0(self):
        self.assertEqual(self.eng.calendar_to_fy_position(1, 1), 0)

    def test_december_is_position_11(self):
        self.assertEqual(self.eng.calendar_to_fy_position(12, 1), 11)

    def test_full_year_round_trips(self):
        for cal_m in range(1, 13):
            pos = self.eng.calendar_to_fy_position(cal_m, 1)
            self.assertEqual(self.eng.fy_position_to_calendar(pos, 1), cal_m)


class TestCalendarFyPositionAprilStart(unittest.TestCase):
    """The pair that actually catches a shift — position and calendar month
    are three apart and cross a calendar-year boundary."""

    def setUp(self):
        self.eng = _load_engine()

    def test_april_is_position_0(self):
        self.assertEqual(self.eng.calendar_to_fy_position(4, 4), 0)

    def test_march_is_position_11(self):
        self.assertEqual(self.eng.calendar_to_fy_position(3, 4), 11)

    def test_full_year_round_trips(self):
        for cal_m in range(1, 13):
            pos = self.eng.calendar_to_fy_position(cal_m, 4)
            self.assertEqual(self.eng.fy_position_to_calendar(pos, 4), cal_m)


class TestFyPositionToCalendarYear(unittest.TestCase):
    """Right month, wrong year is the same bug one level up — caught here
    specifically because it wasn't caught by the month-only tests above."""

    def setUp(self):
        self.eng = _load_engine()

    def test_january_start_every_position_same_year(self):
        for pos in range(12):
            self.assertEqual(self.eng.fy_position_to_calendar_year(pos, 2026, 1), 2026)

    def test_april_start_positions_0_to_8_are_the_start_year(self):
        # position 0=Apr, 8=Dec, both still calendar year 2026 for FY2026.
        for pos in [0, 4, 8]:
            self.assertEqual(self.eng.fy_position_to_calendar_year(pos, 2026, 4), 2026)

    def test_april_start_positions_9_to_11_roll_into_next_year(self):
        # position 9=Jan, 10=Feb, 11=Mar — these are 2027 for FY2026 (April-start).
        for pos in [9, 10, 11]:
            self.assertEqual(self.eng.fy_position_to_calendar_year(pos, 2026, 4), 2027)

    def test_matches_calendar_month_pairing_across_the_full_year(self):
        """Walk the whole FY2026 April-start year and confirm every
        (year, month) pair produced is what a human calendar would say."""
        expected = [
            (2026, 4), (2026, 5), (2026, 6), (2026, 7), (2026, 8), (2026, 9),
            (2026, 10), (2026, 11), (2026, 12), (2027, 1), (2027, 2), (2027, 3),
        ]
        for pos in range(12):
            year = self.eng.fy_position_to_calendar_year(pos, 2026, 4)
            month = self.eng.fy_position_to_calendar(pos, 4)
            self.assertEqual((year, month), expected[pos], f"position {pos}")


class TestAttributeBindingMonthlyDirectionSplit(unittest.TestCase):
    """The Riyadh Bank Loan case: one account, two lines, told apart only by
    direction_mode. If this ever nets instead of splitting, both lines show
    the same wrong number, the way January's budget once showed under
    February."""

    def setUp(self):
        self.eng = _load_engine()
        self.voucher = ("Journal Entry", "JE-0001")
        self.bank_leg = {self.voucher}
        self.transfers = set()
        self.overrides = set()

    def test_debit_only_counts_repayment_not_draw(self):
        rows = [
            {"voucher_type": "Journal Entry", "voucher_no": "JE-0001",
             "posting_date": _Date(1), "debit": 27382, "credit": 0},  # repayment
        ]
        monthly = self.eng.attribute_binding_monthly(
            rows, "Debit Only", self.bank_leg, self.transfers, self.overrides, 1, list(range(12)))
        self.assertEqual(monthly[0], 27382.0)

    def test_credit_only_counts_draw_not_repayment(self):
        rows = [
            {"voucher_type": "Journal Entry", "voucher_no": "JE-0002",
             "posting_date": _Date(2), "debit": 0, "credit": 15000},  # draw
        ]
        monthly = self.eng.attribute_binding_monthly(
            rows, "Credit Only", {("Journal Entry", "JE-0002")}, self.transfers, self.overrides,
            1, list(range(12)))
        self.assertEqual(monthly[1], 15000.0)

    def test_debit_only_excludes_a_credit_row(self):
        """A draw (credit) must not leak into the Debit Only (settlement)
        line — the exact failure mode direction-split exists to prevent."""
        rows = [{"voucher_type": "Journal Entry", "voucher_no": "JE-0002",
                 "posting_date": _Date(2), "debit": 0, "credit": 15000}]
        monthly = self.eng.attribute_binding_monthly(
            rows, "Debit Only", {("Journal Entry", "JE-0002")}, self.transfers, self.overrides,
            1, list(range(12)))
        self.assertEqual(monthly[1], 0.0)

    def test_net_mode_nets_both(self):
        rows = [
            {"voucher_type": "Journal Entry", "voucher_no": "JE-0001",
             "posting_date": _Date(3), "debit": 27382, "credit": 0},
            {"voucher_type": "Journal Entry", "voucher_no": "JE-0003",
             "posting_date": _Date(3), "debit": 0, "credit": 10000},
        ]
        monthly = self.eng.attribute_binding_monthly(
            rows, "Net", {("Journal Entry", "JE-0001"), ("Journal Entry", "JE-0003")},
            self.transfers, self.overrides, 1, list(range(12)))
        self.assertEqual(monthly[2], 17382.0)


class TestAttributeBindingMonthlyCashLegRule(unittest.TestCase):
    """A row whose voucher never touched a bank account is a pure accrual —
    must be excluded, not just left as a zero-value inclusion."""

    def setUp(self):
        self.eng = _load_engine()

    def test_accrual_only_voucher_is_excluded(self):
        rows = [{"voucher_type": "Journal Entry", "voucher_no": "JE-ACCRUAL",
                 "posting_date": _Date(1), "debit": 5000, "credit": 0}]
        # JE-ACCRUAL deliberately NOT in bank_leg_vouchers.
        monthly = self.eng.attribute_binding_monthly(
            rows, "Net", set(), set(), set(), 1, list(range(12)))
        self.assertEqual(monthly[0], 0.0)

    def test_transfer_voucher_is_excluded_even_if_also_a_bank_leg(self):
        """A bank-to-bank transfer can appear in bank_leg_vouchers (its other
        leg IS a cash account) — transfer_vouchers must still win, or every
        internal transfer double-prints as a real cash flow line."""
        key = ("Journal Entry", "JE-XFER")
        rows = [{"voucher_type": "Journal Entry", "voucher_no": "JE-XFER",
                 "posting_date": _Date(5), "debit": 40000, "credit": 0}]
        monthly = self.eng.attribute_binding_monthly(
            rows, "Net", {key}, {key}, set(), 1, list(range(12)))
        self.assertEqual(monthly[4], 0.0)

    def test_override_claimed_voucher_is_excluded_from_the_binding(self):
        """A voucher already claimed by a Tier 2 manual override must not
        ALSO be picked up by whatever Tier 1 binding would otherwise match
        it — the double-count the override mechanism must not itself cause."""
        key = ("Journal Entry", "JE-SPECIAL")
        rows = [{"voucher_type": "Journal Entry", "voucher_no": "JE-SPECIAL",
                 "posting_date": _Date(6), "debit": 9000, "credit": 0}]
        monthly = self.eng.attribute_binding_monthly(
            rows, "Net", {key}, set(), {key}, 1, list(range(12)))
        self.assertEqual(monthly[5], 0.0)


class TestClassifyVoucherLeg(unittest.TestCase):
    """The KSA inter-bank-transfer-with-fee case: three legs (source bank
    credit, destination bank debit, Bank Charges expense debit for the
    SARIE fee). Requiring EVERY other leg to be cash (the original
    implementation) misses this — the fee leg breaks that condition — and
    the transfer money then counts twice: once leaving the source bank,
    once arriving at the destination, as if it were two real, unrelated
    cash movements."""

    def setUp(self):
        self.eng = _load_engine()
        self.cash_accounts = ["Riyadh Bank - CO", "ANB - CO"]

    def test_plain_two_leg_transfer_is_excluded(self):
        is_bank_leg, is_transfer = self.eng.classify_voucher_leg(
            "Riyadh Bank - CO", {"ANB - CO"}, self.cash_accounts)
        self.assertTrue(is_bank_leg)
        self.assertTrue(is_transfer)

    def test_three_leg_transfer_with_fee_source_bank_leg_is_still_excluded(self):
        """The exact bug this fix closes: a third, non-cash fee leg must not
        prevent the two cash legs from being recognised as a transfer."""
        other_legs = {"ANB - CO", "Bank Charges - CO"}
        is_bank_leg, is_transfer = self.eng.classify_voucher_leg(
            "Riyadh Bank - CO", other_legs, self.cash_accounts)
        self.assertTrue(is_bank_leg)
        self.assertTrue(is_transfer, "source bank leg must be recognised as a transfer "
                                      "even with a third, non-cash fee leg present")

    def test_three_leg_transfer_with_fee_destination_bank_leg_is_also_excluded(self):
        other_legs = {"Riyadh Bank - CO", "Bank Charges - CO"}
        is_bank_leg, is_transfer = self.eng.classify_voucher_leg(
            "ANB - CO", other_legs, self.cash_accounts)
        self.assertTrue(is_bank_leg)
        self.assertTrue(is_transfer)

    def test_the_fee_leg_itself_is_never_treated_as_a_transfer(self):
        """The fee is real spend — it must count if bound to a Bank Charges
        line, never excluded by the transfer rule (which only ever applies
        to a leg that is ITSELF a cash account)."""
        other_legs = {"Riyadh Bank - CO", "ANB - CO"}
        is_bank_leg, is_transfer = self.eng.classify_voucher_leg(
            "Bank Charges - CO", other_legs, self.cash_accounts)
        self.assertTrue(is_bank_leg, "the fee leg did move cash (via the other two legs) "
                                     "so it must pass the bank-leg check")
        self.assertFalse(is_transfer, "the fee account itself is not cash, so it can "
                                      "never be classified as a transfer leg")

    def test_a_normal_customer_collection_is_not_a_transfer(self):
        """One cash leg + one Receivables leg — must not be mistaken for a
        transfer just because the cash leg exists."""
        is_bank_leg, is_transfer = self.eng.classify_voucher_leg(
            "Riyadh Bank - CO", {"Trade Receivables - CO"}, self.cash_accounts)
        self.assertTrue(is_bank_leg)
        self.assertFalse(is_transfer)

    def test_a_pure_accrual_with_no_cash_leg_is_neither(self):
        is_bank_leg, is_transfer = self.eng.classify_voucher_leg(
            "Rent Expense - CO", {"Accounts Payable - CO"}, self.cash_accounts)
        self.assertFalse(is_bank_leg)
        self.assertFalse(is_transfer)



class TestBuildTransferLog(unittest.TestCase):
    """Surfacing what classify_voucher_leg excludes — "how do we control
    internal transfers" answered by making them visible, not just removed."""

    def setUp(self):
        self.eng = _load_engine()

    def test_plain_transfer_shows_no_fee(self):
        rows = [
            {"voucher_type": "Journal Entry", "voucher_no": "JE-1", "account": "Riyadh Bank - CO",
             "posting_date": _Date(3), "debit": 0, "credit": 50000},
            {"voucher_type": "Journal Entry", "voucher_no": "JE-1", "account": "ANB - CO",
             "posting_date": _Date(3), "debit": 50000, "credit": 0},
        ]
        log = self.eng.build_transfer_log(rows, 1, list(range(12)))
        self.assertEqual(len(log), 1)
        self.assertEqual(log[0]["amount_sent"], 50000.0)
        self.assertEqual(log[0]["amount_received"], 50000.0)
        self.assertEqual(log[0]["fee"], 0.0)

    def test_ksa_transfer_with_fee_shows_the_fee(self):
        """The three-leg case: destination receives less than the source
        sent, by exactly the SARIE fee — surfaced as its own field, not
        silently folded into either amount."""
        rows = [
            {"voucher_type": "Journal Entry", "voucher_no": "JE-2", "account": "Riyadh Bank - CO",
             "posting_date": _Date(5), "debit": 0, "credit": 100000},
            {"voucher_type": "Journal Entry", "voucher_no": "JE-2", "account": "ANB - CO",
             "posting_date": _Date(5), "debit": 99975, "credit": 0},
        ]
        log = self.eng.build_transfer_log(rows, 1, list(range(12)))
        self.assertEqual(log[0]["amount_sent"], 100000.0)
        self.assertEqual(log[0]["amount_received"], 99975.0)
        self.assertEqual(log[0]["fee"], 25.0)

    def test_from_and_to_accounts_are_identified(self):
        rows = [
            {"voucher_type": "Journal Entry", "voucher_no": "JE-3", "account": "Riyadh Bank - CO",
             "posting_date": _Date(1), "debit": 0, "credit": 20000},
            {"voucher_type": "Journal Entry", "voucher_no": "JE-3", "account": "ANB - CO",
             "posting_date": _Date(1), "debit": 20000, "credit": 0},
        ]
        log = self.eng.build_transfer_log(rows, 1, list(range(12)))
        self.assertEqual(log[0]["from_accounts"], ["Riyadh Bank - CO"])
        self.assertEqual(log[0]["to_accounts"], ["ANB - CO"])


class TestBankBreakdownMonthly(unittest.TestCase):
    """Backs 'click a number, see which bank accounts fed it'."""

    def setUp(self):
        self.eng = _load_engine()

    def test_single_bank_attribution(self):
        rows = [{"voucher_type": "Payment Entry", "voucher_no": "PE-1",
                 "posting_date": _Date(2), "debit": 5000, "credit": 0}]
        key = ("Payment Entry", "PE-1")
        breakdown = self.eng.bank_breakdown_monthly(
            rows, "Net", {key}, set(), set(), {key: ["Riyadh Bank - CO"]}, 1, list(range(12)))
        self.assertEqual(breakdown[1]["Riyadh Bank - CO"], 5000.0)

    def test_split_payment_shares_evenly_across_banks(self):
        rows = [{"voucher_type": "Payment Entry", "voucher_no": "PE-2",
                 "posting_date": _Date(3), "debit": 4000, "credit": 0}]
        key = ("Payment Entry", "PE-2")
        breakdown = self.eng.bank_breakdown_monthly(
            rows, "Net", {key}, set(), set(), {key: ["Riyadh Bank - CO", "ANB - CO"]}, 1, list(range(12)))
        self.assertEqual(breakdown[2]["Riyadh Bank - CO"], 2000.0)
        self.assertEqual(breakdown[2]["ANB - CO"], 2000.0)

    def test_excluded_rows_contribute_nothing(self):
        rows = [{"voucher_type": "Journal Entry", "voucher_no": "JE-X",
                 "posting_date": _Date(4), "debit": 9000, "credit": 0}]
        key = ("Journal Entry", "JE-X")
        breakdown = self.eng.bank_breakdown_monthly(
            rows, "Net", {key}, {key}, set(), {key: ["Riyadh Bank - CO"]}, 1, list(range(12)))
        self.assertEqual(breakdown[3], {})



    """The one genuinely new engine capability — a rollforward, tested with
    the same January-start / April-start pair as everything else, since
    that's exactly where a silent off-by-one would hide."""

    def setUp(self):
        self.eng = _load_engine()

    def test_first_month_opens_at_the_seed(self):
        months = list(range(12))
        cash_in = {m: 0.0 for m in months}
        cash_out = {m: 0.0 for m in months}
        result = self.eng.balance_carry(100000, cash_in, cash_out, months)
        self.assertEqual(result[0]["opening"], 100000.0)

    def test_second_months_opening_is_first_months_closing(self):
        months = list(range(12))
        cash_in = {m: 0.0 for m in months}
        cash_out = {m: 0.0 for m in months}
        cash_in[0] = 50000
        cash_out[0] = 30000
        result = self.eng.balance_carry(100000, cash_in, cash_out, months)
        self.assertEqual(result[0]["closing"], 120000.0)
        self.assertEqual(result[1]["opening"], 120000.0)

    def test_full_year_chains_without_drift(self):
        months = list(range(12))
        cash_in = {m: 1000.0 * (m + 1) for m in months}
        cash_out = {m: 500.0 * (m + 1) for m in months}
        result = self.eng.balance_carry(0, cash_in, cash_out, months)
        running = 0.0
        for m in months:
            self.assertEqual(result[m]["opening"], running)
            running += cash_in[m] - cash_out[m]
            self.assertEqual(result[m]["closing"], round(running, 2))


class TestReconciliationResidual(unittest.TestCase):
    def setUp(self):
        self.eng = _load_engine()

    def test_zero_when_everything_accounted_for(self):
        residual = self.eng.reconciliation_residual(
            actual_bank_delta=20000, classified_cash_in_total=50000, classified_cash_out_total=30000)
        self.assertEqual(residual, 0.0)

    def test_nonzero_when_a_line_is_missing(self):
        """A binding that should have captured SAR 4,200 but didn't (wrong
        account, missing cost centre, whatever) must show up here as a
        residual — never silently absorbed into an existing line."""
        residual = self.eng.reconciliation_residual(
            actual_bank_delta=20000, classified_cash_in_total=50000, classified_cash_out_total=25800)
        self.assertEqual(residual, -4200.0)

    def test_nonzero_when_double_counted(self):
        """The 'Payment To Supplier' overlap scenario — a line captures more
        than the bank actually moved."""
        residual = self.eng.reconciliation_residual(
            actual_bank_delta=20000, classified_cash_in_total=50000, classified_cash_out_total=34000)
        self.assertEqual(residual, 4000.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
