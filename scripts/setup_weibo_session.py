"""
初始化微博 Playwright 持久会话。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, ROOT)

from collectors.weibo_playwright import setup_session  # noqa: E402


def main():
    session_dir = asyncio_run()
    print(f"\n微博会话已保存到: {session_dir}")


def asyncio_run():
    import asyncio

    return asyncio.run(setup_session())


if __name__ == "__main__":
    main()
