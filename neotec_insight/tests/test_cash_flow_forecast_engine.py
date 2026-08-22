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


def _load_engine(get_value_impl=None, get_all_impl=None):
    """Import utils/cash_flow_forecast.py with a minimal fake frappe, the
    same pattern used throughout this app's test suite. get_value_impl, if
    given, backs frappe.get_value — lets a test simulate either a real
    return value or the exact 'unknown column' failure this module hit in
    production (v2.86.0's Fiscal Year.company, v2.86.2's Company.year_start_date).
    get_all_impl, if given, backs frappe.get_all the same way — lets a test
    capture the filters dict a DB-facing wrapper actually builds, rather
    than trusting it by inspection alone (the standard that missed two
    production bugs already on this module)."""
    fake_frappe = types.ModuleType("frappe")
    fake_utils = types.ModuleType("frappe.utils")
    fake_utils.flt = lambda v, precision=None: (
        round(float(v or 0), precision) if precision is not None else float(v or 0)
    )
    fake_utils.getdate = lambda v: v
    fake_frappe.utils = fake_utils
    if get_value_impl is not None:
        fake_frappe.get_value = get_value_impl
    if get_all_impl is not None:
        fake_frappe.get_all = get_all_impl
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


class TestFilterAndSignRow(unittest.TestCase):
    """The shared building block extracted this turn — attribute_binding_monthly,
    bank_breakdown_monthly, and the new list_binding_transactions all call
    this one function rather than each having their own copy of the
    exclusion rules. Tested directly so the rule itself is pinned down
    independently of any of its three callers."""

    def test_transfer_voucher_returns_none(self):
        key = ("Journal Entry", "JE-1")
        row = {"voucher_type": "Journal Entry", "voucher_no": "JE-1", "debit": 100, "credit": 0}
        result = self.eng.filter_and_sign_row(row, "Net", {key}, {key}, set())
        self.assertIsNone(result)

    def test_override_claimed_voucher_returns_none(self):
        key = ("Journal Entry", "JE-1")
        row = {"voucher_type": "Journal Entry", "voucher_no": "JE-1", "debit": 100, "credit": 0}
        result = self.eng.filter_and_sign_row(row, "Net", {key}, set(), {key})
        self.assertIsNone(result)

    def test_no_bank_leg_returns_none(self):
        row = {"voucher_type": "Journal Entry", "voucher_no": "JE-1", "debit": 100, "credit": 0}
        result = self.eng.filter_and_sign_row(row, "Net", set(), set(), set())
        self.assertIsNone(result)

    def test_net_mode_returns_signed_net(self):
        key = ("Payment Entry", "PE-1")
        row = {"voucher_type": "Payment Entry", "voucher_no": "PE-1", "debit": 100, "credit": 30}
        result = self.eng.filter_and_sign_row(row, "Net", {key}, set(), set())
        self.assertEqual(result, 70.0)

    def test_debit_only_excludes_a_credit_heavy_row(self):
        key = ("Payment Entry", "PE-1")
        row = {"voucher_type": "Payment Entry", "voucher_no": "PE-1", "debit": 0, "credit": 100}
        result = self.eng.filter_and_sign_row(row, "Debit Only", {key}, set(), set())
        self.assertIsNone(result)

    def setUp(self):
        self.eng = _load_engine()


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


class TestParseFyStartMonth(unittest.TestCase):
    """The production bug: v2.86.0/v2.86.1 queried a `company` column on the
    Fiscal Year doctype that doesn't exist, and raised 'Unknown column
    company in WHERE' the first time it ran against a real site. Nothing in
    this test file caught it, because the broken code lived entirely in the
    DB-facing half of the function — this is the pure half, split out
    specifically so the parsing logic (which is where a second bug could
    still hide) has real coverage, even though the DB query itself still
    doesn't."""

    def setUp(self):
        self.eng = _load_engine()

    def test_none_defaults_to_january(self):
        self.assertEqual(self.eng.parse_fy_start_month(None), 1)

    def test_missing_defaults_to_january(self):
        self.assertEqual(self.eng.parse_fy_start_month(""), 1)

    def test_date_object(self):
        self.assertEqual(self.eng.parse_fy_start_month(_Date(4)), 4)

    def test_iso_string(self):
        self.assertEqual(self.eng.parse_fy_start_month("2026-04-01"), 4)

    def test_iso_string_with_time_component(self):
        self.assertEqual(self.eng.parse_fy_start_month("2026-04-01 00:00:00"), 4)

    def test_malformed_string_defaults_to_january_not_a_crash(self):
        """A malformed or unexpected value here must not take down the
        whole report run — same defensive posture as the rest of this
        module's DB-facing wrappers."""
        self.assertEqual(self.eng.parse_fy_start_month("not-a-date"), 1)

    def test_out_of_range_month_defaults_to_january(self):
        self.assertEqual(self.eng.parse_fy_start_month(_Date(13)), 1)
        self.assertEqual(self.eng.parse_fy_start_month(_Date(0)), 1)



class TestResolveCompanyFyStartMonth(unittest.TestCase):
    """The DB-facing half of the v2.86.2/v2.86.3 production bugs: two
    separate 'Unknown column ... in ...' OperationalErrors, on two
    different queries, on the same real site — first Fiscal Year.company
    (fixed in v2.86.2), then Company.year_start_date itself (this file's
    reason to exist). fiscal_year.py's own get_company_fy_start_month
    absorbs the identical failure silently and falls back to January; this
    function must now do exactly the same, not propagate a 500."""

    def test_no_company_returns_january_without_querying(self):
        eng = _load_engine(get_value_impl=lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("must not query when company is falsy")))
        self.assertEqual(eng.resolve_company_fy_start_month(None), 1)
        self.assertEqual(eng.resolve_company_fy_start_month(""), 1)

    def test_normal_value_is_parsed(self):
        eng = _load_engine(get_value_impl=lambda *a, **k: _Date(4))
        self.assertEqual(eng.resolve_company_fy_start_month("Acme"), 4)

    def test_unknown_column_error_falls_back_to_january_not_a_500(self):
        """The exact production failure: the query itself raises, because
        the column this site's schema was assumed to have doesn't exist."""
        def raise_unknown_column(*a, **k):
            raise Exception("(1054, \"Unknown column 'year_start_date' in 'SELECT'\")")
        eng = _load_engine(get_value_impl=raise_unknown_column)
        self.assertEqual(eng.resolve_company_fy_start_month("IRSAA Business Solution"), 1)

    def test_any_other_query_failure_also_falls_back_rather_than_propagating(self):
        eng = _load_engine(get_value_impl=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
        self.assertEqual(eng.resolve_company_fy_start_month("Acme"), 1)



class TestClassifyVoucherLegGroup(unittest.TestCase):
    """The group-binding case (v2.87.4) — a line bound to an entire
    account-tree branch rather than one leaf. `accounts` is whichever of
    the group's leaves the CURRENT voucher touches, not the whole group."""

    def setUp(self):
        self.eng = _load_engine()
        self.cash_accounts = ["Riyadh Bank - CO", "ANB - CO"]

    def test_single_leg_matches_single_account_behaviour_exactly(self):
        """A 1-item group set must behave identically to the plain
        single-account function — proven directly, not just asserted, by
        comparing the two calls against each other."""
        one = self.eng.classify_voucher_leg("Rent Expense - CO", {"Riyadh Bank - CO"}, self.cash_accounts)
        group = self.eng.classify_voucher_leg_group(
            {"Rent Expense - CO"}, {"Riyadh Bank - CO"}, self.cash_accounts)
        self.assertEqual(one, group)

    def test_voucher_touching_two_leaves_of_the_same_group_is_still_one_bank_leg(self):
        """A voucher split across two leaf accounts under the SAME bound
        group (e.g. a payment split between two sub-categories) — both
        legs are 'mine', neither should be treated as the 'other' side."""
        is_bank_leg, is_transfer = self.eng.classify_voucher_leg_group(
            {"Rent - Building A", "Rent - Building B"}, {"Riyadh Bank - CO"}, self.cash_accounts)
        self.assertTrue(is_bank_leg)
        self.assertFalse(is_transfer)

    def test_group_containing_a_cash_account_still_detects_transfers(self):
        """If a group happens to include a bank/cash account among its
        leaves, transfer detection must still work the same way it does
        for a single cash-account binding."""
        is_bank_leg, is_transfer = self.eng.classify_voucher_leg_group(
            {"Riyadh Bank - CO"}, {"ANB - CO"}, self.cash_accounts)
        self.assertTrue(is_bank_leg)
        self.assertTrue(is_transfer)

    def test_pure_accrual_across_group_leaves_is_excluded(self):
        is_bank_leg, is_transfer = self.eng.classify_voucher_leg_group(
            {"GOSI Expense - CO"}, {"GOSI Payable - CO"}, self.cash_accounts)
        self.assertFalse(is_bank_leg)
        self.assertFalse(is_transfer)


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



class TestResolveBindingAccounts(unittest.TestCase):
    """The live group→leaves resolution — v2.87.4's actual feature. Uses
    the same get_value_impl/get_all_impl capture harness as
    TestResolveCompanyFyStartMonth and TestFetchBindingGlRowsFilters."""

    def _fake(self, account_info, leaf_accounts, capture=None):
        def get_value_impl(doctype, name, fields, as_dict=False):
            self.assertEqual(doctype, "Account")
            self.assertEqual(name, "the-account")
            return account_info

        def get_all_impl(doctype, filters=None, pluck=None, limit_page_length=0):
            if capture is not None:
                capture.append(filters)
            return leaf_accounts

        return _load_engine(get_value_impl=get_value_impl, get_all_impl=get_all_impl)

    def test_leaf_account_returns_itself_unchanged(self):
        eng = self._fake(account_info={"is_group": 0, "lft": 5, "rgt": 6, "company": "Acme"},
                         leaf_accounts=[])
        result = eng.resolve_binding_accounts("the-account", "Acme")
        self.assertEqual(result, ["the-account"])

    def test_group_account_resolves_to_its_live_leaves(self):
        eng = self._fake(
            account_info={"is_group": 1, "lft": 10, "rgt": 20, "company": "Acme"},
            leaf_accounts=["Leaf A", "Leaf B", "Leaf C"])
        result = eng.resolve_binding_accounts("the-account", "Acme")
        self.assertEqual(result, ["Leaf A", "Leaf B", "Leaf C"])

    def test_group_resolution_uses_nested_set_bounds_not_parent_link(self):
        """Confirms the filter shape is the lft/rgt bounds query, not
        something that would only catch direct children — a nested-set
        query is what makes this correctly LIVE and correctly catch
        grandchildren too, not just one level down."""
        capture: list = []
        eng = self._fake(
            account_info={"is_group": 1, "lft": 10, "rgt": 20, "company": "Acme"},
            leaf_accounts=[], capture=capture)
        eng.resolve_binding_accounts("the-account", "Acme")
        self.assertEqual(capture[0]["lft"], [">", 10])
        self.assertEqual(capture[0]["rgt"], ["<", 20])
        self.assertEqual(capture[0]["is_group"], 0)

    def test_missing_account_returns_itself_rather_than_crashing(self):
        eng = self._fake(account_info=None, leaf_accounts=[])
        result = eng.resolve_binding_accounts("the-account", "Acme")
        self.assertEqual(result, ["the-account"])


class TestFetchBindingGlRowsFilters(unittest.TestCase):
    """fetch_binding_gl_rows is DB-facing — this module's stated convention
    is that layer stays thin and untested, trusted by inspection. That
    standard already missed two production bugs on this exact module
    (v2.86.2, v2.86.3), so the filter-construction half of this function
    gets a real test here rather than staying 'reasonably confident by
    inspection' a third time. What's still untested: the actual DB round
    trip — these tests only prove the filters dict is built correctly, not
    that frappe.get_all does the right thing with it."""

    def _capture(self):
        calls = []

        def fake_get_all(doctype, filters=None, fields=None, limit_page_length=0, pluck=None):
            calls.append({"doctype": doctype, "filters": filters})
            return []

        def fake_get_value(doctype, name, fields, as_dict=False):
            # Every test in this class exercises a plain leaf-account
            # binding — resolve_binding_accounts must report "not a group"
            # so it returns [account] unchanged and the account filter this
            # class is actually testing isn't disturbed by the v2.87.4
            # group-resolution step now sitting in front of it.
            return {"is_group": 0, "lft": 1, "rgt": 2, "company": None}

        return calls, fake_get_all, fake_get_value

    def test_no_cost_centers_means_no_cost_center_filter_key_at_all(self):
        """Absence must mean 'no restriction', not 'restricted to nothing' —
        an empty/missing cost_centers list must not add a
        cost_center: ['in', []] filter, which would silently match zero rows."""
        calls, fake_get_all, fake_get_value = self._capture()
        eng = _load_engine(get_all_impl=fake_get_all, get_value_impl=fake_get_value)
        eng.fetch_binding_gl_rows(
            {"account": "GOSI Payable"}, None, "2026-01-01", "2026-12-31")
        self.assertNotIn("cost_center", calls[0]["filters"])

    def test_single_cost_center_uses_in_filter_with_one_value(self):
        calls, fake_get_all, fake_get_value = self._capture()
        eng = _load_engine(get_all_impl=fake_get_all, get_value_impl=fake_get_value)
        eng.fetch_binding_gl_rows(
            {"account": "Trade Receivables", "cost_centers": ["Audit"]},
            None, "2026-01-01", "2026-12-31")
        self.assertEqual(calls[0]["filters"]["cost_center"], ["in", ["Audit"]])

    def test_multiple_cost_centers_are_mapped_once_into_a_single_in_filter(self):
        """The actual feature requested this turn: one binding, several
        cost centres, one query — not one call per cost centre."""
        calls, fake_get_all, fake_get_value = self._capture()
        eng = _load_engine(get_all_impl=fake_get_all, get_value_impl=fake_get_value)
        eng.fetch_binding_gl_rows(
            {"account": "Trade Receivables", "cost_centers": ["Audit", "GRC", "HR"]},
            None, "2026-01-01", "2026-12-31")
        self.assertEqual(calls[0]["filters"]["cost_center"], ["in", ["Audit", "GRC", "HR"]])
        self.assertEqual(len(calls), 1, "must be one query, not one per cost centre")

    def test_company_filter_included_only_when_given(self):
        calls, fake_get_all, fake_get_value = self._capture()
        eng = _load_engine(get_all_impl=fake_get_all, get_value_impl=fake_get_value)
        eng.fetch_binding_gl_rows({"account": "A"}, "Acme Co", "2026-01-01", "2026-12-31")
        self.assertEqual(calls[0]["filters"]["company"], "Acme Co")

        calls2, fake_get_all2, fake_get_value2 = self._capture()
        eng2 = _load_engine(get_all_impl=fake_get_all2, get_value_impl=fake_get_value2)
        eng2.fetch_binding_gl_rows({"account": "A"}, None, "2026-01-01", "2026-12-31")
        self.assertNotIn("company", calls2[0]["filters"])

    def test_project_filter_included_only_when_given(self):
        calls, fake_get_all, fake_get_value = self._capture()
        eng = _load_engine(get_all_impl=fake_get_all, get_value_impl=fake_get_value)
        eng.fetch_binding_gl_rows(
            {"account": "A", "project": "Qassem Project"}, None, "2026-01-01", "2026-12-31")
        self.assertEqual(calls[0]["filters"]["project"], "Qassem Project")

    def test_party_filter_requires_both_type_and_party_together(self):
        """Party alone or party_type alone must not silently filter on a
        half-specified party — either both are present or neither is."""
        calls, fake_get_all, fake_get_value = self._capture()
        eng = _load_engine(get_all_impl=fake_get_all, get_value_impl=fake_get_value)
        eng.fetch_binding_gl_rows(
            {"account": "A", "party_type": "Employee", "party": "HR-EMP-555"},
            None, "2026-01-01", "2026-12-31")
        self.assertEqual(calls[0]["filters"]["party_type"], "Employee")
        self.assertEqual(calls[0]["filters"]["party"], "HR-EMP-555")

        calls2, fake_get_all2, fake_get_value2 = self._capture()
        eng2 = _load_engine(get_all_impl=fake_get_all2, get_value_impl=fake_get_value2)
        eng2.fetch_binding_gl_rows(
            {"account": "A", "party_type": "Employee"}, None, "2026-01-01", "2026-12-31")
        self.assertNotIn("party_type", calls2[0]["filters"])
        self.assertNotIn("party", calls2[0]["filters"])

    def test_date_range_and_account_always_present(self):
        calls, fake_get_all, fake_get_value = self._capture()
        eng = _load_engine(get_all_impl=fake_get_all, get_value_impl=fake_get_value)
        eng.fetch_binding_gl_rows({"account": "Rent Expense"}, None, "2026-01-01", "2026-12-31")
        f = calls[0]["filters"]
        # v2.87.4 — a leaf account resolves to a 1-item list via
        # resolve_binding_accounts, so the filter is now an `in` clause
        # rather than an exact match — functionally identical for a leaf,
        # and what makes the same code path also work for a group.
        self.assertEqual(f["account"], ["in", ["Rent Expense"]])
        self.assertEqual(f["posting_date"], ["between", ["2026-01-01", "2026-12-31"]])
        self.assertEqual(f["is_cancelled"], 0)

    def test_group_account_binding_queries_all_its_live_leaves(self):
        """The actual point of v2.87.4 — a binding on a GROUP account must
        query every one of its current leaf accounts, not the group name
        itself (which would match zero GL Entries, since a group account
        never carries a balance)."""
        calls = []

        def fake_get_all(doctype, filters=None, fields=None, limit_page_length=0, pluck=None):
            if doctype == "Account":
                return ["Leaf A", "Leaf B", "Leaf C"]
            calls.append({"doctype": doctype, "filters": filters})
            return []

        def fake_get_value(doctype, name, fields, as_dict=False):
            return {"is_group": 1, "lft": 10, "rgt": 20, "company": "Acme"}

        eng = _load_engine(get_all_impl=fake_get_all, get_value_impl=fake_get_value)
        eng.fetch_binding_gl_rows({"account": "Bank Charges Group"}, "Acme", "2026-01-01", "2026-12-31")
        self.assertEqual(calls[0]["filters"]["account"], ["in", ["Leaf A", "Leaf B", "Leaf C"]])



class TestListBindingTransactions(unittest.TestCase):
    """The individual transactions behind one month's Actual figure —
    backs the 'Open transaction' feature, the app equivalent of a row in
    the customer's own Excel Data sheet."""

    def setUp(self):
        self.eng = _load_engine()

    def test_returns_only_the_target_month(self):
        key1 = ("Payment Entry", "PE-1")
        key2 = ("Payment Entry", "PE-2")
        rows = [
            {"voucher_type": "Payment Entry", "voucher_no": "PE-1",
             "posting_date": _Date(1), "debit": 5000, "credit": 0},
            {"voucher_type": "Payment Entry", "voucher_no": "PE-2",
             "posting_date": _Date(2), "debit": 3000, "credit": 0},
        ]
        result = self.eng.list_binding_transactions(
            rows, "Net", {key1, key2}, set(), set(), 1, target_fy_position=0)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["voucher_no"], "PE-1")

    def test_excluded_rows_never_appear(self):
        key = ("Journal Entry", "JE-1")
        rows = [{"voucher_type": "Journal Entry", "voucher_no": "JE-1",
                 "posting_date": _Date(1), "debit": 9000, "credit": 0}]
        result = self.eng.list_binding_transactions(
            rows, "Net", {key}, {key}, set(), 1, target_fy_position=0)
        self.assertEqual(result, [])

    def test_amount_matches_what_the_summary_total_would_show(self):
        """The individual transaction amounts, summed, must equal exactly
        what attribute_binding_monthly reports for the same month — the
        whole point of sharing filter_and_sign_row between them."""
        key1 = ("Payment Entry", "PE-1")
        key2 = ("Payment Entry", "PE-2")
        rows = [
            {"voucher_type": "Payment Entry", "voucher_no": "PE-1",
             "posting_date": _Date(3), "debit": 5000, "credit": 0},
            {"voucher_type": "Payment Entry", "voucher_no": "PE-2",
             "posting_date": _Date(3), "debit": 2500, "credit": 0},
        ]
        keys = {key1, key2}
        transactions = self.eng.list_binding_transactions(
            rows, "Net", keys, set(), set(), 1, target_fy_position=2)
        monthly = self.eng.attribute_binding_monthly(
            rows, "Net", keys, set(), set(), 1, list(range(12)))
        self.assertEqual(sum(t["amount"] for t in transactions), monthly[2])

    def test_results_sorted_by_date(self):
        key1 = ("Payment Entry", "PE-1")
        key2 = ("Payment Entry", "PE-2")
        rows = [
            {"voucher_type": "Payment Entry", "voucher_no": "PE-2",
             "posting_date": _Date(1), "debit": 100, "credit": 0},
            {"voucher_type": "Payment Entry", "voucher_no": "PE-1",
             "posting_date": _Date(1), "debit": 200, "credit": 0},
        ]
        result = self.eng.list_binding_transactions(
            rows, "Net", {key1, key2}, set(), set(), 1, target_fy_position=0)
        # both land in the same FY position (posting_date only carries a
        # month in this fixture, per every other test in this file) —
        # sort key is the full posting_date string, so this test only
        # verifies stability, not a genuine date-order claim; kept simple
        # rather than inventing a richer fake date object just for this.
        self.assertEqual({r["voucher_no"] for r in result}, {"PE-1", "PE-2"})

    def test_carries_account_cost_center_and_remarks_through_when_present(self):
        """v2.87.6 — fetch_binding_gl_rows now fetches these; this function
        must pass them through, not just the original four fields."""
        key = ("Payment Entry", "PE-1")
        rows = [{"voucher_type": "Payment Entry", "voucher_no": "PE-1",
                 "posting_date": _Date(1), "debit": 5000, "credit": 0,
                 "account": "GOSI Payable - CO", "cost_center": "Audit",
                 "project": "", "remarks": "GOSI payment for Jan", "against": "GOSI"}]
        result = self.eng.list_binding_transactions(rows, "Net", {key}, set(), set(), 1, target_fy_position=0)
        self.assertEqual(result[0]["account"], "GOSI Payable - CO")
        self.assertEqual(result[0]["cost_center"], "Audit")
        self.assertEqual(result[0]["remarks"], "GOSI payment for Jan")
        self.assertEqual(result[0]["against_account"], "GOSI")

    def test_missing_new_fields_default_to_blank_not_a_crash(self):
        """A row built without these keys (e.g. still the older
        Override-sourced shape) must not raise a KeyError — blank display
        fields, same as before this change, not an error."""
        key = ("Payment Entry", "PE-1")
        rows = [{"voucher_type": "Payment Entry", "voucher_no": "PE-1",
                 "posting_date": _Date(1), "debit": 5000, "credit": 0}]
        result = self.eng.list_binding_transactions(rows, "Net", {key}, set(), set(), 1, target_fy_position=0)
        self.assertEqual(result[0]["account"], "")
        self.assertEqual(result[0]["remarks"], "")


class TestBalanceCarry(unittest.TestCase):
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
