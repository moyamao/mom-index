import unittest

from analyzer.index_calculator import compute_sector_index
from analyzer.llm_analyzer import AnalysisResult


class MarketSentimentTest(unittest.TestCase):
    def test_news_is_counted_but_excluded_from_sentiment(self):
        news = AnalysisResult(
            "news-1", "公司公告", "xueqiu", "storage",
            level="资讯帖", content_type="news", sentiment_source="llm",
            sentiment_score=1.0, sentiment_label="greed", sentiment_confidence=1.0,
        )
        opinion = AnalysisResult(
            "opinion-1", "我担心继续下跌", "xiaohongshu", "storage",
            content_type="opinion", sentiment_source="llm",
            sentiment_score=-0.8, sentiment_label="fear", sentiment_confidence=0.9,
            position_status="trapped", market_outlook="bearish",
        )

        result = compute_sector_index([news, opinion])

        self.assertEqual(result["index"], -80.0)
        self.assertEqual(result["details"]["news_posts"], 1)
        self.assertEqual(result["details"]["opinion_posts"], 1)
        self.assertEqual(result["details"]["llm_profile"]["analyzed_posts"], 1)
        self.assertEqual(result["details"]["llm_profile"]["trapped_ratio"], 100.0)

    def test_news_only_has_no_sentiment_data(self):
        news = AnalysisResult(
            "news-1", "行业快讯", "weibo", "cpo",
            level="资讯帖", content_type="news", sentiment_source="llm",
        )

        result = compute_sector_index([news])

        self.assertEqual(result["index"], 0.0)
        self.assertEqual(result["interpretation"], "观点样本不足")
        self.assertFalse(result["details"]["llm_profile"]["has_sentiment_data"])


if __name__ == "__main__":
    unittest.main()
