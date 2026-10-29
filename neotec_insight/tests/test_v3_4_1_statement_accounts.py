"""v3.4.1 — party statements need no account choice; all GL fields incl.
Against Voucher; column print order follows the picker."""
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
sys.path.insert(0, str(HERE))
import _fakefrappe as ff  # noqa: E402

SRC = HERE.parents[1] / "reportManager" / "src"


class PartyDefaultAccounts(unittest.TestCase):
    def setUp(self):
        self.site = ff.install()
        import frappe
        self.frappe = frappe
        for n, at in (("1310 - Debtors - A", "Receivable"), ("2110 - Creditors - A", "Payable"),
                      ("1100 - Cash - A", "Cash")):
            self.site.table("Account")[n] = {"name": n, "account_type": at, "is_group": 0, "company": "Alpha Co",
                                             "lft": len(self.site.table("Account"))}
        self.seen = []
        frappe.db.sql = lambda q, params=None, *a, **k: []
        real = frappe.get_all

        def spy(dt, *a, **k):
            out = real(dt, *a, **k)
            if dt == "Account" and k.get("pluck") == "name":
                self.seen.append(list(out))
            return out
        frappe.get_all = spy
        from neotec_insight.neotec_insight.api import report
        self.report = report

    def tearDown(self):
        ff.uninstall()

    def call(self, **kw):
        try:
            return self.report.general_ledger(company="Alpha Co", from_date="2026-01-01", to_date="2026-10-29", **kw)
        except Exception as e:                # later stages need a real database; the account step is what we test
            if "Select at least one account" in str(e):
                raise
            return None

    def test_customer_statement_uses_receivable_accounts(self):
        self.call(accounts=[], customer=["Raqamyah"])
        self.assertEqual(self.seen[0], ["1310 - Debtors - A"])

    def test_supplier_statement_uses_payable_accounts(self):
        self.call(accounts="[]", supplier='["Gulf Supplies"]')
        self.assertEqual(self.seen[0], ["2110 - Creditors - A"])

    def test_plain_ledger_still_requires_an_account(self):
        with self.assertRaises(self.frappe.ValidationError):
            self.report.general_ledger(company="Alpha Co", accounts=[], customer="[]",
                                       from_date="2026-01-01", to_date="2026-10-29")


class Fields(unittest.TestCase):
    def test_against_voucher_selected_and_returned(self):
        src = (HERE.parents[0] / "neotec_insight" / "api" / "report.py").read_text()
        self.assertIn("g.against_voucher AS against_voucher", src)
        self.assertIn('"against_voucher": tr.get("against_voucher")', src)

    def test_screens_offer_the_fields_in_picker_order(self):
        st = (SRC / "features/statement/StatementApp.tsx").read_text()
        self.assertIn("key: 'against_voucher'", st)
        self.assertIn("accounts: acctSel", st)
        self.assertIn("const shown = cols.map(", st)
        gl = (SRC / "features/gl/GeneralLedger.tsx").read_text()
        self.assertIn("key: 'against_voucher'", gl)
        self.assertIn("const base = cols.map(", gl)


if __name__ == "__main__":
    unittest.main()
