# -*- coding: utf-8 -*-
"""HTTP-сервер ATS v2: статика (app/ui), REST API v2, SSE для событий."""
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from urllib.parse import parse_qs, urlparse

from . import api, config, events
from .api import json_bytes

STATIC_DIR = config.APP / "ui"
MAX_BODY = 10 * 1024 * 1024


def auth_ok(headers, query_token=""):
    from . import security
    tok = headers.get("X-Ats-Token") or query_token or ""
    return security.get_session(tok) is not None


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "ATSv2"

    def log_message(self, fmt, *args):
        return  # тихий режим

    # ---------- helpers ----------
    def _send_bytes(self, data, ctype, status=200, extra=None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(data)
        except Exception:
            pass

    def _send_json(self, obj, status=200):
        self._send_bytes(json_bytes(obj), "application/json; charset=utf-8", status)

    def _body(self):
        try:
            n = int(self.headers.get("Content-Length", "0") or "0")
        except Exception:
            n = 0
        if n <= 0 or n > MAX_BODY:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8", "ignore"))
        except Exception:
            return {}

    def _static(self, rel):
        # защита от path traversal
        if rel != rel and (".." in rel or "\\" in rel):
            rel = "index.html"
        path = (STATIC_DIR / rel).resolve()
        if not str(path).startswith(str(STATIC_DIR.resolve())):
            path = STATIC_DIR / "index.html"
        if not path.exists() or path.is_dir():
            path = STATIC_DIR / "index.html"
        data = path.read_bytes()
        ctype = {".html": "text/html; charset=utf-8", ".js": "application/javascript; charset=utf-8",
                 ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml",
                 ".png": "image/png", ".ico": "image/x-icon"}.get(path.suffix.lower(),
                                                                  "application/octet-stream")
        self._send_bytes(data, ctype)

    # ---------- GET ----------
    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/api/v2/events":
            return self._sse()
        if u.path.startswith("/api/"):
            payload, status = api.route("GET", u.path, {}, self.headers)
            if payload is None:
                payload, status = {"ok": False, "error": "not_found"}, 404
            if isinstance(payload, bytes):
                return self._send_bytes(payload, "text/csv; charset=utf-8", status,
                                        {"Content-Disposition": "attachment; filename=contacts.csv"})
            return self._send_json(payload, status)
        if u.path in ("/", "/index.html"):
            self._static("index.html")
            return
        if u.path.startswith("/ui/"):
            self._static(u.path[len("/ui/"):])
            return
        self._send_json({"ok": False, "error": "not_found"}, 404)

    # ---------- SSE ----------
    def _sse(self):
        q = parse_qs(urlparse(self.path).query)
        qtok = (q.get("token") or [""])[0]
        if not auth_ok(self.headers, qtok):
            return self._send_json({"ok": False, "error": "auth_required"}, 401)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        stream = events.EventStream()
        try:
            for chunk in stream.iter_events():
                try:
                    self.wfile.write(chunk.encode("utf-8"))
                    self.wfile.flush()
                except Exception:
                    break
        finally:
            stream.close()

    # ---------- POST ----------
    def do_POST(self):
        u = urlparse(self.path)
        if not u.path.startswith("/api/"):
            return self._send_json({"ok": False, "error": "not_found"}, 404)
        body = self._body()
        payload, status = api.route("POST", u.path, body, self.headers)
        if payload is None:
            payload, status = {"ok": False, "error": "not_found"}, 404
        if isinstance(payload, bytes):
            return self._send_bytes(payload, "text/csv; charset=utf-8", status)
        self._send_json(payload, status)


def create_server(host, port):
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True
    httpd.allow_reuse_address = True
    return httpd
