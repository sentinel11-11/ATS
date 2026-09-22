# -*- coding: utf-8 -*-
"""Модуль официальной интеграции с amoCRM (REST API v4).

Соответствует официальной спецификации amoCRM API v4 для телефонии и CRM:
- Регистрация звонков через официальный эндпоинт POST /api/v4/calls
  (с идемпотентностью через поле uniq);
- Поиск контактов по номеру телефона (с точной сверкой по хвосту номера);
- Автоматическое создание контактов для новых клиентов;
- Добавление примечаний (заметки к контактам/сделкам);
- Автоматическое создание задач (напр. перезвон при недозвоне);
- Создание заявок "Неразобранное" для ВХОДЯЩИХ звонков (Unsorted SIP);
- Синхронизация списка пользователей/менеджеров amoCRM;
- OAuth: обновление пары access/refresh token по 401 (ротация одноразового
  refresh token с персистентным сохранением через on_tokens_updated);
- квота API: локальный rate limiter + обработка 429 Retry-After.

Статусы звонка amoCRM (call_status) по официальной документации
(developers: CRM Platform → Звонки, POST /api/v4/calls):
  1 — оставил голосовое сообщение
  2 — перезвонить позже
  3 — нет на месте
  4 — разговор состоялся
  5 — неверный номер
  6 — не дозвонился
  7 — номер занят
"""
import datetime
import json
import re
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from .base import ProviderApiError, resolve_secret

DEFAULT_AMOCRM_TOKEN_ENV = "ATS_AMOCRM_TOKEN"

# Официальные коды call_status amoCRM (см. докстринг модуля).
AMO_CALL_LEFT_MESSAGE = 1
AMO_CALL_CALLBACK_LATER = 2
AMO_CALL_NOT_IN_PLACE = 3
AMO_CALL_CONVERSATION = 4
AMO_CALL_WRONG_NUMBER = 5
AMO_CALL_NO_REACH = 6
AMO_CALL_BUSY = 7


def _sanitize_phone(phone: str) -> str:
    if not phone:
        return ""
    digits = re.sub(r"\D", "", str(phone))
    if len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]
    return "+" + digits if digits else str(phone).strip()


def phone_tail(phone: str, n: int = 10) -> str:
    """Последние n цифр номера — критерий точного совпадения контакта.

    amoCRM сама ищет сущность для звонка по последним 10 цифрам номера,
    поэтому и мы сверяем кандидатов из поиска так же.
    """
    digits = re.sub(r"\D", "", str(phone or ""))
    return digits[-n:] if len(digits) >= n else digits


def contact_phone_tails(contact: dict) -> set:
    """Все телефонные «хвосты» контакта amoCRM из custom_fields_values."""
    tails = set()
    for cf in (contact or {}).get("custom_fields_values") or []:
        if str(cf.get("field_code") or "").upper() != "PHONE":
            continue
        for v in cf.get("values") or []:
            t = phone_tail(v.get("value"))
            if t:
                tails.add(t)
    return tails


def pick_exact_contact(contacts, phone):
    """Выбрать из кандидатов поиска контакт с ТОЧНЫМ совпадением номера.

    amoCRM /contacts?query= ищет нечётко (по подстроке во многих полях),
    поэтому «первый из списка» не гарантирует нужный номер.

    Возвращает (contact, conflict):
      contact — единственное точное совпадение или None;
      conflict — True, если точных совпадений несколько (дубли в CRM;
                 в этом случае берётся первый, но факт дубля логируется).
    """
    tail = phone_tail(phone)
    if not tail:
        return None, False
    exact = [c for c in (contacts or []) if tail in contact_phone_tails(c)]
    if len(exact) > 1:
        return exact[0], True
    if exact:
        return exact[0], False
    return None, False


def _is_masked_value(val):
    if not val:
        return False
    s = str(val).strip()
    return s in ("********", "••••••••") or set(s) <= {"*"} or set(s) <= {"•"}


class AmoCrmApiError(ProviderApiError):
    """Ошибка API amoCRM."""
    pass


class AmoCrmAuthError(AmoCrmApiError):
    """Ошибка авторизации amoCRM (401, в т.ч. после неудачного refresh)."""
    pass


class AmoCrmClient:
    """HTTP-клиент официального REST API v4 amoCRM.

    Режимы авторизации (взаимодополняющие):
    1) Долгосрочный токен (приватная интеграция, вкладка «Ключи») —
       достаточно access_token; refresh не требуется и не выполняется.
    2) OAuth-пара (короткоживущий access + одноразовый refresh) —
       при 401 клиент один раз обменивает refresh_token на новую пару
       (POST /oauth2/access_token, grant_type=refresh_token) и повторяет
       запрос. Новая пара передаётся в on_tokens_updated для атомарного
       сохранения (refresh token одноразовый: потерять новый = потерять
       доступ). Обмен защищён блокировкой — параллельные потоки ждут одного
       refresh, а не жгут одноразовый токен в нескольких обменах.
    """

    def __init__(self, subdomain, access_token, responsible_user_id=1, timeout_sec=15,
                 client_id="", client_secret="", refresh_token="", redirect_uri="",
                 on_tokens_updated=None, rate_limit_rps=7):
        self.subdomain = str(subdomain or "").strip()
        if not self.subdomain:
            raise AmoCrmApiError("Не указан субдомен amoCRM (например, company из company.amocrm.ru)")

        token_str = str(access_token or "").strip()
        if not token_str or _is_masked_value(token_str):
            raise AmoCrmApiError("Токен доступа amoCRM не задан или маскирован")

        self.access_token = token_str
        self.client_id = str(client_id or "").strip()
        self.client_secret = str(client_secret or "").strip()
        self.refresh_token = str(refresh_token or "").strip()
        self.redirect_uri = str(redirect_uri or "").strip()
        self.on_tokens_updated = on_tokens_updated

        try:
            self.responsible_user_id = int(responsible_user_id or 1)
        except (TypeError, ValueError):
            self.responsible_user_id = 1

        try:
            self.timeout = max(1, int(timeout_sec or 15))
        except (TypeError, ValueError):
            self.timeout = 15

        self.base_url = f"https://{self.subdomain}.amocrm.ru/api/v4"
        self.oauth_url = f"https://{self.subdomain}.amocrm.ru/oauth2/access_token"

        # Квота amoCRM: 7 запросов/с на интеграцию (док. «Ограничения и
        # рекомендации»). Локальный лимитер не даёт упираться в 429.
        try:
            rps = float(rate_limit_rps or 7)
        except (TypeError, ValueError):
            rps = 7.0
        self._min_interval = 1.0 / max(0.5, min(rps, 7.0))
        self._rl_lock = threading.Lock()
        self._last_request_ts = 0.0

        self._refresh_lock = threading.Lock()
        self._refresh_tried = False  # защита от refresh-цикла в рамках одного запроса

    # ---------- транспорт ----------

    def _throttle(self):
        with self._rl_lock:
            now = time.monotonic()
            wait = self._last_request_ts + self._min_interval - now
            if wait > 0:
                time.sleep(wait)
            self._last_request_ts = time.monotonic()

    def _raw_request(self, method, url, body=None):
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "Axioma-ATS/2.0 (amoCRM Telephony Integration)"
        }
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        self._throttle()
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            raw = r.read()
            if not raw or r.status == 204:
                return r.status, {"ok": True}
            text = raw.decode("utf-8", "ignore")
            try:
                return r.status, json.loads(text)
            except ValueError:
                return r.status, {"ok": True, "raw": text}

    def _request(self, method, path, params=None, body=None, _retries_429=2, _allow_refresh=True):
        url = self.base_url + path
        if params:
            flat = [(k, str(v)) for k, v in params.items() if v is not None]
            if flat:
                url += "?" + urllib.parse.urlencode(flat)

        attempts = 0
        while True:
            try:
                _status, data = self._raw_request(method, url, body)
                return data
            except urllib.error.HTTPError as e:
                try:
                    raw = e.read().decode("utf-8", "ignore")
                except Exception:
                    raw = ""
                if e.code == 401 and _allow_refresh and self._refresh_supported():
                    if self._refresh_access_token():
                        _allow_refresh = False  # ровно один refresh+retry на запрос
                        continue
                    raise AmoCrmAuthError(
                        "amoCRM 401: access token отклонён, refresh не удался — "
                        "требуется повторная авторизация интеграции",
                        status=401, payload=raw[:500])
                if e.code == 401:
                    raise AmoCrmAuthError(
                        f"amoCRM HTTP 401: доступ запрещён (проверьте токен). {(raw or '')[:200]}",
                        status=401, payload=raw[:500])
                if e.code == 429 and attempts < _retries_429:
                    attempts += 1
                    retry_after = e.headers.get("Retry-After") if e.headers else None
                    try:
                        delay = min(5.0, max(0.5, float(retry_after))) if retry_after else min(5.0, 0.6 * (2 ** attempts))
                    except (TypeError, ValueError):
                        delay = min(5.0, 0.6 * (2 ** attempts))
                    time.sleep(delay)
                    continue
                msg = f"amoCRM HTTP {e.code}: {(raw or 'Ошибка сервера')[:300]}"
                raise AmoCrmApiError(msg, status=e.code, payload=raw[:500])
            except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError, OSError) as e:
                raise AmoCrmApiError(f"Ошибка сети amoCRM ({self.subdomain}): {e}")

    # ---------- OAuth ----------

    def _refresh_supported(self):
        return bool(self.client_id and self.client_secret and self.refresh_token)

    def _refresh_access_token(self):
        """Обменять одноразовый refresh token на новую пару access/refresh.

        Один обмен на весь процесс-очередь: остальные потоки ждут на lock
        и пользуются уже обновлённым токеном (иначе несколько параллельных
        обменов сожгут одноразовый refresh token гонкой).
        """
        with self._refresh_lock:
            if self._refresh_tried:
                return False
            self._refresh_tried = True
            body = {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "grant_type": "refresh_token",
                "refresh_token": self.refresh_token,
                "redirect_uri": self.redirect_uri or "https://localhost/"
            }
            req = urllib.request.Request(
                self.oauth_url,
                data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                headers={"Content-Type": "application/json", "Accept": "application/json",
                         "User-Agent": "Axioma-ATS/2.0 (amoCRM OAuth refresh)"},
                method="POST")
            try:
                self._throttle()
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    data = json.loads(r.read().decode("utf-8") or "{}")
            except Exception as e:
                print("[amocrm] oauth refresh failed:", e)
                return False
            access = str(data.get("access_token") or "").strip()
            refresh = str(data.get("refresh_token") or "").strip()
            if not access or not refresh:
                return False
            self.access_token = access
            self.refresh_token = refresh
            try:
                expires_in = int(data.get("expires_in") or 0)
            except (TypeError, ValueError):
                expires_in = 0
            if callable(self.on_tokens_updated):
                try:
                    self.on_tokens_updated({
                        "access_token": access,
                        "refresh_token": refresh,
                        "expires_in": expires_in,
                        "server_time": data.get("server_time")
                    })
                except Exception as e:
                    # Персистентность критична (refresh одноразовый), но
                    # падение колбэка не должно ронять сам запрос.
                    print("[amocrm] on_tokens_updated persist failed:", e)
            return True

    def _reset_refresh_flag(self):
        self._refresh_tried = False

    # ---------- аккаунт / справочники ----------

    def get_account_info(self, with_param=None):
        """Получить информацию об аккаунте amoCRM.

        По умолчанию — чистый GET /api/v4/account (надёжная проверка связи
        и прав токена). with_param — только документированные для v4
        значения (напр. "task_types", "amojo_id", "users_groups",
        "datetime_settings", "version"); недокументированные значения
        amoCRM игнорирует, поэтому для check задаём None.
        """
        params = {"with": with_param} if with_param else None
        return self._request("GET", "/account", params=params)

    def get_task_types(self):
        """Справочник типов задач аккаунта (для выбора реального task_type_id)."""
        res = self._request("GET", "/account", params={"with": "task_types"})
        try:
            return res["_embedded"]["task_types"]
        except (TypeError, KeyError):
            return []

    def get_users(self):
        """Получить список пользователей/менеджеров аккаунта amoCRM.

        Ошибки НЕ проглатываются: 401/403/429/5xx поднимаются наружу —
        «CRM недоступна» не должна выглядеть как «пользователей нет».
        """
        res = self._request("GET", "/users")
        if isinstance(res, dict) and "_embedded" in res and "users" in res["_embedded"]:
            return res["_embedded"]["users"]
        if isinstance(res, list):
            return res
        return []

    # ---------- контакты ----------

    def search_contacts(self, phone):
        """Поиск существующих контактов по номеру телефона.

        Ошибки НЕ проглатываются: иначе 401/429/5xx выглядит как
        «контакт не найден» и дальше создаётся дубль.
        """
        phone_clean = _sanitize_phone(phone)
        if not phone_clean:
            return []
        res = self._request("GET", "/contacts", params={"query": phone_clean})
        if isinstance(res, dict) and "_embedded" in res and "contacts" in res["_embedded"]:
            return res["_embedded"]["contacts"]
        return []

    def create_contact(self, name, phone, responsible_user_id=None):
        """Создать новый контакт в amoCRM с номером телефона."""
        phone_clean = _sanitize_phone(phone)
        resp_id = int(responsible_user_id or self.responsible_user_id)
        payload = [{
            "name": str(name or f"Клиент {phone_clean}").strip(),
            "responsible_user_id": resp_id,
            "custom_fields_values": [
                {
                    "field_code": "PHONE",
                    "values": [
                        {
                            "value": phone_clean,
                            "enum_code": "WORK"
                        }
                    ]
                }
            ]
        }]
        res = self._request("POST", "/contacts", body=payload)
        if isinstance(res, dict) and "_embedded" in res and "contacts" in res["_embedded"]:
            return res["_embedded"]["contacts"][0]
        return res

    # ---------- телефония ----------

    def register_call(self, phone, direction, duration, status_code, result_text="",
                      record_link="", responsible_user_id=None, created_at=None,
                      uniq="", call_responsible=""):
        """Зарегистрировать звонок через официальный эндпоинт телефонии amoCRM POST /api/v4/calls.

        Статусы вызова (call_status) по официальной документации amoCRM:
          1 — оставил голосовое сообщение
          2 — перезвонить позже
          3 — нет на месте
          4 — разговор состоялся
          5 — неверный номер
          6 — не дозвонился
          7 — номер занят

        uniq — уникальный идентификатор звонка на стороне ATS
        (идемпотентность: повторный POST с тем же uniq не создаёт дубль).
        call_responsible — имя сотрудника, ответственного за звонок
        (строка; для аналитики звонков в amoCRM).
        """
        phone_clean = _sanitize_phone(phone)
        resp_id = int(responsible_user_id or self.responsible_user_id)

        try:
            c_code = int(status_code)
        except (TypeError, ValueError):
            c_code = AMO_CALL_NO_REACH

        try:
            dur = max(0, int(duration or 0))
        except (TypeError, ValueError):
            dur = 0

        ts = int(created_at or datetime.datetime.now().timestamp())

        call = {
            "phone": phone_clean,
            "direction": "inbound" if str(direction or "").lower() in ("in", "inbound", "входящий") else "outbound",
            "duration": dur,
            "source": "Axioma ATS",
            "call_status": c_code,
            "call_result": str(result_text or "").strip()[:500],
            "responsible_user_id": resp_id,
            "created_at": ts
        }
        link = str(record_link or "").strip()
        if link:
            call["link"] = link
        uniq = str(uniq or "").strip()
        if uniq:
            call["uniq"] = uniq
        call_resp = str(call_responsible or "").strip()
        if call_resp:
            call["call_responsible"] = call_resp[:255]

        return self._request("POST", "/calls", body=[call])

    # ---------- задачи / примечания ----------

    def create_task(self, text, complete_till=None, responsible_user_id=None,
                    entity_id=None, entity_type="contacts", task_type_id=1):
        """Создать задачу (напр. Перезвонить) в amoCRM.

        task_type_id — id типа задачи из справочника аккаунта
        (GET /api/v4/account?with=task_types); 1 — обычная задача по
        умолчанию. Переопределяется настройкой интеграции.
        """
        resp_id = int(responsible_user_id or self.responsible_user_id)
        till = int(complete_till or (datetime.datetime.now().timestamp() + 3600 * 4))  # по умолч +4 часа

        try:
            tt_id = int(task_type_id or 1)
        except (TypeError, ValueError):
            tt_id = 1

        task = {
            "text": str(text or "Связаться с клиентом").strip(),
            "complete_till": till,
            "responsible_user_id": resp_id,
            "task_type_id": tt_id
        }

        if entity_id and entity_type:
            task["entity_id"] = int(entity_id)
            task["entity_type"] = str(entity_type)

        return self._request("POST", "/tasks", body=[task])

    def add_note(self, entity_type, entity_id, note_type, text):
        """Добавить примечание на таймлайн контакта или сделки."""
        payload = [{
            "entity_id": int(entity_id),
            "note_type": str(note_type or "common"),
            "params": {
                "text": str(text or "").strip()
            }
        }]
        return self._request("POST", f"/{entity_type}/notes", body=payload)

    # ---------- неразобранное (ТОЛЬКО входящие) ----------

    def create_unsorted(self, source_name, phone, contact_name="", responsible_user_id=None,
                        duration=0, called_at=None, record_link=""):
        """Создать неразобранную SIP-заявку в воронке amoCRM.

        ВАЖНО: POST /api/v4/leads/unsorted/sip предназначен только для
        ВХОДЯЩИХ звонков (исходящий, инициированный сотрудником/роботом,
        в неразобранное не попадает — регистрируется через /api/v4/calls).
        Для SIP-заявки amoCRM требует metadata с данными звонка.
        source_uid — uuid4 (timestamp-схема давала коллизии в одну секунду).
        """
        phone_clean = _sanitize_phone(phone)
        resp_id = int(responsible_user_id or self.responsible_user_id)
        ts = int(called_at or datetime.datetime.now().timestamp())

        try:
            dur = max(0, int(duration or 0))
        except (TypeError, ValueError):
            dur = 0

        metadata = {
            "from": phone_clean,
            "phone": phone_clean,
            "called_at": ts,
            "duration": dur,
            "service_code": "axioma_ats",
            "is_call_event_needed": True
        }
        link = str(record_link or "").strip()
        if link:
            metadata["link"] = link

        payload = [{
            "source_name": str(source_name or "Axioma ATS Call").strip(),
            "source_uid": f"ats_{uuid.uuid4().hex}",
            "created_at": ts,
            "metadata": metadata,
            "_embedded": {
                "contacts": [
                    {
                        "name": str(contact_name or f"Новый клиент {phone_clean}").strip(),
                        "responsible_user_id": resp_id,
                        "custom_fields_values": [
                            {
                                "field_code": "PHONE",
                                "values": [{"value": phone_clean, "enum_code": "WORK"}]
                            }
                        ]
                    }
                ]
            }
        }]

        return self._request("POST", "/leads/unsorted/sip", body=payload)
