# -*- coding: utf-8 -*-
"""МегаФон ВАТС, стадия 2: вебхук, нормализация, идемпотентность, входящие,
history/rating, полный E2E-цикл через mock ВАТС.

Запуск: python -m unittest discover -s tests -v   (из корня репозитория)
"""
import datetime
import json
import os
import tempfile
import threading
import unittest
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer

_TMP = tempfile.mkdtemp(prefix="ats_test_mfwh_")
os.environ["ATS_FAST"] = "1"
os.environ["ATS_DATA_DIR"] = _TMP
os.environ["ATS_ADMIN_PASSWORD"] = "TestAdmin123!"
os.environ["ATS_DEV_SEED"] = "1"

from app import api, config, db, events  # noqa: E402
from app.engine import Engine  # noqa: E402
from app.providers.megafon_vats import (  # noqa: E402
    MEGAFON_HISTORY_MAP, map_megafon_webhook, parse_vats_start,
    phone_variants, webhook_fingerprint)
from app.server import Handler  # noqa: E402
from tests.test_megafon_vats import FakeMegaFonVatsServer  # noqa: E402

db.init_db()


def _norm(form):
    ev = map_megafon_webhook(form)
    assert ev is not None, form
    ev["fingerprint"] = webhook_fingerprint(ev)
    return ev


class MapTest(unittest.TestCase):
    def test_event_map(self):
        ev = _norm({"cmd": "event", "type": "INCOMING", "callid": "1",
                    "phone": "79260000001", "direction": "in", "user": "admin"})
        self.assertEqual(ev["event"], "ring")
        self.assertEqual(ev["direction"], "in")
        self.assertEqual(ev["raw_type"], "INCOMING")
        out = _norm({"cmd": "event", "type": "OUTGOING", "callid": "2",
                     "phone": "79260000002", "direction": "out"})
        self.assertEqual(out["event"], "ring")
        self.assertEqual(_norm({"cmd": "event", "type": "ACCEPTED", "callid": "3",
                                "phone": "7", "direction": "out"})["event"], "answered")
        self.assertEqual(_norm({"cmd": "event", "type": "COMPLETED", "callid": "4",
                                "phone": "7", "direction": "out"})["event"], "done")
        self.assertEqual(_norm({"cmd": "event", "type": "CANCELLED", "callid": "5",
                                "phone": "7", "direction": "in"})["event"], "canceled")
        tr = _norm({"cmd": "event", "type": "TRANSFERRED", "callid": "6",
                    "phone": "7", "direction": "in", "second_callid": "7"})
        self.assertEqual((tr["event"], tr["second_callid"]), ("transferred", "7"))

    def test_direction_inferred(self):
        ev = _norm({"cmd": "event", "type": "INCOMING", "callid": "1", "phone": "7"})
        self.assertEqual(ev["direction"], "in")

    def test_unknown_and_empty(self):
        self.assertIsNone(map_megafon_webhook({"cmd": "event", "type": "NOPE",
                                               "callid": "1", "phone": "7"}))
        self.assertIsNone(map_megafon_webhook({"cmd": "contact", "phone": "7",
                                               "callid": "1"}))
        self.assertIsNone(map_megafon_webhook({"cmd": "event", "type": "INCOMING",
                                               "phone": "7"}))
        self.assertIsNone(map_megafon_webhook({"cmd": "bogus"}))
        self.assertIsNone(map_megafon_webhook({}))

    def test_history_status_case_insensitive(self):
        h = _norm({"cmd": "history", "type": "out", "status": "Success",
                   "callid": "1", "phone": "7", "user": "u", "start": "20220120T085842Z",
                   "duration": "23", "wait": "5"})
        self.assertEqual(h["status"], "success")
        self.assertEqual(h["duration"], 23)
        self.assertEqual(h["wait"], 5)
        h2 = _norm({"cmd": "history", "type": "in", "status": "missed",
                    "callid": "2", "phone": "7", "user": "u",
                    "start": "20220120T085842Z", "duration": "0", "wait": "13",
                    "missedStatus": "3", "link": "https://r/file.mp3"})
        self.assertEqual((h2["status"], h2["missed_status"], h2["record_url"]),
                         ("missed", "3", "https://r/file.mp3"))
        for raw in ("Success", "missed", "Cancel", "Busy", "NotAvailable",
                    "NotAllowed", "NotFound"):
            self.assertIn(raw.lower(), MEGAFON_HISTORY_MAP)

    def test_rating_and_provider_webhook(self):
        r = _norm({"cmd": "rating", "callid": "1", "phone": "7", "rating": "4",
                   "user": "u"})
        self.assertEqual((r["event"], r["rating"]), ("rating", 4))
        w = _norm({"cmd": "webhook", "type": "sipregs_error", "callid": "",
                   "id": "x"})
        self.assertEqual(w["event"], "provider_webhook")
        self.assertEqual(w["webhook_type"], "sipregs_error")

    def test_parse_vats_start(self):
        self.assertRegex(parse_vats_start("20220120T085842Z"),
                         r"2022-01-20 \d\d:\d\d:\d\d")
        self.assertEqual(parse_vats_start("мусор"), "")
        self.assertEqual(parse_vats_start(""), "")

    def test_phone_variants(self):
        v = phone_variants("+7 (926) 000-00-01")
        self.assertIn("79260000001", v)
        self.assertIn("+79260000001", v)
        self.assertIn("89260000001", v)

    def test_fingerprint_stable(self):
        f1 = {"cmd": "event", "type": "ACCEPTED", "callid": "10", "phone": "77",
              "direction": "out", "crm_token": "S"}
        f2 = dict(f1)
        self.assertEqual(webhook_fingerprint(_norm(f1)), webhook_fingerprint(_norm(f2)))
        f3 = dict(f1, callid="11")
        self.assertNotEqual(webhook_fingerprint(_norm(f1)), webhook_fingerprint(_norm(f3)))


class _FakeEngine:
    def __init__(self):
        self.events = []

    def push_event(self, ev):
        self.events.append(ev)


class HttpWebhookTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._old_engine = api.ENGINE
        cls.fake = _FakeEngine()
        api.ENGINE = cls.fake
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.httpd.daemon_threads = True
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.base = "http://127.0.0.1:{}".format(cls.httpd.server_port)

    @classmethod
    def tearDownClass(cls):
        api.ENGINE = cls._old_engine
        try:
            cls.httpd.shutdown()
        except Exception:
            pass
        try:
            cls.httpd.server_close()
        except Exception:
            pass

    def setUp(self):
        del self.fake.events[:]
        s = db.get_settings()
        self._old_mf = s.get("megafon_vats")
        s["megafon_vats"] = {"crm_token": "SECRET", "api_key": "x",
                             "base_url": "http://x"}
        db.save_settings(s)
        db.insert("contacts", {"name": "Иван", "phone": "79260000001", "grp": "",
                               "note": "", "consent": 1, "consent_source": "t",
                               "blacklisted": 0, "complaints": 0,
                               "created": config.now_iso(), "updated": config.now_iso()})

    def tearDown(self):
        s = db.get_settings()
        if self._old_mf is None:
            s.pop("megafon_vats", None)
        else:
            s["megafon_vats"] = self._old_mf
        db.save_settings(s)
        db.q("DELETE FROM contacts WHERE phone='79260000001'")

    def _post(self, form):
        data = urllib.parse.urlencode(form).encode("utf-8")
        req = urllib.request.Request(
            self.base + "/api/v2/webhooks/megafon", data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8") or "{}")

    def test_form_event_queued(self):
        code, payload = self._post({"cmd": "event", "type": "INCOMING",
                                    "callid": "33274237", "phone": "79260000001",
                                    "direction": "in", "user": "admin",
                                    "crm_token": "SECRET"})
        self.assertEqual(code, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(len(self.fake.events), 1)
        ev = self.fake.events[0]
        self.assertEqual((ev["provider"], ev["event"], ev["external_call_id"]),
                         ("megafon_vats", "ring", "33274237"))
        self.assertTrue(ev["fingerprint"])

    def test_bad_and_missing_secret(self):
        code, _ = self._post({"cmd": "event", "type": "INCOMING", "callid": "1",
                              "phone": "7", "crm_token": "WRONG"})
        self.assertEqual(code, 403)
        code, _ = self._post({"cmd": "event", "type": "INCOMING", "callid": "1",
                              "phone": "7"})
        self.assertEqual(code, 403)
        self.assertEqual(self.fake.events, [])

    def test_webhook_disabled_without_token(self):
        s = db.get_settings()
        s["megafon_vats"] = {"crm_token": "", "crm_token_env": "ATS_MFWH_NOPE_UNSET"}
        db.save_settings(s)
        if "ATS_MFWH_NOPE_UNSET" in os.environ:
            del os.environ["ATS_MFWH_NOPE_UNSET"]
        code, payload = self._post({"cmd": "event", "type": "INCOMING",
                                    "callid": "1", "phone": "7",
                                    "crm_token": "anything"})
        self.assertEqual(code, 403)
        self.assertEqual(payload["error"], "webhook_disabled")

    def test_contact_found_and_unknown(self):
        code, payload = self._post({"cmd": "contact", "phone": "+7(926)000-00-01",
                                    "callid": "1", "crm_token": "SECRET"})
        self.assertEqual(code, 200)
        self.assertEqual(payload, {"contact_name": "Иван"})
        self.assertEqual(self.fake.events, [])  # contact — синхронно, без очереди
        code, payload = self._post({"cmd": "contact", "phone": "79000000000",
                                    "callid": "2", "crm_token": "SECRET"})
        self.assertEqual(payload, {"contact_name": ""})

    def test_unrecognized(self):
        code, payload = self._post({"cmd": "bogus", "crm_token": "SECRET"})
        self.assertEqual(code, 422)


class EngineMegafonTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import pathlib as _pl
        cls._fresh = _pl.Path(tempfile.mkdtemp(prefix="ats_mfwh_"))
        try:
            if db._conn is not None:
                db._conn.close()
        except Exception:
            pass
        db._conn = None
        config.DB_PATH = cls._fresh / "mfwh.db"
        db.init_db()
        cls.vats = FakeMegaFonVatsServer()
        s = db.get_settings()
        s["provider"] = "megafon_vats"
        s["megafon_vats"] = {"base_url": cls.vats.url, "api_key": "test-key",
                             "crm_token": "SECRET", "default_user": "admin",
                             "timeout_sec": 5}
        s["retry_max"] = 2
        db.save_settings(s)
        cls.engine = Engine(auto_start=False)

    @classmethod
    def tearDownClass(cls):
        cls.vats.stop()

    def _mkcontact(self, phone, name="Клиент"):
        return db.insert("contacts", {"name": name, "phone": phone, "grp": "",
                                      "note": "", "consent": 1, "consent_source": "t",
                                      "blacklisted": 0, "complaints": 0,
                                      "created": config.now_iso(), "updated": config.now_iso()})

    def _mknumber(self, number="79262005060"):
        row = db.fetch1("SELECT id FROM numbers WHERE number=?", (number,))
        if row:
            db.q("UPDATE numbers SET cooldown_until='' WHERE id=?", (row["id"],))
            return row["id"]
        return db.insert("numbers", {"number": number, "label": "", "kind": "mobile",
                                     "provider": "megafon_vats", "active": 1,
                                     "daily_limit": 100, "weight": 1, "quarantined": 0,
                                     "cooldown_until": "", "daily_date": "",
                                     "daily_count": 0, "dialed_total": 0,
                                     "answered_total": 0, "created": config.now_iso()})

    def _mkcamp(self, flow="operator", name="MF"):
        return db.insert("campaigns", {
            "name": name, "template_id": 0, "flow": flow, "status": "running",
            "schedule": json.dumps({"start": "00:00", "end": "23:59",
                                    "days": [0, 1, 2, 3, 4, 5, 6]}),
            "max_channels": 1, "retry_max": 2, "retry_delay_min": 1,
            "connect_on_qualify": 1, "created": config.now_iso(),
            "updated": config.now_iso()})

    def _mkitem(self, camp, contact_id, phone):
        return db.insert("campaign_items", {
            "campaign_id": camp, "contact_id": contact_id, "contact_name": "Клиент",
            "contact_phone": phone, "status": "queued", "attempts": 0,
            "next_attempt_at": "", "last_result": "", "created": config.now_iso(),
            "updated": config.now_iso(), "completed_at": ""})

    def _dial(self, phone, flow="operator"):
        self._mkcontact(phone)
        self._mknumber()
        camp = self._mkcamp(flow, "MF-" + phone)
        cid_c = db.fetch1("SELECT id FROM contacts WHERE phone=?", (phone,))["id"]
        self._mkitem(camp, cid_c, phone)
        self.engine.tick_once()
        call = db.fetch1("SELECT * FROM calls WHERE contact_phone=? ORDER BY id DESC",
                         (phone,))
        assert call, "дозвон не стартовал"
        return camp, call

    def _crm_has(self, phone):
        path = config.CRM_OUT_DIR / "crm_push_{}.csv".format(
            datetime.date.today().isoformat())
        if not path.exists():
            return False
        return phone in path.read_text(encoding="utf-8-sig")

    # --- E2E (§59 ТЗ): контакт→кампания→makecall→события→history→CRM, без дублей ---
    def test_e2e_outbound_operator(self):
        _camp, call = self._dial("79260000101")
        self.assertEqual(call["external_call_id"], "2015948553")
        self.assertEqual(call["caller_id"], "79262005060")
        ext = "2015948553"
        e = self.engine
        e.handle_event(_norm({"cmd": "event", "type": "OUTGOING", "callid": ext,
                              "phone": "79260000101", "direction": "out", "user": "admin"}))
        e.handle_event(_norm({"cmd": "event", "type": "ACCEPTED", "callid": ext,
                              "phone": "79260000101", "direction": "out", "user": "admin"}))
        call = db.fetch1("SELECT * FROM calls WHERE id=?", (call["id"],))
        self.assertEqual(call["status"], "wait_operator")
        acd1 = db.fetch("SELECT * FROM acd WHERE call_id=?", (call["id"],))
        self.assertEqual(len(acd1), 1)
        # повтор ACCEPTED — дубль, второй ACD-строки нет
        e.handle_event(_norm({"cmd": "event", "type": "ACCEPTED", "callid": ext,
                              "phone": "79260000101", "direction": "out", "user": "admin"}))
        self.assertEqual(len(db.fetch("SELECT * FROM acd WHERE call_id=?", (call["id"],))), 1)
        e.handle_event(_norm({"cmd": "event", "type": "COMPLETED", "callid": ext,
                              "phone": "79260000101", "direction": "out"}))
        e.handle_event(_norm({"cmd": "history", "type": "out", "status": "Success",
                              "callid": ext, "phone": "79260000101", "user": "admin",
                              "diversion": "79262005060", "start": "20220120T085842Z",
                              "duration": "45", "wait": "5",
                              "link": "https://rec/file.mp3"}))
        call = db.fetch1("SELECT * FROM calls WHERE id=?", (call["id"],))
        self.assertEqual((call["status"], call["result"]), ("done", "operator_ok"))
        self.assertEqual(call["recording_url"], "https://rec/file.mp3")
        self.assertEqual(call["duration_sec"], 45)
        self.assertEqual(call["external_status"], "Success")
        atts = db.fetch("SELECT * FROM attempts WHERE call_id=?", (call["id"],))
        self.assertEqual(len(atts), 1)  # 1 звонок, 0 дублей
        self.assertTrue(self._crm_has("79260000101"))
        sts = [r["status"] for r in db.fetch(
            "SELECT status FROM provider_events WHERE external_call_id=?", (ext,))]
        self.assertTrue(sts and all(s == "done" for s in sts))

    def test_inbound_cycle_no_flow(self):
        self._mkcontact("79260000102", "Петрова")
        e = self.engine
        stream = events.EventStream()
        try:
            e.handle_event(_norm({"cmd": "event", "type": "INCOMING", "callid": "5001",
                                  "phone": "79260000102", "direction": "in",
                                  "diversion": "79264010121", "user": "manager"}))
            call = db.fetch1("SELECT * FROM calls WHERE external_call_id='5001'")
            self.assertEqual((call["direction"], call["status"]), ("in", "ringing"))
            self.assertEqual(call["contact_name"], "Петрова")
            e.handle_event(_norm({"cmd": "event", "type": "ACCEPTED", "callid": "5001",
                                  "phone": "79260000102", "direction": "in"}))
            call = db.fetch1("SELECT * FROM calls WHERE id=?", (call["id"],))
            self.assertEqual(call["status"], "answered")  # НЕ wait_operator, flow нет
            self.assertEqual(db.fetch("SELECT * FROM acd WHERE call_id=?", (call["id"],)), [])
            e.handle_event(_norm({"cmd": "event", "type": "COMPLETED", "callid": "5001",
                                  "phone": "79260000102", "direction": "in"}))
            call = db.fetch1("SELECT * FROM calls WHERE id=?", (call["id"],))
            self.assertEqual((call["status"], call["result"]), ("done", "done_ok"))
            got = []
            try:
                while True:
                    got.append(stream.q.get_nowait())
            except Exception:
                pass
            cards = [g for g in got if g["type"] == "call"
                     and g["payload"].get("incoming")]
            self.assertTrue(cards)  # карточка входящего ушла в SSE
        finally:
            stream.close()

    def test_inbound_missed_task(self):
        e = self.engine
        e.handle_event(_norm({"cmd": "event", "type": "INCOMING", "callid": "5002",
                              "phone": "79260000103", "direction": "in"}))
        e.handle_event(_norm({"cmd": "event", "type": "CANCELLED", "callid": "5002",
                              "phone": "79260000103", "direction": "in"}))
        call = db.fetch1("SELECT * FROM calls WHERE external_call_id='5002'")
        self.assertEqual(call["result"], "missed")
        tasks = config.CRM_OUT_DIR / "crm_tasks.csv"
        self.assertIn("79260000103", tasks.read_text(encoding="utf-8-sig"))

    def test_history_gap_finalizes_busy(self):
        _camp, call = self._dial("79260000104")
        e = self.engine
        e.handle_event(_norm({"cmd": "history", "type": "out", "status": "Busy",
                              "callid": call["external_call_id"],
                              "phone": "79260000104", "user": "admin",
                              "start": "20220120T085842Z", "duration": "0", "wait": "0"}))
        call = db.fetch1("SELECT * FROM calls WHERE id=?", (call["id"],))
        item = db.fetch1("SELECT * FROM campaign_items WHERE id=?", (call["item_id"],))
        self.assertEqual(call["result"], "busy")
        self.assertEqual(item["status"], "queued")  # ретрай по плану

    def test_message_flow_guard(self):
        _camp, call = self._dial("79260000105", flow="message")
        e = self.engine
        e.handle_event(_norm({"cmd": "event", "type": "ACCEPTED",
                              "callid": call["external_call_id"],
                              "phone": "79260000105", "direction": "out"}))
        call = db.fetch1("SELECT * FROM calls WHERE id=?", (call["id"],))
        item = db.fetch1("SELECT * FROM campaign_items WHERE id=?", (call["item_id"],))
        self.assertEqual(call["result"], "no_media")
        self.assertEqual(item["status"], "no_media")  # терминал, не done_ok

    def test_history_corrects_talk_result(self):
        _camp, call = self._dial("79260000106")
        e = self.engine
        ext = call["external_call_id"]
        e.handle_event(_norm({"cmd": "event", "type": "ACCEPTED", "callid": ext,
                              "phone": "79260000106", "direction": "out"}))
        e.handle_event(_norm({"cmd": "event", "type": "COMPLETED", "callid": ext,
                              "phone": "79260000106", "direction": "out"}))
        self.assertEqual(db.fetch1("SELECT * FROM calls WHERE id=?",
                                   (call["id"],))["result"], "operator_ok")
        # ВАТС говорит: направление запрещено — звонка не было
        e.handle_event(_norm({"cmd": "history", "type": "out", "status": "NotAllowed",
                              "callid": ext, "phone": "79260000106", "user": "admin",
                              "start": "20220120T085842Z", "duration": "0", "wait": "0"}))
        call = db.fetch1("SELECT * FROM calls WHERE id=?", (call["id"],))
        self.assertEqual(call["result"], "failed")
        item = db.fetch1("SELECT * FROM campaign_items WHERE id=?", (call["item_id"],))
        self.assertEqual(item["status"], "operator_ok")  # позицию не переоткрываем

    def test_rating_and_transferred(self):
        self._mkcontact("79260000107")
        e = self.engine
        e.handle_event(_norm({"cmd": "event", "type": "INCOMING", "callid": "5007",
                              "phone": "79260000107", "direction": "in"}))
        e.handle_event(_norm({"cmd": "event", "type": "TRANSFERRED", "callid": "5007",
                              "phone": "79260000107", "direction": "in",
                              "second_callid": "5008"}))
        e.handle_event(_norm({"cmd": "rating", "callid": "5007",
                              "phone": "79260000107", "rating": "5", "user": "manager"}))
        call = db.fetch1("SELECT * FROM calls WHERE external_call_id='5007'")
        self.assertEqual(call["rating"], 5)
        self.assertIn("5008", call["detail"])
        # orphan-rating не падает
        e.handle_event(_norm({"cmd": "rating", "callid": "nope",
                              "phone": "7", "rating": "3", "user": "u"}))

    def test_orphan_and_provider_webhook(self):
        e = self.engine
        e.handle_event(_norm({"cmd": "event", "type": "OUTGOING", "callid": "ghost",
                              "phone": "79260000108", "direction": "out"}))
        self.assertIsNone(db.fetch1("SELECT * FROM calls WHERE external_call_id='ghost'"))
        row = db.fetch1("SELECT * FROM provider_events WHERE external_call_id='ghost'")
        self.assertEqual(row["status"], "orphan")
        e.handle_event(_norm({"cmd": "webhook", "type": "sipregs_error",
                              "callid": "", "id": "x"}))
        row2 = db.fetch1("SELECT * FROM provider_events WHERE event_type='provider_webhook'")
        self.assertEqual(row2["status"], "done")
        self.assertIn("sipregs_error", row2["payload_json"])

    def test_history_creates_journal(self):
        e = self.engine
        self._mkcontact("79260000109", "Журналов")
        e.handle_event(_norm({"cmd": "history", "type": "in", "status": "missed",
                              "callid": "6001", "phone": "79260000109",
                              "user": "manager", "diversion": "79264010121",
                              "start": "20220120T085842Z", "duration": "0",
                              "wait": "13", "missedStatus": "3"}))
        call = db.fetch1("SELECT * FROM calls WHERE external_call_id='6001'")
        self.assertEqual((call["result"], call["contact_name"], call["wait_sec"]),
                         ("missed", "Журналов", 13))
        self.assertEqual(call["missed_status"], "3")
        self.assertTrue(self._crm_has("79260000109"))


if __name__ == "__main__":
    unittest.main()
