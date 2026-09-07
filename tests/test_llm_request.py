import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests

from analyzer.llm_sentiment import analyze_sentiment_with_llm


def _response():
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "choices": [{"message": {"content": "{\"content_type\":\"opinion\",\"sentiment_label\":\"neutral\"}"}}]
    }
    return response


class LlmRequestTest(unittest.TestCase):
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
        self.assertEqual(payload["max_tokens"], 256)

    @patch("analyzer.llm_sentiment._max_chars", return_value=1200)
    @patch("analyzer.llm_sentiment._timeout_seconds", return_value=60)
    @patch("analyzer.llm_sentiment.llm_ready", return_value=True)
    @patch("analyzer.llm_sentiment.requests.post")
    def test_long_request_retries_with_shorter_text(self, post, _ready, _timeout, _chars):
        post.side_effect = [requests.exceptions.ReadTimeout("slow"), _response()]

        result = analyze_sentiment_with_llm(
            title="标题", content="长" * 3000, sector="storage", platform="weibo",
            current_result=SimpleNamespace(),
        )

        self.assertIsNotNone(result)
        self.assertEqual(post.call_count, 2)
        first = post.call_args_list[0].kwargs["json"]["messages"][1]["content"]
        second = post.call_args_list[1].kwargs["json"]["messages"][1]["content"]
        self.assertLess(len(second), len(first))


if __name__ == "__main__":
    unittest.main()
