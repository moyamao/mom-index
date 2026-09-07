import unittest

from scripts.web_server import _parse_keyword_update


class KeywordAdminPayloadTest(unittest.TestCase):
    def test_valid_payload_is_normalized(self):
        self.assertEqual(
            _parse_keyword_update({"sector": " gold ", "keyword": " 黄金ETF ", "enabled": False}),
            ("gold", "黄金ETF", False),
        )

    def test_enabled_must_be_boolean(self):
        with self.assertRaisesRegex(ValueError, "布尔值"):
            _parse_keyword_update({"sector": "gold", "keyword": "黄金ETF", "enabled": "false"})

    def test_keyword_is_required(self):
        with self.assertRaisesRegex(ValueError, "关键词不能为空"):
            _parse_keyword_update({"sector": "gold", "keyword": " ", "enabled": True})


if __name__ == "__main__":
    unittest.main()
