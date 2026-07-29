"""
初始化小红书 Playwright 持久会话。

用法:
python3 scripts/setup_xhs_session.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, ROOT)

from collectors.xhs_playwright import setup_session  # noqa: E402


def main():
    session_dir = setup_session()
    print(f"\n会话已保存到: {session_dir}")


if __name__ == "__main__":
    main()
