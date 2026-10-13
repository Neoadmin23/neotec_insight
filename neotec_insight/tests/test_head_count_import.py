"""Pasted head-count table: parsing and cost-centre matching."""
import ast, re, unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "neotec_insight" / "api" / "allocation.py"


def _ns():
    tree = ast.parse(SRC.read_text())
    want = {"parse_head_count_table", "_norm_cc", "match_cost_centre"}
    body = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in want]
    ns = {"re": re}
    exec(compile(ast.Module(body=body, type_ignores=[]), "x", "exec"), ns)
    return ns


NS = _ns()


class TestParse(unittest.TestCase):
    def test_tab_separated(self):
        r = NS["parse_head_count_table"]("Audit & GRC\t8\t8\t8\t8\t9\t8\t8\n")
        self.assertEqual(r, [("Audit & GRC", {1: 8, 2: 8, 3: 8, 4: 8, 5: 9, 6: 8, 7: 8})])

    def test_blank_cells_skipped_so_capture_can_fill_them(self):
        r = NS["parse_head_count_table"]("HR\t3\t4\t\t\t4\n")
        self.assertEqual(r[0][1], {1: 3, 2: 4, 5: 4})

    def test_header_and_blank_lines_ignored(self):
        r = NS["parse_head_count_table"]("Cost centre\tJan\tFeb\n\nGMO\t16\t16\n")
        self.assertEqual([x[0] for x in r], ["GMO"])

    def test_ytd_column_beyond_twelve_ignored(self):
        r = NS["parse_head_count_table"]("GMO\t" + "\t".join(["16"] * 12) + "\t192")
        self.assertEqual(len(r[0][1]), 12)

    def test_comma_and_thousands(self):
        r = NS["parse_head_count_table"]("GMO,16,16,16,16")
        self.assertEqual(r[0][1], {1: 16, 2: 16, 3: 16, 4: 16})

    def test_zero_is_a_value(self):
        r = NS["parse_head_count_table"]("Fixed Assets\t0\t0")
        self.assertEqual(r[0][1], {1: 0, 2: 0})


class TestMatch(unittest.TestCase):
    OPTS = [{"name": "Audit & GRC - IRSAA", "cost_center_name": "Audit & GRC"},
            {"name": "Sales- IRSAA", "cost_center_name": "Sales- IRSAA"},
            {"name": "GMO - IRSAA", "cost_center_name": "GMO"}]

    def m(self, label, opts=None):
        return NS["match_cost_centre"](label, opts or self.OPTS)

    def test_by_display_name(self):
        self.assertEqual(self.m("Audit & GRC"), "Audit & GRC - IRSAA")

    def test_case_and_hyphen_spacing(self):
        self.assertEqual(self.m("sales - irsaa"), "Sales- IRSAA")
        self.assertEqual(self.m("GMO - IRSAA"), "GMO - IRSAA")

    def test_unknown_is_none(self):
        self.assertIsNone(self.m("Nonexistent"))

    def test_ambiguous_is_none(self):
        opts = [{"name": "GMO - A", "cost_center_name": "GMO"}, {"name": "GMO - B", "cost_center_name": "GMO"}]
        self.assertIsNone(self.m("GMO", opts))


if __name__ == "__main__":
    unittest.main()
