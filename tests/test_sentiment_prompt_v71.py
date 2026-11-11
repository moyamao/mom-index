"""Prompt contract checks; these do not measure model semantic accuracy."""
import hashlib
import json
import re
import unittest

from analyzer.llm_contract import ENUMS, REQUIRED_FIELDS
from analyzer.sentiment_prompts import sentiment_v7_prompt, sentiment_v71_prompt


class SentimentPromptV71Tests(unittest.TestCase):
    def test_v7_outputs_are_unchanged(self):
        hashes = {
            True: "5710f458195d6a66b0ec30e9ee98bca14d3679a2631de383e0ee551457d8a5da",
            False: "1c146b20f95787209ab05893d243445d08fa1bd4521aca7647e65b31b0abdd60",
        }
        for reasoning, expected in hashes.items():
            with self.subTest(reasoning=reasoning):
                prompt = sentiment_v7_prompt(include_reasoning=reasoning)
                self.assertEqual(hashlib.sha256(prompt.encode("utf-8")).hexdigest(), expected)

    def test_v71_keeps_existing_core_fields_and_enum_values(self):
        prompt = sentiment_v71_prompt(include_reasoning=True)
        declaration = re.search(r"必需字段：(\{.*?\})。", prompt).group(1)
        fields = json.loads(declaration)
        self.assertEqual(set(fields), set(REQUIRED_FIELDS))
        for field, choices in ENUMS.items():
            if field in fields:
                with self.subTest(field=field):
                    self.assertEqual(set(fields[field].split("|")), set(choices))
        self.assertEqual(fields["reasoning"], "")
        self.assertEqual(fields["emotion_tags"], [])
        self.assertNotIn("investor_maturity", declaration)
        self.assertLess(len(prompt), 2400)

    def test_relevance_author_and_negation_rules_are_present(self):
        prompt = sentiment_v71_prompt(include_reasoning=True)
        for rule in (
            "关键词命中不证明投资相关", "即使其中有强烈生活情绪", "【仅转发/引用，无作者新增表态】",
            "转发不等于认同", "否定范围内的情绪不命中标签", "被否定的遗憾不能填regret",
            "不能漏掉作者明确表达", "不能忽略标题或短句", "只输出一个JSON对象",
        ):
            with self.subTest(rule=rule):
                self.assertIn(rule, prompt)

    def test_separate_emotion_actions_positions_and_outlook_rules_are_present(self):
        prompt = sentiment_v71_prompt(include_reasoning=True)
        for rule in (
            "都不能单独证明乐观", "希望上涨只是愿望", "经营优势不自动代表未来价格上涨",
            "想买但犹豫仍buy", "明确条件交易计划保留动作", "条件未发生不等于已执行",
            "不从愿望、计划、建议或方向推断", "不得拿某一标的清仓代表全部退出",
            "方向/仓位用unknown，动作neutral", "sentiment_score范围[-1,1]",
        ):
            with self.subTest(rule=rule):
                self.assertIn(rule, prompt)

    def test_reasoning_switch_and_no_embedded_case_examples(self):
        with_reason = sentiment_v71_prompt(include_reasoning=True)
        without_reason = sentiment_v71_prompt(include_reasoning=False)
        self.assertIn("reasoning用一句话说明作者自身证据", with_reason)
        self.assertIn("reasoning输出空字符串", without_reason)
        self.assertNotIn("reasoning用一句话", without_reason)
        self.assertNotIn("例：", with_reason)


if __name__ == "__main__":
    unittest.main()
