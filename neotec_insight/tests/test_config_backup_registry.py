"""Tests for utils/config_backup_registry.py — both the pure comparison
logic and a "golden" check that the real registry has no duplicate entries
and that this session's own new doctypes are actually registered, not just
assumed to be from reading the code.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1] / "neotec_insight"
sys.path.insert(0, str(APP_ROOT / "utils"))

import config_backup_registry as reg  # noqa: E402


class TestComputeCoverageIsolated(unittest.TestCase):
    """Uses a small synthetic registry/exclusion set — not the real one —
    so these tests describe the LOGIC, independent of how complete the real
    registry happens to be today."""

    def test_fully_covered_when_every_doctype_is_registered_or_excluded(self):
        result = reg.compute_coverage(
            all_doctypes=["A", "B", "C"],
            registry=[{"doctype": "A", "area": "x"}, {"doctype": "B", "area": "x"}],
            excluded={"C": "real reason"},
        )
        self.assertTrue(result["fully_covered"])
        self.assertEqual(result["unaccounted"], [])

    def test_an_untriaged_doctype_is_flagged(self):
        result = reg.compute_coverage(
            all_doctypes=["A", "B", "NewDoctype"],
            registry=[{"doctype": "A", "area": "x"}],
            excluded={"B": "reason"},
        )
        self.assertFalse(result["fully_covered"])
        self.assertEqual(result["unaccounted"], ["NewDoctype"])

    def test_a_doctype_both_registered_and_excluded_is_a_conflict(self):
        """A doctype can't be simultaneously 'back this up' and 'don't back
        this up, here's why' — that's a real contradiction to catch, not
        something to silently resolve one way or the other."""
        result = reg.compute_coverage(
            all_doctypes=["A"],
            registry=[{"doctype": "A", "area": "x"}],
            excluded={"A": "reason"},
        )
        self.assertFalse(result["fully_covered"])
        self.assertEqual(result["conflicting_registered_and_excluded"], ["A"])

    def test_registered_doctype_no_longer_live_is_reported_not_hidden(self):
        """A doctype that was deleted from the app but never removed from
        the registry — worth surfacing, not silently dropped from view."""
        result = reg.compute_coverage(
            all_doctypes=["A"],
            registry=[{"doctype": "A", "area": "x"}, {"doctype": "Retired Doctype", "area": "y"}],
            excluded={},
        )
        self.assertEqual(result["registered_but_not_live"], ["Retired Doctype"])
        self.assertTrue(result["fully_covered"], "a stale registry entry alone isn't a coverage gap")

    def test_empty_everything_is_trivially_covered(self):
        result = reg.compute_coverage(all_doctypes=[], registry=[], excluded={})
        self.assertTrue(result["fully_covered"])


class TestRealRegistryIntegrity(unittest.TestCase):
    """Checks the actual CONFIG_REGISTRY / EXCLUDED_FROM_CONFIG_BACKUP data
    in this file, not a synthetic stand-in — the "golden" tests."""

    def test_no_doctype_is_registered_twice(self):
        names = [r["doctype"] for r in reg.CONFIG_REGISTRY]
        self.assertEqual(len(names), len(set(names)),
                         "a doctype listed twice in CONFIG_REGISTRY would export it twice")

    def test_no_doctype_is_both_registered_and_excluded(self):
        registered = {r["doctype"] for r in reg.CONFIG_REGISTRY}
        excluded = set(reg.EXCLUDED_FROM_CONFIG_BACKUP.keys())
        self.assertEqual(registered & excluded, set())

    def test_every_exclusion_has_a_real_reason_not_a_placeholder(self):
        for doctype, reason in reg.EXCLUDED_FROM_CONFIG_BACKUP.items():
            self.assertGreater(len(reason), 20, f"{doctype}'s exclusion reason is too short to be real")

    def test_cash_flow_forecast_config_doctypes_are_registered(self):
        """This session's own doctypes — asserted explicitly so a future
        edit to the registry can't silently drop one of these without a
        test noticing, the way the original three-hardcoded-lists design
        let seven pre-existing doctypes go unregistered with nothing to
        catch it."""
        registered = {r["doctype"] for r in reg.CONFIG_REGISTRY}
        for dt in ["Insight Cash Flow Line", "Insight Cash Flow Budget",
                  "Insight Cash Flow Classification Rule", "Insight Cash Flow Settings"]:
            self.assertIn(dt, registered, f"{dt} must be in CONFIG_REGISTRY")

    def test_cash_flow_override_is_explicitly_excluded_not_just_absent(self):
        """The one Cash Flow Forecast doctype that should NOT be backed up
        must be in the excluded dict with a reason — not simply missing
        from the registry, which compute_coverage would (correctly) flag
        as an untriaged gap rather than a deliberate choice."""
        self.assertIn("Insight Cash Flow Override", reg.EXCLUDED_FROM_CONFIG_BACKUP)

    def test_single_doctypes_are_flagged_is_single(self):
        singles = {"Insight AI Settings", "Insight Menu Settings", "Insight Cash Flow Settings"}
        for r in reg.CONFIG_REGISTRY:
            if r["doctype"] in singles:
                self.assertTrue(r.get("is_single"), f"{r['doctype']} should be flagged is_single")


class TestFullCoverageAgainstRealDoctypeFolder(unittest.TestCase):
    """The actual enforcement mechanism this feature exists for: every
    non-child-table doctype in doctype/ must be registered OR explicitly
    excluded with a reason. This is what should fail — loudly, in CI, not
    silently in production three point releases later — the next time
    someone adds a doctype and forgets to triage it, the way seven
    pre-existing doctypes and (nearly) all of Cash Flow Forecast went
    unregistered before this file existed."""

    def _discover_live_doctypes(self) -> list[str]:
        import json as jsonlib
        doctype_dir = APP_ROOT / "doctype"
        names = []
        for folder in sorted(doctype_dir.iterdir()):
            json_path = folder / f"{folder.name}.json"
            if not json_path.exists():
                continue
            data = jsonlib.loads(json_path.read_text())
            if not data.get("istable"):
                names.append(data["name"])
        return names

    def test_every_live_doctype_is_registered_or_explicitly_excluded(self):
        live = self._discover_live_doctypes()
        self.assertGreater(len(live), 0, "sanity check — the doctype folder scan itself must find something")
        result = reg.compute_coverage(live)
        self.assertEqual(
            result["unaccounted"], [],
            f"{len(result['unaccounted'])} doctype(s) are neither in CONFIG_REGISTRY nor "
            f"EXCLUDED_FROM_CONFIG_BACKUP: {result['unaccounted']}. Add each one to exactly "
            f"one of those two, in utils/config_backup_registry.py, with a real reason if excluding."
        )
        self.assertEqual(result["conflicting_registered_and_excluded"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
