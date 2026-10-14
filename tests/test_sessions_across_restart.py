# -*- coding: utf-8 -*-
"""Сессии API: токен переживает рестарт сервиса и не светится в БД открытым текстом.

Регресс живой: `install-multicom-trunk.sh --restart` (как и любой `systemctl restart ats`)
обнулял память процесса, и следующая же команда с валидным ATS_TOKEN получала
{"ok": false, "error": "auth_required"} — выглядело как «токен сломался», хотя логин был
верный, а UI после рестарта вообще разлогинивался.
"""
import os
import pathlib
import sqlite3
import tempfile
import time
import unittest

_TMP = tempfile.mkdtemp(prefix="ats_sessions_")
os.environ.setdefault("ATS_FAST", "1")
os.environ["ATS_DATA_DIR"] = _TMP
os.environ["ATS_ADMIN_PASSWORD"] = "TestAdmin123!"

from app import config, db, security  # noqa: E402


class SessionsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.init_db()

    def setUp(self):
        db.q("DELETE FROM sessions")
        security.SESSIONS.clear()

    def test_token_survives_process_restart(self):
        tok = security.create_token("admin", "admin")
        security.SESSIONS.clear()              # ровно это происходит при рестарте сервиса
        s = security.get_session(tok)
        self.assertIsNotNone(s, "токен умер вместе с процессом — сессии надо хранить в БД")
        self.assertEqual(s["login"], "admin")
        self.assertEqual(s["role"], "admin")

    def test_api_need_auth_works_after_restart(self):
        from app import api
        tok = security.create_token("admin", "admin")
        security.SESSIONS.clear()
        s, err, code = api.need_auth({"X-Ats-Token": tok}, role="admin")
        self.assertIsNone(err, "валидный токен после рестарта дал auth_required")
        self.assertEqual(s["login"], "admin")
        _s2, err2, code2 = api.need_auth({"X-Ats-Token": ""})
        self.assertIsNotNone(err2, "пустой заголовок обязан отклонять")
        self.assertEqual(code2, 401)

    def test_raw_token_never_reaches_the_database(self):
        tok = security.create_token("admin", "admin")
        rows = db.fetch("SELECT token_hash FROM sessions")
        hashes = [r["token_hash"] for r in rows]
        self.assertIn(security.token_hash(tok), hashes)
        self.assertNotIn(tok, hashes, "в БД должен лежать хэш, а не сам токен")
        for name in ("ats.db", "ats.db-wal"):
            f = pathlib.Path(config.DB_PATH).with_name(name)
            if f.exists():
                self.assertNotIn(tok.encode(), f.read_bytes(),
                                "дамп/backup БД не должен выдавать живые токены доступа")

    def test_expired_row_is_rejected_and_purged(self):
        tok = security.create_token("admin", "admin")
        h = security.token_hash(tok)
        db.q("UPDATE sessions SET exp=? WHERE token_hash=?", (time.time() - 5, h))
        security.SESSIONS.clear()
        self.assertIsNone(security.get_session(tok))
        self.assertIsNone(db.fetch1("SELECT 1 FROM sessions WHERE token_hash=?", (h,)),
                          "протухшую строку надо вычищать, а не копить")

    def test_logout_and_password_change_drop_db_rows(self):
        a = security.create_token("admin", "admin")
        b = security.create_token("admin", "admin")
        security.drop_token(a)
        security.SESSIONS.clear()
        self.assertIsNone(security.get_session(a), "logout должен удалять и строку в БД")
        self.assertIsNotNone(security.get_session(b))
        security.drop_sessions_for("admin")     # смена/сброс пароля
        security.SESSIONS.clear()
        self.assertIsNone(security.get_session(b))

    def test_sliding_expiration_is_not_written_on_every_request(self):
        tok = security.create_token("admin", "admin")
        h = security.token_hash(tok)
        first = db.fetch1("SELECT exp, slid FROM sessions WHERE token_hash=?", (h,))
        self.assertEqual(first["slid"], 0)
        for _ in range(5):                      # UI опрашивает API раз в пару секунд
            security.SESSIONS.clear()
            self.assertIsNotNone(security.get_session(tok))
        second = db.fetch1("SELECT exp, slid FROM sessions WHERE token_hash=?", (h,))
        self.assertEqual(second["slid"], 0, "пока срок полный, БД трогать нечем")
        self.assertEqual(second["exp"], first["exp"])
        # а когда срок поджимает — сессия продлевается, активный пользователь не вылетает
        db.q("UPDATE sessions SET exp=? WHERE token_hash=?", (time.time() + 60, h))
        security.SESSIONS.clear()
        self.assertIsNotNone(security.get_session(tok))
        third = db.fetch1("SELECT exp, slid FROM sessions WHERE token_hash=?", (h,))
        self.assertGreater(third["slid"], 0)
        self.assertGreater(third["exp"], time.time() + security.SESSION_TTL - 120)

    def test_security_works_without_database(self):
        """Без БД (аварийный запуск, тестовый стенд) аутентификация деградирует в память,
        а не падает: иначе один сбой диска оставит нас без доступа к UI совсем."""
        real = security._db
        security._db = lambda: (_ for _ in ()).throw(RuntimeError("no db"))
        try:
            tok = security.create_token("admin", "admin")
            s = security.get_session(tok)
            self.assertIsNotNone(s)
            self.assertEqual(s["login"], "admin")
        finally:
            security._db = real


if __name__ == "__main__":
    unittest.main()
