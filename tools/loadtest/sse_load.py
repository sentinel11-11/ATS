#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Нагрузочный сценарий SSE: K одновременных EventSource-подключений (этап F).

Каждое подключение — как реальный UI: POST /auth/ticket (заголовок сессии,
purpose='sse') → GET /api/v2/events?ticket=… (билет одноразовый, на
reconnect — новый). Измеряем: успех подключения, задержку первого байта,
получение keepalive, разрывы.

P2 повторного ревью:
- --url теперь ЧЕСТНО работает с удалённым стендом: TCP/TLS соединение
  идёт на host:port из самого URL (раньше — всегда 127.0.0.1 с Host: l),
  https поддерживается через ssl;
- чтение всех клиентов — через selectors (один цикл обслуживает все
  сокеты), а не последовательные блокирующие recv по 5 с на клиента:
  --duration теперь и есть фактическая длительность прогона.

    python3 tools/loadtest/sse_load.py --clients 100 --duration 30 \
        --report tools/loadtest/report-sse.json
    python3 tools/loadtest/sse_load.py --url https://staging.example.com \
        --clients 50 --duration 60
"""
import argparse
import json
import selectors
import socket
import ssl
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

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
    """Один SSE-клиент: билет → соединение на host:port из base (не на localhost)."""

    def __init__(self, base, token):
        u = urlparse(base)
        self.host = u.hostname or "127.0.0.1"
        self.port = u.port or (443 if u.scheme == "https" else 80)
        self.scheme = u.scheme or "http"
        self.host_header = u.netloc
        self.base = base
        self.token = token
        self.connected = False
        self.connect_error = ""
        self.first_byte_ms = None
        self.pings = 0
        self.events = 0
        self.sock = None

    def connect(self, ticket_timeout=10):
        try:
            req = urllib.request.Request(
                self.base + "/api/v2/auth/ticket",
                data=json.dumps({"purpose": "sse", "resource": "events"}).encode(),
                method="POST",
                headers={"X-Ats-Token": self.token,
                         "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=ticket_timeout) as r:
                ticket = json.loads(r.read().decode())["ticket"]
        except Exception as e:
            self.connect_error = "ticket: {}".format(e)[:200]
            return False
        try:
            raw = socket.create_connection((self.host, self.port), timeout=20)
            if self.scheme == "https":
                ctx = ssl.create_default_context()
                self.sock = ctx.wrap_socket(raw, server_hostname=self.host)
            else:
                self.sock = raw
            self.sock.sendall(("GET /api/v2/events?ticket={} HTTP/1.1\r\n"
                               "Host: {}\r\nAccept: text/event-stream\r\n"
                               "\r\n").format(ticket, self.host_header).encode())
        except Exception as e:
            self.connect_error = "socket: {}".format(e)[:200]
            return False
        t0 = time.time()
        buf = b""
        while b"\r\n\r\n" not in buf:
            try:
                chunk = self.sock.recv(4096)
            except OSError as e:
                self.connect_error = "recv: {}".format(e)[:200]
                return False
            if not chunk:
                self.connect_error = "closed before headers"
                return False
            buf += chunk
        self.first_byte_ms = round((time.time() - t0) * 1000, 1)
        self.connected = buf.startswith(b"HTTP/1.1 200")
        if not self.connected:
            self.connect_error = "status: " + buf.split(b"\r\n")[0].decode("utf-8", "ignore")[:120]
        return self.connected

    def on_data(self, data):
        if not data:
            self.connected = False
            return
        self.pings += data.count(b": ping")
        self.events += data.count(b"data: ")


def _env_metadata():
    import os
    import platform
    meta = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "python": platform.python_version(),
        "cpu_count": os.cpu_count(),
        "machine": platform.machine(),
    }
    try:
        import subprocess
        meta["git_commit"] = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(Path(__file__).resolve().parents[2]),
            capture_output=True, text=True, timeout=5).stdout.strip()[:12]
    except Exception:
        meta["git_commit"] = "unknown"
    return meta


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=None,
                    help="стенд (http/https); без него — self-hosted sim")
    ap.add_argument("--port", type=int, default=19125)
    ap.add_argument("--clients", type=int, default=50)
    ap.add_argument("--duration", type=float, default=20.0)
    ap.add_argument("--report", default=None)
    args = ap.parse_args()

    spawned = None
    if args.url:
        base = args.url.rstrip("/")
    else:
        print("[sse] self-hosted режим, порт", args.port)
        spawned = start_self_hosted(args.port)
        base = "http://127.0.0.1:{}".format(args.port)

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
            clients.append(SseClient(base, tok))
        t0 = time.time()
        ok = 0
        for c in clients:
            if c.connect():
                ok += 1
        connect_sec = time.time() - t0
        print("[sse] подключено {}/{} за {:.1f}s (host={})".format(
            ok, args.clients, connect_sec, clients[0].host if clients else "?"))
        for c in clients:
            if not c.connected and c.connect_error:
                print("    ошибка подключения: {}".format(c.connect_error))

        # все сокеты — в один selector-цикл: ни один клиент не блокирует
        # остальных (P2 повторного ревью), длительность = args.duration
        sel = selectors.DefaultSelector()
        for c in clients:
            if c.connected and c.sock is not None:
                c.sock.setblocking(False)
                sel.register(c.sock, selectors.EVENT_READ, data=c)
        t_start = time.time()
        deadline = t_start + args.duration
        while time.time() < deadline:
            for key, _mask in sel.select(timeout=0.5):
                c = key.data
                try:
                    data = c.sock.recv(65536)
                    c.on_data(data)
                    if not c.connected:
                        sel.unregister(c.sock)
                except (BlockingIOError, InterruptedError):
                    continue
                except OSError:
                    c.connected = False
                    try:
                        sel.unregister(c.sock)
                    except (KeyError, ValueError):
                        pass
        real_duration = time.time() - t_start

        alive = sum(1 for c in clients if c.connected)
        fb = sorted(c.first_byte_ms for c in clients if c.first_byte_ms is not None)
        result = {
            "scenario": "sse_connections",
            "env": "self-hosted sim" if not args.url else args.url,
            "params": {"clients": args.clients, "duration_sec": args.duration},
            "clients": args.clients,
            "connected": ok,
            "connect_errors": sum(1 for c in clients if c.connect_error),
            "alive_clients": alive,
            "duration_sec": round(real_duration, 1),
            "connect_time_sec": round(connect_sec, 2),
            "first_byte_ms": {
                "p50": fb[len(fb) // 2] if fb else None,
                "p99": fb[min(len(fb) - 1, int(len(fb) * 0.99))] if fb else None,
            },
            "keepalives_seen": sum(c.pings for c in clients),
            "events_seen": sum(c.events for c in clients),
            "meta": _env_metadata(),
            "note": "smoke-проверка инструмента и стенда, НЕ заявление о ёмкости; "
                    "жёсткий лимит SSE на инстанс — MAX_SSE_CLIENTS (200).",
        }
        for c in clients:
            try:
                if c.sock is not None:
                    c.sock.close()
            except OSError:
                pass
        sel.close()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if args.report:
            Path(args.report).write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            print("[sse] отчёт:", args.report)
        return 0 if ok == args.clients else 1
    finally:
        if spawned:
            httpd, engine, _tmp = spawned
            httpd.shutdown()
            engine.stop()
            print("[sse] self-hosted инстанс остановлен")


if __name__ == "__main__":
    sys.exit(main())
