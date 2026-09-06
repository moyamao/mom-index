#!/usr/bin/env python3
"""Check a llama.cpp Qwen endpoint and run one semantic smoke test."""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request


BASE_URL = os.getenv("QWEN_BASE_URL", "http://127.0.0.1:8080").rstrip("/")
TIMEOUT_SECONDS = float(os.getenv("QWEN_TIMEOUT_SECONDS", "60"))


def request_json(url: str, *, payload: dict | None = None) -> dict:
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="GET" if body is None else "POST",
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> int:
    models_url = f"{BASE_URL}/v1/models"
    print(f"Qwen endpoint: {BASE_URL}")
    try:
        models = request_json(models_url)
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        print(f"ERROR: Qwen 服务不可访问：GET {models_url}: {exc}", file=sys.stderr)
        return 2

    entries = models.get("data") or models.get("models") or []
    if not entries:
        print(f"ERROR: {models_url} 未返回模型列表", file=sys.stderr)
        return 2
    model = entries[0].get("id") or entries[0].get("model") or entries[0].get("name")
    print(f"Model: {model}")

    payload = {
        "model": model,
        "temperature": 0,
        "max_tokens": 160,
        "chat_template_kwargs": {"enable_thinking": False},
        "messages": [
            {"role": "system", "content": "你是中文股票语义分析器。只输出 JSON，不输出 Markdown。"},
            {
                "role": "user",
                "content": (
                    "分析：我看好后市，但现在不追，跌10%我再加仓。"
                    "输出 market_view、emotion、current_action、intended_action、action_timing。"
                ),
            },
        ],
    }
    started = time.perf_counter()
    try:
        result = request_json(f"{BASE_URL}/v1/chat/completions", payload=payload)
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        print(f"ERROR: Chat Completions 请求失败: {exc}", file=sys.stderr)
        return 3

    latency = time.perf_counter() - started
    message = (result.get("choices") or [{}])[0].get("message") or {}
    content = message.get("content", "")
    if not content:
        print("ERROR: 模型返回空 content；请确认 enable_thinking=false 生效", file=sys.stderr)
        print(json.dumps(result, ensure_ascii=False, indent=2), file=sys.stderr)
        return 4

    print(f"Latency: {latency:.2f}s")
    print("Response:")
    print(content)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
