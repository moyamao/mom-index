#!/usr/bin/env python3
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from collectors.wechat_collector import collect_all

if __name__ == "__main__":
    data = collect_all()
    print(json.dumps({sector: len(posts) for sector, posts in data.items()}, ensure_ascii=False, indent=2))
