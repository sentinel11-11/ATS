# -*- coding: utf-8 -*-
"""МегаФон ВАТС как подключаемый телеком-провайдер ATS.

Реализован строго по официальной документации «REST API ВАТС (CRM)»
(версия 81 стр.: вебхуки form-urlencoded + JSON API CRM→ВАТС):

  CRM → ВАТС:  https://{domain}/crmapi/v1/{endpoint}, заголовок X-API-KEY
  ВАТС → CRM:  POST form-urlencoded с полями cmd/crm_token/... (стадия 2)

Что покрыто на стадии 1:
  users / groups / telnums / sims (чтение + постраничка),
  caller-ids (+telnums), makecall, history (+inner, json/csv),
  record, domain, subscription/dnd (presence сотрудника).
Методы записи (users/groups/telnums CRUD, blacklist, restrictions, sip,
branches, webhook-подписки и т.п.) — следующие стадии, транспорт общий.

Критические семантики из документации (не забыть!):
- makecall = callback: ВАТС сначала звонит менеджеру (user/group),
  затем соединяет его с клиентом. Аудиопотока в API нет — провайдер
  честно interactive=False, make_channel() возвращает None.
- Ответ makecall {callid, clid}: clid возвращается, только если переданный
  номер «может использоваться для исходящей связи». Расхождение запрошенного
  и фактического clid — громкое предупреждение, а не молчание.
- DND/subscription ИНВЕРТИРОВАНЫ относительно классики: POST включает ПРИЁМ
  звонков, DELETE выключает; state:true = сотрудник доступен.
- Списки users/groups/telnums/sims — конверт {items, info}; history/json —
  голый массив; history/csv — текст без заголовка.
- Статусы history пуша регистро-кривые (Success vs missed) — сравнивать
  только case-insensitive (стадия 2).
"""
import json
import re
import socket
import threading
import urllib.error
import urllib.parse
import urllib.request

from ..telephony import ProviderNotConfigured, TelephonyProvider
from .base import ProviderApiError, resolve_secret

API_PREFIX = "/crmapi/v1"
DEFAULT_API_KEY_ENV = "ATS_MEGAFON_API_KEY"
DEFAULT_CRM_TOKEN_ENV = "ATS_MEGAFON_CRM_TOKEN"


class MegafonApiError(ProviderApiError):
    """Ошибка REST API МегаФон ВАТС. Ключ в сообщение не попадает."""
    pass


def _digits(phone):
    return "".join(c for c in str(phone or "") if c.isdigit())


class MegafonVatsClient:
    """Тонкий HTTP-клиент CRM API ВАТС (X-API-KEY, JSON, stdlib only)."""

    def __init__(self, base_url, api_key, timeout_sec=15):
        raw_base = str(base_url or "").strip()
        m = re.search(r'https?://[^\s\]\)\"\']+', raw_base)
        base = m.group(0) if m else raw_base
        base = base.rstrip("/")
        if not base:
            raise MegafonApiError("megafon_vats.base_url пуст — укажите домен ВАТС")
        if not str(api_key or "").strip():
            raise MegafonApiError("API-ключ ВАТС не задан (api_key / ATS_MEGAFON_API_KEY)")
        if base.lower().endswith(API_PREFIX):
            base = base[: -len(API_PREFIX)]
        self.base_url = base
        self.api_key = str(api_key).strip()
        try:
            self.timeout = max(1, int(timeout_sec or 15))
        except (TypeError, ValueError):
            self.timeout = 15

    # ---------- транспорт ----------
    def _request(self, method, path, params=None, body=None):
        url = self.base_url + API_PREFIX + path
        if params:
            flat = []
            for k, v in params.items():
                if v is None or v is False and not isinstance(v, bool):
                    continue
                if isinstance(v, (list, tuple)):
                    flat += [(k, str(x)) for x in v]
                elif isinstance(v, bool):
                    flat.append((k, "true" if v else "false"))
                else:
                    flat.append((k, str(v)))
            if flat:
                url += "?" + urllib.parse.urlencode(flat)
        data = None
        headers = {"X-API-KEY": self.api_key}
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return self._parse(r.status, r.read(), path)
        except urllib.error.HTTPError as e:
            try:
                raw = e.read().decode("utf-8", "ignore")
            except Exception:
                raw = ""
            raise MegafonApiError(self._http_message(e.code, raw), status=e.code,
                                  payload=raw[:500])
        except urllib.error.URLError as e:
            raise MegafonApiError("сеть/таймаут ВАТС ({}): {}".format(self.base_url, e))
        except (socket.timeout, TimeoutError, ConnectionError, OSError) as e:
            # urlopen может кидать сырой TimeoutError/ConnectionError мимо URLError
            raise MegafonApiError("сеть/таймаут ВАТС ({}): {}".format(self.base_url, e))

    @staticmethod
    def _http_message(code, raw):
        detail = (raw or "").strip().replace("\n", " ")[:200]
        if "Domain disabled" in detail:
            return "ВАТС МегаФон отключена (Domain disabled): включите интеграцию в кабинете МегаФон и проверьте статус услуги ВАТС"
        hint = {"400": "Validation error — неверные параметры",
                "401": "авторизация: неверный ключ / CRM выключен / сервис недоступен",
                "403": "нет прав на эндпоинт",
                "405": "метод не поддерживается"}.get(str(code), "HTTP " + str(code))
        return "ВАТС {}: {}".format(hint, detail or "пустой ответ")

    @staticmethod
    def _parse(status, raw, path):
        if status == 204 or not raw:
            return None
        text = raw.decode("utf-8", "ignore")
        if path.endswith("/csv"):
            return text
        try:
            return json.loads(text or "null")
        except ValueError:
            return text

    def _get(self, path, params=None):
        return self._request("GET", path, params=params)

    def _post(self, path, body=None, params=None):
        return self._request("POST", path, params=params, body=body)

    def _put(self, path, body=None):
        return self._request("PUT", path, body=body)

    def _delete(self, path, params=None):
        return self._request("DELETE", path, params=params)

    def _fetch_all(self, path, params=None, limit=100):
        """Вычитать весь список через start/limit (конверт {items, info})."""
        out, start = [], 0
        for _ in range(1000):  # защита от зацикленного сервера
            p = dict(params or {})
            p["start"], p["limit"] = start, limit
            env = self._get(path, p) or {}
            items = env.get("items") or []
            out += items
            info = env.get("info") or {}
            total = info.get("total")
            if total is None:
                break
            start += len(items)
            if start >= int(total or 0) or not items:
                break
        return out

    # ---------- сотрудники (§6) ----------
    def get_users(self, search=None, start=None, limit=None, with_status=False):
        p = {}
        if search:
            p["search"] = search
        if start is not None:
            p["start"] = start
        if limit is not None:
            p["limit"] = limit
        if with_status:
            p["with"] = "status"
        return self._get("/users", p)

    def get_users_all(self):
        return self._fetch_all("/users")

    def get_user(self, login, with_status=False):
        p = {"with": "status"} if with_status else None
        return self._get("/users/" + urllib.parse.quote(str(login), safe=""), p)

    def get_user_groups(self, login):
        return self._get("/users/" + urllib.parse.quote(str(login), safe="") + "/groups")

    # presence: POST = ВКЛЮЧИТЬ приём звонков, DELETE = выключить (§6.7-6.9)
    def get_subscription(self, login, group_id=None):
        p = {"group_id": group_id} if group_id else None
        res = self._get("/users/" + urllib.parse.quote(str(login), safe="") + "/subscription", p)
        return bool((res or {}).get("state"))

    def set_subscription(self, login, enabled, group_id=None):
        p = {"group_id": group_id} if group_id else None
        path = "/users/" + urllib.parse.quote(str(login), safe="") + "/subscription"
        if enabled:
            self._post(path, params=p)
        else:
            self._delete(path, params=p)

    def get_dnd(self, login):
        """True = сотрудник ПРИНИМАЕТ звонки (DND выключен). Осторожно: инверсия!"""
        res = self._get("/users/" + urllib.parse.quote(str(login), safe="") + "/dnd")
        return bool((res or {}).get("state"))

    def set_dnd(self, login, enabled):
        """enabled=True — включить приём звонков (POST), False — выключить (DELETE)."""
        path = "/users/" + urllib.parse.quote(str(login), safe="") + "/dnd"
        if enabled:
            self._post(path)
        else:
            self._delete(path)

    # ---------- Отделы (§8) ----------
    def get_groups(self, search=None, start=None, limit=None):
        p = {}
        if search:
            p["search"] = search
        if start is not None:
            p["start"] = start
        if limit is not None:
            p["limit"] = limit
        return self._get("/groups", p)

    def get_groups_all(self):
        return self._fetch_all("/groups")

    def get_group(self, group_id):
        return self._get("/groups/" + urllib.parse.quote(str(group_id), safe=""))

    # ---------- Номера (§9) и SIM (§10, чтение) ----------
    def get_telnums(self, search=None, start=None, limit=None):
        p = {}
        if search:
            p["search"] = search
        if start is not None:
            p["start"] = start
        if limit is not None:
            p["limit"] = limit
        return self._get("/telnums", p)

    def get_telnums_all(self):
        return self._fetch_all("/telnums")

    def get_telnum(self, telnum):
        return self._get("/telnums/" + urllib.parse.quote(_digits(telnum), safe=""))

    def get_sims(self, search=None, start=None, limit=None):
        p = {}
        if search:
            p["search"] = search
        if start is not None:
            p["start"] = start
        if limit is not None:
            p["limit"] = limit
        return self._get("/sims", p)

    def get_sims_all(self):
        return self._fetch_all("/sims")

    def get_sim(self, telnum):
        return self._get("/sims/" + urllib.parse.quote(_digits(telnum), safe=""))

    # ---------- Исходящие номера (§13) ----------
    def get_caller_ids(self):
        """Настройки исходящих: {main, users[], groups[], regions[]}."""
        return self._get("/caller-ids")

    def get_caller_id_telnums(self):
        """Список [{telnum, enabled}] — что РАЗРЕШЕНО для исходящей связи."""
        return self._get("/caller-ids/telnums")

    def allowed_caller_ids(self):
        """Только enabled==true — единственные номера, которые ATS вправе
        подставлять как CallerID (fail-closed против спуфинга)."""
        res = self.get_caller_id_telnums() or []
        return [_digits(r.get("telnum")) for r in res
                if r.get("enabled") is True and _digits(r.get("telnum"))]

    # ---------- Исходящий звонок (§5) ----------
    def make_call(self, phone, user=None, group=None, clid=None, show_phone=None):
        """POST /makecall → {callid, clid?}. Callback: ВАТС звонит сначала
        менеджеру (user/group), затем соединяет с клиентом. Медиа нет."""
        num = _digits(phone)
        if not num:
            raise MegafonApiError("make_call: пустой номер телефона")
        body = {"phone": num}
        if user:
            body["user"] = str(user)
        if group:
            body["group"] = str(group)
        if clid and _digits(clid):
            body["clid"] = _digits(clid)
        if show_phone is not None:
            body["show_phone"] = bool(show_phone)
        res = self._post("/makecall", body) or {}
        if not res.get("callid"):
            raise MegafonApiError("makecall: ВАТС не вернула callid: {!r}".format(res)[:200])
        return res

    # ---------- История (§4) ----------
    def get_history_json(self, **params):
        """Голый массив [{uid, type, status, client, ...}]. Параметры — как
        в доке: uid/start/end/period/type/limit/user/diversion/client/groups/
        first_answered/processMissed/missedStatus (pass-through, без выдумок)."""
        return self._get("/history/json", params or None)

    def get_history_csv(self, **params):
        return self._get("/history/csv", params or None)

    def get_history_inner_json(self, **params):
        return self._get("/history/inner/json", params or None)

    def get_history_inner_csv(self, **params):
        return self._get("/history/inner/csv", params or None)

    @staticmethod
    def parse_history_csv(text):
        """Разобрать CSV внешней истории. Колонки строго по §4.1:
        uid,type,client,via,start,wait,duration,record,rating."""
        rows = []
        for line in str(text or "").splitlines():
            line = line.strip()
            if not line:
                continue
            cols = [c.strip() for c in line.split(",")]
            rows.append(dict(zip(
                ("uid", "type", "client", "via", "start", "wait", "duration",
                 "record", "rating"), cols + [""] * 9)))
        return rows

    # ---------- Домен/запись (§12) ----------
    def get_record_settings(self):
        return self._get("/record")

    def get_domain(self):
        return self._get("/domain")

    def connection_check(self):
        """Проверка связи для будущей кнопки «Проверить соединение» (§51 ТЗ):
        три опорных чтения; первое упавшее — в ошибке с конкретным статусом."""
        users = self.get_users(limit=1)
        telnums = self.get_telnums(limit=1)
        caller_ids = self.get_caller_id_telnums()
        return {"users": users, "telnums": telnums, "caller_ids": caller_ids}


class MegafonVatsProvider(TelephonyProvider):
    """Адаптер МегаФон ВАТС: исходящие через makecall (callback на менеджера).

    Конфигурация (settings.megafon_vats): base_url (https://{domain}),
    api_key или api_key_env (default ATS_MEGAFON_API_KEY), crm_token /
    crm_token_env (для вебхуков, стадия 2), default_user / default_group
    (кому ВАТС звонит первым), timeout_sec=15.

    interactive=False: REST API не даёт аудиоканал — agent-поток движок
    честно завершит как no_channel (см. engine._on_answered), голосовой AI
    возможен только через медиа-слой Asterisk.
    """
    name = "megafon_vats"
    interactive = False

    def __init__(self):
        super().__init__()
        self.cfg = {}
        self.client = None
        self._external = {}  # call_id -> {"callid":..., "clid":...}
        self._lock = threading.RLock()

    def configure(self, settings: dict):
        cfg = (settings or {}).get("megafon_vats", {}) or {}
        base_url = str(cfg.get("base_url") or "").strip()
        if not base_url:
            raise ProviderNotConfigured(
                "megafon_vats не настроен: укажите settings.megafon_vats.base_url "
                "(домен ВАТС вида https://vatsXXXX.megafon.ru).")
        api_key = resolve_secret(cfg, "api_key", "api_key_env", DEFAULT_API_KEY_ENV)
        if not api_key:
            raise ProviderNotConfigured(
                "megafon_vats не настроен: задайте API-ключ (megafon_vats.api_key "
                "или env {}) — поле «Ключ для авторизации в АТС» в панели ВАТС.".format(
                    cfg.get("api_key_env") or DEFAULT_API_KEY_ENV))
        try:
            self.client = MegafonVatsClient(base_url, api_key, cfg.get("timeout_sec", 15))
        except MegafonApiError as e:
            raise ProviderNotConfigured("megafon_vats: {}".format(e))
        self.cfg = dict(cfg)
        return self

    def dial(self, req: dict):
        """Исходящий через POST /makecall. user/group обязателен: ВАТС должна
        знать, кому звонить первым (callback). clid — из пула номеров ATS."""
        if self.client is None:
            raise ProviderNotConfigured("megafon_vats: провайдер не сконфигурирован")
        req = req or {}
        phone = _digits(req.get("phone"))
        if not phone:
            raise ProviderNotConfigured("megafon_vats.dial: пустой номер телефона")
        user = str(req.get("user") or self.cfg.get("default_user") or "").strip()
        group = str(req.get("group") or self.cfg.get("default_group") or "").strip()
        if not user and not group:
            raise ProviderNotConfigured(
                "megafon_vats.dial: не задан сотрудник (user/group) — ВАТС не знает, "
                "кому звонить первым. Укажите megafon_vats.default_user (логин) "
                "или default_group в настройках.")
        clid = _digits(req.get("caller_id")) or None
        try:
            res = self.client.make_call(phone, user=user or None, group=group or None,
                                        clid=clid)
        except MegafonApiError as e:
            raise ProviderNotConfigured("megafon_vats.dial: {}".format(e))
        got_clid = _digits(res.get("clid"))
        if clid and got_clid != clid:
            # Честность CallerID (§65 ТЗ): ВАТС могла подставить свой номер
            # (запрошенный запрещён для исходящей). Не молчим — фиксируем факт.
            print("[megafon_vats] ВНИМАНИЕ: запрошен clid={}, ВАТС вернула {!r} "
                  "(callid={}) — исходящий номер подставила сама ВАТС.".format(
                      clid, res.get("clid"), res.get("callid")))
        with self._lock:
            self._external[req.get("call_id")] = {"callid": str(res.get("callid")),
                                                  "clid": got_clid}
        print("[megafon_vats] makecall ok: call_id={} phone={} user={} callid={} clid={}".format(
            req.get("call_id"), phone, user or group, res.get("callid"),
            got_clid or clid or "-"))
        return True

    def external_id(self, call_id):
        """Вернуть {callid, clid} последнего makecall для нашего call_id."""
        with self._lock:
            return dict(self._external.get(call_id) or {})

    def hangup(self, call_id):
        # В REST API ВАТС нет метода завершения звонка со стороны CRM:
        # teardown'ом управляет сама ВАТС по событиям. No-op осознанный.
        print("[megafon_vats] hangup({}): метод отсутствует в API ВАТС, "
              "завершение — на стороне ВАТС.".format(call_id))

    def make_channel(self, call_id, phone):
        # Аудиоканала в REST API нет (§6 ТЗ) — честный None вместо притворства.
        return None

    def connect_operator(self, call_id, operator_ref=None, progress=None):
        """ACD-принятие на ВАТС: голосовой бридж менеджер↔клиент уже построен
        самой ВАТС в момент ACCEPTED (callback), строить нечего. True ⟺ звонок
        реально отвечен и не завершён (проверка по журналу, без звонков к API).
        progress("answered") — факт из журнала; фазу ringing не эмулируем."""
        from .. import db as _db
        try:
            call_id = int(call_id)
        except (TypeError, ValueError):
            return False
        call = _db.fetch1("SELECT * FROM calls WHERE id=?", (call_id,))
        if not call or not call.get("answered_at") or call.get("ended_at") \
                or call.get("status") == "done":
            return False
        if progress:
            try:
                progress("answered")
            except Exception:
                pass
        return True


# ---------- Вебхуки ВАТС → ATS (стадия 2): нормализация ----------
# ВАТС шлёт POST application/x-www-form-urlencoded с полями cmd/crm_token/...
# на единую точку; в ответ ждёт JSON. События движка — нормализованные:
# движок НЕ знает про crm_token/diversion/telnum (§3 ТЗ).

# event.type → событие движка (сырец сохраняется в norm["raw_type"])
MEGAFON_EVENT_MAP = {
    "INCOMING": "ring",        # поступил входящий (у менеджера звонит телефон)
    "OUTGOING": "ring",        # менеджер совершает исходящий (дозвон до клиента)
    "ACCEPTED": "answered",    # трубку сняли; для исходящего callback — см. допущение ниже
    "COMPLETED": "done",       # разговор завершён (положили трубку после разговора)
    "CANCELLED": "canceled",    # сброшен до ответа / не дождался
    "TRANSFERRED": "transferred",
}

# history.status → (result движка, retryable); сравнение — case-insensitive:
# в доке "Success", но "missed" строчными (§2.1). Inbound missed обрабатывается
# отдельно (задача «перезвонить»), out missed = клиент не взял трубку.
MEGAFON_HISTORY_MAP = {
    "success": ("ok", True),       # ok резолвится по flow вызывающей стороной
    "missed": ("missed", False),   # in: пропущенный; out: перемаппится в no_answer
    "cancel": ("failed", True),
    "busy": ("busy", True),
    "notavailable": ("no_answer", True),
    "notallowed": ("failed", False),  # запрет направления — ретраи бессмысленны
    "notfound": ("failed", False),    # нет такого SIP-номера — чинить конфиг
}


def parse_vats_start(ts):
    """YYYYmmddTHHMMSSZ (UTC) → локальное 'YYYY-MM-DD HH:MM:SS'. '' при мусоре."""
    import datetime as _dt
    s = str(ts or "").strip()
    try:
        d = _dt.datetime.strptime(s, "%Y%m%dT%H%M%SZ").replace(tzinfo=_dt.timezone.utc)
        return d.astimezone().strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return ""


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
            if v not in out:
                out.append(v)
    return out


def _num(v, default=0):
    try:
        return int(float(str(v).strip() or default))
    except (TypeError, ValueError):
        return default


def map_megafon_webhook(form):
    """Нормализовать form-вебхук ВАТС в событие движка (dict) или None.

    Возвращает нормализованное событие; engine работает только с ним.
    cmd=contact здесь НЕ обрабатывается (требует синхронного ответа с именем —
    его отдаёт HTTP-хендлер напрямую, см. app/api.py).
    """
    f = {str(k): ("" if v is None else str(v)) for k, v in (form or {}).items()}
    cmd = f.get("cmd", "").strip().lower()
    if cmd in ("", "contact"):
        return None
    callid = f.get("callid", "").strip()
    if not callid and cmd in ("event", "history", "rating"):
        return None
    norm = {"provider": "megafon_vats", "cmd": cmd, "external_call_id": callid,
            "phone": f.get("phone", "").strip(), "raw": dict(f)}
    if cmd == "event":
        raw_type = f.get("type", "").strip().upper()
        if raw_type not in MEGAFON_EVENT_MAP:
            return None
        direction = f.get("direction", "").strip().lower()
        if direction not in ("in", "out"):
            # direction обязателен по доку, но страхуемся выводом из типа
            direction = "out" if raw_type == "OUTGOING" else "in" if raw_type == "INCOMING" else ""
        norm.update({"event": MEGAFON_EVENT_MAP[raw_type], "raw_type": raw_type,
                     "direction": direction, "user": f.get("user", "").strip(),
                     "diversion": f.get("diversion", "").strip(),
                     "group": f.get("groupRealName", "").strip(),
                     "telnum": f.get("telnum", "").strip(),
                     "second_callid": f.get("second_callid", "").strip()})
        return norm
    if cmd == "history":
        htype = f.get("type", "").strip().lower()
        if htype not in ("in", "out"):
            return None
        norm.update({"event": "history", "direction": htype,
                     "user": f.get("user", "").strip(),
                     "diversion": f.get("diversion", "").strip(),
                     "group": f.get("groupRealName", "").strip(),
                     "telnum": f.get("telnum", "").strip(),
                     "status": f.get("status", "").strip().lower(),
                     "start": parse_vats_start(f.get("start")),
                     "duration": _num(f.get("duration")),
                     "wait": _num(f.get("wait")),
                     "rating": _num(f.get("rating")),
                     "missed_status": f.get("missedStatus", "").strip(),
                     "record_url": f.get("link", "").strip()})
        return norm
    if cmd == "rating":
        norm.update({"event": "rating", "phone": f.get("phone", "").strip(),
                     "rating": _num(f.get("rating")),
                     "user": f.get("user", "").strip(),
                     "direction": "in"})
        return norm
    if cmd == "webhook":
        # Пока единственный тип — sipregs_error (§15.6/§18): неуспешная
        # SIP-регистрация. Звонков не касается — алерт администратору.
        norm.update({"event": "provider_webhook",
                     "webhook_type": f.get("type", "").strip(),
                     "data": f.get("data", "")})
        return norm
    return None


def webhook_fingerprint(norm):
    """Устойчивый отпечаток события для идемпотентности (§21 ТЗ):
    sha256 по стабильным полям. Повторная доставка того же вебхука
    даёт тот же отпечаток и не обрабатывается дважды."""
    import hashlib
    n = norm or {}
    parts = [str(n.get("provider", "")), str(n.get("cmd", "")),
             str(n.get("raw_type") or n.get("event") or ""),
             str(n.get("external_call_id", "")), str(n.get("phone", "")),
             str(n.get("direction", "")), str(n.get("status", "")),
             str(n.get("start", "")), str(n.get("duration", "")),
             str(n.get("rating", "")), str(n.get("webhook_type", ""))]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


# ---------- Синк пула номеров (стадия 3): чистый планировщик ----------
def _canon_digits(v):
    """Канонические цифры для СРАВНЕНИЯ номеров: 8XXXXXXXXXX → 7XXXXXXXXXX."""
    d = _digits(v)
    if len(d) == 11 and d.startswith("8"):
        d = "7" + d[1:]
    return d


def plan_number_sync(caller_telnums, telnums, local_rows):
    """План сверки локального пула с ВАТС (pure, без I/O).

    caller_telnums — ответ GET /caller-ids/telnums ([{telnum, enabled}]),
    telnums — ответ GET /telnums (items, важен флаг disabled),
    local_rows — строки numbers provider=megafon_vats.
    Номер пригоден ⟺ enabled==true И telnums.disabled!=true.
    Всего, чего нет в caller-ids, не существует (fail-closed).
    Возвращает {"add":[{number,label,carrier,provider_ref}],
                "enable":[id], "disable":[id]}.
    """
    allowed = {}
    for r in caller_telnums or []:
        d = _canon_digits((r or {}).get("telnum"))
        if d:
            allowed[d] = bool((r or {}).get("enabled") is True)
    info = {}
    for t in telnums or []:
        d = _canon_digits((t or {}).get("telnum"))
        if d:
            info[d] = t or {}
    usable = {d for d, en in allowed.items()
              if en and not bool((info.get(d) or {}).get("disabled"))}
    by_digits = {}
    for row in local_rows or []:
        d = _canon_digits((row or {}).get("number"))
        if d and d not in by_digits:
            by_digits[d] = row
    add, enable, disable = [], [], []
    for d in sorted(usable):
        row = by_digits.get(d)
        if row is None:
            t = info.get(d) or {}
            add.append({"number": d, "label": str(t.get("name") or "")[:200],
                        "carrier": "megafon",
                        "provider_ref": str(t.get("telnum") or d)[:64]})
        elif not row.get("enabled_outgoing"):
            enable.append(row["id"])
    for d, row in by_digits.items():
        if d not in usable and row.get("enabled_outgoing"):
            disable.append(row["id"])
    return {"add": add, "enable": sorted(enable), "disable": sorted(disable)}
