"""
小红书 Playwright smoke test

用法:
python3 scripts/test_xhs_playwright.py "纳指还能买吗" 8
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, ROOT)

from collectors.xhs_playwright import collect_keyword  # noqa: E402


def main():
    keyword = sys.argv[1] if len(sys.argv) > 1 else "纳指还能买吗"
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 8
    posts = collect_keyword(keyword, limit=limit)
    print(json.dumps(posts, ensure_ascii=False, indent=2))
    print(f"\n共返回 {len(posts)} 条")


if __name__ == "__main__":
    main()
