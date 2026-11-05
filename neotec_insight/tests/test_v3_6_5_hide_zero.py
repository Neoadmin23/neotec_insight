"""v3.6.5 — ageing pack: parties with a zero balance are hidden by default
(negative balances stay); an option shows them."""
import io
import json
import sys
import types
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
sys.path.insert(0, str(HERE))
import _fakefrappe as ff  # noqa: E402


def ageing(company=None, as_of=None, party_type="Customer", split_not_due=0, **k):
    from neotec_insight.neotec_insight.api.ageing import _slab_labels
    rows = []
    if party_type == "Supplier":
        for n, inv, paid, out in (("Oracle Systems Limited", 2863942.96, 2468630.46, 395312.50),
                                  ("Tawuniya", 1346425.27, 1288778.64, 0.0),     # settled (with a debit note)
                                  ("CERCLI PAYROLL PAYMENTS LLC", 6775862.43, 6775862.43, 0.0),
                                  ("Digital Twins", 6116088.12, 6121977.32, -5889.20)):
            rows.append({"party": n, "label": n, "invoiced": inv, "paid": paid, "outstanding": out, "not_due": 0.0,
                         "buckets": [out, 0, 0, 0, 0]})
    return {"rows": rows, "labels": _slab_labels([30, 60, 90, 120], "days"), "currency": "SAR"}


class HideZero(unittest.TestCase):
    def setUp(self):
        self.site = ff.install()
        import frappe
        self.frappe = frappe
        self.site.table("Company")["Alpha Co"] = {"name": "Alpha Co", "company_name": "Alpha", "default_currency": "SAR"}
        frappe.local.response = types.SimpleNamespace()
        from neotec_insight.neotec_insight.api import ageing as ag, packs
        self.packs = packs
        ag.ar_ap_ageing = ageing
        packs._notes_by_party = lambda c, d, pt: {"Tawuniya": 57646.62} if pt == "Supplier" else {}
        packs._seed_ageing_pack()

    def tearDown(self):
        ff.uninstall()

    def names(self, **ap_opts):
        rec = self.site.table("Insight Export Pack")["ar_ap_ageing"]
        cfg = json.loads(rec["config_json"])
        cfg["sheets"][2]["options"].update(ap_opts)
        rec["config_json"] = json.dumps(cfg)
        self.packs.generate_pack("ar_ap_ageing", "Alpha Co", "2026-10-31", "2026-10-31")
        import openpyxl
        ws = openpyxl.load_workbook(io.BytesIO(self.frappe.local.response.filecontent))["AP Ageing"]
        out = []
        for r in range(10, 40):
            v = ws.cell(row=r, column=2).value
            if ws.cell(row=r, column=1).value == "Total" or v is None:
                break
            out.append(v)
        return out

    def test_zero_hidden_by_default_negative_kept(self):
        self.assertEqual(self.names(), ["Oracle Systems Limited", "Digital Twins"])

    def test_option_shows_them(self):
        self.assertEqual(len(self.names(show_zero=1)), 4)

    def test_option_is_offered_on_both_sheets(self):
        types_ = {t["type"]: [o["key"] for o in t["options"]] for t in self.packs.SHEET_TYPES}
        self.assertIn("show_zero", types_["ar_ageing"])
        self.assertIn("show_zero", types_["ap_ageing"])


if __name__ == "__main__":
    unittest.main()
