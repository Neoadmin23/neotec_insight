"""v3.7.2 — the ageing pack from ERPNext's Payment Ledger, matching ERPNext's
own Accounts Payable report for Oracle Systems Limited (2026-11-08):
Invoiced 595,199.63 · Paid 199,887.12 (payments 197,656.24 + journal 2,230.88)
· Outstanding 395,312.51, all in 31–60 days."""
import io
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

PI, SI = "Purchase Invoice", "Sales Invoice"
OUT = Path("/tmp/ple_pack_test.xlsx")


def E(party, vt, vn, avt, avn, date, amount, due=None):
    return {"party": party, "voucher_type": vt, "voucher_no": vn, "against_voucher_type": avt,
            "against_voucher_no": avn, "posting_date": date, "due_date": due, "amount": amount}


O = "SUP-ORACLE"
SUPPLIER = [
    E(O, "Journal Entry", "ACC-JV-2023-00070", "Journal Entry", "ACC-JV-2023-00070", "2023-01-31", 2230.88),
    E(O, "Journal Entry", "ACC-JV-2023-00071", "Journal Entry", "ACC-JV-2023-00071", "2023-02-21", -2230.88),
    E(O, PI, "ACC-PINV-2026-00072", PI, "ACC-PINV-2026-00072", "2026-04-30", 592968.75, "2026-05-30"),
    E(O, "Payment Entry", "ACC-PAY-2026-00507", PI, "ACC-PINV-2026-00072", "2026-09-21", -592968.40),
    E(O, "Journal Entry", "ACC-JV-EXCH-1", PI, "ACC-PINV-2026-00072", "2026-09-21", -0.35),   # exchange difference
    E(O, PI, "ACC-PINV-2026-00118-1", PI, "ACC-PINV-2026-00118-1", "2026-07-31", 592968.75, "2026-09-29"),
    E(O, "Payment Entry", "ACC-PAY-2026-00573", PI, "ACC-PINV-2026-00118-1", "2026-10-21", -197656.24),
]
C = "CUST-A"
CUSTOMER = [
    E(C, SI, "SINV-1", SI, "SINV-1", "2026-06-01", 1000.0, "2026-06-01"),
    E(C, SI, "SINV-1-RET", SI, "SINV-1", "2026-06-10", -100.0),                 # credit note against SINV-1
    E(C, "Payment Entry", "PAY-1", SI, "SINV-1", "2026-06-15", -300.0),
    E(C, SI, "SINV-2", SI, "SINV-2", "2026-10-20", 3000.0, "2026-11-30"),      # not yet due
    E(C, "Payment Entry", "PAY-ADV", "Payment Entry", "PAY-ADV", "2026-11-01", -500.0),  # unallocated advance
]


class PaymentLedgerPack(unittest.TestCase):
    def setUp(self):
        self.site = ff.install()
        import frappe
        self.frappe = frappe
        self.site.table("Company")["DSC"] = {"name": "DSC", "company_name": "DSC", "default_currency": "SAR"}
        self.site.table("Supplier")[O] = {"name": O, "supplier_name": "Oracle Systems Limited"}
        self.site.table("Customer")[C] = {"name": C, "customer_name": "Customer A"}
        frappe.local.response = types.SimpleNamespace()
        frappe.utils.get_url = lambda *a: "https://x"
        frappe.db.sql = lambda q, p=None, as_dict=False: (
            [dict(r) for r in (SUPPLIER if p[1] == "Supplier" else CUSTOMER)] if "Payment Ledger Entry" in q else [])
        from neotec_insight.neotec_insight.api import packs
        self.packs = packs
        packs._has_ple = lambda: True
        packs._seed_ageing_pack()

    def tearDown(self):
        ff.uninstall()

    def build(self):
        self.packs.generate_pack("ar_ap_ageing", "DSC", "2026-11-08", "2026-11-08")
        data = self.frappe.local.response.filecontent
        OUT.write_bytes(data)
        import openpyxl
        return openpyxl.load_workbook(io.BytesIO(data))

    def test_oracle_matches_erpnext(self):
        wb = self.build()
        ap = wb["AP Ageing"]
        row = [ap.cell(row=10, column=c).value for c in range(2, 15)]
        self.assertEqual(row[0], "Oracle Systems Limited")
        self.assertEqual(row[1:5], [595199.63, 197656.24, 0.0, 2230.88])          # invoiced, paid, debit note, adjustment
        self.assertEqual(row[5], "=C10-D10-E10-F10")
        self.assertEqual(row[7:12], [0.0, 395312.51, 0.0, 0.0, 0.0])               # 0–30 … 121+: all in 31–60
        det = wb["AP Ageing Detail"]
        docs = [det.cell(row=r, column=3).value for r in range(5, 30) if det.cell(row=r, column=3).value]
        self.assertEqual([d for d in docs if d.startswith("ACC-")],
                         ["ACC-JV-2023-00070", "ACC-JV-2023-00071", "ACC-PINV-2026-00118-1"])  # as ERPNext lists
        self.assertNotIn("Exchange / rounding differences", docs)

    def test_customer_columns_and_buckets(self):
        ar = self.build()["AR Ageing"]
        row = [ar.cell(row=10, column=c).value for c in range(2, 15)]
        # SINV-1 1,000 − note 100 − paid 300 = 600 (age 160 → 121+); SINV-2 3,000 not yet due; advance −500
        self.assertEqual(row[1:5], [4000.0, 800.0, 100.0, 0.0])
        self.assertEqual(row[6], 3000.0)                                          # not yet due
        self.assertEqual(row[7], -500.0)                                          # the advance, aged by its date
        self.assertEqual(row[11], 600.0)

    @unittest.skipUnless(shutil.which("soffice") or shutil.which("libreoffice"), "LibreOffice not installed")
    def test_recalculated_workbook_reconciles(self):
        self.build()
        subprocess.run([sys.executable, "/mnt/skills/public/xlsx/scripts/recalc.py", str(OUT), "60"],
                       capture_output=True, text=True, timeout=120)
        import openpyxl
        wb = openpyxl.load_workbook(OUT, data_only=True)
        for name in ("AR Ageing", "AP Ageing"):
            ws = wb[name]
            self.assertEqual(round(ws["G10"].value, 2), {"AR Ageing": 3100.0, "AP Ageing": 395312.51}[name])
            tr = next(r for r in range(10, 40) if ws.cell(row=r, column=1).value == "Total")
            self.assertAlmostEqual(ws.cell(row=tr + 2, column=14).value, 0.0, places=2)    # check row
            det = wb[name + " Detail"]
            checks = [det.cell(row=r, column=8).value for r in range(5, 40) if det.cell(row=r, column=3).value == "Party total"]
            self.assertEqual(set(checks), {"✓ matches summary"})


if __name__ == "__main__":
    unittest.main()
