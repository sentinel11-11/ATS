# -*- coding: utf-8 -*-
"""Хранилище ATS v2: SQLite (одна БД, WAL), схема, миграции, импорт из legacy-JSON, настройки."""
import json
import os
import sqlite3
import threading

from . import config

_lock = threading.RLock()
_conn = None


def connect() -> sqlite3.Connection:
    global _conn
    with _lock:
        if _conn is None:
            _conn = sqlite3.connect(str(config.DB_PATH), check_same_thread=False)
            _conn.row_factory = sqlite3.Row
            _conn.execute("PRAGMA journal_mode=WAL")
            _conn.execute("PRAGMA busy_timeout=5000")
        return _conn


def q(sql, params=()):
    """Выполнить запись/произвольный запрос (commit)."""
    with _lock:
        c = connect()
        cur = c.execute(sql, params)
        c.commit()
        return cur


def fetch(sql, params=()):
    with _lock:
        cur = connect().execute(sql, params)
        rows = cur.fetchall()
        return [dict(r) for r in rows]


def fetch1(sql, params=()):
    with _lock:
        cur = connect().execute(sql, params)
        r = cur.fetchone()
        return dict(r) if r else None


def insert(table, data: dict) -> int:
    cols = list(data.keys())
    sql = "INSERT INTO {} ({}) VALUES ({})".format(table, ", ".join(cols), ", ".join("?" * len(cols)))
    cur = q(sql, [data[c] for c in cols])
    return cur.lastrowid


def update(table, data: dict, where: str, where_params=()):
    cols = [c for c in data if c not in ("id",)]
    sets = ", ".join("{} = ?".format(c) for c in cols)
    q("UPDATE {} SET {} WHERE {}".format(table, sets, where),
      [data[c] for c in cols] + list(where_params))


SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  login TEXT UNIQUE NOT NULL,
  role TEXT NOT NULL DEFAULT 'admin',
  salt TEXT NOT NULL,
  password_hash TEXT NOT NULL,
  created TEXT
);
CREATE TABLE IF NOT EXISTS templates(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  text TEXT NOT NULL DEFAULT '',
  scenario TEXT NOT NULL DEFAULT '{}',
  active INTEGER NOT NULL DEFAULT 1,
  updated TEXT
);
CREATE TABLE IF NOT EXISTS contacts(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL DEFAULT '',
  phone TEXT NOT NULL,
  grp TEXT NOT NULL DEFAULT '',
  note TEXT NOT NULL DEFAULT '',
  consent INTEGER NOT NULL DEFAULT 0,
  consent_source TEXT NOT NULL DEFAULT '',
  blacklisted INTEGER NOT NULL DEFAULT 0,
  complaints INTEGER NOT NULL DEFAULT 0,
  created TEXT,
  updated TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_contacts_phone ON contacts(phone);
CREATE TABLE IF NOT EXISTS numbers(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  number TEXT UNIQUE NOT NULL,
  label TEXT NOT NULL DEFAULT '',
  kind TEXT NOT NULL DEFAULT 'mobile',
  provider TEXT NOT NULL DEFAULT 'sim',
  active INTEGER NOT NULL DEFAULT 1,
  daily_limit INTEGER NOT NULL DEFAULT 100,
  weight INTEGER NOT NULL DEFAULT 1,
  quarantined INTEGER NOT NULL DEFAULT 0,
  cooldown_until TEXT NOT NULL DEFAULT '',
  daily_date TEXT NOT NULL DEFAULT '',
  daily_count INTEGER NOT NULL DEFAULT 0,
  created TEXT
);
CREATE TABLE IF NOT EXISTS campaigns(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  template_id INTEGER NOT NULL DEFAULT 0,
  flow TEXT NOT NULL DEFAULT 'agent',      -- message | agent | operator
  status TEXT NOT NULL DEFAULT 'stopped', -- stopped | running | paused
  schedule TEXT NOT NULL DEFAULT '{}',    -- {start,end,days}
  max_channels INTEGER NOT NULL DEFAULT 0,-- 0 = из настроек
  retry_max INTEGER NOT NULL DEFAULT -1,  -- -1 = из настроек
  retry_delay_min INTEGER NOT NULL DEFAULT -1,
  retry_map TEXT NOT NULL DEFAULT '{}',  -- {"busy":10,"no_answer":30,...} мин по причинам
  connect_on_qualify INTEGER NOT NULL DEFAULT 1,
  created TEXT,
  updated TEXT
);
CREATE TABLE IF NOT EXISTS campaign_items(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  campaign_id INTEGER NOT NULL,
  contact_id INTEGER NOT NULL,
  contact_name TEXT NOT NULL DEFAULT '',
  contact_phone TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'queued',
  attempts INTEGER NOT NULL DEFAULT 0,
  next_attempt_at TEXT NOT NULL DEFAULT '',
  last_result TEXT NOT NULL DEFAULT '',
  created TEXT,
  updated TEXT,
  completed_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_items_camp ON campaign_items(campaign_id, status);
CREATE TABLE IF NOT EXISTS calls(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  campaign_id INTEGER NOT NULL DEFAULT 0,
  item_id INTEGER NOT NULL DEFAULT 0,
  contact_id INTEGER NOT NULL DEFAULT 0,
  contact_name TEXT NOT NULL DEFAULT '',
  contact_phone TEXT NOT NULL DEFAULT '',
  caller_id TEXT NOT NULL DEFAULT '',
  number_id INTEGER NOT NULL DEFAULT 0,
  provider TEXT NOT NULL DEFAULT '',
  direction TEXT NOT NULL DEFAULT 'out',
  status TEXT NOT NULL DEFAULT 'new',
  result TEXT NOT NULL DEFAULT '',
  detail TEXT NOT NULL DEFAULT '',
  agent_result TEXT NOT NULL DEFAULT '',
  recording TEXT NOT NULL DEFAULT '',
  started_at TEXT NOT NULL DEFAULT '',
  answered_at TEXT NOT NULL DEFAULT '',
  ended_at TEXT NOT NULL DEFAULT '',
  duration_sec INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_calls_started ON calls(started_at);
CREATE TABLE IF NOT EXISTS attempts(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  call_id INTEGER NOT NULL DEFAULT 0,
  item_id INTEGER NOT NULL DEFAULT 0,
  attempt_no INTEGER NOT NULL DEFAULT 0,
  started_at TEXT NOT NULL DEFAULT '',
  ended_at TEXT NOT NULL DEFAULT '',
  result TEXT NOT NULL DEFAULT '',
  detail TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS operators(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL DEFAULT 0,
  name TEXT NOT NULL DEFAULT '',
  ext TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'offline', -- offline|free|busy|break
  updated TEXT
);
CREATE TABLE IF NOT EXISTS acd(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  call_id INTEGER NOT NULL DEFAULT 0,
  item_id INTEGER NOT NULL DEFAULT 0,
  contact_id INTEGER NOT NULL DEFAULT 0,
  contact_name TEXT NOT NULL DEFAULT '',
  contact_phone TEXT NOT NULL DEFAULT '',
  campaign_id INTEGER NOT NULL DEFAULT 0,
  operator_id INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'queued', -- queued|offered|accepted|missed|completed
  created TEXT,
  updated TEXT
);
CREATE TABLE IF NOT EXISTS agent_sessions(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  call_id INTEGER NOT NULL DEFAULT 0,
  scenario TEXT NOT NULL DEFAULT '{}',
  result TEXT NOT NULL DEFAULT '{}',
  transcript TEXT NOT NULL DEFAULT '',
  created TEXT,
  ended_at TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS blacklist(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  phone TEXT UNIQUE NOT NULL,
  reason TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL DEFAULT '',
  created TEXT
);
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT,
  type TEXT,
  payload TEXT
);
CREATE TABLE IF NOT EXISTS settings(
  id INTEGER PRIMARY KEY CHECK (id = 1),
  data TEXT NOT NULL
);
"""

DEFAULT_SCENARIO = {
    "greeting": "Здравствуйте! Это автоматический звонок компании. ",
    "questions": [
        {"id": "q1", "text": "Вам удобно сейчас разговаривать? Скажите «да» или «нет».",
         "choices": {"да": {"next": "q2", "qualified": None}, "нет": {"next": "end", "qualified": False}}}
    ],
    "qualified_text": "Спасибо, передаю ваш звонок специалисту.",
    "not_qualified_text": "Спасибо, до свидания.",
    "default_qualified": False
}


def init_db():
    with _lock:
        c = connect()
        c.executescript(SCHEMA)
        c.commit()
        _migrate(c)
    if not fetch1("SELECT id FROM settings WHERE id=1"):
        save_settings(dict(config.DEFAULT_SETTINGS))
    _seed_templates()
    _seed_numbers()
    _seed_users()
    _import_legacy()


def _migrate(c):
    """Лёгкие миграции для уже созданных БД (новые колонки и т.п.)."""
    cols = [r[1] for r in c.execute("PRAGMA table_info(campaigns)").fetchall()]
    if "retry_map" not in cols:
        c.execute("ALTER TABLE campaigns ADD COLUMN retry_map TEXT NOT NULL DEFAULT '{}'")
        c.commit()


def _seed_templates():
    if fetch1("SELECT id FROM templates LIMIT 1"):
        return
    default_text = "Здравствуйте. Это автоматическое информационное сообщение. {name}, прослушайте, пожалуйста, информацию."
    insert("templates", {"name": "Основной (сообщение)", "text": default_text,
                         "scenario": json.dumps(DEFAULT_SCENARIO, ensure_ascii=False), "active": 1,
                         "updated": config.now_iso()})
    q1 = [{"id": "q1", "text": "Вас интересует наше предложение? Нажмите 1 — да, 2 — нет.",
           "choices": {"1": {"next": "q2", "qualified": None}, "2": {"next": "end", "qualified": False}}}]
    q2 = [{"id": "q2", "text": "Удобно, если специалист перезвонит вам сегодня? Нажмите 1 — да, 2 — нет.",
           "choices": {"1": {"next": "end", "qualified": True}, "2": {"next": "end", "qualified": False}}}]
    q3 = [{"id": "q3", "text": "Вам подойдёт звонок специалиста в первой половине дня? Нажмите 1 — да, 2 — нет.",
           "choices": {"1": {"next": "end", "qualified": True}, "2": {"next": "end", "qualified": True}}}]
    for i, (name, text, questions) in enumerate([
        ("Квалификация (бот)", "Здравствуйте! Компания проводит опрос клиентов.", q1 + q2),
        ("Квалификация подробная", "Здравствуйте! Это служба заботы о клиентах.", q1 + q2 + q3),
    ]):
        sc = dict(DEFAULT_SCENARIO)
        sc["questions"] = questions
        insert("templates", {"name": name, "text": text, "scenario": json.dumps(sc, ensure_ascii=False),
                             "active": 1, "updated": config.now_iso()})


def _seed_numbers(count=None):
    if fetch1("SELECT id FROM numbers LIMIT 1"):
        return
    count = count or 5
    for i in range(1, count + 1):
        insert("numbers", {"number": "7900000000{:02d}".format(i), "label": "Пилотный №{}".format(i),
                           "kind": "mobile", "provider": "sim", "active": 1,
                           "daily_limit": 100, "weight": 1, "quarantined": 0,
                           "cooldown_until": "", "created": config.now_iso()})


def _seed_users():
    if fetch1("SELECT id FROM users LIMIT 1"):
        return
    password = os.environ.get("ATS_ADMIN_PASSWORD", "")
    generated = False
    if not password:
        password = gen_admin_pwd()
        generated = True
    salt = config_secure_salt()
    from .security import hash_password
    import sqlite3 as _s
    try:
        insert("users", {"login": "admin", "role": "admin", "salt": salt,
                         "password_hash": hash_password(password, salt), "created": config.now_iso()})
    except Exception:
        pass
    # Оператор-демо
    salt2 = config_secure_salt()
    insert("users", {"login": "operator", "role": "operator", "salt": salt2,
                     "password_hash": hash_password("operator1234", salt2), "created": config.now_iso()})
    insert("operators", {"user_id": 2, "name": "Оператор (демо)", "ext": "101", "status": "offline",
                         "updated": config.now_iso()})
    note = ("Логин: admin  Пароль: {}  (роль admin)\n"
            "Логин: operator  Пароль: operator1234  (роль operator)\n"
            "СМЕНИТЕ ПАРОЛЬ АДМИНА в Настройках после первого входа.").format(password)
    (config.DATA_DIR / "initial_credentials.txt").write_text(note, encoding="utf-8")
    print("[ATS v2] Создан администратор. Учётные данные записаны в data_v2/initial_credentials.txt")
    if generated:
        print("[ATS v2] Админ-пароль (сгенерирован):", password)


def gen_admin_pwd():
    from .security import gen_admin_password
    return gen_admin_password()


def config_secure_salt():
    from .security import new_salt
    return new_salt()


def _import_legacy():
    """Первичный импорт контактов и шаблонов из JSON старого прототипа (data/*.json)."""
    if fetch1("SELECT id FROM contacts LIMIT 1") or not config.LEGACY_DATA.exists():
        return
    try:
        raw = json.loads((config.LEGACY_DATA / "contacts.json").read_text(encoding="utf-8"))
        added = 0
        for c in raw:
            phone = "".join(ch for ch in str(c.get("phone", "")) if ch.isdigit() or ch == "+")
            if not phone:
                continue
            try:
                insert("contacts", {"name": c.get("name", ""), "phone": phone, "grp": c.get("group", ""),
                                    "note": c.get("note", ""), "consent": 1 if c.get("consent") else 0,
                                    "consent_source": "legacy_import", "blacklisted": 0, "complaints": 0,
                                    "created": config.now_iso(), "updated": config.now_iso()})
                added += 1
            except Exception:
                continue
        print("[ATS v2] Импортировано контактов из legacy:", added)
    except Exception as e:
        print("[ATS v2] Импорт legacy контактов пропущен:", e)
    try:
        raw = json.loads((config.LEGACY_DATA / "templates.json").read_text(encoding="utf-8"))
        for t in raw:
            insert("templates", {"name": t.get("name", "Legacy"), "text": t.get("text", ""),
                                 "scenario": json.dumps(DEFAULT_SCENARIO, ensure_ascii=False),
                                 "active": 1 if t.get("active", True) else 0, "updated": config.now_iso()})
        print("[ATS v2] Импортировано шаблонов из legacy:", len(raw))
    except Exception as e:
        print("[ATS v2] Импорт legacy шаблонов пропущен:", e)


# ---- Настройки ----
def get_settings() -> dict:
    r = fetch1("SELECT data FROM settings WHERE id=1")
    base = dict(config.DEFAULT_SETTINGS)
    if r:
        try:
            base.update(json.loads(r["data"]))
        except Exception:
            pass
    return base


def save_settings(data: dict):
    merged = dict(config.DEFAULT_SETTINGS)
    merged.update({k: v for k, v in data.items()})
    payload = json.dumps(merged, ensure_ascii=False)
    with _lock:
        q("INSERT INTO settings(id, data) VALUES(1, ?) ON CONFLICT(id) DO UPDATE SET data=excluded.data", (payload,))


def public_settings() -> dict:
    s = get_settings()
    for k in ("uis", "ami", "llm", "bitrix24"):
        pass  # не отдаём секреты наружу
    pub = {k: v for k, v in s.items() if k not in ("uis", "ami", "llm", "bitrix24")}
    return pub


def update_setting(key, value):
    s = get_settings()
    s[key] = value
    save_settings(s)
