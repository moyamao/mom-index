import unittest

from analyzer.platform_trends import build_model_sector_history


class ModelSectorHistoryTest(unittest.TestCase):
    def test_superseded_opinion_cannot_downweight_other_models(self):
        base = dict(sector="gold", platform="weibo", post_datetime="2026-09-08",
                    analysis_engine="llm", content_type="opinion", post_id="a")
        rows = [dict(base, analysis_profile="mini-14b", sentiment_score=1),
                dict(base, analysis_profile="mini-14b", post_id="b", sentiment_score=-1),
                dict(base, analysis_profile="macbook-27b", content_type="news", sentiment_score=0),
                dict(base, analysis_profile="macbook-27b", sentiment_score=-1)]
        self.assertEqual(build_model_sector_history(rows)["mini-14b"]["gold"][0]["index"], 0)

    def test_curve_is_the_card_index_not_an_unweighted_average(self):
        from storage.mysql_store import build_model_daily_snapshots
        base = dict(sector="gold", analysis_profile="mini-14b", platform="weibo",
                    content_type="opinion", analysis_engine="llm", post_datetime="2026-10-15")
        rows = [dict(base, post_id="a", sentiment_score=1, sentiment_confidence=.9),
                dict(base, post_id="b", sentiment_score=-1, sentiment_confidence=.1)]
        card = build_model_daily_snapshots(rows)["mini-14b"][0]["sectors"]["gold"]
        point = build_model_sector_history(rows)["mini-14b"]["gold"][0]
        self.assertEqual(card["index"], 80)
        self.assertEqual(point["index"], card["index"])

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
            {"date": "2026-09-08", "index": -4.0, "post_count": 2},
        ])
        self.assertEqual(result["macbook-27b"]["gold"][0]["index"], -40.0)


if __name__ == "__main__":
    unittest.main()
