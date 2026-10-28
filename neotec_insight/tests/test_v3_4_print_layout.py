"""v3.4.0 — per-report print layout + letterhead (Print/PDF/Excel), and the
Statement of Account report (General Ledger or Ageing source)."""
import re
import shutil
import subprocess
import unittest
from pathlib import Path

RM = Path(__file__).resolve().parents[2] / "reportManager"
SRC = RM / "src"


class Wiring(unittest.TestCase):
    def test_every_exporting_report_has_its_own_layout_key(self):
        for f in SRC.rglob("*.tsx"):
            for m in re.finditer(r"<ExportBar\b([^>]*)>", f.read_text()):
                self.assertIn("reportKey=", m.group(1), f.name)

    def test_exports_apply_layout_and_letterhead(self):
        bar = (SRC / "features/ExportBar.tsx").read_text()
        self.assertIn("layout: { ...(doc.layout || {}), ...toLayout(ps) }", bar)
        self.assertIn("letterhead: lh", bar)
        doc = (SRC / "utils/reportdoc.ts").read_text()
        self.assertIn("return mergeBrand(b, doc.layout, doc.letterhead);", doc)
        # Print, PDF, CSV and Excel all resolve the brand through resolveBrand
        self.assertGreaterEqual(doc.count("resolveBrand(doc)"), 4)

    def test_statement_report_registered_with_both_sources(self):
        app = (SRC / "App.tsx").read_text()
        self.assertIn("ws: 'statement'", app)
        st = (SRC / "features/statement/StatementApp.tsx").read_text()
        self.assertIn("api.generalLedger(", st)
        self.assertIn("api.billwise(", st)
        self.assertIn('reportKey="statement"', st)


@unittest.skipUnless(shutil.which("node"), "node not installed")
class PrintLogic(unittest.TestCase):
    def test_print_settings(self):
        r = subprocess.run(["node", "--experimental-strip-types", "--no-warnings", "scripts/test-print.mjs"],
                           cwd=RM, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr or r.stdout)


if __name__ == "__main__":
    unittest.main()
