# -*- coding: utf-8 -*-
"""Интеграция с CRM (R8): интерфейс CrmDriver + реализации.

- CsvCrm        — рабочий драйвер по умолчанию: экспорт результатов в CSV (data_v2/crm_out);
                  списки для кампаний загружаются через API (CSV/массив).
- Bitrix24Crm   — каркас адаптера Битрикс24 (вебхук REST). Заполняется после уточнения CRM клиента (T03/T33).
"""
import csv
import datetime
import json
import os

from . import config, db
from .providers.base import resolve_secret


class CrmDriver:
    name = "base"

    def __init__(self, settings):
        self.settings = settings

    def push_result(self, call: dict):
        """Записать результат звонка в CRM. call — словарь записи calls."""
        raise NotImplementedError

    def create_task(self, title, desc="", responsible=""):
        raise NotImplementedError


class CsvCrm(CrmDriver):
    name = "csv"

    def _file(self):
        d = config.CRM_OUT_DIR
        d.mkdir(parents=True, exist_ok=True)
        return d / "crm_push_{}.csv".format(datetime.date.today().isoformat())

    def _ensure_header(self, path, header):
        if not path.exists() or path.stat().st_size == 0:
            with open(path, "w", encoding="utf-8-sig", newline="") as f:
                csv.writer(f, delimiter=";").writerow(header)

    def push_result(self, call: dict):
        path = self._file()
        header = ["id", "datetime", "phone", "name", "campaign", "caller_id", "status", "result", "detail", "agent"]
        self._ensure_header(path, header)
        with open(path, "a", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow([call.get("id"), call.get("started_at"), call.get("contact_phone"),
                        call.get("contact_name"), call.get("campaign_id"), call.get("caller_id"),
                        call.get("status"), call.get("result"), call.get("detail"), call.get("agent_result")])
        return path

    def create_task(self, title, desc="", responsible=""):
        path = config.CRM_OUT_DIR / "crm_tasks.csv"
        self._ensure_header(path, ["created", "title", "desc", "responsible"])
        with open(path, "a", encoding="utf-8-sig", newline="") as f:
            csv.writer(f, delimiter=";").writerow([datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                                   title, desc, responsible])
        return path


class Bitrix24Crm(CrmDriver):
    """Каркас: Битрикс24 REST через входящий вебхук. Заполнить webhook_url и методы по CRM клиента."""
    name = "bitrix24"

    def _configured(self):
        return bool((self.settings.get("bitrix24") or {}).get("webhook_url"))

    def push_result(self, call: dict):
        if not self._configured():
            raise RuntimeError("Bitrix24 не настроен (webhook_url). Используйте csv-драйвер.")
        # TODO(T33): crm.lead.update / crm.timeline.comment.add по call
        import urllib.request
        url = self.settings["bitrix24"]["webhook_url"] + "/crm.timeline.comment.add.json"
        fields = {"fields": {"ENTITY_ID": call.get("contact_id", 0), "ENTITY_TYPE": "contact",
                             "COMMENT": "Звонок #{}: {} — {}".format(call.get("id"), call.get("status"),
                                                                      call.get("detail", ""))}}
        req = urllib.request.Request(url, data=json.dumps(fields).encode("utf-8"),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode("utf-8"))

    def create_task(self, title, desc="", responsible=""):
        if not self._configured():
            raise RuntimeError("Bitrix24 не настроен (webhook_url).")
        import urllib.request
        url = self.settings["bitrix24"]["webhook_url"] + "/tasks.task.add.json"
        fields = {"fields": {"TITLE": title, "DESCRIPTION": desc,
                             "RESPONSIBLE_ID": responsible or 1}}
        req = urllib.request.Request(url, data=json.dumps(fields).encode("utf-8"),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode("utf-8"))


class AmoCrm(CrmDriver):
    """Официальный драйвер amoCRM (REST API v4): фиксация звонков через POST /api/v4/calls,
    автосоздание контактов, примечаний и задач.
    """
    name = "amocrm"

    def _client(self):
        from .providers.amocrm import AmoCrmClient, DEFAULT_AMOCRM_TOKEN_ENV
        mcfg = self.settings.get("amocrm") or {}
        subdomain = str(mcfg.get("subdomain") or "").strip()
        token = resolve_secret(mcfg, "access_token", "access_token_env", DEFAULT_AMOCRM_TOKEN_ENV)
        resp_id = mcfg.get("responsible_user_id") or 1
        timeout = mcfg.get("timeout_sec") or 15
        return AmoCrmClient(subdomain=subdomain, access_token=token, responsible_user_id=resp_id, timeout_sec=timeout)

    def _configured(self):
        mcfg = self.settings.get("amocrm") or {}
        subdomain = str(mcfg.get("subdomain") or "").strip()
        token = resolve_secret(mcfg, "access_token", "access_token_env", "ATS_AMOCRM_TOKEN")
        return bool(subdomain and token)

    def _request(self, method, endpoint, body=None):
        client = self._client()
        return client._request(method, endpoint, body=body)

    def push_result(self, call: dict):
        if not self._configured():
            raise RuntimeError("amoCRM не настроен: укажите subdomain и access_token в настройках.")

        mcfg = self.settings.get("amocrm") or {}
        client = self._client()

        phone = str(call.get("contact_phone") or call.get("phone") or "").strip()
        cname = str(call.get("contact_name") or f"Клиент {phone}").strip()
        status = str(call.get("status") or "").lower()
        direction = str(call.get("direction") or "out").lower()
        duration = int(call.get("duration_sec") or call.get("duration") or 0)
        record_link = str(call.get("record_url") or call.get("record_link") or "").strip()
        result_text = str(call.get("result") or call.get("detail") or "").strip()

        # Маппинг статусов вызова АТС на официальные коды amoCRM
        if status in ("operator_ok", "answered", "success", "completed", "ok"):
            call_code = 1  # Разговор состоялся
        elif status in ("no_answer", "timeout", "missed"):
            call_code = 2  # Пропущенный
        elif status in ("busy",):
            call_code = 3  # Занято
        elif status in ("failed", "rejected", "canceled"):
            call_code = 4  # Ошибка
        else:
            call_code = 6  # Неизвестно / недоступен

        # 1. Поиск или автосоздание контакта в amoCRM
        contact_id = None
        responsible_id = int(mcfg.get("responsible_user_id") or 1)

        contacts = client.search_contacts(phone) if phone else []
        if contacts:
            contact = contacts[0]
            contact_id = contact.get("id")
            if contact.get("responsible_user_id"):
                responsible_id = int(contact["responsible_user_id"])
        elif phone and mcfg.get("auto_create_contacts", True):
            try:
                created = client.create_contact(name=cname, phone=phone, responsible_user_id=responsible_id)
                if isinstance(created, dict) and created.get("id"):
                    contact_id = created["id"]
            except Exception:
                pass

        # 2. Регистрация звонка в amoCRM по официальному стандарту POST /api/v4/calls
        call_res = client.register_call(
            phone=phone,
            direction=direction,
            duration=duration,
            status_code=call_code,
            result_text=result_text,
            record_link=record_link,
            responsible_user_id=responsible_id
        )

        # 3. Автосоздание задачи при пропущенном/неуспешном вызове
        if call_code != 1 and mcfg.get("auto_create_tasks", True) and phone:
            task_text = f"Пропущенный звонок: {phone} ({cname}). Перезвонить клиенту!"
            try:
                client.create_task(
                    text=task_text,
                    responsible_user_id=responsible_id,
                    entity_id=contact_id,
                    entity_type="contacts" if contact_id else None
                )
            except Exception:
                pass

        # 4. Добавление примечания в контакт при наличии описания/стенограммы
        if contact_id and result_text:
            note_text = f"Детали звонка #{call.get('id', '')}:\nСтатус: {status}\nДлительность: {duration} сек\nРезультат: {result_text}"
            try:
                client.add_note(entity_type="contacts", entity_id=contact_id, note_type="common", text=note_text)
            except Exception:
                pass

        return call_res

    def create_task(self, title, desc="", responsible=""):
        client = self._client()
        mcfg = self.settings.get("amocrm") or {}
        try:
            resp_id = int(responsible or mcfg.get("responsible_user_id") or 1)
        except (TypeError, ValueError):
            resp_id = 1
        return client.create_task(text=f"{title}\n{desc}".strip(), responsible_user_id=resp_id)


def make_crm(settings) -> CrmDriver:
    cfg = settings.get("crm") or {}
    name = str(cfg.get("driver") if isinstance(cfg, dict) else cfg or "csv").strip().lower()
    if name == "bitrix24":
        return Bitrix24Crm(settings)
    if name == "amocrm":
        return AmoCrm(settings)
    return CsvCrm(settings)
