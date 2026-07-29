"""
宝妈指数计算引擎
各个板块独立计算，各自有完整的历史曲线
"""
from datetime import datetime, date
from typing import Dict, List
import json
import os

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")

SECTOR_NAMES = {
    "nasdaq": "纳斯达克",
    "gold": "黄金",
    "cpo": "CPO通信",
    "semiconductor": "半导体",
    "storage": "存储",
}


def compute_sector_index(analysis_results: List) -> Dict:
    """
    计算单个板块的宝妈指数 (0-100)
    
    四个维度:
    1. 小白占比 (40%) — 该板块中小白帖的比例
    2. 小白强度 (25%) — 小白帖的平均得分
    3. 情绪极端度 (20%) — 贪婪/恐慌的情绪极端程度
    4. 热度信号 (15%) — 该板块的讨论活跃度
    """
    if not analysis_results:
        return {
            "index": 0, 
            "interpretation": "无数据",
            "details": {}
        }
    
    total = len(analysis_results)

    # 过滤掉垃圾帖/纯资讯梳理帖
    excluded_levels = {"垃圾帖", "资讯帖"}
    valid_posts = [r for r in analysis_results if r.level not in excluded_levels]
    spam_count = total - len(valid_posts)
    source_counts = {}
    by_platform = {}
    for item in valid_posts:
        platform = getattr(item, "platform", "") or "unknown"
        source_counts[platform] = source_counts.get(platform, 0) + 1
        by_platform.setdefault(platform, []).append(item)

    summary = _compute_summary_metrics(valid_posts)
    index = summary["index"]
    newbie_posts = summary["newbie_posts"]
    avg_newbie_score = summary["avg_newbie_score"]
    avg_sentiment = summary["avg_sentiment"]
    purity_signal = summary["purity_signal"]
    mom_buy_index = summary["mom_buy_index"]
    mom_sell_index = summary["mom_sell_index"]
    buy_sell_ratio = summary["buy_sell_ratio"]

    platform_breakdown = {}
    platform_indices = []
    for platform, items in sorted(by_platform.items()):
        platform_summary = _compute_summary_metrics(items)
        platform_breakdown[platform] = {
            "index": platform_summary["index"],
            "valid_posts": platform_summary["valid_posts"],
            "newbie_posts": platform_summary["newbie_count"],
            "newbie_ratio": platform_summary["newbie_ratio"],
            "avg_newbie_score": platform_summary["avg_newbie_score"],
            "avg_sentiment": platform_summary["avg_sentiment"],
            "mom_buy_index": platform_summary["mom_buy_index"],
            "mom_sell_index": platform_summary["mom_sell_index"],
            "buy_sell_ratio": platform_summary["buy_sell_ratio"],
        }
        platform_indices.append(platform_summary["index"])

    platform_divergence = round(max(platform_indices) - min(platform_indices), 1) if len(platform_indices) >= 2 else 0.0
    
    return {
        "index": index,
        "interpretation": interpret_index(index),
        "details": {
            "total_posts": total,
            "valid_posts": len(valid_posts),
            "spam_posts": spam_count,
            "newbie_posts": summary["newbie_count"],
            "pure_newbie": summary["pure_newbie_count"],
            "newbie_ratio": summary["newbie_ratio"],
            "avg_newbie_score": round(avg_newbie_score, 1),
            "avg_sentiment": round(avg_sentiment, 1),
            "purity_signal": round(purity_signal, 1),
            "activity": round(summary["activity_signal"], 1),
            # 买入/卖出子指数
            "mom_buy_index": mom_buy_index,
            "mom_sell_index": mom_sell_index,
            "buy_sell_ratio": buy_sell_ratio,
            "buy_count": summary["buy_count"],
            "sell_count": summary["sell_count"],
            "source_counts": source_counts,
            "platform_breakdown": platform_breakdown,
            "platform_divergence": platform_divergence,
        },
        "top_newbie_posts": [
            {
                "title": r.title[:60],
                "score": r.newbie_score,
                "level": r.level,
                "reasoning": r.reasoning[:150],
                "sentiment": r.sentiment_score,
                "intent": r.intent,
                "platform": getattr(r, "platform", ""),
                "intent_label": {"buy": "🟢 买入", "sell": "🔴 卖出", "neutral": "⚪ 观望"}.get(r.intent, ""),
                "key_signals": r.key_signals[:2],
            }
            for r in sorted(newbie_posts, key=lambda x: x.newbie_score, reverse=True)[:5]
        ],
    }


def _compute_summary_metrics(valid_posts: List) -> Dict:
    newbie_posts = [r for r in valid_posts if r.newbie_score >= 20]
    pure_newbie = [r for r in valid_posts if r.newbie_score >= 50]
    newbie_count = len(newbie_posts)
    valid_count = len(valid_posts)

    newbie_ratio = (newbie_count / valid_count) * 100 if valid_count else 0
    avg_newbie_score = sum(r.newbie_score for r in newbie_posts) / max(newbie_count, 1)
    sentiments = [abs(r.sentiment_score) for r in newbie_posts]
    avg_sentiment = sum(sentiments) / max(len(sentiments), 1) * 100
    purity_signal = (len(pure_newbie) / max(newbie_count, 1)) * 100 if newbie_count > 0 else 0
    activity_signal = min(100, valid_count / 80 * 100)

    index = (
        newbie_ratio * 0.40 +
        avg_newbie_score * 0.25 +
        avg_sentiment * 0.20 +
        purity_signal * 0.15
    )
    index = round(min(100, index), 1)

    newbie_buy = [r for r in newbie_posts if r.intent == "buy"]
    newbie_sell = [r for r in newbie_posts if r.intent == "sell"]
    buy_ratio = len(newbie_buy) / max(newbie_count, 1)
    sell_ratio = len(newbie_sell) / max(newbie_count, 1)
    buy_intensity = sum(r.intent_strength for r in newbie_buy) / max(len(newbie_buy), 1)
    sell_intensity = sum(r.intent_strength for r in newbie_sell) / max(len(newbie_sell), 1)

    mom_buy_index = round(min(100, (
        buy_ratio * 100 * 0.50 +
        (avg_newbie_score / 100) * buy_ratio * 30 * 0.30 +
        buy_intensity * 100 * 0.20
    )), 1)
    mom_sell_index = round(min(100, (
        sell_ratio * 100 * 0.50 +
        (avg_newbie_score / 100) * sell_ratio * 30 * 0.30 +
        sell_intensity * 100 * 0.20
    )), 1)
    buy_sell_ratio = round(len(newbie_buy) / max(len(newbie_sell), 1), 1)

    return {
        "valid_posts": valid_count,
        "newbie_posts": newbie_posts,
        "newbie_count": newbie_count,
        "pure_newbie_count": len(pure_newbie),
        "newbie_ratio": round(newbie_ratio, 1),
        "avg_newbie_score": avg_newbie_score,
        "avg_sentiment": avg_sentiment,
        "purity_signal": purity_signal,
        "activity_signal": activity_signal,
        "index": index,
        "mom_buy_index": mom_buy_index,
        "mom_sell_index": mom_sell_index,
        "buy_sell_ratio": buy_sell_ratio,
        "buy_count": len(newbie_buy),
        "sell_count": len(newbie_sell),
    }


def interpret_index(index: float) -> str:
    if index >= 75:
        return "🔴 极度狂热 — 擦鞋童时刻！小白情绪爆表，历史级别的危险信号"
    elif index >= 60:
        return "🟠 高度警惕 — 小白大量涌入，市场情绪过热，建议大幅减仓"
    elif index >= 40:
        return "🟡 开始升温 — 小白活跃度明显上升，需保持关注"
    elif index >= 20:
        return "🟢 正常区间 — 小白参与度适中，无需特别操作"
    else:
        return "🔵 极度冷清 — 小白沉默不语，可能是市场底部信号"


def load_history() -> Dict:
    """加载历史数据"""
    history_file = os.path.join(DATA_DIR, "history.json")
    if os.path.exists(history_file):
        with open(history_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {"records": []}


def save_history(history: Dict):
    """保存历史数据"""
    history_file = os.path.join(DATA_DIR, "history.json")
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(history_file, 'w', encoding='utf-8') as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def _merge_sector_maps(base: Dict, incoming: Dict) -> Dict:
    merged = dict(base or {})
    for sector, payload in (incoming or {}).items():
        merged[sector] = payload
    return merged


def _normalize_history_records(records: List[Dict]) -> List[Dict]:
    """
    兼容旧历史文件里“同一天多条记录”的情况，压成每天一条。
    同日多条时，按时间顺序后写覆盖前写，但保留所有板块。
    """
    grouped: Dict[str, Dict] = {}
    for record in records:
        day = record.get("date")
        if not day:
            continue
        existing = grouped.get(day)
        if not existing:
            grouped[day] = {
                "date": day,
                "timestamp": record.get("timestamp", f"{day} 00:00:00"),
                "sectors": dict(record.get("sectors", {})),
            }
            continue

        existing["sectors"] = _merge_sector_maps(existing.get("sectors", {}), record.get("sectors", {}))
        incoming_ts = record.get("timestamp", f"{day} 00:00:00")
        if incoming_ts >= existing.get("timestamp", f"{day} 00:00:00"):
            existing["timestamp"] = incoming_ts

    normalized = list(grouped.values())
    normalized.sort(key=lambda r: (r.get("date", ""), r.get("timestamp", "")))
    return normalized


def _merge_record_by_date(history: Dict, record: Dict):
    history["records"] = _normalize_history_records(history.get("records", []) + [record])


def _build_daily_records(analysis_results: Dict[str, List]) -> List[Dict]:
    grouped: Dict[str, Dict[str, List]] = {}
    for sector, results in analysis_results.items():
        for item in results:
            day = item.source_date or datetime.now().strftime("%Y-%m-%d")
            grouped.setdefault(day, {}).setdefault(sector, []).append(item)

    records = []
    for day, sector_map in grouped.items():
        sectors = {}
        for sector, results in sector_map.items():
            sectors[sector] = compute_sector_index(results)
        records.append(
            {
                "date": day,
                "timestamp": max(
                    (
                        item.source_datetime
                        for results in sector_map.values()
                        for item in results
                        if item.source_datetime
                    ),
                    default=f"{day} 00:00:00",
                ),
                "sectors": sectors,
            }
        )
    records.sort(key=lambda r: r["date"])
    return records


def add_record(sector_indices: Dict[str, Dict], analysis_results: Dict):
    """按帖子日期聚合，添加多条历史记录。"""
    history = load_history()

    records = _build_daily_records(analysis_results)
    if not records:
        records = [
            {
                "date": datetime.now().strftime("%Y-%m-%d"),
                "timestamp": datetime.now().isoformat(),
                "sectors": sector_indices,
            }
        ]

    for record in records:
        _merge_record_by_date(history, record)

    history["records"].sort(key=lambda r: r["date"])
    save_history(history)


def get_dashboard_data() -> Dict:
    """获取前端所需的完整数据"""
    history = load_history()
    records = _normalize_history_records(history.get("records", []))
    
    # 最新一条
    latest = records[-1] if records else None
    
    # 为每个板块准备历史曲线数据
    sector_history = {
        "nasdaq": [],
        "gold": [],
        "cpo": [],
        "semiconductor": [],
        "storage": [],
    }
    
    for r in records:
        for sector, data in r.get("sectors", {}).items():
            if sector in sector_history:
                sector_history[sector].append({
                    "date": r["date"],
                    "index": data["index"],
                })

    latest_changes = {}
    for sector, points in sector_history.items():
        if not points:
            continue
        current = points[-1]
        previous = points[-2] if len(points) >= 2 else None
        delta = round(current["index"] - previous["index"], 1) if previous else None
        latest_changes[sector] = {
            "date": current["date"],
            "previous_date": previous["date"] if previous else None,
            "delta": delta,
            "points": len(points),
        }
    
    return {
        "latest": latest,
        "sector_history": sector_history,
        "record_count": len(records),
        "latest_changes": latest_changes,
    }
