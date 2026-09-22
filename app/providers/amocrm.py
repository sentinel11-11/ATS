# -*- coding: utf-8 -*-
"""Модуль официальной интеграции с amoCRM (REST API v4).

Соответствует официальному спецификации amoCRM API v4 для телефонии и CRM:
- Регистрация звонков через официальный эндпоинт POST /api/v4/calls
- Поиск контактов и сделок по номеру телефона
- Автоматическое создание контактов для новых клиентов
- Добавление примечаний (заметки к контактам/сделкам)
- Автоматическое создание задач (напр. перезвон при пропущенном звонке)
- Создание сделок в секции "Неразобранное" (Unsorted)
- Синхронизация списка пользователей/менеджеров amoCRM
"""
import datetime
import json
import re
import socket
import urllib.error
import urllib.parse
import urllib.request

from .base import ProviderApiError, resolve_secret

DEFAULT_AMOCRM_TOKEN_ENV = "ATS_AMOCRM_TOKEN"


def _sanitize_phone(phone: str) -> str:
    if not phone:
        return ""
    digits = re.sub(r"\D", "", str(phone))
    if len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]
    return "+" + digits if digits else str(phone).strip()


def _is_masked_value(val):
    if not val:
        return False
    s = str(val).strip()
    return s in ("********", "••••••••") or set(s) <= {"*"} or set(s) <= {"•"}


class AmoCrmApiError(ProviderApiError):
    """Ошибка API amoCRM."""
    pass


class AmoCrmClient:
    """HTTP-клиент официального REST API v4 amoCRM."""

    def __init__(self, subdomain, access_token, responsible_user_id=1, timeout_sec=15):
        self.subdomain = str(subdomain or "").strip()
        if not self.subdomain:
            raise AmoCrmApiError("Не указан субдомен amoCRM (например, company из company.amocrm.ru)")

        token_str = str(access_token or "").strip()
        if not token_str or _is_masked_value(token_str):
            raise AmoCrmApiError("Токен доступа amoCRM не задан или маскирован")

        self.access_token = token_str
        try:
            self.responsible_user_id = int(responsible_user_id or 1)
        except (TypeError, ValueError):
            self.responsible_user_id = 1

        try:
            self.timeout = max(1, int(timeout_sec or 15))
        except (TypeError, ValueError):
            self.timeout = 15

        self.base_url = f"https://{self.subdomain}.amocrm.ru/api/v4"

    def _request(self, method, path, params=None, body=None):
        url = self.base_url + path
        if params:
            flat = [(k, str(v)) for k, v in params.items() if v is not None]
            if flat:
                url += "?" + urllib.parse.urlencode(flat)

        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "Axioma-ATS/2.0 (amoCRM Telephony Integration)"
        }

        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, headers=headers, method=method)

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                raw = r.read()
                if not raw or r.status == 204:
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
            msg = f"amoCRM HTTP {e.code}: {(raw or 'Ошибка сервера')[:300]}"
            raise AmoCrmApiError(msg, status=e.code, payload=raw[:500])
        except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError, OSError) as e:
            raise AmoCrmApiError(f"Ошибка сети amoCRM ({self.subdomain}): {e}")

    def get_account_info(self):
        """Получить информацию об аккаунте amoCRM, пользователях и воронках."""
        return self._request("GET", "/account", params={"with": "users,pipelines"})

    def get_users(self):
        """Получить список пользователей/менеджеров аккаунта amoCRM."""
        try:
            res = self._request("GET", "/users")
            if isinstance(res, dict) and "_embedded" in res and "users" in res["_embedded"]:
                return res["_embedded"]["users"]
            if isinstance(res, list):
                return res
            return []
        except AmoCrmApiError:
            return []

    def search_contacts(self, phone):
        """Поиск существующих контактов по номеру телефона."""
        phone_clean = _sanitize_phone(phone)
        if not phone_clean:
            return []
        try:
            res = self._request("GET", "/contacts", params={"query": phone_clean})
            if isinstance(res, dict) and "_embedded" in res and "contacts" in res["_embedded"]:
                return res["_embedded"]["contacts"]
            return []
        except AmoCrmApiError:
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

    def register_call(self, phone, direction, duration, status_code, result_text="", record_link="", responsible_user_id=None, created_at=None):
        """Зарегистрировать звонок через официальный эндпоинт телефонии amoCRM POST /api/v4/calls.
        
        Статусы вызова (call_status) по докумeнтации amoCRM:
        1 - Успешный разговор (Answered)
        2 - Пропущенный (Missed)
        3 - Занято (Busy)
        4 - Ошибка / Отклонен (Failed)
        6 - Недоступен (Unavailable)
        """
        phone_clean = _sanitize_phone(phone)
        resp_id = int(responsible_user_id or self.responsible_user_id)

        try:
            c_code = int(status_code)
        except (TypeError, ValueError):
            c_code = 1

        try:
            dur = max(0, int(duration or 0))
        except (TypeError, ValueError):
            dur = 0

        ts = int(created_at or datetime.datetime.now().timestamp())

        payload = [{
            "phone": phone_clean,
            "direction": "inbound" if str(direction or "").lower() in ("in", "inbound", "входящий") else "outbound",
            "duration": dur,
            "source": "Axioma ATS",
            "call_status": c_code,
            "call_result": str(result_text or "").strip()[:500],
            "link": str(record_link or "").strip(),
            "responsible_user_id": resp_id,
            "created_at": ts
        }]

        return self._request("POST", "/calls", body=payload)

    def create_task(self, text, complete_till=None, responsible_user_id=None, entity_id=None, entity_type="contacts"):
        """Создать задачу (напр. Перезвонить) в amoCRM."""
        resp_id = int(responsible_user_id or self.responsible_user_id)
        till = int(complete_till or (datetime.datetime.now().timestamp() + 3600 * 4))  # по умолч +4 часа

        task = {
            "text": str(text or "Связаться с клиентом").strip(),
            "complete_till": till,
            "responsible_user_id": resp_id,
            "task_type_id": 1  # 1 = Звонок
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

    def create_unsorted(self, source_name, phone, contact_name="", responsible_user_id=None):
        """Создать неразобранную заявку (Unsorted SIP/Form) в воронке amoCRM."""
        phone_clean = _sanitize_phone(phone)
        resp_id = int(responsible_user_id or self.responsible_user_id)

        payload = [{
            "source_name": str(source_name or "Axioma ATS Call").strip(),
            "source_uid": f"ats_{int(datetime.datetime.now().timestamp())}",
            "created_at": int(datetime.datetime.now().timestamp()),
            "pipeline_id": None,
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
