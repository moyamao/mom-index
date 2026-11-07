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
            "published_at": "2026-09-12 00:01:00",
            "collected_at": "2026-09-12 00:05:00",
        })

        with patch.object(pipeline, "beijing_now", return_value=datetime(2026, 9, 12, 0, 5)), \
                patch.object(pipeline, "ini_get_int", side_effect=lambda _s, key, default: {
                    "max_age_days": 7,
                    "daily_min_posts": 20,
                }.get(key, default)):
            filtered, windows = pipeline._filter_recent_posts({"gold": posts})

        self.assertEqual(windows["gold"]["label"], "昨日")
        self.assertEqual(windows["gold"]["daily_count"], 25)
        self.assertEqual(len(filtered["gold"]), 25)
        self.assertIn("today", {post["id"] for post in filtered["gold"]})

    def test_mobile_weibo_date_survives_filter(self):
        from collectors.weibo_collector import _parse_m_weibo_cards
        posts = _parse_m_weibo_cards([{
            "id": "post1", "text": "这是用于测试的微博内容",
            "created_at": "Fri Sep 11 23:05:00 +0800 2026",
        }], "黄金")
        with patch.object(pipeline, "beijing_now", return_value=datetime(2026, 9, 11, 23, 15)):
            filtered, _ = pipeline._filter_recent_posts({"gold": posts})
        self.assertEqual(filtered["gold"][0]["published_at"], "2026-09-11 23:05:00")


if __name__ == "__main__":
    unittest.main()
