"""Validate model responses before they can become successful analyses.

This contract applies to newly generated responses, not historical database rows.
An empty/malformed response must never turn into a successful neutral judgement.
"""
from __future__ import annotations

import copy
import math
from typing import Any


ENUMS = {
    "content_type": ("opinion", "news"),
    "sentiment_label": ("fear", "greed", "neutral", "mixed"),
    "intent": ("buy", "add", "hold", "reduce", "sell", "clear", "neutral"),
    "position_status": ("none", "holding", "trapped", "exited", "unknown"),
    "market_outlook": ("bullish", "bearish", "sideways", "unknown"),
    "investor_maturity": ("novice", "general", "professional", "not_applicable", "unknown"),
}
EMOTION_TAGS = (
    "anxiety", "anger", "excitement", "optimism", "pessimism",
    "disappointment", "regret", "sarcasm", "calm",
)
NUMBER_BOUNDS = {
    "sentiment_score": (-1, 1),
    "emotion_intensity": (0, 1),
    "intent_strength": (0, 1),
    "confidence": (0, 1),
    "maturity_confidence": (0, 1),
}
REQUIRED_FIELDS = (
    "content_type", "sentiment_label", "sentiment_score", "emotion_tags",
    "emotion_intensity", "intent", "intent_strength", "position_status",
    "market_outlook", "confidence", "reasoning",
)
OPTIONAL_DEFAULTS = {"investor_maturity": "unknown", "maturity_confidence": 0.0}


def validate_model_payload(payload: Any) -> dict[str, Any]:
    """Return a validated copy, or raise ValueError naming the invalid fields.

    Only optional maturity metadata receives defaults. Core predictions must be
    explicit, including genuine zero scores and unknown positions/outlooks.
    """
    if not isinstance(payload, dict):
        raise ValueError("model response must be a JSON object")
    missing = [field for field in REQUIRED_FIELDS if field not in payload]
    if missing:
        raise ValueError("missing required fields: " + ", ".join(missing))
    extra = set(payload) - set(REQUIRED_FIELDS) - set(OPTIONAL_DEFAULTS)
    if extra:
        raise ValueError("unexpected fields: " + ", ".join(sorted(map(str, extra))))

    result = copy.deepcopy(payload)
    for field, default in OPTIONAL_DEFAULTS.items():
        result.setdefault(field, default)
    for field, choices in ENUMS.items():
        value = result[field]
        if not isinstance(value, str) or value not in choices:
            raise ValueError(f"invalid {field}: expected one of {', '.join(choices)}")
    for field, (low, high) in NUMBER_BOUNDS.items():
        value = result[field]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"invalid {field}: expected a number")
        if not low <= value <= high or not math.isfinite(value):
            raise ValueError(f"invalid {field}: expected a finite number in [{low}, {high}]")

    tags = result["emotion_tags"]
    if not isinstance(tags, list) or any(
        not isinstance(tag, str) or tag not in EMOTION_TAGS for tag in tags
    ):
        raise ValueError("invalid emotion_tags: expected an array of allowed emotion names")
    if len(tags) != len(set(tags)):
        raise ValueError("invalid emotion_tags: duplicate emotion names")
    if not isinstance(result["reasoning"], str):
        raise ValueError("invalid reasoning: expected a string")

    # Non-participating news cannot contribute emotion or an author's actions.
    # Enforce this here rather than depending on server-specific JSON grammar
    # support for conditional schemas.
    if result["content_type"] == "news":
        expected_news = {
            "sentiment_label": "neutral", "sentiment_score": 0,
            "emotion_tags": [], "emotion_intensity": 0, "intent": "neutral",
            "intent_strength": 0, "position_status": "unknown", "market_outlook": "unknown",
        }
        conflicts = [field for field, expected in expected_news.items() if result[field] != expected]
        if conflicts:
            raise ValueError("news has participating values in: " + ", ".join(conflicts))
    return result


def model_response_format() -> dict[str, Any]:
    """JSON schema for a llama.cpp/OpenAI-compatible structured response.

    Do not advertise OpenAI strict mode: the two maturity fields are optional.
    Client validation remains mandatory for ranges and cross-field consistency.
    """
    properties = {
        field: {"type": "string", "enum": list(choices)}
        for field, choices in ENUMS.items()
    }
    properties.update({
        field: {"type": "number", "minimum": low, "maximum": high}
        for field, (low, high) in NUMBER_BOUNDS.items()
    })
    properties["emotion_tags"] = {
        "type": "array", "items": {"type": "string", "enum": list(EMOTION_TAGS)},
        "uniqueItems": True,
    }
    properties["reasoning"] = {"type": "string"}
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "sentiment",
            "schema": {
                "type": "object", "properties": properties,
                "required": list(REQUIRED_FIELDS), "additionalProperties": False,
            },
        },
    }
