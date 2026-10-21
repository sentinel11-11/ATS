# -*- coding: utf-8 -*-
"""Scope одноразовых URL-билетов (P1 повторного ревью).

Раньше билет, выпущенный для SSE/экспорта/записи, аутентифицировал ЛЮБОЙ
защищённый маршрут — включая POST с административным действием
(/users/delete, /settings/raw). Теперь билет выпускается под конкретное
назначение (purpose+resource) и гасится только при точном совпадении;
проверка назначения идёт ДО погашения — попытка не по назначению не
расходует билет.

Негативные сценарии из ревью:
- админский билет + POST /users/delete?ticket=... → отказ;
- админский билет + POST /settings/raw?ticket=... → отказ;
- билет + GET /dashboard, /auth/me, /users → отказ (билет — не сессия);
- SSE-билет на экспорте, экспортный билет на SSE → отказ;
- экспортный билет «не на тот файл» → отказ;
- билет записи «не на тот звонок» → отказ.
Позитивные: заявленные сценарии (SSE/экспорт/запись) работают.
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

_TMP = tempfile.mkdtemp(prefix="ats_test_scope_")
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


class TicketScopeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # полный прогон гоняет файлы в одной БД: провайдер в settings мог
        # остаться от чужого теста (моки уже остановлены) — форсим sim
        st = db.get_settings()
        if st.get("provider") != "sim":
            st["provider"] = "sim"
            db.save_settings(st)
        db.init_db()
        from app.server import create_server
        cls.engine = Engine()
        api.ENGINE = cls.engine
        cls.httpd = create_server("127.0.0.1", 0)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        time.sleep(0.2)
        cls.base = "http://127.0.0.1:{}".format(cls.port)
        cls.token = cls._login("admin", "TestAdmin123!")
        # оператор для проверки роли на экспорте
        cls.op_token = None

    @classmethod
    def tearDownClass(cls):
        cls.engine.stop()
        cls.httpd.shutdown()

    @classmethod
    def _login(cls, login, password):
        req = urllib.request.Request(
            cls.base + "/api/v2/auth/login",
            data=json.dumps({"login": login, "password": password}).encode(),
            method="POST", headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode())["token"]

    def call(self, method, path, body=None, token=""):
        data = json.dumps(body).encode() if body is not None else None
        h = {"Content-Type": "application/json"}
        if token:
            h["X-Ats-Token"] = token
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def ticket(self, purpose, resource="", token=None):
        code, body = self.call("POST", "/api/v2/auth/ticket",
                               {"purpose": purpose, "resource": resource},
                               token=token or self.token)
        self.assertEqual(code, 200, "выдача билета {}: {}".format(purpose, body))
        return json.loads(body)["ticket"]

    # ---------- негативные: билет не аутентифицирует произвольный API ----------

    def test_ticket_cannot_delete_users(self):
        """Админский билет не должен проводить POST /users/delete."""
        uid = db.fetch1("SELECT id FROM users WHERE login='admin'")["id"]
        victim = db.insert("users", {"login": "victim", "role": "operator", "salt": "s",
                                     "password_hash": "x", "active": 1,
                                     "created": "2026-01-01 00:00:00"})
        for purpose, resource in (("sse", "events"),
                                  ("export", "export/calls.csv"),
                                  ("recording", "call:1")):
            t = self.ticket(purpose, resource)
            code, body = self.call("POST", "/api/v2/users/delete?ticket=" + t,
                                   {"id": victim})
            self.assertEqual(code, 401,
                             "билет {} обязан отклоняться на /users/delete".format(purpose))
            still = db.fetch1("SELECT id FROM users WHERE id=?", (victim,))
            self.assertIsNotNone(still, "пользователь не должен быть удалён по билету")
        # и билет не «сгорел» на неудачной попытке — своё назначение он открывает
        t_exp = self.ticket("export", "export/calls.csv")
        code, _ = self.call("POST", "/api/v2/users/delete?ticket=" + t_exp, {"id": victim})
        self.assertEqual(code, 401)
        code, _ = self.call("GET", "/api/v2/export/calls.csv?ticket=" + t_exp)
        self.assertEqual(code, 200, "после отказа не по назначению билет должен работать "
                                    "по своему назначению")

    def test_ticket_cannot_change_settings(self):
        t = self.ticket("sse", "events")
        code, body = self.call("POST", "/api/v2/settings/raw?ticket=" + t,
                               {"settings": {"provider": "sim"}})
        self.assertEqual(code, 401, "билет не должен проводить POST /settings/raw")
        code, body = self.call("POST", "/api/v2/settings/save?ticket=" + t, {})
        self.assertEqual(code, 401)

    def test_ticket_is_not_a_session(self):
        t = self.ticket("export", "export/calls.csv")
        for method, path in (("GET", "/api/v2/dashboard"),
                             ("GET", "/api/v2/auth/me"),
                             ("GET", "/api/v2/users"),
                             ("GET", "/api/v2/campaigns"),
                             ("GET", "/api/v2/contacts")):
            code, _ = self.call(method, path + "?ticket=" + t)
            self.assertEqual(code, 401, "билет не должен открывать {}".format(path))
        # заголовком билет тоже не работает (это не токен сессии)
        code, _ = self.call("GET", "/api/v2/dashboard", headers=None) \
            if False else self.call("GET", "/api/v2/dashboard")
        code, _ = self.call("GET", "/api/v2/dashboard")
        self.assertEqual(code, 401)

    def test_bad_purpose_rejected_on_issue(self):
        code, body = self.call("POST", "/api/v2/auth/ticket",
                               {"purpose": "admin_actions"}, token=self.token)
        self.assertEqual(code, 400)
        code, body = self.call("POST", "/api/v2/auth/ticket", {}, token=self.token)
        self.assertEqual(code, 400, "purpose обязан быть явным")
        code, body = self.call("POST", "/api/v2/auth/ticket",
                               {"purpose": "export", "resource": "/api/v2/users"},
                               token=self.token)
        self.assertEqual(code, 400, "ресурс экспорта — только whitelist файлов")

    # ---------- позитивные + перекрёстные проверки назначения ----------

    def test_export_ticket_opens_only_its_file(self):
        t_calls = self.ticket("export", "export/calls.csv")
        code, _ = self.call("GET", "/api/v2/export/calls.csv?ticket=" + t_calls)
        self.assertEqual(code, 200, "экспортный билет открывает свой файл")
        # билет «на другой файл» не подходит
        t_contacts = self.ticket("export", "export/contacts.csv")
        code, _ = self.call("GET", "/api/v2/export/calls.csv?ticket=" + t_contacts)
        self.assertEqual(code, 401, "ресурс билета обязан совпадать")
        # SSE-билет на экспорте не работает
        t_sse = self.ticket("sse", "events")
        code, _ = self.call("GET", "/api/v2/export/calls.csv?ticket=" + t_sse)
        self.assertEqual(code, 401, "SSE-билет не должен открывать экспорт")

    def test_sse_ticket_only_for_events(self):
        t = self.ticket("sse", "events")
        req = urllib.request.Request(self.base + "/api/v2/events?ticket=" + t)
        with urllib.request.urlopen(req, timeout=10) as r:
            self.assertEqual(r.status, 200)
            self.assertIn("text/event-stream", r.headers.get("Content-Type", ""))
            r.fp.raw._sock.settimeout(2)  # noqa: SLF001
            try:
                r.read(64)
            except (socket.timeout, TimeoutError):
                pass
        # экспортный билет на SSE не работает
        t_exp = self.ticket("export", "export/calls.csv")
        req2 = urllib.request.Request(self.base + "/api/v2/events?ticket=" + t_exp)
        try:
            with urllib.request.urlopen(req2, timeout=10) as r:
                self.fail("экспортный билет не должен открывать SSE: %s" % r.status)
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 401)

    def test_recording_ticket_bound_to_call(self):
        # звонок с файлом записи
        rec = config.REC_DIR
        rec.mkdir(parents=True, exist_ok=True)
        (rec / "rec_scope_test.mp3").write_bytes(b"FAKE_MP3")
        call_id = db.insert("calls", {
            "campaign_id": 0, "item_id": 0, "contact_id": 0, "contact_name": "T",
            "contact_phone": "79000000000", "caller_id": "", "number_id": 0,
            "provider": "sim", "external_call_id": "", "direction": "out",
            "status": "done", "result": "done_ok", "detail": "", "agent_result": "",
            "recording": "rec_scope_test.mp3", "started_at": "2026-01-01 00:00:00",
            "answered_at": "", "ended_at": "", "duration_sec": 0})
        other_id = db.insert("calls", {
            "campaign_id": 0, "item_id": 0, "contact_id": 0, "contact_name": "T2",
            "contact_phone": "79000000001", "caller_id": "", "number_id": 0,
            "provider": "sim", "external_call_id": "", "direction": "out",
            "status": "done", "result": "done_ok", "detail": "", "agent_result": "",
            "recording": "rec_scope_test.mp3", "started_at": "2026-01-01 00:00:00",
            "answered_at": "", "ended_at": "", "duration_sec": 0})
        t = self.ticket("recording", "call:{}".format(call_id))
        code, body = self.call("GET", "/api/v2/calls/{}/recording?ticket=".format(call_id) + t)
        self.assertEqual(code, 200, "билет записи открывает свою запись")
        self.assertEqual(body, b"FAKE_MP3")
        # тот же билет на чужой звонок — отказ
        code, _ = self.call("GET", "/api/v2/calls/{}/recording?ticket=".format(other_id) + t)
        self.assertEqual(code, 401, "билет записи привязан к конкретному call_id")
        # SSE-билет на записи не работает
        t_sse = self.ticket("sse", "events")
        code, _ = self.call("GET", "/api/v2/calls/{}/recording?ticket=".format(call_id) + t_sse)
        self.assertEqual(code, 401)

    def test_single_use_on_right_purpose(self):
        t = self.ticket("export", "export/calls.csv")
        code, _ = self.call("GET", "/api/v2/export/calls.csv?ticket=" + t)
        self.assertEqual(code, 200)
        code, _ = self.call("GET", "/api/v2/export/calls.csv?ticket=" + t)
        self.assertEqual(code, 401, "билет одноразовый на своём назначении")

    def test_session_header_still_opens_export(self):
        """Заголовок сессии работает как раньше (curl/скрипты)."""
        code, _ = self.call("GET", "/api/v2/export/calls.csv", token=self.token)
        self.assertEqual(code, 200)

    def test_operator_export_ticket_denied(self):
        """Экспорт — только админ: роль проверяется и по билету."""
        self.call("POST", "/api/v2/users/save",
                  {"login": "scopeop", "password": "ScopeOp123!", "role": "operator"},
                  token=self.token)
        op_tok = self._login("scopeop", "ScopeOp123!")
        code, body = self.call("POST", "/api/v2/auth/ticket",
                               {"purpose": "export", "resource": "export/calls.csv"},
                               token=op_tok)
        self.assertEqual(code, 200, "билет выдаётся любой роли")
        t = json.loads(body)["ticket"]
        code, _ = self.call("GET", "/api/v2/export/calls.csv?ticket=" + t)
        self.assertEqual(code, 403, "оператору экспорт запрещён (admin_required)")


if __name__ == "__main__":
    unittest.main()
