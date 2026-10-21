"""v2.99.0 — dashboard designer: sharing, layout hygiene, and page filters."""
import json
import shutil
import subprocess
import sys
import types
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
sys.path.insert(0, str(HERE))
import _fakefrappe as ff  # noqa: E402

VIS = {"id": "v1", "title": "Bridge", "type": "waterfall", "source": {"kind": "analysis", "key": "profit_bridge"},
       "x": 0, "y": 0, "w": 6, "h": 4}


class Base(unittest.TestCase):
    def setUp(self):
        self.site = ff.install()
        import frappe
        self.frappe = frappe
        from neotec_insight.neotec_insight.api import boards
        self.b = boards

    def tearDown(self):
        ff.uninstall()

    def as_user(self, u, roles):
        self.site.user = u
        self.site.roles[u] = set(roles)


class Sharing(Base):
    def test_owner_edits_shared_role_views_others_refused(self):
        self.as_user("cfo@a.co", {"Accounts Manager"})
        name = self.b.save_board(title="CFO view", layout=[VIS], filters={"company": "Alpha"},
                                 shared_roles="Insight Viewer")["name"]
        self.site.table("Insight Board")[name]["owner"] = "cfo@a.co"
        g = self.b.get_board(name)
        self.assertTrue(g["can_edit"])
        self.assertEqual(g["layout"][0]["type"], "waterfall")
        self.as_user("viewer@a.co", {"Accounts User", "Insight Viewer"})
        self.assertFalse(self.b.get_board(name)["can_edit"])
        self.assertEqual([x["name"] for x in self.b.list_boards()], [name])
        with self.assertRaises(self.frappe.PermissionError):
            self.b.save_board(name=name, title="hijack", layout=[VIS])
        with self.assertRaises(self.frappe.PermissionError):
            self.b.delete_board(name)
        self.as_user("other@a.co", {"Accounts User"})
        self.assertEqual(self.b.list_boards(), [])
        with self.assertRaises(self.frappe.PermissionError):
            self.b.get_board(name)

    def test_people_only_refused(self):
        self.as_user("hr@a.co", {"HR Manager"})
        with self.assertRaises(self.frappe.PermissionError):
            self.b.list_boards()


class Layout(unittest.TestCase):
    def test_clean_layout(self):
        sys.path.insert(0, str(HERE))
        ff.install()
        try:
            from neotec_insight.neotec_insight.api.boards import clean_layout
            out = clean_layout([
                {**VIS, "x": 11, "w": 6, "h": 40},
                {**VIS, "id": "bad", "type": "3d-donut"},
                {**VIS, "id": "bad2", "source": {"kind": "sql", "key": "x"}},
                {**VIS, "id": "bad3", "source": {"kind": "analysis"}},
                "junk"])
            self.assertEqual(len(out), 1)
            self.assertEqual((out[0]["x"], out[0]["w"], out[0]["h"]), (6, 6, 12))
        finally:
            ff.uninstall()


class Filters(Base):
    def test_analysis_gets_company_and_period(self):
        seen = {}
        mod = types.ModuleType("neotec_insight.neotec_insight.api.analysis_pack")
        mod.compute = lambda key, company, f, t: seen.update(key=key, company=company, f=f, t=t) or {"key": key}
        sys.modules[mod.__name__] = mod
        self.b.run_visual({"kind": "analysis", "key": "dupont"},
                          {"company": "Alpha", "from_date": "2026-01-01", "to_date": "2026-03-31"})
        self.assertEqual(seen, {"key": "dupont", "company": "Alpha", "f": "2026-01-01", "t": "2026-03-31"})

    def test_studio_filters_only_where_fields_exist(self):
        self.site.metas["Sales Invoice"] = {"name": "Sales Invoice", "fields": [
            {"fieldname": "company", "fieldtype": "Link"}, {"fieldname": "posting_date", "fieldtype": "Date"},
            {"fieldname": "grand_total", "fieldtype": "Currency"}]}
        self.site.table("Studio Report")["sales"] = {"name": "sales", "config_json": json.dumps({
            "doctype": "Sales Invoice", "columns": ["grand_total"],
            "filters": [{"field": "status", "op": "=", "value": "", "ask": 1}, {"field": "docstatus", "op": "=", "value": 1}]})}
        got = {}
        from neotec_insight.neotec_insight.api import studio
        studio.run_query = lambda cfg: got.update(cfg) or {"columns": [{"field": "grand_total", "label": "Total", "numeric": True}],
                                                           "rows": [{"grand_total": 5}]}
        d = self.b.run_visual({"kind": "studio", "report": "sales"},
                              {"company": "Alpha", "from_date": "2026-01-01", "to_date": "2026-03-31", "cost_center": "Main"})
        fields = [f["field"] for f in got["filters"]]
        self.assertEqual(fields, ["docstatus", "company", "posting_date"])      # no cost_center field; ask-filter dropped
        self.assertEqual(d["applied"], ["company", "posting_date"])
        self.assertEqual(d["columns"][0]["type"], "currency")


@unittest.skipUnless(shutil.which("node"), "node not installed")
class ScreenLogic(unittest.TestCase):
    def test_board_core(self):
        r = subprocess.run(["node", "--experimental-strip-types", "--no-warnings", "scripts/test-board.mjs"],
                           cwd=HERE.parents[1] / "reportManager", capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr or r.stdout)


if __name__ == "__main__":
    unittest.main()
