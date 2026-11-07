"""市场情绪监控主流程：采集、分析、聚合、存储和输出。"""
import sys
import os
import json
import re
import uuid
from datetime import datetime, timedelta

# 确保项目根目录在 path 中
sys.path.insert(0, os.path.dirname(__file__))

from collectors.xhs_collector import collect_all as collect_xhs
from collectors.weibo_collector import collect_all as collect_weibo
from collectors.xueqiu_collector import collect_all as collect_xueqiu, xueqiu_enabled
from collectors.wechat_collector import collect_all as collect_wechat, wechat_enabled
from analyzer.llm_analyzer import analyze_all
from analyzer.index_calculator import (
    compute_sector_index, add_record, get_dashboard_data, SECTOR_NAMES
)
from storage.mysql_store import (
    mysql_enabled, persist_pipeline_run, fetch_keyword_history, fetch_model_comparison,
    fetch_latest_collection_today, filter_posts_pending_analysis, persist_standalone_analysis,
    fetch_production_summary,
)
from keyword_config import get_keywords, get_sector_catalog
from runtime_config import ini_get, ini_get_bool, ini_get_int
from post_time import normalize_social_datetime, beijing_now, report_day
from local_llm_runtime import local_llm_for_analysis

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")

SECTOR_HINTS = get_keywords()
SECTOR_NAMES.update({item["code"]: item["name"] for item in get_sector_catalog()})


def _assert_collection_role() -> None:
    role = os.environ.get("MOM_INDEX_RUNTIME_ROLE", "").strip() or ini_get("runtime", "role", "analyst")
    allowed_env = os.environ.get("MOM_INDEX_ALLOW_COLLECTION", "").strip().lower()
    allowed = allowed_env in {"1", "true", "yes", "on"} if allowed_env else ini_get_bool(
        "runtime", "allow_collection", False
    )
    if role != "collector" or not allowed:
        raise RuntimeError(
            "当前节点未授权采集。Mac mini 请在 config.ini 设置 [runtime] role=collector、"
            "allow_collection=true；MacBook 保持 role=analyst。"
        )


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
                "sentiment_label": getattr(analysis, "sentiment_label", "neutral") or "neutral",
                "emotion_tags": getattr(analysis, "emotion_tags", []) or [],
                "emotion_intensity": float(getattr(analysis, "emotion_intensity", 0) or 0),
                "sentiment_confidence": float(getattr(analysis, "sentiment_confidence", 0) or 0),
                "sentiment_source": getattr(analysis, "sentiment_source", "rules") or "rules",
                "llm_error": getattr(analysis, "llm_error", "") or "",
                "intent": getattr(analysis, "intent", "neutral") or "neutral",
                "intent_strength": float(getattr(analysis, "intent_strength", 0) or 0),
                "position_status": getattr(analysis, "position_status", "unknown") or "unknown",
                "market_outlook": getattr(analysis, "market_outlook", "unknown") or "unknown",
                "content_type": getattr(analysis, "content_type", "opinion") or "opinion",
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


def _build_current_snapshot(sector_indices: dict, now=None) -> dict:
    now = now or beijing_now()
    report_date = (now - timedelta(days=1)).date() if now.hour < 6 else now.date()
    return {
        "date": report_date.isoformat(),
        "timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
        "sectors": sector_indices,
    }


def _enabled(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _should_reuse_today_collection() -> bool:
    if _enabled("MOM_INDEX_FORCE_COLLECTION", default=False):
        return False
    value = os.environ.get("MOM_INDEX_REUSE_TODAY_COLLECTION")
    if value is not None:
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return ini_get_bool("runtime", "reuse_today_collection", True)


def _requested_platform_refreshes() -> set[str]:
    raw = os.environ.get("MOM_INDEX_REFRESH_PLATFORMS", "")
    return {item.strip().lower() for item in raw.split(",") if item.strip()}


def _refresh_platforms(all_posts: dict, platforms: set[str]) -> tuple[dict, set[str]]:
    """Refresh selected sources while retaining the rest of today's collection."""
    supported = {"weibo": collect_weibo}
    unknown = platforms - supported.keys()
    if unknown:
        raise ValueError(f"不支持单独刷新的平台: {', '.join(sorted(unknown))}")

    merged = {sector: list(posts) for sector, posts in all_posts.items()}
    refreshed = set()
    for platform in sorted(platforms):
        print(f"  [增量刷新-{platform}] 开始补抓")
        incoming = supported[platform]()
        incoming_total = sum(len(posts) for posts in incoming.values())
        if incoming_total <= 0:
            print(f"  ⚠️ [增量刷新-{platform}] 返回 0 条，保留原有数据")
            continue
        for sector in set(merged) | set(incoming):
            retained = [post for post in merged.get(sector, []) if post.get("platform") != platform]
            merged[sector] = retained + list(incoming.get(sector, []))
        refreshed.add(platform)
        print(f"  [增量刷新-{platform}] 成功补抓 {incoming_total} 条并替换旧样本")
    return merged, refreshed


def _collect_posts() -> dict:
    all_posts = {}

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

    if _enabled(
        "MOM_INDEX_ENABLE_WEIBO",
        default=ini_get_bool("weibo", "enabled", False),
    ):
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

    return all_posts


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


def _filter_recent_posts(all_posts: dict, now=None) -> tuple[dict, dict]:
    """优先使用目标自然日样本；数量不足时自动扩大到最近若干天。"""
    max_age_days = max(1, ini_get_int("recency", "max_age_days", 7))
    daily_min_posts = max(1, ini_get_int("recency", "daily_min_posts", 20))
    now = now or beijing_now()
    target_day = report_day(now, now)
    cutoff = min(now - timedelta(days=max_age_days), datetime.combine(target_day, datetime.min.time()))
    day_label = "昨日" if target_day < now.date() else "今日"
    filtered_posts = {}
    windows = {}

    for sector, posts in all_posts.items():
        kept = []
        expired = 0
        unknown_time = 0
        unknown_by_platform = {}
        expired_by_platform = {}
        for post in posts:
            collected = normalize_social_datetime(post.get("collected_at")) or now.isoformat(sep=" ")
            post["collected_at"] = collected
            collected_at = datetime.fromisoformat(collected)
            raw_time = (normalize_social_datetime(post.get("published_at"), collected_at)
                        or normalize_social_datetime(post.get("date"), collected_at))
            post["published_at"] = raw_time
            post["time_zone"] = "Asia/Shanghai"
            try:
                published_at = datetime.fromisoformat(raw_time) if raw_time else None
            except ValueError:
                published_at = None

            if published_at is None:
                unknown_time += 1
                platform = post.get("platform") or "unknown"
                unknown_by_platform[platform] = unknown_by_platform.get(platform, 0) + 1
            elif cutoff <= published_at <= now:
                kept.append(post)
            else:
                expired += 1
                platform = post.get("platform") or "unknown"
                expired_by_platform[platform] = expired_by_platform.get(platform, 0) + 1

        if expired:
            detail = ", ".join(f"{name}={count}" for name, count in sorted(expired_by_platform.items()))
            print(f"  [时效-{sector}] 去掉 {expired} 条超过 {max_age_days} 天的帖子 ({detail})")
        if unknown_time:
            detail = ", ".join(f"{name}={count}" for name, count in sorted(unknown_by_platform.items()))
            print(f"  [时效-{sector}] {unknown_time} 条未识别发布时间，排除当期分析 ({detail})")
        kept.sort(key=lambda post: post["published_at"], reverse=True)
        daily_posts = [
            post for post in kept
            if report_day(post["published_at"], now) == target_day
        ]
        use_daily = len(daily_posts) >= daily_min_posts
        selected = daily_posts if use_daily else kept
        mode = "day" if use_daily else "week"
        label = day_label if use_daily else f"近{max_age_days}天"
        filtered_posts[sector] = selected
        windows[sector] = {
            "mode": mode,
            "label": label,
            "sample_count": len(selected),
            "daily_count": len(daily_posts),
            "daily_min_posts": daily_min_posts,
            "start_at": selected[-1]["published_at"] if selected else "",
            "end_at": selected[0]["published_at"] if selected else "",
        }
        print(
            f"  [窗口-{sector}] {label}: {len(selected)} 条 "
            f"({day_label} {len(daily_posts)} 条，日阈值 {daily_min_posts})"
        )
    return filtered_posts, windows


def run_pipeline():
    """执行完整的数据采集→分析→指数计算流程"""
    _assert_collection_role()
    print("=" * 65)
    print("   市场情绪监控 · 数据采集与分析")
    print(f"   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 65)
    
    # ===== 第1步: 数据采集 =====
    print("\n📡 第1步: 数据采集")
    
    reused_collection = None
    refreshed_platforms = set()
    if _should_reuse_today_collection() and mysql_enabled():
        try:
            reused_collection = fetch_latest_collection_today()
        except Exception as e:
            print(f"  ⚠️ 检查今日采集批次失败，将执行正常采集: {e}")

    if reused_collection:
        all_posts = reused_collection["posts"]
        reused_total = sum(len(posts) for posts in all_posts.values())
        print(
            f"  ♻️ 今天已经采集过，复用 MySQL run_id={reused_collection['run_id']} "
            f"({reused_collection['run_at']}, {reused_total} 条)，跳过所有平台抓取"
        )
        requested_refreshes = _requested_platform_refreshes()
        if requested_refreshes:
            try:
                all_posts, refreshed_platforms = _refresh_platforms(all_posts, requested_refreshes)
            except Exception as e:
                print(f"  ⚠️ 单平台增量刷新失败，继续使用原批次: {e}")
    else:
        all_posts = _collect_posts()

    all_posts = _deconflict_cross_sector_posts(all_posts)
    all_posts = _dedupe_posts_by_title(all_posts)
    collection_at = (datetime.fromisoformat(reused_collection["run_at"])
                     if reused_collection and not refreshed_platforms else beijing_now())
    all_posts, analysis_windows = _filter_recent_posts(all_posts, collection_at)

    if mysql_enabled():
        from analyzer.llm_sentiment import llm_profile
        all_posts, pending_stats = filter_posts_pending_analysis(all_posts, llm_profile())
        print(
            "  [模型去重] "
            f"待分析 {pending_stats['pending']} 条；"
            f"已由当前模型分析 {pending_stats['already_analyzed']} 条；"
            f"批内重复 {pending_stats['duplicates']} 条"
        )
    
    total_collected = sum(len(v) for v in all_posts.values())
    print(f"\n  共采集 {total_collected} 条帖子\n")
    
    # ===== 第2步: LLM分析 =====
    print("🧠 第2步: LLM 多维度分析")
    if total_collected:
        with local_llm_for_analysis():
            analysis_results = analyze_all(all_posts)
    else:
        analysis_results = {sector: [] for sector in all_posts}
        print("  没有当前模型尚未分析的新帖子，跳过 LLM 启动")
    
    for sector, results in analysis_results.items():
        news_count = sum(getattr(r, "content_type", "opinion") == "news" or r.level == "资讯帖" for r in results)
        print(f"\n  [{SECTOR_NAMES.get(sector, sector)}] 共分析 {len(results)} 条，其中资讯 {news_count} 条")
    
    # ===== 第3步: 指数计算 =====
    print("\n📊 第3步: 指数计算")
    
    sector_indices = {}
    for sector, results in analysis_results.items():
        result = compute_sector_index(results)
        result.setdefault("details", {})["analysis_window"] = analysis_windows.get(sector, {})
        sector_indices[sector] = result
        name = SECTOR_NAMES.get(sector, sector)
        d = result["details"]
        print(
            f"  {name:6s} 情绪 {result['index']:+6.1f}  "
            f"[观点 {d.get('opinion_posts', 0)} / 资讯 {d.get('news_posts', 0)}]"
        )
    
    # ===== 第4步: 存储历史 =====
    print("\n💾 第4步: 存储历史记录")
    dashboard = get_dashboard_data()
    from analyzer.llm_sentiment import llm_model_name, llm_profile, llm_prompt_version
    dashboard["analysis_identity"] = {
        "profile": llm_profile(),
        "model_name": llm_model_name(),
        "prompt_version": llm_prompt_version(),
        "runtime_role": os.environ.get("MOM_INDEX_RUNTIME_ROLE", "").strip()
        or ini_get("runtime", "role", "analyst"),
    }
    dashboard["history_latest"] = dashboard.get("latest")
    dashboard["latest"] = _build_current_snapshot(sector_indices, collection_at)

    run_id = None
    if mysql_enabled():
        print("\n🗄️ 第5.5步: 写入 MySQL")
        note = f"collector:{uuid.uuid4().hex}"
        try:
            if reused_collection and not refreshed_platforms:
                run_id = reused_collection["run_id"]
                batch_id = persist_standalone_analysis(
                    analysis_results,
                    all_posts,
                    note=note,
                )
                print(f"  MySQL 分析写入成功: collection_run_id={run_id}, batch_id={batch_id}")
            else:
                run_id = persist_pipeline_run(all_posts, analysis_results, sector_indices, dashboard, note=note)
                if run_id:
                    refresh_note = f"，增量刷新 {','.join(sorted(refreshed_platforms))}" if refreshed_platforms else ""
                    print(f"  MySQL 采集与分析写入成功: run_id={run_id}{refresh_note}")
        except Exception as e:
            from storage.pending_run import save_pending_run
            path = save_pending_run(note, all_posts, analysis_results, sector_indices, dashboard,
                                    reused_collection["run_id"] if reused_collection and not refreshed_platforms else None)
            raise RuntimeError(f"MySQL 写入失败，本次不发布；恢复文件：{path}") from e

    add_record(sector_indices, analysis_results, run_at=collection_at)
    history = get_dashboard_data()
    dashboard.update({k: v for k, v in history.items() if k != "latest"})
    dashboard["history_latest"] = history.get("latest")

    # ===== 第5步: 输出前端数据 =====
    try:
        dashboard["keyword_history"] = fetch_keyword_history() if mysql_enabled() else {}
    except Exception as e:
        print(f"  ⚠️ 关键词历史加载失败: {e}")
        dashboard["keyword_history"] = {}

    post_detail_dataset = _build_post_detail_dataset(all_posts, analysis_results)
    from analyzer.platform_trends import fetch_platform_trends
    try:
        dashboard["platform_sentiment_trends"] = fetch_platform_trends()
    except Exception as exc:
        dashboard["platform_sentiment_trends"] = {"series": [], "note": "平台历史读取失败"}
        print(f"  平台情绪历史读取失败: {exc}")
    try:
        dashboard["model_comparison"] = fetch_model_comparison() if mysql_enabled() else {"profiles": []}
        dashboard["production_summary"] = fetch_production_summary() if mysql_enabled() else {}
    except Exception as exc:
        dashboard["model_comparison"] = {"profiles": [], "note": str(exc)}
        print(f"  模型结果读取失败: {exc}")

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


if __name__ == "__main__":
    run_pipeline()
