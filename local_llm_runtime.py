"""Start an owned llama-server or reuse an independently managed service."""
from __future__ import annotations

import fcntl
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
        self.reused = False
        self._owner_lock = None

    def __enter__(self):
        if not self.enabled:
            return self
        model = Path(ini_get("local_llm", "model_path", "")).expanduser().resolve()
        server_setting = ini_get("local_llm", "llama_server", "llama-server").strip()
        port = ini_get_int("local_llm", "port", 8080)
        context = ini_get_int("local_llm", "context", 4096)
        startup_timeout = ini_get_int("local_llm", "startup_timeout_seconds", 300)
        request_timeout = ini_get_int("local_llm", "request_timeout_seconds", 120)
        if not model.is_file():
            raise RuntimeError(f"模型文件不存在: {model}")
        # Hold this lock throughout an owned process's lifetime. Otherwise a
        # second job might reuse that temporary server just before it is killed.
        self._acquire_ownership_lock(port)
        try:
            return self._start_or_reuse(model, server_setting, port, context,
                                        startup_timeout, request_timeout)
        except BaseException:
            try:
                self._stop_owned()
            finally:
                if self.log_handle:
                    self.log_handle.close()
                    self.log_handle = None
                self._release_ownership_lock()
            raise

    def _start_or_reuse(self, model, server_setting, port, context, startup_timeout, request_timeout):
        try:
            self._require_free_port(port)
        except RuntimeError:
            if not ini_get_bool("local_llm", "reuse_existing", True):
                raise
            # Only reuse a server that reports the configured model identity.
            # Never attach ownership to a process started outside this context.
            model_id = self._wait_ready(port, model, min(startup_timeout, 15))
            self.reused = True
            self._configure_environment(port, model_id, request_timeout)
            self._release_ownership_lock()
            print(f"  复用本机常驻模型: {model_id}；任务结束后保持运行", flush=True)
            return self
        server = shutil.which(server_setting)
        if not server:
            raise RuntimeError(f"找不到llama-server: {server_setting}")
        logs = Path(__file__).resolve().parent / "logs"
        logs.mkdir(exist_ok=True)
        self.log_path = logs / f"qwen14b_{time.strftime('%Y%m%d_%H%M%S')}_{os.getpid()}.log"
        self.log_handle = self.log_path.open("w")
        command = [
            server, "-m", str(model), "--host", "127.0.0.1", "--port", str(port),
            "-c", str(context), "-ngl", "99", "--parallel", "1", "--no-reasoning-preserve",
        ]
        print(f"  启动本机模型，日志: {self.log_path}", flush=True)
        self.process = subprocess.Popen(
            command,
            cwd=Path(__file__).resolve().parent,
            stdout=self.log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        model_id = self._wait_ready(port, model, startup_timeout)
        self._configure_environment(port, model_id, request_timeout)
        print(f"  模型已就绪: {model_id}", flush=True)
        return self

    def _acquire_ownership_lock(self, port):
        logs = Path(__file__).resolve().parent / "logs"
        logs.mkdir(exist_ok=True)
        lock = (logs / f"local_llm_{port}.lock").open("a")
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            lock.close()
            raise RuntimeError(f"端口{port}有按需模型任务正在运行；请等待该任务结束后再试。") from exc
        except BaseException:
            lock.close()
            raise
        self._owner_lock = lock

    def _release_ownership_lock(self):
        if self._owner_lock is not None:
            self._owner_lock.close()
            self._owner_lock = None

    def _configure_environment(self, port, model_id, request_timeout):
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

    def __exit__(self, exc_type, exc, traceback):
        if not self.enabled:
            return False
        if self.reused:
            print("  本次使用的常驻模型保持运行", flush=True)
            return False
        try:
            self._stop_owned()
        finally:
            if self.log_handle:
                self.log_handle.close()
                self.log_handle = None
            self._release_ownership_lock()
        print("  本脚本启动的模型服务已关闭", flush=True)
        return False

    @staticmethod
    def _require_free_port(port):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", port))
            except OSError as exc:
                raise RuntimeError(
                    f"本机端口{port}已被占用；不会关闭非本脚本启动的进程。"
                    "按需模式可通过local_llm.reuse_existing复用身份匹配的服务。"
                ) from exc

    def _wait_ready(self, port, model, timeout):
        deadline = time.monotonic() + timeout
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        last_error = ""
        while time.monotonic() < deadline:
            if self.process is not None and self.process.poll() is not None:
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
        raise RuntimeError(f"本机模型就绪检测超过{timeout}秒；最近错误: {last_error}")

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
