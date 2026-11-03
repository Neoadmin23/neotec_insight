"""v3.6.3 — one reconciliation place: invoice-number matching, CSV/Excel
statements with the same proof, fee + VAT split, Bank tab = slips."""
import datetime as dt
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[0] / "neotec_insight" / "utils"))
sys.path.insert(0, str(HERE.parents[1]))
sys.path.insert(0, str(HERE))
import bankrec_core as b  # noqa: E402
import _fakefrappe as ff  # noqa: E402

D = dt.date
SRC = HERE.parents[1] / "reportManager" / "src"


class InvoicePass(unittest.TestCase):
    def test_invoice_number_in_remittance_text(self):
        line = {"key": "L", "date": D(2026, 5, 25), "reference": "VITII88706035606", "credit": 2300.0, "debit": 0,
                "details": "FROM SIRION INFORMATION TECHNO,REMT INF: INVOICE ACC SINV 2026 00244,SAUDI ARABIA"}
        cands = [{"doctype": "Payment Entry", "name": "PE-A", "date": D(2026, 5, 25), "amount": 2300.0, "direction": "in",
                  "party": "Other", "reference": "", "invoices": ["ACC-SINV-2026-00262"]},
                 {"doctype": "Payment Entry", "name": "PE-B", "date": D(2026, 5, 25), "amount": 2300.0, "direction": "in",
                  "party": "Sirion", "reference": "", "invoices": ["ACC-SINV-2026-00244"]}]
        m = b.match([line], cands)[0]
        self.assertEqual((m["rule"], m["vouchers"][0][1]), ("invoice", "PE-B"))


class Flow(unittest.TestCase):
    def setUp(self):
        self.site = ff.install()
        import frappe
        self.frappe = frappe
        self.site.table("Bank Account")["Riyad - IRSAA"] = {"name": "Riyad - IRSAA", "is_company_account": 1,
                                                             "account": "1110 - Riyad Bank - IR", "company": "IRSAA"}
        self.site.table("Insight AI Settings")["Insight AI Settings"] = {"name": "Insight AI Settings",
                                                                         "bank_charges_account": "5310 - Bank Charges - IR",
                                                                         "input_vat_account": "1410 - Input VAT - IR"}
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["Value Date", "Details", "Reference", "Cheque No", "Transaction Type", "Credit Amount", "Debit Amount", "Balance"])
        ws.append(["31/05/2026", "Local Transfer,FEE 5.00 SAR,VAT AMOUNT 0.75 SAR,VAT% 15%", "REF 9467FGQ", "", "BANK FEES", 0, 5.75, 182421.76])
        ws.append(["25/05/2026", "FROM SIRION INVOICE ACC SINV 2026 00244", "REF VITII887", "", "INCOMING TRANSFER", 2300, 0, 182427.51])
        ws.append(["20/05/2026", "OPENING DEPOSIT", "REF X1", "", "INCOMING", 1000, 0, 180127.51])
        self.tmp = tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False)
        wb.save(self.tmp.name)
        from neotec_insight.neotec_insight.api import bankrec, statement_reader
        self.br = bankrec
        statement_reader._file_path = lambda url: self.tmp.name
        self.site.table("Insight Bank Format")["Riyad Bank — Account Statement"] = {
            "name": "Riyad Bank — Account Statement", "format_name": "Riyad Bank — Account Statement", "columns_json": "{}"}

    def tearDown(self):
        ff.uninstall()

    def test_excel_statement_proves_and_fee_vat_split(self):
        s = self.br.create_statement("Riyad - IRSAA", "/files/stmt.xlsx")
        self.assertEqual((s["status"], len(s["lines"]), s["balance_errors"]), ("Read", 3, 0))
        self.assertEqual((s["opening"], s["closing"]), (179127.51, 182421.76))
        fee_line = next(l for l in s["lines"] if l["debit"] == 5.75)
        out = self.br.draft_charges(s["name"], [fee_line["name"]])
        je = self.site.table("Journal Entry")[out["drafts"][0]]
        rows = {r["account"]: (r["debit_in_account_currency"], r["credit_in_account_currency"]) for r in je["accounts"]}
        self.assertEqual(rows["5310 - Bank Charges - IR"], (5.0, 0))
        self.assertEqual(rows["1410 - Input VAT - IR"], (0.75, 0))
        self.assertEqual(rows["1110 - Riyad Bank - IR"], (0, 5.75))


class Screens(unittest.TestCase):
    def test_one_place(self):
        bank = (SRC / "features/bank/BankApp.tsx").read_text()
        for gone in ("<StatementImport", "<Reconcile ", "<ReconcileReport", "next milestone"):
            self.assertNotIn(gone, bank)
        self.assertFalse((SRC / "features/bank/Reconcile.tsx").exists())
        rec = (SRC / "features/bankrec/BankRecApp.tsx").read_text()
        for v in ("'statements'", "'merchant'", "'report'", 'accept=".pdf,.csv,.tsv,.xlsx,.xls"'):
            self.assertIn(v, rec)


if __name__ == "__main__":
    unittest.main()
