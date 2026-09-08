# -*- coding: utf-8 -*-
"""Безопасность: хэширование паролей (PBKDF2-SHA256), токены сессий, роли."""
import hashlib
import os
import secrets
import time

SESSION_TTL = 8 * 3600  # 8 часов
SESSIONS = {}            # token -> {"login":..., "role":..., "exp":...}
SESSIONS_LOCK_ = None


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
    with _lock():
        SESSIONS[t] = {"login": login, "role": role, "exp": time.time() + SESSION_TTL}
    return t


def drop_token(token: str):
    with _lock():
        SESSIONS.pop(token, None)


def drop_sessions_for(login: str):
    """Завершить все активные сессии пользователя (смена/сброс пароля, деактивация)."""
    with _lock():
        for tok in [t for t, s in SESSIONS.items() if s.get("login") == login]:
            SESSIONS.pop(tok, None)


def refresh_session_role(token: str, role: str):
    """Актуализировать роль в сессии (роль прочитана из БД)."""
    with _lock():
        s = SESSIONS.get(token)
        if s:
            s["role"] = role


def get_session(token: str):
    with _lock():
        s = SESSIONS.get(token)
        if not s:
            return None
        if s["exp"] < time.time():
            SESSIONS.pop(token, None)
            return None
        s["exp"] = time.time() + SESSION_TTL  # продлеваем
        return s


def gen_admin_password(length=12) -> str:
    alpha = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alpha) for _ in range(length))


def secure_rand_hex(n=16) -> str:
    return secrets.token_hex(n)
