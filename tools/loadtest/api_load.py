#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Нагрузочный сценарий ATS v2: пользователи, сессии, API-поллинг (этап F).

Запуск (без внешних зависимостей):

    # self-hosted: поднимает экземпляр на sim-провайдере во временной БД
    python3 tools/loadtest/api_load.py --users 5000 --sessions 500 \
        --duration 60 --report tools/loadtest/report-api.json

    # против уже запущенного стенда (ОПАСНО на проде: создаёт пользователей!)
    python3 tools/loadtest/api_load.py --url http://127.0.0.1:9124 --admin-password ... --users 200

Честная метрика: печатает p50/p95/p99 latency, error rate, RPS и пишет
JSON-отчёт. Это ИНСТРУМЕНТ: результаты имеют смысл только для того окружения,
в котором запущен; никаких заявлений о ёмкости без воспроизводимого отчёта.
"""
import argparse
import json
import random
import statistics
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


class HttpClient:
    def __init__(self, base):
        self.base = base

    def call(self, method, path, body=None, token="", timeout=15):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method)
        if body is not None:
            req.add_header("Content-Type", "application/json")
        if token:
            req.add_header("X-Ats-Token", token)
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                r.read()
                return r.status, time.time() - t0
        except urllib.error.HTTPError as e:
            e.read()
            return e.code, time.time() - t0
        except Exception:
            return 0, time.time() - t0


class Sampler:
    def __init__(self):
        self.lock = threading.Lock()
        self.lat = []
        self.errors = 0
        self.total = 0

    def observe(self, status, dt):
        with self.lock:
            self.total += 1
            if status != 200:
                self.errors += 1
            else:
                self.lat.append(dt)

    def summary(self, duration):
        lat = sorted(self.lat) or [0.0]

        def pct(p):
            return round(lat[min(len(lat) - 1, int(len(lat) * p))] * 1000, 1)
        return {
            "rps": round(self.total / max(duration, 0.001), 1),
            "total_requests": self.total,
            "errors": self.errors,
            "error_rate": round(self.errors / max(self.total, 1), 4),
            "latency_ms": {"p50": pct(0.50), "p95": pct(0.95), "p99": pct(0.99),
                           "max": round(lat[-1] * 1000, 1)},
        }


def start_self_hosted(port):
    """Поднять чистый экземпляр ATS на sim-провайдере (временная БД)."""
    import os
    tmp = tempfile.mkdtemp(prefix="ats_load_")
    os.environ.update({
        "ATS_DATA_DIR": tmp,
        "ATS_ADMIN_PASSWORD": "LoadAdmin123!",
        "ATS_DEV_SEED": "1",
        "ATS_FAST": "1",
        "ATS_HOST": "127.0.0.1",
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=None, help="стенд; без него — self-hosted sim")
    ap.add_argument("--port", type=int, default=19124)
    ap.add_argument("--users", type=int, default=200, help="сколько пользователей создать")
    ap.add_argument("--sessions", type=int, default=50, help="одновременных сессий (входы)")
    ap.add_argument("--duration", type=float, default=30.0)
    ap.add_argument("--pollers", type=int, default=20, help="параллельных поллеров API")
    ap.add_argument("--admin-password", default="LoadAdmin123!")
    ap.add_argument("--report", default=None)
    args = ap.parse_args()

    spawned = None
    if args.url:
        base = args.url.rstrip("/")
    else:
        print("[load] self-hosted режим: sim-провайдер, временная БД, порт", args.port)
        spawned = start_self_hosted(args.port)
        base = "http://127.0.0.1:{}".format(args.port)

    client = HttpClient(base)
    try:
        # --- вход администратора ---
        st, _ = client.call("POST", "/api/v2/auth/login",
                            {"login": "admin", "password": args.admin_password})
        if st != 200:
            print("[load] вход администратора не удался:", st)
            return 2
        # второй вызов: токен нужен в теле ответа
        req = urllib.request.Request(base + "/api/v2/auth/login",
                                     data=json.dumps({"login": "admin",
                                                      "password": args.admin_password}).encode(),
                                     method="POST",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            admin_tok = json.loads(r.read().decode())["token"]

        # --- сид пользователей (через API, как делает админ) ---
        t0 = time.time()
        for i in range(args.users):
            st, _ = client.call("POST", "/api/v2/users/save",
                                {"login": "load{}".format(i), "password": "LoadUser123!",
                                 "role": "operator"}, token=admin_tok)
            if st != 200:
                print("[load] users/save код", st, "на пользователе", i)
                break
        print("[load] создано пользователей: {} за {:.1f}s".format(
            args.users, time.time() - t0))

        # --- сессии: входы (частично конкурентно) ---
        t0 = time.time()
        tokens = {}
        lock = threading.Lock()

        def login_batch(lo, hi):
            for i in range(lo, hi):
                req = urllib.request.Request(
                    base + "/api/v2/auth/login",
                    data=json.dumps({"login": "load{}".format(i),
                                     "password": "LoadUser123!"}).encode(),
                    method="POST",
                    headers={"Content-Type": "application/json"})
                try:
                    with urllib.request.urlopen(req, timeout=10) as r:
                        tok = json.loads(r.read().decode()).get("token")
                        if tok:
                            with lock:
                                tokens[i] = tok
                except Exception:
                    pass
        n = min(max(args.sessions, 1), args.users)
        step = max(1, (n + 7) // 8)
        threads = [threading.Thread(target=login_batch,
                                    args=(i, min(i + step, n)))
                   for i in range(0, n, step)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        print("[load] активных сессий: {} за {:.1f}s".format(len(tokens), time.time() - t0))

        # --- поллинг API как UI: dashboard/calls/journal ---
        sampler = Sampler()
        stop = threading.Event()
        toks = list(tokens.values())

        def poller():
            # маршруты, которые реально опрашивает UI (обе роли):
            # dashboard/numbers/campaigns — GET; журнал звонков — POST /calls
            gets = ["/api/v2/dashboard", "/api/v2/numbers", "/api/v2/campaigns"]
            while not stop.is_set():
                tok = random.choice(toks) if toks else admin_tok
                if random.random() < 0.25:
                    st, dt = client.call("POST", "/api/v2/calls",
                                         {"limit": 50}, token=tok)
                else:
                    st, dt = client.call("GET", random.choice(gets), token=tok)
                sampler.observe(st, dt)
                time.sleep(0.05)

        pollers = [threading.Thread(target=poller, daemon=True)
                   for _ in range(max(args.pollers, 1))]
        for th in pollers:
            th.start()
        print("[load] поллинг {} поток(ов) в течение {}s…".format(
            args.pollers, args.duration))
        time.sleep(args.duration)
        stop.set()
        time.sleep(0.5)

        result = {
            "scenario": "api_polling",
            "env": "self-hosted sim" if not args.url else args.url,
            "users": args.users,
            "sessions": len(tokens),
            "pollers": args.pollers,
            "duration_sec": args.duration,
            "api": sampler.summary(args.duration),
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if args.report:
            Path(args.report).write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            print("[load] отчёт:", args.report)
        return 0
    finally:
        if spawned:
            httpd, engine, _tmp = spawned
            httpd.shutdown()
            engine.stop()
            print("[load] self-hosted инстанс остановлен")


if __name__ == "__main__":
    sys.exit(main())
