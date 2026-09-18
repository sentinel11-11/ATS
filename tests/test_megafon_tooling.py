# -*- coding: utf-8 -*-
"""МегаФон ВАТС, стадия 4 (COMMIT 10): health, проверка соединения,
симулятор вебхуков.

Запуск: python -m unittest discover -s tests -v   (из корня репозитория)
"""
import json
import os
import tempfile
import unittest

_TMP = tempfile.mkdtemp(prefix="ats_test_mftool_")
os.environ["ATS_FAST"] = "1"
os.environ["ATS_DATA_DIR"] = _TMP
os.environ["ATS_ADMIN_PASSWORD"] = "TestAdmin123!"
os.environ["ATS_DEV_SEED"] = "1"

from app import api, config, db  # noqa: E402
from app import security  # noqa: E402
from app.engine import Engine  # noqa: E402
from tests.test_megafon_vats import FakeMegaFonVatsServer  # noqa: E402

db.init_db()


class ToolingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import pathlib as _pl
        cls._fresh = _pl.Path(tempfile.mkdtemp(prefix="ats_mftool_"))
        try:
            if db._conn is not None:
                db._conn.close()
        except Exception:
            pass
        db._conn = None
        config.DB_PATH = cls._fresh / "mftool.db"
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

    def _admin(self):
        return {"X-Ats-Token": security.create_token("admin", "admin")}

    def _operator(self):
        return {"X-Ats-Token": security.create_token("op1", "operator")}

    # --- health ---
    def test_health_public_no_secrets(self):
        payload, code = api.route("GET", "/api/v2/health", {}, {})
        self.assertEqual(code, 200)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["db"])
        self.assertTrue(payload["engine"])
        self.assertEqual(payload["telephony"]["provider"], "megafon_vats")
        self.assertTrue(payload["telephony"]["connected"])
        self.assertTrue(payload["webhook"])
        self.assertIn("timestamp", payload)
        blob = json.dumps(payload)
        self.assertNotIn("SECRET", blob)
        self.assertNotIn("test-key", blob)

    def test_health_webhook_flag(self):
        s = db.get_settings()
        old = s.get("megafon_vats")
        s["megafon_vats"] = {"crm_token": "", "crm_token_env": "ATS_MFTOOL_NOPE"}
        if "ATS_MFTOOL_NOPE" in os.environ:
            del os.environ["ATS_MFTOOL_NOPE"]
        db.save_settings(s)
        try:
            payload, _ = api.route("GET", "/api/v2/health", {}, {})
            self.assertFalse(payload["webhook"])
        finally:
            s["megafon_vats"] = old
            db.save_settings(s)

    def test_health_ami_connected_semantics(self):
        class _Cli:
            def __init__(self, c):
                self.connected = c

        class _Prov:
            name = "ami"

            def __init__(self, c):
                self.client = _Cli(c)

        class _Eng:
            def __init__(self, c):
                self.provider = _Prov(c)

        old = api.ENGINE
        try:
            api.ENGINE = _Eng(True)
            payload, _ = api.route("GET", "/api/v2/health", {}, {})
            self.assertTrue(payload["telephony"]["connected"])
            api.ENGINE = _Eng(False)
            payload, _ = api.route("GET", "/api/v2/health", {}, {})
            self.assertFalse(payload["telephony"]["connected"])
        finally:
            api.ENGINE = old

    def test_health_details_admin_only_masked(self):
        payload, code = api.route("GET", "/api/v2/health/details", {}, {})
        self.assertEqual(code, 401)
        payload, code = api.route("GET", "/api/v2/health/details", {},
                                  self._operator())
        self.assertEqual(code, 403)
        s = db.get_settings()
        mf = dict(s.get("megafon_vats") or {})
        mf["api_key"] = "LITERAL-SECRET-KEY"
        s["megafon_vats"] = mf
        db.save_settings(s)
        try:
            payload, code = api.route("GET", "/api/v2/health/details", {},
                                      self._admin())
            self.assertEqual(code, 200)
            self.assertIn("health", payload)
            self.assertIn("stats", payload)
            self.assertIn("pool_megafon", payload["stats"])
            blob = json.dumps(payload)
            self.assertNotIn("LITERAL-SECRET-KEY", blob)
            self.assertNotIn("SECRET", blob)
            self.assertEqual(payload["provider_config"]["megafon_vats"]["api_key"],
                             "********")
        finally:
            mf["api_key"] = "test-key"
            s["megafon_vats"] = mf
            db.save_settings(s)

    # --- check ---
    def test_check_ok(self):
        payload, code = api.route("POST", "/api/v2/megafon/check", {},
                                  self._admin())
        self.assertEqual(code, 200)
        self.assertTrue(payload["ok"])
        stages = payload["report"]["stages"]
        self.assertTrue(stages["users"]["ok"])
        self.assertTrue(stages["telnums"]["ok"])
        self.assertTrue(stages["caller_ids"]["ok"])

    def test_check_rbac(self):
        payload, code = api.route("POST", "/api/v2/megafon/check", {},
                                  self._operator())
        self.assertEqual(code, 403)

    def test_check_bad_key_502(self):
        s = db.get_settings()
        old = dict(s.get("megafon_vats") or {})
        s["megafon_vats"] = dict(old, api_key="WRONG")
        db.save_settings(s)
        eng2 = Engine(auto_start=False)
        prev = api.ENGINE
        api.ENGINE = eng2
        try:
            payload, code = api.route("POST", "/api/v2/megafon/check", {},
                                      self._admin())
            self.assertEqual(code, 502)
            self.assertEqual(payload["report"]["failed_stage"], "users")
            self.assertEqual(payload["report"]["stages"]["users"]["status"], 401)
        finally:
            api.ENGINE = prev
            s["megafon_vats"] = old
            db.save_settings(s)

    def test_check_unreachable_502(self):
        s = db.get_settings()
        old = dict(s.get("megafon_vats") or {})
        s["megafon_vats"] = dict(old, base_url="http://127.0.0.1:9",
                                 timeout_sec=2)
        db.save_settings(s)
        eng2 = Engine(auto_start=False)
        prev = api.ENGINE
        api.ENGINE = eng2
        try:
            payload, code = api.route("POST", "/api/v2/megafon/check", {},
                                      self._admin())
            self.assertEqual(code, 502)
            self.assertIsNone(payload["report"]["stages"]["users"]["status"])
        finally:
            api.ENGINE = prev
            s["megafon_vats"] = old
            db.save_settings(s)

    # --- simulate-event ---
    def test_simulate_event(self):
        payload, code = api.route(
            "POST", "/api/v2/megafon/simulate-event",
            {"cmd": "event", "type": "INCOMING", "callid": "sim1",
             "phone": "79260000901", "direction": "in"}, self._admin())
        self.assertEqual(code, 200)
        self.assertEqual(payload["event"]["provider"], "megafon_vats")
        got = self.engine.drain_events()
        self.assertEqual(len(got), 1)
        self.assertTrue(got[0]["fingerprint"])
        # движок реально обрабатывает симуляцию
        self.engine.handle_event(got[0])
        call = db.fetch1("SELECT * FROM calls WHERE external_call_id='sim1'")
        self.assertEqual(call["direction"], "in")

    def test_simulate_unrecognized_and_rbac(self):
        payload, code = api.route("POST", "/api/v2/megafon/simulate-event",
                                  {"cmd": "bogus"}, self._admin())
        self.assertEqual(code, 422)
        payload, code = api.route("POST", "/api/v2/megafon/simulate-event",
                                  {"cmd": "event"}, self._operator())
        self.assertEqual(code, 403)


if __name__ == "__main__":
    unittest.main()
