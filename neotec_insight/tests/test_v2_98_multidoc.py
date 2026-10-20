"""v2.98.0 — Studio across several documents: two-level links and totals from
related documents, permission-checked at every step."""
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
sys.path.insert(0, str(HERE))
import _fakefrappe as ff  # noqa: E402


def F(fn, ft, opt=None, label=None):
    d = {"fieldname": fn, "fieldtype": ft, "label": label or fn.replace("_", " ").title()}
    if opt:
        d["options"] = opt
    return d


class Base(unittest.TestCase):
    def setUp(self):
        self.site = ff.install()
        import frappe
        self.frappe = frappe
        m = self.site.metas
        m["Customer"] = {"name": "Customer", "fields": [F("customer_name", "Data"), F("territory", "Link", "Territory")]}
        m["Territory"] = {"name": "Territory", "fields": [F("territory_name", "Data"),
                                                         F("parent_territory", "Link", "Territory")]}
        m["Sales Invoice"] = {"name": "Sales Invoice", "is_submittable": 1,
                              "fields": [F("customer", "Link", "Customer"), F("grand_total", "Currency"),
                                         F("remarks", "Small Text"), F("docstatus", "Int")]}
        t = self.site.table
        t("Territory")["Riyadh"] = {"name": "Riyadh", "territory_name": "Riyadh", "parent_territory": "KSA"}
        t("Territory")["Jeddah"] = {"name": "Jeddah", "territory_name": "Jeddah", "parent_territory": "KSA"}
        t("Territory")["KSA"] = {"name": "KSA", "territory_name": "KSA", "parent_territory": "All"}
        t("Customer")["Acme"] = {"name": "Acme", "customer_name": "Acme", "territory": "Riyadh", "creation": "1"}
        t("Customer")["Beta"] = {"name": "Beta", "customer_name": "Beta", "territory": "Jeddah", "creation": "2"}
        for n, (c, amt, ds) in enumerate([("Acme", 100, 1), ("Acme", 250, 1), ("Acme", 999, 0), ("Beta", 50, 1)]):
            t("Sales Invoice")[f"SI{n}"] = {"name": f"SI{n}", "customer": c, "grand_total": amt, "docstatus": ds,
                                             "creation": str(n)}
        self.site.table("DocField")["d1"] = {"name": "d1", "parent": "Sales Invoice", "fieldtype": "Link",
                                              "options": "Customer", "fieldname": "customer", "label": "Customer"}
        frappe.db.commit()
        from neotec_insight.neotec_insight.api import studio
        self.studio = studio

    def tearDown(self):
        ff.uninstall()

    def run_cfg(self, **cfg):
        base = {"doctype": "Customer", "columns": ["customer_name"], "filters": [], "limit": 100}
        base.update(cfg)
        return self.studio.run_query(base)


class TwoLevelLinks(Base):
    def test_customer_territory_parent(self):
        r = self.run_cfg(columns=["customer_name", "territory.parent_territory",
                                  "territory.parent_territory.parent_territory"])
        rows = {x["customer_name"]: x for x in r["rows"]}
        self.assertEqual(rows["Acme"]["territory.parent_territory"], "KSA")
        self.assertEqual(rows["Acme"]["territory.parent_territory.parent_territory"], "All")
        labels = {c["field"]: c["label"] for c in r["columns"]}
        self.assertEqual(labels["territory.parent_territory.parent_territory"].count("·"), 2)

    def test_unreadable_second_level_record_is_blank(self):
        self.site.perm = lambda dt, p, doc, u: (doc != "KSA") if dt == "Territory" and doc is not None else None
        r = self.run_cfg(columns=["customer_name", "territory.parent_territory.territory_name"])
        self.assertTrue(all(x["territory.parent_territory.territory_name"] is None for x in r["rows"]))

    def test_paths_are_validated(self):
        r = self.run_cfg(columns=["customer_name", "territory.nope.x", "customer_name.a.b", "territory.a.b.c"])
        self.assertEqual([c["field"] for c in r["columns"]], ["customer_name"])


class RelatedTotals(Base):
    def test_invoiced_and_count_per_customer(self):
        r = self.run_cfg(related=[
            {"doctype": "Sales Invoice", "link_field": "customer", "measure": "sum", "field": "grand_total",
             "label": "Invoiced", "submitted_only": True},
            {"doctype": "Sales Invoice", "link_field": "customer", "measure": "count", "submitted_only": True}])
        rows = {x["customer_name"]: x for x in r["rows"]}
        self.assertEqual((rows["Acme"]["rel_0"], rows["Acme"]["rel_1"]), (350.0, 2))     # draft 999 excluded
        self.assertEqual((rows["Beta"]["rel_0"], rows["Beta"]["rel_1"]), (50.0, 1))
        labels = {c["field"]: c for c in r["columns"]}
        self.assertEqual(labels["rel_0"]["label"], "Invoiced")
        self.assertTrue(labels["rel_0"]["numeric"])

    def test_including_drafts_and_other_measures(self):
        r = self.run_cfg(related=[{"doctype": "Sales Invoice", "link_field": "customer", "measure": "max",
                                   "field": "grand_total"}])
        self.assertEqual({x["customer_name"]: x["rel_0"] for x in r["rows"]}["Acme"], 999.0)

    def test_only_readable_related_records_count(self):
        self.site.perm = lambda dt, p, doc, u: (doc != "SI1") if dt == "Sales Invoice" and doc is not None else None
        r = self.run_cfg(related=[{"doctype": "Sales Invoice", "link_field": "customer", "measure": "sum",
                                   "field": "grand_total", "submitted_only": True}])
        self.assertEqual({x["customer_name"]: x["rel_0"] for x in r["rows"]}["Acme"], 100.0)

    def test_bad_specs_are_dropped(self):
        r = self.run_cfg(related=[
            {"doctype": "Sales Invoice", "link_field": "grand_total", "measure": "sum", "field": "grand_total"},
            {"doctype": "Sales Invoice", "link_field": "customer", "measure": "sum", "field": "remarks"},
            {"doctype": "Sales Invoice", "link_field": "customer", "measure": "median", "field": "grand_total"},
            {"doctype": "Territory", "link_field": "parent_territory", "measure": "count"}])
        self.assertEqual([c["field"] for c in r["columns"]], ["customer_name"])

    def test_list_related(self):
        out = self.studio.list_related("Customer")
        self.assertEqual([(o["doctype"], o["link_field"]) for o in out], [("Sales Invoice", "customer")])
        self.assertIn("grand_total", [f["fieldname"] for f in out[0]["numeric_fields"]])
        self.assertTrue(out[0]["submittable"])


class Wiring(unittest.TestCase):
    def test_run_query_is_still_an_endpoint_and_helpers_are_not(self):
        import ast
        src = (HERE.parents[0] / "neotec_insight" / "api" / "studio.py").read_text()
        wl = {n.name for n in ast.parse(src).body if isinstance(n, ast.FunctionDef)
              and any("whitelist" in ast.unparse(d) for d in n.decorator_list)}
        self.assertIn("run_query", wl)
        self.assertIn("list_related", wl)
        for helper in ("_link_target", "_apply_related", "_related_spec_ok"):
            self.assertNotIn(helper, wl)


if __name__ == "__main__":
    unittest.main()
