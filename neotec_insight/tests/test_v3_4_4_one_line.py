"""v3.4.4 — Statement: one line per voucher (invoice revenue + VAT lines merged)."""
import shutil
import subprocess
import unittest
from pathlib import Path

RM = Path(__file__).resolve().parents[2] / "reportManager"


@unittest.skipUnless(shutil.which("node"), "node not installed")
class OneLine(unittest.TestCase):
    def test_merge_logic(self):
        r = subprocess.run(["node", "--experimental-strip-types", "--no-warnings", "scripts/test-statement.mjs"],
                           cwd=RM, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr or r.stdout)

    def test_statement_uses_it_by_default(self):
        st = (RM / "src/features/statement/StatementApp.tsx").read_text()
        self.assertIn("oneLine ? mergeByVoucher(b.transactions || [])", st)
        self.assertIn("localStorage.getItem('ni-statement-oneline') !== '0'", st)     # on unless switched off


if __name__ == "__main__":
    unittest.main()
