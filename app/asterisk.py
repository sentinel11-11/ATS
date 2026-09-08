# -*- coding: utf-8 -*-
"""AMI-клиент Asterisk (Manager API) для ATS v2.

Реализует протокол AMI поверх TCP: сообщения «Key: Value», разделитель — пустая строка.
- login/logoff, action() с синхронным ожиданием ответа по ActionID;
- фоновый поток читает события и отдаёт их в обработчик.

Используется адаптером AsteriskAmiProvider (app/telephony.py). Тесты протокола —
tests/test_asterisk.py (кодек проверяется на фейковом AMI-сервере).
"""
import logging
import socket
import threading
import time
import uuid

log = logging.getLogger("ats.ami")


def parse_message(blob):
    """Разобрать сырое AMI-сообщение (до пустой строки) в словарь.
    Повторяющиеся ключи сохраняются в списки (в AMI бывают одинаковые ключи)."""
    msg = {}
    for line in blob.split("\r\n"):
        if not line:
            continue
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip()
        if key in msg:
            if not isinstance(msg[key], list):
                msg[key] = [msg[key]]
            msg[key].append(val)
        else:
            msg[key] = val
    return msg


def encode_action(name, params=None, action_id=None):
    action_id = action_id or uuid.uuid4().hex
    lines = ["Action: {}".format(name)]
    for k, v in (params or {}).items():
        lines.append("{}: {}".format(k, str(v)))
    lines.append("ActionID: {}".format(action_id))
    return "\r\n".join(lines) + "\r\n\r\n", action_id


class AMIError(RuntimeError):
    pass


class AMIClient:
    def __init__(self, host, port=5038, user="", secret="", timeout=5.0):
        self.host = host
        self.port = int(port)
        self.user = user
        self.secret = secret
        self.timeout = timeout
        self.sock = None
        self._wlock = threading.Lock()
        self._reader = None
        self._pending = {}
        self._cond = threading.Condition()
        self.event_handler = None   # callable(event_dict)
        self._buffer = b""
        self._closed = threading.Event()
        self.connected = False

    # ---------- соединение ----------
    def connect(self):
        self.sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        self.sock.settimeout(self.timeout)
        # принять баннер (AMI шлёт "Asterisk Call Manager/1.1")
        self._read_until_blank(initial=True)
        self._reader = threading.Thread(target=self._read_loop, daemon=True, name="ami-reader")
        self._reader.start()
        return True

    def close(self):
        self._closed.set()
        with self._cond:
            self._pending.clear()
            self._cond.notify_all()
        try:
            if self.sock:
                self.sock.close()
        except Exception:
            pass
        self.connected = False

    def __enter__(self):
        self.connect()
        self.login()
        return self

    def __exit__(self, *exc):
        try:
            self.logoff()
        except Exception:
            pass
        self.close()

    def login(self):
        resp = self.action("Login", {"Username": self.user, "Secret": self.secret}, check=False)
        if str(resp.get("Response", "")).lower() != "success":
            raise AMIError("AMI Login failed: {}".format(resp.get("Message", resp)))
        self.connected = True
        return resp

    def logoff(self):
        if self.connected:
            try:
                self.action("Logoff", check=False)
            except Exception:
                pass

    # ---------- действия ----------
    def action(self, name, params=None, timeout=None, check=True):
        action_id = None
        with self._cond:
            try:
                payload, action_id = encode_action(name, params)
                with self._wlock:
                    if not self.sock:
                        raise AMIError("AMI not connected")
                    self.sock.sendall(payload.encode("utf-8"))
            except OSError as e:
                raise AMIError("AMI send failed: {}".format(e))
            result = {"Response": "Error", "Message": "timeout"}
            waiter = threading.Event()
            self._pending[action_id] = (waiter, result)
        waiter.wait(timeout or self.timeout)
        with self._cond:
            self._pending.pop(action_id, None)
        if check and str(result.get("Response", "")).lower() != "success":
            raise AMIError("AMI {}: {}".format(name, result.get("Message", result)))
        return dict(result)

    def ping(self):
        return self.action("Ping", check=False)

    # ---------- чтение ----------
    def _read_until_blank(self, initial=False):
        while b"\r\n\r\n" not in self._buffer:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise AMIError("AMI connection closed")
            self._buffer += chunk
        blob, self._buffer = self._buffer.split(b"\r\n\r\n", 1)
        if initial:
            # баннер может содержать и первую пустую строку сразу; отдаём как есть
            return blob
        return blob

    def _read_loop(self):
        while not self._closed.is_set():
            try:
                blob = self._read_until_blank()
            except Exception:
                break
            try:
                msg = parse_message(blob.decode("utf-8", "ignore"))
            except Exception:
                continue
            etype = str(msg.get("Event", "")).lower()
            if etype == "":
                aid = str(msg.get("ActionID", ""))
                if aid in self._pending:
                    waiter, result = self._pending[aid]
                    result.update(msg)
                    waiter.set()
            else:
                if self.event_handler:
                    try:
                        self.event_handler(msg)
                    except Exception:
                        log.exception("AMI event handler")
        self.connected = False
