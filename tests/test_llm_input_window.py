import unittest

from analyzer.llm_sentiment import _truncate_text, _system_prompt


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


if __name__ == "__main__":
    unittest.main()
