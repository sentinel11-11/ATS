# -*- coding: utf-8 -*-
"""Тесты production-hardening: даты пула, fallback, вебхук, секреты,
ACD-порядок, None-канал агента, static-traversal, файл учётных данных,
P0-фиксы внешнего ревью (RBAC, согласия, guard-ы, очередь вебхуков).

Запуск: python -m unittest discover -s tests -v   (из корня репозитория)
"""
import base64
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

# Fail-closed провайдера: чистой БД симулятор не подставляется, поэтому тесты
# явно выбирают sim (модули делят одну тестовую БД; сид идемпотентен).
db.init_db()
_test_seed = db.get_settings()
if not _test_seed.get("provider"):
    _test_seed["provider"] = "sim"
    db.save_settings(_test_seed)


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


def _p0_call_row(status="talk", phone="79997770101"):
    return {"campaign_id": 0, "item_id": 0, "contact_id": 0,
            "contact_name": "P0", "contact_phone": phone,
            "caller_id": "", "number_id": 0, "provider": "sim",
            "direction": "out", "status": status, "result": "",
            "detail": "", "agent_result": "", "recording": "",
            "started_at": config.now_iso(), "answered_at": "",
            "ended_at": "", "duration_sec": 0}


def _p0_make_acd(phone="79997770101"):
    call_id = db.insert("calls", _p0_call_row("talk", phone))
    acd_id = db.insert("acd", {"call_id": call_id, "item_id": 0, "operator_id": 0,
                               "status": "queued", "created": config.now_iso(),
                               "updated": config.now_iso()})
    return acd_id, call_id


class TestP0RbacHttp(unittest.TestCase):
    """P0 из внешнего ревью, HTTP-уровень: RBAC владения, согласия,
    guard-ы кампаний, импорт без призраков, вебхук в очередь, номера."""

    @classmethod
    def setUpClass(cls):
        db.init_db()
        from app import security
        for login, name, ext in (("p0op1", "P0-Оператор-1", "201"),
                                 ("p0op2", "P0-Оператор-2", "202")):
            if not db.fetch1("SELECT id FROM users WHERE login=?", (login,)):
                salt = security.new_salt()
                db.insert("users", {"login": login, "role": "operator", "salt": salt,
                                    "password_hash": security.hash_password("op123456", salt),
                                    "active": 1, "created": config.now_iso()})
            u = db.fetch1("SELECT id FROM users WHERE login=?", (login,))
            if not db.fetch1("SELECT id FROM operators WHERE user_id=?", (u["id"],)):
                db.insert("operators", {"user_id": u["id"], "name": name, "ext": ext,
                                        "status": "free", "updated": config.now_iso()})
        if not db.fetch1("SELECT id FROM users WHERE login='p0nobind'"):
            salt = security.new_salt()
            db.insert("users", {"login": "p0nobind", "role": "operator", "salt": salt,
                                "password_hash": security.hash_password("op123456", salt),
                                "active": 1, "created": config.now_iso()})
        cls.engine = Engine(auto_start=False)
        api.ENGINE = cls.engine
        from app.server import create_server
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

    def _login(self, login, password):
        s, raw = self.call("POST", "/api/v2/auth/login",
                           {"login": login, "password": password})
        self.assertEqual(s, 200, raw)
        return json.loads(raw)["token"]

    def _admin(self):
        return self._login("admin", "TestAdmin123!")

    def _free_p0_ops(self):
        db.q("UPDATE operators SET status='free' WHERE name IN ('P0-Оператор-1','P0-Оператор-2')")
        op1 = db.fetch1("SELECT * FROM operators WHERE name='P0-Оператор-1'")
        op2 = db.fetch1("SELECT * FROM operators WHERE name='P0-Оператор-2'")
        return op1, op2

    def test_operator_accept_forces_own(self):
        op1, op2 = self._free_p0_ops()
        acd_id, call_id = _p0_make_acd("79997770101")
        tok = self._login("p0op1", "op123456")
        # подсовываем чужого оператора — должен приняться свой
        st, raw = self.call("POST", "/api/v2/acd/accept",
                            {"id": acd_id, "operator_id": op2["id"]}, token=tok)
        self.assertEqual(st, 200, raw)
        self.assertEqual(json.loads(raw)["operator_id"], op1["id"])
        acd = db.fetch1("SELECT * FROM acd WHERE id=?", (acd_id,))
        self.assertEqual(acd["operator_id"], op1["id"])
        self.assertEqual(acd["status"], "accepted")
        self.assertEqual(db.fetch1("SELECT status FROM operators WHERE id=?",
                                   (op2["id"],))["status"], "free")
        st, raw = self.call("POST", "/api/v2/calls/complete",
                            {"call_id": call_id, "operator_id": op1["id"]}, token=tok)
        self.assertEqual(st, 200, raw)

    def test_operator_complete_foreign_call_refused(self):
        op1, op2 = self._free_p0_ops()
        _acd, call_id = _p0_make_acd("79997770102")
        tok1 = self._login("p0op1", "op123456")
        st, raw = self.call("POST", "/api/v2/acd/accept", {"id": _acd}, token=tok1)
        self.assertEqual(st, 200, raw)
        tok2 = self._login("p0op2", "op123456")
        st, raw = self.call("POST", "/api/v2/calls/complete",
                            {"call_id": call_id, "operator_id": op2["id"]}, token=tok2)
        self.assertEqual(st, 403)
        self.assertIn("not_your_call", raw)
        self.assertEqual(db.fetch1("SELECT ended_at FROM calls WHERE id=?",
                                   (call_id,))["ended_at"], "")
        # свой завершает нормально (даже без operator_id в теле)
        st, raw = self.call("POST", "/api/v2/calls/complete",
                            {"call_id": call_id}, token=tok1)
        self.assertEqual(st, 200, raw)
        self.assertNotEqual(db.fetch1("SELECT ended_at FROM calls WHERE id=?",
                                      (call_id,))["ended_at"], "")

    def test_operator_accept_without_binding_404(self):
        acd_id, _call = _p0_make_acd("79997770103")
        tok = self._login("p0nobind", "op123456")
        st, raw = self.call("POST", "/api/v2/acd/accept", {"id": acd_id}, token=tok)
        self.assertEqual(st, 404)
        self.assertIn("operator_not_found", raw)

    def test_operator_cannot_change_consent(self):
        cid = db.insert("contacts", {"name": "P0", "phone": "79997770104", "grp": "",
                                     "note": "", "consent": 0, "blacklisted": 0,
                                     "tags": "", "database_id": 0, "created": config.now_iso(),
                                     "updated": config.now_iso(), "consent_source": "manual",
                                     "complaints": 0})
        tok = self._login("p0op1", "op123456")
        st, raw = self.call("POST", "/api/v2/contacts/save",
                            {"id": cid, "phone": "79997770104", "name": "P0-hack",
                             "consent": True, "blacklisted": True}, token=tok)
        self.assertEqual(st, 200, raw)
        c = db.fetch1("SELECT * FROM contacts WHERE id=?", (cid,))
        self.assertEqual(c["consent"], 0)
        self.assertEqual(c["blacklisted"], 0)
        self.assertEqual(c["name"], "P0-hack")  # остальное редактировать можно
        # новый контакт от оператора — всегда без согласия
        st, raw = self.call("POST", "/api/v2/contacts/save",
                            {"phone": "79997770105", "name": "P0-new", "consent": True},
                            token=tok)
        self.assertEqual(st, 200, raw)
        c2 = db.fetch1("SELECT * FROM contacts WHERE phone=?", ("79997770105",))
        self.assertEqual(c2["consent"], 0)
        # админ — может
        tok_a = self._admin()
        st, raw = self.call("POST", "/api/v2/contacts/save",
                            {"id": cid, "phone": "79997770104", "consent": True},
                            token=tok_a)
        self.assertEqual(st, 200, raw)
        self.assertEqual(db.fetch1("SELECT consent FROM contacts WHERE id=?",
                                   (cid,))["consent"], 1)

    def _make_campaign(self, status="stopped"):
        return db.insert("campaigns", {
            "name": "P0", "template_id": 1, "flow": "message", "status": status,
            "schedule": "{}", "max_channels": 1, "retry_max": 2, "retry_delay_min": 1,
            "connect_on_qualify": 0,
            "created": config.now_iso(), "updated": config.now_iso()})

    def _add_item(self, camp, phone, status="queued", attempts=0):
        return db.insert("campaign_items", {
            "campaign_id": camp, "contact_id": 0, "contact_name": "P",
            "contact_phone": phone, "status": status, "attempts": attempts,
            "next_attempt_at": "", "last_result": "",
            "created": config.now_iso(), "updated": config.now_iso(),
            "completed_at": ""})

    def test_clear_running_refused(self):
        tok = self._admin()
        camp = self._make_campaign(status="running")
        self._add_item(camp, "79997770106")
        st, raw = self.call("POST", "/api/v2/campaigns/%d/clear" % camp, {}, token=tok)
        self.assertEqual(st, 400)
        self.assertIn("campaign_running", raw)
        self.assertEqual(db.fetch1("SELECT COUNT(*) c FROM campaign_items WHERE campaign_id=?",
                                   (camp,))["c"], 1)
        st, _ = self.call("POST", "/api/v2/campaigns/%d/stop" % camp, {}, token=tok)
        self.assertEqual(st, 200)
        st, raw = self.call("POST", "/api/v2/campaigns/%d/clear" % camp, {}, token=tok)
        self.assertEqual(st, 200, raw)
        self.assertEqual(db.fetch1("SELECT COUNT(*) c FROM campaign_items WHERE campaign_id=?",
                                   (camp,))["c"], 0)

    def test_start_keeps_exhausted_and_retry_exhausted(self):
        tok = self._admin()
        camp = self._make_campaign(status="stopped")
        self._add_item(camp, "79997770107", status="exhausted", attempts=3)
        self._add_item(camp, "79997770108", status="canceled", attempts=1)
        st, _ = self.call("POST", "/api/v2/campaigns/%d/start" % camp, {}, token=tok)
        self.assertEqual(st, 200)
        by_phone = {i["contact_phone"]: i for i in db.fetch(
            "SELECT * FROM campaign_items WHERE campaign_id=?", (camp,))}
        self.assertEqual(by_phone["79997770107"]["status"], "exhausted")  # не тронут
        self.assertEqual(by_phone["79997770108"]["status"], "queued")     # canceled поднят
        st, raw = self.call("POST", "/api/v2/campaigns/%d/retry-exhausted" % camp, {},
                            token=tok)
        self.assertEqual(st, 200, raw)
        self.assertEqual(json.loads(raw)["requeued"], 1)
        it = db.fetch1("SELECT * FROM campaign_items WHERE campaign_id=? AND contact_phone=?",
                       (camp, "79997770107"))
        self.assertEqual(it["status"], "queued")
        self.assertEqual(it["attempts"], 0)

    def test_import_bad_file_creates_no_database(self):
        tok = self._admin()
        before = db.fetch1("SELECT COUNT(*) c FROM databases")["c"]
        bad = base64.b64encode(b"this is not a zip file at all" * 10).decode()
        st, raw = self.call("POST", "/api/v2/contacts/import-file",
                            {"filename": "bad.xlsx", "content_b64": bad,
                             "database_name": "P0-призрак"}, token=tok)
        self.assertEqual(st, 400)
        self.assertIn("parse_error", raw)
        self.assertEqual(db.fetch1("SELECT COUNT(*) c FROM databases")["c"], before)
        # а валидный CSV — создаёт базу и контакты как раньше
        good = base64.b64encode("name,phone\nP0-Иван,79997770109\n".encode("utf-8")).decode()
        st, raw = self.call("POST", "/api/v2/contacts/import-file",
                            {"filename": "good.csv", "content_b64": good,
                             "database_name": "P0-База"}, token=tok)
        self.assertEqual(st, 200, raw)
        self.assertTrue(json.loads(raw)["ok"])
        self.assertIsNotNone(db.fetch1("SELECT id FROM contacts WHERE phone=?",
                                       ("79997770109",)))

    def test_webhook_enqueued_not_applied(self):
        snap = _save_settings()
        try:
            s = db.get_settings()
            s["uis"] = {"api_url": "http://x", "api_key": "k", "webhook_secret": "p0secret"}
            db.save_settings(s)
            call_id = db.insert("calls", _p0_call_row("dialing", "79997770111"))
            st, raw = self.call("POST", "/api/v2/webhooks/uis",
                                {"call_id": call_id, "status": "ringing"},
                                headers={"X-UIS-Secret": "p0secret"})
            self.assertEqual(st, 200, raw)
            # событие ещё НЕ применено — только в очереди
            self.assertEqual(db.fetch1("SELECT status FROM calls WHERE id=?",
                                       (call_id,))["status"], "dialing")
            self.engine.tick_once()
            self.assertEqual(db.fetch1("SELECT status FROM calls WHERE id=?",
                                       (call_id,))["status"], "ringing")
        finally:
            _restore_settings(snap)

    def test_number_validation_http(self):
        tok = self._admin()
        st, raw = self.call("POST", "/api/v2/numbers/save",
                            {"number": "12", "provider": "sim", "daily_limit": 100},
                            token=tok)
        self.assertEqual(st, 400)
        self.assertIn("bad_number", raw)
        st, raw = self.call("POST", "/api/v2/numbers/save",
                            {"number": "79997770110", "provider": "mts", "daily_limit": 100},
                            token=tok)
        self.assertEqual(st, 400)
        self.assertIn("bad_provider", raw)
        st, raw = self.call("POST", "/api/v2/numbers/save",
                            {"number": "79997770110", "provider": "sim", "daily_limit": 0},
                            token=tok)
        self.assertEqual(st, 400)
        self.assertIn("bad_limit", raw)
        st, raw = self.call("POST", "/api/v2/numbers/save",
                            {"number": "79997770110", "label": "P0", "kind": "mobile",
                             "provider": "sim", "daily_limit": 100}, token=tok)
        self.assertEqual(st, 200, raw)
        db.q("DELETE FROM numbers WHERE number=?", ("79997770110",))


class TestP0EngineUnit(unittest.TestCase):
    """P0-фиксы уровня движка и модулей (без HTTP)."""

    @classmethod
    def setUpClass(cls):
        db.init_db()
        cls.engine = Engine(auto_start=False)

    def test_connect_false_rolls_back(self):
        op_id = db.insert("operators", {"user_id": 0, "name": "P0-false",
                                        "ext": "103", "status": "free",
                                        "updated": config.now_iso()})
        acd_id, _call = _p0_make_acd("79997770112")

        class FalseProvider:
            name = "p0false"

            def connect_operator(self, call_id, ext):
                return False

        real = self.engine.provider
        self.engine.provider = FalseProvider()
        try:
            ok, err = self.engine.accept_acd(acd_id, op_id)
            self.assertFalse(ok)
            self.assertEqual(err, "transfer_failed")
            acd = db.fetch1("SELECT * FROM acd WHERE id=?", (acd_id,))
            self.assertEqual(acd["status"], "queued")
            self.assertEqual(acd["operator_id"], 0)
            self.assertEqual(db.fetch1("SELECT status FROM operators WHERE id=?",
                                       (op_id,))["status"], "free")
        finally:
            self.engine.provider = real

    def test_explicit_busy_operator_refused(self):
        op_busy = db.insert("operators", {"user_id": 0, "name": "P0-busy",
                                          "ext": "104", "status": "busy",
                                          "updated": config.now_iso()})
        acd_id, _call = _p0_make_acd("79997770113")
        ok, err = self.engine.accept_acd(acd_id, op_busy)
        self.assertFalse(ok)
        self.assertEqual(err, "operator_not_free")
        op_free = db.insert("operators", {"user_id": 0, "name": "P0-free2",
                                          "ext": "105", "status": "free",
                                          "updated": config.now_iso()})
        ok, err = self.engine.accept_acd(acd_id, op_free)
        self.assertTrue(ok, err)
        call_id = db.fetch1("SELECT call_id FROM acd WHERE id=?", (acd_id,))["call_id"]
        ok, err = self.engine.complete_operator_call(call_id, op_free)
        self.assertTrue(ok, err)

    def test_ago_accepts_float(self):
        a_half = self.engine._ago(0.5)
        a_one = self.engine._ago(1)
        a_zero = self.engine._ago(0)
        self.assertEqual(len(a_half), 19)  # формат ISO цел
        self.assertGreater(a_half, a_one)  # полминуты назад позже минуты
        self.assertNotEqual(a_half, a_zero)  # int() больше не обнуляет

    def test_finish_attempt_publishes_fresh_status(self):
        from app import events
        captured = []
        orig = events.publish
        events.publish = lambda topic, payload: captured.append((topic, payload))
        try:
            call_id = db.insert("calls", _p0_call_row("dialing", "79997770114"))
            item_id = db.insert("campaign_items", {
                "campaign_id": 0, "contact_id": 0, "contact_name": "P",
                "contact_phone": "79997770114", "status": "dialing", "attempts": 99,
                "next_attempt_at": "", "last_result": "",
                "created": config.now_iso(), "updated": config.now_iso(),
                "completed_at": ""})
            call = db.fetch1("SELECT * FROM calls WHERE id=?", (call_id,))
            item = db.fetch1("SELECT * FROM campaign_items WHERE id=?", (item_id,))
            self.engine._finish_attempt(call, item, "busy", "занято", retryable=True)
        finally:
            events.publish = orig
        items = [p for t, p in captured if t == "item"]
        self.assertTrue(items, "событие item не опубликовано: %r" % (captured,))
        self.assertEqual(items[-1]["id"], item_id)
        self.assertEqual(items[-1]["status"], "exhausted")  # а не протухший dialing

    def test_unknown_provider_fail_closed(self):
        from app.telephony import ProviderNotConfigured, make_provider
        with self.assertRaises(ProviderNotConfigured):
            make_provider({"provider": "uis "})
        with self.assertRaises(ProviderNotConfigured):
            make_provider({"provider": "asterisk"})
        with self.assertRaises(ProviderNotConfigured):
            make_provider({})
        with self.assertRaises(ProviderNotConfigured):
            make_provider({"provider": "   "})
        self.assertEqual(make_provider({"provider": "sim"}).name, "sim")
        self.assertEqual(make_provider({"provider": " SIM "}).name, "sim")
        # fail-closed по умолчанию: чистой установке симулятор не подставляется
        self.assertEqual(config.DEFAULT_SETTINGS.get("provider"), "")

    def test_password_reset_drops_sessions(self):
        from app import security
        if not db.fetch1("SELECT id FROM users WHERE login='p0sess'"):
            salt = security.new_salt()
            db.insert("users", {"login": "p0sess", "role": "operator", "salt": salt,
                                "password_hash": security.hash_password("oldpass1", salt),
                                "active": 1, "created": config.now_iso()})
        tok = security.create_token("p0sess", "operator")
        self.assertIsNotNone(security.get_session(tok))
        self.assertTrue(db.set_user_password("p0sess", "newpass2"))
        self.assertIsNone(security.get_session(tok))
        u = db.fetch1("SELECT * FROM users WHERE login='p0sess'")
        self.assertEqual(security.hash_password("newpass2", u["salt"]), u["password_hash"])

    def test_ami_action_registers_before_send(self):
        from app.asterisk import AMIClient
        c = AMIClient("127.0.0.1", timeout=5.0)
        seen = {}

        class FakeSock:
            def sendall(self, data):
                # в момент отправки ожидание уже должно быть зарегистрировано
                seen["pending_during_send"] = len(c._pending)

        c.sock = FakeSock()
        res = c.action("Ping", timeout=0.05, check=False)
        self.assertEqual(seen.get("pending_during_send"), 1)
        self.assertEqual(res.get("Response"), "Error")  # таймаут без сервера
        self.assertEqual(c._pending, {})  # ожидание убрано


class TestNumberPoolRotation(unittest.TestCase):
    """CallerID-пул: ротация по счётчикам, карантин/лимиты, статистика."""

    @classmethod
    def setUpClass(cls):
        db.init_db()
        cls.engine = Engine(auto_start=False)

    def _add_num(self, number, weight=99):
        ok, err = numbers_mod.add_number(number, label="rot", kind="mobile",
                                         provider="sim", daily_limit=100)
        self.assertTrue(ok, err)
        n = db.fetch1("SELECT * FROM numbers WHERE number=?", (number,))
        db.q("UPDATE numbers SET weight=? WHERE id=?", (weight, n["id"]))
        return db.fetch1("SELECT * FROM numbers WHERE id=?", (n["id"],))

    def test_acquire_rotates_by_counter(self):
        self._add_num("79998880001")
        self._add_num("79998880002")
        try:
            seq = []
            for _ in range(4):
                got = numbers_mod.acquire(provider="sim", cooldown_sec=0)
                self.assertIsNotNone(got)
                seq.append(got["number"])
                numbers_mod.mark_used(got["id"], 0)
            self.assertEqual(seq, ["79998880001", "79998880002",
                                   "79998880001", "79998880002"])
        finally:
            db.q("DELETE FROM numbers WHERE number IN (?,?)",
                 ("79998880001", "79998880002"))

    def test_quarantine_and_limit(self):
        n1 = self._add_num("79998880011")
        n2 = self._add_num("79998880012")
        try:
            numbers_mod.quarantine(n1["id"], True)
            for _ in range(2):
                got = numbers_mod.acquire(provider="sim", cooldown_sec=0)
                self.assertEqual(got["number"], "79998880012")
                numbers_mod.mark_used(got["id"], 0)
            db.q("UPDATE numbers SET daily_limit=1, daily_count=1, daily_date=? "
                 "WHERE id IN (?,?)", (numbers_mod.today(), n1["id"], n2["id"]))
            numbers_mod.quarantine(n1["id"], False)
            got = numbers_mod.acquire(provider="sim", cooldown_sec=0)
            if got is not None:
                self.assertNotIn(got["number"], ("79998880011", "79998880012"))
        finally:
            db.q("DELETE FROM numbers WHERE number IN (?,?)",
                 ("79998880011", "79998880012"))

    def test_stats_counters(self):
        n = self._add_num("79998880021")
        try:
            numbers_mod.mark_used(n["id"], 0)
            numbers_mod.mark_used(n["id"], 0)
            numbers_mod.mark_answered(n["id"])
            r = db.fetch1("SELECT dialed_total, answered_total FROM numbers WHERE id=?",
                          (n["id"],))
            self.assertEqual(r["dialed_total"], 2)
            self.assertEqual(r["answered_total"], 1)
            st = {x["number"]: x for x in numbers_mod.pool_state()}
            self.assertEqual(st["79998880021"]["dialed_total"], 2)
            self.assertEqual(st["79998880021"]["answered_total"], 1)
        finally:
            db.q("DELETE FROM numbers WHERE number=?", ("79998880021",))

    def test_engine_marks_answered(self):
        n = self._add_num("79998880031")
        try:
            row = _p0_call_row("ringing", "79998880032")
            row["number_id"] = n["id"]
            call_id = db.insert("calls", row)
            call = db.fetch1("SELECT * FROM calls WHERE id=?", (call_id,))
            self.engine._on_answered(call, None, True)
            r = db.fetch1("SELECT answered_total FROM numbers WHERE id=?", (n["id"],))
            self.assertEqual(r["answered_total"], 1)
        finally:
            db.q("DELETE FROM numbers WHERE number=?", ("79998880031",))
            db.q("DELETE FROM calls WHERE contact_phone=?", ("79998880032",))
