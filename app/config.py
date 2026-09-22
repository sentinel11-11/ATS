# -*- coding: utf-8 -*-
"""Конфигурация ATS v2: пути, умолчания, хранилище настроек (таблица settings)."""
import json
import os
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent          # корень репозитория
APP = Path(__file__).resolve().parent                  # app/
DATA_DIR = Path(os.environ.get("ATS_DATA_DIR", BASE / "data_v2"))
DB_PATH = Path(os.environ.get("ATS_DB", DATA_DIR / "ats.db"))
LOG_DIR = DATA_DIR / "logs"
REC_DIR = DATA_DIR / "recordings"
CRM_OUT_DIR = DATA_DIR / "crm_out"
LEGACY_DATA = BASE / "data"                            # данные старого прототипа (импорт)

HOST_DEFAULT = "0.0.0.0"
PORT_DEFAULT = 9124
FAST = os.environ.get("ATS_FAST", "") == "1"           # ускоренные тайминги (тесты/демо)

DEFAULT_SETTINGS = {
    "host": HOST_DEFAULT,
    "port": PORT_DEFAULT,
    # Fail-closed: пусто = провайдер НЕ ВЫБРАН, движок не стартует, пока админ
    # явно не укажет sim|uis|ami|megafon_vats (--provider при старте или Настройки).
    # sim — только стенд/тесты, молча не подставляется. Реализации: app/telephony.py.
    "provider": "",
    "max_channels": 3,
    "consent_required": True,
    "retry_max": 2,
    "retry_delay_min": 15,
    "line_cooldown_sec": 5,
    "watchdog_timeout_min": 20,
    "acd_wait_timeout_sec": 60,
    # Страховка VATS-разговоров: нет финала от ВАТС дольше N мин —
    # timeout+ретрай (0 — выкл). Короткий acd-таймаут их не касается.
    "vats_conversation_timeout_min": 30,
    "window_start": "08:00",
    "window_end": "20:00",
    "window_days": [0, 1, 2, 3, 4, 5, 6],
    "sim_outcome": {"answered_human": 45, "machine": 10, "busy": 15, "no_answer": 20, "failed": 8, "blocked": 2},
    "sim_answer": "1",
    "auto_quarantine_on_complaints": 3,
    "log_non_campaign_calls": True,
    "uis": {"api_url": "", "api_key": "", "number_pool_api": ""},
    "ami": {"host": "127.0.0.1", "port": 5038, "user": "", "secret": ""},
    # МегаФон ВАТС (REST CRM API): base_url=https://{domain}; секреты — literal
    # или env (маскируются в UI, плейсхолдер ******** не затирает — см. api.py).
    "megafon_vats": {"base_url": "", "api_key": "", "api_key_env": "ATS_MEGAFON_API_KEY",
                     "crm_token": "", "crm_token_env": "ATS_MEGAFON_CRM_TOKEN",
                     "default_user": "", "default_group": "", "timeout_sec": 15},
    "multicom": {"api_url": "https://api.multicom.ru/v1", "api_key": "", "api_key_env": "ATS_MULTICOM_API_KEY",
                 "account_id": "", "sip_host": "sip.multicom.ru", "sip_user": "", "sip_secret": "", "timeout_sec": 15},
    "llm": {"enabled": False, "base_url": "", "api_key_env": "ATS_LLM_KEY", "model": ""},
    "crm": {"driver": "csv"},     # csv | bitrix24 | amocrm
    "bitrix24": {"webhook_url": ""},
    "amocrm": {
        "subdomain": "",
        "access_token": "",
        "access_token_env": "ATS_AMOCRM_TOKEN",
        # OAuth-интеграция (опционально): при заданных client_id/secret/refresh_token
        # клиент сам обновляет пару токенов по 401 и атомарно сохраняет новую в настройки.
        # Для долгосрочного токена (вкладка «Ключи» приватной интеграции) эти поля не нужны.
        "client_id": "",
        "client_secret": "",
        "client_secret_env": "ATS_AMOCRM_CLIENT_SECRET",
        "refresh_token": "",
        "refresh_token_env": "ATS_AMOCRM_REFRESH_TOKEN",
        "redirect_uri": "",
        # Вебхук (клик-дозвон/события из amoCRM): fail-closed без токена.
        "webhook_token": "",
        "webhook_token_env": "ATS_AMOCRM_WEBHOOK_TOKEN",
        "responsible_user_id": 1,
        # Карта операторов ATS -> пользователи amoCRM:
        # {"vats_login|ext": amo_user_id} — звонок вешается на реального сотрудника.
        "operator_user_map": {},
        # Брать ответственного из найденного контакта (когда карта операторов не сработала).
        "use_contact_responsible": True,
        "task_type_id": 1,
        "auto_create_contacts": True,
        "auto_create_tasks": True,
        "auto_task_results": ["busy", "no_answer", "timeout", "missed",
                              "machine", "no_operator", "rejected", "declined"],
        # Переопределение маппинга результатов АТС -> код call_status amoCRM:
        # call_status_map = {"result_ats": 4|7|6|5|3|2|1}
        "call_status_map": {},
        "rate_limit_rps": 7,
        "unsorted_on_new": False,
        "timeout_sec": 15
    },
}

for p in (DATA_DIR, LOG_DIR, REC_DIR, CRM_OUT_DIR):
    p.mkdir(parents=True, exist_ok=True)


def now_iso():
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
