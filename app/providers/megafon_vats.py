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
        base = str(base_url or "").strip().rstrip("/")
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
        hint = {"400": "Validation error — неверные параметры",
                "401": "авторизация: неверный ключ / CRM выключен / сервис недоступен",
                "403": "нет прав на эндпоинт",
                "405": "метод не поддерживается"}.get(str(code), "HTTP " + str(code))
        detail = (raw or "").strip().replace("\n", " ")[:200]
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
