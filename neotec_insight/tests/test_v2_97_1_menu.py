"""v2.97.1 — the old Briefing / Dashboard / Analysis sections fold into
Performance (the logic is TypeScript; this runs its Node test)."""
import shutil
import subprocess
import unittest
from pathlib import Path

RM = Path(__file__).resolve().parents[2] / "reportManager"


@unittest.skipUnless(shutil.which("node"), "node not installed")
class TestMenuFold(unittest.TestCase):
    def test_fold_cases(self):
        r = subprocess.run(["node", "--experimental-strip-types", "--no-warnings", "scripts/test-menu-fold.mjs"],
                           cwd=RM, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr or r.stdout)
        self.assertIn("6 cases passed", r.stdout)

    def test_app_uses_the_tested_module(self):
        app = (RM / "src" / "App.tsx").read_text()
        self.assertIn("from './utils/menuFold'", app)
        self.assertNotIn("const LEGACY_PERF", app)
