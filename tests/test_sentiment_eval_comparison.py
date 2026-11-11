import json
from pathlib import Path
import tempfile
import unittest

from scripts.sentiment_eval_comparison import write_paired_comparison


def record(identifier, variant, *, seed=42, repeat=1, status="ok", **changes):
    decision = dict(content_type="opinion", sentiment_score=0, sentiment_label="neutral",
                    emotion_tags=[], intent="neutral", position_status="unknown",
                    market_outlook="unknown", reasoning="无明确情绪")
    decision.update(changes)
    return dict(id=identifier, variant=variant, seed=seed, repeat=repeat, status=status,
                decision=decision if status == "ok" else None, analysis_text="完整测试输入",
                attempts=[] if status == "ok" else [{"error": "请求失败"}], seconds=1)


class SentimentPairedComparisonTests(unittest.TestCase):
    def report(self, rows, samples=None):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            summary = write_paired_comparison(directory, samples or [], rows)
            saved = json.loads((directory / "comparison.json").read_text())
            page = (directory / "comparison.html").read_text()
        return summary, saved["pairs"], page

    def test_same_labels_ignore_tag_order_and_reason_prose(self):
        rows = [record("a", "baseline", emotion_tags=["anxiety", "regret"]),
                record("a", "prompt", emotion_tags=["regret", "anxiety"], reasoning="不同理由")]
        summary, pairs, page = self.report(rows)
        self.assertEqual(summary["same"], 1)
        self.assertEqual(summary["different"], 0)
        self.assertTrue(pairs[0]["reasoning_changed"])
        self.assertIn("不同理由", page)

    def test_all_core_differences_visible_and_large_oppositions_highlighted(self):
        rows = [record("a", "baseline", sentiment_score=-0.2, intent="buy", market_outlook="bullish"),
                record("a", "prompt", sentiment_score=0.1, intent="clear", market_outlook="bearish"),
                record("b", "baseline", content_type="opinion", sentiment_score=0),
                record("b", "prompt", content_type="news", sentiment_score=0)]
        summary, pairs, page = self.report(rows)
        self.assertEqual(summary["different"], 2)
        self.assertEqual(summary["highlighted"], 1)
        self.assertEqual(set(pairs[0]["highlight_fields"]), {"sentiment_score", "intent", "market_outlook"})
        self.assertEqual(pairs[0]["effective_score_gap_points"], 30)
        self.assertEqual(pairs[1]["changed_fields"], ["content_type"])
        self.assertIn("内容类型", page)

    def test_news_effective_score_is_zero_without_hiding_raw_value(self):
        rows = [record("a", "baseline", content_type="news", sentiment_score=-0.8),
                record("a", "prompt", sentiment_score=0),
                record("b", "baseline", content_type="news", sentiment_score=0.9),
                record("b", "prompt", sentiment_score=-0.6)]
        _, pairs, page = self.report(rows)
        self.assertEqual(pairs[0]["effective_score_gap_points"], 0)
        self.assertNotIn("sentiment_score", pairs[0]["changed_fields"])
        self.assertEqual(pairs[1]["effective_score_gap_points"], 60)
        self.assertIn("原始值 -0.8", page)
        self.assertEqual(pairs[0]["baseline"]["decision"]["sentiment_score"], -0.8)

    def test_failures_and_missing_kept_in_denominator(self):
        rows = [record("a", "baseline"), record("a", "prompt", status="failed"),
                record("b", "baseline"), record("c", "prompt", status="failed")]
        summary, pairs, page = self.report(rows)
        self.assertEqual(summary["total_pairs"], 3)
        self.assertEqual(summary["both_valid"], 0)
        self.assertEqual(summary["failed"], 2)
        self.assertEqual(summary["missing"], 2)
        self.assertEqual(summary["same"] + summary["different"] + summary["incomplete"], 3)
        self.assertEqual(len(pairs), 3)
        self.assertIn("请求失败", page)
        self.assertIn("缺少结果", page)

    def test_pairing_never_matches_other_seed_or_repeat(self):
        rows = [record("a", "baseline", seed=42), record("a", "prompt", seed=43),
                record("a", "baseline", seed=44, repeat=1),
                record("a", "prompt", seed=44, repeat=2)]
        summary, pairs, _ = self.report(rows)
        self.assertEqual(summary["total_pairs"], 4)
        self.assertEqual(summary["missing"], 4)
        self.assertEqual(summary["both_valid"], 0)

    def test_untrusted_strings_are_escaped_everywhere(self):
        attack = '<script>alert("x")</script><img src=x onerror=alert(1)>'
        rows = [record(attack, "baseline", reasoning=attack), record(attack, "prompt", status="failed")]
        rows[0]["analysis_text"] = attack
        rows[1]["attempts"] = [{"error": attack}]
        samples = [dict(id=attack, title=attack, content=attack, platform=attack, sector=attack)]
        _, pairs, page = self.report(rows, samples)
        self.assertNotIn("<script", page)
        self.assertNotIn("<img", page)
        self.assertIn("&lt;script&gt;", page)
        self.assertEqual(pairs[0]["source"]["content"], attack)

    def test_only_other_variants_returns_unavailable_without_invented_pairs(self):
        summary, pairs, page = self.report([record("a", "greedy")])
        self.assertFalse(summary["available"])
        self.assertEqual(pairs, [])
        self.assertIn("无法生成", summary["note"])
        self.assertIn("无法生成", page)

    def test_duplicate_pair_does_not_silently_overwrite(self):
        with self.assertRaisesRegex(ValueError, "重复"):
            self.report([record("a", "baseline"), record("a", "baseline")])

    def test_existing_artifact_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            (folder / "comparison.html").write_text("keep")
            with self.assertRaises(FileExistsError):
                write_paired_comparison(folder, [], [record("a", "baseline")])
            self.assertEqual((folder / "comparison.html").read_text(), "keep")
            self.assertFalse((folder / "comparison.json").exists())


if __name__ == "__main__":
    unittest.main()
