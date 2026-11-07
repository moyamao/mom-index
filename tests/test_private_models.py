import os
import unittest
from unittest.mock import patch

from analyzer.llm_consensus import adjudicate
from analyzer.llm_sentiment import analyze_sentiment_with_llm
from analyzer.model_privacy import PrivateEndpointError, cloud_allowed, request_options
from analyzer.test_llm_consensus import value, response
from scripts import adjudicate_stored_results


class PrivateModelTests(unittest.TestCase):
    @patch.dict(os.environ, {"MOM_INDEX_WECHAT_ALLOW_CLOUD": "0"})
    def test_private_requests_cannot_use_remote_endpoint_redirect_or_proxy(self):
        with patch("analyzer.llm_sentiment.requests.post") as post, patch("analyzer.llm_sentiment.llm_ready", return_value=True), \
                patch("analyzer.llm_sentiment._base_url", return_value="https://cloud.example/v1"):
            with self.assertRaises(PrivateEndpointError):
                analyze_sentiment_with_llm(title="这是一条需要分析的私密正文", content="", sector="gold",
                                           platform="wechat", current_result=None)
            post.assert_not_called()
        opts = request_options("wechat", "http://127.0.0.1:8080/v1")
        self.assertFalse(opts["allow_redirects"])
        self.assertEqual(opts["proxies"]["all"], "")
        self.assertTrue(cloud_allowed("weibo"))
        with patch.dict(os.environ, {"MOM_INDEX_WECHAT_ALLOW_CLOUD": "1"}):
            self.assertEqual(request_options("wechat", "https://cloud.example/v1"), {})

    @patch.dict(os.environ, {"MOM_INDEX_WECHAT_ALLOW_CLOUD": "0"})
    def test_both_adjudication_paths_keep_wechat_disputes_local(self):
        stored = dict(id=1, batch_id=1, **value(intent="sell"), sentiment_confidence=1)
        with patch("analyzer.llm_consensus.call_model", return_value=response(value(intent="buy"))) as call:
            _, audit = adjudicate(title="t", content="private", platform="wechat", sector="gold",
                                  configs={"local_27b": {}, "cloud": {}}, timeout=1, stored_14b=stored)
            self.assertEqual(call.call_count, 1)
            self.assertEqual(audit["source"], "private_local_27b")
            self.assertTrue(audit["requires_review"])
        key = "id|wechat|p"
        posts = {"gold": [{"id": "p", "platform": "wechat", "title": "黄金", "content": "private"}]}
        with patch("sys.argv", ["adjudicate"]), patch("storage.mysql_store.fetch_posts_for_analysis", return_value=posts), \
                patch("storage.mysql_store.fetch_reference_analyses", side_effect=[{key: stored}, {}, {key: stored}]), \
                patch("storage.mysql_store.persist_standalone_analysis") as persist, \
                patch("analyzer.llm_consensus.call_model") as cloud, patch("analyzer.llm_consensus.endpoint_config") as config:
            adjudicate_stored_results.main()
            self.assertEqual(persist.call_args.args[0]["gold"][0].adjudication_source, "private_local_14b")
            cloud.assert_not_called()
            config.assert_not_called()
