"""v3.6.1 — the Bank tab's statement import reads PDFs through the bank
formats (proven balances only); CSV/Excel unchanged."""
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
sys.path.insert(0, str(HERE))
import _fakefrappe as ff  # noqa: E402
from test_v3_6_bankrec import ROWS, page  # noqa: E402


class PdfImport(unittest.TestCase):
    def setUp(self):
        self.site = ff.install()
        import frappe
        self.frappe = frappe
        from neotec_insight.neotec_insight.api import bankrec, statement_reader
        self.sr, self.br = statement_reader, bankrec
        self.rows = list(ROWS)
        bankrec.core.pdf_words = lambda content: [page(self.rows)]

    def tearDown(self):
        ff.uninstall()

    def test_pdf_lines_in_the_importers_shape(self):
        lines, fmt = self.sr.read_pdf_lines(b"%PDF")
        self.assertEqual(fmt, "Riyad Bank — Account Statement")
        self.assertEqual(len(lines), 5)
        l1 = lines[1]
        self.assertEqual((l1["value_date"], l1["debit"], l1["direction"]), ("2026-05-31", 23677.0, "Outgoing"))
        self.assertEqual(lines[2]["direction"], "Incoming")

    def test_unproven_pdf_is_refused_with_the_reason(self):
        self.rows[2] = self.rows[2][:3] + (2200.00, 0, 206104.51)
        with self.assertRaises(self.frappe.ValidationError) as cm:
            self.sr.read_pdf_lines(b"%PDF")
        self.assertIn("do not prove", str(cm.exception))

    def test_unknown_layout_names_the_formats_tried(self):
        self.br.core.pdf_words = lambda content: [[{"text": "Hello", "x0": 10, "top": 10}]]
        with self.assertRaises(self.frappe.ValidationError) as cm:
            self.sr.read_pdf_lines(b"%PDF")
        self.assertIn("Riyad Bank", str(cm.exception))

    def test_pdf_no_longer_refused_and_upload_accepts_it(self):
        src = (HERE.parents[0] / "neotec_insight" / "api" / "statement_reader.py").read_text()
        self.assertNotIn("PDF statement parsing isn't reliable", src)
        ui = (HERE.parents[1] / "reportManager" / "src" / "features" / "bank" / "StatementImport.tsx").read_text()
        self.assertIn('accept=".pdf,', ui)


if __name__ == "__main__":
    unittest.main()


class FeeText(unittest.TestCase):
    def test_fee_split_when_arabic_interleaves(self):
        ff.install()
        try:
            from neotec_insight.neotec_insight.api import statement_reader as sr
            ln = sr._normalize_account_line({"details": "INSTANT PAYMENT السريعFEE تحويل , 0000000000000.50 SAR,VAT AMOUNT 00000.08 SAR",
                                             "debit": "0.58", "credit": "0", "value_date": "21/05/2026"})
            self.assertEqual((ln["fee"], ln["vat"]), (0.5, 0.08))
        finally:
            ff.uninstall()
