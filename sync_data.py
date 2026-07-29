"""数据同步脚本 — 从 data/ 同步到 frontend/data/
运行: python sync_data.py
pipeline.py 已内置自动同步，此脚本用于手动修复不一致。
"""
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "data")
FRONTEND_DATA_DIR = os.path.join(HERE, "frontend", "data")
FILES = ["dashboard_data.json", "history.json", "xhs_posts.json", "weibo_posts.json", "xueqiu_posts.json", "platform_posts.json"]

def sync():
    if not os.path.isdir(DATA_DIR):
        print(f"错误: 数据目录不存在 {DATA_DIR}", file=sys.stderr)
        sys.exit(1)
    os.makedirs(FRONTEND_DATA_DIR, exist_ok=True)
    for fname in FILES:
        src = os.path.join(DATA_DIR, fname)
        dst = os.path.join(FRONTEND_DATA_DIR, fname)
        if os.path.exists(src):
            shutil.copy2(src, dst)
            size = os.path.getsize(dst)
            print(f"  ✓ {fname} ({size} bytes)")
        else:
            print(f"  ○ {fname} (源文件不存在，跳过)")
    print(f"\n同步完成: {DATA_DIR} → {FRONTEND_DATA_DIR}")

if __name__ == "__main__":
    sync()
