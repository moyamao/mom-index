import unittest
from unittest.mock import MagicMock, patch

from storage.mysql_store import fetch_posts_for_analysis


class IncrementalAnalysisQueryTest(unittest.TestCase):
    def test_profile_and_prompt_enable_missing_analysis_filter(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchall.return_value = []

        with patch("storage.mysql_store._connect", return_value=connection), \
                patch("storage.mysql_store._ensure_tables"):
            result = fetch_posts_for_analysis(
                days=14,
                limit=5000,
                analysis_profile="macbook-27b",
                prompt_version="sentiment-v4",
            )

        sql, params = cursor.execute.call_args.args
        self.assertIn("NOT EXISTS", sql)
        self.assertIn("a.analysis_engine = 'llm'", sql)
        self.assertEqual(params, (14, "macbook-27b", "sentiment-v4", 5000))
        self.assertEqual(result, {})
        connection.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
