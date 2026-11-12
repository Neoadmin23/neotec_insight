"""v3.8.5 — ERPNext's Trial Balance options."""
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
sys.path.insert(0, str(HERE))
import _fakefrappe as ff  # noqa: E402
from test_v3_8_4_tb_pl_opening_gl_flags import COA  # noqa: E402

GL = [("Round Off - DSC", "2025-12-31", 0.01, 0.0, "Journal Entry"),
      ("Round Off - DSC", "2026-03-31", 4.34, 0.0, "Journal Entry"),
      ("Round Off - DSC", "2026-12-31", 0.0, 4.34, "Period Closing Voucher")]


class TbErpOptions(unittest.TestCase):
    def setUp(self):
        self.site = ff.install()
        from neotec_insight.neotec_insight.utils import balance_execution as be
        self.be = be
        be._load_chart_of_accounts = lambda company: [dict(a) for a in COA]

        def sums(company=None, from_date=None, to_date=None, exclude_pcv=False, **k):
            out = {}
            for acc, d, dr, cr, vt in GL:
                if exclude_pcv and vt == "Period Closing Voucher":
                    continue
                if (from_date is None or d >= str(from_date)) and (to_date is None or d <= str(to_date)):
                    s = out.setdefault(acc, {"debit": 0.0, "credit": 0.0})
                    s["debit"] += dr
                    s["credit"] += cr
            return out
        be._gl_sums_per_account = sums

    def tearDown(self):
        ff.uninstall()

    def ro(self, **opts):
        r = self.be.run_trial_balance_engine(company="DSC", fiscal_year_start="2026-01-01", as_of_date="2026-12-31",
                                             from_date="2026-01-01", show_zero_values=True, **opts)
        return next(a for a in r["accounts"] if a["name"] == "Round Off - DSC")

    def test_defaults_match_erpnext(self):
        r = self.ro()
        self.assertEqual((r["opening_debit"], r["period_debit"], r["period_credit"]), (0.0, 4.34, 4.34))

    def test_unclosed_pl_and_without_pcv_opening(self):
        self.assertEqual(self.ro(show_unclosed_pl=True)["opening_debit"], 0.01)
        # v3.8.6 — as ERPNext: the option leaves Period Closing Vouchers out of the
        # opening, which matters only when the books were closed before the period
        self.assertEqual(self.ro(with_pcv_opening=False)["opening_debit"], 0.0)
        self.be._last_period_closing_end = lambda company, before: "2025-12-31"
        self.assertEqual(self.ro(with_pcv_opening=False)["opening_debit"], 0.01)

    def test_pcv_current_period_off(self):
        self.assertEqual(self.ro(pcv_current=False)["period_credit"], 0.0)          # the closing voucher is left out

    def test_default_book_condition(self):
        conds, params = [], {}
        self.be._apply_extra_gl_filters(conds, params, finance_book="IFRS", include_default_fb=True)
        self.assertIn("IFNULL(g.finance_book, '') = ''", conds[0])
        self.assertIn("default_finance_book", conds[0])
        conds = []
        self.be._apply_extra_gl_filters(conds, params, finance_book="IFRS")
        self.assertEqual(conds, ["g.finance_book = %(finance_book)s"])


if __name__ == "__main__":
    unittest.main()
