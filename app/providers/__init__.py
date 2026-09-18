# -*- coding: utf-8 -*-
"""Подключаемые телеком-провайдеры ATS (transport layer).

Engine работает только с базовым интерфейсом TelephonyProvider
(см. app/telephony.py) и нормализованными событиями
движка; provider-specific детали (X-API-KEY, crm_token, callid, diversion
и т.п.) за пределы этого пакета не выходят.

Состав:
- base.py         — общие ошибки и резолв секретов (literal или env:*),
                    переиспользуются будущими MTS/Beeline-адаптерами;
- megafon_vats.py — клиент REST API МегаФон ВАТС (CRM API, /crmapi/v1)
                    + MegafonVatsProvider.
"""
from .base import ProviderApiError, resolve_secret
from .megafon_vats import MegafonApiError, MegafonVatsClient, MegafonVatsProvider

__all__ = ["ProviderApiError", "resolve_secret", "MegafonApiError",
           "MegafonVatsClient", "MegafonVatsProvider"]
