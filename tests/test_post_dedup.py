import unittest

from storage.mysql_store import post_content_key


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


if __name__ == "__main__":
    unittest.main()
