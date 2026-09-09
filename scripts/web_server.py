#!/usr/bin/env python3
"""Serve the dashboard and a token-protected keyword administration API."""
import json
import os
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from datetime import date
from urllib.parse import parse_qs, urlsplit

ROOT = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, ROOT)

from keyword_config import list_keyword_records, list_sector_records, set_keyword, set_sector
from runtime_config import ini_get, ini_get_bool, ini_get_int


def _parse_keyword_update(payload):
    if not isinstance(payload, dict):
        raise ValueError("请求内容必须是 JSON 对象")
    sector = str(payload.get("sector", "")).strip()
    keyword = str(payload.get("keyword", "")).strip()
    enabled = payload.get("enabled")
    if not sector:
        raise ValueError("板块不能为空")
    if not keyword:
        raise ValueError("关键词不能为空")
    if not isinstance(enabled, bool):
        raise ValueError("enabled 必须是布尔值")
    return sector, keyword, enabled


def _dashboard_payload():
    dashboard_path = os.path.join(ROOT, "data", "dashboard_data.json")
    with open(dashboard_path, "r", encoding="utf-8") as handle:
        dashboard = json.load(handle)
    from analyzer.platform_trends import fetch_model_sector_history, fetch_platform_trends
    from storage.mysql_store import fetch_model_comparison, fetch_model_daily_snapshots
    dashboard["model_comparison"] = fetch_model_comparison()
    dashboard["model_daily_snapshots"] = fetch_model_daily_snapshots()
    dashboard["platform_sentiment_trends"] = fetch_platform_trends()
    dashboard["model_sector_history"] = fetch_model_sector_history()
    dashboard["sector_catalog"] = list_sector_records(enabled_only=True)
    return dashboard


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=os.path.join(ROOT, "frontend"), **kwargs)

    def _json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def end_headers(self):
        if urlsplit(self.path).path.endswith(".html"):
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        super().end_headers()

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/api/keyword-admin/status":
            self._json(200, {
                "enabled": ini_get_bool("keyword_admin", "enabled", False),
                "token_required": bool(ini_get("keyword_admin", "token", "").strip()),
            })
            return
        if path == "/api/dashboard-data":
            try:
                self._json(200, _dashboard_payload())
            except Exception as exc:
                self._json(500, {"error": f"读取最新模型结果失败: {exc}"})
            return
        if path == "/api/post-model-comparison":
            try:
                from storage.mysql_store import fetch_post_model_comparison
                query = parse_qs(urlsplit(self.path).query)
                requested_date = query.get("date", [""])[0]
                target_date = date.fromisoformat(requested_date) if requested_date else None
                payload = fetch_post_model_comparison(target_date)
                payload["sector_catalog"] = list_sector_records(enabled_only=True)
                self._json(200, payload)
            except Exception as exc:
                self._json(500, {"error": f"读取帖子模型判定失败: {exc}"})
            return
        if path == "/api/keywords":
            try:
                self._json(200, {"items": list_keyword_records()})
            except Exception as exc:
                self._json(500, {"error": str(exc)})
            return
        if path == "/api/sectors":
            try:
                self._json(200, {"items": list_sector_records()})
            except Exception as exc:
                self._json(500, {"error": str(exc)})
            return
        super().do_GET()

    def do_POST(self):
        path = urlsplit(self.path).path
        if path not in {"/api/keywords", "/api/sectors"}:
            self._json(404, {"error": "接口不存在"})
            return
        if not ini_get_bool("keyword_admin", "enabled", False):
            self._json(403, {"error": "关键词管理未启用，请在 config.ini 的 [keyword_admin] 中设置 enabled = true"})
            return
        expected = ini_get("keyword_admin", "token", "")
        supplied = self.headers.get("X-Admin-Token", "")
        if not expected or supplied != expected:
            self._json(401, {"error": "管理 token 未填写或不正确"})
            return
        try:
            length = min(int(self.headers.get("Content-Length", "0")), 4096)
            payload = json.loads(self.rfile.read(length))
            if path == "/api/sectors":
                code = str(payload.get("code", "")).strip()
                name = str(payload.get("name", "")).strip()
                color = str(payload.get("color", "#94a3b8")).strip()
                enabled = payload.get("enabled")
                if not isinstance(enabled, bool):
                    raise ValueError("enabled 必须是布尔值")
                set_sector(code, name, color, enabled)
                self._json(200, {"ok": True, "item": {"code": code, "name": name, "color": color, "enabled": enabled}})
            else:
                sector, keyword, enabled = _parse_keyword_update(payload)
                set_keyword(sector, keyword, enabled, "web")
                self._json(200, {
                    "ok": True,
                    "item": {"sector": sector, "keyword": keyword, "enabled": enabled},
                })
        except (ValueError, json.JSONDecodeError) as exc:
            self._json(400, {"error": str(exc)})
        except Exception as exc:
            self._json(500, {"error": str(exc)})


if __name__ == "__main__":
    host = ini_get("web", "host", "0.0.0.0") or "0.0.0.0"
    port = ini_get_int("web", "port", 8081)
    print(f"mom-index web: http://{host}:{port}/dashboard.html")
    ThreadingHTTPServer((host, port), Handler).serve_forever()
