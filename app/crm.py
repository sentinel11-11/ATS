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


def make_crm(settings) -> CrmDriver:
    name = (settings.get("crm") or {}).get("driver", "csv") if isinstance(settings.get("crm"), dict) else settings.get("crm", "csv")
    if name == "bitrix24":
        return Bitrix24Crm(settings)
    return CsvCrm(settings)
