#!/usr/bin/env python3
"""MacBook on-demand analysis: reads MySQL, runs the configured model, writes a separate batch."""
import argparse
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


def main() -> None:
    parser = argparse.ArgumentParser(description="只分析 MySQL 已采集帖子，不执行任何抓取")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--reanalyze-all", action="store_true", help="重新分析窗口内全部帖子")
    args = parser.parse_args()

    os.environ.setdefault("MOM_INDEX_RUNTIME_ROLE", "analyst")
    os.environ.setdefault("MOM_INDEX_LLM_PROFILE", "macbook-27b")
    os.environ.setdefault("MOM_INDEX_LLM_MODE", "all")
    os.environ.setdefault("MOM_INDEX_LLM_MAX_POSTS_PER_RUN", str(max(1, args.limit)))

    from analyzer.llm_analyzer import analyze_all
    from analyzer.llm_sentiment import llm_model_name, llm_profile, llm_prompt_version, llm_ready
    from analyzer.platform_trends import fetch_platform_trends
    from storage.mysql_store import fetch_model_comparison, fetch_posts_for_analysis, persist_standalone_analysis

    if not llm_ready():
        raise SystemExit("27B LLM 未就绪，请检查 config.ini 或 MOM_INDEX_LLM_* / QWEN_BASE_URL")
    profile = llm_profile()
    posts = fetch_posts_for_analysis(
        args.days,
        args.limit,
        analysis_profile="" if args.reanalyze_all else profile,
        prompt_version="" if args.reanalyze_all else llm_prompt_version(),
    )
    total = sum(len(items) for items in posts.values())
    mode = "全部重算" if args.reanalyze_all else "仅补未分析"
    print(f"读取最近 {args.days} 天去重帖子 {total} 条；{mode}；profile={profile} model={llm_model_name()}")
    if not total:
        print("没有待补的帖子，本次任务正常结束。")
        return
    results = analyze_all(posts)
    batch_id = persist_standalone_analysis(results, posts, note=f"MacBook on-demand {args.days}d")
    print(f"27B 分析完成并单独入库: batch_id={batch_id}")
    root = os.path.dirname(os.path.dirname(__file__))
    dashboard_path = os.path.join(root, "data", "dashboard_data.json")
    if os.path.exists(dashboard_path):
        with open(dashboard_path, "r", encoding="utf-8") as handle:
            dashboard = json.load(handle)
        dashboard["model_comparison"] = fetch_model_comparison()
        dashboard["platform_sentiment_trends"] = fetch_platform_trends()
        with open(dashboard_path, "w", encoding="utf-8") as handle:
            json.dump(dashboard, handle, ensure_ascii=False, indent=2)
        shutil.copy2(dashboard_path, os.path.join(root, "frontend", "data", "dashboard_data.json"))
        print("已刷新网页模型对比数据（未执行抓取）")


if __name__ == "__main__":
    main()
