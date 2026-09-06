from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any, Optional

import requests

from runtime_config import ini_get, ini_get_bool, ini_get_float, ini_get_int, ini_get_optional


DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-4.1-mini"


@dataclass
class LlmSentimentDecision:
    sentiment_score: float
    sentiment_label: str
    emotion_intensity: float
    intent: str
    intent_strength: float
    position_status: str
    market_outlook: str
    confidence: float
    reasoning: str
    source: str = "llm"


def llm_sentiment_enabled() -> bool:
    if os.environ.get("MOM_INDEX_LLM_ENABLED", "").strip():
        return os.environ["MOM_INDEX_LLM_ENABLED"].strip().lower() in {"1", "true", "yes", "on"}
    return ini_get_bool("llm", "enabled", False)


def _api_key() -> str:
    return (
        os.environ.get("MOM_INDEX_LLM_API_KEY", "").strip()
        or (ini_get_optional("llm", "api_key") or "").strip()
    )


def _base_url() -> str:
    # QWEN_BASE_URL is the server root (for example http://host:8080), while
    # MOM_INDEX_LLM_BASE_URL keeps supporting an OpenAI-style /v1 base URL.
    qwen_base_url = os.environ.get("QWEN_BASE_URL", "").strip()
    if qwen_base_url:
        return f"{qwen_base_url.rstrip('/')}/v1"
    value = (
        os.environ.get("MOM_INDEX_LLM_BASE_URL", "").strip()
        or (ini_get_optional("llm", "base_url") or "").strip()
        or DEFAULT_BASE_URL
    ).rstrip("/")
    if not re.match(r"^https?://", value, flags=re.I):
        value = f"https://{value}"
    return value


def _model() -> str:
    return (
        os.environ.get("MOM_INDEX_LLM_MODEL", "").strip()
        or (ini_get_optional("llm", "model") or "").strip()
        or DEFAULT_MODEL
    )


def llm_model_name() -> str:
    """Return the exact configured model name for persistence and auditing."""
    return _model()


def llm_profile() -> str:
    """Stable result namespace, for example mini-14b or macbook-27b."""
    return (
        os.environ.get("MOM_INDEX_LLM_PROFILE", "").strip()
        or ini_get("llm", "profile", "default").strip()
        or "default"
    )


def llm_prompt_version() -> str:
    return (
        os.environ.get("MOM_INDEX_LLM_PROMPT_VERSION", "").strip()
        or ini_get("llm", "prompt_version", "sentiment-v2").strip()
        or "sentiment-v2"
    )


def _timeout_seconds() -> float:
    return float(os.environ.get("MOM_INDEX_LLM_TIMEOUT_SECONDS", "") or ini_get_float("llm", "timeout_seconds", 20.0))


def _max_chars() -> int:
    return int(os.environ.get("MOM_INDEX_LLM_MAX_CHARS", "") or ini_get_int("llm", "max_chars", 1200))


def llm_max_posts_per_run() -> int:
    raw = os.environ.get("MOM_INDEX_LLM_MAX_POSTS_PER_RUN", "")
    if raw:
        try:
            return max(0, int(raw))
        except ValueError:
            pass
    return max(0, ini_get_int("llm", "max_posts_per_run", 60))


def llm_request_interval_seconds() -> float:
    raw = os.environ.get("MOM_INDEX_LLM_REQUEST_INTERVAL_SECONDS", "")
    if raw:
        try:
            return max(0.0, float(raw))
        except ValueError:
            pass
    return max(0.0, ini_get_float("llm", "request_interval_seconds", 0.25))


def _mode() -> str:
    return (
        os.environ.get("MOM_INDEX_LLM_MODE", "").strip().lower()
        or ini_get("llm", "mode", "uncertain").strip().lower()
        or "uncertain"
    )


def llm_mode() -> str:
    return _mode()


def _request_reasoning() -> bool:
    if os.environ.get("MOM_INDEX_LLM_INCLUDE_REASONING", "").strip():
        return os.environ["MOM_INDEX_LLM_INCLUDE_REASONING"].strip().lower() in {"1", "true", "yes", "on"}
    return ini_get_bool("llm", "include_reasoning", True)


def llm_ready() -> bool:
    # llama-server does not require an API key by default.
    return llm_sentiment_enabled() and bool(_model())


def should_run_llm_second_pass(result: Any) -> bool:
    mode = _mode()
    if mode == "off":
        return False
    if mode == "all":
        return True
    if getattr(result, "level", "") in {"垃圾帖", "资讯帖"}:
        return False

    sentiment_score = abs(float(getattr(result, "sentiment_score", 0.0) or 0.0))
    intent = getattr(result, "intent", "neutral") or "neutral"
    newbie_conf = getattr(result, "newbie_confidence", "low") or "low"
    title = getattr(result, "title", "") or ""

    # 默认 uncertain：优先把规则不确定、情绪模糊、意图不清的帖子交给 LLM
    if newbie_conf == "low":
        return True
    if sentiment_score <= 0.34:
        return True
    if intent == "neutral":
        return True
    if len(title) >= 40 and sentiment_score <= 0.5:
        return True
    return False


def analyze_sentiment_with_llm(*, title: str, content: str, sector: str, platform: str, current_result: Any) -> Optional[LlmSentimentDecision]:
    if not llm_ready():
        return None

    text = _truncate_text(title=title, content=content, max_chars=_max_chars())
    if len(text.strip()) < 8:
        return None

    payload = {
        "model": _model(),
        "temperature": 0.1,
        "chat_template_kwargs": {"enable_thinking": False},
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "system",
                "content": _system_prompt(include_reasoning=_request_reasoning()),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "sector": sector,
                        "platform": platform,
                        "title": title,
                        "content": content,
                        "current_rule_result": {
                            "level": getattr(current_result, "level", ""),
                            "newbie_score": float(getattr(current_result, "newbie_score", 0) or 0),
                            "sentiment_score": float(getattr(current_result, "sentiment_score", 0) or 0),
                            "intent": getattr(current_result, "intent", "neutral") or "neutral",
                            "intent_strength": float(getattr(current_result, "intent_strength", 0) or 0),
                        },
                        "analysis_text": text,
                    },
                    ensure_ascii=False,
                ),
            },
        ],
    }

    headers = {"Content-Type": "application/json"}
    if _api_key():
        headers["Authorization"] = f"Bearer {_api_key()}"

    resp = requests.post(
        f"{_base_url()}/chat/completions",
        headers=headers,
        json=payload,
        timeout=_timeout_seconds(),
    )
    resp.raise_for_status()

    data = resp.json()
    content_text = (
        data.get("choices", [{}])[0]
        .get("message", {})
        .get("content", "")
    )
    parsed = _parse_json_payload(content_text)
    if not isinstance(parsed, dict):
        return None

    return LlmSentimentDecision(
        sentiment_score=_clamp(float(parsed.get("sentiment_score", 0.0)), -1.0, 1.0),
        sentiment_label=_normalize_sentiment_label(parsed.get("sentiment_label", "neutral")),
        emotion_intensity=_clamp(float(parsed.get("emotion_intensity", 0.0)), 0.0, 1.0),
        intent=_normalize_intent(parsed.get("intent", "neutral")),
        intent_strength=_clamp(float(parsed.get("intent_strength", 0.0)), 0.0, 1.0),
        position_status=_normalize_position(parsed.get("position_status", "unknown")),
        market_outlook=_normalize_outlook(parsed.get("market_outlook", "unknown")),
        confidence=_clamp(float(parsed.get("confidence", 0.5)), 0.0, 1.0),
        reasoning=str(parsed.get("reasoning", "") or "").strip(),
    )


def _truncate_text(*, title: str, content: str, max_chars: int) -> str:
    text = (f"{title}\n\n{content}" if content else title).strip()
    text = re.sub(r"\s+", " ", text)
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "..."


def _system_prompt(*, include_reasoning: bool) -> str:
    reasoning_line = (
        '"reasoning": "一句话说明依据"'
        if include_reasoning
        else '"reasoning": ""'
    )
    return (
        "你是中文投资社媒情绪分析助手。"
        "你的任务是提取发帖人的情绪、交易意图、当前持仓状态和本人对未来走势的观点，不是替用户预测股票涨跌。"
        "重点区分：恐慌、贪婪、中性、混合；以及买入、卖出、观望。"
        "要识别反讽、口嗨、转述新闻、纯资讯、情绪宣泄。"
        "如果帖子主要是在转发资讯或产业事实，而不是表达本人情绪，sentiment_label 应偏 neutral，intent 应为 neutral。"
        "请只输出 JSON，不要输出 markdown。"
        "字段必须包含："
        '{"sentiment_label":"fear|greed|neutral|mixed",'
        '"sentiment_score":-1.0,'
        '"emotion_intensity":0.0,'
        '"intent":"buy|sell|neutral",'
        '"intent_strength":0.0,'
        '"position_status":"none|holding|trapped|exited|unknown",'
        '"market_outlook":"bullish|bearish|sideways|unknown",'
        '"confidence":0.0,'
        f"{reasoning_line}"
        "}"
    )


def _parse_json_payload(text: str) -> Any:
    text = (text or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.S)
        if not match:
            return None
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return None


def _normalize_sentiment_label(value: Any) -> str:
    raw = str(value or "neutral").strip().lower()
    if raw in {"fear", "greed", "neutral", "mixed"}:
        return raw
    return "neutral"


def _normalize_intent(value: Any) -> str:
    raw = str(value or "neutral").strip().lower()
    if raw in {"buy", "sell", "neutral"}:
        return raw
    if raw in {"hold", "observe", "watch"}:
        return "neutral"
    return "neutral"


def _normalize_position(value: Any) -> str:
    raw = str(value or "unknown").strip().lower()
    return raw if raw in {"none", "holding", "trapped", "exited", "unknown"} else "unknown"


def _normalize_outlook(value: Any) -> str:
    raw = str(value or "unknown").strip().lower()
    return raw if raw in {"bullish", "bearish", "sideways", "unknown"} else "unknown"


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))
