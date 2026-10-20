"""v2.96.0 — one AI client, any provider: OpenAI-compatible (OpenAI, Gemini,
Ollama…), Anthropic (Claude) and Azure OpenAI."""
import json
import sys
import types
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
sys.path.insert(0, str(HERE))
import _fakefrappe as ff  # noqa: E402

GEMINI = "https://generativelanguage.googleapis.com/v1beta/openai/"


class Shapes(unittest.TestCase):
    def setUp(self):
        ff.install()
        from neotec_insight.neotec_insight.utils import ai_client as c
        self.c = c

    def tearDown(self):
        ff.uninstall()

    def test_gemini_openai_compatible(self):
        url, h, b = self.c.build_request(self.c.OPENAI, GEMINI, "gemini-3.8-flash", "K", "sys", "hi", 0.2, 4096)
        self.assertEqual(url, "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions")
        self.assertEqual(h["Authorization"], "Bearer K")
        self.assertEqual(b["messages"][0], {"role": "system", "content": "sys"})
        self.assertEqual((b["model"], b["max_tokens"], b["stream"]), ("gemini-3.8-flash", 4096, False))

    def test_claude_native(self):
        url, h, b = self.c.build_request(self.c.ANTHROPIC, "", "claude-sonnet-5-5", "sk-ant", "sys", "hi", 0.2, 2048)
        self.assertEqual(url, "https://api.anthropic.com/v1/messages")
        self.assertEqual((h["x-api-key"], h["anthropic-version"]), ("sk-ant", "2023-06-01"))
        self.assertNotIn("Authorization", h)
        self.assertEqual(b["system"], "sys")
        self.assertEqual(b["messages"], [{"role": "user", "content": "hi"}])
        self.assertEqual(b["max_tokens"], 2048)
        url2, _h, _b = self.c.build_request(self.c.ANTHROPIC, "https://api.anthropic.com/v1", "m", "k", "", "x", 0, 1)
        self.assertEqual(url2, "https://api.anthropic.com/v1/messages")

    def test_azure(self):
        url, h, b = self.c.build_request(self.c.AZURE, "https://acme.openai.azure.com/", "gpt-prod", "AZ", "", "hi", 0, 100,
                                         "2024-10-21")
        self.assertEqual(url, "https://acme.openai.azure.com/openai/deployments/gpt-prod/chat/completions?api-version=2024-10-21")
        self.assertEqual(h["api-key"], "AZ")
        self.assertNotIn("model", b)

    def test_parse(self):
        self.assertEqual(self.c.parse_response(self.c.ANTHROPIC, {"content": [{"type": "text", "text": "A"},
                                                                              {"type": "text", "text": "B"}]}), "AB")
        self.assertEqual(self.c.parse_response(self.c.OPENAI, {"choices": [{"message": {"content": " ok "}}]}), "ok")
        self.assertEqual(self.c.parse_response(self.c.OPENAI, {"choices": [{"message": {"content": [{"text": "p"}]}}]}), "p")
        self.assertEqual(self.c.parse_response(self.c.OPENAI, "junk"), "")

    def test_errors_in_plain_words(self):
        e = self.c.explain_error(self.c.OPENAI, 400, json.dumps([{"error": {"message": "model not found: gemini-x"}}]))
        self.assertIn("model name", e)
        self.assertIn("gemini-x", e)
        e = self.c.explain_error(self.c.ANTHROPIC, 401, json.dumps({"type": "error", "error": {"type": "authentication_error",
                                                                                               "message": "invalid x-api-key"}}))
        self.assertIn("API key was rejected", e)
        self.assertIn("invalid x-api-key", e)
        self.assertIn("Rate limit", self.c.explain_error(self.c.OPENAI, 429, ""))

    def test_external(self):
        for url, ext in ((GEMINI, True), ("https://api.anthropic.com", True), ("http://localhost:11434/v1", False),
                         ("http://host.docker.internal:11434/v1", False), ("http://10.0.0.5:8000/v1", False),
                         ("http://192.168.1.9/v1", False), ("http://ollama.local/v1", False)):
            self.assertEqual(self.c.is_external(url), ext, url)


class Calls(unittest.TestCase):
    def setUp(self):
        self.site = ff.install()
        import frappe
        self.frappe = frappe
        self.settings = types.SimpleNamespace(ai_enabled=1, ai_provider="OpenAI-compatible", ai_endpoint=GEMINI,
                                              ai_model="gemini-3.8-flash", ai_api_key="set", ai_max_tokens=4096,
                                              ai_timeout_seconds=120, ai_api_version="",
                                              get_password=lambda *a, **k: "KEY")
        frappe.get_single = lambda dt: self.settings
        self.sent, self.replies = [], []
        test = self

        class R:
            def __init__(self, code, body):
                self.status_code, self._b = code, body
                self.text = json.dumps(body)

            def json(self):
                return self._b

            def raise_for_status(self):
                if self.status_code >= 400:
                    raise RequestException(str(self.status_code))

        class RequestException(Exception):
            pass

        def post(url, headers=None, json=None, timeout=None):
            test.sent.append((url, headers, json, timeout))
            code, body = test.replies.pop(0)
            if code == "down":
                raise RequestException("connection refused")
            return R(code, body)
        req = types.ModuleType("requests")
        req.post = post
        req.get = lambda *a, **k: R(200, {"data": [{"id": "models/gemini-3.8-flash"}, {"id": "gemini-2.5-pro"}]})
        req.exceptions = types.SimpleNamespace(RequestException=RequestException)
        sys.modules["requests"] = req
        import time
        time.sleep = lambda s: None
        from neotec_insight.neotec_insight.utils import ai_client
        self.c = ai_client

    def tearDown(self):
        sys.modules.pop("requests", None)
        ff.uninstall()

    def test_gemini_call(self):
        self.replies = [(200, {"choices": [{"message": {"content": "OK"}}]})]
        self.assertEqual(self.c.chat("s", "u"), "OK")
        self.assertTrue(self.sent[0][0].endswith("/v1beta/openai/chat/completions"))
        self.assertEqual(self.sent[0][3], 120)

    def test_claude_call(self):
        self.settings.ai_provider, self.settings.ai_endpoint, self.settings.ai_model = \
            "Anthropic (Claude)", "", "claude-sonnet-5-5"
        self.assertTrue(self.c.ready())                        # no endpoint needed for Claude
        self.replies = [(200, {"content": [{"type": "text", "text": "OK"}]})]
        self.assertEqual(self.c.chat("s", "u"), "OK")
        self.assertEqual(self.sent[0][0], "https://api.anthropic.com/v1/messages")
        self.assertEqual(self.sent[0][1]["x-api-key"], "KEY")

    def test_rate_limit_is_retried_once(self):
        self.replies = [(429, {"error": {"message": "slow down"}}), (200, {"choices": [{"message": {"content": "OK"}}]})]
        self.assertEqual(self.c.chat("s", "u"), "OK")
        self.assertEqual(len(self.sent), 2)

    def test_bad_key_is_explained_and_not_retried(self):
        self.replies = [(401, {"error": {"message": "API key not valid"}})]
        with self.assertRaises(self.c.AIError) as cm:
            self.c.chat("s", "u")
        self.assertIn("API key was rejected", str(cm.exception))
        self.assertEqual(len(self.sent), 1)

    def test_unreachable(self):
        self.replies = [("down", None)]
        with self.assertRaises(self.c.AIError) as cm:
            self.c.chat("s", "u")
        self.assertIn("Could not reach", str(cm.exception))

    def test_off_or_no_model(self):
        self.settings.ai_enabled = 0
        with self.assertRaises(self.c.AIError):
            self.c.chat("s", "u")
        self.settings.ai_enabled, self.settings.ai_model = 1, ""
        with self.assertRaises(self.c.AIError):
            self.c.chat("s", "u")

    def test_models_list_strips_gemini_prefix(self):
        self.assertEqual(self.c.list_models(), ["gemini-2.5-pro", "gemini-3.8-flash"])


class EveryFeatureUsesTheClient(unittest.TestCase):
    def test_no_private_request_code_left(self):
        api = HERE.parents[0] / "neotec_insight" / "api"
        for f in ("ai.py", "studio.py", "copilot.py", "health.py"):
            src = (api / f).read_text()
            self.assertNotIn("/chat/completions", src, f)
            self.assertNotIn('headers["Authorization"]', src, f)
        self.assertIn("ai_client.chat(", (api / "studio.py").read_text())
        self.assertIn("ai_client.chat(", (api / "ai.py").read_text())
        self.assertIn("ai_client.chat(", (api / "copilot.py").read_text())


if __name__ == "__main__":
    unittest.main()
