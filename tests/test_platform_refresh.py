import unittest
from unittest.mock import patch

from pipeline import _refresh_platforms


class PlatformRefreshTest(unittest.TestCase):
    def test_weibo_refresh_replaces_only_weibo_posts(self):
        existing = {
            "storage": [
                {"id": "x1", "platform": "xueqiu"},
                {"id": "w-old", "platform": "weibo"},
            ]
        }
        incoming = {"storage": [{"id": "w-new", "platform": "weibo"}]}

        with patch("pipeline.collect_weibo", return_value=incoming):
            merged, refreshed = _refresh_platforms(existing, {"weibo"})

        self.assertEqual(refreshed, {"weibo"})
        self.assertEqual([post["id"] for post in merged["storage"]], ["x1", "w-new"])

    def test_empty_refresh_keeps_existing_posts(self):
        existing = {"storage": [{"id": "w-old", "platform": "weibo"}]}

        with patch("pipeline.collect_weibo", return_value={"storage": []}):
            merged, refreshed = _refresh_platforms(existing, {"weibo"})

        self.assertFalse(refreshed)
        self.assertEqual(merged, existing)


if __name__ == "__main__":
    unittest.main()
