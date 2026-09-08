import unittest
from types import SimpleNamespace

from storage.mysql_store import _iter_analysis_rows, _to_analysis_like


class ModelComparisonRowTest(unittest.TestCase):
    def test_pipeline_analysis_row_matches_extended_schema(self):
        item = SimpleNamespace(
            post_id="p1", title="看涨", platform="xueqiu", newbie_score=0,
            newbie_confidence="", level="普通投资者", sentiment_score=0.7,
            sentiment_label="greed", sentiment_confidence=0.9, intent="buy",
            intent_strength=0.8, position_status="holding", market_outlook="bullish",
            content_type="opinion", key_signals=[], reasoning="", matched_newbie=[],
            matched_pro=[], sentiment_source="llm",
        )
        rows = list(_iter_analysis_rows(1, {"storage": [item]}, {"storage": [{"id": "p1"}]}, 2))
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(rows[0]), 26)

    def test_reconstructs_current_llm_fields(self):
        item = _to_analysis_like({
            "title": "继续看涨",
            "sentiment_score": 0.6,
            "sentiment_label": "greed",
            "sentiment_confidence": 0.8,
            "analysis_engine": "llm",
            "position_status": "holding",
            "market_outlook": "bullish",
            "content_type": "opinion",
        })
        self.assertEqual(item.sentiment_source, "llm")
        self.assertEqual(item.sentiment_label, "greed")
        self.assertEqual(item.sentiment_confidence, 0.8)
        self.assertEqual(item.position_status, "holding")
        self.assertEqual(item.market_outlook, "bullish")

    def test_legacy_batch_recovers_direction_and_news_type(self):
        opinion = _to_analysis_like({
            "sentiment_score": -0.4,
            "sentiment_label": "neutral",
            "sentiment_confidence": 0,
            "analysis_engine": "llm",
        })
        news = _to_analysis_like({
            "level": "资讯帖",
            "content_type": "opinion",
            "analysis_engine": "llm",
        })
        self.assertEqual(opinion.sentiment_label, "fear")
        self.assertEqual(opinion.sentiment_confidence, 1.0)
        self.assertEqual(news.content_type, "news")


if __name__ == "__main__":
    unittest.main()
