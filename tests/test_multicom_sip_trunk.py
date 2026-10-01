# -*- coding: utf-8 -*-
"""Мультиком через SIP-транк (Asterisk/AMI): форматы номера для оператора,
передача текста на озвучку в диалплан, входящие с DID в журнал/CRM, честный
финал потока message, подписанные ссылки на локальные записи.

Запуск: python -m unittest discover -s tests -v   (из корня репозитория)
"""
import base64
import datetime
import json
import os
import tempfile
import unittest

_TMP = tempfile.mkdtemp(prefix="ats_mc_sip_")
os.environ.setdefault("ATS_FAST", "1")
os.environ["ATS_DATA_DIR"] = _TMP
os.environ["ATS_ADMIN_PASSWORD"] = "TestAdmin123!"

from app import asterisk as ami_mod  # noqa: E402
from app import config, db, records  # noqa: E402
from app.engine import Engine  # noqa: E402
from app.telephony import (  # noqa: E402
    AsteriskAmiProvider, TelephonyProvider, format_outbound_number)

db.init_db()


class NumberFormatTest(unittest.TestCase):
    def test_ru8_for_multicom(self):
        """Мультиком требует исходящий набор 8_КодГорода_Номер — в базе E.164."""
        for v in ("+79690229926", "79690229926", "89690229926", "+7 (969) 022-99-26"):
            self.assertEqual(format_outbound_number(v, "ru8"), "89690229926", v)
        self.assertEqual(format_outbound_number("9690229926", "ru8"), "89690229926")
        self.assertEqual(format_outbound_number("+74951234567", "ru8"), "84951234567")

    def test_other_formats(self):
        self.assertEqual(format_outbound_number("+79690229926", "e164"), "+79690229926")
        self.assertEqual(format_outbound_number("89690229926", "e164"), "+79690229926")
        self.assertEqual(format_outbound_number("+79690229926", "d10"), "9690229926")
        self.assertEqual(format_outbound_number("+79690229926", "digits"), "79690229926")
        self.assertEqual(format_outbound_number("+79690229926", "raw"), "+79690229926")
        self.assertEqual(format_outbound_number("+79690229926", ""), "+79690229926")

    def test_garbage_is_empty(self):
        for v in ("", None, "абвгд", "—"):
            self.assertEqual(format_outbound_number(v, "ru8"), "")
        self.assertEqual(format_outbound_number("не номер", "digits"), "")


class FakeClient:
    """Записывает действия; события подаются вручную через provider._on_event."""

    def __init__(self):
        self.actions = []
        self._n = 0

    def action(self, name, params=None, timeout=None, check=True, action_id=None):
        self._n += 1
        self.actions.append((name, dict(params or {})))
        return {"Response": "Success", "ActionID": action_id or "aid-{}".format(self._n)}

    def wait_for(self, predicate, timeout):
        return None


def _provider(**cfg):
    p = AsteriskAmiProvider()
    base = dict(config.DEFAULT_SETTINGS["ami"], host="127.0.0.1", user="ats", secret="s",
                trunk="mcm")
    base.update(cfg)
    p.cfg = base
    p.client = FakeClient()
    p._sink = __import__("queue").Queue()
    return p


class AmiDialTest(unittest.TestCase):
    def test_channel_uses_tech_and_trunk(self):
        p = _provider(tech="SIP", number_format="ru8", caller_id_format="d10")
        p.dial({"call_id": 7, "phone": "+79690229926", "caller_id": "+79690229926",
                "flow": "message", "text": ""})
        name, params = p.client.actions[0]
        self.assertEqual(name, "Originate")
        self.assertEqual(params["Channel"], "SIP/mcm/89690229926")
        self.assertEqual(params["CallerID"], '"ats-call-7" <9690229926>')

    def test_pjsip_default_and_prefix(self):
        p = _provider(number_format="digits", dial_prefix="0")
        p.dial({"call_id": 1, "phone": "89690229926", "caller_id": "", "flow": "message"})
        params = p.client.actions[0][1]
        self.assertEqual(params["Channel"], "PJSIP/089690229926@mcm")

    def test_empty_number_fails_fast(self):
        p = _provider(number_format="ru8")
        with self.assertRaises(Exception):
            p.dial({"call_id": 2, "phone": "—", "caller_id": "", "flow": "message"})

    def test_text_goes_to_dialplan_as_base64(self):
        p = _provider(number_format="ru8")
        p.dial({"call_id": 3, "phone": "+79690229926", "caller_id": "+79690229926",
                "flow": "message", "text": "Здравствуйте,\r\n это ATS"})
        params = p.client.actions[0][1]
        self.assertEqual(params["Variable"][0], "ATS_CALL_ID=3")
        b64 = params["Variable"][1].split("=", 1)[1]
        self.assertEqual(base64.b64decode(b64).decode("utf-8"), "Здравствуйте,\r\n это ATS")
        blob = ami_mod.encode_action("Originate", params)[0]
        self.assertEqual(blob.count("Variable: "), 3)   # call_id + текст + ATS_RECORD
        self.assertNotIn("\r\n\r\nЗдрав", blob)   # перевод строк наружу не утекает

    def test_pjsip_channel_syntax_and_pattern_override(self):
        """chan_pjsip набирается как PJSIP/номер@endpoint; шаблон можно заменить."""
        p = _provider(channel_pattern="{trunk}/{number}")
        p.dial({"call_id": 9, "phone": "+79690229926", "caller_id": "", "flow": "message"})
        self.assertEqual(p.client.actions[0][1]["Channel"], "mcm/+79690229926")
        p2 = _provider(number_format="raw")
        p2.dial({"call_id": 9, "phone": "89690229926", "caller_id": "", "flow": "message"})
        self.assertEqual(p2.client.actions[0][1]["Channel"], "PJSIP/89690229926@mcm")

    def test_record_variable_for_mixmonitor(self):
        p = _provider()
        p.dial({"call_id": 8, "phone": "+79690229926", "caller_id": "", "flow": "message"})
        self.assertIn("ATS_RECORD=1", p.client.actions[0][1]["Variable"])
        p2 = _provider(record_calls=False)
        p2.dial({"call_id": 8, "phone": "+79690229926", "caller_id": "", "flow": "message"})
        self.assertNotIn("ATS_RECORD=1", p2.client.actions[0][1]["Variable"])

    def test_play_failure_reported_by_dialplan_variable(self):
        """AGI без TTS не должен оставляать «доставлено»: диалплан ставит ATS_PLAY=failed."""
        p = _provider()
        out = []
        p.emit = lambda ev: out.append(dict(ev, provider=p.name))
        p.channels[42] = "PJSIP/89690229926@mcm"
        p._on_event({"Event": "VariableSet", "Channel": "PJSIP/89690229926@mcm",
                     "Variable": "ATS_PLAY", "Value": "failed: no TTS binary"})
        self.assertEqual(out[-1]["status"], "failed")
        self.assertIn("no TTS binary", out[-1]["detail"])
        out.clear()
        p._on_event({"Event": "VariableSet", "Channel": "PJSIP/89690229926@mcm",
                     "Variable": "ATS_PLAY", "Value": "ok"})
        self.assertEqual(out, [])

    def test_play_message_disabled(self):
        p = _provider(play_message=False, record_calls=False)
        p.dial({"call_id": 4, "phone": "+79690229926", "caller_id": "", "flow": "message",
                "text": "текст"})
        self.assertEqual(p.client.actions[0][1]["Variable"], ["ATS_CALL_ID=4"])

    def test_operator_leg_follows_tech(self):
        p = _provider(tech="SIP", operator_trunk="mcm-op")
        p.channels[11] = "SIP/mcm-00000001"
        # wait_for ничего не отдаёт → оператор не ответил → бриджа нет
        self.assertFalse(p.connect_operator(11, "101"))
        name, params = p.client.actions[0]
        self.assertEqual((name, params["Channel"]), ("Originate", "SIP/mcm-op/101"))


class AmiInboundTest(unittest.TestCase):
    def _events(self, p):
        out = []
        p._sink.put = out.append
        p.emit = lambda ev: out.append(dict(ev, provider=p.name))
        return out

    def test_inbound_lifecycle(self):
        p = _provider(inbound_contexts="from-mcm")
        out = self._events(p)
        p._on_event({"Event": "Newchannel", "Channel": "PJSIP/mcm-0000000a",
                     "Context": "from-mcm", "Exten": "9690229937",
                     "CallerIDNum": "9690229926"})
        self.assertEqual(out[-1]["event"], "inbound")
        self.assertEqual(out[-1]["phone"], "9690229926")
        self.assertEqual(out[-1]["did"], "9690229937")
        # повторный Newchannel по тому же каналу не плодит дубль
        p._on_event({"Event": "Newchannel", "Channel": "PJSIP/mcm-0000000a",
                     "Context": "from-mcm", "Exten": "9690229937"})
        self.assertEqual(sum(1 for e in out if e["event"] == "inbound"), 1)
        p._on_event({"Event": "Newstate", "Channel": "PJSIP/mcm-0000000a",
                     "ChannelState": "6"})
        self.assertEqual(out[-1]["event"], "inbound_answer")
        p._on_event({"Event": "Hangup", "Channel": "PJSIP/mcm-0000000a", "Cause": "16"})
        self.assertEqual(out[-1]["event"], "inbound_end")
        # канал больше не отслеживается
        p._on_event({"Event": "Newstate", "Channel": "PJSIP/mcm-0000000a",
                     "ChannelState": "6"})
        self.assertEqual(out[-1]["event"], "inbound_end")

    def test_other_contexts_are_not_inbound(self):
        p = _provider(inbound_contexts="from-mcm")
        out = self._events(p)
        p._on_event({"Event": "Newchannel", "Channel": "PJSIP/mcm-0000000b",
                     "Context": "from-internal", "Exten": "101"})
        self.assertEqual(out, [])
        # и чужой канал (не с нашего транка) — тоже
        p._on_event({"Event": "Newchannel", "Channel": "PJSIP/other-0001",
                     "Context": "from-mcm", "Exten": "101"})
        self.assertEqual(out, [])

    def test_inbound_disabled(self):
        p = _provider(inbound_contexts="from-mcm", inbound_enabled=False)
        out = self._events(p)
        p._on_event({"Event": "Newchannel", "Channel": "PJSIP/mcm-0000000c",
                     "Context": "from-mcm", "Exten": "1"})
        self.assertEqual(out, [])


class StubAmi(TelephonyProvider):
    """Провайдер-заглушка с возможностями Asterisk: озвучка в диалплане."""
    name = "ami"
    dialplan_plays_text = True

    def __init__(self):
        super().__init__()
        self.dials = []

    def configure(self, settings):
        return self

    def dial(self, req):
        self.dials.append(req)
        return True

    def hangup(self, call_id):
        return None


class EngineAmiFlowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        s = db.get_settings()
        cls._old = {k: s.get(k) for k in ("crm", "provider")}
        s["crm"] = {"driver": "csv"}
        s["provider"] = "ami"
        s["message_max_sec"] = 300
        db.save_settings(s)
        cls.prov = StubAmi()
        cls.eng = Engine(provider=cls.prov, auto_start=False)

    @classmethod
    def tearDownClass(cls):
        cls.eng.stop()

    def setUp(self):
        # сквозной счётчик: имена/номера не должны пересекаться между тестами
        type(self)._gseq = getattr(type(self), "_gseq", 0) + 1
        self.seq = type(self)._gseq

    def _mk(self, flow="message", status="dialing"):
        now = config.now_iso()
        camp_id = db.insert("campaigns", {"name": "SIP {}".format(self.seq), "flow": flow,
                                          "status": "paused", "template_id": 0,
                                          "created": now, "updated": now})
        phone = "7969022{:05d}".format(self.seq)
        contact_id = db.insert("contacts", {"name": "Клиент {}".format(self.seq), "phone": phone,
                                            "consent": 1, "created": now, "updated": now})
        item_id = db.insert("campaign_items", {"campaign_id": camp_id, "contact_id": contact_id,
                                              "contact_name": "Клиент", "contact_phone": phone,
                                              "status": "dialing", "attempts": 1,
                                              "created": now, "updated": now})
        call_id = db.insert("calls", {"campaign_id": camp_id, "item_id": item_id,
                                      "contact_id": contact_id, "contact_phone": phone,
                                      "contact_name": "Клиент", "provider": "ami",
                                      "external_call_id": "PJSIP/mcm-out-{}".format(self.seq),
                                      "direction": "out", "status": status, "started_at": now,
                                      "caller_id": "+79690229926"})
        return camp_id, item_id, call_id

    def _call(self, call_id):
        return db.fetch1("SELECT * FROM calls WHERE id=?", (call_id,))

    def test_message_waits_for_real_playback(self):
        """flow=message на Asterisk: «доставлено» только после финала канала."""
        _, item_id, call_id = self._mk()
        self.eng.handle_event({"provider": "ami", "event": "answered", "call_id": call_id,
                               "human": True})
        call = self._call(call_id)
        self.assertEqual(call["status"], "talk")
        self.assertEqual(call["ended_at"], "")
        self.assertEqual(call["result"], "", "результат не должен появляться до озвучки")
        self.eng.handle_event({"provider": "ami", "event": "done", "call_id": call_id,
                               "detail": "Hangup"})
        call = self._call(call_id)
        self.assertEqual((call["status"], call["result"]), ("done", "done_ok"))
        self.assertIn("Asterisk", call["detail"])
        self.assertEqual(db.fetch1("SELECT status FROM campaign_items WHERE id=?",
                                   (item_id,))["status"], "done_ok")

    def test_lost_hangup_closed_by_watchdog(self):
        """Если событие Hangup потеряно — зависшая озвучка закрывается watchdog'ом."""
        _, _, call_id = self._mk()
        old = (datetime.datetime.now() - datetime.timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S")
        db.q("UPDATE calls SET status='talk', started_at=? WHERE id=?", (old, call_id))
        self.eng._watchdog()
        call = self._call(call_id)
        self.assertEqual(call["result"], "timeout")
        self.assertIn("Озвучка не завершена", call["detail"])

    def test_inbound_logged_answered_and_finished(self):
        """Входящий с DID: журнал, поиск контакта по 10 цифрам, номер пула, финал."""
        now = config.now_iso()
        db.q("DELETE FROM contacts WHERE phone IN ('+79863149340','79863149340')")
        db.q("DELETE FROM numbers WHERE number='+79863149340'")
        db.insert("contacts", {"name": "Иван Петров", "phone": "+79863149340", "consent": 1,
                               "created": now, "updated": now})
        num_id = db.insert("numbers", {"number": "+79863149340", "label": "MCM", "kind": "mobile",
                                       "provider": "ami", "active": 1, "daily_limit": 200})
        ch = "PJSIP/mcm-00000099"
        db.q("DELETE FROM calls WHERE external_call_id=?", (ch,))
        self.eng.handle_event({"provider": "ami", "event": "inbound", "channel": ch,
                               "phone": "9863149340", "did": "9863149340"})
        call = db.fetch1("SELECT * FROM calls WHERE external_call_id=?", (ch,))
        self.assertTrue(call, "входящий должен попасть в журнал")
        self.assertEqual(call["direction"], "in")
        self.assertEqual(call["status"], "ringing")
        self.assertEqual(call["contact_name"], "Иван Петров")
        self.assertEqual(call["number_id"], num_id)
        self.assertEqual(call["provider"], "ami")
        # дубль события не создаёт второй звонок
        self.eng.handle_event({"provider": "ami", "event": "inbound", "channel": ch,
                               "phone": "9863149340", "did": "9863149340"})
        self.assertEqual(len(db.fetch("SELECT id FROM calls WHERE external_call_id=?", (ch,))), 1)
        self.eng.handle_event({"provider": "ami", "event": "inbound_answer", "channel": ch})
        call = self._call(call["id"])
        self.assertEqual(call["status"], "answered")
        self.assertTrue(call["answered_at"])
        self.eng.handle_event({"provider": "ami", "event": "inbound_end", "channel": ch,
                               "detail": "16"})
        call = self._call(call["id"])
        self.assertEqual((call["status"], call["result"]), ("done", "done_ok"))

    def test_missed_inbound_becomes_callback_task(self):
        now = config.now_iso()
        ch = "PJSIP/mcm-00000077"
        db.q("DELETE FROM calls WHERE external_call_id=?", (ch,))
        self.eng.handle_event({"provider": "ami", "event": "inbound", "channel": ch,
                               "phone": "9362574897", "did": "9362574897"})
        call = db.fetch1("SELECT * FROM calls WHERE external_call_id=?", (ch,))
        self.assertEqual(call["contact_name"], "", "неизвестный номер — без контакта")
        self.eng.handle_event({"provider": "ami", "event": "inbound_end", "channel": ch})
        call = self._call(call["id"])
        self.assertEqual((call["status"], call["result"]), ("done", "missed"))
        self.assertIn("Пропущенный", call["detail"])


class RecordsLinkTest(unittest.TestCase):
    def setUp(self):
        now = config.now_iso()
        self.call_id = db.insert("calls", {"contact_phone": "79690229926", "provider": "ami",
                                           "direction": "out", "status": "done",
                                           "started_at": now, "ended_at": now})
        s = db.get_settings()
        self._old = dict(s.get("records") or {})
        s["records"] = dict(config.DEFAULT_SETTINGS["records"], base_url="https://ats.example.ru",
                            link_ttl_hours=48, link_secret="topsecret")
        db.save_settings(s)

    def tearDown(self):
        s = db.get_settings()
        s["records"] = self._old or dict(config.DEFAULT_SETTINGS["records"])
        db.save_settings(s)

    def test_no_file_no_link(self):
        self.assertEqual(records.link(self.call_id), "")

    def test_link_sign_and_verify(self):
        db.q("UPDATE calls SET recording=? WHERE id=?", ("call_1_good.wav", self.call_id))
        link = records.link(self.call_id)
        self.assertTrue(link.startswith("https://ats.example.ru/api/v2/records/"))
        self.assertIn("exp=", link)
        query = dict(p.split("=", 1) for p in link.split("?", 1)[1].split("&"))
        self.assertTrue(records.verify(self.call_id, query["exp"], query["sig"]))
        self.assertFalse(records.verify(self.call_id, query["exp"], "0" * 64))
        past = int(datetime.datetime.now().timestamp()) - 10
        self.assertFalse(records.verify(self.call_id, past, records._sign(self.call_id, past,
                                                                          "topsecret")))

    def test_ttl_zero_disables_links(self):
        db.q("UPDATE calls SET recording=? WHERE id=?", ("call_1_good.wav", self.call_id))
        s = db.get_settings()
        s["records"] = dict(s["records"], link_ttl_hours=0, base_url="")
        db.save_settings(s)
        self.assertEqual(records.link(self.call_id), "")

    def test_path_for_blocks_traversal(self):
        db.q("UPDATE calls SET recording=? WHERE id=?", ("../../etc/passwd", self.call_id))
        self.assertIsNone(records.path_for(self.call_id))
        db.q("UPDATE calls SET recording=? WHERE id=?", ("call_1_missing.wav", self.call_id))
        self.assertIsNone(records.path_for(self.call_id))
        (config.REC_DIR / "call_1_missing.wav").write_bytes(b"data")
        self.assertTrue(str(records.path_for(self.call_id)).endswith("call_1_missing.wav"))

    def test_secret_autogenerated_and_hidden(self):
        s = db.get_settings()
        s["records"] = dict(config.DEFAULT_SETTINGS["records"], link_secret="",
                            link_secret_env="ATS_RECORDS_UNSET_ENV")
        os.environ.pop("ATS_RECORDS_UNSET_ENV", None)
        db.save_settings(s)
        secret = records.link_secret(db.get_settings())
        self.assertTrue(len(secret) >= 32)
        self.assertEqual(db.get_settings()["records"].get("link_secret"), secret,
                         "секрет должен сохраниться — ссылки живы после рестарта")
        self.assertNotIn("records", db.public_settings(), "секретный блок наружу не отдаётся")


def _load_deploy(name, rel):
    import importlib.util
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), rel)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class AmiCheckTest(unittest.TestCase):
    """POST /api/v2/ami/check — диагностика без звонков."""

    def _settings(self, **ami):
        from app import api
        from app.telephony import ami_check
        s = db.get_settings()
        base = dict(config.DEFAULT_SETTINGS["ami"])
        base.update({"host": "127.0.0.1", "port": 1, "user": "ats", "secret": "s",
                     "trunk": "mcm"})
        base.update(ami)
        return api, ami_check, dict(s, ami=base)

    def test_missing_credentials(self):
        from app.telephony import ami_check
        rep = ami_check({"ami": {"host": "", "user": "", "secret": ""}})
        self.assertFalse(rep["ok"])
        self.assertEqual(rep["checks"][0]["name"], "config")

    def test_unreachable_reports_clearly(self):
        api, ami_check, s = self._settings()
        rep = ami_check(s)
        self.assertEqual(rep.get("error"), "ami_unreachable")
        self.assertFalse(rep["ok"])
        self.assertIn("Connection refused", rep["checks"][0]["detail"])
        payload, code = api._ami_check_route({"ami": {"host": "127.0.0.1", "port": 1,
                                                      "user": "ats", "secret": "s"}})
        self.assertEqual(code, 200)          # отчёт, а не HTTP-ошибка: UI показывает чек-лист
        self.assertEqual(payload["error"], "ami_unreachable")

    def test_numbers_pool_check_present(self):
        api, ami_check, s = self._settings()
        names = [c["name"] for c in ami_check(s)["checks"]]
        self.assertIn("ami_login", names)     # дальше проверки не идут без связи

    def test_masked_secret_not_overridden(self):
        from app import api as api_mod
        s = db.get_settings()
        s["ami"] = dict(config.DEFAULT_SETTINGS["ami"], host="127.0.0.1", port=1,
                        user="ats", secret="real-secret", trunk="mcm")
        db.save_settings(s)
        payload, code = api_mod._ami_check_route({"ami": {"secret": "***", "port": 1}})
        self.assertEqual(code, 200)
        self.assertEqual(db.get_settings()["ami"]["secret"], "real-secret",
                         "маска не должна затирать секрет в БД")


class _NullEngine:
    def reload_settings(self):
        pass


class SettingsRoundtripTest(unittest.TestCase):
    """Новые секции (ami-транк, records) и message_max_sec должны сохраняться и
    не терять секреты при masked-редактировании в UI."""

    def setUp(self):
        from app import api
        self._old_engine = api.ENGINE
        api.ENGINE = _NullEngine()

    def tearDown(self):
        from app import api
        api.ENGINE = self._old_engine

    def test_raw_roundtrip_keeps_ami_and_records(self):
        from app import api
        s = db.get_settings()
        s["provider"] = "ami"
        s["ami"] = dict(config.DEFAULT_SETTINGS["ami"], host="10.0.0.9", trunk="mcm",
                        tech="PJSIP", number_format="ru8", caller_id_format="d10",
                        user="ats", secret="AMI_SECRET_1")
        s["records"] = dict(config.DEFAULT_SETTINGS["records"], base_url="https://ats.tst",
                            link_ttl_hours=12, link_secret="REC_SECRET_1")
        db.save_settings(s)
        got, code = api._route("GET", "/api/v2/settings/raw", {}, {"X-Ats-Token": "unused"})
        # без токена — 401; проверяем сам механизм сохранения через _route с сессией нельзя
        # (нужна живая сессия), поэтому дёргаем внутренние функции напрямую:
        body = {"provider_config": {"ami": {k: v for k, v in s["ami"].items()},
                                    "records": dict(s["records"], link_secret="********")}}
        from app import security
        tok = security.create_token("admin", "admin")
        headers = {"X-Ats-Token": tok}
        payload, code = api._route("POST", "/api/v2/settings/raw", body, headers)
        self.assertEqual(code, 200)
        cur = db.get_settings()
        self.assertEqual(cur["ami"]["number_format"], "ru8")
        self.assertEqual(cur["records"]["link_ttl_hours"], 12)
        self.assertEqual(cur["records"]["link_secret"], "REC_SECRET_1",
                         "маска не должна затирать сохранённый секрет")
        # и в masked-выводе секрет не виден
        payload, code = api._route("GET", "/api/v2/settings/raw", {}, headers)
        self.assertNotIn("REC_SECRET_1", json.dumps(payload, ensure_ascii=False))
        self.assertEqual(payload["provider_config"]["ami"]["number_format"], "ru8")

    def test_settings_save_accepts_message_max_sec(self):
        from app import api, security
        tok = security.create_token("admin", "admin")
        payload, code = api._route("POST", "/api/v2/settings/save",
                                   {"message_max_sec": 120, "records": {"link_ttl_hours": 24}},
                                   {"X-Ats-Token": tok})
        self.assertEqual(code, 200)
        cur = db.get_settings()
        self.assertEqual(cur["message_max_sec"], 120)
        self.assertEqual(cur["records"]["link_ttl_hours"], 24)
        self.assertIn("link_secret", cur["records"])


class FakeAsteriskAMI:
    """Миниатюрный Asterisk по AMI: Login/Ping/Command(Follows-блоком)/Logoff.

    Ответ `Command` отдаётся ровно в том виде, в каком его шлёт реальный
    Asterisk (AMI 2/3): сначала заголовок с пустой строкой, затем текст вывода
    и `--END COMMAND--` — то есть пары «Key: value» только в заголовке.
    """

    def __init__(self, outputs):
        import socket as _s
        import threading as _th
        self.outputs = outputs
        self._s = _s
        self._th = _th
        self.srv = _s.socket(_s.AF_INET, _s.SOCK_STREAM)
        self.srv.setsockopt(_s.SOL_SOCKET, _s.SO_REUSEADDR, 1)
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(4)
        self.port = self.srv.getsockname()[1]
        self.conns = []
        self._stop = _th.Event()

    def start(self):
        self._th.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while not self._stop.is_set():
            try:
                c, _ = self.srv.accept()
            except OSError:
                return
            self.conns.append(c)
            self._th.Thread(target=self._handle, args=(c,), daemon=True).start()

    def _handle(self, c):
        buf = b""
        c.sendall(b"Asterisk Call Manager/6.0.1\r\n\r\n")
        while not self._stop.is_set():
            try:
                data = c.recv(4096)
            except OSError:
                return
            if not data:
                return
            buf += data
            while b"\r\n\r\n" in buf:
                pkt, buf = buf.split(b"\r\n\r\n", 1)
                head = {}
                for ln in pkt.decode("utf-8", "ignore").split("\r\n"):
                    k, _, v = ln.partition(":")
                    head[k.strip()] = v.strip()
                aid = head.get("ActionID", "1")
                act = head.get("Action", "")
                if act == "Command":
                    out = self.outputs.get(head.get("Command", ""), "No such command")
                    c.sendall(("Response: Follows\r\nPrivilege: Command\r\n\r\n"
                               + out + "\r\n--END COMMAND--\r\n\r\n").encode("utf-8"))
                    continue
                if act == "Login" and head.get("Secret") != "smokesecret":
                    c.sendall(("ActionID: {}\r\nResponse: Error\r\nMessage: Authentication "
                               "failed\r\n\r\n").format(aid).encode("utf-8"))
                    continue
                c.sendall(("ActionID: {}\r\nResponse: Success\r\nMessage: ok\r\n\r\n"
                           .format(aid)).encode("utf-8"))

    def stop(self):
        self._stop.set()
        for c in self.conns:
            try:
                c.close()
            except OSError:
                pass
        try:
            self.srv.close()
        except OSError:
            pass


class AmiCheckLiveTest(unittest.TestCase):
    """Чек-лист ami_check против протокольного фейка Asterisk (тот же формат вывода)."""

    OUTPUTS = {
        "core show version": "Asterisk 20.7.1~dfsg, built by pbuilder",
        "pjsip show contacts": "  Contact:  <Aor/ContactUri>\n"
                               "  Contact:  mcm/sip:00083819@95.128.224.47:5060  Avail",
        "pjsip show endpoints": "  Endpoint: <Aor>\n  Endpoint: mcm mcm-aor",
        "dialplan show ats-out": "[ Context 'ats-out' ]\n  's' => 1 NoOp(ATS out)",
        "dialplan show ats-play": "[ Context 'ats-play' ]\n  's' => 1 AGI(ats_say.py)",
        "module show like codec_g729": "  Modules like 'codec_g729' => 0 loaded",
    }

    def setUp(self):
        now = config.now_iso()
        db.q("DELETE FROM numbers WHERE number='+79690229926'")
        db.insert("numbers", {"number": "+79690229926", "label": "MCM", "kind": "mobile",
                              "provider": "ami", "active": 1, "daily_limit": 120,
                              "quarantined": 0})
        self.srv = FakeAsteriskAMI(self.OUTPUTS)
        self.srv.start()

    def tearDown(self):
        self.srv.stop()

    def _check(self, **over):
        from app.telephony import ami_check
        cfg = dict(config.DEFAULT_SETTINGS["ami"])
        cfg.update({"host": "127.0.0.1", "port": self.srv.port, "user": "ats",
                    "secret": "smokesecret", "trunk": "mcm", "tech": "PJSIP",
                    "number_format": "ru8", "caller_id_format": "d10"})
        cfg.update(over)
        return ami_check({"ami": cfg})

    def test_all_critical_checks_pass(self):
        rep = self._check()
        by_name = {c["name"]: c for c in rep["checks"]}
        self.assertTrue(rep["ok"], json.dumps(rep["checks"], ensure_ascii=False)[:600])
        self.assertTrue(by_name["ami_login"]["ok"])
        self.assertTrue(by_name["registration"]["ok"])
        self.assertIn("95.128.224.47", by_name["registration"]["detail"])
        self.assertTrue(by_name["dialplan"]["ok"])
        self.assertTrue(by_name["numbers_pool"]["ok"])

    def test_missing_context_fails_dialplan(self):
        self.srv.outputs = dict(self.OUTPUTS, **{"dialplan show ats-out":
                                                  "No such context 'ats-out'."})
        rep = self._check()
        by_name = {c["name"]: c for c in rep["checks"]}
        self.assertFalse(by_name["dialplan"]["ok"])
        self.assertFalse(rep["ok"])

    def test_unregistered_trunk_fails_registration(self):
        self.srv.outputs = dict(self.OUTPUTS, **{"pjsip show contacts":
                                                 "  Contact:  <Aor/ContactUri>\n  0 contacts"})
        rep = self._check()
        by_name = {c["name"]: c for c in rep["checks"]}
        self.assertFalse(by_name["registration"]["ok"])

    def test_bad_credentials_reported(self):
        rep = self._check(secret="wrong")
        self.assertEqual(rep.get("error"), "ami_unreachable")
        self.assertFalse(rep["ok"])


class ListenResolutionTest(unittest.TestCase):
    """ATS_HOST/ATS_PORT из env (systemd EnvironmentFile, run_ats2.sh) учитываются."""

    def test_precedence(self):
        from app.run import resolve_listen
        keys = ("ATS_HOST", "ATS_PORT")
        saved = {k: os.environ.get(k) for k in keys}
        try:
            os.environ.pop("ATS_HOST", None)
            os.environ.pop("ATS_PORT", None)
            self.assertEqual(resolve_listen({}), ("0.0.0.0", config.PORT_DEFAULT))
            self.assertEqual(resolve_listen({"host": "127.0.0.1", "port": 9010}),
                             ("127.0.0.1", 9010))
            os.environ["ATS_PORT"] = "9793"
            self.assertEqual(resolve_listen({"port": 9010})[1], 9793, "env важнее настроек")
            self.assertEqual(resolve_listen({"port": 9010}, None, 9500)[1], 9500,
                             "CLI-флаг важнее env")
            os.environ["ATS_PORT"] = "не-число"
            self.assertEqual(resolve_listen({"port": 9010})[1], 9010, "мусор в env — не падать")
            os.environ["ATS_PORT"] = "99999"
            self.assertEqual(resolve_listen({})[1], config.PORT_DEFAULT)
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


class AsteriskSideScriptsTest(unittest.TestCase):
    """Скрипты, которые ставятся на сторону Asterisk (deploy/asterisk/)."""

    def setUp(self):
        self.attach = _load_deploy("ats_attach", "deploy/asterisk/attach_recordings.py")
        self.say = _load_deploy("ats_say", "deploy/asterisk/agi/ats_say.py")

    def test_recording_name_mapping(self):
        self.assertEqual(self.attach.parse_name("call-123.wav"), (123, ""))
        self.assertEqual(self.attach.parse_name("play-7.opus"), (7, ""))
        self.assertEqual(self.attach.parse_name("in-PJSIP_mcm-0000000a.wav"),
                         (0, "PJSIP/mcm-0000000a"))
        self.assertEqual(self.attach.parse_name("zzz-1.wav"), (0, ""))

    def test_only_ready_files_are_picked(self):
        import time as _t
        tmp = tempfile.mkdtemp(prefix="ats_rec_")
        for name in ("call-1.wav", "call-2.wav", "in-PJSIP_mcm-1.wav", "other.wav"):
            with open(os.path.join(tmp, name), "wb") as fh:
                fh.write(b"x" * 200)
        os.mkdir(os.path.join(tmp, "nested"))
        old = _t.time() - 600
        os.utime(os.path.join(tmp, "call-1.wav"), (old, old))       # готов
        os.utime(os.path.join(tmp, "call-2.wav"), (old, old))
        os.truncate(os.path.join(tmp, "call-2.wav"), 10)             # пустой — не берём
        os.utime(os.path.join(tmp, "in-PJSIP_mcm-1.wav"), (_t.time(), _t.time()))  # свежий
        files = self.attach.candidate_files(tmp, min_age_sec=20)
        self.assertEqual([os.path.basename(f) for f in files], ["call-1.wav"])

    def test_script_matches_ats_api_contract(self):
        """Скрипт должен говорить с тем API, которое реально принимает ATS."""
        with open(self.attach.__file__, encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn('add_header("X-Ats-Token", token)', src)
        self.assertIn('"/api/v2/auth/login"', src)
        self.assertIn('"/api/v2/calls/recording"', src)
        self.assertIn('"external_call_id"', src)

    def test_env_file_loading(self):
        tmp = tempfile.mkdtemp(prefix="ats_env_")
        path = os.path.join(tmp, "ats.env")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("# комментарий\nATS_URL=\"http://127.0.0.1:9199\"\n\nATS_LOGIN=rec\n")
        os.environ.pop("ATS_URL", None)
        os.environ.pop("ATS_LOGIN", None)
        self.attach.load_env([path])
        self.assertEqual(os.environ.get("ATS_URL"), "http://127.0.0.1:9199")
        self.assertEqual(os.environ.get("ATS_LOGIN"), "rec")
        os.environ.pop("ATS_URL", None)
        os.environ.pop("ATS_LOGIN", None)

    def test_agi_text_cleaning_and_quoting(self):
        self.assertEqual(self.say.clean_text('   вет\r\n "миp"\tэто  ats  '),
                         "вет 'миp' это ats")
        self.assertEqual(self.say.clean_text("a" * 5000)[:1], "a")
        self.assertEqual(len(self.say.clean_text("a" * 5000)), self.say.MAX_CHARS)
        cmd = self.say.encode_for_cmd("/bin/echo {out} {text}", "/tmp/а б.wav", 'привет; rm -rf /')
        self.assertEqual(cmd[0], "sh")
        self.assertNotIn("rm -rf / ", cmd[2] + " ")   # только как аргумент echo, без разрыва
        self.assertIn("'/tmp/а б.wav'", cmd[2])

    def test_agi_protocol_exchange(self):
        import io
        stdin = io.StringIO("AGI_REQUEST: agi/entry\nAGI_CHANNEL: PJSIP/1-1\n"
                            "AGI_ANSWERED: true\n\n")
        stdout = io.StringIO()
        agi = self.say.Agi(stdin=stdin, stdout=stdout)
        params = agi.read_params()
        self.assertEqual(params["agi_channel"], "PJSIP/1-1")
        self.assertEqual(params["agi_answered"], "true")
        self.assertEqual(stdout.getvalue(), "")     # на чтение ничего не пишем
        agi.set_var("ATS_PLAY", "ok")
        self.assertEqual(stdout.getvalue(), 'SET VARIABLE ATS_PLAY "ok"\n\n')

    def test_agi_reply_parsing(self):
        import io
        agi = self.say.Agi(stdin=io.StringIO("200 result=1 (9)\n"), stdout=io.StringIO())
        reply = agi._read_reply()
        self.assertEqual(reply, {"code": 200, "result": "1", "item": "9"})

    def test_recording_attach_by_external_call_id(self):
        """Файл входящего привязывается по имени канала (external_call_id)."""
        from app import api
        now = config.now_iso()
        ch = "PJSIP/mcm-00000123"
        db.q("DELETE FROM calls WHERE external_call_id=?", (ch,))
        call_id = db.insert("calls", {"contact_phone": "9362962622", "provider": "ami",
                                      "external_call_id": ch, "direction": "in",
                                      "status": "done", "started_at": now, "ended_at": now})
        import base64 as b64
        body = {"external_call_id": ch, "filename": "in-PJSIP_mcm-00000123.wav",
                "data_b64": b64.b64encode(b"RIFF" + b"x" * 100).decode("ascii")}
        payload, code = api._recording_upload(body)
        self.assertEqual((code, payload.get("ok")), (200, True))
        row = db.fetch1("SELECT recording FROM calls WHERE id=?", (call_id,))
        self.assertTrue(row["recording"])
        # неизвестный канал → отказ, чужой файл в БД не осиротевает
        bad = dict(body, external_call_id="PJSIP/mcm-9999")
        payload, code = api._recording_upload(bad)
        self.assertEqual((code, payload["error"]), (400, "call_id_required"))
        del call_id


if __name__ == "__main__":
    unittest.main()
