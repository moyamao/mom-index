"""Stable fallback identities when a collector cannot obtain a native post ID."""
import hashlib
import json
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def canonical_url(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        parts = urlsplit(raw)
    except ValueError:
        return raw
    ignored = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "spm", "from"}
    query = urlencode(
        sorted((key, val) for key, val in parse_qsl(parts.query, keep_blank_values=True) if key.lower() not in ignored)
    )
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, query, ""))


def fallback_id(platform, *, url="", author="", title="", content=""):
    link = canonical_url(url)
    fields = ["url", link] if link else [
        "text", *(" ".join(str(s or "").split()) for s in (author, title, content))
    ]
    raw = json.dumps([platform, *fields], ensure_ascii=False).encode("utf-8")
    return "fallback_" + hashlib.sha256(raw).hexdigest()
