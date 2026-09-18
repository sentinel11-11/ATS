import csv
import hashlib
import io
import json
import os
import secrets
import socket
import socketserver
import sys
import threading
import time
import uuid
import webbrowser
from datetime import datetime
from http.server import SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse

FROZEN = bool(getattr(sys, "frozen", False))
BASE = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent
STATIC = Path(getattr(sys, "_MEIPASS", BASE)) / "static" if FROZEN else BASE / "static"
DATA = BASE / "data"
SHARED = BASE / "shared"
for p in [DATA, STATIC, SHARED, SHARED / "commands", SHARED / "status", SHARED / "processed", SHARED / "bad"]:
    p.mkdir(parents=True, exist_ok=True)

PATHS = {k: DATA / (k + ".json") for k in ["contacts", "templates", "lines", "queue", "call_log", "settings"]}
LOCK = threading.RLock()
SESSIONS = {}
STOP = threading.Event()

DEFAULT_SETTINGS = {
    "admin_login": "admin",
    "admin_password_salt": "",
    "admin_password_hash": "",
    "host": "0.0.0.0",
    "port": 9123,
    "max_parallel": 6,
    "retry_count": 1,
    "retry_delay_sec": 60,
    "line_cooldown_sec": 5,
    "allowed_hours_start": "08:00",
    "allowed_hours_end": "20:00",
    "consent_required": True,
    "test_mode": True
}

DEFAULT_TEMPLATES = [
    {"id": 1, "name": "Основной", "text": "Здравствуйте. Это автоматическое информационное сообщение. {name}, прослушайте, пожалуйста, информацию.", "active": True},
    {"id": 2, "name": "Напоминание", "text": "Здравствуйте, {name}. Напоминаем вам о запланированном событии. Благодарим за внимание.", "active": True}
]

DEFAULT_LINES = []
for i in range(1, 7):
    DEFAULT_LINES.append({"id": i, "name": "Линия {}".format(i), "enabled": True, "mode": "test", "com_port": "COM{}".format(i), "baudrate": 9600, "dial_prefix": "ATD", "dial_suffix": ";", "hangup_command": "ATH", "audio_command": "", "status": "offline", "current_call_id": "", "last_error": "", "last_seen": ""})


def dump(v):
    return json.dumps(v, ensure_ascii=False, indent=2)


def read(name, default):
    with LOCK:
        p = PATHS[name]
        if not p.exists():
            p.write_text(dump(default), encoding="utf-8")
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return default


def write(name, value):
    with LOCK:
        tmp = PATHS[name].with_suffix(".tmp")
        tmp.write_text(dump(value), encoding="utf-8")
        tmp.replace(PATHS[name])


def stamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def hash_password(password, salt):
    return hashlib.sha256((salt + "::" + str(password)).encode("utf-8")).hexdigest()


def init_data():
    for name, default in [("contacts", []), ("templates", DEFAULT_TEMPLATES), ("lines", DEFAULT_LINES), ("queue", []), ("call_log", []), ("settings", DEFAULT_SETTINGS)]:
        if not PATHS[name].exists():
            write(name, default)
    s = read("settings", DEFAULT_SETTINGS)
    if not s.get("admin_password_salt"):
        salt = secrets.token_hex(16)
        s["admin_password_salt"] = salt
        s["admin_password_hash"] = hash_password("admin1234", salt)
        write("settings", s)


def next_id(items):
    vals = []
    for x in items:
        try:
            vals.append(int(x.get("id", 0)))
        except Exception:
            pass
    return max(vals + [0]) + 1


def public_settings():
    s = read("settings", DEFAULT_SETTINGS)
    return {k: v for k, v in s.items() if "password" not in k and k != "admin_login"}


def check_password(login, password):
    s = read("settings", DEFAULT_SETTINGS)
    return str(login) == str(s.get("admin_login", "admin")) and hash_password(password, s.get("admin_password_salt", "")) == s.get("admin_password_hash", "")


def normalize_phone(v):
    raw = str(v or "").strip()
    out = ""
    for c in raw:
        if c.isdigit() or (c == "+" and not out):
            out += c
    return out


def in_allowed_hours():
    s = read("settings", DEFAULT_SETTINGS)
    now = datetime.now().strftime("%H:%M")
    return str(s.get("allowed_hours_start", "08:00")) <= now <= str(s.get("allowed_hours_end", "20:00"))


def render_text(template, contact):
    text = str(template.get("text", ""))
    values = {
        "name": contact.get("name", ""),
        "phone": contact.get("phone", ""),
        "group": contact.get("group", ""),
        "note": contact.get("note", "")
    }
    for k, v in values.items():
        text = text.replace("{" + k + "}", str(v or ""))
    return text


def write_command(line, item, contact, template):
    root = SHARED / "commands"
    cmd_id = uuid.uuid4().hex
    text = render_text(template, contact)
    body = {
        "id": cmd_id,
        "kind": "outbound_call",
        "created_at": stamp(),
        "call_id": item["id"],
        "line_id": line["id"],
        "phone": contact["phone"],
        "text": text,
        "contact_name": contact.get("name", ""),
        "template_id": template["id"],
        "line": line
    }
    path = root / ("call_{}_{}.json".format(item["id"], cmd_id[:8]))
    path.write_text(dump(body), encoding="utf-8")
    return cmd_id, text


def update_log(call_id, **kwargs):
    logs = read("call_log", [])
    for x in logs:
        if str(x.get("id")) == str(call_id):
            x.update(kwargs)
            write("call_log", logs)
            return x
    return None


def line_by_id(lines, lid):
    for x in lines:
        if str(x.get("id")) == str(lid):
            return x
    return None


def scan_status():
    status_dir = SHARED / "status"
    lines = read("lines", DEFAULT_LINES)
    queue = read("queue", [])
    logs = read("call_log", [])
    changed = False
    for path in list(status_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            lid = data.get("line_id")
            cid = data.get("call_id")
            st = data.get("status", "error")
            line = line_by_id(lines, lid)
            if line:
                line["status"] = "ready" if st in ["done", "failed", "error", "no_answer", "busy"] else st
                line["current_call_id"] = "" if st in ["done", "failed", "error", "no_answer", "busy"] else cid
                line["last_error"] = data.get("status_text", "") if st in ["failed", "error"] else ""
                line["last_seen"] = stamp()
            for q in queue:
                if str(q.get("id")) == str(cid):
                    if st in ["done", "failed", "error", "no_answer", "busy"]:
                        q["status"] = "completed" if st == "done" else st
                        q["completed_at"] = stamp()
                    else:
                        q["status"] = st
            for l in logs:
                if str(l.get("id")) == str(cid):
                    l["status"] = st
                    l["result"] = data.get("status_text", "")
                    if st in ["done", "failed", "error", "no_answer", "busy"]:
                        l["finished_at"] = stamp()
            dest = SHARED / "processed" / path.name
            path.replace(dest)
            changed = True
        except Exception:
            try:
                path.replace(SHARED / "bad" / path.name)
            except Exception:
                pass
    if changed:
        write("lines", lines)
        write("queue", queue)
        write("call_log", logs)


def dispatch_loop():
    while not STOP.is_set():
        try:
            scan_status()
            if not in_allowed_hours():
                time.sleep(1)
                continue
            settings = read("settings", DEFAULT_SETTINGS)
            lines = read("lines", DEFAULT_LINES)
            queue = read("queue", [])
            contacts = read("contacts", [])
            templates = read("templates", DEFAULT_TEMPLATES)
            active = [x for x in lines if x.get("enabled") and x.get("status") in ["ready", "offline"]]
            active = active[:max(1, min(6, int(settings.get("max_parallel", 6))))]
            pending = [x for x in queue if x.get("status") == "queued"]
            for line in active:
                if not pending:
                    break
                item = pending.pop(0)
                contact = next((x for x in contacts if str(x.get("id")) == str(item.get("contact_id"))), None)
                template = next((x for x in templates if str(x.get("id")) == str(item.get("template_id"))), None)
                if not contact or not template:
                    item["status"] = "error"
                    item["completed_at"] = stamp()
                    continue
                if settings.get("consent_required", True) and not contact.get("consent", False):
                    item["status"] = "blocked_no_consent"
                    item["completed_at"] = stamp()
                    continue
                if settings.get("test_mode", True):
                    line["mode"] = "test"
                cmd_id, text = write_command(line, item, contact, template)
                item["status"] = "dialing"
                item["started_at"] = stamp()
                item["line_id"] = line["id"]
                item["command_id"] = cmd_id
                line["status"] = "dialing"
                line["current_call_id"] = item["id"]
                logs = read("call_log", [])
                logs.append({"id": item["id"], "datetime": stamp(), "contact_id": contact["id"], "name": contact.get("name", ""), "phone": contact.get("phone", ""), "line_id": line["id"], "template_id": template["id"], "text": text, "status": "dialing", "result": "", "finished_at": ""})
                write("call_log", logs[-10000:])
            write("queue", queue)
            write("lines", lines)
        except Exception:
            pass
        time.sleep(1)


def local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


class Handler(SimpleHTTPRequestHandler):
    def translate_path(self, path):
        if path.startswith("/static/"):
            rel = path[len("/static/"):].split("?", 1)[0].split("#", 1)[0]
            return str(STATIC / rel)
        return super().translate_path(path)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def send_json(self, value, status=200):
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def body(self):
        n = int(self.headers.get("Content-Length", "0") or "0")
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8", "ignore"))
        except Exception:
            return {}

    def token(self):
        return self.headers.get("X-Admin-Token", "")

    def authorized(self):
        t = self.token()
        return t in SESSIONS and SESSIONS[t] > time.time()

    def need_admin(self):
        if self.authorized():
            SESSIONS[self.token()] = time.time() + 28800
            return True
        self.send_json({"ok": False, "error": "admin_required"}, 401)
        return False

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/":
            self.path = "/static/index.html"
            return SimpleHTTPRequestHandler.do_GET(self)
        if u.path == "/api/state":
            scan_status()
            self.send_json({"contacts": read("contacts", []), "templates": read("templates", DEFAULT_TEMPLATES), "lines": read("lines", DEFAULT_LINES), "queue": read("queue", []), "call_log": read("call_log", []), "settings": public_settings(), "admin": self.authorized(), "lan_ip": local_ip()})
            return
        if u.path == "/api/export/contacts.csv":
            out = io.StringIO()
            w = csv.writer(out, delimiter=";")
            w.writerow(["name", "phone", "group", "note", "consent"])
            for x in read("contacts", []):
                w.writerow([x.get("name", ""), x.get("phone", ""), x.get("group", ""), x.get("note", ""), "1" if x.get("consent") else "0"])
            raw = ("\ufeff" + out.getvalue()).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition", "attachment; filename=contacts.csv")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return
        return SimpleHTTPRequestHandler.do_GET(self)

    def do_POST(self):
        u = urlparse(self.path)
        b = self.body()
        if u.path == "/api/auth/login":
            if check_password(b.get("login", ""), b.get("password", "")):
                t = secrets.token_hex(24)
                SESSIONS[t] = time.time() + 28800
                return self.send_json({"ok": True, "token": t})
            return self.send_json({"ok": False, "error": "bad_login"}, 403)
        if u.path == "/api/auth/logout":
            SESSIONS.pop(self.token(), None)
            return self.send_json({"ok": True})
        if not self.need_admin():
            return
        if u.path == "/api/contacts/save":
            items = read("contacts", [])
            item = dict(b)
            item["phone"] = normalize_phone(item.get("phone"))
            if not item.get("phone"):
                return self.send_json({"ok": False, "error": "phone_required"}, 400)
            if item.get("id"):
                for i, old in enumerate(items):
                    if str(old.get("id")) == str(item.get("id")):
                        item["id"] = old["id"]
                        items[i] = item
                        break
                else:
                    item["id"] = next_id(items)
                    items.append(item)
            else:
                item["id"] = next_id(items)
                items.append(item)
            write("contacts", items)
            return self.send_json({"ok": True})
        if u.path == "/api/contacts/delete":
            ids = {str(x) for x in b.get("ids", [])}
            write("contacts", [x for x in read("contacts", []) if str(x.get("id")) not in ids])
            return self.send_json({"ok": True})
        if u.path == "/api/contacts/import":
            rows = b.get("rows", [])
            items = read("contacts", [])
            added = 0
            for r in rows:
                phone = normalize_phone(r.get("phone"))
                if not phone:
                    continue
                if any(normalize_phone(x.get("phone")) == phone for x in items):
                    continue
                items.append({"id": next_id(items), "name": r.get("name", ""), "phone": phone, "group": r.get("group", ""), "note": r.get("note", ""), "consent": bool(r.get("consent", False))})
                added += 1
            write("contacts", items)
            return self.send_json({"ok": True, "added": added})
        if u.path == "/api/templates/save":
            items = read("templates", DEFAULT_TEMPLATES)
            item = dict(b)
            if item.get("id"):
                for i, old in enumerate(items):
                    if str(old.get("id")) == str(item.get("id")):
                        item["id"] = old["id"]
                        items[i] = item
                        break
                else:
                    item["id"] = next_id(items)
                    items.append(item)
            else:
                item["id"] = next_id(items)
                items.append(item)
            write("templates", items)
            return self.send_json({"ok": True})
        if u.path == "/api/templates/delete":
            tid = str(b.get("id", ""))
            write("templates", [x for x in read("templates", []) if str(x.get("id")) != tid])
            return self.send_json({"ok": True})
        if u.path == "/api/lines/save":
            items = read("lines", DEFAULT_LINES)
            lid = str(b.get("id", ""))
            for i, old in enumerate(items):
                if str(old.get("id")) == lid:
                    merged = dict(old)
                    merged.update(b)
                    items[i] = merged
                    break
            write("lines", items)
            return self.send_json({"ok": True})
        if u.path == "/api/lines/reset":
            items = read("lines", DEFAULT_LINES)
            for x in items:
                x["status"] = "offline"
                x["current_call_id"] = ""
                x["last_error"] = ""
            write("lines", items)
            return self.send_json({"ok": True})
        if u.path == "/api/queue/add":
            ids = [str(x) for x in b.get("contact_ids", [])]
            tid = b.get("template_id")
            items = read("queue", [])
            contacts = read("contacts", [])
            added = 0
            for c in contacts:
                if str(c.get("id")) in ids:
                    items.append({"id": uuid.uuid4().hex, "contact_id": c["id"], "template_id": tid, "status": "queued", "created_at": stamp(), "started_at": "", "completed_at": "", "line_id": "", "command_id": ""})
                    added += 1
            write("queue", items[-20000:])
            return self.send_json({"ok": True, "added": added})
        if u.path == "/api/queue/clear":
            write("queue", [x for x in read("queue", []) if x.get("status") not in ["queued"]])
            return self.send_json({"ok": True})
        if u.path == "/api/settings/save":
            s = read("settings", DEFAULT_SETTINGS)
            for k in ["host", "port", "max_parallel", "retry_count", "retry_delay_sec", "line_cooldown_sec", "allowed_hours_start", "allowed_hours_end", "consent_required", "test_mode"]:
                if k in b:
                    s[k] = b[k]
            if b.get("new_login"):
                s["admin_login"] = str(b.get("new_login"))
            if b.get("new_password"):
                salt = secrets.token_hex(16)
                s["admin_password_salt"] = salt
                s["admin_password_hash"] = hash_password(b.get("new_password"), salt)
            write("settings", s)
            return self.send_json({"ok": True})
        return self.send_json({"ok": False, "error": "not_found"}, 404)


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def open_server():
    s = read("settings", DEFAULT_SETTINGS)
    host = str(s.get("host", "0.0.0.0"))
    try:
        port = int(os.environ.get("ATS_PORT", s.get("port", 9123)))
    except Exception:
        port = 9123
    for p in [port, 9123, 8090, 8091, 8080, 5050]:
        try:
            return Server((host, p), Handler), p
        except OSError:
            continue
    raise RuntimeError("Не удалось открыть порт ATS")


if __name__ == "__main__":
    init_data()
    os.chdir(BASE)
    threading.Thread(target=dispatch_loop, daemon=True).start()
    httpd, port = open_server()
    url = "http://127.0.0.1:{}".format(port)
    print("ATS Dispatcher started")
    print("Local:", url)
    print("LAN: http://{}:{}".format(local_ip(), port))
    print("Admin login: admin")
    print("Default password: admin1234")
    threading.Timer(1, lambda: webbrowser.open(url)).start()
    try:
        with httpd:
            httpd.serve_forever()
    finally:
        STOP.set()
