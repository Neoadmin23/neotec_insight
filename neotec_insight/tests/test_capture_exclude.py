"""v2.88.5 - drift placeholders, cost-centre exclusion, partial-freeze status."""
import ast, datetime, unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "neotec_insight" / "utils" / "driver_capture.py"


def _ns():
    tree = ast.parse(SRC.read_text())
    want = {"row_action", "drop_excluded", "status_by_month"}
    body = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in want]
    ns = {"flt": lambda x: float(x or 0)}
    exec(compile(ast.Module(body=body, type_ignores=[]), "x", "exec"), ns)
    return ns


NS = _ns()


class TestRowAction(unittest.TestCase):
    def ra(self, ex, counted, **kw):
        return NS["row_action"](ex, counted, **kw)

    def test_frozen_zero_placeholder_is_written(self):
        # The August case: frozen, value 0, never captured, counted 5.
        ex = {"driver_value": 0, "is_frozen": 1, "source": "manual", "captured_on": None}
        self.assertEqual(self.ra(ex, 5), "update")

    def test_frozen_captured_value_is_still_drift(self):
        ex = {"driver_value": 4, "is_frozen": 1, "source": "employee_headcount",
              "captured_on": "2026-09-01 10:00"}
        self.assertEqual(self.ra(ex, 5), "drift")
        self.assertEqual(self.ra(ex, 4), "frozen")

    def test_frozen_manual_nonzero_is_drift(self):
        ex = {"driver_value": 6, "is_frozen": 1, "source": "manual", "captured_on": None}
        self.assertEqual(self.ra(ex, 7), "drift")

    def test_frozen_manual_zero_but_captured_is_drift(self):
        ex = {"driver_value": 0, "is_frozen": 1, "source": "manual", "captured_on": "x"}
        self.assertEqual(self.ra(ex, 3), "drift")

    def test_plain_actions(self):
        self.assertEqual(self.ra(None, 3), "write")
        self.assertEqual(self.ra({"driver_value": 2}, 3), "update")
        self.assertEqual(self.ra({"driver_value": 3}, 3), "unchanged")
        self.assertEqual(self.ra({"driver_value": 2, "is_override": 1}, 3), "kept")

    def test_excluded_wins(self):
        self.assertEqual(self.ra(None, 3, excluded=True), "excluded")
        self.assertEqual(self.ra({"driver_value": 2, "is_frozen": 1}, 3, excluded=True), "excluded")


class TestDropExcluded(unittest.TestCase):
    def test_moves_out_of_counts(self):
        hc = {"counts": {"A": 5, "B": 2}, "evidence": {"A": [1] * 5, "B": [1, 2]}}
        out = NS["drop_excluded"](hc, {"B"})
        self.assertEqual(out["counts"], {"A": 5})
        self.assertEqual(out["excluded_counts"], {"B": 2})
        self.assertNotIn("B", out["evidence"])
        self.assertEqual(len(out["excluded_evidence"]["B"]), 2)

    def test_denominator_ignores_excluded(self):
        hc = NS["drop_excluded"]({"counts": {"A": 5, "B": 5}, "evidence": {}}, {"B"})
        self.assertEqual(sum(hc["counts"].values()), 5)

    def test_none_excluded_is_noop(self):
        hc = NS["drop_excluded"]({"counts": {"A": 1}, "evidence": {"A": [1]}}, set())
        self.assertEqual(hc["counts"], {"A": 1})
        self.assertEqual(hc["excluded_counts"], {})


class TestPartialStatus(unittest.TestCase):
    def test_partial(self):
        d = lambda m, fr: {"period_month": datetime.date(2026, m, 1), "source": "manual",
                           "is_frozen": fr, "captured_on": None}
        s = NS["status_by_month"]([d(8, 1), d(8, 0), d(8, 1)])
        self.assertEqual(s[8]["state"], "partial")


if __name__ == "__main__":
    unittest.main()
