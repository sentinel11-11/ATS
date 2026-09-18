# -*- coding: utf-8 -*-
"""Тесты AMI-моста и устойчивости (app/asterisk.py, AsteriskAmiProvider).

ScriptedAMIServer — программируемый фейк Asterisk: Login/Ping/Originate
(+OriginateResponse по сценарию)/Bridge/Hangup, разрыв соединения по команде.
Проверяем честный handshake ACD-бриджа, fail-fast при ошибках, reconnect
с backoff и выживание idle-таймаута сокета.

Запуск: python -m unittest discover -s tests -v   (из корня репозитория)
"""
import os
import queue
import socket
import tempfile
import threading
import time
import unittest

_TMP = tempfile.mkdtemp(prefix="ats_bridge_")
os.environ["ATS_FAST"] = "1"
os.environ["ATS_DATA_DIR"] = _TMP
os.environ["ATS_ADMIN_PASSWORD"] = "TestAdmin123!"
os.environ["ATS_DEV_SEED"] = "1"

from app import asterisk as ami_mod  # noqa: E402
from app import config, db  # noqa: E402
from app.telephony import AsteriskAmiProvider  # noqa: E402

db.init_db()
_test_seed = db.get_settings()
if not _test_seed.get("provider"):
    _test_seed["provider"] = "sim"
    db.save_settings(_test_seed)


class ScriptedAMIServer:
    """Программируемый фейк Asterisk AMI для тестов бриджа и reconnect.

    Умеет: Login/Ping/Logoff/Originate (+OriginateResponse по сценарию)/
    Bridge (настраиваемый результат)/Hangup. Все принятые actions — в seen.
    Принимает подключения подряд (для reconnect), разрыв — drop_all().
    """

    def __init__(self):
        self.srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(5)
        self.port = self.srv.getsockname()[1]
        self.seen = []
        self.originate_responses = []
        self.bridge_result = "Success"
        self.lock = threading.Lock()
        self._conns = []
        self._stop = threading.Event()
        self._threads = []
        self._accept_thread = threading.Thread(target=self._serve, daemon=True)

    def start(self):
        self._accept_thread.start()

    def stop(self):
        self._stop.set()
        with self.lock:
            conns = list(self._conns)
        for c in conns:
            try:
                c.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                c.close()
            except OSError:
                pass
        try:
            self.srv.close()
        except OSError:
            pass
        self._accept_thread.join(timeout=2)
        for t in list(self._threads):
            t.join(timeout=2)

    def drop_all(self):
        """Разорвать все клиентские соединения (имитация падения Asterisk)."""
        with self.lock:
            conns = list(self._conns)
            self._conns = []
        for c in conns:
            try:
                c.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                c.close()
            except OSError:
                pass

    def actions(self, name):
        with self.lock:
            return [m for m in self.seen if m.get("Action") == name]

    def _serve(self):
        self.srv.settimeout(0.2)
        while not self._stop.is_set():
            try:
                conn, _ = self.srv.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with self.lock:
                self._conns.append(conn)
            t = threading.Thread(target=self._handle, args=(conn,), daemon=True)
            with self.lock:
                self._threads.append(t)
            t.start()

    def _handle(self, conn):
        try:
            conn.sendall(b"Asterisk Call Manager/1.1\r\n\r\n")
        except OSError:
            return
        buf = b""
        conn.settimeout(0.2)
        while not self._stop.is_set():
            try:
                data = conn.recv(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            if not data:
                break
            buf += data
            closed = False
            while b"\r\n\r\n" in buf:
                blob, buf = buf.split(b"\r\n\r\n", 1)
                msg = ami_mod.parse_message(blob.decode("utf-8", "ignore"))
                with self.lock:
                    self.seen.append(msg)
                if self._dispatch(conn, msg) == "close":
                    closed = True
                    break
            if closed:
                break
        with self.lock:
            try:
                self._conns.remove(conn)
            except ValueError:
                pass
        try:
            conn.close()
        except OSError:
            pass

    def _send(self, conn, fields):
        try:
            raw = "\r\n".join("{}: {}".format(k, v) for k, v in fields.items())
            conn.sendall((raw + "\r\n\r\n").encode("utf-8"))
        except OSError:
            pass

    def _dispatch(self, conn, msg):
        action = str(msg.get("Action", ""))
        aid = str(msg.get("ActionID", ""))
        if action == "Login":
            self._send(conn, {"ActionID": aid, "Response": "Success",
                              "Message": "Authentication accepted"})
        elif action == "Ping":
            self._send(conn, {"ActionID": aid, "Response": "Success", "Ping": "Pong"})
        elif action == "Logoff":
            self._send(conn, {"ActionID": aid, "Response": "Goodbye"})
            return "close"
        elif action == "Originate":
            self._send(conn, {"ActionID": aid, "Response": "Success",
                              "Message": "Originate successfully queued"})
            with self.lock:
                script = self.originate_responses.pop(0) if self.originate_responses else None
            if script is not None:
                ev = {"Event": "OriginateResponse", "ActionID": aid}
                ev.update(script)
                self._send(conn, ev)
        elif action == "Bridge":
            if self.bridge_result == "Success":
                self._send(conn, {"ActionID": aid, "Response": "Success",
                                  "BridgeUniqueid": "bridge-1"})
            else:
                self._send(conn, {"ActionID": aid, "Response": "Error",
                                  "Message": "bridge failed"})
        elif action == "Hangup":
            self._send(conn, {"ActionID": aid, "Response": "Success"})
        else:
            self._send(conn, {"ActionID": aid, "Response": "Error",
                              "Message": "Unknown action"})
        return None


class TestAmiBridge(unittest.TestCase):
    def setUp(self):
        self.fake = ScriptedAMIServer()
        self.fake.start()
        self.addCleanup(self.fake.stop)

    def _provider(self, **over):
        cfg = {"host": "127.0.0.1", "port": self.fake.port, "user": "ats", "secret": "s",
               "trunk": "mtt", "timeout": 3, "op_wait_sec": 5,
               "acd_answer_timeout": 3, "bridge_timeout": 2,
               "op_ring_timeout_ms": 30000}
        cfg.update(over)
        p = AsteriskAmiProvider()
        p.configure({"ami": cfg})
        self.addCleanup(p.stop)
        return p

    def test_bridge_happy_path(self):
        self.fake.originate_responses.append(
            {"Response": "Success", "Channel": "PJSIP/mtt/101-00000001", "Reason": "4"})
        p = self._provider()
        p.channels[7] = "PJSIP/mtt/79990000001-00000000"
        self.assertTrue(p.connect_operator(7, "101"))
        orig = self.fake.actions("Originate")
        self.assertEqual(len(orig), 1)
        self.assertTrue(orig[0]["Channel"].endswith("/101"))
        self.assertEqual(orig[0].get("Application"), "Wait")
        self.assertTrue(str(orig[0].get("ActionID", "")).startswith("ats-acd-7-"))
        br = self.fake.actions("Bridge")
        self.assertEqual(len(br), 1)
        self.assertEqual(br[0]["Channel1"], "PJSIP/mtt/101-00000001")
        self.assertEqual(br[0]["Channel2"], "PJSIP/mtt/79990000001-00000000")

    def test_bridge_originate_failure(self):
        self.fake.originate_responses.append({"Response": "Failure", "Reason": "5"})
        p = self._provider()
        p.channels[7] = "PJSIP/mtt/x-1"
        self.assertFalse(p.connect_operator(7, "101"))
        self.assertEqual(self.fake.actions("Bridge"), [])

    def test_bridge_timeout_no_response(self):
        p = self._provider(acd_answer_timeout=0.3)
        p.channels[7] = "PJSIP/mtt/x-1"
        self.assertFalse(p.connect_operator(7, "101"))
        self.assertEqual(self.fake.actions("Bridge"), [])

    def test_bridge_action_error_hangs_up_op_leg(self):
        self.fake.originate_responses.append(
            {"Response": "Success", "Channel": "PJSIP/mtt/101-00000002"})
        self.fake.bridge_result = "Error"
        p = self._provider()
        p.channels[7] = "PJSIP/mtt/cli-1"
        self.assertFalse(p.connect_operator(7, "101"))
        hangs = self.fake.actions("Hangup")
        self.assertEqual(len(hangs), 1)
        self.assertEqual(hangs[0]["Channel"], "PJSIP/mtt/101-00000002")

    def test_connect_operator_no_channel(self):
        p = self._provider()
        self.assertFalse(p.connect_operator(777, "101"))
        self.assertEqual(self.fake.actions("Originate"), [])

    def test_dial_failure_emits_failed(self):
        self.fake.originate_responses.append({"Response": "Failure", "Reason": "0"})
        p = self._provider()
        q = queue.Queue()
        p.attach(q)
        p.dial({"call_id": 9, "phone": "79990000001", "caller_id": "74950000000"})
        ev = q.get(timeout=5)
        self.assertEqual(ev["event"], "status")
        self.assertEqual(ev["call_id"], 9)
        self.assertEqual(ev["status"], "failed")
        orig = self.fake.actions("Originate")
        self.assertEqual(len(orig), 1)
        self.assertIn("ats-call-9", orig[0]["CallerID"])
        self.assertIn("74950000000", orig[0]["CallerID"])
        self.assertEqual(orig[0]["Variable"], "ATS_CALL_ID=9")

    def test_dial_success_no_failure_event(self):
        self.fake.originate_responses.append(
            {"Response": "Success", "Channel": "PJSIP/mtt/7999-1"})
        p = self._provider()
        q = queue.Queue()
        p.attach(q)
        p.dial({"call_id": 10, "phone": "79990000002", "caller_id": "74950000000"})
        deadline = time.time() + 3
        while time.time() < deadline and p._originate:
            time.sleep(0.02)
        self.assertEqual(p._originate, {})
        self.assertTrue(q.empty())

    def test_reconnect_after_drop(self):
        p = self._provider()
        c = p.client
        c.reconnect_delay = 0.05
        c.reconnect_max = 0.2
        self.assertTrue(c.connected)
        self.fake.drop_all()
        deadline = time.time() + 10
        # сначала ждём, что клиент ЗАМЕТИЛ разрыв (иначе poll выйдет мгновенно
        # по ещё-True флагу и следующий action честно упадёт fail-fast)...
        while time.time() < deadline and c.connected:
            time.sleep(0.02)
        self.assertFalse(c.connected, "клиент не заметил разрыв")
        # ...а потом переподключился
        deadline = time.time() + 10
        while time.time() < deadline and not c.connected:
            time.sleep(0.05)
        self.assertTrue(c.connected, "клиент не переподключился")
        resp = c.action("Ping", check=False)
        self.assertEqual(str(resp.get("Response", "")).lower(), "success")
        self.assertGreaterEqual(len(self.fake.actions("Login")), 2)

    def test_wait_for_timeout(self):
        c = ami_mod.AMIClient("127.0.0.1")
        self.assertIsNone(c.wait_for(lambda m: True, 0.1))

    def test_idle_timeout_survival(self):
        c = ami_mod.AMIClient("127.0.0.1", self.fake.port, "ats", "s", timeout=0.3)
        c.auto_reconnect = False  # проверяем именно reader, а не спасение реконнектом
        c.connect()
        c.login()
        self.addCleanup(c.close)
        time.sleep(0.8)  # >2 sock-таймаутов тишины
        self.assertTrue(c._reader.is_alive(), "reader умер на idle-таймауте")
        resp = c.action("Ping", check=False)
        self.assertEqual(str(resp.get("Response", "")).lower(), "success")

    def test_action_id_passthrough(self):
        p = self._provider()
        resp = p.client.action("Ping", action_id="fixed-1", check=False)
        self.assertEqual(resp.get("ActionID"), "fixed-1")
        pings = self.fake.actions("Ping")
        self.assertIn("fixed-1", [m.get("ActionID") for m in pings])


if __name__ == "__main__":
    unittest.main()
