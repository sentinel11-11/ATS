# -*- coding: utf-8 -*-
"""Тесты ATS v2 (ядро, пул номеров, движок E2E, HTTP API).

Запуск: python -m unittest discover -s tests -v   (из корня репозитория)
Внимание: переменные окружения ATS_DATA_DIR / ATS_ADMIN_PASSWORD задаются ДО импорта app.*
(пути в config.py вычисляются при импорте).
"""
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

_TMP = tempfile.mkdtemp(prefix="ats_test_")
os.environ["ATS_FAST"] = "1"
os.environ["ATS_DATA_DIR"] = _TMP
os.environ["ATS_ADMIN_PASSWORD"] = "TestAdmin123!"

from app import api, config, db, numbers as numbers_mod, security  # noqa: E402
from app.engine import Engine  # noqa: E402


def iso_add_minutes(**kw):
    import datetime
    return (datetime.datetime.now() + datetime.timedelta(**kw)).strftime("%Y-%m-%d %H:%M:%S")


def add_contact(name, phone, consent=1, grp="тест"):
    return db.insert("contacts", {"name": name, "phone": phone, "grp": grp, "note": "",
                                  "consent": consent, "consent_source": "test", "blacklisted": 0,
                                  "complaints": 0, "created": db_now(), "updated": db_now()})


def db_now():
    return db.config.now_iso() if hasattr(db, "config") else config.now_iso()


def make_campaign(name="Тест", flow="agent", template_id=2, retry_max=0, connect_on_qualify=1):
    return db.insert("campaigns", {
        "name": name, "template_id": template_id, "flow": flow, "status": "stopped",
        "schedule": json.dumps({"start": "00:00", "end": "23:59",
                                "days": [0, 1, 2, 3, 4, 5, 6]}),
        "max_channels": 3, "retry_max": retry_max, "retry_delay_min": 1,
        "connect_on_qualify": connect_on_qualify,
        "created": config.now_iso(), "updated": config.now_iso()})


class TestSecurity(unittest.TestCase):
    def test_password_hash_roundtrip(self):
        salt = security.new_salt()
        h1 = security.hash_password("secret", salt)
        h2 = security.hash_password("secret", salt)
        h3 = security.hash_password("other", salt)
        self.assertEqual(h1, h2)
        self.assertNotEqual(h1, h3)

    def test_token_lifecycle(self):
        t = security.create_token("admin", "admin")
        self.assertIsNotNone(security.get_session(t))
        security.drop_token(t)
        self.assertIsNone(security.get_session(t))


class TestDb(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.init_db()

    def test_seeds(self):
        self.assertGreaterEqual(len(db.fetch("SELECT id FROM users")), 2)
        self.assertGreaterEqual(db.fetch("SELECT COUNT(*) c FROM numbers")[0]["c"], 1)
        self.assertGreaterEqual(db.fetch("SELECT COUNT(*) c FROM templates")[0]["c"], 1)
        self.assertIsNotNone(db.fetch1("SELECT data FROM settings WHERE id=1"))

    def test_contacts_unique_phone(self):
        cid = add_contact("Дубль", "79990000001")
        with self.assertRaises(Exception):
            add_contact("Дубль2", "79990000001")


class TestNumberPool(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.init_db()

    def _only_sim_number(self, keep_id):
        """Оставляем активным один sim-номер (для детерминизма), остальные временно выключаем."""
        db.q("UPDATE numbers SET active=0 WHERE provider='sim' AND id<>?", (keep_id,))

    def _restore(self):
        db.q("UPDATE numbers SET active=1 WHERE provider='sim'")

    def test_daily_limit(self):
        nid = db.insert("numbers", {"number": "79995550001", "label": "лимит-1", "kind": "mobile",
                                    "provider": "sim", "active": 1, "daily_limit": 1, "weight": 1,
                                    "quarantined": 0, "cooldown_until": "",
                                    "daily_date": __import__("datetime").date.today().isoformat(),
                                    "daily_count": 0, "created": config.now_iso()})
        try:
            self._only_sim_number(nid)
            first = numbers_mod.acquire(provider="sim", cooldown_sec=0)
            self.assertIsNotNone(first)
            numbers_mod.mark_used(first["id"], 0)
            second = numbers_mod.acquire(provider="sim", cooldown_sec=0)
            self.assertIsNone(second)  # суточный лимит исчерпан, других активных нет
            n = db.fetch1("SELECT * FROM numbers WHERE id=?", (nid,))
            self.assertEqual(n["daily_count"], 1)
        finally:
            self._restore()
            db.q("DELETE FROM numbers WHERE id=?", (nid,))

    def test_quarantine_excluded(self):
        nid = db.insert("numbers", {"number": "79995550002", "label": "карантин", "kind": "mobile",
                                    "provider": "sim", "active": 1, "daily_limit": 100, "weight": 1,
                                    "quarantined": 0, "cooldown_until": "",
                                    "daily_date": __import__("datetime").date.today().isoformat(),
                                    "daily_count": 0, "created": config.now_iso()})
        try:
            self._only_sim_number(nid)
            self.assertIsNotNone(numbers_mod.acquire(provider="sim", cooldown_sec=0))
            numbers_mod.quarantine(nid, True)
            self.assertIsNone(numbers_mod.acquire(provider="sim", cooldown_sec=0))
            numbers_mod.quarantine(nid, False)
            self.assertIsNotNone(numbers_mod.acquire(provider="sim", cooldown_sec=0))
        finally:
            self._restore()
            db.q("DELETE FROM numbers WHERE id=?", (nid,))


class TestEngineE2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.init_db()
        cls.engine = Engine(auto_start=False)

    def test_agent_qualified_transfer_and_operator(self):
        c1 = add_contact("Квал", "79031000001")
        c2 = add_contact("Отказ", "79031000002")
        cid = make_campaign(retry_max=0)
        db.insert("campaign_items", {"campaign_id": cid, "contact_id": c1, "contact_name": "Квал",
                                     "contact_phone": "79031000001", "status": "queued", "attempts": 0,
                                     "next_attempt_at": "", "last_result": "",
                                     "created": config.now_iso(), "updated": config.now_iso(), "completed_at": ""})
        db.insert("campaign_items", {"campaign_id": cid, "contact_id": c2, "contact_name": "Отказ",
                                     "contact_phone": "79031000002", "status": "queued", "attempts": 0,
                                     "next_attempt_at": "", "last_result": "",
                                     "created": config.now_iso(), "updated": config.now_iso(), "completed_at": ""})
        p = self.engine.provider
        p.set_outcome("79031000001", "answered_human")
        p.set_answer("79031000001", "1")     # q1->q2, q2 default '1' => qualified
        p.set_outcome("79031000002", "answered_human")
        p.set_answer("79031000002", "2")     # q1 => end, not qualified
        db.q("UPDATE campaigns SET status='running' WHERE id=?", (cid,))

        accepted = {}

        def accept_loop():
            deadline = time.time() + 15
            while time.time() < deadline:
                self.engine.tick_once()
                q = db.fetch("SELECT * FROM acd WHERE status='queued'")
                if q and not accepted.get("done"):
                    ok, _ = self.engine.accept_acd(q[0]["id"])
                    if ok:
                        accepted["done"] = True
                        op = db.fetch1("SELECT * FROM operators WHERE status='busy'")
                        time.sleep(0.1)
                        self.engine.complete_operator_call(q[0]["call_id"], op["id"])
                if self.engine.active_channels() == 0 and not db.fetch(
                        "SELECT * FROM acd WHERE status IN ('queued','accepted')") \
                        and db.fetch("SELECT COUNT(*) c FROM campaign_items WHERE status IN "
                                     "('queued','dialing','agent','wait_operator','talk')")[0]["c"] == 0:
                    return
                time.sleep(0.03)

        accept_loop()
        self.assertTrue(accepted.get("done"), "оператор должен принять квалифицированного")
        by_phone = {i["contact_phone"]: i for i in db.fetch("SELECT * FROM campaign_items WHERE campaign_id=?", (cid,))}
        self.assertEqual(by_phone["79031000001"]["status"], "operator_ok")
        self.assertEqual(by_phone["79031000002"]["status"], "done_agent")
        calls = db.fetch("SELECT * FROM calls WHERE campaign_id=?", (cid,))
        res = {c["contact_phone"]: c["result"] for c in calls}
        self.assertEqual(res["79031000001"], "operator_ok")
        self.assertEqual(res["79031000002"], "qualified_no")
        # CRM-файл создан
        files = list(config.CRM_OUT_DIR.glob("crm_push_*.csv"))
        self.assertTrue(files)

    def test_busy_retry_then_exhausted(self):
        cid = add_contact("Занято", "79032000001")
        camp = make_campaign(flow="message", template_id=1, retry_max=1)
        db.insert("campaign_items", {"campaign_id": camp, "contact_id": cid, "contact_name": "Занято",
                                     "contact_phone": "79032000001", "status": "queued", "attempts": 0,
                                     "next_attempt_at": "", "last_result": "",
                                     "created": config.now_iso(), "updated": config.now_iso(), "completed_at": ""})
        self.engine.provider.set_outcome("79032000001", "busy")
        db.q("UPDATE campaigns SET status='running' WHERE id=?", (camp,))
        deadline = time.time() + 12
        while time.time() < deadline:
            self.engine.tick_once()
            if self.engine.active_channels() == 0:
                item = db.fetch1("SELECT * FROM campaign_items WHERE campaign_id=?", (camp,))
                if item and item["status"] == "exhausted":
                    break
            time.sleep(0.03)
        item = db.fetch1("SELECT * FROM campaign_items WHERE campaign_id=?", (camp,))
        self.assertEqual(item["status"], "exhausted")
        self.assertGreaterEqual(item["attempts"], 1)

    @classmethod
    def tearDownClass(cls):
        cls.engine.stop()


class TestHttpApi(unittest.TestCase):
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

    @classmethod
    def tearDownClass(cls):
        cls.engine.stop()
        cls.httpd.shutdown()

    def call(self, method, path, body=None, token=""):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method)
        if body is not None:
            req.add_header("Content-Type", "application/json")
        if token:
            req.add_header("X-Ats-Token", token)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read().decode())
            except Exception:
                return e.code, {}

    def _token(self, login="admin", password="TestAdmin123!"):
        s, j = self.call("POST", "/api/v2/auth/login", {"login": login, "password": password})
        self.assertEqual(s, 200)
        return j["token"]

    def test_auth_required(self):
        s, j = self.call("GET", "/api/v2/dashboard")
        self.assertEqual(s, 401)

    def test_admin_flow(self):
        tok = self._token()
        self.assertEqual(self.call("GET", "/api/v2/dashboard", token=tok)[0], 200)
        s, j = self.call("POST", "/api/v2/contacts/import",
                         {"rows": [{"name": "ХТТП", "phone": "+7 (903) 555-11-22", "consent": "1"}]}, token=tok)
        self.assertEqual(s, 200)
        self.assertEqual(j["added"], 1)
        s, j = self.call("POST", "/api/v2/campaigns/save",
                         {"name": "Кампания API", "template_id": 1, "flow": "message",
                          "schedule": {"start": "00:00", "end": "23:59", "days": [0, 1, 2, 3, 4, 5, 6]}}, token=tok)
        self.assertEqual(s, 200)
        cid = j["id"]
        contacts = [c["id"] for c in self.call("GET", "/api/v2/contacts", token=tok)[1]["contacts"]]
        s, j = self.call("POST", "/api/v2/campaigns/{}/add-contacts".format(cid),
                         {"contact_ids": contacts}, token=tok)
        self.assertGreaterEqual(j["added"], 1)
        s, _ = self.call("POST", "/api/v2/campaigns/{}/start".format(cid), {}, token=tok)
        self.assertEqual(s, 200)

    def test_operator_role_limited(self):
        otok = self._token(login="operator", password="operator1234")
        self.assertEqual(self.call("GET", "/api/v2/settings", token=otok)[0], 200)
        s, j = self.call("POST", "/api/v2/settings/save", {"max_channels": 9}, token=otok)
        self.assertEqual(s, 403)

    def test_numbers_and_blacklist(self):
        tok = self._token()
        s, j = self.call("POST", "/api/v2/numbers/save",
                         {"number": "79033334455", "label": "API-номер", "daily_limit": 200}, token=tok)
        self.assertEqual(s, 200)
        nums = self.call("GET", "/api/v2/numbers", token=tok)[1]["numbers"]
        self.assertTrue(any(n["number"] == "79033334455" for n in nums))
        s, _ = self.call("POST", "/api/v2/blacklist/add", {"phone": "79033334455", "reason": "тест"}, token=tok)
        self.assertEqual(s, 200)
        bl = self.call("GET", "/api/v2/blacklist", token=tok)[1]["blacklist"]
        self.assertTrue(any(b["phone"] == "79033334455" for b in bl))



class TestLoginThrottle(unittest.TestCase):
    """Защита входа от перебора: после MAX_FAILS неудач -> 429, потом снова можно."""
    def test_lock_after_failed_attempts(self):
        from app import api as ap
        login = "hacker-throttle"
        ap._login_ok(login)  # сброс
        for _ in range(ap.MAX_FAILS):
            _body, code = ap.route("POST", "/api/v2/auth/login",
                                   {"login": login, "password": "wrong"}, {})
            self.assertEqual(code, 403)
        body, code = ap.route("POST", "/api/v2/auth/login",
                              {"login": login, "password": "wrong"}, {})
        self.assertEqual(code, 429)
        self.assertIn("retry_after_sec", body)
        ap._login_ok(login)  # очистка, чтобы не влиять на другие тесты


class TestReportsEndpoint(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from app.server import create_server
        db.init_db()
        cls.engine = Engine(auto_start=False)
        api.ENGINE = cls.engine
        cls.httpd = create_server("127.0.0.1", 0)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        time.sleep(0.2)
        cls.base = "http://127.0.0.1:{}".format(cls.port)

    @classmethod
    def tearDownClass(cls):
        cls.engine.stop()
        cls.httpd.shutdown()

    def call(self, method, path, body=None, token=""):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method)
        if body is not None:
            req.add_header("Content-Type", "application/json")
        if token:
            req.add_header("X-Ats-Token", token)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read().decode())
            except Exception:
                return e.code, {}

    def test_reports_shape(self):
        s, j = self.call("POST", "/api/v2/auth/login", {"login": "admin", "password": "TestAdmin123!"})
        tok = j["token"]
        code, rep = self.call("GET", "/api/v2/reports", token=tok)
        self.assertEqual(code, 200)
        self.assertIn("today", rep)
        self.assertIn("by_campaign", rep)
        self.assertIn("by_number", rep)
        self.assertIn("trend", rep)
        self.assertIn("by_result", rep)
        self.assertEqual(len(rep["trend"]), 7)

    def test_export_calls_admin_only(self):
        s, j = self.call("POST", "/api/v2/auth/login", {"login": "admin", "password": "TestAdmin123!"})
        tok = j["token"]
        req = urllib.request.Request(self.base + "/api/v2/export/calls.csv", headers={"X-Ats-Token": tok})
        with urllib.request.urlopen(req, timeout=10) as r:
            self.assertEqual(r.status, 200)
            head = r.read(60).decode("utf-8-sig", "ignore")
            self.assertIn("id", head)
        # оператор не может
        s, j = self.call("POST", "/api/v2/auth/login", {"login": "operator", "password": "operator1234"})
        otok = j["token"]
        code, _ = self.call("GET", "/api/v2/export/calls.csv", token=otok)
        self.assertEqual(code, 403)



class TestMiscEndpoints(unittest.TestCase):
    """Демо-сид, умные интервалы (retry_map), загрузка/отдача записи разговора."""
    @classmethod
    def setUpClass(cls):
        from app.server import create_server
        db.init_db()
        cls.engine = Engine(auto_start=False)
        api.ENGINE = cls.engine
        cls.httpd = create_server("127.0.0.1", 0)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        time.sleep(0.2)
        cls.base = "http://127.0.0.1:{}".format(cls.port)

    @classmethod
    def tearDownClass(cls):
        cls.engine.stop()
        cls.httpd.shutdown()

    def call(self, method, path, body=None, token=""):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method)
        if body is not None:
            req.add_header("Content-Type", "application/json")
        if token:
            req.add_header("X-Ats-Token", token)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read().decode())
            except Exception:
                return e.code, {}

    def _token(self):
        s, j = self.call("POST", "/api/v2/auth/login", {"login": "admin", "password": "TestAdmin123!"})
        return j["token"]

    def test_demo_seed(self):
        tok = self._token()
        code, j = self.call("POST", "/api/v2/demo/seed", {}, token=tok)
        self.assertEqual(code, 200)
        self.assertGreaterEqual(j["contacts_added"], 0)
        self.assertTrue(j["campaign_id"])
        self.assertGreaterEqual(j["items"], 0)
        # повторный вызов не должен падать (дубли пропускаются)
        code2, j2 = self.call("POST", "/api/v2/demo/seed", {}, token=tok)
        self.assertEqual(code2, 200)

    def test_campaign_retry_map(self):
        tok = self._token()
        code, j = self.call("POST", "/api/v2/campaigns/save",
                            {"name": "С картой", "template_id": 1, "flow": "message",
                             "retry_map": {"busy": 7, "no_answer": 25},
                             "schedule": {"start": "00:00", "end": "23:59", "days": [0, 1, 2, 3, 4, 5, 6]}},
                            token=tok)
        self.assertEqual(code, 200)
        code, detail = self.call("GET", "/api/v2/campaigns/{}".format(j["id"]), token=tok)
        self.assertEqual(detail["campaign"]["retry_map"], {"busy": 7, "no_answer": 25})
        # движок: интервал повтора busy из карты
        item = {"campaign_id": j["id"], "attempts": 1}
        camp = db.fetch1("SELECT * FROM campaigns WHERE id=?", (j["id"],))
        from app.engine import Engine as _E
        delay = _E._retry_delay(None, {"campaign_id": j["id"], "attempts": 1}, camp, "busy")
        self.assertEqual(delay, 7)

    def test_recording_upload_and_get(self):
        import base64
        tok = self._token()
        call_id = db.insert("calls", {"campaign_id": 0, "item_id": 0, "contact_id": 0,
                                      "contact_name": "Р", "contact_phone": "79001110000",
                                      "caller_id": "79000000001", "number_id": 0, "provider": "sim",
                                      "direction": "out", "status": "done", "result": "done_ok",
                                      "detail": "", "agent_result": "", "recording": "",
                                      "started_at": config.now_iso(), "answered_at": "",
                                      "ended_at": config.now_iso(), "duration_sec": 3})
        code, j = self.call("POST", "/api/v2/calls/recording",
                            {"call_id": call_id, "filename": "rec.mp3",
                             "data_b64": base64.b64encode(b"\x00fakeaudio\x00").decode()},
                            token=tok)
        self.assertEqual(code, 200)
        # GET файла (auth через заголовок)
        req = urllib.request.Request(self.base + "/api/v2/calls/{}/recording".format(call_id),
                                     headers={"X-Ats-Token": tok})
        with urllib.request.urlopen(req, timeout=10) as r:
            self.assertEqual(r.status, 200)
            self.assertIn("audio/", r.headers["Content-Type"])
        row = db.fetch1("SELECT recording FROM calls WHERE id=?", (call_id,))
        self.assertTrue(row["recording"].startswith("call_{}_".format(call_id)))

if __name__ == "__main__":
    unittest.main()
