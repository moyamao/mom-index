import unittest

from scripts.web_server import _apply_latest_model_snapshot


class WebDashboardLatestTest(unittest.TestCase):
    def test_latest_uses_current_model_snapshot_and_history_delta(self):
        dashboard = {"latest": {"date": "2026-08-30"}}
        comparison = {
            "comparison_date": "2026-09-09",
            "profiles": [{"profile": "mini-14b", "completed_at": "2026-09-09 17:57:24"}],
        }
        snapshots = {"mini-14b": [
            {"date": "2026-09-09", "sectors": {"gold": {"index": 9.8}}},
        ]}
        history = {"mini-14b": {"gold": [
            {"date": "2026-09-08", "index": -7.8},
            {"date": "2026-09-09", "index": 9.8},
        ]}}

        _apply_latest_model_snapshot(dashboard, comparison, snapshots, history)

        self.assertEqual(dashboard["latest"]["date"], "2026-09-09")
        self.assertEqual(dashboard["latest"]["profile"], "mini-14b")
        self.assertEqual(dashboard["latest"]["sectors"]["gold"]["index"], 9.8)
        self.assertEqual(dashboard["latest_changes"]["gold"]["delta"], 17.6)


if __name__ == "__main__":
    unittest.main()
