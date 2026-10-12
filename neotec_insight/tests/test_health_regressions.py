"""Regressions found by the v2.88.4 health check (static analysis)."""
import ast, unittest
from pathlib import Path

API = Path(__file__).resolve().parents[1] / "neotec_insight" / "api"


class TestNoUndefinedHelpers(unittest.TestCase):
    def test_reconcile_imports_nowdate(self):
        src = (API / "reconcile.py").read_text()
        tree = ast.parse(src)
        imported = {a.asname or a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for a in n.names}
        self.assertIn("nowdate", imported)

    def test_every_default_company_use_in_report_is_imported(self):
        tree = ast.parse((API / "report.py").read_text())
        top = {a.asname or a.name for n in tree.body if isinstance(n, ast.ImportFrom) for a in n.names}
        for fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
            uses = any(isinstance(x, ast.Name) and x.id == "_default_company" for x in ast.walk(fn))
            local = any(isinstance(x, ast.ImportFrom) and any(a.name == "_default_company" for a in x.names)
                        for x in ast.walk(fn))
            if uses:
                self.assertTrue(local or "_default_company" in top, fn.name)

    def test_fiscal_year_fallback_filters_are_a_list(self):
        """A dict with a repeated key silently dropped the lower bound."""
        self.assertNotIn('"year_start_date": [">="', (API / "report.py").read_text())


if __name__ == "__main__":
    unittest.main()
