import unittest

from analyzer.llm_contract import model_response_format, validate_model_payload


def valid_opinion(**changes):
    payload = {
        "content_type": "opinion", "sentiment_label": "fear", "sentiment_score": -0.6,
        "emotion_tags": ["anxiety"], "emotion_intensity": 0.6,
        "intent": "neutral", "intent_strength": 0.0, "position_status": "trapped",
        "market_outlook": "bullish", "confidence": 0.8, "reasoning": "看好，但套得睡不着。",
    }
    payload.update(changes)
    return payload


def valid_news(**changes):
    return valid_opinion(**{
        "content_type": "news", "sentiment_label": "neutral", "sentiment_score": 0,
        "emotion_tags": [], "emotion_intensity": 0, "intent": "neutral",
        "intent_strength": 0, "position_status": "unknown", "market_outlook": "unknown",
        **changes,
    })


class ModelContractTest(unittest.TestCase):
    def test_opinion_keeps_independent_emotion_and_outlook(self):
        payload = valid_opinion()
        result = validate_model_payload(payload)
        self.assertEqual(result["sentiment_score"], -0.6)
        self.assertEqual(result["market_outlook"], "bullish")
        self.assertEqual(result["investor_maturity"], "unknown")
        self.assertEqual(result["maturity_confidence"], 0)
        self.assertNotIn("investor_maturity", payload)
        result["emotion_tags"].append("regret")
        self.assertEqual(payload["emotion_tags"], ["anxiety"])

    def test_news_and_legacy_maturity_are_valid(self):
        payload = valid_news(investor_maturity="not_applicable", maturity_confidence=0.9)
        self.assertEqual(validate_model_payload(payload), payload)

    def test_empty_or_partial_json_is_not_neutral_success(self):
        for payload in ({}, {"content_type": "opinion"}, {"sentiment_score": 0}):
            with self.subTest(payload=payload), self.assertRaisesRegex(ValueError, "missing required"):
                validate_model_payload(payload)

    def test_non_object_response_is_rejected(self):
        for payload in (None, [], "{}", 0):
            with self.subTest(payload=payload), self.assertRaisesRegex(ValueError, "JSON object"):
                validate_model_payload(payload)

    def test_missing_core_field_names_error(self):
        payload = valid_opinion()
        del payload["market_outlook"]
        with self.assertRaisesRegex(ValueError, "market_outlook"):
            validate_model_payload(payload)

    def test_nonfinite_out_of_range_and_coerced_numbers_are_rejected(self):
        for value in (float("nan"), float("inf"), -float("inf"), 1.01, -1.01, 10**1000, True, "0.5", None):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "sentiment_score"):
                validate_model_payload(valid_opinion(sentiment_score=value))
        for field in ("confidence", "emotion_intensity", "intent_strength", "maturity_confidence"):
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, field):
                validate_model_payload(valid_opinion(**{field: -0.1}))

    def test_invalid_enums_do_not_silently_become_neutral(self):
        for field, value in (("intent", "wait"), ("content_type", "advertisement"),
                             ("market_outlook", "neutral"), ("sentiment_label", "angry"),
                             ("position_status", None), ("investor_maturity", "expert")):
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, field):
                validate_model_payload(valid_opinion(**{field: value}))

    def test_invalid_tags_and_reasoning_are_rejected(self):
        for tags in ("anxiety", ["made_up"], [None], ["anxiety", "anxiety"]):
            with self.subTest(tags=tags), self.assertRaisesRegex(ValueError, "emotion_tags"):
                validate_model_payload(valid_opinion(emotion_tags=tags))
        with self.assertRaisesRegex(ValueError, "reasoning"):
            validate_model_payload(valid_opinion(reasoning=None))
        self.assertEqual(validate_model_payload(valid_opinion(reasoning=""))["reasoning"], "")

    def test_news_cannot_silently_contribute_opinion_values(self):
        for field, value in (("sentiment_score", -0.6), ("sentiment_label", "fear"),
                             ("emotion_tags", ["anxiety"]), ("emotion_intensity", 0.5),
                             ("intent", "buy"), ("intent_strength", 0.3),
                             ("position_status", "holding"), ("market_outlook", "bullish")):
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, field):
                validate_model_payload(valid_news(**{field: value}))

    def test_unknown_fields_rejected_and_schema_calls_are_independent(self):
        with self.assertRaisesRegex(ValueError, "unexpected fields: sentiment"):
            validate_model_payload(valid_opinion(sentiment=0))
        schema = model_response_format()
        schema["json_schema"]["schema"]["required"].clear()
        self.assertIn("sentiment_score", model_response_format()["json_schema"]["schema"]["required"])


if __name__ == "__main__":
    unittest.main()
