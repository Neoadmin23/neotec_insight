"""status_by_month — what the capture picker shows for earlier months."""
import ast, datetime, unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "neotec_insight" / "utils" / "driver_capture.py"


def _fn():
    tree = ast.parse(SRC.read_text())
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "status_by_month")
    ns = {}
    exec(compile(ast.Module(body=[node], type_ignores=[]), "x", "exec"), ns)
    return ns["status_by_month"]


def e(m, source="manual", frozen=0, on=None):
    return {"period_month": datetime.date(2026, m, 1), "source": source, "is_frozen": frozen, "captured_on": on}


class TestStatusByMonth(unittest.TestCase):
    def setUp(self):
        self.s = _fn()([
            e(1, frozen=1), e(1, frozen=1),                      # manual, frozen
            e(8, "employee_headcount", 1, "2026-09-01 10:00"),  # captured + frozen
            e(9, "employee_headcount", 0, "2026-10-01 09:00"),  # captured, open
            e(9, "employee_headcount", 0, "2026-10-02 09:00"),
            e(7),                                               # manual, open
        ])

    def test_states(self):
        self.assertEqual(self.s[1]["state"], "frozen")
        self.assertEqual(self.s[8]["state"], "frozen")
        self.assertEqual(self.s[9]["state"], "captured")
        self.assertEqual(self.s[7]["state"], "manual")
        self.assertEqual(self.s[12]["state"], "empty")

    def test_latest_capture_time(self):
        self.assertEqual(self.s[9]["captured_on"], "2026-10-02 09:00")

    def test_covers_all_twelve_months(self):
        self.assertEqual(sorted(self.s), list(range(1, 13)))


if __name__ == "__main__":
    unittest.main()
