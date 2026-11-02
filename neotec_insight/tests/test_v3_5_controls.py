"""v3.5.0 — Insight Controls: lifecycle, detection and the runner."""
import datetime as dt
import sys
import types
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[0] / "neotec_insight" / "utils"))
sys.path.insert(0, str(HERE.parents[1]))
sys.path.insert(0, str(HERE))
import controls_core as c  # noqa: E402
import _fakefrappe as ff  # noqa: E402


class Lifecycle(unittest.TestCase):
    def test_every_path(self):
        existing = {"new?": None, "a": c.OPEN, "b": c.ACK, "f": c.FIXED, "d": c.DISMISSED, "gone": c.OPEN, "stale": c.OPEN}
        existing.pop("new?")
        p = c.plan(existing, {"n", "a", "f", "d"}, self_closing=False, gone={"gone"})
        self.assertEqual(p["create"], ["n"])
        self.assertEqual(sorted(p["touch"]), ["a", "d", "f"])            # Fixed stays fixed for non-self-closing
        self.assertEqual(p["reopen"], [])
        self.assertEqual(p["close"], ["gone"])                             # document cancelled → closed
        p = c.plan(existing, {"f", "d"}, self_closing=True)
        self.assertEqual(p["reopen"], ["f"])                                # came back → reopened
        self.assertIn("d", p["touch"])                                      # a person's dismissal is kept
        self.assertEqual(sorted(p["close"]), ["a", "b", "gone", "stale"])  # cause disappeared → fixed


class Detection(unittest.TestCase):
    def test_same_amount_within_days(self):
        rows = [{"name": "PI-1", "supplier": "S", "amount": 1000, "posting_date": dt.date(2026, 1, 1)},
                {"name": "PI-2", "supplier": "S", "amount": 1000, "posting_date": dt.date(2026, 1, 3)},
                {"name": "PI-3", "supplier": "S", "amount": 1000, "posting_date": dt.date(2026, 2, 1)},
                {"name": "PI-4", "supplier": "T", "amount": 1000, "posting_date": dt.date(2026, 1, 2)}]
        pairs = [(a["name"], b["name"]) for a, b in c.same_amount_within(rows, "supplier", 3)]
        self.assertEqual(pairs, [("PI-1", "PI-2")])

    def test_bill_number_twins_ignore_case_and_blanks(self):
        rows = [{"name": "PI-1", "supplier": "S", "bill_no": "INV-9", "posting_date": "2026-01-01"},
                {"name": "PI-2", "supplier": "S", "bill_no": "inv-9 ", "posting_date": "2026-03-01"},
                {"name": "PI-3", "supplier": "S", "bill_no": "", "posting_date": "2026-03-01"}]
        self.assertEqual([(a["name"], b["name"]) for a, b in c.same_bill_no(rows)], [("PI-1", "PI-2")])

    def test_rate_outliers(self):
        out = c.rate_outliers([{"item_code": "X", "warehouse": "W", "incoming_rate": 160},
                               {"item_code": "X", "warehouse": "W", "incoming_rate": 120},
                               {"item_code": "Y", "warehouse": "W", "incoming_rate": 5}], {("X", "W"): 100.0}, 50)
        self.assertEqual([(o["incoming_rate"], o["diff_pct"]) for o in out], [(160, 60.0)])

    def test_pair_key_is_order_independent(self):
        self.assertEqual(c.pair_key("R", "B", "A"), c.pair_key("R", "A", "B"))

    def test_catalogue_complete(self):
        src = (HERE.parents[0] / "neotec_insight" / "api" / "controls.py").read_text()
        for code, spec in c.CATALOGUE.items():
            self.assertIn(f'"{code}": c_', src)
            self.assertIn(spec["severity"], ("High", "Medium", "Low"))


class Runner(unittest.TestCase):
    def setUp(self):
        self.site = ff.install()
        import frappe
        self.frappe = frappe
        frappe.log_error = lambda *a, **k: None
        self.site.table("Company")["Alpha Co"] = {"name": "Alpha Co"}
        from neotec_insight.neotec_insight.api import controls
        self.ctl = controls
        self.pi = [{"name": "PI-1", "supplier": "Gulf", "bill_no": "B-77", "posting_date": dt.date(2026, 10, 1), "amount": 5000.0},
                   {"name": "PI-2", "supplier": "Gulf", "bill_no": "B-77", "posting_date": dt.date(2026, 10, 20), "amount": 5100.0}]
        self.bins = [{"item_code": "WATER-330", "warehouse": "Main - A", "actual_qty": -12}]

        def sql(q, params=None, as_dict=False):
            if "tabPurchase Invoice" in q:
                return [dict(r) for r in self.pi]
            if "tabBin" in q:
                return [dict(r) for r in self.bins]
            return []
        frappe.db.sql = sql
        frappe.db.commit = lambda: None
        frappe.db.rollback = lambda: None
        for code in c.CATALOGUE:
            self.site.metas.setdefault(code, {"name": code, "fields": []})

    def tearDown(self):
        ff.uninstall()

    def findings(self):
        return {r["finding_key"]: r for r in self.site.table("Insight Control Finding").values()}

    def test_run_twice_creates_once_and_closes_fixed_stock(self):
        s1 = self.ctl.run_all(["Alpha Co"])
        self.assertEqual(s1["errors"], [])
        f = self.findings()
        self.assertEqual(s1["new"], 2)                                    # bill-number twin + negative stock
        self.assertIn(c.pair_key("DUP_PURCHASE_INVOICE", "PI-1", "PI-2"), f)
        neg = "NEGATIVE_STOCK:WATER-330:Main - A"
        self.assertEqual(f[neg]["status"], c.OPEN)
        self.assertEqual(len(self.site.table("Insight Control Rule")), len(c.CATALOGUE))   # rules seeded
        self.assertEqual(self.ctl.run_all(["Alpha Co"])["new"], 0)       # nothing raised twice
        self.bins = []                                                    # stock corrected
        self.ctl.run_all(["Alpha Co"])
        self.assertEqual(self.findings()[neg]["status"], c.FIXED)
        self.bins = [{"item_code": "WATER-330", "warehouse": "Main - A", "actual_qty": -1}]
        self.ctl.run_all(["Alpha Co"])
        self.assertEqual(self.findings()[neg]["status"], c.OPEN)         # came back → reopened

    def test_a_failing_check_does_not_stop_the_others(self):
        self.ctl.CHECKS["DUP_SALES_INVOICE"] = lambda company, rule: 1 / 0
        try:
            s = self.ctl.run_all(["Alpha Co"])
        finally:
            self.ctl.CHECKS["DUP_SALES_INVOICE"] = self.ctl.c_dup_sales_invoice
        self.assertEqual(len(s["errors"]), 1)
        self.assertEqual(s["new"], 2)

    def test_status_changes_need_a_manager(self):
        self.ctl.run_all(["Alpha Co"])
        name = next(iter(self.site.table("Insight Control Finding")))
        self.site.user = "clerk@a.co"
        self.site.roles["clerk@a.co"] = {"Accounts User"}
        with self.assertRaises(self.frappe.PermissionError):
            self.ctl.set_status([name], c.DISMISSED, "ok")
        self.site.user = "fm@a.co"
        self.site.roles["fm@a.co"] = {"Accounts Manager"}
        self.ctl.set_status([name], c.DISMISSED, "Known supplier re-billing")
        rec = self.site.table("Insight Control Finding")[name]
        self.assertEqual((rec["status"], rec["note"], rec["resolved_by"]), (c.DISMISSED, "Known supplier re-billing", "fm@a.co"))


class Copilot(unittest.TestCase):
    def test_routing(self):
        import copilot_core as cc
        for q in ("show open findings", "any duplicate invoices?", "negative stock", "فواتير مكررة"):
            self.assertEqual(cc.validate_plan(cc.rule_plan(q))["tool"], "control_findings", q)


if __name__ == "__main__":
    unittest.main()
