"""v2.89.6 — a report's account map can no longer be wiped by an import that
matches nothing, and a lost map comes back from the log or a snapshot."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_v2_89_2_layout_and_pool import Base  # noqa: E402

REPORT = "consolidated-p-and-l"
ACCOUNTS = {"4100 - Revenue - IRSAA": ("4100", "Revenue", "Revenue"),
            "4200 - Other Revenue - IRSAA": ("4200", "Other Revenue", "Other Revenue"),
            "5100 - Rent - IRSAA": ("5100", "Rent", "Rent")}


class MapBase(Base):
    def setUp(self):
        super().setUp()
        import frappe
        for name, (code, an, _flag) in ACCOUNTS.items():
            self.site.table("Account")[name] = {"name": name, "account_number": code,
                                                "account_name": an, "company": "IRSAA"}
        for name, (_c, _n, flag) in ACCOUNTS.items():
            frappe.get_doc({"doctype": "Account Flag Mapping", "report": REPORT, "account": name,
                            "flag": flag, "source": "manual"}).insert()
        frappe.db.commit()
        from neotec_insight.neotec_insight.api import recovery
        from neotec_insight.neotec_insight.utils import map_importer
        self.rec, self.mi = recovery, map_importer

    def count(self):
        import frappe
        return frappe.db.count("Account Flag Mapping", {"report": REPORT})


class TestImporter(MapBase):
    def test_sheet_matching_nothing_keeps_the_map(self):
        r = self.mi.apply_map_to_report(report=REPORT, parsed={"rows": [
            {"flag": "Revenue", "code": "9999", "name": "No such account"}], "warnings": []},
            replace=True, company="IRSAA")
        self.assertEqual(self.count(), 3)
        self.assertEqual(r["removed"], 0)
        self.assertTrue(any("Replace skipped" in w for w in r["warnings"]))

    def test_replace_removes_only_what_the_sheet_left_out_and_logs_it(self):
        r = self.mi.apply_map_to_report(report=REPORT, parsed={"rows": [
            {"flag": "Revenue", "code": "4100", "name": ""},
            {"flag": "Other Revenue", "code": "4200", "name": ""}], "warnings": []},
            replace=True, company="IRSAA")
        self.assertEqual((self.count(), r["removed"]), (2, 1))
        self.assertEqual([d["deleted_name"] for d in self.site.deleted_log
                          if d["deleted_doctype"] == "Account Flag Mapping"].__len__(), 1)


class TestRecoverMappings(MapBase):
    def wipe(self):
        import frappe
        for n in frappe.get_all("Account Flag Mapping", filters={"report": REPORT}, pluck="name"):
            frappe.delete_doc("Account Flag Mapping", n, force=1)
        frappe.db.commit()

    def test_from_the_deleted_log(self):
        self.wipe()
        src = self.rec.mapping_sources(REPORT)
        self.assertEqual(src["current"], 0)
        self.assertEqual(src["sources"][0]["kind"], "deleted")
        self.assertEqual(src["sources"][0]["count"], 3)
        out = self.rec.restore_mappings(REPORT, "deleted")
        self.assertEqual((out["restored"], out["now"]), (3, 3))
        self.assertEqual(self.rec.mapping_sources(REPORT)["sources"], [])     # log marked restored

    def test_from_a_snapshot_even_when_the_log_is_empty(self):
        import frappe
        from neotec_insight.neotec_insight.utils import snapshots
        snap = snapshots.take("manual")["name"]
        for n in frappe.get_all("Account Flag Mapping", filters={"report": REPORT}, pluck="name"):
            frappe.delete_doc("Account Flag Mapping", n, force=1, delete_permanently=True)  # raw-style loss
        frappe.db.commit()
        src = self.rec.mapping_sources(REPORT)
        hit = [s for s in src["sources"] if s["kind"] == "snapshot"]
        self.assertEqual((hit[0]["ref"], hit[0]["count"]), (snap, 3))
        out = self.rec.restore_mappings(REPORT, "snapshot", snap)
        self.assertEqual(out["now"], 3)

    def test_already_mapped_accounts_are_not_duplicated(self):
        import frappe
        from neotec_insight.neotec_insight.utils import snapshots
        snap = snapshots.take("manual")["name"]
        n = frappe.get_all("Account Flag Mapping", filters={"report": REPORT}, pluck="name")[0]
        frappe.delete_doc("Account Flag Mapping", n, force=1, delete_permanently=True)
        frappe.db.commit()
        out = self.rec.restore_mappings(REPORT, "snapshot", snap)
        self.assertEqual((out["restored"], out["already_mapped"], out["now"]), (1, 2, 3))

    def test_snapshot_index_counts_mappings_per_report(self):
        import frappe, json
        from neotec_insight.neotec_insight.utils import snapshots
        snap = snapshots.take("manual")["name"]
        idx = json.loads(frappe.db.get_value(snapshots.DT, snap, "index_json"))
        self.assertEqual(idx["mappings_by_report"][REPORT], 3)


if __name__ == "__main__":
    unittest.main()
