"""v2.97.0 — Analysis Pack: the finance in each analysis, then the endpoints."""
import datetime as dt
import sys
import types
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[0] / "neotec_insight" / "utils"))
sys.path.insert(0, str(HERE.parents[1]))
sys.path.insert(0, str(HERE))
import analysis_core as ac  # noqa: E402
import copilot_core as core  # noqa: E402
import _fakefrappe as ff  # noqa: E402


class Finance(unittest.TestCase):
    def test_bridge_adds_up_to_net_profit(self):
        d = ac.profit_bridge(1000, 400, [("Salaries", 300), ("Rent", 100), ("Travel", 50)])
        steps = {r["step"]: r["amount"] for r in d["rows"]}
        self.assertEqual(steps["Gross profit"], 600)
        self.assertEqual(steps["Net profit"], 150)
        self.assertEqual(sum(r["amount"] for r in d["rows"] if r["kind"] == "delta") + 1000, 150)
        bars = d["chart"]["series"][0]["data"]
        self.assertEqual(bars[1], [600, 1000])          # cost of sales hangs from revenue
        self.assertEqual(bars[-1], [0, 150])
        self.assertEqual(d["figures"][1]["value"], 15.0)

    def test_bridge_groups_small_items(self):
        d = ac.profit_bridge(100, 0, [(f"g{i}", 1) for i in range(10)], max_groups=3)
        self.assertIn("Other expenses", [r["step"] for r in d["rows"]])
        self.assertEqual(d["rows"][-1]["amount"], 90)

    def test_common_size(self):
        d = ac.common_size([{"line": "COGS", "now": 400, "then": 300}], 1000, 1000, "k", "t", "revenue")
        self.assertEqual((d["rows"][0]["now_pct"], d["rows"][0]["change_pts"]), (40.0, 10.0))

    def test_variance_signs(self):
        d = ac.variance_drivers([{"account": "Sales", "root_type": "Income", "now": 120, "then": 100},
                                 {"account": "Rent", "root_type": "Expense", "now": 60, "then": 50}])
        impact = {r["account"]: r["profit_impact"] for r in d["rows"]}
        self.assertEqual(impact, {"Sales": 20, "Rent": -10})
        self.assertEqual(d["figures"][0]["value"], 10)

    def test_working_capital_days(self):
        p = ac.wc_point(receivables=100, payables=50, inventory=25, revenue_12m=365, cogs_12m=365)
        self.assertEqual(p, {"dso": 100.0, "dpo": 50.0, "dio": 25.0, "ccc": 75.0})
        self.assertIsNone(ac.wc_point(1, 1, 1, 0, 0)["dso"])

    def test_dupont_identity(self):
        d = ac.dupont(net_income=100, revenue=1000, assets=2000, equity=500)
        self.assertEqual((d["net_margin"], d["asset_turnover"], d["equity_multiplier"]), (10.0, 0.5, 4.0))
        self.assertEqual(d["roe"], 20.0)                # 100 / 500
        self.assertIsNone(ac.dupont(1, 0, 1, 1)["roe"])

    def test_pareto_and_hhi(self):
        d = ac.pareto([("A", 70), ("B", 20), ("C", 10)], "k", "t", "Customers")
        figs = {f["label"]: f["value"] for f in d["figures"]}
        self.assertEqual(figs["Customers making 80%"], 2)
        self.assertEqual(figs["Concentration (HHI)"], 70 ** 2 + 20 ** 2 + 10 ** 2)
        self.assertIn("high", d["notes"][0])
        self.assertEqual(d["rows"][-1]["cumulative"], 100.0)

    def test_anomaly_rules(self):
        typ = {"Cash": 1000.0}
        fri = dt.date(2026, 10, 16)              # Friday
        sun = dt.date(2026, 10, 18)              # Sunday — a working day in KSA
        r = ac.anomaly_reasons({"amount": 50000, "posting_date": fri, "account": "Cash"}, typ, {"2026-10-16"})
        self.assertEqual(len(r), 4)
        self.assertIn("50× the usual size for this account", r)
        self.assertEqual(ac.anomaly_reasons({"amount": 1234.56, "posting_date": sun, "account": "Cash"}, typ, set()), [])
        self.assertEqual(ac.anomaly_reasons({"amount": 5000, "posting_date": sun, "account": "Cash"}, typ, set()), [])

    def test_anomaly_dataset_ranks_multi_reason_first(self):
        hist = [{"account": "Cash", "amount": 100} for _ in range(10)]
        entries = [{"voucher_no": "JV1", "posting_date": "2026-10-18", "account": "Cash", "amount": 20000},
                   {"voucher_no": "JV2", "posting_date": "2026-10-16", "account": "Cash", "amount": 50000}]
        d = ac.gl_anomalies(entries, hist, {"2026-10-31"})
        self.assertEqual(d["rows"][0]["voucher_no"], "JV2")
        self.assertEqual(d["figures"][1]["value"], 2)

    def test_month_ends(self):
        self.assertEqual([str(x) for x in ac.month_ends(dt.date(2026, 1, 15), dt.date(2026, 3, 10))],
                         ["2026-01-31", "2026-02-28", "2026-03-10"])


class Endpoints(unittest.TestCase):
    def setUp(self):
        self.site = ff.install()
        import frappe
        self.frappe = frappe
        self.site.table("Company")["Alpha Co"] = {"name": "Alpha Co"}
        self.site.metas.setdefault("Company", {"name": "Company", "fields": []})
        frappe.defaults = types.SimpleNamespace(get_user_default=lambda k: "Alpha Co", get_global_default=lambda k: "Alpha Co")
        from neotec_insight.neotec_insight.api import analysis_pack as ap
        self.ap = ap
        base = dict(assets=2000.0, liabilities=1500.0, equity=0.0, revenue=1000.0, expenses=850.0, cogs=400.0,
                    current_assets=1200.0, non_current_assets=800.0, inventory=100.0, cash=300.0, receivables=500.0,
                    payables=200.0, current_liabilities=900.0, net_income=150.0, gross_profit=600.0)
        ap._metrics = lambda c, f, t: dict(base)
        self.rows = [{"account": "Sales", "root_type": "Income", "parent": "Income - A", "account_type": "", "amount": 1000.0},
                     {"account": "COGS", "root_type": "Expense", "parent": "Direct - A", "account_type": "Cost of Goods Sold", "amount": 400.0},
                     {"account": "Salaries", "root_type": "Expense", "parent": "Staff Costs - A", "account_type": "", "amount": 300.0},
                     {"account": "Rent", "root_type": "Expense", "parent": "Premises - A", "account_type": "", "amount": 150.0}]
        def sql(q, *a, **k):
            if "tabSales Invoice" in q:
                return [{"customer": "Acme", "amount": 700.0}, {"customer": "Beta", "amount": 300.0}]
            if "Journal Entry" in q:
                return [{"voucher_no": "JV1", "posting_date": "2026-03-27", "account": "Cash", "amount": 25000.0}]
            return [dict(r) for r in self.rows]
        frappe.db.sql = sql

    def tearDown(self):
        ff.uninstall()

    def test_every_analysis_runs_and_has_the_standard_shape(self):
        for a in self.ap.list_analyses():
            d = self.ap.run_analysis(a["key"], from_date="2026-01-01", to_date="2026-06-30")
            for k in ("key", "title", "figures", "columns", "rows", "chart", "notes", "from_date", "compare_to"):
                self.assertIn(k, d, f"{a['key']}: {k}")

    def test_bridge_uses_expense_groups_not_cogs(self):
        d = self.ap.run_analysis("profit_bridge", from_date="2026-01-01", to_date="2026-06-30")
        steps = [r["step"] for r in d["rows"]]
        self.assertIn("Staff Costs", steps)
        self.assertNotIn("Direct", steps)

    def test_comparison_periods(self):
        d = self.ap.run_analysis("dupont", from_date="2026-01-01", to_date="2026-06-30")
        self.assertEqual((d["compare_from"], d["compare_to"]), ("2025-01-01", "2025-06-30"))
        d = self.ap.run_analysis("dupont", from_date="2026-04-01", to_date="2026-06-30", compare="prior_period")
        self.assertEqual((d["compare_from"], d["compare_to"]), ("2025-12-31", "2026-03-31"))

    def test_people_only_refused(self):
        self.site.user = "hr@a.co"
        self.site.roles["hr@a.co"] = {"HR Manager"}
        with self.assertRaises(self.frappe.PermissionError):
            self.ap.run_analysis("profit_bridge")

    def test_unknown_analysis(self):
        with self.assertRaises(self.frappe.ValidationError):
            self.ap.run_analysis("drop_tables")


class CopilotRouting(unittest.TestCase):
    def test_questions_reach_the_analyses(self):
        for q, tool in (("Show the profit bridge", "profit_bridge"), ("What moved profit vs last year?", "variance_drivers"),
                        ("How is our cash conversion cycle?", "working_capital_cycle"),
                        ("Any suspicious journal entries?", "gl_anomalies")):
            self.assertEqual(core.validate_plan(core.rule_plan(q))["tool"], tool, q)


if __name__ == "__main__":
    unittest.main()
