# -*- coding: utf-8 -*-
"""Верхнеуровневый обработчик ошибок API (P2 повторного ревью).

Раньше необработанное исключение _route (например, int('abc') в body)
распространялось наружу: соединение рвалось без контролируемого ответа,
traceback мог утекать клиенту, а диагностика не связывалась.

Проверяем:
- некорректные входные данные → 400 bad_request (JSON, без traceback);
- непредвиденная ошибка → 500 internal_error + correlation_id, полный
  traceback — только в лог сервера (в ответе клиента его нет);
- оба случая по-прежнему пишутся в аудит.
"""
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

_TMP = tempfile.mkdtemp(prefix="ats_test_errh_")
os.environ["ATS_FAST"] = "1"
os.environ["ATS_DATA_DIR"] = _TMP
os.environ["ATS_ADMIN_PASSWORD"] = "TestAdmin123!"
os.environ["ATS_DEV_SEED"] = "1"

from app import api, db  # noqa: E402

db.init_db()
_seed = db.get_settings()
if not _seed.get("provider"):
    _seed["provider"] = "sim"
    db.save_settings(_seed)


class ApiErrorHandlerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from app.server import create_server
        cls.httpd = create_server("127.0.0.1", 0)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        time.sleep(0.2)
        cls.base = "http://127.0.0.1:{}".format(cls.port)
        req = urllib.request.Request(
            cls.base + "/api/v2/auth/login",
            data=json.dumps({"login": "admin", "password": "TestAdmin123!"}).encode(),
            method="POST", headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            cls.token = json.loads(r.read().decode())["token"]

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def call(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            self.base + path, data=data, method=method,
            headers={"Content-Type": "application/json", "X-Ats-Token": self.token})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()

    def test_bad_input_returns_400_json(self):
        # users/save приводит id через int(): 'abc' → ValueError → 400
        code, body = self.call("POST", "/api/v2/users/save",
                               {"id": "abc", "login": "x"})
        self.assertEqual(code, 400, body)
        j = json.loads(body)
        self.assertFalse(j["ok"])
        self.assertEqual(j["error"], "bad_request")
        self.assertNotIn("Traceback", body, "traceback не должен уходить клиенту")

    def test_unexpected_error_returns_500_with_correlation_id(self):
        from unittest import mock

        def boom(*a, **kw):
            raise RuntimeError("внутренняя ошибка")
        with mock.patch.object(api, "_export_calls", side_effect=boom):
            code, body = self.call("GET", "/api/v2/export/calls.csv")
        self.assertEqual(code, 500, body)
        j = json.loads(body)
        self.assertEqual(j["error"], "internal_error")
        self.assertTrue(j.get("correlation_id"), "500 обязан нести correlation_id")
        self.assertNotIn("Traceback", body)
        self.assertNotIn("внутренняя ошибка", body,
                         "детали внутренней ошибки не уходят клиенту")
        # correlation_id связывает с логом: в аудите фиксируется 500
        row = db.fetch1("SELECT status, path FROM audit_logs "
                        "WHERE path LIKE '%export%' ORDER BY id DESC LIMIT 1")
        self.assertIsNotNone(row, "ошибочный ответ пишется в аудит")

    def test_normal_routes_unaffected(self):
        # маршрут без ENGINE (не зависит от движка)
        code, body = self.call("GET", "/api/v2/templates")
        self.assertEqual(code, 200, body)


if __name__ == "__main__":
    unittest.main()
