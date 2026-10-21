"""v3.1.1 — headcount with a second dimension as columns (Saudi / non-Saudi per
department, or any employee field)."""
import sys
import types
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
sys.path.insert(0, str(HERE))
import _fakefrappe as ff  # noqa: E402


class Crosstab(unittest.TestCase):
    def setUp(self):
        self.site = ff.install()
        import frappe
        self.frappe = frappe
        self.site.table("Company")["Alpha Co"] = {"name": "Alpha Co"}
        self.site.metas.setdefault("Company", {"name": "Company", "fields": []})
        self.site.metas["Employee"] = {"name": "Employee", "fields": [
            {"fieldname": "department", "fieldtype": "Link", "label": "Department"},
            {"fieldname": "custom_nationality", "fieldtype": "Link", "label": "Nationality"},
            {"fieldname": "gender", "fieldtype": "Link", "label": "Gender"},
            {"fieldname": "status", "fieldtype": "Select", "label": "Status"}]}
        staff = [("GMO", "Saudi Arabia", "Male"), ("GMO", "India", "Male"), ("GMO", "India", "Female"),
                 ("GMO", "Kuwait", "Female"), ("Audit", "Saudi Arabia", "Female"), ("Audit", None, "Male")]
        for i, (d, n, g) in enumerate(staff):
            self.site.table("Employee")[f"E{i}"] = {"name": f"E{i}", "department": d, "custom_nationality": n,
                                                     "gender": g, "status": "Active", "company": "Alpha Co",
                                                     "creation": str(i)}
        frappe.defaults = types.SimpleNamespace(get_user_default=lambda k: "Alpha Co", get_global_default=lambda k: "Alpha Co")
        frappe.logger = lambda *a, **k: types.SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None)
        from neotec_insight.neotec_insight.api import copilot
        self.cp = copilot
        copilot._ai_ready = lambda s=None: False
        copilot._saudization_rules = lambda: {"saudi_values": "Saudi\nSaudi Arabia", "counted_as_saudi_values": "Kuwait"}

    def tearDown(self):
        ff.uninstall()

    def test_department_wise_saudi_and_non_saudi(self):
        r = self.cp.ask("Department wise saudi and non-saudi employee head count")
        cols = {c["key"]: c["label"] for c in r["columns"]}
        by = {x["group"]: {cols[k]: v for k, v in x.items() if k in cols} for x in r["rows"]}
        self.assertEqual(by["GMO"]["Saudi"], 1)
        self.assertEqual(by["GMO"]["Counted as Saudi"], 1)
        self.assertEqual(by["GMO"]["Non-Saudi"], 2)
        self.assertEqual(by["GMO"]["Saudi share %"], 50.0)
        self.assertEqual(by["Audit"]["Nationality missing"], 1)
        self.assertEqual(by["Audit"]["Total"], 2)
        self.assertIn("Saudi share 50.0%", r["title"])
        self.assertEqual(r["chart"]["type"], "bar")
        self.assertGreaterEqual(len(r["chart"]["series"]), 3)          # one bar per column

    def test_any_second_field(self):
        cols, rows, title = self.cp.t_headcount("Alpha Co", "", "", group_by="department", split_by="gender")
        labels = [c["label"] for c in cols]
        self.assertIn("Male", labels)
        self.assertIn("Female", labels)
        self.assertIn("gender", title)

    def test_unknown_split_refused(self):
        with self.assertRaises(self.frappe.ValidationError):
            self.cp.t_headcount("Alpha Co", "", "", group_by="department", split_by="blood group")


if __name__ == "__main__":
    unittest.main()
