"""v3.7.1 — 'open' is the invoice's own outstanding (as ERPNext), so USD
invoices left with only exchange-rate residue in SAR no longer count.
Reported case (Oracle): prepared report 595,200 / 199,887 / 395,313; the app
showed 2,299,869.59 invoiced."""
import io
import json
import sys
import types
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
sys.path.insert(0, str(HERE))
import _fakefrappe as ff  # noqa: E402


def ageing(company=None, as_of=None, party_type="Customer", split_not_due=0, **k):
    from neotec_insight.neotec_insight.api.ageing import _slab_labels
    rows = [{"party": "ORA", "label": "Oracle Systems Limited", "invoiced": 0, "paid": 0, "outstanding": 395312.50,
             "not_due": 0.0, "buckets": [0, 395312.50, 0, 0, 0]}] if party_type == "Supplier" else []
    return {"rows": rows, "labels": _slab_labels([30, 60, 90, 120], "days"), "currency": "SAR"}


def billwise(company=None, party_type="Customer", parties=None, as_of=None, **k):
    # the ledger engine sees many invoices with small SAR residue left after USD payments
    bills = [{"bill": "PINV-OPEN-1", "date": "2026-09-20", "amount": 595200.0, "remaining": 395312.0, "age": 45, "slab": "31–60 Days"}]
    bills += [{"bill": f"PINV-OLD-{i}", "date": "2026-03-01", "amount": 100000.0, "remaining": 0.5, "age": 240, "slab": "121+ Days"}
              for i in range(17)]
    rows = [{"voucher_type": "Payment Entry", "voucher_no": "PE-9", "against": "PINV-OPEN-1", "allocated": 199887.0}]
    return {"blocks": [{"party": "ORA", "open_bills": bills, "unallocated": [], "rows": rows}]}


class FxOpen(unittest.TestCase):
    def setUp(self):
        self.site = ff.install()
        import frappe
        self.frappe = frappe
        self.site.table("Company")["Alpha Co"] = {"name": "Alpha Co", "company_name": "Alpha", "default_currency": "SAR"}
        self.site.table("Purchase Invoice")["PINV-OPEN-1"] = {"name": "PINV-OPEN-1"}
        frappe.local.response = types.SimpleNamespace()
        frappe.utils.get_url = lambda *a: "https://x"
        from neotec_insight.neotec_insight.api import ageing as ag, packs
        self.packs = packs
        ag.ar_ap_ageing, ag.billwise = ageing, billwise
        packs._notes_by_party = lambda *a: {}
        # ERPNext: only PINV-OPEN-1 has an outstanding (USD 105,416.53 × 3.75 = 395,312.0 SAR)
        packs.open_invoice_docs = lambda company, pt, parties, as_of: {
            "ORA": {"PINV-OPEN-1": {"base_total": 595200.0, "base_outstanding": 395312.0}}} if pt == "Supplier" else {}
        packs._seed_ageing_pack()

    def tearDown(self):
        ff.uninstall()

    def test_matches_the_prepared_report(self):
        self.packs.generate_pack("ar_ap_ageing", "Alpha Co", "2026-11-07", "2026-11-07")
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(self.frappe.local.response.filecontent))
        ap = wb["AP Ageing"]
        self.assertEqual((ap["C10"].value, ap["D10"].value, ap["E10"].value), (595200.0, 199887.0, 0.0))
        self.assertAlmostEqual(ap["F10"].value, 0.50, places=2)               # exchange / rounding residue
        self.assertEqual(ap["G10"].value, "=C10-D10-E10-F10")                  # = 395,312.50 = the ageing balance
        det = wb["AP Ageing Detail"]
        docs = [det.cell(row=r, column=3).value for r in range(5, 40)]
        self.assertIn("PINV-OPEN-1", docs)
        self.assertFalse(any(str(d).startswith("PINV-OLD") for d in docs))   # residue-only invoices not listed
        self.assertIn("Exchange / rounding differences", docs)

    def test_rule_only_for_current_dates(self):
        real = __import__("neotec_insight.neotec_insight.api.packs", fromlist=["x"])
        import importlib
        mod = importlib.reload(real)
        self.assertIsNone(mod.open_invoice_docs("Alpha Co", "Supplier", ["ORA"], "2000-01-01"))


if __name__ == "__main__":
    unittest.main()
