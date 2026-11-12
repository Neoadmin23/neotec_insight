"""v3.9.0 — Trial Balance through ERPNext's own report (exact figures)."""
import sys
import types
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
sys.path.insert(0, str(HERE))
import _fakefrappe as ff  # noqa: E402

COA = [{"name": "6/0 - Expenses", "account_name": "Operating Expenses", "account_number": "6/0/00/00/000", "root_type": "Expense",
        "is_group": 1, "parent_account": None, "lft": 1, "rgt": 4, "account_type": ""},
       {"name": "Round Off - DSC", "account_name": "Round Off", "account_number": "6/1/01/01/030", "root_type": "Expense",
        "is_group": 0, "parent_account": "6/0 - Expenses", "lft": 2, "rgt": 3, "account_type": "Round Off"}]
ERP_ROWS = [{"account": "6/0 - Expenses", "indent": 0, "opening_debit": 0, "opening_credit": 0, "debit": 5.62, "credit": 1.37,
             "closing_debit": 4.25, "closing_credit": 0},
            {"account": "Round Off - DSC", "indent": 1, "opening_debit": 0, "opening_credit": 0, "debit": 5.62, "credit": 1.37,
             "closing_debit": 4.25, "closing_credit": 0},
            {},
            {"account": "'Total'", "opening_debit": 46941856.33, "opening_credit": 46941856.33, "debit": 78120334.71,
             "credit": 78120334.71, "closing_debit": 58365706.37, "closing_credit": 58365706.37}]


class ErpEngine(unittest.TestCase):
    def setUp(self):
        self.site = ff.install()
        import frappe
        self.frappe = frappe
        self.sent = {}
        mod = types.ModuleType("erpnext.accounts.report.trial_balance.trial_balance")
        mod.execute = lambda f: (self.sent.update(f) or ([], [dict(r) for r in ERP_ROWS]))
        for n in ("erpnext", "erpnext.accounts", "erpnext.accounts.report", "erpnext.accounts.report.trial_balance"):
            sys.modules.setdefault(n, types.ModuleType(n))
        sys.modules["erpnext.accounts.report.trial_balance"].trial_balance = mod
        sys.modules[mod.__name__] = mod
        self.site.table("Fiscal Year")["2026"] = {"name": "2026", "year_start_date": "2026-01-01"}
        self.site.table("Company")["DSC"] = {"name": "DSC", "default_currency": "SAR"}
        from neotec_insight.neotec_insight.utils import balance_execution as be, erpnext_engine as ee
        be._load_chart_of_accounts = lambda company: [dict(a) for a in COA]
        self.ee = ee

    def tearDown(self):
        for n in [k for k in sys.modules if k.startswith("erpnext")]:
            sys.modules.pop(n, None)
        ff.uninstall()

    def test_figures_are_erpnexts(self):
        r = self.ee.trial_balance(company="DSC", fiscal_year_start="2026-01-01", as_of_date="2026-12-31",
                                  from_date="2026-01-01", cost_center=["Main - DSC"])
        ro = next(a for a in r["accounts"] if a["name"] == "Round Off - DSC")
        self.assertEqual((ro["opening_debit"], ro["period_debit"], ro["period_credit"], ro["closing_debit"]),
                         (0.0, 5.62, 1.37, 4.25))
        self.assertEqual((ro["code"], ro["depth"], ro["parent"]), ("6/1/01/01/030", 1, "6/0 - Expenses"))
        self.assertEqual(r["totals"]["period_debit"], 78120334.71)        # ERPNext's own period total
        self.assertEqual((self.sent["fiscal_year"], self.sent["cost_center"], self.sent["show_net_values"],
                          self.sent["with_period_closing_entry_for_opening"]), ("2026", "Main - DSC", 1, 1))
        self.assertEqual(r["engine"], "erpnext")

    def test_group_accounts_hidden_on_request(self):
        r = self.ee.trial_balance(company="DSC", fiscal_year_start="2026-01-01", as_of_date="2026-12-31",
                                  show_group_accounts=False)
        self.assertEqual([a["name"] for a in r["accounts"]], ["Round Off - DSC"])

    def test_when_erpnext_applies(self):
        self.assertEqual(self.ee.fits(["Main - DSC"], None, None), (True, ""))
        self.assertEqual(self.ee.fits(["A", "B"], None, None)[1], "several cost centres")
        self.assertEqual(self.ee.fits(None, None, ["Asset"])[1], "a subset of root types")


if __name__ == "__main__":
    unittest.main()


class ErpTotals(ErpEngine):
    """v3.9.2 — opening/closing totals from the netted top-level rows (ERPNext's screen),
    not ERPNext's gross Total row; the period keeps ERPNext's own total."""

    def test_net_totals(self):
        global ERP_ROWS
        rows = [dict(r) for r in ERP_ROWS]
        rows[0].update(opening_debit=37872016.91, opening_credit=3385813.51, closing_debit=10.0, closing_credit=4.0)
        rows[3].update(opening_debit=50480533.73, opening_credit=50480533.73)          # ERPNext's gross Total
        import erpnext.accounts.report.trial_balance.trial_balance as tb
        tb.execute = lambda f: ([], rows)
        r = self.ee.trial_balance(company="DSC", fiscal_year_start="2026-01-01", as_of_date="2026-12-31")
        self.assertEqual((r["totals"]["opening_debit"], r["totals"]["opening_credit"]), (34486203.4, 0.0))
        self.assertEqual((r["totals"]["closing_debit"], r["totals"]["closing_credit"]), (6.0, 0.0))
        self.assertEqual(r["totals"]["period_debit"], 78120334.71)                      # ERPNext's own


class ErpRounding(ErpEngine):
    """v3.9.3 — unrounded ERPNext rows must not leave a halala in the totals."""

    def test_rounded_before_adding(self):
        rows = [dict(r) for r in ERP_ROWS]
        rows[0].update(closing_debit=23582680.0600000002, closing_credit=0.0)
        rows.insert(1, {"account": "1/0 - Assets", "indent": 0, "closing_debit": 0.0, "closing_credit": 23582680.0599999998})
        import erpnext.accounts.report.trial_balance.trial_balance as tb
        tb.execute = lambda f: ([], rows)
        import neotec_insight.neotec_insight.utils.balance_execution as be
        be._load_chart_of_accounts = lambda company: [dict(a) for a in COA] + [
            {"name": "1/0 - Assets", "account_name": "Assets", "account_number": "1/0", "root_type": "Asset", "is_group": 1,
             "parent_account": None, "lft": 5, "rgt": 6, "account_type": ""}]
        r = self.ee.trial_balance(company="DSC", fiscal_year_start="2026-01-01", as_of_date="2026-12-31")
        self.assertEqual(r["totals"]["closing_debit"], r["totals"]["closing_credit"])
