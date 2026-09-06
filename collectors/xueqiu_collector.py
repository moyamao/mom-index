"""
雪球采集器

依赖登录 Cookie，优先读取：
- conf/config.ini -> [xueqiu] -> cookie

可选环境变量：
- MOM_INDEX_ENABLE_XUEQIU=1
- XUEQIU_COOKIE=...
- XUEQIU_COUNT=10
- XUEQIU_PAGES=2
"""
from __future__ import annotations

import configparser
import html as html_mod
import json
import os
import re
from datetime import datetime
from typing import Dict, List, Optional
from urllib.parse import quote

import requests

from .anti_detection import get_anti_detection
from .xueqiu_playwright import collect_keyword as collect_keyword_playwright, collect_all as collect_all_playwright
from runtime_config import ini_get, ini_get_int
from keyword_config import get_keywords

PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))
DATA_DIR = os.path.join(PROJECT_ROOT, "data")
OUTPUT_FILE = os.path.join(DATA_DIR, "xueqiu_posts.json")
DEFAULT_CONFIG_FILE = os.path.join(PROJECT_ROOT, "conf", "config.ini")

SEARCH_KEYWORDS = get_keywords()

_ad = get_anti_detection()
SEARCH_ENDPOINTS = [
    "https://xueqiu.com/query/v1/search/status.json",
    "https://xueqiu.com/query/v1/search.json",
]


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _build_proxies() -> Optional[Dict[str, str]]:
    proxy = os.environ.get("MOM_INDEX_PROXY", "").strip()
    if not proxy:
        return None
    return {"http": proxy, "https": proxy}


PROXY = _build_proxies()


def _debug_dir() -> Optional[str]:
    path = os.environ.get("XUEQIU_DEBUG_DIR", "").strip()
    if not path:
        return None
    path = os.path.expanduser(path)
    os.makedirs(path, exist_ok=True)
    return path


def _write_debug(keyword: str, suffix: str, body: str) -> None:
    debug_dir = _debug_dir()
    if not debug_dir:
        return
    safe_keyword = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff_-]+", "_", keyword)[:40]
    with open(os.path.join(debug_dir, f"{suffix}_{safe_keyword}.txt"), "w", encoding="utf-8") as f:
        f.write(body)


def _response_preview(text: str, limit: int = 220) -> str:
    compact = re.sub(r"\s+", " ", (text or "")).strip()
    if len(compact) <= limit:
        return compact
    return compact[:limit] + "..."


def _read_ini_xueqiu_config() -> Dict[str, str]:
    config_path = os.environ.get("MOM_INDEX_CONFIG_FILE", DEFAULT_CONFIG_FILE).strip() or DEFAULT_CONFIG_FILE
    if not os.path.exists(config_path):
        return {}

    parser = configparser.ConfigParser(interpolation=None)
    parser.read(config_path, encoding="utf-8")
    if not parser.has_section("xueqiu"):
        return {}

    section = parser["xueqiu"]
    cookie = section.get("cookie", "").strip()
    if cookie.startswith(("'", '"')) and cookie.endswith(("'", '"')) and len(cookie) >= 2:
        cookie = cookie[1:-1]

    return {
        "cookie": cookie,
    }


def xueqiu_enabled() -> bool:
    if _env_flag("MOM_INDEX_ENABLE_XUEQIU", default=False):
        return True
    ini_config = _read_ini_xueqiu_config()
    return bool(ini_config.get("cookie") or os.environ.get("XUEQIU_COOKIE", "").strip())


def _has_playwright_session() -> bool:
    session_dir = os.environ.get(
        "XUEQIU_SESSION_DIR",
        os.path.join(PROJECT_ROOT, ".xueqiu_session"),
    ).strip()
    return os.path.isdir(os.path.expanduser(session_dir))


def _cookie() -> str:
    ini_config = _read_ini_xueqiu_config()
    return os.environ.get("XUEQIU_COOKIE", ini_config.get("cookie", "")).strip()


def _headers(referer: str) -> Dict[str, str]:
    headers = _ad.get_ajax_headers(referer=referer)
    headers["Accept"] = "application/json, text/plain, */*"
    headers["Origin"] = "https://xueqiu.com"
    cookie = _cookie()
    if cookie:
        headers["Cookie"] = cookie
    return headers


def _search_referer(keyword: str) -> str:
    return f"https://xueqiu.com/k?q={quote(keyword)}"


def _strip_tags(text: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html_mod.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _parse_time(raw) -> tuple[str, Optional[str]]:
    if raw in (None, ""):
        return "未知", None

    if isinstance(raw, (int, float)):
        try:
            ts = float(raw)
            if ts > 1e12:
                ts /= 1000.0
            dt = datetime.fromtimestamp(ts)
            return dt.strftime("%Y-%m-%d %H:%M:%S"), dt.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return str(raw), None

    raw_text = str(raw).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%m-%d %H:%M"):
        try:
            dt = datetime.strptime(raw_text, fmt)
            if fmt == "%m-%d %H:%M":
                dt = dt.replace(year=datetime.now().year)
            return raw_text, dt.strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
    return raw_text, None


def _parse_items(payload: Dict, keyword: str) -> List[Dict]:
    raw_items = payload.get("list") or payload.get("items") or payload.get("statuses") or []
    if not isinstance(raw_items, list):
        return []

    posts: List[Dict] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue

        text = _strip_tags(
            item.get("text")
            or item.get("description")
            or item.get("title")
            or item.get("content")
            or ""
        )
        if len(text) < 6:
            continue

        user = item.get("user") or item.get("owner") or {}
        post_id = (
            item.get("id")
            or item.get("status_id")
            or item.get("statusId")
            or item.get("target")
            or ""
        )
        created_text, published_at = _parse_time(
            item.get("created_at")
            or item.get("createdAt")
            or item.get("time_before")
            or item.get("updated_at")
        )
        author = (
            user.get("screen_name")
            or user.get("name")
            or item.get("user_name")
            or "未知"
        )
        title = _strip_tags(item.get("title") or "")[:180] or text[:180]
        url = ""
        if post_id:
            url = f"https://xueqiu.com/{post_id}"
            if str(post_id).isdigit():
                url = f"https://xueqiu.com/{user.get('id', 'u')}/{post_id}"

        posts.append(
            {
                "id": str(post_id) or f"xueqiu_{abs(hash((keyword, title)))}",
                "title": title,
                "content": text,
                "url": url,
                "platform": "xueqiu",
                "source_mode": "cookie_api",
                "keyword": keyword,
                "author": author,
                "date": created_text,
                "published_at": published_at,
                "collected_at": datetime.now().isoformat(),
            }
        )
    return posts


def _classify_non_json_response(text: str) -> str:
    preview = _response_preview(text)
    lowered = (text or "").lower()
    if "<html" in lowered or "<!doctype html" in lowered:
        if "captcha" in lowered or "验证" in text or "安全" in text:
            return f"雪球触发验证页: {preview}"
        if "登录" in text or "sign in" in lowered:
            return f"雪球返回登录页，Cookie 可能失效: {preview}"
        return f"雪球返回 HTML 页，可能被风控或接口已变更: {preview}"
    return f"雪球返回非 JSON 内容: {preview}"


def _keyword_match_ratio(posts: List[Dict], keyword: str) -> float:
    if not posts:
        return 0.0
    chars = {ch for ch in keyword if ch.strip()}
    if not chars:
        return 0.0
    matched = 0
    for post in posts:
        haystack = f"{post.get('title', '')} {post.get('content', '')}"
        if any(ch in haystack for ch in chars):
            matched += 1
    return matched / len(posts)


def search_xueqiu(keyword: str) -> List[Dict]:
    mode = (
        os.environ.get("XUEQIU_SOURCE_MODE", "").strip().lower()
        or ini_get("xueqiu", "source_mode", "auto").strip().lower()
        or "auto"
    )
    cookie = _cookie()
    if not cookie:
        raise RuntimeError("缺少雪球 Cookie，请在 conf/config.ini 的 [xueqiu] 里配置 cookie")

    def _search_api(kw: str) -> List[Dict]:
        session = requests.Session()
        count = int(os.environ.get("XUEQIU_COUNT", str(ini_get_int("xueqiu", "count", 10))))
        pages = int(os.environ.get("XUEQIU_PAGES", str(ini_get_int("xueqiu", "pages", 2))))
        all_posts: List[Dict] = []
        last_error = None

        warmup_url = "https://xueqiu.com/"
        try:
            session.get(warmup_url, headers=_headers(warmup_url), proxies=PROXY, timeout=15)
        except Exception:
            pass

        for page in range(1, pages + 1):
            params = {
                "q": kw,
                "count": count,
                "page": page,
                "sort": "time",
                "source": "all",
            }
            page_posts: List[Dict] = []
            page_success = False

            for endpoint in SEARCH_ENDPOINTS:
                endpoint_name = endpoint.rsplit("/", 1)[-1].replace(".json", "")
                try:
                    resp = session.get(
                        endpoint,
                        params=params,
                        headers=_headers(_search_referer(kw)),
                        proxies=PROXY,
                        timeout=20,
                    )
                    _write_debug(kw, f"{endpoint_name}_page{page}", resp.text)

                    if resp.status_code != 200:
                        last_error = RuntimeError(f"雪球接口 HTTP {resp.status_code}: {endpoint_name}")
                        continue

                    try:
                        payload = resp.json()
                    except Exception:
                        last_error = RuntimeError(_classify_non_json_response(resp.text))
                        continue

                    error_desc = payload.get("error_description") or payload.get("error_description_cn")
                    if error_desc:
                        last_error = RuntimeError(f"雪球接口报错: {error_desc}")
                        continue

                    page_posts = _parse_items(payload, kw)
                    if not page_posts:
                        last_error = RuntimeError(f"雪球接口空结果: {endpoint_name}")
                        continue

                    page_success = True
                    break
                except Exception as e:
                    last_error = e
                    continue

            if not page_success:
                if page == 1:
                    raise RuntimeError(str(last_error or "雪球搜索失败"))
                break

            all_posts.extend(page_posts)
            _ad.sleep_like_human("search")

        ratio = _keyword_match_ratio(all_posts, kw)
        if all_posts and ratio < 0.15:
            raise RuntimeError(f"雪球结果相关性过低 ({ratio:.0%})")
        return all_posts

    def _search_playwright(kw: str) -> List[Dict]:
        limit = int(
            os.environ.get(
                "XUEQIU_PLAYWRIGHT_LIMIT",
                os.environ.get("XUEQIU_COUNT", str(ini_get_int("xueqiu", "count", 10))),
            )
        )
        return collect_keyword_playwright(kw, limit=limit)

    strategies = {
        "api": [("api", _search_api)],
        "playwright": [("playwright", _search_playwright)],
        "auto": [
            ("api", _search_api),
            ("playwright", _search_playwright),
        ],
    }
    if _has_playwright_session():
        strategies["auto"] = [
            ("playwright", _search_playwright),
            ("api", _search_api),
        ]

    last_error = None
    for _, fn in strategies.get(mode, strategies["auto"]):
        try:
            return fn(keyword)
        except Exception as e:
            last_error = e
            continue
    raise RuntimeError(str(last_error or "雪球搜索失败"))


def collect_all() -> Dict[str, List[Dict]]:
    mode = (
        os.environ.get("XUEQIU_SOURCE_MODE", "").strip().lower()
        or ini_get("xueqiu", "source_mode", "auto").strip().lower()
        or "auto"
    )
    if mode == "playwright":
        result = collect_all_playwright()
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        return result

    result: Dict[str, List[Dict]] = {}

    for sector_key, keywords in SEARCH_KEYWORDS.items():
        collected: List[Dict] = []
        for keyword in keywords:
            try:
                posts = search_xueqiu(keyword)
                collected.extend(posts)
                print(f"  [雪球-{sector_key}] '{keyword}' → {len(posts)} 条")
            except Exception as e:
                print(f"  [雪球-{sector_key}] '{keyword}' 失败: {e}")

        seen = set()
        unique: List[Dict] = []
        for post in collected:
            dedupe_key = post.get("id") or post.get("url") or post.get("title")
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            unique.append(post)
        result[sector_key] = unique

    os.makedirs(DATA_DIR, exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    return result


if __name__ == "__main__":
    data = collect_all()
    total = sum(len(v) for v in data.values())
    print(f"\n共采集 {total} 条雪球帖子 → {OUTPUT_FILE}")
