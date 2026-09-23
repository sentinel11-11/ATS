# -*- coding: utf-8 -*-
"""Мультиком (агрегатор): контракт провайдера для движка, makecall, вебхук
статусов (секрет/дедуп) и корреляция событий с FSM движка — автодозвон,
финал разговора, входящие, честный no_media там, где нет аудио.

Запуск: python -m unittest discover -s tests -v   (из корня репозитория)
"""
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

_TMP = tempfile.mkdtemp(prefix="ats_mc_")
os.environ.setdefault("ATS_FAST", "1")
os.environ["ATS_DATA_DIR"] = _TMP
os.environ["ATS_ADMIN_PASSWORD"] = "TestAdmin123!"

from app import api, config, db  # noqa: E402
from app.engine import Engine  # noqa: E402
from app.providers.multicom import (  # noqa: E402
    MulticomClient, MulticomProvider, map_multicom_webhook,
    normalize_number, phone_variants, webhook_fingerprint)
from app.telephony import ProviderNotConfigured, make_provider  # noqa: E402

db.init_db()

CALLS_MADE = []   # что реально ушло в API оператора (path, payload)
HANGUPS = []


class DummyMulticomHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _json(self, obj, code=200):
        raw = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path in ("/v1/account", "/v1/status"):
            self._json({"ok": True, "account": "test_multicom"})
        elif self.path == "/v1/numbers":
            # намеренно «грязные» номера: один и тот же номер в двух написаниях
            self._json({"items": [{"number": "+7 900 111-22-33"},
                                  {"number": "79001112233"},
                                  {"phone": "89005554433"}]})
        else:
            self._json({"error": "not_found"}, 404)

    def do_POST(self):
        n = int(self.headers.get("Content-Length", "0") or 0)
        body = json.loads(self.rfile.read(n).decode("utf-8") or "{}") if n else {}
        if self.path.endswith("/calls/make") or self.path.endswith("/dial"):
            CALLS_MADE.append((self.path, body))
            self._json({"call_id": "mc-777", "clid": "79001112233"})
        elif self.path.startswith("/v1/calls/") and self.path.endswith("/hangup"):
            HANGUPS.append(self.path)
            self._json({"ok": True})
        else:
            self._json({"error": "bad_request"}, 400)


class MulticomTest(unittest.TestCase):
    """Клиент/провайдер: контракт движка dial(req), внешний id, hangup, check."""

    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), DummyMulticomHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = "http://127.0.0.1:{}".format(cls.server.server_port)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def _provider(self, **extra):
        cfg = {"multicom": dict({"api_url": self.url + "/v1", "api_key": "test-key"}, **extra)}
        return MulticomProvider().configure(cfg)

    def test_client_and_provider_read(self):
        client = MulticomClient(api_url=self.url + "/v1", api_key="test-key")
        self.assertTrue(client.get_account_info().get("ok"))
        nums = client.get_numbers()
        self.assertEqual(len(nums), 3)
        self.assertEqual(nums[0]["number"], "+7 900 111-22-33")
        self.assertEqual(client.make_call(phone="79998887766").get("call_id"), "mc-777")
        prov = self._provider()
        self.assertEqual(prov.check()["account"]["account"], "test_multicom")

    def test_masked_key_fails_closed(self):
        with self.assertRaises(Exception):
            MulticomClient(api_url=self.url + "/v1", api_key="********")
        with self.assertRaises(ProviderNotConfigured):
            make_provider({"provider": "multicom",
                           "multicom": {"api_url": self.url + "/v1", "api_key": ""}})

    def test_normalize_and_variants(self):
        self.assertEqual(normalize_number("+7 (900) 222-33-44"), "79002223344")
        self.assertEqual(normalize_number("8 (900) 222-33-44"), "79002223344")
        self.assertEqual(normalize_number(""), "")
        self.assertIn("79001112233", phone_variants("+7 900 111-22-33"))
        self.assertIn("89001112233", phone_variants("+7 900 111-22-33"))

    def test_dial_contract_req_dict(self):
        """Движок зовёт dial(req: dict) — провайдер обязан понимать именно это."""
        del CALLS_MADE[:]
        prov = self._provider()
        prov.dial({"call_id": 99, "phone": "+7 (900) 222-33-44",
                   "caller_id": "+79001112233", "flow": "operator", "text": "привет"})
        path, payload = CALLS_MADE[-1]
        self.assertEqual(path, "/v1/calls/make")
        self.assertEqual(payload["phone"], "79002223344")
        self.assertEqual(payload["caller_id"], "79001112233")
        self.assertEqual(payload["direction"], "outbound")
        self.assertEqual(payload["client_reference"], "ats-call-99")
        # correlation-ключ для вебхуков (движок пишет это в calls.external_call_id)
        self.assertEqual(prov.external_id(99)["callid"], "mc-777")
        self.assertEqual(prov.external_id(99)["clid"], "79001112233")

    def test_dial_legacy_kwargs_still_work(self):
        del CALLS_MADE[:]
        prov = self._provider()
        res = prov.dial(phone="79998887766")
        self.assertTrue(res.get("ok"))
        self.assertEqual(CALLS_MADE[-1][1]["phone"], "79998887766")

    def test_dial_empty_number_is_error(self):
        prov = self._provider()
        with self.assertRaises(ProviderNotConfigured):
            prov.dial({"call_id": 1, "phone": "  "})

    def test_dial_api_error_bubbles_to_engine(self):
        """Сбой оператора — исключение (движок зафиксирует failed+ретрай),
        а не «успех», после которого звонок висел бы до watchdog."""
        prov = self._provider(api_url="http://127.0.0.1:1/v1")
        with self.assertRaises(ProviderNotConfigured):
            prov.dial({"call_id": 5, "phone": "79002223344"})

    def test_hangup_best_effort(self):
        del HANGUPS[:]
        prov = self._provider()
        self.assertFalse(prov.hangup(12345))          # нет внешнего id → no-op, без падения
        prov.dial({"call_id": 7, "phone": "79002223344"})
        self.assertTrue(prov.hangup(7))               # есть → POST /calls/mc-777/hangup
        self.assertEqual(HANGUPS, ["/v1/calls/mc-777/hangup"])

    def test_custom_paths(self):
        del CALLS_MADE[:]
        prov = self._provider(makecall_path="/openapi/dial", account_path="/openapi/me")
        prov.dial({"call_id": 1, "phone": "79002223344"})
        self.assertEqual(CALLS_MADE[-1][0], "/v1/openapi/dial")
        self.assertEqual(prov.client.paths["account_path"], "/openapi/me")

    def test_provider_capabilities(self):
        prov = self._provider()
        self.assertEqual(prov.name, "multicom")
        self.assertFalse(prov.interactive)      # интерактивного канала нет
        self.assertFalse(prov.supports_media)   # и аудио нет → flow=message = no_media
        self.assertIsNone(prov.make_channel(1, "79002223344"))


class WebhookMapTest(unittest.TestCase):
    def test_status_mapping(self):
        cases = {
            "answered": "answered", "connected": "answered", "talking": "answered",
            "completed": "completed", "hangup": "completed", "ended": "completed",
            "busy": "busy", "user_busy": "busy",
            "no_answer": "no_answer", "timeout": "no_answer", "missed": "no_answer",
            "rejected": "failed", "error": "failed",
            "canceled": "canceled", "cancel": "canceled",
            "ringing": "ring", "dialing": "ring",
            "dropped": "dropped", "disconnected": "dropped",
        }
        for raw, ev in cases.items():
            got = map_multicom_webhook({"call_id": "c1", "status": raw})
            self.assertEqual(got["event"], ev, raw)
            self.assertEqual(got["raw_type"], raw)

    def test_flat_nested_and_form(self):
        flat = map_multicom_webhook({"id": "9", "state": "busy", "client": "79001112233",
                                     "duration": "0"})
        nested = map_multicom_webhook({"call": {"id": "9", "state": "busy"},
                                       "duration": "12"})
        self.assertEqual(flat["external_call_id"], "9")
        self.assertEqual(flat["phone"], "79001112233")
        self.assertEqual(nested["duration"], 12)
        self.assertIsNone(map_multicom_webhook({"status": "busy"}))   # нет id звонка
        self.assertIsNone(map_multicom_webhook(None))

    def test_direction_and_clid(self):
        ev = map_multicom_webhook({"callid": "5", "event": "answer", "dir": "INCOMING",
                                   "clid": "+7 900 111-22-33", "cause": "ok"})
        self.assertEqual(ev["direction"], "in")
        self.assertEqual(ev["clid"], "79001112233")
        self.assertEqual(ev["detail"], "ok")
        self.assertEqual(ev["event"], "answered")

    def test_fingerprint_stable_and_unique(self):
        a = map_multicom_webhook({"call_id": "1", "status": "busy"})
        b = map_multicom_webhook({"call_id": "1", "status": "busy"})
        c = map_multicom_webhook({"call_id": "2", "status": "busy"})
        self.assertEqual(webhook_fingerprint(a), webhook_fingerprint(b))
        self.assertNotEqual(webhook_fingerprint(a), webhook_fingerprint(c))


class EngineMulticomTest(unittest.TestCase):
    """Корреляция вебхука с FSM движка + авторизация маршрута + синк пула."""

    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), DummyMulticomHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        url = "http://127.0.0.1:{}".format(cls.server.server_port)
        s = db.get_settings()
        cls._old = {k: s.get(k) for k in ("multicom", "crm", "provider")}
        s["multicom"] = dict(config.DEFAULT_SETTINGS["multicom"],
                             api_url=url + "/v1", api_key="test-key",
                             webhook_secret="MCSECRET")
        s["crm"] = {"driver": "csv"}
        db.save_settings(s)
        cls.prov = MulticomProvider().configure(s)
        cls.eng = Engine(provider=cls.prov, auto_start=False)
        cls._old_engine = api.ENGINE
        api.ENGINE = cls.eng

    @classmethod
    def tearDownClass(cls):
        api.ENGINE = cls._old_engine
        cls.eng.stop()
        cls.server.shutdown()

    def setUp(self):
        self.seq = 0

    def _call(self, flow="operator", ext="mc-777", status="dialing", answered=""):
        """Кампания(paused)+позиция+звонок в состоянии «дозваниваемся»."""
        self.seq += 1
        now = config.now_iso()
        camp_id = db.insert("campaigns", {"name": "MC {}".format(self.seq), "flow": flow,
                                          "status": "paused", "template_id": 0,
                                          "created": now, "updated": now})
        phone = "79002220{:04d}".format(self.seq)
        db.q("DELETE FROM campaign_items WHERE contact_phone=?", (phone,))
        db.q("DELETE FROM contacts WHERE phone=?", (phone,))
        contact_id = db.insert("contacts", {"name": "Тест {}".format(self.seq),
                                            "phone": phone,
                                            "consent": 1, "created": now, "updated": now})
        item_id = db.insert("campaign_items", {"campaign_id": camp_id,
                                               "contact_id": contact_id,
                                               "contact_name": "Тест", "contact_phone": phone,
                                               "status": "dialing", "attempts": 1,
                                               "created": now, "updated": now})
        call_id = db.insert("calls", {"campaign_id": camp_id, "item_id": item_id,
                                      "contact_id": contact_id, "contact_phone": phone,
                                      "contact_name": "Тест", "provider": "multicom",
                                      "external_call_id": ext, "direction": "out",
                                      "status": status, "started_at": now,
                                      "answered_at": answered, "caller_id": "79001112233"})
        return camp_id, item_id, call_id

    def _push(self, body, secret="MCSECRET"):
        path = "/api/v2/webhooks/multicom" + (("?secret=" + secret) if secret else "")
        headers = {"X-Multicom-Secret": secret} if secret else {}
        payload, code = api._route("POST", path, body, headers)
        self.eng.tick_once()
        return payload, code

    def test_webhook_auth_fail_closed(self):
        s = db.get_settings()
        saved = dict(s["multicom"])
        s["multicom"]["webhook_secret"] = ""
        s["multicom"]["webhook_secret_env"] = "ATS_MC_UNSET_NOPE"
        os.environ.pop("ATS_MC_UNSET_NOPE", None)
        db.save_settings(s)
        try:
            payload, code = api._route("POST", "/api/v2/webhooks/multicom",
                                       {"call_id": "x", "status": "busy"}, {})
            self.assertEqual((code, payload["error"]), (403, "webhook_disabled"))
            payload, code = api._route("POST", "/api/v2/webhooks/multicom",
                                       {"call_id": "x", "status": "busy"},
                                       {"X-Multicom-Secret": "wrong"})
            self.assertEqual(code, 403)   # секрет не задан — маршрут закрыт целиком
        finally:
            s = db.get_settings()
            s["multicom"] = saved
            db.save_settings(s)

    def test_secret_required_per_request(self):
        payload, code = api._route("POST", "/api/v2/webhooks/multicom",
                                   {"call_id": "x", "status": "busy"},
                                   {"X-Multicom-Secret": "nope"})
        self.assertEqual((code, payload["error"]), (401, "bad_secret"))

    def test_unrecognized_event_422(self):
        payload, code = api._route("POST", "/api/v2/webhooks/multicom",
                                   {"status": "busy"}, {"X-Multicom-Secret": "MCSECRET"})
        self.assertEqual((code, payload["error"]), (422, "unrecognized"))

    def test_answered_then_completed_operator_ok(self):
        camp_id, item_id, call_id = self._call(flow="operator")
        payload, code = self._push({"call_id": "mc-777", "status": "answered"})
        self.assertEqual(code, 200)
        call = db.fetch1("SELECT * FROM calls WHERE id=?", (call_id,))
        self.assertEqual(call["status"], "wait_operator")
        # финал: ACД-запись закрывается, результат = разговор с оператором
        self._push({"call_id": "mc-777", "status": "completed",
                    "record_url": "https://mc/records/777.mp3", "duration": "41"})
        call = db.fetch1("SELECT * FROM calls WHERE id=?", (call_id,))
        self.assertEqual((call["status"], call["result"]), ("done", "operator_ok"))
        self.assertEqual(call["recording_url"], "https://mc/records/777.mp3")
        item = db.fetch1("SELECT * FROM campaign_items WHERE id=?", (item_id,))
        self.assertEqual(item["status"], "operator_ok")
        # пуш в CRM случился (csv-драйвер пишет файл выгрузки)
        self.assertTrue(list((config.CRM_OUT_DIR).glob("crm_push_*.csv")))

    def test_busy_retries_item(self):
        camp_id, item_id, call_id = self._call(ext="mc-7001")
        self._push({"call_id": "mc-7001", "status": "busy", "cause": " occupied"})
        item = db.fetch1("SELECT * FROM campaign_items WHERE id=?", (item_id,))
        self.assertEqual(item["status"], "queued")           # автодозвон
        self.assertTrue(item["next_attempt_at"])             # с паузой
        call = db.fetch1("SELECT * FROM calls WHERE id=?", (call_id,))
        self.assertEqual((call["status"], call["result"]), ("done", "busy"))

    def test_duplicate_event_ignored(self):
        camp_id, item_id, call_id = self._call(ext="mc-7002")
        body = {"call_id": "mc-7002", "status": "busy"}
        self._push(dict(body))
        self._push(dict(body))
        rows = db.fetch("SELECT * FROM attempts WHERE call_id=?", (call_id,))
        self.assertEqual(len(rows), 1, "повтор вебхука не должен плодить попытки")

    def test_orphan_event_does_not_break(self):
        payload, code = self._push({"call_id": "ghost-{}".format(self.seq), "status": "busy"})
        self.assertEqual(code, 200)      # отвечаем 200, но ничего не находим
        self.assertEqual(db.fetch1("SELECT status FROM provider_events WHERE "
                                    "external_call_id=? ORDER BY id DESC LIMIT 1",
                                   ("ghost-{}".format(self.seq),))["status"], "orphan")

    def test_incoming_call_logged(self):
        ext = "mc-in-{}".format(self.seq)
        phone = "79003331122"
        db.q("DELETE FROM contacts WHERE phone=?", (phone,))
        self._push({"call_id": ext, "status": "ringing", "direction": "in", "phone": phone})
        call = self._resolve(ext)
        self.assertIsNotNone(call, "входящий должен попасть в журнал")
        self.assertEqual((call["direction"], call["status"]), ("in", "ringing"))
        self._push({"call_id": ext, "status": "completed"})
        call = self._resolve(ext)
        self.assertEqual(call["result"], "missed")   # без ответа = пропущенный
        db.q("DELETE FROM contacts WHERE phone=?", (phone,))

    def _resolve(self, ext):
        return db.fetch1("SELECT * FROM calls WHERE provider='multicom' "
                         "AND external_call_id=?", (ext,))

    def test_message_flow_honest_no_media(self):
        """У агрегатора нет аудио — «сообщение доставлено» врать не будем."""
        camp_id, item_id, call_id = self._call(flow="message", ext="mc-7003")
        self._push({"call_id": "mc-7003", "status": "answered"})
        call = db.fetch1("SELECT * FROM calls WHERE id=?", (call_id,))
        self.assertEqual(call["result"], "no_media")

    def test_pool_sync_normalizes_and_idempotent(self):
        db.q("DELETE FROM numbers WHERE provider='multicom'")
        rep = self.eng.multicom_pool_sync(dry_run=True)
        self.assertTrue(rep["dry_run"])
        added_dry = rep["added"]
        self.assertEqual(added_dry, ["79001112233", "79005554433"],
                         "нормализация E.164 + дедуп одного номера в двух написаниях")
        self.assertEqual(len(db.fetch("SELECT id FROM numbers WHERE provider='multicom'")), 0)
        rep2 = self.eng.multicom_pool_sync()
        self.assertEqual(rep2["added"], added_dry)
        rep3 = self.eng.multicom_pool_sync()
        self.assertEqual(rep3["added"], [], "повторный синк ничего не добавляет")
        db.q("DELETE FROM numbers WHERE provider='multicom'")

    def test_multicom_numbers_accepted_in_pool(self):
        from app import numbers as numbers_mod
        ok, why = numbers_mod.add_number("+7 900 777-66-55", provider="multicom")
        self.assertTrue(ok, why)
        db.q("DELETE FROM numbers WHERE number LIKE '79007776655%' OR number LIKE '+79007776655%'")

    def test_cleanup(self):
        db.q("DELETE FROM campaign_items")
        db.q("DELETE FROM calls")
        db.q("DELETE FROM campaigns")
        db.q("DELETE FROM provider_events")
        db.q("DELETE FROM attempts")


if __name__ == "__main__":
    unittest.main()
