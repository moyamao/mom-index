"""
东方财富股吧采集器 — 反检测升级版
支持板块: 纳斯达克ETF, 黄金ETF, 通信ETF(CPO), 半导体ETF
"""
import re
import html as html_mod
import requests
import time
import os
from datetime import datetime
from typing import List, Dict, Optional

from .anti_detection import get_anti_detection
from post_time import normalize_social_datetime

SECTORS = {
    "nasdaq":     {"name": "纳斯达克", "code": "of159941", "etf": "513100"},
    "gold":       {"name": "黄金",     "code": "of518880", "etf": "518880"},
    "cpo":        {"name": "CPO通信",  "code": "of515880", "etf": "515880"},
    "semiconductor": {"name": "半导体", "code": "of512480", "etf": "512480"},
}


def _build_proxies() -> Optional[Dict[str, str]]:
    """按环境变量决定是否走代理，默认直连。"""
    proxy = os.environ.get("MOM_INDEX_PROXY", "").strip()
    if not proxy:
        return None
    return {"http": proxy, "https": proxy}


PROXY = _build_proxies()

_ad = get_anti_detection()


def _is_valid_board_html(html_text: str) -> bool:
    if not html_text or len(html_text) < 2000:
        return False
    return any(marker in html_text for marker in ["股吧", "listarticle", "articleh", "l3 a3"])


def fetch_board(code: str, retries: int = 3) -> str:
    """获取按发帖时间排序的股吧列表，避免评论顶帖污染最新情绪。"""
    url = f"https://guba.eastmoney.com/list,{code},f.html"
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            headers = _ad.get_common_headers(referer="https://guba.eastmoney.com")
            resp = requests.get(url, headers=headers, proxies=PROXY, timeout=15)
            resp.encoding = "utf-8"
            resp.raise_for_status()
            if not _is_valid_board_html(resp.text):
                raise RuntimeError("股吧返回空页或异常页")
            return resp.text
        except Exception as e:
            last_error = e
            if attempt >= retries:
                break
            sleep_seconds = min(2 * attempt, 6)
            print(f"    ⚠️ 股吧请求失败，{sleep_seconds}s 后重试 ({attempt}/{retries}): {e}")
            time.sleep(sleep_seconds)
    raise RuntimeError(str(last_error))


def parse_posts(html_content: str) -> List[Dict]:
    """逐行解析帖子，兼容新版 ``span.l*`` 和旧版 ``cite.l*`` 结构。"""
    row_pattern = re.compile(
        r'<div[^>]*class="[^"]*articleh[^"]*"[^>]*>(.*?)</div>',
        re.DOTALL | re.IGNORECASE,
    )

    def field(row: str, class_name: str) -> str:
        match = re.search(
            rf'<(?:span|cite)[^>]*class="[^"]*\b{class_name}\b[^"]*"[^>]*>(.*?)</(?:span|cite)>',
            row,
            re.DOTALL | re.IGNORECASE,
        )
        if not match:
            return ""
        value = re.sub(r"<[^>]+>", "", match.group(1))
        return html_mod.unescape(value).strip()

    posts = []
    for row in row_pattern.findall(html_content):
        title_match = re.search(
            r'<a[^>]*href="(/news,[^"]*)"[^>]*title="([^"]*)"[^>]*>',
            row,
            re.DOTALL | re.IGNORECASE,
        )
        if not title_match:
            continue
        url, title = title_match.groups()
        title = html_mod.unescape(title.strip())
        if not title or title == '点击开始搜索':
            continue
        date_text = field(row, "l5") or "未知"
        posts.append({
            "id": f"guba_{url.split(',')[-1].replace('.html','')}",
            "title": title,
            "url": f"https://guba.eastmoney.com{url}",
            "platform": "guba",
            "author": field(row, "l4") or "未知",
            "reads": field(row, "l1") or "0",
            "replies": field(row, "l2") or "0",
            "date": date_text,
            "published_at": normalize_social_datetime(date_text),
            "time_basis": "published_at",
            "list_sort": "publish_time",
            "collected_at": datetime.now().isoformat(),
        })
    return posts


def collect_all() -> Dict[str, List[Dict]]:
    """采集所有板块 — 带人类延迟防触发风控"""
    result = {}
    for sector_key, cfg in SECTORS.items():
        try:
            html = fetch_board(cfg["code"])
            posts = parse_posts(html)
            result[sector_key] = posts
            print(f"  [{cfg['name']}] 采集到 {len(posts)} 条帖子")
            # 板块之间加延迟
            _ad.sleep_like_human("scroll")
        except Exception as e:
            print(f"  [{cfg['name']}] 采集失败: {e}")
            result[sector_key] = []
    return result


if __name__ == "__main__":
    data = collect_all()
    for k, v in data.items():
        print(f"{k}: {len(v)} posts")
