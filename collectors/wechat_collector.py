"""Import explicitly exported WeChat group messages as a local-only source."""
from __future__ import annotations

import csv
import hashlib
import json
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Iterable, List

from runtime_config import ini_get, ini_get_bool, ini_get_int
from keyword_config import get_keywords

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_EXPORT_DIR = PROJECT_ROOT / "private" / "wechat_exports"
SUPPORTED_SUFFIXES = {".json", ".jsonl", ".csv", ".txt"}
SECTOR_KEYWORDS = get_keywords()


def wechat_enabled() -> bool:
    raw = os.environ.get("MOM_INDEX_ENABLE_WECHAT")
    if raw is not None:
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    return ini_get_bool("wechat", "enabled", False)


def _split_groups(raw: str) -> set[str]:
    return {item.strip() for item in re.split(r"[,，\n]", raw or "") if item.strip()}


def _parse_datetime(value: object) -> datetime | None:
    if isinstance(value, (int, float)):
        timestamp = float(value) / (1000 if float(value) > 10_000_000_000 else 1)
        try:
            return datetime.fromtimestamp(timestamp)
        except (ValueError, OSError):
            return None
    text = str(value or "").strip().replace("T", " ").replace("Z", "")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y/%m/%d %H:%M:%S"):
        try:
            return datetime.strptime(text[:19], fmt)
        except ValueError:
            continue
    return None


def _normalized_record(record: dict, source: Path) -> dict | None:
    aliases = {
        "group": ("group", "group_name", "chatroom", "群名"),
        "sender": ("sender", "sender_name", "nickname", "发送者"),
        "content": ("content", "text", "message", "消息"),
        "time": ("published_at", "timestamp", "time", "date", "时间"),
        "type": ("type", "message_type", "消息类型"),
    }

    def pick(name: str) -> object:
        return next((record[key] for key in aliases[name] if record.get(key) not in (None, "")), "")

    content = re.sub(r"\s+", " ", str(pick("content"))).strip()
    if not content or content.startswith(("[图片]", "[视频]", "[语音]", "[表情]")):
        return None
    message_type = str(pick("type")).lower()
    if message_type and message_type not in {"1", "text", "文本"}:
        return None
    return {
        "group": str(pick("group")).strip() or source.stem,
        "sender": str(pick("sender")).strip(),
        "content": content,
        "published_at": _parse_datetime(pick("time")),
    }


def _read_records(path: Path) -> Iterable[dict]:
    if path.suffix.lower() == ".json":
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        if isinstance(payload, dict):
            payload = payload.get("messages", payload.get("items", []))
        if isinstance(payload, list):
            yield from (item for item in payload if isinstance(item, dict))
        return
    if path.suffix.lower() == ".jsonl":
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            if line.strip():
                item = json.loads(line)
                if isinstance(item, dict):
                    yield item
        return
    delimiter = "\t" if path.suffix.lower() == ".txt" else ","
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        yield from csv.DictReader(handle, delimiter=delimiter)


def _sectors_for(text: str) -> List[str]:
    lowered = text.lower()
    scores = {sector: sum(keyword.lower() in lowered for keyword in keywords) for sector, keywords in SECTOR_KEYWORDS.items()}
    best = max(scores.values(), default=0)
    return [sector for sector, score in scores.items() if score == best and score > 0]


def _hash(value: str, salt: str) -> str:
    return "wx_" + hashlib.sha256(f"{salt}:{value}".encode()).hexdigest()[:12]


def collect_all() -> Dict[str, List[dict]]:
    result = {sector: [] for sector in SECTOR_KEYWORDS}
    export_dir = Path(os.environ.get("WECHAT_EXPORT_DIR") or ini_get("wechat", "export_dir", str(DEFAULT_EXPORT_DIR))).expanduser()
    if not export_dir.exists():
        print(f"  微信导出目录不存在: {export_dir}")
        return result
    allowed_groups = _split_groups(os.environ.get("WECHAT_GROUPS") or ini_get("wechat", "groups", ""))
    lookback_days = max(1, int(os.environ.get("WECHAT_LOOKBACK_DAYS") or ini_get_int("wechat", "lookback_days", 7)))
    cutoff = datetime.now() - timedelta(days=lookback_days)
    salt = os.environ.get("WECHAT_PRIVACY_SALT") or ini_get("wechat", "privacy_salt", "mom-index-local")
    seen = set()
    for path in sorted(export_dir.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in SUPPORTED_SUFFIXES:
            continue
        try:
            for raw in _read_records(path):
                item = _normalized_record(raw, path)
                if not item or (allowed_groups and item["group"] not in allowed_groups):
                    continue
                published = item["published_at"]
                if published and published < cutoff:
                    continue
                sectors = _sectors_for(item["content"])
                if not sectors:
                    continue
                published_text = (published or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")
                identity = f'{item["group"]}|{item["sender"]}|{published_text}|{item["content"]}'
                post_id = _hash(identity, salt)
                if post_id in seen:
                    continue
                seen.add(post_id)
                for sector in sectors:
                    result[sector].append({
                        "id": post_id, "title": item["content"][:120], "content": item["content"],
                        "platform": "wechat", "source_mode": "local_export",
                        "author": _hash(item["sender"] or "unknown", salt), "keyword": "微信群聊",
                        "published_at": published_text, "collected_at": datetime.now().isoformat(timespec="seconds"),
                        "url": "", "is_mock": False,
                    })
        except (OSError, UnicodeError, json.JSONDecodeError, csv.Error) as exc:
            print(f"  微信导出文件跳过 {path.name}: {exc}")
    print(f"  微信群聊导入 {sum(len(items) for items in result.values())} 条（群名/发送者已脱敏）")
    return result
