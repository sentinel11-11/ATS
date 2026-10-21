#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Нагрузочный сценарий ATS v2: пользователи, сессии, API-поллинг (этап F).

Запуск (без внешних зависимостей):

    # self-hosted: поднимает экземпляр на sim-провайдере во временной БД
    python3 tools/loadtest/api_load.py --users 5000 --sessions 500 \
        --duration 60 --report tools/loadtest/report-api.json

    # против уже запущенного стенда (ОПАСНО на проде: создаёт пользователей!)
    python3 tools/loadtest/api_load.py --url http://127.0.0.1:9124 --admin-password ... --users 200

Честная метрика (P2 повторного ревью): latency-перцентили считаются по ВСЕМ
завершившимся запросам (успешным и ошибочным отдельно) — «хорошие p95 на
фильтрованных 200-х» при перегрузке не допускаются; таймауты считаются
отдельной метрикой. Отчёт содержит коммит, версии, CPU/RAM и параметры —
результат имеет смысл только для того окружения, в котором запущен; никаких
заявлений о ёмкости без воспроизводимого отчёта с целевого стенда.
"""
import argparse
import json
import os
import platform
import random
import socket
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
    def call(self, base, method, path, body=None, token="", timeout=15):
        """Возвращает (status, dt, timed_out): status 0 — транспортная ошибка."""
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(base + path, data=data, method=method)
        if body is not None:
            req.add_header("Content-Type", "application/json")
        if token:
            req.add_header("X-Ats-Token", token)
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                r.read()
                return r.status, time.time() - t0, False
        except urllib.error.HTTPError as e:
            e.read()
            return e.code, time.time() - t0, False
        except (socket.timeout, TimeoutError):
            return 0, time.time() - t0, True
        except Exception:
            return 0, time.time() - t0, False


class Sampler:
    """Latency по ВСЕМ завершившимся запросам (P2 повторного ревью).

    Успешные и ошибочные — раздельно: при перегрузке самые медленные запросы
    как раз завершаются ошибкой, и фильтрованный по 200-м p95 врал бы.
    """

    def __init__(self):
        self.lock = threading.Lock()
        self.lat_all = []    # все завершившиеся (успех + ошибка)
        self.lat_ok = []     # только HTTP 200
        self.lat_err = []    # ошибки HTTP
        self.errors = 0
        self.timeouts = 0
        self.total = 0

    def observe(self, status, dt, timed_out=False):
        with self.lock:
            self.total += 1
            self.lat_all.append(dt)
            if timed_out:
                self.timeouts += 1
                self.errors += 1
                return
            if status != 200:
                self.errors += 1
                self.lat_err.append(dt)
            else:
                self.lat_ok.append(dt)

    def summary(self, duration):
        def pct(arr, p):
            arr = sorted(arr)
            if not arr:
                return None
            return round(arr[min(len(arr) - 1, int(len(arr) * p))] * 1000, 1)

        all_sorted = sorted(self.lat_all)
        return {
            "rps": round(self.total / max(duration, 0.001), 1),
            "total_requests": self.total,
            "errors": self.errors,
            "timeouts": self.timeouts,
            "error_rate": round(self.errors / max(self.total, 1), 4),
            "latency_ms_all": {  # все завершившиеся запросы — основная метрика
                "p50": pct(self.lat_all, 0.50), "p95": pct(self.lat_all, 0.95),
                "p99": pct(self.lat_all, 0.99),
                "max": round(all_sorted[-1] * 1000, 1) if all_sorted else None,
            },
            "latency_ms_success": {  # отдельно 200-е — для сравнения
                "p50": pct(self.lat_ok, 0.50), "p95": pct(self.lat_ok, 0.95),
                "p99": pct(self.lat_ok, 0.99),
            },
            "latency_ms_errors": {
                "p50": pct(self.lat_err, 0.50), "p95": pct(self.lat_err, 0.95),
            },
        }


def _env_metadata():
    """Коммит/версии/CPU/RAM — без этого отчёт невоспроизводим (P2 ревью)."""
    meta = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "python": platform.python_version(),
        "cpu_count": os.cpu_count(),
        "machine": platform.machine(),
    }
    try:
        import resource
        # Linux: ru_maxrss в КБ — пик RSS процесса инструмента
        meta["tool_max_rss_mb"] = round(resource.getrusage(
            resource.RUSAGE_SELF).ru_maxrss / 1024, 1)
    except Exception:
        pass
    try:
        import subprocess
        meta["git_commit"] = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(ROOT),
            capture_output=True, text=True, timeout=5).stdout.strip()[:12]
    except Exception:
        meta["git_commit"] = "unknown"
    return meta


def start_self_hosted(port):
    """Поднять чистый экземпляр ATS на sim-провайдере (временная БД)."""
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

    client = HttpClient()
    try:
        # --- вход администратора ---
        req = urllib.request.Request(base + "/api/v2/auth/login",
                                     data=json.dumps({"login": "admin",
                                                      "password": args.admin_password}).encode(),
                                     method="POST",
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                admin_tok = json.loads(r.read().decode())["token"]
        except Exception as e:
            print("[load] вход администратора не удался:", e)
            return 2

        # --- сид пользователей (через API, как делает админ) ---
        t0 = time.time()
        for i in range(args.users):
            st, _, _ = client.call(base, "POST", "/api/v2/users/save",
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

        # --- поллинг API как UI: dashboard/numbers/campaigns + журнал ---
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
                    st, dt, to_ = client.call(base, "POST", "/api/v2/calls",
                                              {"limit": 50}, token=tok)
                else:
                    st, dt, to_ = client.call(base, "GET", random.choice(gets), token=tok)
                sampler.observe(st, dt, timed_out=to_)
                time.sleep(0.05)

        pollers = [threading.Thread(target=poller, daemon=True)
                   for _ in range(max(args.pollers, 1))]
        for th in pollers:
            th.start()
        print("[load] поллинг {} поток(ов) в течение {}s…".format(
            args.pollers, args.duration))
        t_start = time.time()
        time.sleep(args.duration)
        stop.set()
        time.sleep(0.5)
        real_duration = time.time() - t_start

        result = {
            "scenario": "api_polling",
            "env": "self-hosted sim" if not args.url else args.url,
            "params": {"users": args.users, "sessions": args.sessions,
                       "pollers": args.pollers,
                       "duration_sec": args.duration},
            "users": args.users,
            "sessions": len(tokens),
            "pollers": args.pollers,
            "duration_sec": round(real_duration, 1),
            "meta": _env_metadata(),
            "api": sampler.summary(real_duration),
            "note": "latency_ms_all — по ВСЕМ завершившимся запросам (успех+"
                    "ошибки); success/errors — раздельно. Это smoke инструмента, "
                    "НЕ заявление о ёмкости.",
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
