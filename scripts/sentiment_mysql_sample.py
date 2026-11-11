"""Read-only, reproducible source-post sampling for local sentiment experiments."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta
import hashlib
import random
import re
import unicodedata

from analyzer.llm_sentiment import _analysis_text
from analyzer.sentiment_prompts import sentiment_v7_prompt
from post_time import BEIJING, beijing_now
from storage.mysql_store import _connect


PUBLIC_PLATFORMS = ("xueqiu", "weibo", "xiaohongshu", "xhs")


def _normalized(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value)).strip()


def _full_text(title: str, content: str) -> str:
    """Keep the whole source while avoiding a duplicated title/body prefix."""
    title, content = _normalized(title), _normalized(content)
    if content and (not title or content.startswith(title)):
        return content
    return _normalized(f"{title} {content}")


def _source_datetime(value):
    if not isinstance(value, datetime):
        try:
            value = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    return value.astimezone(BEIJING).replace(tzinfo=None) if value.tzinfo else value


def _prompt_examples() -> set[str]:
    # Match only the explicitly introduced source examples, never every quoted
    # phrase or any post merely containing an example as a substring.
    return {_normalized(text) for text in re.findall(
        r"例：‘([^’]+)’→", sentiment_v7_prompt(include_reasoning=True)
    )}


def sample_mysql_posts(*, days: int = 30, count: int = 100, seed: int = 20261111,
                       max_chars: int = 1200) -> tuple[list[dict], dict]:
    """Return uniform samples of unique eligible texts and selection metadata.

    The pool spans the entire requested rolling window, not the newest N posts.
    No stored model predictions or human labels are read into the sample.
    """
    for name, value in (("days", days), ("count", count), ("max_chars", max_chars)):
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"{name} 必须是正整数")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("seed 必须是整数")
    end = beijing_now().replace(microsecond=0)
    start = end - timedelta(days=days)
    connection = None
    try:
        connection = _connect()
        with connection.cursor() as cursor:
            cursor.execute("START TRANSACTION READ ONLY")
            placeholders = ",".join(["%s"] * len(PUBLIC_PLATFORMS))
            cursor.execute(
                f"""SELECT c.content_key, c.post_id AS source_post_id, c.platform,
                           c.title, c.content, c.post_datetime AS published_at,
                           c.canonical_url AS url, s.sector
                    FROM mom_index_post_catalog c
                    LEFT JOIN mom_index_post_sightings s ON s.id = (
                        SELECT MAX(s2.id) FROM mom_index_post_sightings s2
                        WHERE s2.content_key = c.content_key
                    )
                    WHERE c.platform IN ({placeholders})
                      AND c.post_datetime >= %s AND c.post_datetime <= %s
                    ORDER BY c.content_key ASC""",
                PUBLIC_PLATFORMS + (start, end),
            )
            rows = cursor.fetchall()
    except Exception as exc:
        # Driver messages may contain hostname/user details. Keep diagnostics
        # useful without copying connection settings into experiment reports.
        raise RuntimeError(
            f"MySQL只读抽样失败（{type(exc).__name__}）；请检查数据库连接和现有帖子表。"
        ) from None
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception as exc:
                raise RuntimeError(
                    f"MySQL只读抽样连接关闭失败（{type(exc).__name__}）；本次抽样未完成。"
                ) from None

    excluded = Counter()
    seen_ids, seen_full, seen_inputs = set(), set(), set()
    examples = _prompt_examples()
    pool = []
    # Stable tie-breakers also protect mocked/imported duplicate source rows.
    ordered = sorted(rows, key=lambda row: (
        str(row.get("content_key") or ""), str(row.get("sector") or ""),
        str(row.get("title") or ""), str(row.get("content") or ""),
    ))
    for row in ordered:
        identifier = str(row.get("content_key") or "").strip()
        platform = str(row.get("platform") or "").strip()
        if platform not in PUBLIC_PLATFORMS:
            excluded["non_public_platform"] += 1
            continue
        published = _source_datetime(row.get("published_at"))
        if published is None:
            excluded["missing_date"] += 1
            continue
        if not start <= published <= end:
            excluded["outside_date_window"] += 1
            continue
        if not identifier:
            excluded["missing_identity"] += 1
            continue
        if identifier in seen_ids:
            excluded["duplicate_identity"] += 1
            continue
        seen_ids.add(identifier)
        title, content = str(row.get("title") or ""), str(row.get("content") or "")
        full = _full_text(title, content)
        analysis_text = _analysis_text(title=title, content=content, platform=platform, max_chars=max_chars)
        normalized_input = _normalized(analysis_text)
        if not full or not normalized_input:
            excluded["empty_text"] += 1
            continue
        # _analysis_text prepends this marker only when extracting a reply.
        compared_input = normalized_input
        if platform == "xueqiu" and re.match(r"^\s*回复\s*@[^:：\s]+\s*[:：]", title):
            compared_input = re.sub(r"^本次回复\s+", "", normalized_input)
        if full in examples or compared_input in examples:
            excluded["prompt_example"] += 1
            continue
        if full in seen_full:
            excluded["duplicate_full_text"] += 1
            continue
        if normalized_input in seen_inputs:
            excluded["duplicate_analysis_text"] += 1
            continue
        seen_full.add(full)
        seen_inputs.add(normalized_input)
        pool.append({
            "id": identifier, "source_post_id": str(row.get("source_post_id") or ""),
            "platform": platform, "title": title, "content": content,
            "sector": str(row.get("sector") or ""),
            "published_at": published.strftime("%Y-%m-%d %H:%M:%S"),
            "url": str(row.get("url") or ""), "split": "random", "gold": {},
            "analysis_text": analysis_text,
            "analysis_text_sha256": hashlib.sha256(analysis_text.encode("utf-8")).hexdigest(),
        })

    if len(pool) < count:
        raise ValueError(
            f"最近{days}天去重和排除提示词示例后仅有{len(pool)}条合格公开帖子，"
            f"不足请求的{count}条；请扩大日期窗口，本次不启动模型。"
        )
    selected = random.Random(seed).sample(pool, count)
    selection = {
        "source": "mysql_post_catalog", "timezone": "Asia/Shanghai",
        "window_start_inclusive": start.strftime("%Y-%m-%d %H:%M:%S"),
        "window_end_inclusive": end.strftime("%Y-%m-%d %H:%M:%S"),
        "days": days, "seed": seed, "requested_count": count, "selected_count": len(selected),
        "public_platform_filter": list(PUBLIC_PLATFORMS),
        "input_count": len(rows), "eligible_unique_pool_count": len(pool),
        "excluded_counts": dict(sorted(excluded.items())),
        "prompt_example_count": len(examples), "max_chars": max_chars,
        "platform_counts": dict(sorted(Counter(row["platform"] for row in selected).items())),
        "sector_counts": dict(sorted(Counter(row["sector"] for row in selected).items())),
        "sampling": "uniform_without_replacement_after_identity_full_text_and_actual_input_dedup",
        "labels": "unlabeled_source_posts_no_model_outputs",
    }
    return selected, selection
