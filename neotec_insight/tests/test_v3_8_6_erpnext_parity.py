"""v3.8.6 — General Ledger and Trial Balance follow ERPNext's rules.

The SQL the two reports build runs here against an in-memory SQLite copy of
GL Entry, so the conditions themselves are exercised, not only their text."""
import re
import sqlite3
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
sys.path.insert(0, str(HERE))
import _fakefrappe as ff  # noqa: E402

GL_COLS = ["company", "account", "posting_date", "debit", "credit", "voucher_type", "voucher_no", "against",
           "party_type", "party", "cost_center", "project", "department", "remarks", "against_voucher_type",
           "against_voucher", "finance_book", "is_opening", "is_cancelled", "account_currency",
           "debit_in_account_currency", "credit_in_account_currency", "creation"]


class SqliteGl:
    """`frappe.db.sql` for the GL Entry queries, on SQLite."""

    def __init__(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(f'CREATE TABLE "tabGL Entry" ({", ".join(GL_COLS)})')
        self.conn.execute('CREATE TABLE "tabCompany" (name, default_finance_book)')
        self.n = 0

    def add(self, account, date, debit=0.0, credit=0.0, voucher_no="JE-1", voucher_type="Journal Entry", **kw):
        self.n += 1
        row = {c: None for c in GL_COLS}
        row.update(company="DSC", account=account, posting_date=date, debit=debit, credit=credit,
                   voucher_type=voucher_type, voucher_no=voucher_no, is_opening="No", is_cancelled=0,
                   creation=f"{self.n:06d}")
        row.update(kw)
        self.conn.execute(f'INSERT INTO "tabGL Entry" VALUES ({", ".join("?" * len(GL_COLS))})',
                          [row[c] for c in GL_COLS])

    def sql(self, query, params=None, as_dict=False, **k):
        args = {}
        if isinstance(params, dict):
            def named(m):
                key, val = m.group(1), params[m.group(1)]
                if isinstance(val, (list, tuple)):
                    for i, v in enumerate(val):
                        args[f"{key}_x{i}"] = v
                    return "(" + ", ".join(f":{key}_x{i}" for i in range(len(val))) + ")"
                args[key] = str(val) if val is not None else None
                return f":{key}"
            query = re.sub(r"%\((\w+)\)s", named, query)
            rows = self.conn.execute(query, args).fetchall()
        else:
            rows = self.conn.execute(query.replace("%s", "?"), list(params or [])).fetchall()
        return [ff._dict(dict(r)) for r in rows]


class Base(unittest.TestCase):
    def setUp(self):
        self.site = ff.install()
        import frappe
        self.frappe = frappe
        self.gl = SqliteGl()
        self.settings = {}
        frappe.db.sql = self.gl.sql
        frappe.db.has_column = lambda dt, col: True
        frappe.db.get_single_value = lambda dt, field: self.settings.get(field)
        frappe.db.get_table_columns = lambda dt: list(GL_COLS)
        self.gl.conn.execute("INSERT INTO tabCompany VALUES ('DSC', NULL)")
        for i, (name, root, atype) in enumerate((("Bank - DSC", "Asset", "Bank"), ("Debtors - DSC", "Asset", "Receivable"),
                                                 ("Retained - DSC", "Equity", ""), ("Sales - DSC", "Income", ""))):
            self.site.table("Account")[name] = {
                "name": name, "account_name": name.split(" - ")[0], "account_number": str(i + 1), "root_type": root,
                "account_type": atype, "is_group": 0, "company": "DSC", "disabled": 0, "parent_account": None,
                "lft": 2 * i + 1, "rgt": 2 * i + 2}

    def tearDown(self):
        ff.uninstall()


class GeneralLedgerOptions(Base):
    def setUp(self):
        super().setUp()
        g = self.gl
        g.add("Bank - DSC", "2025-06-30", debit=1000, voucher_no="JE-OLD")
        g.add("Bank - DSC", "2026-01-01", debit=500, voucher_no="JE-OPEN", is_opening="Yes")
        g.add("Bank - DSC", "2026-02-10", debit=200, voucher_no="PE-1", voucher_type="Payment Entry",
              against_voucher="SINV-9")
        g.add("Bank - DSC", "2026-03-05", credit=50, voucher_no="JE-TAX", finance_book="Tax")
        g.add("Bank - DSC", "2026-03-10", debit=70, voucher_no="JE-X", is_cancelled=1)
        g.add("Bank - DSC", "2026-04-01", credit=30, voucher_no="JE-ERR")
        self.site.table("Finance Book")["Tax"] = {"name": "Tax"}
        self.site.table("Journal Entry")["JE-ERR"] = {"name": "JE-ERR", "docstatus": 1, "company": "DSC",
                                                      "voucher_type": "Exchange Rate Revaluation"}
        from neotec_insight.neotec_insight.api import report
        self.report = report

    def run_(self, **kw):
        r = self.report.general_ledger(company="DSC", accounts=["Bank - DSC"], from_date="2026-01-01",
                                       to_date="2026-12-31", **kw)
        b = r["accounts"][0]
        return b["opening_raw"], [t["voucher_no"] for t in b["transactions"]], b["closing_raw"]

    def test_defaults_as_erpnext(self):
        # the opening entry is part of the opening; the other book's and the cancelled entry are left out
        self.assertEqual(self.run_(), (1500.0, ["PE-1", "JE-ERR"], 1670.0))

    def test_show_opening_entries_lists_them(self):
        self.assertEqual(self.run_(show_opening_entries="1"), (1000.0, ["JE-OPEN", "PE-1", "JE-ERR"], 1670.0))

    def test_ignore_is_opening_setting_makes_them_ordinary_entries(self):
        self.settings["ignore_is_opening_check_for_reporting"] = 1
        self.assertEqual(self.run_()[0], 1000.0)

    def test_disable_opening_balance_calculation(self):
        self.assertEqual(self.run_(disable_opening_balance_calculation=1), (0.0, ["JE-OPEN", "PE-1", "JE-ERR"], 670.0))

    def test_finance_book(self):
        self.assertEqual(self.run_(finance_book="Tax"), (1500.0, ["PE-1", "JE-TAX", "JE-ERR"], 1620.0))
        # the company's default book counts while "Include Default FB Entries" is on
        self.gl.conn.execute("UPDATE tabCompany SET default_finance_book = 'Tax'")
        self.assertEqual(self.run_()[2], 1620.0)
        self.assertEqual(self.run_(include_default_book_entries="0")[2], 1670.0)

    def test_without_finance_books_nothing_is_filtered(self):
        self.site.table("Finance Book").clear()
        self.assertEqual(self.run_()[2], 1620.0)

    def test_show_cancelled_entries(self):
        self.assertEqual(self.run_(show_cancelled_entries=1), (1500.0, ["PE-1", "JE-X", "JE-ERR"], 1740.0))

    def test_ignore_exchange_revaluation_journals(self):
        self.assertEqual(self.run_(ignore_err=1), (1500.0, ["PE-1"], 1700.0))

    def test_voucher_filters(self):
        self.assertEqual(self.run_(voucher_no="PE-1"), (0.0, ["PE-1"], 200.0))
        self.assertEqual(self.run_(against_voucher_no="SINV-9"), (0.0, ["PE-1"], 200.0))

    def test_unticked_boxes_sent_as_text_are_off(self):
        self.assertEqual(self.run_(show_cancelled_entries="0", ignore_err="0", show_opening_entries="0",
                                   disable_opening_balance_calculation="0"), (1500.0, ["PE-1", "JE-ERR"], 1670.0))

    def test_party_grouping_uses_the_same_opening_rule(self):
        g = self.gl
        g.add("Debtors - DSC", "2026-01-01", debit=300, voucher_no="SINV-OPEN", is_opening="Yes",
              party_type="Customer", party="Oracle")
        g.add("Debtors - DSC", "2026-05-01", debit=100, voucher_no="SINV-2", party_type="Customer", party="Oracle")
        r = self.report.general_ledger(company="DSC", accounts=["Debtors - DSC"], customer=["Oracle"],
                                       from_date="2026-01-01", to_date="2026-12-31", group_by="party")
        b = r["accounts"][0]
        self.assertEqual((b["opening_raw"], b["closing_raw"]), (300.0, 400.0))


class TrialBalanceRules(Base):
    def setUp(self):
        super().setUp()
        g = self.gl
        g.add("Bank - DSC", "2025-06-30", debit=1000)
        g.add("Sales - DSC", "2025-06-30", credit=1000)
        g.add("Old Cash - DSC", "2025-03-01", debit=40)
        g.add("Retained - DSC", "2025-03-01", credit=40)
        g.add("Sales - DSC", "2025-12-31", debit=1000, voucher_type="Period Closing Voucher", voucher_no="PCV-1")
        g.add("Retained - DSC", "2025-12-31", credit=1000, voucher_type="Period Closing Voucher", voucher_no="PCV-1")
        g.add("Bank - DSC", "2026-01-01", debit=500, is_opening="Yes")
        g.add("Retained - DSC", "2026-01-01", credit=500, is_opening="Yes")
        g.add("Bank - DSC", "2026-02-10", debit=200)
        g.add("Sales - DSC", "2026-02-10", credit=200)
        g.add("Bank - DSC", "2026-03-05", credit=50, finance_book="Tax")
        g.add("Sales - DSC", "2026-03-05", debit=50, finance_book="Tax")
        self.site.table("Finance Book")["Tax"] = {"name": "Tax"}
        self.site.table("Account")["Old Cash - DSC"] = {
            "name": "Old Cash - DSC", "account_name": "Old Cash", "account_number": "9", "root_type": "Asset",
            "account_type": "Cash", "is_group": 0, "company": "DSC", "disabled": 1, "parent_account": None,
            "lft": 11, "rgt": 12}
        from neotec_insight.neotec_insight.utils import balance_execution as be
        self.be = be

    def run_(self, **kw):
        r = self.be.run_trial_balance_engine(company="DSC", fiscal_year_start="2026-01-01", from_date="2026-01-01",
                                             as_of_date="2026-12-31", **kw)
        keys = ("opening_debit", "opening_credit", "period_debit", "period_credit", "closing_debit", "closing_credit")
        return {a["name"].split(" - ")[0]: tuple(a[k] for k in keys) for a in r["accounts"]}, r["totals"]

    def test_defaults_as_erpnext(self):
        rows, totals = self.run_()
        self.assertEqual(rows["Bank"], (1500.0, 0.0, 200.0, 0.0, 1700.0, 0.0))        # opening entry in the opening
        self.assertEqual(rows["Sales"], (0.0, 0.0, 0.0, 200.0, 0.0, 200.0))           # closed year not carried
        self.assertEqual(rows["Retained"], (0.0, 1540.0, 0.0, 0.0, 0.0, 1540.0))
        self.assertEqual(rows["Old Cash"], (40.0, 0.0, 0.0, 0.0, 40.0, 0.0))          # disabled, still listed
        self.assertEqual(totals["opening_debit"], totals["opening_credit"])
        self.assertEqual(totals["closing_debit"], totals["closing_credit"])

    def test_ignore_is_opening_setting(self):
        self.settings["ignore_is_opening_check_for_reporting"] = 1
        self.assertEqual(self.run_()[0]["Bank"], (1000.0, 0.0, 700.0, 0.0, 1700.0, 0.0))

    def test_selected_finance_book_adds_its_entries(self):
        rows, totals = self.run_(finance_book="Tax")
        self.assertEqual(rows["Bank"][2:4], (200.0, 50.0))
        self.assertEqual(totals["closing_debit"], totals["closing_credit"])

    def test_without_period_closing_entry_for_opening(self):
        # no closing before the period: nothing changes
        self.assertEqual(self.run_(with_pcv_opening=False), self.run_())
        # books closed on 31 Dec 2025: the opening is shown as it stood before that closing
        self.site.table("Period Closing Voucher")["PCV-1"] = {
            "name": "PCV-1", "docstatus": 1, "company": "DSC", "period_end_date": "2025-12-31"}
        rows, totals = self.run_(with_pcv_opening=False)
        self.assertEqual(rows["Sales"][:2], (0.0, 1000.0))
        self.assertEqual(rows["Retained"][:2], (0.0, 540.0))
        self.assertEqual(totals["opening_debit"], totals["opening_credit"])
        self.settings["ignore_account_closing_balance"] = 1                           # ERPNext then reads GL only
        self.assertEqual(self.run_(with_pcv_opening=False)[0]["Sales"][:2], (0.0, 0.0))

    def test_party_drill_adds_up_to_the_account_row(self):
        g = self.gl
        g.add("Debtors - DSC", "2025-11-01", debit=90, party_type="Customer", party="Oracle")
        g.add("Debtors - DSC", "2026-01-01", debit=300, is_opening="Yes", party_type="Customer", party="Oracle")
        g.add("Debtors - DSC", "2026-03-01", debit=100, party_type="Customer", party="Oracle")
        g.add("Debtors - DSC", "2026-08-01", credit=60, party_type="Customer", party="Oracle")
        g.add("Debtors - DSC", "2026-08-02", debit=5, finance_book="Tax", party_type="Customer", party="Oracle")
        kw = dict(company="DSC", fiscal_year_start="2026-01-01", as_of_date="2026-12-31", from_date="2026-07-01")
        row = next(a for a in self.be.run_trial_balance_engine(**kw)["accounts"] if a["name"] == "Debtors - DSC")
        party = self.be.run_trial_balance_parties_engine(account="Debtors - DSC", **kw)[0]
        keys = ("opening_debit", "opening_credit", "period_debit", "period_credit", "closing_debit", "closing_credit")
        self.assertEqual(tuple(party[k] for k in keys), (490.0, 0.0, 0.0, 60.0, 430.0, 0.0))
        self.assertEqual(tuple(party[k] for k in keys), tuple(row[k] for k in keys))


class FinanceBookRule(unittest.TestCase):
    def setUp(self):
        ff.install()
        from neotec_insight.neotec_insight.utils import balance_execution as be
        self.be = be

    def tearDown(self):
        ff.uninstall()

    def test_clause(self):
        params = {}
        c = self.be._erp_finance_book_clause(params, None, False)
        self.assertEqual(c, "(IFNULL(g.finance_book, \'\') = \'\')")                 # only entries with no book
        c = self.be._erp_finance_book_clause(params, "IFRS", True)
        self.assertIn("g.finance_book = %(finance_book)s", c)
        self.assertIn("default_finance_book", c)
        self.assertEqual(params, {"finance_book": "IFRS"})


if __name__ == "__main__":
    unittest.main()
