"""v2.89.2 — the grid keeps its cost centres when figures are gone, and an
empty pool always says why."""
import json
import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _fakefrappe as ff  # noqa: E402
from test_v2_89_end_to_end import RULE, entries, seed  # noqa: E402

# The IRSAA layout from the old screen.
HEAD = {"Audit & GRC": [8, 8, 8, 8, 9, 8, 8],
        "Financial & Admin Outsourcing Services": [8, 8, 8, 8, 8, 8, 8],
        "Fixed Assets": [0, 0, 0, 0, 0, 0, 0],
        "HR Outsourcing Services": [3, 4, 4, 4, 4, 4, 4],
        "Internal Audit": [6, 6, 6, 5, 5, 5, 5],
        "Data Processing": [12, 12, 11, 10, 9, 8, 7]}


class Base(unittest.TestCase):
    ROWS = [{"key": "fml_5aew2", "label": "GMO total expenses",
             "monthly": {str(i): 1000.0 * (i + 1) for i in range(12)}},
            {"key": "total_income", "label": "Total income", "monthly": {"0": 5.0}}]

    def setUp(self):
        self.site = ff.install()
        seed(self.site)
        import frappe
        for cc in list(HEAD) + ["Main"]:
            self.site.table("Cost Center")[f"{cc} - IRSAA"] = {
                "name": f"{cc} - IRSAA", "cost_center_name": cc, "is_group": 0, "company": "IRSAA"}
        self.exec_calls = []
        fake_exec = types.ModuleType("neotec_insight.neotec_insight.utils.execution")
        def execute_report(**kw):
            self.exec_calls.append(kw)
            if getattr(self, "boom", False):
                raise RuntimeError("Too many connections")
            keys = {r.get("key") for r in (kw.get("report_def") or {}).get("rows") or []}
            return {"rows": [r for r in self.ROWS if not keys or r["key"] in keys]}
        fake_exec.execute_report = execute_report
        fake_exec.load_flag_mappings = lambda r: {}
        fake_exec.load_flag_to_accounts = lambda r: {}
        sys.modules["neotec_insight.neotec_insight.utils.execution"] = fake_exec
        from neotec_insight.neotec_insight.api import allocation
        allocation.calendar_month_for_fy_month = lambda company, k: int(k) + 1
        self.a = allocation
        frappe.db.set_value("Insight Report Definition", "consolidated-p-and-l", "definition_json",
                            json.dumps({"rows": [{"key": r["key"], "label": r["label"]} for r in self.ROWS]}))
        frappe.db.set_value("Insight Allocation Rule", RULE, {
            "pool_mode": "report_row", "pool_report": "consolidated-p-and-l",
            "pool_row_key": "fml_5aew2", "pool_cost_center": "GMO - IRSAA"})
        frappe.db.commit()

    def tearDown(self):
        ff.uninstall()

    def rule(self):
        import frappe
        return frappe.get_doc("Insight Allocation Rule", RULE)


class TestLayoutSurvivesLoss(Base):
    def test_cost_centres_stay_on_the_grid_after_every_figure_is_gone(self):
        import frappe
        g0 = self.a.get_grid(RULE, 2026)                     # seeds the layout
        ccs = set(g0["cost_centers"])
        for e in entries():
            frappe.delete_doc("Insight Allocation Entry", e["name"])
        frappe.db.commit()
        g = self.a.get_grid(RULE, 2026)
        self.assertEqual(set(g["cost_centers"]), ccs)
        self.assertEqual(g["entry_count"], 0)
        self.assertEqual(g["basis"]["Main - IRSAA"], "amount")
        self.assertEqual(set(g["layout_only"]), ccs)

    def test_a_new_year_shows_the_layout(self):
        self.a.get_grid(RULE, 2026)
        g = self.a.get_grid(RULE, 2027)
        self.assertIn("Audit & GRC - IRSAA", g["cost_centers"])
        self.assertEqual(g["entry_count"], 0)

    def test_removing_a_row_takes_it_off_the_layout(self):
        g = self.a.get_grid(RULE, 2026)
        self.a.save_grid(RULE, 2026, cells={}, basis=g["basis"], remove=["Internal Audit - IRSAA"],
                         loaded_count=g["entry_count"])
        self.assertNotIn("Internal Audit - IRSAA", self.a.get_grid(RULE, 2027)["cost_centers"])

    def test_excluded_cost_centre_is_not_shown(self):
        self.a.remember_layout(RULE, add={"Fixed Assets - IRSAA": "head_count"})
        self.assertNotIn("Fixed Assets - IRSAA", self.a.get_grid(RULE, 2026)["cost_centers"])

    def test_paste_restores_the_old_screen_and_remembers_it(self):
        import frappe
        for e in entries():
            frappe.delete_doc("Insight Allocation Entry", e["name"])
        frappe.db.set_value("Insight Allocation Rule", RULE, "layout_json", "")
        frappe.db.commit()
        head = "\n".join(",".join([cc] + [str(v) for v in vals]) for cc, vals in HEAD.items())
        r = self.a.import_head_counts(RULE, 2026, head, dry_run=0)
        self.assertEqual(r["unmatched"], [])
        self.assertEqual(r["created"], 42)
        r2 = self.a.import_head_counts(RULE, 2026, "Main," + ",".join(["45000"] * 12), dry_run=0,
                                       basis="amount")
        self.assertEqual(r2["created"], 12)
        g = self.a.get_grid(RULE, 2026)
        self.assertEqual(g["cells"]["Data Processing - IRSAA"][3]["driver"], 11)
        self.assertEqual(g["cells"]["Main - IRSAA"][9]["amount"], 45000)
        self.assertEqual(g["basis"]["Main - IRSAA"], "amount")
        lay = json.loads(frappe.db.get_value("Insight Allocation Rule", RULE, "layout_json"))
        self.assertEqual(lay["Main - IRSAA"], "amount")
        self.assertEqual(len(lay), 7)       # Fixed Assets is excluded in the seed only on the grid


class TestPoolStatus(Base):
    def status(self):
        return self.a._pool_with_status(self.rule(), "IRSAA", 2026)

    def test_ok(self):
        pool, st = self.status()
        self.assertEqual(st["state"], "ok")
        self.assertEqual(pool[1], 1000.0)

    def test_failed_report_run_says_so(self):
        self.boom = True
        pool, st = self.status()
        self.assertEqual((pool, st["state"]), ({}, "error"))
        self.assertIn("Too many connections", st["message"])

    def test_missing_row_lists_candidates(self):
        import frappe
        frappe.db.set_value("Insight Allocation Rule", RULE, "pool_row_key", "fml_gone")
        pool, st = self.status()
        self.assertEqual(st["state"], "row_missing")
        self.assertEqual([c["key"] for c in st["candidates"]], ["fml_5aew2", "total_income"])
        first = st["candidates"][0]
        self.assertEqual((first["jan"], first["ytd"]), (1000.0, 78000.0))

    def test_candidates_with_figures_come_first(self):
        import frappe
        self.ROWS = [{"key": "hdr", "label": "Header", "monthly": {}}] + Base.ROWS
        frappe.db.set_value("Insight Report Definition", "consolidated-p-and-l", "definition_json",
                            json.dumps({"rows": [{"key": r["key"], "label": r["label"]} for r in self.ROWS]}))
        frappe.db.set_value("Insight Allocation Rule", RULE, "pool_row_key", "fml_gone")
        _, st = self.status()
        self.assertEqual(st["candidates"][-1]["key"], "hdr")

    def test_missing_row_found_again_by_label(self):
        import frappe
        frappe.db.set_value("Insight Allocation Rule", RULE,
                            {"pool_row_key": "fml_old", "pool_row_label": "GMO total expenses"})
        pool, st = self.status()
        self.assertEqual(st["state"], "relinked")
        self.assertEqual(pool[12], 12000.0)
        self.assertEqual(frappe.db.get_value("Insight Allocation Rule", RULE, "pool_row_key"), "fml_5aew2")

    def test_zero_row_is_reported(self):
        self.ROWS = [{"key": "fml_5aew2", "label": "GMO", "monthly": {}}]
        _, st = self.status()
        self.assertEqual(st["state"], "zero")

    def test_missing_report(self):
        import frappe
        frappe.db.set_value("Insight Allocation Rule", RULE, "pool_report", "gone-report")
        _, st = self.status()
        self.assertEqual(st["state"], "report_missing")

    def test_not_configured(self):
        import frappe
        frappe.db.set_value("Insight Allocation Rule", RULE, "pool_row_key", "")
        _, st = self.status()
        self.assertEqual(st["state"], "not_configured")

    def test_set_pool_row_stores_key_and_label(self):
        import frappe
        self.a.set_pool_row(RULE, "total_income")
        self.assertEqual(frappe.db.get_value("Insight Allocation Rule", RULE, "pool_row_label"), "Total income")
        with self.assertRaises(frappe.ValidationError):
            self.a.set_pool_row(RULE, "nope")

    def test_engine_signature_unchanged(self):
        self.assertEqual(self.a._pool_from_report_row(self.rule(), "IRSAA", 2026)[1], 1000.0)


if __name__ == "__main__":
    unittest.main()
