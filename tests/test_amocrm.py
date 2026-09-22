# -*- coding: utf-8 -*-
"""Контрактные тесты интеграции amoCRM.

В отличие от первой версии, тестовый сервер НЕ отвечает 200 на любой POST:
он записывает payload'ы, а тесты проверяют реальный контракт API v4:
- официальные коды call_status по РЕАЛЬНОЙ структуре звонка ATS
  (calls.status='done' + calls.result='<результат>');
- идемпотентность (uniq), call_responsible, ссылка на запись (recording_url);
- отсутствие проглатывания ошибок (401/429/5xx поиска контактов);
- точный выбор контакта по хвосту номера и защиту от дублей;
- задачи только на неуспешные результаты;
- OAuth refresh-ротацию по 401;
- backoff по 429;
- вебхук amoCRM: fail-closed, HMAC-подпись хука отключения;
- CRM outbox движка.

Запуск: python -m unittest discover -s tests -v   (из корня репозитория)
"""
import json
import os
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

_TMP = tempfile.mkdtemp(prefix="ats_test_amo_")
os.environ["ATS_FAST"] = "1"
os.environ["ATS_DATA_DIR"] = _TMP
os.environ["ATS_ADMIN_PASSWORD"] = "TestAdmin123!"
os.environ["ATS_DEV_SEED"] = "1"

from app import api, config, db  # noqa: E402
from app.crm import AMO_DEFAULT_RESULT_MAP, AmoCrm, make_crm  # noqa: E402
from app.engine import Engine  # noqa: E402
from app.providers.amocrm import (  # noqa: E402
    AMO_CALL_CONVERSATION, AmoCrmApiError, AmoCrmAuthError, AmoCrmClient,
    pick_exact_contact, phone_tail, _sanitize_phone)
from app.telephony import SimProvider  # noqa: E402

db.init_db()

# Результат → официальный код amoCRM (4=разговор, 7=занято, 6=недозвон, 2=перезвонить)
EXP_CONVERSATION = 4
EXP_BUSY = 7
EXP_NO_REACH = 6
EXP_CALLBACK = 2


def engine_call(**over):
    """Реальная production-структура записи calls на момент CRM-push:
    status='done', смысл — в result (так пишет Engine._finish_attempt)."""
    call = {
        "id": 101,
        "contact_phone": "89261234567",
        "contact_name": "Тестовый Клиент",
        "status": "done",
        "result": "operator_ok",
        "detail": "Разговор завершён (ВАТС)",
        "direction": "out",
        "duration_sec": 45,
        "started_at": "2026-09-22 12:00:00",
        "external_call_id": "vats-123",
        "provider_user": "",
        "recording": "",
        "recording_url": "",
        "agent_result": "",
    }
    call.update(over)
    return call


class ContractAmoServer:
    """Конфигурируемый двойник amoCRM: записывает запросы, умеет падать."""

    def __init__(self):
        self.requests = []          # [(method, path, payload)]
        self.fail = {}              # path_prefix -> http code (однократно)
        self.fail_always = {}       # path_prefix -> http code (всегда)
        self.contacts = []          # кандидаты на GET /contacts
        cls = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _fail_code(self, path):
                for pref, code in list(cls.fail.items()):
                    if pref in path:
                        del cls.fail[pref]
                        return code
                for pref, code in cls.fail_always.items():
                    if pref in path:
                        return code
                return None

            def _send(self, code, obj):
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                if code == 429:
                    self.send_header("Retry-After", "1")
                self.end_headers()
                self.wfile.write(json.dumps(obj, ensure_ascii=False).encode("utf-8"))

            def _handle(self, method):
                from urllib.parse import urlparse
                path = urlparse(self.path).path
                body = None
                if method == "POST":
                    length = int(self.headers.get("Content-Length") or 0)
                    raw = self.rfile.read(length) if length else b""
                    try:
                        body = json.loads(raw.decode("utf-8")) if raw else None
                    except ValueError:
                        body = None
                fc = self._fail_code(path)
                if fc:
                    self._send(fc, {"title": "forced error", "status": fc,
                                    "detail": f"forced {fc} for test"})
                    return
                cls.requests.append((method, path, body))
                if path.endswith("/oauth2/access_token"):
                    self._send(200, {"token_type": "Bearer", "expires_in": 86400,
                                     "server_time": int(time.time()),
                                     "access_token": "NEW_ACCESS_XYZ",
                                     "refresh_token": "NEW_REFRESH_XYZ"})
                elif method == "GET" and path.endswith("/account"):
                    self._send(200, {"id": 12345, "name": "Test Company",
                                     "subdomain": "test", "current_user_id": 2})
                elif method == "GET" and path.endswith("/users"):
                    self._send(200, {"_embedded": {"users": [
                        {"id": 2, "name": "Менеджер Один", "email": "m1@x.ru"}]}})
                elif method == "GET" and path.endswith("/contacts"):
                    self._send(200, {"_embedded": {"contacts": cls.contacts}} if cls.contacts else {"ok": True})
                elif method == "POST" and path.endswith("/contacts"):
                    self._send(200, {"_embedded": {"contacts": [{"id": 777}]}})
                elif method == "POST" and path.endswith("/calls"):
                    self._send(200, {"_embedded": {"calls": [{"id": 901}]}})
                elif method == "POST" and path.endswith("/tasks"):
                    self._send(200, {"_embedded": {"tasks": [{"id": 902}]}})
                elif method == "POST" and "/notes" in path:
                    self._send(200, {"_embedded": {"notes": [{"id": 903}]}})
                else:
                    self._send(404, {"title": "unsupported", "status": 404})

            def do_GET(self):
                self._handle("GET")

            def do_POST(self):
                self._handle("POST")

        self.httpd = HTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def posts(self, suffix):
        return [b for m, p, b in self.requests
                if m == "POST" and p.endswith(suffix)]

    def base_url(self):
        return f"http://127.0.0.1:{self.port}/api/v4"

    def oauth_url(self):
        return f"http://127.0.0.1:{self.port}/oauth2/access_token"


def make_driver(server, mcfg_over=None):
    mcfg = {
        "subdomain": "test",
        "access_token": "token123",
        "responsible_user_id": 2,
        "auto_create_contacts": True,
        "auto_create_tasks": True,
    }
    mcfg.update(mcfg_over or {})
    crm = AmoCrm({"crm": {"driver": "amocrm"}, "amocrm": mcfg})
    orig_client = crm._client

    def patched():
        c = orig_client()
        c.base_url = server.base_url()
        c.oauth_url = server.oauth_url()
        c._min_interval = 0  # тесты без задержек rate limiter'а
        return c

    crm._client = patched
    return crm


class TestSanitizersAndHelpers(unittest.TestCase):
    def test_phone_sanitization(self):
        self.assertEqual(_sanitize_phone("89261234567"), "+79261234567")
        self.assertEqual(_sanitize_phone("+7 (926) 123-45-67"), "+79261234567")

    def test_phone_tail(self):
        self.assertEqual(phone_tail("+7 (926) 123-45-67"), "9261234567")
        self.assertEqual(phone_tail("123"), "123")

    def test_pick_exact_contact(self):
        def c(phone, cid):
            return {"id": cid, "custom_fields_values": [
                {"field_code": "PHONE", "values": [{"value": phone}]}]}
        # Нечёткие кандидаты amoCRM: совпадение только по настоящему хвосту
        pick, conflict = pick_exact_contact(
            [c("+74950000001", 1), c("+79261234567", 2)], "+79261234567")
        self.assertEqual(pick["id"], 2)
        self.assertFalse(conflict)
        # Неточные кандидаты → контакта «нет» (создадим новый, дубля не будет)
        pick, _ = pick_exact_contact([c("+74950000001", 1)], "+79261234567")
        self.assertIsNone(pick)
        # Дубль в CRM → conflict=True, берём первый и логируем
        pick, conflict = pick_exact_contact(
            [c("+79261234567", 5), c("89261234567", 6)], "+79261234567")
        self.assertEqual(pick["id"], 5)
        self.assertTrue(conflict)

    def test_default_result_map_official_codes(self):
        # Успех — «разговор состоялся» (4), НЕ «оставил сообщение» (1)
        self.assertEqual(AMO_DEFAULT_RESULT_MAP["operator_ok"], 4)
        self.assertEqual(AMO_DEFAULT_RESULT_MAP["done_ok"], 4)
        self.assertEqual(AMO_DEFAULT_RESULT_MAP["done_agent"], 4)
        # Занято — 7
        self.assertEqual(AMO_DEFAULT_RESULT_MAP["busy"], 7)
        # Недозвон — 6
        self.assertEqual(AMO_DEFAULT_RESULT_MAP["no_answer"], 6)
        self.assertEqual(AMO_DEFAULT_RESULT_MAP["timeout"], 6)
        self.assertEqual(AMO_DEFAULT_RESULT_MAP["missed"], 6)
        self.assertEqual(AMO_DEFAULT_RESULT_MAP["machine"], 6)
        self.assertEqual(AMO_DEFAULT_RESULT_MAP["failed"], 6)
        # Неверный номер — 5
        self.assertEqual(AMO_DEFAULT_RESULT_MAP["wrong_number"], 5)
        # Абонент ответил, но разговор не состоялся — перезвонить позже (2)
        self.assertEqual(AMO_DEFAULT_RESULT_MAP["no_operator"], 2)
        self.assertEqual(AMO_DEFAULT_RESULT_MAP["rejected"], 2)


class TestFactory(unittest.TestCase):
    def test_factory_amocrm(self):
        crm = make_crm({"crm": {"driver": "amocrm"},
                        "amocrm": {"subdomain": "test", "access_token": "token123"}})
        self.assertIsInstance(crm, AmoCrm)
        self.assertEqual(crm.name, "amocrm")

    def test_factory_unknown_driver_fail_closed(self):
        # Опечатка в драйвере НЕ должна молча превращаться в CSV
        with self.assertRaises(ValueError):
            make_crm({"crm": {"driver": "amocrn"}})
        # Пустое значение — дефолтный CSV (расширение по умолчанию)
        crm = make_crm({"crm": {"driver": ""}})
        self.assertEqual(crm.name, "csv")


class TestClientContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ContractAmoServer()

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()

    def setUp(self):
        self.server.requests.clear()
        self.server.contacts = []
        self.server.fail.clear()
        self.server.fail_always.clear()

    def _client(self, **kw):
        c = AmoCrmClient(subdomain="test", access_token="token123", **kw)
        c.base_url = self.server.base_url()
        c.oauth_url = self.server.oauth_url()
        c._min_interval = 0
        return c

    def test_account_check_plain(self):
        info = self._client().get_account_info()
        self.assertEqual(info.get("id"), 12345)
        # проверка связи не шлёт недокументированный with=users,pipelines
        method_paths = [p for m, p, b in self.server.requests if m == "GET"]
        self.assertTrue(all(p.endswith("/account") for p in method_paths))

    def test_get_users_ok(self):
        users = self._client().get_users()
        self.assertEqual(len(users), 1)
        self.assertEqual(users[0]["id"], 2)

    def test_search_errors_not_swallowed(self):
        # 401 при поиске контакта = ошибка, а НЕ «контакт не найден»
        self.server.fail_always["/contacts"] = 401
        with self.assertRaises(AmoCrmAuthError):
            self._client().search_contacts("+79261234567")
        self.server.fail_always.clear()
        self.server.fail_always["/contacts"] = 500
        with self.assertRaises(AmoCrmApiError):
            self._client().search_contacts("+79261234567")

    def test_users_errors_not_swallowed(self):
        self.server.fail_always["/users"] = 429
        with self.assertRaises(AmoCrmApiError):
            self._client().get_users()

    def test_register_call_payload(self):
        c = self._client()
        c.register_call("+79261234567", "outbound", 45, AMO_CALL_CONVERSATION,
                        result_text="Разговор", record_link="http://x/rec.mp3",
                        responsible_user_id=2, uniq="ats-call-101",
                        call_responsible="Иван")
        calls = self.server.posts("/calls")
        self.assertEqual(len(calls), 1)
        call = calls[0][0]
        self.assertEqual(call["phone"], "+79261234567")
        self.assertEqual(call["direction"], "outbound")
        self.assertEqual(call["call_status"], AMO_CALL_CONVERSATION)
        self.assertEqual(call["uniq"], "ats-call-101")
        self.assertEqual(call["call_responsible"], "Иван")
        self.assertEqual(call["link"], "http://x/rec.mp3")
        self.assertEqual(call["responsible_user_id"], 2)

    def test_429_retried_with_retry_after(self):
        self.server.fail["/account"] = 429  # одноразовый 429
        t0 = time.monotonic()
        info = self._client().get_account_info()
        self.assertEqual(info.get("id"), 12345)
        self.assertLess(time.monotonic() - t0, 10)  # Retry-After=1, не завис

    def test_oauth_refresh_on_401(self):
        saved = []
        c = self._client(client_id="cid", client_secret="secret",
                         refresh_token="OLD_REFRESH",
                         on_tokens_updated=lambda t: saved.append(t))
        self.server.fail["/account"] = 401  # первый запрос — 401
        info = c.get_account_info()
        self.assertEqual(info.get("id"), 12345)
        # OAuth-обмен был вызван и новая пара передана на сохранение
        self.assertTrue(self.server.posts("/oauth2/access_token") or
                        any("/oauth2/access_token" in p
                            for m, p, b in self.server.requests))
        self.assertEqual(c.access_token, "NEW_ACCESS_XYZ")
        self.assertEqual(saved[0]["refresh_token"], "NEW_REFRESH_XYZ")

    def test_401_without_refresh_credentials(self):
        self.server.fail_always["/account"] = 401
        with self.assertRaises(AmoCrmAuthError):
            self._client().get_account_info()


class TestPushResultContract(unittest.TestCase):
    """Главный набор: реальный контракт ATS→amoCRM по документации."""

    @classmethod
    def setUpClass(cls):
        cls.server = ContractAmoServer()

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()

    def setUp(self):
        self.server.requests.clear()
        self.server.contacts = []
        self.server.fail.clear()
        self.server.fail_always.clear()

    def _push(self, call, mcfg_over=None):
        crm = make_driver(self.server, mcfg_over)
        return crm.push_result(call), crm

    def _registered_call(self):
        calls = self.server.posts("/calls")
        self.assertEqual(len(calls), 1, "ожидался ровно один POST /api/v4/calls")
        return calls[0][0]

    def test_success_real_engine_shape_status4_and_no_task(self):
        # P0 ревью: status='done', result='operator_ok' → call_status=4,
        # и НИКАКОЙ задачи «перезвонить»
        self._push(engine_call(result="operator_ok", status="done"))
        call = self._registered_call()
        self.assertEqual(call["call_status"], EXP_CONVERSATION)
        self.assertEqual(call["uniq"], "ats-call-101")
        self.assertEqual(call["direction"], "outbound")
        self.assertEqual(self.server.posts("/tasks"), [])

    def test_busy_status7_creates_task(self):
        self._push(engine_call(result="busy", detail="Занято"))
        call = self._registered_call()
        self.assertEqual(call["call_status"], EXP_BUSY)
        tasks = self.server.posts("/tasks")
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0][0]["entity_type"], "contacts")

    def test_no_answer_status6(self):
        self._push(engine_call(result="no_answer", detail="Нет ответа"))
        self.assertEqual(self._registered_call()["call_status"], EXP_NO_REACH)

    def test_missed_inbound_status6_task(self):
        self._push(engine_call(result="missed", direction="in",
                               detail="Пропущенный входящий"))
        call = self._registered_call()
        self.assertEqual(call["call_status"], EXP_NO_REACH)
        self.assertEqual(call["direction"], "inbound")
        self.assertEqual(len(self.server.posts("/tasks")), 1)

    def test_machine_status6(self):
        self._push(engine_call(result="machine", detail="Автоответчик"))
        self.assertEqual(self._registered_call()["call_status"], EXP_NO_REACH)

    def test_no_operator_status2(self):
        self._push(engine_call(result="no_operator",
                               detail="Оператор не ответил"))
        self.assertEqual(self._registered_call()["call_status"], EXP_CALLBACK)

    def test_wrong_number_status5(self):
        self._push(engine_call(result="wrong_number", detail="Номер не существует"))
        call = self._registered_call()
        self.assertEqual(call["call_status"], 5)

    def test_legacy_status_fallback(self):
        # обратная совместимость: result пуст, результат в status
        self._push(engine_call(result="", status="operator_ok"))
        self.assertEqual(self._registered_call()["call_status"], EXP_CONVERSATION)

    def test_skip_infra_results(self):
        res, _ = self._push(engine_call(result="blocked", status="done"))
        self.assertIsNone(res)
        self.assertEqual(self.server.posts("/calls"), [])

    def test_recording_url_field_used_as_link(self):
        # колонка calls.recording_url (её пишет МегаФон-вебхук) → amo link
        self._push(engine_call(recording_url="https://vats/rec/101.mp3",
                               record_url="", result="operator_ok"))
        call = self._registered_call()
        self.assertEqual(call["link"], "https://vats/rec/101.mp3")

    def test_no_link_field_when_empty(self):
        self._push(engine_call(recording_url="", record_url=""))
        self.assertNotIn("link", self._registered_call())

    def test_exact_contact_no_create_when_found(self):
        self.server.contacts = [{
            "id": 4242, "responsible_user_id": 9,
            "custom_fields_values": [
                {"field_code": "PHONE", "values": [{"value": "+7 (926) 123-45-67"}]}]}]
        self._push(engine_call(result="operator_ok"))
        self.assertEqual(self.server.posts("/contacts"), [],
                         "контакт найден по хвосту — создавать НЕ надо")
        call = self._registered_call()
        # use_contact_responsible=True (дефолт): ответственный из контакта
        self.assertEqual(call["responsible_user_id"], 9)

    def test_fuzzy_candidate_creates_contact(self):
        # amoCRM вернул нечёткое совпадение (другой номер) — создаём свой
        self.server.contacts = [{
            "id": 5, "custom_fields_values": [
                {"field_code": "PHONE", "values": [{"value": "+74959990011"}]}]}]
        self._push(engine_call(result="operator_ok"))
        created = self.server.posts("/contacts")
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0][0]["custom_fields_values"][0]
                         ["values"][0]["value"], "+79261234567")

    def test_search_error_aborts_no_duplicate_create(self):
        # 500 на поиске ≠ «нет контакта»: create НЕ дёргаем, push падает
        self.server.fail_always["/contacts"] = 500
        with self.assertRaises(AmoCrmApiError):
            self._push(engine_call())
        self.assertEqual(self.server.posts("/contacts"), [])
        self.assertEqual(self.server.posts("/calls"), [])

    def test_create_contact_failure_still_registers_call(self):
        # POST /contacts упал (валидация/права) — звонок всё равно
        # регистрируем: amoCRM сам привяжет его по последним 10 цифрам номера.
        crm = make_driver(self.server)
        client = crm._client()
        client.search_contacts = lambda p: []  # контакта нет

        def boom_create(name, phone, responsible_user_id=None):
            raise AmoCrmApiError("amoCRM HTTP 400: validation", status=400)

        client.create_contact = boom_create
        crm._client = lambda: client
        res = crm.push_result(engine_call(result="operator_ok"))
        self.assertIsNotNone(res)
        calls = self.server.posts("/calls")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0]["call_status"], EXP_CONVERSATION)

    def test_operator_user_map_responsible(self):
        self._push(engine_call(result="operator_ok", provider_user="100"),
                   mcfg_over={"operator_user_map": {"100": 555}})
        call = self._registered_call()
        self.assertEqual(call["responsible_user_id"], 555)
        self.assertEqual(call["call_responsible"], "100")

    def test_call_status_map_override(self):
        # машина → «оставил сообщение» (1), если админ так настроил
        self._push(engine_call(result="machine"),
                   mcfg_over={"call_status_map": {"machine": 1}})
        self.assertEqual(self._registered_call()["call_status"], 1)

    def test_auto_task_results_override(self):
        # задачи отключены выбором пустого списка результатов
        self._push(engine_call(result="busy"),
                   mcfg_over={"auto_task_results": ["no_answer"]})
        self.assertEqual(self.server.posts("/tasks"), [])

    def test_created_at_from_started_at(self):
        self._push(engine_call(result="operator_ok",
                               started_at="2026-09-22 12:00:00"))
        call = self._registered_call()
        import datetime
        self.assertEqual(call["created_at"],
                         int(datetime.datetime(2026, 9, 22, 12, 0, 0).timestamp()))

    def test_task_type_id_configurable(self):
        self._push(engine_call(result="busy"), mcfg_over={"task_type_id": 7})
        tasks = self.server.posts("/tasks")
        self.assertEqual(tasks[0][0]["task_type_id"], 7)


class TestWebhookRoute(unittest.TestCase):
    """Вебхук amoCRM: доступ БЕЗ ATS-сессии, но fail-closed по секретам."""

    def setUp(self):
        self._saved = db.get_settings()
        self._orig_engine = api.ENGINE

    def tearDown(self):
        db.save_settings(self._saved)
        api.ENGINE = self._orig_engine

    def _wh(self, method="POST", path="/api/v2/amocrm/webhook", body=None,
            headers=None):
        return api.route(method, path, body or {}, headers or {})

    def test_no_token_configured_fail_closed(self):
        amo = dict(self._saved.get("amocrm") or {})
        amo["webhook_token"] = ""
        amo["webhook_token_env"] = "ATS_AMOCRM_WEBHOOK_TOKEN_NONE"
        s = dict(self._saved)
        s["amocrm"] = amo
        db.save_settings(s)
        payload, code = self._wh()
        self.assertEqual(code, 403)
        self.assertEqual(payload["error"], "webhook_disabled")

    def test_bad_token_rejected_and_no_auth_required(self):
        amo = dict(self._saved.get("amocrm") or {})
        amo["webhook_token"] = "sekret"
        amo["webhook_token_env"] = "ATS_AMOCRM_WEBHOOK_TOKEN_NONE"
        s = dict(self._saved)
        s["amocrm"] = amo
        db.save_settings(s)
        payload, code = self._wh(path="/api/v2/amocrm/webhook?token=wrong")
        self.assertEqual(code, 403)
        self.assertEqual(payload["error"], "bad_secret")

    def test_ok_token_received(self):
        amo = dict(self._saved.get("amocrm") or {})
        amo["webhook_token"] = "sekret"
        amo["webhook_token_env"] = "ATS_AMOCRM_WEBHOOK_TOKEN_NONE"
        s = dict(self._saved)
        s["amocrm"] = amo
        db.save_settings(s)
        payload, code = self._wh(path="/api/v2/amocrm/webhook?token=sekret",
                                 body={"event": "test"})
        self.assertEqual(code, 200)
        self.assertTrue(payload["ok"])

    def test_click2call_explicit_501_not_crash(self):
        amo = dict(self._saved.get("amocrm") or {})
        amo["webhook_token"] = "sekret"
        amo["webhook_token_env"] = "ATS_AMOCRM_WEBHOOK_TOKEN_NONE"
        s = dict(self._saved)
        s["amocrm"] = amo
        db.save_settings(s)
        api.ENGINE = object()  # движок без dial()
        payload, code = self._wh(path="/api/v2/amocrm/webhook?token=sekret",
                                 body={"phone": "+79261234567", "user": "100"})
        self.assertEqual(code, 501)
        self.assertEqual(payload["error"], "click2call_not_supported")

    def test_disconnect_hook_signature(self):
        import hashlib
        import hmac as hmac_mod
        secret = "oauthsecret"
        amo = dict(self._saved.get("amocrm") or {})
        amo["client_secret"] = secret
        amo["client_secret_env"] = "ATS_AMOCRM_SECRET_ENV_NONE"
        s = dict(self._saved)
        s["amocrm"] = amo
        db.save_settings(s)
        cu, acc = "uuid-123", "777"
        sig = hmac_mod.new(secret.encode(), f"{cu}{acc}".encode(),
                           hashlib.sha256).hexdigest()
        # неверная подпись — отказ
        payload, code = api.route(
            "GET", f"/api/v2/amocrm/webhook?client_uuid={cu}&account_id={acc}"
                   f"&signature=deadbeef", {}, {})
        self.assertEqual(code, 403)
        # верная подпись — интеграция выключается
        payload, code = api.route(
            "GET", f"/api/v2/amocrm/webhook?client_uuid={cu}&account_id={acc}"
                   f"&signature={sig}", {}, {})
        self.assertEqual(code, 200)
        self.assertTrue(payload["ok"])
        after = db.get_settings().get("amocrm") or {}
        self.assertTrue(after.get("disabled"))

    def test_disconnect_hook_no_secret_fail_closed(self):
        amo = dict(self._saved.get("amocrm") or {})
        amo["client_secret"] = ""
        amo["client_secret_env"] = "ATS_AMOCRM_SECRET_ENV_NONE"
        s = dict(self._saved)
        s["amocrm"] = amo
        db.save_settings(s)
        payload, code = api.route(
            "GET", "/api/v2/amocrm/webhook?client_uuid=x&account_id=1&signature=y",
            {}, {})
        self.assertEqual(code, 403)
        self.assertEqual(payload["error"], "webhook_disabled")


class _FailingCrm:
    name = "failing"

    def push_result(self, call):
        raise AmoCrmApiError("amoCRM HTTP 500: boom", status=500)


class _OkCrm:
    name = "ok"

    def __init__(self):
        self.pushed = []

    def push_result(self, call):
        self.pushed.append(call)


class TestCrmOutbox(unittest.TestCase):
    """Результат звонка не теряется при недоступной CRM: outbox + ретраи."""

    def setUp(self):
        db.q("DELETE FROM crm_outbox")
        cid = db.insert("calls", {
            "campaign_id": 0, "item_id": 0, "contact_id": 0,
            "contact_name": "Оутбокс Тест", "contact_phone": "+79260000099",
            "caller_id": "", "number_id": 0, "provider": "sim",
            "external_call_id": "", "direction": "out", "status": "done",
            "result": "operator_ok", "detail": "Тест", "agent_result": "",
            "recording": "", "started_at": config.now_iso(), "answered_at": "",
            "ended_at": config.now_iso(), "duration_sec": 10, "diversion": "",
            "provider_user": ""})
        self.call = db.fetch1("SELECT * FROM calls WHERE id=?", (cid,))

    def tearDown(self):
        db.q("DELETE FROM crm_outbox")
        if self.call:
            db.q("DELETE FROM calls WHERE id=?", (self.call["id"],))

    def _engine(self, crm_obj):
        eng = Engine(provider=SimProvider(), auto_start=False)
        eng.crm = crm_obj
        return eng

    def test_failure_goes_to_outbox(self):
        eng = self._engine(_FailingCrm())
        eng._push_crm(self.call, "operator_ok")
        rows = db.fetch("SELECT * FROM crm_outbox WHERE status='pending'")
        self.assertEqual(len(rows), 1)
        payload = json.loads(rows[0]["payload_json"])
        self.assertEqual(payload["id"], self.call["id"])

    def test_outbox_tick_delivers_and_marks_done(self):
        db.crm_outbox_enqueue("push_result", self.call, error="test")
        ok_crm = _OkCrm()
        eng = self._engine(ok_crm)
        eng._crm_outbox_tick()
        self.assertEqual(len(ok_crm.pushed), 1)
        stats = db.crm_outbox_stats()
        self.assertEqual(stats.get("done"), 1)
        self.assertEqual(db.fetch("SELECT * FROM crm_outbox WHERE status='pending'"), [])

    def test_outbox_tick_backoff_and_giveup(self):
        db.crm_outbox_enqueue("push_result", self.call, error="test")
        eng = self._engine(_FailingCrm())
        for i in range(9):
            # принудительно «созреваем» запись для следующего повтора
            db.q("UPDATE crm_outbox SET next_attempt_at=? WHERE status='pending'",
                 (config.now_iso(),))
            eng._crm_outbox_tick()
        row = db.fetch1("SELECT * FROM crm_outbox")
        self.assertEqual(row["status"], "failed")
        self.assertGreaterEqual(row["attempts"], 8)


if __name__ == "__main__":
    unittest.main()
