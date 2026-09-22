# -*- coding: utf-8 -*-
import json
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
import threading

from app.crm import AmoCrm, make_crm
from app.providers.amocrm import AmoCrmClient, AmoCrmApiError, _sanitize_phone


class DummyAmoHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        if "/api/v4/account" in self.path:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"id": 12345, "name": "Test Company"}).encode())
        elif "/api/v4/users" in self.path:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"_embedded": {"users": [{"id": 1, "name": "Manager 1"}]}}).encode())
        elif "/api/v4/contacts" in self.path:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"_embedded": {"contacts": [{"id": 777, "name": "Ivan", "responsible_user_id": 1}]}}).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"_embedded": {"items": [{"id": 999}]}}).encode())


class AmoCrmTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), DummyAmoHandler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def test_phone_sanitization(self):
        self.assertEqual(_sanitize_phone("89261234567"), "+79261234567")
        self.assertEqual(_sanitize_phone("+7 (926) 123-45-67"), "+79261234567")

    def test_factory_and_amocrm(self):
        crm = make_crm({"crm": {"driver": "amocrm"}, "amocrm": {"subdomain": "test", "access_token": "token123"}})
        self.assertIsInstance(crm, AmoCrm)
        self.assertEqual(crm.name, "amocrm")

    def test_client_methods(self):
        client = AmoCrmClient(subdomain="test", access_token="token123")
        client.base_url = f"http://127.0.0.1:{self.port}/api/v4"

        info = client.get_account_info()
        self.assertEqual(info.get("id"), 12345)

        users = client.get_users()
        self.assertEqual(len(users), 1)

        contacts = client.search_contacts("+79261234567")
        self.assertEqual(len(contacts), 1)
        self.assertEqual(contacts[0]["id"], 777)

        res = client.register_call("+79261234567", "outbound", 30, 1, "Разговор успешно завершен")
        self.assertTrue(isinstance(res, dict))

    def test_push_result_flow(self):
        settings = {
            "crm": {"driver": "amocrm"},
            "amocrm": {
                "subdomain": "test",
                "access_token": "token123",
                "responsible_user_id": 1,
                "auto_create_contacts": True,
                "auto_create_tasks": True
            }
        }
        crm = AmoCrm(settings)
        # Подменяем base_url клиента на тестовый сервер
        orig_client = crm._client
        def mock_client():
            c = orig_client()
            c.base_url = f"http://127.0.0.1:{self.port}/api/v4"
            return c
        crm._client = mock_client

        call_data = {
            "id": 101,
            "contact_phone": "89261234567",
            "contact_name": "Тестовый Клиент",
            "status": "operator_ok",
            "direction": "out",
            "duration_sec": 45,
            "result": "Успешная презентация",
            "record_url": "http://ats/rec/101.mp3"
        }
        res = crm.push_result(call_data)
        self.assertIsNotNone(res)


if __name__ == "__main__":
    unittest.main()
