"""v2.89.5 — a customised report replaced by the stock preset is found and
put back; deleted Insight records come back; the preset no longer re-seeds
over a deleted report."""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_v2_89_2_layout_and_pool import Base  # noqa: E402
from test_v2_89_end_to_end import RULE  # noqa: E402

PRESET = json.loads((Path(__file__).resolve().parents[1] / "neotec_insight" / "report_presets"
                     / "consolidated-p-and-l.json").read_text())


def custom_definition():
    """The stock rows plus what the site had added: Total Expenses
    (fml_5aew2), the before-allocation line and two allocation rows."""
    d = json.loads(json.dumps(PRESET["definition"]))
    rows = [r for r in d["rows"] if r["key"] != "net_income"]
    rows += [{"kind": "formula", "key": "fml_5aew2", "label": "Total Expenses",
              "formula": "total_cogs+total_admin+total_ga+total_below"},
             {"kind": "formula", "key": "fml_noi", "label": "Net Operating Income (Before Allocation)",
              "formula": "total_revenue - fml_5aew2"},
             {"kind": "allocation", "key": "alc_zorb3", "label": "GMO Allocation"},
             {"kind": "allocation", "key": "alc_xttqu", "label": "Sales & Marketing Allocation"},
             {"kind": "formula", "key": "net_income", "label": "Net income",
              "formula": "ebitda - total_below - alc_zorb3 - alc_xttqu"}]
    d["rows"] = rows
    return d


class Recover(Base):
    def setUp(self):
        super().setUp()
        import frappe
        from neotec_insight.neotec_insight.api import recovery as rec
        self.rec = rec
        frappe.db.set_value("Insight Report Definition", "consolidated-p-and-l",
                            "definition_json", json.dumps(custom_definition()))
        frappe.db.commit()

    def replay_the_loss(self):
        """What happened on the site: forced delete, then after_migrate
        re-created the stock preset under the same name."""
        import frappe
        frappe.delete_doc("Insight Report Definition", "consolidated-p-and-l", force=1)
        frappe.get_doc({"doctype": "Insight Report Definition", "slug": "consolidated-p-and-l",
                        "report_name": "Consolidated P&L", "report_type": "pnl", "version": 1,
                        "seed_source": "app-preset",
                        "definition_json": json.dumps(PRESET["definition"])}).insert(
            set_name="consolidated-p-and-l")
        frappe.db.commit()


class TestReportVersions(Recover):
    def test_deleted_custom_version_is_found_first_and_restored(self):
        self.replay_the_loss()
        _, st = self.a._pool_with_status(self.rule(), "IRSAA", 2026)
        v = self.rec.report_versions("consolidated-p-and-l")
        self.assertEqual(v["current"]["summary"]["missing_pool_rows"], ["fml_5aew2"])
        best = v["versions"][0]
        self.assertEqual(best["source"], "deleted copy")
        self.assertEqual(best["summary"]["has_pool_rows"], ["fml_5aew2"])
        self.assertEqual(best["summary"]["allocation_rows"], ["GMO Allocation", "Sales & Marketing Allocation"])
        self.assertIn("alc_zorb3", best["summary"]["last_formula"])
        out = self.rec.restore_report_version("consolidated-p-and-l", best["id"])
        self.assertTrue(out["pre_restore_snapshot"])
        import frappe
        rows = json.loads(frappe.db.get_value("Insight Report Definition", "consolidated-p-and-l",
                                              "definition_json"))["rows"]
        self.assertIn("alc_xttqu", [r["key"] for r in rows])
        self.assertEqual(self.rec.report_versions("consolidated-p-and-l")["current"]["summary"]
                         ["has_pool_rows"], ["fml_5aew2"])

    def test_version_history_is_a_source(self):
        import frappe
        old = json.dumps(custom_definition())
        frappe.db.set_value("Insight Report Definition", "consolidated-p-and-l", "definition_json",
                            json.dumps(PRESET["definition"]))
        frappe.get_doc({"doctype": "Version", "ref_doctype": "Insight Report Definition",
                        "docname": "consolidated-p-and-l",
                        "data": json.dumps({"changed": [["definition_json", old,
                                                         json.dumps(PRESET["definition"])]]})}).insert()
        frappe.db.commit()
        v = self.rec.report_versions("consolidated-p-and-l")
        self.assertEqual(v["versions"][0]["source"], "version (before change)")
        self.assertEqual(v["versions"][0]["summary"]["has_pool_rows"], ["fml_5aew2"])

    def test_current_version_is_not_listed(self):
        v = self.rec.report_versions("consolidated-p-and-l")
        self.assertEqual(v["versions"], [])

    def test_diagnose_offers_the_restore(self):
        self.replay_the_loss()
        c = {x["key"]: x for x in self.a.diagnose(RULE, 2026)["checks"]}
        self.assertEqual(c["pool"]["fix"], "pick_row")
        self.assertEqual(c["report_version"]["fix"], "restore_report")
        self.rec.restore_report_version(c["report_version"]["report"], c["report_version"]["version_id"])
        c = {x["key"]: x for x in self.a.diagnose(RULE, 2026)["checks"]}
        self.assertTrue(c["pool"]["ok"])

    def test_restore_when_the_report_is_missing_entirely(self):
        import frappe
        frappe.delete_doc("Insight Report Definition", "consolidated-p-and-l", force=1)
        frappe.db.commit()
        v = self.rec.report_versions("consolidated-p-and-l")
        self.assertIsNone(v["current"])
        self.rec.restore_report_version("consolidated-p-and-l", v["versions"][0]["id"])
        self.assertTrue(frappe.db.exists("Insight Report Definition", "consolidated-p-and-l"))


class TestPresetNoLongerResurrects(Recover):
    def test_deleted_report_is_not_replaced_by_the_stock_preset(self):
        import frappe
        from neotec_insight.neotec_insight.utils.report_presets import sync_default_app_report_presets
        frappe.delete_doc("Insight Report Definition", "consolidated-p-and-l", force=1)
        frappe.db.commit()
        sync_default_app_report_presets()
        self.assertFalse(frappe.db.exists("Insight Report Definition", "consolidated-p-and-l"))
        self.assertTrue(any("preset not re-created" in e for e in self.site.errors))


class TestDeletedRecords(Recover):
    def test_summary_list_and_restore(self):
        import frappe
        from test_v2_89_end_to_end import entries
        gone = entries()[:5]
        for e in gone:
            frappe.delete_doc("Insight Allocation Entry", e["name"])
        frappe.db.commit()
        s = {r["doctype"]: r for r in self.rec.deleted_summary()}
        self.assertEqual(s["Insight Allocation Entry"]["count"], 5)
        lst = self.rec.deleted_list("Insight Allocation Entry")
        self.assertIn(RULE, lst[0]["title"])
        out = self.rec.restore_deleted([r["name"] for r in lst])
        self.assertEqual(out["restored"], 5)
        self.assertEqual(len(entries()), 28)
        self.assertEqual(self.rec.deleted_summary(), [])

    def test_name_in_use_is_skipped_or_replaced(self):
        import frappe
        frappe.delete_doc("Insight Report Definition", "consolidated-p-and-l", force=1)
        frappe.get_doc({"doctype": "Insight Report Definition", "slug": "consolidated-p-and-l",
                        "report_name": "stock", "report_type": "pnl", "version": 1,
                        "definition_json": "{}"}).insert(set_name="consolidated-p-and-l")
        frappe.db.commit()
        lst = self.rec.deleted_list("Insight Report Definition")
        self.assertTrue(lst[0]["exists_now"])
        self.assertEqual(self.rec.restore_deleted([lst[0]["name"]])["skipped"], 1)
        self.assertEqual(self.rec.restore_deleted([lst[0]["name"]], on_conflict="replace")["replaced"], 1)
        self.assertNotEqual(frappe.db.get_value("Insight Report Definition", "consolidated-p-and-l",
                                                "report_name"), "stock")


class TestSummarize(Base):
    def test_pure_summary(self):
        from neotec_insight.neotec_insight.api.recovery import summarize_definition
        s = summarize_definition(custom_definition(), ["fml_5aew2", "gone"])
        self.assertEqual(s["has_pool_rows"], ["fml_5aew2"])
        self.assertEqual(s["missing_pool_rows"], ["gone"])
        self.assertEqual(len(s["allocation_rows"]), 2)
        self.assertEqual(summarize_definition("not json")["rows"], 0)


if __name__ == "__main__":
    unittest.main()
