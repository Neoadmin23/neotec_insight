"""v2.89.0 — the backup, snapshot and allocation-save paths run end to end
against an in-memory site (tests/_fakefrappe.py) with real commit/rollback."""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))       # repo root
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _fakefrappe as ff  # noqa: E402

RULE = "GMO Allocations"


def seed(site):
    import frappe
    frappe.get_doc({"doctype": "Insight Report Definition", "slug": "consolidated-p-and-l",
                    "report_name": "Consolidated P&L", "report_type": "pnl", "version": 1,
                    "definition_json": "{}"}).insert(set_name="consolidated-p-and-l")
    frappe.get_doc({"doctype": "Insight Allocation Rule", "title": RULE, "company": "IRSAA",
                    "is_active": 1, "driver_label": "Head count",
                    "excluded_ccs": [{"cost_center": "Fixed Assets - IRSAA"}]}).insert()
    heads = {"Audit & GRC - IRSAA": [8, 8, 8, 8, 9, 8, 8],
             "Internal Audit - IRSAA": [6, 6, 6, 5, 5, 5, 5],
             "Data Processing - IRSAA": [12, 12, 11, 10, 9, 8, 7]}
    for cc, vals in heads.items():
        for m, v in enumerate(vals, 1):
            frappe.get_doc({"doctype": "Insight Allocation Entry", "rule": RULE, "company": "IRSAA",
                            "cost_center": cc, "period_month": f"2026-{m:02d}-01",
                            "basis": "head_count", "driver_value": v}).insert()
    for m in range(1, 8):
        frappe.get_doc({"doctype": "Insight Allocation Entry", "rule": RULE, "company": "IRSAA",
                        "cost_center": "Main - IRSAA", "period_month": f"2026-{m:02d}-01",
                        "basis": "amount", "amount": 45000}).insert()
    frappe.get_doc({"doctype": "Insight VAT Adjustment", "company": "IRSAA", "action": "Include",
                    "voucher_type": "Purchase Invoice", "voucher_no": "ACC-PINV-2026-00392",
                    "from_date": "2026-04-01", "to_date": "2026-06-30",
                    "reason": "Reverse charge"}).insert(set_name="VATADJ-0001")
    frappe.db.commit()


def entries():
    import frappe
    return frappe.get_all("Insight Allocation Entry", filters={"rule": RULE},
                          fields=["name", "cost_center", "period_month", "driver_value", "amount",
                                  "is_frozen", "is_override", "basis"])


class Base(unittest.TestCase):
    def setUp(self):
        self.site = ff.install()
        seed(self.site)
        from neotec_insight.neotec_insight.utils import backup_engine, snapshots
        from neotec_insight.neotec_insight.api import allocation, backup
        self.be, self.snap, self.alloc, self.backup = backup_engine, snapshots, allocation, backup

    def tearDown(self):
        ff.uninstall()


class TestExport(Base):
    def test_bundle_holds_head_counts_amounts_rules_and_data(self):
        b = self.be.build_bundle()
        self.assertEqual(b["counts"]["Insight Allocation Entry"], 28)
        self.assertEqual(b["counts"]["Insight Allocation Rule"], 1)
        self.assertEqual(b["counts"]["Insight VAT Adjustment"], 1)
        self.assertNotIn("Insight Backup Snapshot", b["data"])
        rule = b["data"]["Insight Allocation Rule"][0]
        self.assertEqual(rule["excluded_ccs"][0]["cost_center"], "Fixed Assets - IRSAA")
        self.assertNotIn("parent", rule["excluded_ccs"][0])
        self.assertLess(b["order"].index("Insight Allocation Rule"),
                        b["order"].index("Insight Allocation Entry"))
        self.assertEqual(b["format"], 2)

    def test_export_is_bulk_not_per_record(self):
        """v2.89.1 — a few queries per doctype, however many records it holds."""
        import frappe
        for i in range(300):
            frappe.get_doc({"doctype": "Insight Allocation Entry", "rule": RULE, "company": "IRSAA",
                            "cost_center": f"CC{i} - IRSAA", "period_month": "2026-08-01",
                            "basis": "head_count", "driver_value": 1}).insert()
        self.site.queries = 0
        b = self.be.build_bundle(["Insight Allocation Entry", "Insight Allocation Rule"])
        self.assertEqual(b["counts"]["Insight Allocation Entry"], 328)
        self.assertLess(self.site.queries, 15)
        rule = b["data"]["Insight Allocation Rule"][0]
        self.assertEqual(rule["excluded_ccs"][0]["cost_center"], "Fixed Assets - IRSAA")
        self.assertEqual(rule["excluded_ccs"][0]["doctype"], "Insight Allocation Excluded CC")

    def test_sections_filter(self):
        b = self.be.build_bundle(["Insight Allocation Entry"])
        self.assertEqual(list(b["data"]), ["Insight Allocation Entry"])


class TestRestore(Base):
    def test_wiped_entries_come_back_with_merge(self):
        import frappe
        bundle = json.loads(json.dumps(self.be.build_bundle(), default=str))
        for e in entries():
            frappe.delete_doc("Insight Allocation Entry", e["name"])
        frappe.db.commit()
        self.assertEqual(len(entries()), 0)
        s = self.be.restore_bundle(bundle, mode="merge")
        self.assertFalse(s["rolled_back"], s["errors"])
        self.assertEqual(len(entries()), 28)
        self.assertEqual(s["inserted"]["Insight Allocation Entry"], 28)

    def test_merge_matches_entries_by_meaning_not_name(self):
        bundle = json.loads(json.dumps(self.be.build_bundle(), default=str))
        for e in bundle["data"]["Insight Allocation Entry"]:
            e["name"] = "renamed-" + e["name"]          # another site, other hashes
        s = self.be.restore_bundle(bundle, mode="merge")
        self.assertFalse(s["rolled_back"], s["errors"])
        self.assertEqual(len(entries()), 28)
        self.assertEqual(s["updated"]["Insight Allocation Entry"], 28)

    def test_replace_removes_extras_and_keeps_names(self):
        import frappe
        bundle = json.loads(json.dumps(self.be.build_bundle(["Insight Allocation Entry"]), default=str))
        names_before = {e["name"] for e in entries()}
        frappe.get_doc({"doctype": "Insight Allocation Entry", "rule": RULE, "company": "IRSAA",
                        "cost_center": "Stray - IRSAA", "period_month": "2026-09-01",
                        "basis": "head_count", "driver_value": 3}).insert()
        frappe.db.commit()
        s = self.be.restore_bundle(bundle, mode="replace")
        self.assertFalse(s["rolled_back"], s["errors"])
        self.assertEqual({e["name"] for e in entries()}, names_before)   # updated in place
        self.assertEqual(s["deleted"]["Insight Allocation Entry"], 1)
        # an area the bundle does not carry is untouched
        self.assertEqual(frappe.db.count("Insight VAT Adjustment"), 1)

    def test_a_failing_record_rolls_everything_back(self):
        bundle = json.loads(json.dumps(self.be.build_bundle(), default=str))
        bundle["data"]["Insight Allocation Entry"].append(
            {"name": "broken", "rule": RULE, "period_month": "2026-12-01"})   # no cost_center
        before = len(entries())
        s = self.be.restore_bundle(bundle, mode="replace")
        self.assertTrue(s["rolled_back"])
        self.assertTrue(any("cost_center" in e for e in s["errors"]))
        self.assertEqual(len(entries()), before)

    def test_allow_partial_keeps_what_succeeded(self):
        bundle = json.loads(json.dumps(self.be.build_bundle(["Insight Allocation Entry"]), default=str))
        bundle["data"]["Insight Allocation Entry"].append(
            {"name": "broken", "rule": RULE, "period_month": "2026-12-01"})
        s = self.be.restore_bundle(bundle, mode="merge", atomic=False)
        self.assertFalse(s["rolled_back"])
        self.assertEqual(s["failed"]["Insight Allocation Entry"], 1)

    def test_unknown_doctype_in_bundle_is_skipped_not_fatal(self):
        bundle = json.loads(json.dumps(self.be.build_bundle(["Insight Dashboard"]), default=str))
        bundle["data"]["Insight Something Retired"] = [{"name": "x"}]
        s = self.be.restore_bundle(bundle, mode="merge")
        self.assertEqual(s["skipped_unknown"], ["Insight Something Retired"])
        self.assertFalse(s["rolled_back"])

    def test_old_bundle_without_entries_never_wipes_them(self):
        bundle = json.loads(json.dumps(self.be.build_bundle(), default=str))
        del bundle["data"]["Insight Allocation Entry"]                 # a pre-2.88.11 bundle
        self.be.restore_bundle(bundle, mode="replace")
        self.assertEqual(len(entries()), 28)


class TestSnapshots(Base):
    def test_take_dedupe_and_load(self):
        r1 = self.snap.take("scheduled", dedupe=True)
        r2 = self.snap.take("scheduled", dedupe=True)
        self.assertFalse(r1["skipped"])
        self.assertTrue(r2["skipped"])
        self.assertEqual(r2["name"], r1["name"])
        b = self.snap.load(r1["name"])
        self.assertEqual(len(b["data"]["Insight Allocation Entry"]), 28)

    def test_disappearing_entries_raise_an_alert(self):
        import frappe
        self.snap.take("scheduled")
        for e in entries():
            frappe.delete_doc("Insight Allocation Entry", e["name"])
        frappe.db.commit()
        r = self.snap.take("scheduled")
        self.assertIn("Insight Allocation Entry: 28 → 0", r["alert"])
        self.assertTrue(any("disappeared" in e for e in self.site.errors))

    def test_after_migrate_flags_loss_against_pre_migrate(self):
        import frappe
        self.snap.before_migrate()
        for e in entries():
            frappe.delete_doc("Insight Allocation Entry", e["name"])
        frappe.db.commit()
        self.snap.after_migrate()
        self.assertTrue(any("missing after migrate" in e for e in self.site.errors))
        kinds = [s["snapshot_type"] for s in frappe.get_all(self.snap.DT, fields=["snapshot_type"])]
        self.assertIn("post_migrate", kinds)

    def test_first_migrate_without_store_writes_a_file_then_adopts_it(self):
        import frappe
        meta = self.site.metas.pop(self.snap.DT)               # store not installed yet
        self.site.metas["File"] = {"name": "File", "fields": [], "autoname": "hash"}
        orig = frappe.get_doc

        class F(ff.Document):
            def get_content(self):
                return self.content
        def get_doc(arg, name=None):
            if (isinstance(arg, dict) and arg.get("doctype") == "File") or arg == "File":
                if isinstance(arg, dict):
                    return F("File", arg)
                d = F("File"); d._data.update(self.site.table("File")[name]); return d
            return orig(arg, name)
        frappe.get_doc = get_doc
        self.snap.get_doc = get_doc
        r = self.snap.take("pre_migrate")
        self.assertTrue(r.get("as_file"))
        self.site.metas[self.snap.DT] = meta                   # migrate created it
        self.assertEqual(self.snap.adopt_pending_files(), 1)
        rows = frappe.get_all(self.snap.DT, fields=["name", "snapshot_type"])
        self.assertEqual([x["snapshot_type"] for x in rows], ["pre_migrate"])
        self.assertEqual(len(self.snap.load(rows[0]["name"])["data"]["Insight Allocation Entry"]), 28)

    def test_restore_snapshot_endpoint_takes_a_pre_restore_snapshot(self):
        import frappe
        name = self.snap.take("manual")["name"]
        for e in entries():
            frappe.delete_doc("Insight Allocation Entry", e["name"])
        frappe.db.commit()
        out = self.backup.restore_snapshot(name, mode="merge")
        self.assertTrue(out["pre_restore_snapshot"])
        self.assertEqual(len(entries()), 28)

    def test_compare(self):
        name = self.snap.take("manual")["name"]
        rows = {r["doctype"]: r for r in self.backup.compare_snapshot(name)["rows"]}
        self.assertEqual(rows["Insight Allocation Entry"]["diff"], 0)


class TestGuardedSave(Base):
    def grid(self, year=2026):
        return self.alloc.get_grid(RULE, year)

    def test_grid_reports_entry_count(self):
        self.assertEqual(self.grid()["entry_count"], 28)

    def test_stale_grid_is_refused(self):
        import frappe
        with self.assertRaises(frappe.ValidationError) as cm:
            self.alloc.save_grid(RULE, 2026, cells={"Audit & GRC - IRSAA": {"8": {"driver": 8}}},
                                 basis={"Audit & GRC - IRSAA": "head_count"}, loaded_count=0)
        self.assertIn("GRID_STALE", str(cm.exception))
        self.assertEqual(len(entries()), 28)

    def test_blank_grid_cannot_wipe_figures_without_confirmation(self):
        import frappe
        g = self.grid()
        cells = {cc: {str(m): {"driver": 0, "amount": 0} for m in range(1, 13)} for cc in g["cost_centers"]}
        with self.assertRaises(frappe.ValidationError) as cm:
            self.alloc.save_grid(RULE, 2026, cells=cells, basis=g["basis"], loaded_count=g["entry_count"])
        self.assertIn("BULK_DELETE", str(cm.exception))
        self.assertEqual(len(entries()), 28)

    def test_confirmed_bulk_delete_snapshots_first_and_is_recoverable(self):
        import frappe
        g = self.grid()
        cells = {cc: {str(m): {"driver": 0, "amount": 0} for m in range(1, 13)} for cc in g["cost_centers"]}
        res = self.alloc.save_grid(RULE, 2026, cells=cells, basis=g["basis"],
                                   loaded_count=g["entry_count"], confirm_bulk=1)
        self.assertEqual(res["deleted"], 28)
        self.assertTrue(res["snapshot"])
        self.assertEqual(len(entries()), 0)
        cand = self.alloc.snapshot_candidates(RULE, 2026)["rows"]
        self.assertEqual(cand[0]["name"], res["snapshot"])
        self.assertEqual(cand[0]["entries_year"], 28)
        dry = self.alloc.restore_from_snapshot(RULE, res["snapshot"], dry_run=1)
        self.assertEqual((dry["inserted"], len(entries())), (28, 0))
        out = self.alloc.restore_from_snapshot(RULE, res["snapshot"])
        self.assertEqual(out["inserted"], 28)
        self.assertEqual(len(entries()), 28)
        self.assertEqual(sum(e["amount"] or 0 for e in entries()), 45000 * 7)
        self.assertEqual(frappe.db.count("Insight Allocation Entry"), 28)

    def test_frozen_month_survives_edit_blank_and_removal(self):
        import frappe
        jan = next(e for e in entries() if e["cost_center"] == "Audit & GRC - IRSAA"
                   and str(e["period_month"]) == "2026-01-01")
        frappe.db.set_value("Insight Allocation Entry", jan["name"], "is_frozen", 1)
        frappe.db.commit()
        g = self.grid()
        self.assertIn("Audit & GRC - IRSAA|1", g["frozen"])
        res = self.alloc.save_grid(RULE, 2026, cells={}, basis=g["basis"],
                                   remove=["Audit & GRC - IRSAA"], loaded_count=g["entry_count"])
        self.assertEqual(res["skipped_frozen"], 1)
        self.assertEqual(res["deleted"], 6)
        left = [e for e in entries() if e["cost_center"] == "Audit & GRC - IRSAA"]
        self.assertEqual([(str(e["period_month"]), e["driver_value"]) for e in left], [("2026-01-01", 8)])

    def test_unchanged_save_writes_nothing(self):
        g = self.grid()
        res = self.alloc.save_grid(RULE, 2026, cells=g["cells"], basis=g["basis"],
                                   manual_pool=g["manual_pool"], loaded_count=g["entry_count"])
        self.assertEqual((res["written"], res["deleted"]), (0, 0))
        self.assertIsNone(res["snapshot"])

    def test_old_client_without_count_still_saves(self):
        res = self.alloc.save_grid(RULE, 2026, cells={"Audit & GRC - IRSAA": {"8": {"driver": 7}}},
                                   basis={"Audit & GRC - IRSAA": "head_count"})
        self.assertEqual(res["written"], 1)

    def test_rule_itself_comes_back_from_snapshot(self):
        import frappe
        name = self.snap.take("manual")["name"]
        for e in entries():
            frappe.delete_doc("Insight Allocation Entry", e["name"])
        frappe.delete_doc("Insight Allocation Rule", RULE)
        frappe.db.commit()
        out = self.alloc.restore_from_snapshot(RULE, name)
        self.assertTrue(out["rule_restored"])
        self.assertEqual(out["inserted"], 28)
        self.assertTrue(frappe.db.exists("Insight Allocation Rule", RULE))


if __name__ == "__main__":
    unittest.main()
