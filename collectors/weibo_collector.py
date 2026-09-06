"""
微博采集器（实验性）

支持多条抓取路径：
1. s.weibo.com 公开搜索页
2. m.weibo.cn 移动端 JSON 接口
3. weibo.cn 轻量页（可选 Cookie）
4. m.weibo.cn Playwright 浏览器会话
"""
import html as html_mod
import json
import os
import re
from datetime import datetime
from typing import Dict, List, Optional
from urllib.parse import quote

import requests

from .anti_detection import get_anti_detection
from .weibo_playwright import collect_keyword as collect_keyword_playwright, collect_all as collect_all_playwright
from runtime_config import ini_get, ini_get_int
from keyword_config import get_keywords

SEARCH_KEYWORDS = get_keywords()

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
OUTPUT_FILE = os.path.join(DATA_DIR, "weibo_posts.json")

_ad = get_anti_detection()


def _build_proxies() -> Optional[Dict[str, str]]:
    proxy = os.environ.get("MOM_INDEX_PROXY", "").strip()
    if not proxy:
        return None
    return {"http": proxy, "https": proxy}


PROXY = _build_proxies()


def _debug_dir() -> Optional[str]:
    path = os.environ.get("WEIBO_DEBUG_DIR", "").strip()
    if not path:
        return None
    path = os.path.expanduser(path)
    os.makedirs(path, exist_ok=True)
    return path


def _write_debug(keyword: str, mode: str, body: str):
    debug_dir = _debug_dir()
    if not debug_dir:
        return
    safe_keyword = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff_-]+", "_", keyword)[:40]
    with open(os.path.join(debug_dir, f"{mode}_{safe_keyword}.txt"), "w", encoding="utf-8") as f:
        f.write(body)


def _strip_tags(text: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html_mod.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _headers(referer: str) -> Dict[str, str]:
    headers = _ad.get_common_headers(referer=referer)
    headers["Accept"] = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    return headers


def _json_headers(referer: str) -> Dict[str, str]:
    headers = _ad.get_ajax_headers(referer=referer)
    headers["Accept"] = "application/json, text/plain, */*"
    return headers


def _cookie_headers() -> Dict[str, str]:
    headers = _headers("https://weibo.cn/")
    cookie = os.environ.get("WEIBO_COOKIE", "").strip()
    if cookie:
        headers["Cookie"] = cookie
    return headers


def _parse_s_weibo_cards(html_text: str, keyword: str) -> List[Dict]:
    card_pattern = re.compile(
        r'(<div[^>]+class="[^"]*card-wrap[^"]*"[^>]*>.*?<div[^>]+class="[^"]*card-feed[^"]*"[^>]*>.*?</div>\s*</div>)',
        re.S,
    )
    text_pattern = re.compile(r'<p[^>]+class="[^"]*txt[^"]*"[^>]*>(.*?)</p>', re.S)
    name_pattern = re.compile(r'nick-name="([^"]+)"')
    href_pattern = re.compile(r'<a[^>]+href="(//weibo\.com/[^"]+|https://weibo\.com/[^"]+)"')
    time_pattern = re.compile(r'<a[^>]+href="[^"]*detail[^"]*"[^>]*>(.*?)</a>', re.S)
    mid_pattern = re.compile(r'(?:mid|data-mid)="([^"]+)"')

    posts = []
    for raw_card in card_pattern.findall(html_text):
        text_match = text_pattern.search(raw_card)
        if not text_match:
            continue
        content = _strip_tags(text_match.group(1))
        if len(content) < 6:
            continue

        author_match = name_pattern.search(raw_card)
        href_match = href_pattern.search(raw_card)
        time_match = time_pattern.search(raw_card)
        mid_match = mid_pattern.search(raw_card)
        title = content[:100]
        url = ""
        if href_match:
            raw_href = href_match.group(1)
            url = raw_href if raw_href.startswith("http") else f"https:{raw_href}"

        posts.append(
            {
                "id": mid_match.group(1) if mid_match else f"weibo_s_{abs(hash((keyword, title)))}",
                "title": title,
                "content": content,
                "url": url,
                "platform": "weibo",
                "source_mode": "public_web",
                "keyword": keyword,
                "author": html_mod.unescape(author_match.group(1)) if author_match else "未知",
                "date": _strip_tags(time_match.group(1)) if time_match else "未知",
                "collected_at": datetime.now().isoformat(),
            }
        )
    return posts


def search_weibo_public_web(keyword: str) -> List[Dict]:
    query = quote(keyword)
    url = f"https://s.weibo.com/weibo?q={query}"
    resp = requests.get(url, headers=_headers("https://s.weibo.com/"), proxies=PROXY, timeout=20)
    resp.encoding = resp.apparent_encoding or "utf-8"
    html_text = resp.text
    _write_debug(keyword, "public_web", html_text)

    if "passport.weibo.com/visitor/" in html_text or "visitor/visitor" in html_text:
        raise RuntimeError("微博 visitor 游客验证")
    if "请输入验证码" in html_text or "安全验证" in html_text:
        raise RuntimeError("微博触发安全验证")
    if "抱歉，未找到相关结果" in html_text:
        return []
    return _parse_s_weibo_cards(html_text, keyword)


def _parse_m_weibo_cards(items: List[Dict], keyword: str) -> List[Dict]:
    posts = []
    for item in items:
        mblog = item.get("mblog") or item
        if not isinstance(mblog, dict):
            continue
        text = _strip_tags(mblog.get("text", ""))
        if len(text) < 6:
            continue
        user = mblog.get("user") or {}
        mid = str(mblog.get("mid") or mblog.get("id") or "")
        bid = mblog.get("bid") or ""
        url = f"https://m.weibo.cn/detail/{mid}" if mid else ""
        if bid:
            url = f"https://weibo.com/{user.get('id', '')}/{bid}"
        posts.append(
            {
                "id": mid or f"weibo_m_{abs(hash((keyword, text[:40])))}",
                "title": text[:100],
                "content": text,
                "url": url,
                "platform": "weibo",
                "source_mode": "mobile_api",
                "keyword": keyword,
                "author": user.get("screen_name", "未知"),
                "date": mblog.get("created_at", "未知"),
                "collected_at": datetime.now().isoformat(),
            }
        )
    return posts


def search_weibo_mobile_api(keyword: str) -> List[Dict]:
    query = quote(keyword)
    candidate_containerids = [
        f"100103type=1&q={keyword}",
        f"100103type=1&t=10&q={keyword}",
        f"100103type=60&q={keyword}",
    ]
    last_error = None
    for containerid in candidate_containerids:
        url = "https://m.weibo.cn/api/container/getIndex"
        params = {"containerid": containerid, "page_type": "searchall"}
        try:
            resp = requests.get(
                url,
                params=params,
                headers=_json_headers(f"https://m.weibo.cn/search?containerid={quote(containerid)}"),
                proxies=PROXY,
                timeout=20,
            )
            text = resp.text
            _write_debug(keyword, f"mobile_api_{re.sub(r'[^0-9A-Za-z]+', '_', containerid)}", text)
            if resp.status_code != 200:
                last_error = RuntimeError(f"移动端接口 HTTP {resp.status_code}")
                continue
            data = resp.json()
            cards = data.get("data", {}).get("cards", [])
            items = []
            for card in cards:
                if card.get("card_group"):
                    items.extend(card.get("card_group") or [])
                else:
                    items.append(card)
            posts = _parse_m_weibo_cards(items, keyword)
            if posts:
                return posts
        except Exception as e:
            last_error = e
    if last_error:
        raise RuntimeError(f"移动端接口失败: {last_error}")
    return []


def _parse_weibo_cn_posts(html_text: str, keyword: str) -> List[Dict]:
    block_pattern = re.compile(r'(<div class="c" id="M_[^"]+".*?</div>)', re.S)
    time_pattern = re.compile(r'<span class="ct">(.*?)</span>', re.S)
    link_pattern = re.compile(r'<a href="(/comment/[^"]+|/repost/[^"]+|/[^"]+/[^"]+\?[^"]*?)"')
    id_pattern = re.compile(r'<div class="c" id="M_([^"]+)"')

    posts = []
    for raw in block_pattern.findall(html_text):
        text = _strip_tags(raw)
        if len(text) < 6:
            continue
        text = text.replace("赞[", " 赞[").replace("转发[", " 转发[").replace("评论[", " 评论[")
        post_id_match = id_pattern.search(raw)
        time_match = time_pattern.search(raw)
        link_match = link_pattern.search(raw)
        url = f"https://weibo.cn{link_match.group(1)}" if link_match else ""
        posts.append(
            {
                "id": post_id_match.group(1) if post_id_match else f"weibo_cn_{abs(hash((keyword, text[:40])))}",
                "title": text[:100],
                "content": text,
                "url": url,
                "platform": "weibo",
                "source_mode": "weibo_cn",
                "keyword": keyword,
                "author": "未知",
                "date": _strip_tags(time_match.group(1)) if time_match else "未知",
                "collected_at": datetime.now().isoformat(),
            }
        )
    return posts


def search_weibo_cn(keyword: str) -> List[Dict]:
    query = quote(keyword)
    url = f"https://weibo.cn/search/mblog?keyword={query}"
    resp = requests.get(url, headers=_cookie_headers(), proxies=PROXY, timeout=20)
    resp.encoding = resp.apparent_encoding or "utf-8"
    html_text = resp.text
    _write_debug(keyword, "weibo_cn", html_text)

    if "登录" in html_text and "password" in html_text:
        raise RuntimeError("weibo.cn 需要登录 Cookie")
    if "验证码" in html_text or "security" in html_text.lower():
        raise RuntimeError("weibo.cn 触发验证")
    return _parse_weibo_cn_posts(html_text, keyword)


def _keyword_match_ratio(posts: List[Dict], keyword: str) -> float:
    if not posts:
        return 0.0
    chars = {ch for ch in keyword if ch.strip()}
    if not chars:
        return 0.0
    matched = 0
    for post in posts:
        title = post.get("title", "")
        content = post.get("content", "")
        haystack = f"{title} {content}"
        if any(ch in haystack for ch in chars):
            matched += 1
    return matched / len(posts)


def search_weibo(keyword: str) -> List[Dict]:
    mode = (
        os.environ.get("WEIBO_SOURCE_MODE", "").strip().lower()
        or ini_get("weibo", "source_mode", "auto").strip().lower()
        or "auto"
    )
    has_playwright_session = os.path.isdir(
        os.environ.get(
            "WEIBO_SESSION_DIR",
            os.path.join(os.path.dirname(os.path.dirname(__file__)), ".weibo_session"),
        )
    )

    def _playwright_search(kw: str) -> List[Dict]:
        limit = int(os.environ.get("WEIBO_PLAYWRIGHT_LIMIT", str(ini_get_int("weibo", "playwright_limit", 10))))
        return collect_keyword_playwright(kw, limit=limit)

    strategies = {
        "public_web": [("public_web", search_weibo_public_web)],
        "mobile_api": [("mobile_api", search_weibo_mobile_api)],
        "cn_cookie": [("cn_cookie", search_weibo_cn)],
        "playwright": [("playwright", _playwright_search)],
        "auto": [
            *([("playwright", _playwright_search)] if has_playwright_session else []),
            ("mobile_api", search_weibo_mobile_api),
            ("public_web", search_weibo_public_web),
            ("cn_cookie", search_weibo_cn),
        ],
    }
    selected = strategies.get(mode, strategies["auto"])
    last_error = None

    for mode_name, fn in selected:
        try:
            posts = fn(keyword)
            ratio = _keyword_match_ratio(posts, keyword)
            if posts and ratio < 0.15:
                print(f"    [微博-{mode_name}] 结果相关性过低 ({ratio:.0%})，视为失败")
                continue
            return posts
        except Exception as e:
            last_error = e
            continue

    if last_error:
        raise RuntimeError(str(last_error))
    return []


def collect_all() -> Dict[str, List[Dict]]:
    mode = (
        os.environ.get("WEIBO_SOURCE_MODE", "").strip().lower()
        or ini_get("weibo", "source_mode", "auto").strip().lower()
        or "auto"
    )
    if mode == "playwright":
        result = collect_all_playwright()
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        return result

    result: Dict[str, List[Dict]] = {}
    all_dump: Dict[str, List[Dict]] = {}

    for sector_key, keywords in SEARCH_KEYWORDS.items():
        collected: List[Dict] = []
        for keyword in keywords:
            try:
                posts = search_weibo(keyword)
                collected.extend(posts)
                print(f"  [微博-{sector_key}] '{keyword}' → {len(posts)} 条")
                _ad.sleep_like_human("search")
            except Exception as e:
                print(f"  [微博-{sector_key}] '{keyword}' 失败: {e}")

        seen = set()
        unique: List[Dict] = []
        for post in collected:
            dedupe_key = post.get("id") or post.get("url") or post.get("title")
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            unique.append(post)

        result[sector_key] = unique
        all_dump[sector_key] = unique

    os.makedirs(DATA_DIR, exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(all_dump, f, ensure_ascii=False, indent=2)

    return result


if __name__ == "__main__":
    data = collect_all()
    total = sum(len(v) for v in data.values())
    print(f"\n共采集 {total} 条微博帖子 → {OUTPUT_FILE}")
