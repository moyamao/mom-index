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
import hashlib
import re
from types import SimpleNamespace
from typing import Dict, Iterable, List, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import pymysql
from post_time import beijing_now, normalize_social_datetime


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
    conn = pymysql.connect(**_mysql_config())
    with conn.cursor() as cur:
        cur.execute("SET time_zone = '+08:00'")
    return conn


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
        CREATE TABLE IF NOT EXISTS mom_index_post_catalog (
            content_key CHAR(64) NOT NULL PRIMARY KEY,
            platform VARCHAR(32) NOT NULL,
            post_id VARCHAR(128) DEFAULT '',
            canonical_url TEXT,
            author VARCHAR(255) DEFAULT '',
            title TEXT,
            content MEDIUMTEXT,
            post_datetime DATETIME NULL,
            first_seen_at DATETIME NOT NULL,
            last_seen_at DATETIME NOT NULL,
            first_run_id BIGINT UNSIGNED NOT NULL,
            last_run_id BIGINT UNSIGNED NOT NULL,
            raw_json LONGTEXT,
            KEY idx_post_catalog_platform_id (platform, post_id),
            KEY idx_post_catalog_datetime (post_datetime),
            KEY idx_post_catalog_last_seen (last_seen_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS mom_index_post_sightings (
            id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
            run_id BIGINT UNSIGNED NOT NULL,
            content_key CHAR(64) NOT NULL,
            sector VARCHAR(32) NOT NULL,
            keyword VARCHAR(255) DEFAULT '',
            collected_at DATETIME NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE KEY uq_post_sighting (run_id, content_key, sector, keyword),
            KEY idx_post_sightings_run (run_id),
            KEY idx_post_sightings_content (content_key),
            KEY idx_post_sightings_sector_time (sector, collected_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS mom_index_analysis_batches (
            id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
            profile VARCHAR(64) NOT NULL,
            model_name VARCHAR(255) NOT NULL,
            prompt_version VARCHAR(64) NOT NULL,
            machine_role VARCHAR(32) NOT NULL,
            started_at DATETIME NOT NULL,
            completed_at DATETIME NULL,
            status VARCHAR(16) NOT NULL DEFAULT 'running',
            requested_posts INT NOT NULL DEFAULT 0,
            analyzed_posts INT NOT NULL DEFAULT 0,
            llm_posts INT NOT NULL DEFAULT 0,
            failed_posts INT NOT NULL DEFAULT 0,
            note VARCHAR(255) DEFAULT '',
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            KEY idx_analysis_batches_profile_time (profile, started_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )
    for column, definition in {
        "llm_posts": "INT NOT NULL DEFAULT 0",
        "failed_posts": "INT NOT NULL DEFAULT 0",
    }.items():
        if not _column_exists(cur, "mom_index_analysis_batches", column):
            cur.execute(f"ALTER TABLE mom_index_analysis_batches ADD COLUMN {column} {definition}")
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
            sentiment_label VARCHAR(16) NOT NULL DEFAULT 'neutral',
            emotion_tags_json TEXT,
            sentiment_confidence DECIMAL(6,4) NOT NULL DEFAULT 0,
            intent VARCHAR(16) DEFAULT '',
            intent_strength DECIMAL(6,2) NOT NULL DEFAULT 0,
            position_status VARCHAR(16) NOT NULL DEFAULT 'unknown',
            market_outlook VARCHAR(16) NOT NULL DEFAULT 'unknown',
            content_type VARCHAR(16) NOT NULL DEFAULT 'opinion',
            key_signals_json TEXT,
            reasoning TEXT,
            matched_newbie_json TEXT,
            matched_pro_json TEXT,
            batch_id BIGINT UNSIGNED NULL,
            analysis_profile VARCHAR(64) NOT NULL DEFAULT 'legacy',
            model_name VARCHAR(255) NOT NULL DEFAULT 'legacy',
            prompt_version VARCHAR(64) NOT NULL DEFAULT 'legacy',
            analysis_engine VARCHAR(16) NOT NULL DEFAULT 'rules',
            content_hash CHAR(64) DEFAULT '',
            adjudication_source VARCHAR(40) DEFAULT '',
            adjudication_json LONGTEXT,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            KEY idx_mom_index_analysis_run_id (run_id),
            KEY idx_mom_index_analysis_sector (sector),
            KEY idx_mom_index_analysis_post_id (post_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )
    analysis_columns = {
        "batch_id": "BIGINT UNSIGNED NULL",
        "analysis_profile": "VARCHAR(64) NOT NULL DEFAULT 'legacy'",
        "model_name": "VARCHAR(255) NOT NULL DEFAULT 'legacy'",
        "prompt_version": "VARCHAR(64) NOT NULL DEFAULT 'legacy'",
        "analysis_engine": "VARCHAR(16) NOT NULL DEFAULT 'rules'",
        "content_hash": "CHAR(64) DEFAULT ''",
        "position_status": "VARCHAR(16) NOT NULL DEFAULT 'unknown'",
        "market_outlook": "VARCHAR(16) NOT NULL DEFAULT 'unknown'",
        "sentiment_label": "VARCHAR(16) NOT NULL DEFAULT 'neutral'",
        "emotion_tags_json": "TEXT",
        "sentiment_confidence": "DECIMAL(6,4) NOT NULL DEFAULT 0",
        "content_type": "VARCHAR(16) NOT NULL DEFAULT 'opinion'",
        "adjudication_source": "VARCHAR(40) DEFAULT ''",
        "adjudication_json": "LONGTEXT",
    }
    for column, definition in analysis_columns.items():
        if not _column_exists(cur, "mom_index_analysis", column):
            cur.execute(f"ALTER TABLE mom_index_analysis ADD COLUMN {column} {definition}")


def _insert_run(cur, dashboard: Dict, sector_indices: Dict, total_posts: int) -> int:
    cur.execute(
        """
        INSERT INTO mom_index_runs (run_at, total_posts, sector_count, note, dashboard_json, sector_indices_json)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (
            beijing_now().strftime("%Y-%m-%d %H:%M:%S"),
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
                json.dumps(post, ensure_ascii=False, default=str),
            )


def _content_hash(post: Dict) -> str:
    payload = f"{post.get('title', '')}\n{post.get('content', '')}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _normalize_identity_text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().lower()


def _canonical_url(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        parts = urlsplit(raw)
    except ValueError:
        return raw
    ignored = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "spm", "from"}
    query = urlencode(
        sorted((key, val) for key, val in parse_qsl(parts.query, keep_blank_values=True) if key.lower() not in ignored)
    )
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, query, ""))


def post_content_key(post: Dict) -> str:
    """Return a stable cross-run identity for one source post."""
    platform = _normalize_identity_text(post.get("platform")) or "unknown"
    post_id = _normalize_identity_text(post.get("id") or post.get("post_id"))
    if post_id:
        identity = f"id|{platform}|{post_id}"
    else:
        canonical_url = _canonical_url(post.get("url"))
        if canonical_url:
            identity = f"url|{platform}|{canonical_url}"
        else:
            published_at = normalize_social_datetime(post.get("published_at") or post.get("date"))
            identity = "fallback|{}|{}|{}|{}|{}".format(
                platform,
                _normalize_identity_text(post.get("author")),
                published_at,
                _normalize_identity_text(post.get("title")),
                _normalize_identity_text(post.get("content")),
            )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _post_seen_at(post: Dict) -> datetime:
    normalized = normalize_social_datetime(post.get("collected_at"))
    if normalized:
        return datetime.strptime(normalized, "%Y-%m-%d %H:%M:%S")
    return beijing_now()


def _post_datetime(post: Dict):
    return normalize_social_datetime(post.get("published_at") or post.get("date")) or None


def _upsert_post_catalog(cur, run_id: int, all_posts: Dict[str, List[Dict]]) -> tuple[int, int]:
    catalog_rows = []
    sighting_rows = []
    for sector, posts in all_posts.items():
        for post in posts:
            if post.get("platform") == "xiaohongshu" and post.get("is_mock"):
                continue
            content_key = post_content_key(post)
            seen_at = _post_seen_at(post)
            catalog_rows.append((
                content_key, post.get("platform", "") or "unknown",
                str(post.get("id") or post.get("post_id") or ""), _canonical_url(post.get("url")),
                post.get("author", ""), post.get("title", ""), post.get("content", ""),
                _post_datetime(post), seen_at, seen_at, run_id, run_id,
                json.dumps(post, ensure_ascii=False, default=str),
            ))
            sighting_rows.append((
                run_id, content_key, sector, str(post.get("keyword", "") or "")[:255], seen_at,
            ))

    if catalog_rows:
        cur.executemany(
            """
            INSERT INTO mom_index_post_catalog (
                content_key, platform, post_id, canonical_url, author, title, content,
                post_datetime, first_seen_at, last_seen_at, first_run_id, last_run_id, raw_json
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
                last_seen_at=GREATEST(last_seen_at, VALUES(last_seen_at)),
                last_run_id=VALUES(last_run_id),
                canonical_url=IF(VALUES(canonical_url) <> '', VALUES(canonical_url), canonical_url),
                author=IF(VALUES(author) <> '', VALUES(author), author),
                title=IF(CHAR_LENGTH(VALUES(title)) > CHAR_LENGTH(COALESCE(title, '')), VALUES(title), title),
                content=IF(CHAR_LENGTH(VALUES(content)) > CHAR_LENGTH(COALESCE(content, '')), VALUES(content), content),
                post_datetime=COALESCE(post_datetime, VALUES(post_datetime)),
                raw_json=IF(CHAR_LENGTH(VALUES(content)) >= CHAR_LENGTH(COALESCE(content, '')), VALUES(raw_json), raw_json)
            """,
            catalog_rows,
        )
    if sighting_rows:
        cur.executemany(
            """
            INSERT INTO mom_index_post_sightings (run_id, content_key, sector, keyword, collected_at)
            VALUES (%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE collected_at=VALUES(collected_at)
            """,
            sighting_rows,
        )
    return len(catalog_rows), len(sighting_rows)


def _iter_analysis_rows(run_id: int, analysis_results: Dict[str, List], all_posts: Dict[str, List[Dict]], batch_id: int) -> Iterable[tuple]:
    from analyzer.llm_sentiment import llm_model_name, llm_profile, llm_prompt_version
    post_lookup = {
        (sector, str(post.get("id", ""))): post
        for sector, posts in all_posts.items() for post in posts
    }
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
                getattr(item, "sentiment_label", "neutral") or "neutral",
                json.dumps(getattr(item, "emotion_tags", []) or [], ensure_ascii=False),
                float(getattr(item, "sentiment_confidence", 0) or 0),
                item.intent,
                float(item.intent_strength),
                getattr(item, "position_status", "unknown") or "unknown",
                getattr(item, "market_outlook", "unknown") or "unknown",
                getattr(item, "content_type", "opinion") or "opinion",
                json.dumps(item.key_signals, ensure_ascii=False),
                item.reasoning,
                json.dumps(item.matched_newbie, ensure_ascii=False),
                json.dumps(item.matched_pro, ensure_ascii=False),
                batch_id,
                llm_profile(),
                llm_model_name(),
                llm_prompt_version(),
                getattr(item, "sentiment_source", "rules") or "rules",
                _content_hash(post_lookup.get((sector, str(item.post_id)), {})),
            )


def _insert_analysis_batch(cur, analysis_results: Dict[str, List]) -> int:
    from analyzer.llm_sentiment import llm_model_name, llm_profile, llm_prompt_version
    requested = sum(len(items) for items in analysis_results.values())
    llm_posts = sum(item.sentiment_source == "llm" for items in analysis_results.values() for item in items)
    failed_posts = sum(bool(getattr(item, "llm_error", "")) for items in analysis_results.values() for item in items)
    cur.execute(
        """
        INSERT INTO mom_index_analysis_batches (
            profile, model_name, prompt_version, machine_role, started_at,
            requested_posts, analyzed_posts, llm_posts, failed_posts, status, note
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'running', %s)
        """,
        (llm_profile(), llm_model_name(), llm_prompt_version(),
         os.environ.get("MOM_INDEX_RUNTIME_ROLE", "collector"), beijing_now(),
         requested, requested, llm_posts, failed_posts, "pipeline analysis"),
    )
    return int(cur.lastrowid)


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
            batch_id = _insert_analysis_batch(cur, analysis_results)

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
            _upsert_post_catalog(cur, run_id, all_posts)

            analysis_rows = list(_iter_analysis_rows(run_id, analysis_results, all_posts, batch_id))
            if analysis_rows:
                cur.executemany(
                    """
                    INSERT INTO mom_index_analysis (
                        run_id, sector, post_id, title, platform, newbie_score, newbie_confidence,
                        level, sentiment_score, sentiment_label, emotion_tags_json, sentiment_confidence, intent, intent_strength,
                        position_status, market_outlook, content_type, key_signals_json, reasoning,
                        matched_newbie_json, matched_pro_json, batch_id, analysis_profile,
                        model_name, prompt_version, analysis_engine, content_hash
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                              %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    analysis_rows,
                )
            cur.execute(
                """UPDATE mom_index_analysis_batches
                   SET status='completed', completed_at=%s, analyzed_posts=%s WHERE id=%s""",
                (beijing_now(), len(analysis_rows), batch_id),
            )

        conn.commit()
        return run_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def backfill_post_catalog(batch_size: int = 1000) -> Dict[str, int]:
    """Populate canonical post identities and sightings from legacy snapshots."""
    conn = _connect()
    scanned = 0
    last_id = 0
    try:
        with conn.cursor() as cur:
            _ensure_tables(cur)
            while True:
                cur.execute(
                    """
                    SELECT id, run_id, sector, platform, source_mode, post_id, title, content,
                           url, author, post_date, post_datetime, collected_at, raw_json
                    FROM mom_index_posts
                    WHERE id > %s
                    ORDER BY id
                    LIMIT %s
                    """,
                    (last_id, max(1, batch_size)),
                )
                rows = cur.fetchall()
                if not rows:
                    break
                grouped: Dict[int, Dict[str, List[Dict]]] = defaultdict(lambda: defaultdict(list))
                for row in rows:
                    try:
                        raw = json.loads(row.get("raw_json") or "{}")
                    except (TypeError, json.JSONDecodeError):
                        raw = {}
                    post = dict(raw) if isinstance(raw, dict) else {}
                    post.update({
                        "platform": row.get("platform") or post.get("platform", ""),
                        "source_mode": row.get("source_mode") or post.get("source_mode", ""),
                        "id": row.get("post_id") or post.get("id", ""),
                        "title": row.get("title") or post.get("title", ""),
                        "content": row.get("content") or post.get("content", ""),
                        "url": row.get("url") or post.get("url", ""),
                        "author": row.get("author") or post.get("author", ""),
                        "date": row.get("post_date") or post.get("date", ""),
                        "published_at": row.get("post_datetime") or post.get("published_at", ""),
                        "collected_at": row.get("collected_at") or post.get("collected_at", ""),
                    })
                    grouped[int(row["run_id"])][row["sector"]].append(post)
                    last_id = int(row["id"])
                    scanned += 1
                for run_id, posts in grouped.items():
                    _upsert_post_catalog(cur, run_id, posts)
                conn.commit()

            cur.execute("SELECT COUNT(*) AS count FROM mom_index_post_catalog")
            unique_posts = int(cur.fetchone()["count"])
            cur.execute("SELECT COUNT(*) AS count FROM mom_index_post_sightings")
            sightings = int(cur.fetchone()["count"])
        return {"scanned": scanned, "unique_posts": unique_posts, "sightings": sightings}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def fetch_posts_for_analysis(
    days: int = 7,
    limit: int = 1000,
    analysis_profile: str = "",
    target_date: Optional[date] = None,
) -> Dict[str, List[Dict]]:
    """Load source posts by rolling window or one exact source date."""
    conn = _connect()
    try:
        with conn.cursor() as cur:
            _ensure_tables(cur)
            missing_clause = ""
            if target_date is not None:
                date_clause = "c.post_datetime >= %s AND c.post_datetime < %s"
                params: List[object] = [target_date, target_date + timedelta(days=1)]
            else:
                date_clause = "c.post_datetime >= DATE_SUB(NOW(), INTERVAL %s DAY)"
                params = [max(1, days)]
            if analysis_profile:
                missing_clause = """
                AND NOT EXISTS (
                    SELECT 1 FROM mom_index_analysis a
                    WHERE a.analysis_profile = %s
                      AND a.analysis_engine IN ('llm', 'insufficient', 'llm_failed')
                      AND a.platform = c.platform
                      AND (
                          (COALESCE(c.post_id, '') <> '' AND a.post_id = c.post_id)
                          OR (COALESCE(c.post_id, '') = '' AND a.content_hash =
                              SHA2(CONCAT(COALESCE(c.title, ''), '\n', COALESCE(c.content, '')), 256))
                      )
                )
                """
                params.append(analysis_profile)
            params.append(max(1, limit))
            cur.execute(
                f"""
                SELECT s.run_id, s.sector, c.platform, c.post_id AS id,
                       c.title, c.content, c.canonical_url AS url, c.author,
                       c.post_datetime AS published_at, s.collected_at, c.raw_json
                FROM mom_index_post_catalog c
                INNER JOIN (
                    SELECT content_key, MAX(id) AS newest_id
                    FROM mom_index_post_sightings
                    GROUP BY content_key
                ) latest ON latest.content_key = c.content_key
                INNER JOIN mom_index_post_sightings s ON s.id = latest.newest_id
                WHERE {date_clause}
                {missing_clause}
                ORDER BY c.post_datetime DESC
                LIMIT %s
                """,
                tuple(params),
            )
            rows = cur.fetchall()
        result: Dict[str, List[Dict]] = defaultdict(list)
        for row in rows:
            try:
                raw = json.loads(row.get("raw_json") or "{}")
            except (TypeError, json.JSONDecodeError):
                raw = {}
            post = dict(raw) if isinstance(raw, dict) else {}
            post.update({key: value for key, value in row.items() if key != "raw_json"})
            if isinstance(post.get("published_at"), datetime):
                post["published_at"] = post["published_at"].strftime("%Y-%m-%d %H:%M:%S")
            post.pop("raw_json", None)
            result[post.pop("sector")].append(post)
        return dict(result)
    finally:
        conn.close()


def fetch_reference_analyses(
    posts: Dict[str, List[Dict]], analysis_profile: str = "mini-14b",
    analysis_engines: tuple[str, ...] = ("llm",),
) -> Dict[str, Dict]:
    """Return the latest stored LLM analysis for each requested source post."""
    wanted_ids = set()
    wanted_hashes = set()
    for items in posts.values():
        for post in items:
            platform = _normalize_identity_text(post.get("platform"))
            post_id = _normalize_identity_text(post.get("id") or post.get("post_id"))
            if post_id:
                wanted_ids.add((platform, post_id))
            else:
                wanted_hashes.add((platform, _content_hash(post)))

    conn = _connect()
    try:
        with conn.cursor() as cur:
            _ensure_tables(cur)
            placeholders = ",".join(["%s"] * len(analysis_engines))
            cur.execute(
                f"""SELECT id,batch_id,analysis_profile,model_name,prompt_version,analysis_engine,
                          platform,post_id,content_hash,content_type,sentiment_score,
                          sentiment_label,emotion_tags_json,sentiment_confidence,intent,
                          intent_strength,position_status,market_outlook,newbie_score,
                          newbie_confidence,level,reasoning
                   FROM mom_index_analysis
                   WHERE analysis_profile=%s AND analysis_engine IN ({placeholders})
                   ORDER BY id DESC""",
                (analysis_profile, *analysis_engines),
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    references: Dict[str, Dict] = {}
    for row in rows:
        platform = _normalize_identity_text(row.get("platform"))
        post_id = _normalize_identity_text(row.get("post_id"))
        content_hash = str(row.get("content_hash") or "")
        if post_id and (platform, post_id) in wanted_ids:
            key = f"id|{platform}|{post_id}"
        elif content_hash and (platform, content_hash) in wanted_hashes:
            key = f"hash|{platform}|{content_hash}"
        else:
            continue
        references.setdefault(key, dict(row))
    return references


def reference_analysis_key(post: Dict) -> str:
    """Build the lookup key used by fetch_reference_analyses."""
    platform = _normalize_identity_text(post.get("platform"))
    post_id = _normalize_identity_text(post.get("id") or post.get("post_id"))
    if post_id:
        return f"id|{platform}|{post_id}"
    return f"hash|{platform}|{_content_hash(post)}"


def filter_posts_pending_analysis(
    all_posts: Dict[str, List[Dict]], analysis_profile: str
) -> tuple[Dict[str, List[Dict]], Dict[str, int]]:
    """Keep each source post once and skip successful analyses for this profile."""
    unique_posts: Dict[str, List[Dict]] = {sector: [] for sector in all_posts}
    seen_content_keys = set()
    duplicate_count = 0
    analyzed_count = 0

    conn = _connect()
    try:
        with conn.cursor() as cur:
            _ensure_tables(cur)
            cur.execute(
                """SELECT platform, post_id, content_hash
                   FROM mom_index_analysis
                   WHERE analysis_profile=%s AND analysis_engine IN ('llm','insufficient','llm_failed')""",
                (analysis_profile,),
            )
            analyzed_rows = cur.fetchall()
    finally:
        conn.close()

    analyzed_ids = {
        (_normalize_identity_text(row.get("platform")), _normalize_identity_text(row.get("post_id")))
        for row in analyzed_rows
        if _normalize_identity_text(row.get("post_id"))
    }
    analyzed_hashes = {
        (_normalize_identity_text(row.get("platform")), str(row.get("content_hash") or ""))
        for row in analyzed_rows
        if str(row.get("content_hash") or "")
    }

    for sector, posts in all_posts.items():
        for post in posts:
            content_key = post_content_key(post)
            if content_key in seen_content_keys:
                duplicate_count += 1
                continue
            seen_content_keys.add(content_key)

            platform = _normalize_identity_text(post.get("platform"))
            post_id = _normalize_identity_text(post.get("id") or post.get("post_id"))
            already_analyzed = (
                (bool(post_id) and (platform, post_id) in analyzed_ids)
                or (not post_id and (platform, _content_hash(post)) in analyzed_hashes)
            )
            if already_analyzed:
                analyzed_count += 1
                continue
            unique_posts[sector].append(post)

    return unique_posts, {
        "input": sum(len(posts) for posts in all_posts.values()),
        "pending": sum(len(posts) for posts in unique_posts.values()),
        "duplicates": duplicate_count,
        "already_analyzed": analyzed_count,
    }


def fetch_latest_collection_today() -> Optional[Dict]:
    """Load the latest Beijing-time collection run from today, if one exists."""
    if not mysql_enabled():
        return None

    conn = _connect()
    try:
        with conn.cursor() as cur:
            _ensure_tables(cur)
            cur.execute(
                """
                SELECT r.id, r.run_at
                FROM mom_index_runs r
                WHERE r.run_at >= CURDATE()
                  AND r.run_at < DATE_ADD(CURDATE(), INTERVAL 1 DAY)
                  AND EXISTS (
                      SELECT 1 FROM mom_index_posts p WHERE p.run_id = r.id
                  )
                ORDER BY r.run_at DESC, r.id DESC
                LIMIT 1
                """
            )
            run = cur.fetchone()
            if not run:
                return None

            cur.execute(
                """
                SELECT run_id, sector, platform, source_mode, post_id AS id,
                       title, content, url, author, post_date AS date,
                       post_datetime AS published_at, collected_at, raw_json
                FROM mom_index_posts
                WHERE run_id = %s
                ORDER BY sector, post_datetime DESC, id DESC
                """,
                (run["id"],),
            )
            rows = cur.fetchall()

        posts: Dict[str, List[Dict]] = defaultdict(list)
        for row in rows:
            try:
                raw = json.loads(row.get("raw_json") or "{}")
            except (TypeError, json.JSONDecodeError):
                raw = {}
            post = dict(raw) if isinstance(raw, dict) else {}
            post.update({
                "run_id": int(row["run_id"]),
                "id": row.get("id") or post.get("id", ""),
                "platform": row.get("platform") or post.get("platform", ""),
                "source_mode": row.get("source_mode") or post.get("source_mode", ""),
                "title": row.get("title") or post.get("title", ""),
                "content": row.get("content") or post.get("content", ""),
                "url": row.get("url") or post.get("url", ""),
                "author": row.get("author") or post.get("author", ""),
                "date": row.get("date") or post.get("date", ""),
                "collected_at": row.get("collected_at") or post.get("collected_at", ""),
            })
            published_at = row.get("published_at")
            if isinstance(published_at, datetime):
                published_at = published_at.strftime("%Y-%m-%d %H:%M:%S")
            post["published_at"] = published_at or post.get("published_at", "")
            posts[row["sector"]].append(post)

        run_at = run.get("run_at")
        if isinstance(run_at, datetime):
            run_at = run_at.strftime("%Y-%m-%d %H:%M:%S")
        return {"run_id": int(run["id"]), "run_at": run_at or "", "posts": dict(posts)}
    finally:
        conn.close()


def persist_standalone_analysis(analysis_results: Dict[str, List], posts: Dict[str, List[Dict]], note: str = "on-demand analysis") -> int:
    """Persist an analysis-only batch while retaining each post's source run id."""
    from analyzer.llm_sentiment import llm_model_name, llm_profile, llm_prompt_version
    from runtime_config import ini_get
    conn = _connect()
    try:
        with conn.cursor() as cur:
            _ensure_tables(cur)
            requested = sum(len(items) for items in analysis_results.values())
            llm_posts = sum(item.sentiment_source == "llm" for items in analysis_results.values() for item in items)
            failed_posts = sum(bool(getattr(item, "llm_error", "")) for items in analysis_results.values() for item in items)
            cur.execute(
                """INSERT INTO mom_index_analysis_batches
                   (profile, model_name, prompt_version, machine_role, started_at,
                    requested_posts, analyzed_posts, llm_posts, failed_posts, status, note)
                   VALUES (%s,%s,%s,%s,%s,%s,0,%s,%s,'running',%s)""",
                (llm_profile(), llm_model_name(), llm_prompt_version(),
                 os.environ.get("MOM_INDEX_RUNTIME_ROLE", "").strip()
                 or ini_get("runtime", "role", "analyst"), beijing_now(), requested,
                 llm_posts, failed_posts, note),
            )
            batch_id = int(cur.lastrowid)
            post_lookup = {
                (sector, str(post.get("id", ""))): post
                for sector, items in posts.items() for post in items
            }
            rows = []
            for sector, results in analysis_results.items():
                for item in results:
                    post = post_lookup.get((sector, str(item.post_id)), {})
                    rows.append((
                        int(post.get("run_id") or 0), sector, item.post_id, item.title, item.platform,
                        float(item.newbie_score), item.newbie_confidence, item.level,
                        float(item.sentiment_score),
                        getattr(item, "sentiment_label", "neutral") or "neutral",
                        json.dumps(getattr(item, "emotion_tags", []) or [], ensure_ascii=False),
                        float(getattr(item, "sentiment_confidence", 0) or 0),
                        item.intent, float(item.intent_strength),
                        getattr(item, "position_status", "unknown") or "unknown",
                        getattr(item, "market_outlook", "unknown") or "unknown",
                        getattr(item, "content_type", "opinion") or "opinion",
                        json.dumps(item.key_signals, ensure_ascii=False), item.reasoning,
                        json.dumps(item.matched_newbie, ensure_ascii=False),
                        json.dumps(item.matched_pro, ensure_ascii=False), batch_id, llm_profile(),
                        llm_model_name(), llm_prompt_version(), item.sentiment_source,
                        _content_hash(post),
                        getattr(item, "adjudication_source", "") or "",
                        getattr(item, "adjudication_json", "") or "",
                    ))
            if rows:
                cur.executemany(
                    """INSERT INTO mom_index_analysis
                       (run_id,sector,post_id,title,platform,newbie_score,newbie_confidence,
                        level,sentiment_score,sentiment_label,emotion_tags_json,sentiment_confidence,intent,intent_strength,
                        position_status,market_outlook,content_type,key_signals_json,reasoning,
                        matched_newbie_json,matched_pro_json,batch_id,analysis_profile,model_name,
                        prompt_version,analysis_engine,content_hash,adjudication_source,adjudication_json)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    rows,
                )
            cur.execute(
                """UPDATE mom_index_analysis_batches SET status='completed', completed_at=%s,
                   analyzed_posts=%s WHERE id=%s""",
                (beijing_now(), len(rows), batch_id),
            )
        conn.commit()
        return batch_id
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
    score = float(row.get("sentiment_score") or 0)
    label = row.get("sentiment_label") or "neutral"
    if label == "neutral" and score:
        label = "greed" if score > 0 else "fear"
    engine = row.get("analysis_engine") or "rules"
    content_type = "news" if row.get("level") == "资讯帖" else (row.get("content_type") or "opinion")
    return SimpleNamespace(
        post_id=row.get("post_id", "") or "",
        title=row.get("title", "") or "",
        platform=row.get("platform", "") or "unknown",
        newbie_score=float(row.get("newbie_score") or 0),
        level=row.get("level", "") or "",
        reasoning="",
        sentiment_score=score,
        sentiment_label=label,
        emotion_tags=_parse_json_list(row.get("emotion_tags_json")),
        sentiment_confidence=float(row.get("sentiment_confidence") or 1),
        sentiment_source="llm" if engine == "llm" else engine,
        intent=row.get("intent", "") or "neutral",
        intent_strength=float(row.get("intent_strength") or 0),
        position_status=row.get("position_status", "unknown") or "unknown",
        market_outlook=row.get("market_outlook", "unknown") or "unknown",
        content_type=content_type,
        key_signals=[],
    )


def _parse_json_list(value) -> List[str]:
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(value or "[]")
    except (json.JSONDecodeError, TypeError):
        return []
    return parsed if isinstance(parsed, list) else []


def _parse_json_dict(value) -> Dict:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _week_start(day: date) -> str:
    monday = day - timedelta(days=day.weekday())
    return monday.isoformat()


def fetch_keyword_history(profile: Optional[str] = None) -> Dict[str, Dict]:
    """
    从 MySQL 回捞关键词历史，输出日线/周线。
    依赖 mom_index_posts.raw_json 里的 keyword 字段，以及 analysis 表里的情绪结果。
    """
    if not mysql_enabled():
        return {}

    conn = _connect()
    try:
        with conn.cursor() as cur:
            _ensure_tables(cur)
            if not profile:
                from analyzer.llm_sentiment import llm_profile
                profile = llm_profile()
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
                    a.sentiment_label,
                    a.emotion_tags_json,
                    a.sentiment_confidence,
                    a.intent,
                    a.intent_strength,
                    a.position_status,
                    a.market_outlook,
                    a.content_type,
                    a.analysis_engine
                FROM mom_index_posts p
                INNER JOIN mom_index_analysis a
                    ON a.run_id = p.run_id
                   AND a.sector = p.sector
                   AND a.post_id = p.post_id
                   AND a.platform = p.platform
                WHERE a.analysis_profile = %s
                ORDER BY p.post_datetime ASC, p.id ASC
                """,
                (profile,),
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
                "analysis_profile": profile,
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


MODEL_SECTOR_ORDER = ("nasdaq", "gold", "cpo", "semiconductor", "storage")


def _ordered_sectors(values) -> List[str]:
    available = set(values)
    return [item for item in MODEL_SECTOR_ORDER if item in available] + sorted(available - set(MODEL_SECTOR_ORDER))


def _analysis_key(row: Dict):
    """Build a stable cross-batch identity for one collected post."""
    sector = str(row.get("sector") or "").strip()
    platform = str(row.get("platform") or "").strip()
    post_id = str(row.get("post_id") or "").strip()
    content_hash = str(row.get("content_hash") or "").strip()
    identity = post_id or content_hash
    return (sector, platform, identity) if sector and platform and identity else None


def _latest_model_batches(cur) -> List[Dict]:
    cur.execute(
        """
        SELECT b.id, b.profile, b.model_name, b.prompt_version, b.completed_at,
               b.requested_posts, b.analyzed_posts, b.llm_posts, b.failed_posts
        FROM mom_index_analysis_batches b
        INNER JOIN (
            SELECT profile, MAX(id) AS latest_id
            FROM mom_index_analysis_batches WHERE status='completed'
            GROUP BY profile
        ) latest ON latest.latest_id=b.id
        WHERE b.profile NOT IN ('legacy', 'rules')
        ORDER BY b.completed_at DESC
        """
    )
    return cur.fetchall()


def _default_comparison_day(anchor: Dict, anchor_rows: List[Dict]) -> date:
    source_days = {_row_day(row) for row in anchor_rows}
    source_days.discard(None)
    completed_at = anchor.get("completed_at") or anchor.get("started_at")
    if isinstance(completed_at, datetime) and completed_at.hour < 6:
        previous_day = completed_at.date() - timedelta(days=1)
        if previous_day in source_days:
            return previous_day
    return max(source_days, default=beijing_now().date())


def _comparison_model_batches(cur, target_day: Optional[date] = None):
    """Use newest production/local profiles and align batches by same-post overlap."""
    latest_batches = _latest_model_batches(cur)
    if not latest_batches:
        return [], {}, target_day
    anchor = latest_batches[0]
    anchor_rows = _batch_analysis_rows(cur, anchor["id"])
    if target_day is None:
        target_day = _default_comparison_day(anchor, anchor_rows)
    preferred = ("local-consensus-cloud", "macbook-27b", "mini-14b", "mini-17b")
    by_profile = {batch["profile"]: batch for batch in latest_batches}
    profiles = [profile for profile in preferred if profile in by_profile][:3]
    if anchor["profile"] not in profiles:
        profiles.insert(0, anchor["profile"])
        profiles = profiles[:3]
    if len(profiles) < 2:
        return [anchor], {anchor["profile"]: anchor_rows}, target_day

    placeholders = ",".join(["%s"] * len(profiles))
    cur.execute(
        f"""
        SELECT id, profile, model_name, prompt_version, completed_at,
               requested_posts, analyzed_posts, llm_posts, failed_posts
        FROM mom_index_analysis_batches
        WHERE status='completed' AND profile IN ({placeholders})
        ORDER BY completed_at DESC, id DESC
        """,
        tuple(profiles),
    )
    candidates = defaultdict(list)
    for batch in cur.fetchall():
        if len(candidates[batch["profile"]]) < 14:
            candidates[batch["profile"]].append(batch)

    candidate_ids = [batch["id"] for profile in profiles for batch in candidates[profile]]
    keys_by_batch = {batch_id: set() for batch_id in candidate_ids}
    if candidate_ids:
        id_placeholders = ",".join(["%s"] * len(candidate_ids))
        next_day = target_day + timedelta(days=1)
        cur.execute(
            f"""
            SELECT DISTINCT a.batch_id, a.sector, a.platform, a.post_id, a.content_hash
            FROM mom_index_analysis a
            INNER JOIN mom_index_posts p
              ON p.run_id=a.run_id AND p.sector=a.sector
             AND p.platform=a.platform AND a.post_id <> '' AND p.post_id=a.post_id
            WHERE a.batch_id IN ({id_placeholders})
              AND a.analysis_engine='llm'
              AND p.post_datetime >= %s AND p.post_datetime < %s
            """,
            tuple(candidate_ids) + (target_day, next_day),
        )
        for row in cur.fetchall():
            key = _analysis_key(row)
            if key:
                keys_by_batch[row["batch_id"]].add(key)

    anchor_keys = keys_by_batch.get(anchor["id"], set())
    selected = [anchor]
    for profile in profiles:
        if profile == anchor["profile"]:
            continue
        matched = max(
            candidates[profile],
            key=lambda batch: (len(anchor_keys & keys_by_batch[batch["id"]]), int(batch["id"])),
            default=None,
        )
        overlap = len(anchor_keys & keys_by_batch.get(matched["id"], set())) if matched else 0
        if matched and overlap:
            selected.append(matched)
    selected.sort(key=lambda batch: batch.get("completed_at") or datetime.min, reverse=True)
    return selected, {
        batch["profile"]: anchor_rows if batch["id"] == anchor["id"] else _batch_analysis_rows(cur, batch["id"])
        for batch in selected
    }, target_day


def _batch_analysis_rows(cur, batch_id: int) -> List[Dict]:
    cur.execute(
        """SELECT run_id, sector, post_id, title, platform, newbie_score,
                  newbie_confidence, level, sentiment_score, sentiment_label, emotion_tags_json,
                  sentiment_confidence, intent, intent_strength, position_status,
                  market_outlook, content_type, analysis_engine, reasoning,
                  content_hash,
                  (SELECT MAX(p.post_datetime)
                     FROM mom_index_posts p
                    WHERE p.run_id=mom_index_analysis.run_id
                      AND p.sector=mom_index_analysis.sector
                      AND p.platform=mom_index_analysis.platform
                      AND mom_index_analysis.post_id <> ''
                      AND p.post_id=mom_index_analysis.post_id) AS post_datetime
           FROM mom_index_analysis WHERE batch_id=%s ORDER BY id DESC""",
        (batch_id,),
    )
    return cur.fetchall()


def _row_day(row: Dict) -> Optional[date]:
    value = row.get("post_datetime")
    if isinstance(value, datetime):
        return value.date()
    if value:
        try:
            return datetime.fromisoformat(str(value)).date()
        except ValueError:
            return None
    return None


def _shared_llm_keys(rows_by_profile: Dict[str, List[Dict]], target_day: Optional[date] = None) -> set:
    """Only rows successfully judged by every model are comparable."""
    key_sets = []
    for rows in rows_by_profile.values():
        keys = {
            key for row in rows
            if row.get("analysis_engine") == "llm"
            and (target_day is None or _row_day(row) == target_day)
            and (key := _analysis_key(row))
        }
        key_sets.append(keys)
    return set.intersection(*key_sets) if key_sets else set()


def _disagreement_keys(rows_by_profile: Dict[str, List[Dict]], target_day: Optional[date] = None) -> set:
    """Flag large opinion-score gaps only across distinct models on the same post."""
    scores = defaultdict(dict)
    for profile, rows in rows_by_profile.items():
        for row in rows:
            if row.get("analysis_engine") != "llm" or row.get("content_type") != "opinion":
                continue
            day = _row_day(row)
            key = _analysis_key(row)
            if not key or (target_day is not None and day != target_day):
                continue
            scores[(day, key)].setdefault(profile, float(row.get("sentiment_score") or 0) * 100)
    return {
        (day, key) for (day, key), by_profile in scores.items()
        if len(by_profile) > 1 and (
            max(by_profile.values()) - min(by_profile.values()) >= 30
            or (min(by_profile.values()) < -15 and max(by_profile.values()) > 15)
        )
    }


def _weighted_analysis(row: Dict, disagreement_keys: set) -> SimpleNamespace:
    item = _to_analysis_like(row)
    # 本地原始模型对尚未解决的分歧降权；生产 profile 已采用27B或云端裁决，
    # 最终结果不应再次因为曾经存在分歧而受到惩罚。
    is_production_final = row.get("analysis_profile") == "local-consensus-cloud"
    is_disagreement = (_row_day(row), _analysis_key(row)) in disagreement_keys
    item.disagreement_weight = 0.25 if is_disagreement and not is_production_final else 1.0
    return item


def fetch_model_comparison() -> Dict:
    """Compare newest model batches on their exact shared, successful LLM sample."""
    if not mysql_enabled():
        return {"profiles": []}
    conn = _connect()
    try:
        with conn.cursor() as cur:
            _ensure_tables(cur)
            batches, rows_by_profile, target_day = _comparison_model_batches(cur)
            shared_keys = _shared_llm_keys(rows_by_profile, target_day)
            disagreement_keys = _disagreement_keys(rows_by_profile, target_day)
            profiles = []
            from analyzer.index_calculator import compute_sector_index
            for batch in batches:
                unique_rows = {}
                for row in rows_by_profile[batch["profile"]]:
                    key = _analysis_key(row)
                    if key in shared_keys and row.get("analysis_engine") == "llm":
                        unique_rows.setdefault(key, row)
                by_sector = defaultdict(list)
                for row in unique_rows.values():
                    by_sector[row["sector"]].append(_weighted_analysis(row, disagreement_keys))
                completed = batch.get("completed_at")
                profiles.append({
                    "profile": batch["profile"],
                    "model_name": batch["model_name"],
                    "prompt_version": batch["prompt_version"],
                    "completed_at": completed.strftime("%Y-%m-%d %H:%M:%S") if completed else "",
                    "requested_posts": batch["requested_posts"],
                    "analyzed_posts": batch["analyzed_posts"],
                    "llm_posts": batch["llm_posts"],
                    "failed_posts": batch["failed_posts"],
                    "comparison_posts": len(shared_keys),
                    "disagreement_posts": len(disagreement_keys),
                    "comparison_scope": "shared-successful-llm-posts",
                    "comparison_date": target_day.isoformat(),
                    "sectors": {},
                })
                for sector in _ordered_sectors(by_sector):
                    if not by_sector.get(sector):
                        continue
                    sector_result = compute_sector_index(by_sector[sector])
                    sector_result.setdefault("details", {})["analysis_window"] = {
                        "mode": "day",
                        "label": f"北京时间 {target_day.isoformat()}",
                        "sample_count": len(by_sector[sector]),
                    }
                    profiles[-1]["sectors"][sector] = sector_result
            return {
                "timezone": "Asia/Shanghai",
                "comparison_scope": "最新模型结果；其他模型仅在覆盖同一发布日期及同一帖子时加入对比",
                "comparison_date": target_day.isoformat(),
                "comparison_posts": len(shared_keys),
                "disagreement_posts": len(disagreement_keys),
                "profiles": profiles,
            }
    finally:
        conn.close()


def fetch_production_summary() -> Dict:
    """Return coverage and final-source metrics for the three-stage model pipeline."""
    if not mysql_enabled():
        return {}
    profiles = ("mini-14b", "macbook-27b", "local-consensus-cloud")
    conn = _connect()
    try:
        with conn.cursor() as cur:
            _ensure_tables(cur)
            cur.execute("SELECT COUNT(*) AS n FROM mom_index_post_catalog")
            collected = int(cur.fetchone()["n"])
            rows_by_profile = {}
            llm_keys_by_profile = {}
            latest_by_profile = {}
            source_by_key = {}
            review_keys = set()
            for profile in profiles:
                cur.execute(
                    """SELECT id,sector,platform,post_id,content_hash,created_at,analysis_engine,
                              adjudication_source,adjudication_json
                       FROM mom_index_analysis
                       WHERE analysis_profile=%s AND analysis_engine IN ('llm','insufficient','llm_failed')
                       ORDER BY id DESC""",
                    (profile,),
                )
                keys = set()
                llm_keys = set()
                latest = None
                for row in cur.fetchall():
                    key = _analysis_key(row)
                    if not key or key in keys:
                        continue
                    keys.add(key)
                    if row.get("analysis_engine") == "llm":
                        llm_keys.add(key)
                    latest = latest or row.get("created_at")
                    if profile == "local-consensus-cloud":
                        source = row.get("adjudication_source") or "unknown"
                        source_by_key[key] = source
                        try:
                            audit = json.loads(row.get("adjudication_json") or "{}")
                        except (TypeError, json.JSONDecodeError):
                            audit = {}
                        if audit.get("requires_review") or "failure" in source:
                            review_keys.add(key)
                rows_by_profile[profile] = keys
                llm_keys_by_profile[profile] = llm_keys
                latest_by_profile[profile] = latest.strftime("%Y-%m-%d %H:%M:%S") if latest else ""

        small = rows_by_profile["mini-14b"]
        large = rows_by_profile["macbook-27b"]
        final = rows_by_profile["local-consensus-cloud"]
        paired = llm_keys_by_profile["mini-14b"] & llm_keys_by_profile["macbook-27b"]
        source_counts = defaultdict(int)
        for source in source_by_key.values():
            source_counts[source] += 1
        return {
            "collected_posts": collected,
            "small_model_posts": len(small),
            "large_model_posts": len(large),
            "paired_posts": len(paired),
            "final_posts": len(final),
            "waiting_27b": len(small - large),
            "waiting_final": len(paired - final),
            "cloud_adjudications": sum(
                count for source, count in source_counts.items()
                if source in {"cloud_adjudication", "cloud_after_27b_failure"}
            ),
            "local_agreements": source_counts.get("stored_agreement_27b", 0),
            "review_posts": len(review_keys),
            "source_counts": dict(source_counts),
            "latest_by_profile": latest_by_profile,
            "production_profile": "local-consensus-cloud",
        }
    finally:
        conn.close()


def fetch_post_model_comparison(target_day: Optional[date] = None) -> Dict:
    """Return latest source-day posts across local and production profiles."""
    if not mysql_enabled():
        return {"profiles": [], "items": []}
    conn = _connect()
    try:
        with conn.cursor() as cur:
            _ensure_tables(cur)
            preferred_profiles = ("mini-14b", "macbook-27b", "local-consensus-cloud")
            profile_placeholders = ",".join(["%s"] * len(preferred_profiles))
            if target_day is None:
                # A newly completed backlog batch may contain older posts. The detail
                # page date must follow source-post recency, not batch completion time.
                cur.execute(
                    f"""
                    SELECT MAX(p.post_datetime) AS latest_post_datetime
                    FROM mom_index_analysis a
                    INNER JOIN mom_index_posts p
                      ON p.run_id=a.run_id AND p.sector=a.sector
                     AND p.platform=a.platform AND a.post_id <> '' AND p.post_id=a.post_id
                    WHERE a.analysis_profile IN ({profile_placeholders})
                    """,
                    preferred_profiles,
                )
                latest = cur.fetchone().get("latest_post_datetime")
                target_day = latest.date() if isinstance(latest, datetime) else None
            if target_day is None:
                return {"profiles": [], "items": []}
            next_day = target_day + timedelta(days=1)
            cur.execute(
                f"""
                SELECT a.batch_id, a.run_id, a.analysis_profile, a.model_name,
                       a.prompt_version, a.analysis_engine, a.content_hash,
                       a.sector, a.platform, a.post_id, a.title, a.level,
                       a.sentiment_score, a.sentiment_label, a.emotion_tags_json, a.sentiment_confidence,
                       a.intent, a.intent_strength, a.position_status,
                       a.market_outlook, a.content_type, a.reasoning,
                       a.adjudication_source, a.adjudication_json,
                       b.completed_at AS batch_completed_at,
                       p.content, p.url, p.author, p.post_date, p.post_datetime,
                       p.raw_json
                FROM mom_index_analysis a
                LEFT JOIN mom_index_analysis_batches b ON b.id=a.batch_id
                LEFT JOIN mom_index_posts p
                  ON p.run_id=a.run_id AND p.sector=a.sector
                 AND p.platform=a.platform AND a.post_id <> '' AND p.post_id=a.post_id
                WHERE a.analysis_profile IN ({profile_placeholders})
                  AND p.post_datetime >= %s AND p.post_datetime < %s
                ORDER BY COALESCE(p.post_datetime, a.created_at) DESC, a.id DESC
                """,
                preferred_profiles + (target_day, next_day),
            )
            rows = cur.fetchall()

        grouped = {}
        available_profiles = set()
        for row in rows:
            if _row_day(row) != target_day:
                continue
            key = _analysis_key(row)
            if not key:
                continue
            item = grouped.setdefault(key, {
                "sector": row.get("sector") or "",
                "platform": row.get("platform") or "unknown",
                "post_id": row.get("post_id") or "",
                "title": row.get("title") or "",
                "content": row.get("content") or "",
                "url": row.get("url") or "",
                "author": row.get("author") or "",
                "source_datetime": row["post_datetime"].strftime("%Y-%m-%d %H:%M:%S") if row.get("post_datetime") else (row.get("post_date") or ""),
                "keyword": "",
                "analyses": {},
            })
            try:
                raw = json.loads(row.get("raw_json") or "{}")
                item["keyword"] = item["keyword"] or raw.get("keyword", "")
            except (json.JSONDecodeError, TypeError):
                pass
            profile = row.get("analysis_profile") or "unknown"
            if profile in item["analyses"]:
                continue
            available_profiles.add(profile)
            item["analyses"][profile] = {
                "profile": profile,
                "model_name": row.get("model_name") or "",
                "batch_id": row.get("batch_id"),
                "completed_at": row["batch_completed_at"].strftime("%Y-%m-%d %H:%M:%S") if row.get("batch_completed_at") else "",
                "prompt_version": row.get("prompt_version") or "",
                "analysis_engine": row.get("analysis_engine") or "rules",
                "content_type": "news" if row.get("level") == "资讯帖" else (row.get("content_type") or "opinion"),
                "sentiment_score": float(row.get("sentiment_score") or 0),
                "sentiment_label": row.get("sentiment_label") or "neutral",
                "emotion_tags": _parse_json_list(row.get("emotion_tags_json")),
                "sentiment_confidence": float(row.get("sentiment_confidence") or 0),
                "intent": row.get("intent") or "neutral",
                "intent_strength": float(row.get("intent_strength") or 0),
                "position_status": row.get("position_status") or "unknown",
                "market_outlook": row.get("market_outlook") or "unknown",
                "reasoning": row.get("reasoning") or "",
                "adjudication_source": row.get("adjudication_source") or "",
                "adjudication": _parse_json_dict(row.get("adjudication_json")),
            }

        items = list(grouped.values())
        profiles = [profile for profile in preferred_profiles if profile in available_profiles]
        profile_order = {profile: index for index, profile in enumerate(profiles)}
        for item in items:
            item["analyses"] = sorted(
                item["analyses"].values(),
                key=lambda value: profile_order.get(value["profile"], 99),
            )
            item["model_count"] = sum(a["analysis_engine"] == "llm" for a in item["analyses"])
            item["content_length"] = len(item["content"])
        items.sort(key=lambda item: item.get("source_datetime") or "", reverse=True)
        return {
            "timezone": "Asia/Shanghai",
            "comparison_date": target_day.isoformat(),
            "profiles": profiles,
            "items": items,
        }
    finally:
        conn.close()


def fetch_model_daily_snapshots(days: int = 30) -> Dict:
    """Return complete daily sector cards per model profile for history browsing."""
    if not mysql_enabled():
        return {}
    conn = _connect()
    try:
        with conn.cursor() as cur:
            _ensure_tables(cur)
            start_day = beijing_now().date() - timedelta(days=max(1, days) - 1)
            cur.execute(
                """
                SELECT a.id, a.analysis_profile, a.sector, a.platform, a.post_id,
                       a.title, a.newbie_score, a.level, a.sentiment_score,
                       a.sentiment_label, a.emotion_tags_json, a.sentiment_confidence, a.intent,
                       a.intent_strength, a.position_status, a.market_outlook,
                       a.content_type, a.analysis_engine, p.post_datetime
                FROM mom_index_analysis a
                INNER JOIN mom_index_posts p
                  ON p.run_id=a.run_id AND p.sector=a.sector
                 AND p.platform=a.platform AND a.post_id <> '' AND p.post_id=a.post_id
                WHERE a.analysis_profile NOT IN ('legacy', 'rules')
                  AND a.analysis_engine='llm' AND p.post_datetime >= %s
                ORDER BY a.id DESC
                """,
                (start_day,),
            )
            rows = cur.fetchall()

        rows_by_profile = defaultdict(list)
        for row in rows:
            rows_by_profile[row["analysis_profile"]].append(row)
        disagreement_keys = _disagreement_keys(rows_by_profile)
        seen = set()
        grouped = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
        for row in rows:
            day = _row_day(row)
            if day is None:
                continue
            key = (row["analysis_profile"], day, row["sector"], row["platform"], row["post_id"])
            if key in seen:
                continue
            seen.add(key)
            grouped[row["analysis_profile"]][day.isoformat()][row["sector"]].append(
                _weighted_analysis(row, disagreement_keys)
            )

        from analyzer.index_calculator import compute_sector_index
        result = {}
        for profile, dates in grouped.items():
            snapshots = []
            for day_key, sectors in sorted(dates.items()):
                sector_results = {}
                for sector in _ordered_sectors(sectors):
                    items = sectors.get(sector)
                    if not items:
                        continue
                    value = compute_sector_index(items)
                    value.setdefault("details", {})["analysis_window"] = {
                        "mode": "day",
                        "label": f"北京时间 {day_key}",
                        "sample_count": len(items),
                    }
                    sector_results[sector] = value
                snapshots.append({"date": day_key, "sectors": sector_results})
            result[profile] = snapshots
        return result
    finally:
        conn.close()
