"""
微博 Playwright 采集器（实验性）

推荐流程：
1. 先运行 scripts/setup_weibo_session.py 手动登录一次
2. 再运行 scripts/test_weibo_playwright.py 测关键词搜索
"""
import asyncio
import os
import re
from datetime import datetime, timedelta
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
DEFAULT_SESSION_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".weibo_session")


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
    override = os.environ.get("WEIBO_SESSION_DIR", "").strip()
    if override:
        return os.path.expanduser(override)
    return DEFAULT_SESSION_DIR


def _chrome_channel() -> str:
    return os.environ.get("WEIBO_CHROME_CHANNEL", "").strip()


async def _launch_persistent_context(playwright_obj, user_data_dir: str, headless: bool):
    launch_kwargs = dict(
        user_data_dir=user_data_dir,
        headless=headless,
        args=_ad.get_playwright_launch_args(),
        viewport={"width": 1366, "height": 900},
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
            print(f"    ⚠️ Weibo Chrome channel={channel} 启动失败，回退到 Playwright chromium: {e}")
    return await playwright_obj.chromium.launch_persistent_context(**launch_kwargs)


def _headless() -> bool:
    value = os.environ.get("WEIBO_HEADLESS")
    if value is not None:
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return ini_get_bool("weibo", "headless", False)


def _fetch_full_text_enabled() -> bool:
    value = os.environ.get("WEIBO_FETCH_FULL_TEXT")
    if value is not None:
        return value.strip().lower() in {"1", "true", "yes", "on"}
    if os.environ.get("MOM_INDEX_FETCH_FULL_TEXT") is not None:
        return _env_flag("MOM_INDEX_FETCH_FULL_TEXT", default=True)
    return ini_get_bool("weibo", "fetch_full_text", True)


def _detail_fetch_limit(default_limit: int) -> int:
    raw = os.environ.get("WEIBO_DETAIL_FETCH_LIMIT", "").strip()
    if not raw:
        return ini_get_int("weibo", "detail_fetch_limit", default_limit)
    try:
        return max(0, int(raw))
    except ValueError:
        return default_limit


def _render_wait_seconds() -> float:
    return ini_get_float("weibo", "render_wait_seconds", 5.0)


def _detail_wait_seconds() -> float:
    return ini_get_float("weibo", "detail_wait_seconds", 2.5)


def _debug_dir() -> Optional[str]:
    value = os.environ.get("WEIBO_DEBUG_DIR", "").strip()
    if not value:
        return None
    path = os.path.expanduser(value)
    os.makedirs(path, exist_ok=True)
    return path


def _safe_name(text: str) -> str:
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff_-]+", "_", text)[:40]


def _clean_post_text(text: str) -> str:
    text = (text or "").strip()
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"(?:(?:\.\.\.|…)\s*)?全文\s*$", "", text)
    text = re.sub(r"\s*收起[d]?\s*$", "", text)
    text = re.sub(r"\s*展开[c]?\s*$", "", text)
    return text.strip(" -.。\u3000")


def _looks_like_low_quality_post(content: str) -> bool:
    compact = re.sub(r"\s+", "", content or "")
    if len(compact) < 12:
        return True
    if compact.startswith("#") and compact.count("#") >= 2 and len(compact) <= 24:
        return True
    if compact.startswith("@") and len(compact) <= 24:
        return True

    low_quality_patterns = [
        r"点击.*(?:链接|主页|头像)",
        r"(私信|留言).*?(领取|获取|咨询)",
        r"(关注|加V|vx|v信|微信)",
        r"(推荐给大家|建议收藏|速看|冲冲冲)$",
    ]
    return any(re.search(pattern, compact, re.I) for pattern in low_quality_patterns)


def _normalize_weibo_datetime(raw_text: str) -> str:
    raw = (raw_text or "").strip()
    if not raw or raw == "未知":
        return ""

    now = datetime.now()

    if raw == "刚刚":
        return now.strftime("%Y-%m-%d %H:%M:%S")

    minute_match = re.match(r"(\d+)\s*分钟前", raw)
    if minute_match:
        dt = now - timedelta(minutes=int(minute_match.group(1)))
        return dt.strftime("%Y-%m-%d %H:%M:%S")

    hour_match = re.match(r"(\d+)\s*小时前", raw)
    if hour_match:
        dt = now - timedelta(hours=int(hour_match.group(1)))
        return dt.strftime("%Y-%m-%d %H:%M:%S")

    second_match = re.match(r"(\d+)\s*秒前", raw)
    if second_match:
        dt = now - timedelta(seconds=int(second_match.group(1)))
        return dt.strftime("%Y-%m-%d %H:%M:%S")

    yesterday_match = re.match(r"昨天\s+(\d{1,2}):(\d{1,2})", raw)
    if yesterday_match:
        dt = (now - timedelta(days=1)).replace(
            hour=int(yesterday_match.group(1)),
            minute=int(yesterday_match.group(2)),
            second=0,
            microsecond=0,
        )
        return dt.strftime("%Y-%m-%d %H:%M:%S")

    month_day_time_match = re.match(r"(\d{1,2})-(\d{1,2})\s+(\d{1,2}):(\d{1,2})", raw)
    if month_day_time_match:
        dt = now.replace(
            month=int(month_day_time_match.group(1)),
            day=int(month_day_time_match.group(2)),
            hour=int(month_day_time_match.group(3)),
            minute=int(month_day_time_match.group(4)),
            second=0,
            microsecond=0,
        )
        if dt > now + timedelta(days=1):
            dt = dt.replace(year=dt.year - 1)
        return dt.strftime("%Y-%m-%d %H:%M:%S")

    full_date_match = re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})(?:\s+(\d{1,2}):(\d{1,2}))?", raw)
    if full_date_match:
        dt = datetime(
            year=int(full_date_match.group(1)),
            month=int(full_date_match.group(2)),
            day=int(full_date_match.group(3)),
            hour=int(full_date_match.group(4) or 0),
            minute=int(full_date_match.group(5) or 0),
            second=0,
        )
        return dt.strftime("%Y-%m-%d %H:%M:%S")

    month_day_match = re.match(r"(\d{1,2})-(\d{1,2})$", raw)
    if month_day_match:
        dt = now.replace(
            month=int(month_day_match.group(1)),
            day=int(month_day_match.group(2)),
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
        if dt > now + timedelta(days=1):
            dt = dt.replace(year=dt.year - 1)
        return dt.strftime("%Y-%m-%d %H:%M:%S")

    return ""


async def setup_session(start_url: str = "https://m.weibo.cn/") -> str:
    _ensure_playwright()
    session_dir = _session_dir()
    os.makedirs(session_dir, exist_ok=True)

    async with async_playwright() as p:
        browser = await _launch_persistent_context(p, session_dir, headless=False)
        for script in _ad.get_stealth_scripts():
            await browser.add_init_script(script)

        page = await browser.new_page()
        await page.goto(start_url, wait_until="domcontentloaded", timeout=30000)
        print("浏览器已打开，请完成微博登录/验证，然后回终端按回车保存会话。")
        await asyncio.to_thread(input)
        await browser.close()

    return session_dir


async def _navigate_via_ui(page, keyword: str):
    await page.goto("https://m.weibo.cn/", wait_until="commit", timeout=45000)
    await asyncio.sleep(3)

    trigger_selectors = [
        'a[href*="search"]',
        'div[class*="search"]',
        'span[class*="search"]',
        'button[aria-label*="搜索"]',
    ]
    for selector in trigger_selectors:
        try:
            trigger = page.locator(selector).first
            if await trigger.is_visible(timeout=1500):
                await trigger.click()
                await asyncio.sleep(1)
                break
        except Exception:
            continue

    input_selectors = [
        'input[placeholder*="搜索"]',
        'input[type="search"]',
        'textarea[placeholder*="搜索"]',
        'input',
    ]
    input_found = None
    for selector in input_selectors:
        try:
            locator = page.locator(selector).first
            await locator.wait_for(timeout=4000)
            if await locator.is_visible():
                input_found = locator
                break
        except Exception:
            continue

    if input_found is None:
        raise RuntimeError("未找到微博搜索输入框")

    await input_found.click()
    await asyncio.sleep(0.5)
    await input_found.fill(keyword)
    await asyncio.sleep(0.8)
    await input_found.press("Enter")
    await asyncio.sleep(5)


async def _extract_posts_from_dom(page, keyword: str, limit: int, prefer_realtime_block: bool = False) -> List[Dict]:
    js = """
    ({ limit, preferRealtimeBlock }) => {
      const out = [];
      let cards = [];
      if (preferRealtimeBlock) {
        const rtTitle = Array.from(document.querySelectorAll('h2.card-title'))
          .find(el => (el.innerText || el.textContent || '').includes('实时微博'));
        const rtRoot = rtTitle.closest('.card11, .card');
        if (rtRoot) {
          cards = Array.from(rtRoot.querySelectorAll('.card9.weibo-member, .card9'));
        }
      }
      if (!cards.length) {
        cards = Array.from(document.querySelectorAll('.card9.weibo-member, .card9'));
      }

      for (const card of cards) {
        const textNode = card.querySelector('.weibo-main .weibo-text');
        if (!textNode) continue;

        const content = (textNode.innerText || textNode.textContent || '')
          .trim()
          .replace(/\\s+/g, ' ');
        if (!content || content.length < 20) continue;

        const authorNode = card.querySelector('.m-text-box h3');
        const timeNode = card.querySelector('.m-text-box .time');
        const fromNode = card.querySelector('.m-text-box .from');
        const detailLink = card.querySelector('a[href*="/status/"], a[href*="/detail/"]');

        let href = detailLink ? (detailLink.getAttribute('href') || '') : '';
        if (!href) {
          const statusMatch = card.innerHTML.match(/href="([^"]*\\/status\\/[^"]+)"/);
          href = statusMatch ? statusMatch[1] : '';
        }
        if (!href || (!href.includes('/status/') && !href.includes('/detail/'))) continue;

        let author = authorNode ? (authorNode.innerText || authorNode.textContent || '') : '';
        author = author.replace(/\\s+/g, ' ').trim();
        let date = timeNode ? (timeNode.innerText || timeNode.textContent || '') : '';
        date = date.replace(/\\s+/g, ' ').trim();
        let source = fromNode ? (fromNode.innerText || fromNode.textContent || '') : '';
        source = source.replace(/\\s+/g, ' ').trim();

        out.push({
          href,
          title: content.slice(0, 180),
          content,
          author,
          date,
          source
        });
        if (out.length >= limit) break;
      }
      return out;
    }
    """
    raw = await page.evaluate(
        js,
        {
            "limit": limit,
            "preferRealtimeBlock": prefer_realtime_block,
        },
    )
    posts = []
    for item in raw:
        href = item.get("href", "")
        title = _clean_post_text(item.get("title", ""))
        content = _clean_post_text(item.get("content", ""))
        if href.startswith("/"):
            href = f"https://m.weibo.cn{href}"
        mid_match = re.search(r"/(?:detail|status)/([^/?#]+)", href)
        posts.append(
            {
                "id": mid_match.group(1) if mid_match else f"weibo_pw_{abs(hash(title))}",
                "title": title[:180],
                "content": content,
                "url": href,
                "platform": "weibo",
                "source_mode": "playwright",
                "keyword": keyword,
                "author": item.get("author", "").strip() or "未知",
                "date": item.get("date", "").strip() or "未知",
                "published_at": _normalize_weibo_datetime(item.get("date", "").strip()),
                "source": item.get("source", "").strip(),
                "collected_at": datetime.now().isoformat(),
            }
        )
    seen = set()
    filtered = []
    for post in posts:
        key = post["id"] or post["url"] or post["title"]
        if key in seen:
            continue
        seen.add(key)
        if not post["url"] or "javascript:" in post["url"]:
            continue
        if "/status/" not in post["url"] and "/detail/" not in post["url"]:
            continue
        if _looks_like_low_quality_post(post["content"]):
            continue
        filtered.append(post)
    filtered.sort(
        key=lambda post: post.get("published_at") or "0000-00-00 00:00:00",
        reverse=True,
    )
    return filtered[:limit]
    

async def _switch_to_realtime_tab(page) -> bool:
    candidates = [
        page.locator("li span", has_text="实时").first,
        page.locator("li", has_text="实时").first,
        page.locator("a", has_text="实时").first,
        page.locator("text=实时").first,
    ]
    for locator in candidates:
        try:
            if await locator.is_visible(timeout=1500):
                await locator.click()
                await asyncio.sleep(3)
                return True
        except Exception:
            continue
    return False


def _is_login_page(url: str, html: str) -> bool:
    return ("login" in (url or "").lower()) or ("登录" in html[:4000] and "注册" in html[:4000])


def _is_verification_page(url: str, html: str) -> bool:
    head = (html or "")[:12000]
    markers = [
        "访问频次过高",
        "安全验证",
        "请输入验证码",
        "请完成验证",
    ]
    return any(marker in head for marker in markers) or "passport.weibo.com" in (url or "")


def _is_search_page(url: str, html: str) -> bool:
    url = url or ""
    if "search" in url or "containerid=100103" in url:
        return True
    head = html[:8000]
    return any(marker in head for marker in ["搜索", "综合", "实时", "话题"])


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
            '.detail_wbtext_4CRf9',
            '.weibo-text',
            '.weibo-main .weibo-text',
            'div[class*="detail"] div[class*="text"]',
            '.card9 .weibo-text',
          ]);
          const author = pickLongest([
            '.m-text-box h3',
            'header h3',
            'h3[class*="author"]',
            '.head-info_name_6sFQg',
          ]);
          const date = pickLongest([
            '.m-text-box .time',
            '.m-text-box .from',
            '.head-info_time_6sFQg',
            '.time',
            '.from',
          ]);
          const title = norm(content).slice(0, 180);
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
                await asyncio.sleep(float(os.environ.get("WEIBO_DETAIL_WAIT_SECONDS", _detail_wait_seconds())))
                detail = await _extract_detail_post(detail_page)
                detail_content = _clean_post_text(detail.get("content", ""))
                if len(detail_content) < max(40, len(post.get("content", "")) + 20):
                    continue

                post["content"] = detail_content
                if detail.get("title"):
                    post["title"] = _clean_post_text(detail["title"])[:180]
                if detail.get("author"):
                    post["author"] = detail["author"].strip() or post.get("author", "未知")
                if detail.get("date"):
                    normalized_date = _clean_post_text(detail["date"])
                    post["date"] = normalized_date or post.get("date", "未知")
                    post["published_at"] = _normalize_weibo_datetime(normalized_date) or post.get("published_at", "")
                post["detail_fetched"] = True
                post["content_source"] = "detail_page"
            except Exception as e:
                if debug_dir:
                    safe = _safe_name(f"{keyword}_{index}_detail")
                    with open(os.path.join(debug_dir, f"weibo_pw_{safe}.txt"), "w", encoding="utf-8") as f:
                        f.write(f"url={url}\\nerror={e}\\n")
                continue
        return posts
    finally:
        await detail_page.close()


async def _search_weibo_on_page(browser, page, keyword: str, limit: int, debug_dir: Optional[str]) -> List[Dict]:
    direct_url = f"https://m.weibo.cn/search?containerid=100103type%3D1%26q%3D{quote(keyword)}"
    try:
        await page.goto(direct_url, wait_until="commit", timeout=45000)
    except PlaywrightTimeoutError:
        print(f"    ⚠️ 直接搜索超时，回退 UI 搜索: {keyword}")
        await _navigate_via_ui(page, keyword)

    await asyncio.sleep(float(os.environ.get("WEIBO_RENDER_WAIT_SECONDS", _render_wait_seconds())))
    html = await page.content()
    url = page.url

    if debug_dir:
        safe = _safe_name(keyword)
        with open(os.path.join(debug_dir, f"weibo_pw_{safe}.html"), "w", encoding="utf-8") as f:
            f.write(html)
        await page.screenshot(path=os.path.join(debug_dir, f"weibo_pw_{safe}.png"), full_page=True)
        print(f"    调试页面: title={await page.title()} url={url}")

    if _is_verification_page(url, html):
        print(f"    ⚠️ 微博触发安全验证，请在浏览器里手动完成验证后再继续: {keyword}")
        if debug_dir:
            print(f"    调试文件已保存到: {debug_dir}")
        return []

    if _is_login_page(url, html):
        print(f"    ⚠️ 微博仍要求登录: {url}")
        return []

    if not _is_search_page(url, html):
        print(f"    ⚠️ 未成功进入微博搜索页: {url}")
        try:
            await _navigate_via_ui(page, keyword)
            await asyncio.sleep(float(os.environ.get("WEIBO_RENDER_WAIT_SECONDS", _render_wait_seconds())))
            html = await page.content()
            url = page.url
        except Exception as e:
            print(f"    ⚠️ UI 搜索失败: {e}")
            return []

    switched = await _switch_to_realtime_tab(page)
    if switched:
        await asyncio.sleep(float(os.environ.get("WEIBO_RENDER_WAIT_SECONDS", ini_get_float("weibo", "realtime_wait_seconds", 3.0))))
        html = await page.content()
        url = page.url

    if _is_verification_page(url, html):
        print(f"    ⚠️ 微博触发安全验证，请在浏览器里手动完成验证后再继续: {keyword}")
        if debug_dir:
            print(f"    调试文件已保存到: {debug_dir}")
        return []

    posts = await _extract_posts_from_dom(
        page,
        keyword,
        limit,
        prefer_realtime_block=not switched,
    )
    posts = await _enrich_posts_with_detail(browser, posts, keyword, debug_dir)
    ratio = _keyword_match_ratio(posts, keyword)
    if posts and ratio < 0.15:
        print(f"    ⚠️ 微博结果相关性过低 ({ratio:.0%})，丢弃")
        return []
    return posts


async def search_weibo(keyword: str, limit: int = 10) -> List[Dict]:
    _ensure_playwright()
    session_dir = _session_dir()
    if not os.path.isdir(session_dir):
        raise RuntimeError("微博会话不存在，请先运行: python3 scripts/setup_weibo_session.py")

    async with async_playwright() as p:
        browser = await _launch_persistent_context(p, session_dir, headless=_headless())
        for script in _ad.get_stealth_scripts():
            await browser.add_init_script(script)

        page = await browser.new_page()
        debug_dir = _debug_dir()
        try:
            return await _search_weibo_on_page(browser, page, keyword, limit, debug_dir)
        finally:
            await browser.close()


def collect_keyword(keyword: str, limit: int = 10) -> List[Dict]:
    return asyncio.run(search_weibo(keyword, limit=limit))


async def _collect_all_async() -> Dict[str, List[Dict]]:
    from .weibo_collector import SEARCH_KEYWORDS

    result: Dict[str, List[Dict]] = {}
    session_dir = _session_dir()
    if not os.path.isdir(session_dir):
        raise RuntimeError("微博会话不存在，请先运行: python3 scripts/setup_weibo_session.py")

    async with async_playwright() as p:
        browser = await _launch_persistent_context(p, session_dir, headless=_headless())
        for script in _ad.get_stealth_scripts():
            await browser.add_init_script(script)

        page = await browser.new_page()
        debug_dir = _debug_dir()
        try:
            limit = int(os.environ.get("WEIBO_PLAYWRIGHT_LIMIT", "10"))
            for sector_key, keywords in SEARCH_KEYWORDS.items():
                collected: List[Dict] = []
                for keyword in keywords:
                    try:
                        posts = await _search_weibo_on_page(browser, page, keyword, limit, debug_dir)
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
        finally:
            await browser.close()

    return result


def collect_all() -> Dict[str, List[Dict]]:
    return asyncio.run(_collect_all_async())
