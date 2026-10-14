# -*- coding: utf-8 -*-
"""Безопасность: хэширование паролей (PBKDF2-SHA256), токены сессий, роли."""
import hashlib
import os
import secrets
import time

SESSION_TTL = 8 * 3600  # 8 часов (скользящий: активный UI не выкидывает)
SESSIONS = {}            # token -> {"login":..., "role":..., "exp":...} — кэш и фолбэк
SESSIONS_LOCK_ = None
SESSION_SLIDE_MIN = 300  # продлевать запись в БД не чаще раза в 5 минут на сессию


def _db():
    """БД подключается лениво: security импортируется раньше db в ряде тестов."""
    from . import db
    return db


def token_hash(token: str) -> str:
    """Ключ строки в sessions. Сам токен в БД не пишем нигде."""
    return hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()


def _lock():
    import threading
    global SESSIONS_LOCK_
    if SESSIONS_LOCK_ is None:
        SESSIONS_LOCK_ = threading.RLock()
    return SESSIONS_LOCK_


def hash_password(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", str(password).encode("utf-8"),
                               salt.encode("utf-8"), 120_000).hex()


def new_salt() -> str:
    return secrets.token_hex(16)


def create_token(login: str, role: str) -> str:
    t = secrets.token_hex(24)
    exp = time.time() + SESSION_TTL
    with _lock():
        SESSIONS[t] = {"login": login, "role": role, "exp": exp}
    try:
        d = _db()
        d.q("INSERT OR REPLACE INTO sessions(token_hash, login, role, exp, slid) "
            "VALUES(?,?,?,?,0)", (token_hash(t), login, role, exp))
        # заодно чистим протухшее — отдельного демона для этого нет
        d.q("DELETE FROM sessions WHERE exp < ?", (exp - SESSION_TTL,))
    except Exception:  # noqa: BLE001 — без БД живём на памяти, доступ не должен ломаться
        pass
    return t


def drop_token(token: str):
    with _lock():
        SESSIONS.pop(token, None)
    try:
        _db().q("DELETE FROM sessions WHERE token_hash=?", (token_hash(token),))
    except Exception:  # noqa: BLE001
        pass


def drop_sessions_for(login: str):
    """Завершить все активные сессии пользователя (смена/сброс пароля, деактивация)."""
    want = str(login or "").strip().lower()
    with _lock():
        for tok in [tk for tk, s in SESSIONS.items()
                    if str(s.get("login") or "").strip().lower() == want]:
            SESSIONS.pop(tok, None)
    try:
        _db().q("DELETE FROM sessions WHERE lower(login)=?", (want,))
    except Exception:  # noqa: BLE001
        pass


def refresh_session_role(token: str, role: str):
    """Актуализировать роль в сессии (роль прочитана из БД)."""
    with _lock():
        s = SESSIONS.get(token)
        if s:
            s["role"] = role
    try:
        _db().q("UPDATE sessions SET role=? WHERE token_hash=?", (role, token_hash(token)))
    except Exception:  # noqa: BLE001
        pass


def get_session(token: str):
    if not token:
        return None
    now = time.time()
    row = None
    try:
        row = _db().fetch1("SELECT login, role, exp, slid FROM sessions WHERE token_hash=?",
                           (token_hash(token),))
    except Exception:  # noqa: BLE001 — нет БД/таблицы → работаем по кэшу
        row = None
    slide = False
    with _lock():
        if row is None:
            s = SESSIONS.get(token)
            if not s:
                return None
            if s["exp"] < now:
                SESSIONS.pop(token, None)
                return None
            s["exp"] = now + SESSION_TTL
            return dict(s)
        if float(row["exp"]) < now:
            SESSIONS.pop(token, None)
            slide = False
            expired = True
        else:
            s = {"login": row["login"], "role": row["role"], "exp": float(row["exp"])}
            SESSIONS[token] = dict(s)
            expired = False
            # продлеваем, только когда срок реально поджимает: свежую сессию трогать незачем,
            # а UI дёргает API раз в пару секунд
            slide = (float(row["exp"]) - now) < (SESSION_TTL - SESSION_SLIDE_MIN)
            if slide:
                s["exp"] = now + SESSION_TTL
    if expired:
        try:
            _db().q("DELETE FROM sessions WHERE token_hash=?", (token_hash(token),))
        except Exception:  # noqa: BLE001
            pass
        return None
    if slide:
        try:
            _db().q("UPDATE sessions SET exp=?, slid=? WHERE token_hash=?",
                    (now + SESSION_TTL, now, token_hash(token)))
        except Exception:  # noqa: BLE001
            pass
    return dict(s)


def gen_admin_password(length=12) -> str:
    alpha = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alpha) for _ in range(length))


def secure_rand_hex(n=16) -> str:
    return secrets.token_hex(n)
