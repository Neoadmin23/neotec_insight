"""v3.1.0 — Nitaqat-style Saudization estimate from editable rules, and the
compliance overview as Copilot tools."""
import sys
import types
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[0] / "neotec_insight" / "utils"))
sys.path.insert(0, str(HERE.parents[1]))
sys.path.insert(0, str(HERE))
import copilot_core as core  # noqa: E402
import saudization_core as sc  # noqa: E402
import _fakefrappe as ff  # noqa: E402

RULES = {"saudi_values": "Saudi\nSaudi Arabia", "counted_as_saudi_values": "Bahrain\nKuwait",
         "part_time_values": "Part-time", "part_time_weight": 0.5, "disability_field": "custom_disabled",
         "disabled_weight": 4, "salary_source": "Salary Structure Assignment", "min_wage_full": 4000,
         "min_wage_floor": 1500, "weight_below_full": 0.5,
         "platinum": 60, "high_green": 45, "medium_green": 35, "low_green": 25}


class Core(unittest.TestCase):
    def test_weights(self):
        c = lambda **e: sc.classify({"name": "x", **e}, RULES)[:2]   # noqa: E731
        self.assertEqual(c(nationality="Saudi Arabia", wage=5000), ("Saudi", 1.0))
        self.assertEqual(c(nationality="Saudi", wage=3000), ("Saudi — below full-count wage", 0.5))
        self.assertEqual(c(nationality="Saudi", wage=1000), ("Saudi — below wage floor", 0.0))
        self.assertEqual(c(nationality="Saudi", employment_type="Part-time", wage=9000), ("Saudi — part-time", 0.5))
        self.assertEqual(c(nationality="Saudi", disabled=1, wage=9000), ("Saudi — disability", 4.0))
        self.assertEqual(c(nationality="Kuwait", wage=5000), ("Counted as Saudi", 1.0))
        self.assertEqual(c(nationality="India", wage=9000), ("Non-Saudi", 0.0))
        self.assertEqual(c(nationality=None), ("Nationality missing", 0.0))
        self.assertIn("wage unknown", sc.classify({"name": "x", "nationality": "Saudi"}, RULES)[2])

    def test_bands_and_gap(self):
        self.assertEqual(sc.band_for(40, RULES), "medium_green")
        self.assertEqual(sc.band_for(10, RULES), "red")
        self.assertEqual(sc.band_for(40, {}), "unknown")
        self.assertEqual(sc.saudis_needed(23, 61, 40), 3)            # 26/64 = 40.6 %
        self.assertEqual(sc.saudis_needed(10, 10, 50), 0)
        est = sc.estimate([{"name": "a", "nationality": "Saudi", "wage": 5000},
                           {"name": "b", "nationality": "India"}, {"name": "c", "nationality": "India"}], RULES)
        self.assertEqual((est["pct"], est["band"], est["next"]["band"]), (33.33, "low_green", "medium_green"))

    def test_routing(self):
        for q, tool in (("What is our Saudization?", "saudization"), ("nitaqat band", "saudization"),
                        ("Are we compliant?", "compliance_overview"), ("iqama status", "compliance_overview"),
                        ("حالة الامتثال", "compliance_overview")):
            self.assertEqual(core.validate_plan(core.rule_plan(q))["tool"], tool, q)


class Site(unittest.TestCase):
    def setUp(self):
        self.site = ff.install()
        import frappe
        self.frappe = frappe
        self.site.table("Company")["Alpha Co"] = {"name": "Alpha Co"}
        self.site.metas.setdefault("Company", {"name": "Company", "fields": []})
        self.site.metas["Employee"] = {"name": "Employee", "fields": [
            {"fieldname": "nationality", "fieldtype": "Link", "label": "Nationality"},
            {"fieldname": "employment_type", "fieldtype": "Link", "label": "Employment Type"},
            {"fieldname": "custom_disabled", "fieldtype": "Check", "label": "Disabled"},
            {"fieldname": "bank_ac_no", "fieldtype": "Data", "label": "Bank A/C No"},
            {"fieldname": "status", "fieldtype": "Select", "label": "Status"}]}
        self.site.metas["Salary Structure Assignment"] = {"name": "Salary Structure Assignment", "fields": []}
        staff = [("Saudi", None, 0, 6000, "X1"), ("Saudi", "Part-time", 0, 6000, "X2"), ("Saudi", None, 0, 3000, None),
                 ("India", None, 0, 4000, "X4"), ("India", None, 0, 4000, "X5"), ("Egypt", None, 0, 4000, "X6")]
        for i, (n, et, dis, wage, bank) in enumerate(staff):
            self.site.table("Employee")[f"E{i}"] = {"name": f"E{i}", "nationality": n, "employment_type": et,
                                                     "custom_disabled": dis, "bank_ac_no": bank, "status": "Active",
                                                     "company": "Alpha Co", "creation": str(i)}
            self.site.table("Salary Structure Assignment")[f"S{i}"] = {"name": f"S{i}", "employee": f"E{i}", "base": wage,
                                                                       "docstatus": 1, "company": "Alpha Co",
                                                                       "from_date": "2026-01-01"}
        frappe.defaults = types.SimpleNamespace(get_user_default=lambda k: "Alpha Co", get_global_default=lambda k: "Alpha Co")
        frappe.logger = lambda *a, **k: types.SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None)
        frappe.format = lambda v, df=None: f"{v}"
        from neotec_insight.neotec_insight.api import copilot
        self.cp = copilot
        copilot._ai_ready = lambda s=None: False
        copilot._saudization_rules = lambda: dict(RULES)

    def tearDown(self):
        ff.uninstall()

    def test_saudization_tool(self):
        r = self.cp.ask("What is our Saudization?")
        self.assertEqual(r["tool"], "saudization")
        # 1 + 0.5 (part-time) + 0.5 (below full wage) = 2 of 6 = 33.33 %
        self.assertIn("33.33% weighted (2.0 of 6)", r["title"])
        self.assertIn("Low Green", r["title"])
        self.assertIn("more Saudi hires for Medium Green", r["title"])
        self.assertIn("verify in Qiwa", r["rows"][-1]["category"])

    def test_compliance_overview_survives_a_failing_area(self):
        vat = types.ModuleType("neotec_insight.neotec_insight.api.vat")
        vat.vat_return = lambda c, f, t: {"net": 1250.0}
        zak = types.ModuleType("neotec_insight.neotec_insight.api.zakat")

        def boom(*a, **k):
            raise RuntimeError("zakat settings missing")
        zak.zakat_estimate = boom
        sys.modules[vat.__name__], sys.modules[zak.__name__] = vat, zak
        r = self.cp.ask("Are we compliant?")
        by = {x["area"]: x for x in r["rows"]}
        self.assertEqual(by["Saudization (estimate)"]["status"], "Attention")           # Low Green
        self.assertIn("1 without bank account", by["Employee records"]["detail"])
        self.assertIn("no ID / iqama number field", by["Employee records"]["detail"])
        self.assertEqual(by["ID / iqama expiry"]["status"], "Unknown")
        self.assertIn("1250", by["VAT (period)"]["detail"])
        self.assertEqual(by["Zakat (estimate)"]["status"], "Unknown")
        self.assertIn("zakat settings missing", by["Zakat (estimate)"]["detail"])

    def test_people_parts_need_hr_access(self):
        self.site.user = "acc@a.co"
        self.site.roles["acc@a.co"] = {"Accounts User"}
        self.site.perm = lambda dt, p, doc, u: False if dt in ("Salary Slip", "Employee") else None
        cols, rows, _t = self.cp.t_compliance_overview("Alpha Co", "2026-01-01", "2026-12-31")
        by = {x["area"]: x for x in rows}
        self.assertEqual(by["Saudization (estimate)"]["detail"], "Needs HR or payroll access.")


if __name__ == "__main__":
    unittest.main()
