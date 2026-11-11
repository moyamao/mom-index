#!/usr/bin/env python3
"""Evaluate local sentiment variants without writing production data or using cloud APIs."""
from __future__ import annotations

import argparse
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
import math
import os
from pathlib import Path
import statistics
import sys
import time
from urllib.parse import urlsplit
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import requests

from analyzer.llm_contract import EMOTION_TAGS, ENUMS, model_response_format, validate_model_payload
from analyzer.llm_sentiment import _analysis_text, _parse_json_payload, _system_prompt
from analyzer.model_privacy import request_options
from local_llm_runtime import LocalLlmRuntime
from runtime_config import ini_get_int


VARIANTS = {
    "baseline": ("sentiment-v6", {"temperature": 0.1}),
    "prompt": ("sentiment-v7", {"temperature": 0.1}),
    "greedy": ("sentiment-v7", {"temperature": 0}),
    "official": ("sentiment-v7", {
        "temperature": 0.7, "top_p": 0.8, "top_k": 20,
        "min_p": 0, "repeat_penalty": 1,
    }),
}
GOLD_FIELDS = {
    "content_type", "sentiment_label", "sentiment_score", "emotion_tags",
    "intent", "position_status", "market_outlook",
}
SMOKE_EXAMPLES = [
    {"id": "synthetic-1", "title": "今天清仓了", "content": "", "platform": "xueqiu", "sector": "storage"},
    {"id": "synthetic-2", "title": "存储还没跌完", "content": "", "platform": "xueqiu", "sector": "storage"},
    {"id": "synthetic-3", "title": "长期看好，但套得我睡不着", "content": "", "platform": "xueqiu", "sector": "storage"},
]


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def local_base_url(value):
    """Reject public hosts, credentials and redirects; Tailscale uses 100.64.0.0/10."""
    parsed = urlsplit(value.rstrip("/"))
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path not in {"", "/v1"}):
        raise ValueError("本地地址应为 http(s)://本机或私有IP:端口，可选 /v1，不允许凭据或查询参数")
    host = parsed.hostname
    if host != "localhost":
        try:
            address = ipaddress.ip_address(host)
        except ValueError as exc:
            raise ValueError("仅允许 localhost 或明确的本地/私有/Tailscale IP，不允许公网或其他主机名") from exc
        networks = [ipaddress.ip_network(n) for n in (
            "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10", "fc00::/7",
        )]
        if not address.is_loopback and not any(address in net for net in networks if address.version == net.version):
            raise ValueError("评测只允许本地/私有/Tailscale IP，拒绝向公网模型发送文本")
    # Access .port to reject malformed or out-of-range ports before any request.
    parsed.port
    return parsed.geturl() if parsed.path == "/v1" else parsed.geturl() + "/v1"


def positive_int(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("必须大于0")
    return number


def seed_value(value):
    number = int(value)
    if not -1 <= number <= 4294967295:
        raise argparse.ArgumentTypeError("seed必须在[-1,4294967295]范围内")
    return number


def validate_gold(gold, identifier):
    if not isinstance(gold, dict) or set(gold) - GOLD_FIELDS:
        raise ValueError(f"{identifier} 的gold字段不受支持：只支持 {sorted(GOLD_FIELDS)}")
    for field, value in gold.items():
        if field in ENUMS and (not isinstance(value, str) or value not in ENUMS[field]):
            raise ValueError(f"{identifier} 的人工标签 {field} 不在允许枚举内")
        if field == "sentiment_score" and (
            isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or not -1 <= value <= 1
        ):
            raise ValueError(f"{identifier} 的人工情绪分数必须是[-1,1]有限数字")
        if field == "emotion_tags" and (
            not isinstance(value, list)
            or any(not isinstance(tag, str) or tag not in EMOTION_TAGS for tag in value)
            or len(value) != len(set(value))
        ):
            raise ValueError(f"{identifier} 的人工具体情绪必须是无重复的有效标签数组")
    if gold.get("content_type") == "news":
        expected = {"sentiment_score": 0, "sentiment_label": "neutral", "emotion_tags": [],
                    "intent": "neutral", "position_status": "unknown", "market_outlook": "unknown"}
        if any(field in gold and gold[field] != value for field, value in expected.items()):
            raise ValueError(f"{identifier} 的人工news标签包含不适用的情绪或行为值")
    return gold


def load_samples(path=None, smoke=False, split="all", limit=None):
    if smoke:
        rows = [dict(row, split="smoke") for row in SMOKE_EXAMPLES]
    else:
        text = Path(path).read_text(encoding="utf-8-sig")
        rows = json.loads(text) if text.lstrip().startswith("[") else [
            json.loads(line) for line in text.splitlines() if line.strip()
        ]
    if not isinstance(rows, list):
        raise ValueError("输入必须是 JSON 数组或逐行 JSON 对象")
    seen = set()
    checked = []
    for index, raw in enumerate(rows, 1):
        if not isinstance(raw, dict) or not isinstance(raw.get("id"), (str, int)):
            raise ValueError(f"第{index}条缺少有效id")
        row = dict(raw, id=str(raw["id"]))
        if not row["id"] or row["id"] in seen:
            raise ValueError(f"重复或为空的id：{row['id']!r}")
        seen.add(row["id"])
        for name in ("title", "content", "sector", "platform"):
            if not isinstance(row.get(name, ""), str):
                raise ValueError(f"{row['id']} 的 {name} 必须是字符串")
            row.setdefault(name, "")
        row["gold"] = validate_gold(row.get("gold", {}), row["id"])
        if split == "all" or row.get("split") == split:
            checked.append(row)
    selected = checked[:limit] if limit is not None else checked
    if not selected:
        raise ValueError("当前日期/划分/上限条件下没有输入样本")
    return selected, {"input_count": len(rows), "split_count": len(checked), "selected_count": len(selected)}


def ensure_success(response):
    if not 200 <= response.status_code < 300:
        raise ValueError(f"HTTP {response.status_code}，拒绝重定向或错误响应")


def discover_model(session, base_url, expected, timeout):
    response = session.get(base_url + "/models", timeout=timeout, allow_redirects=False)
    ensure_success(response)
    data = response.json()
    if not isinstance(data, dict) or not isinstance(data.get("data"), list):
        raise ValueError("模型预检响应必须包含data数组")
    entries = data["data"]
    ids = [entry["id"] for entry in entries if isinstance(entry, dict)
           and isinstance(entry.get("id"), str) and entry["id"]]
    if not ids or (expected and expected not in ids):
        raise ValueError(f"模型预检失败，期望={expected or '一个模型'}，实际={ids}")
    if not expected and len(ids) != 1:
        raise ValueError("服务返回多个模型，请使用 --model 指定本次评测模型")
    return expected or ids[0]


@contextmanager
def evaluation_runtime(args):
    """Optionally own one local process; restore its process-local environment afterwards."""
    if not args.start_local_model:
        yield (local_base_url(args.base_url or os.environ.get("QWEN_BASE_URL", "http://127.0.0.1:8080")),
               args.model, args.timeout or 120)
        return
    port = ini_get_int("local_llm", "port", 8080)
    base_url = local_base_url(f"http://127.0.0.1:{port}")
    if args.base_url:
        explicit = urlsplit(local_base_url(args.base_url))
        if explicit.scheme != "http" or explicit.hostname not in {"localhost", "127.0.0.1"} or (explicit.port or 80) != port:
            raise ValueError("--start-local-model 与 --base-url 冲突；应省略地址或使用配置端口的本机地址")
    timeout = args.timeout if args.timeout is not None else ini_get_int("local_llm", "request_timeout_seconds", 120)
    if timeout < 1:
        raise ValueError("local_llm.request_timeout_seconds 必须大于0")
    env_keys = ("QWEN_BASE_URL", "MOM_INDEX_LLM_BASE_URL", "MOM_INDEX_LLM_MODEL", "MOM_INDEX_LLM_API_KEY",
                "MOM_INDEX_LLM_ENABLED", "MOM_INDEX_LLM_TIMEOUT_SECONDS", "NO_PROXY", "no_proxy")
    previous = {key: os.environ.get(key) for key in env_keys}
    runtime = LocalLlmRuntime()
    runtime.enabled = True  # Explicit CLI opt-in only; never rewrite config.ini.
    try:
        with runtime:
            started_url = local_base_url(os.environ["QWEN_BASE_URL"])
            started_model = os.environ["MOM_INDEX_LLM_MODEL"]
            if started_url != base_url:
                raise ValueError("本机模型启动地址与配置端口不匹配")
            if args.model and args.model != started_model:
                raise ValueError(f"--model 与已启动模型冲突：期望={args.model}，实际={started_model}")
            yield started_url, started_model, timeout
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def evaluate_one(session, base_url, model, row, variant, seed, repeat, args):
    version, generation = VARIANTS[variant]
    text = _analysis_text(title=row["title"], content=row["content"], platform=row["platform"], max_chars=args.max_chars)
    prompt = _system_prompt(include_reasoning=True, prompt_version=version)
    payload = {
        "model": model, **generation, "seed": seed, "max_tokens": args.max_tokens,
        "chat_template_kwargs": {"enable_thinking": False},
        "response_format": model_response_format(),
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": json.dumps({"sector": row["sector"], "platform": row["platform"], "analysis_text": text}, ensure_ascii=False)},
        ],
    }
    result = {
        "id": row["id"], "variant": variant, "prompt_version": version,
        "seed": seed, "repeat": repeat, "split": row.get("split", "unassigned"),
        "gold": row["gold"], "analysis_text": text, "input_sha256": digest(text),
        "prompt_sha256": digest(prompt), "request_sha256": digest(payload),
        "status": "failed", "decision": None, "attempts": [],
    }
    for number in range(1, 4):
        start = time.perf_counter()
        attempt = {"number": number, "raw_output": "", "finish_reason": None, "usage": None}
        try:
            options = request_options(row["platform"], base_url)
            options["allow_redirects"] = False
            response = session.post(base_url + "/chat/completions", json=payload, timeout=args.timeout or 120, **options)
            ensure_success(response)
            data = response.json()
            if not isinstance(data, dict):
                raise ValueError("响应外层必须是JSON对象")
            choices = data.get("choices")
            if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
                raise ValueError("响应缺少有效choices数组")
            choice = choices[0]
            message = choice.get("message")
            if not isinstance(message, dict) or not isinstance(message.get("content"), str):
                raise ValueError("响应缺少字符串message.content")
            attempt["finish_reason"] = choice.get("finish_reason")
            attempt["usage"] = data.get("usage")
            attempt["raw_output"] = message["content"]
            if attempt["finish_reason"] == "length":
                raise ValueError("模型输出达到token上限，不能视为有效完整结果")
            decision = validate_model_payload(_parse_json_payload(attempt["raw_output"]))
            result.update(status="ok", decision=decision)
            attempt["error"] = None
        except (requests.RequestException, ValueError, KeyError, IndexError, TypeError) as exc:
            attempt["error"] = f"{type(exc).__name__}: {exc}"
        attempt["seconds"] = round(time.perf_counter() - start, 4)
        result["attempts"].append(attempt)
        if result["status"] == "ok":
            break
    result["seconds"] = round(sum(x["seconds"] for x in result["attempts"]), 4)
    return result


def comparable(value):
    return sorted(value) if isinstance(value, list) else value


def emotion_f1(rows):
    pairs = [(r["gold"]["sentiment_label"], (r.get("decision") or {}).get("sentiment_label"))
             for r in rows if "sentiment_label" in r.get("gold", {})]
    if not pairs:
        return None
    labels = sorted({label for pair in pairs for label in pair if label is not None})
    scores = {}
    for label in labels:
        tp = sum(a == label and b == label for a, b in pairs)
        fp = sum(a != label and b == label for a, b in pairs)
        fn = sum(a == label and b != label for a, b in pairs)
        scores[label] = {"support": sum(a == label for a, _ in pairs), "f1": 2 * tp / (2 * tp + fp + fn) if tp + fp + fn else 0}
    return {"macro_f1": statistics.mean(x["f1"] for x in scores.values()), "classes": scores}


def repeat_consistency(rows, same_seed):
    groups = defaultdict(list)
    for row in rows:
        key = (row["id"], row["seed"]) if same_seed else row["id"]
        groups[key].append(row)
    groups = [items for items in groups.values()
              if len(items) > 1 and (same_seed or len({row["seed"] for row in items}) > 1)]
    if not groups:
        return {"groups": 0, "all_equal_rate": None}
    consistent = 0
    for group in groups:
        signatures = {digest({name: comparable((r.get("decision") or {}).get(name)) for name in sorted(GOLD_FIELDS)}) for r in group}
        consistent += all(r["status"] == "ok" for r in group) and len(signatures) == 1
    return {"groups": len(groups), "all_equal_rate": consistent / len(groups)}


def summarize(rows):
    by_variant = defaultdict(list)
    for row in rows:
        by_variant[row["variant"]].append(row)
    result = {}
    for variant, items in by_variant.items():
        fields = {}
        for field in sorted(GOLD_FIELDS - {"sentiment_score"}):
            labeled = [r for r in items if field in r.get("gold", {})]
            if labeled:
                correct = sum(r["status"] == "ok" and comparable(r["decision"].get(field)) == comparable(r["gold"][field]) for r in labeled)
                fields[field] = {"correct": correct, "total_including_failures": len(labeled), "accuracy": correct / len(labeled)}
        scored = [r for r in items if "sentiment_score" in r.get("gold", {})]
        valid = [r for r in scored if r["status"] == "ok"]
        errors = [abs(r["decision"]["sentiment_score"] - r["gold"]["sentiment_score"]) * 100 for r in valid]
        result[variant] = {
            "scoring_status": "scored" if fields or scored else "unscored_no_human_gold",
            "runs": len(items), "successes": sum(r["status"] == "ok" for r in items),
            "failures": sum(r["status"] != "ok" for r in items),
            "success_rate": sum(r["status"] == "ok" for r in items) / len(items),
            "mean_seconds_including_retries": statistics.mean(r["seconds"] for r in items),
            "fields": fields, "emotion": emotion_f1(items),
            "score": {"labeled": len(scored), "valid": len(valid), "failed": len(scored) - len(valid),
                      "mae_points_valid_only": statistics.mean(errors) if errors else None,
                      "within_20_points_including_failures": sum(e <= 20 for e in errors) / len(scored) if scored else None},
            "repeat_same_seed": repeat_consistency(items, True),
            "repeat_across_seeds": repeat_consistency(items, False),
        }
    return result


def run(args, session=None):
    samples, selection = load_samples(args.input, args.smoke, args.split, args.limit)
    variants = list(dict.fromkeys(args.variant or ["baseline", "prompt"]))
    seeds = list(dict.fromkeys(args.seeds))
    directory = Path(args.output_dir) if args.output_dir else ROOT / "outputs" / "sentiment-eval" / (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:8])
    if directory.exists() or directory.is_symlink():
        raise FileExistsError(f"评测目录已存在，为避免覆盖请换一个目录：{directory}")
    parent = directory.parent
    while not parent.exists() and not parent.is_symlink():
        parent = parent.parent
    if not parent.is_dir() or not os.access(parent, os.W_OK | os.X_OK):
        raise OSError(f"评测目录的上级目录不可写：{parent}")
    owned_session = session is None
    session = session or requests.Session()
    session.trust_env = False
    try:
        with evaluation_runtime(args) as (base_url, expected_model, timeout):
            # Keep the caller's parsed options unchanged across repeated programmatic runs.
            effective_args = argparse.Namespace(**vars(args))
            effective_args.timeout = timeout
            return run_evaluation(effective_args, samples, selection, variants, seeds, directory,
                                  session, base_url, expected_model)
    finally:
        if owned_session:
            session.close()


def run_evaluation(args, samples, selection, variants, seeds, directory, session, base_url, expected_model):
    model = discover_model(session, base_url, expected_model, min(args.timeout, 15))
    directory.mkdir(parents=True, exist_ok=False)
    metadata = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(), "base_url": base_url, "model": model,
        "input": str(args.input) if args.input else "synthetic smoke examples; no human gold",
        "dataset_sha256": digest(samples), **selection, "split": args.split,
        "variants": {name: {"prompt_version": VARIANTS[name][0], **VARIANTS[name][1]} for name in variants},
        "seeds": seeds, "repeats_per_seed": args.repeats,
        "max_chars": args.max_chars, "max_tokens": args.max_tokens, "timeout_seconds": args.timeout,
        "started_local_model": args.start_local_model,
        "schema": model_response_format(), "status": "running",
        "comparison_note": "baseline是v6提示词控制组；所有组共用严格输出契约和token预算，并非重放旧生产流程。official同时改变多项采样参数。",
    }
    with (directory / "metadata.json").open("x", encoding="utf-8") as out:
        json.dump(metadata, out, ensure_ascii=False, indent=2)
    rows = []
    expected = len(samples) * len(variants) * len(seeds) * args.repeats
    with (directory / "results.jsonl").open("x", encoding="utf-8") as out:
        for variant in variants:
            for sample in samples:
                for seed in seeds:
                    for repeat in range(1, args.repeats + 1):
                        row = evaluate_one(session, base_url, model, sample, variant, seed, repeat, args)
                        out.write(json.dumps(row, ensure_ascii=False) + "\n")
                        out.flush()
                        rows.append(row)
                        print(f"[{len(rows)}/{expected}] {variant} {sample['id']} {row['status']} {row['seconds']:.2f}s", flush=True)
    report = {"status": "complete", "expected_runs": expected, "completed_runs": len(rows), "variants": summarize(rows)}
    with (directory / "summary.json").open("x", encoding="utf-8") as out:
        json.dump(report, out, ensure_ascii=False, indent=2)
    metadata["status"] = "complete"
    with (directory / "metadata.json").open("w", encoding="utf-8") as out:
        json.dump(metadata, out, ensure_ascii=False, indent=2)
    print(f"评测完成：{directory}；没有写入MySQL或调用云端", flush=True)
    return report, directory


def parser():
    ap = argparse.ArgumentParser(description=__doc__)
    source = ap.add_mutually_exclusive_group(required=True)
    source.add_argument("--input", type=Path, help="UTF-8 JSONL或JSON数组；gold可选")
    source.add_argument("--smoke", action="store_true", help="内置3条合成短句，仅检查连通性与输出，不计算准确率")
    ap.add_argument("--output-dir", type=Path, help="必须不存在；默认在outputs/sentiment-eval创建独立目录")
    ap.add_argument("--base-url", help="默认QWEN_BASE_URL或http://127.0.0.1:8080；自动启动时使用本机地址")
    ap.add_argument("--start-local-model", action="store_true", help="按[local_llm]配置启动本机模型；结束/异常时关闭本次启动的进程")
    ap.add_argument("--model", default="")
    ap.add_argument("--variant", action="append", choices=tuple(VARIANTS), help="可重复；默认baseline和prompt")
    ap.add_argument("--split", default="all", help="仅选指定split，如dev/test/challenge；默认all")
    ap.add_argument("--limit", type=positive_int)
    ap.add_argument("--seeds", type=seed_value, nargs="+", default=[42])
    ap.add_argument("--repeats", type=positive_int, default=1)
    ap.add_argument("--max-chars", type=positive_int, default=1200)
    ap.add_argument("--max-tokens", type=positive_int, default=768)
    ap.add_argument("--timeout", type=positive_int, help="请求超时秒数；默认120，自动启动时默认使用[local_llm]配置")
    return ap


def main():
    try:
        report, _ = run(parser().parse_args())
    except (ValueError, OSError, RuntimeError, requests.RequestException) as exc:
        raise SystemExit(f"评测停止：{exc}") from exc
    if any(item["failures"] for item in report["variants"].values()):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
