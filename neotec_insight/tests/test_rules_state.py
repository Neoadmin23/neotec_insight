"""classify_rules_state - why the Allocation rule picker is empty."""
import ast, unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "neotec_insight" / "api" / "allocation.py"


def _fn():
    tree = ast.parse(SRC.read_text())
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "classify_rules_state")
    ns = {}
    exec(compile(ast.Module(body=[node], type_ignores=[]), "x", "exec"), ns)
    return ns["classify_rules_state"]


class TestRulesState(unittest.TestCase):
    def setUp(self):
        self.f = _fn()

    def test_ok(self):
        self.assertEqual(self.f("A", [{"name": "r", "company": "A", "is_active": 1}], [], []), "ok")

    def test_other_company(self):
        self.assertEqual(self.f("A", [{"name": "r", "company": "B", "is_active": 1}], [], []), "other_company")

    def test_inactive_only(self):
        self.assertEqual(self.f("A", [{"name": "r", "company": "A", "is_active": 0}], [], []), "inactive_only")

    def test_deleted_beats_orphans(self):
        self.assertEqual(self.f("A", [], [{"rule": "x", "entries": 5}],
                                [{"deleted_name": "x", "restored": 0}]), "deleted")

    def test_restored_deletion_ignored(self):
        self.assertEqual(self.f("A", [], [], [{"deleted_name": "x", "restored": 1}]), "none_exist")

    def test_orphan_entries(self):
        self.assertEqual(self.f("A", [], [{"rule": "x", "entries": 5}], []), "orphan_entries")

    def test_none(self):
        self.assertEqual(self.f("A", [], [], []), "none_exist")

    def test_error_wins(self):
        self.assertEqual(self.f("A", [{"name": "r", "company": "A", "is_active": 1}], [], [], "boom"), "read_error")


if __name__ == "__main__":
    unittest.main()
