# -*- coding: utf-8 -*-
"""Безопасность: хэширование паролей (PBKDF2-SHA256), токены сессий, роли.

Режимы хранения сессий:
- production (по умолчанию): persistent store обязателен. Ошибка БД сессий —
  отказ в доступе (SessionStorageUnavailable → 503/401), а НЕ тихий переход
  на память. Иначе отзыв токена/деактивация пользователя перестают работать,
  а несколько процессов расходятся в представлениях о сессиях.
- memory-fallback (только стенд/тесты): ATS_FAST=1 (тесты) или
  ATS_MEMORY_SESSIONS=1 — при недоступной БД сессии живут в памяти процесса.
  Явный флаг вместо неявного «повезло» — см. docs/SECURITY_CLEANUP.md §2а.
"""
import hashlib
import os
import secrets
import time

SESSION_TTL = 8 * 3600  # 8 часов (скользящий: активный UI не выкидывает)
SESSIONS = {}            # token -> {"login":..., "role":..., "exp":...} — кэш и фолбэк
SESSIONS_LOCK_ = None
SESSION_SLIDE_MIN = 300  # продлевать запись в БД не чаще раза в 5 минут на сессию


class SessionStorageUnavailable(Exception):
    """Persistent-хранилище сессий недоступно, а memory-fallback запрещён
    (production). Аутентификация обязана отказать, а не «пропустить по кэшу»."""


def _memory_fallback_allowed():
    """In-memory сессии при сбое БД — только явный режим стенда/тестов."""
    return bool(SESSIONS_MEMORY_FALLBACK)


# Вычисляется один раз при импорте: ATS_FAST ставят тесты до импорта app.*;
# ATS_MEMORY_SESSIONS=1 — явное разрешение для локальной отладки без БД.
SESSIONS_MEMORY_FALLBACK = (
    os.environ.get("ATS_MEMORY_SESSIONS", "") == "1"
    or os.environ.get("ATS_FAST", "") == "1"
)


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
    try:
        d = _db()
        d.q("INSERT OR REPLACE INTO sessions(token_hash, login, role, exp, slid) "
            "VALUES(?,?,?,?,0)", (token_hash(t), login, role, exp))
        # заодно чистим протухшее — отдельного демона для этого нет
        d.q("DELETE FROM sessions WHERE exp < ?", (exp - SESSION_TTL,))
    except Exception:  # noqa: BLE001
        if not _memory_fallback_allowed():
            # production: выдать токен, которого нет в persistent store, нельзя —
            # иначе logout/деактивация в другом процессе его не отзовут.
            with _lock():
                SESSIONS.pop(t, None)
            raise SessionStorageUnavailable(
                "sessions table unavailable; memory fallback disabled")
        # стенд/тесты: живём на памяти
        with _lock():
            SESSIONS[t] = {"login": login, "role": role, "exp": exp}
        return t
    with _lock():
        SESSIONS[t] = {"login": login, "role": role, "exp": exp}
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
    db_error = False
    try:
        row = _db().fetch1("SELECT login, role, exp, slid FROM sessions WHERE token_hash=?",
                           (token_hash(token),))
    except Exception:  # noqa: BLE001 — нет БД/таблицы → режим зависит от конфигурации
        row = None
        db_error = True
    if db_error and not _memory_fallback_allowed():
        # production: fail-closed. Кэш процесса не может быть источником истины,
        # когда persistent store недоступен (иначе отзыв токена не работает).
        raise SessionStorageUnavailable("sessions store unavailable; "
                                        "memory fallback disabled")
    slide = False
    with _lock():
        if row is None:
            s = SESSIONS.get(token)
            if not s:
                return None
            if db_error and _memory_fallback_allowed():
                # аварийный/тестовый режим: БД лежит, принимаем кэш процесса
                if s["exp"] < now:
                    SESSIONS.pop(token, None)
                    return None
                s["exp"] = now + SESSION_TTL
                return dict(s)
            # БД доступна и ответила «строки нет» — сессия отозвана/истекла
            # (в т.ч. другим процессом). Кэш не может её «оживить».
            SESSIONS.pop(token, None)
            return None
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


# ---------------- одноразовые билеты для URL-доступа ----------------
# Полный токен сессии (TTL 8 ч) в query string не передаём: URL попадает в
# access-логи reverse-proxy, историю браузера, скриншоты. Для SSE/экспортов/
# записей UI сначала запрашивает билет (POST /auth/ticket, заголовок
# X-Ats-Token), затем открывает URL с ?ticket=...: TTL короткий, ровно одно
# использование, в БД только SHA-256. Билет — НЕ сессия: остальных API-прав
# у него нет (need_auth принимает его только как query_ticket).

TICKET_TTL = 60            # секунд: хватает на открытие ссылки/EventSource
MAX_TICKETS_PER_USER = 30  # незакрытых билетов на пользователя (анти-спам)


def create_ticket(login: str, role: str, ttl: float = TICKET_TTL) -> str:
    """Выдать одноразовый билет (требует активной сессии — см. api /auth/ticket)."""
    t = secrets.token_hex(20)
    now = time.time()
    d = _db()
    # чистим протухшие и ограничиваем число живых билетов на пользователя
    d.q("DELETE FROM auth_tickets WHERE exp < ?", (now,))
    rows = d.fetch("SELECT ticket_hash FROM auth_tickets WHERE login=? ORDER BY rowid",
                   (login,))
    if len(rows) >= MAX_TICKETS_PER_USER:
        stale = [r["ticket_hash"] for r in rows[:len(rows) - MAX_TICKETS_PER_USER + 1]]
        if stale:
            ph = ",".join("?" * len(stale))
            d.q("DELETE FROM auth_tickets WHERE ticket_hash IN ({})".format(ph), stale)
    d.q("INSERT INTO auth_tickets(ticket_hash, login, role, exp, used, created) "
        "VALUES(?,?,?, ?,0,?)",
        (token_hash(t), login, role, now + ttl, time.strftime("%Y-%m-%d %H:%M:%S")))
    return t


def consume_ticket(ticket: str):
    """Погасить билет: возвращает {login, role} или None.

    Одноразовость — атомарным UPDATE: два конкурентных запроса с одним
    билетом не получат оба доступ (used=1 меняет затронутые строки на 0).
    """
    if not ticket:
        return None
    h = token_hash(ticket)
    d = _db()
    cur = d.q("UPDATE auth_tickets SET used=1 WHERE ticket_hash=? AND used=0 AND exp>?",
              (h, time.time()))
    if not cur.rowcount:
        return None
    row = d.fetch1("SELECT login, role FROM auth_tickets WHERE ticket_hash=?", (h,))
    if not row:
        return None
    return {"login": row["login"], "role": row["role"]}


def gen_admin_password(length=12) -> str:
    alpha = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alpha) for _ in range(length))


def secure_rand_hex(n=16) -> str:
    return secrets.token_hex(n)
