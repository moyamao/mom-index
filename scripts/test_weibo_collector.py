"""
微博采集器 smoke test

用法:
python3 scripts/test_weibo_collector.py "纳指还能买吗"
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, ROOT)

from collectors.weibo_collector import search_weibo  # noqa: E402


def main():
    keyword = sys.argv[1] if len(sys.argv) > 1 else "纳指还能买吗"
    posts = search_weibo(keyword)
    print(json.dumps(posts, ensure_ascii=False, indent=2))
    print(f"\n共返回 {len(posts)} 条")


if __name__ == "__main__":
    main()
