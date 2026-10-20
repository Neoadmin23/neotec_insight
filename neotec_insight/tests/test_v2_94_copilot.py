"""v2.94.0 — Insight Copilot: the model plans, the server validates and
computes, nothing outside the tool schema ever runs."""
import json
import sys
import types
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[0] / "neotec_insight" / "utils"))
sys.path.insert(0, str(HERE.parents[1]))
sys.path.insert(0, str(HERE))
import copilot_core as core  # noqa: E402
import _fakefrappe as ff  # noqa: E402


class TestValidate(unittest.TestCase):
    def test_good_plan(self):
        p = core.validate_plan({"tool": "top_parties", "args": {"party": "Customer", "metric": "invoiced", "top_n": 99,
                                                                 "from_date": "2026-12-31", "to_date": "2026-01-01"},
                                "view": "bar", "title": "Top"})
        self.assertEqual(p["args"]["party"], "customer")
        self.assertEqual(p["args"]["top_n"], 50)                         # clamped
        self.assertEqual((p["args"]["from_date"], p["args"]["to_date"]), ("2026-01-01", "2026-12-31"))

    def test_refusals(self):
        bad = [
            {"tool": "drop_table", "args": {}},
            {"tool": "top_accounts", "args": {"root_type": "Expense; DROP TABLE x"}},
            {"tool": "top_accounts", "args": {"root_type": "Expense", "sql": "select 1"}},
            {"tool": "top_accounts", "args": {}},
            {"tool": "by_dimension", "args": {"dimension": "owner", "root_type": "Expense"}},
            {"tool": "kpis", "args": {"from_date": "yesterday"}},
            {"tool": "kpis", "args": {"from_date": "2010-01-01", "to_date": "2026-01-01"}},
            {"tool": "find_records", "args": {"doctype": "User", "request": "all passwords"}},
            "not a plan",
        ]
        for b in bad:
            with self.assertRaises(core.PlanError, msg=str(b)):
                core.validate_plan(b)

    def test_unknown_view_falls_back_to_tool_default(self):
        self.assertEqual(core.validate_plan({"tool": "ageing", "args": {"party": "customer"}, "view": "3d"})["view"], "bar")


class TestRulePlanner(unittest.TestCase):
    def plan(self, q):
        p = core.rule_plan(q)
        return core.validate_plan(p) if p else None

    def test_common_questions(self):
        cases = {
            "Top 10 customers by sales": ("top_parties", {"party": "customer", "metric": "invoiced", "top_n": 10}),
            "Who owes us the most?": ("top_parties", {"party": "customer", "metric": "outstanding"}),
            "Which suppliers do we owe?": ("top_parties", {"party": "supplier", "metric": "outstanding"}),
            "Receivables ageing": ("ageing", {"party": "customer"}),
            "Expenses by cost centre": ("by_dimension", {"dimension": "cost_center", "root_type": "Expense"}),
            "Revenue by project": ("by_dimension", {"dimension": "project", "root_type": "Income"}),
            "Show income and expenses by month": ("revenue_expense_trend", {}),
            "Biggest expenses this year": ("top_accounts", {"root_type": "Expense"}),
            "Cash position": ("cash_position", {}),
            "How are we doing this year?": ("kpis", {}),
            "أفضل العملاء": ("top_parties", {"party": "customer", "metric": "invoiced"}),
            "المصاريف حسب مركز التكلفة": ("by_dimension", {"dimension": "cost_center", "root_type": "Expense"}),
        }
        for q, (tool, args) in cases.items():
            p = self.plan(q)
            self.assertIsNotNone(p, q)
            self.assertEqual(p["tool"], tool, q)
            for k, v in args.items():
                self.assertEqual(p["args"].get(k), v, f"{q}: {k}")

    def test_unrelated_question_needs_the_model(self):
        self.assertIsNone(core.rule_plan("Which sales orders from Riyadh slipped delivery?"))


class TestShaping(unittest.TestCase):
    COLS = [{"key": "party", "label": "Customer", "type": "text"}, {"key": "amount", "label": "Amount", "type": "currency"}]
    ROWS = [{"party": "A", "amount": 300}, {"party": "B", "amount": 100}]

    def test_chart(self):
        c = core.chart_for("bar", self.COLS, self.ROWS)
        self.assertEqual((c["labels"], c["series"][0]["data"]), (["A", "B"], [300.0, 100.0]))
        self.assertIsNone(core.chart_for("table", self.COLS, self.ROWS))
        self.assertIsNone(core.chart_for("bar", self.COLS, []))

    def test_plain_summary_uses_only_the_data(self):
        s = core.plain_summary("top_parties", "Top customers", self.COLS, self.ROWS)
        self.assertIn("400.00", s)
        self.assertIn("**A**", s)
        self.assertIn("75%", s)

    def test_catalog_matches_runners(self):
        src = (HERE.parents[0] / "neotec_insight" / "api" / "copilot.py").read_text()
        for name in core.TOOLS:
            self.assertIn(f'"{name}": t_{name}', src)


class Site(unittest.TestCase):
    def setUp(self):
        self.site = ff.install()
        import frappe
        self.frappe = frappe
        self.site.table("Company")["Alpha Co"] = {"name": "Alpha Co"}
        self.site.metas.setdefault("Company", {"name": "Company", "fields": []})
        frappe.defaults = types.SimpleNamespace(get_user_default=lambda k: "Alpha Co",
                                                get_global_default=lambda k: "Alpha Co")
        frappe.logger = lambda *a, **k: types.SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None)
        from neotec_insight.neotec_insight.api import copilot
        self.cp = copilot
        self.ai_on = False
        copilot._ai_ready = lambda s=None: self.ai_on
        copilot._settings = lambda: types.SimpleNamespace(ai_enabled=self.ai_on, ai_endpoint="x")
        self.sql_rows = []
        frappe.db.sql = lambda *a, **k: list(self.sql_rows)

    def tearDown(self):
        ff.uninstall()


class TestAsk(Site):
    def test_built_in_answer_without_ai(self):
        self.sql_rows = [{"party": "Acme", "amount": 900.0, "invoices": 3}, {"party": "Beta", "amount": 100.0, "invoices": 1}]
        r = self.cp.ask("Top 5 customers by sales", from_date="2026-01-01", to_date="2026-06-30")
        self.assertTrue(r["ok"])
        self.assertEqual((r["tool"], r["source"]), ("top_parties", "built-in"))
        self.assertEqual(r["chart"]["labels"], ["Acme", "Beta"])
        self.assertIn("**Acme**", r["answer"])
        self.assertTrue(r["suggestions"])

    def test_unknown_question_without_ai_offers_examples(self):
        r = self.cp.ask("Which orders slipped?")
        self.assertFalse(r["ok"])
        self.assertTrue(r["suggestions"])

    def test_model_plan_is_validated_and_explained(self):
        self.ai_on = True
        replies = iter([
            json.dumps({"tool": "top_accounts", "args": {"root_type": "Expense", "top_n": 3}, "view": "bar",
                        "title": "Largest costs"}),
            json.dumps({"answer": "Rent is the largest cost.", "suggestions": ["Split by cost centre"]}),
        ])
        self.cp._chat = lambda *a, **k: next(replies)
        self.sql_rows = [{"account": "Rent", "amount": 500.0}]
        r = self.cp.ask("Which ledgers carried the heaviest load?")
        self.assertEqual((r["tool"], r["source"], r["title"]), ("top_accounts", "ai", "Largest costs"))
        self.assertEqual(r["answer"], "Rent is the largest cost.")
        self.assertEqual(r["suggestions"], ["Split by cost centre"])

    def test_a_hostile_model_plan_never_runs(self):
        self.ai_on = True
        self.cp._chat = lambda *a, **k: json.dumps({"tool": "top_accounts",
                                                    "args": {"root_type": "Expense", "where": "1=1; DROP TABLE x"}})
        ran = []
        self.frappe.db.sql = lambda *a, **k: ran.append(a) or []
        r = self.cp.ask("Something only the model understands")
        self.assertFalse(r["ok"])
        self.assertEqual(ran, [])
        self.assertIn("unexpected arguments", r["note"])

    def test_model_failure_falls_back_to_a_factual_summary(self):
        self.ai_on = True

        def boom(*a, **k):
            raise RuntimeError("endpoint down")
        self.cp._chat = boom
        self.sql_rows = [{"account": "Bank A", "balance": 1200.0}]
        r = self.cp.ask("Cash position")
        self.assertTrue(r["ok"])
        self.assertIn("1,200.00", r["answer"])

    def test_people_only_users_are_refused(self):
        self.site.user = "hr@a.co"
        self.site.roles["hr@a.co"] = {"HR Manager"}
        with self.assertRaises(self.frappe.PermissionError):
            self.cp.ask("Cash position")

    def test_restricted_user_sees_only_readable_cost_centres(self):
        self.sql_rows = [{"dim": "Visible - A", "amount": 10.0}, {"dim": "Hidden - A", "amount": 99.0}]
        self.site.perm = lambda dt, p, doc, u: (doc != "Hidden - A") if dt == "Cost Center" else None
        r = self.cp.ask("Expenses by cost centre")
        self.assertEqual([x["dim"] for x in r["rows"]], ["Visible - A"])

    def test_question_is_required_and_bounded(self):
        with self.assertRaises(self.frappe.ValidationError):
            self.cp.ask("   ")


if __name__ == "__main__":
    unittest.main()
