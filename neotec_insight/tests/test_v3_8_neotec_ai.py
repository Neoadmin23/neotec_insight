"""v3.8.0 — Neotec AI connection: tool schemas, routing switch, integration spec."""
import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
sys.path.insert(0, str(HERE))
import _fakefrappe as ff  # noqa: E402


class Link(unittest.TestCase):
    def setUp(self):
        ff.install()
        from neotec_insight.neotec_insight.utils import copilot_core as core
        from neotec_insight.neotec_insight.utils import neotec_ai_link as link
        self.core, self.link = core, link

    def tearDown(self):
        ff.uninstall()

    def test_every_copilot_tool_gets_a_valid_schema(self):
        for name, spec in self.core.TOOLS.items():
            props = self.link.schema_for(spec)
            req = props.pop("__required__")
            self.assertTrue({"company", "from_date", "to_date"} <= set(props), name)
            for r in req:
                self.assertIn(r, props, name)
            json.dumps(props)

    def test_enum_and_int_shapes(self):
        p = self.link.schema_for(self.core.TOOLS["top_parties"])
        self.assertEqual(p["party"]["enum"], ["customer", "supplier"])
        self.assertEqual(p["top_n"]["type"], "integer")
        self.assertEqual(sorted(p["__required__"]), ["metric", "party"])

    def test_integration_spec(self):
        spec = self.link.integration()
        self.assertEqual(spec["settings_doctype"], "Insight AI Settings")
        self.assertEqual(spec["on_value"], "Neotec AI")
        self.assertNotEqual(spec["on_value"], spec["off_value"])

    def test_not_routed_without_the_app(self):
        self.assertFalse(self.link.routed())

    def test_hooks_point_at_real_functions(self):
        src = (HERE.parents[0] / "hooks.py").read_text()
        self.assertIn("neotec_ai_link.get_tools", src)
        self.assertIn("neotec_ai_link.integration", src)


if __name__ == "__main__":
    unittest.main()
