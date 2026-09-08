import unittest

from analyzer.platform_trends import build_model_sector_history


class ModelSectorHistoryTest(unittest.TestCase):
    def test_deduplicates_posts_and_excludes_news(self):
        base = {
            "sector": "gold",
            "platform": "xueqiu",
            "post_datetime": "2026-09-08 10:00:00",
            "level": "普通投资者",
            "analysis_engine": "llm",
            "content_type": "opinion",
        }
        rows = [
            {**base, "post_id": "1", "analysis_profile": "mini-14b", "sentiment_score": 0.6},
            {**base, "post_id": "1", "analysis_profile": "mini-14b", "sentiment_score": -0.8},
            {**base, "post_id": "2", "analysis_profile": "mini-14b", "sentiment_score": -0.2},
            {**base, "post_id": "3", "analysis_profile": "mini-14b", "sentiment_score": 1, "content_type": "news"},
            {**base, "post_id": "1", "analysis_profile": "macbook-27b", "sentiment_score": -0.4},
        ]

        result = build_model_sector_history(rows)

        self.assertEqual(result["mini-14b"]["gold"], [
            {"date": "2026-09-08", "index": 20.0, "post_count": 2},
        ])
        self.assertEqual(result["macbook-27b"]["gold"][0]["index"], -40.0)


if __name__ == "__main__":
    unittest.main()
