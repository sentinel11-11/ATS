# -*- coding: utf-8 -*-
"""Тесты production-hardening: даты пула, fallback, вебхук, секреты,
ACD-порядок, None-канал агента, static-traversal, файл учётных данных.

Запуск: python -m unittest discover -s tests -v   (из корня репозитория)
"""
import datetime
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

_TMP = tempfile.mkdtemp(prefix="ats_test_hard_")
os.environ["ATS_FAST"] = "1"
os.environ["ATS_DATA_DIR"] = _TMP
os.environ["ATS_ADMIN_PASSWORD"] = "TestAdmin123!"
os.environ["ATS_DEV_SEED"] = "1"

from app import api, config, db, numbers as numbers_mod  # noqa: E402
from app.engine import Engine  # noqa: E402


def _save_settings():
    return json.loads(json.dumps(db.get_settings()))


def _restore_settings(snap):
    db.save_settings(snap)


class TestNumbersDate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.init_db()

    def test_daily_reset_after_midnight(self):
        """Счётчики сбрасываются по актуальной дате, а не дате импорта модуля."""
        numbers_mod.add_number("79997770001", label="hardening-date")
        yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
        db.q("UPDATE numbers SET daily_date=?, daily_count=5 WHERE number=?",
             (yesterday, "79997770001"))
        numbers_mod.reset_daily_if_needed()
        r = db.fetch1("SELECT daily_date, daily_count FROM numbers WHERE number=?",
                      ("79997770001",))
        self.assertEqual(r["daily_count"], 0)
        self.assertEqual(r["daily_date"], datetime.date.today().isoformat())

    def test_today_is_dynamic(self):
        import app.numbers as m
        self.assertFalse(hasattr(m, "TODAY"))
        self.assertTrue(callable(m.today))
        self.assertEqual(m.today(), datetime.date.today().isoformat())


class TestSimFallback(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.init_db()

    def test_no_silent_fallback_by_default(self):
        snap = _save_settings()
        try:
            s = db.get_settings()
            s["provider"] = "uis"
            s["uis"] = {"api_url": "", "api_key": ""}
            db.save_settings(s)
            os.environ.pop("ATS_ALLOW_SIM_FALLBACK", None)
            with self.assertRaises(Exception):
                Engine(auto_start=False)
        finally:
            _restore_settings(snap)

    def test_explicit_fallback_flag(self):
        snap = _save_settings()
        try:
            s = db.get_settings()
            s["provider"] = "uis"
            s["uis"] = {"api_url": "", "api_key": ""}
            db.save_settings(s)
            os.environ["ATS_ALLOW_SIM_FALLBACK"] = "1"
            eng = Engine(auto_start=False)
            self.assertEqual(eng.provider.name, "sim")
        finally:
            os.environ.pop("ATS_ALLOW_SIM_FALLBACK", None)
            _restore_settings(snap)


class TestAcceptOrder(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.init_db()
        cls.engine = Engine(auto_start=False)

    def _make_acd(self):
        call_id = db.insert("calls", {"campaign_id": 0, "item_id": 0, "contact_id": 0,
                                      "contact_name": "H", "contact_phone": "79997770009",
                                      "caller_id": "", "number_id": 0, "provider": "sim",
                                      "direction": "out", "status": "talk", "result": "",
                                      "detail": "", "agent_result": "", "recording": "",
                                      "started_at": config.now_iso(), "answered_at": "",
                                      "ended_at": "", "duration_sec": 0})
        return db.insert("acd", {"call_id": call_id, "item_id": 0, "operator_id": 0,
                                 "status": "queued", "created": config.now_iso(),
                                 "updated": config.now_iso()})

    def test_no_free_operator(self):
        snap = {r["id"]: r["status"] for r in db.fetch("SELECT id, status FROM operators")}
        try:
            db.q("UPDATE operators SET status='busy'")
            acd_id = self._make_acd()
            ok, err = self.engine.accept_acd(acd_id)
            self.assertFalse(ok)
            self.assertEqual(err, "no_free_operator")
            # виртуальный оператор не создан
            self.assertEqual(len(db.fetch("SELECT id FROM operators")), len(snap))
        finally:
            for oid, st in snap.items():
                db.q("UPDATE operators SET status=? WHERE id=?", (st, oid))

    def test_transfer_failed_rolls_back(self):
        op_id = db.insert("operators", {"user_id": 0, "name": "Hardening-op",
                                        "ext": "102", "status": "free",
                                        "updated": config.now_iso()})
        acd_id = self._make_acd()

        class FailProvider:
            name = "fail"

            def connect_operator(self, call_id, ext):
                raise RuntimeError("bridge down")

        real = self.engine.provider
        self.engine.provider = FailProvider()
        try:
            ok, err = self.engine.accept_acd(acd_id, op_id)
            self.assertFalse(ok)
            self.assertEqual(err, "transfer_failed")
            acd = db.fetch1("SELECT * FROM acd WHERE id=?", (acd_id,))
            self.assertEqual(acd["status"], "queued")
            op = db.fetch1("SELECT * FROM operators WHERE id=?", (op_id,))
            self.assertEqual(op["status"], "free")
        finally:
            self.engine.provider = real


class TestAgentNoChannel(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.init_db()
        cls.engine = Engine(auto_start=False)

    def test_no_channel_result_instead_of_crash(self):
        cid = db.insert("contacts", {"name": "NC", "phone": "79997770011", "grp": "",
                                     "note": "", "consent": 1, "consent_source": "test",
                                     "blacklisted": 0, "complaints": 0,
                                     "created": config.now_iso(), "updated": config.now_iso()})
        camp_id = db.insert("campaigns", {"name": "NC-camp", "template_id": 2,
                                          "flow": "agent", "status": "stopped",
                                          "schedule": "{}", "max_channels": 1,
                                          "retry_max": 0, "retry_delay_min": 1,
                                          "connect_on_qualify": 0,
                                          "created": config.now_iso(),
                                          "updated": config.now_iso()})
        item_id = db.insert("campaign_items", {"campaign_id": camp_id, "contact_id": cid,
                                               "contact_name": "NC",
                                               "contact_phone": "79997770011",
                                               "status": "agent", "attempts": 1,
                                               "next_attempt_at": "", "last_result": "",
                                               "created": config.now_iso(),
                                               "updated": config.now_iso(), "completed_at": ""})
        call_id = db.insert("calls", {"campaign_id": camp_id, "item_id": item_id,
                                      "contact_id": cid, "contact_name": "NC",
                                      "contact_phone": "79997770011", "caller_id": "",
                                      "number_id": 0, "provider": "sim", "direction": "out",
                                      "status": "answered", "result": "", "detail": "",
                                      "agent_result": "", "recording": "",
                                      "started_at": config.now_iso(),
                                      "answered_at": config.now_iso(), "ended_at": "",
                                      "duration_sec": 0})
        real_mc = self.engine.provider.make_channel
        self.engine.provider.make_channel = lambda *a, **k: None
        try:
            call = db.fetch1("SELECT * FROM calls WHERE id=?", (call_id,))
            item = db.fetch1("SELECT * FROM campaign_items WHERE id=?", (item_id,))
            camp = db.fetch1("SELECT * FROM campaigns WHERE id=?", (camp_id,))
            self.engine._run_agent(call, item, camp)  # не должно падать
        finally:
            self.engine.provider.make_channel = real_mc
        row = db.fetch1("SELECT agent_result, result FROM calls WHERE id=?", (call_id,))
        res = json.loads(row["agent_result"] or "{}")
        self.assertEqual(res.get("engine"), "no_channel")
        self.assertEqual(row["result"], "qualified_no")


class TestCredFile(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.init_db()

    def test_password_change_drops_file(self):
        cred = config.DATA_DIR / "initial_credentials.txt"
        cred.write_text("dummy", encoding="utf-8")
        u = db.fetch1("SELECT salt, password_hash FROM users WHERE login='admin'")
        try:
            self.assertTrue(db.set_admin_password("TmpHard123!"))
            self.assertFalse(cred.exists())
        finally:
            db.q("UPDATE users SET salt=?, password_hash=? WHERE login='admin'",
                 (u["salt"], u["password_hash"]))


class TestHardeningHttp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.init_db()
        from app.server import create_server
        cls.engine = Engine(auto_start=False)
        api.ENGINE = cls.engine
        cls.httpd = create_server("127.0.0.1", 0)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        time.sleep(0.2)
        cls.base = "http://127.0.0.1:{}".format(cls.port)

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def call(self, method, path, body=None, token="", headers=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method)
        if body is not None:
            req.add_header("Content-Type", "application/json")
        if token:
            req.add_header("X-Ats-Token", token)
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            try:
                return e.code, e.read().decode()
            except Exception:
                return e.code, ""

    def _token(self):
        s, raw = self.call("POST", "/api/v2/auth/login",
                           {"login": "admin", "password": "TestAdmin123!"})
        self.assertEqual(s, 200)
        return json.loads(raw)["token"]

    def test_webhook_requires_secret(self):
        snap = _save_settings()
        try:
            s = db.get_settings()
            s["uis"] = {"api_url": "http://x", "api_key": "k", "webhook_secret": ""}
            db.save_settings(s)
            st, raw = self.call("POST", "/api/v2/webhooks/uis",
                                {"call_id": 424242, "status": "ringing"})
            self.assertEqual(st, 403)
            self.assertIn("webhook_disabled", raw)
            s["uis"]["webhook_secret"] = "s3cr3t"
            db.save_settings(s)
            st, _ = self.call("POST", "/api/v2/webhooks/uis",
                              {"call_id": 424242, "status": "ringing"})
            self.assertEqual(st, 403)  # без заголовка
            st, _ = self.call("POST", "/api/v2/webhooks/uis",
                              {"call_id": 424242, "status": "ringing"},
                              headers={"X-UIS-Secret": "s3cr3t"})
            self.assertEqual(st, 200)  # неизвестный call_id — тихо игнорируется
        finally:
            _restore_settings(snap)

    def test_settings_raw_masks_and_keeps(self):
        snap = _save_settings()
        try:
            s = db.get_settings()
            s["uis"] = {"api_url": "http://x", "api_key": "SECRET123"}
            s["ami"] = {"host": "h", "port": 5038, "user": "u", "secret": "S2"}
            s["llm"] = {"enabled": False, "base_url": "", "api_key_env": "ATS_LLM_KEY",
                        "model": ""}
            db.save_settings(s)
            tok = self._token()
            st, raw = self.call("GET", "/api/v2/settings/raw", token=tok)
            self.assertEqual(st, 200)
            cfg = json.loads(raw)["provider_config"]
            self.assertEqual(cfg["uis"]["api_key"], "********")
            self.assertEqual(cfg["ami"]["secret"], "********")
            self.assertEqual(cfg["llm"]["api_key_env"], "ATS_LLM_KEY")  # не секрет
            self.assertNotIn("SECRET123", raw)
            # сохранение без изменений не затирает секреты
            st, _ = self.call("POST", "/api/v2/settings/raw",
                              {"provider_config": cfg}, token=tok)
            self.assertEqual(st, 200)
            self.assertEqual(db.get_settings()["uis"]["api_key"], "SECRET123")
            # явное новое значение — обновляется
            cfg["uis"]["api_key"] = "NEWKEY"
            st, _ = self.call("POST", "/api/v2/settings/raw",
                              {"provider_config": cfg}, token=tok)
            self.assertEqual(st, 200)
            self.assertEqual(db.get_settings()["uis"]["api_key"], "NEWKEY")
        finally:
            _restore_settings(snap)

    def test_login_case_insensitive(self):
        tok = self._token()
        st, _ = self.call("POST", "/api/v2/users/save",
                          {"login": "HardCase1", "role": "operator",
                           "password": "HardCase123!", "name": "Регистр Тест",
                           "ext": "103"}, token=tok)
        self.assertEqual(st, 200)
        row = db.fetch1("SELECT login FROM users WHERE login='hardcase1'")
        self.assertTrue(row)
        for variant in ("HardCase1", "HARDCASE1", "hardcase1"):
            st, raw = self.call("POST", "/api/v2/auth/login",
                                {"login": variant, "password": "HardCase123!"})
            self.assertEqual(st, 200, variant)
            self.assertEqual(json.loads(raw)["role"], "operator")

    def test_cli_set_password_recovery(self):
        tok = self._token()
        st, _ = self.call("POST", "/api/v2/users/save",
                          {"login": "hardcli1", "role": "operator",
                           "password": "HardCli123!", "name": "CLI"}, token=tok)
        self.assertEqual(st, 200)
        st, _ = self.call("POST", "/api/v2/auth/login",
                          {"login": "hardcli1", "password": "WrongPass"})
        self.assertEqual(st, 403)
        self.assertTrue(db.set_user_password("HARDcli1", "NewHard456!"))
        st, raw = self.call("POST", "/api/v2/auth/login",
                            {"login": "hardcli1", "password": "NewHard456!"})
        self.assertEqual(st, 200)
        self.assertFalse(db.set_user_password("no-such-user", "xxx"))

    def test_static_traversal_blocked(self):
        st, raw = self.call("GET", "/ui/../../app/db.py")
        self.assertEqual(st, 200)
        self.assertNotIn("sqlite3", raw)
        st, raw = self.call("GET", "/ui/index.html")
        self.assertEqual(st, 200)
        self.assertIn("html", raw.lower())


class TestQuietServer(unittest.TestCase):
    def test_connection_noise_silenced_real_errors_shown(self):
        import io
        from contextlib import redirect_stderr
        from app.server import create_server
        srv = create_server("127.0.0.1", 0)
        try:
            try:
                raise ConnectionAbortedError(10053, "teardown")
            except OSError:
                buf = io.StringIO()
                with redirect_stderr(buf):
                    srv.handle_error(None, ("127.0.0.1", 1))
                self.assertEqual(buf.getvalue(), "")
            try:
                raise ValueError("boom")
            except ValueError:
                buf = io.StringIO()
                with redirect_stderr(buf):
                    srv.handle_error(None, ("127.0.0.1", 1))
                self.assertIn("ValueError", buf.getvalue())
        finally:
            srv.server_close()


if __name__ == "__main__":
    unittest.main()
