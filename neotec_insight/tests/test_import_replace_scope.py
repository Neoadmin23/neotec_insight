"""v2.88.9 - a replace-import clears only the doctypes the bundle carries."""
import ast, unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "neotec_insight" / "api" / "report.py"


class TestReplaceScope(unittest.TestCase):
    def fn(self, order):
        tree = ast.parse(SRC.read_text())
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_doctypes_to_clear")
        node.decorator_list = []
        ns = {"_IMPORT_ORDER": order}
        exec(compile(ast.Module(body=[node], type_ignores=[]), "x", "exec"), ns)
        return ns["_doctypes_to_clear"]

    def test_only_bundle_doctypes(self):
        f = self.fn(["Insight Dashboard", "Insight Allocation Rule", "Insight GTPL Rule"])
        self.assertEqual(f({"Insight Dashboard": [], "Insight GTPL Rule": [1]}),
                         ["Insight Dashboard", "Insight GTPL Rule"])

    def test_allocation_rules_untouched_when_absent(self):
        f = self.fn(["Insight Dashboard", "Insight Allocation Rule"])
        self.assertNotIn("Insight Allocation Rule", f({"Insight Dashboard": []}))

    def test_empty_bundle_clears_nothing(self):
        self.assertEqual(self.fn(["A"])({}), [])
        self.assertEqual(self.fn(["A"])(None), [])


if __name__ == "__main__":
    unittest.main()
