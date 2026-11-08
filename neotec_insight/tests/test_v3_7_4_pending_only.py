"""v3.7.4 — pending documents only: settled invoices left with exchange or
rounding halalas are not pending (reference 'AP Ageing', Oct-26); payments
age from their own date."""
import io
import sys
import types
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
sys.path.insert(0, str(HERE))
import _fakefrappe as ff  # noqa: E402
from test_v3_7_2_payment_ledger import E, O, PI, SUPPLIER  # noqa: E402

T, DT = "SUP-TAW", "SUP-DT"
EXTRA = []
for i, left in enumerate((0.02, -0.03, -0.05)):                       # settled invoices with leftovers (−0.06)
    EXTRA += [E(O, PI, f"PINV-OLD-{i}", PI, f"PINV-OLD-{i}", "2026-02-28", 100000.0, "2026-03-30"),
              E(O, "Payment Entry", f"PAY-OLD-{i}", PI, f"PINV-OLD-{i}", "2026-04-15", -(100000.0 - left))]
EXTRA += [E(T, PI, "PINV-TAW", PI, "PINV-TAW", "2026-09-01", 5590.15, "2026-10-01"),
          E(T, "Payment Entry", "PAY-TAW", PI, "PINV-TAW", "2026-10-05", -5590.14),            # 0.01 left
          # Digital Twins: an old overpayment; its ledger row carries a recent due date from a linked invoice
          E(DT, "Payment Entry", "PAY-DT", "Payment Entry", "PAY-DT", "2026-03-10", -5889.20, "2026-09-25")]


class PendingOnly(unittest.TestCase):
    def setUp(self):
        self.site = ff.install()
        import frappe
        self.frappe = frappe
        self.site.table("Company")["DSC"] = {"name": "DSC", "company_name": "DSC", "default_currency": "SAR"}
        for k, n in ((O, "Oracle Systems Limited"), (T, "Tawuniya"), (DT, "Digital Twins")):
            self.site.table("Supplier")[k] = {"name": k, "supplier_name": n}
        frappe.local.response = types.SimpleNamespace()
        frappe.utils.get_url = lambda *a: "https://x"
        frappe.db.sql = lambda q, p=None, as_dict=False: (
            [dict(r) for r in SUPPLIER + EXTRA] if "Payment Ledger Entry" in q and p[1] == "Supplier" else [])
        from neotec_insight.neotec_insight.api import packs
        self.packs = packs
        packs._has_ple = lambda: True
        packs._seed_ageing_pack()

    def tearDown(self):
        ff.uninstall()

    def test_matches_the_reference_sheet(self):
        self.packs.generate_pack("ar_ap_ageing", "DSC", "2026-11-08", "2026-11-08")
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(self.frappe.local.response.filecontent))
        ap = wb["AP Ageing"]
        rows = {}
        for r in range(10, 20):
            if ap.cell(row=r, column=1).value == "Total":
                break
            rows[ap.cell(row=r, column=2).value] = [ap.cell(row=r, column=c).value for c in range(3, 15)]
        self.assertEqual(set(rows), {"Oracle Systems Limited", "Digital Twins"})        # no Tawuniya 0.01
        o = rows["Oracle Systems Limited"]
        self.assertEqual(o[:4], [595199.63, 197656.24, 0.0, 2230.88])
        self.assertEqual(o[7], 395312.51)                                                # 31–60, nothing elsewhere
        self.assertEqual(sum(x for x in o[6:11] if isinstance(x, (int, float))), 395312.51)
        d = rows["Digital Twins"]
        self.assertEqual((d[1], d[10]), (5889.2, -5889.2))                                 # paid; aged 121+
        det = wb["AP Ageing Detail"]
        docs = [det.cell(row=r, column=3).value for r in range(5, 40)]
        self.assertFalse(any(str(x).startswith("PINV-OLD") for x in docs))                 # settled invoices not listed

    def test_threshold_is_an_option(self):
        from neotec_insight.neotec_insight.utils import ledger_ageing as la
        docs = la.documents([r for r in EXTRA if r["party"] == T], PI)
        self.assertEqual(la.party_rows(docs, "2026-11-08", [30, 60, 90, 120])[0]["docs"], [])
        self.assertEqual(len(la.party_rows(docs, "2026-11-08", [30, 60, 90, 120], min_balance=0)[0]["docs"]), 1)


if __name__ == "__main__":
    unittest.main()
