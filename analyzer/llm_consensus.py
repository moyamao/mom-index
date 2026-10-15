"""Compare stored 14B output with live 27B, using cloud only for material disputes."""
from __future__ import annotations

import configparser
from dataclasses import asdict
import json
import os
from pathlib import Path
import re
import time
from typing import Any

import requests

from .llm_sentiment import (
    LlmSentimentDecision,
    _normalize_content_type,
    _normalize_emotion_tags,
    _normalize_intent,
    _normalize_maturity,
    _normalize_outlook,
    _normalize_position,
    _normalize_sentiment_label,
    _parse_json_payload,
    _system_prompt,
    _analysis_text,
)

ROOT = Path(__file__).resolve().parents[1]
def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _config() -> configparser.ConfigParser:
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(os.environ.get("MOM_INDEX_CONFIG_FILE", str(ROOT / "conf/config.ini")), encoding="utf-8")
    return parser


def endpoint_config() -> dict[str, dict[str, str]]:
    parser = _config()
    cloud = parser["llm"] if parser.has_section("llm") else {}
    configs = {
        "local_27b": {
            "base_url": os.environ.get("CONSENSUS_27B_BASE_URL", "http://127.0.0.1:8080/v1").rstrip("/"),
            "model": os.environ.get("CONSENSUS_27B_MODEL", "").strip(), "api_key": "",
        },
        "cloud": {
            "base_url": os.environ.get("CONSENSUS_CLOUD_BASE_URL", cloud.get("base_url", "")).rstrip("/"),
            "model": os.environ.get("CONSENSUS_CLOUD_MODEL", cloud.get("model", "")).strip(),
            "api_key": os.environ.get("CONSENSUS_CLOUD_API_KEY", cloud.get("api_key", "")).strip(),
        },
    }
    for name, config in configs.items():
        if not config["base_url"]:
            raise RuntimeError(f"{name} base_url未配置")
        if name == "cloud" and not (config["model"] and config["api_key"]):
            raise RuntimeError("云端裁决模型或API key未配置")
    return configs


def discover_local_model(config: dict[str, str], timeout: float) -> str:
    response = requests.get(config["base_url"] + "/models", timeout=timeout)
    response.raise_for_status()
    entries = response.json().get("data", [])
    if not entries:
        raise RuntimeError(f"{config['base_url']}/models未返回模型")
    return str(entries[0].get("id") or entries[0].get("model") or entries[0].get("name") or "")


def preflight(configs: dict[str, dict[str, str]], timeout: float = 15) -> None:
    for name in ("local_27b",):
        try:
            discovered = discover_local_model(configs[name], timeout)
        except Exception as exc:
            raise RuntimeError(
                f"{name}预检失败：无法访问{configs[name]['base_url']}/models；请先启动对应模型服务"
            ) from exc
        if not discovered:
            raise RuntimeError(f"{name}模型身份无效")
        configured = configs[name]["model"]
        if configured and configured != discovered:
            raise RuntimeError(f"{name}模型不匹配：期望{configured}，实际{discovered}")
        configs[name]["model"] = discovered


def _decision(parsed: dict[str, Any]) -> LlmSentimentDecision:
    return LlmSentimentDecision(
        content_type=_normalize_content_type(parsed.get("content_type", "opinion")),
        sentiment_score=_clamp(float(parsed.get("sentiment_score", 0)), -1, 1),
        sentiment_label=_normalize_sentiment_label(parsed.get("sentiment_label", "neutral")),
        emotion_tags=_normalize_emotion_tags(parsed.get("emotion_tags", [])),
        emotion_intensity=_clamp(float(parsed.get("emotion_intensity", 0)), 0, 1),
        intent=_normalize_intent(parsed.get("intent", "neutral")),
        intent_strength=_clamp(float(parsed.get("intent_strength", 0)), 0, 1),
        position_status=_normalize_position(parsed.get("position_status", "unknown")),
        market_outlook=_normalize_outlook(parsed.get("market_outlook", "unknown")),
        investor_maturity=_normalize_maturity(parsed.get("investor_maturity", "unknown")),
        maturity_confidence=_clamp(float(parsed.get("maturity_confidence", 0)), 0, 1),
        confidence=_clamp(float(parsed.get("confidence", .5)), 0, 1),
        reasoning=str(parsed.get("reasoning", "") or "").strip(),
    )


def call_model(config: dict[str, str], *, title: str, content: str, sector: str,
               platform: str, timeout: float, retries: int = 2) -> dict[str, Any]:
    text = _analysis_text(title=title, content=content, platform=platform, max_chars=1200)
    headers = {"Content-Type": "application/json"}
    if config.get("api_key"):
        headers["Authorization"] = "Bearer " + config["api_key"]
    payload = {
        "model": config["model"], "temperature": 0, "max_tokens": 512,
        "chat_template_kwargs": {"enable_thinking": False},
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": _system_prompt(include_reasoning=True)},
            {"role": "user", "content": json.dumps(
                {"sector": sector, "platform": platform, "analysis_text": text}, ensure_ascii=False)},
        ],
    }
    attempts = []
    for number in range(1, retries + 2):
        started = time.perf_counter()
        raw = ""
        try:
            response = requests.post(config["base_url"] + "/chat/completions", headers=headers,
                                     json=payload, timeout=timeout)
            response.raise_for_status()
            raw = str(response.json().get("choices", [{}])[0].get("message", {}).get("content", ""))
            parsed = _parse_json_payload(raw)
            if not isinstance(parsed, dict):
                raise ValueError("响应中没有可解析的JSON对象")
            decision = _decision(parsed)
            attempts.append({"attempt": number, "seconds": round(time.perf_counter()-started, 3), "error": ""})
            return {"ok": True, "model": config["model"], "decision": asdict(decision), "attempts": attempts}
        except Exception as exc:
            attempts.append({"attempt": number, "seconds": round(time.perf_counter()-started, 3),
                             "error": f"{type(exc).__name__}: {exc}"})
    return {"ok": False, "model": config["model"], "decision": None,
            "attempts": attempts, "error": attempts[-1]["error"]}


def material_disagreements(first: LlmSentimentDecision, second: LlmSentimentDecision) -> list[str]:
    """Escalate only large score gaps or explicitly opposite trading directions."""
    differences = []
    # 资讯/广告的“不参与”在指数中等价于 0 分。内容类型不同本身不值得
    # 调用云端；只有折算后的有效情绪差距足够大才升级。
    first_score = first.sentiment_score if first.content_type == "opinion" else 0.0
    second_score = second.sentiment_score if second.content_type == "opinion" else 0.0
    if abs(first_score - second_score) >= 0.30:
        differences.append("sentiment_score")

    buy_side = {"buy", "add"}
    sell_side = {"reduce", "sell", "clear"}
    if ((first.intent in buy_side and second.intent in sell_side)
            or (first.intent in sell_side and second.intent in buy_side)):
        differences.append("intent")

    if {first.market_outlook, second.market_outlook} == {"bullish", "bearish"}:
        differences.append("market_outlook")

    # 内容类型、情绪标签、仓位、成熟度与强度字段都不单独触发云端。
    return differences


def decision_from_stored(row: dict[str, Any]) -> LlmSentimentDecision:
    """Rebuild comparable decision fields from one persisted 14B analysis row."""
    try:
        emotion_tags = json.loads(row.get("emotion_tags_json") or "[]")
    except (TypeError, json.JSONDecodeError):
        emotion_tags = []
    level = str(row.get("level") or "")
    if level in {"纯小白", "偏小白"}:
        maturity = "novice"
    elif level in {"偏专业", "专业"}:
        maturity = "professional"
    elif level == "中间派":
        maturity = "general"
    elif level == "资讯帖":
        maturity = "not_applicable"
    else:
        maturity = "unknown"
    return _decision({
        "content_type": row.get("content_type"),
        "sentiment_score": row.get("sentiment_score"),
        "sentiment_label": row.get("sentiment_label"),
        "emotion_tags": emotion_tags,
        "emotion_intensity": abs(float(row.get("sentiment_score") or 0)),
        "intent": row.get("intent"),
        "intent_strength": row.get("intent_strength"),
        "position_status": row.get("position_status"),
        "market_outlook": row.get("market_outlook"),
        "investor_maturity": maturity,
        "maturity_confidence": 0.8 if maturity != "unknown" else 0,
        "confidence": row.get("sentiment_confidence"),
        "reasoning": row.get("reasoning"),
    })


def adjudicate(*, title: str, content: str, sector: str, platform: str,
               configs: dict[str, dict[str, str]], timeout: float,
               stored_14b: dict[str, Any] | None) -> tuple[LlmSentimentDecision | None, dict[str, Any]]:
    local_27b = call_model(configs["local_27b"], title=title, content=content, sector=sector,
                           platform=platform, timeout=timeout)
    stored_meta = None if stored_14b is None else {
        "id": stored_14b.get("id"), "batch_id": stored_14b.get("batch_id"),
        "analysis_profile": stored_14b.get("analysis_profile"),
        "model": stored_14b.get("model_name"), "prompt_version": stored_14b.get("prompt_version"),
    }
    audit: dict[str, Any] = {"stored_14b": stored_meta, "local_27b": local_27b}
    if not local_27b["ok"]:
        audit.update({"source": "27b_failure", "disagreements": ["local_27b_request_failure"],
                      "cloud": None, "requires_review": True})
        if stored_14b is None:
            return None, audit
        return decision_from_stored(stored_14b), audit
    second = _decision(local_27b["decision"])
    if stored_14b is None:
        audit.update({"source": "missing_14b_reference_27b", "disagreements": ["missing_14b_reference"],
                      "cloud": None, "requires_review": True})
        return second, audit
    first = decision_from_stored(stored_14b)
    audit["stored_14b"]["decision"] = asdict(first)
    differences = material_disagreements(first, second)
    audit["disagreements"] = differences
    if not differences:
        audit.update({"source": "local_agreement_27b", "cloud": None, "requires_review": False})
        return second, audit
    cloud = call_model(configs["cloud"], title=title, content=content, sector=sector,
                       platform=platform, timeout=timeout)
    audit["cloud"] = cloud
    if cloud["ok"]:
        audit.update({"source": "cloud_adjudication", "requires_review": False})
        return _decision(cloud["decision"]), audit
    audit.update({"source": "cloud_failure_fallback_27b", "requires_review": True})
    return second, audit
