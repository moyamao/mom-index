#!/usr/bin/env python3
"""Keep the configured local model serving until this foreground process stops."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shlex
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from local_llm_runtime import LocalLlmRuntime
from runtime_config import ini_get, ini_get_int


def server_command(host):
    setting = ini_get("local_llm", "model_path", "")
    if not setting:
        raise RuntimeError("请在本机conf/config.ini的[local_llm]配置model_path")
    model = Path(setting).expanduser().resolve()
    if not model.is_file():
        raise RuntimeError(f"模型文件不存在: {model}")
    binary = ini_get("local_llm", "llama_server", "llama-server")
    server = shutil.which(binary)
    if not server:
        raise RuntimeError(f"找不到llama-server: {binary}")
    port = ini_get_int("local_llm", "port", 8080)
    context = ini_get_int("local_llm", "context", 4096)
    if not 1 <= port <= 65535 or context < 1:
        raise RuntimeError("[local_llm]的port或context无效")
    LocalLlmRuntime._require_free_port(port)
    return [server, "-m", str(model), "--host", host, "--port", str(port),
            "-c", str(context), "-ngl", "99", "--parallel", "1", "--no-reasoning-preserve"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1", choices=("127.0.0.1", "0.0.0.0"),
                        help="MacBook远程调用时使用0.0.0.0；默认仅本机")
    args = parser.parse_args()
    try:
        command = server_command(args.host)
        print("常驻模型命令：" + shlex.join(command), flush=True)
        print("保持此终端运行；评测结束不会关闭服务，按Ctrl+C停止。", flush=True)
        # Replace ourselves so signals reach llama-server directly and no orphan
        # child is left behind when the foreground service is stopped.
        os.execv(command[0], command)
    except (OSError, RuntimeError) as exc:
        raise SystemExit(f"服务启动停止：{exc}") from None


if __name__ == "__main__":
    main()
