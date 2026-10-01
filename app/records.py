# -*- coding: utf-8 -*-
"""Записи разговоров: подписанные ссылки для CRM и почты.

Файл записи лежит в ``data_v2/recordings/`` и без авторизации не отдаётся.
Чтобы ссылку можно было положить в сделку amoCRM (или письмо), не передавая
туда токен сессии, выдаётся короткоживущая подпись: HMAC от пары
``<call_id>.<exp>``. Проверка — :func:`verify`; срок и секрет — в настройках
``records`` (``link_ttl_hours``, ``link_secret``/``link_secret_env``,
``base_url``).
"""
import datetime
import hashlib
import hmac
import os
import secrets
import threading

from . import config, db

ALGO = "v1"
_write_lock = threading.Lock()


def _cfg(settings=None):
    settings = settings if settings is not None else (db.get_settings() or {})
    return settings.get("records") or {}


def link_secret(settings=None):
    """Секрет подписи: из настроек, иначе из переменной окружения."""
    cfg = _cfg(settings)
    secret = str(cfg.get("link_secret") or "").strip()
    if not secret:
        env = str(cfg.get("link_secret_env") or "ATS_RECORDS_LINK_TOKEN").strip()
        secret = os.environ.get(env, "").strip()
    if not secret:
        # Секрета нет нигде: генерируем один раз и сохраняем в настройки, чтобы
        # выданные ссылки жили и после перезапуска сервиса.
        secret = _generate_secret()
    return secret


def _generate_secret():
    generated = secrets.token_hex(32)
    with _write_lock:
        try:
            cur = db.get_settings() or {}
            rec = dict(cur.get("records") or {})
            if str(rec.get("link_secret") or "").strip():
                return str(rec["link_secret"]).strip()
            rec["link_secret"] = generated
            cur["records"] = rec
            db.save_settings(cur)
        except Exception:
            pass
    return generated


def _sign(call_id, exp, secret):
    msg = "{}.{}.{}".format(ALGO, int(call_id), int(exp)).encode("utf-8")
    return hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()


def ttl_hours(settings=None):
    cfg = _cfg(settings)
    try:
        return float(cfg.get("link_ttl_hours", 72))
    except (TypeError, ValueError):
        return 72.0


def link(call_id, settings=None):
    """Подписанная ссылка на запись звонка. '' — если звонка/файла нет или срок = 0."""
    if not call_id:
        return ""
    settings = settings if settings is not None else (db.get_settings() or {})
    hours = ttl_hours(settings)
    if hours <= 0:
        return ""
    call = db.fetch1("SELECT id, recording FROM calls WHERE id=?", (int(call_id),))
    if not call or not str(call.get("recording") or ""):
        return ""
    exp = int((datetime.datetime.now() + datetime.timedelta(hours=hours)).timestamp())
    sig = _sign(call["id"], exp, link_secret(settings))
    path = "/api/v2/records/{}?exp={}&sig={}".format(call["id"], exp, sig)
    base = str((_cfg(settings).get("base_url") or "")).strip().rstrip("/")
    return (base + path) if base else path


def verify(call_id, exp, sig, settings=None):
    """Проверить подпись и срок действия ссылки."""
    try:
        call_id = int(call_id)
        exp = int(exp)
    except (TypeError, ValueError):
        return False
    if not sig:
        return False
    settings = settings if settings is not None else (db.get_settings() or {})
    if not hmac.compare_digest(_sign(call_id, exp, link_secret(settings)), str(sig)[:128]):
        return False
    return exp >= int(datetime.datetime.now().timestamp())


def path_for(call_id):
    """Путь к файлу записи звонка (или None). Имя берётся из БД — наружу не выходит."""
    call = db.fetch1("SELECT recording FROM calls WHERE id=?", (int(call_id),))
    if not call or not str(call.get("recording") or ""):
        return None
    name = os.path.basename(str(call["recording"]))
    try:
        full = (config.REC_DIR / name).resolve()
        full.relative_to(config.REC_DIR.resolve())
    except Exception:
        return None
    if not full.exists() or not full.is_file():
        return None
    return full
