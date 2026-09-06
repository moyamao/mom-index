from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import socket
import time
import urllib.parse
from pathlib import Path
from typing import Any, Dict, Optional

import requests

from runtime_config import ini_get, ini_get_bool, ini_get_float


DEFAULT_SOCIAL_MONITOR_CONFIG = os.path.expanduser(
    "~/python/stock_monitor/conf/social_monitor.json"
)


def _resolve_env(value: str) -> str:
    match = re.fullmatch(r"\$\{([A-Z0-9_]+)\}", value or "")
    return os.environ.get(match.group(1), "") if match else value


def _dingtalk_config() -> Optional[Dict[str, str]]:
    if not ini_get_bool("notifications", "dingtalk_enabled", True):
        return None

    config_path = os.environ.get("MOM_INDEX_DINGTALK_CONFIG_FILE", "").strip()
    if not config_path:
        config_path = ini_get(
            "notifications", "dingtalk_config_file", DEFAULT_SOCIAL_MONITOR_CONFIG
        )
    path = Path(os.path.expanduser(config_path))
    if not path.is_file():
        return None

    payload: Dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    for notifier in payload.get("notifiers", []):
        if str(notifier.get("provider", "")).strip().lower() != "dingtalk":
            continue
        webhook = _resolve_env(str(notifier.get("webhook", "")).strip())
        secret = _resolve_env(str(notifier.get("secret", "")).strip())
        if webhook:
            return {"webhook": webhook, "secret": secret}
    return None


def _signed_webhook(webhook: str, secret: str) -> str:
    if not secret:
        return webhook
    timestamp = str(int(time.time() * 1000))
    message = f"{timestamp}\n{secret}".encode("utf-8")
    digest = hmac.new(secret.encode("utf-8"), message, hashlib.sha256).digest()
    sign = urllib.parse.quote_plus(base64.b64encode(digest).decode("utf-8"))
    joiner = "&" if "?" in webhook else "?"
    return f"{webhook}{joiner}timestamp={timestamp}&sign={sign}"


def send_dingtalk_text(text: str) -> bool:
    config = _dingtalk_config()
    if not config:
        print("    ⚠️ 未找到钉钉机器人配置，跳过通知")
        return False

    timeout = max(1.0, ini_get_float("notifications", "timeout_seconds", 10.0))
    try:
        response = requests.post(
            _signed_webhook(config["webhook"], config["secret"]),
            json={"msgtype": "text", "text": {"content": text}},
            timeout=timeout,
        )
        response.raise_for_status()
        result = response.json()
        if result.get("errcode") not in (0, "0", None):
            raise RuntimeError(f"钉钉返回错误码 {result.get('errcode')}")
        return True
    except Exception as exc:
        print(f"    ⚠️ 钉钉通知发送失败: {exc}")
        return False


def xhs_verification_message(keyword: str, wait_seconds: int) -> str:
    return (
        "【mom-index】小红书需要人工滑块验证\n"
        f"机器：{socket.gethostname()}\n"
        f"关键词：{keyword}\n"
        f"等待：{wait_seconds} 秒\n"
        "请打开当前采集浏览器完成验证，通过后任务会自动继续。"
    )
