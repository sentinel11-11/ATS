# -*- coding: utf-8 -*-
"""Общая база transport layer: ошибки API провайдеров и резолв секретов.

Никакого provider-specific кода здесь нет — только то, что одинаково
понадобится Megafon/MTS/Beeline-адаптерам.
"""
import os


class ProviderApiError(RuntimeError):
    """Ошибка HTTP API телеком-провайдера.

    status — HTTP-статус (None при сетевой ошибке/таймауте),
    payload — тело ответа (обрезанное) для диагностики.
    Секреты (ключи/токены) в сообщение и payload НЕ попадают никогда.
    """

    def __init__(self, message, status=None, payload=""):
        super().__init__(message)
        self.status = status
        self.payload = payload


def resolve_secret(cfg, literal_key, env_key, default_env=""):
    """Достать секрет из конфига: literal-значение или имя env-переменной.

    cfg — dict настроек провайдера (напр. settings["megafon_vats"]).
    Сначала пробуется cfg[literal_key] (хранится в SQLite, в UI маскируется),
    затем env-переменная cfg[env_key] (или default_env). Пустая строка —
    «секрет не задан» (провайдер обязан ответить fail-closed).
    """
    cfg = cfg or {}
    v = str(cfg.get(literal_key) or "").strip()
    if v:
        return v
    env_name = str(cfg.get(env_key) or default_env or "").strip()
    if env_name:
        return str(os.environ.get(env_name, "") or "").strip()
    return ""
