"""classify_entries_state - what to tell someone whose rule has no entries."""
import ast, unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "neotec_insight" / "api" / "allocation.py"


def _fn():
    tree = ast.parse(SRC.read_text())
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "classify_entries_state")
    ns = {}
    exec(compile(ast.Module(body=[node], type_ignores=[]), "x", "exec"), ns)
    return ns["classify_entries_state"]


class TestEntriesState(unittest.TestCase):
    def setUp(self):
        self.f = _fn()

    def test_ok_when_rule_has_entries(self):
        self.assertEqual(self.f(5, [{"rule": "x", "entries": 3}], [{"rule": "y", "entries": 2}]), "ok")

    def test_deleted_log_first(self):
        self.assertEqual(self.f(0, [{"rule": "x", "entries": 3}], [{"rule": "y", "entries": 2}]), "deleted")

    def test_orphans(self):
        self.assertEqual(self.f(0, [{"rule": "x", "entries": 3}], []), "orphans")

    def test_none(self):
        self.assertEqual(self.f(0, [], []), "none")
        self.assertEqual(self.f(0, [{"rule": "x", "entries": 0}], [{"rule": "y", "entries": 0}]), "none")


if __name__ == "__main__":
    unittest.main()
