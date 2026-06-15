import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DashboardConfigTest(unittest.TestCase):
    def test_dashboard_uses_repo_relative_live_outputs(self):
        source = (ROOT / "v4_dashboard.py").read_text(encoding="utf-8")

        self.assertNotIn("/CMLM V4.0", source)
        self.assertNotIn("v4_volume_surge.csv", source)
        self.assertIn("v4_rrg_data.csv", source)
        self.assertIn("v4_surge_trend.csv", source)


if __name__ == "__main__":
    unittest.main()
