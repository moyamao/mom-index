"""
小红书 Playwright 采集器
适配 macOS / Windows / Linux，支持:
1. 专用持久会话目录（推荐，先手动登录一次）
2. 复制本机 Chrome profile 作为回退
"""
import asyncio
import json
import os
import re
import shutil
import tempfile
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from .anti_detection import get_anti_detection
from runtime_config import ini_get_bool, ini_get_float, ini_get_int
from keyword_config import get_keywords
from post_time import normalize_social_datetime

try:
    from playwright.async_api import async_playwright
    from playwright.async_api import TimeoutError as PlaywrightTimeoutError
except ImportError:  # pragma: no cover - 运行时依赖
    async_playwright = None
    PlaywrightTimeoutError = RuntimeError

_ad = get_anti_detection()

SEARCH_KEYWORDS = get_keywords()
SECTOR_HINTS = get_keywords()

INVESTMENT_HINTS = [
    "etf", "基金", "股票", "a股", "港股", "美股", "买", "卖", "上车", "建仓", "加仓", "减仓",
    "抄底", "追高", "止盈", "止损", "定投", "满仓", "清仓", "行情", "估值", "业绩", "财报",
    "涨", "跌", "反弹", "回调", "新高", "逻辑", "景气", "龙头", "概念", "板块", "爆发", "起飞",
]

LOW_QUALITY_PATTERNS = [
    r"^\d{1,2}分钟前$",
    r"^\d{1,2}小时前$",
    r"^\d{1,2}-\d{1,2}$",
    r"^[A-Za-z0-9_]{1,24}\s+\d{1,2}分钟前$",
    r"^[A-Za-z0-9_]{1,24}\s+\d{1,2}小时前$",
    r"^(今日|今天|昨天)?(分享|记录|日常|生活)$",
    r"^(回来了|冲呀|姐妹们|家人们)[!！,.，]*$",
]

OUTPUT_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "xhs_posts.json")
DEFAULT_SESSION_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".xhs_session")


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _detect_chrome_user_data_dir() -> str:
    override = os.environ.get("XHS_CHROME_USER_DATA_DIR", "").strip()
    if override:
        return os.path.expanduser(override)

    home = os.path.expanduser("~")
    if os.name == "nt":
        return os.path.join(
            os.environ.get("LOCALAPPDATA", ""),
            "Google",
            "Chrome",
            "User Data",
        )

    if sys_platform() == "darwin":
        return os.path.join(home, "Library", "Application Support", "Google", "Chrome")

    return os.path.join(home, ".config", "google-chrome")


def _detect_tmp_profile_dir() -> str:
    override = os.environ.get("XHS_TMP_PROFILE_DIR", "").strip()
    if override:
        return os.path.expanduser(override)
    return os.path.join(tempfile.gettempdir(), "pw_xhs_profile")


def _session_dir() -> str:
    override = os.environ.get("XHS_SESSION_DIR", "").strip()
    if override:
        return os.path.expanduser(override)
    return DEFAULT_SESSION_DIR


def _chrome_channel() -> str:
    return os.environ.get("XHS_CHROME_CHANNEL", "").strip()


async def _launch_persistent_context(playwright_obj, user_data_dir: str, headless: bool):
    launch_kwargs = dict(
        user_data_dir=user_data_dir,
        headless=headless,
        args=_ad.get_playwright_launch_args(),
        viewport={"width": 1366, "height": 768},
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
            print(f"    ⚠️ XHS Chrome channel={channel} 启动失败，回退到 Playwright chromium: {e}")
    return await playwright_obj.chromium.launch_persistent_context(**launch_kwargs)


def _headless() -> bool:
    value = os.environ.get("XHS_HEADLESS")
    if value is not None:
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return ini_get_bool("xiaohongshu", "headless", False)


def _fetch_full_text_enabled() -> bool:
    value = os.environ.get("XHS_FETCH_FULL_TEXT")
    if value is not None:
        return value.strip().lower() in {"1", "true", "yes", "on"}
    if os.environ.get("MOM_INDEX_FETCH_FULL_TEXT") is not None:
        return _env_flag("MOM_INDEX_FETCH_FULL_TEXT", default=True)
    return ini_get_bool("xiaohongshu", "fetch_full_text", True)


def _detail_fetch_limit(default_limit: int) -> int:
    raw = os.environ.get("XHS_DETAIL_FETCH_LIMIT", "").strip()
    if not raw:
        return ini_get_int("xiaohongshu", "detail_fetch_limit", default_limit)
    try:
        return max(0, int(raw))
    except ValueError:
        return default_limit


def _render_wait_seconds() -> float:
    return ini_get_float("xiaohongshu", "render_wait_seconds", 4.0)


def _detail_wait_seconds() -> float:
    return ini_get_float("xiaohongshu", "detail_wait_seconds", 3.0)


def _profile_dir_name() -> str:
    return os.environ.get("XHS_CHROME_PROFILE_DIR", "Default").strip() or "Default"


def _ensure_playwright():
    if async_playwright is None:
        raise RuntimeError(
            "未安装 playwright。先执行: python3 -m pip install playwright && python3 -m playwright install chromium"
        )


def sys_platform() -> str:
    import sys

    return sys.platform


def _prepare_profile_copy() -> str:
    """复制本机 Chrome 登录态到临时目录，避免和正在运行的 Chrome 锁冲突。"""
    source_root = _detect_chrome_user_data_dir()
    source_profile = os.path.join(source_root, _profile_dir_name())
    tmp_root = _detect_tmp_profile_dir()

    if not os.path.isdir(source_root):
        raise RuntimeError(f"Chrome 用户目录不存在: {source_root}")
    if not os.path.isdir(source_profile):
        raise RuntimeError(
            f"Chrome profile 不存在: {source_profile}，可通过 XHS_CHROME_PROFILE_DIR 指定，如 'Profile 1'"
        )

    if os.path.exists(tmp_root):
        shutil.rmtree(tmp_root, ignore_errors=True)
    os.makedirs(tmp_root, exist_ok=True)

    local_state = os.path.join(source_root, "Local State")
    if os.path.exists(local_state):
        shutil.copy2(local_state, os.path.join(tmp_root, "Local State"))

    shutil.copytree(source_profile, os.path.join(tmp_root, _profile_dir_name()))
    return tmp_root


def _cleanup_profile_copy(tmp_root: str):
    if _env_flag("XHS_KEEP_TMP_PROFILE", default=False):
        print(f"    保留临时 profile: {tmp_root}")
        return
    shutil.rmtree(tmp_root, ignore_errors=True)


def _debug_dump_dir() -> Optional[str]:
    value = os.environ.get("XHS_DEBUG_DIR", "").strip()
    if not value:
        return None
    path = os.path.expanduser(value)
    os.makedirs(path, exist_ok=True)
    return path


def _extract_posts_from_html(html: str, keyword: str, limit: int) -> List[Dict]:
    note_ids = re.findall(r'"note_id":"([^"]+)"', html)
    titles = re.findall(r'"display_title":"([^"]+)"', html)
    posts = []

    for i, raw_title in enumerate(titles[:limit]):
        title = raw_title.encode("utf-8").decode("unicode_escape", errors="ignore")
        note_id = note_ids[i] if i < len(note_ids) else ""
        posts.append(
            {
                "id": note_id or f"xhs_{abs(hash(title))}",
                "title": title[:180],
                "url": f"https://www.xiaohongshu.com/explore/{note_id}" if note_id else "",
                "platform": "xiaohongshu",
                "keyword": keyword,
                "collected_at": datetime.now().isoformat(),
            }
        )

    return posts


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", "", (text or "")).lower()


def _title_contains_keyword(title: str, keyword: str) -> bool:
    normalized_title = _normalize_text(title)
    normalized_keyword = _normalize_text(keyword)
    if not normalized_title or not normalized_keyword:
        return False
    return normalized_keyword in normalized_title


def _looks_like_low_quality_xhs_title(title: str) -> bool:
    compact = re.sub(r"\s+", "", title or "")
    if len(compact) < 4:
        return True
    return any(re.search(pattern, compact, re.I) for pattern in LOW_QUALITY_PATTERNS)


def _is_relevant_xhs_post(post: Dict, sector: str, keyword: str) -> bool:
    title = (post.get("title") or "").strip()
    if not title or _looks_like_low_quality_xhs_title(title):
        return False

    title_norm = _normalize_text(title)
    sector_hints = [kw.lower() for kw in SECTOR_HINTS.get(sector, [])]
    investment_hints = [kw.lower() for kw in INVESTMENT_HINTS]
    keyword_norm = _normalize_text(keyword)

    keyword_hit = bool(keyword_norm) and keyword_norm in title_norm
    sector_hit = any(hint in title_norm for hint in sector_hints)
    invest_hit = any(hint in title_norm for hint in investment_hints)

    # 标题本身命中完整搜索词，且同时带行业词/投资词，优先保留。
    if keyword_hit and (sector_hit or invest_hit):
        return True

    # 行业词 + 投资词同时命中，通常是我们想要的讨论。
    if sector_hit and invest_hit:
        return True

    # 某些存储/半导体词本身已很强，允许只靠行业词通过。
    strong_sector_markers = {
        "hbm", "dram", "nand", "海力士", "sk海力士", "美光", "三星存储", "长鑫存储",
        "兆易创新", "西部数据", "闪迪", "存储芯片", "半导体", "芯片", "纳斯达克", "黄金etf", "光模块",
    }
    if any(marker.lower() in title_norm for marker in strong_sector_markers):
        return True

    return False


async def _extract_posts_from_dom(page, keyword: str, limit: int) -> List[Dict]:
    """优先从页面 DOM 提取标题，兼容前端结构变化。"""
    js = r"""
    (limit) => {
      const results = [];
      const seen = new Set();
      const linkSelectors = [
        'a[href*="/explore/"]',
        'section a',
        '.note-item a',
        '[data-testid*="note"] a'
      ];

      for (const selector of linkSelectors) {
        for (const el of document.querySelectorAll(selector)) {
          const href = el.getAttribute('href') || '';
          const text = (el.innerText || el.textContent || '').trim().replace(/\s+/g, ' ');
          if (!href || !text || text.length < 2) continue;
          if (href.includes('/user/profile/')) continue;
          if (!href.includes('/explore/') && !href.includes('/search_result/')) continue;
          const key = href + '|' + text;
          if (seen.has(key)) continue;
          seen.add(key);
          const card = el.closest('section, [class*="note-item"], [class*="feed-item"], [class*="note-card"]') || el.parentElement;
          const cardText = (card?.innerText || card?.textContent || '').trim().replace(/\s+/g, ' ');
          const dateMatch = cardText.match(/(刚刚|\d+\s*(?:秒|分钟|小时)前|(?:今天|昨天)\s*\d{1,2}:\d{1,2}|\d{4}[-/.]\d{1,2}[-/.]\d{1,2}(?:\s+\d{1,2}:\d{1,2})?|\d{1,2}[-/.]\d{1,2}(?:\s+\d{1,2}:\d{1,2})?)/);
          results.push({ href, title: text, date: dateMatch ? dateMatch[1] : '' });
          if (results.length >= limit) return results;
        }
      }
      return results;
    }
    """
    raw_items = await page.evaluate(js, limit)
    posts = []
    for item in raw_items:
        href = item.get("href", "")
        title = item.get("title", "").strip()
        if not title:
            continue
        if "/user/profile/" in href:
            continue
        if "/explore/" not in href and "/search_result/" not in href:
            continue
        note_id_match = re.search(r"/(?:explore|search_result)/([^/?#]+)", href)
        note_id = note_id_match.group(1) if note_id_match else ""
        if href.startswith("/"):
            href = f"https://www.xiaohongshu.com{href}"
        posts.append(
            {
                "id": note_id or f"xhs_{abs(hash(title))}",
                "title": title[:180],
                "url": href,
                "platform": "xiaohongshu",
                "keyword": keyword,
                "date": item.get("date", "").strip() or "未知",
                "published_at": normalize_social_datetime(item.get("date", "")),
                "collected_at": datetime.now().isoformat(),
            }
        )
    return posts


async def _first_visible_locator(page, selectors: List[str], timeout_ms: int = 4000):
    """
    返回第一组真正可见、可交互的元素，避开 aria-hidden 或被 CSS 隐藏的假输入框。
    """
    deadline = asyncio.get_running_loop().time() + max(timeout_ms, 0) / 1000

    while True:
        for selector in selectors:
            try:
                locator = page.locator(selector)
                count = await locator.count()
            except Exception:
                continue

            for index in range(min(count, 8)):
                candidate = locator.nth(index)
                try:
                    if not await candidate.is_visible():
                        continue
                    box = await candidate.bounding_box()
                    if not box or box.get("width", 0) < 8 or box.get("height", 0) < 8:
                        continue
                    aria_hidden = (await candidate.get_attribute("aria-hidden") or "").strip().lower()
                    if aria_hidden == "true":
                        continue
                    disabled = await candidate.get_attribute("disabled")
                    if disabled is not None:
                        continue
                    tab_index = await candidate.get_attribute("tabindex")
                    if tab_index == "-1":
                        continue
                    return candidate
                except Exception:
                    continue

        if asyncio.get_running_loop().time() >= deadline:
            return None
        await asyncio.sleep(0.2)


async def _click_first_visible(page, selectors: List[str], timeout_ms: int = 2000) -> bool:
    target = await _first_visible_locator(page, selectors, timeout_ms=timeout_ms)
    if target is None:
        return False
    try:
        await target.click()
        return True
    except Exception:
        return False


async def _switch_to_latest_sort(page) -> bool:
    """打开右侧筛选抽屉，并选择“排序依据 -> 最新”。"""
    opened = await page.evaluate(
        r"""
        () => {
          const visible = (el) => {
            const rect = el.getBoundingClientRect();
            const style = getComputedStyle(el);
            return rect.width >= 8 && rect.height >= 8 && style.display !== 'none' && style.visibility !== 'hidden';
          };
          const exactText = (el) => (el.innerText || el.textContent || '').replace(/\s+/g, '').trim();
          const visibleTexts = [...document.querySelectorAll('body *')]
            .filter((el) => visible(el))
            .map((el) => exactText(el));
          if (visibleTexts.includes('排序依据') && visibleTexts.includes('最多点赞') && visibleTexts.includes('最多评论')) {
            return true;
          }
          const candidates = [...document.querySelectorAll('button, [role="button"], a, div, span')];
          for (const node of candidates) {
            if (!visible(node) || !['筛选', '已筛选'].includes(exactText(node))) continue;
            const rect = node.getBoundingClientRect();
            if (rect.left < window.innerWidth * 0.55 || rect.top > window.innerHeight * 0.45) continue;
            const target = node.closest('button, [role="button"], a') || node;
            target.click();
            return true;
          }
          return false;
        }
        """
    )
    if not opened:
        return False
    await asyncio.sleep(0.8)
    clicked = await page.evaluate(
        r"""
        () => {
          const visible = (el) => {
            const rect = el.getBoundingClientRect();
            const style = getComputedStyle(el);
            return rect.width >= 8 && rect.height >= 8 && style.display !== 'none' && style.visibility !== 'hidden';
          };
          const compact = (el) => (el.innerText || el.textContent || '').replace(/\s+/g, '').trim();
          const markers = [...document.querySelectorAll('body *')].filter((el) => visible(el) && compact(el) === '排序依据');
          for (const marker of markers) {
            let panel = marker.parentElement;
            for (let depth = 0; panel && depth < 7; depth += 1, panel = panel.parentElement) {
              const panelText = compact(panel);
              if (!panelText.includes('最多点赞') || !panelText.includes('最多评论')) continue;
              const latest = [...panel.querySelectorAll('button, [role="button"], li, div, span')]
                .find((el) => visible(el) && compact(el) === '最新');
              if (!latest) continue;
              const target = latest.closest('button, [role="button"], li') || latest;
              target.click();
              return true;
            }
          }
          return false;
        }
        """
    )
    if not clicked:
        return False
    await asyncio.sleep(1.5)
    html = await page.content()
    if not _is_search_page(page.url, html) or _is_home_feed(page.url, html):
        return False
    return await page.evaluate(
        r"""
        () => {
          const compact = (el) => (el.innerText || el.textContent || '').replace(/\s+/g, '').trim();
          const visible = (el) => {
            const rect = el.getBoundingClientRect();
            return rect.width >= 8 && rect.height >= 8 && getComputedStyle(el).visibility !== 'hidden';
          };
          for (const marker of [...document.querySelectorAll('body *')]) {
            if (!visible(marker) || compact(marker) !== '排序依据') continue;
            let panel = marker.parentElement;
            for (let depth = 0; panel && depth < 7; depth += 1, panel = panel.parentElement) {
              if (!compact(panel).includes('最多点赞')) continue;
              const choices = [...panel.querySelectorAll('button, [role="button"], li, div, span')];
              const latest = choices.find((el) => visible(el) && compact(el) === '最新');
              const general = choices.find((el) => visible(el) && compact(el) === '综合');
              if (!latest) continue;
              const target = latest.closest('button, [role="button"], li') || latest;
              const classes = `${target.className || ''} ${target.parentElement?.className || ''}`.toLowerCase();
              const explicit = target.getAttribute('aria-selected') === 'true' ||
                target.getAttribute('aria-current') === 'true' ||
                ['active', 'checked', 'selected'].includes(target.getAttribute('data-state')) ||
                /(active|selected|checked)/.test(classes);
              if (explicit) return true;
              if (general) {
                const latestStyle = getComputedStyle(latest);
                const generalStyle = getComputedStyle(general);
                if (latestStyle.color !== generalStyle.color ||
                    latestStyle.backgroundColor !== generalStyle.backgroundColor ||
                    latestStyle.fontWeight !== generalStyle.fontWeight) return true;
              }
            }
          }
          return false;
        }
        """
    )


async def _navigate_via_search_ui(page, keyword: str, go_home: bool = True) -> None:
    """
    更像真人的搜索流程：先到首页，再尝试通过搜索框输入关键词。
    页面结构可能变化，所以这里做多组选择器回退。
    """
    if go_home:
        await page.goto("https://www.xiaohongshu.com/", wait_until="commit", timeout=45000)
        await asyncio.sleep(3)

    trigger_selectors = [
        'button[aria-label*="搜索"]',
        'button.min-width-search-icon',
        'button[class*="search-icon"]',
        '.input-button .search-icon',
        '.search-icon[data-hp-bound="1"]',
        'div.search-icon',
        'span.search-icon',
        'a[href*="search"]',
        'div[class*="search"]',
    ]
    if await _click_first_visible(page, trigger_selectors, timeout_ms=1500):
        await asyncio.sleep(1)

    selectors = [
        '#search-input:not([aria-hidden="true"])',
        'input.search-input:not([aria-hidden="true"])',
        'input[placeholder*="搜索"]',
        'input[placeholder*="登录探索更多内容"]',
        'textarea[placeholder*="搜索"]',
        'input[type="search"]',
        '.search-input input',
        '.search-input textarea',
    ]

    input_found = await _first_visible_locator(page, selectors, timeout_ms=4000)

    if input_found is None:
        raise RuntimeError("未找到搜索输入框")

    await input_found.click(force=True)
    await asyncio.sleep(0.5)
    try:
        await input_found.press("Meta+A")
    except Exception:
        pass
    try:
        await input_found.fill("")
    except Exception:
        pass
    await page.keyboard.type(keyword, delay=120)
    await asyncio.sleep(0.8)
    submitted = False
    try:
        await input_found.press("Enter")
        submitted = True
    except Exception:
        pass

    if not submitted:
        submit_selectors = [
            'button.min-width-search-icon',
            'button[class*="search-icon"]',
            '.input-button .search-icon',
            '.search-icon[data-hp-bound="1"]',
            '.input-button',
            'div.search-icon',
            'span.search-icon',
        ]
        submitted = await _click_first_visible(page, submit_selectors, timeout_ms=2000)

    await asyncio.sleep(5)
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=5000)
    except PlaywrightTimeoutError:
        pass


def _is_search_page(url: str, html: str) -> bool:
    url = url or ""
    if "search_result" in url or "/search" in url:
        return True
    search_markers = [
        "搜索结果",
        "大家都在搜",
        "相关搜索",
        "搜索",
    ]
    html_head = html[:8000]
    return any(marker in html_head for marker in search_markers)


def _is_home_feed(url: str, html: str) -> bool:
    url = url or ""
    if "/explore" in url and "search_result" not in url:
        return True
    html_head = html[:8000]
    return "你的生活兴趣社区" in html_head and "搜索结果" not in html_head


async def _extract_xhs_detail_post(page) -> Dict[str, str]:
    return await page.evaluate(
        """
        () => {
          const norm = (text) => (text || '').replace(/\\s+/g, ' ').trim();
          const pickLongest = (selectors) => {
            const values = [];
            for (const selector of selectors) {
              for (const node of document.querySelectorAll(selector)) {
                const text = norm(node.innerText || node.textContent || '');
                if (text.length >= 8) values.push(text);
              }
            }
            values.sort((a, b) => b.length - a.length);
            return values[0] || '';
          };

          const title = pickLongest([
            'h1',
            '[class*="title"]',
          ]).slice(0, 180);
          const content = pickLongest([
            '#detail-desc',
            '.note-content',
            '.desc',
            '[class*="note-content"]',
            '[class*="desc"]',
            'article',
          ]);
          const author = pickLongest([
            '[class*="author"]',
            '[class*="user"] [class*="name"]',
          ]);
          const date = pickLongest([
            '[class*="date"]',
            '[class*="time"]',
            '.date',
            '.time',
          ]);
          return { title, content, author, date };
        }
        """
    )


async def _enrich_xhs_posts_with_detail(detail_page, posts: List[Dict], keyword: str, debug_dir: Optional[str]) -> List[Dict]:
    if not posts or detail_page is None or not _fetch_full_text_enabled():
        return posts

    limit = min(len(posts), _detail_fetch_limit(len(posts)))
    if limit <= 0:
        return posts

    for index, post in enumerate(posts[:limit], start=1):
        url = (post.get("url") or "").strip()
        if not url:
            continue
        try:
            try:
                await detail_page.goto(url, wait_until="domcontentloaded", timeout=45000)
            except PlaywrightTimeoutError:
                await detail_page.goto(url, wait_until="commit", timeout=45000)
            await asyncio.sleep(float(os.environ.get("XHS_DETAIL_WAIT_SECONDS", _detail_wait_seconds())))
            detail = await _extract_xhs_detail_post(detail_page)
            detail_content = re.sub(r"\s+", " ", (detail.get("content") or "")).strip()
            if len(detail_content) < 20:
                continue

            post["content"] = detail_content[:4000]
            if detail.get("title"):
                post["title"] = detail["title"].strip()[:180] or post.get("title", "")
            if detail.get("author"):
                post["author"] = detail["author"].strip() or post.get("author", "")
            if detail.get("date"):
                detail_date = detail["date"].strip()
                detail_published_at = normalize_social_datetime(detail_date)
                if detail_published_at:
                    post["date"] = detail_date
                    post["published_at"] = detail_published_at
            post["detail_fetched"] = True
            post["content_source"] = "detail_page"
        except Exception as e:
            if debug_dir:
                safe_keyword = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff_-]+", "_", f"{keyword}_{index}_detail")[:40]
                with open(os.path.join(debug_dir, f"{safe_keyword}.txt"), "w", encoding="utf-8") as f:
                    f.write(f"url={url}\\nerror={e}\\n")
            continue
    return posts


def _keyword_match_ratio(posts: List[Dict], keyword: str) -> float:
    if not posts:
        return 0.0
    chars = {ch for ch in keyword if ch.strip()}
    if not chars:
        return 0.0
    matched = 0
    for post in posts:
        title = post.get("title", "")
        if any(ch in title for ch in chars):
            matched += 1
    return matched / len(posts)


def _resolve_user_data_dir() -> Tuple[str, bool]:
    """
    返回 (user_data_dir, needs_cleanup)
    优先使用专用持久会话目录；不存在时回退到复制 Chrome profile。
    """
    session_dir = _session_dir()
    if os.path.isdir(session_dir):
        return session_dir, False
    return _prepare_profile_copy(), True


def _is_xhs_url(url: str) -> bool:
    return "xiaohongshu.com" in (url or "")


async def _select_working_page(browser):
    """
    从持久化上下文里挑一个最靠谱的页面。
    优先使用已经在小红书域名下的页面，避免误拿到 about:blank。
    """
    pages = [page for page in browser.pages if not page.is_closed()]
    xhs_pages = [page for page in pages if _is_xhs_url(page.url)]
    if xhs_pages:
        return xhs_pages[-1]
    non_blank_pages = [page for page in pages if (page.url or "").strip() and not page.url.startswith("about:blank")]
    if non_blank_pages:
        return non_blank_pages[-1]
    if pages:
        return pages[-1]
    return await browser.new_page()


async def _close_extra_blank_pages(browser, keep_page) -> None:
    for page in list(browser.pages):
        if page == keep_page or page.is_closed():
            continue
        current_url = (page.url or "").strip()
        if not current_url or current_url.startswith("about:blank"):
            try:
                await page.close()
            except Exception:
                continue


async def _ensure_home_page(page) -> None:
    current_url = (page.url or "").strip()
    if _is_xhs_url(current_url) and not current_url.startswith("about:blank"):
        return
    await page.goto("https://www.xiaohongshu.com/", wait_until="commit", timeout=45000)
    await asyncio.sleep(2)


async def bootstrap_session(start_url: str = "https://www.xiaohongshu.com/") -> str:
    """
    初始化专用会话目录。首次运行时让用户手动登录/过验证。
    """
    _ensure_playwright()
    session_dir = _session_dir()
    os.makedirs(session_dir, exist_ok=True)

    async with async_playwright() as p:
        browser = await _launch_persistent_context(p, session_dir, headless=False)

        for script in _ad.get_stealth_scripts():
            await browser.add_init_script(script)

        page = await _select_working_page(browser)
        await _close_extra_blank_pages(browser, page)
        await page.goto(start_url, wait_until="domcontentloaded", timeout=30000)
        print(f"浏览器已打开，请在页面里完成登录/验证，然后回终端按回车保存会话。")
        await asyncio.to_thread(input)
        await browser.close()

    return session_dir


async def _search_xhs_on_page(page, keyword: str, limit: int = 10, reuse_page: bool = False, detail_page=None) -> List[Dict]:
    posts: List[Dict] = []
    try:
        search_url = f"https://www.xiaohongshu.com/search_result?keyword={keyword}&type=51"
        used_ui_mode = False
        if reuse_page:
            try:
                current_url = (page.url or "").strip()
                should_go_home = (not current_url) or current_url.startswith("about:blank")
                await _navigate_via_search_ui(page, keyword, go_home=should_go_home)
                used_ui_mode = True
            except Exception as nav_err:
                print(f"    ⚠️ 当前页切词失败，回退直达搜索: {nav_err}")

        if not used_ui_mode:
            try:
                await page.goto(search_url, wait_until="commit", timeout=45000)
            except PlaywrightTimeoutError:
                print(f"    ⚠️ goto 超时，继续读取当前页面: {keyword}")
                try:
                    await _navigate_via_search_ui(page, keyword, go_home=not reuse_page)
                    used_ui_mode = True
                except Exception as nav_err:
                    print(f"    ⚠️ UI 搜索回退失败: {nav_err}")
        await asyncio.sleep(float(os.environ.get("XHS_RENDER_WAIT_SECONDS", _render_wait_seconds())))
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=5000)
        except PlaywrightTimeoutError:
            pass

        if await _switch_to_latest_sort(page):
            print(f"    [XHS] '{keyword}' 已切换最新排序")
        else:
            print(f"    ⚠️ [XHS] '{keyword}' 未确认最新排序，丢弃本次结果")
            debug_dir = _debug_dump_dir()
            if debug_dir:
                safe_keyword = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff_-]+", "_", keyword)[:40]
                html = await page.content()
                with open(os.path.join(debug_dir, f"{safe_keyword}_latest_unconfirmed.html"), "w", encoding="utf-8") as f:
                    f.write(html)
                await page.screenshot(
                    path=os.path.join(debug_dir, f"{safe_keyword}_latest_unconfirmed.png"),
                    full_page=True,
                )
            return []

        html = await page.content()
        debug_dir = _debug_dump_dir()
        if debug_dir:
            try:
                print(f"    调试页面: title={await page.title()} url={page.url}")
            except Exception:
                pass
        if debug_dir:
            safe_keyword = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff_-]+", "_", keyword)[:40]
            with open(os.path.join(debug_dir, f"{safe_keyword}.html"), "w", encoding="utf-8") as f:
                f.write(html)
            await page.screenshot(path=os.path.join(debug_dir, f"{safe_keyword}.png"), full_page=True)

        current_url = page.url

        if "安全限制" in html or "验证" in html or "300012" in html:
            print(f"    ⚠️ XHS 风控拦截: {keyword}")
            if debug_dir:
                print(f"    调试文件已保存到: {debug_dir}")
            return posts

        if "登录" in html[:3000] and "手机号" in html[:3000]:
            print(f"    ⚠️ XHS 需要登录: {keyword}")
            if debug_dir:
                print(f"    调试文件已保存到: {debug_dir}")
            return posts

        if _is_home_feed(current_url, html):
            print(f"    ⚠️ 当前仍停留在首页推荐流，不是真正搜索页: {current_url}")

        posts = await _extract_posts_from_dom(page, keyword, limit)
        if not posts:
            posts = _extract_posts_from_html(html, keyword, limit)
        if not posts and not used_ui_mode:
            try:
                print(f"    ⚠️ 直接搜索未提取到结果，切换 UI 搜索: {keyword}")
                await _navigate_via_search_ui(page, keyword, go_home=not reuse_page)
                await asyncio.sleep(float(os.environ.get("XHS_RENDER_WAIT_SECONDS", _render_wait_seconds())))
                html = await page.content()
                if debug_dir:
                    safe_keyword = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff_-]+", "_", keyword)[:40]
                    with open(os.path.join(debug_dir, f"{safe_keyword}_ui.html"), "w", encoding="utf-8") as f:
                        f.write(html)
                    await page.screenshot(path=os.path.join(debug_dir, f"{safe_keyword}_ui.png"), full_page=True)
                posts = await _extract_posts_from_dom(page, keyword, limit)
                if not posts:
                    posts = _extract_posts_from_html(html, keyword, limit)
            except Exception as ui_err:
                print(f"    ⚠️ UI 搜索提取失败: {ui_err}")

        current_url = page.url
        if not _is_search_page(current_url, html):
            print(f"    ⚠️ 未成功进入搜索结果页: {current_url}")
            return []

        if _is_home_feed(current_url, html):
            print(f"    ⚠️ 命中了首页推荐流，丢弃本次结果")
            return []

        ratio = _keyword_match_ratio(posts, keyword)
        if posts and ratio < 0.15:
            print(f"    ⚠️ 结果与关键词相关性过低 ({ratio:.0%})，丢弃本次结果")
            return []

        posts = await _enrich_xhs_posts_with_detail(debug_dir=debug_dir, detail_page=detail_page, posts=posts, keyword=keyword)

        print(f"    '{keyword}' → {len(posts)}条")
        return posts

    except Exception as e:
        print(f"    ❌ {keyword}: {e}")
        return posts


async def _launch_xhs_context():
    _ensure_playwright()
    user_data_dir, needs_cleanup = _resolve_user_data_dir()
    playwright = await async_playwright().start()
    browser = await _launch_persistent_context(playwright, user_data_dir, headless=_headless())

    for script in _ad.get_stealth_scripts():
        await browser.add_init_script(script)

    return playwright, browser, user_data_dir, needs_cleanup


async def search_xhs(keyword: str, limit: int = 10) -> List[Dict]:
    """搜索小红书，尽量复用本机 Chrome 登录态。"""
    playwright = browser = None
    user_data_dir = ""
    needs_cleanup = False
    try:
        playwright, browser, user_data_dir, needs_cleanup = await _launch_xhs_context()
        page = await _select_working_page(browser)
        await _close_extra_blank_pages(browser, page)
        detail_page = await browser.new_page()
        await _ensure_home_page(page)
        return await _search_xhs_on_page(page, keyword, limit=limit, reuse_page=False, detail_page=detail_page)
    finally:
        if browser is not None:
            await browser.close()
        if playwright is not None:
            await playwright.stop()
        if needs_cleanup and user_data_dir:
            _cleanup_profile_copy(user_data_dir)


async def _collect_all_async() -> Dict[str, List[Dict]]:
    """复用同一个浏览器上下文和页面，连续采集全部关键词。"""
    result: Dict[str, List[Dict]] = {}
    playwright = browser = None
    user_data_dir = ""
    needs_cleanup = False
    try:
        playwright, browser, user_data_dir, needs_cleanup = await _launch_xhs_context()
        page = await _select_working_page(browser)
        await _close_extra_blank_pages(browser, page)
        detail_page = await browser.new_page()
        try:
            await _ensure_home_page(page)
        except Exception:
            pass

        for sector_key, keywords in SEARCH_KEYWORDS.items():
            all_notes = []
            for kw in keywords:
                try:
                    if page.is_closed():
                        page = await _select_working_page(browser)
                    await _close_extra_blank_pages(browser, page)
                    await _ensure_home_page(page)
                    notes = await _search_xhs_on_page(page, kw, limit=8, reuse_page=True, detail_page=detail_page)
                    filtered_notes = [note for note in notes if _is_relevant_xhs_post(note, sector_key, kw)]
                    dropped = len(notes) - len(filtered_notes)
                    if dropped > 0:
                        print(f"    [XHS-{sector_key}] '{kw}' 过滤掉 {dropped} 条弱相关结果")
                    all_notes.extend(filtered_notes)
                    _ad.sleep_like_human("search")
                except Exception as e:
                    print(f"  [XHS-{sector_key}] '{kw}' 失败: {e}")
                    try:
                        if not page.is_closed():
                            await page.goto("https://www.xiaohongshu.com/", wait_until="commit", timeout=15000)
                    except Exception:
                        pass

            seen = set()
            unique = []
            for note in all_notes:
                dedupe_key = note.get("id") or note.get("title")
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                unique.append(note)
            result[sector_key] = unique
    finally:
        if browser is not None:
            await browser.close()
        if playwright is not None:
            await playwright.stop()
        if needs_cleanup and user_data_dir:
            _cleanup_profile_copy(user_data_dir)

    return result


def collect_all() -> Dict[str, List[Dict]]:
    """采集所有板块，每个关键词之间加入模拟人类延迟。"""
    return asyncio.run(_collect_all_async())


def collect_keyword(keyword: str, limit: int = 10) -> List[Dict]:
    return asyncio.run(search_xhs(keyword, limit=limit))


def setup_session(start_url: str = "https://www.xiaohongshu.com/") -> str:
    return asyncio.run(bootstrap_session(start_url=start_url))


if __name__ == "__main__":
    data = collect_all()
    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    total = sum(len(v) for v in data.values())
    print(f"\n共采集 {total} 条 XHS 帖子 → {OUTPUT_FILE}")
