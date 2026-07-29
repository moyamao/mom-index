import json
import sys

sys.path.insert(0, ".")

from collectors.xueqiu_collector import search_xueqiu


def main():
    if len(sys.argv) < 2:
        print('用法: python3 scripts/test_xueqiu_collector.py "关键词"')
        sys.exit(1)

    keyword = sys.argv[1]
    posts = search_xueqiu(keyword)
    print(json.dumps(posts, ensure_ascii=False, indent=2))
    print(f"\n共返回 {len(posts)} 条")


if __name__ == "__main__":
    main()
