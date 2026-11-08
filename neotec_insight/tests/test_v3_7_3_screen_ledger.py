"""v3.7.3 — the Ageing screen ("actual" allocation) reads the Payment Ledger,
so it shows what ERPNext's AP report and the pack show."""
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
sys.path.insert(0, str(HERE))
import _fakefrappe as ff  # noqa: E402
from test_v3_7_2_payment_ledger import CUSTOMER, O, SUPPLIER  # noqa: E402


class ScreenLedger(unittest.TestCase):
    def setUp(self):
        self.site = ff.install()
        import frappe
        self.frappe = frappe
        self.site.table("Company")["DSC"] = {"name": "DSC", "company_name": "DSC", "default_currency": "SAR"}
        self.site.table("Supplier")[O] = {"name": O, "supplier_name": "Oracle Systems Limited"}
        frappe.has_permission = lambda *a, **k: True
        self.gl_queries = 0

        def sql(q, p=None, as_dict=False):
            if "Payment Ledger Entry" in q:
                return [dict(r) for r in (SUPPLIER if p[1] == "Supplier" else CUSTOMER)]
            return []
        frappe.db.sql = sql
        from neotec_insight.neotec_insight.api import ageing, packs
        self.ageing = ageing
        packs._has_ple = lambda: True

    def tearDown(self):
        ff.uninstall()

    def test_screen_matches_erpnext(self):
        out = self.ageing.ar_ap_ageing(company="DSC", as_of="2026-11-08", party_type="Supplier")
        r = out["rows"][0]
        self.assertEqual((r["label"], r["invoiced"], r["paid"], r["outstanding"]),
                         ("Oracle Systems Limited", 595199.63, 199887.12, 395312.51))
        self.assertEqual(r["buckets"], [0.0, 395312.51, 0.0, 0.0, 0.0])
        self.assertEqual(out["total"]["outstanding"], 395312.51)
        self.assertIn("Payment Ledger", out["notes"][0])

    def test_exclusion_and_fifo(self):
        out = self.ageing.ar_ap_ageing(company="DSC", as_of="2026-11-08", party_type="Supplier", exclude_parties=f'["{O}"]')
        self.assertEqual(out["rows"], [])
        fifo = self.ageing.ar_ap_ageing(company="DSC", as_of="2026-11-08", party_type="Supplier", allocation="fifo")
        self.assertIn("FIFO", fifo["notes"][0])                     # the alternative view stays GL-based


if __name__ == "__main__":
    unittest.main()
