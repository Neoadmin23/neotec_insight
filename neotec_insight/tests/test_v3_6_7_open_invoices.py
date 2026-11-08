"""v3.6.7 — ageing pack Invoiced / Paid cover open invoices only (the
reported example: 6,000 / 1,900 / 4,100, not 9,000 / 4,900 / 4,100)."""
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
    rows = [{"party": "CUST-A", "label": "Customer-A", "invoiced": 9000.0, "paid": 4900.0, "outstanding": 4100.0,
             "not_due": 0.0, "buckets": [4100.0, 0, 0, 0, 0]}] if party_type == "Customer" else []
    return {"rows": rows, "labels": _slab_labels([30, 60, 90, 120], "days"), "currency": "SAR"}


def billwise(company=None, party_type="Customer", parties=None, as_of=None, **k):
    # Inv010 (3,000 fully received) is no longer open, so the engine does not list it
    return {"blocks": [{"party": "CUST-A", "open_bills": [
        {"bill": "Inv001", "date": "2026-09-01", "amount": 1000.0, "remaining": 600.0, "age": 60, "slab": "31–60 Days"},
        {"bill": "Inv045", "date": "2026-10-01", "amount": 5000.0, "remaining": 3500.0, "age": 30, "slab": "0–30 Days"}],
        "rows": [{"voucher_type": "Payment Entry", "voucher_no": "PE-1", "against": "Inv001", "allocated": 300.0},
                 {"voucher_type": "Sales Invoice", "voucher_no": "CN-1", "against": "Inv001", "allocated": 100.0},
                 {"voucher_type": "Payment Entry", "voucher_no": "PE-2", "against": "Inv045", "allocated": 1400.0},
                 {"voucher_type": "Journal Entry", "voucher_no": "JV-1", "against": "Inv045", "allocated": 100.0},
                 {"voucher_type": "Payment Entry", "voucher_no": "PE-0", "against": "Inv010", "allocated": 3000.0}],
        "unallocated": []}] if "CUST-A" in (parties or []) else []}


class OpenInvoices(unittest.TestCase):
    def setUp(self):
        self.site = ff.install()
        import frappe
        self.frappe = frappe
        self.site.table("Company")["Alpha Co"] = {"name": "Alpha Co", "company_name": "Alpha", "default_currency": "SAR"}
        frappe.local.response = types.SimpleNamespace()
        frappe.utils.get_url = lambda *a: "https://x"
        from neotec_insight.neotec_insight.api import ageing as ag, packs
        self.packs = packs
        ag.ar_ap_ageing, ag.billwise = ageing, billwise
        packs._notes_by_party = lambda c, d, pt: {}
        packs.open_invoice_docs = lambda *a, **k: None      # the ledger-based path (past report dates)
        packs._seed_ageing_pack()

    def tearDown(self):
        ff.uninstall()

    def ar(self, **opts):
        rec = self.site.table("Insight Export Pack")["ar_ap_ageing"]
        cfg = json.loads(rec["config_json"])
        cfg["sheets"][1]["options"].update(opts)
        rec["config_json"] = json.dumps(cfg)
        self.packs.generate_pack("ar_ap_ageing", "Alpha Co", "2026-10-31", "2026-10-31")
        import openpyxl
        return openpyxl.load_workbook(io.BytesIO(self.frappe.local.response.filecontent))["AR Ageing"]

    def test_reported_example(self):
        ws = self.ar()
        # v3.6.8 — received split by how it was settled; the settled Inv010 contributes nothing
        self.assertEqual((ws["B10"].value, ws["C10"].value, ws["D10"].value, ws["E10"].value, ws["F10"].value),
                         ("Customer-A", 6000.0, 1700.0, 100.0, 100.0))
        self.assertEqual(ws["F9"].value, "Adjustment")
        self.assertEqual(ws["G10"].value, "=C10-D10-E10-F10")        # 6,000 − 1,700 − 100 − 100 = 4,100
        self.assertEqual(6000.0 - 1900.0, 4100.0)

    def test_all_invoices_on_request(self):
        ws = self.ar(amounts="all")
        self.assertEqual((ws["C10"].value, ws["D10"].value), (9000.0, 4900.0))


if __name__ == "__main__":
    unittest.main()
