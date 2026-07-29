"""
雪球 Playwright 采集器

支持两种方式：
1. 复用 `.xueqiu_session/` 持久会话目录
2. 直接把 `conf/config.ini` / `XUEQIU_COOKIE` 里的 Cookie 注入浏览器上下文
"""
from __future__ import annotations

import asyncio
import os
import re
from datetime import datetime
from typing import Dict, List, Optional
from urllib.parse import quote

from .anti_detection import get_anti_detection
from runtime_config import ini_get_bool, ini_get_float, ini_get_int

try:
    from playwright.async_api import async_playwright
    from playwright.async_api import TimeoutError as PlaywrightTimeoutError
except ImportError:  # pragma: no cover
    async_playwright = None
    PlaywrightTimeoutError = RuntimeError

_ad = get_anti_detection()
PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))
DEFAULT_SESSION_DIR = os.path.join(PROJECT_ROOT, ".xueqiu_session")
NEWSLIKE_AUTHORS = {
    "7X24快讯", "钛媒体APP", "财联社", "界面新闻", "华尔街见闻",
    "全天候科技", "新浪财经", "证券时报", "第一财经", "智通财经APP",
}


def _ensure_playwright():
    if async_playwright is None:
        raise RuntimeError(
            "未安装 playwright。先执行: python3 -m pip install playwright && python3 -m playwright install chromium"
        )


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _session_dir() -> str:
    override = os.environ.get("XUEQIU_SESSION_DIR", "").strip()
    if override:
        return os.path.expanduser(override)
    return DEFAULT_SESSION_DIR


def _chrome_channel() -> str:
    return os.environ.get("XUEQIU_CHROME_CHANNEL", "").strip()


async def _launch_persistent_context(playwright_obj, user_data_dir: str, headless: bool):
    launch_kwargs = dict(
        user_data_dir=user_data_dir,
        headless=headless,
        args=_ad.get_playwright_launch_args(),
        viewport={"width": 1440, "height": 960},
        locale="zh-CN",
        timezone_id="Asia/Shanghai",
        device_scale_factor=1,
    )
    channel = _chrome_channel()
    if channel:
        try:
            return await playwright_obj.chromium.launch_persistent_context(
                channel=channel,
                **launch_kwargs,
            )
        except Exception as e:
            print(f"    ⚠️ Xueqiu Chrome channel={channel} 启动失败，回退到 Playwright chromium: {e}")
    return await playwright_obj.chromium.launch_persistent_context(**launch_kwargs)


def _headless() -> bool:
    value = os.environ.get("XUEQIU_HEADLESS")
    if value is not None:
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return ini_get_bool("xueqiu", "headless", False)


def _fetch_full_text_enabled() -> bool:
    value = os.environ.get("XUEQIU_FETCH_FULL_TEXT")
    if value is not None:
        return value.strip().lower() in {"1", "true", "yes", "on"}
    if os.environ.get("MOM_INDEX_FETCH_FULL_TEXT") is not None:
        return _env_flag("MOM_INDEX_FETCH_FULL_TEXT", default=True)
    return ini_get_bool("xueqiu", "fetch_full_text", True)


def _detail_fetch_limit(default_limit: int) -> int:
    raw = os.environ.get("XUEQIU_DETAIL_FETCH_LIMIT", "").strip()
    if not raw:
        return ini_get_int("xueqiu", "detail_fetch_limit", default_limit)
    try:
        return max(0, int(raw))
    except ValueError:
        return default_limit


def _render_wait_seconds() -> float:
    return ini_get_float("xueqiu", "render_wait_seconds", 5.0)


def _detail_wait_seconds() -> float:
    return ini_get_float("xueqiu", "detail_wait_seconds", 2.5)


def _debug_dir() -> Optional[str]:
    value = os.environ.get("XUEQIU_DEBUG_DIR", "").strip()
    if not value:
        return None
    path = os.path.expanduser(value)
    os.makedirs(path, exist_ok=True)
    return path


def _safe_name(text: str) -> str:
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff_-]+", "_", text)[:40]


def _cookie_string() -> str:
    from .xueqiu_collector import _cookie  # 延迟导入，避免循环在模块加载时扩大

    return _cookie()


def _cookies_from_string(cookie_str: str) -> List[Dict]:
    cookies: List[Dict] = []
    for chunk in (cookie_str or "").split(";"):
        part = chunk.strip()
        if not part or "=" not in part:
            continue
        name, value = part.split("=", 1)
        name = name.strip()
        value = value.strip()
        if not name:
            continue
        cookies.append(
            {
                "name": name,
                "value": value,
                "domain": ".xueqiu.com",
                "path": "/",
            }
        )
    return cookies


async def _has_valid_login_cookie(context) -> bool:
    try:
        cookies = await context.cookies(["https://xueqiu.com/", "https://www.xueqiu.com/"])
    except Exception:
        cookies = await context.cookies()
    token_values = {
        cookie.get("name", ""): (cookie.get("value", "") or "").strip()
        for cookie in cookies
    }
    required = {"xq_a_token", "xqat", "xq_id_token"}
    valid_hits = sum(1 for name in required if token_values.get(name))
    return valid_hits >= 2


async def _inject_cookie_fallback_if_needed(context) -> bool:
    if await _has_valid_login_cookie(context):
        return False

    cookie_str = _cookie_string()
    cookies = _cookies_from_string(cookie_str)
    if not cookies:
        return False

    try:
        await context.add_cookies(cookies)
    except Exception:
        return False
    return True


def _looks_like_login_page(url: str, html: str) -> bool:
    head = (html or "")[:16000]
    normalized_url = (url or "").rstrip("/")
    login_markers = [
        "手机号登录",
        "验证码登录",
        "密码登录",
        "立即登录",
        "注册/登录",
        "登录/注册",
    ]
    marker_hits = sum(1 for marker in login_markers if marker in head)
    return (
        "login" in (url or "").lower()
        or marker_hits >= 2
        or (normalized_url == "https://xueqiu.com" and marker_hits >= 1)
        or ("登录" in head and "注册" in head)
    )


def _looks_like_verification_page(url: str, html: str) -> bool:
    head = (html or "")[:12000]
    markers = [
        "访问验证",
        "请按住滑块",
        "通过后即可继续访问网页",
        "TraceID",
    ]
    return "captcha" in (url or "").lower() or all(marker in head for marker in ["访问验证", "请按住滑块"])


def _keyword_match_ratio(posts: List[Dict], keyword: str) -> float:
    if not posts:
        return 0.0
    chars = {ch for ch in keyword if ch.strip()}
    if not chars:
        return 0.0
    matched = 0
    for post in posts:
        hay = f"{post.get('title', '')} {post.get('content', '')}"
        if any(ch in hay for ch in chars):
            matched += 1
    return matched / len(posts)


def _clean_trailing_ui_text(text: str) -> str:
    cleaned = (text or "").strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = re.sub(r"(?:\.\.\.)?展开[\w]*\s*$", "", cleaned)
    cleaned = re.sub(r"\s*[]\s*\S+\s*$", "", cleaned)
    cleaned = re.sub(r"\s*(?:转发|讨论|收藏)\s*$", "", cleaned)
    cleaned = re.sub(r"\s*[]\s*\d+\s*$", "", cleaned)
    cleaned = re.sub(r"\s*(?:转发\s+\S+|讨论\s+\S+|收藏)\s*$", "", cleaned)
    return cleaned.strip(" -.。\u3000")


def _clean_title(title: str, content: str) -> str:
    cleaned = _clean_trailing_ui_text(title)
    if len(cleaned) < 4:
        cleaned = _clean_trailing_ui_text(content[:180])
    return cleaned[:180]


def _clean_content(content: str) -> str:
    cleaned = _clean_trailing_ui_text(content)
    cleaned = re.sub(r"\s*(?:转发|讨论|收藏)(?:\s+\d+)?\s*$", "", cleaned)
    return cleaned[:2000]


def _clean_author(author: str) -> str:
    cleaned = (author or "").strip()
    cleaned = re.sub(r"(修改于.*|刚刚.*|\d{1,2}-\d{1,2}.*|\d+\s*小时前.*)$", "", cleaned).strip()
    return cleaned or "未知"


def _looks_like_newslike_post(author: str, title: str, content: str) -> bool:
    author = (author or "").strip()
    title = (title or "").strip()
    content = (content or "").strip()
    text = f"{title} {content}"

    if author in NEWSLIKE_AUTHORS:
        return True

    if title.startswith("【") and "】" in title:
        return True

    if any(token in title for token in ["快讯", "总市值达", "高开超", "表示，", "发布公告"]):
        return True

    if any(token in text for token in ["据报道", "消息面上", "记者获悉", "官方公告显示"]) and len(content) < 180:
        return True

    return False


async def _extract_detail_post(page) -> Dict[str, str]:
    return await page.evaluate(
        """
        () => {
          const norm = (text) => (text || '').replace(/\\s+/g, ' ').trim();
          const pickLongest = (selectors) => {
            const values = [];
            for (const selector of selectors) {
              for (const node of document.querySelectorAll(selector)) {
                const text = norm(node.innerText || node.textContent || '');
                if (text.length >= 12) values.push(text);
              }
            }
            values.sort((a, b) => b.length - a.length);
            return values[0] || '';
          };

          const content = pickLongest([
            '.article__bd__detail',
            '.timeline__item__content',
            '.content--description',
            '.detail__content',
            'article .text',
          ]);
          const title = pickLongest([
            '.article__bd__title',
            '.timeline__item__title',
            'h1',
            'title',
          ]).slice(0, 180);
          const author = pickLongest([
            '.user-name',
            '.article__bd__from .user-name',
            '[class*="author"]',
          ]);
          const date = pickLongest([
            '.date-and-source',
            '.article__bd__from',
            'time',
            '[class*="date"]',
          ]);
          return { title, content, author, date };
        }
        """
    )


async def _enrich_posts_with_detail(browser, posts: List[Dict], keyword: str, debug_dir: Optional[str]) -> List[Dict]:
    if not posts or not _fetch_full_text_enabled():
        return posts

    limit = min(len(posts), _detail_fetch_limit(len(posts)))
    if limit <= 0:
        return posts

    detail_page = await browser.new_page()
    try:
        for index, post in enumerate(posts[:limit], start=1):
            url = (post.get("url") or "").strip()
            if not url:
                continue
            try:
                try:
                    await detail_page.goto(url, wait_until="domcontentloaded", timeout=45000)
                except PlaywrightTimeoutError:
                    await detail_page.goto(url, wait_until="commit", timeout=45000)
                await asyncio.sleep(float(os.environ.get("XUEQIU_DETAIL_WAIT_SECONDS", _detail_wait_seconds())))
                detail = await _extract_detail_post(detail_page)
                detail_content = _clean_content(detail.get("content", ""))
                if len(detail_content) < max(60, len(post.get("content", "")) + 20):
                    continue

                post["content"] = detail_content
                if detail.get("title"):
                    post["title"] = _clean_title(detail["title"], detail_content)
                if detail.get("author"):
                    post["author"] = _clean_author(detail["author"])
                if detail.get("date"):
                    post["date"] = _clean_trailing_ui_text(detail["date"]) or post.get("date", "未知")
                post["detail_fetched"] = True
                post["content_source"] = "detail_page"
            except Exception as e:
                if debug_dir:
                    safe = _safe_name(f"{keyword}_{index}_detail")
                    with open(os.path.join(debug_dir, f"xueqiu_pw_{safe}.txt"), "w", encoding="utf-8") as f:
                        f.write(f"url={url}\\nerror={e}\\n")
                continue
        return posts
    finally:
        await detail_page.close()


async def _search_xueqiu_on_page(browser, page, keyword: str, limit: int, debug_dir: Optional[str]) -> List[Dict]:
    url = _search_url(keyword)
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
    except PlaywrightTimeoutError:
        await page.goto(url, wait_until="commit", timeout=45000)

    # 优先切到“讨论”tab，避免综合页混入股票/公告/新闻结果。
    try:
        discussion_tab = page.locator('a[href="#/timeline"]').first
        if await discussion_tab.count():
            await discussion_tab.click(timeout=5000)
            await asyncio.sleep(1.5)
    except Exception:
        pass

    await asyncio.sleep(float(os.environ.get("XUEQIU_RENDER_WAIT_SECONDS", _render_wait_seconds())))
    html = await page.content()
    current_url = page.url

    if debug_dir:
        safe = _safe_name(keyword)
        with open(os.path.join(debug_dir, f"xueqiu_pw_{safe}.html"), "w", encoding="utf-8") as f:
            f.write(html)
        await page.screenshot(path=os.path.join(debug_dir, f"xueqiu_pw_{safe}.png"), full_page=True)
        print(f"    调试页面: title={await page.title()} url={current_url}")

    if _looks_like_verification_page(current_url, html):
        print(f"    ⚠️ 雪球触发访问验证，请在浏览器里手动完成滑块后再继续: {keyword}")
        if debug_dir:
            print(f"    调试文件已保存到: {debug_dir}")
        return []

    if _looks_like_login_page(current_url, html):
        raise RuntimeError(f"雪球页面仍要求登录，当前停留在未登录首页: {current_url}")

    if current_url.rstrip("/") == "https://xueqiu.com" and keyword not in html:
        raise RuntimeError(f"雪球搜索未生效，当前仍停留在首页: {current_url}")

    posts = await _extract_posts(page, keyword, limit)
    posts = await _enrich_posts_with_detail(browser, posts, keyword, debug_dir)
    return posts


async def setup_session(start_url: str = "https://xueqiu.com/") -> str:
    _ensure_playwright()
    session_dir = _session_dir()
    os.makedirs(session_dir, exist_ok=True)

    async with async_playwright() as p:
        browser = await _launch_persistent_context(p, session_dir, headless=False)
        for script in _ad.get_stealth_scripts():
            await browser.add_init_script(script)

        page = await browser.new_page()
        await page.goto(start_url, wait_until="domcontentloaded", timeout=45000)
        print("浏览器已打开，请完成雪球登录/验证，然后回终端按回车保存会话。")
        await asyncio.to_thread(input)
        verify_keyword = "海力士"
        verify_url = _search_url(verify_keyword)
        try:
            await page.goto(verify_url, wait_until="domcontentloaded", timeout=45000)
        except PlaywrightTimeoutError:
            await page.goto(verify_url, wait_until="commit", timeout=45000)
        await asyncio.sleep(4.0)
        html = await page.content()
        current_url = page.url
        has_login_cookie = await _has_valid_login_cookie(browser)
        if _looks_like_verification_page(current_url, html):
            raise RuntimeError("雪球仍处于访问验证页，登录态未保存，请先完成滑块验证")
        if _looks_like_login_page(current_url, html) or not has_login_cookie:
            raise RuntimeError("雪球登录态未生效，未检测到有效登录 Cookie，请确认已经真正登录成功后再保存")
        if current_url.rstrip("/") == "https://xueqiu.com" or verify_keyword not in html:
            raise RuntimeError("雪球登录态未通过搜索校验：保存前仍无法进入搜索结果页")
        await browser.close()

    return session_dir


async def _extract_posts(page, keyword: str, limit: int) -> List[Dict]:
    raw_items = await page.evaluate(
        """
        (limit) => {
          const norm = (text) => (text || "").replace(/\\s+/g, " ").trim();
          const articles = Array.from(document.querySelectorAll('article.timeline__item'));
          const candidates = [];
          for (const article of articles) {
            const infoNode = article.querySelector('.timeline__item__info');
            const contentNode = article.querySelector('.timeline__item__content');
            if (!infoNode || !contentNode) continue;

            const userLink = article.querySelector('.user-name');
            const detailLink =
              article.querySelector('.date-and-source[href*="/"]') ||
              article.querySelector('.timeline__item__content a[href*="/"][target="_blank"]');
            const titleNode = article.querySelector('.timeline__item__title span:last-child');
            const descNode = article.querySelector('.content--description');
            const sourceNode = article.querySelector('.date-and-source');

            const href = (detailLink && detailLink.href) || "";
            if (!href.includes("xueqiu.com/")) continue;
            if (href.includes("/k?") || href.endsWith("/")) continue;

            const title = norm((titleNode && titleNode.innerText) || "");
            const body = norm((descNode && descNode.innerText) || contentNode.innerText || "");
            const author = norm((userLink && userLink.innerText) || "");
            const meta = norm((sourceNode && sourceNode.innerText) || "");
            const text = norm([author, meta, title, body].filter(Boolean).join(" "));

            if (body.length < 12) continue;
            candidates.push({
              href,
              author,
              meta,
              title: (title || body).slice(0, 180),
              text: text.slice(0, 2000),
              body: body.slice(0, 2000),
            });
            if (candidates.length >= limit * 3) break;
          }
          return candidates;
        }
        """,
        limit,
    )

    posts: List[Dict] = []
    seen = set()
    for item in raw_items:
        href = (item.get("href") or "").strip()
        title = (item.get("title") or "").strip()
        content = (item.get("body") or item.get("text") or "").strip()
        dedupe_key = href or title or content[:80]
        if not dedupe_key or dedupe_key in seen:
            continue
        seen.add(dedupe_key)

        author = _clean_author(item.get("author") or "")

        date_text = "未知"
        meta_text = (item.get("meta") or item.get("text") or "").strip()
        date_match = re.search(
            r"(\d{4}-\d{1,2}-\d{1,2}(?:\s+\d{1,2}:\d{1,2})?|\d{1,2}-\d{1,2}(?:\s+\d{1,2}:\d{1,2})?|刚刚|\d+\s*分钟前|\d+\s*小时前|昨天\s+\d{1,2}:\d{1,2})",
            meta_text,
        )
        if date_match:
            date_text = date_match.group(1)

        post_id = re.sub(r"[^0-9A-Za-z]+", "_", href).strip("_")[:120]
        clean_content = _clean_content(content)
        clean_title = _clean_title(title, clean_content or content)
        if _looks_like_newslike_post(author, clean_title, clean_content):
            continue
        posts.append(
            {
                "id": post_id or f"xueqiu_pw_{abs(hash((keyword, title)))}",
                "title": clean_title,
                "content": clean_content,
                "url": href,
                "platform": "xueqiu",
                "source_mode": "playwright",
                "keyword": keyword,
                "author": author,
                "date": date_text,
                "collected_at": datetime.now().isoformat(),
            }
        )
        if len(posts) >= limit:
            break

    ratio = _keyword_match_ratio(posts, keyword)
    if posts and ratio < 0.15:
        return []
    return posts


async def search_xueqiu(keyword: str, limit: int = 10) -> List[Dict]:
    _ensure_playwright()
    session_dir = _session_dir()
    os.makedirs(session_dir, exist_ok=True)

    async with async_playwright() as p:
        browser = await _launch_persistent_context(p, session_dir, headless=_headless())
        for script in _ad.get_stealth_scripts():
            await browser.add_init_script(script)

        await _inject_cookie_fallback_if_needed(browser)

        page = await browser.new_page()
        debug_dir = _debug_dir()
        try:
            posts = await _search_xueqiu_on_page(browser, page, keyword, limit, debug_dir)
            if posts:
                return posts

            raise RuntimeError(f"雪球页面抓取为空: {page.url}")
        finally:
            await browser.close()


def _search_url(keyword: str, tab: str = "timeline") -> str:
    base = f"https://xueqiu.com/k?q={quote(keyword)}"
    if not tab:
        return base
    return f"{base}#/{tab.lstrip('#/')}"


def collect_keyword(keyword: str, limit: int = 10) -> List[Dict]:
    return asyncio.run(search_xueqiu(keyword, limit=limit))


async def _collect_all_async() -> Dict[str, List[Dict]]:
    from .xueqiu_collector import SEARCH_KEYWORDS

    result: Dict[str, List[Dict]] = {}
    session_dir = _session_dir()
    os.makedirs(session_dir, exist_ok=True)

    async with async_playwright() as p:
        browser = await _launch_persistent_context(p, session_dir, headless=_headless())
        for script in _ad.get_stealth_scripts():
            await browser.add_init_script(script)

        await _inject_cookie_fallback_if_needed(browser)

        page = await browser.new_page()
        debug_dir = _debug_dir()
        try:
            limit = int(os.environ.get("XUEQIU_PLAYWRIGHT_LIMIT", os.environ.get("XUEQIU_COUNT", "10")))
            for sector_key, keywords in SEARCH_KEYWORDS.items():
                collected: List[Dict] = []
                for keyword in keywords:
                    try:
                        posts = await _search_xueqiu_on_page(browser, page, keyword, limit, debug_dir)
                        collected.extend(posts)
                        print(f"  [雪球-{sector_key}] '{keyword}' → {len(posts)} 条")
                        _ad.sleep_like_human("search")
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
        finally:
            await browser.close()

    return result


def collect_all() -> Dict[str, List[Dict]]:
    return asyncio.run(_collect_all_async())
