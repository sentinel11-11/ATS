# -*- coding: utf-8 -*-
"""Fail-closed отзыв сессий (P1 повторного ревью).

Раньше drop_token()/drop_sessions_for() сначала чистили кэш памяти, а ошибку
DELETE из БД молча глотали: /auth/logout отвечал ok:true, при этом строка
сессии оставалась в БД и продолжала действовать после восстановления БД.

Проверяем:
- logout при сбое DELETE → 503, сессия НЕ считается отозванной (работает);
- logout при живой БД → 200, сессия отозвана;
- деактивация пользователя при сбое DELETE → 503 и откат (active=1);
- смена пароля при сбое DELETE → 503 и откат (старый пароль жив);
- удаление пользователя при сбое DELETE → 503 и откат (пользователь жив);
- CLI set_user_password при сбое → исключение, пароль НЕ изменён.
"""
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from unittest import mock

_TMP = tempfile.mkdtemp(prefix="ats_test_rev_")
os.environ["ATS_FAST"] = "1"
os.environ["ATS_DATA_DIR"] = _TMP
os.environ["ATS_ADMIN_PASSWORD"] = "TestAdmin123!"
os.environ["ATS_DEV_SEED"] = "1"

from app import api, db, security  # noqa: E402
from app.engine import Engine  # noqa: E402

db.init_db()
_seed = db.get_settings()
if not _seed.get("provider"):
    _seed["provider"] = "sim"
    db.save_settings(_seed)


def _failing_delete(sql, *a, **kw):
    if "delete from sessions" in str(sql or "").lower():
        raise RuntimeError("injected sessions-table failure")
    return db._orig_q(sql, *a, **kw)


class RevocationFailClosedTest(unittest.TestCase):
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
        # оператор, чьи сессии будем «не смочь отозвать»
        db.q("DELETE FROM operators WHERE user_id IN (SELECT id FROM users WHERE login='op1')")
        db.q("DELETE FROM users WHERE login='op1'")
        db.insert("users", {"login": "op1", "role": "operator", "salt": "s",
                            "password_hash": security.hash_password("OpPass123!", "s"),
                            "active": 1, "created": "2026-01-01 00:00:00"})

    @classmethod
    def tearDownClass(cls):
        cls.engine.stop()
        cls.httpd.shutdown()

    def call(self, method, path, body=None, token=""):
        data = json.dumps(body).encode() if body is not None else None
        h = {"Content-Type": "application/json"}
        if token:
            h["X-Ats-Token"] = token
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read().decode() or "{}")
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read().decode() or "{}")
            except Exception:
                return e.code, {}

    def _login(self, login, password):
        code, j = self.call("POST", "/api/v2/auth/login", {"login": login,
                                                           "password": password})
        self.assertEqual(code, 200, "вход {}: {}".format(login, j))
        return j["token"]

    def test_logout_fails_closed_on_db_error(self):
        tok = self._login("admin", "TestAdmin123!")
        db._orig_q = db.q
        with mock.patch("app.db.q", side_effect=_failing_delete):
            code, j = self.call("POST", "/api/v2/auth/logout", {}, token=tok)
        self.assertEqual(code, 503, "logout при сбое DELETE обязан отвечать 503: %r" % j)
        self.assertEqual(j.get("error"), "auth_storage_unavailable")
        # сессия НЕ отозвана: клиент видит честный отказ, доступ жив
        code2, j2 = self.call("GET", "/api/v2/auth/me", token=tok)
        self.assertEqual(code2, 200, "сессия должна работать, пока отзыв не подтверждён")
        # и после «лечения» БД logout проходит штатно
        code3, _ = self.call("POST", "/api/v2/auth/logout", {}, token=tok)
        self.assertEqual(code3, 200)
        code4, _ = self.call("GET", "/api/v2/auth/me", token=tok)
        self.assertEqual(code4, 401, "после успешного logout сессия отозвана")

    def test_deactivate_rolls_back_on_revocation_failure(self):
        tok = self._login("op1", "OpPass123!")
        admin_tok = self._login("admin", "TestAdmin123!")
        uid = db.fetch1("SELECT id FROM users WHERE login='op1'")["id"]
        db._orig_q = db.q
        # переключатель «доступ»: {"id": uid, "active": 0} без login
        with mock.patch("app.db.q", side_effect=_failing_delete):
            code, j = self.call("POST", "/api/v2/users/save",
                                {"id": uid, "active": 0}, token=admin_tok)
        self.assertEqual(code, 503, "деактивация при сбое отзыва — 503: %r" % j)
        u = db.fetch1("SELECT active FROM users WHERE login='op1'")
        self.assertEqual(u["active"], 1, "деактивация обязана откатиться")
        code2, _ = self.call("GET", "/api/v2/auth/me", token=tok)
        self.assertEqual(code2, 200, "сессия оператора жива (отката не было)")

    def test_password_change_rolls_back_on_revocation_failure(self):
        admin_tok = self._login("admin", "TestAdmin123!")
        uid = db.fetch1("SELECT id FROM users WHERE login='op1'")["id"]
        db._orig_q = db.q
        with mock.patch("app.db.q", side_effect=_failing_delete):
            code, j = self.call("POST", "/api/v2/users/save",
                                {"id": uid, "login": "op1", "role": "operator",
                                 "password": "NewPass123!", "active": 1},
                                token=admin_tok)
        self.assertEqual(code, 503, "смена пароля при сбое отзыва — 503: %r" % j)
        # откат: старый пароль работает, новый — нет
        code2, _ = self.call("POST", "/api/v2/auth/login",
                             {"login": "op1", "password": "OpPass123!"})
        self.assertEqual(code2, 200, "старый пароль должен остаться рабочим после отката")
        code3, _ = self.call("POST", "/api/v2/auth/login",
                             {"login": "op1", "password": "NewPass123!"})
        self.assertEqual(code3, 403, "новый пароль не должен примениться при откате")

    def test_user_delete_rolls_back_on_revocation_failure(self):
        admin_tok = self._login("admin", "TestAdmin123!")
        uid = db.fetch1("SELECT id FROM users WHERE login='op1'")["id"]
        db._orig_q = db.q
        with mock.patch("app.db.q", side_effect=_failing_delete):
            code, j = self.call("POST", "/api/v2/users/delete", {"id": uid},
                                token=admin_tok)
        self.assertEqual(code, 503, "удаление при сбое отзыва — 503: %r" % j)
        u = db.fetch1("SELECT id FROM users WHERE login='op1'")
        self.assertIsNotNone(u, "удаление пользователя обязано откатиться")

    def test_cli_set_password_fails_closed(self):
        db._orig_q = db.q
        with mock.patch("app.db.q", side_effect=_failing_delete):
            with self.assertRaises(Exception):
                db.set_user_password("op1", "CliPass123!")
        u = db.fetch1("SELECT password_hash, salt FROM users WHERE login='op1'")
        self.assertEqual(u["password_hash"],
                         security.hash_password("OpPass123!", u["salt"]),
                         "CLI-смена пароля при сбое отзыва не должна применяться")


if __name__ == "__main__":
    unittest.main()
