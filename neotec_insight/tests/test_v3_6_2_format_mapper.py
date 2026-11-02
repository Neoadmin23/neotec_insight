"""v3.6.2 — Bank Format Mapper: a new bank's layout defined from a sample PDF,
without code. Synthetic 'second bank': oldest first, 'Transaction Date' and
'Transaction Type' headers, Deposit/Withdrawal, dd-Mon-yyyy dates."""
import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[0] / "neotec_insight" / "utils"))
sys.path.insert(0, str(HERE.parents[1]))
sys.path.insert(0, str(HERE))
import bankrec_core as b  # noqa: E402
import _fakefrappe as ff  # noqa: E402

H = [("Transaction", 30, 80), ("Date", 83, 100), ("Narration", 140, 190), ("Transaction", 300, 350), ("Type", 353, 372),
     ("Deposit", 420, 455), ("Withdrawal", 480, 530), ("Balance", 560, 600)]
ROWS = [("01-May-2026", "Opening transfer in", "IN", 1000.00, 0, 11000.00),       # oldest first
        ("03-May-2026", "Rent EJAR", "OUT", 0, 4000.00, 7000.00),
        ("09-May-2026", "Customer receipt ALBANI", "IN", 2500.00, 0, 9500.00)]


def second_bank(rows=ROWS):
    ws = [{"text": "Statement", "x0": 30, "x1": 90, "top": 40}]
    ws += [{"text": t, "x0": x, "x1": x1, "top": 100} for t, x, x1 in H]
    top = 120
    for d, desc, typ, cr, dr, bal in rows:
        ws += [{"text": d, "x0": 30, "x1": 85, "top": top}, {"text": desc.split()[0], "x0": 140, "x1": 180, "top": top},
               {"text": typ, "x0": 300, "x1": 320, "top": top},
               {"text": f"{cr:,.2f}", "x0": 420, "x1": 455, "top": top}, {"text": f"{dr:,.2f}", "x0": 480, "x1": 520, "top": top},
               {"text": f"{bal:,.2f}", "x0": 560, "x1": 600, "top": top}]
        top += 20
    return [ws]


class Helpers(unittest.TestCase):
    def test_suggestions_and_detection(self):
        pages = second_bank()
        cands = b.header_candidates(pages)
        self.assertEqual(cands[0]["words"][0]["text"], "Transaction")
        m = b.suggest_mapping(cands[0]["words"])
        self.assertEqual(m, {"date": "Transaction", "details": "Narration", "type": "Transaction#2",
                             "credit": "Deposit", "debit": "Withdrawal", "balance": "Balance"})
        self.assertEqual(b.guess_date_format(pages)[0], "%d-%b-%Y")
        order, proof, lines = b.try_orders(pages, {"columns": m, "date_format": "%d-%b-%Y", "order": "auto",
                                                   "footer_markers": [], "slack": 10})
        self.assertEqual((order, len(lines), proof["ok"], proof["closing"]), ("oldest_first", 3, True, 9500.0))
        self.assertEqual(lines[1]["type"], "OUT")                       # the 2nd "Transaction" column

    def test_arabic_headers(self):
        ws = [{"text": t, "x0": x, "x1": x + 30, "top": 100} for t, x in
              (("التاريخ", 30), ("البيان", 140), ("مدين", 420), ("دائن", 480), ("الرصيد", 560))]
        self.assertEqual(b.suggest_mapping(ws), {"date": "التاريخ", "details": "البيان", "debit": "مدين",
                                                 "credit": "دائن", "balance": "الرصيد"})


class Flow(unittest.TestCase):
    def setUp(self):
        self.site = ff.install()
        import frappe
        self.frappe = frappe
        self.site.table("File")["f"] = {"name": "f", "file_url": "/files/sample.pdf"}
        ff.Document.get_content = lambda self: b"%PDF"
        self.addCleanup(lambda: delattr(ff.Document, "get_content"))
        from neotec_insight.neotec_insight.api import bankrec, statement_reader
        self.br, self.sr = bankrec, statement_reader
        self.pages = second_bank()
        bankrec.core.pdf_words = lambda content: self.pages
        self.site.roles[self.site.user] = {"Accounts Manager"}

    def tearDown(self):
        ff.uninstall()

    def spec(self, **over):
        info = self.br.inspect_pdf("/files/sample.pdf")
        c = info["candidates"][0]
        return {"format_name": "Second Bank — Statement", "bank_name": "Second Bank", "columns": c["suggested"],
                "date_format": info["date_format"], "order": "auto", "footer_markers": "", "slack": 10, **over}

    def test_inspect_test_save_then_import_reads_it(self):
        s = self.spec()
        t = self.br.test_format("/files/sample.pdf", json.dumps(s))
        self.assertEqual((t["ok"], t["lines"], t["order"]), (True, 3, "oldest_first"))
        self.br.save_format("/files/sample.pdf", json.dumps(s))
        saved = self.site.table("Insight Bank Format")["Second Bank — Statement"]
        self.assertEqual(saved["statement_order"], "oldest_first")             # 'auto' resolved and stored
        lines, fmt = self.sr.read_pdf_lines(b"%PDF")                             # the Bank tab picks it up
        self.assertEqual((fmt, len(lines)), ("Second Bank — Statement", 3))

    def test_a_mapping_that_does_not_prove_is_not_saved(self):
        s = self.spec(columns={"date": "Transaction", "details": "Narration", "credit": "Withdrawal",
                               "debit": "Deposit", "balance": "Balance"})      # credit/debit swapped
        self.assertFalse(self.br.test_format("/files/sample.pdf", json.dumps(s))["ok"])
        with self.assertRaises(self.frappe.ValidationError):
            self.br.save_format("/files/sample.pdf", json.dumps(s))
        self.assertNotIn("Second Bank — Statement", self.site.table("Insight Bank Format"))

    def test_required_columns_and_managers_only(self):
        with self.assertRaises(self.frappe.ValidationError):
            self.br.test_format("/files/sample.pdf", json.dumps(self.spec(columns={"date": "Transaction"})))
        self.site.user = "clerk@a.co"
        self.site.roles["clerk@a.co"] = {"Accounts User"}
        with self.assertRaises(self.frappe.PermissionError):
            self.br.inspect_pdf("/files/sample.pdf")


if __name__ == "__main__":
    unittest.main()
