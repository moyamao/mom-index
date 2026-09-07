#!/usr/bin/env python3
"""Backfill global post identities and per-run sightings from legacy rows."""
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from storage.mysql_store import backfill_post_catalog


def main() -> None:
    result = backfill_post_catalog()
    print(
        "回填完成: 扫描 {scanned} 条历史记录，得到 {unique_posts} 个唯一帖子、"
        "{sightings} 条抓取发现记录".format(**result)
    )


if __name__ == "__main__":
    main()
