"""v3.0.2 — headcount by any Employee field (custom nationality included),
Saudization share, and no silent fallback to department."""
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


class Routing(unittest.TestCase):
    def test_reported_questions(self):
        # v3.1.0 — asking to check Saudization now gets the weighted estimate tool
        self.assertEqual(core.validate_plan(core.rule_plan(
            "Nationality wise employee count, and chekc Saudiazation also"))["tool"], "saudization")
        for q in ("Can you provide a breakdown of employees by nationality?", "Nationality wise employee count",
                  "عدد الموظفين حسب الجنسية"):
            p = core.validate_plan(core.rule_plan(q))
            self.assertEqual((p["tool"], p["args"]["group_by"]), ("headcount", "nationality"), q)


class Site(unittest.TestCase):
    def setUp(self):
        self.site = ff.install()
        import frappe
        self.frappe = frappe
        self.site.table("Company")["Alpha Co"] = {"name": "Alpha Co"}
        self.site.metas.setdefault("Company", {"name": "Company", "fields": []})
        self.site.metas["Employee"] = {"name": "Employee", "fields": [
            {"fieldname": "department", "fieldtype": "Link", "label": "Department"},
            {"fieldname": "status", "fieldtype": "Select", "label": "Status"},
            {"fieldname": "custom_nationality", "fieldtype": "Link", "label": "Nationality"},
            {"fieldname": "gender", "fieldtype": "Link", "label": "Gender"},
            {"fieldname": "ctc", "fieldtype": "Currency", "label": "CTC"}]}
        nat = ["Saudi Arabia"] * 3 + ["India"] * 4 + ["Egypt", None]
        for i, n in enumerate(nat):
            self.site.table("Employee")[f"E{i}"] = {"name": f"E{i}", "custom_nationality": n, "department": "Ops",
                                                     "status": "Active", "company": "Alpha Co", "creation": str(i)}
        self.site.table("Employee")["L1"] = {"name": "L1", "custom_nationality": "Saudi Arabia", "status": "Left",
                                             "company": "Alpha Co", "creation": "z"}
        frappe.defaults = types.SimpleNamespace(get_user_default=lambda k: "Alpha Co", get_global_default=lambda k: "Alpha Co")
        frappe.logger = lambda *a, **k: types.SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None)
        from neotec_insight.neotec_insight.api import copilot
        self.cp = copilot
        copilot._ai_ready = lambda s=None: False

    def tearDown(self):
        ff.uninstall()

    def test_nationality_from_a_custom_field_with_saudization(self):
        r = self.cp.ask("Nationality wise employee count")
        self.assertEqual(r["tool"], "headcount")
        got = {x["group"]: x["employees"] for x in r["rows"]}
        self.assertEqual(got, {"India": 4, "Saudi Arabia": 3, "Egypt": 1, "(not set)": 1})   # leaver excluded
        self.assertIn("Saudi share 33.3% (3 Saudi of 9, plain count", r["title"])
        self.assertEqual(r["columns"][0]["label"], "Nationality")

    def test_unknown_grouping_is_refused_not_replaced_by_department(self):
        with self.assertRaises(self.frappe.ValidationError) as cm:
            self.cp.t_headcount("Alpha Co", "2026-01-01", "2026-12-31", group_by="blood group")
        msg = str(cm.exception)
        self.assertIn("blood group", msg)
        self.assertIn("Nationality", msg)
        self.assertNotIn("CTC", msg)                        # numeric pay fields are not groupings

    def test_other_fields_and_synonyms(self):
        _c, rows, title = self.cp.t_headcount("Alpha Co", "2026-01-01", "2026-12-31", group_by="sex")
        self.assertIn("gender", title.lower())
        self.assertEqual(self.cp.resolve_employee_field("citizenship")[0], "custom_nationality")


if __name__ == "__main__":
    unittest.main()
