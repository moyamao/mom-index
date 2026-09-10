"""Own a local llama-server process only while pipeline analysis is running."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import time
import urllib.error
import urllib.request

from runtime_config import ini_get, ini_get_bool, ini_get_int


class LocalLlmRuntime:
    def __init__(self):
        self.enabled = ini_get_bool("local_llm", "enabled", False)
        self.process = None
        self.log_handle = None
        self.log_path = None

    def __enter__(self):
        if not self.enabled:
            return self
        model = Path(ini_get("local_llm", "model_path", "")).expanduser().resolve()
        server_setting = ini_get("local_llm", "llama_server", "llama-server").strip()
        server = shutil.which(server_setting)
        port = ini_get_int("local_llm", "port", 8080)
        context = ini_get_int("local_llm", "context", 4096)
        startup_timeout = ini_get_int("local_llm", "startup_timeout_seconds", 300)
        request_timeout = ini_get_int("local_llm", "request_timeout_seconds", 120)
        if not model.is_file():
            raise RuntimeError(f"14B模型文件不存在: {model}")
        if not server:
            raise RuntimeError(f"找不到llama-server: {server_setting}")
        self._require_free_port(port)
        logs = Path(__file__).resolve().parent / "logs"
        logs.mkdir(exist_ok=True)
        self.log_path = logs / f"qwen14b_{time.strftime('%Y%m%d_%H%M%S')}_{os.getpid()}.log"
        self.log_handle = self.log_path.open("w")
        command = [
            server, "-m", str(model), "--host", "127.0.0.1", "--port", str(port),
            "-c", str(context), "-ngl", "99", "--parallel", "1", "--no-reasoning-preserve",
        ]
        print(f"  启动Mac mini 14B模型，日志: {self.log_path}", flush=True)
        try:
            self.process = subprocess.Popen(
                command,
                cwd=Path(__file__).resolve().parent,
                stdout=self.log_handle,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            model_id = self._wait_ready(port, model, startup_timeout)
        except BaseException:
            self._stop_owned()
            self.log_handle.close()
            self.log_handle = None
            raise
        base_url = f"http://127.0.0.1:{port}"
        os.environ.update({
            "QWEN_BASE_URL": base_url,
            "MOM_INDEX_LLM_BASE_URL": base_url + "/v1",
            "MOM_INDEX_LLM_MODEL": model_id,
            "MOM_INDEX_LLM_API_KEY": "local-no-auth",
            "MOM_INDEX_LLM_ENABLED": "1",
            "MOM_INDEX_LLM_TIMEOUT_SECONDS": str(request_timeout),
            "NO_PROXY": self._no_proxy(),
            "no_proxy": self._no_proxy(),
        })
        print(f"  14B模型已就绪: {model_id}", flush=True)
        return self

    def __exit__(self, exc_type, exc, traceback):
        if not self.enabled:
            return False
        self._stop_owned()
        if self.log_handle:
            self.log_handle.close()
        print("  14B模型服务已关闭", flush=True)
        return False

    @staticmethod
    def _require_free_port(port):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", port))
            except OSError as exc:
                raise RuntimeError(
                    f"本机端口{port}已被占用；请先关闭长期运行的llama-server。"
                    "按需脚本不会关闭非它启动的进程。"
                ) from exc

    def _wait_ready(self, port, model, timeout):
        deadline = time.monotonic() + timeout
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        last_error = ""
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError(f"llama-server启动失败，退出码{self.process.returncode}；请查看 {self.log_path}")
            try:
                with opener.open(f"http://127.0.0.1:{port}/v1/models", timeout=3) as response:
                    entries = json.load(response).get("data", [])
                if entries:
                    model_ids = [str(item.get("id", "")) for item in entries]
                    for model_id in model_ids:
                        if model_id in {str(model), model.name}:
                            return model_id
                    raise RuntimeError(f"模型身份不匹配: {model_ids}")
                last_error = "模型列表为空"
            except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
                last_error = str(exc)
            time.sleep(1)
        raise RuntimeError(f"14B模型启动超过{timeout}秒；最近错误: {last_error}")

    def _stop_owned(self):
        if self.process is None or self.process.poll() is not None:
            return
        try:
            os.killpg(self.process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            self.process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            self.process.wait(timeout=10)

    @staticmethod
    def _no_proxy():
        current = os.environ.get("NO_PROXY", os.environ.get("no_proxy", ""))
        values = [item for item in current.split(",") if item]
        for value in ("127.0.0.1", "localhost"):
            if value not in values:
                values.append(value)
        return ",".join(values)


def local_llm_for_analysis():
    return LocalLlmRuntime()
