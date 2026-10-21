# -*- coding: utf-8 -*-
"""Одноразовые билеты URL-доступа (SSE/экспорт/записи) — P1-аудита.

Долгоживущий токен сессии больше не передаётся в query string (access-логи
прокси, история браузера, скриншоты). Вместо него — билет: TTL 60 с, одно
использование, в БД только SHA-256, сам по себе сессией не является.

Проверяем:
- POST /auth/ticket требует заголовок сессии и выдаёт билет;
- билет открывает экспорт/SSE/запись ровно один раз;
- повторное использование, просроченный и чужой билет — отказ;
- токен сессии в query string больше НЕ работает (ломающее изменение,
  осознанное: см. docs/engineering/BASELINE.md, этап B);
- подделка заголовка X-Ats-Ticket-Actor клиентом не меняет актора аудита.
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

_TMP = tempfile.mkdtemp(prefix="ats_test_tix_")
os.environ["ATS_FAST"] = "1"
os.environ["ATS_DATA_DIR"] = _TMP
os.environ["ATS_ADMIN_PASSWORD"] = "TestAdmin123!"
os.environ["ATS_DEV_SEED"] = "1"

from app import api, config, db, security  # noqa: E402
from app.engine import Engine  # noqa: E402

db.init_db()
_test_seed = db.get_settings()
if not _test_seed.get("provider"):
    _test_seed["provider"] = "sim"
    db.save_settings(_test_seed)


class UrlTicketsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.init_db()
        from app.server import create_server
        cls.engine = Engine()
        api.ENGINE = cls.engine
        cls.httpd = create_server("127.0.0.1", 0)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        time.sleep(0.2)
        cls.base = "http://127.0.0.1:{}".format(cls.port)
        # вход
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

    def call(self, method, path, body=None, token="", headers=None):
        data = json.dumps(body).encode() if body is not None else None
        h = dict(headers or {})
        if token:
            h["X-Ats-Token"] = token
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, r.read(), dict(r.headers)
        except urllib.error.HTTPError as e:
            return e.code, e.read(), dict(e.headers)

    def test_ticket_issue_requires_session(self):
        code, body, _ = self.call("POST", "/api/v2/auth/ticket")
        self.assertEqual(code, 401, "выдача билета без сессии должна требовать вход")
        code, body, _ = self.call("POST", "/api/v2/auth/ticket", {}, token=self.token)
        self.assertEqual(code, 200)
        j = json.loads(body)
        self.assertTrue(j["ok"])
        self.assertIn("ticket", j)
        self.assertLessEqual(j["expires_in"], security.TICKET_TTL + 1)

    def test_ticket_opens_export_once(self):
        _, body, _ = self.call("POST", "/api/v2/auth/ticket", {}, token=self.token)
        ticket = json.loads(body)["ticket"]
        code, body, _ = self.call("GET", "/api/v2/export/calls.csv?ticket=" + ticket)
        self.assertEqual(code, 200, "валидный билет обязан открывать экспорт")
        # билет одноразовый
        code2, _, _ = self.call("GET", "/api/v2/export/calls.csv?ticket=" + ticket)
        self.assertEqual(code2, 401, "повторное использование билета должно отклоняться")

    def test_expired_ticket_rejected(self):
        _, body, _ = self.call("POST", "/api/v2/auth/ticket", {}, token=self.token)
        ticket = json.loads(body)["ticket"]
        db.q("UPDATE auth_tickets SET exp=? WHERE ticket_hash=?",
             (time.time() - 1, security.token_hash(ticket)))
        code, _, _ = self.call("GET", "/api/v2/export/calls.csv?ticket=" + ticket)
        self.assertEqual(code, 401, "просроченный билет не должен работать")

    def test_session_token_in_query_rejected(self):
        code, _, _ = self.call("GET", "/api/v2/export/calls.csv?token=" + self.token)
        self.assertEqual(code, 401,
                         "токен сессии в query string больше не принимается (P1)")
        code, _, _ = self.call("GET", "/api/v2/export/calls.csv")
        self.assertEqual(code, 401)

    def test_garbage_ticket_rejected(self):
        code, _, _ = self.call("GET", "/api/v2/export/calls.csv?ticket=deadbeef")
        self.assertEqual(code, 401)

    def test_ticket_is_not_a_session_token(self):
        """Билет не даёт доступ к обычным API-маршрутам через заголовок."""
        _, body, _ = self.call("POST", "/api/v2/auth/ticket", {}, token=self.token)
        ticket = json.loads(body)["ticket"]
        code, _, _ = self.call("GET", "/api/v2/dashboard", headers={"X-Ats-Token": ticket})
        self.assertEqual(code, 401, "билет — не сессия: в заголовке он не работает")

    def test_sse_via_ticket(self):
        _, body, _ = self.call("POST", "/api/v2/auth/ticket", {}, token=self.token)
        ticket = json.loads(body)["ticket"]
        req = urllib.request.Request(self.base + "/api/v2/events?ticket=" + ticket)
        with urllib.request.urlopen(req, timeout=10) as r:
            self.assertEqual(r.status, 200)
            self.assertIn("text/event-stream", r.headers.get("Content-Type", ""))
            r.fp.raw._sock.settimeout(2)  # noqa: SLF001 — читаем первый чанк и закрываем
            try:
                chunk = r.read(64)
                self.assertTrue(chunk is not None)
            except (socket.timeout, TimeoutError):
                pass  # keepalive придёт позже — важен уже сам 200 event-stream
        # второй раз тем же билетом — отказ
        req2 = urllib.request.Request(self.base + "/api/v2/events?ticket=" + ticket)
        try:
            with urllib.request.urlopen(req2, timeout=10) as r:
                self.fail("повторное использование билета для SSE прошло: %s" % r.status)
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 401)

    def test_forged_audit_actor_header_is_scrubbed(self):
        """Клиент не может подделать актора аудита заголовком X-Ats-Ticket-Actor."""
        code, _, _ = self.call(
            "GET", "/api/v2/export/calls.csv?ticket=whatever",
            headers={"X-Ats-Ticket-Actor": json.dumps({"login": "admin", "role": "admin"})})
        self.assertEqual(code, 401, "мусорный билет должен отклоняться до аудита")
        # а валидный билет с подделанным заголовком не подменяет актора
        _, body, _ = self.call("POST", "/api/v2/auth/ticket", {}, token=self.token)
        ticket = json.loads(body)["ticket"]
        self.call("GET", "/api/v2/export/calls.csv?ticket=" + ticket,
                  headers={"X-Ats-Ticket-Actor": json.dumps({"login": "fake", "role": "admin"})})
        row = db.fetch1("SELECT actor_login FROM audit_logs WHERE path LIKE '%export%' "
                        "ORDER BY id DESC LIMIT 1")
        # актор определён из проверенного билета (admin), не из заголовка клиента
        self.assertIn(row and row["actor_login"], (None, "", "admin"),
                      "подделанный актор аудита: %r" % (row and row["actor_login"]))

    def test_ticket_ttl_and_hash_storage(self):
        _, body, _ = self.call("POST", "/api/v2/auth/ticket", {}, token=self.token)
        ticket = json.loads(body)["ticket"]
        rows = db.fetch("SELECT ticket_hash FROM auth_tickets")
        self.assertIn(security.token_hash(ticket), [r["ticket_hash"] for r in rows],
                      "в БД должен лежать хэш билета, не сам билет")
        for r in rows:
            self.assertNotIn(ticket, r["ticket_hash"])


class SecurityHeadersAndBindTest(unittest.TestCase):
    """P1-аудита: security-заголовки на всех ответах; loopback-умолчание."""

    @classmethod
    def setUpClass(cls):
        # свой сервер: порядок классов в загрузчике алфавитный, полагаться на
        # чужой setUpClass нельзя
        from app.server import create_server
        cls.httpd = create_server("127.0.0.1", 0)
        cls.base = "http://127.0.0.1:{}".format(cls.httpd.server_address[1])
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        time.sleep(0.2)

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def _call(self, method, path):
        req = urllib.request.Request(self.base + path, method=method)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, r.read(), dict(r.headers)
        except urllib.error.HTTPError as e:
            return e.code, e.read(), dict(e.headers)

    def test_security_headers_on_api_responses(self):
        code, _, headers = self._call("GET", "/api/v2/dashboard")
        self.assertEqual(code, 401)
        self.assertEqual(headers.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(headers.get("X-Frame-Options"), "DENY")
        self.assertEqual(headers.get("Referrer-Policy"), "no-referrer")
        self.assertIn("frame-ancestors 'none'", headers.get("Content-Security-Policy", ""))

    def test_security_headers_on_static_ui(self):
        code, body, headers = self._call("GET", "/ui/index.html")
        self.assertEqual(code, 200)
        self.assertEqual(headers.get("X-Content-Type-Options"), "nosniff")
        self.assertIn("default-src 'self'", headers.get("Content-Security-Policy", ""))

    def test_loopback_is_default_bind(self):
        from app import config as cfg
        self.assertEqual(cfg.HOST_DEFAULT, "127.0.0.1",
                         "приложение не терминирует TLS — умолчание loopback")
        self.assertEqual(cfg.DEFAULT_SETTINGS.get("host"), "127.0.0.1")


if __name__ == "__main__":
    unittest.main()


class SecurityHeadersAndBindTest(unittest.TestCase):
    """P1-аудита: security-заголовки на всех ответах; loopback-умолчание."""

    @classmethod
    def setUpClass(cls):
        # свой сервер: порядок классов в загрузчике алфавитный, полагаться на
        # чужой setUpClass нельзя
        from app.server import create_server
        cls.httpd = create_server("127.0.0.1", 0)
        cls.base = "http://127.0.0.1:{}".format(cls.httpd.server_address[1])
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        time.sleep(0.2)

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def _call(self, method, path):
        req = urllib.request.Request(self.base + path, method=method)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, r.read(), dict(r.headers)
        except urllib.error.HTTPError as e:
            return e.code, e.read(), dict(e.headers)

    def test_security_headers_on_api_responses(self):
        code, _, headers = self._call("GET", "/api/v2/dashboard")
        self.assertEqual(code, 401)
        self.assertEqual(headers.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(headers.get("X-Frame-Options"), "DENY")
        self.assertEqual(headers.get("Referrer-Policy"), "no-referrer")
        self.assertIn("frame-ancestors 'none'", headers.get("Content-Security-Policy", ""))

    def test_security_headers_on_static_ui(self):
        code, body, headers = self._call("GET", "/ui/index.html")
        self.assertEqual(code, 200)
        self.assertEqual(headers.get("X-Content-Type-Options"), "nosniff")
        self.assertIn("default-src 'self'", headers.get("Content-Security-Policy", ""))

    def test_loopback_is_default_bind(self):
        from app import config as cfg
        self.assertEqual(cfg.HOST_DEFAULT, "127.0.0.1",
                         "приложение не терминирует TLS — умолчание loopback")
        self.assertEqual(cfg.DEFAULT_SETTINGS.get("host"), "127.0.0.1")


if __name__ == "__main__":
    unittest.main()
