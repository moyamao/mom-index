import unittest
import json
import os

from analyzer.llm_sentiment import _normalize_emotion_tags
from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests

from analyzer.llm_sentiment import analyze_sentiment_with_llm, generation_options


def _response(payload=None, finish_reason="stop"):
    response = Mock()
    response.raise_for_status.return_value = None
    if payload is None:
        payload = dict(content_type="opinion", sentiment_label="neutral", sentiment_score=0,
                       emotion_tags=[], emotion_intensity=0, intent="neutral", intent_strength=0,
                       position_status="unknown", market_outlook="unknown", confidence=.8,
                       reasoning="未表达明确情绪")
    response.json.return_value = {"choices": [{"message": {"content": json.dumps(payload)},
                                              "finish_reason": finish_reason}]}
    return response


class LlmRequestTest(unittest.TestCase):
    def test_emotion_tags_are_limited_and_deduplicated(self):
        self.assertEqual(
            _normalize_emotion_tags(["anger", "anxiety", "anger", "unknown"]),
            ["anger", "anxiety"],
        )

    @patch("analyzer.llm_sentiment.llm_ready", return_value=True)
    @patch("analyzer.llm_sentiment.requests.post")
    def test_empty_text_is_terminal_count_only_without_request(self, post, _ready):
        result = analyze_sentiment_with_llm(
            title="", content="  ", sector="gold", platform="xiaohongshu",
            current_result=SimpleNamespace(),
        )
        self.assertEqual(result.source, "insufficient")
        self.assertEqual(result.content_type, "news")
        post.assert_not_called()

    @patch("analyzer.llm_sentiment.llm_ready", return_value=True)
    @patch("analyzer.llm_sentiment.requests.post")
    def test_short_action_and_emotion_are_sent_to_model(self, post, _ready):
        post.return_value = _response()
        for title in ("今天清仓了", "跌麻了", "黄金ETF", "😭😭😭"):
            with self.subTest(title=title):
                before = post.call_count
                result = analyze_sentiment_with_llm(
                    title=title, content="", sector="gold", platform="xueqiu", current_result=None)
                self.assertEqual(post.call_count, before + 1)
                self.assertEqual(result.source, "llm")
    @patch("analyzer.llm_sentiment._max_chars", return_value=1200)
    @patch("analyzer.llm_sentiment._timeout_seconds", return_value=60)
    @patch("analyzer.llm_sentiment.llm_ready", return_value=True)
    @patch("analyzer.llm_sentiment.requests.post")
    def test_request_sends_only_truncated_analysis_text(self, post, _ready, _timeout, _chars):
        post.return_value = _response()
        long_content = "长" * 3000

        analyze_sentiment_with_llm(
            title="标题", content=long_content, sector="storage", platform="weibo",
            current_result=SimpleNamespace(),
        )

        payload = post.call_args.kwargs["json"]
        user_content = payload["messages"][1]["content"]
        self.assertLess(len(user_content), 1400)
        self.assertNotIn(long_content, user_content)
        self.assertEqual(payload["max_tokens"], 384)

    @patch("analyzer.llm_sentiment._max_chars", return_value=1200)
    @patch("analyzer.llm_sentiment._timeout_seconds", return_value=60)
    @patch("analyzer.llm_sentiment.llm_ready", return_value=True)
    @patch("analyzer.llm_sentiment.requests.post")
    def test_retry_preserves_input_and_sampling_parameters(self, post, _ready, _timeout, _chars):
        post.side_effect = [requests.exceptions.ReadTimeout("slow"), _response()]

        result = analyze_sentiment_with_llm(
            title="标题", content="长" * 3000, sector="storage", platform="weibo",
            current_result=SimpleNamespace(),
        )

        self.assertIsNotNone(result)
        self.assertEqual(post.call_count, 2)
        first = post.call_args_list[0].kwargs["json"]["messages"][1]["content"]
        second = post.call_args_list[1].kwargs["json"]["messages"][1]["content"]
        self.assertEqual(second, first)
        self.assertEqual(post.call_args_list[0].kwargs["json"], post.call_args_list[1].kwargs["json"])

    @patch("analyzer.llm_sentiment._max_chars", return_value=1200)
    @patch("analyzer.llm_sentiment._timeout_seconds", return_value=60)
    @patch("analyzer.llm_sentiment.llm_ready", return_value=True)
    @patch("analyzer.llm_sentiment.requests.post")
    def test_invalid_json_retries_twice(self, post, _ready, _timeout, _chars):
        invalid = Mock()
        invalid.raise_for_status.return_value = None
        invalid.json.return_value = {"choices": [{"message": {"content": "not json"}}]}
        post.side_effect = [invalid, invalid, _response()]

        result = analyze_sentiment_with_llm(
            title="标题", content="有效正文内容", sector="storage", platform="weibo",
            current_result=SimpleNamespace(),
        )

        self.assertIsNotNone(result)
        self.assertEqual(post.call_count, 3)

    @patch("analyzer.llm_sentiment.llm_ready", return_value=True)
    @patch("analyzer.llm_sentiment.requests.post")
    def test_empty_json_is_retried_and_fails_instead_of_neutral_success(self, post, _ready):
        post.return_value = _response({})
        with self.assertRaisesRegex(ValueError, "已尝试3次"):
            analyze_sentiment_with_llm(title="我今天已经清仓了", content="", sector="gold",
                                       platform="weibo", current_result=None)
        self.assertEqual(post.call_count, 3)

    @patch("analyzer.llm_sentiment.llm_ready", return_value=True)
    @patch("analyzer.llm_sentiment.requests.post")
    def test_invalid_envelope_is_retried_and_reported(self, post, _ready):
        for envelope in (None, [], {"choices": [None]}, {"choices": [{"message": None}]}):
            with self.subTest(envelope=envelope):
                post.reset_mock()
                post.return_value = _response()
                post.return_value.json.return_value = envelope
                with self.assertRaisesRegex(ValueError, "已尝试3次"):
                    analyze_sentiment_with_llm(title="跌麻了", content="", sector="gold",
                                               platform="weibo", current_result=None)
                self.assertEqual(post.call_count, 3)

    @patch("analyzer.llm_analyzer.analyze_sentiment_with_llm", return_value=None)
    def test_analyzer_sends_original_title_instead_of_display_excerpt(self, analyze):
        from analyzer.llm_analyzer import _apply_llm_second_pass

        original = "这是完整帖子的开头" + "甲" * 200 + "最后决定今天清仓"
        result = SimpleNamespace(title=original[:180], sector="gold", platform="weibo")
        _apply_llm_second_pass(result, {"title": original, "content": ""})
        self.assertEqual(analyze.call_args.kwargs["title"], original)

    @patch("analyzer.llm_sentiment.llm_ready", return_value=True)
    @patch("analyzer.llm_sentiment.requests.post")
    def test_token_truncation_retries_even_if_json_parses(self, post, _ready):
        post.side_effect = [_response(finish_reason="length"), _response()]
        result = analyze_sentiment_with_llm(title="我今天已经清仓了", content="", sector="gold",
                                           platform="weibo", current_result=None)
        self.assertEqual(result.source, "llm")
        self.assertEqual(post.call_count, 2)

    @patch("analyzer.llm_sentiment.ini_get", return_value="")
    def test_explicit_sampler_zero_and_invalid_values(self, _ini):
        with patch.dict(os.environ, {"MOM_INDEX_LLM_TEMPERATURE": "0", "MOM_INDEX_LLM_SEED": "42"}, clear=True):
            self.assertEqual(generation_options(), {"temperature": 0.0, "seed": 42})
        with patch.dict(os.environ, {"MOM_INDEX_LLM_TEMPERATURE": "nan"}, clear=True):
            with self.assertRaisesRegex(ValueError, "temperature"):
                generation_options()


if __name__ == "__main__":
    unittest.main()
