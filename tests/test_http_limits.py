# -*- coding: utf-8 -*-
"""Лимиты HTTP-слоя (C4, P1-аудита).

- раздельные лимиты тела: обычный JSON 2 MiB, загрузки (импорт баз,
  записи разговоров) 64 MiB; превышение → 413 + Connection: close,
  тело в память не читается;
- потолок одновременных SSE-подписчиков и лимит на пользователя;
- per-IP rate limit входа (перебор по многим логинам с одного адреса).
"""
import json
import os
import socket
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

_TMP = tempfile.mkdtemp(prefix="ats_test_httpl_")
os.environ["ATS_FAST"] = "1"
os.environ["ATS_DATA_DIR"] = _TMP
os.environ["ATS_ADMIN_PASSWORD"] = "TestAdmin123!"
os.environ["ATS_DEV_SEED"] = "1"

from app import api, config, db, security  # noqa: E402
from app.engine import Engine  # noqa: E402
from app.server import MAX_SSE_PER_USER, create_server  # noqa: E402

db.init_db()
_s = db.get_settings()
if not _s.get("provider"):
    _s["provider"] = "sim"
    db.save_settings(_s)


class HttpLimitsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = Engine(auto_start=False)
        api.ENGINE = cls.engine
        cls.httpd = create_server("127.0.0.1", 0)
        cls.base = "http://127.0.0.1:{}".format(cls.httpd.server_address[1])
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        time.sleep(0.2)
        req = urllib.request.Request(cls.base + "/api/v2/auth/login",
                                     data=json.dumps({"login": "admin",
                                                      "password": "TestAdmin123!"}).encode(),
                                     method="POST",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            cls.token = json.loads(r.read().decode())["token"]

    @classmethod
    def tearDownClass(cls):
        cls.engine.stop()
        cls.httpd.shutdown()

    def _post(self, path, data, headers=None):
        h = {"Content-Type": "application/json"}
        h.update(headers or {})
        req = urllib.request.Request(self.base + path, data=data, method="POST", headers=h)
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return r.status, r.read(), dict(r.headers)
        except urllib.error.HTTPError as e:
            return e.code, e.read(), dict(e.headers)

    def test_json_route_413_over_2mib(self):
        """Обычный JSON-маршрут: тело > 2 MiB → 413, соединение закрывается.

        Сырой сокет: сервер отвечает 413, не дочитывая тело, — обычный
        HTTP-клиент оборвёт отправку и не увидит ответ.
        """
        with socket.create_connection(("127.0.0.1", self.port), timeout=10) as s:
            head = ("POST /api/v2/settings/save HTTP/1.1\r\nHost: t\r\n"
                    "Content-Type: application/json\r\n"
                    "X-Ats-Token: {}\r\nContent-Length: {}\r\n\r\n").format(
                        self.token, 3 * 1024 * 1024)
            s.sendall(head.encode())
            try:
                s.sendall(b"x" * (256 * 1024))  # начало тела; дальше сервер откажет
            except (BrokenPipeError, ConnectionError):
                pass  # ожидаемо: сервер закрыл соединение после 413
            s.settimeout(5)
            data = b""
            try:
                while b"\r\n\r\n" not in data:
                    chunk = s.recv(4096)
                    if not chunk:
                        break
                    data += chunk
            except (socket.timeout, TimeoutError):
                pass
        self.assertIn(b" 413 ", data.split(b"\r\n", 1)[0],
                      "ожидали 413, получили: %r" % data[:80])
        self.assertIn(b"payload_too_large", data)
        self.assertIn(b"Connection: close", data)

    def test_json_route_under_limit_not_413(self):
        """Тело под лимитом проходит слой сервера (дальше — бизнес-валидация)."""
        small = b'{"max_channels": 3, "pad": "' + b"y" * (512 * 1024) + b'"}'
        code, body, _ = self._post("/api/v2/settings/save", small,
                                   {"X-Ats-Token": self.token})
        self.assertNotEqual(code, 413, "тело < 2 MiB не должно отбиваться лимитом")
        self.assertNotEqual(code, 503)

    def test_upload_route_allows_up_to_64mib_boundary(self):
        """Маршрут загрузки: граница 2..64 MiB не отбивается (413 только за 64+)."""
        # 3 MiB на upload-маршруте: сервер не должен отвечать 413
        payload = json.dumps({"filename": "f.csv", "data_b64": "x" * (3 * 1024 * 1024)}).encode()
        code, body, _ = self._post("/api/v2/contacts/import-file", payload,
                                   {"X-Ats-Token": self.token})
        self.assertNotEqual(code, 413,
                            "upload-маршрут имеет собственный лимит 64 MiB, "
                            "а не общий JSON-лимит")
        self.assertIn(json.loads(body).get("error"),
                     ("bad_base64", "no_file", "empty", "file_required"))

    def test_huge_content_length_rejected_without_reading(self):
        """Content-Length 100 MiB → 413 сразу, тело не читается."""
        with socket.create_connection(("127.0.0.1", self.port), timeout=10) as s:
            req = ("POST /api/v2/settings/save HTTP/1.1\r\n"
                   "Host: t\r\n"
                   "Content-Type: application/json\r\n"
                   "Content-Length: {}\r\n"
                   "X-Ats-Token: {}\r\n\r\n").format(100 * 1024 * 1024, self.token)
            s.sendall(req.encode())
            s.settimeout(5)
            data = b""
            try:
                while b"\r\n\r\n" not in data:
                    chunk = s.recv(4096)
                    if not chunk:
                        break
                    data += chunk
            except (socket.timeout, TimeoutError):
                pass
            first_line = data.split(b"\r\n", 1)[0].decode()
            self.assertIn("413", first_line, "ожидали 413, получили: %r" % first_line)
            self.assertIn(b"Connection: close", data)

    # ---------- SSE ----------

    def _open_sse(self):
        """Открыть SSE-соединение по свежему билету; вернуть (sock, first_bytes)."""
        code, body, _ = self._post("/api/v2/auth/ticket",
                                   json.dumps({"purpose": "sse",
                                               "resource": "events"}).encode(),
                                   {"X-Ats-Token": self.token,
                                    "Content-Type": "application/json"})
        self.assertEqual(code, 200, body)
        ticket = json.loads(body)["ticket"]
        s = socket.create_connection(("127.0.0.1", self.port), timeout=10)
        req = ("GET /api/v2/events?ticket={} HTTP/1.1\r\nHost: t\r\n\r\n").format(ticket)
        s.sendall(req.encode())
        s.settimeout(5)
        try:
            first = s.recv(1024)
        except (socket.timeout, TimeoutError):
            first = b""
        return s, first

    def test_sse_per_user_limit(self):
        """Больше MAX_SSE_PER_USER соединений одного пользователя → 429."""
        conns = []
        try:
            for _ in range(MAX_SSE_PER_USER):
                s, first = self._open_sse()
                self.assertIn(b"200", first.split(b"\r\n", 1)[0],
                              "в пределах лимита SSE обязан открываться")
                conns.append(s)
            # (MAX_SSE_PER_USER + 1)-е соединение — отказ без расходования потока
            s, first = self._open_sse()
            conns.append(s)
            self.assertIn(b"429", first.split(b"\r\n", 1)[0],
                          "превышение per-user SSE-лимита должно отклоняться")
        finally:
            for s in conns:
                try:
                    s.close()
                except OSError:
                    pass
            time.sleep(0.3)  # сервер успевает декрементировать счётчики

    # ---------- per-IP login rate limit ----------

    def test_login_ip_rate_limit(self):
        """Перебор разных логинов с одного IP упирается в per-IP лимит."""
        api._LOGIN_IP_ATTEMPTS.clear()
        try:
            code = None
            for i in range(api.LOGIN_IP_MAX + 2):
                body = json.dumps({"login": "nonexistent{}".format(i),
                                   "password": "wrong"}).encode()
                code, resp, _ = self._post("/api/v2/auth/login", body)
                if code == 429:
                    break
            self.assertEqual(code, 429,
                             "per-IP лимит входа не сработал (последний код %s)" % code)
            self.assertIn("too_many_attempts", resp.decode())
        finally:
            api._LOGIN_IP_ATTEMPTS.clear()
            api._LOGIN_ATTEMPTS.clear()


if __name__ == "__main__":
    unittest.main()
