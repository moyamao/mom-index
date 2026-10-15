import unittest

from analyzer.llm_sentiment import _analysis_text, _truncate_text, _system_prompt


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


if __name__ == "__main__":
    unittest.main()
