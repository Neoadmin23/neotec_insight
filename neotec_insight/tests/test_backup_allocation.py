"""v2.88.11 - allocation driver values are part of the configuration backup."""
import datetime, sys, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "neotec_insight" / "utils"))
import config_backup_registry as reg  # noqa: E402


class TestAllocationInBackup(unittest.TestCase):
    def names(self):
        return [e["doctype"] for e in reg.CONFIG_REGISTRY]

    def test_entries_are_registered_not_excluded(self):
        self.assertIn("Insight Allocation Entry", self.names())
        self.assertNotIn("Insight Allocation Entry", reg.EXCLUDED_FROM_CONFIG_BACKUP)

    def test_rule_restores_before_its_entries(self):
        n = self.names()
        self.assertLess(n.index("Insight Allocation Rule"), n.index("Insight Allocation Entry"))

    def test_rule_and_entries_share_one_checkbox(self):
        area = {e["doctype"]: e["area"] for e in reg.CONFIG_REGISTRY}
        self.assertEqual(area["Insight Allocation Rule"], area["Insight Allocation Entry"])

    def test_no_registry_exclusion_conflict(self):
        both = set(self.names()) & set(reg.EXCLUDED_FROM_CONFIG_BACKUP)
        self.assertEqual(both, set())


class TestNaturalKey(unittest.TestCase):
    def test_entry_key(self):
        k = reg.natural_key("Insight Allocation Entry", {
            "name": "abc", "rule": "GMO", "cost_center": "IT", "period_month": datetime.date(2026, 8, 1)})
        self.assertEqual(k, {"rule": "GMO", "cost_center": "IT", "period_month": "2026-08-01"})

    def test_incomplete_record_has_no_key(self):
        self.assertIsNone(reg.natural_key("Insight Allocation Entry", {"rule": "GMO"}))

    def test_other_doctypes_match_by_name(self):
        self.assertIsNone(reg.natural_key("Insight Dashboard", {"name": "x"}))


if __name__ == "__main__":
    unittest.main()
