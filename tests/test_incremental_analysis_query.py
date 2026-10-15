import unittest
from datetime import date
from unittest.mock import MagicMock, patch

from storage.mysql_store import fetch_posts_for_analysis


class IncrementalAnalysisQueryTest(unittest.TestCase):
    def test_profile_enables_strict_missing_analysis_filter(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchall.return_value = []

        with patch("storage.mysql_store._connect", return_value=connection), \
                patch("storage.mysql_store._ensure_tables"):
            result = fetch_posts_for_analysis(
                days=14,
                limit=5000,
                analysis_profile="macbook-27b",
            )

        sql, params = cursor.execute.call_args.args
        self.assertIn("NOT EXISTS", sql)
        self.assertIn("a.analysis_engine IN ('llm', 'insufficient', 'llm_failed')", sql)
        self.assertNotIn("a.prompt_version", sql)
        self.assertNotIn("a.sector = s.sector", sql)
        self.assertIn("GROUP BY content_key", sql)
        self.assertNotIn("GROUP BY content_key, sector", sql)
        self.assertEqual(params, (14, "macbook-27b", 5000))
        self.assertEqual(result, {})
        connection.close.assert_called_once()

    def test_exact_source_date_replaces_rolling_window(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchall.return_value = []

        with patch("storage.mysql_store._connect", return_value=connection), \
                patch("storage.mysql_store._ensure_tables"):
            fetch_posts_for_analysis(
                days=99,
                limit=1000,
                target_date=date(2026, 10, 14),
            )

        sql, params = cursor.execute.call_args.args
        self.assertNotIn("DATE_SUB", sql)
        self.assertIn("c.post_datetime >= %s AND c.post_datetime < %s", sql)
        self.assertEqual(params, (date(2026, 10, 14), date(2026, 10, 15), 1000))


if __name__ == "__main__":
    unittest.main()
