# -*- coding: utf-8 -*-
"""Тесты live-готовности МегаФон-потока:
- watchdog не убивает живые VATS-разговоры коротким ACD-таймаутом;
- страховочный таймаут VATS-разговоров (потеря финала);
- heal no_operator → operator_ok по ground truth ВАТС (COMPLETED/history);
- GET /api/v2/calls/<id>/timeline для live-forensics.

Запуск: python -m unittest discover -s tests -v   (из корня репозитория)
"""
import os
import tempfile
import unittest

_TMP = tempfile.mkdtemp(prefix="ats_test_live_")
os.environ["ATS_FAST"] = "1"
os.environ["ATS_DATA_DIR"] = _TMP
os.environ["ATS_ADMIN_PASSWORD"] = "TestAdmin123!"
os.environ["ATS_DEV_SEED"] = "1"

from app import api, config, db  # noqa: E402
from app.engine import Engine  # noqa: E402
from app.telephony import SimProvider  # noqa: E402

db.init_db()


def _campaign(flow="operator"):
    return db.insert("campaigns", {
        "name": "LIVE", "template_id": 0, "flow": flow, "status": "running",
        "schedule": "{}", "max_channels": 1, "retry_max": 2, "retry_delay_min": 1,
        "connect_on_qualify": 1, "created": config.now_iso(),
        "updated": config.now_iso()})


def _contact(phone):
    return db.insert("contacts", {
        "name": "LIVE", "phone": phone, "grp": "", "note": "", "consent": 1,
        "consent_source": "test", "blacklisted": 0, "complaints": 0,
        "created": config.now_iso(), "updated": config.now_iso()})


def _item(cid, contact_id, phone, status="wait_operator", attempts=1):
    return db.insert("campaign_items", {
        "campaign_id": cid, "contact_id": contact_id, "contact_name": "LIVE",
        "contact_phone": phone, "status": status, "attempts": attempts,
        "next_attempt_at": "", "last_result": "", "created": config.now_iso(),
        "updated": config.now_iso(), "completed_at": ""})


def _call(cid, item_id, contact_id, phone, provider="megafon_vats",
          status="wait_operator", started=None, external="LIVECALL",
          answered="", ended="", result=""):
    return db.insert("calls", {
        "campaign_id": cid, "item_id": item_id, "contact_id": contact_id,
        "contact_name": "LIVE", "contact_phone": phone,
        "caller_id": "79990001122", "number_id": 0, "provider": provider,
        "external_call_id": external, "direction": "out", "status": status,
        "result": result, "detail": "", "agent_result": "", "recording": "",
        "started_at": started or config.now_iso(), "answered_at": answered,
        "ended_at": ended, "duration_sec": 0})


def _acd(call_id, status="queued"):
    return db.insert("acd", {"call_id": call_id, "item_id": 0,
                             "operator_id": 0, "status": status,
                             "created": config.now_iso(),
                             "updated": config.now_iso()})


class TestVatsConversationWatchdog(unittest.TestCase):
    def setUp(self):
        self.engine = Engine(provider=SimProvider(), auto_start=False)

    def tearDown(self):
        db.q("DELETE FROM acd WHERE call_id IN "
             "(SELECT id FROM calls WHERE contact_name='LIVE')")
        db.q("DELETE FROM attempts WHERE call_id IN "
             "(SELECT id FROM calls WHERE contact_name='LIVE')")
        db.q("DELETE FROM calls WHERE contact_name='LIVE'")
        db.q("DELETE FROM campaign_items WHERE contact_name='LIVE'")
        db.q("DELETE FROM contacts WHERE name='LIVE' AND phone LIKE '799977703%'")
        db.q("DELETE FROM campaigns WHERE name='LIVE'")
        db.q("DELETE FROM provider_events WHERE external_call_id LIKE 'LIVE%'")

    def test_live_vats_conversation_survives_acd_timeout(self):
        """VATS-разговор (external_call_id) переживает acd_wait_timeout."""
        cid = _campaign()
        c = _contact("79997770301")
        item = _item(cid, c, "79997770301")
        call_id = _call(cid, item, c, "79997770301",
                        started=self.engine._ago(5), external="LIVE1")
        _acd(call_id)
        self.engine.tick_once()
        call = db.fetch1("SELECT * FROM calls WHERE id=?", (call_id,))
        self.assertEqual(call["status"], "wait_operator")
        self.assertEqual(call["result"], "")
        acd = db.fetch1("SELECT * FROM acd WHERE call_id=?", (call_id,))
        self.assertEqual(acd["status"], "queued")

    def test_plain_wait_operator_still_expires(self):
        """Обычный wait_operator без ВАТС по-прежнему уходит в no_operator."""
        cid = _campaign()
        c = _contact("79997770302")
        item = _item(cid, c, "79997770302")
        call_id = _call(cid, item, c, "79997770302", provider="sim",
                        started=self.engine._ago(5), external="")
        _acd(call_id)
        self.engine.tick_once()
        call = db.fetch1("SELECT * FROM calls WHERE id=?", (call_id,))
        self.assertEqual(call["status"], "done")
        self.assertEqual(call["result"], "no_operator")
        acd = db.fetch1("SELECT * FROM acd WHERE call_id=?", (call_id,))
        self.assertEqual(acd["status"], "missed")

    def test_stuck_vats_conversation_safety_timeout(self):
        """Потеря финала ВАТС: долгий wait_operator → timeout+ретрай, не вечность."""
        cid = _campaign()
        c = _contact("79997770303")
        item = _item(cid, c, "79997770303")
        call_id = _call(cid, item, c, "79997770303",
                        started=self.engine._ago(40), external="LIVE3")
        _acd(call_id)
        self.engine.tick_once()
        call = db.fetch1("SELECT * FROM calls WHERE id=?", (call_id,))
        self.assertEqual(call["status"], "done")
        self.assertEqual(call["result"], "timeout")
        item = db.fetch1("SELECT * FROM campaign_items WHERE id=?", (item,))
        self.assertEqual(item["status"], "queued")  # ретрай запланирован
        self.assertNotEqual(item["next_attempt_at"], "")
        acd = db.fetch1("SELECT * FROM acd WHERE call_id=?", (call_id,))
        self.assertEqual(acd["status"], "missed")

    def _done_no_operator(self, phone, external):
        cid = _campaign()
        c = _contact(phone)
        item = db.insert("campaign_items", {
            "campaign_id": cid, "contact_id": c, "contact_name": "LIVE",
            "contact_phone": phone, "status": "no_operator", "attempts": 1,
            "next_attempt_at": "", "last_result": "Оператор не принял",
            "created": config.now_iso(), "updated": config.now_iso(),
            "completed_at": config.now_iso()})
        call_id = _call(cid, item, c, phone, status="done",
                        started=self.engine._ago(3), external=external,
                        answered=self.engine._ago(2), ended=config.now_iso(),
                        result="no_operator")
        return cid, item, call_id

    def test_heal_via_completed(self):
        """COMPLETED после гонки watchdog: no_operator → operator_ok."""
        cid, item, call_id = self._done_no_operator("79997770304", "LIVE4")
        ev = {"provider": "megafon_vats", "call_id": None, "event": "done",
              "external_call_id": "LIVE4", "fingerprint": "LIVEheal4"}
        self.engine.handle_event(ev)
        call = db.fetch1("SELECT * FROM calls WHERE id=?", (call_id,))
        self.assertEqual(call["result"], "operator_ok")
        # Позицию не переоткрываем (как history-downgrade).
        item = db.fetch1("SELECT * FROM campaign_items WHERE id=?", (item,))
        self.assertEqual(item["status"], "no_operator")

    def test_heal_via_history_success(self):
        """history Success после гонки: no_operator → operator_ok."""
        cid, item, call_id = self._done_no_operator("79997770305", "LIVE5")
        ev = {"provider": "megafon_vats", "call_id": None, "event": "history",
              "status": "Success", "raw": {"status": "Success"}, "duration": 95,
              "direction": "out", "external_call_id": "LIVE5",
              "fingerprint": "LIVEheal5"}
        self.engine.handle_event(ev)
        call = db.fetch1("SELECT * FROM calls WHERE id=?", (call_id,))
        self.assertEqual(call["result"], "operator_ok")
        self.assertEqual(call["duration_sec"], 95)

    def test_no_heal_when_history_failed(self):
        """history с провалом: no_operator не трогаем."""
        cid, item, call_id = self._done_no_operator("79997770306", "LIVE6")
        ev = {"provider": "megafon_vats", "call_id": None, "event": "history",
              "status": "Busy", "raw": {"status": "Busy"}, "direction": "out",
              "external_call_id": "LIVE6", "fingerprint": "LIVEnoheal6"}
        self.engine.handle_event(ev)
        call = db.fetch1("SELECT * FROM calls WHERE id=?", (call_id,))
        self.assertEqual(call["result"], "no_operator")


class TestCallTimelineApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.init_db()
        cls.engine = Engine(provider=SimProvider(), auto_start=False)
        api.ENGINE = cls.engine
        payload, st = api.route("POST", "/api/v2/auth/login",
                                {"login": "admin", "password": "TestAdmin123!"}, {})
        assert st == 200, payload
        cls.token = payload["token"]

    def tearDown(self):
        db.q("DELETE FROM acd WHERE call_id IN "
             "(SELECT id FROM calls WHERE contact_name='LIVE')")
        db.q("DELETE FROM calls WHERE contact_name='LIVE'")
        db.q("DELETE FROM campaign_items WHERE contact_name='LIVE'")
        db.q("DELETE FROM contacts WHERE name='LIVE' AND phone LIKE '799977704%'")
        db.q("DELETE FROM campaigns WHERE name='LIVE'")
        db.q("DELETE FROM provider_events WHERE external_call_id LIKE 'LIVE%'")

    def _mk(self, phone="79997770401", external="LIVETL1"):
        cid = _campaign()
        c = _contact(phone)
        item = _item(cid, c, phone)
        call_id = _call(cid, item, c, phone, external=external)
        return call_id

    def test_timeline_ok(self):
        call_id = self._mk()
        for i, et in enumerate(("OUTGOING", "ACCEPTED")):
            db.insert("provider_events", {
                "provider": "megafon_vats", "fingerprint": "LIVEtl{}".format(i),
                "external_call_id": "LIVETL1", "event_type": et,
                "payload_json": "{\"type\": \"%s\"}" % et,
                "received_at": config.now_iso(), "processed_at": "",
                "status": "done"})
        payload, st = api.route(
            "GET", "/api/v2/calls/{}/timeline".format(call_id), {},
            {"X-Ats-Token": self.token})
        self.assertEqual(st, 200, payload)
        self.assertEqual(payload["call"]["id"], call_id)
        self.assertEqual([e["event_type"] for e in payload["events"]],
                         ["OUTGOING", "ACCEPTED"])
        self.assertEqual(payload["events"][0]["payload"], {"type": "OUTGOING"})
        self.assertIn("acd", payload)

    def test_timeline_404(self):
        payload, st = api.route("GET", "/api/v2/calls/999999/timeline", {},
                                {"X-Ats-Token": self.token})
        self.assertEqual(st, 404)

    def test_timeline_auth_required(self):
        payload, st = api.route("GET", "/api/v2/calls/1/timeline", {}, {})
        self.assertEqual(st, 401)


if __name__ == "__main__":
    unittest.main()
