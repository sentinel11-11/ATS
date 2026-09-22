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
import threading

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


# Маппинг результатов звонка ATS на официальные коды call_status amoCRM:
#   4 — разговор состоялся, 7 — номер занят, 6 — не дозвонился,
#   5 — неверный номер, 3 — нет на месте, 2 — перезвонить позже,
#   1 — оставил голосовое сообщение.
# Маппинг строится по call.result (финальный результат попытки), а НЕ по
# call.status: в ATS завершённый звонок всегда имеет status='done', а
# смысл несёт колонка result. Переопределяется настройкой
# amocrm.call_status_map = {"<result>": <код>}.
AMO_DEFAULT_RESULT_MAP = {
    # Успешный диалог (робот/оператор/входящий) — разговор состоялся
    "done_ok": 4, "operator_ok": 4, "done_agent": 4,
    "answered": 4, "success": 4, "completed": 4, "ok": 4,
    # Занято
    "busy": 7,
    # Не дозвонились (нет ответа, таймаут, пропущенный входящий,
    # автоответчик, техпричины, отмена до ответа)
    "no_answer": 6, "timeout": 6, "missed": 6, "machine": 6,
    "no_media": 6, "failed": 6, "canceled": 6, "cancelled": 6,
    "exhausted": 6,
    # Неверный/несуществующий номер
    "wrong_number": 5, "invalid_number": 5,
    # Абонент ответил, но разговора не вышло — перезвонить позже
    "no_operator": 2, "rejected": 2, "declined": 2,
}
# Результаты, которые в amoCRM не отправляем вовсе (инфраструктурные).
AMO_SKIP_RESULTS = {"blocked", "blocked_no_consent", "blacklisted", "dialing", ""}
# Результаты, по которым по умолчанию создаётся задача «перезвонить»
# (успешный разговор задачу не создаёт). Переопределяется настройкой
# amocrm.auto_task_results = [...].
AMO_DEFAULT_TASK_RESULTS = ("busy", "no_answer", "timeout", "missed",
                            "machine", "no_operator", "rejected", "declined")


_AMO_FALSE_STRINGS = frozenset(
    {"", "false", "0", "нет", "no", "off", "n", "f", "не", "ложь", "нету"})
_AMO_TRUE_STRINGS = frozenset(
    {"true", "1", "да", "yes", "on", "y", "t", "д", "правда"})


def _amo_flag(v, default=True):
    """Булев флаг amoCRM с толерантностью к legacy-строкам.

    POST /settings/raw раньше делал str() для ВСЕХ значений, поэтому в БД
    могли осесть строки "False"/"True" (а "False" truthy в Python — снятие
    галочки «автосоздание контакта/задачи» не работало). Лечим на чтении:
    legacy-строки "false"/"0"/"нет"/"no"/"off"/"" → False.
    """
    if v is None:
        return bool(default)
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    s = str(v).strip().lower()
    if s in _AMO_FALSE_STRINGS:
        return False
    if s in _AMO_TRUE_STRINGS:
        return True
    return bool(default)


class AmoCrm(CrmDriver):
    """Официальный драйвер amoCRM (REST API v4): фиксация звонков через POST /api/v4/calls
    (идемпотентно по uniq), автосоздание контактов, примечаний и задач.
    """
    name = "amocrm"

    def __init__(self, settings):
        super().__init__(settings)
        self._tokens_lock = threading.RLock()
        self._contact_lock = threading.RLock()  # локальная сериализация search→create

    # ---------- конфиг / клиент ----------

    def _mcfg(self):
        return self.settings.get("amocrm") or {}

    def _on_tokens_updated(self, tokens):
        """Атомарно сохранить новую OAuth-пару в настройки (refresh одноразовый,
        потерять его = потерять доступ к аккаунту)."""
        with self._tokens_lock:
            try:
                from . import db as _db
                s = _db.get_settings()
                amo = dict(s.get("amocrm") or {})
                amo["access_token"] = tokens.get("access_token") or amo.get("access_token") or ""
                amo["refresh_token"] = tokens.get("refresh_token") or amo.get("refresh_token") or ""
                if tokens.get("expires_in"):
                    amo["token_expires_in"] = tokens["expires_in"]
                amo["token_updated"] = config.now_iso()
                s["amocrm"] = amo
                _db.save_settings(s)
                self.settings = s
            except Exception as e:
                print("[amocrm] token persist failed:", e)

    def _client(self):
        from .providers.amocrm import AmoCrmClient, DEFAULT_AMOCRM_TOKEN_ENV
        mcfg = self._mcfg()
        subdomain = str(mcfg.get("subdomain") or "").strip()
        token = resolve_secret(mcfg, "access_token", "access_token_env", DEFAULT_AMOCRM_TOKEN_ENV)
        resp_id = mcfg.get("responsible_user_id") or 1
        timeout = mcfg.get("timeout_sec") or 15
        refresh = resolve_secret(mcfg, "refresh_token", "refresh_token_env",
                                 "ATS_AMOCRM_REFRESH_TOKEN")
        return AmoCrmClient(
            subdomain=subdomain, access_token=token, responsible_user_id=resp_id,
            timeout_sec=timeout,
            client_id=str(mcfg.get("client_id") or ""),
            client_secret=resolve_secret(mcfg, "client_secret", "client_secret_env",
                                         "ATS_AMOCRM_CLIENT_SECRET"),
            refresh_token=refresh,
            redirect_uri=str(mcfg.get("redirect_uri") or ""),
            on_tokens_updated=self._on_tokens_updated,
            rate_limit_rps=mcfg.get("rate_limit_rps") or 7)

    def _configured(self):
        mcfg = self._mcfg()
        if _amo_flag(mcfg.get("disabled"), default=False):
            # Интеграция выключена (напр. хуком отключения amoCRM) — fail-closed.
            return False
        subdomain = str(mcfg.get("subdomain") or "").strip()
        token = resolve_secret(mcfg, "access_token", "access_token_env", "ATS_AMOCRM_TOKEN")
        return bool(subdomain and token)

    def _request(self, method, endpoint, body=None):
        client = self._client()
        return client._request(method, endpoint, body=body)

    # ---------- маппинг результата ----------

    def _map_call_status(self, result):
        """Код call_status amoCRM по результату ATS (настройки могут переопределить)."""
        mcfg = self._mcfg()
        result = str(result or "").strip().lower()
        custom = mcfg.get("call_status_map")
        if isinstance(custom, dict) and result in custom:
            try:
                return int(custom[result])
            except (TypeError, ValueError):
                pass
        return AMO_DEFAULT_RESULT_MAP.get(result, 6)  # 6 — не дозвонился (по умолчанию)

    def _resolve_responsible(self, call, contact, mcfg):
        """Ответственный в amoCRM: карта операторов → контакт → дефолт интеграции.

        operator_user_map = {"<vats_login|ext|provider_user>": <amo_user_id>}
        связывает оператора ATS с пользователем amoCRM — звонок регистрируется
        на реального сотрудника, а не на «ответственного за контакт».
        """
        op_map = mcfg.get("operator_user_map")
        if isinstance(op_map, dict) and op_map:
            for key in (call.get("provider_user"), call.get("operator_ext"),
                        call.get("operator_login"), call.get("agent_login")):
                k = str(key or "").strip()
                if k and k in op_map:
                    try:
                        return int(op_map[k])
                    except (TypeError, ValueError):
                        pass
        if _amo_flag(mcfg.get("use_contact_responsible", True), default=True) and contact:
            try:
                if contact.get("responsible_user_id"):
                    return int(contact["responsible_user_id"])
            except (TypeError, ValueError):
                pass
        try:
            return int(mcfg.get("responsible_user_id") or 1)
        except (TypeError, ValueError):
            return 1

    # ---------- основной поток ----------

    def push_result(self, call: dict):
        if not self._configured():
            raise RuntimeError("amoCRM не настроен: укажите subdomain и access_token в настройках.")

        from .providers.amocrm import pick_exact_contact

        mcfg = self._mcfg()
        client = self._client()

        phone = str(call.get("contact_phone") or call.get("phone") or "").strip()
        cname = str(call.get("contact_name") or f"Клиент {phone}").strip()
        result = str(call.get("result") or "").strip().lower()
        if not result:
            # Обратная совместимость со старым вызывающим кодом, который
            # клал результат в status. В проде такого нет (status='done').
            result = str(call.get("status") or "").strip().lower()
        status = str(call.get("status") or "").strip().lower()
        direction = str(call.get("direction") or "out").lower()
        try:
            duration = int(call.get("duration_sec") or call.get("duration") or 0)
        except (TypeError, ValueError):
            duration = 0
        # Ссылка на запись: из провайдерской recording_url (ВАТС) или явных полей.
        record_link = str(call.get("record_url") or call.get("record_link")
                          or call.get("recording_url") or "").strip()
        # call_result в amoCRM — свободный текст: человекочитаемый detail
        # («Разговор завершён (ВАТС)»), машинный код результата — в скобках.
        detail_text = str(call.get("detail") or "").strip()
        result_text = f"{detail_text} [{result}]" if detail_text else result

        # Инфраструктурные результаты в CRM не толкаем.
        if result in AMO_SKIP_RESULTS:
            return None

        call_code = self._map_call_status(result)

        # Идемпотентность: стабильный uniq на один звонок ATS.
        uniq = None
        call_id = call.get("id")
        if call_id:
            uniq = f"ats-call-{call_id}"
        elif call.get("external_call_id"):
            uniq = f"ats-{call['external_call_id']}"

        # Дата звонка — started_at звонка (а не «сейчас»).
        ts = None
        started = str(call.get("started_at") or "").strip()
        if started:
            try:
                ts = int(datetime.datetime.strptime(started[:19], "%Y-%m-%d %H:%M:%S").timestamp())
            except (TypeError, ValueError):
                ts = None

        # Имя ответственного за звонок (call_responsible — строка для аналитики).
        call_responsible = str(call.get("provider_user") or
                               mcfg.get("call_responsible") or "").strip()

        # 1. Контакт: точный поиск по номеру → автосоздание (под локальным
        #    локом, чтобы два параллельных звонка одному номеру не создали дубль).
        #    Ошибки поиска (401/429/5xx) НЕ проглатываем: иначе «CRM недоступна»
        #    превращается в «контакта нет» → дубли в amoCRM.
        contact_id = None
        contact = None
        responsible_id = int(mcfg.get("responsible_user_id") or 1)

        if phone:
            with self._contact_lock:
                contacts = client.search_contacts(phone)  # поднимает исключения
                contact, conflict = pick_exact_contact(contacts, phone)
                if conflict:
                    print(f"[amocrm] дубль контакта по номеру {phone}: "
                          f"выбран id={contact.get('id')}, требуется зачистка в amoCRM")
                if contact:
                    contact_id = contact.get("id")
                elif _amo_flag(mcfg.get("auto_create_contacts", True), default=True):
                    try:
                        created = client.create_contact(name=cname, phone=phone,
                                                        responsible_user_id=responsible_id)
                        if isinstance(created, dict) and created.get("id"):
                            contact_id = created["id"]
                    except Exception as e:
                        # Создание не критично: register_call всё равно
                        # привяжется по номеру (amoCRM ищет по последним 10 цифрам).
                        print(f"[amocrm] create_contact {phone}: {e}")

        responsible_id = self._resolve_responsible(call, contact, mcfg)

        # 2. Регистрация звонка (официальный контракт + uniq + call_responsible).
        call_res = client.register_call(
            phone=phone,
            direction=direction,
            duration=duration,
            status_code=call_code,
            result_text=result_text,
            record_link=record_link,
            responsible_user_id=responsible_id,
            created_at=ts,
            uniq=uniq,
            call_responsible=call_responsible
        )

        # 3. Задача «перезвонить» — только для реально неуспешных результатов,
        #    никогда для состоявшегося разговора.
        task_results = mcfg.get("auto_task_results")
        if not (isinstance(task_results, (list, tuple)) and task_results):
            task_results = AMO_DEFAULT_TASK_RESULTS
        if (_amo_flag(mcfg.get("auto_create_tasks", True), default=True)
                and phone and result in task_results):
            task_text = (f"Перезвонить клиенту: {phone} ({cname}). "
                         f"Результат звонка АТС: {result_text or result}.")
            try:
                client.create_task(
                    text=task_text,
                    responsible_user_id=responsible_id,
                    entity_id=contact_id,
                    entity_type="contacts" if contact_id else None,
                    task_type_id=mcfg.get("task_type_id") or 1
                )
            except Exception as e:
                print(f"[amocrm] create_task {phone}: {e}")

        # 4. Примечание (common) — только для текстовой аналитики/итога,
        #    системный телефонный факт уже зарегистрирован через /calls.
        agent_result = str(call.get("agent_result") or "").strip()
        note_body = result_text if result_text else agent_result
        if contact_id and note_body:
            note_text = (f"Детали звонка #{call.get('id', '')}:\n"
                         f"Результат: {result_text or '-'}\n"
                         f"Длительность: {duration} сек\n"
                         + (f"Итог ИИ-диалога: {agent_result}" if agent_result and agent_result != result_text else ""))
            try:
                client.add_note(entity_type="contacts", entity_id=contact_id,
                                note_type="common", text=note_text)
            except Exception as e:
                print(f"[amocrm] add_note contact#{contact_id}: {e}")

        return call_res

    def create_task(self, title, desc="", responsible=""):
        client = self._client()
        mcfg = self._mcfg()
        try:
            resp_id = int(responsible or mcfg.get("responsible_user_id") or 1)
        except (TypeError, ValueError):
            resp_id = 1
        return client.create_task(text=f"{title}\n{desc}".strip(),
                                  responsible_user_id=resp_id,
                                  task_type_id=mcfg.get("task_type_id") or 1)


def make_crm(settings) -> CrmDriver:
    cfg = settings.get("crm") or {}
    name = str(cfg.get("driver") if isinstance(cfg, dict) else cfg or "csv").strip().lower() or "csv"
    if name == "csv":
        return CsvCrm(settings)
    if name == "bitrix24":
        return Bitrix24Crm(settings)
    if name == "amocrm":
        return AmoCrm(settings)
    # Fail-closed: опечатка в драйвере не должна молча превращаться в CSV —
    # иначе «интеграция настроена», а данные тихо пишутся в файл.
    raise ValueError(f"Неизвестный CRM-драйвер: {name!r} (допустимо: csv | bitrix24 | amocrm)")
