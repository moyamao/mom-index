import hashlib
import unittest
from unittest.mock import MagicMock, patch

from storage.mysql_store import filter_posts_pending_analysis


class StrictModelDedupTest(unittest.TestCase):
    def test_skips_analyzed_and_cross_sector_duplicates(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchall.return_value = [{
            "platform": "xueqiu",
            "post_id": "already",
            "content_hash": "",
        }]
        repeated = {
            "platform": "xueqiu",
            "id": "new",
            "title": "same post",
            "content": "same content",
        }
        posts = {
            "gold": [
                {"platform": "xueqiu", "id": "already", "title": "old"},
                repeated,
            ],
            "storage": [dict(repeated)],
        }

        with patch("storage.mysql_store._connect", return_value=connection), \
                patch("storage.mysql_store._ensure_tables"):
            filtered, stats = filter_posts_pending_analysis(posts, "mini-14b")

        self.assertEqual([post["id"] for post in filtered["gold"]], ["new"])
        self.assertEqual(filtered["storage"], [])
        self.assertEqual(stats["already_analyzed"], 1)
        self.assertEqual(stats["duplicates"], 1)
        self.assertEqual(stats["pending"], 1)
        self.assertEqual(cursor.execute.call_args.args[1], ("mini-14b",))

    def test_missing_post_id_uses_content_hash(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        digest = hashlib.sha256("title\nbody".encode("utf-8")).hexdigest()
        cursor.fetchall.return_value = [{
            "platform": "weibo",
            "post_id": "",
            "content_hash": digest,
        }]

        with patch("storage.mysql_store._connect", return_value=connection), \
                patch("storage.mysql_store._ensure_tables"):
            filtered, stats = filter_posts_pending_analysis({
                "semi": [{"platform": "weibo", "title": "title", "content": "body"}],
            }, "mini-14b")

        self.assertEqual(filtered["semi"], [])
        self.assertEqual(stats["already_analyzed"], 1)


if __name__ == "__main__":
    unittest.main()
