"""v3.6.0 — bank reconciliation: reading, balance proof, matching, flow.
Synthetic statement in the Riyad Bank layout (real statements hold bank data
and are not shipped)."""
import datetime as dt
import json
import sys
import types
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[0] / "neotec_insight" / "utils"))
sys.path.insert(0, str(HERE.parents[1]))
sys.path.insert(0, str(HERE))
import bankrec_core as b  # noqa: E402
import _fakefrappe as ff  # noqa: E402

FMT = {"columns": {"date": "Value Date", "details": "Details", "reference": "Reference", "cheque": "Cheque No",
                   "type": "Transaction Type", "credit": "Credit Amount", "debit": "Debit Amount", "balance": "Balance"},
       "date_format": "%d/%m/%Y", "order": "newest_first", "footer_markers": ["Page"], "slack": 10}
X = {"date": 35, "details": 108, "reference": 332, "cheque": 422, "type": 493, "credit": 596, "debit": 678, "balance": 756}


def W(t, col, top):
    return {"text": t, "x0": X[col] + (0 if col != "balance" else 16), "top": top}


def page(rows, decoy=True):
    ws = []
    if decoy:                                         # a details box with the same words, above the header
        ws += [{"text": "Transaction", "x0": 40, "top": 214}, {"text": "Type", "x0": 90, "top": 214}]
    for lbl, col in (("Value", "date"), ("Details", "details"), ("Reference", "reference"), ("Cheque", "cheque"),
                     ("Transaction", "type"), ("Credit", "credit"), ("Debit", "debit"), ("Balance", "balance")):
        ws.append({"text": lbl, "x0": X[col], "top": 313})
    top = 330
    for date, desc, ref, cr, dr, bal in rows:        # description wraps above and below the middle line
        ws += [W(desc[0], "details", top), W("REF", "reference", top), W(date, "date", top + 5),
               W(ref, "reference", top + 5), W(f"{cr:,.2f}", "credit", top + 5), W(f"{dr:,.2f}", "debit", top + 5),
               {"text": "SAR", "x0": 756, "top": top + 5}, W(f"{bal:,.2f}", "balance", top + 5),
               W(desc[1], "details", top + 10)]
        top += 30
    ws.append({"text": "Page", "x0": 400, "top": 800})
    return ws


ROWS = [  # newest first, as printed
    ("31/05/2026", ("Local", "Transfer FEE"), "REF-FEE-1", 0, 5.75, 182421.76),
    ("31/05/2026", ("TO KINDA", "CATERING SA72"), "9467015109510FGQ", 0, 23677.00, 182427.51),
    ("25/05/2026", ("FROM SIRION", "INFORMATION TECHNO"), "VITII88706035606", 2300.00, 0, 206104.51),
    ("21/05/2026", ("INSTANT", "FEE"), "IPSW1", 0, 0.58, 203804.51),
    ("21/05/2026", ("INSTANT", "FEE"), "IPSW2", 0, 0.58, 203805.09),
]


class Reading(unittest.TestCase):
    def test_parse_and_prove_newest_first_with_decoy_header(self):
        lines = b.parse_pages([page(ROWS)], FMT)
        self.assertEqual(len(lines), 5)
        self.assertEqual(lines[1]["reference"], "REF 9467015109510FGQ")
        self.assertIn("KINDA", lines[1]["details"])
        pr = b.prove(b.posting_order(lines, FMT))
        self.assertTrue(pr["ok"], pr["bad"])
        self.assertEqual((pr["opening"], pr["closing"]), (203805.67, 182421.76))
        self.assertEqual(round(pr["opening"] + pr["credits"] - pr["debits"], 2), pr["closing"])

    def test_a_misread_line_breaks_the_chain(self):
        rows = list(ROWS)
        rows[2] = rows[2][:3] + (2200.00, 0, 206104.51)            # credit misread
        pr = b.prove(b.posting_order(b.parse_pages([page(rows)], FMT), FMT))
        self.assertFalse(pr["ok"])

    def test_identical_fee_lines_get_distinct_keys(self):
        lines = b.parse_pages([page(ROWS)], FMT)
        keys = {b.line_key("ACC", ln) for ln in lines}
        self.assertEqual(len(keys), 5)


D = dt.date


class Matching(unittest.TestCase):
    def setUp(self):
        self.lines = [
            {"key": "L1", "date": D(2026, 5, 31), "details": "TO KINDA CATERING", "reference": "9467015109510FGQ", "credit": 0, "debit": 23677.0},
            {"key": "L2", "date": D(2026, 5, 25), "details": "FROM SIRION INFORMATION TECHNO", "reference": "", "credit": 2300.0, "debit": 0},
            {"key": "L3", "date": D(2026, 5, 12), "details": "AL BANI DEVELOPMENT CO LTD", "reference": "", "credit": 10350.0, "debit": 0},
            {"key": "L4", "date": D(2026, 5, 20), "details": "INSTANT FEE", "reference": "", "credit": 0, "debit": 315.0},
        ]
        self.cands = [
            {"doctype": "Payment Entry", "name": "PE-1", "date": D(2026, 5, 31), "amount": 23677.0, "direction": "out", "party": "Kinda", "reference": "9467015109510FGQ"},
            {"doctype": "Payment Entry", "name": "PE-2", "date": D(2026, 5, 24), "amount": 2300.0, "direction": "in", "party": "Sirion Information Technology", "reference": ""},
            {"doctype": "Payment Entry", "name": "PE-3", "date": D(2026, 5, 26), "amount": 2300.0, "direction": "in", "party": "Other Customer", "reference": ""},
            {"doctype": "Payment Entry", "name": "PE-4", "date": D(2026, 5, 12), "amount": 5175.0, "direction": "in", "party": "Al Bani Development Co", "reference": ""},
            {"doctype": "Payment Entry", "name": "PE-5", "date": D(2026, 5, 12), "amount": 5175.0, "direction": "in", "party": "Al Bani Development Co", "reference": ""},
            {"doctype": "Journal Entry", "name": "JV-9", "date": D(2026, 5, 20), "amount": 315.0, "direction": "out", "party": "", "reference": ""},
        ]

    def test_three_passes(self):
        res = {m["key"]: m for m in b.match(self.lines, self.cands)}
        self.assertEqual((res["L1"]["rule"], res["L1"]["vouchers"][0][1]), ("reference", "PE-1"))
        self.assertEqual((res["L2"]["rule"], res["L2"]["vouchers"][0][1]), ("amount+party", "PE-2"))   # not PE-3
        self.assertEqual(res["L3"]["rule"], "combination")
        self.assertEqual({v[1] for v in res["L3"]["vouchers"]}, {"PE-4", "PE-5"})
        self.assertEqual((res["L4"]["rule"], res["L4"]["vouchers"][0][1]), ("amount+date", "JV-9"))
        used = [v[1] for m in res.values() for v in m["vouchers"]]
        self.assertEqual(len(used), len(set(used)))                # each entry once

    def test_ai_validator(self):
        by = {c["name"]: c for c in self.cands}
        ln = self.lines[1]
        self.assertIsNone(b.validate_ai(ln, [("Payment Entry", "PE-3", 2300.0)], by, set()))
        self.assertIn("other way", b.validate_ai(self.lines[0], [("Payment Entry", "PE-2", 2300.0)], by, set()))
        self.assertIn("already", b.validate_ai(ln, [("Payment Entry", "PE-3", 2300.0)], by, {"PE-3"}))
        self.assertIn("add up", b.validate_ai(self.lines[2], [("Payment Entry", "PE-4", 5175.0)], by, set()))
        far = dict(ln, date=D(2026, 6, 30))
        self.assertIn("days away", b.validate_ai(far, [("Payment Entry", "PE-3", 2300.0)], by, set()))


class Flow(unittest.TestCase):
    def setUp(self):
        self.site = ff.install()
        import frappe
        self.frappe = frappe
        frappe.log_error = lambda *a, **k: None
        self.site.table("Bank Account")["Riyad - IRSAA"] = {"name": "Riyad - IRSAA", "is_company_account": 1,
                                                             "account": "1110 - Riyad Bank - IR", "company": "IRSAA"}
        self.site.table("File")["f1"] = {"name": "f1", "file_url": "/files/stmt.pdf"}
        ff.Document.get_content = lambda self: b"%PDF"
        ff.Document.submit = lambda self: setattr(self, "docstatus", 1)
        self.addCleanup(lambda: (delattr(ff.Document, "get_content"), delattr(ff.Document, "submit")))
        from neotec_insight.neotec_insight.api import bankrec
        self.br = bankrec
        bankrec.core.pdf_words = lambda content: [page(ROWS)]
        frappe.db.sql = lambda q, p=None, as_dict=False: [
            {"name": "PE-1", "posting_date": D(2026, 5, 31), "paid_from": "1110 - Riyad Bank - IR", "paid_to": "Creditors",
             "paid_amount": 23677.0, "received_amount": 23677.0, "party": "Kinda", "reference_no": "9467015109510FGQ"}] \
            if "Payment Entry" in q else []
        self.reconciled = []
        mod = types.ModuleType("erpnext.accounts.doctype.bank_reconciliation_tool.bank_reconciliation_tool")
        mod.reconcile_vouchers = lambda bt, v: self.reconciled.append((bt, json.loads(v)))
        for n in ("erpnext", "erpnext.accounts", "erpnext.accounts.doctype", "erpnext.accounts.doctype.bank_reconciliation_tool"):
            sys.modules.setdefault(n, types.ModuleType(n))
        sys.modules[mod.__name__] = mod
        from neotec_insight.neotec_insight.api import copilot
        copilot._ai_ready = lambda s=None: False

    def tearDown(self):
        ff.uninstall()

    def test_read_create_match_accept_reconcile(self):
        s = self.br.create_statement("Riyad - IRSAA", "/files/stmt.pdf")
        self.assertEqual((s["status"], s["balance_errors"], len(s["lines"])), ("Read", 0, 5))
        self.assertEqual((s["opening"], s["closing"]), (203805.67, 182421.76))
        r = self.br.create_transactions(s["name"])
        self.assertEqual(r["created"], 5)
        self.assertEqual(self.br.create_transactions(s["name"])["created"], 0)       # never twice
        m = self.br.find_matches(s["name"], use_ai=0)
        self.assertEqual(m["rule_matches"], 1)
        self.br.set_lines(s["name"], threshold=90)
        self.frappe.db.get_value = lambda dt, n, f=None, *a, **k: 1 if f == "docstatus" else None
        out = self.br.reconcile(s["name"])
        self.assertEqual(out["reconciled"], 1)
        self.assertEqual(self.reconciled[0][1], [{"payment_doctype": "Payment Entry", "payment_name": "PE-1", "amount": 23677.0}])

    def test_balance_error_blocks_posting(self):
        rows = list(ROWS)
        rows[2] = rows[2][:3] + (2200.00, 0, 206104.51)
        self.br.core.pdf_words = lambda content: [page(rows)]
        s = self.br.create_statement("Riyad - IRSAA", "/files/stmt.pdf")
        self.assertEqual(s["status"], "Balance error")
        with self.assertRaises(self.frappe.ValidationError):
            self.br.create_transactions(s["name"])


if __name__ == "__main__":
    unittest.main()
