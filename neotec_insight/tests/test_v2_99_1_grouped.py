"""v2.99.1 — grouped Studio results (e.g. "employee by designation") reach the
Copilot and the Designer instead of showing as empty."""
import json
import sys
import types
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
sys.path.insert(0, str(HERE))
import _fakefrappe as ff  # noqa: E402

GROUPED = {"columns": [{"field": "designation", "label": "Designation", "numeric": False},
                       {"field": "ctc", "label": "CTC", "numeric": True}],
           "group_by": "designation", "group_label": "Designation",
           "groups": [{"key": "Accountant", "count": 3, "subtotal": {"ctc": 30000}, "rows": []},
                      {"key": "Engineer", "count": 5, "subtotal": {"ctc": 90000}, "rows": []}]}


class Base(unittest.TestCase):
    def setUp(self):
        self.site = ff.install()
        import frappe
        self.frappe = frappe
        self.site.table("Company")["Alpha Co"] = {"name": "Alpha Co"}
        self.site.metas.setdefault("Company", {"name": "Company", "fields": []})
        self.site.metas["Employee"] = {"name": "Employee", "fields": [{"fieldname": "company", "fieldtype": "Link"},
                                                                     {"fieldname": "designation", "fieldtype": "Link"}]}
        frappe.defaults = types.SimpleNamespace(get_user_default=lambda k: "Alpha Co", get_global_default=lambda k: "Alpha Co")
        frappe.logger = lambda *a, **k: types.SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None)
        from neotec_insight.neotec_insight.api import copilot, studio
        self.cp, self.studio = copilot, studio
        self.result = GROUPED
        studio._can_read = lambda dt: True
        studio.ai_build = lambda dt, req: {"columns": ["designation", "ctc"], "group_by": "designation", "filters": []}
        studio.run_query = lambda cfg: self.result
        copilot.readable_doctypes = lambda: ["Employee", "Sales Invoice"]
        copilot._ai_ready = lambda s=None: True
        self.explained = []

    def tearDown(self):
        ff.uninstall()

    def plan_then(self, *later):
        it = iter([json.dumps({"tool": "find_records", "args": {"doctype": "Employee", "request": "count by designation"},
                               "view": "table", "title": "Employees by Designation"}), *later])

        def chat(*a, **k):
            self.explained.append(a)
            return next(it)
        self.cp._chat = chat


class FlatResult(Base):
    def test_grouped_and_plain(self):
        cols, rows = self.studio.flat_result(GROUPED)
        self.assertEqual([c["key"] for c in cols], ["designation", "count", "ctc"])
        self.assertEqual(rows[0], {"designation": "Engineer", "count": 5, "ctc": 90000})     # largest group first
        cols2, rows2 = self.studio.flat_result({"columns": GROUPED["columns"], "rows": [{"designation": "x", "ctc": 1}]})
        self.assertEqual((len(cols2), rows2[0]["ctc"]), (2, 1))


class CopilotEmployeeQuestion(Base):
    def test_employee_by_designation_is_no_longer_empty(self):
        self.plan_then(json.dumps({"answer": "Engineers are the largest group.", "suggestions": []}))
        r = self.cp.ask("show records grouped by designation")
        self.assertTrue(r["ok"])
        self.assertEqual([x["designation"] for x in r["rows"]], ["Engineer", "Accountant"])
        self.assertEqual(r["chart"]["type"], "bar")
        self.assertEqual(r["chart"]["labels"], ["Engineer", "Accountant"])
        self.assertEqual(r["answer"], "Engineers are the largest group.")

    def test_truly_empty_says_so_without_asking_the_model(self):
        self.result = {"columns": GROUPED["columns"], "group_by": "designation", "groups": []}
        self.plan_then()
        r = self.cp.ask("show records grouped by designation")
        self.assertIn("no matching records", r["answer"])
        self.assertEqual(len(self.explained), 1)                 # planning only — no explanation call


class DesignerGrouped(Base):
    def test_grouped_studio_visual(self):
        self.site.table("Studio Report")["by-desig"] = {"name": "by-desig", "config_json": json.dumps(
            {"doctype": "Employee", "columns": ["designation"], "group_by": "designation"})}
        from neotec_insight.neotec_insight.api import boards
        d = boards.run_visual({"kind": "studio", "report": "by-desig"}, {})
        self.assertEqual(len(d["rows"]), 2)
        self.assertEqual(d["columns"][1]["key"], "count")


if __name__ == "__main__":
    unittest.main()
