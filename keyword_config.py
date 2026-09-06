"""One shared keyword list for collection, classification, and de-duplication."""
from __future__ import annotations

import re
from typing import Dict, List, Optional

from runtime_config import ini_get


# Every term becomes one search request on Xiaohongshu, Weibo, and Xueqiu.
DEFAULT_KEYWORDS: Dict[str, List[str]] = {
    "nasdaq": ["美股怎么买", "纳斯达克新手", "纳指还能买吗", "纳指ETF", "美股定投", "买美股"],
    "gold": ["黄金怎么买", "买黄金亏了", "黄金新手", "黄金还能涨吗", "黄金ETF", "买黄金"],
    "cpo": ["CPO还能买吗", "光模块还能涨吗", "通信ETF", "算力牛市"],
    "semiconductor": ["芯片还能买吗", "半导体新手", "半导体ETF", "半导体追高", "AI芯片"],
    "storage": ["存储", "存储芯片", "海力士", "SK海力士", "HBM", "美光", "三星", "三星存储", "长鑫存储", "兆易创新", "西部数据", "闪迪"],
}


def _parse_list(raw: str) -> List[str]:
    return [item.strip() for item in re.split(r"[,，\n]", raw or "") if item.strip()]


def get_keywords() -> Dict[str, List[str]]:
    """Prefer shared MySQL keywords, then fall back to local config/built-ins."""
    db_keywords = _get_mysql_keywords()
    if db_keywords:
        return db_keywords
    result: Dict[str, List[str]] = {}
    for sector, fallback in DEFAULT_KEYWORDS.items():
        configured = _parse_list(ini_get("keywords", sector, ""))
        result[sector] = configured or list(fallback)
    return result


def _ensure_keyword_tables(cur) -> None:
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS mom_index_keywords (
            id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
            sector VARCHAR(32) NOT NULL,
            keyword VARCHAR(128) NOT NULL,
            enabled TINYINT(1) NOT NULL DEFAULT 1,
            sort_order INT NOT NULL DEFAULT 0,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY uk_mom_index_keyword (sector, keyword),
            KEY idx_mom_index_keywords_enabled (enabled, sector, sort_order)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS mom_index_keyword_changes (
            id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
            keyword_id BIGINT UNSIGNED NULL,
            sector VARCHAR(32) NOT NULL,
            keyword VARCHAR(128) NOT NULL,
            action VARCHAR(16) NOT NULL,
            changed_by VARCHAR(64) NOT NULL DEFAULT 'cli',
            changed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )


def _get_mysql_keywords() -> Dict[str, List[str]]:
    try:
        from storage.mysql_store import _connect, mysql_enabled
        if not mysql_enabled():
            return {}
        conn = _connect()
        try:
            with conn.cursor() as cur:
                _ensure_keyword_tables(cur)
                cur.execute(
                    """SELECT sector, keyword FROM mom_index_keywords
                       WHERE enabled=1 ORDER BY sector, sort_order, id"""
                )
                rows = cur.fetchall()
            conn.commit()
        finally:
            conn.close()
    except Exception:
        return {}
    result: Dict[str, List[str]] = {}
    for row in rows:
        result.setdefault(row["sector"], []).append(row["keyword"])
    return result


def sync_config_keywords_to_mysql(changed_by: str = "bootstrap") -> int:
    """Insert missing config keywords without overwriting DB enable choices."""
    configured: Dict[str, List[str]] = {}
    for sector, fallback in DEFAULT_KEYWORDS.items():
        values = _parse_list(ini_get("keywords", sector, ""))
        configured[sector] = values or list(fallback)
    from storage.mysql_store import _connect
    conn = _connect()
    inserted = 0
    try:
        with conn.cursor() as cur:
            _ensure_keyword_tables(cur)
            for sector, keywords in configured.items():
                for order, keyword in enumerate(keywords):
                    cur.execute(
                        """INSERT IGNORE INTO mom_index_keywords
                           (sector, keyword, enabled, sort_order) VALUES (%s, %s, 1, %s)""",
                        (sector, keyword, order),
                    )
                    if cur.rowcount:
                        inserted += 1
                        keyword_id = cur.lastrowid
                        cur.execute(
                            """INSERT INTO mom_index_keyword_changes
                               (keyword_id, sector, keyword, action, changed_by)
                               VALUES (%s, %s, %s, 'create', %s)""",
                            (keyword_id, sector, keyword, changed_by),
                        )
        conn.commit()
        return inserted
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def set_keyword(sector: str, keyword: str, enabled: bool, changed_by: str = "cli") -> None:
    if sector not in DEFAULT_KEYWORDS:
        raise ValueError(f"未知板块: {sector}")
    keyword = keyword.strip()
    if not keyword:
        raise ValueError("关键词不能为空")
    from storage.mysql_store import _connect
    conn = _connect()
    try:
        with conn.cursor() as cur:
            _ensure_keyword_tables(cur)
            cur.execute(
                """INSERT INTO mom_index_keywords (sector, keyword, enabled)
                   VALUES (%s, %s, %s)
                   ON DUPLICATE KEY UPDATE enabled=VALUES(enabled)""",
                (sector, keyword, int(enabled)),
            )
            cur.execute(
                "SELECT id FROM mom_index_keywords WHERE sector=%s AND keyword=%s",
                (sector, keyword),
            )
            keyword_id = cur.fetchone()["id"]
            cur.execute(
                """INSERT INTO mom_index_keyword_changes
                   (keyword_id, sector, keyword, action, changed_by)
                   VALUES (%s, %s, %s, %s, %s)""",
                (keyword_id, sector, keyword, "enable" if enabled else "disable", changed_by),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def list_keyword_records() -> List[Dict]:
    from storage.mysql_store import _connect
    conn = _connect()
    try:
        with conn.cursor() as cur:
            _ensure_keyword_tables(cur)
            cur.execute(
                """SELECT sector, keyword, enabled, sort_order, updated_at
                   FROM mom_index_keywords ORDER BY sector, sort_order, id"""
            )
            rows = cur.fetchall()
        return [
            {**row, "enabled": bool(row["enabled"]),
             "updated_at": row["updated_at"].strftime("%Y-%m-%d %H:%M:%S") if row.get("updated_at") else ""}
            for row in rows
        ]
    finally:
        conn.close()
