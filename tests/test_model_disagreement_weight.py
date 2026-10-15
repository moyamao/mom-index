import unittest
from datetime import datetime

from analyzer.index_calculator import compute_sector_index
from storage.mysql_store import _disagreement_keys, _weighted_analysis


def row(profile, post_id, score, *, content_type="opinion"):
    return {
        "analysis_profile": profile, "sector": "gold", "platform": "weibo",
        "post_id": post_id, "post_datetime": datetime(2026, 10, 3, 10),
        "analysis_engine": "llm", "content_type": content_type,
        "sentiment_score": score, "sentiment_confidence": 1,
        "level": "普通投资者",
    }


class ModelDisagreementWeightTest(unittest.TestCase):
    def test_only_shared_opinion_disagreement_is_downweighted(self):
        mini = [row("mini", "a", 0), row("mini", "b", 1), row("mini", "c", -1, content_type="news")]
        big = [row("big", "a", 0.8), row("big", "c", 1, content_type="news")]
        disagreements = _disagreement_keys({"mini": mini, "big": big})
        self.assertEqual(len(disagreements), 1)
        self.assertEqual(_weighted_analysis(mini[0], disagreements).disagreement_weight, 0.25)
        self.assertEqual(_weighted_analysis(mini[1], disagreements).disagreement_weight, 1)
        result = compute_sector_index([_weighted_analysis(item, disagreements) for item in mini])
        self.assertEqual(result["index"], 80.0)
        self.assertEqual(result["details"]["llm_profile"]["disagreement_posts"], 1)
        self.assertEqual(result["details"]["news_posts"], 1)

    def test_single_model_is_unchanged(self):
        self.assertFalse(_disagreement_keys({"mini": [row("mini", "a", 1)]}))

    def test_production_verdict_keeps_full_weight(self):
        final = row("local-consensus-cloud", "a", 0.8)
        key = (final["post_datetime"].date(), ("gold", "weibo", "a"))
        self.assertEqual(_weighted_analysis(final, {key}).disagreement_weight, 1)


if __name__ == "__main__":
    unittest.main()
