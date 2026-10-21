# -*- coding: utf-8 -*-
"""Один HTTP-ответ на один GET-запрос (P1 повторного ревью).

Раньше Handler.do_GET() после отдачи статики отправлял ВТОРОЙ ответ
(404 JSON) в то же соединение — нарушение фрейминга HTTP/1.1: на
keep-alive соединении следующий запрос читал мусор от предыдущего.

Проверяем по сырому сокету (so близко к реальности, как позволяет тест):
- GET / и GET /ui/index.html отдают ровно один статус + тело = файл;
- два последовательных запроса по ОДНОМУ keep-alive соединению получают
  по одному корректному ответу каждый;
- неизвестный путь — SPA-fallback (index.html), тоже ровно один ответ;
- в теле первого ответа нет «приклеенного» JSON от второго.
"""
import os
import socket
import tempfile
import threading
import time
import unittest

_TMP = tempfile.mkdtemp(prefix="ats_test_http1_")
os.environ["ATS_FAST"] = "1"
os.environ["ATS_DATA_DIR"] = _TMP
os.environ["ATS_ADMIN_PASSWORD"] = "TestAdmin123!"
os.environ["ATS_DEV_SEED"] = "1"

from app import db  # noqa: E402
from app.server import STATIC_DIR  # noqa: E402

db.init_db()
_seed = db.get_settings()
if not _seed.get("provider"):
    _seed["provider"] = "sim"
    db.save_settings(_seed)


def _read_one_response(sock):
    """Прочитать ровно один HTTP-ответ (по Content-Length)."""
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = sock.recv(65536)
        if not chunk:
            raise AssertionError("соединение закрыто до конца заголовков: %r" % buf[:200])
        buf += chunk
    head, _, rest = buf.partition(b"\r\n\r\n")
    clen = 0
    for line in head.split(b"\r\n"):
        if line.lower().startswith(b"content-length:"):
            clen = int(line.split(b":", 1)[1].strip())
    body = rest
    while len(body) < clen:
        chunk = sock.recv(65536)
        if not chunk:
            raise AssertionError("соединение закрыто до конца тела")
        body += chunk
    return head, body


class SingleResponsePerGETTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from app.server import create_server
        cls.httpd = create_server("127.0.0.1", 0)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        time.sleep(0.2)

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def _sock(self):
        s = socket.create_connection(("127.0.0.1", self.port), timeout=10)
        s.settimeout(10)
        return s

    def test_root_is_single_html_response(self):
        with self._sock() as s:
            s.sendall(b"GET / HTTP/1.1\r\nHost: t\r\nConnection: keep-alive\r\n\r\n")
            head, body = _read_one_response(s)
            self.assertIn(b"200", head.split(b"\r\n")[0])
            self.assertIn(b"text/html", head.lower())
            expected = (STATIC_DIR / "index.html").read_bytes()
            self.assertEqual(body, expected,
                             "тело должно быть ровно index.html без приклеенного мусора")

    def test_two_requests_over_one_keepalive_connection(self):
        """Ядро регрессии: второй запрос не должен читать мусор первого."""
        with self._sock() as s:
            for path in (b"/", b"/ui/index.html"):
                s.sendall(b"GET " + path +
                          b" HTTP/1.1\r\nHost: t\r\nConnection: keep-alive\r\n\r\n")
                head, body = _read_one_response(s)
                status = head.split(b"\r\n")[0]
                self.assertIn(b"200", status, "%s: %r" % (path, status))
                expected = (STATIC_DIR / "index.html").read_bytes()
                self.assertEqual(body, expected)

    def test_unknown_path_is_spa_fallback_single_response(self):
        with self._sock() as s:
            s.sendall(b"GET /some/spa/route HTTP/1.1\r\nHost: t\r\n\r\n")
            head, body = _read_one_response(s)
            self.assertIn(b"200", head.split(b"\r\n")[0])
            expected = (STATIC_DIR / "index.html").read_bytes()
            self.assertEqual(body, expected, "SPA-fallback отдаёт index.html")
            # и вторым запросом по тому же соединению — снова чистый ответ
            s.sendall(b"GET / HTTP/1.1\r\nHost: t\r\n\r\n")
            head2, body2 = _read_one_response(s)
            self.assertIn(b"200", head2.split(b"\r\n")[0])
            self.assertEqual(body2, expected)

    def test_no_second_response_after_static(self):
        """После первого ответа в соединении не должно быть данных,
        пока клиент не отправил следующий запрос."""
        with self._sock() as s:
            s.sendall(b"GET / HTTP/1.1\r\nHost: t\r\nConnection: keep-alive\r\n\r\n")
            _head, body = _read_one_response(s)
            self.assertNotIn(b'"error":"not_found"', body)
            self.assertNotIn(b"not_found", body)
            s.settimeout(0.6)
            try:
                extra = s.recv(4096)
            except (socket.timeout, TimeoutError):
                extra = b""
            self.assertEqual(extra, b"",
                             "сервер отправил второй ответ без запроса: %r" % extra[:200])


if __name__ == "__main__":
    unittest.main()
