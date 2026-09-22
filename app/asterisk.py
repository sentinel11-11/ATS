# -*- coding: utf-8 -*-
"""AMI-клиент Asterisk (Manager API) для ATS v2.

Реализует протокол AMI поверх TCP: сообщения «Key: Value», разделитель — пустая строка.
- login/logoff, action() с синхронным ожиданием ответа по ActionID;
- фоновый поток читает события и отдаёт их в обработчик.

Используется адаптером AsteriskAmiProvider (app/telephony.py). Тесты протокола —
tests/test_telephony_extra.py (кодек и базовый клиент на фейковом AMI-сервере)
и tests/test_ami_bridge.py (handshake бриджа, reconnect, таймауты).
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
        self._event_waiters = []  # [(predicate, event, box)] для wait_for()
        self.auto_reconnect = True
        self.reconnect_delay = 1.0
        self.reconnect_max = 30.0
        self._cond = threading.Condition()
        self.event_handler = None   # callable(event_dict)
        self._buffer = b""
        self._closed = threading.Event()
        self.connected = False

    # ---------- соединение ----------
    def connect(self):
        self._closed.clear()
        sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        sock.settimeout(self.timeout)
        with self._wlock:
            old = self.sock
            self.sock = sock
            self._buffer = b""
        if old is not None:
            try:
                old.close()
            except Exception:
                pass
        try:
            # принять баннер (AMI шлёт "Asterisk Call Manager/1.1")
            self._read_until_blank(initial=True)
        except Exception:
            with self._wlock:
                if self.sock is sock:
                    self.sock = None
            try:
                sock.close()
            except Exception:
                pass
            raise
        self._reader = threading.Thread(target=self._read_loop, daemon=True, name="ami-reader")
        self._reader.start()
        return True

    def close(self):
        self._closed.set()
        self._fail_pending("closed")
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
    def action(self, name, params=None, timeout=None, check=True, action_id=None):
        with self._cond:
            payload, action_id = encode_action(name, params, action_id)
            # Регистрируем ожидание ДО отправки: иначе быстрый ответ сервера
            # придёт раньше и будет потерян (ложный таймаут Originate).
            result = {"Response": "Error", "Message": "timeout"}
            waiter = threading.Event()
            self._pending[action_id] = (waiter, result)
            try:
                with self._wlock:
                    if not self.sock:
                        raise AMIError("AMI not connected")
                    self.sock.sendall(payload.encode("utf-8"))
            except (OSError, AMIError) as e:
                self._pending.pop(action_id, None)
                if isinstance(e, AMIError):
                    raise
                raise AMIError("AMI send failed: {}".format(e))
        waiter.wait(timeout or self.timeout)
        with self._cond:
            self._pending.pop(action_id, None)
        if check and str(result.get("Response", "")).lower() != "success":
            raise AMIError("AMI {}: {}".format(name, result.get("Message", result)))
        return dict(result)

    def ping(self):
        return self.action("Ping", check=False)

    def wait_for(self, predicate, timeout):
        """Ждать событие AMI, удовлетворяющее predicate(msg)->bool.
        Возвращает сообщение или None по таймауту/разрыву соединения."""
        box = {}
        waiter = threading.Event()
        key = (predicate, waiter, box)
        with self._cond:
            self._event_waiters.append(key)
        try:
            waiter.wait(timeout)
        finally:
            with self._cond:
                try:
                    self._event_waiters.remove(key)
                except ValueError:
                    pass
        return box.get("msg")

    def _fail_pending(self, why):
        """Разбудить всех ожидающих (ответы на actions и события) с ошибкой why."""
        with self._cond:
            for _aid, (waiter, result) in list(self._pending.items()):
                result["Message"] = why
                waiter.set()
            for _pred, waiter, _box in list(self._event_waiters):
                waiter.set()

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
            except socket.timeout:
                continue  # тишина в пределах таймаута сокета — не разрыв
            except Exception:
                break
            try:
                msg = parse_message(blob.decode("utf-8", "ignore"))
            except Exception:
                continue
            etype = str(msg.get("Event", "")).lower()
            if etype == "":
                aid = str(msg.get("ActionID", ""))
                with self._cond:
                    pending = self._pending.get(aid)
                if pending:
                    waiter, result = pending
                    result.update(msg)
                    waiter.set()
            else:
                with self._cond:
                    waiters = list(self._event_waiters)
                for pred, waiter, box in waiters:
                    try:
                        if pred(msg):
                            box["msg"] = msg
                            waiter.set()
                    except Exception:
                        pass
                if self.event_handler:
                    try:
                        self.event_handler(msg)
                    except Exception:
                        log.exception("AMI event handler")
        self.connected = False
        self._fail_pending("disconnected")
        if not self._closed.is_set() and self.auto_reconnect:
            self._reconnect_loop()

    def _reconnect_loop(self):
        """Фоновая петля переподключения: backoff + connect + login.
        Вызывается из умирающего reader-потока; connect() стартует новый reader.
        Подписки восстанавливать не нужно: AMI после login шлёт все события."""
        delay = self.reconnect_delay if self.reconnect_delay > 0 else 0.1
        while not self._closed.is_set():
            if self._closed.wait(delay):
                return
            try:
                self.connect()
                self.login()
            except Exception as e:
                log.warning("AMI reconnect failed (%s:%s): %s", self.host, self.port, e)
                delay = min(max(delay * 2, 0.1), self.reconnect_max)
                continue
            if self._closed.is_set():
                # закрыли, пока переподключались, — откатываем
                try:
                    self.close()
                except Exception:
                    pass
                return
            log.warning("AMI reconnected to %s:%s", self.host, self.port)
            return
