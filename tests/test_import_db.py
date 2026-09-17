# -*- coding: utf-8 -*-
"""Тесты баз данных: импорт CSV/XLSX, теги, история номера, API баз.

Запуск: python -m unittest discover -s tests -v   (из корня репозитория)
"""
import base64
import io
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import zipfile

_TMP = tempfile.mkdtemp(prefix="ats_test_import_")
os.environ["ATS_FAST"] = "1"
os.environ["ATS_DATA_DIR"] = _TMP
os.environ["ATS_ADMIN_PASSWORD"] = "TestAdmin123!"
os.environ["ATS_DEV_SEED"] = "1"

from app import api, config, db, security  # noqa: E402
from app import importers  # noqa: E402
from app.engine import Engine  # noqa: E402

# Fail-closed провайдера: чистой БД симулятор не подставляется, поэтому тесты
# явно выбирают sim (модули делят одну тестовую БД; сид идемпотентен).
db.init_db()
_test_seed = db.get_settings()
if not _test_seed.get("provider"):
    _test_seed["provider"] = "sim"
    db.save_settings(_test_seed)


def make_xlsx(rows):
    """Минимальный .xlsx в памяти (sharedStrings + sheet1)."""
    ss_vals = []

    def esc(s):
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def cell(r, c, v):
        col = chr(65 + c)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return '<c r="%s%d"><v>%s</v></c>' % (col, r, v)
        if v not in ss_vals:
            ss_vals.append(v)
        return '<c r="%s%d" t="s"><v>%d</v></c>' % (col, r, ss_vals.index(v))

    sheet = "".join('<row r="%d">%s</row>' % (i + 1, "".join(
        cell(i + 1, j, v) for j, v in enumerate(r))) for i, r in enumerate(rows))
    si = "".join("<si><t>%s</t></si>" % esc(v) for v in ss_vals)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("xl/sharedStrings.xml",
                   '<?xml version="1.0"?><sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">%s</sst>' % si)
        z.writestr("xl/worksheets/sheet1.xml",
                   '<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                   '<sheetData>%s</sheetData></worksheet>' % sheet)
    return buf.getvalue()


class TestNormalize(unittest.TestCase):
    def test_normalize_phone(self):
        self.assertEqual(importers.normalize_phone("8(925)000-11-22"), "79250001122")
        self.assertEqual(importers.normalize_phone("+7 925 000 11 22"), "79250001122")
        self.assertEqual(importers.normalize_phone("9250001122"), "79250001122")
        self.assertEqual(importers.normalize_phone("79250001122"), "79250001122")
        self.assertEqual(importers.normalize_phone("380501234567"), "380501234567")
        self.assertEqual(importers.normalize_phone(""), "")
        self.assertTrue(importers.phone_valid("79250001122"))
        self.assertFalse(importers.phone_valid("123"))
        self.assertFalse(importers.phone_valid(""))

    def test_clean_tags(self):
        self.assertEqual(importers.clean_tags("vip, москва;vip|холодный"), "vip, москва, холодный")
        self.assertEqual(importers.clean_tags("  "), "")
        self.assertEqual(importers.clean_tags(["a", "b", "a"]), "a, b")

    def test_map_headers_ru_en(self):
        m = importers.map_headers(["ФИО", "Телефон", "Теги", "Согласие"])
        self.assertEqual(m, {"name": 0, "phone": 1, "tags": 2, "consent": 3})
        m = importers.map_headers(["name", "phone", "group", "note"])
        self.assertEqual(m, {"name": 0, "phone": 1, "group": 2, "note": 3})


class TestParsers(unittest.TestCase):
    def test_csv_cp1251_semicolon(self):
        data = "Имя;Телефон;Согласие\nИван Петров;89250001122;да\n".encode("cp1251")
        h, rows = importers.parse_csv(data)
        self.assertEqual(h, ["Имя", "Телефон", "Согласие"])
        self.assertEqual(rows, [["Иван Петров", "89250001122", "да"]])

    def test_csv_utf8_comma(self):
        h, rows = importers.parse_csv("name,phone\nBob,79250001133\n".encode("utf-8"))
        self.assertEqual(h, ["name", "phone"])
        self.assertEqual(rows, [["Bob", "79250001133"]])

    def test_xlsx_mixed_cells(self):
        x = make_xlsx([["Phone", "Name", "Tags"],
                       ["79250001144", "Сидоров", "vip"],
                       [79250001155, "Числом", ""]])
        h, rows = importers.parse_xlsx(x)
        self.assertEqual(h, ["Phone", "Name", "Tags"])
        self.assertEqual(rows[0], ["79250001144", "Сидоров", "vip"])
        self.assertEqual(rows[1][0], "79250001155")  # число без .0

    def test_no_header_phone_list(self):
        recs, info = importers.rows_to_records(["79250001166"], [["79250001167"]])
        self.assertTrue(info["no_header"])
        self.assertEqual(len(recs), 2)
        self.assertEqual(recs[0]["phone"], "79250001166")

    def test_missing_phone_column(self):
        with self.assertRaises(ValueError):
            importers.rows_to_records(["Имя", "Город"], [["Боб", "МСК"]])

    def test_old_xls_rejected(self):
        with self.assertRaises(ValueError):
            importers.import_file("base.xls", b"junk", database_id=0)


class TestImportApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.init_db()
        # тестовый оператор для проверки ролей
        if not db.fetch1("SELECT id FROM users WHERE login='operator'"):
            salt = security.new_salt()
            db.insert("users", {"login": "operator", "role": "operator", "salt": salt,
                                "password_hash": security.hash_password("operator1234", salt),
                                "active": 1, "created": config.now_iso()})
        from app.server import create_server
        cls.engine = Engine()
        api.ENGINE = cls.engine
        cls.httpd = create_server("127.0.0.1", 0)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        time.sleep(0.2)
        cls.base = "http://127.0.0.1:{}".format(cls.port)

    @classmethod
    def tearDownClass(cls):
        cls.engine.stop()
        cls.httpd.shutdown()

    def call(self, method, path, body=None, token=""):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method)
        if body is not None:
            req.add_header("Content-Type", "application/json")
        if token:
            req.add_header("X-Ats-Token", token)
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return r.status, json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read().decode())
            except Exception:
                return e.code, {}

    def _token(self, login="admin", password="TestAdmin123!"):
        s, j = self.call("POST", "/api/v2/auth/login", {"login": login, "password": password})
        self.assertEqual(s, 200)
        return j["token"]

    def test_1_import_csv_creates_database(self):
        tok = self._token()
        csv_data = ("Имя;Телефон;Теги;Согласие\n"
                    "База Один;8(951)000-00-01;vip, мск;да\n"
                    "База Два;79510000002;холодный;\n"
                    "БезНомера;;холодный;\n").encode("cp1251")
        s, j = self.call("POST", "/api/v2/contacts/import-file",
                         {"filename": "msk.csv", "content_b64": base64.b64encode(csv_data).decode(),
                          "database_name": "ИмпортТест-МСК"}, token=tok)
        self.assertEqual(s, 200)
        self.assertEqual((j["added"], j["updated"], j["skipped"]), (2, 0, 1))
        self.assertIn("phone", j["mapping"])
        did = j["database_id"]
        self.assertTrue(did)
        c1 = db.fetch1("SELECT * FROM contacts WHERE phone='79510000001'")
        self.assertEqual(c1["name"], "База Один")
        self.assertEqual(c1["tags"], "vip, мск")
        self.assertEqual(c1["database_id"], did)
        self.assertEqual(c1["consent"], 1)

    def test_2_import_xlsx_and_merge_tags(self):
        tok = self._token()
        x = make_xlsx([["Phone", "Name", "Tags"],
                       ["79510000001", "База Один", "повторный"],
                       ["79510000003", "Новичок", ""]])
        s, j = self.call("POST", "/api/v2/contacts/import-file",
                         {"filename": "b2.xlsx", "content_b64": base64.b64encode(x).decode(),
                          "database_name": "ИмпортТест-XLSX"}, token=tok)
        self.assertEqual(s, 200)
        self.assertEqual((j["added"], j["updated"]), (1, 1))
        c1 = db.fetch1("SELECT * FROM contacts WHERE phone='79510000001'")
        # теги объединились, контакт переехал в новую базу
        self.assertEqual(c1["tags"], "vip, мск, повторный")
        self.assertEqual(c1["database_id"], j["database_id"])

    def test_3_databases_list_and_history(self):
        tok = self._token()
        s, j = self.call("GET", "/api/v2/databases", token=tok)
        self.assertEqual(s, 200)
        names = [d["name"] for d in j["databases"]]
        self.assertIn("ИмпортТест-МСК", names)
        d = [x for x in j["databases"] if x["name"] == "ИмпортТест-МСК"][0]
        self.assertIn("contacts_count", d)
        self.assertIn("vip", d["tags"] + [t for x in j["databases"] for t in x.get("tags", [])])
        c = db.fetch1("SELECT id FROM contacts WHERE phone='79510000002'")
        s, h = self.call("GET", "/api/v2/contacts/history?id=%d" % c["id"], token=tok)
        self.assertEqual(s, 200)
        self.assertEqual(h["contact"]["phone"], "79510000002")
        self.assertIn("stats", h)
        self.assertIn("calls", h)
        # оператор тоже видит базы и карточку
        tok_op = self._token("operator", "operator1234")
        self.assertEqual(self.call("GET", "/api/v2/databases", token=tok_op)[0], 200)
        self.assertEqual(self.call("GET", "/api/v2/contacts/history?id=%d" % c["id"],
                                   token=tok_op)[0], 200)

    def test_4_roles_and_parse_errors(self):
        tok_op = self._token("operator", "operator1234")
        s, _ = self.call("POST", "/api/v2/contacts/import-file",
                         {"filename": "x.csv", "content_b64": "eA=="}, token=tok_op)
        self.assertEqual(s, 403)
        tok = self._token()
        s, j = self.call("POST", "/api/v2/contacts/import-file",
                         {"filename": "bad.xlsx",
                          "content_b64": base64.b64encode(b"not-a-zip").decode()},
                         token=tok)
        self.assertEqual(s, 400)
        self.assertEqual(j["error"], "parse_error")

    def test_5_campaign_add_by_database_and_delete(self):
        tok = self._token()
        cid = db.insert("campaigns", {"name": "ИмпортТест-кампания", "template_id": 1,
                                      "flow": "message", "status": "stopped",
                                      "schedule": json.dumps({"start": "00:00", "end": "23:59",
                                                              "days": [0, 1, 2, 3, 4, 5, 6]}),
                                      "max_channels": 3, "retry_max": 0, "retry_delay_min": 1,
                                      "connect_on_qualify": 0,
                                      "created": config.now_iso(), "updated": config.now_iso()})
        d = db.fetch1("SELECT id FROM databases WHERE name='ИмпортТест-МСК'")
        s, j = self.call("POST", "/api/v2/campaigns/%d/add-contacts" % cid,
                         {"database_id": d["id"], "consent_only": False}, token=tok)
        self.assertEqual(s, 200)
        self.assertGreaterEqual(j["added"], 1)
        # удаление базы с откреплением
        s, _ = self.call("POST", "/api/v2/databases/delete", {"ids": [d["id"]]}, token=tok)
        self.assertEqual(s, 200)
        self.assertIsNone(db.fetch1("SELECT id FROM databases WHERE id=?", (d["id"],)))
        left = db.fetch("SELECT database_id FROM contacts WHERE phone LIKE '7951000000%'")
        self.assertTrue(all(r["database_id"] != d["id"] for r in left))


if __name__ == "__main__":
    unittest.main()
