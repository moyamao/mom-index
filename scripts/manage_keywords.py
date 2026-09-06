#!/usr/bin/env python3
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from keyword_config import get_keywords, set_keyword, sync_config_keywords_to_mysql


def main() -> None:
    parser = argparse.ArgumentParser(description="管理 Mac mini MySQL 中的监控关键词")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list")
    sub.add_parser("bootstrap")
    for command in ("enable", "disable"):
        item = sub.add_parser(command)
        item.add_argument("sector")
        item.add_argument("keyword")
    args = parser.parse_args()
    if args.command == "bootstrap":
        print(f"新增 {sync_config_keywords_to_mysql()} 个关键词")
    elif args.command == "list":
        for sector, keywords in get_keywords().items():
            print(f"{sector}: {', '.join(keywords)}")
    else:
        set_keyword(args.sector, args.keyword, args.command == "enable")
        print(f"{args.command}: {args.sector} / {args.keyword}")


if __name__ == "__main__":
    main()
