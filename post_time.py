"""Normalize common Chinese social-post timestamps into sortable local datetimes."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

BEIJING = timezone(timedelta(hours=8))


def beijing_now() -> datetime:
    return datetime.now(BEIJING).replace(tzinfo=None)


def normalize_social_datetime(raw_value: object, now: datetime | None = None) -> str:
    raw = re.sub(r"\s+", " ", str(raw_value or "")).strip()
    if not raw or raw == "未知":
        return ""

    now = now or beijing_now()
    if now.tzinfo:
        now = now.astimezone(BEIJING).replace(tzinfo=None)
    try:
        value = datetime.fromisoformat(raw.replace('Z', '+00:00'))
        if value.tzinfo:
            value = value.astimezone(BEIJING).replace(tzinfo=None)
        return value.strftime('%Y-%m-%d %H:%M:%S')
    except ValueError:
        pass
    if "刚刚" in raw:
        return now.strftime("%Y-%m-%d %H:%M:%S")

    for pattern, delta_key in ((r"(\d+)\s*秒前", "seconds"), (r"(\d+)\s*分钟前", "minutes"), (r"(\d+)\s*小时前", "hours")):
        match = re.search(pattern, raw)
        if match:
            return (now - timedelta(**{delta_key: int(match.group(1))})).strftime("%Y-%m-%d %H:%M:%S")

    day_time = re.search(r"(今天|昨天)\s*(\d{1,2}):(\d{1,2})", raw)
    if day_time:
        day_offset = 1 if day_time.group(1) == "昨天" else 0
        value = (now - timedelta(days=day_offset)).replace(
            hour=int(day_time.group(2)), minute=int(day_time.group(3)), second=0, microsecond=0
        )
        return value.strftime("%Y-%m-%d %H:%M:%S")

    full_date = re.search(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?:\s+(\d{1,2}):(\d{1,2}))?", raw)
    if full_date:
        try:
            value = datetime(
                int(full_date.group(1)), int(full_date.group(2)), int(full_date.group(3)),
                int(full_date.group(4) or 0), int(full_date.group(5) or 0),
            )
            return value.strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            return ""

    month_day = re.search(r"(?<!\d)(\d{1,2})[-/.](\d{1,2})(?:\s+(\d{1,2}):(\d{1,2}))?", raw)
    if month_day:
        try:
            value = datetime(
                now.year, int(month_day.group(1)), int(month_day.group(2)),
                int(month_day.group(3) or 0), int(month_day.group(4) or 0),
            )
            if value > now + timedelta(days=1):
                value = value.replace(year=value.year - 1)
            return value.strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            return ""

    return ""
