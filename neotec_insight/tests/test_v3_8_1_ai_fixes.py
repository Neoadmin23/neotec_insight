"""v3.8.1 — health-check fixes for the Neotec AI connection:
bank-slip reading falls back to Insight's own model when the gateway fails;
coworker tools return the real column names."""
import sys
import types
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
sys.path.insert(0, str(HERE))
import _fakefrappe as ff  # noqa: E402


class Fixes(unittest.TestCase):
    def setUp(self):
        self.site = ff.install()
        import frappe
        self.frappe = frappe
        self.logged = []
        frappe.log_error = lambda *a, **k: self.logged.append(k.get("title") or (a[0] if a else ""))
        frappe.get_traceback = lambda *a, **k: "tb"
        plat = types.ModuleType("neotec_ai.platform")

        def boom(*a, **k):
            raise RuntimeError("gateway down")
        plat.complete = boom
        plat.is_available = lambda: True
        plat.extract_json = lambda s: {}
        sys.modules["neotec_ai"] = types.ModuleType("neotec_ai")
        sys.modules["neotec_ai.platform"] = plat
        from neotec_insight.neotec_insight.utils import neotec_ai_link
        self.link = neotec_ai_link
        neotec_ai_link.routed = lambda s=None: True

    def tearDown(self):
        sys.modules.pop("neotec_ai", None)
        sys.modules.pop("neotec_ai.platform", None)
        ff.uninstall()

    def test_bank_slip_falls_back_when_gateway_fails(self):
        from neotec_insight.neotec_insight.api import bank_reader as br
        br._ollama_config = lambda: {"url": "http://local", "base_url": "http://local", "model": "qwen", "text_model": "qwen", "vision_model": "qwen-vl", "timeout": 5}
        calls = []

        class R:
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return {"message": {"content": '{"amount": 1150, "currency": "SAR"}'}, "response": '{"amount": 1150}'}
        import requests
        requests.post = lambda *a, **k: calls.append(a[0] if a else k.get("url")) or R()
        out = br._ollama_structure_text("Transfer of SAR 1,150 to Al Bani Development")
        self.assertTrue(calls, "Insight's own model was not called after the gateway failed")
        self.assertEqual(out.get("amount"), 1150)
        self.assertTrue(any("Neotec AI failed" in str(t) for t in self.logged))

    def test_coworker_columns_have_names(self):
        from neotec_insight.neotec_insight.api import copilot
        from neotec_insight.neotec_insight.utils import access
        access.require_finance_read = lambda: None
        copilot._company = lambda c=None: "IRSAA"
        copilot._period = lambda c, f, t: ("2026-01-01", "2026-10-31")
        copilot.RUNNERS["kpis"] = lambda c, f, t, **a: (
            [{"key": "metric", "label": "Metric"}, {"key": "value", "label": "Value"}], [{"metric": "Revenue", "value": 1.0}], "KPIs")
        out = self.link._runner("kpis")()
        self.assertEqual((out["columns"], out["column_labels"]), (["metric", "value"], ["Metric", "Value"]))


if __name__ == "__main__":
    unittest.main()
