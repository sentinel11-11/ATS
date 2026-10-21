# -*- coding: utf-8 -*-
"""HTTP-сервер ATS v2: статика (app/ui), REST API v2, SSE для событий."""
import json
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from urllib.parse import parse_qs, urlparse

from . import api, config, db, events
from .api import json_bytes

# учёт SSE-соединений по пользователю (лимит MAX_SSE_PER_USER)
_SSE_USERS = {}
_SSE_USERS_LOCK = threading.Lock()


def _sse_user_inc(login):
    with _SSE_USERS_LOCK:
        n = _SSE_USERS.get(login, 0) + 1
        _SSE_USERS[login] = n
        return n


def _sse_user_dec(login):
    with _SSE_USERS_LOCK:
        n = _SSE_USERS.get(login, 0) - 1
        if n > 0:
            _SSE_USERS[login] = n
        else:
            _SSE_USERS.pop(login, None)

STATIC_DIR = config.APP / "ui"
# Раздельные лимиты тела (C4, P1-аудита): обычный JSON — небольшой; тяжёлые
# загрузки (базы контактов в base64, записи разговоров) — отдельные маршруты.
# Превышение → 413 + закрытие соединения (тело не читаем в память).
MAX_BODY_JSON = 2 * 1024 * 1024       # 2 MiB на обычные JSON-маршруты
MAX_BODY_UPLOAD = 64 * 1024 * 1024    # 64 MiB только на импорт/загрузку аудио
UPLOAD_PATH_SUFFIXES = ("/contacts/import-file", "/calls/recording")

# Потолок одновременных SSE-подписчиков и на одного пользователя: без него
# каждый открытый таб держит поток, и сотни соединений исчерпают потоки/память.
MAX_SSE_CLIENTS = 200
MAX_SSE_PER_USER = 10


class BodyTooLarge(Exception):
    """Тело запроса превышает лимит для этого маршрута."""


def _body_limit_for(path):
    p = path.rstrip("/")
    for suf in UPLOAD_PATH_SUFFIXES:
        if p.endswith(suf):
            return MAX_BODY_UPLOAD
    return MAX_BODY_JSON


def auth_ok(headers, query_ticket=""):
    """URL-доступ (SSE, записи): заголовок X-Ats-Token или одноразовый ?ticket=.

    Долгоживущий токен сессии в query string не принимается (P1-аудита:
    URL попадает в access-логи прокси и историю браузера). Полная проверка
    сессии/активности пользователя — api.need_auth.
    """
    from . import api
    sess, err, code = api.need_auth(headers, query_ticket=query_ticket)
    if err:
        return False
    return True


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "ATSv2"
    # таймаут на чтение запроса от клиента (медленные/зависшие body не держат
    # поток вечно; SSE-запись им не ограничена — там свой keepalive-ритм)
    timeout = 75

    def log_message(self, fmt, *args):
        return  # тихий режим

    def end_headers(self):
        """Security-заголовки на каждый ответ (включая ошибки и статикой)."""
        try:
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            # UI — единый origin: свои скрипты/стили/шрифты, fetch и EventSource
            # на тот же origin; инлайн-стили React (style={{...}}) разрешены,
            # инлайн-скрипты — нет. CSP API/JSON-ответам не мешает.
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; script-src 'self'; "
                             "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
                             "connect-src 'self'; media-src 'self'; font-src 'self'; "
                             "frame-ancestors 'none'; base-uri 'self'; form-action 'self'")
        except Exception:
            pass
        super().end_headers()

    # ---------- helpers ----------
    def _api_headers(self):
        """Копия заголовков запроса с адресом клиента для аудита."""
        # Keep the Message object: its lookup is case-insensitive, which is
        # required for provider headers such as X-UIS-Secret.
        self.headers["X-Ats-Client-IP"] = self.client_address[0]
        return self.headers

    def _send_bytes(self, data, ctype, status=200, extra=None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(data)
        except Exception:
            pass

    def _send_json(self, obj, status=200):
        self._send_bytes(json_bytes(obj), "application/json; charset=utf-8", status)

    def _body(self, limit=None):
        limit = limit or MAX_BODY_JSON
        try:
            n = int(self.headers.get("Content-Length", "0") or "0")
        except Exception:
            n = 0
        if n <= 0:
            return {}
        if n > limit:
            raise BodyTooLarge(n)
        raw = self.rfile.read(n)
        ctype = str(self.headers.get("Content-Type", "") or "").lower()
        if "application/x-www-form-urlencoded" in ctype:
            # Вебхуки ВАТС (МегаФон и др.): form, а не JSON. Значения — первые.
            try:
                from urllib.parse import parse_qsl
                return {k: v for k, v in parse_qsl(raw.decode("utf-8", "ignore"),
                                                  keep_blank_values=True)}
            except Exception:
                return {}
        try:
            return json.loads(raw.decode("utf-8", "ignore"))
        except Exception:
            return {}

    def _static(self, rel):
        # защита от path traversal: файл обязан лежать внутри STATIC_DIR.
        # Возвращает True, если отдан реальный файл, False — SPA-fallback
        # (index.html). В любом случае отправляется РОВНО один ответ.
        root = STATIC_DIR.resolve()
        try:
            path = (root / rel).resolve()
            path.relative_to(root)
        except Exception:
            path = root / "index.html"
        found = True
        if not path.exists() or path.is_dir():
            path = root / "index.html"
            found = False
        data = path.read_bytes()
        ctype = {".html": "text/html; charset=utf-8", ".js": "application/javascript; charset=utf-8",
                 ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml",
                 ".png": "image/png", ".ico": "image/x-icon"}.get(path.suffix.lower(),
                                                                  "application/octet-stream")
        self._send_bytes(data, ctype)
        return found

    # ---------- GET ----------
    def do_GET(self):
        u = urlparse(self.path)
        # запись разговора: файл + content-type по расширению (auth: заголовок или ?token=)
        import re as _re
        mrec = _re.match(r"^(?:/api/v2|/v2)?/calls/(\d+)/recording$", u.path)
        if mrec:
            return self._recording(int(mrec.group(1)), parse_qs(u.query))
        # запись по подписанной ссылке (для amoCRM/почты): без токена сессии,
        # но с HMAC-подписью и сроком действия — см. app/records.py
        msig = _re.match(r"^(?:/api/v2|/v2)?/records/(\d+)$", u.path)
        if msig:
            return self._recording_signed(int(msig.group(1)), parse_qs(u.query))
        if u.path in ("/api/v2/events", "/v2/events", "/events"):
            return self._sse()

        if u.path.startswith("/ui/"):
            self._static(u.path[len("/ui/"):])
            return

        if u.path.startswith("/api/") or u.path.startswith("/v2/") or u.path.startswith("/export/"):
            payload, status = api.route("GET", self.path, {}, self._api_headers())
            if payload is None:
                payload, status = {"ok": False, "error": "not_found"}, 404
            if isinstance(payload, bytes):
                fname = u.path.rstrip("/").split("/")[-1] or "download.csv"
                if not fname.endswith(".csv"):
                    fname += ".csv"
                return self._send_bytes(payload, "text/csv; charset=utf-8", status,
                                        {"Content-Disposition": "attachment; filename=" + fname})
            return self._send_json(payload, status)

        # Статика/SPA-fallback: ровно ОДИН HTTP-ответ на запрос. Раньше здесь
        # после _static() отправлялся второй ответ (404 JSON) в то же
        # keep-alive соединение — нарушение HTTP/1.1-фрейминга (P1-ревью):
        # следующий запрос по соединению читал мусор от предыдущего.
        self._static(u.path.lstrip("/"))
        return

    def _recording_signed(self, call_id, query):
        from . import records
        q = query or {}
        exp = (q.get("exp") or [""])[0]
        sig = (q.get("sig") or [""])[0]
        if not records.verify(call_id, exp, sig):
            return self._send_json({"ok": False, "error": "bad_signature"}, 403)
        path = records.path_for(call_id)
        if path is None:
            return self._send_json({"ok": False, "error": "no_recording"}, 404)
        import os
        fname = os.path.basename(str(path))
        ctype = {".mp3": "audio/mpeg", ".wav": "audio/wav", ".ogg": "audio/ogg",
                 ".opus": "audio/ogg", ".m4a": "audio/mp4", ".aac": "audio/aac",
                 ".flac": "audio/flac"}.get(path.suffix.lower(), "application/octet-stream")
        self._send_bytes(path.read_bytes(), ctype, 200,
                         {"Content-Disposition": 'inline; filename="' + fname + '"',
                          "Cache-Control": "private, max-age=60"})

    def _recording(self, call_id, query):
        qticket = (query.get("ticket") or [""])[0]
        if not auth_ok(self.headers, qticket):
            return self._send_json({"ok": False, "error": "auth_required"}, 401)
        import os
        call = db.fetch1("SELECT recording FROM calls WHERE id=?", (call_id,))
        if not call or not call["recording"]:
            return self._send_json({"ok": False, "error": "no_recording"}, 404)
        fname = os.path.basename(str(call["recording"]))
        path = (config.REC_DIR / fname).resolve()
        if not str(path).startswith(str(config.REC_DIR.resolve())) or not path.exists():
            return self._send_json({"ok": False, "error": "no_file"}, 404)
        ext = os.path.splitext(fname)[1].lower()
        ctype = {".mp3": "audio/mpeg", ".wav": "audio/wav", ".ogg": "audio/ogg", ".opus": "audio/ogg",
                 ".m4a": "audio/mp4", ".aac": "audio/aac", ".flac": "audio/flac"}.get(ext, "application/octet-stream")
        data = path.read_bytes()
        self._send_bytes(data, ctype, 200, {"Content-Disposition": 'inline; filename="' + fname + '"'})

    # ---------- SSE ----------
    def _sse(self):
        q = parse_qs(urlparse(self.path).query)
        qticket = (q.get("ticket") or [""])[0]
        # лимиты соединений ДО расходования билета и открытия потока (C4)
        if events.subscriber_count() >= MAX_SSE_CLIENTS:
            return self._send_json({"ok": False, "error": "sse_capacity",
                                    "detail": "слишком много SSE-подписчиков"}, 503)
        from . import api as _api
        sess, err, code = _api.need_auth(self.headers, query_ticket=qticket)
        if err:
            return self._send_json(err, code)
        login = sess["login"]
        if _sse_user_inc(login) > MAX_SSE_PER_USER:
            _sse_user_dec(login)
            return self._send_json({"ok": False, "error": "sse_limit_per_user",
                                    "detail": "слишком много открытых вкладок/соединений"},
                                   429)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        stream = events.EventStream()
        try:
            for chunk in stream.iter_events():
                try:
                    self.wfile.write(chunk.encode("utf-8"))
                    self.wfile.flush()
                except Exception:
                    break
        finally:
            stream.close()
            _sse_user_dec(login)

    # ---------- POST ----------
    def do_POST(self):
        u = urlparse(self.path)
        try:
            body = self._body(_body_limit_for(u.path))
        except BodyTooLarge as e:
            # 413 + разрыв keep-alive: тело не читаем (иначе висящее соединение
            # ждёт байты, которых мы не планируем забирать)
            self.close_connection = True
            payload = json_bytes({"ok": False, "error": "payload_too_large",
                                  "detail": "лимит {} байт для этого маршрута "
                                            "превышен ({})".format(
                                      _body_limit_for(u.path), e.args[0])})
            return self._send_bytes(payload, "application/json; charset=utf-8", 413,
                                    {"Connection": "close", "Retry-After": "5"})
        payload, status = api.route("POST", u.path, body, self._api_headers())
        if payload is None:
            return self._send_json({"ok": False, "error": "not_found"}, 404)
        if isinstance(payload, bytes):
            fname = u.path.rstrip("/").split("/")[-1] or "download.csv"
            if not fname.endswith(".csv"):
                fname += ".csv"
            return self._send_bytes(payload, "text/csv; charset=utf-8", status,
                                    {"Content-Disposition": "attachment; filename=" + fname})
        self._send_json(payload, status)


class QuietHTTPServer(ThreadingHTTPServer):
    """Глушит штатный сетевой шум: клиент разорвал соединение раньше,
    чем сервер успел ответить (WinError 10053, reset/pipe). Это не ошибка
    программы — браузеры, антивирусы и сканеры сети так делают регулярно."""

    def handle_error(self, request, client_address):
        exc = sys.exc_info()[1]
        if isinstance(exc, ConnectionError):
            return
        super().handle_error(request, client_address)


def create_server(host, port):
    httpd = QuietHTTPServer((host, port), Handler)
    httpd.daemon_threads = True
    httpd.allow_reuse_address = True
    return httpd
