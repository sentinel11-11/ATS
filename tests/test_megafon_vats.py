# -*- coding: utf-8 -*-
"""МегаФон ВАТС, стадия 1: REST-клиент (/crmapi/v1), провайдер, конфигурация.

Mock: FakeMegaFonVatsServer отвечает в формах официальной документации
«REST API ВАТС (CRM)»: списки — конверт {items, info}, history/json —
голый массив, history/csv — строки без заголовка, auth — X-API-KEY.

Запуск: python -m unittest discover -s tests -v   (из корня репозитория)
"""
import io
import json
import os
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

_TMP = tempfile.mkdtemp(prefix="ats_test_megafon_")
os.environ["ATS_FAST"] = "1"
os.environ["ATS_DATA_DIR"] = _TMP
os.environ["ATS_ADMIN_PASSWORD"] = "TestAdmin123!"
os.environ["ATS_DEV_SEED"] = "1"

from app import api, config, db, numbers as numbers_mod  # noqa: E402
from app import security  # noqa: E402
from app.providers.base import resolve_secret  # noqa: E402
from app.providers.megafon_vats import (  # noqa: E402
    MegafonApiError, MegafonVatsClient, MegafonVatsProvider)
from app.telephony import ProviderNotConfigured, make_provider  # noqa: E402

db.init_db()

USERS = [
    {"login": "admin", "name": "Администратор", "position": "Администратор",
     "ext": "701", "telnum": "", "email": "admin@example.com", "role": "admin",
     "mobile": "79263808397", "mobile_redirect": {"enabled": True, "forward": False,
                                                 "delay": 15}, "status": "online"},
    {"login": "manager", "name": "Менеджер", "position": "Менеджер",
     "ext": "702", "telnum": "", "email": "", "role": "user",
     "mobile": "", "mobile_redirect": {"enabled": False}, "status": "online"},
    {"login": "ivan", "name": "Иван", "position": "Менеджер",
     "ext": "703", "telnum": "", "email": "", "role": "user",
     "mobile": "", "mobile_redirect": {"enabled": False}, "status": "offline"},
    {"login": "u704", "name": "Пользователь", "position": "Менеджер",
     "ext": "704", "telnum": "", "email": "", "role": "user",
     "mobile": "", "mobile_redirect": {"enabled": False}, "status": "offline"},
    {"login": "u705", "name": "Пользователь2", "position": "Менеджер",
     "ext": "705", "telnum": "", "email": "", "role": "user",
     "mobile": "", "mobile_redirect": {"enabled": False}, "status": "offline"},
]


def _envelope(items, start, limit, search=""):
    total = len(items)
    return {"items": items[start:start + limit],
            "info": {"search": search, "start": start, "limit": limit,
                     "total": total,
                     "next": min(start + limit, total)}}


class _FakeHandler(BaseHTTPRequestHandler):
    server_version = "FakeVATS"
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        return

    def _send(self, obj, status=200, ctype="application/json"):
        if obj is None:
            body = b""
        elif isinstance(obj, str):
            body = obj.encode("utf-8")
        else:
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def _read_json(self):
        try:
            n = int(self.headers.get("Content-Length", "0") or 0)
        except Exception:
            n = 0
        if n <= 0:
            return None
        try:
            return json.loads(self.rfile.read(n).decode("utf-8", "ignore") or "null")
        except ValueError:
            return None

    def _route(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if self.headers.get("X-API-KEY", "") != self.server.api_key:
            return self._send("Invalid token", 401, "text/plain")
        if q.get("mode") == ["slow"]:
            time.sleep(2)
        body = self._read_json() if self.command in ("POST", "PUT") else None
        self.server.requests.append({"method": self.command, "path": u.path,
                                     "query": q, "body": body})
        p, m = u.path, self.command
        if not p.startswith("/crmapi/v1/"):
            return self._send("not found", 404, "text/plain")
        ep = p[len("/crmapi/v1/"):]

        if ep == "users" and m == "GET":
            start = int((q.get("start") or ["0"])[0] or 0)
            limit = int((q.get("limit") or ["100"])[0] or 100)
            items = USERS
            if (q.get("search") or [""])[0]:
                s = q["search"][0].lower()
                items = [x for x in USERS if s in (x["login"] + x["name"]).lower()]
            return self._send(_envelope(items, start, limit, (q.get("search") or [""])[0]))
        if ep.startswith("users/") and "/groups" in ep and m == "GET":
            return self._send([{"id": "sales", "name": "Отдел продаж", "ext": "700"}])
        if "/subscription" in ep:
            return self._send({"state": True} if m == "GET" else None,
                              200 if m == "GET" else 204)
        if ep.endswith("/dnd"):
            return self._send({"state": True} if m == "GET" else None,
                              200 if m == "GET" else 204)
        if ep.startswith("users/") and m == "GET":
            login = ep.split("/")[1]
            for x in USERS:
                if x["login"] == login or x["ext"] == login:
                    return self._send(x)
            return self._send("not found", 404, "text/plain")
        if ep == "groups" and m == "GET":
            return self._send(_envelope(
                [{"id": "sales", "name": "Отдел продаж", "ext": "700",
                  "call_order": "BYORDER", "call_duration": 10, "users": [],
                  "timeout": {"time": 120, "target": "user", "user": "admin"},
                  "advanced": "off", "queue_position": False}], 0, 100))
        if ep.startswith("groups/") and m == "GET":
            gid = ep.split("/")[1]
            return self._send({"id": gid, "name": "Отдел продаж", "ext": "700",
                               "call_order": "BYORDER", "call_duration": 10,
                               "users": [], "timeout": {}, "advanced": "off",
                               "queue_position": False})
        if ep == "telnums" and m == "GET":
            return self._send(_envelope(
                [{"type": "group", "group": "sales", "group_name": "Отдел продаж",
                  "greeting": False, "is_main_phone": True, "location": "",
                  "disabled": False, "telnum": "79264010121", "name": "",
                  "crm": "", "calltracking": ""}], 0, 100))
        if ep.startswith("telnums/") and m == "GET":
            t = ep.split("/")[1]
            return self._send({"type": "user", "user": "admin", "disabled": False,
                               "telnum": t, "is_main_phone": False})
        if ep == "sims" and m == "GET":
            return self._send(_envelope(
                [{"telnum": "79262005060", "user": "admin", "company": False,
                  "autocaller": False, "disabled": False}], 0, 100))
        if ep.startswith("sims/") and m == "GET":
            t = ep.split("/")[1]
            return self._send({"telnum": t, "user": "admin", "company": False,
                               "autocaller": False, "disabled": False})
        if ep == "caller-ids" and m == "GET":
            return self._send({"main": "79262005060",
                               "users": [{"login": "admin", "name": "Администратор",
                                          "telnum": "79262005060"}],
                               "groups": [], "regions": []})
        if ep == "caller-ids/telnums" and m == "GET":
            return self._send([{"telnum": "79262005060", "enabled": True},
                               {"telnum": "79264010121", "enabled": False}])
        if ep == "makecall" and m == "POST":
            if not body or not body.get("phone"):
                return self._send("Validation error", 400, "text/plain")
            if body.get("phone") == "70000000000":
                return self._send("Validation error", 400, "text/plain")
            if body.get("phone") == "71111111111":
                return self._send({"noid": True})  # ВАТС не вернула callid
            out = {"callid": "2015948553"}
            # clid возвращается, только если «может использоваться» (§5.1)
            if body.get("clid") and body["clid"] != "79264010121":
                out["clid"] = body["clid"]
            return self._send(out)
        if ep == "history/json" and m == "GET":
            return self._send([{"uid": "1755936870", "type": "in", "status": "success",
                                "client": "79263808397", "user": "admin",
                                "start": "2022-01-20T08:58:42Z", "wait": 5,
                                "duration": 23, "record": ""}])
        if ep == "history/csv" and m == "GET":
            return self._send(
                "1755936870,success,74951904198,79262005060,"
                "2022-01-20T08:58:42Z,5,23,,4\n",
                ctype="text/csv")
        if ep == "history/inner/json" and m == "GET":
            return self._send([{"uid": "3934307521", "status": "noanswer",
                                "from": "admin", "to": "manager",
                                "start": "2022-01-20T08:59:22Z", "wait": 13,
                                "duration": 0, "record": ""}])
        if ep == "history/inner/csv" and m == "GET":
            return self._send("3934307521,noanswer,admin,manager,"
                              "2022-01-20T08:59:22Z,13,0,\n", ctype="text/csv")
        if ep == "record" and m == "GET":
            return self._send({"external": True, "inner": True, "users_exception": []})
        if ep == "domain" and m == "GET":
            return self._send({"timezone": {"name": "Europe/Moscow", "offset": 180},
                               "limits": {}, "services": {"record": True}})
        return self._send("not found", 404, "text/plain")

    do_GET = _route
    do_POST = _route
    do_PUT = _route
    do_DELETE = _route


class FakeMegaFonVatsServer:
    """Mock ВАТС по документации: X-API-KEY, конверты списков, makecall."""

    def __init__(self, api_key="test-key"):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _FakeHandler)
        self.httpd.daemon_threads = True
        self.httpd.api_key = api_key
        self.httpd.requests = []
        self._t = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self._t.start()

    @property
    def url(self):
        return "http://127.0.0.1:{}".format(self.httpd.server_port)

    @property
    def requests(self):
        return self.httpd.requests

    def stop(self):
        try:
            self.httpd.shutdown()
        except Exception:
            pass
        try:
            self.httpd.server_close()
        except Exception:
            pass


class MegafonClientTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = FakeMegaFonVatsServer()

    @classmethod
    def tearDownClass(cls):
        cls.srv.stop()

    def setUp(self):
        del self.srv.requests[:]
        self.c = MegafonVatsClient(self.srv.url, "test-key", timeout_sec=5)

    def test_users_envelope(self):
        env = self.c.get_users()
        self.assertEqual(env["info"]["total"], 5)
        self.assertEqual(len(env["items"]), 5)
        self.assertEqual(env["items"][0]["login"], "admin")

    def test_users_search_and_paging(self):
        env = self.c.get_users(search="админ", start=0, limit=10)
        self.assertEqual(env["info"]["total"], 1)
        last = self.srv.requests[-1]
        self.assertEqual(last["query"].get("search"), ["админ"])

    def test_users_all_paginates(self):
        all_users = self.c._fetch_all("/users", limit=2)
        self.assertEqual(len(all_users), 5)
        self.assertEqual([u["login"] for u in all_users],
                         ["admin", "manager", "ivan", "u704", "u705"])

    def test_auth_401_no_key_material(self):
        bad = MegafonVatsClient(self.srv.url, "WRONG-SECRET-KEY", timeout_sec=5)
        with self.assertRaises(MegafonApiError) as ctx:
            bad.get_users()
        self.assertEqual(ctx.exception.status, 401)
        self.assertNotIn("WRONG-SECRET-KEY", str(ctx.exception))
        self.assertNotIn("WRONG-SECRET-KEY", ctx.exception.payload)

    def test_get_user_and_ext_login(self):
        u = self.c.get_user("admin", with_status=True)
        self.assertEqual(u["ext"], "701")
        u2 = self.c.get_user("701")
        self.assertEqual(u2["login"], "admin")

    def test_user_groups(self):
        g = self.c.get_user_groups("admin")
        self.assertEqual(g[0]["id"], "sales")

    def test_subscription_semantics(self):
        self.assertTrue(self.c.get_subscription("admin", "sales"))
        self.c.set_subscription("admin", True, "sales")
        self.assertEqual(self.srv.requests[-1]["method"], "POST")  # вкл = POST
        self.c.set_subscription("admin", False)
        self.assertEqual(self.srv.requests[-1]["method"], "DELETE")

    def test_dnd_inverted_semantics(self):
        self.assertTrue(self.c.get_dnd("admin"))  # True = принимает звонки
        self.c.set_dnd("admin", True)
        self.assertEqual(self.srv.requests[-1]["method"], "POST")
        self.c.set_dnd("admin", False)
        self.assertEqual(self.srv.requests[-1]["method"], "DELETE")

    def test_groups(self):
        env = self.c.get_groups()
        self.assertEqual(env["items"][0]["id"], "sales")
        g = self.c.get_group("sales")
        self.assertEqual(g["call_order"], "BYORDER")

    def test_telnums(self):
        env = self.c.get_telnums()
        self.assertEqual(env["items"][0]["telnum"], "79264010121")
        t = self.c.get_telnum("+7 (926) 401-01-21")
        self.assertEqual(t["telnum"], "79264010121")

    def test_sims(self):
        env = self.c.get_sims()
        self.assertEqual(env["items"][0]["telnum"], "79262005060")

    def test_caller_ids_and_allowed(self):
        s = self.c.get_caller_ids()
        self.assertEqual(s["main"], "79262005060")
        # только enabled==true — fail-closed против спуфинга (§23 ТЗ)
        self.assertEqual(self.c.allowed_caller_ids(), ["79262005060"])

    def test_makecall_ok(self):
        res = self.c.make_call("+7 (495) 200-50-60", user="admin", clid="79262005060")
        self.assertEqual(res["callid"], "2015948553")
        self.assertEqual(res["clid"], "79262005060")
        last = self.srv.requests[-1]
        self.assertEqual(last["method"], "POST")
        self.assertEqual(last["body"]["phone"], "74952005060")
        self.assertEqual(last["body"]["user"], "admin")

    def test_makecall_group_and_show_phone(self):
        self.c.make_call("74952005060", group="sales", show_phone=True)
        last = self.srv.requests[-1]
        self.assertEqual(last["body"]["group"], "sales")
        self.assertTrue(last["body"]["show_phone"])
        self.assertNotIn("user", last["body"])

    def test_makecall_no_callid_raises(self):
        with self.assertRaises(MegafonApiError):
            self.c.make_call("71111111111", user="admin")

    def test_makecall_400_raises(self):
        with self.assertRaises(MegafonApiError) as ctx:
            self.c.make_call("70000000000", user="admin")
        self.assertEqual(ctx.exception.status, 400)

    def test_makecall_empty_phone_raises_locally(self):
        with self.assertRaises(MegafonApiError):
            self.c.make_call("нет номера", user="admin")

    def test_history_json_passthrough(self):
        rows = self.c.get_history_json(period="today", type="all", limit=100)
        self.assertEqual(rows[0]["uid"], "1755936870")
        last = self.srv.requests[-1]
        self.assertEqual(last["query"].get("period"), ["today"])
        self.assertEqual(last["query"].get("limit"), ["100"])

    def test_history_csv_and_parse(self):
        text = self.c.get_history_csv(period="today")
        rows = MegafonVatsClient.parse_history_csv(text)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["uid"], "1755936870")
        self.assertEqual(rows[0]["via"], "79262005060")
        self.assertEqual(rows[0]["duration"], "23")
        self.assertEqual(rows[0]["rating"], "4")

    def test_history_inner(self):
        rows = self.c.get_history_inner_json(period="today")
        self.assertEqual(rows[0]["from"], "admin")
        text = self.c.get_history_inner_csv(period="today")
        self.assertIn("3934307521", text)

    def test_record_and_domain(self):
        self.assertTrue(self.c.get_record_settings()["external"])
        self.assertTrue(self.c.get_domain()["services"]["record"])

    def test_connection_check(self):
        res = self.c.connection_check()
        self.assertIn("users", res)
        self.assertIn("telnums", res)
        self.assertIn("caller_ids", res)

    def test_timeout_raises(self):
        slow = MegafonVatsClient(self.srv.url, "test-key", timeout_sec=1)
        with self.assertRaises(MegafonApiError):
            slow._get("/users", {"mode": "slow"})

    def test_base_url_with_prefix_tolerated(self):
        c = MegafonVatsClient(self.srv.url + "/crmapi/v1/", "test-key")
        self.assertEqual(c.get_users()["info"]["total"], 5)


class MegafonProviderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = FakeMegaFonVatsServer()

    @classmethod
    def tearDownClass(cls):
        cls.srv.stop()

    def setUp(self):
        del self.srv.requests[:]

    def _cfg(self, **kw):
        s = {"megafon_vats": {"base_url": self.srv.url, "api_key": "test-key",
                              "default_user": "admin", "timeout_sec": 5}}
        s["megafon_vats"].update(kw)
        return s

    def test_configure_fail_closed(self):
        with self.assertRaises(ProviderNotConfigured):
            MegafonVatsProvider().configure({"megafon_vats": {"api_key": "x"}})
        with self.assertRaises(ProviderNotConfigured):
            MegafonVatsProvider().configure({"megafon_vats": {"base_url": self.srv.url}})

    def test_configure_env_key(self):
        os.environ["ATS_MEGAFON_API_KEY"] = "test-key"
        try:
            p = MegafonVatsProvider().configure(
                {"megafon_vats": {"base_url": self.srv.url, "api_key_env": "ATS_MEGAFON_API_KEY"}})
            self.assertEqual(p.name, "megafon_vats")
        finally:
            del os.environ["ATS_MEGAFON_API_KEY"]

    def test_resolve_secret(self):
        self.assertEqual(resolve_secret({"api_key": "lit"}, "api_key", "api_key_env"), "lit")
        os.environ["ATS_TMP_K"] = "from-env"
        try:
            self.assertEqual(resolve_secret({"api_key_env": "ATS_TMP_K"}, "api_key", "api_key_env"),
                             "from-env")
            self.assertEqual(resolve_secret({}, "api_key", "api_key_env"), "")
        finally:
            del os.environ["ATS_TMP_K"]

    def test_dial_ok_stores_external(self):
        p = MegafonVatsProvider().configure(self._cfg())
        self.assertTrue(p.dial({"call_id": 42, "phone": "+7(495)200-50-60",
                                "caller_id": "79262005060"}))
        self.assertEqual(p.external_id(42),
                         {"callid": "2015948553", "clid": "79262005060"})

    def test_dial_requires_user_or_group(self):
        p = MegafonVatsProvider().configure(self._cfg(default_user=""))
        with self.assertRaises(ProviderNotConfigured):
            p.dial({"call_id": 1, "phone": "74952005060"})
        p2 = MegafonVatsProvider().configure(self._cfg(default_user="", default_group="sales"))
        self.assertTrue(p2.dial({"call_id": 2, "phone": "74952005060"}))

    def test_dial_clid_mismatch_warns_and_keeps_fact(self):
        p = MegafonVatsProvider().configure(self._cfg())
        buf = io.StringIO()
        with redirect_stdout(buf):
            # 79264010121 запрещён для исходящей — ВАТС вернёт ответ без clid
            self.assertTrue(p.dial({"call_id": 7, "phone": "74952005060",
                                    "caller_id": "79264010121"}))
        self.assertIn("79264010121", buf.getvalue())  # громкое предупреждение
        self.assertEqual(p.external_id(7)["callid"], "2015948553")

    def test_dial_api_error_loud(self):
        p = MegafonVatsProvider().configure(self._cfg())
        with self.assertRaises(ProviderNotConfigured):
            p.dial({"call_id": 9, "phone": "70000000000"})

    def test_no_interactive_channel(self):
        p = MegafonVatsProvider().configure(self._cfg())
        self.assertFalse(p.interactive)
        self.assertIsNone(p.make_channel(1, "74952005060"))
        p.hangup(1)  # no-op, не падает
        p.stop()

    def test_factory(self):
        p = make_provider({"provider": "megafon_vats", "megafon_vats": {
            "base_url": self.srv.url, "api_key": "test-key"}})
        self.assertEqual(p.name, "megafon_vats")
        with self.assertRaises(ProviderNotConfigured):
            make_provider({"provider": "megafon_vats", "megafon_vats": {}})


class MegafonConfigTest(unittest.TestCase):
    def test_number_providers(self):
        self.assertIn("megafon_vats", numbers_mod.NUMBER_PROVIDERS)

    def test_default_settings_block(self):
        self.assertIn("megafon_vats", config.DEFAULT_SETTINGS)
        blk = config.DEFAULT_SETTINGS["megafon_vats"]
        self.assertEqual(blk["api_key_env"], "ATS_MEGAFON_API_KEY")
        self.assertEqual(blk["crm_token_env"], "ATS_MEGAFON_CRM_TOKEN")

    def _admin(self):
        return {"X-Ats-Token": security.create_token("admin", "admin")}

    def test_settings_raw_masks_and_preserves(self):
        s = db.get_settings()
        s["megafon_vats"] = {"base_url": "https://x", "api_key": "SECRET-KEY",
                             "api_key_env": "ATS_MEGAFON_API_KEY",
                             "crm_token": "CRM-SECRET", "default_user": "admin"}
        db.save_settings(s)
        payload, code = api.route("GET", "/api/v2/settings/raw", {}, self._admin())
        self.assertEqual(code, 200)
        blk = payload["provider_config"]["megafon_vats"]
        self.assertEqual(blk["api_key"], "********")
        self.assertEqual(blk["crm_token"], "********")
        self.assertEqual(blk["api_key_env"], "ATS_MEGAFON_API_KEY")  # имя env — не секрет
        # POST с плейсхолдером секрет не затирает
        payload, code = api.route("POST", "/api/v2/settings/raw",
                                  {"provider_config": {"megafon_vats": {
                                      "api_key": "********", "crm_token": "********",
                                      "default_user": "manager"}}}, self._admin())
        self.assertEqual(code, 200)
        s2 = db.get_settings()["megafon_vats"]
        self.assertEqual(s2["api_key"], "SECRET-KEY")
        self.assertEqual(s2["crm_token"], "CRM-SECRET")
        self.assertEqual(s2["default_user"], "manager")

    def test_settings_save_accepts_provider(self):
        payload, code = api.route("POST", "/api/v2/settings/save",
                                  {"provider": "megafon_vats"}, self._admin())
        self.assertEqual(code, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(db.get_settings()["provider"], "megafon_vats")


if __name__ == "__main__":
    unittest.main()
