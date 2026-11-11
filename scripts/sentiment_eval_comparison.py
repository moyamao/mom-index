"""Write a static review of two variants without treating differences as errors."""
from __future__ import annotations

from collections import Counter
import html
import json
import math
from pathlib import Path


VARIANTS = ("baseline", "prompt")
VARIANT_LABELS = {"baseline": "v6基线", "prompt": "v7", "refined": "v7.1"}
FIELDS = (
    "content_type", "sentiment_score", "sentiment_label", "emotion_tags",
    "intent", "position_status", "market_outlook",
)
FIELD_NAMES = {
    "content_type": "内容类型", "sentiment_score": "有效情绪分数",
    "sentiment_label": "情绪类别", "emotion_tags": "具体情绪",
    "intent": "交易动作或意向", "position_status": "当前仓位",
    "market_outlook": "未来走势预期", "reasoning": "判定理由",
}
NAMES = {
    "opinion": "个人观点", "news": "资讯／不参与", "fear": "负向情绪",
    "greed": "正向情绪", "neutral": "中性／无明确动作", "mixed": "混合",
    "anxiety": "焦虑", "anger": "愤怒", "excitement": "兴奋", "optimism": "乐观",
    "pessimism": "悲观", "disappointment": "失望", "regret": "后悔",
    "sarcasm": "反讽", "calm": "平静", "buy": "买入", "add": "加仓",
    "hold": "继续持有", "reduce": "减仓", "sell": "卖出", "clear": "清仓",
    "none": "空仓", "holding": "持有", "trapped": "被套", "exited": "已退出",
    "unknown": "未知", "bullish": "看涨", "bearish": "看跌", "sideways": "横盘／震荡",
}


def _escape(value):
    return html.escape("" if value is None else str(value), quote=True)


def _valid(row):
    return bool(row and row.get("status") == "ok" and isinstance(row.get("decision"), dict))


def _effective_score(decision):
    if decision.get("content_type") == "news":
        return 0.0
    score = decision.get("sentiment_score")
    if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score):
        return None
    return score * 100


def _value(decision, field):
    if field == "sentiment_score":
        return _effective_score(decision)
    value = decision.get(field)
    return sorted(value) if field == "emotion_tags" and isinstance(value, list) else value


def _equal(first, second, field):
    if field == "sentiment_score" and first is not None and second is not None:
        return math.isclose(first, second, rel_tol=0, abs_tol=1e-8)
    return first == second


def _side(row, variant):
    if row is None:
        return {"variant": variant, "status": "missing", "decision": None,
                "errors": [], "analysis_text": None}
    attempts = row.get("attempts") or []
    errors = [attempt.get("error") for attempt in attempts if attempt.get("error")]
    if row.get("error"):
        errors.append(row["error"])
    return {
        "variant": variant,
        "status": "ok" if _valid(row) else "failed",
        "original_status": row.get("status"), "decision": row.get("decision"),
        "analysis_text": row.get("analysis_text"), "seconds": row.get("seconds"),
        "errors": errors, "attempts": attempts,
    }


def _build_pairs(samples, rows, left_variant, right_variant):
    variants = (left_variant, right_variant)
    sources = {str(sample["id"]): sample for sample in samples}
    keyed = {}
    for row in rows:
        if row.get("variant") not in variants:
            continue
        key = (str(row["id"]), row.get("seed"), row.get("repeat"))
        sides = keyed.setdefault(key, {})
        if row["variant"] in sides:
            raise ValueError(f"重复的评测结果：{key} / {row['variant']}")
        sides[row["variant"]] = row

    pairs = []
    for (identifier, seed, repeat), sides in keyed.items():
        source = sources.get(identifier, {})
        first, second = sides.get(left_variant), sides.get(right_variant)
        both_valid = _valid(first) and _valid(second)
        changes, highlights = [], []
        gap = None
        reasoning_changed = None
        raw_score_changed = None
        if both_valid:
            a, b = first["decision"], second["decision"]
            changes = [field for field in FIELDS if not _equal(_value(a, field), _value(b, field), field)]
            reasoning_changed = a.get("reasoning") != b.get("reasoning")
            raw_score_changed = a.get("sentiment_score") != b.get("sentiment_score")
            av, bv = _effective_score(a), _effective_score(b)
            if av is not None and bv is not None:
                gap = round(abs(av - bv), 6)
                if gap >= 30:
                    highlights.append("sentiment_score")
            if {a.get("market_outlook"), b.get("market_outlook")} == {"bullish", "bearish"}:
                highlights.append("market_outlook")
            buy, sell = {"buy", "add"}, {"reduce", "sell", "clear"}
            if ((a.get("intent") in buy and b.get("intent") in sell)
                    or (b.get("intent") in buy and a.get("intent") in sell)):
                highlights.append("intent")
        missing = first is None or second is None
        failed = any(row is not None and not _valid(row) for row in (first, second))
        pair = {
            "id": identifier, "seed": seed, "repeat": repeat,
            "source": {name: source.get(name, "") for name in ("title", "content", "platform", "sector", "split")},
            "both_valid": both_valid, "missing": missing, "failed": failed,
            "core_equal": not changes if both_valid else None,
            "changed_fields": changes, "reasoning_changed": reasoning_changed,
            "raw_score_changed": raw_score_changed, "effective_score_gap_points": gap,
            "highlight_fields": highlights,
            "input_mismatch": bool(first is not None and second is not None
                                   and first.get("analysis_text") != second.get("analysis_text")),
            "left": _side(first, left_variant), "right": _side(second, right_variant),
        }
        if variants == VARIANTS:
            # Preserve the original report's keys only when their names are true.
            pair.update(baseline=pair["left"], prompt=pair["right"])
        pairs.append(pair)
    return pairs


def _display(decision, field):
    if field == "sentiment_score":
        effective = _effective_score(decision)
        if effective is None:
            return "无有效分数"
        value = f"{effective:+g}"
        if decision.get("content_type") == "news":
            raw = decision.get("sentiment_score")
            value += f"（不参与，比较按 0；原始值 {_escape(raw)}）"
        return value
    value = decision.get(field)
    if value is None:
        return "未提供"
    if field == "reasoning":
        return _escape(value) or "未提供理由"
    if field == "emotion_tags":
        return "、".join(_escape(NAMES.get(v, v)) for v in value) if isinstance(value, list) and value else "无"
    if value == "neutral":
        return "无明确动作" if field == "intent" else "中性"
    return _escape(NAMES.get(value, value))


def _status(side):
    return {"ok": "输出有效", "missing": "缺少结果", "failed": "请求或校验失败"}[side["status"]]


def _render_pair(pair, index, left_label, right_label):
    a, b = pair["left"], pair["right"]
    if pair["both_valid"]:
        state = "核心字段相同" if pair["core_equal"] else "核心字段不同"
    else:
        state = "无法完整比较（失败或缺少结果）"
    flags = []
    if "sentiment_score" in pair["highlight_fields"]:
        flags.append(f"有效情绪相差 {pair['effective_score_gap_points']:g} 分（至少 30 分）")
    if "market_outlook" in pair["highlight_fields"]:
        flags.append("看涨与看跌相反")
    if "intent" in pair["highlight_fields"]:
        flags.append("买入与卖出方向相反")
    if pair["input_mismatch"]:
        flags.append("两组实际输入不同，差异不能单独归因于提示词")
    content = [f'<article><h2>{index}. {_escape(pair["id"])}：{state}</h2>',
               f'<p class="meta">种子 {_escape(pair["seed"])}；重复次序 {_escape(pair["repeat"])}；'
               f'平台 {_escape(pair["source"]["platform"])}；板块 {_escape(pair["source"]["sector"])}</p>']
    if flags:
        content.append('<p class="notice">' + "；".join(_escape(flag) for flag in flags) + '</p>')
    content.append('<h3>模型实际收到的全文</h3>')
    if a["analysis_text"] is not None and a["analysis_text"] == b["analysis_text"]:
        content.append(f'<pre>{_escape(a["analysis_text"])}</pre>')
    else:
        for name, side in ((left_label, a), (right_label, b)):
            content.append(f'<p>{_escape(name)}</p><pre>{_escape(side["analysis_text"]) if side["analysis_text"] is not None else "未保存输入"}</pre>')
    content.append('<details><summary>查看原始标题和正文（未按模型输入长度裁剪）</summary>'
                   f'<h3>原始标题</h3><pre>{_escape(pair["source"]["title"])}</pre>'
                   f'<h3>原始正文</h3><pre>{_escape(pair["source"]["content"])}</pre></details>')
    content.append(f'<table><thead><tr><th>字段</th><th>{_escape(left_label)}</th><th>{_escape(right_label)}</th></tr></thead><tbody>'
                   f'<tr><th>状态</th><td>{_status(a)}</td><td>{_status(b)}</td></tr>')
    for field in (*FIELDS, "reasoning"):
        changed = field in pair["changed_fields"] or (field == "reasoning" and pair["reasoning_changed"])
        style = ' class="changed"' if changed else ''
        cells = []
        for side in (a, b):
            cells.append(_display(side["decision"], field) if side["status"] == "ok" else _status(side))
        content.append(f'<tr{style}><th>{FIELD_NAMES[field]}</th><td>{cells[0]}</td><td>{cells[1]}</td></tr>')
    content.append('</tbody></table>')
    if pair["raw_score_changed"] and "sentiment_score" not in pair["changed_fields"]:
        content.append('<p>两组原始分数不同，但资讯不参与情绪比较，有效分数均按 0 处理。</p>')
    for name, side in ((left_label, a), (right_label, b)):
        if side["errors"]:
            content.append(f'<details><summary>{_escape(name)} 请求尝试中的错误</summary><pre>{_escape(chr(10).join(map(str, side["errors"])))}</pre></details>')
    content.append('</article>')
    return "\n".join(content)


def write_paired_comparison(directory: Path, samples: list[dict], rows: list[dict], *,
                            left_variant: str = "baseline", right_variant: str = "prompt",
                            left_label=None, right_label=None) -> dict:
    """Write comparison.json/html and return their comparison summary.

    Pairing uses id/seed/repeat, never sample order. Core equality excludes prose
    reasoning; differences in reasons and raw nonparticipating scores remain shown.
    Failed and missing results are retained. Their counts may overlap for a pair.
    Rows from other variants are ignored. Every pair has left/right side keys;
    the default pair additionally retains the historical baseline/prompt aliases.
    """
    if (not isinstance(left_variant, str) or not left_variant.strip()
            or not isinstance(right_variant, str) or not right_variant.strip()
            or left_variant == right_variant):
        raise ValueError("对照必须选择两个不同且非空的 variant")
    left_label = VARIANT_LABELS.get(left_variant, left_variant) if left_label is None else str(left_label)
    right_label = VARIANT_LABELS.get(right_variant, right_variant) if right_label is None else str(right_label)
    pairs = _build_pairs(samples, rows, left_variant, right_variant)
    difference_counts = Counter(field for pair in pairs for field in pair["changed_fields"])
    summary = {
        "left_variant": left_variant, "right_variant": right_variant,
        "left_label": left_label, "right_label": right_label,
        "available": bool(pairs), "total_pairs": len(pairs),
        "both_valid": sum(pair["both_valid"] for pair in pairs),
        "same": sum(pair["core_equal"] is True for pair in pairs),
        "different": sum(pair["core_equal"] is False for pair in pairs),
        "failed": sum(pair["failed"] for pair in pairs),
        "missing": sum(pair["missing"] for pair in pairs),
        "incomplete": sum(not pair["both_valid"] for pair in pairs),
        "highlighted": sum(bool(pair["highlight_fields"]) for pair in pairs),
        "input_mismatch": sum(pair["input_mismatch"] for pair in pairs),
        "reasoning_changed": sum(pair["reasoning_changed"] is True for pair in pairs),
        "field_differences": dict(difference_counts),
        "note": ("按样本ID、种子和重复次序配对。核心字段一致性不包含理由文字；"
                 "失败和缺失可能重叠。差异不等于错误，本报告不计算准确率。"
                 if pairs else f"没有 {left_variant} 或 {right_variant} 结果，无法生成 {left_label} / {right_label} 配对比较。"),
    }
    body = ["<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">",
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            '<title>本地情绪模型逐条对照</title><style>',
            'body{font:16px/1.6 system-ui,sans-serif;color:#1f2937;background:#f8fafc;max-width:1100px;margin:32px auto;padding:0 18px}'
            'h1{font-size:26px}h2{font-size:20px}h3{font-size:16px}article{background:white;border:1px solid #cbd5e1;border-radius:8px;padding:20px;margin:24px 0}'
            'table{border-collapse:collapse;width:100%;table-layout:fixed;margin:16px 0}th,td{border:1px solid #cbd5e1;padding:10px;text-align:left;vertical-align:top;overflow-wrap:anywhere;white-space:pre-wrap}'
            'th:first-child{width:20%}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:inherit;background:#f1f5f9;padding:12px;border-radius:5px}'
            '.changed{background:#fff7dd}.notice{background:#ffedd5;padding:10px}.meta{color:#475569;font-size:14px}summary{cursor:pointer}details{margin:12px 0}'
            '@media(max-width:600px){body{padding:0 10px}article{padding:12px}td,th{padding:6px;font-size:14px}}',
            '</style></head><body><h1>本地情绪模型逐条对照</h1>',
            f'<p>对照版本：{_escape(left_label)}（{_escape(left_variant)}）与 '
            f'{_escape(right_label)}（{_escape(right_variant)}）。黄色行表示输出发生变化，不代表哪组错误。'
            '没有人工确认的标签，就不能计算准确率或判断哪组更准。理由不同另行展示，不计入核心字段一致性。</p>',
            '<p>情绪分数按 -100 至 +100 展示；资讯的“不参与”在分差比较时按 0。'
            '30 分差、相反涨跌预期或买卖方向仅作为复核重点，不触发生产云端调用。'
            '模型实际输入可能经过裁剪或回复提取，请展开原文核对。</p>',
            '<p>' + _escape(summary["note"]) + '</p>',
            f'<p>总配对 {summary["total_pairs"]}；双方有效 {summary["both_valid"]}；核心相同 {summary["same"]}；'
            f'核心不同 {summary["different"]}；至少一方失败 {summary["failed"]}；至少一方缺失 {summary["missing"]}；'
            f'重点差异 {summary["highlighted"]}。</p>',
    ]
    body.extend(_render_pair(pair, index, left_label, right_label) for index, pair in enumerate(pairs, 1))
    body.append('</body></html>')
    directory = Path(directory)
    targets = [directory / "comparison.json", directory / "comparison.html"]
    for target in targets:
        if target.exists():
            raise FileExistsError(f"拒绝覆盖已有对照文件：{target}")
    directory.mkdir(parents=True, exist_ok=True)
    with targets[0].open("x", encoding="utf-8") as output:
        json.dump({"summary": summary, "pairs": pairs}, output, ensure_ascii=False, indent=2)
    with targets[1].open("x", encoding="utf-8") as output:
        output.write("\n".join(body))
    return summary
