"""Market sentiment aggregation by sector and platform."""
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


def _compute_llm_profile(posts: List) -> Dict:
    llm_posts = [
        r for r in posts
        if getattr(r, "sentiment_source", "rules") == "llm"
        and getattr(r, "content_type", "opinion") == "opinion"
        and getattr(r, "level", "") not in {"垃圾帖", "资讯帖"}
    ]
    sentiment = {name: sum(getattr(r, "sentiment_label", "neutral") == name for r in llm_posts)
                 for name in ("fear", "greed", "neutral", "mixed")}
    position = {name: sum(getattr(r, "position_status", "unknown") == name for r in llm_posts)
                for name in ("none", "holding", "trapped", "exited", "unknown")}
    outlook = {name: sum(getattr(r, "market_outlook", "unknown") == name for r in llm_posts)
               for name in ("bullish", "bearish", "sideways", "unknown")}
    weight = sum(max(float(getattr(r, "sentiment_confidence", 0) or 0), 0.1) for r in llm_posts)
    sentiment_index = round(sum(
        float(getattr(r, "sentiment_score", 0) or 0) * max(float(getattr(r, "sentiment_confidence", 0) or 0), 0.1)
        for r in llm_posts
    ) / weight * 100, 1) if weight else 0.0
    known_positions = sum(position[name] for name in ("none", "holding", "trapped", "exited"))
    known_outlooks = sum(outlook[name] for name in ("bullish", "bearish", "sideways"))
    ratio = lambda count, total: round(count / max(total, 1) * 100, 1)
    return {
        "analyzed_posts": len(llm_posts), "coverage_ratio": ratio(len(llm_posts), len(posts)),
        "sentiment": sentiment, "position": position, "outlook": outlook,
        "market_sentiment_index": sentiment_index,
        "outlook_index": round((outlook["bullish"] - outlook["bearish"]) / max(known_outlooks, 1) * 100, 1),
        "known_position_posts": known_positions, "known_outlook_posts": known_outlooks,
        "holding_ratio": ratio(position["holding"], known_positions),
        "trapped_ratio": ratio(position["trapped"], known_positions),
        "none_ratio": ratio(position["none"], known_positions),
        "exited_ratio": ratio(position["exited"], known_positions),
        "bullish_ratio": ratio(outlook["bullish"], known_outlooks),
        "bearish_ratio": ratio(outlook["bearish"], known_outlooks),
        "has_sentiment_data": bool(llm_posts),
        "has_position_data": known_positions > 0,
        "has_outlook_data": known_outlooks > 0,
    }


def compute_sector_index(analysis_results: List) -> Dict:
    """Compute the directional market sentiment index (-100 to +100)."""
    if not analysis_results:
        return {
            "index": 0, 
            "interpretation": "无数据",
            "details": {}
        }
    
    total = len(analysis_results)

    spam_posts = [r for r in analysis_results if getattr(r, "content_type", "") == "spam" or r.level == "垃圾帖"]
    news_posts = [r for r in analysis_results if getattr(r, "content_type", "") == "news" or r.level == "资讯帖"]
    valid_posts = [r for r in analysis_results if r not in spam_posts and r not in news_posts]
    source_counts = {}
    by_platform = {}
    for item in valid_posts:
        platform = getattr(item, "platform", "") or "unknown"
        source_counts[platform] = source_counts.get(platform, 0) + 1
        by_platform.setdefault(platform, []).append(item)

    llm_profile = _compute_llm_profile(valid_posts)

    index = llm_profile["market_sentiment_index"]

    platform_breakdown = {}
    for platform, items in sorted(by_platform.items()):
        profile = _compute_llm_profile(items)
        platform_breakdown[platform] = {
            "index": profile["market_sentiment_index"],
            "valid_posts": len(items),
            "llm_profile": profile,
        }
    platform_values = [v["index"] for v in platform_breakdown.values() if v["llm_profile"]["has_sentiment_data"]]
    platform_divergence = round(max(platform_values) - min(platform_values), 1) if len(platform_values) >= 2 else 0.0
    
    return {
        "index": index,
        "interpretation": interpret_sentiment(index, llm_profile["has_sentiment_data"]),
        "details": {
            "total_posts": total,
            "valid_posts": len(valid_posts),
            "opinion_posts": len(valid_posts),
            "news_posts": len(news_posts),
            "spam_posts": len(spam_posts),
            "index_method": "llm-market-sentiment-v1",
            "source_counts": source_counts,
            "platform_breakdown": platform_breakdown,
            "platform_divergence": platform_divergence,
            "llm_profile": llm_profile,
        },
    }


def interpret_sentiment(index: float, has_data: bool = True) -> str:
    if not has_data:
        return "观点样本不足"
    if index >= 50:
        return "明显贪婪"
    if index >= 15:
        return "偏贪婪"
    if index <= -50:
        return "明显恐慌"
    if index <= -15:
        return "偏恐慌"
    return "情绪中性"


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

    # 独立的新手参与热度，不混入市场情绪方向。
    newbie_participation_index = round(min(100, (
        newbie_ratio * 0.50 +
        avg_newbie_score * 0.30 +
        purity_signal * 0.20
    )), 1)

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
        "newbie_participation_index": newbie_participation_index,
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
            if (
                sector in sector_history
                and data.get("details", {}).get("index_method") == "llm-market-sentiment-v1"
            ):
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
