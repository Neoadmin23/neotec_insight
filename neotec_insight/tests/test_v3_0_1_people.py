"""v3.0.1 — headcount counts employees (not designation records); payroll cost;
payables-with-PO reports linked from the Ageing screen."""
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
    def tool(self, q):
        return core.validate_plan(core.rule_plan(q))

    def test_people_questions(self):
        p = self.tool("What is the total headcount across all 99 designations?")
        self.assertEqual((p["tool"], p["args"]["group_by"]), ("headcount", "designation"))
        self.assertEqual(self.tool("employee by designation")["tool"], "headcount")
        p = self.tool("employees by department")
        self.assertEqual((p["tool"], p["args"]["group_by"]), ("headcount", "department"))
        self.assertEqual(self.tool("عدد الموظفين حسب الفرع")["args"]["group_by"], "branch")
        p = self.tool("payroll cost by designation")
        self.assertEqual((p["tool"], p["args"]["group_by"]), ("people_cost", "designation"))

    def test_finance_questions_unchanged(self):
        p = self.tool("Expenses by department")
        self.assertEqual((p["tool"], p["args"]["dimension"]), ("by_dimension", "department"))


class People(unittest.TestCase):
    def setUp(self):
        self.site = ff.install()
        import frappe
        self.frappe = frappe
        self.site.table("Company")["Alpha Co"] = {"name": "Alpha Co"}
        self.site.metas.setdefault("Company", {"name": "Company", "fields": []})
        self.site.metas["Employee"] = {"name": "Employee", "fields": [
            {"fieldname": "designation", "fieldtype": "Link", "label": "Designation"},
            {"fieldname": "department", "fieldtype": "Link", "label": "Department"},
            {"fieldname": "status", "fieldtype": "Select", "label": "Status"},
            {"fieldname": "company", "fieldtype": "Link", "label": "Company"}]}
        self.site.metas["Salary Slip"] = {"name": "Salary Slip", "fields": [
            {"fieldname": "department", "fieldtype": "Link", "label": "Department"}]}
        for i, (des, st) in enumerate([("Accountant", "Active"), ("Accountant", "Active"), ("Engineer", "Active"),
                                       ("Engineer", "Active"), ("Engineer", "Active"), ("Engineer", "Left")]):
            self.site.table("Employee")[f"E{i}"] = {"name": f"E{i}", "designation": des, "department": "Ops",
                                                     "status": st, "company": "Alpha Co", "creation": str(i)}
        for i, (dep, emp, g) in enumerate([("Ops", "E0", 1000), ("Ops", "E0", 1000), ("Finance", "E2", 3000)]):
            self.site.table("Salary Slip")[f"S{i}"] = {"name": f"S{i}", "department": dep, "employee": emp,
                                                        "gross_pay": g, "net_pay": g * 0.9, "docstatus": 1,
                                                        "company": "Alpha Co", "start_date": "2026-03-01"}
        frappe.defaults = types.SimpleNamespace(get_user_default=lambda k: "Alpha Co", get_global_default=lambda k: "Alpha Co")
        frappe.logger = lambda *a, **k: types.SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None)
        from neotec_insight.neotec_insight.api import copilot
        self.cp = copilot
        copilot._ai_ready = lambda s=None: False

    def tearDown(self):
        ff.uninstall()

    def test_the_reported_question_counts_employees(self):
        r = self.cp.ask("What is the total headcount across all 99 designations?")
        self.assertEqual(r["tool"], "headcount")
        self.assertEqual({x["group"]: x["employees"] for x in r["rows"]}, {"Engineer": 3, "Accountant": 2})
        self.assertIn("5", r["title"])                      # 5 active, the leaver excluded
        self.assertEqual(r["chart"]["type"], "bar")

    def test_status_grouping_includes_leavers(self):
        cols, rows, _t = self.cp.t_headcount("Alpha Co", "2026-01-01", "2026-12-31", group_by="status")
        self.assertEqual({x["group"]: x["employees"] for x in rows}, {"Active": 5, "Left": 1})

    def test_payroll_cost(self):
        cols, rows, _t = self.cp.t_people_cost("Alpha Co", "2026-01-01", "2026-12-31")
        by = {x["group"]: x for x in rows}
        self.assertEqual((by["Ops"]["gross_pay"], by["Ops"]["employees_paid"]), (2000, 1))
        self.assertEqual(rows[0]["group"], "Finance")

    def test_finance_only_user_refused_people_data(self):
        self.site.user = "acc@a.co"
        self.site.roles["acc@a.co"] = {"Accounts User"}
        self.site.perm = lambda dt, p, doc, u: False if dt in ("Salary Slip", "Employee") else None
        with self.assertRaises(self.frappe.PermissionError):
            self.cp.t_headcount("Alpha Co", "2026-01-01", "2026-12-31")


class LinkedReports(unittest.TestCase):
    def setUp(self):
        self.site = ff.install()
        import frappe
        self.frappe = frappe
        self.site.table("Report")["Accounts Payable with PO"] = {"name": "Accounts Payable with PO", "disabled": 0}
        self.site.table("Report")["Accounts Payable Summary with PO"] = {"name": "Accounts Payable Summary with PO",
                                                                         "disabled": 1}
        ff.Document.is_permitted = lambda self: True
        self.addCleanup(lambda: delattr(ff.Document, "is_permitted"))
        from neotec_insight.neotec_insight.api import ageing
        self.ag = ageing

    def tearDown(self):
        ff.uninstall()

    def test_only_existing_enabled_permitted_reports(self):
        out = self.ag.linked_reports("Supplier")
        self.assertEqual([x["report"] for x in out], ["Accounts Payable with PO"])        # summary disabled
        self.assertTrue(out[0]["url"].endswith("/query-report/Accounts Payable with PO"))
        self.assertEqual(self.ag.linked_reports("Customer"), [])
        ff.Document.is_permitted = lambda self: False
        self.assertEqual(self.ag.linked_reports("Supplier"), [])


if __name__ == "__main__":
    unittest.main()
