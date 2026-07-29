"""
宝妈指数 — 主流程
采集 → 分析 → 计算 → 存储 → 输出
"""
import sys
import os
import json
import re
from datetime import datetime

# 确保项目根目录在 path 中
sys.path.insert(0, os.path.dirname(__file__))

from collectors.guba_collector import collect_all as collect_guba
from collectors.xhs_collector import collect_all as collect_xhs
from collectors.weibo_collector import collect_all as collect_weibo
from collectors.xueqiu_collector import collect_all as collect_xueqiu, xueqiu_enabled
from collectors.wechat_collector import collect_all as collect_wechat, wechat_enabled
from analyzer.llm_analyzer import analyze_all
from analyzer.index_calculator import (
    compute_sector_index, add_record, get_dashboard_data, SECTOR_NAMES
)
from storage.mysql_store import mysql_enabled, persist_pipeline_run, fetch_keyword_history

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")

SECTOR_HINTS = {
    "nasdaq": ["纳指", "纳斯达克", "美股", "标普", "美债", "科技股", "英伟达", "特斯拉", "苹果"],
    "gold": ["黄金", "金价", "金条", "金豆", "纸黄金", "黄金etf", "现货黄金"],
    "cpo": ["cpo", "光模块", "通信", "算力", "800g", "交换机", "服务器", "铜缆"],
    "semiconductor": ["半导体", "芯片", "晶圆", "封测", "gpu", "cpu", "eda", "设备材料"],
    "storage": ["存储", "存储芯片", "dram", "nand", "hbm", "海力士", "sk海力士", "美光", "三星存储", "长鑫存储", "兆易创新", "西部数据", "闪迪"],
}


def _build_post_detail_dataset(all_posts: dict, analysis_results: dict) -> dict:
    analysis_lookup = {}
    for sector, results in analysis_results.items():
        for item in results:
            analysis_lookup[(sector, str(item.post_id))] = item

    items = []
    for sector, posts in all_posts.items():
        for post in posts:
            post_id = str(post.get("id", "") or "")
            analysis = analysis_lookup.get((sector, post_id))
            title = post.get("title", "") or ""
            content = post.get("content", "") or ""
            title_compact = re.sub(r"\s+", "", title)
            content_compact = re.sub(r"\s+", "", content)
            content_equals_title = bool(content_compact) and title_compact == content_compact
            content_scope = "full"
            if not content:
                content_scope = "title_only"
            elif content_equals_title:
                content_scope = "same_as_title"
            elif len(content) < 140:
                content_scope = "excerpt"

            if content_scope == "full":
                content_note = ""
            elif content_scope == "title_only":
                content_note = "当前只抓到标题，未拿到正文；可点标题跳转原帖查看。"
            elif content_scope == "same_as_title":
                content_note = "当前抓到的正文与标题相同，通常说明来源页只提供摘要；可点标题跳转原帖查看。"
            else:
                content_note = "当前展示的是列表摘要，不一定是原帖全文；可点标题跳转原帖查看。"
            items.append({
                "sector": sector,
                "platform": post.get("platform", "") or "unknown",
                "source_mode": post.get("source_mode", "") or "",
                "post_id": post_id,
                "title": title,
                "content": content,
                "url": post.get("url", "") or "",
                "author": post.get("author", "") or "",
                "date": post.get("date", "") or "",
                "published_at": post.get("published_at", "") or "",
                "collected_at": post.get("collected_at", "") or "",
                "keyword": post.get("keyword", "") or "",
                "is_mock": bool(post.get("is_mock", False)),
                "content_scope": content_scope,
                "content_note": content_note,
                "content_equals_title": content_equals_title,
                "content_length": len(content),
                "newbie_score": float(getattr(analysis, "newbie_score", 0) or 0),
                "newbie_confidence": getattr(analysis, "newbie_confidence", "low") or "low",
                "level": getattr(analysis, "level", "") or "",
                "sentiment_score": float(getattr(analysis, "sentiment_score", 0) or 0),
                "intent": getattr(analysis, "intent", "neutral") or "neutral",
                "intent_strength": float(getattr(analysis, "intent_strength", 0) or 0),
                "reasoning": getattr(analysis, "reasoning", "") or "",
                "key_signals": list(getattr(analysis, "key_signals", []) or []),
                "source_date": getattr(analysis, "source_date", "") or "",
                "source_datetime": getattr(analysis, "source_datetime", "") or "",
            })

    items.sort(
        key=lambda item: (
            item.get("source_datetime") or item.get("published_at") or item.get("collected_at") or "",
            item.get("platform") or "",
            item.get("title") or "",
        ),
        reverse=True,
    )

    counts = {}
    for item in items:
        platform = item["platform"]
        counts[platform] = counts.get(platform, 0) + 1

    return {
        "generated_at": datetime.now().isoformat(),
        "total_posts": len(items),
        "platform_counts": counts,
        "items": items,
    }


def _build_current_snapshot(sector_indices: dict) -> dict:
    return {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "sectors": sector_indices,
    }


def _enabled(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _normalize_post_title(title: str) -> str:
    title = re.sub(r"\s+", "", title or "")
    return title[:160].lower()


def _dedupe_posts_by_title(all_posts: dict) -> dict:
    """
    跨来源去重，压掉新闻搬运/同题复读。
    只按较长标题去重，避免误伤真正的短帖。
    """
    deduped = {}
    for sector, posts in all_posts.items():
        seen_titles = set()
        unique_posts = []
        dropped = 0
        for post in posts:
            title_key = _normalize_post_title(post.get("title", ""))
            if len(title_key) >= 18 and title_key in seen_titles:
                dropped += 1
                continue
            if len(title_key) >= 18:
                seen_titles.add(title_key)
            unique_posts.append(post)
        deduped[sector] = unique_posts
        if dropped > 0:
            print(f"  [去重-{sector}] 去掉 {dropped} 条重复标题")
    return deduped


def _sector_match_score(text: str, sector: str) -> int:
    normalized = (text or "").lower()
    score = 0
    for hint in SECTOR_HINTS.get(sector, []):
        hint_norm = hint.lower()
        if hint_norm in normalized:
            score += 3 if len(hint_norm) >= 4 else 1
    return score


def _deconflict_cross_sector_posts(all_posts: dict) -> dict:
    """
    同一标题如果落入多个板块，按标题/正文命中的行业词强弱只保留到最相关板块。
    """
    title_to_entries = {}
    for sector, posts in all_posts.items():
        for post in posts:
            title_key = _normalize_post_title(post.get("title", ""))
            if len(title_key) < 6:
                continue
            title_to_entries.setdefault(title_key, []).append((sector, post))

    removals = {sector: set() for sector in all_posts}
    for entries in title_to_entries.values():
        sectors = {sector for sector, _ in entries}
        if len(sectors) <= 1:
            continue

        scored = []
        for sector, post in entries:
            text = f"{post.get('title', '')} {post.get('content', '')}"
            score = _sector_match_score(text, sector)
            scored.append((score, sector, post))

        scored.sort(key=lambda item: (item[0], len(item[1])), reverse=True)
        keep_score, keep_sector, keep_post = scored[0]

        # 如果所有板块都没有明显行业命中，就保持原状，避免误杀。
        if keep_score <= 0:
            continue

        keep_title = keep_post.get("title", "")
        for score, sector, post in scored[1:]:
            if post.get("title") == keep_title:
                removals[sector].add(id(post))

    result = {}
    for sector, posts in all_posts.items():
        filtered = [post for post in posts if id(post) not in removals.get(sector, set())]
        dropped = len(posts) - len(filtered)
        if dropped > 0:
            print(f"  [分流-{sector}] 挪走 {dropped} 条跨板块帖子")
        result[sector] = filtered
    return result


def run_pipeline():
    """执行完整的数据采集→分析→指数计算流程"""
    print("=" * 65)
    print("   👩‍👧 宝妈指数 · 数据采集与分析")
    print(f"   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 65)
    
    # ===== 第1步: 数据采集 =====
    print("\n📡 第1步: 数据采集")
    
    all_posts = {}
    
    # 东方财富股吧
    print("  [东方财富股吧]")
    guba_data = collect_guba()
    for sector, posts in guba_data.items():
        all_posts[sector] = all_posts.get(sector, []) + posts
    
    # 小红书 (如果有API Key)
    print("  [小红书]")
    try:
        xhs_data = collect_xhs()
        xhs_total = sum(len(posts) for posts in xhs_data.values())
        xhs_mock_count = sum(
            1 for posts in xhs_data.values() for post in posts
            if post.get("platform") == "xiaohongshu" and post.get("is_mock")
        )
        if xhs_total > 0:
            if xhs_mock_count == xhs_total:
                print(f"  ⚠️ 小红书本轮为模拟数据，不写入 MySQL ({xhs_total} 条)")
            elif xhs_mock_count > 0:
                print(f"  ⚠️ 小红书含部分模拟数据，仅真实数据会写入 MySQL ({xhs_total - xhs_mock_count}/{xhs_total} 条真实)")
        for sector, posts in xhs_data.items():
            all_posts[sector] = all_posts.get(sector, []) + posts
    except Exception as e:
        print(f"  小红书采集跳过: {e}")

    if _enabled("MOM_INDEX_ENABLE_WEIBO", default=False):
        print("  [微博]")
        try:
            weibo_data = collect_weibo()
            for sector, posts in weibo_data.items():
                all_posts[sector] = all_posts.get(sector, []) + posts
        except Exception as e:
            print(f"  微博采集跳过: {e}")

    if xueqiu_enabled():
        print("  [雪球]")
        try:
            xueqiu_data = collect_xueqiu()
            for sector, posts in xueqiu_data.items():
                all_posts[sector] = all_posts.get(sector, []) + posts
        except Exception as e:
            print(f"  雪球采集跳过: {e}")

    if wechat_enabled():
        print("  [微信群聊 · 本地导出]")
        try:
            wechat_data = collect_wechat()
            for sector, posts in wechat_data.items():
                all_posts[sector] = all_posts.get(sector, []) + posts
        except Exception as e:
            print(f"  微信群聊导入跳过: {e}")

    all_posts = _deconflict_cross_sector_posts(all_posts)
    all_posts = _dedupe_posts_by_title(all_posts)
    
    total_collected = sum(len(v) for v in all_posts.values())
    print(f"\n  共采集 {total_collected} 条帖子\n")
    
    # ===== 第2步: LLM分析 =====
    print("🧠 第2步: LLM 多维度分析")
    analysis_results = analyze_all(all_posts)
    
    # 打印每个板块的 top 小白帖
    for sector, results in analysis_results.items():
        top_newbie = [r for r in results if r.newbie_score >= 30][:3]
        print(f"\n  [{SECTOR_NAMES.get(sector, sector)}] 共分析 {len(results)} 条")
        if top_newbie:
            print(f"  🔥 典型小白帖:")
            for r in top_newbie:
                print(f"     [{r.level} {r.newbie_score}分] {r.title[:50]}...")
    
    # ===== 第3步: 指数计算 =====
    print("\n📊 第3步: 指数计算")
    
    sector_indices = {}
    for sector, results in analysis_results.items():
        result = compute_sector_index(results)
        sector_indices[sector] = result
        name = SECTOR_NAMES.get(sector, sector)
        d = result["details"]
        bar = "█" * int(result["index"] / 5) + "░" * (20 - int(result["index"] / 5))
        print(f"  {name:6s} {bar} {result['index']:5.1f}  [{d['newbie_posts']}/{d['total_posts']}小白, {d['newbie_ratio']}%]")
    
    # ===== 第4步: 存储历史 =====
    print("\n💾 第4步: 存储历史记录")
    add_record(sector_indices, analysis_results)
    
    dashboard = get_dashboard_data()
    dashboard["history_latest"] = dashboard.get("latest")
    dashboard["latest"] = _build_current_snapshot(sector_indices)

    run_id = None
    if mysql_enabled():
        print("\n🗄️ 第5.5步: 写入 MySQL")
        try:
            run_id = persist_pipeline_run(all_posts, analysis_results, sector_indices, dashboard)
            if run_id:
                print(f"  MySQL 写入成功: run_id={run_id}")
        except Exception as e:
            print(f"  MySQL 写入跳过: {e}")

    # ===== 第5步: 输出前端数据 =====
    try:
        dashboard["keyword_history"] = fetch_keyword_history() if mysql_enabled() else {}
    except Exception as e:
        print(f"  ⚠️ 关键词历史加载失败: {e}")
        dashboard["keyword_history"] = {}

    post_detail_dataset = _build_post_detail_dataset(all_posts, analysis_results)

    os.makedirs(DATA_DIR, exist_ok=True)
    dashboard_file = os.path.join(DATA_DIR, "dashboard_data.json")
    with open(dashboard_file, 'w', encoding='utf-8') as f:
        json.dump(dashboard, f, ensure_ascii=False, indent=2)
    post_detail_file = os.path.join(DATA_DIR, "platform_posts.json")
    with open(post_detail_file, 'w', encoding='utf-8') as f:
        json.dump(post_detail_dataset, f, ensure_ascii=False, indent=2)
    print(f"  数据已保存: {dashboard_file}")
    
    # 同步到 frontend/data/（前端服务器从这里读取）
    frontend_data_dir = os.path.join(os.path.dirname(__file__), "frontend", "data")
    os.makedirs(frontend_data_dir, exist_ok=True)
    for fname in ["dashboard_data.json", "history.json", "xhs_posts.json", "weibo_posts.json", "xueqiu_posts.json", "platform_posts.json"]:
        src = os.path.join(DATA_DIR, fname)
        dst = os.path.join(frontend_data_dir, fname)
        if os.path.exists(src):
            import shutil
            shutil.copy2(src, dst)
    print(f"  已同步到: {frontend_data_dir}")

    # ===== 总结 =====
    print("\n" + "=" * 65)
    print("   ✅ 分析完成!")
    print(f"   历史记录: {dashboard['record_count']} 条")
    if dashboard["latest"]:
        for sector, data in dashboard["latest"]["sectors"].items():
            name = SECTOR_NAMES.get(sector, sector)
            print(f"   {name}: {data['index']} — {data['interpretation']}")
    print("=" * 65)
    
    return dashboard


def generate_sample_history(days: int = 30):
    """生成模拟历史数据（用于展示前端曲线效果）"""
    import random
    import math
    
    history = {"records": []}
    base_indices = {
        "nasdaq": 35,
        "gold": 28,
        "cpo": 22,
        "semiconductor": 30,
        "storage": 32,
    }
    
    today = datetime.now()
    for i in range(days, 0, -1):
        d = today.replace(day=min(today.day, 28))  # 简化
        d = d.replace(day=max(1, d.day - i))
        record = {"date": d.strftime("%Y-%m-%d"), "sectors": {}}
        
        for sector, base in base_indices.items():
            # 模拟波动：当前趋势 + 随机噪音
            trend = 15 * math.sin(i / 10.0)  # 周期性波动
            noise = random.uniform(-8, 8)
            idx = round(base + trend + noise, 1)
            idx = max(0, min(100, idx))
            
            record["sectors"][sector] = {
                "index": idx,
                "interpretation": interpret(idx),
                "details": {
                    "total_posts": random.randint(60, 85),
                    "valid_posts": random.randint(55, 80),
                    "spam_posts": random.randint(0, 5),
                    "newbie_posts": random.randint(3, 25),
                    "pure_newbie": random.randint(0, 5),
                    "newbie_ratio": round(random.uniform(5, 35), 1),
                    "avg_newbie_score": round(random.uniform(20, 50), 1),
                    "avg_sentiment": round(random.uniform(20, 80), 1),
                    "purity_signal": round(random.uniform(10, 60), 1),
                    "activity": round(random.uniform(60, 100), 1),
                },
                "top_newbie_posts": [],
            }
        
        history["records"].append(record)
    
    history["records"].sort(key=lambda r: r["date"])
    return history


def interpret(idx):
    if idx >= 75: return "🔴 极度狂热"
    if idx >= 60: return "🟠 高度警惕"
    if idx >= 40: return "🟡 开始升温"
    if idx >= 20: return "🟢 正常区间"
    return "🔵 极度冷清"


if __name__ == "__main__":
    # 先跑真实采集
    dashboard = run_pipeline()
    
    # 如果历史数据不够，补充模拟数据
    if dashboard["record_count"] < 5:
        print("\n📝 历史数据不足，生成30天模拟数据用于前端展示...")
        sample = generate_sample_history(30)
        from analyzer.index_calculator import save_history
        # 合并：保留真实数据，补充模拟历史
        existing = dashboard.get("sector_history", {})
        real_dates = set()
        if dashboard["latest"]:
            real_dates.add(dashboard["latest"]["date"])
        
        history = {"records": []}
        for r in sample["records"]:
            if r["date"] not in real_dates:
                history["records"].append(r)
        # 再加回真实记录
        history["records"].extend([
            r for r in sample["records"] if r["date"] in real_dates
        ])
        history["records"].sort(key=lambda r: r["date"])
        save_history(history)
        print(f"  已生成 {len(history['records'])} 天历史数据")
