#!/usr/bin/env python3
"""Compare stored 14B analyses with live 27B and cloud-adjudicate disputes."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


def main() -> None:
    parser = argparse.ArgumentParser(description="双本地模型分析；实质分歧交给云端裁决")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--interval", type=float, default=0.1)
    parser.add_argument("--reanalyze-all", action="store_true")
    parser.add_argument("--reference-profile", default=os.environ.get("CONSENSUS_14B_PROFILE", "mini-14b"))
    args = parser.parse_args()
    if min(args.days, args.limit, args.timeout) <= 0 or args.interval < 0:
        parser.error("days、limit、timeout必须大于0，interval不能小于0")

    os.environ.setdefault("MOM_INDEX_RUNTIME_ROLE", "analyst")
    os.environ.setdefault("MOM_INDEX_LLM_PROFILE", "local-consensus-cloud")
    os.environ.setdefault("MOM_INDEX_LLM_MODEL", "14B+27B+cloud-adjudication")
    os.environ.setdefault("MOM_INDEX_LLM_PROMPT_VERSION", "sentiment-consensus-v1")

    from analyzer.llm_analyzer import analyze_post, apply_llm_decision
    from analyzer.llm_consensus import adjudicate, endpoint_config, preflight
    from analyzer.platform_trends import fetch_platform_trends
    from storage.mysql_store import (fetch_model_comparison, fetch_posts_for_analysis,
        fetch_reference_analyses, persist_standalone_analysis, reference_analysis_key)

    configs = endpoint_config()
    preflight(configs)
    print(f"本机27B已就绪：{configs['local_27b']['model']}；14B从MySQL历史结果读取")
    print(f"云端裁决模型：{configs['cloud']['model']}")
    profile = os.environ["MOM_INDEX_LLM_PROFILE"]
    posts = fetch_posts_for_analysis(
        args.days, args.limit, analysis_profile="" if args.reanalyze_all else profile,
    )
    total = sum(len(items) for items in posts.values())
    print(f"读取最近{args.days}天去重帖子{total}条；{'全部重算' if args.reanalyze_all else '仅补未分析'}")
    if not total:
        print("没有待补的帖子，本次任务正常结束。")
        return
    references = fetch_reference_analyses(posts, args.reference_profile)
    print(f"14B历史配置={args.reference_profile}；匹配到{len(references)}/{total}条")

    results = {}
    processed = cloud_count = review_count = 0
    for sector, items in posts.items():
        sector_results = []
        for post in items:
            result = analyze_post(post, sector, run_llm_second_pass=False)
            decision, audit = adjudicate(
                title=result.title, content=post.get("content", "") or "", sector=sector,
                platform=result.platform, configs=configs, timeout=args.timeout,
                stored_14b=references.get(reference_analysis_key(post)),
            )
            if decision is not None:
                apply_llm_decision(result, decision)
                result.analysis_profile = "local-consensus-cloud"
                result.model_name = "14B+27B+cloud-adjudication"
                result.prompt_version = "sentiment-consensus-v1"
            else:
                result.llm_error = "27B未返回有效结果且没有14B历史结果"
            result.adjudication_source = audit["source"]
            result.adjudication_json = json.dumps(audit, ensure_ascii=False)
            if audit.get("requires_review"):
                review_count += 1
                result.llm_error = (result.llm_error + "; " if result.llm_error else "") + "裁决未完成，需复核"
            if audit["source"] == "cloud_adjudication":
                cloud_count += 1
            result.key_signals.append(f"「裁决来源」{audit['source']}")
            sector_results.append(result)
            processed += 1
            print(f"[{processed}/{total}] {sector}/{result.post_id} {audit['source']}"
                  f" 分歧={','.join(audit.get('disagreements', [])) or '无'}", flush=True)
            if args.interval and processed < total:
                time.sleep(args.interval)
        sector_results.sort(key=lambda row: row.newbie_score, reverse=True)
        results[sector] = sector_results

    note = f"14B+27B consensus; cloud={cloud_count}; review={review_count}; {args.days}d"
    batch_id = persist_standalone_analysis(results, posts, note=note)
    print(f"分析完成并入库：batch_id={batch_id}；云端裁决={cloud_count}；待复核={review_count}")

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
        print("已刷新网页模型对比数据")


if __name__ == "__main__":
    main()
