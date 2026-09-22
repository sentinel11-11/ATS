# -*- coding: utf-8 -*-
import json
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
import threading

from app.providers.multicom import MulticomClient, MulticomProvider, MulticomApiError


class DummyMulticomHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        if self.path == "/v1/account" or self.path == "/v1/status":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True, "account": "test_multicom"}).encode())
        elif self.path == "/v1/numbers":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps([{"number": "79001112233"}]).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path == "/v1/calls/make":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True, "call_id": "mc-12345"}).encode())
        else:
            self.send_response(400)
            self.end_headers()


class MulticomTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), DummyMulticomHandler)
        cls.port = cls.server.server_port
        cls.thread = threading.Thread(target=cls.server.serve_forever)
        cls.thread.daemon = True
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def test_client_and_provider(self):
        url = f"http://127.0.0.1:{self.port}/v1"
        client = MulticomClient(api_url=url, api_key="test-key")
        info = client.get_account_info()
        self.assertTrue(info.get("ok"))

        numbers = client.get_numbers()
        self.assertEqual(len(numbers), 1)
        self.assertEqual(numbers[0]["number"], "79001112233")

        call = client.make_call(phone="79998887766")
        self.assertEqual(call.get("call_id"), "mc-12345")

        provider = MulticomProvider().configure({
            "multicom": {
                "api_url": url,
                "api_key": "test-key"
            }
        })
        dial_res = provider.dial(phone="79998887766")
        self.assertTrue(dial_res.get("ok"))
        self.assertEqual(dial_res.get("call_id"), "mc-12345")


if __name__ == "__main__":
    unittest.main()
