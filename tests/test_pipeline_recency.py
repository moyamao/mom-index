import unittest
from datetime import datetime
from unittest.mock import patch

import pipeline


class PipelineRecencyTest(unittest.TestCase):
    def test_midnight_run_uses_previous_beijing_day(self):
        posts = [
            {
                "id": str(hour),
                "platform": "雪球",
                "published_at": f"2026-09-11 {hour:02d}:00:00",
                "collected_at": "2026-09-12 00:00:00",
            }
            for hour in range(24)
        ]
        posts.append({
            "id": "today",
            "platform": "雪球",
            "published_at": "2026-09-12 00:00:00",
            "collected_at": "2026-09-12 00:00:00",
        })

        with patch.object(pipeline, "beijing_now", return_value=datetime(2026, 9, 12, 0, 0, 0)), \
                patch.object(pipeline, "ini_get_int", side_effect=lambda _s, key, default: {
                    "max_age_days": 7,
                    "daily_min_posts": 20,
                }.get(key, default)):
            filtered, windows = pipeline._filter_recent_posts({"gold": posts})

        self.assertEqual(windows["gold"]["label"], "昨日")
        self.assertEqual(windows["gold"]["daily_count"], 24)
        self.assertEqual(len(filtered["gold"]), 24)
        self.assertNotIn("today", {post["id"] for post in filtered["gold"]})


if __name__ == "__main__":
    unittest.main()
