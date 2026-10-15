#!/usr/bin/env python3
"""Cloud-adjudicate material disagreements between already stored 14B/27B rows."""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


def main() -> None:
    parser = argparse.ArgumentParser(description="只读取历史14B/27B结果，分歧时调用云端")
    parser.add_argument("--days", type=int, default=14)
    parser.add_argument("--limit", type=int, default=5000)
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--profile-14b", default="mini-14b")
    parser.add_argument("--profile-27b", default="macbook-27b")
    args = parser.parse_args()

    os.environ["MOM_INDEX_LLM_PROFILE"] = "local-consensus-cloud"
    os.environ["MOM_INDEX_LLM_MODEL"] = "stored-14B+27B+cloud-adjudication"
    os.environ["MOM_INDEX_LLM_PROMPT_VERSION"] = "sentiment-consensus-v1"
    os.environ.setdefault("MOM_INDEX_RUNTIME_ROLE", "analyst")

    from analyzer.llm_analyzer import analyze_post, apply_llm_decision
    from analyzer.llm_consensus import (_decision, call_model, decision_from_stored,
                                        endpoint_config, material_disagreements)
    from storage.mysql_store import (fetch_posts_for_analysis, fetch_reference_analyses,
                                     persist_standalone_analysis, reference_analysis_key)

    # Read a wider pending window so persistent 27B failures cannot be starved by
    # newer ordinary comparisons; the actual processed count still obeys --limit.
    candidate_limit = max(args.limit * 10, 5000)
    posts = fetch_posts_for_analysis(
        args.days, candidate_limit, analysis_profile="local-consensus-cloud"
    )
    refs14 = fetch_reference_analyses(posts, args.profile_14b)
    refs27 = fetch_reference_analyses(posts, args.profile_27b)
    failed27 = fetch_reference_analyses(posts, args.profile_27b, ("llm_failed",))
    configs = endpoint_config()
    total = sum(len(items) for items in posts.values())
    paired = cloud_count = agreement_count = 0
    results = {}
    selected_posts = {}

    print(f"待裁决池={total}；本次上限={args.limit}；14B匹配={len(refs14)}；27B匹配={len(refs27)}；27B终态失败={len(failed27)}")
    ordered_posts = [
        (sector, post) for sector, items in posts.items() for post in items
    ]
    ordered_posts.sort(
        key=lambda item: reference_analysis_key(item[1]) in failed27,
        reverse=True,
    )
    handled = 0
    for sector, post in ordered_posts:
            if handled >= args.limit:
                break
            key = reference_analysis_key(post)
            row14, row27 = refs14.get(key), refs27.get(key)
            row27_failure = failed27.get(key)
            if not row14 or (not row27 and not row27_failure):
                continue
            handled += 1
            paired += 1
            decision14 = decision_from_stored(row14)
            decision27 = decision_from_stored(row27) if row27 else None
            differences = (["missing_valid_27b"] if row27_failure
                           else material_disagreements(decision14, decision27))
            if not differences and decision27 is not None:
                agreement_count += 1
                cloud = None
                final = decision27
                source = "stored_agreement_27b"
            else:
                cloud = call_model(
                    configs["cloud"], title=post.get("title", ""), content=post.get("content", ""),
                    sector=sector, platform=post.get("platform", ""), timeout=args.timeout,
                )
                fallback = decision27 or decision14
                final = fallback if not cloud["ok"] else _decision(cloud["decision"])
                if cloud["ok"]:
                    source = "cloud_after_27b_failure" if row27_failure else "cloud_adjudication"
                else:
                    source = "cloud_failure_fallback_27b" if decision27 else "cloud_failure_fallback_14b"
                cloud_count += 1
            result = analyze_post(post, sector, run_llm_second_pass=False)
            apply_llm_decision(result, final)
            result.analysis_profile = "local-consensus-cloud"
            result.model_name = "stored-14B+27B+cloud-adjudication"
            result.prompt_version = "sentiment-consensus-v1"
            result.adjudication_source = source
            result.adjudication_json = json.dumps({
                "source": source, "disagreements": differences,
                "stored_14b": {"id": row14["id"], "batch_id": row14["batch_id"]},
                "stored_27b": ({"id": row27["id"], "batch_id": row27["batch_id"]}
                               if row27 else None),
                "failed_27b": ({"id": row27_failure["id"], "batch_id": row27_failure["batch_id"]}
                               if row27_failure else None),
                "cloud": cloud, "requires_review": bool(cloud is not None and not cloud["ok"]),
            }, ensure_ascii=False)
            if cloud is not None and not cloud["ok"]:
                result.llm_error = "云端裁决失败，采用27B结果，需复核"
            results.setdefault(sector, []).append(result)
            selected_posts.setdefault(sector, []).append(post)
            if differences:
                print(f"[{cloud_count}] {sector}/{result.post_id} {source} 分歧={','.join(differences)}", flush=True)

    if results:
        batch_id = persist_standalone_analysis(
            results, selected_posts,
            note=f"stored consensus; paired={paired}; agreements={agreement_count}; cloud={cloud_count}",
        )
        print(f"裁决完成并入库：batch_id={batch_id}；配对={paired}；一致={agreement_count}；分歧调用云端={cloud_count}")
    else:
        print(f"无需新增云端裁决；配对={paired}；一致={agreement_count}")


if __name__ == "__main__":
    main()
