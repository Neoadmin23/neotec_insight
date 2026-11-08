"""v3.6.9 — the ageing pack covers every party: the bill-wise engine capped
its party list at 20 (a screen limit), so the 21st customer onward showed no
invoices in the pack and the summary did not reconcile with the buckets."""
import datetime as dt
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
sys.path.insert(0, str(HERE))
import _fakefrappe as ff  # noqa: E402

PARTIES = [f"CUST-{i:02d}" for i in range(1, 26)]           # 25 customers


class AllParties(unittest.TestCase):
    def setUp(self):
        self.site = ff.install()
        import frappe
        self.frappe = frappe
        for p in PARTIES:
            self.site.table("Customer")[p] = {"name": p, "customer_name": f"Customer {p}"}

        def sql(q, params=None, as_dict=False):
            if "gle.party IN" in q:                           # the bill-wise ledger query
                return [{"party": p, "posting_date": dt.date(2026, 6, 1), "due_date": dt.date(2026, 6, 1),
                         "voucher_type": "Sales Invoice", "voucher_no": f"SINV-{p}", "against_voucher_type": None,
                         "against_voucher": None, "debit": 1000.0, "credit": 0.0} for p in params["p"]]
            return []
        frappe.db.sql = sql
        from neotec_insight.neotec_insight.api import ageing
        self.ageing = ageing

    def tearDown(self):
        ff.uninstall()

    def test_pack_gets_every_party(self):
        out = self.ageing.billwise(company="Alpha", party_type="Customer", parties=PARTIES,
                                   as_of="2026-11-05", max_parties=len(PARTIES))
        blocks = {b["party"]: b for b in out["blocks"]}
        self.assertEqual(len(blocks), 25)
        self.assertEqual(blocks["CUST-25"]["open_bills"][0]["bill"], "SINV-CUST-25")   # the 25th is there

    def test_screen_still_shows_twenty(self):
        out = self.ageing.billwise(company="Alpha", party_type="Customer", parties=PARTIES, as_of="2026-11-05")
        self.assertEqual(len(out["blocks"]), 20)

    def test_the_pack_asks_for_all(self):
        src = (HERE.parents[0] / "neotec_insight" / "api" / "packs.py").read_text()
        self.assertEqual(src.count("max_parties="), 2)                 # summary amounts and detail sheet


if __name__ == "__main__":
    unittest.main()


class Flagged(unittest.TestCase):
    """A party whose invoice detail does not add up to its ageing balance is
    highlighted and counted in a warning line (no more silent gaps)."""

    def test_mismatch_highlighted(self):
        import io
        import json
        import types
        site = ff.install()
        try:
            import frappe
            from neotec_insight.neotec_insight.api import ageing as ag, packs
            from neotec_insight.neotec_insight.api.ageing import _slab_labels
            site.table("Company")["Alpha Co"] = {"name": "Alpha Co", "company_name": "Alpha", "default_currency": "SAR"}
            frappe.local.response = types.SimpleNamespace()
            frappe.utils.get_url = lambda *a: "https://x"
            ag.ar_ap_ageing = lambda **k: {"rows": [
                {"party": "C1", "label": "Fine", "invoiced": 1, "paid": 0, "outstanding": 100.0, "not_due": 0, "buckets": [100.0, 0, 0, 0, 0]},
                {"party": "C2", "label": "Broken", "invoiced": 1, "paid": 0, "outstanding": 500.0, "not_due": 0, "buckets": [500.0, 0, 0, 0, 0]}]
                if k.get("party_type") == "Customer" else [], "labels": _slab_labels([30, 60, 90, 120], "days"), "currency": "SAR"}
            ag.billwise = lambda **k: {"blocks": [{"party": "C1", "open_bills": [{"bill": "I1", "date": "2026-10-01", "amount": 100.0,
                                                                                  "remaining": 100.0, "age": 5, "slab": "0–30 Days"}],
                                                   "unallocated": [], "rows": []},
                                                  {"party": "C2", "open_bills": [], "unallocated": [], "rows": []}]}
            packs._notes_by_party = lambda *a: {}
            packs.open_invoice_docs = lambda *a, **k: None
            packs._seed_ageing_pack()
            packs.generate_pack("ar_ap_ageing", "Alpha Co", "2026-11-05", "2026-11-05")
            import openpyxl
            ws = openpyxl.load_workbook(io.BytesIO(frappe.local.response.filecontent))["AR Ageing"]
            self.assertEqual(ws["B11"].value, "Broken")
            self.assertEqual(ws["B11"].fill.fgColor.rgb[-6:], "FFE8C2")
            texts = [str(ws.cell(row=r, column=2).value or "") for r in range(10, 25)]
            self.assertTrue(any(t.startswith("⚠ 1 party") for t in texts))
            _ = json
        finally:
            ff.uninstall()
