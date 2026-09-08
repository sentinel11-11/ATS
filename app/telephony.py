# -*- coding: utf-8 -*-
"""Телефония ATS v2: единый интерфейс TelephonyProvider и адаптеры.

Реализации:
- SimProvider        — симуляция (тесты, демо, «сухой» пилот без модемов/UIS);
- UISCallApiProvider — адаптер UIS (Call API / SIP) — каркас, заполняется по ответу UIS (T01);
- AsteriskAmiProvider— адаптер медиа-слоя Asterisk/AMI (SIP-транк к оператору) — каркас.

Фабрика выбирает адаптер по settings["provider"]. Новые операторы (МТС и др.) = новые классы
с тем же интерфейсом (мультиоператорность, задача T38).
"""
import queue
import random
import threading
import time
import uuid

from . import config


class ProviderNotConfigured(RuntimeError):
    pass


# ---------- Базовый интерфейс ----------
class TelephonyProvider:
    name = "base"
    interactive = False  # True, если провайдер умеет «интерактивный канал» (опрос/DTMF/агент)

    def __init__(self):
        self._sink = None          # очередь событий движка
        self._stop = threading.Event()

    def attach(self, sink: "queue.Queue"):
        self._sink = sink

    def emit(self, ev: dict):
        ev = dict(ev)
        ev["provider"] = self.name
        if self._sink:
            self._sink.put(ev)

    def configure(self, settings: dict):
        """Проверка/применение настроек; кидает ProviderNotConfigured при нехватке данных."""
        raise NotImplementedError

    def dial(self, req: dict):
        """Инициировать исходящий звонок. req: call_id, phone, caller_id, flow, meta..."""
        raise NotImplementedError

    def hangup(self, call_id):
        raise NotImplementedError

    def make_channel(self, call_id, phone):
        """Интерактивный канал (для agent-потока). У провайдеров с interactive=False вернёт None."""
        return None

    def stop(self):
        self._stop.set()


# ---------- Симуляция ----------
class SimChannel:
    """Канал «абонент» для провайдера sim: ask() возвращает ответ из сценария."""
    def __init__(self, provider, call_id, phone):
        self.provider = provider
        self.call_id = call_id
        self.phone = phone
        self.log = []

    def say(self, text):
        self.log.append(("say", text))
        time.sleep(0.05 if config.FAST else 0.15)

    def ask(self, text, timeout=10):
        self.log.append(("ask", text))
        ans = self.provider.sim_answers.get(self.phone, self.provider.default_answer)
        self.log.append(("answer", ans))
        return ans

    def transcript(self):
        return " | ".join("{}: {}".format(k, v) for k, v in self.log)


class SimProvider(TelephonyProvider):
    name = "sim"
    interactive = True

    def __init__(self):
        super().__init__()
        self.outcome_override = {}    # phone -> результат (для тестов)
        self.sim_answers = {}         # phone -> ответ абонента
        self.default_answer = "1"
        self._events_per_call = {}
        self._lock = threading.RLock()
        self._threads = []
        self.profile = {"answered_human": 45, "machine": 10, "busy": 15,
                        "no_answer": 20, "failed": 8, "blocked": 2}

    def configure(self, settings: dict):
        self.profile = dict(settings.get("sim_outcome", self.profile))
        self.default_answer = str(settings.get("sim_answer", "1"))
        return self

    def _pick(self, phone):
        with self._lock:
            if phone in self.outcome_override:
                return self.outcome_override[phone]
        rnd = random.Random(phone)
        table = []
        for k, v in self.profile.items():
            table += [k] * max(1, int(v))
        return rnd.choice(table)

    def _wait_hangup(self, call_id, max_sec):
        with self._lock:
            ev = self._events_per_call.setdefault(call_id, threading.Event())
        ev.wait(max_sec)
        return ev.is_set()

    def dial(self, req: dict):
        t = threading.Thread(target=self._run, args=(dict(req),), daemon=True)
        self._threads.append(t)
        t.start()
        return True

    def hangup(self, call_id):
        with self._lock:
            ev = self._events_per_call.get(call_id)
        if ev:
            ev.set()

    def make_channel(self, call_id, phone):
        return SimChannel(self, call_id, phone)

    def set_outcome(self, phone, outcome):
        with self._lock:
            self.outcome_override[phone] = outcome

    def set_answer(self, phone, answer):
        with self._lock:
            self.sim_answers[phone] = answer

    def _run(self, req):
        call_id = req["call_id"]
        phone = req["phone"]
        with self._lock:
            # событие создаём заранее, чтобы hangup() из движка не терялся из-за гонки
            self._events_per_call.setdefault(call_id, threading.Event())
        ring_delay = 0.02 if config.FAST else 1.0
        time.sleep(ring_delay * 0.3)
        self.emit({"event": "ring", "call_id": call_id})
        time.sleep(ring_delay * 0.7)
        outcome = self._pick(phone)
        if outcome == "machine":
            outcome = "answered_machine"   # нормализация профиля
        if outcome == "answered_human":
            self.emit({"event": "answered", "call_id": call_id, "human": True})
        elif outcome == "answered_machine":
            self.emit({"event": "answered", "call_id": call_id, "human": False})
        elif outcome == "busy":
            self.emit({"event": "status", "call_id": call_id, "status": "busy", "detail": "Занято"})
            return
        elif outcome == "no_answer":
            self.emit({"event": "status", "call_id": call_id, "status": "no_answer", "detail": "Нет ответа"})
            return
        elif outcome == "failed":
            self.emit({"event": "status", "call_id": call_id, "status": "failed", "detail": "Сбой оператора"})
            return
        elif outcome == "blocked":
            self.emit({"event": "status", "call_id": call_id, "status": "blocked", "detail": "Вызов отклонён (антиспам)"})
            return
        # Ответили: держим канал, пока движок не завершит (agent/message/operator)
        max_sec = 3 if config.FAST else 180
        if not self._wait_hangup(call_id, max_sec):
            self.emit({"event": "dropped", "call_id": call_id, "detail": "Таймаут удержания канала"})
            return
        self.emit({"event": "done", "call_id": call_id, "detail": "Разговор завершён"})

    def stop(self):
        super().stop()
        with self._lock:
            for ev in self._events_per_call.values():
                ev.set()


# ---------- UIS (Call API / SIP) — HTTP-транспорт + вебхуки ----------
class UISCallApiProvider(TelephonyProvider):
    """Адаптер UIS (МегаФон «Ювис»): исходящий звонок через Call API (HTTP).

    Конфигурация (settings.uis): api_url (базовый URL), api_key (токен),
    endpoints (опционально переопределить пути: dial, hangup), webhook_secret.

    Точная схема JSON и адреса методов UIS уточняются по документации/ответу UIS (T01).
    Здесь транспорт написан в общем виде: POST {api_url}/{dial_endpoint} с телом
    {call_id, to, from(caller_id), text} и заголовком Authorization: Bearer {api_key}.
    Ответы и вебхуки статусов принимаются на POST /api/v2/webhooks/uis (см. app/api.py),
    разбор — map_uis_webhook(). Если UIS предоставит SIP-линии — вместо этого адаптера
    используется AsteriskAmiProvider (медиа-слой).
    """
    name = "uis"

    def __init__(self):
        super().__init__()
        self.cfg = {}

    def configure(self, settings: dict):
        cfg = settings.get("uis", {}) or {}
        if not cfg.get("api_url"):
            raise ProviderNotConfigured(
                "UIS не настроен: заполните settings.uis.api_url (и api_key). "
                "Данные выдаёт UIS после запроса T01. Пока используется провайдер sim.")
        self.cfg = cfg
        return self

    def _post(self, endpoint, payload):
        import json
        import urllib.request
        url = self.cfg["api_url"].rstrip("/") + "/" + endpoint.lstrip("/")
        headers = {"Content-Type": "application/json"}
        if self.cfg.get("api_key"):
            headers["Authorization"] = "Bearer " + self.cfg["api_key"]
        req = urllib.request.Request(url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                                     headers=headers)
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode("utf-8") or "{}")

    def dial(self, req: dict):
        ep = (self.cfg.get("endpoints") or {}).get("dial", "call/outgoing")
        payload = {"call_id": req["call_id"], "to": req["phone"],
                   "from": req.get("caller_id", ""), "text": req.get("text", ""),
                   "flow": req.get("flow", "")}
        try:
            self._post(ep, payload)
        except Exception as e:
            raise ProviderNotConfigured("UIS dial failed ({}): {}".format(self.cfg.get("api_url"), e))
        return True

    def hangup(self, call_id):
        ep = (self.cfg.get("endpoints") or {}).get("hangup", "call/hangup")
        try:
            self._post(ep, {"call_id": call_id})
        except Exception:
            pass


UIS_WEBHOOK_STATUSES = {
    "ringing": "ring", "ring": "ring",
    "answered": "answered", "answer": "answered", "talk": "answered",
    "human": "answered", "voice": "answered",
    "machine": "answered", "voicemail": "answered", "ivr": "answered",
    "busy": "busy", "no_answer": "no_answer", "no-answer": "no_answer",
    "noanswer": "no_answer", "failed": "failed", "error": "failed",
    "blocked": "blocked", "hangup": "done", "done": "done", "completed": "done",
}


def map_uis_webhook(body: dict):
    """Преобразовать вебхук UIS в событие движка (provider-style).

    Ожидаемые ключи тела (обобщённо, уточняются по документации UIS T01):
    call_id (наш id звонка), status (строка), detail/status_text (описание), human (bool|str).
    Возвращает dict-событие для engine.handle_event или None, если статус не распознан.
    """
    call_id = body.get("call_id") or body.get("callId") or body.get("id")
    status = str(body.get("status") or body.get("event") or body.get("type") or "").lower()
    detail = str(body.get("detail") or body.get("status_text") or body.get("message") or "")[:300]
    if call_id is None or status not in UIS_WEBHOOK_STATUSES:
        return None
    ev = UIS_WEBHOOK_STATUSES[status]
    if ev == "ring":
        return {"event": "ring", "call_id": call_id}
    if ev == "answered":
        human = body.get("human")
        h = True
        if human is not None:
            h = human if isinstance(human, bool) else str(human).lower() not in ("0", "false", "machine", "voicemail")
        return {"event": "answered", "call_id": call_id, "human": h}
    if ev == "done":
        return {"event": "done", "call_id": call_id, "detail": detail}
    return {"event": "status", "call_id": call_id, "status": ev, "detail": detail}


# ---------- Asterisk/AMI — транспорт на базе AMIClient ----------
class AsteriskAmiProvider(TelephonyProvider):
    """Адаптер медиа-слоя Asterisk по AMI (SIP-транк к оператору).

    Конфигурация (settings.ami): host, port=5038, user, secret, trunk (имя PJSIP/SIP-транка),
    dial_prefix (напр. '' или '8'), context (контекст набора), ring_timeout_ms, amd (bool).

    Originate: Channel PJSIP/{trunk}/{dial_prefix}{phone}, CallerID "{ats-call-<id>}" <caller_id>.
    Маркер в CallerIDName используется, чтобы сопоставить канал с нашим call_id (Newchannel).
    Карта событий AMI → события движка минимальная и требует проверки на живом стенде (T14):
    Newchannel -> ring, Dial(DialStatus=ANSWER)/Bridge -> answered, Hangup -> done.
    connect_operator(): Originate на внутренний номер оператора + Bridge с каналом абонента.
    """
    name = "ami"

    def __init__(self):
        super().__init__()
        self.cfg = {}
        self.client = None
        self.channels = {}   # call_id -> channel name
        self._lock = threading.RLock()

    def configure(self, settings: dict):
        cfg = settings.get("ami", {}) or {}
        if not (cfg.get("host") and cfg.get("user") and cfg.get("secret")):
            raise ProviderNotConfigured(
                "Asterisk AMI не настроен (settings.ami.host/user/secret). Пока используется sim.")
        self.cfg = cfg
        from . import asterisk as ami_mod
        try:
            client = ami_mod.AMIClient(cfg["host"], cfg.get("port", 5038),
                                       cfg["user"], cfg["secret"], timeout=float(cfg.get("timeout", 5)))
            client.connect()
            client.login()
            client.event_handler = self._on_event
        except Exception as e:
            raise ProviderNotConfigured("AMI недоступен ({}): {}".format(cfg.get("host"), e))
        self.client = client
        return self

    def _trunk_channel(self, phone):
        return "PJSIP/{}/{}".format(self.cfg.get("trunk", ""), phone)

    def dial(self, req: dict):
        if not self.client:
            raise ProviderNotConfigured("AMI не подключён")
        phone = str(req["phone"])
        if self.cfg.get("dial_prefix"):
            phone = str(self.cfg["dial_prefix"]) + phone
        chan = self._trunk_channel(phone)
        cid_num = req.get("caller_id", "")
        cid_name = "ats-call-{}".format(req["call_id"])
        params = {
            "Channel": chan,
            "Exten": "s",
            "Context": self.cfg.get("context", "ats-out"),
            "Priority": "1",
            "CallerID": '"{}" <{}>'.format(cid_name, cid_num),
            "Timeout": str(self.cfg.get("ring_timeout_ms", 35000)),
            "Variable": "ATS_CALL_ID={}".format(req["call_id"]),
            "Async": "true",
        }
        self.client.action("Originate", params)
        return True

    def hangup(self, call_id):
        if not self.client:
            return
        with self._lock:
            ch = self.channels.get(call_id)
        if ch:
            try:
                self.client.action("Hangup", {"Channel": ch})
            except Exception:
                pass

    def connect_operator(self, call_id, operator_ext):
        """(живой стенд T14) соединить абонента с внутренним номером оператора."""
        if not self.client or not operator_ext:
            return False
        with self._lock:
            caller_ch = self.channels.get(call_id)
        if not caller_ch:
            return False
        op_chan = "PJSIP/{}".format(self.cfg.get("operator_trunk", self.cfg.get("trunk", ""))) + "/" + str(operator_ext)
        # ВАЖНО: точная схема бриджа зависит от диаплана/транков; проверить на стенде.
        # Штатно в Asterisk используется Dial() в диаплане: ответивший абонент попадает
        # в ACD-контекст, который сам набирает оператора. Здесь — AMI Bridge двух каналов.
        try:
            self.client.action("Originate", {
                "Channel": op_chan, "Exten": "s", "Context": self.cfg.get("context", "ats-in"),
                "Priority": "1", "CallerID": '"ATS-ACD" <{}>'.format(self.cfg.get("acd_callerid", "")),
                "Timeout": "30000", "Async": "true"})
            # (после появления второго канала — Bridge, логика на стенде)
        except Exception:
            return False
        return True

    def _on_event(self, ev: dict):
        etype = str(ev.get("Event", "")).lower()
        # маркер канала в CallerIDName (может быть "ats-call-<id>")
        ch = ev.get("Channel")
        marker = None
        for key in ("CallerIDName", "ConnectedLineName", "CallerID"):
            v = str(ev.get(key, ""))
            if "ats-call-" in v:
                marker = v
                break
        call_id = None
        if marker:
            try:
                call_id = int(marker.split("ats-call-")[1].split('"')[0])
            except Exception:
                call_id = None
        # VariableSet с нашей переменной — тоже маркер
        if not call_id and etype == "variableset" and str(ev.get("Variable", "")) == "ATS_CALL_ID":
            try:
                call_id = int(ev.get("Value", ""))
            except Exception:
                call_id = None
        if not call_id and etype == "newchannel":
            ch2 = self.channels_rev_get(ch)
            call_id = ch2
        if call_id and ch:
            with self._lock:
                self.channels[call_id] = ch
        if etype == "newchannel" and call_id:
            self.emit({"event": "ring", "call_id": call_id})
        elif etype == "dial":
            st = str(ev.get("DialStatus", "")).upper()
            if st in ("ANSWER",):
                self.emit({"event": "answered", "call_id": call_id, "human": True})
        elif etype == "hangup" and call_id:
            self.emit({"event": "done", "call_id": call_id, "detail": "Hangup"})

    def channels_rev_get(self, ch):
        with self._lock:
            for cid, c in self.channels.items():
                if c == ch:
                    return cid
        return None

    def stop(self):
        super().stop()
        if self.client:
            try:
                self.client.logoff()
            except Exception:
                pass
            try:
                self.client.close()
            except Exception:
                pass


def make_provider(settings: dict) -> TelephonyProvider:
    name = settings.get("provider", "sim")
    if name == "uis":
        return UISCallApiProvider().configure(settings)
    if name == "ami":
        return AsteriskAmiProvider().configure(settings)
    return SimProvider().configure(settings)

