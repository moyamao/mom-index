"""
MySQL 持久化

优先读取：
- conf/config.ini -> [mysql]

可选环境变量：
- MOM_INDEX_ENABLE_MYSQL=1
- MOM_INDEX_DB_HOST=127.0.0.1
- MOM_INDEX_DB_PORT=3306
- MOM_INDEX_DB_USER=root
- MOM_INDEX_DB_PASSWORD=...
- MOM_INDEX_DB_NAME=stock
"""
from __future__ import annotations

import configparser
import json
import os
from collections import defaultdict
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from typing import Dict, Iterable, List, Optional

import pymysql


PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))
DEFAULT_CONFIG_FILE = os.path.join(PROJECT_ROOT, "conf", "config.ini")


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def mysql_enabled() -> bool:
    if _env_flag("MOM_INDEX_ENABLE_MYSQL", default=False):
        return True
    if _read_ini_mysql_config():
        return True
    return bool(os.environ.get("MOM_INDEX_DB_PASSWORD"))


def _read_ini_mysql_config() -> Dict[str, str]:
    config_path = os.environ.get("MOM_INDEX_CONFIG_FILE", DEFAULT_CONFIG_FILE).strip() or DEFAULT_CONFIG_FILE
    if not os.path.exists(config_path):
        return {}

    parser = configparser.ConfigParser()
    parser.read(config_path, encoding="utf-8")
    if not parser.has_section("mysql"):
        return {}

    section = parser["mysql"]
    return {
        "host": section.get("host", "").strip(),
        "port": section.get("port", "").strip(),
        "user": section.get("user", "").strip(),
        "password": section.get("pass", "").strip(),
        "database": section.get("db", "").strip(),
    }


def _mysql_config() -> Dict:
    ini_config = _read_ini_mysql_config()
    return {
        "host": os.environ.get("MOM_INDEX_DB_HOST", ini_config.get("host", "127.0.0.1")).strip() or "127.0.0.1",
        "port": int(os.environ.get("MOM_INDEX_DB_PORT", ini_config.get("port", "3306") or "3306")),
        "user": os.environ.get("MOM_INDEX_DB_USER", ini_config.get("user", "root")).strip() or "root",
        "password": os.environ.get("MOM_INDEX_DB_PASSWORD", ini_config.get("password", "")),
        "database": os.environ.get("MOM_INDEX_DB_NAME", ini_config.get("database", "stock")).strip() or "stock",
        "charset": "utf8mb4",
        "autocommit": False,
        "cursorclass": pymysql.cursors.DictCursor,
    }


def _connect():
    return pymysql.connect(**_mysql_config())


def _column_exists(cur, table_name: str, column_name: str) -> bool:
    cur.execute(f"SHOW COLUMNS FROM {table_name} LIKE %s", (column_name,))
    return cur.fetchone() is not None


def _ensure_tables(cur) -> None:
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS mom_index_runs (
            id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
            run_at DATETIME NOT NULL,
            total_posts INT NOT NULL DEFAULT 0,
            sector_count INT NOT NULL DEFAULT 0,
            note VARCHAR(255) DEFAULT '',
            dashboard_json LONGTEXT,
            sector_indices_json LONGTEXT,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS mom_index_posts (
            id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
            run_id BIGINT UNSIGNED NOT NULL,
            sector VARCHAR(32) NOT NULL,
            platform VARCHAR(32) NOT NULL,
            source_mode VARCHAR(32) DEFAULT '',
            post_id VARCHAR(128) DEFAULT '',
            title TEXT,
            content MEDIUMTEXT,
            url TEXT,
            author VARCHAR(255) DEFAULT '',
            post_date VARCHAR(64) DEFAULT '',
            post_datetime DATETIME NULL,
            collected_at VARCHAR(64) DEFAULT '',
            raw_json LONGTEXT,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            KEY idx_mom_index_posts_run_id (run_id),
            KEY idx_mom_index_posts_sector (sector),
            KEY idx_mom_index_posts_platform (platform),
            KEY idx_mom_index_posts_post_id (post_id),
            KEY idx_mom_index_posts_post_datetime (post_datetime)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )
    if not _column_exists(cur, "mom_index_posts", "post_datetime"):
        cur.execute(
            """
            ALTER TABLE mom_index_posts
            ADD COLUMN post_datetime DATETIME NULL AFTER post_date
            """
        )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS mom_index_analysis (
            id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
            run_id BIGINT UNSIGNED NOT NULL,
            sector VARCHAR(32) NOT NULL,
            post_id VARCHAR(128) DEFAULT '',
            title TEXT,
            platform VARCHAR(32) DEFAULT '',
            newbie_score DECIMAL(6,2) NOT NULL DEFAULT 0,
            newbie_confidence VARCHAR(16) DEFAULT '',
            level VARCHAR(32) DEFAULT '',
            sentiment_score DECIMAL(6,2) NOT NULL DEFAULT 0,
            intent VARCHAR(16) DEFAULT '',
            intent_strength DECIMAL(6,2) NOT NULL DEFAULT 0,
            key_signals_json TEXT,
            reasoning TEXT,
            matched_newbie_json TEXT,
            matched_pro_json TEXT,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            KEY idx_mom_index_analysis_run_id (run_id),
            KEY idx_mom_index_analysis_sector (sector),
            KEY idx_mom_index_analysis_post_id (post_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )


def _insert_run(cur, dashboard: Dict, sector_indices: Dict, total_posts: int) -> int:
    cur.execute(
        """
        INSERT INTO mom_index_runs (run_at, total_posts, sector_count, note, dashboard_json, sector_indices_json)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            total_posts,
            len(sector_indices),
            "mom-index pipeline",
            json.dumps(dashboard, ensure_ascii=False),
            json.dumps(sector_indices, ensure_ascii=False),
        ),
    )
    return int(cur.lastrowid)


def _iter_post_rows(run_id: int, all_posts: Dict[str, List[Dict]]) -> Iterable[tuple]:
    for sector, posts in all_posts.items():
        for post in posts:
            if post.get("platform") == "xiaohongshu" and post.get("is_mock"):
                continue
            yield (
                run_id,
                sector,
                post.get("platform", ""),
                post.get("source_mode", ""),
                str(post.get("id", "")),
                post.get("title", ""),
                post.get("content", ""),
                post.get("url", ""),
                post.get("author", ""),
                post.get("date", ""),
                post.get("published_at") or None,
                post.get("collected_at", ""),
                json.dumps(post, ensure_ascii=False),
            )


def _iter_analysis_rows(run_id: int, analysis_results: Dict[str, List]) -> Iterable[tuple]:
    for sector, results in analysis_results.items():
        for item in results:
            if item.platform == "xiaohongshu" and str(item.post_id).startswith("xhs_sim_"):
                continue
            yield (
                run_id,
                sector,
                item.post_id,
                item.title,
                item.platform,
                float(item.newbie_score),
                item.newbie_confidence,
                item.level,
                float(item.sentiment_score),
                item.intent,
                float(item.intent_strength),
                json.dumps(item.key_signals, ensure_ascii=False),
                item.reasoning,
                json.dumps(item.matched_newbie, ensure_ascii=False),
                json.dumps(item.matched_pro, ensure_ascii=False),
            )


def persist_pipeline_run(
    all_posts: Dict[str, List[Dict]],
    analysis_results: Dict[str, List],
    sector_indices: Dict,
    dashboard: Dict,
) -> Optional[int]:
    if not mysql_enabled():
        return None

    conn = _connect()
    try:
        with conn.cursor() as cur:
            _ensure_tables(cur)
            run_id = _insert_run(cur, dashboard, sector_indices, sum(len(v) for v in all_posts.values()))

            post_rows = list(_iter_post_rows(run_id, all_posts))
            if post_rows:
                cur.executemany(
                    """
                    INSERT INTO mom_index_posts (
                        run_id, sector, platform, source_mode, post_id, title, content, url,
                        author, post_date, post_datetime, collected_at, raw_json
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    post_rows,
                )

            analysis_rows = list(_iter_analysis_rows(run_id, analysis_results))
            if analysis_rows:
                cur.executemany(
                    """
                    INSERT INTO mom_index_analysis (
                        run_id, sector, post_id, title, platform, newbie_score, newbie_confidence,
                        level, sentiment_score, intent, intent_strength, key_signals_json, reasoning,
                        matched_newbie_json, matched_pro_json
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    analysis_rows,
                )

        conn.commit()
        return run_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _parse_post_day(post_datetime, post_date: str) -> Optional[date]:
    if post_datetime:
        if isinstance(post_datetime, datetime):
            return post_datetime.date()
        try:
            return datetime.fromisoformat(str(post_datetime)).date()
        except ValueError:
            pass

    raw = (post_date or "").strip()
    if not raw:
        return None

    formats = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%m-%d %H:%M",
        "%m-%d",
    ]
    now = datetime.now()
    for fmt in formats:
        try:
            parsed = datetime.strptime(raw, fmt)
            if fmt.startswith("%m-%d"):
                parsed = parsed.replace(year=now.year)
                if parsed > now:
                    parsed = parsed.replace(year=now.year - 1)
            return parsed.date()
        except ValueError:
            continue
    return None


def _to_analysis_like(row: Dict) -> SimpleNamespace:
    return SimpleNamespace(
        title=row.get("title", "") or "",
        newbie_score=float(row.get("newbie_score") or 0),
        level=row.get("level", "") or "",
        reasoning="",
        sentiment_score=float(row.get("sentiment_score") or 0),
        intent=row.get("intent", "") or "neutral",
        intent_strength=float(row.get("intent_strength") or 0),
        key_signals=[],
    )


def _week_start(day: date) -> str:
    monday = day - timedelta(days=day.weekday())
    return monday.isoformat()


def fetch_keyword_history() -> Dict[str, Dict]:
    """
    从 MySQL 回捞关键词历史，输出日线/周线。
    依赖 mom_index_posts.raw_json 里的 keyword 字段，以及 analysis 表里的情绪结果。
    """
    if not mysql_enabled():
        return {}

    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    p.sector,
                    p.post_id,
                    p.title,
                    p.post_date,
                    p.post_datetime,
                    p.raw_json,
                    a.level,
                    a.newbie_score,
                    a.sentiment_score,
                    a.intent,
                    a.intent_strength
                FROM mom_index_posts p
                INNER JOIN mom_index_analysis a
                    ON a.run_id = p.run_id
                   AND a.sector = p.sector
                   AND a.post_id = p.post_id
                ORDER BY p.post_datetime ASC, p.id ASC
                """
            )
            rows = cur.fetchall()

        from analyzer.index_calculator import compute_sector_index

        daily_groups: Dict[str, Dict[str, Dict[str, List]]] = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
        weekly_groups: Dict[str, Dict[str, Dict[str, List]]] = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
        keyword_post_keys: Dict[str, Dict[str, set]] = defaultdict(lambda: defaultdict(set))
        seen_daily_keys = set()
        seen_weekly_keys = set()

        for row in rows:
            try:
                raw_json = json.loads(row.get("raw_json") or "{}")
            except json.JSONDecodeError:
                raw_json = {}

            keyword = (raw_json.get("keyword") or "").strip()
            sector = (row.get("sector") or "").strip()
            if not keyword or not sector:
                continue

            day = _parse_post_day(row.get("post_datetime"), row.get("post_date", ""))
            if day is None:
                continue

            post_key = str(row.get("post_id") or row.get("title") or "").strip()
            if not post_key:
                continue

            analysis_like = _to_analysis_like(row)
            day_key = day.isoformat()
            week_key = _week_start(day)

            daily_dedupe_key = (sector, keyword, day_key, post_key)
            if daily_dedupe_key not in seen_daily_keys:
                seen_daily_keys.add(daily_dedupe_key)
                daily_groups[sector][keyword][day_key].append(analysis_like)

            weekly_dedupe_key = (sector, keyword, week_key, post_key)
            if weekly_dedupe_key not in seen_weekly_keys:
                seen_weekly_keys.add(weekly_dedupe_key)
                weekly_groups[sector][keyword][week_key].append(analysis_like)

            keyword_post_keys[sector][keyword].add((day_key, post_key))

        result: Dict[str, Dict] = {}
        for sector, keyword_map in keyword_post_keys.items():
            sorted_keywords = sorted(
                keyword_map.keys(),
                key=lambda kw: (len(keyword_post_keys[sector][kw]), kw),
                reverse=True,
            )
            result[sector] = {
                "keywords": sorted_keywords,
                "daily": {},
                "weekly": {},
            }

            for keyword in sorted_keywords:
                daily_records = []
                for day_key, items in sorted(daily_groups[sector][keyword].items()):
                    index_data = compute_sector_index(items)
                    daily_records.append(
                        {
                            "date": day_key,
                            "index": index_data["index"],
                            "post_count": len(items),
                            "newbie_posts": index_data["details"].get("newbie_posts", 0),
                        }
                    )

                weekly_records = []
                for week_key, items in sorted(weekly_groups[sector][keyword].items()):
                    index_data = compute_sector_index(items)
                    weekly_records.append(
                        {
                            "week": week_key,
                            "index": index_data["index"],
                            "post_count": len(items),
                            "newbie_posts": index_data["details"].get("newbie_posts", 0),
                        }
                    )

                result[sector]["daily"][keyword] = daily_records
                result[sector]["weekly"][keyword] = weekly_records

        return result
    finally:
        conn.close()
