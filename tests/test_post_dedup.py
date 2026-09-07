import unittest
from datetime import datetime

from storage.mysql_store import _upsert_post_catalog, post_content_key


class RecordingCursor:
    def __init__(self):
        self.calls = []

    def executemany(self, sql, rows):
        self.calls.append((sql, rows))


class PostContentKeyTest(unittest.TestCase):
    def test_platform_and_post_id_are_primary_identity(self):
        first = {"platform": "xueqiu", "id": "123", "content": "摘要"}
        enriched = {"platform": "xueqiu", "id": "123", "content": "完整正文"}
        self.assertEqual(post_content_key(first), post_content_key(enriched))

    def test_same_id_on_different_platforms_is_not_duplicate(self):
        xueqiu = {"platform": "xueqiu", "id": "123"}
        xhs = {"platform": "xiaohongshu", "id": "123"}
        self.assertNotEqual(post_content_key(xueqiu), post_content_key(xhs))

    def test_tracking_parameters_and_fragment_do_not_change_url_identity(self):
        plain = {"platform": "xueqiu", "url": "https://xueqiu.com/1/2?a=1"}
        tracked = {
            "platform": "xueqiu",
            "url": "https://xueqiu.com/1/2/?utm_source=test&a=1#comments",
        }
        self.assertEqual(post_content_key(plain), post_content_key(tracked))

    def test_fallback_normalizes_whitespace_and_case(self):
        first = {
            "platform": "WEIBO",
            "author": " Alice ",
            "published_at": "2026-09-07 10:00:00",
            "title": "HBM  看涨",
            "content": "Long   MU",
        }
        second = {
            "platform": "weibo",
            "author": "alice",
            "published_at": "2026-09-07 10:00:00",
            "title": "hbm 看涨",
            "content": "long mu",
        }
        self.assertEqual(post_content_key(first), post_content_key(second))

    def test_catalog_serializes_database_datetime_values(self):
        cursor = RecordingCursor()
        post = {
            "platform": "xueqiu",
            "id": "123",
            "published_at": datetime(2026, 9, 7, 10, 0),
            "collected_at": datetime(2026, 9, 7, 10, 5),
        }

        _upsert_post_catalog(cursor, 1, {"storage": [post]})

        raw_json = cursor.calls[0][1][0][-1]
        self.assertIn('"published_at": "2026-09-07 10:00:00"', raw_json)


if __name__ == "__main__":
    unittest.main()
