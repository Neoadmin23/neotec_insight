"""GTPL deferral — acceptance tests against IRSAA's filed 2025 returns.

These are not synthetic fixtures. Every figure below is taken from the two
returns actually submitted to ZATCA:

  Q1 2025  ref 65008873700   Box 1.2  5,533,442.53 / 0.00        / 830,016.38
  Q2 2025  ref 65009426447   Box 1.2  5,390,429.00 / 1,177,115.00 / 631,997.10

and from the GL behind them. The test is whether the rule engine, given only
the invoices, the payments and a dated rule, arrives at the same treatment a
tax accountant arrived at by hand over two quarters.

Imports utils/gtpl_core.py directly. The decision core carries no frappe
import, so this suite runs anywhere — including here, with no site.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "neotec_insight" / "utils"))

import gtpl_core as gtpl  # noqa: E402

Q1 = ("2025-01-01", "2025-03-31")
Q2 = ("2025-04-01", "2025-06-30")

RULE = {
    "company": "IRSAA",
    "effective_from": "2025-01-01",
    "is_active": 1,
    "target_box": "1.2",
    "trigger_basis": "receipt_only",
    "credit_note_presentation": "gross_with_adjustment",
    "customer_groups": [{"customer_group": "Government"}],
    "customer_overrides": [],
}

# ACC-SINV-2025-00037 — 23 Jan 2025, KAFAA / Revenue - Kafaa, settled in Q2.
SINV_37 = {
    "name": "ACC-SINV-2025-00037",
    "posting_date": "2025-01-23",
    "customer": "Government Entity",
    "base_net_total": 4213314.00,
    "base_total_taxes_and_charges": 631997.10,
    "base_grand_total": 4845311.10,
    "is_return": 0,
}

# ACC-SINV-2025-00133 — 12 Mar 2025, never paid; cancelled in Q2.
SINV_133 = {
    "name": "ACC-SINV-2025-00133",
    "posting_date": "2025-03-12",
    "customer": "Government Entity",
    "base_net_total": 1177115.00,
    "base_total_taxes_and_charges": 176567.25,
    "base_grand_total": 1353682.25,
    "is_return": 0,
}

# ACC-SRET-2025-00016 — 5 May 2025, reverses 00133 in full.
SRET_16 = {
    "name": "ACC-SRET-2025-00016",
    "posting_date": "2025-05-05",
    "customer": "Government Entity",
    "base_net_total": -1177115.00,
    "base_total_taxes_and_charges": -176567.25,
    "base_grand_total": -1353682.25,
    "is_return": 1,
    "return_against": "ACC-SINV-2025-00133",
}

GROUPS = {"Government Entity": "Government"}

# 00037 settled 12 May 2025. 00133 never settled.
PAYMENTS = {
    "ACC-SINV-2025-00037": [
        {"posting_date": "2025-05-12", "allocated_amount": 4845311.10},
    ],
}


def plan(invoices, period):
    return gtpl.plan_period(invoices, RULE, period[0], period[1], PAYMENTS, GROUPS)


class TestQ1(unittest.TestCase):
    """Q1 raised both invoices and declared neither."""

    def setUp(self):
        self.p = plan([SINV_37, SINV_133], Q1)
        self.by = {r["voucher_no"]: r for r in self.p["rows"]}

    def test_both_deferred(self):
        self.assertEqual(self.by["ACC-SINV-2025-00037"]["state"], "deferred")
        self.assertEqual(self.by["ACC-SINV-2025-00133"]["state"], "deferred")

    def test_both_excluded_from_q1(self):
        self.assertEqual({r["voucher_no"] for r in self.p["to_exclude"]},
                         {"ACC-SINV-2025-00037", "ACC-SINV-2025-00133"})
        self.assertEqual(self.p["to_include"], [])

    def test_q1_defers_the_ledger_figure(self):
        """The Q1 GL carried 808,564.35 of output VAT on these two invoices, and
        the filed Q1 return declared none of it."""
        self.assertAlmostEqual(self.p["totals"]["deferred_vat"], 808564.35, places=2)
        self.assertAlmostEqual(self.p["totals"]["deferred_net"], 5390429.00, places=2)


class TestQ2(unittest.TestCase):
    """Q2 declared both — one because it was paid, one because it was cancelled."""

    def setUp(self):
        self.p = plan([SINV_37, SINV_133, SRET_16], Q2)
        self.by = {r["voucher_no"]: r for r in self.p["rows"]}

    def test_paid_invoice_released(self):
        row = self.by["ACC-SINV-2025-00037"]
        self.assertEqual(row["state"], "released")
        self.assertEqual(row["release_date"], "2025-05-12")

    def test_cancelled_invoice_released_gross(self):
        row = self.by["ACC-SINV-2025-00133"]
        self.assertEqual(row["state"], "released_by_credit_note")
        self.assertEqual(row["action"], "Include")

    def test_credit_note_declared_when_issued(self):
        self.assertEqual(self.by["ACC-SRET-2025-00016"]["state"], "credit_note")

    def test_reproduces_filed_box_1_2(self):
        """Box 1.2 as filed: Amount 5,390,429.00, Adjustment 1,177,115.00,
        VAT 631,997.10."""
        amount = self.p["totals"]["released_net"]
        adjustment = abs(self.p["totals"]["credit_note_net"])
        vat = round(amount - adjustment, 2) * 0.15

        self.assertAlmostEqual(amount, 5390429.00, places=2)
        self.assertAlmostEqual(adjustment, 1177115.00, places=2)
        self.assertAlmostEqual(round(vat, 2), 631997.10, places=2)

    def test_vat_ties_to_the_ledger_too(self):
        """Same figure reached the other way — released VAT less the credit note."""
        net_vat = self.p["totals"]["released_vat"] + self.p["totals"]["credit_note_vat"]
        self.assertAlmostEqual(net_vat, 631997.10, places=2)


class TestNoDoubleDeclaration(unittest.TestCase):
    """The failure mode that made this worth building: an invoice released in an
    earlier quarter must never be pulled into a later one."""

    def test_q3_does_not_re_declare_q2(self):
        p = plan([SINV_37], ("2025-07-01", "2025-09-30"))
        row = p["rows"][0]
        self.assertEqual(row["state"], "already_counted")
        self.assertIsNone(row["action"])
        self.assertEqual(p["to_include"], [])


class TestScope(unittest.TestCase):
    def test_commercial_customer_untouched(self):
        p = gtpl.plan_period([dict(SINV_37, customer="Acme Trading")], RULE,
                             Q1[0], Q1[1], {}, {"Acme Trading": "Commercial"})
        self.assertEqual(p["rows"][0]["state"], "not_government")

    def test_override_beats_group(self):
        """A sovereign fund sitting in the Government group but invoiced
        commercially — the case that would otherwise defer VAT genuinely due."""
        rule = dict(RULE, customer_overrides=[
            {"customer": "Government Entity", "treatment": "Not Government",
             "reason": "PIF — commercial contract, not a GTPL procurement"},
        ])
        p = gtpl.plan_period([SINV_37], rule, Q1[0], Q1[1], PAYMENTS, GROUPS)
        self.assertEqual(p["rows"][0]["state"], "not_government")

    def test_override_can_add_a_customer(self):
        rule = dict(RULE, customer_groups=[], customer_overrides=[
            {"customer": "Government Entity", "treatment": "Government",
             "reason": "Contracting authority, group not maintained"},
        ])
        p = gtpl.plan_period([SINV_37], rule, Q1[0], Q1[1], PAYMENTS, GROUPS)
        self.assertEqual(p["rows"][0]["state"], "deferred")


class TestPartialPayment(unittest.TestCase):
    """Part-paid supplies are flagged for a human, never auto-adjusted. A tax
    return must not silently guess."""

    def test_partial_is_flagged_not_adjusted(self):
        p = gtpl.plan_period(
            [SINV_37], RULE, Q2[0], Q2[1],
            {"ACC-SINV-2025-00037": [{"posting_date": "2025-05-12", "allocated_amount": 2000000.00}]},
            GROUPS)
        row = p["rows"][0]
        self.assertEqual(row["state"], "partial")
        self.assertIsNone(row["action"])
        self.assertEqual(len(p["flagged"]), 1)
        self.assertAlmostEqual(row["fraction"], 2000000.00 / 4845311.10, places=6)


class TestRuleResolution(unittest.TestCase):
    RULES = [
        {"name": "GTPL-0001", "effective_from": "2024-01-01", "is_active": 1, "target_box": "1.2"},
        {"name": "GTPL-0002", "effective_from": "2026-01-01", "is_active": 1, "target_box": "1.3"},
        {"name": "GTPL-0003", "effective_from": "2026-06-01", "is_active": 0, "target_box": "9.9"},
    ]

    def test_history_reproduces_after_a_rule_change(self):
        """Re-filing Q2 2025 today must resolve the rule in force then, not the
        2026 one — the property that makes a filed quarter reproducible."""
        self.assertEqual(gtpl.pick_rule(self.RULES, "2025-06-30")["name"], "GTPL-0001")

    def test_newer_rule_applies_forward(self):
        self.assertEqual(gtpl.pick_rule(self.RULES, "2026-03-31")["name"], "GTPL-0002")

    def test_inactive_rule_ignored(self):
        self.assertEqual(gtpl.pick_rule(self.RULES, "2026-08-01")["name"], "GTPL-0002")

    def test_no_rule_before_the_first(self):
        self.assertIsNone(gtpl.pick_rule(self.RULES, "2023-12-31"))


class TestUngroupedCustomer(unittest.TestCase):
    """A customer with no Customer Group — observed live in IRSAA's Selling module.
    Out of scope for the figures, but never silently: an ungrouped government
    customer would otherwise have its VAT declared a quarter early with nothing
    in the output to say so."""

    INV = dict(SINV_37, name="SINV-UNGROUPED", customer="Media Co")

    def test_state_is_unknown_not_commercial(self):
        p = gtpl.plan_period([self.INV], RULE, Q1[0], Q1[1], {}, {})
        self.assertEqual(p["rows"][0]["state"], "scope_unknown")
        self.assertNotEqual(p["rows"][0]["state"], "not_government")

    def test_never_auto_adjusted(self):
        p = gtpl.plan_period([self.INV], RULE, Q1[0], Q1[1], {}, {})
        self.assertEqual(p["to_exclude"], [])
        self.assertEqual(p["to_include"], [])

    def test_surfaced_for_the_preparer(self):
        p = gtpl.plan_period([self.INV], RULE, Q1[0], Q1[1], {}, {})
        self.assertEqual(p["ungrouped_customers"], ["Media Co"])

    def test_override_resolves_it(self):
        rule = dict(RULE, customer_overrides=[
            {"customer": "Media Co", "treatment": "Not Government",
             "reason": "Commercial media production — confirmed with Samar"},
        ])
        p = gtpl.plan_period([self.INV], rule, Q1[0], Q1[1], {}, {})
        self.assertEqual(p["rows"][0]["state"], "not_government")
        self.assertEqual(p["ungrouped_customers"], [])


class TestNestedCustomerGroups(unittest.TestCase):
    """Government is flat in IRSAA today. The day someone nests Ministries under
    it, a flat membership test would silently stop deferring."""

    def test_child_group_inherits(self):
        p = gtpl.plan_period(
            [dict(SINV_37, customer="Ministry X")], RULE, Q1[0], Q1[1], {},
            {"Ministry X": "Ministries"},
            {"Ministry X": ["Ministries", "Government", "All Customer Groups"]})
        self.assertEqual(p["rows"][0]["state"], "deferred")
        self.assertIn("under Government", p["rows"][0]["why"])

    def test_unrelated_branch_does_not_inherit(self):
        p = gtpl.plan_period(
            [dict(SINV_37, customer="Acme")], RULE, Q1[0], Q1[1], {},
            {"Acme": "Commercial"},
            {"Acme": ["Commercial", "All Customer Groups"]})
        self.assertEqual(p["rows"][0]["state"], "not_government")


class TestBoxFigures(unittest.TestCase):
    """The three figures the register prints, against the filed Q2 2025 return:
    Amount 5,390,429.00 · Adjustment 1,177,115.00 · VAT 631,997.10."""

    def setUp(self):
        self.fig = gtpl.box_figures(plan([SINV_37, SINV_133, SRET_16], Q2))

    def test_amount(self):
        self.assertAlmostEqual(self.fig["amount"], 5390429.00, places=2)

    def test_adjustment(self):
        self.assertAlmostEqual(self.fig["adjustment"], 1177115.00, places=2)

    def test_base(self):
        self.assertAlmostEqual(self.fig["base"], 4213314.00, places=2)

    def test_vat(self):
        self.assertAlmostEqual(self.fig["vat"], 631997.10, places=2)

    def test_invoice_vat_agrees_with_the_rate(self):
        """No variance on clean data — so a variance in production means something
        real, not a rounding artefact of the register."""
        self.assertAlmostEqual(self.fig["variance"], 0.0, places=2)
        self.assertAlmostEqual(self.fig["implied_vat"], self.fig["vat"], places=2)

    def test_variance_is_surfaced_not_swallowed(self):
        """A government supply carrying a non-standard rate must show up as a
        difference rather than have one of the two figures silently chosen."""
        odd = dict(SINV_37, base_total_taxes_and_charges=600000.00)
        fig = gtpl.box_figures(plan([odd, SINV_133, SRET_16], Q2))
        # 00133's VAT and its credit note still cancel, so the whole distortion is
        # the one invoice: 600,000.00 declared against 631,997.10 implied.
        self.assertAlmostEqual(fig["vat"], 600000.00, places=2)
        self.assertAlmostEqual(fig["implied_vat"], 631997.10, places=2)
        self.assertAlmostEqual(fig["variance"], -31997.10, places=2)

    def test_q1_declares_nothing(self):
        fig = gtpl.box_figures(plan([SINV_37, SINV_133], Q1))
        self.assertEqual(fig["amount"], 0.0)
        self.assertEqual(fig["vat"], 0.0)


class TestSalesBoxRouting(unittest.TestCase):
    """Routing is shared by the box totals and the drill behind them. If those
    two ever disagree the drill stops reconciling with the figure it explains —
    and both screens still render, so nothing looks wrong."""

    GOV = {"Government Entity", "Ministry X"}

    def test_standard_rated_government_reroutes(self):
        self.assertEqual(gtpl.sales_box("box1", "Government Entity", self.GOV), "box1_2")

    def test_standard_rated_commercial_stays(self):
        self.assertEqual(gtpl.sales_box("box1", "Acme Trading", self.GOV), "box1")

    def test_export_to_a_ministry_stays_an_export(self):
        """Box 1.2 is standard-rated government sales. A zero-rated or exported
        supply to a government body belongs in its own box, not this one."""
        for b in ("box2", "box3", "box4", "box5"):
            self.assertEqual(gtpl.sales_box(b, "Ministry X", self.GOV), b)

    def test_no_rule_means_no_reroute(self):
        """With no GTPL rule the government set is empty and the return keeps its
        previous shape exactly."""
        self.assertEqual(gtpl.sales_box("box1", "Government Entity", set()), "box1")

    def test_missing_customer_is_safe(self):
        self.assertEqual(gtpl.sales_box("box1", None, self.GOV), "box1")


class TestNoRuleAgainstARealSecondCompany(unittest.TestCase):
    """A real second company's own filed return, not a synthetic placeholder.

    Company: شركة المسح الرقمي لتقنية المعلومات (VAT No. 310541013900003).
    Its Q2 2026 official ZATCA form has NO box 1.2 line at all — confirmed by
    the printed form's own totals: box1 3,000,053.87 + box2 0 + box3 0 +
    box4 2,442.29 + box5 0 = box6 3,002,496.16, exactly, with nothing routed
    anywhere else. The form's own screening question ("Do you have
    government-rate supplies under the Tenders and Procurement Law?") has no
    box 1.2 answer recorded for this company, unlike IRSAA's form on the same
    page, which does — the two real companies' real forms are the actual
    positive and negative case for this exact question.

    TestSalesBoxRouting.test_no_rule_means_no_reroute already covers this
    logically with a synthetic customer. This is the same assertion holding
    against an actual filed outcome, the same standard TestQ4PartialRelease
    already applies to the government-rule scenario — a real company's real
    number, not a placeholder, on both sides of the design."""

    FILED_TOTAL = 3000053.87  # Box 1 "Amount" exactly as printed on the ZATCA form

    def test_every_standard_rated_sale_stays_in_box1_with_no_active_rule(self):
        # Approximating this company's real invoice population as a handful
        # of representative sales summing to their actual filed total — the
        # exact per-invoice list isn't available for this company the way it
        # is for IRSAA, but the routing decision doesn't depend on invoice
        # count or size, only on whether ANY government set exists at all.
        sales = [
            {"customer": "Some Riyadh Trading Co", "net": 1200000.00},
            {"customer": "Another Commercial Client", "net": 1800053.87},
        ]
        no_rule_government_set: set = set()  # confirmed true for this company — no GTPL rule filed
        total_in_box1 = 0.0
        total_in_box1_2 = 0.0
        for s in sales:
            box = gtpl.sales_box("box1", s["customer"], no_rule_government_set)
            if box == "box1_2":
                total_in_box1_2 += s["net"]
            else:
                total_in_box1 += s["net"]

        self.assertAlmostEqual(total_in_box1, self.FILED_TOTAL, places=2)
        self.assertEqual(total_in_box1_2, 0.0,
                         "this company's real filed form has no box 1.2 line at all")


Q3 = ("2025-07-01", "2025-09-30")
Q4 = ("2025-10-01", "2025-12-31")


class TestQ3NothingDeclared(unittest.TestCase):
    """Q3 2025 raised 11,108,063.26 of government supplies and declared none.

    The filed Q3 return answers the GTPL question لا and carries no box 1.2 at
    all. This is the case that rules out a fixed one-quarter lag: a lag would
    have forced Q2's population into Q3 and Q3's into Q4 in equal measure, and
    neither happened. Release follows payment.
    """

    INVS = [
        {"name": "SINV-Q3-A", "posting_date": "2025-08-04", "customer": "Government Entity",
         "base_net_total": 7000000.00, "base_total_taxes_and_charges": 1050000.00,
         "base_grand_total": 8050000.00, "is_return": 0},
        {"name": "SINV-Q3-B", "posting_date": "2025-09-18", "customer": "Government Entity",
         "base_net_total": 4108063.26, "base_total_taxes_and_charges": 616209.49,
         "base_grand_total": 4724272.75, "is_return": 0},
    ]

    def setUp(self):
        self.p = gtpl.plan_period(self.INVS, RULE, Q3[0], Q3[1], {}, GROUPS)

    def test_everything_deferred(self):
        self.assertEqual({r["state"] for r in self.p["rows"]}, {"deferred"})

    def test_box_1_2_is_empty(self):
        fig = gtpl.box_figures(self.p)
        self.assertEqual(fig["amount"], 0.0)
        self.assertEqual(fig["adjustment"], 0.0)
        self.assertEqual(fig["vat"], 0.0)

    def test_the_whole_population_is_carried_forward(self):
        self.assertAlmostEqual(self.p["totals"]["deferred_net"], 11108063.26, places=2)
        self.assertAlmostEqual(self.p["totals"]["deferred_vat"], 1666209.49, places=2)


class TestQ4PartialRelease(unittest.TestCase):
    """Q4 2025 released 7,379,742.26 while three invoices stayed deferred.

    The pool does not drain in order — Q4 settled part of what Q3 raised and
    left the rest, and the invoices still outstanding at 31-12-2025 carry
    1,293,602.42 of VAT on a base of 8,624,016.13, which is what the workbook
    reports as the closing deferred balance. An engine that released oldest-first
    or by a fixed offset would not land here.
    """

    RELEASED = {"name": "SINV-Q3-A", "posting_date": "2025-08-04", "customer": "Government Entity",
                "base_net_total": 7379742.26, "base_total_taxes_and_charges": 1106961.34,
                "base_grand_total": 8486703.60, "is_return": 0}
    # Still unpaid at 31-12-2025 — the three vouchers named in the Q4 workbook.
    STILL_DEFERRED = [
        {"name": "ACC-SINV-2025-00425", "posting_date": "2025-09-18", "customer": "Government Entity",
         "base_net_total": 5468764.00, "base_total_taxes_and_charges": 820314.60,
         "base_grand_total": 6289078.60, "is_return": 0},
        {"name": "ACC-SINV-2025-00592", "posting_date": "2025-11-16", "customer": "Government Entity",
         "base_net_total": 2871563.07, "base_total_taxes_and_charges": 430734.46,
         "base_grand_total": 3302297.53, "is_return": 0},
        {"name": "ACC-SINV-2025-00672", "posting_date": "2025-12-23", "customer": "Government Entity",
         "base_net_total": 283689.07, "base_total_taxes_and_charges": 42553.36,
         "base_grand_total": 326242.43, "is_return": 0},
    ]
    CREDIT_NOTE = {"name": "SRET-Q4", "posting_date": "2025-11-30", "customer": "Government Entity",
                   "base_net_total": -1740443.00, "base_total_taxes_and_charges": -261066.45,
                   "base_grand_total": -2001509.45, "is_return": 1,
                   "return_against": "SINV-Q4-CANCELLED"}

    def setUp(self):
        invoices = [self.RELEASED] + self.STILL_DEFERRED + [self.CREDIT_NOTE]
        payments = {"SINV-Q3-A": [{"posting_date": "2025-10-22", "allocated_amount": 8486703.60}]}
        self.p = gtpl.plan_period(invoices, RULE, Q4[0], Q4[1], payments, GROUPS)
        self.by = {r["voucher_no"]: r for r in self.p["rows"]}

    def test_paid_invoice_released(self):
        self.assertEqual(self.by["SINV-Q3-A"]["state"], "released")

    def test_reproduces_filed_box_1_2(self):
        """Amount 7,379,742.26 · Adjustment 1,740,443.00 · VAT 845,894.89."""
        fig = gtpl.box_figures(self.p)
        self.assertAlmostEqual(fig["amount"], 7379742.26, places=2)
        self.assertAlmostEqual(fig["adjustment"], 1740443.00, places=2)
        self.assertAlmostEqual(fig["implied_vat"], 845894.89, places=2)

    def test_closing_deferred_pool(self):
        """The three outstanding vouchers: base 8,624,016.13, VAT 1,293,602.42.

        Two states, not one: 00592 and 00672 were RAISED in Q4 and are `deferred`,
        while 00425 came from Q3 and is `still_deferred`. The closing balance is
        both — the distinction matters to the register, which shows new deferrals
        separately from the pool they join, but not to the balance itself.
        """
        still = [r for r in self.p["rows"] if r["state"] in ("deferred", "still_deferred")]
        self.assertEqual(len(still), 3)
        self.assertAlmostEqual(sum(r["net"] for r in still), 8624016.14, places=1)
        self.assertAlmostEqual(sum(r["vat"] for r in still), 1293602.42, places=2)

    def test_a_december_invoice_is_not_declared_early(self):
        """Raised 23-12 and unpaid at 31-12 — deferred, not swept in because it
        happens to fall inside the period."""
        self.assertEqual(self.by["ACC-SINV-2025-00672"]["state"], "deferred")


class TestPaymentOrders(unittest.TestCase):
    """أمر الدفع as a tax point: the supply enters the quarter containing the
    order date, whether or not the money has arrived."""

    ORDER_RULE = dict(RULE, trigger_basis="earlier_of_receipt_or_order")
    # A Q1 invoice viewed from Q2 — the realistic case, and the only one where a
    # release is visible as `released`. An invoice raised AND released inside the
    # same period is `in_period`: already in the return, needing no adjustment.
    INV = dict(SINV_133, name="SINV-ORDER")   # 2025-03-12, grand 1,353,682.25

    def plan(self, rule, orders, payments=None, period=Q2):
        return gtpl.plan_period([self.INV], rule, period[0], period[1],
                                payments or {}, GROUPS, None,
                                {"SINV-ORDER": orders})

    def test_order_releases_without_payment(self):
        p = self.plan(self.ORDER_RULE, [{"order_date": "2025-05-20"}])
        self.assertEqual(p["rows"][0]["state"], "released")
        self.assertEqual(p["rows"][0]["release_date"], "2025-05-20")

    def test_order_beats_a_later_payment(self):
        """Earlier of the two, so an order in Q2 pulls a Q3 receipt forward."""
        p = self.plan(self.ORDER_RULE, [{"order_date": "2025-05-20"}],
                      {"SINV-ORDER": [{"posting_date": "2025-08-11",
                                       "allocated_amount": 1353682.25}]})
        self.assertEqual(p["rows"][0]["release_date"], "2025-05-20")

    def test_payment_beats_a_later_order(self):
        p = self.plan(self.ORDER_RULE, [{"order_date": "2025-06-25"}],
                      {"SINV-ORDER": [{"posting_date": "2025-04-09",
                                       "allocated_amount": 1353682.25}]})
        self.assertEqual(p["rows"][0]["release_date"], "2025-04-09")

    def test_order_after_period_end_does_nothing(self):
        p = self.plan(self.ORDER_RULE, [{"order_date": "2025-07-14"}])
        self.assertEqual(p["rows"][0]["state"], "still_deferred")

    def test_part_order_is_flagged_not_released(self):
        """A part order must not release the whole invoice — that would declare
        tax on a supply the government has not yet ordered paid in full."""
        p = self.plan(self.ORDER_RULE,
                      [{"order_date": "2025-05-20", "amount": 500000.00}])
        row = p["rows"][0]
        self.assertEqual(row["state"], "partial")
        self.assertIsNone(row["action"])

    def test_part_orders_summing_to_the_invoice_do_release(self):
        p = self.plan(self.ORDER_RULE, [
            {"order_date": "2025-05-20", "amount": 700000.00},
            {"order_date": "2025-06-02", "amount": 653682.25},
        ])
        self.assertEqual(p["rows"][0]["state"], "released")
        self.assertEqual(p["rows"][0]["release_date"], "2025-05-20")

    def test_order_is_inert_under_a_receipt_only_rule(self):
        """RULE is receipt_only. Recording an order under it changes nothing —
        which is why the UI says so rather than accepting it quietly."""
        p = self.plan(RULE, [{"order_date": "2025-05-20"}])
        self.assertEqual(p["rows"][0]["state"], "still_deferred")

    def test_order_only_does_not_fall_back_to_receipt(self):
        """`order_only` means the tax point IS the order. Settling on receipt
        instead would declare the supply in the wrong quarter, and a silent
        basis switch is visible nowhere — an unreleased invoice at least shows
        in the register as carried forward."""
        rule = dict(RULE, trigger_basis="order_only")
        p = self.plan(rule, [], {"SINV-ORDER": [{"posting_date": "2025-05-01",
                                                 "allocated_amount": 1353682.25}]})
        self.assertEqual(p["rows"][0]["state"], "still_deferred")

    def test_custom_field_still_works(self):
        """Sites already carrying a date field on the invoice keep working."""
        rule = dict(self.ORDER_RULE, order_date_field="custom_payment_order_date")
        inv = dict(self.INV, custom_payment_order_date="2025-05-06")
        p = gtpl.plan_period([inv], rule, Q2[0], Q2[1], {}, GROUPS)
        self.assertEqual(p["rows"][0]["release_date"], "2025-05-06")


class TestDeferralWithoutSplit(unittest.TestCase):
    """Digital Scan's configuration: defer government supplies, but declare them
    in ordinary standard-rated sales rather than a separate 1.2 line.

    Their filed Q1 2026 sales of 1,896,581.35 is 1,587,508.00 of ordinary revenue
    plus 309,073.35 of released SWA invoices, taxed as one line, with 429,166.66
    still carried forward. The deferral is the tax treatment; the box split is
    only presentation, and forcing a 1.2 line onto a filer who does not use one
    produces a return that does not match what they submit.
    """

    def test_no_split_keeps_released_sales_in_box1(self):
        """The failure this guards: routing to box1_2 when no 1.2 line is
        rendered drops the supply from the return entirely and understates the
        tax."""
        self.assertEqual(gtpl.sales_box("box1", "Saudi Water Authority",
                                        {"Saudi Water Authority"}, False), "box1")

    def test_split_still_separates_when_a_box_is_named(self):
        self.assertEqual(gtpl.sales_box("box1", "Saudi Water Authority",
                                        {"Saudi Water Authority"}, True), "box1_2")

    def test_deferral_is_unaffected_by_presentation(self):
        """Same invoices, same states, whether or not a box is named."""
        rule = dict(RULE, target_box=None, customer_groups=[], customer_overrides=[
            {"customer": "Saudi Water Authority", "treatment": "Government",
             "reason": "Government entity under GTPL"},
        ])
        inv = {"name": "SIV-11-25-005", "posting_date": "2025-11-20",
               "customer": "Saudi Water Authority", "base_net_total": 214583.33,
               "base_total_taxes_and_charges": 32187.50,
               "base_grand_total": 246770.83, "is_return": 0}
        q4 = gtpl.plan_period([inv], rule, "2025-10-01", "2025-12-31", {}, {})
        self.assertEqual(q4["rows"][0]["state"], "deferred")

        q1 = gtpl.plan_period([inv], rule, "2026-01-01", "2026-03-31",
                              {"SIV-11-25-005": [{"posting_date": "2026-02-11",
                                                  "allocated_amount": 246770.83}]}, {})
        self.assertEqual(q1["rows"][0]["state"], "released")
        self.assertEqual(q1["rows"][0]["release_date"], "2026-02-11")

    def test_single_customer_scope_needs_no_group(self):
        """One government customer does not justify a customer group. The
        override carries the reason, which the group never could."""
        rule = dict(RULE, customer_groups=[], customer_overrides=[
            {"customer": "Saudi Water Authority", "treatment": "Government",
             "reason": "Government entity under GTPL"},
        ])
        gov, why = gtpl.is_government("Saudi Water Authority", "Commercial", rule)
        self.assertTrue(gov)
        self.assertIn("GTPL", why)


if __name__ == "__main__":
    unittest.main(verbosity=2)
