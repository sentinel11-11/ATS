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
    "provider": "sim",            # sim | uis | ami  (реализации в app/telephony.py)
    "max_channels": 3,
    "consent_required": True,
    "retry_max": 2,
    "retry_delay_min": 15,
    "line_cooldown_sec": 5,
    "watchdog_timeout_min": 20,
    "acd_wait_timeout_sec": 60,
    "window_start": "08:00",
    "window_end": "20:00",
    "window_days": [0, 1, 2, 3, 4, 5, 6],
    "sim_outcome": {"answered_human": 45, "machine": 10, "busy": 15, "no_answer": 20, "failed": 8, "blocked": 2},
    "sim_answer": "1",
    "auto_quarantine_on_complaints": 3,
    "uis": {"api_url": "", "api_key": "", "number_pool_api": ""},
    "ami": {"host": "127.0.0.1", "port": 5038, "user": "", "secret": ""},
    "llm": {"enabled": False, "base_url": "", "api_key_env": "ATS_LLM_KEY", "model": ""},
    "crm": {"driver": "csv"},     # csv | bitrix24
    "bitrix24": {"webhook_url": ""},
}

for p in (DATA_DIR, LOG_DIR, REC_DIR, CRM_OUT_DIR):
    p.mkdir(parents=True, exist_ok=True)


def now_iso():
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
