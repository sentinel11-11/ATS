# -*- coding: utf-8 -*-
"""МегаФон ВАТС, стадия 3: синк CallerID-пула, синк users/groups, маппинг
операторов (vats_login).

Запуск: python -m unittest discover -s tests -v   (из корня репозитория)
"""
import os
import tempfile
import unittest

_TMP = tempfile.mkdtemp(prefix="ats_test_mfsync_")
os.environ["ATS_FAST"] = "1"
os.environ["ATS_DATA_DIR"] = _TMP
os.environ["ATS_ADMIN_PASSWORD"] = "TestAdmin123!"
os.environ["ATS_DEV_SEED"] = "1"

from app import api, config, db, numbers as numbers_mod  # noqa: E402
from app import security  # noqa: E402
from app.engine import Engine  # noqa: E402
from app.providers.megafon_vats import plan_number_sync  # noqa: E402
from tests.test_megafon_vats import FakeMegaFonVatsServer  # noqa: E402

db.init_db()


class PlannerTest(unittest.TestCase):
    def test_add_enable_disable(self):
        caller = [{"telnum": "79262005060", "enabled": True},
                  {"telnum": "79264010121", "enabled": False}]
        telnums = [{"telnum": "79262005060", "disabled": False, "name": "Осн"},
                   {"telnum": "79264010121", "disabled": False}]
        local = [{"id": 1, "number": "+79262005060", "enabled_outgoing": 0},
                 {"id": 2, "number": "79264010121", "enabled_outgoing": 1},
                 {"id": 3, "number": "79990000000", "enabled_outgoing": 1}]
        plan = plan_number_sync(caller, telnums, local)
        self.assertEqual(plan["add"], [])
        self.assertEqual(plan["enable"], [1])
        self.assertEqual(sorted(plan["disable"]), [2, 3])  # запрет + вне API

    def test_add_new_usable(self):
        plan = plan_number_sync([{"telnum": "79262005060", "enabled": True}],
                                [{"telnum": "79262005060", "disabled": False,
                                  "name": "Осн"}], [])
        self.assertEqual(plan["add"][0]["number"], "79262005060")
        self.assertEqual(plan["add"][0]["carrier"], "megafon")
        self.assertEqual(plan["add"][0]["label"], "Осн")

    def test_fail_closed_matrix(self):
        # enabled=true, но маршрут выключен → непригоден
        plan = plan_number_sync([{"telnum": "7001", "enabled": True}],
                                [{"telnum": "7001", "disabled": True}], [])
        self.assertEqual(plan, {"add": [], "enable": [], "disable": []})
        # нет в caller-ids вообще → непригоден, хотя маршрут включён
        plan = plan_number_sync([],
                                [{"telnum": "7002", "disabled": False}], [])
        self.assertEqual(plan["add"], [])
        # enabled не boolean True (1/"true") → непригоден
        plan = plan_number_sync([{"telnum": "7003", "enabled": 1}], [], [])
        self.assertEqual(plan["add"], [])

    def test_digits_compare(self):
        plan = plan_number_sync([{"telnum": "+7 (926) 200-50-60", "enabled": True}],
                                [], [{"id": 9, "number": "89262005060",
                                      "enabled_outgoing": 0}])
        self.assertEqual(plan["enable"], [9])  # 8↔7 — тот же номер


class SyncTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import pathlib as _pl
        cls._fresh = _pl.Path(tempfile.mkdtemp(prefix="ats_mfsync_"))
        try:
            if db._conn is not None:
                db._conn.close()
        except Exception:
            pass
        db._conn = None
        config.DB_PATH = cls._fresh / "mfsync.db"
        db.init_db()
        cls.vats = FakeMegaFonVatsServer()
        s = db.get_settings()
        s["provider"] = "megafon_vats"
        s["megafon_vats"] = {"base_url": cls.vats.url, "api_key": "test-key",
                             "crm_token": "SECRET", "default_user": "admin",
                             "timeout_sec": 5}
        db.save_settings(s)
        db.insert("users", {"login": "op1", "role": "operator", "salt": "s",
                                  "password_hash": "h", "active": 1, "created": ""})
        cls._old_engine = api.ENGINE
        cls.engine = Engine(auto_start=False)
        api.ENGINE = cls.engine

    @classmethod
    def tearDownClass(cls):
        api.ENGINE = cls._old_engine
        cls.vats.stop()

    def setUp(self):
        db.q("DELETE FROM numbers")
        db.q("DELETE FROM vats_users")
        db.q("DELETE FROM vats_groups")

    def _admin(self):
        return {"X-Ats-Token": security.create_token("admin", "admin")}

    def _operator(self):
        return {"X-Ats-Token": security.create_token("op1", "operator")}

    # --- пул ---
    def test_pool_sync_add_enable_disable(self):
        numbers_mod.add_number("79262005060", provider="megafon_vats",
                               enabled_outgoing=0)
        numbers_mod.add_number("79264010121", provider="megafon_vats")
        numbers_mod.add_number("79990000000", provider="megafon_vats")
        rep = self.engine.megafon_pool_sync()
        self.assertFalse(rep["dry_run"])
        self.assertEqual(rep["added"], [])  # все уже локально
        self.assertEqual(rep["enabled"], ["79262005060"])
        self.assertEqual(sorted(rep["disabled"]), ["79264010121", "79990000000"])
        rows = {r["number"]: r for r in db.fetch("SELECT * FROM numbers")}
        self.assertEqual(rows["79262005060"]["enabled_outgoing"], 1)
        self.assertEqual(rows["79264010121"]["enabled_outgoing"], 0)
        self.assertEqual(rows["79990000000"]["enabled_outgoing"], 0)

    def test_pool_sync_adds_usable_only(self):
        rep = self.engine.megafon_pool_sync()
        self.assertEqual(rep["added"], ["79262005060"])
        rows = db.fetch("SELECT * FROM numbers")
        self.assertEqual(len(rows), 1)  # 10121/09999 непригодны — не добавляем
        self.assertEqual(rows[0]["carrier"], "megafon")
        self.assertEqual(rows[0]["label"], "Основной")
        self.assertEqual(rows[0]["enabled_outgoing"], 1)

    def test_pool_sync_dry_run_no_writes(self):
        numbers_mod.add_number("79990000000", provider="megafon_vats")
        rep = self.engine.megafon_pool_sync(dry_run=True)
        self.assertTrue(rep["dry_run"])
        self.assertEqual(rep["added"], ["79262005060"])
        rows = db.fetch("SELECT * FROM numbers")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["enabled_outgoing"], 1)  # не тронут

    def test_acquire_skips_disabled_outgoing(self):
        numbers_mod.add_number("79264010121", provider="megafon_vats",
                               enabled_outgoing=0)
        self.assertIsNone(numbers_mod.acquire(provider="megafon_vats",
                                              cooldown_sec=0))
        numbers_mod.add_number("79262005060", provider="megafon_vats")
        num = numbers_mod.acquire(provider="megafon_vats", cooldown_sec=0)
        self.assertEqual(num["number"], "79262005060")

    def test_pool_sync_route(self):
        payload, code = api.route("POST", "/api/v2/megafon/pool-sync",
                                  {"dry_run": True}, self._admin())
        self.assertEqual(code, 200)
        self.assertEqual(payload["report"]["added"], ["79262005060"])
        payload, code = api.route("POST", "/api/v2/megafon/pool-sync",
                                  {}, self._operator())
        self.assertEqual(code, 403)
        payload, code = api.route("POST", "/api/v2/megafon/pool-sync", {}, {})
        self.assertEqual(code, 401)

    def test_pool_sync_not_configured(self):
        s = db.get_settings()
        old = s.get("megafon_vats")
        s["megafon_vats"] = {"base_url": "", "api_key": ""}
        s["provider"] = "sim"
        db.save_settings(s)
        # движок читает настройки при создании — пересоздаём
        eng2 = Engine(auto_start=False)
        old_eng = api.ENGINE
        api.ENGINE = eng2
        try:
            payload, code = api.route("POST", "/api/v2/megafon/pool-sync",
                                      {}, self._admin())
            self.assertEqual(code, 400)
            self.assertEqual(payload["error"], "vats_not_configured")
        finally:
            api.ENGINE = old_eng
            s["megafon_vats"] = old
            s["provider"] = "megafon_vats"
            db.save_settings(s)

    # --- сотрудники и отделы ---
    def test_users_sync_and_mapping(self):
        uid = db.insert("users", {"login": "opm", "role": "operator", "salt": "s",
                                  "password_hash": "h", "active": 1, "created": ""})
        db.insert("operators", {"user_id": uid, "name": "Опер", "ext": "702",
                                "vats_login": "manager", "status": "offline",
                                "updated": ""})
        uid2 = db.insert("users", {"login": "ghostm", "role": "operator", "salt": "s",
                                   "password_hash": "h", "active": 1, "created": ""})
        db.insert("operators", {"user_id": uid2, "name": "Призрак", "ext": "799",
                                "vats_login": "ghost", "status": "offline",
                                "updated": ""})
        rep = self.engine.megafon_users_sync()
        self.assertEqual(rep["total"], 5)
        self.assertEqual(rep["upserted"], 5)
        self.assertEqual(rep["mapped"], ["manager"])
        self.assertEqual(len(rep["dangling"]), 1)
        self.assertIn("admin", rep["unmapped"])
        row = db.fetch1("SELECT * FROM vats_users WHERE login='admin'")
        self.assertEqual(row["ext"], "701")
        # повторный синк — апдейт, не дубли
        self.engine.megafon_users_sync()
        self.assertEqual(db.fetch1("SELECT COUNT(*) c FROM vats_users")["c"], 5)

    def test_groups_sync(self):
        rep = self.engine.megafon_groups_sync()
        self.assertEqual(rep["upserted"], 1)
        row = db.fetch1("SELECT * FROM vats_groups WHERE group_id='sales'")
        self.assertEqual(row["name"], "Отдел продаж")

    def test_sync_routes_and_directory(self):
        payload, code = api.route("POST", "/api/v2/megafon/users-sync",
                                  {}, self._admin())
        self.assertEqual(code, 200)
        self.assertEqual(payload["report"]["total"], 5)
        payload, code = api.route("POST", "/api/v2/megafon/groups-sync",
                                  {}, self._admin())
        self.assertEqual(code, 200)
        payload, code = api.route("GET", "/api/v2/megafon/directory",
                                  {}, self._operator())
        self.assertEqual(code, 200)
        self.assertEqual(len(payload["users"]), 5)
        self.assertEqual(payload["groups"][0]["group_id"], "sales")

    # --- vats_login в карточке пользователя ---
    def test_user_save_vats_login(self):
        payload, code = api.route(
            "POST", "/api/v2/users/save",
            {"login": "op2", "role": "operator", "password": "secret12",
             "name": "Опер2", "ext": "703", "vats_login": "ivan"}, self._admin())
        self.assertEqual(code, 200)
        op = db.fetch1("SELECT * FROM operators WHERE user_id=?", (payload["id"],))
        self.assertEqual(op["vats_login"], "ivan")
        # правка без ключа — не затирает
        payload2, code2 = api.route(
            "POST", "/api/v2/users/save",
            {"id": payload["id"], "login": "op2", "role": "operator",
             "name": "Опер2", "ext": "703"}, self._admin())
        self.assertEqual(code2, 200)
        op = db.fetch1("SELECT * FROM operators WHERE user_id=?", (payload["id"],))
        self.assertEqual(op["vats_login"], "ivan")
        # правка с ключом — обновляет
        api.route("POST", "/api/v2/users/save",
                  {"id": payload["id"], "login": "op2", "role": "operator",
                   "name": "Опер2", "ext": "703", "vats_login": "manager"},
                  self._admin())
        op = db.fetch1("SELECT * FROM operators WHERE user_id=?", (payload["id"],))
        self.assertEqual(op["vats_login"], "manager")
        # выдача содержит маппинг
        lst, code3 = api.route("GET", "/api/v2/users", {}, self._admin())
        self.assertEqual(code3, 200)
        mine = [u for u in lst["users"] if u["login"] == "op2"][0]
        self.assertEqual(mine["op_vats"], "manager")


class MigrationTest(unittest.TestCase):
    """Регрессия: init_db на СТАРОЙ БД (таблицы без новых колонок) не падает.

    Ловит ошибку «индекс по новой колонке в SCHEMA»: на существующих БД
    CREATE TABLE IF NOT EXISTS колонок не добавляет, и CREATE INDEX падает
    с no such column. Индексы по новым колонкам — только в _migrate()."""

    _OLD_SCHEMA = """
CREATE TABLE calls(id INTEGER PRIMARY KEY AUTOINCREMENT,
campaign_id INTEGER NOT NULL DEFAULT 0, item_id INTEGER NOT NULL DEFAULT 0,
contact_id INTEGER NOT NULL DEFAULT 0, contact_name TEXT NOT NULL DEFAULT '',
contact_phone TEXT NOT NULL DEFAULT '', caller_id TEXT NOT NULL DEFAULT '',
number_id INTEGER NOT NULL DEFAULT 0, provider TEXT NOT NULL DEFAULT '',
direction TEXT NOT NULL DEFAULT 'out', status TEXT NOT NULL DEFAULT 'new',
result TEXT NOT NULL DEFAULT '', detail TEXT NOT NULL DEFAULT '',
agent_result TEXT NOT NULL DEFAULT '', recording TEXT NOT NULL DEFAULT '',
started_at TEXT NOT NULL DEFAULT '', answered_at TEXT NOT NULL DEFAULT '',
ended_at TEXT NOT NULL DEFAULT '', duration_sec INTEGER NOT NULL DEFAULT 0);
CREATE TABLE numbers(id INTEGER PRIMARY KEY AUTOINCREMENT,
number TEXT UNIQUE NOT NULL, label TEXT NOT NULL DEFAULT '',
kind TEXT NOT NULL DEFAULT 'mobile', provider TEXT NOT NULL DEFAULT 'sim',
active INTEGER NOT NULL DEFAULT 1, daily_limit INTEGER NOT NULL DEFAULT 100,
weight INTEGER NOT NULL DEFAULT 1, quarantined INTEGER NOT NULL DEFAULT 0,
cooldown_until TEXT NOT NULL DEFAULT '', daily_date TEXT NOT NULL DEFAULT '',
daily_count INTEGER NOT NULL DEFAULT 0, created TEXT);
CREATE TABLE operators(id INTEGER PRIMARY KEY AUTOINCREMENT,
user_id INTEGER NOT NULL DEFAULT 0, name TEXT NOT NULL DEFAULT '',
ext TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'offline',
updated TEXT);
"""

    def test_old_db_migrates(self):
        import pathlib as _pl
        import sqlite3
        tmp = _pl.Path(tempfile.mkdtemp(prefix="ats_mig_")) / "old.db"
        c = sqlite3.connect(str(tmp))
        c.executescript(self._OLD_SCHEMA)
        c.execute("INSERT INTO calls(contact_phone,status) VALUES ('79000000001','done')")
        c.commit()
        c.close()
        old_path, old_conn = config.DB_PATH, db._conn
        try:
            try:
                if old_conn is not None:
                    old_conn.close()
            except Exception:
                pass
            db._conn = None
            config.DB_PATH = tmp
            db.init_db()  # главная проверка: не падает
            cols = [r["name"] for r in db.fetch("PRAGMA table_info(calls)")]
            for col in ("external_call_id", "diversion", "provider_user",
                        "recording_url", "external_status", "wait_sec",
                        "missed_status", "rating"):
                self.assertIn(col, cols)
            idx = [r["name"] for r in
                   db.fetch("SELECT name FROM sqlite_master WHERE type='index'")]
            for ix in ("ix_calls_provider_ext", "ux_provider_events_fp",
                       "ix_provider_events_call"):
                self.assertIn(ix, idx)
            tbl = [r["name"] for r in
                   db.fetch("SELECT name FROM sqlite_master WHERE type='table'")]
            for t in ("provider_events", "vats_users", "vats_groups"):
                self.assertIn(t, tbl)
            ncols = [r["name"] for r in db.fetch("PRAGMA table_info(numbers)")]
            for col in ("carrier", "provider_ref", "enabled_outgoing"):
                self.assertIn(col, ncols)
            self.assertIn("vats_login", [r["name"] for r in
                                         db.fetch("PRAGMA table_info(operators)")])
            rows = db.fetch("SELECT * FROM calls")
            self.assertEqual(len(rows), 1)  # данные пользователя целы
            self.assertEqual(rows[0]["contact_phone"], "79000000001")
            db.init_db()  # идемпотентность
        finally:
            try:
                if db._conn is not None:
                    db._conn.close()
            except Exception:
                pass
            db._conn = None
            config.DB_PATH = old_path


if __name__ == "__main__":
    unittest.main()
