from datetime import datetime, timedelta
import random
import unittest
from unittest.mock import MagicMock, patch

from scripts.sentiment_mysql_sample import sample_mysql_posts


NOW = datetime(2026, 11, 11, 12)


def post(number, **changes):
    row = {
        "content_key": f"post-{number:04d}", "source_post_id": str(number),
        "platform": "xueqiu", "title": f"第{number}条测试观点", "content": "",
        "published_at": NOW - timedelta(days=1), "url": "https://example.test/post",
        "sector": "storage",
    }
    row.update(changes)
    return row


class MysqlSampleTests(unittest.TestCase):
    def invoke(self, rows, **options):
        conn = MagicMock()
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchall.return_value = rows
        with patch("scripts.sentiment_mysql_sample._connect", return_value=conn), \
                patch("scripts.sentiment_mysql_sample.beijing_now", return_value=NOW):
            result = sample_mysql_posts(**options)
        return result, conn, cur

    def test_query_is_read_only_and_covers_full_window(self):
        (samples, meta), conn, cur = self.invoke([post(i) for i in range(140)], count=100)
        self.assertEqual(len(samples), 100)
        statements = [call.args[0].strip() for call in cur.execute.call_args_list]
        self.assertEqual(statements[0], "START TRANSACTION READ ONLY")
        query = statements[1]
        self.assertTrue(query.startswith("SELECT"))
        self.assertNotIn("LIMIT", query)
        self.assertNotIn("mom_index_analysis", query)
        self.assertNotIn("raw_json", query)
        self.assertIn("SELECT MAX(s2.id)", query)
        self.assertEqual(cur.execute.call_args.args[1][-2:], (NOW - timedelta(days=30), NOW))
        conn.commit.assert_not_called()
        conn.close.assert_called_once()
        self.assertEqual(meta["eligible_unique_pool_count"], 140)
        self.assertTrue(all(row["gold"] == {} and row["split"] == "random" for row in samples))

    def test_deterministic_random_selection_is_not_newest_limit(self):
        rows = [post(i, published_at=NOW - timedelta(minutes=i)) for i in range(180)]
        first, _, _ = self.invoke(rows, count=100, seed=12)
        random.Random(53).shuffle(rows)
        second, _, _ = self.invoke(rows, count=100, seed=12)
        self.assertEqual(first, second)
        ids = {row["source_post_id"] for row in first[0]}
        self.assertTrue(any(int(identifier) > 100 for identifier in ids))
        self.assertEqual(len(ids), 100)

    def test_deduplicates_identity_and_cross_platform_text(self):
        rows = [post(1), post(1, sector="gold"), post(2, title="复制观点", platform="weibo"),
                post(3, title="复制观点", platform="xiaohongshu"), post(4)]
        (samples, meta), _, _ = self.invoke(rows, count=3)
        self.assertEqual(len({row["id"] for row in samples}), 3)
        self.assertEqual(meta["excluded_counts"]["duplicate_identity"], 1)
        self.assertEqual(meta["excluded_counts"]["duplicate_full_text"], 1)

    def test_same_actual_truncated_input_only_sampled_once(self):
        rows = [post(1, title="共同标题", content="头" * 80 + "甲" * 500 + "尾" * 80),
                post(2, title="共同标题", content="头" * 80 + "乙" * 500 + "尾" * 80), post(3)]
        (samples, meta), _, _ = self.invoke(rows, count=2, max_chars=80)
        self.assertEqual(meta["excluded_counts"]["duplicate_analysis_text"], 1)
        self.assertTrue(all(len(row["analysis_text"]) <= 80 for row in samples))

    def test_short_text_allowed_and_examples_only_excluded_exactly(self):
        rows = [post(1, title="怕了"), post(2, title="今天清仓了"),
                post(3, title="今天清仓了但我还有别的打算"), post(4, title="   "),
                post(5, title="回复@张三:存储还没跌完 //@张三:旧内容")]
        (samples, meta), _, _ = self.invoke(rows, count=2)
        self.assertEqual({row["title"] for row in samples}, {"怕了", "今天清仓了但我还有别的打算"})
        self.assertEqual(meta["excluded_counts"]["prompt_example"], 2)
        self.assertEqual(meta["excluded_counts"]["empty_text"], 1)

    def test_private_future_and_undated_rows_are_excluded(self):
        rows = [post(1), post(2, platform="wechat"), post(3, published_at=NOW + timedelta(seconds=1)),
                post(4, published_at=None), post(5, published_at=NOW - timedelta(days=31))]
        (samples, meta), _, _ = self.invoke(rows, count=1)
        self.assertEqual(samples[0]["source_post_id"], "1")
        self.assertEqual(meta["excluded_counts"], {
            "missing_date": 1, "non_public_platform": 1, "outside_date_window": 2,
        })

    def test_insufficient_pool_fails_without_silent_shrink(self):
        with self.assertRaisesRegex(ValueError, "不足请求的100条"):
            self.invoke([post(1)], count=100)

    def test_sql_error_closes_connection_and_sanitizes_details(self):
        conn = MagicMock()
        cur = conn.cursor.return_value.__enter__.return_value
        cur.execute.side_effect = [None, RuntimeError("password=secret host=private-host")]
        with patch("scripts.sentiment_mysql_sample._connect", return_value=conn):
            with self.assertRaises(RuntimeError) as raised:
                sample_mysql_posts()
        self.assertNotIn("secret", str(raised.exception))
        self.assertNotIn("private-host", str(raised.exception))
        conn.close.assert_called_once()

    def test_connection_error_is_sanitized(self):
        with patch("scripts.sentiment_mysql_sample._connect", side_effect=OSError("user=private")):
            with self.assertRaisesRegex(RuntimeError, "MySQL只读抽样失败") as raised:
                sample_mysql_posts()
        self.assertNotIn("user=private", str(raised.exception))

    def test_close_error_is_sanitized(self):
        conn = MagicMock()
        conn.close.side_effect = OSError("user=private")
        with patch("scripts.sentiment_mysql_sample._connect", return_value=conn):
            with self.assertRaisesRegex(RuntimeError, "连接关闭失败") as raised:
                sample_mysql_posts()
        self.assertNotIn("user=private", str(raised.exception))
        conn.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
