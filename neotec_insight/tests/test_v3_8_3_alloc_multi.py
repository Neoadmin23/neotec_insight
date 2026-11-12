"""v3.8.3 — allocation rows with several cost centres (user-selectable mode)
and per-row "hide when" conditions."""
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
sys.path.insert(0, str(HERE))
import _fakefrappe as ff  # noqa: E402

# Procurement is the source (credited −300); Finance receives 200, GMO 100.
SHARE = {"Procurement - IR": -300.0, "Finance - IR": 200.0, "GMO - IR": 100.0}
ROWS = [{"key": "alloc", "label": "Procurement Allocation", "kind": "allocation", "allocation_rule": "R1"},
        {"key": "net", "label": "Net after allocation", "kind": "formula", "formula": "alloc"},
        {"key": "zero", "label": "Always zero", "kind": "formula", "formula": "0", "hide_if": "all_zero"}]


class AllocMulti(unittest.TestCase):
    def setUp(self):
        self.site = ff.install()
        import frappe
        self.frappe = frappe
        from neotec_insight.neotec_insight.utils import execution as ex
        self.ex = ex
        ex._allocation_monthly = lambda **k: ({m: SHARE.get(k["cost_center"], 0.0) for m in k["months"]}, True)

    def tearDown(self):
        ff.uninstall()

    def run_(self, ccs, mode=None):
        self.frappe.form_dict = {"alloc_multi": mode} if mode else {}
        out = self.ex.execute_report(report_def={"rows": ROWS}, fiscal_year=2026, month_from=1, month_to=1,
                                     cost_center=ccs, company="IR", flag_to_accounts={})
        return {r["key"]: r for r in out["rows"]}

    def test_net_cancels_between_selected_centres(self):
        r = self.run_(["Procurement - IR", "Finance - IR"])            # default mode = net
        self.assertEqual(r["alloc"]["monthly"][1], -100.0)             # only GMO's 100 leaves the selection
        self.assertFalse(r["alloc"].get("hidden"))                     # visible with several centres
        self.assertEqual(r["net"]["monthly"][1], -100.0)

    def test_received_only(self):
        self.assertEqual(self.run_(["Procurement - IR", "Finance - IR"], "received")["alloc"]["monthly"][1], 200.0)

    def test_one_row_per_centre(self):
        r = self.run_(["Procurement - IR", "Finance - IR"], "per_cc")
        self.assertTrue(r["alloc"].get("hidden"))                      # the combined row only feeds totals
        self.assertEqual(r["net"]["monthly"][1], -100.0)              # totals use the net
        per = sorted((x["label"], x["monthly"][1]) for k, x in r.items() if k.startswith("alloc__"))
        self.assertEqual(per, [("Procurement Allocation · Finance - IR", 200.0),
                               ("Procurement Allocation · Procurement - IR", -300.0)])

    def test_hide_conditions(self):
        r = self.run_(["Finance - IR"])
        self.assertTrue(r["zero"].get("hidden"))
        h = self.ex.hide_by_condition
        self.assertTrue(h("total_zero", {1: 5.0, 2: -5.0}))
        self.assertFalse(h("all_zero", {1: 5.0, 2: -5.0}))
        self.assertTrue(h("total_negative", {1: -1.0}))
        self.assertTrue(h("total_positive", {1: 1.0}))
        self.assertFalse(h("bogus", {1: 0.0}))


if __name__ == "__main__":
    unittest.main()
