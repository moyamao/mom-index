import unittest

from analyzer.llm_sentiment import _analysis_text, _truncate_text, _system_prompt, QUOTE_ONLY_TEXT


class LlmInputWindowTests(unittest.TestCase):
    def test_long_post_keeps_opening_and_conclusion_within_budget(self):
        text = _truncate_text(title="标题", content="开头" + "中" * 2000 + "最终结论", max_chars=1200)
        self.assertLessEqual(len(text), 1200)
        self.assertTrue(text.startswith("标题 开头"))
        self.assertTrue(text.endswith("最终结论"))
        self.assertIn("[中间省略]", text)

    def test_short_post_is_unchanged(self):
        self.assertEqual(_truncate_text(title="标题", content="结论", max_chars=1200), "标题 结论")

    def test_prompt_separates_emotion_from_outlook(self):
        prompt = _system_prompt(include_reasoning=False)
        self.assertIn("看涨预期与当前焦虑可以并存", prompt)

    def test_experimental_prompt_keeps_short_action_and_outlook_examples(self):
        prompt = _system_prompt(include_reasoning=False, prompt_version="sentiment-v7")
        self.assertIn("存储还没跌完", prompt)
        self.assertIn("情绪neutral/0/[]，预期bearish", prompt)
        self.assertIn("今天清仓了", prompt)
        self.assertNotIn('"investor_maturity"', prompt)

    def test_explicit_baseline_prompt_does_not_inherit_experiment(self):
        prompt = _system_prompt(include_reasoning=True, prompt_version="sentiment-v6")
        self.assertIn('"investor_maturity"', prompt)

    def test_xueqiu_reply_excludes_quoted_history(self):
        text = _analysis_text(
            title="回复@仰望太阳: 需要开港卡吗？ //@仰望太阳:回复@文少满满仓:请问买美股",
            content="买入英伟达NVDA 昨晚完成了2笔操作。清仓了英伟达的仓位。",
            platform="xueqiu", max_chars=1200,
        )
        self.assertIn("需要开港卡吗", text)
        self.assertNotIn("清仓", text)
        self.assertNotIn("请问买美股", text)

    def test_other_posts_keep_regular_content(self):
        text = _analysis_text(title="普通标题", content="我的当前观点", platform="xueqiu", max_chars=1200)
        self.assertIn("我的当前观点", text)

    def test_reply_uses_full_matching_body_instead_of_page_title_excerpt(self):
        text = _analysis_text(title="回复@甲: 我正在考虑... - 雪球",
                              content="回复@甲: 我正在考虑买入，但还未下单。 //@甲: 我已经满仓了",
                              platform="xueqiu", max_chars=1200)
        self.assertEqual(text, "本次回复 我正在考虑买入，但还未下单。")
        self.assertNotIn("满仓", text)
        self.assertNotIn("雪球", text)

    def test_empty_reply_never_falls_back_to_quote(self):
        text = _analysis_text(title="回复@甲: //@甲:我满仓买入很开心",
                              content="回复@甲: //@甲:我满仓买入很开心，后面还会大涨",
                              platform="xueqiu", max_chars=1200)
        self.assertEqual(text, QUOTE_ONLY_TEXT)
        self.assertNotIn("满仓", text)

    def test_unrelated_reply_body_cannot_replace_current_title(self):
        text = _analysis_text(title="回复@甲: 我没有买入 //@甲:市场大涨",
                              content="回复@乙: 我买了很多 //@乙:看涨",
                              platform="xueqiu", max_chars=1200)
        self.assertEqual(text, "本次回复 我没有买入")

    def test_reply_in_body_without_reply_title_is_extracted(self):
        text = _analysis_text(title="查看讨论", content="回复@甲：还没操作 // @甲:赶紧买",
                              platform="xueqiu", max_chars=1200)
        self.assertEqual(text, "查看讨论 本次回复 还没操作")

    def test_same_recipient_with_conflicting_reply_cannot_override_title(self):
        text = _analysis_text(title="回复@甲: 我还没有买入 //@甲:看涨",
                              content="回复@甲: 我已经满仓买入 //@乙:很好",
                              platform="xueqiu", max_chars=1200)
        self.assertEqual(text, "本次回复 我还没有买入")

    def test_non_reply_title_stance_is_preserved(self):
        text = _analysis_text(title="我不同意这种做法", content="回复@甲:我满仓买了 //@甲:赶紧买",
                              platform="xueqiu", max_chars=1200)
        self.assertIn("我不同意这种做法", text)
        self.assertNotIn("赶紧买", text)

    def test_quote_only_body_cannot_hide_title_stance(self):
        text = _analysis_text(title="我不同意这种做法", content="回复@甲: //@甲:我满仓买了",
                              platform="xueqiu", max_chars=1200)
        self.assertEqual(text, "我不同意这种做法")

    def test_full_reply_preserves_head_tail_and_budget(self):
        text = _analysis_text(title="回复@甲: 开头...", content="回复@甲:开头" + "中" * 1400 + "结尾 //@甲:外部引用",
                              platform="xueqiu", max_chars=1200)
        self.assertEqual(len(text), 1200)
        self.assertTrue(text.endswith("结尾"))
        self.assertNotIn("外部引用", text)

    def test_v71_dispatch_does_not_replace_v7_or_v6(self):
        old = _system_prompt(include_reasoning=True, prompt_version="sentiment-v7")
        new = _system_prompt(include_reasoning=True, prompt_version="sentiment-v7.1")
        self.assertNotEqual(old, new)
        self.assertIn(QUOTE_ONLY_TEXT, new)


if __name__ == "__main__":
    unittest.main()
