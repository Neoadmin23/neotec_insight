"""v3.6.4 — Defined (master) payroll counted every employee's latest Salary
Structure Assignment: people who had left, and assignments dated in the
future. Now: the assignment in effect, for employees still employed."""
import sys
import types
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
sys.path.insert(0, str(HERE))
import _fakefrappe as ff  # noqa: E402


class DefinedPayroll(unittest.TestCase):
    def setUp(self):
        self.site = ff.install()
        import frappe
        self.frappe = frappe
        for code in ("Salary Structure Assignment", "Employee", "Salary Slip"):
            self.site.metas.setdefault(code, {"name": code, "fields": []})
        emp = self.site.table("Employee")
        for n, st, rel in (("A", "Active", None), ("B", "Left", "2026-03-15"), ("C", "Active", None),
                           ("D", "Active", None), ("E", "Left", "2026-11-20")):
            emp[n] = {"name": n, "employee_name": f"Emp {n}", "status": st, "relieving_date": rel,
                      "company": "IRSAA", "date_of_joining": "2022-01-01"}
        ssa = self.site.table("Salary Structure Assignment")
        for i, (e, base, frm) in enumerate((("A", 10000, "2025-01-01"), ("A", 12000, "2026-01-01"), ("B", 9000, "2024-01-01"),
                                            ("C", 7000, "2026-12-01"), ("D", 8000, "2025-06-01"), ("D", 9500, "2026-12-01"),
                                            ("E", 6000, "2025-01-01"))):
            ssa[f"S{i}"] = {"name": f"S{i}", "employee": e, "base": base, "from_date": frm, "docstatus": 1, "company": "IRSAA"}
        from neotec_insight.neotec_insight.api import cfo
        self.cfo = cfo
        cfo._has = lambda dt: True
        cfo._company = lambda c=None: "IRSAA"
        cfo._require_people = lambda: None

    def tearDown(self):
        ff.uninstall()

    def test_only_employed_staff_on_assignments_in_effect(self):
        total, per = self.cfo._defined_monthly("IRSAA", "2026-10-31")
        self.assertEqual(per, {"A": 12000.0, "D": 8000.0, "E": 6000.0})
        self.assertEqual(total, 26000.0)

    def test_check_explains_every_employee_and_the_old_total(self):
        d = self.cfo.hr_defined_detail("IRSAA", "2026-10-31")
        by = {r["employee"]: r for r in d["rows"]}
        self.assertTrue(by["A"]["counted"])
        self.assertIn("relieved 2026-03-15", by["B"]["reason"])
        self.assertIn("only from 2026-12-01", by["C"]["reason"])
        self.assertEqual((by["D"]["counted"], by["D"]["base"]), (True, 8000.0))
        self.assertEqual((d["counted_n"], d["counted_total"]), (3, 26000.0))
        self.assertEqual(d["previous_total"], 12000 + 9000 + 7000 + 9500 + 6000)   # what the card used to show

    def test_after_the_increment_date_the_new_salary_counts(self):
        total, per = self.cfo._defined_monthly("IRSAA", "2026-12-15")
        self.assertEqual((per["D"], "C" in per, "E" in per), (9500.0, True, False))


if __name__ == "__main__":
    unittest.main()
