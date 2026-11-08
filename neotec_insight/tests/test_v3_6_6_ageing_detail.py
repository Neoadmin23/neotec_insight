"""v3.6.6 — ageing pack: the documents behind each party's balance, linked
from the summary (party name → detail, ↩ back) and to ERPNext."""
import io
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

OUT = Path("/tmp/ageing_detail_test.xlsx")


def ageing(company=None, as_of=None, party_type="Customer", split_not_due=0, **k):
    from neotec_insight.neotec_insight.api.ageing import _slab_labels
    rows = []
    if party_type == "Supplier":
        rows = [{"party": "SUP-ORA", "label": "Oracle Systems Limited", "invoiced": 2000.0, "paid": 500.0,
                 "outstanding": 1500.0, "not_due": 0.0, "buckets": [1000.0, 500.0, 0, 0, 0]},
                {"party": "SUP-DT", "label": "Digital Twins", "invoiced": 100.0, "paid": 150.0,
                 "outstanding": -50.0, "not_due": 0.0, "buckets": [-50.0, 0, 0, 0, 0]}]
    return {"rows": rows, "labels": _slab_labels([30, 60, 90, 120], "days"), "currency": "SAR"}


def billwise(company=None, party_type="Customer", parties=None, as_of=None, **k):
    blocks = {"SUP-ORA": {"party": "SUP-ORA", "open_bills": [
                  {"bill": "ACC-PINV-2026-00010", "date": "2026-10-20", "amount": 1000.0, "remaining": 1000.0, "age": 11, "slab": "0–30 Days"},
                  {"bill": "ACC-PINV-2026-00007", "date": "2026-09-10", "amount": 1000.0, "remaining": 500.0, "age": 51, "slab": "31–60 Days"}],
                  "unallocated": []},
              "SUP-DT": {"party": "SUP-DT", "open_bills": [],
                         "unallocated": [{"posting_date": "2026-10-25", "voucher_type": "Payment Entry",
                                          "voucher_no": "ACC-PAY-2026-01400", "amount": 50.0}]}}
    return {"blocks": [blocks[p] for p in parties or [] if p in blocks]}


class Detail(unittest.TestCase):
    def setUp(self):
        self.site = ff.install()
        import frappe
        self.frappe = frappe
        self.site.table("Company")["Alpha Co"] = {"name": "Alpha Co", "company_name": "Alpha", "default_currency": "SAR"}
        for n in ("ACC-PINV-2026-00010", "ACC-PINV-2026-00007"):
            self.site.table("Purchase Invoice")[n] = {"name": n}
        frappe.local.response = types.SimpleNamespace()
        frappe.utils.get_url = lambda *a: "https://neoirsaa.frappe.cloud"
        from neotec_insight.neotec_insight.api import ageing as ag, packs
        self.packs = packs
        ag.ar_ap_ageing, ag.billwise = ageing, billwise
        packs._notes_by_party = lambda c, d, pt: {}
        packs.open_invoice_docs = lambda *a, **k: None      # the ledger-based path (past report dates)
        packs._seed_ageing_pack()

    def tearDown(self):
        ff.uninstall()

    def build(self, **ap_opts):
        rec = self.site.table("Insight Export Pack")["ar_ap_ageing"]
        cfg = json.loads(rec["config_json"])
        cfg["sheets"][2]["options"].update(ap_opts)
        rec["config_json"] = json.dumps(cfg)
        self.packs.generate_pack("ar_ap_ageing", "Alpha Co", "2026-10-31", "2026-10-31")
        data = self.frappe.local.response.filecontent
        OUT.write_bytes(data)
        import openpyxl
        return openpyxl.load_workbook(io.BytesIO(data))

    def test_links_both_ways_and_to_erpnext(self):
        wb = self.build()
        self.assertIn("AP Ageing Detail", wb.sheetnames)
        ap, det = wb["AP Ageing"], wb["AP Ageing Detail"]
        self.assertEqual(ap["B10"].hyperlink.location, "'AP Ageing Detail'!B5")          # party → its section
        self.assertEqual(det["B5"].value, "Oracle Systems Limited")
        self.assertEqual(det["I5"].hyperlink.location, "'AP Ageing'!B10")                 # ↩ back
        self.assertEqual(det["C7"].value, "ACC-PINV-2026-00010")
        self.assertTrue(det["C7"].hyperlink.target.endswith("/purchase-invoice/ACC-PINV-2026-00010"))
        cells = [det.cell(row=r, column=3) for r in range(5, 30)]
        pay = next(c for c in cells if c.value == "ACC-PAY-2026-01400")
        self.assertTrue(pay.hyperlink.target.endswith("/payment-entry/ACC-PAY-2026-01400"))
        self.assertEqual(det.cell(row=pay.row, column=9).value, -50.0)                    # unallocated reduces

    def test_option_off(self):
        self.assertNotIn("AP Ageing Detail", self.build(details=0).sheetnames)

    @unittest.skipUnless(shutil.which("soffice") or shutil.which("libreoffice"), "LibreOffice not installed")
    def test_party_totals_match_the_summary_after_recalculation(self):
        self.build()
        r = subprocess.run([sys.executable, "/mnt/skills/public/xlsx/scripts/recalc.py", str(OUT), "60"],
                           capture_output=True, text=True, timeout=120)
        self.assertIn('"total_errors": 0', r.stdout.replace(" ", "").replace('"total_errors":0', '"total_errors": 0'))
        import openpyxl
        det = openpyxl.load_workbook(OUT, data_only=True)["AP Ageing Detail"]
        checks = [det.cell(row=r, column=8).value for r in range(5, 40) if det.cell(row=r, column=3).value == "Party total"]
        self.assertEqual(checks, ["✓ matches summary", "✓ matches summary"])


if __name__ == "__main__":
    unittest.main()
