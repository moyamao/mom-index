#!/usr/bin/env python3
"""Serve the dashboard and a token-protected keyword administration API."""
import json
import os
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, ROOT)

from keyword_config import list_keyword_records, set_keyword
from runtime_config import ini_get, ini_get_bool, ini_get_int


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=os.path.join(ROOT, "frontend"), **kwargs)

    def _json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/keywords":
            try:
                self._json(200, {"items": list_keyword_records()})
            except Exception as exc:
                self._json(500, {"error": str(exc)})
            return
        super().do_GET()

    def do_POST(self):
        if self.path != "/api/keywords":
            self._json(404, {"error": "not found"})
            return
        if not ini_get_bool("keyword_admin", "enabled", False):
            self._json(403, {"error": "keyword admin disabled"})
            return
        expected = ini_get("keyword_admin", "token", "")
        supplied = self.headers.get("X-Admin-Token", "")
        if not expected or supplied != expected:
            self._json(401, {"error": "invalid admin token"})
            return
        try:
            length = min(int(self.headers.get("Content-Length", "0")), 4096)
            payload = json.loads(self.rfile.read(length))
            set_keyword(payload.get("sector", ""), payload.get("keyword", ""), bool(payload.get("enabled")), "web")
            self._json(200, {"ok": True})
        except (ValueError, json.JSONDecodeError) as exc:
            self._json(400, {"error": str(exc)})
        except Exception as exc:
            self._json(500, {"error": str(exc)})


if __name__ == "__main__":
    host = ini_get("web", "host", "0.0.0.0") or "0.0.0.0"
    port = ini_get_int("web", "port", 8080)
    print(f"mom-index web: http://{host}:{port}/dashboard.html")
    ThreadingHTTPServer((host, port), Handler).serve_forever()
