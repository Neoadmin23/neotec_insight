"""v2.89.3 — figures stamped with another company still allocate, and
Diagnose finds figures wherever they went."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_v2_89_2_layout_and_pool import Base  # noqa: E402
from test_v2_89_end_to_end import RULE, entries  # noqa: E402


def checks(d):
    return {c["key"]: c for c in d["checks"]}


class TestCompanyStamp(Base):
    def test_entries_with_another_company_still_allocate(self):
        import frappe
        from neotec_insight.neotec_insight.utils.allocation import compute
        for e in entries():
            frappe.db.set_value("Insight Allocation Entry", e["name"], "company", "IRSAA Business Solution")
        frappe.db.commit()
        pool, _ = self.a._pool_with_status(self.rule(), "IRSAA", 2026)
        res = compute(RULE, "IRSAA", 2026, pool_by_month=pool)
        charged = sum(res["by_month"][m]["charged"] for m in res["months"])
        self.assertGreater(charged, 0)
        c = checks(self.a.diagnose(RULE, 2026, "IRSAA"))
        self.assertIn("IRSAA Business Solution", c["entry_company"]["detail"])


class TestDiagnose(Base):
    def test_healthy_chain(self):
        c = checks(self.a.diagnose(RULE, 2026, "IRSAA"))
        self.assertTrue(c["rule"]["ok"])
        self.assertTrue(c["entries"]["ok"])
        self.assertIn("28 figures", c["entries"]["title"])
        self.assertTrue(c["pool"]["ok"])
        self.assertTrue(c["result"]["ok"])

    def test_figures_saved_under_a_half_typed_year_are_found_and_moved(self):
        import frappe
        for e in entries():
            pm = str(e["period_month"])
            frappe.db.set_value("Insight Allocation Entry", e["name"], "period_month", "0202" + pm[4:])
        frappe.db.commit()
        c = checks(self.a.diagnose(RULE, 2026))
        self.assertFalse(c["entries"]["ok"])
        self.assertEqual(c["odd_years"]["years"], [202])
        r = self.a.move_entries_year(RULE, 202, 2026)
        self.assertEqual(r["moved"], 28)
        c = checks(self.a.diagnose(RULE, 2026))
        self.assertTrue(c["entries"]["ok"])
        self.assertNotIn("odd_years", c)

    def test_figures_in_another_year_offer_a_move_when_this_year_is_empty(self):
        import frappe
        for e in entries():
            pm = str(e["period_month"])
            frappe.db.set_value("Insight Allocation Entry", e["name"], "period_month", "2025" + pm[4:])
        frappe.db.commit()
        c = checks(self.a.diagnose(RULE, 2026))
        self.assertEqual(c["other_years"]["fix"], "move_year")
        self.assertEqual(c["other_years"]["years"], [2025])

    def test_move_keeps_filled_target_months(self):
        import frappe
        e = entries()[0]
        frappe.get_doc({"doctype": "Insight Allocation Entry", "rule": RULE, "company": "IRSAA",
                        "cost_center": e["cost_center"], "period_month": "2025" + str(e["period_month"])[4:],
                        "basis": e["basis"] or "head_count", "driver_value": 99}).insert()
        frappe.db.commit()
        r = self.a.move_entries_year(RULE, 2025, 2026)
        self.assertEqual((r["moved"], r["kept_target"]), (0, 1))

    def test_missing_pool_row_offers_the_picker(self):
        import frappe
        frappe.db.set_value("Insight Allocation Rule", RULE, "pool_row_key", "fml_gone")
        c = checks(self.a.diagnose(RULE, 2026))
        self.assertFalse(c["pool"]["ok"])
        self.assertEqual(c["pool"]["fix"], "pick_row")
        self.assertTrue(c["pool"]["candidates"])

    def test_no_figures_points_to_paste_and_snapshots(self):
        import frappe
        self.snap = __import__("neotec_insight.neotec_insight.utils.snapshots", fromlist=["x"])
        self.snap.take("manual")
        for e in entries():
            frappe.delete_doc("Insight Allocation Entry", e["name"])
        frappe.db.commit()
        c = checks(self.a.diagnose(RULE, 2026))
        self.assertEqual(c["entries"]["fix"], "paste")
        self.assertIn("1 snapshots", c["snapshots"]["title"])
        self.assertEqual(c["snapshots"]["fix"], "snapshots")
        self.assertIn("deleted", c)


if __name__ == "__main__":
    unittest.main()
