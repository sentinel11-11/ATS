# -*- coding: utf-8 -*-
"""Тесты протокола AMI (app/asterisk.py), вебхуков UIS и ACD-watchdog (engine)."""
import json
import os
import socket
import tempfile
import threading
import time
import unittest

_TMP = tempfile.mkdtemp(prefix="ats_ami_")
os.environ["ATS_FAST"] = "1"
os.environ["ATS_DATA_DIR"] = _TMP
os.environ["ATS_ADMIN_PASSWORD"] = "TestAdmin123!"

from app import asterisk as ami_mod  # noqa: E402
from app import config, db  # noqa: E402
from app.engine import Engine  # noqa: E402
from app.telephony import map_uis_webhook  # noqa: E402


class FakeAMIServer:
    """Минимальный AMI-сервер для тестов: Login, Ping, Logoff, событие по команде."""
    def __init__(self):
        self.srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(1)
        self.port = self.srv.getsockname()[1]
        self.conn = None
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.seen = []

    def start(self):
        self.thread.start()

    def _serve(self):
        conn, _ = self.srv.accept()
        self.conn = conn
        conn.sendall(b"Asterisk Call Manager/1.1\r\n\r\n")
        buf = b""
        while True:
            try:
                data = conn.recv(4096)
            except Exception:
                break
            if not data:
                break
            buf += data
            while b"\r\n\r\n" in buf:
                blob, buf = buf.split(b"\r\n\r\n", 1)
                msg = ami_mod.parse_message(blob.decode("utf-8", "ignore"))
                self.seen.append(msg)
                action = str(msg.get("Action", ""))
                aid = str(msg.get("ActionID", ""))
                if action == "Login":
                    self._resp({"Response": "Success", "Message": "Authentication accepted"}, aid)
                elif action == "Ping":
                    self._resp({"Response": "Success", "Ping": "Pong"}, aid)
                elif action == "Logoff":
                    self._resp({"Response": "Goodbye", "Message": "Thanks for all the fish."}, aid)
                    break
                elif action == "FireEvent":
                    self._resp({"Response": "Success"}, aid)
                    ev = {"Event": "Newchannel", "Channel": "PJSIP/trunk/79990000001",
                          "CallerIDName": "ats-call-42", "CallerIDNum": "79000000001"}
                    raw = "\r\n".join("{}: {}".format(k, v) for k, v in ev.items()) + "\r\n\r\n"
                    conn.sendall(raw.encode("utf-8"))
                else:
                    self._resp({"Response": "Error", "Message": "Unknown action"}, aid)
        try:
            conn.close()
        except Exception:
            pass
        self.srv.close()

    def _resp(self, fields, aid):
        if self.conn:
            fields = {"ActionID": aid, **fields}
            raw = "\r\n".join("{}: {}".format(k, v) for k, v in fields.items()) + "\r\n\r\n"
            self.conn.sendall(raw.encode("utf-8"))

    def stop(self):
        try:
            if self.conn:
                self.conn.close()
        except Exception:
            pass
        try:
            self.srv.close()
        except Exception:
            pass


class TestAmiCodec(unittest.TestCase):
    def test_parse_message(self):
        blob = "Event: Newchannel\r\nChannel: PJSIP/x\r\nChannel: PJSIP/y\r\nCallerIDNum: 123\r\n"
        msg = ami_mod.parse_message(blob)
        self.assertEqual(msg["Event"], "Newchannel")
        self.assertEqual(msg["Channel"], ["PJSIP/x", "PJSIP/y"])
        self.assertEqual(msg["CallerIDNum"], "123")

    def test_encode_action(self):
        payload, aid = ami_mod.encode_action("Originate", {"Channel": "PJSIP/a", "Async": "true"}, "myid1")
        self.assertIn("Action: Originate", payload)
        self.assertIn("ActionID: myid1", payload)
        self.assertEqual(aid, "myid1")
        self.assertTrue(payload.endswith("\r\n\r\n"))


class TestAmiClient(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fake = FakeAMIServer()
        cls.fake.start()
        cls.client = ami_mod.AMIClient("127.0.0.1", cls.fake.port, "ats", "secret", timeout=3)
        cls.client.event_handler = cls._on_ev
        cls.events = []
        cls.client.connect()
        cls.client.login()

    @classmethod
    def _on_ev(cls, ev):
        cls.events.append(ev)

    def test_ping(self):
        resp = self.client.ping()
        self.assertEqual(str(resp.get("Response", "")).lower(), "success")

    def test_unknown_action_fails(self):
        with self.assertRaises(ami_mod.AMIError):
            self.client.action("Nope", check=True)

    def test_login_ok(self):
        # уже залогинены в setUpClass; повторный login должен пройти (сервер отвечает Success)
        resp = self.client.login()
        self.assertEqual(str(resp.get("Response", "")).lower(), "success")

    def test_event_dispatch(self):
        self.client.action("FireEvent")  # сервер после ответа шлёт событие Newchannel
        deadline = time.time() + 3
        while not self.events and time.time() < deadline:
            time.sleep(0.05)
        self.assertTrue(self.events)
        self.assertEqual(self.events[0]["Event"], "Newchannel")
        self.assertIn("ats-call-42", str(self.events[0].get("CallerIDName")))

    @classmethod
    def tearDownClass(cls):
        try:
            cls.client.logoff()
        except Exception:
            pass
        cls.client.close()
        cls.fake.stop()


class TestUisWebhookMap(unittest.TestCase):
    def test_answered_human(self):
        ev = map_uis_webhook({"call_id": "7", "status": "answered", "human": True, "detail": "ok"})
        self.assertEqual(ev["event"], "answered")
        self.assertTrue(ev["human"])

    def test_busy(self):
        ev = map_uis_webhook({"call_id": 8, "status": "busy"})
        self.assertEqual(ev["event"], "status")
        self.assertEqual(ev["status"], "busy")

    def test_unrecognized_returns_none(self):
        self.assertIsNone(map_uis_webhook({"call_id": 1, "status": "whatever"}))


class TestAcdWatchdog(unittest.TestCase):
    """Оператор не принял звонок за acd_wait_timeout_sec -> no_operator + задача в CRM."""
    @classmethod
    def setUpClass(cls):
        # изоляция: собственная свежая БД (общий data_dir модулей даёт помехи)
        import pathlib as _pl
        cls._fresh = _pl.Path(tempfile.mkdtemp(prefix="ats_acd_"))
        old_conn = db._conn
        try:
            if old_conn is not None:
                old_conn.close()
        except Exception:
            pass
        db._conn = None
        config.DB_PATH = cls._fresh / "acd.db"
        db.init_db()
        s = db.get_settings()
        s["acd_wait_timeout_sec"] = 1
        db.save_settings(s)
        cls.engine = Engine(auto_start=False)

    def test_wait_operator_timeout(self):
        cid_c = db.insert("contacts", {"name": "Ждун", "phone": "79039990001", "grp": "т", "note": "",
                                       "consent": 1, "consent_source": "t", "blacklisted": 0,
                                       "complaints": 0, "created": config.now_iso(), "updated": config.now_iso()})
        camp = db.insert("campaigns", {"name": "ACD-таймаут", "template_id": 2, "flow": "agent",
                                       "status": "running",
                                       "schedule": json.dumps({"start": "00:00", "end": "23:59",
                                                               "days": [0, 1, 2, 3, 4, 5, 6]}),
                                       "max_channels": 1, "retry_max": 0, "retry_delay_min": 1,
                                       "connect_on_qualify": 1,
                                       "created": config.now_iso(), "updated": config.now_iso()})
        db.insert("campaign_items", {"campaign_id": camp, "contact_id": cid_c, "contact_name": "Ждун",
                                     "contact_phone": "79039990001", "status": "queued", "attempts": 0,
                                     "next_attempt_at": "", "last_result": "",
                                     "created": config.now_iso(), "updated": config.now_iso(), "completed_at": ""})
        p = self.engine.provider
        p.set_outcome("79039990001", "answered_human")
        p.set_answer("79039990001", "1")   # квалифицирован -> wait_operator
        deadline = time.time() + 20
        finished = False
        started = False
        while time.time() < deadline:
            self.engine.tick_once()
            call = db.fetch1("SELECT * FROM calls WHERE contact_phone='79039990001' ORDER BY id DESC")
            if call:
                started = True
            if call and call["status"] == "done" and call["result"] == "no_operator":
                finished = True
                break
            time.sleep(0.03)
        self.assertTrue(started, "звонок вообще не начался")
        self.assertTrue(finished, "звонок должен завершиться no_operator по таймауту ACD")
        item = db.fetch1("SELECT * FROM campaign_items WHERE contact_phone='79039990001'")
        self.assertEqual(item["status"], "no_operator")
        acd = db.fetch1("SELECT * FROM acd WHERE call_id=?", (call["id"],))
        self.assertEqual(acd["status"], "missed")
        tasks = list(config.CRM_OUT_DIR.glob("crm_tasks.csv"))
        self.assertTrue(tasks, "должна создаться задача «перезвонить» в CRM")

    @classmethod
    def tearDownClass(cls):
        cls.engine.stop()


if __name__ == "__main__":
    unittest.main()
