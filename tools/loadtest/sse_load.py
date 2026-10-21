#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Нагрузочный сценарий SSE: K одновременных EventSource-подключений (этап F).

Каждое подключение — как реальный UI: POST /auth/ticket (заголовок сессии) →
GET /api/v2/events?ticket=… (билет одноразовый, на reconnect — новый).
Измеряем: успех подключения, задержку первого байта, получение keepalive,
разрывы. Self-hosted режим — как у api_load.py.

    python3 tools/loadtest/sse_load.py --clients 100 --duration 30 \
        --report tools/loadtest/report-sse.json
"""
import argparse
import json
import socket
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def start_self_hosted(port):
    import os
    tmp = tempfile.mkdtemp(prefix="ats_sse_")
    os.environ.update({
        "ATS_DATA_DIR": tmp,
        "ATS_ADMIN_PASSWORD": "LoadAdmin123!",
        "ATS_DEV_SEED": "1",
        "ATS_FAST": "1",
    })
    from app import api, db
    from app.engine import Engine
    from app.server import create_server
    db.init_db()
    s = db.get_settings()
    s["provider"] = "sim"
    db.save_settings(s)
    engine = Engine()
    api.ENGINE = engine
    httpd = create_server("127.0.0.1", port)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, engine, tmp


class SseClient:
    def __init__(self, base, port, token):
        self.base = base
        self.port = port
        self.token = token
        self.connected = False
        self.first_byte_ms = None
        self.pings = 0
        self.events = 0
        self.sock = None

    def connect(self):
        req = urllib.request.Request(self.base + "/api/v2/auth/ticket",
                                     data=b"{}", method="POST",
                                     headers={"X-Ats-Token": self.token,
                                              "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            ticket = json.loads(r.read().decode())["ticket"]
        self.sock = socket.create_connection(("127.0.0.1", self.port), timeout=20)
        self.sock.sendall(("GET /api/v2/events?ticket={} HTTP/1.1\r\n"
                           "Host: l\r\n\r\n").format(ticket).encode())
        t0 = time.time()
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                return False
            buf += chunk
        self.first_byte_ms = round((time.time() - t0) * 1000, 1)
        self.connected = buf.startswith(b"HTTP/1.1 200")
        return self.connected

    def read_some(self):
        try:
            self.sock.settimeout(5)
            data = self.sock.recv(4096)
        except (socket.timeout, TimeoutError):
            return
        except OSError:
            self.connected = False
            return
        if not data:
            self.connected = False
            return
        self.pings += data.count(b": ping")
        self.events += data.count(b"data: ")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=None)
    ap.add_argument("--port", type=int, default=19125)
    ap.add_argument("--clients", type=int, default=50)
    ap.add_argument("--duration", type=float, default=20.0)
    ap.add_argument("--report", default=None)
    args = ap.parse_args()

    spawned = None
    if args.url:
        base = args.url.rstrip("/")
        port = int(base.rsplit(":", 1)[1]) if ":" in base else 80
    else:
        print("[sse] self-hosted режим, порт", args.port)
        spawned = start_self_hosted(args.port)
        base = "http://127.0.0.1:{}".format(args.port)
        port = args.port

    try:
        def _post(path, body, token=""):
            req = urllib.request.Request(base + path,
                                         data=json.dumps(body).encode(), method="POST",
                                         headers={"Content-Type": "application/json",
                                                  "X-Ats-Token": token})
            with urllib.request.urlopen(req, timeout=10) as r:
                return json.loads(r.read().decode())

        admin_tok = _post("/api/v2/auth/login",
                          {"login": "admin", "password": "LoadAdmin123!"})["token"]
        # каждый SSE-клиент — отдельный пользователь: per-user лимит соединений
        # (MAX_SSE_PER_USER) не должен резать нагрузочный сценарий, как резал бы
        # одного админа с 30 вкладками (это защита C4, и она работает)
        for i in range(args.clients):
            _post("/api/v2/users/save", {"login": "sse{}".format(i),
                                         "password": "LoadUser123!", "role": "operator"},
                  token=admin_tok)

        clients = []
        for i in range(args.clients):
            tok = _post("/api/v2/auth/login",
                        {"login": "sse{}".format(i),
                         "password": "LoadUser123!"})["token"]
            clients.append(SseClient(base, port, tok))
        t0 = time.time()
        ok = 0
        for c in clients:
            if c.connect():
                ok += 1
        connect_sec = time.time() - t0
        print("[sse] подключено {}/{} за {:.1f}s".format(ok, args.clients, connect_sec))

        deadline = time.time() + args.duration
        while time.time() < deadline:
            for c in clients:
                if c.connected:
                    c.read_some()

        alive = sum(1 for c in clients if c.connected)
        fb = sorted(c.first_byte_ms for c in clients if c.first_byte_ms is not None)
        result = {
            "scenario": "sse_connections",
            "env": "self-hosted sim" if not args.url else args.url,
            "clients": args.clients,
            "connected": ok,
            "alive_after_sec": alive,
            "duration_sec": args.duration,
            "connect_time_sec": round(connect_sec, 2),
            "first_byte_ms": {
                "p50": fb[len(fb) // 2] if fb else None,
                "p99": fb[min(len(fb) - 1, int(len(fb) * 0.99))] if fb else None,
            },
            "keepalives_seen": sum(c.pings for c in clients),
            "events_seen": sum(c.events for c in clients),
        }
        for c in clients:
            try:
                c.sock.close()
            except OSError:
                pass
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if args.report:
            Path(args.report).write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            print("[sse] отчёт:", args.report)
        return 0
    finally:
        if spawned:
            httpd, engine, _tmp = spawned
            httpd.shutdown()
            engine.stop()
            print("[sse] self-hosted инстанс остановлен")


if __name__ == "__main__":
    sys.exit(main())
