"""v2.88.9 / v2.89.0 - a replace-restore only clears doctypes the bundle carries."""
import sys, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "neotec_insight" / "utils"))
from config_backup_registry import doctypes_to_clear  # noqa: E402


class TestReplaceScope(unittest.TestCase):
    def test_only_bundle_doctypes(self):
        order = ["Insight Dashboard", "Insight Allocation Rule", "Insight GTPL Rule"]
        self.assertEqual(doctypes_to_clear(order, {"Insight Dashboard": [], "Insight GTPL Rule": [1]}),
                         ["Insight Dashboard", "Insight GTPL Rule"])

    def test_allocation_rules_untouched_when_absent(self):
        order = ["Insight Dashboard", "Insight Allocation Rule"]
        self.assertNotIn("Insight Allocation Rule", doctypes_to_clear(order, {"Insight Dashboard": []}))

    def test_empty_bundle_clears_nothing(self):
        self.assertEqual(doctypes_to_clear(["A"], {}), [])
        self.assertEqual(doctypes_to_clear(["A"], None), [])


if __name__ == "__main__":
    unittest.main()
