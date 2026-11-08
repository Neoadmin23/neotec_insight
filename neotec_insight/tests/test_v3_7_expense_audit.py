"""v3.7.0 — Expense Audit, steps 1–2 (company only). Patterns from a real
Riyad Bank statement; the statement itself is not shipped."""
import datetime as dt
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[0] / "neotec_insight" / "utils"))
sys.path.insert(0, str(HERE.parents[1]))
sys.path.insert(0, str(HERE))
import spend_core as s  # noqa: E402
import _fakefrappe as ff  # noqa: E402

D = dt.date


def L(d, details, debit=0.0, credit=0.0, typ="", account="Riyad"):
    return {"date": d, "account": account, "details": details, "reference": "", "type": typ,
            "debit": debit, "credit": credit, "currency": "SAR"}


class Cleaning(unittest.TestCase):
    def test_payees(self):
        c = s.clean_merchant
        self.assertEqual(c("REF 946701410IPSWTSX,REF 20260521SARIBLRIBL6BTMS116,MAHMOUD RAFIK HUSSEIN MABROUK,RJHISARI,"
                           "SA2380000126608012037130,21-05-2026 04:36:17 PM,Personal/Other,VIA CORE SYSTEM"), "Mahmoud Rafik Hussein Mabrouk")
        self.assertEqual(c("TO:NURUDDIN SHEIK ISSAK,REF 946701400951ZM02,SAR 30000.00"), "Nuruddin Sheik Issak")
        self.assertEqual(c("MOL,SUB104724010043789,REF TBC2605245882920,ONE TIME BILL PAYMENT"), "MOL")
        self.assertEqual(c("SQ *JOES COFFEE 4421"), "Joes Coffee")
        self.assertEqual(c("FILE : 6004171302,PAY29759", "CORPORATE PAYROLL"), "Corporate payroll")

    def test_centres(self):
        self.assertEqual(s.centre("X", "Int. Transfer,FEE 50.00 SAR,VAT AMOUNT 7.50", "BANK FEES/COMMISSIONS"), "Bank fees")
        self.assertEqual(s.centre("Sherif Ibrahim", "Consultation fee related", "OUTGOING INTL. TRANS."), "Other")   # not a fee
        self.assertNotEqual(s.centre("Kinda", "Current account to Mr Med,catering", "OUTGOING TRANSF"), "Rent")
        self.assertEqual(s.centre("MOL", "MOL,SUB1047,ONE TIME BILL PAYMENT", "UTILITY BILL PAYMENT"), "Government & visas")
        self.assertEqual(s.centre("EJAR", "EJAR,SUB2028,ONE TIME BILL PAYMENT", ""), "Rent")
        self.assertEqual(s.centre("GOSI", "GOSI,SUB5039", ""), "Government & visas")


class Internal(unittest.TestCase):
    def test_kinds(self):
        rows = s.classify([
            L(D(2026, 5, 13), "FROM شركه ارساء الجزيره,My Account In Another Bank", credit=900000.0),          # own transfer in
            L(D(2026, 5, 7), "TRNSFR,شركة شيرا للمقاولات", credit=2875.0, typ="TRNSFR FROM C/A TO C/A"),      # a customer
            L(D(2026, 5, 2), "TO:IRSAA SNB", debit=5000.0, account="Riyad"),
            L(D(2026, 5, 3), "FROM RIYAD", credit=5000.0, account="SNB"),                                    # pair across accounts
            L(D(2026, 5, 9), "REFUND ORACLE", credit=120.0),
        ], own_names=("ارساء",))
        self.assertEqual([r["kind"] for r in rows], ["internal", "income", "internal", "internal", "refund"])
        m = s.monthly(rows)
        self.assertEqual((m[0]["in"], m[0]["out"]), (2995.0, 0.0))        # 2,875 receipt + 120 refund; transfers excluded


class Recurring(unittest.TestCase):
    def test_monthly_with_price_rise_and_middleman(self):
        rows = s.classify(
            [L(D(2026, m, 5), "APPLE.COM/BILL ITUNES.COM", debit=3.99) for m in (3, 4, 5)]
            + [L(D(2026, 3, 28), "TO:ORACLE SYSTEMS LIMITED", debit=1000.0), L(D(2026, 4, 28), "TO:ORACLE SYSTEMS LIMITED", debit=1000.0),
               L(D(2026, 5, 28), "TO:ORACLE SYSTEMS LIMITED", debit=1100.0)]
            + [L(D(2026, 5, 20), "TO:ONE OFF VENDOR", debit=777.0)])
        rec = {r["merchant"]: r for r in s.recurring(rows)}
        o = rec["Oracle Systems Limited"]
        self.assertEqual((o["period"], o["price"], o["per_year"], o["price_rise"], o["next"]),
                         ("Monthly", 1100.0, 13200.0, 100.0, "2026-06-27"))
        apple = next(r for r in rec.values() if r["via"] == "Apple")
        self.assertEqual((apple["period"], apple["price"]), ("Monthly", 3.99))
        self.assertNotIn("One Off Vendor", rec)


class Endpoint(unittest.TestCase):
    def setUp(self):
        self.site = ff.install()
        import frappe
        self.frappe = frappe
        self.site.table("Company")["IRSAA"] = {"name": "IRSAA", "company_name": "IRSAA Business Solution", "abbr": "IR"}
        self.site.table("Bank Account")["Riyad - IR"] = {"name": "Riyad - IR", "bank": "Riyad Bank"}
        self.site.table("Account")["1110 - Riyad"] = {"name": "1110 - Riyad", "account_currency": "SAR"}
        for st in ("S1", "S2"):
            self.site.table("Insight Bank Statement")[st] = {"name": st, "company": "IRSAA", "bank_account": "Riyad - IR",
                                                             "gl_account": "1110 - Riyad", "from_date": "2026-05-01"}
        rows = [("S1", "k1", "2026-05-06", "GOSI,SUB503964775", 0, 58086.33, ""),
                ("S1", "k2", "2026-05-17", "INSTANT PAYMENT FEE 0.50 SAR,VAT AMOUNT 0.08", 0, 0.58, "INSTANT PAYMENT FEES"),
                ("S2", "k2", "2026-05-17", "INSTANT PAYMENT FEE 0.50 SAR,VAT AMOUNT 0.08", 0, 0.58, "INSTANT PAYMENT FEES"),  # overlap
                ("S2", "k3", "2026-05-19", "FROM SIRION INFORMATION TECHNO", 2300.0, 0, "INCOMING TRANSFER"),
                ("S2", "k4", "2026-05-20", "VISA CREDIT CARD PAYMENT 4417", 0, 1500.0, "")]
        for st in ("S1", "S2"):
            self.site.table("Insight Bank Statement")[st]["lines"] = [
                {"name": f"l{i}", "line_key": k, "posting_date": d, "details": det, "reference": "", "txn_type": typ,
                 "credit": cr, "debit": dr, "idx": i} for i, (p, k, d, det, cr, dr, typ) in enumerate(rows) if p == st]
        from neotec_insight.neotec_insight.api import spend
        self.spend = spend

    def tearDown(self):
        ff.uninstall()

    def test_audit(self):
        r = self.spend.audit("IRSAA", "2026-05-01", "2026-05-31")
        self.assertEqual(r["lines"], 4)                                      # the overlapping fee counted once
        self.assertEqual((r["money_in"], r["money_out"]), (2300.0, 59586.91))
        self.assertEqual(r["accounts"][0]["bank"], "Riyad Bank")
        cents = {c["centre"]: c["total"] for c in r["centres"]}
        self.assertEqual((cents["Government & visas"], cents["Bank fees"]), (58086.33, 0.58))
        self.assertEqual(len(r["card_payments"]), 1)                          # a card bill with no card statement


if __name__ == "__main__":
    unittest.main()
