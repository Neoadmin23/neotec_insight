"""v3.8.4 — Trial Balance: income/expense accounts open at zero each fiscal
year (as ERPNext); General Ledger: unticked "show accounts…" options work."""
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
sys.path.insert(0, str(HERE))
import _fakefrappe as ff  # noqa: E402

COA = [{"name": "Round Off - DSC", "account_name": "Round Off", "account_number": "6/1/01/01/030", "root_type": "Expense",
        "is_group": 0, "parent_account": None, "lft": 1, "rgt": 2},
       {"name": "Bank - DSC", "account_name": "Bank", "account_number": "1/1/01/02/001", "root_type": "Asset",
        "is_group": 0, "parent_account": None, "lft": 3, "rgt": 4}]
# prior years: Round Off 0.01 (left after closing), Bank 1,000; 2026: Round Off 5.62 / 1.37 by July
GL = [("Round Off - DSC", "2025-12-31", 0.01, 0.0), ("Bank - DSC", "2025-06-30", 1000.0, 0.0),
      ("Round Off - DSC", "2026-03-31", 4.34, 0.0), ("Round Off - DSC", "2026-08-10", 1.28, 1.37)]


class TbPlOpening(unittest.TestCase):
    def setUp(self):
        self.site = ff.install()
        from neotec_insight.neotec_insight.utils import balance_execution as be
        self.be = be
        be._load_chart_of_accounts = lambda company: [dict(a) for a in COA]

        def sums(company=None, from_date=None, to_date=None, **k):
            out = {}
            for acc, d, dr, cr in GL:
                if (from_date is None or d >= str(from_date)) and (to_date is None or d <= str(to_date)):
                    s = out.setdefault(acc, {"debit": 0.0, "credit": 0.0})
                    s["debit"] += dr
                    s["credit"] += cr
            return out
        be._gl_sums_per_account = sums

    def tearDown(self):
        ff.uninstall()

    def row(self, res, name):
        return next(a for a in res["accounts"] if a["name"] == name)

    def run_(self, frm):
        return self.be.run_trial_balance_engine(company="DSC", fiscal_year_start="2026-01-01", as_of_date="2026-12-31",
                                                from_date=frm)

    def test_expense_opens_at_zero_on_the_first_day_of_the_year(self):
        r = self.run_("2026-01-01")
        ro = self.row(r, "Round Off - DSC")
        self.assertEqual((ro["opening_debit"], ro["opening_credit"]), (0.0, 0.0))     # ERPNext: 0.00, not 0.01
        self.assertEqual(self.row(r, "Bank - DSC")["opening_debit"], 1000.0)           # balance sheet keeps history

    def test_mid_year_opening_counts_from_the_fiscal_year_start(self):
        ro = self.row(self.run_("2026-07-01"), "Round Off - DSC")
        self.assertEqual(ro["opening_debit"], 4.34)                                     # Jan–Jun 2026 only


class GlFlags(unittest.TestCase):
    def test_flags_are_converted_before_use(self):
        src = (HERE.parents[0] / "neotec_insight" / "api" / "report.py").read_text()
        for f in ("show_without_transactions", "show_zero_closing", "show_only_opening"):
            self.assertIn(f"{f} = _cint({f})", src)


if __name__ == "__main__":
    unittest.main()
