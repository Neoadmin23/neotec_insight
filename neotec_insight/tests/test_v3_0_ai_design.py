"""v3.0.0 — AI-designed dashboards and statements: the model proposes, the
server checks, nothing is saved without review."""
import json
import sys
import types
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
sys.path.insert(0, str(HERE))
import _fakefrappe as ff  # noqa: E402

ACCOUNTS = [
    {"name": "4100 - Sales - A", "account_name": "Sales", "root_type": "Income", "account_type": "", "parent": "Income - A", "activity": 900},
    {"name": "5100 - COGS - A", "account_name": "Cost of Goods Sold", "root_type": "Expense", "account_type": "Cost of Goods Sold", "parent": "Direct - A", "activity": 400},
    {"name": "5200 - Salaries - A", "account_name": "Salaries", "root_type": "Expense", "account_type": "", "parent": "Staff - A", "activity": 300},
    {"name": "5300 - Rent - A", "account_name": "Rent", "root_type": "Expense", "account_type": "", "parent": "Premises - A", "activity": 100},
    {"name": "5900 - Interest - A", "account_name": "Interest", "root_type": "Expense", "account_type": "", "parent": "Finance - A", "activity": 50},
]
GOOD = {"title": "Management P&L", "rows": [
    {"key": "sec_rev", "kind": "section", "label": "Revenue"},
    {"key": "r_sales", "kind": "source", "label": "Sales", "accounts": ["4100 - Sales - A"]},
    {"key": "r_cogs", "kind": "source", "label": "Cost of sales", "accounts": ["Cost of Goods Sold"]},
    {"key": "gross_profit", "kind": "formula", "label": "Gross profit", "formula": "r_sales - r_cogs"},
    {"key": "r_opex", "kind": "source", "label": "Operating costs", "accounts": ["5200 - Salaries - A", "5300 - Rent - A"]},
    {"key": "ebitda", "kind": "formula", "label": "EBITDA", "formula": "gross_profit - r_opex"},
    {"key": "alc_gmo", "kind": "allocation", "label": "GMO allocation", "allocation_rule": "GMO Allocations"},
    {"key": "net", "kind": "formula", "label": "Net profit", "formula": "ebitda - alc_gmo"}]}


class Base(unittest.TestCase):
    def setUp(self):
        self.site = ff.install()
        import frappe
        self.frappe = frappe
        self.site.table("Company")["Alpha Co"] = {"name": "Alpha Co"}
        self.site.metas.setdefault("Company", {"name": "Company", "fields": []})
        self.site.table("Insight Allocation Rule")["GMO Allocations"] = {"name": "GMO Allocations", "company": "Alpha Co"}
        frappe.defaults = types.SimpleNamespace(get_user_default=lambda k: "Alpha Co", get_global_default=lambda k: "Alpha Co")
        frappe.db.sql = lambda *a, **k: [dict(x) for x in ACCOUNTS]
        frappe.scrub = lambda s: str(s).strip().lower().replace(" ", "_").replace("&", "and")
        from neotec_insight.neotec_insight.api import ai_design
        self.d = ai_design
        self.d._ai_ready = lambda: True
        import neotec_insight.neotec_insight.doctype.insight_report_definition.insight_report_definition as rd
        self.rd = rd
        rd.can_manage = lambda user=None: True

    def tearDown(self):
        ff.uninstall()

    def check(self, draft):
        return self.d.check_statement(draft, [dict(a) for a in ACCOUNTS], {"GMO Allocations"}, "pnl")


class StatementChecks(Base):
    def test_good_draft(self):
        definition, mappings, errors, warnings, coverage = self.check(GOOD)
        self.assertEqual(errors, [])
        self.assertEqual(len(definition["rows"]), 8)
        self.assertIn({"account": "5100 - COGS - A", "flag": "Cost of sales"}, mappings)     # matched by name
        self.assertEqual(coverage["missing_active"], 1)                                        # Interest left out
        self.assertEqual(coverage["missing_top"][0]["account"], "5900 - Interest - A")

    def test_errors(self):
        bad = {"rows": [
            {"key": "a", "kind": "section", "label": "short key"},
            {"key": "r_x", "kind": "source", "label": "X", "accounts": []},
            {"key": "r_x", "kind": "source", "label": "Dup"},
            {"key": "t_early", "kind": "formula", "label": "Early", "formula": "r_x + later"},
            {"key": "later", "kind": "source", "label": "Later", "accounts": []},
            {"key": "alc", "kind": "allocation", "label": "A", "allocation_rule": "Nope"},
            {"key": "w", "kind": "chart", "label": "W"}]}
        _d, _m, errors, _w, _c = self.check(bad)
        text = " | ".join(errors)
        for frag in ("must be lower-case", "used twice", "Early", "Nope", "unknown row kind"):
            self.assertIn(frag, text)

    def test_warnings(self):
        draft = {"rows": [{"key": "r_a", "kind": "source", "label": "A", "accounts": ["4100 - Sales - A", "9999 - Ghost"]},
                          {"key": "r_b", "kind": "source", "label": "B", "accounts": ["4100 - Sales - A"]}]}
        _d, mappings, errors, warnings, _c = self.check(draft)
        self.assertEqual(errors, [])
        self.assertEqual(len(mappings), 1)
        joined = " | ".join(warnings)
        self.assertIn("9999 - Ghost", joined)
        self.assertIn("already in", joined)
        self.assertIn("has no accounts yet", joined)


class StatementFlow(Base):
    def test_one_corrected_retry(self):
        bad = {"title": "P&L", "rows": [{"key": "t", "kind": "formula", "label": "T", "formula": "missing + 1"}]}
        replies = iter([json.dumps(bad), json.dumps(GOOD)])
        self.d._chat = lambda s, u: next(replies)
        out = self.d.design_statement("management P&L")
        self.assertEqual(out["errors"], [])
        self.assertEqual(out["title"], "Management P&L")

    def test_save_is_rechecked_inactive_and_mapped(self):
        definition, mappings, *_ = self.check(GOOD)
        draft = {"title": "Management P&L", "statement_type": "pnl", "company": "Alpha Co",
                 "definition": definition, "mappings": mappings}
        r = self.d.save_statement(draft)
        rec = self.site.table("Insight Report Definition")[r["name"]]
        self.assertEqual((rec["is_active"], rec["report_type"]), (0, "pnl"))
        self.assertEqual(r["mappings"], 4)          # sales, cost of sales, salaries, rent
        flags = {m["flag"] for m in self.site.table("Account Flag Mapping").values()}
        self.assertEqual(flags, {"Sales", "Cost of sales", "Operating costs"})
        sources = {m["source"] for m in self.site.table("Account Flag Mapping").values()}
        self.assertEqual(sources, {"auto-suggested"})
        tampered = json.loads(json.dumps(draft))
        tampered["definition"]["rows"][3]["formula"] = "nonexistent * 2"
        with self.assertRaises(self.frappe.ValidationError):
            self.d.save_statement(tampered)

    def test_non_managers_refused(self):
        self.rd.can_manage = lambda user=None: False
        with self.assertRaises(self.frappe.PermissionError):
            self.d.save_statement({"company": "Alpha Co"})
        with self.assertRaises(self.frappe.PermissionError):
            self.d.start_design(kind="statement", prompt="P&L")


class Boards(Base):
    def test_pack_flows_into_twelve_columns(self):
        out = self.d.pack([{"title": "a", "type": "kpi", "source": {}, "size": "small"}] * 5
                          + [{"title": "b", "type": "bar", "source": {}, "size": "wide"}])
        self.assertEqual([(v["x"], v["y"]) for v in out[:5]], [(0, 0), (3, 0), (6, 0), (9, 0), (0, 2)])
        self.assertEqual((out[5]["x"], out[5]["y"], out[5]["w"]), (0, 4, 12))

    def test_unknown_sources_and_types_left_out(self):
        board, notes = self.d.check_board({"title": "T", "visuals": [
            {"title": "ok", "type": "bar", "source": {"kind": "analysis", "key": "dupont"}},
            {"title": "ghost", "type": "bar", "source": {"kind": "analysis", "key": "drop_tables"}},
            {"title": "3d", "type": "3d", "source": {"kind": "analysis", "key": "dupont"}},
            {"title": "studio", "type": "table", "source": {"kind": "studio", "report": "nope"}}]}, {"dupont"}, set())
        self.assertEqual([v["title"] for v in board["layout"]], ["ok"])
        self.assertEqual(len(notes), 3)

    def test_ai_off_gives_the_cfo_starter(self):
        self.d._ai_ready = lambda: False
        out = self.d.design_board("anything")
        self.assertEqual(out["source"], "template")
        self.assertGreaterEqual(len(out["layout"]), 8)
        self.assertEqual(out["layout"][4]["type"], "waterfall")

    def test_ai_board(self):
        self.d._chat = lambda s, u: json.dumps({"title": "Sales", "visuals": [
            {"title": "Top customers", "type": "bar", "source": {"kind": "analysis", "key": "revenue_concentration"}, "size": "medium"}]})
        out = self.d.design_board("sales dashboard")
        self.assertEqual((out["title"], out["source"], len(out["layout"])), ("Sales", "ai", 1))


if __name__ == "__main__":
    unittest.main()
