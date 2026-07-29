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
    """获取股吧页面HTML — 使用反检测请求头"""
    url = f"https://guba.eastmoney.com/list,{code}.html"
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
    """解析帖子列表"""
    title_pattern = re.compile(
        r'<a[^>]*href="(/news,[^"]*)"[^>]*title="([^"]*)"[^>]*>',
        re.DOTALL
    )
    read_pattern = re.compile(r'<cite[^>]*class="[^"]*l1[^"]*"[^>]*>(.*?)</cite>', re.DOTALL)
    reply_pattern = re.compile(r'<cite[^>]*class="[^"]*l2[^"]*"[^>]*>(.*?)</cite>', re.DOTALL)
    author_pattern = re.compile(r'<cite[^>]*class="[^"]*l4[^"]*"[^>]*>.*?<a[^>]*>(.*?)</a>', re.DOTALL)
    date_pattern = re.compile(r'<cite[^>]*class="[^"]*l5[^"]*"[^>]*>(.*?)</cite>', re.DOTALL)

    titles = title_pattern.findall(html_content)
    reads = read_pattern.findall(html_content)
    replies = reply_pattern.findall(html_content)
    authors = author_pattern.findall(html_content)
    dates = date_pattern.findall(html_content)

    posts = []
    for i, (url, title) in enumerate(titles):
        title = html_mod.unescape(title.strip())
        if not title or title == '点击开始搜索':
            continue
        posts.append({
            "id": f"guba_{url.split(',')[-1].replace('.html','')}",
            "title": title,
            "url": f"https://guba.eastmoney.com{url}",
            "platform": "guba",
            "author": authors[i].strip() if i < len(authors) else "未知",
            "reads": reads[i].strip() if i < len(reads) else "0",
            "replies": replies[i].strip() if i < len(replies) else "0",
            "date": dates[i].strip() if i < len(dates) else "未知",
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
