# -*- coding: utf-8 -*-
"""Мультиком (MultiCom) как подключаемый телеком-провайдер ATS.

Поддерживает интеграцию с агрегатором Мультиком (REST API + SIP/Trunk):
- Проверка связи и получение профиля аккаунта;
- Синхронизация номеров пула Caller ID;
- Совершение исходящих вызовов (makecall) с корректным CallerID из пула ATS;
- Приём вебхуков статуса звонка (idempotent, fail-closed по секрету) —
  события коррелируются с звонками ATS по external_call_id и двигают FSM
  движка (дозвон/ответ/завершение), а значит и автодозвон, ACD и CRM-пуши.

Контракт провайдера (см. app/telephony.py TelephonyProvider):
    configure(settings) / dial(req: dict) / external_id(call_id) /
    hangup(call_id) / check()
Движок НЕ знает про HTTP-контракт Мультикома — только про нормализованные
события, которые возвращает map_multicom_webhook().

ВАЖНО (честно про ограничения):
- Аудиослоя здесь нет (интерактивный IVR/TTS требует медиа-сервера:
  Asterisk/FreeSWITCH поверх SIP-транка Мультикома). Поэтому flow=message
  фиксируется как no_media, а flow=agent — как no_channel.
- connect_operator не реализован: API агрегатора не даёт команды «соединить
  два плеча». Перевод на сотрудника = либо ACD-очередь ATS + «мягкий» bridge
  (оператор набирает клиента сам), либо SIP-транк в Asterisk (там и бридж).
"""
import hashlib
import json
import re
import socket
import threading
import urllib.error
import urllib.parse
import urllib.request

from ..telephony import ProviderNotConfigured, TelephonyProvider
from .base import ProviderApiError, resolve_secret

DEFAULT_API_KEY_ENV = "ATS_MULTICOM_API_KEY"
DEFAULT_WEBHOOK_SECRET_ENV = "ATS_MULTICOM_WEBHOOK_TOKEN"

DEFAULT_PATHS = {
    "account_path": "/account",
    "numbers_path": "/numbers",
    "makecall_path": "/calls/make",
    "hangup_path": "/calls/{call_id}/hangup",
}

# status/state/event из вебхука Мультикома → событие движка ATS.
MULTICOM_EVENT_MAP = {
    "answered": "answered",
    "connected": "answered",
    "bridged": "answered",
    "answer": "answered",
    "talking": "answered",
    "in_progress": "answered",
    "completed": "completed",
    "ended": "completed",
    "finished": "completed",
    "success": "completed",
    "done": "completed",
    "hangup": "completed",
    "busy": "busy",
    "occupied": "busy",
    "user_busy": "busy",
    "no_answer": "no_answer",
    "noanswer": "no_answer",
    "timeout": "no_answer",
    "ring_no_answer": "no_answer",
    "failed": "failed",
    "rejected": "failed",
    "error": "failed",
    "call_rejected": "failed",
    "unavailable": "failed",
    "cancel": "canceled",
    "canceled": "canceled",
    "cancelled": "canceled",
    "ring": "ring",
    "ringing": "ring",
    "dialing": "ring",
    "originate": "ring",
    "missed": "no_answer",
}
# Сырец статуса, который означает «разговор был, но оборвался» — трактуем
# как завершение, а не как провал (иначе автодозвон поролит ретраи).
MULTICOM_DROP_STATUSES = {"dropped", "disconnect", "disconnected", "abandoned"}


def _is_masked_value(val):
    if not val:
        return False
    s = str(val).strip()
    if not s:
        return False
    if s == "********" or s == "••••••••":
        return True
    chars = set(s)
    return chars <= {"*"} or chars <= {"•"} or chars <= {"*", "•"}


def _digits(phone):
    return re.sub(r"\D", "", str(phone or ""))


def phone_variants(phone):
    """Варианты написания номера для поиска контакта (digits/+/8↔7)."""
    d = _digits(phone)
    out = []
    for v in (d, "+" + d if d else ""):
        if v and v not in out:
            out.append(v)
    if len(d) == 11 and d[0] in ("7", "8"):
        alt = ("8" if d[0] == "7" else "7") + d[1:]
        for v in (alt, "+" + alt):
            if v and v not in out:
                out.append(v)
    return out


def normalize_number(phone):
    """E.164 без «+» для API оператора: 8… → 7…, нецифры выкидываются."""
    d = _digits(phone)
    if len(d) == 11 and d.startswith("8"):
        d = "7" + d[1:]
    return d


class MulticomApiError(ProviderApiError):
    """Ошибка API агрегатора Мультиком."""
    pass


class MulticomClient:
    """HTTP-клиент REST API / SIP агрегатора Мультиком.

    Пути эндпоинтов настраиваются (paths в секции multicom): публичного
    описания API агрегатора у нас нет, а контракт «makecall/номера/статусы»
    у операторов отличается полем за полем — правим настройки, а не код.
    """

    def __init__(self, api_url, api_key, account_id="", sip_host="", sip_user="", sip_secret="",
                 timeout_sec=15, paths=None):
        raw_url = str(api_url or "").strip()
        m = re.search(r'https?://[^\s\]\"\']+', raw_url)
        url = m.group(0) if m else raw_url
        self.api_url = url.rstrip("/") if url else "https://api.multicom.ru/v1"

        key_str = str(api_key or "").strip()
        if not key_str or _is_masked_value(key_str):
            raise MulticomApiError("API-ключ Мультиком не задан (заполните API Key в настройках Мультиком)")

        self.api_key = key_str
        self.account_id = str(account_id or "").strip()
        self.sip_host = str(sip_host or "sip.multicom.ru").strip()
        self.sip_user = str(sip_user or "").strip()
        self.sip_secret = str(sip_secret or "").strip()
        self.paths = dict(DEFAULT_PATHS)
        for k, v in (paths or {}).items():
            if k in self.paths and str(v or "").strip():
                self.paths[k] = "/" + str(v).strip().lstrip("/")

        try:
            self.timeout = max(1, int(timeout_sec or 15))
        except (TypeError, ValueError):
            self.timeout = 15

    def _request(self, method, path, params=None, body=None):
        url = self.api_url + path
        if params:
            flat = [(k, str(v)) for k, v in params.items() if v is not None]
            if flat:
                url += "?" + urllib.parse.urlencode(flat)
        data = None
        headers = {
            "Authorization": "Bearer " + self.api_key,
            "X-Api-Key": self.api_key,
            "Accept": "application/json"
        }
        if self.account_id:
            headers["X-Account-Id"] = self.account_id

        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"

        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                raw = r.read()
                if not raw:
                    return {"ok": True}
                text = raw.decode("utf-8", "ignore")
                try:
                    return json.loads(text)
                except ValueError:
                    return {"ok": True, "raw": text}
        except urllib.error.HTTPError as e:
            try:
                raw = e.read().decode("utf-8", "ignore")
            except Exception:
                raw = ""
            msg = f"Мультиком HTTP {e.code}: {(raw or 'ошибка сервера')[:200]}"
            raise MulticomApiError(msg, status=e.code, payload=raw[:500])
        except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError, OSError) as e:
            raise MulticomApiError(f"Ошибка сети Мультиком ({self.api_url}): {e}")

    def get_account_info(self):
        """Проверить подключение и получить профиль аккаунта."""
        try:
            return self._request("GET", self.paths["account_path"])
        except MulticomApiError as e:
            if e.status in (404, 405):
                # Эндпоинт пинга/статуса
                return self._request("GET", "/status")
            raise

    def get_numbers(self):
        """Получить список выделенных номеров компании."""
        try:
            res = self._request("GET", self.paths["numbers_path"])
        except MulticomApiError:
            return []
        if isinstance(res, list):
            return res
        if isinstance(res, dict):
            for key in ("items", "numbers", "_embedded"):
                v = res.get(key)
                if isinstance(v, list):
                    return v
                if isinstance(v, dict) and isinstance(v.get("numbers"), list):
                    return v["numbers"]
            if isinstance(res.get("items"), dict):
                return list(res["items"].values())
        return []

    def make_call(self, phone, caller_id=None, user=None, direction="out",
                  callback_number=None, record=None, client_reference=None):
        """Инициировать исходящий вызов через Мультиком.

        phone — номер клиента (нормализуется в E.164 без «+»),
        caller_id — номер из пула ATS (то, что увидит абонент),
        callback_number — если оператор работает по callback-схеме (сначала
        звоним сотруднику на softphone, потом клиенту),
        client_reference — наш call_id, чтобы оператор вернул его в вебхуке.
        """
        payload = {
            "phone": normalize_number(phone),
            "caller_id": normalize_number(caller_id) or None,
            "user": str(user or "admin").strip(),
            "direction": "inbound" if str(direction or "").lower() in ("in", "inbound") else "outbound",
        }
        if callback_number:
            payload["from"] = normalize_number(callback_number)
        if record is not None:
            payload["record"] = bool(record)
        if client_reference:
            payload["client_reference"] = str(client_reference)[:64]
        payload = {k: v for k, v in payload.items() if v is not None}
        return self._request("POST", self.paths["makecall_path"], body=payload)

    def hangup_call(self, call_id):
        """Завершить звонок по внешнему id (если оператор это умеет)."""
        path = self.paths["hangup_path"]
        if "{call_id}" in path:
            path = path.format(call_id=urllib.parse.quote(str(call_id)))
            return self._request("POST", path)
        return self._request("POST", path, body={"call_id": str(call_id)})

    @staticmethod
    def external_call_id(res):
        """Вытащить внешний id звонка из ответа оператора (по-разному в API)."""
        if not isinstance(res, dict):
            return ""
        for k in ("call_id", "callid", "id", "uuid", "call_uuid", "external_id"):
            v = res.get(k)
            if v:
                return str(v).strip()[:64]
        for wrapper in ("data", "result", "call"):
            inner = res.get(wrapper)
            if isinstance(inner, dict):
                got = MulticomClient.external_call_id(inner)
                if got:
                    return got
        return ""


class MulticomProvider(TelephonyProvider):
    """Провайдер телефонии Мультиком для ATS.

    dial(req) — контрактный метод движка (req — dict, см. telephony.py).
    Поддержка вызова dial(phone=..., caller_id=...) оставлена ради
    обратной совместимости старых вызовов.
    """

    name = "multicom"
    interactive = False       # интерактивного канала (DTMF/ASR) в REST-агрегаторе нет
    needs_operator_ext = False
    supports_media = False    # аудио (TTS/запись речи) не отсюда

    def __init__(self):
        super().__init__()
        self.client = None
        self.settings = {}
        self.cfg = {}
        self._lock = threading.RLock()
        self._external = {}   # our call_id -> {"callid":…, "clid":…}

    def configure(self, settings: dict):
        self.settings = dict(settings or {})
        mcfg = self.settings.get("multicom") or {}
        self.cfg = dict(mcfg)
        api_url = str(mcfg.get("api_url") or "https://api.multicom.ru/v1").strip()
        api_key = resolve_secret(mcfg, "api_key", "api_key_env", DEFAULT_API_KEY_ENV)
        account_id = str(mcfg.get("account_id") or "").strip()
        sip_host = str(mcfg.get("sip_host") or "sip.multicom.ru").strip()
        sip_user = str(mcfg.get("sip_user") or "").strip()
        sip_secret = str(mcfg.get("sip_secret") or "").strip()

        if not api_key:
            raise ProviderNotConfigured("Мультиком не настроен: укажите API Key в настройках Мультиком")

        self.client = MulticomClient(
            api_url=api_url,
            api_key=api_key,
            account_id=account_id,
            sip_host=sip_host,
            sip_user=sip_user,
            sip_secret=sip_secret,
            timeout_sec=mcfg.get("timeout_sec", 15),
            paths={k: mcfg.get(k) for k in DEFAULT_PATHS},
        )
        return self

    # ---------- контракт движка ----------

    def dial(self, req=None, phone=None, caller_id="", **kwargs):
        """Исходящий вызов. req — dict от движка {call_id, phone, caller_id, flow…}.

        Ошибка оператора поднимается как ProviderNotConfigured: движок в
        _start_one честно зафиксирует failed + автодозвон, а не «успех», после
        которого звонок висел бы до watchdog.
        """
        if self.client is None:
            raise ProviderNotConfigured("Мультиком провайдер не инициализирован")
        if isinstance(req, dict):
            r = dict(req)
        else:
            # legacy-форма: dial(phone, caller_id=...) / dial(None, phone=...)
            p = req if isinstance(req, str) else phone
            r = {"phone": p, "caller_id": caller_id}
            r.update({k: v for k, v in kwargs.items() if k != "req"})
        num = normalize_number(r.get("phone"))
        if not num:
            raise ProviderNotConfigured("multicom.dial: пустой номер телефона")
        user = str(r.get("user") or self.cfg.get("default_user") or "ats").strip()
        clid = normalize_number(r.get("caller_id")) or None
        try:
            res = self.client.make_call(
                phone=num, caller_id=clid, user=user,
                callback_number=self.cfg.get("callback_number") or r.get("callback_number"),
                record=self.cfg.get("record"),
                client_reference=("ats-call-%s" % r["call_id"]) if r.get("call_id") else None)
        except MulticomApiError as e:
            raise ProviderNotConfigured("multicom.dial: {}".format(e))
        ext = self.client.external_call_id(res)
        got_clid = _digits((res or {}).get("clid") or (res or {}).get("caller_id")) if isinstance(res, dict) else ""
        if clid and got_clid and got_clid != clid:
            print("[multicom] ВНИМАНИЕ: запрошен caller_id={}, оператор вернул {} "
                  "(исходящий номер подставил оператор).".format(clid, got_clid))
        with self._lock:
            self._external[r.get("call_id")] = {"callid": ext, "clid": got_clid}
        print("[multicom] makecall ok: call_id={} phone={} clid={} ext={}".format(
            r.get("call_id"), num, clid or "-", ext or "-"))
        return {"ok": True, "call_id": ext, "external_id": ext,
                "provider": self.name, "raw": res}

    def external_id(self, call_id):
        """{"callid":…, "clid":…} последнего dial для нашего call_id.

        Движок пишет callid в calls.external_call_id — по нему вебхук
        оператора снова находится на звонок ATS (переживает рестарт)."""
        with self._lock:
            return dict(self._external.get(call_id) or {})

    def hangup(self, call_id):
        """Завершение звонка: best-effort. Нет внешнего id/эндпоинта — no-op
        с логом (падение движка из-за отсутствующей команды оператора — зло)."""
        with self._lock:
            ext = (self._external.get(call_id) or {}).get("callid")
        if not ext or self.client is None:
            print("[multicom] hangup({}: нет внешнего id) — no-op".format(call_id))
            return False
        try:
            self.client.hangup_call(ext)
            return True
        except Exception as e:
            print("[multicom] hangup({}) не выполнен: {}".format(ext, str(e)[:200]))
            return False

    def check(self) -> dict:
        if self.client is None:
            raise ProviderNotConfigured("Мультиком провайдер не инициализирован")
        info = self.client.get_account_info()
        try:
            numbers = len(self.client.get_numbers())
        except Exception:
            numbers = -1
        return {"ok": True, "account": info, "numbers": numbers}


# ---------- Вебхук оператора → события движка ----------

def webhook_fingerprint(ev):
    """Отпечаток события для дедупликации (повторные POST оператора = один раз)."""
    src = "{}|{}|{}".format(ev.get("provider", ""), ev.get("external_call_id", ""),
                            ev.get("raw_type", ev.get("event", "")))
    return hashlib.sha1(src.encode("utf-8")).hexdigest()[:40]


def verify_webhook_secret(settings, headers=None, qparams=None):
    """Секрет вебхука Мультикома: fail-closed, как у UIS/МегаФона/amoCRM.

    Заголовок X-Multicom-Secret или ?secret=. Без настроенного
    multicom.webhook_secret маршрут закрыт — иначе любой, кто знает URL,
    мог бы «завершать» чужие звонки и портить статистику кампаний.
    """
    mcfg = (settings or {}).get("multicom") or {}
    expected = resolve_secret(mcfg, "webhook_secret", "webhook_secret_env",
                              DEFAULT_WEBHOOK_SECRET_ENV)
    if not expected:
        return False, "webhook_disabled"
    headers = headers or {}
    qparams = qparams or {}
    provided = str(headers.get("X-Multicom-Secret") or
                   (qparams.get("secret") or [""])[0] or "").strip()
    if not provided:
        return False, "secret_required"
    import hmac as hmac_mod
    if not hmac_mod.compare_digest(provided, expected):
        return False, "bad_secret"
    return True, ""


def map_multicom_webhook(form_or_json):
    """Нормализовать входящий вебхук Мультиком в событие движка ATS.

    Принимает и плоский JSON, и form-encoded, и вложенный {"call": {…}} —
    операторы присылают всё три вида. Возвращает None, если событие ни с чем
    не связать (нет внешнего id звонка) — такие ответы не должны
    «съедаться» молча, хендлер отвечает 422.
    """
    raw = form_or_json or {}
    if isinstance(raw, dict):
        f = dict(raw)
        inner = f.get("call") or f.get("data")
        if isinstance(inner, dict) and inner:
            merged = dict(inner)
            merged.update({k: v for k, v in f.items() if k not in ("call", "data")})
            f = merged
    else:
        f = {}

    callid = ""
    for k in ("call_id", "id", "external_call_id", "callid", "uuid", "call_uuid"):
        v = str(f.get(k) or "").strip()
        if v:
            callid = v
            break
    if not callid:
        return None

    raw_status = str(f.get("status") or f.get("state") or f.get("event") or
                     f.get("event_type") or "").strip().lower()
    event_type = MULTICOM_EVENT_MAP.get(raw_status)
    if event_type is None:
        if raw_status in MULTICOM_DROP_STATUSES:
            event_type = "dropped"
        else:
            event_type = raw_status or "event"

    def _int(*keys):
        for k in keys:
            v = f.get(k)
            if str(v or "").lstrip("-").isdigit():
                return int(v)
        return 0

    phone = ""
    for k in ("phone", "client", "number", "dst", "to", "callee"):
        v = str(f.get(k) or "").strip()
        if v:
            phone = v
            break

    direction = str(f.get("direction") or f.get("dir") or "").strip().lower()
    if direction in ("in", "incoming"):
        direction = "in"
    elif direction in ("out", "outgoing", "dial"):
        direction = "out"
    else:
        direction = ""

    norm = {
        "provider": "multicom",
        "event": event_type,
        "raw_type": raw_status,
        "external_call_id": callid[:64],
        "phone": phone,
        "duration": _int("duration", "duration_sec", "billsec"),
        "record_url": str(f.get("record_url") or f.get("recording_url") or
                          f.get("record") or "").strip()[:500],
        "direction": direction,
        "clid": _digits(f.get("clid") or f.get("caller_id") or f.get("from")),
        "user": str(f.get("user") or f.get("internal") or f.get("extension") or "").strip()[:64],
        "detail": str(f.get("cause") or f.get("reason") or f.get("status_text") or "").strip()[:200],
        "raw": f,
    }
    if norm["clid"]:
        norm["caller_id"] = norm["clid"]
    return norm
