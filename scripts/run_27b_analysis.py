#!/usr/bin/env python3
"""Own one local llama-server lifecycle around the existing MySQL analysis job."""
from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODEL = Path('/Users/mhy/models/qwen3.8-27b/Qwen3.8-27B-Q8_0.gguf')


def stop_owned(process):
    """Only signal the process group created by this launcher."""
    if process is None or process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=20)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=10)


def require_free_port(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(('127.0.0.1', port))
        except OSError as exc:
            raise RuntimeError(f'本机端口{port}已被占用。请先自行关闭原服务，或指定 --port 8082；脚本不会关闭现有服务。') from exc


def wait_ready(process, base_url, model_path, timeout):
    deadline = time.monotonic() + timeout
    last_error = ''
    # Local requests must not accidentally go through a configured HTTP proxy.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f'llama-server启动失败，退出码{process.returncode}；请查看模型日志')
        try:
            with opener.open(base_url + '/v1/models', timeout=3) as response:
                entries = json.load(response).get('data', [])
            for entry in entries:
                model_id = str(entry.get('id', ''))
                if model_id in {str(model_path), model_path.name}:
                    if process.poll() is None:
                        return model_id
            if entries:
                raise RuntimeError('API返回的模型身份不匹配，停止计算；请检查端口和模型文件')
            last_error = '模型列表为空'
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            last_error = str(exc)
        time.sleep(1)
    raise RuntimeError(f'模型启动超过{timeout:g}秒，停止计算。最近错误：{last_error}')


def child_env(base_url, model_id, limit):
    env = os.environ.copy()
    env.update({
        'QWEN_BASE_URL': base_url,
        'MOM_INDEX_LLM_BASE_URL': base_url + '/v1',
        'MOM_INDEX_LLM_MODEL': model_id,
        'MOM_INDEX_LLM_ENABLED': '1',
        'MOM_INDEX_LLM_MODE': 'all',
        'MOM_INDEX_LLM_PROFILE': 'macbook-27b',
        'MOM_INDEX_RUNTIME_ROLE': 'analyst',
        'MOM_INDEX_LLM_MAX_POSTS_PER_RUN': str(limit),
        'PYTHONUNBUFFERED': '1',
    })
    env.setdefault('MOM_INDEX_LLM_TIMEOUT_SECONDS', '300')
    # Do not send a configured cloud credential to a local server.
    env['MOM_INDEX_LLM_API_KEY'] = 'local-no-auth'
    no_proxy = env.get('NO_PROXY', env.get('no_proxy', ''))
    env['NO_PROXY'] = env['no_proxy'] = ','.join(filter(None, [no_proxy, '127.0.0.1', 'localhost']))
    return env


def run(args):
    model = args.model.expanduser().resolve()
    server = shutil.which(args.llama_server)
    if not model.is_file():
        raise RuntimeError(f'模型文件不存在：{model}')
    if server is None:
        raise RuntimeError(f'找不到llama-server：{args.llama_server}，可用 --llama-server 指定完整路径')
    command = [server, '-m', str(model), '--host', '127.0.0.1', '--port', str(args.port),
               '-c', str(args.context), '-ngl', '99', '--parallel', '1', '--no-reasoning-preserve']
    analysis = [args.python, '-u', str(ROOT / 'scripts/analyze_mysql_posts.py'),
                '--days', str(args.days), '--limit', str(args.limit)]
    if args.reanalyze_all:
        analysis.append('--reanalyze-all')
    print('模型启动命令：' + shlex.join(command), flush=True)
    print('仅计算命令：' + shlex.join(analysis), flush=True)
    if args.dry_run:
        print('预览完成：未启动模型、未访问MySQL、未写入分析结果。')
        return 0

    logs = ROOT / 'logs'
    logs.mkdir(exist_ok=True)
    log_path = logs / f'qwen27b_{datetime.now():%Y%m%d_%H%M%S}_{os.getpid()}.log'
    with (logs / 'run_27b_analysis.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError('已有27B按需计算脚本在运行，请等待其结束') from exc
        require_free_port(args.port)
        server_process = task_process = None
        print(f'模型日志：{log_path}', flush=True)
        with log_path.open('w') as log:
            try:
                server_process = subprocess.Popen(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
                base = f'http://127.0.0.1:{args.port}'
                model_id = wait_ready(server_process, base, model, args.startup_timeout)
                print(f'27B已就绪：{model_id}；开始计算和入库。', flush=True)
                task_process = subprocess.Popen(analysis, cwd=ROOT, env=child_env(base, model_id, args.limit), start_new_session=True)
                code = task_process.wait()
                if code:
                    print(f'计算任务退出码：{code}。请检查上方输出；不要将此视为全部入库成功。', flush=True)
                else:
                    print('计算任务正常结束，入库批次见上方输出。', flush=True)
                return code if code >= 0 else 128 - code
            finally:
                # Repeated Ctrl+C must not interrupt cleanup of the owned server.
                handlers = {sig: signal.signal(sig, signal.SIG_IGN) for sig in (signal.SIGINT, signal.SIGTERM)}
                try:
                    try:
                        stop_owned(task_process)
                    finally:
                        stop_owned(server_process)
                    print('本脚本启动的27B服务已关闭。', flush=True)
                finally:
                    for sig, handler in handlers.items():
                        signal.signal(sig, handler)


def main():
    ap = argparse.ArgumentParser(description='启动本机27B → 只计算MySQL帖子并入库 → 关闭模型服务')
    ap.add_argument('--model', type=Path, default=DEFAULT_MODEL)
    ap.add_argument('--llama-server', default='llama-server')
    ap.add_argument('--python', default=sys.executable, help='计算任务使用的Python解释器')
    ap.add_argument('--port', type=int, default=8080)
    ap.add_argument('--context', type=int, default=4096)
    ap.add_argument('--days', type=int, default=7)
    ap.add_argument('--limit', type=int, default=1000)
    ap.add_argument('--startup-timeout', type=float, default=300)
    ap.add_argument('--reanalyze-all', action='store_true', help='重新分析窗口内全部帖子')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    if min(args.context, args.days, args.limit, args.startup_timeout) <= 0 or not 1 <= args.port <= 65535:
        ap.error('context/days/limit/startup-timeout必须大于0，port须为1至65535')
    def interrupted(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupted)
    try:
        return run(args)
    except KeyboardInterrupt:
        print('任务已中断，已执行模型清理。', file=sys.stderr)
        return 130
    except Exception as exc:
        print(f'运行失败：{exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
