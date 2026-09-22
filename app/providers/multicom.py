# -*- coding: utf-8 -*-
"""Мультиком (MultiCom) как подключаемый телеком-провайдер ATS.

Поддерживает интеграцию с агрегатором Мультиком (REST API + SIP/Trunk):
- Проверка связи и получение профиля аккаунта;
- Синхронизация номеров пула Caller ID;
- Совершение исходящих вызовов (dial/make_call).
"""
import json
import re
import socket
import urllib.error
import urllib.parse
import urllib.request

from ..telephony import ProviderNotConfigured, TelephonyProvider
from .base import ProviderApiError, resolve_secret

DEFAULT_API_KEY_ENV = "ATS_MULTICOM_API_KEY"


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


class MulticomApiError(ProviderApiError):
    """Ошибка API агрегатора Мультиком."""
    pass


class MulticomClient:
    """HTTP-клиент REST API / SIP агрегатора Мультиком."""

    def __init__(self, api_url, api_key, account_id="", sip_host="", sip_user="", sip_secret="", timeout_sec=15):
        raw_url = str(api_url or "").strip()
        m = re.search(r'https?://[^\s\]\)\"\']+', raw_url)
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
            return self._request("GET", "/account")
        except MulticomApiError as e:
            if e.status in (404, 405):
                # Эндпоинт пинга/статуса
                return self._request("GET", "/status")
            raise

    def get_numbers(self):
        """Получить список выделенных номеров компании."""
        try:
            res = self._request("GET", "/numbers")
            if isinstance(res, list):
                return res
            if isinstance(res, dict) and "items" in res:
                return res["items"]
            return []
        except MulticomApiError:
            return []

    def make_call(self, phone, caller_id=None, user=None):
        """Инициировать исходящий вызов через Мультиком."""
        payload = {
            "phone": str(phone or "").strip(),
            "caller_id": str(caller_id or "").strip(),
            "user": str(user or "admin").strip()
        }
        return self._request("POST", "/calls/make", body=payload)


class MulticomProvider(TelephonyProvider):
    """Провайдер телефонии Мультиком для ATS."""

    def __init__(self):
        self.client = None
        self.settings = {}

    @property
    def name(self) -> str:
        return "multicom"

    def configure(self, settings: dict):
        self.settings = dict(settings or {})
        mcfg = self.settings.get("multicom") or {}
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
            timeout_sec=mcfg.get("timeout_sec", 15)
        )
        return self

    def dial(self, phone: str, caller_id: str = "", timeout_sec: int = 15, **kwargs) -> dict:
        if not self.client:
            raise ProviderNotConfigured("Мультиком провайдер не инициализирован")
        res = self.client.make_call(phone=phone, caller_id=caller_id, user=kwargs.get("user"))
        call_id = str((res or {}).get("call_id") or (res or {}).get("id") or "")
        return {
            "ok": True,
            "call_id": call_id,
            "external_id": call_id,
            "provider": self.name,
            "raw": res
        }

    def check(self) -> dict:
        if not self.client:
            raise ProviderNotConfigured("Мультиком провайдер не инициализирован")
        info = self.client.get_account_info()
        return {"ok": True, "account": info}


def map_multicom_webhook(form_or_json):
    """Нормализовать входящий вебхук Мультиком в событие движка ATS."""
    f = dict(form_or_json or {})
    callid = str(f.get("call_id") or f.get("id") or f.get("external_call_id") or f.get("callid") or "").strip()
    if not callid:
        return None
    raw_status = str(f.get("status") or f.get("state") or f.get("event") or "").strip().lower()

    if raw_status in ("answered", "connected", "bridged", "answer", "talking"):
        event_type = "answered"
    elif raw_status in ("completed", "ended", "success", "done", "finished", "hangup"):
        event_type = "completed"
    elif raw_status in ("busy", "occupied"):
        event_type = "busy"
    elif raw_status in ("no_answer", "noanswer", "timeout", "cancel"):
        event_type = "no_answer"
    elif raw_status in ("failed", "rejected", "error"):
        event_type = "failed"
    elif raw_status in ("ring", "ringing", "dialing"):
        event_type = "ring"
    else:
        event_type = raw_status or "event"

    return {
        "provider": "multicom",
        "event": event_type,
        "raw_type": raw_status,
        "external_call_id": callid,
        "phone": str(f.get("phone") or f.get("client") or f.get("number") or "").strip(),
        "duration": int(f.get("duration") or 0) if str(f.get("duration") or "").isdigit() else 0,
        "record_url": str(f.get("record_url") or f.get("record") or "").strip(),
        "raw": f
    }
