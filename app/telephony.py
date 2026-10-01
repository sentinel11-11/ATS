# -*- coding: utf-8 -*-
"""Телефония ATS v2: единый интерфейс TelephonyProvider и адаптеры.

Реализации:
- SimProvider        — симуляция (тесты, демо, «сухой» пилот без модемов/UIS);
- UISCallApiProvider — адаптер UIS (Call API / SIP) — каркас, заполняется по ответу UIS (T01);
- AsteriskAmiProvider— адаптер медиа-слоя Asterisk/AMI (SIP-транк к оператору) — каркас;
- MegafonVatsProvider— МегаФон ВАТС (REST CRM API, см. app/providers/megafon_vats.py).

Фабрика выбирает адаптер по settings["provider"]. Новые операторы (МТС и др.) = новые классы
с тем же интерфейсом (мультиоператорность, задача T38).
"""
import base64
import queue
import random
import re
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
    needs_operator_ext = False  # True: для бриджа обязателен ext оператора (иначе accept запрещён)
    supports_media = True  # False: транспорта без аудиослоя (REST-клиент без TTS/IVR)

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

    def connect_operator(self, call_id, operator_ext, progress=None):
        """Соединить абонента с оператором (ACD accept). Возвращает True, если
        голосовой бридж реально состоялся, False — если нет (движок откатит
        принятие). None = «бридж неприменим» (симуляция/legacy) — движок
        зафиксирует бридж формально. progress — опциональный колбэк
        progress("ringing"|"answered"), который провайдер вызывает в РЕАЛЬНЫЕ
        моменты протокола (не для красоты); движок двигает по нему FSM."""
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
                "Данные выдаёт UIS после запроса T01.")
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
AMI_NUMBER_FORMATS = ("raw", "digits", "d10", "e164", "ru8")


def digits_only(value):
    return re.sub(r"\D", "", str(value or ""))


def format_outbound_number(value, fmt="raw"):
    """Формат набора для SIP-транка. Операторы диктуют свой: «ru8» — 8XXXXXXXXXX
    (Мультиком), «e164» — +7XXXXXXXXXX, «d10» — XXXXXXXXXX, «digits» — все цифры,
    «raw» — строка как лежит в БД (обратная совместимость)."""
    d = digits_only(value)
    if not d:
        return ""
    fmt = str(fmt or "raw").strip().lower()
    if fmt == "digits":
        return d
    if fmt == "d10":
        return d[-10:] if len(d) >= 10 else d
    if fmt == "e164":
        if len(d) == 11 and d[0] in ("7", "8"):
            return "+7" + d[1:]
        return "+" + d
    if fmt == "ru8":
        if len(d) == 11 and d[0] in ("7", "8"):
            return "8" + d[1:]
        if len(d) == 10:
            return "8" + d
        return d
    return str(value).strip()


def ami_secret(cfg=None):
    """Пароль AMI: settings.ami.secret, иначе env из settings.ami.secret_env.

    Второй вариант нужен, чтобы секрет не лежал в ats.db: /etc/ats/ats.env с
    0640 root:ats защищён лучше, а в API и UI значение настроек и так маскируется
    (то есть прочитать сохранённый пароль из интерфейса невозможно — источник истины
    /etc/asterisk/manager-ats.conf).
    """
    from .providers.base import resolve_secret
    return resolve_secret(cfg or {}, "secret", "secret_env", "ATS_AMI_SECRET")


class AsteriskAmiProvider(TelephonyProvider):
    """Адаптер медиа-слоя Asterisk по AMI (SIP-транк к оператору).

    Конфигурация (settings.ami): host, port=5038, user, secret, trunk (имя PJSIP/SIP-транка),
    tech (PJSIP|SIP — технология канала), number_format/caller_id_format (raw|digits|d10|e164|ru8),
    dial_prefix (надбавка после нормализации), context (контекст набора), ring_timeout_ms,
    play_message/play_context (озвучка текста через AGI в диалплане),
    inbound_enabled/inbound_contexts (входящие с транка в журнал),
    op_context/op_wait_sec/bridge_timeout (перевод на оператора).

    Originate: Channel PJSIP/{trunk}/{dial_prefix}{phone}, CallerID "{ats-call-<id>}" <caller_id>.
    Маркер в CallerIDName используется, чтобы сопоставить канал с нашим call_id (Newchannel).
    Карта событий AMI → события движка (проверена на протокольном фейке, живой стенд T14):
    Newchannel -> ring, Dial(ANSWER) -> answered, Hangup -> done,
    OriginateResponse(Failure) -> failed. Newchannel в контексте оператора ->
    inbound/inbound_answer/inbound_end (входящий с DID попадает в журнал ATS и в CRM).
    connect_operator(): Originate операторского leg'а -> ожидание OriginateResponse
    (ответ оператора) -> AMI Bridge двух каналов. True только если бридж состоялся.
    Настройки бриджа (settings.ami): op_context (свой диалплан вместо Wait),
    op_wait_sec=45, op_ring_timeout_ms=30000, acd_answer_timeout=35, bridge_timeout=10.
    """
    name = "ami"
    needs_operator_ext = True
    # Текст сообщения озвучивает сам Asterisk (AGI в play_context): движок ждёт
    # финал по событию, а не «спит секунду и пишет «доставлено»».
    dialplan_plays_text = True

    def __init__(self):
        super().__init__()
        self.cfg = {}
        self.client = None
        self.channels = {}   # call_id -> channel name
        self._inbound = {}   # channel name -> True (входящий с транка, отслеживаем)
        self._originate = {}  # action_id dial-Originate -> call_id (ждём OriginateResponse)
        self._lock = threading.RLock()

    def configure(self, settings: dict):
        cfg = settings.get("ami", {}) or {}
        secret = ami_secret(cfg)
        if not (cfg.get("host") and cfg.get("user") and secret):
            raise ProviderNotConfigured(
                "Asterisk AMI не настроен (settings.ami.host/user/secret). Пароль AMI — "
                "это НЕ пароль из письма оператора: его задаёт администратор Asterisk в "
                "/etc/asterisk/manager-ats.conf (секция [ats], строка secret). Хранить его "
                "можно не в БД, а в окружении сервиса: ATS_AMI_SECRET в /etc/ats/ats.env.")
        self.cfg = dict(cfg, secret=secret)   # resolved-значение только в памяти
        from . import asterisk as ami_mod
        try:
            client = ami_mod.AMIClient(cfg["host"], cfg.get("port", 5038),
                                       cfg["user"], secret, timeout=float(cfg.get("timeout", 5)))
            client.connect()
            client.login()
            client.event_handler = self._on_event
        except Exception as e:
            raise ProviderNotConfigured("AMI недоступен ({}): {}".format(cfg.get("host"), e))
        self.client = client
        return self

    def _tech(self):
        return str(self.cfg.get("tech") or "PJSIP").strip() or "PJSIP"

    def _channel_string(self, phone, trunk=None):
        """Диал-строка канала. У технологий свой синтаксис: chan_sip — `SIP/peer/номер`,
        chan_pjsip — `PJSIP/номер@endpoint`. Переопределяется целиком настройкой
        `channel_pattern` (шаблон с {number} и {trunk})."""
        trunk = str(trunk if trunk is not None else self.cfg.get("trunk", ""))
        pattern = str(self.cfg.get("channel_pattern") or "").strip()
        if pattern:
            try:
                return pattern.format(number=phone, trunk=trunk)
            except (KeyError, IndexError, ValueError):
                pass
        if self._tech().upper() == "SIP":
            return "SIP/{}/{}".format(trunk, phone)
        return "PJSIP/{}@{}".format(phone, trunk)

    def _trunk_channel(self, phone):
        return self._channel_string(phone)

    def dial(self, req: dict):
        if not self.client:
            raise ProviderNotConfigured("AMI не подключён")
        phone = format_outbound_number(req["phone"], self.cfg.get("number_format", "raw"))
        if self.cfg.get("dial_prefix"):
            phone = str(self.cfg["dial_prefix"]) + phone
        if not phone:
            raise ProviderNotConfigured(
                "Номер {!r} пуст после нормализации (number_format={})".format(
                    req.get("phone"), self.cfg.get("number_format", "raw")))
        chan = self._trunk_channel(phone)
        cid_num = format_outbound_number(req.get("caller_id", ""),
                                         self.cfg.get("caller_id_format", "digits"))
        cid_name = "ats-call-{}".format(req["call_id"])
        variables = ["ATS_CALL_ID={}".format(req["call_id"])]
        text = str(req.get("text") or "").strip()
        if text and self.cfg.get("play_message", True):
            # base64: в AMI-пакете значения не должны содержать CR/LF, а текст
            # кампании — любое; диалплан декодирует через BASE64_DECODE.
            variables.append("ATS_TEXT_B64=" + base64.b64encode(
                text[:1500].encode("utf-8")).decode("ascii"))
        if self.cfg.get("record_calls", True):
            # по этому флагу диалплан включает MixMonitor (запись на сервере Asterisk)
            variables.append("ATS_RECORD=1")
        params = {
            "Channel": chan,
            "Exten": "s",
            "Context": self.cfg.get("context", "ats-out"),
            "Priority": "1",
            "CallerID": '"{}" <{}>'.format(cid_name, cid_num),
            "Timeout": str(self.cfg.get("ring_timeout_ms", 35000)),
            "Variable": variables,
            "Async": "true",
        }
        resp = self.client.action("Originate", params)
        aid = str(resp.get("ActionID", ""))
        if aid:
            with self._lock:
                self._originate[aid] = req["call_id"]
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

    def connect_operator(self, call_id, operator_ext, progress=None):
        """Соединить абонента с оператором: Originate операторского leg'а,
        ожидание ответа (OriginateResponse), затем AMI Bridge двух каналов.
        True — только если бридж реально состоялся; иначе False и движок откатит
        принятие (звонок останется в очереди, оператор — свободным).
        progress("ringing") — после принятого Originate, progress("answered") —
        после OriginateResponse(success); бридж — только после ответа."""
        def _pg(phase):
            if progress:
                try:
                    progress(phase)
                except Exception:
                    pass
        if not self.client or not operator_ext:
            return False
        with self._lock:
            caller_ch = self.channels.get(call_id)
        if not caller_ch:
            return False
        op_chan = self._channel_string(
            format_outbound_number(operator_ext, self.cfg.get("number_format", "raw"))
            or str(operator_ext),
            self.cfg.get("operator_trunk", self.cfg.get("trunk", "")))
        op_params = {
            "Channel": op_chan,
            "CallerID": '"ATS-ACD" <{}>'.format(self.cfg.get("acd_callerid", "")),
            "Timeout": str(self.cfg.get("op_ring_timeout_ms", 30000)),
            "Async": "true",
        }
        if self.cfg.get("op_context"):
            # Свой диалплан: ответивший оператор попадёт в него (должен ждать бриджа).
            op_params.update({"Exten": "s", "Context": self.cfg["op_context"], "Priority": "1"})
        else:
            # Штатно: ответивший канал ждёт бриджа внутри Wait.
            op_params.update({"Application": "Wait",
                              "Data": str(self.cfg.get("op_wait_sec", 45))})
        aid = "ats-acd-{}-{}".format(call_id, uuid.uuid4().hex[:8])
        try:
            resp = self.client.action("Originate", op_params, action_id=aid)
        except Exception:
            return False
        if str(resp.get("Response", "")).lower() != "success":
            return False
        _pg("ringing")  # Originate принят — операторское плечо звонит
        answer_timeout = float(self.cfg.get("acd_answer_timeout", 35))
        ev = self.client.wait_for(
            lambda m: str(m.get("Event", "")).lower() == "originateresponse"
                      and str(m.get("ActionID", "")) == aid,
            answer_timeout)
        if not ev or str(ev.get("Response", "")).lower() != "success":
            return False
        op_ch = str(ev.get("Channel", ""))
        if not op_ch:
            return False
        _pg("answered")  # OriginateResponse(success) — оператор снял трубку
        try:
            self.client.action("Bridge", {"Channel1": op_ch, "Channel2": caller_ch},
                               timeout=float(self.cfg.get("bridge_timeout", 10)))
        except Exception:
            try:
                self.client.action("Hangup", {"Channel": op_ch}, check=False)
            except Exception:
                pass
            return False
        return True

    def _on_play_variable(self, ev):
        """ATS_PLAY=failed:<причина> — озвучка не состоялась (нет TTS, ошибка AGI).
        При успехе финал отдаёт Hangup. Так в журнале не появится ложное «доставлено»."""
        value = str(ev.get("Value", "") or "")
        if not value.lower().startswith("fail"):
            return
        ch = str(ev.get("Channel", "") or "")
        call_id = self.channels_rev_get(ch)
        if not call_id:
            return
        self.emit({"event": "status", "call_id": call_id, "status": "failed",
                   "detail": ("Озвучка не выполнена: " + value.split(":", 1)[-1])[:200]})

    # ---------- входящие с транка (DID) ----------
    def _inbound_candidate(self, ev):
        """Имя канала, если это входящий с транка (DID в контексте оператора)."""
        if not self.cfg.get("inbound_enabled", True):
            return ""
        allowed = [c.strip() for c in str(self.cfg.get("inbound_contexts", "") or "").split(",")
                   if c.strip()]
        if allowed and str(ev.get("Context", "")) not in allowed:
            return ""
        ch = str(ev.get("Channel", "") or "")
        tech = str(self.cfg.get("inbound_tech") or self.cfg.get("tech") or "PJSIP").strip()
        trunk = str(self.cfg.get("trunk") or "").strip()
        if trunk and not ch.startswith("{}/{}".format(tech, trunk)):
            return ""
        return ch

    def _on_event(self, ev: dict):
        etype = str(ev.get("Event", "")).lower()
        if etype == "originateresponse":
            self._on_originate_response(ev)
            return
        ch = ev.get("Channel")
        if etype == "variableset" and str(ev.get("Variable", "")) == "ATS_PLAY":
            # AGI не знает нашего call_id: итог озвучки ставит диалплан
            self._on_play_variable(ev)
            return
        if etype == "newchannel":
            inbound_ch = self._inbound_candidate(ev)
            if inbound_ch:
                with self._lock:
                    known = inbound_ch in self._inbound
                    self._inbound[inbound_ch] = True
                if not known:
                    self.emit({"event": "inbound", "channel": inbound_ch,
                               "phone": str(ev.get("CallerIDNum") or ev.get("ConnectedLineNum") or ""),
                               "did": str(ev.get("Exten", "") or "")})
                return
        elif etype == "newstate":
            with self._lock:
                tracked = str(ev.get("Channel", "")) in self._inbound
            if tracked and str(ev.get("ChannelState", "")) == "6":  # 6 = UP
                self.emit({"event": "inbound_answer", "channel": str(ev.get("Channel", ""))})
            return
        elif etype == "hangup":
            with self._lock:
                tracked = str(ev.get("Channel", "")) in self._inbound
                if tracked:
                    self._inbound.pop(str(ev.get("Channel", "")), None)
            if tracked:
                self.emit({"event": "inbound_end", "channel": str(ev.get("Channel", "")),
                           "detail": str(ev.get("Cause", "") or "")})
                return
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
            with self._lock:
                for k in [k for k, v in self._originate.items() if v == call_id]:
                    del self._originate[k]
            self.emit({"event": "done", "call_id": call_id, "detail": "Hangup"})

    def _on_originate_response(self, ev: dict):
        """Ответ на dial-Originate: Failure сразу отдаём движку как failed
        (иначе звонок висел бы в dialing до watchdog). Success дальше ведут
        Newchannel/Dial/Hangup. ACD-leg'и сюда не попадают: их ждёт wait_for
        в connect_operator (их ActionID в карте нет)."""
        with self._lock:
            call_id = self._originate.pop(str(ev.get("ActionID", "")), None)
        if not call_id:
            return
        if str(ev.get("Response", "")).lower() != "success":
            detail = str(ev.get("Reason", ev.get("Message", "")))[:200] or "Originate failed"
            self.emit({"event": "status", "call_id": call_id,
                       "status": "failed", "detail": detail})

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


def ami_check(settings: dict) -> dict:
    """Диагностика связки ATS ↔ Asterisk ↔ SIP-транк без единого звонка.

    Проверяет: доступность и логин AMI, версию Asterisk, состояние регистрации у
    оператора (pjsip/sip), наличие контекстов диалплана, кодеков, и то, что пул
    номеров настроен на провайдера `ami`. Возвращает отчёт по чек-листу; `ok` —
    только если пройдены критичные проверки.
    """
    from . import asterisk as ami_mod
    cfg = dict((settings or {}).get("ami") or {})
    tech = str(cfg.get("tech") or "PJSIP").strip().upper() or "PJSIP"
    trunk = str(cfg.get("trunk") or "").strip()
    context = str(cfg.get("context") or "ats-out")
    report = {"ok": False, "tech": tech, "trunk": trunk, "checks": [],
              "number_format": str(cfg.get("number_format") or "raw"),
              "caller_id_format": str(cfg.get("caller_id_format") or "digits")}

    def add(name, ok, detail="", critical=True):
        report["checks"].append({"name": name, "ok": bool(ok), "detail": str(detail)[:600],
                                 "critical": bool(critical)})
        return bool(ok)

    secret = ami_secret(cfg)
    if not (cfg.get("host") and cfg.get("user") and secret):
        add("config", False, "settings.ami: заполните host/user/secret (пароль — из "
                              "/etc/asterisk/manager-ats.conf, либо из ATS_AMI_SECRET)")
        report["error"] = "ami_not_configured"
        return report
    cfg = dict(cfg, secret=secret)
    client = None
    try:
        client = ami_mod.AMIClient(cfg["host"], cfg.get("port", 5038), cfg["user"],
                                   secret, timeout=float(cfg.get("timeout", 5)))
        client.connect()
        client.login()
        add("ami_login", True, "{}:{}".format(cfg["host"], cfg.get("port", 5038)))
    except Exception as e:  # noqa: BLE001
        add("ami_login", False, e)
        report["error"] = "ami_unreachable"
        if client:
            try:
                client.close()
            except Exception:  # noqa: BLE001
                pass
        return report

    def command(cmd, timeout=2.5):
        """CLI через AMI (формат ответа может различаться) — только текстом."""
        try:
            return client.run_command(cmd, timeout=timeout)
        except Exception as e:  # noqa: BLE001
            return "ошибка команды: {}".format(e)

    try:
        ver = command("core show version")
        add("asterisk", "Asterisk" in ver or "version" in ver.lower(), ver.strip().splitlines()[-1:]
            and ver.strip().splitlines()[-1] or ver, critical=False)
        ping = client.action("Ping", check=False)
        add("ping", str(ping.get("Response", "")).lower() == "success",
            ping.get("Message", ""), critical=False)
        if tech == "SIP":
            reg, peers = command("sip show peers"), command("sip show registry")
            trunk_ok = trunk.lower() in reg.lower()
        else:
            reg, peers = command("pjsip show contacts"), command("pjsip show endpoints")
            trunk_ok = (not trunk) or trunk.lower() in peers.lower() or trunk.lower() in reg.lower()
        registered = any(word in reg.lower() for word in
                         ("avail", "requested", "bound", "ok", "registered"))
        add("trunk_configured", trunk_ok,
            ("транк '{}' найден в выводе".format(trunk) if trunk_ok else
             "транк '{}' не найден — проверьте pjsip.conf/sip.conf и имя в настройках".format(trunk)))
        add("registration", registered, reg.strip() or "регистрации/контакта нет — "
                                                        "проверьте логин/пароль и файрвол")
        def context_ok(text, ctx):
            low = (text or "").lower()
            if any(bad in low for bad in ("no such context", "cannot exist", "no context matched")):
                return False
            return ctx.lower() in low
        plan = command("dialplan show {}".format(context))
        add("dialplan", context_ok(plan, context),
            (plan.strip()[:400] or "контекст {} не виден в диалплане".format(context)))
        play_ctx = str(cfg.get("play_context") or "ats-play")
        if cfg.get("play_message", True):
            play = command("dialplan show {}".format(play_ctx))
            add("agi_play", context_ok(play, play_ctx), play.strip()[:300], critical=False)
        g729 = command("module show like codec_g729")
        add("codec_g729", "codec_g729" in g729.lower(), g729.strip()[:200], critical=False)
    finally:
        try:
            client.logoff()
            client.close()
        except Exception:  # noqa: BLE001
            pass

    try:
        from . import db as _db
        row = _db.fetch1("SELECT COUNT(*) AS c FROM numbers WHERE provider='ami' AND active=1"
                        " AND quarantined=0")
        count = int((row or {}).get("c") or 0)
    except Exception:  # noqa: BLE001
        count = 0
    add("numbers_pool", count > 0,
        "активных номеров ami в пуле: {}{}".format(
            count, "" if count else " — добавьте номера с provider=ami (см. docs/MULTICOM_SIP_CONNECT.md)"))
    report["numbers_active"] = count
    report["ok"] = all(c["ok"] for c in report["checks"] if c.get("critical"))
    return report


# Единый список провайдеров телефонии: настройки (settings.provider), пул
# номеров (numbers.provider), CLI (--provider) и валидация в api — все сверяются
# с ним, чтобы «добавили провайдера в реестр, забыли в трёх местах» не повторялось.
PROVIDER_NAMES = ("sim", "uis", "ami", "megafon_vats", "multicom")


def make_provider(settings: dict) -> TelephonyProvider:
    name = str(settings.get("provider") or "").strip().lower()
    if not name:
        raise ProviderNotConfigured(
            "provider не задан (settings.provider пустой) — ATS не запускает звонки. "
            "Укажите провайдера явно: --provider sim|uis|ami|megafon_vats при старте "
            "или Настройки → Провайдер (стенд/тесты: sim).")
    if name == "uis":
        return UISCallApiProvider().configure(settings)
    if name == "ami":
        return AsteriskAmiProvider().configure(settings)
    if name == "sim":
        return SimProvider().configure(settings)
    if name == "megafon_vats":
        from .providers.megafon_vats import MegafonVatsProvider  # лениво: без цикла импорта
        return MegafonVatsProvider().configure(settings)
    if name == "multicom":
        from .providers.multicom import MulticomProvider
        return MulticomProvider().configure(settings)
    # Fail-closed: неизвестное имя (опечатка «uis » и т.п.) — громкая ошибка,
    # а не молчаливый звонок через симулятор.
    raise ProviderNotConfigured(
        "unknown provider: {!r} (ожидалось: {})".format(
            name, " / ".join(PROVIDER_NAMES)))

