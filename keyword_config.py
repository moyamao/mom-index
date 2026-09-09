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

DEFAULT_SECTORS = {
    "nasdaq": {"name": "纳斯达克", "color": "#22d3ee"},
    "gold": {"name": "黄金", "color": "#fbbf24"},
    "cpo": {"name": "CPO通信", "color": "#a78bfa"},
    "semiconductor": {"name": "半导体", "color": "#34d399"},
    "storage": {"name": "存储", "color": "#fb7185"},
}

SECTOR_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{1,31}$")


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


def get_sector_catalog() -> List[Dict]:
    """Return enabled category metadata, with built-ins as an offline fallback."""
    try:
        rows = list_sector_records(enabled_only=True)
        if rows:
            return rows
    except Exception:
        pass
    return [
        {"code": code, "name": item["name"], "color": item["color"],
         "enabled": True, "sort_order": order}
        for order, (code, item) in enumerate(DEFAULT_SECTORS.items())
    ]


def _ensure_keyword_tables(cur) -> None:
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS mom_index_sectors (
            id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
            code VARCHAR(32) NOT NULL,
            name VARCHAR(64) NOT NULL,
            color VARCHAR(16) NOT NULL DEFAULT '#94a3b8',
            enabled TINYINT(1) NOT NULL DEFAULT 1,
            sort_order INT NOT NULL DEFAULT 0,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY uk_mom_index_sector_code (code),
            KEY idx_mom_index_sectors_enabled (enabled, sort_order)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )
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


def _seed_default_sectors(cur) -> None:
    """Seed built-ins from one transaction, never from concurrent read endpoints."""
    for order, (code, item) in enumerate(DEFAULT_SECTORS.items()):
        cur.execute(
            """INSERT IGNORE INTO mom_index_sectors
               (code, name, color, enabled, sort_order) VALUES (%s, %s, %s, 1, %s)""",
            (code, item["name"], item["color"], order),
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
                    """SELECT k.sector, k.keyword FROM mom_index_keywords k
                       INNER JOIN mom_index_sectors s ON s.code=k.sector AND s.enabled=1
                       WHERE k.enabled=1 ORDER BY s.sort_order, s.id, k.sort_order, k.id"""
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
            _seed_default_sectors(cur)
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
    keyword = keyword.strip()
    if not keyword:
        raise ValueError("关键词不能为空")
    from storage.mysql_store import _connect
    conn = _connect()
    try:
        with conn.cursor() as cur:
            _ensure_keyword_tables(cur)
            cur.execute("SELECT enabled FROM mom_index_sectors WHERE code=%s", (sector,))
            sector_row = cur.fetchone()
            if not sector_row:
                raise ValueError(f"未知板块: {sector}")
            if enabled and not sector_row["enabled"]:
                raise ValueError(f"板块已停用: {sector}")
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


def set_sector(code: str, name: str, color: str, enabled: bool) -> None:
    code = code.strip().lower()
    name = name.strip()
    color = color.strip().lower() or "#94a3b8"
    if not SECTOR_CODE_RE.fullmatch(code):
        raise ValueError("类目编码需以小写字母开头，仅允许小写字母、数字和下划线，长度 2-32")
    if not name or len(name) > 64:
        raise ValueError("类目名称不能为空且最多 64 个字符")
    if not re.fullmatch(r"#[0-9a-f]{6}", color):
        raise ValueError("颜色必须是 #RRGGBB 格式")
    from storage.mysql_store import _connect
    conn = _connect()
    try:
        with conn.cursor() as cur:
            _ensure_keyword_tables(cur)
            cur.execute(
                """INSERT INTO mom_index_sectors (code, name, color, enabled, sort_order)
                   VALUES (%s, %s, %s, %s, (SELECT next_order FROM
                     (SELECT COALESCE(MAX(sort_order), -1) + 1 AS next_order FROM mom_index_sectors) x))
                   ON DUPLICATE KEY UPDATE name=VALUES(name), color=VALUES(color), enabled=VALUES(enabled)""",
                (code, name, color, int(enabled)),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def list_sector_records(enabled_only: bool = False) -> List[Dict]:
    from storage.mysql_store import _connect
    conn = _connect()
    try:
        with conn.cursor() as cur:
            _ensure_keyword_tables(cur)
            _seed_default_sectors(cur)
            where = "WHERE enabled=1" if enabled_only else ""
            cur.execute(
                f"""SELECT code, name, color, enabled, sort_order, updated_at
                    FROM mom_index_sectors {where} ORDER BY sort_order, id"""
            )
            rows = cur.fetchall()
        conn.commit()
        return [
            {**row, "enabled": bool(row["enabled"]),
             "updated_at": row["updated_at"].strftime("%Y-%m-%d %H:%M:%S") if row.get("updated_at") else ""}
            for row in rows
        ]
    finally:
        conn.close()
