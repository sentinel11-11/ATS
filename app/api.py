# -*- coding: utf-8 -*-
"""HTTP API v2 ATS (замена открытых эндпоинтов server.py). Авторизация: X-ATS-Token."""
import base64
import csv
import io
import json
import re
import threading
import time
import uuid
from datetime import datetime
from urllib.parse import parse_qs, urlparse

from . import agent as agent_mod
from . import config, db, events, numbers as numbers_mod, security

ENGINE = None  # устанавливается при старте (см. run.py)

OK = {"ok": True}

# Защита входа от перебора: MAX_FAILS неудачных попыток -> блокировка LOCK_SECONDS
MAX_FAILS = 5
LOCK_SECONDS = 300
WINDOW_SECONDS = 600
_LOGIN_ATTEMPTS = {}   # login -> [timestamps]
_LOGIN_LOCK = threading.RLock()


def _login_allowed(login):
    now = time.time()
    with _LOGIN_LOCK:
        arr = [t for t in _LOGIN_ATTEMPTS.get(login, []) if now - t < WINDOW_SECONDS]
        if len(arr) >= MAX_FAILS:
            retry = max(0, int(arr[0] + LOCK_SECONDS - now))
            return False, retry
        return True, 0


def _login_fail(login):
    now = time.time()
    with _LOGIN_LOCK:
        arr = _LOGIN_ATTEMPTS.setdefault(login, [])
        arr.append(now)
        _LOGIN_ATTEMPTS[login] = [t for t in arr if now - t < WINDOW_SECONDS]


def _login_ok(login):
    with _LOGIN_LOCK:
        _LOGIN_ATTEMPTS.pop(login, None)


def json_bytes(value):
    return json.dumps(value, ensure_ascii=False).encode("utf-8")


def now_iso():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


MASK = "********"


def _is_secret_key(key):
    kl = str(key or "").lower()
    if kl.endswith("_env"):
        return False  # имя env-переменной (напр. api_key_env) — не секрет
    return any(s in kl for s in ("api_key", "apikey", "secret", "token", "password", "passwd"))


def _mask_secrets(obj):
    if isinstance(obj, dict):
        return {k: (MASK if _is_secret_key(k) and v else _mask_secrets(v))
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [_mask_secrets(x) for x in obj]
    return obj


def need_auth(headers, role=None, query_token=""):
    tok = headers.get("X-Ats-Token") or headers.get("X-Admin-Token") or query_token or ""
    s = security.get_session(tok)
    if not s:
        return None, {"ok": False, "error": "auth_required"}, 401
    # Пользователь мог быть деактивирован/удалён или ему сменили роль — сверяем с БД
    user = db.fetch1("SELECT role, active FROM users WHERE login=?", (s["login"],))
    if not user or user["active"] != 1:
        security.drop_token(tok)
        return None, {"ok": False, "error": "auth_required"}, 401
    if user["role"] != s["role"]:
        security.refresh_session_role(tok, user["role"])
        s["role"] = user["role"]
    if role and s["role"] != role and s["role"] != "admin":
        return None, {"ok": False, "error": "admin_required"}, 403
    return s, None, None


def parse_path(path):
    u = urlparse(path)
    parts = [p for p in u.path.split("/") if p]
    return parts, u


def route(method, path, body, headers):
    """Возвращает (payload_dict, status) или (bytes, status) для файлов."""
    parts, u = parse_path(path)
    if parts[:2] != ["api", "v2"]:
        return None
    p = parts[2:]
    qparams = parse_qs(u.query)
    qtoken = (qparams.get("token") or [""])[0]
    # ---------- auth ----------
    if p == ["auth", "login"]:
        # Логины хранятся строчными (см. _user_save) — приводим и при входе,
        # иначе "Operator1" не найдёт "operator1" -> ложный bad_login.
        login = str(body.get("login", "")).strip().lower()
        password = str(body.get("password", ""))
        allowed, retry_after = _login_allowed(login)
        if not allowed:
            return {"ok": False, "error": "too_many_attempts",
                    "retry_after_sec": retry_after}, 429
        user = db.fetch1("SELECT * FROM users WHERE login=? AND active=1", (login,))
        if user and security.hash_password(password, user["salt"]) == user["password_hash"]:
            _login_ok(login)
            tok = security.create_token(user["login"], user["role"])
            return {"ok": True, "token": tok, "role": user["role"]}, 200
        _login_fail(login)
        return {"ok": False, "error": "bad_login"}, 403
    if p == ["auth", "logout"]:
        security.drop_token(headers.get("X-Ats-Token", ""))
        return OK, 200
    if p == ["webhooks", "uis"] and method == "POST":
        s = db.get_settings()
        secret = (s.get("uis") or {}).get("webhook_secret", "")
        if not secret:
            # Production: вебхук без настроенного секрета отключён,
            # иначе любой может слать события в движок.
            return {"ok": False, "error": "webhook_disabled"}, 403
        if headers.get("X-UIS-Secret", "") != secret:
            return {"ok": False, "error": "bad_secret"}, 403
        from .telephony import map_uis_webhook
        ev = map_uis_webhook(body)
        if not ev:
            return {"ok": False, "error": "unrecognized"}, 422
        # Только в очередь — отвечаем мгновенно, обработку делает тик движка.
        # Иначе провайдер упрётся в HTTP-таймаут и завалит нас ретраями,
        # а повторная обработка тех же событий даст дубли.
        ENGINE.push_event(ev)
        return OK, 200
    if p == ["webhooks", "megafon"] and method == "POST":
        from .providers.base import resolve_secret
        from .providers.megafon_vats import (map_megafon_webhook, phone_variants,
                                             webhook_fingerprint)
        s = db.get_settings()
        mcfg = s.get("megafon_vats") or {}
        expected = resolve_secret(mcfg, "crm_token", "crm_token_env",
                                  "ATS_MEGAFON_CRM_TOKEN")
        if not expected:
            # Fail-closed: без настроенного crm_token вебхук отключён.
            return {"ok": False, "error": "webhook_disabled"}, 403
        if str((body or {}).get("crm_token") or "") != expected:
            return {"ok": False, "error": "bad_secret"}, 403
        cmd = str((body or {}).get("cmd") or "").strip().lower()
        if cmd == "contact":
            # Единственный синхронный ответ: ВАТС ждёт имя/ответственного
            # прямо в HTTP-ответе (показ на телефоне, автораутинг).
            # Только быстрый индексированный поиск — никакой тяжёлой работы.
            c = None
            for variant in phone_variants((body or {}).get("phone")):
                if not variant:
                    continue
                c = db.fetch1("SELECT * FROM contacts WHERE phone=?", (variant,))
                if c:
                    break
            if c and c.get("name"):
                return {"contact_name": c["name"]}, 200
            return {"contact_name": ""}, 200
        ev = map_megafon_webhook(body or {})
        if not ev:
            return {"ok": False, "error": "unrecognized"}, 422
        # Отпечаток для идемпотентности считает транспортный слой —
        # движок использует его как opaque-строку (§3 ТЗ).
        ev["fingerprint"] = webhook_fingerprint(ev)
        # Live-forensics: каждый вебхук виден в логе (сверка фаз ВАТС).
        print("[megafon] webhook cmd={} ev={} callid={} dir={} user={}".format(
            cmd, ev.get("event"), ev.get("external_call_id"),
            ev.get("direction"), ev.get("user")))
        # Как и UIS: только в очередь, обработка — тиком движка.
        ENGINE.push_event(ev)
        return OK, 200
    if p == ["health"] and method == "GET":
        return _health(), 200
    if p == ["auth", "me"]:
        sess2, err2, code2 = need_auth(headers, query_token=qtoken)
        if err2:
            return err2, code2
        return {"ok": True, "login": sess2["login"], "role": sess2["role"]}, 200

    sess, err, code = need_auth(headers, query_token=qtoken)
    if err:
        return err, code

    def admin_only():
        if sess["role"] != "admin":
            return {"ok": False, "error": "admin_required"}, 403
        return None

    # ---------- общедоступные чтения (обе роли) ----------
    if p == ["dashboard"]:
        return ENGINE.dashboard(), 200
    if p == ["reports"]:
        q = parse_qs(u.query)
        d_from = (q.get("from") or [""])[0]
        d_to = (q.get("to") or [""])[0]
        res = _reports(d_from or None, d_to or None)
        if res is None:
            return {"ok": False, "error": "bad_range"}, 400
        return res, 200
    if p == ["contacts"]:
        rows = db.fetch("SELECT c.*, d.name AS db_name FROM contacts c "
                        "LEFT JOIN databases d ON d.id=c.database_id "
                        "ORDER BY c.id DESC LIMIT 5000")
        return {"contacts": rows}, 200
    if p == ["contacts", "history"]:
        q = parse_qs(u.query)
        try:
            cid = int((q.get("id") or ["0"])[0])
        except Exception:
            cid = 0
        res = _contact_history(cid)
        if res is None:
            return {"ok": False, "error": "not_found"}, 404
        return res, 200
    if p == ["databases"]:
        return {"databases": db.list_databases()}, 200
    if p == ["numbers"]:
        return {"numbers": numbers_mod.pool_state()}, 200
    if p == ["campaigns"]:
        camps = db.fetch("SELECT * FROM campaigns ORDER BY id DESC")
        try:
            for c in camps:
                c["schedule"] = json.loads(c.get("schedule") or "{}")
                c["retry_map"] = json.loads(c.get("retry_map") or "{}")
        except Exception:
            pass
        tmpl = {t["id"]: t["name"] for t in db.fetch("SELECT id,name FROM templates")}
        cnt = db.fetch("SELECT campaign_id, status, COUNT(*) c FROM campaign_items GROUP BY campaign_id, status")
        calls = db.fetch("SELECT campaign_id, COUNT(*) c, SUM(CASE WHEN result IN ('done_ok','operator_ok','done_agent') THEN 1 ELSE 0 END) ok FROM calls GROUP BY campaign_id")
        by_c = {}
        for r in cnt:
            by_c.setdefault(r["campaign_id"], {})[r["status"]] = r["c"]
        calls_c = {}
        for r in calls:
            calls_c[r["campaign_id"]] = {"calls": r["c"], "ok": r["ok"] or 0}
        for c in camps:
            c["template_name"] = tmpl.get(c["template_id"], "")
            st = by_c.get(c["id"], {})
            c["items_total"] = sum(st.values())
            c["items_queued"] = st.get("queued", 0)
            c["items_done"] = sum(st.get(k, 0) for k in ("done_ok", "done_agent", "operator_ok", "exhausted", "no_operator", "blocked_no_consent", "blacklisted"))
            cc = calls_c.get(c["id"], {})
            c["calls"] = cc.get("calls", 0)
            c["calls_ok"] = cc.get("ok", 0)
        return {"campaigns": camps}, 200
    if p and p[0] == "campaigns" and len(p) == 2 and p[1].isdigit():
        cid = int(p[1])
        camp = db.fetch1("SELECT * FROM campaigns WHERE id=?", (cid,))
        if camp:
            try:
                camp["schedule"] = json.loads(camp.get("schedule") or "{}")
                camp["retry_map"] = json.loads(camp.get("retry_map") or "{}")
            except Exception:
                pass
        return {"campaign": camp, "items": db.fetch("SELECT * FROM campaign_items WHERE campaign_id=? ORDER BY id DESC LIMIT 2000", (cid,)),
                "calls": db.fetch("SELECT * FROM calls WHERE campaign_id=? ORDER BY id DESC LIMIT 1000", (cid,))}, 200
    if p and p[0] == "campaigns" and len(p) == 3 and p[2] == "items" and p[1].isdigit():
        cid = int(p[1])
        items = db.fetch("SELECT * FROM campaign_items WHERE campaign_id=? ORDER BY id DESC LIMIT 1000", (cid,))
        return {"items": items}, 200
    if p == ["templates"]:
        return {"templates": db.fetch("SELECT * FROM templates ORDER BY id")}, 200
    if p == ["calls"]:
        limit = min(int(body.get("limit", 500)) if body else 500, 2000)
        where = "1=1"
        params = []
        st = body.get("status") if body else None
        cid = body.get("campaign_id") if body else None
        if st:
            where += " AND result=?"
            params.append(st)
        if cid:
            where += " AND campaign_id=?"
            params.append(cid)
        rows = db.fetch("SELECT * FROM calls WHERE {} ORDER BY id DESC LIMIT {}".format(where, limit), params)
        return {"calls": rows}, 200
    if p and len(p) == 3 and p[0] == "calls" and p[1].isdigit() and p[2] == "timeline" \
            and method == "GET":
        # Forensics звонка для live-отладки (шаг 13 LIVE): карточка звонка +
        # все события ВАТС по external_call_id в порядке поступления + ACD-след.
        call = db.fetch1("SELECT * FROM calls WHERE id=?", (int(p[1]),))
        if not call:
            return {"ok": False, "error": "not_found"}, 404
        evs = []
        if call.get("external_call_id"):
            evs = db.fetch(
                "SELECT fingerprint, event_type, external_call_id, received_at, "
                "processed_at, status, payload_json FROM provider_events "
                "WHERE external_call_id=? ORDER BY received_at, id",
                (call["external_call_id"],))
            for e in evs:
                try:
                    e["payload"] = json.loads(e.pop("payload_json") or "{}")
                except Exception:
                    e["payload"] = {}
        acd = db.fetch("SELECT * FROM acd WHERE call_id=? ORDER BY id", (call["id"],))
        return {"call": call, "events": evs, "acd": acd}, 200
    if p == ["blacklist"]:
        return {"blacklist": db.fetch("SELECT * FROM blacklist ORDER BY id DESC")}, 200
    if p == ["settings"]:
        return {"settings": db.public_settings()}, 200
    if p == ["settings", "raw"] and method == "GET":
        if sess["role"] != "admin":
            return {"ok": False, "error": "admin_required"}, 403
        s = db.get_settings()
        masked = _mask_secrets({k: s.get(k) for k in ("uis", "ami", "megafon_vats", "llm", "bitrix24", "crm")})
        return {"provider_config": masked}, 200
    if p == ["settings", "raw"] and method == "POST":
        if sess["role"] != "admin":
            return {"ok": False, "error": "admin_required"}, 403
        s = db.get_settings()
        cfg = body.get("provider_config") or body
        for k in ("uis", "ami", "megafon_vats", "llm", "bitrix24", "crm"):
            if isinstance(cfg.get(k), dict):
                merged = dict(s.get(k) or {})
                for fk, fv in cfg[k].items():
                    if fv == MASK and fk in merged:
                        continue  # плейсхолдер — секрет не менялся, оставить старый
                    merged[fk] = fv
                s[k] = merged
        db.save_settings(s)
        return OK, 200
    if p == ["megafon", "pool-sync"] and method == "POST":
        if sess["role"] != "admin":
            return {"ok": False, "error": "admin_required"}, 403
        return _megafon_sync_route("pool", body)
    if p == ["megafon", "users-sync"] and method == "POST":
        if sess["role"] != "admin":
            return {"ok": False, "error": "admin_required"}, 403
        return _megafon_sync_route("users", body)
    if p == ["megafon", "groups-sync"] and method == "POST":
        if sess["role"] != "admin":
            return {"ok": False, "error": "admin_required"}, 403
        return _megafon_sync_route("groups", body)
    if p == ["megafon", "directory"]:
        return {"users": db.fetch(
            "SELECT login,name,position,ext,telnum,role,status,updated "
            "FROM vats_users ORDER BY login"),
                "groups": db.fetch(
            "SELECT group_id,name,ext,call_order,updated "
            "FROM vats_groups ORDER BY name")}, 200
    if p == ["health", "details"]:
        if sess["role"] != "admin":
            return {"ok": False, "error": "admin_required"}, 403
        return _health_details(), 200
    if p == ["megafon", "check"] and method == "POST":
        if sess["role"] != "admin":
            return {"ok": False, "error": "admin_required"}, 403
        return _megafon_check_route()
    if p == ["megafon", "simulate-event"] and method == "POST":
        if sess["role"] != "admin":
            return {"ok": False, "error": "admin_required"}, 403
        return _megafon_simulate_route(body)
    if p == ["operators"]:
        return {"operators": ENGINE.operators()}, 200
    if p == ["acd"]:
        return {"acd": ENGINE.acd_queued(),
                "operators": ENGINE.operators()}, 200
    if p == ["agent", "scenarios"]:
        out = []
        for t in db.fetch("SELECT id,name,scenario,text FROM templates"):
            try:
                sc = json.loads(t["scenario"] or "{}")
            except Exception:
                sc = {}
            out.append({"template_id": t["id"], "name": t["name"], "text": t["text"], "scenario": sc})
        return {"scenarios": out}, 200

    # ---------- только админ ----------
    if p == ["export", "contacts.csv"]:
        if sess["role"] != "admin":
            return {"ok": False, "error": "admin_required"}, 403
        return _export_contacts()
    if p == ["export", "calls.csv"]:
        if sess["role"] != "admin":
            return {"ok": False, "error": "admin_required"}, 403
        return _export_calls()

    if sess["role"] != "admin":
        # разрешённые операторские действия
        if p == ["operators", "status"] and method == "POST":
            return _operator_status(body, sess)
        if p == ["acd", "accept"] and method == "POST":
            return _acd_accept(body, sess)
        if p == ["calls", "complete"] and method == "POST":
            return _call_complete(body, sess)
        if p in (["contacts"], ["contacts", "save"]) and method == "POST":
            return _contact_save(body, sess)
        # оператор может сменить собственный пароль (и только это)
        if p == ["settings", "save"] and method == "POST" and set((body or {}).keys()) <= {"new_password"}:
            return _settings_save(body, sess)
        return {"ok": False, "error": "admin_required"}, 403

    # ================= администратор =================
    # пользователи и операторы
    if p == ["users"] and method == "GET":
        return {"users": _users_list()}, 200
    if p == ["users", "save"] and method == "POST":
        return _user_save(body, sess)
    if p == ["users", "delete"] and method == "POST":
        return _user_delete(body, sess)
    # контакты
    if p in (["contacts"], ["contacts", "save"]) and method == "POST":
        return _contact_save(body, sess)
    if p == ["contacts", "delete"]:
        ids = [str(x) for x in body.get("ids", [])]
        if ids:
            db.q("DELETE FROM contacts WHERE id IN ({})".format(",".join("?" * len(ids))), ids)
        return OK, 200
    if p == ["contacts", "import"]:
        return _contacts_import(body)
    if p == ["contacts", "import-file"]:
        return _contacts_import_file(body)
    # базы данных (списки контактов для обзвона)
    if p == ["databases", "save"]:
        return _database_save(body)
    if p == ["databases", "delete"]:
        ids = body.get("ids", [])
        with_contacts = bool(body.get("delete_contacts"))
        for raw in ids:
            try:
                db.delete_database(int(raw), delete_contacts=with_contacts)
            except Exception:
                pass
        return OK, 200
    # номера
    if p == ["numbers", "save"]:
        return _number_save(body)
    if p == ["numbers", "quarantine"]:
        numbers_mod.quarantine(int(body.get("id", 0)), bool(body.get("on", True)))
        return OK, 200
    if p == ["numbers", "delete"]:
        nid = int(body.get("id", 0))
        if nid:
            numbers_mod.delete_number(nid)
            return OK, 200
        return {"error": "bad_id"}, 400
    if p == ["numbers", "clear"]:
        db.q("DELETE FROM numbers")
        try:
            db.q("DELETE FROM sqlite_sequence WHERE name='numbers'")
        except Exception:
            pass
        return OK, 200
    if p == ["numbers", "reset"]:
        for n in numbers_mod.list_numbers(include_disabled=True):
            numbers_mod.update_number(n["id"], {"quarantined": False, "daily_count": 0, "cooldown_until": ""})
        return OK, 200
    # кампании
    if p == ["campaigns", "save"]:
        return _campaign_save(body)
    if p == ["campaigns", "clear_all"]:
        running = db.fetch("SELECT id FROM campaigns WHERE status='running'")
        rids = [x["id"] for x in running]
        if rids:
            excl = "WHERE campaign_id NOT IN ({})".format(",".join("?" * len(rids)))
            excl_c = "WHERE id NOT IN ({})".format(",".join("?" * len(rids)))
            db.q("DELETE FROM campaign_items " + excl, rids)
            db.q("DELETE FROM campaigns " + excl_c, rids)
            db.q("UPDATE calls SET campaign_id=0 " + excl, rids)
        else:
            db.q("DELETE FROM campaign_items")
            db.q("DELETE FROM campaigns")
            db.q("UPDATE calls SET campaign_id=0")
            try:
                db.q("DELETE FROM sqlite_sequence WHERE name IN ('campaigns', 'campaign_items')")
            except Exception:
                pass
        return OK, 200
    if p and p[0] == "campaigns" and len(p) == 2 and p[1].isdigit() and p[2:] == []:
        return {"campaign": db.fetch1("SELECT * FROM campaigns WHERE id=?", (int(p[1]),))}, 200
    if p and len(p) == 3 and p[0] == "campaigns":
        cid = int(p[1])
        act = p[2]
        if act == "start":
            # start = «продолжить»: исчерпавших лимит НЕ трогаем, иначе теряются
            # причина остановки и счётчики. Для повторного дозвона —
            # отдельное действие retry-exhausted.
            db.q("UPDATE campaigns SET status='running', updated=? WHERE id=?", (now_iso(), cid))
            db.q("UPDATE campaign_items SET status='queued', next_attempt_at='' WHERE campaign_id=? AND status IN ('queued','canceled')",
                 (cid,))
            events.publish("campaign", {"id": cid, "status": "running"})
            return OK, 200
        if act == "retry-exhausted":
            db.q("UPDATE campaigns SET status='running', updated=? WHERE id=?", (now_iso(), cid))
            cur = db.q("UPDATE campaign_items SET status='queued', next_attempt_at='', attempts=0 WHERE campaign_id=? AND status='exhausted'",
                       (cid,))
            events.publish("campaign", {"id": cid, "status": "running"})
            return {"ok": True, "requeued": cur.rowcount}, 200
        if act == "pause":
            db.q("UPDATE campaigns SET status='paused', updated=? WHERE id=?", (now_iso(), cid))
            events.publish("campaign", {"id": cid, "status": "paused"})
            return OK, 200
        if act == "stop":
            db.q("UPDATE campaigns SET status='stopped', updated=? WHERE id=?", (now_iso(), cid))
            db.q("UPDATE campaign_items SET status='canceled', completed_at=? WHERE campaign_id=? AND status IN ('queued')",
                 (now_iso(), cid))
            events.publish("campaign", {"id": cid, "status": "stopped"})
            return OK, 200
        if act == "add-contacts":
            return _campaign_add_contacts(cid, body)
        if act == "clear":
            st = db.fetch1("SELECT status FROM campaigns WHERE id=?", (cid,))
            if st and st["status"] == "running":
                return {"ok": False, "error": "campaign_running"}, 400
            active = db.fetch1("SELECT COUNT(*) c FROM calls WHERE campaign_id=? AND ended_at=''",
                               (cid,))
            if active and active["c"]:
                return {"ok": False, "error": "calls_in_progress"}, 400
            db.q("DELETE FROM campaign_items WHERE campaign_id=?", (cid,))
            return OK, 200
        if act == "delete":
            st = db.fetch1("SELECT status FROM campaigns WHERE id=?", (cid,))
            if st and st["status"] == "running":
                return {"ok": False, "error": "campaign_running"}, 400
            active = db.fetch1("SELECT COUNT(*) c FROM calls WHERE campaign_id=? AND ended_at=''",
                               (cid,))
            if active and active["c"]:
                return {"ok": False, "error": "calls_in_progress"}, 400
            db.q("DELETE FROM campaign_items WHERE campaign_id=?", (cid,))
            db.q("DELETE FROM campaigns WHERE id=?", (cid,))
            db.q("UPDATE calls SET campaign_id=0 WHERE campaign_id=?", (cid,))
            db.q("UPDATE calls SET campaign_id=0 WHERE campaign_id NOT IN (SELECT id FROM campaigns)")
            return OK, 200
    # шаблоны
    if p == ["templates", "save"]:
        return _template_save(body)
    if p == ["templates", "delete"]:
        db.q("DELETE FROM templates WHERE id=?", (int(body.get("id", 0)),))
        return OK, 200
    # чёрный список / жалобы
    if p == ["blacklist", "add"]:
        return _blacklist_add(body)
    if p == ["blacklist", "delete"]:
        db.q("DELETE FROM blacklist WHERE id=?", (int(body.get("id", 0)),))
        return OK, 200
    if p == ["complaint"]:
        return _complaint(body)
    if p == ["calls", "recording"] and method == "POST":
        if sess["role"] != "admin":
            return {"ok": False, "error": "admin_required"}, 403
        return _recording_upload(body)
    # настройки / пароль
    if p == ["settings", "save"]:
        return _settings_save(body, sess)
    if p == ["sim", "script"]:
        return _sim_script(body)
    # операторы (админ тоже может)
    if p == ["operators", "status"]:
        return _operator_status(body, sess)
    if p == ["acd", "accept"]:
        return _acd_accept(body, sess)
    if p == ["calls", "complete"]:
        return _call_complete(body, sess)
    if p == ["acd", "miss"] and method == "POST":
        acd_id = int(body.get("id", 0))
        db.q("UPDATE acd SET status='missed', updated=? WHERE id=?", (now_iso(), acd_id))
        return OK, 200
    return {"ok": False, "error": "not_found"}, 404


# ---------------- реализации ----------------
def _export_contacts():
    out = io.StringIO()
    w = csv.writer(out, delimiter=";")
    w.writerow(["id", "name", "phone", "group", "database", "tags", "note",
                "consent", "blacklisted", "complaints"])
    dbnames = {d["id"]: d["name"] for d in db.fetch("SELECT id, name FROM databases")}
    for x in db.fetch("SELECT * FROM contacts"):
        w.writerow([x["id"], x["name"], x["phone"], x["grp"],
                    dbnames.get(x.get("database_id") or 0, ""), x.get("tags", ""), x["note"],
                    "1" if x["consent"] else "0", "1" if x["blacklisted"] else "0", x["complaints"]])
    raw = ("\ufeff" + out.getvalue()).encode("utf-8")
    return raw, 200


def _clean_phone(v):
    raw = str(v or "").strip()
    out = ""
    for ch in raw:
        if ch.isdigit() or (ch == "+" and not out):
            out += ch
    return out


def _phone_valid(phone):
    """E.164-минимум: 10–15 цифр (без кода страны '8' подстановка не нужна)."""
    digits = "".join(ch for ch in str(phone or "") if ch.isdigit())
    return 10 <= len(digits) <= 15


def _contact_save(body, sess=None):
    item = dict(body)
    item["phone"] = _clean_phone(item.get("phone"))
    if not item.get("phone"):
        return {"ok": False, "error": "phone_required"}, 400
    if not _phone_valid(item["phone"]):
        return {"ok": False, "error": "bad_phone"}, 400
    cid = item.get("id")
    from . import importers as importers_mod
    try:
        database_id = int(item.get("database_id", 0) or 0)
    except Exception:
        database_id = 0
    data = {"name": str(item.get("name", ""))[:200], "phone": item["phone"],
            "grp": str(item.get("group", item.get("grp", "")))[:200],
            "note": str(item.get("note", ""))[:500],
            "consent": 1 if item.get("consent") else 0,
            "blacklisted": 1 if item.get("blacklisted") else 0,
            "tags": importers_mod.clean_tags(item.get("tags", "")),
            "database_id": database_id,
            "updated": now_iso()}
    if sess is not None and sess.get("role") != "admin":
        # Оператор не управляет согласиями и чёрным списком (152-ФЗ):
        # у существующих контактов значения сохраняем, у новых — «нет».
        try:
            cid_int = int(cid) if cid else 0
        except (TypeError, ValueError):
            cid_int = 0
        if cid_int:
            cur = db.fetch1("SELECT consent, blacklisted FROM contacts WHERE id=?", (cid_int,))
        else:
            cur = db.fetch1("SELECT consent, blacklisted FROM contacts WHERE phone=?", (item["phone"],))
        if cur:
            data["consent"] = cur["consent"]
            data["blacklisted"] = cur["blacklisted"]
        else:
            data["consent"] = 0
            data["blacklisted"] = 0
    if cid:
        try:
            db.update("contacts", data, "id=?", (int(cid),))
            return OK, 200
        except Exception as e:
            return {"ok": False, "error": str(e)}, 400
    existing = db.fetch1("SELECT id FROM contacts WHERE phone=?", (item["phone"],))
    if existing:
        db.update("contacts", {**data, "consent": data["consent"]}, "id=?", (existing["id"],))
        return {"ok": True, "updated_existing": existing["id"]}, 200
    try:
        db.insert("contacts", {**data, "created": now_iso(), "consent_source": "manual",
                               "complaints": 0})
        return OK, 200
    except Exception as e:
        return {"ok": False, "error": str(e)}, 400


def _contacts_import(body):
    """Быстрый импорт готовых строк {phone,name,group,note,consent,tags} (вставка из UI)."""
    from . import importers as importers_mod
    rows = body.get("rows", [])
    records = []
    for i, r in enumerate(rows):
        if not isinstance(r, dict):
            records.append({"row": i + 1, "phone": "", "consent": False})
            continue
        records.append({"row": i + 1, "phone": r.get("phone"), "name": r.get("name", ""),
                        "group": r.get("group", r.get("grp", "")), "tags": r.get("tags", ""),
                        "note": r.get("note", ""), "consent": r.get("consent")})
    try:
        database_id = int(body.get("database_id", 0) or 0)
    except Exception:
        database_id = 0
    res = importers_mod.import_records(records, database_id=database_id)
    return {"ok": True, "added": res["added"], "updated": res["updated"],
            "skipped": res["skipped"], "errors": res["errors"][:50],
            "errors_total": res["errors_total"]}, 200


def _contacts_import_file(body):
    """Загрузка базы файлом: {filename, content_b64, database_id?, database_name?,
    notes?, consent_default?} — .xlsx/.csv."""
    from . import importers as importers_mod
    filename = str(body.get("filename", ""))
    raw_b64 = body.get("content_b64", "")
    if not filename or not raw_b64:
        return {"ok": False, "error": "file_required"}, 400
    try:
        data = base64.b64decode(raw_b64)
    except Exception:
        return {"ok": False, "error": "bad_base64"}, 400
    if len(data) > 48 * 1024 * 1024:
        return {"ok": False, "error": "file_too_large"}, 400
    database_id = 0
    try:
        database_id = int(body.get("database_id", 0) or 0)
    except Exception:
        database_id = 0
    if database_id:
        row = db.fetch1("SELECT id FROM databases WHERE id=?", (database_id,))
        if not row:
            return {"ok": False, "error": "database_not_found"}, 404
    # Сначала парсим файл, и только потом создаём базу — иначе от каждого
    # битого файла остаётся пустая база-призрак.
    try:
        parsed = importers_mod.parse_file(
            filename, data, consent_default=bool(body.get("consent_default", False)))
    except ValueError as e:
        return {"ok": False, "error": "parse_error", "detail": str(e)[:300]}, 400
    except Exception as e:
        return {"ok": False, "error": "import_failed", "detail": str(e)[:300]}, 500
    if not database_id:
        name = str(body.get("database_name", "") or "").strip()[:200]
        if not name:
            # имя базы по умолчанию — из имени файла
            name = filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1][:200] or "База"
        database_id = db.insert("databases", {"name": name, "filename": filename[-300:],
                                              "notes": str(body.get("notes", ""))[:500],
                                              "created": now_iso(), "updated": now_iso()})
    try:
        res = importers_mod.import_parsed(*parsed, database_id=database_id)
    except Exception as e:
        return {"ok": False, "error": "import_failed", "detail": str(e)[:300]}, 500
    res["ok"] = True
    res["database_id"] = database_id
    row = db.fetch1("SELECT * FROM databases WHERE id=?", (database_id,))
    res["database"] = dict(row) if row else None
    return res, 200


def _database_save(body):
    name = str(body.get("name", "") or "").strip()[:200]
    if not name:
        return {"ok": False, "error": "name_required"}, 400
    did = body.get("id")
    data = {"name": name, "filename": str(body.get("filename", ""))[:300],
            "notes": str(body.get("notes", ""))[:500], "updated": now_iso()}
    if did:
        try:
            db.update("databases", data, "id=?", (int(did),))
        except Exception as e:
            return {"ok": False, "error": str(e)}, 400
        return {"ok": True, "id": int(did)}, 200
    data["created"] = now_iso()
    try:
        new_id = db.insert("databases", data)
    except Exception as e:
        return {"ok": False, "error": str(e)}, 400
    return {"ok": True, "id": new_id}, 200


def _contact_history(cid):
    """Карточка номера: контакт + динамика (звонки и элементы кампаний)."""
    c = db.fetch1("SELECT c.*, d.name AS db_name FROM contacts c "
                  "LEFT JOIN databases d ON d.id=c.database_id WHERE c.id=?", (cid,))
    if not c:
        return None
    calls = db.fetch("SELECT cl.*, camp.name AS camp_name FROM calls cl "
                     "LEFT JOIN campaigns camp ON camp.id=cl.campaign_id "
                     "WHERE cl.contact_id=? OR cl.contact_phone=? "
                     "ORDER BY cl.id DESC LIMIT 200", (cid, c["phone"]))
    items = db.fetch("SELECT ci.*, camp.name AS camp_name FROM campaign_items ci "
                     "LEFT JOIN campaigns camp ON camp.id=ci.campaign_id "
                     "WHERE ci.contact_id=? ORDER BY ci.id DESC LIMIT 200", (cid,))
    done_calls = [x for x in calls if x.get("ended_at")]
    last = done_calls[0] if done_calls else (calls[0] if calls else None)
    return {"contact": c, "calls": calls, "items": items,
            "stats": {"calls_total": len(calls),
                      "calls_done": len(done_calls),
                      "last_result": (last or {}).get("result", ""),
                      "last_at": (last or {}).get("ended_at") or (last or {}).get("started_at", "")}}


def _truthy(v):
    if isinstance(v, bool):
        return v
    return str(v or "").strip().lower() in ("1", "да", "yes", "true", "+")


# ---------------- пользователи и операторы (админ) ----------------
def _health():
    """Публичный health (§52 ТЗ): только флаги, никаких секретов."""
    from .providers.base import resolve_secret
    db_ok = False
    try:
        db.fetch1("SELECT 1")
        db_ok = True
    except Exception:
        pass
    prov = getattr(ENGINE, "provider", None) if ENGINE else None
    pname = getattr(prov, "name", "") if prov is not None else ""
    connected = False
    if prov is not None:
        if pname == "ami":
            try:
                connected = bool(getattr(getattr(prov, "client", None),
                                         "connected", False))
            except Exception:
                connected = False
        else:
            # sim/uis/megafon_vats: persistent-соединения нет (REST/эмуляция);
            # «подключён» = провайдер сконфигурирован. Живость API ВАТС —
            # отдельным POST /megafon/check (там реальные чтения).
            connected = True
    s = {}
    try:
        s = db.get_settings()
    except Exception:
        pass
    wh_megafon = bool(resolve_secret(s.get("megafon_vats") or {}, "crm_token",
                                     "crm_token_env", "ATS_MEGAFON_CRM_TOKEN"))
    wh_uis = bool((s.get("uis") or {}).get("webhook_secret", ""))
    eng_ok = ENGINE is not None
    return {"ok": bool(db_ok and eng_ok), "db": db_ok, "engine": eng_ok,
            "telephony": {"provider": pname, "connected": connected},
            "webhook": bool(wh_megafon or wh_uis), "timestamp": now_iso()}


def _health_details():
    """Расширенный health для админа: маскированный конфиг, счётчики."""
    h = _health()
    s = db.get_settings()
    masked = _mask_secrets({k: s.get(k) for k in ("megafon_vats", "uis", "ami")})
    today = now_iso()[:10]
    try:
        calls_today = db.fetch1(
            "SELECT COUNT(*) c FROM calls WHERE started_at>=?",
            (today + " 00:00:00",))["c"]
        items_queued = db.fetch1(
            "SELECT COUNT(*) c FROM campaign_items WHERE status='queued'")["c"]
        ops_free = db.fetch1(
            "SELECT COUNT(*) c FROM operators WHERE status='free'")["c"]
        pool_total = db.fetch1(
            "SELECT COUNT(*) c FROM numbers WHERE provider='megafon_vats'")["c"]
        pool_usable = db.fetch1(
            "SELECT COUNT(*) c FROM numbers WHERE provider='megafon_vats' "
            "AND active=1 AND quarantined=0 AND enabled_outgoing=1")["c"]
        vats_users = db.fetch1("SELECT COUNT(*) c FROM vats_users")["c"]
        vats_groups = db.fetch1("SELECT COUNT(*) c FROM vats_groups")["c"]
    except Exception:
        calls_today = items_queued = ops_free = -1
        pool_total = pool_usable = vats_users = vats_groups = -1
    thread_alive = bool(ENGINE is not None and ENGINE.is_running())
    return {"health": h, "engine_thread": thread_alive,
            "provider_config": masked,
            "webhooks": {"megafon": bool(h["webhook"] and
                                         (masked.get("megafon_vats") or {}).get(
                                             "crm_token")),
                         "uis": bool((s.get("uis") or {}).get("webhook_secret"))},
            "stats": {"calls_today": calls_today, "items_queued": items_queued,
                      "operators_free": ops_free,
                      "pool_megafon": {"total": pool_total, "usable": pool_usable},
                      "vats_users": vats_users, "vats_groups": vats_groups},
            "settings": {k: s.get(k) for k in
                         ("provider", "max_channels", "retry_max",
                          "window_start", "window_end")}}


def _megafon_check_route():
    from .providers.base import ProviderApiError
    from .telephony import ProviderNotConfigured
    try:
        rep = ENGINE.megafon_check()
    except ProviderNotConfigured as e:
        return {"ok": False, "error": "vats_not_configured",
                "detail": str(e)[:300]}, 400
    except ProviderApiError as e:
        return {"ok": False, "error": "vats_error",
                "detail": str(e)[:300]}, 502
    if rep.get("ok"):
        return {"ok": True, "report": rep}, 200
    return {"ok": False, "error": "vats_error", "report": rep}, 502


def _megafon_simulate_route(body):
    """Локальная симуляция вебхука ВАТС (admin, БЕЗ crm_token): прогнать
    входящий/историю через движок без живой ВАТС. Только для стенда/отладки."""
    from .providers.megafon_vats import (map_megafon_webhook,
                                         webhook_fingerprint)
    ev = map_megafon_webhook(body or {})
    if not ev:
        return {"ok": False, "error": "unrecognized"}, 422
    ev["fingerprint"] = webhook_fingerprint(ev)
    ENGINE.push_event(ev)
    return {"ok": True, "event": {k: v for k, v in ev.items() if k != "raw"}}, 200


def _megafon_sync_route(kind, body):
    from .providers.base import ProviderApiError
    from .telephony import ProviderNotConfigured
    try:
        if kind == "pool":
            rep = ENGINE.megafon_pool_sync(dry_run=bool((body or {}).get("dry_run")))
        elif kind == "users":
            rep = ENGINE.megafon_users_sync()
        else:
            rep = ENGINE.megafon_groups_sync()
        return {"ok": True, "report": rep}, 200
    except ProviderNotConfigured as e:
        return {"ok": False, "error": "vats_not_configured",
                "detail": str(e)[:300]}, 400
    except ProviderApiError as e:
        return {"ok": False, "error": "vats_error", "detail": str(e)[:300]}, 502


def _users_list():
    return db.fetch(
        "SELECT u.id, u.login, u.role, u.active, u.created,"
        " o.id AS op_id, o.name AS op_name, o.ext, o.status AS op_status, "
        "o.vats_login AS op_vats"
        " FROM users u LEFT JOIN operators o ON o.user_id=u.id ORDER BY u.id")


def _count_admins():
    return db.fetch1("SELECT COUNT(*) c FROM users WHERE role='admin' AND active=1")["c"]


def _user_save(body, sess):
    uid = int(body.get("id") or 0)
    # Точечное обновление без смены логина/роли (переключатель «доступ»)
    if uid and "login" not in body:
        u = db.fetch1("SELECT * FROM users WHERE id=?", (uid,))
        if not u:
            return {"ok": False, "error": "user_not_found"}, 404
        active = 1 if body.get("active", True) not in (0, "0", False) else 0
        if active == 0:
            me = db.fetch1("SELECT id FROM users WHERE login=?", (sess["login"],))
            if me and me["id"] == uid:
                return {"ok": False, "error": "cannot_disable_self"}, 400
            if u["role"] == "admin" and _count_admins() <= 1:
                return {"ok": False, "error": "last_admin"}, 400
        db.q("UPDATE users SET active=? WHERE id=?", (active, uid))
        if not active:
            security.drop_sessions_for(u["login"])
        return {"ok": True, "id": uid}, 200
    login = str(body.get("login") or "").strip().lower()
    role = str(body.get("role") or "operator")
    name = str(body.get("name") or "").strip()[:200] or login
    ext = str(body.get("ext") or "").strip()[:30]
    vats_login = str(body.get("vats_login") or "").strip()[:64]
    password = str(body.get("password") or "")
    active = 1 if body.get("active", True) not in (0, "0", False) else 0
    if role not in ("admin", "operator"):
        role = "operator"
    if not re.fullmatch(r"[a-z0-9_.\-]{2,32}", login):
        return {"ok": False, "error": "bad_login"}, 400
    if password and len(password) < 6:
        return {"ok": False, "error": "password_short"}, 400

    me = db.fetch1("SELECT id, role FROM users WHERE login=?", (sess["login"],))
    me_id = me["id"] if me else None

    if uid:  # редактирование
        u = db.fetch1("SELECT * FROM users WHERE id=?", (uid,))
        if not u:
            return {"ok": False, "error": "user_not_found"}, 404
        if uid == me_id and (role != "admin" or active == 0):
            return {"ok": False, "error": "cannot_disable_self"}, 400
        other = db.fetch1("SELECT id FROM users WHERE login=? AND id<>?", (login, uid))
        if other:
            return {"ok": False, "error": "login_exists"}, 400
        if u["role"] == "admin" and (role != "admin" or active == 0) and _count_admins() <= 1:
            return {"ok": False, "error": "last_admin"}, 400
        db.q("UPDATE users SET login=?, role=?, active=? WHERE id=?", (login, role, active, uid))
        if password:
            salt = security.new_salt()
            db.q("UPDATE users SET salt=?, password_hash=? WHERE id=?",
                 (salt, security.hash_password(password, salt), uid))
        # синхронизация записи оператора (для ACD/статусов)
        if role == "operator":
            op = db.fetch1("SELECT id FROM operators WHERE user_id=?", (uid,))
            if op:
                if "vats_login" in body:
                    db.q("UPDATE operators SET name=?, ext=?, vats_login=? WHERE id=?",
                         (name, ext, vats_login, op["id"]))
                else:
                    db.q("UPDATE operators SET name=?, ext=? WHERE id=?",
                         (name, ext, op["id"]))
            else:
                db.insert("operators", {"user_id": uid, "name": name, "ext": ext,
                                        "vats_login": vats_login,
                                        "status": "offline", "updated": now_iso()})
        else:
            db.q("DELETE FROM operators WHERE user_id=?", (uid,))
        # если сменили пароль — завершаем старые сессии (текущая админа не трогаем)
        if password and uid != me_id:
            security.drop_sessions_for(u["login"])
            if u["login"] != login:
                security.drop_sessions_for(login)
        return {"ok": True, "id": uid}, 200

    # создание
    if db.fetch1("SELECT id FROM users WHERE login=?", (login,)):
        return {"ok": False, "error": "login_exists"}, 400
    if not password:
        return {"ok": False, "error": "password_required"}, 400
    salt = security.new_salt()
    nid = db.insert("users", {"login": login, "role": role, "salt": salt,
                              "password_hash": security.hash_password(password, salt),
                              "active": active, "created": now_iso()})
    if role == "operator":
        db.insert("operators", {"user_id": nid, "name": name, "ext": ext,
                                "vats_login": vats_login,
                                "status": "offline", "updated": now_iso()})
    return {"ok": True, "id": nid}, 200


def _user_delete(body, sess):
    uid = int(body.get("id") or 0)
    u = db.fetch1("SELECT * FROM users WHERE id=?", (uid,))
    if not u:
        return {"ok": False, "error": "user_not_found"}, 404
    me = db.fetch1("SELECT id FROM users WHERE login=?", (sess["login"],))
    if me and me["id"] == uid:
        return {"ok": False, "error": "cannot_delete_self"}, 400
    if u["role"] == "admin" and _count_admins() <= 1:
        return {"ok": False, "error": "last_admin"}, 400
    db.q("DELETE FROM operators WHERE user_id=?", (uid,))
    db.q("DELETE FROM users WHERE id=?", (uid,))
    security.drop_sessions_for(u["login"])
    return OK, 200


def _number_save(body):
    nid = body.get("id")
    if nid:
        numbers_mod.update_number(int(nid), body)
        return OK, 200
    ok, err = numbers_mod.add_number(body.get("number", ""), body.get("label", ""),
                                     body.get("kind", "mobile"), body.get("provider", "sim"),
                                     body.get("daily_limit", 100))
    if err == "duplicate":
        return {"ok": False, "error": "number_exists"}, 400
    if not ok:
        return {"ok": False, "error": err or "save_failed"}, 400
    return OK, 200


def _campaign_save(body):
    cid = body.get("id")
    schedule = body.get("schedule") or {}
    sc = json.dumps(schedule, ensure_ascii=False)
    data = {"name": str(body.get("name", "Кампания"))[:200],
            "template_id": int(body.get("template_id", 1) or 1),
            "flow": body.get("flow", "agent") if body.get("flow") in ("message", "agent", "operator") else "agent",
            "schedule": sc,
            "max_channels": int(body.get("max_channels", 0) or 0),
            "retry_max": int(body.get("retry_max", -1) or -1),
            "retry_delay_min": int(body.get("retry_delay_min", -1) or -1),
            "connect_on_qualify": 1 if body.get("connect_on_qualify", True) else 0,
            "retry_map": json.dumps(body.get("retry_map") or {}, ensure_ascii=False),
            "updated": now_iso()}
    if cid:
        db.update("campaigns", data, "id=?", (int(cid),))
        return {"ok": True, "id": int(cid)}, 200
    cid2 = db.insert("campaigns", {**data, "status": "stopped", "created": now_iso()})
    return {"ok": True, "id": cid2}, 200


def _campaign_add_contacts(cid, body):
    contact_ids = body.get("contact_ids")
    grp_filter = body.get("group")
    consent_only = bool(body.get("consent_only", True))
    if not contact_ids:
        sql = "SELECT * FROM contacts WHERE 1=1"
        params = []
        if grp_filter:
            sql += " AND grp=?"
            params.append(grp_filter)
        if consent_only:
            sql += " AND consent=1"
        if body.get("with_consent") is not None and not body.get("with_consent"):
            sql += " AND consent=0"
        rows = db.fetch(sql, params)
        contact_ids = [c["id"] for c in rows]
    items = db.fetch("SELECT * FROM campaign_items WHERE campaign_id=?", (cid,))
    existing_contacts = {i["contact_id"] for i in items if i["status"] not in ("canceled",)}
    added = 0
    for cid_c in contact_ids:
        try:
            cid_c = int(cid_c)
        except Exception:
            continue
        c = db.fetch1("SELECT * FROM contacts WHERE id=?", (cid_c,))
        if not c or c["id"] in existing_contacts:
            continue
        db.insert("campaign_items", {"campaign_id": cid, "contact_id": c["id"],
                                     "contact_name": c.get("name", ""), "contact_phone": c.get("phone", ""),
                                     "status": "queued", "attempts": 0, "next_attempt_at": "",
                                     "last_result": "", "created": now_iso(), "updated": now_iso(),
                                     "completed_at": ""})
        existing_contacts.add(c["id"])
        added += 1
    return {"ok": True, "added": added}, 200


def _template_save(body):
    tid = body.get("id")
    scenario = body.get("scenario")
    if scenario and not isinstance(scenario, str):
        scenario = json.dumps(scenario, ensure_ascii=False)
    data = {"name": str(body.get("name", "Шаблон"))[:200], "text": str(body.get("text", ""))[:4000],
            "scenario": scenario or json.dumps(db.DEFAULT_SCENARIO, ensure_ascii=False),
            "active": 1 if body.get("active", True) else 0, "updated": now_iso()}
    if tid:
        db.update("templates", data, "id=?", (int(tid),))
        return {"ok": True, "id": int(tid)}, 200
    nid = db.insert("templates", {**data, "active": data["active"]})
    return {"ok": True, "id": nid}, 200


def _blacklist_add(body):
    phone = _clean_phone(body.get("phone"))
    if not phone:
        return {"ok": False, "error": "phone_required"}, 400
    try:
        db.insert("blacklist", {"phone": phone, "reason": str(body.get("reason", ""))[:200],
                                "source": str(body.get("source", "manual"))[:50], "created": now_iso()})
    except Exception:
        return {"ok": True, "note": "already"}, 200
    db.q("UPDATE contacts SET blacklisted=1, updated=? WHERE phone=?", (now_iso(), phone))
    # снимаем с очередей
    items = db.fetch("SELECT ci.id FROM campaign_items ci JOIN contacts c ON c.id=ci.contact_id"
                     " WHERE c.phone=? AND ci.status IN ('queued')", (phone,))
    for i in items:
        db.q("UPDATE campaign_items SET status='blacklisted', completed_at=? WHERE id=?", (now_iso(), i["id"]))
    return OK, 200


def _complaint(body):
    """Абонент пожаловался/попросил не звонить: чёрный список + счётчик жалоб + карантин номера."""
    phone = _clean_phone(body.get("phone"))
    if phone:
        db.q("UPDATE contacts SET complaints=complaints+1, updated=? WHERE phone=?", (now_iso(), phone))
        try:
            db.insert("blacklist", {"phone": phone, "reason": "жалоба/отказ",
                                    "source": str(body.get("source", "operator"))[:50], "created": now_iso()})
        except Exception:
            pass
        db.q("UPDATE contacts SET blacklisted=1, updated=? WHERE phone=?", (now_iso(), phone))
        last = db.fetch1("SELECT * FROM calls WHERE contact_phone=? AND ended_at<>'' ORDER BY id DESC LIMIT 1", (phone,))
        if last and last["number_id"]:
            st = db.get_settings()
            thr = int(st.get("auto_quarantine_on_complaints", 3))
            # считаем жалобы на номер за всё время
            cnt = db.fetch1("SELECT COUNT(*) c FROM calls c JOIN contacts x ON x.phone=c.contact_phone"
                            " WHERE c.number_id=? AND x.complaints>0", (last["number_id"],))
            if cnt and cnt["c"] >= thr:
                numbers_mod.quarantine(last["number_id"], True)
    return OK, 200


def _settings_save(body, sess=None):
    s = db.get_settings()
    if "provider" in body:
        pv = str(body.get("provider") or "").strip().lower()
        if pv not in ("", "sim", "uis", "ami", "megafon_vats"):
            return {"ok": False, "error": "bad_provider"}, 400
        s["provider"] = pv  # "" = не настроен (fail-closed до явного выбора)
    for k in ("max_channels", "consent_required", "retry_max", "retry_delay_min",
              "line_cooldown_sec", "watchdog_timeout_min", "acd_wait_timeout_sec",
              "vats_conversation_timeout_min",
              "window_start", "window_end", "sim_answer", "auto_quarantine_on_complaints",
              "sim_outcome", "crm"):
        if k in body:
            if k in ("consent_required",):
                s[k] = bool(body[k])
            elif k == "sim_outcome" and isinstance(body[k], dict):
                s[k] = {str(x): int(body[k][x]) for x in body[k]}
            elif k in ("crm", "uis", "ami", "llm", "bitrix24") and isinstance(body[k], dict):
                s[k] = {**s.get(k, {}), **body[k]}
            else:
                s[k] = body[k]
    if body.get("window_days") is not None:
        s["window_days"] = [int(d) for d in body["window_days"]]
    # секции секретов не меняем через UI (кроме меток)
    if body.get("new_password"):
        if len(str(body["new_password"])) < 6:
            return {"ok": False, "error": "password_short"}, 400
        login = (sess or {}).get("login") or "admin"
        salt = security.new_salt()
        db.q("UPDATE users SET salt=?, password_hash=? WHERE login=?",
             (salt, security.hash_password(body["new_password"], salt), login))
        if login == "admin":
            db.drop_initial_credentials()  # стартовый пароль из файла недействителен
    db.save_settings(s)
    return OK, 200


def _sim_script(body):
    phone = _clean_phone(body.get("phone"))
    if not phone:
        return {"ok": False, "error": "phone_required"}, 400
    p = ENGINE.provider
    if p.name != "sim":
        return {"ok": False, "error": "provider_not_sim"}, 400
    if body.get("outcome"):
        p.set_outcome(phone, str(body["outcome"]))
    if body.get("answer"):
        p.set_answer(phone, str(body["answer"]))
    return OK, 200


def _own_operator(sess):
    """Оператор, привязанный к залогиненному пользователю (users.login -> operators.user_id)."""
    u = db.fetch1("SELECT id FROM users WHERE login=?", (sess.get("login"),))
    if not u:
        return None
    return db.fetch1("SELECT * FROM operators WHERE user_id=?", (u["id"],))


def _operator_status(body, sess):
    op = _own_operator(sess)
    if not op:
        return {"ok": False, "error": "operator_not_found"}, 404
    ENGINE.operator_status(op["id"], body.get("status", "free"))
    return OK, 200


def _acd_accept(body, sess=None):
    acd_id = int(body.get("id", 0))
    if sess is not None and sess.get("role") != "admin":
        # Оператор принимает звонок только на себя: чужой operator_id из тела
        # игнорируется (иначе можно вешать звонки на коллег).
        op = _own_operator(sess)
        if not op:
            return {"ok": False, "error": "operator_not_found"}, 404
    else:
        uid = body.get("operator_id")
        op = None
        if uid:
            op = db.fetch1("SELECT * FROM operators WHERE id=?", (int(uid),))
        if not op and uid is None:
            op = db.fetch1("SELECT * FROM operators WHERE status='free' ORDER BY id LIMIT 1")
        if not op:
            # Production: виртуальных операторов не создаём — звонок ждёт свободного.
            return {"ok": False, "error": "no_free_operator"}, 400
    ok, err = ENGINE.accept_acd(acd_id, op["id"])
    if not ok:
        return {"ok": False, "error": err}, 400
    return {"ok": True, "operator": op["name"], "operator_id": op["id"]}, 200


def _call_complete(body, sess=None):
    call_id = int(body.get("call_id", 0))
    op_id = int(body.get("operator_id", 0))
    if sess is not None and sess.get("role") != "admin":
        # Завершать можно только свой принятый вызов.
        me = _own_operator(sess)
        if not me:
            return {"ok": False, "error": "operator_not_found"}, 404
        mine = db.fetch1("SELECT id FROM acd WHERE call_id=? AND operator_id=? AND status IN ('accepted','bridged')",
                         (call_id, me["id"]))
        if not mine:
            return {"ok": False, "error": "not_your_call"}, 403
        op_id = me["id"]
    ok, err = ENGINE.complete_operator_call(call_id, op_id or None)
    return (OK, 200) if ok else ({"ok": False, "error": err}, 400)
# ---------------- отчёты и экспорт журнала ----------------
_ISO_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _reports(d_from=None, d_to=None):
    """Отчёт за период. Если d_from/d_to не заданы — данные за сегодня + тренд 7 дней.

    d_from..d_to — 'YYYY-MM-DD', диапазон до 366 дней. Возвращает None при неверных датах.
    """
    from datetime import date, timedelta
    today_d = date.today()

    def _d(x):
        if not x:
            return None
        if not isinstance(x, str) or not _ISO_DAY.match(x):
            return None
        try:
            return date.fromisoformat(x)
        except Exception:
            return None

    a, b = _d(d_from), _d(d_to)
    if (d_from and not a) or (d_to and not b):
        return None
    if a and b and a > b:
        return None
    if a and not b:
        b = today_d
    if (a or b) and b and (b - (a or b)).days > 366:
        return None

    custom = bool(a or b)
    start = a or today_d
    end = b or today_d
    start_s = start.isoformat() + " 00:00:00"
    end_s = (end + timedelta(days=1)).isoformat() + " 00:00:00"

    OK_RES = ("done_ok", "operator_ok", "done_agent")
    BAD_RES = ("busy", "no_answer", "machine", "failed", "blocked")
    pl = ",".join("?" * len(OK_RES))
    pb = ",".join("?" * len(BAD_RES))
    where_period = "started_at>=? AND started_at<?"

    totals = db.fetch(
        "SELECT COUNT(*) n, SUM(CASE WHEN result IN ({}) THEN 1 ELSE 0 END) ok,"
        " COALESCE(SUM(duration_sec),0) dur FROM calls WHERE {}".format(pl, where_period),
        [*OK_RES, start_s, end_s])
    n = (totals[0]["n"] or 0) if totals else 0
    ok = (totals[0]["ok"] or 0) if totals else 0
    dur = (totals[0]["dur"] or 0) if totals else 0
    by_result = db.fetch(
        "SELECT result, COUNT(*) c FROM calls WHERE {} GROUP BY result ORDER BY c DESC".format(where_period),
        (start_s, end_s))
    by_campaign = db.fetch(
        "SELECT c.campaign_id, COALESCE(camp.name,'(удалена)') name, COUNT(*) n,"
        " SUM(CASE WHEN c.result IN ({}) THEN 1 ELSE 0 END) ok,"
        " SUM(CASE WHEN c.result IN ({}) THEN 1 ELSE 0 END) bad,"
        " COALESCE(SUM(c.duration_sec),0) dur"
        " FROM calls c LEFT JOIN campaigns camp ON camp.id=c.campaign_id"
        " WHERE {} GROUP BY c.campaign_id ORDER BY n DESC LIMIT 25".format(pl, pb, where_period),
        [*OK_RES, *BAD_RES, start_s, end_s])
    by_number = db.fetch(
        "SELECT c.caller_id, c.number_id, COUNT(*) n,"
        " SUM(CASE WHEN c.result IN ({}) THEN 1 ELSE 0 END) ok"
        " FROM calls c WHERE {} GROUP BY c.caller_id ORDER BY n DESC LIMIT 25".format(pl, where_period),
        [*OK_RES, start_s, end_s])

    # Тренд: по дням (до 31 точки), дальше — по неделям
    span_days = max(1, (end - start).days + 1)
    trend = []
    if custom and span_days > 31:
        cur = start
        while cur <= end:
            wk_end = min(end, cur + timedelta(days=6))
            r = db.fetch("SELECT COUNT(*) n, SUM(CASE WHEN result IN ({}) THEN 1 ELSE 0 END) ok"
                         " FROM calls WHERE started_at>=? AND started_at<?".format(pl),
                         [*OK_RES, cur.isoformat() + " 00:00:00",
                          (wk_end + timedelta(days=1)).isoformat() + " 00:00:00"])
            trend.append({"date": cur.isoformat(), "label": "{}–{}".format(
                cur.strftime("%d.%m"), wk_end.strftime("%d.%m")),
                "n": (r[0]["n"] or 0) if r else 0, "ok": (r[0]["ok"] or 0) if r else 0})
            cur = wk_end + timedelta(days=1)
    else:
        days = []
        if custom:
            days = [start + timedelta(days=i) for i in range(span_days)]
        else:
            days = [today_d - timedelta(days=i) for i in range(6, -1, -1)]
        for day in days:
            ds = day.isoformat()
            r = db.fetch("SELECT COUNT(*) n, SUM(CASE WHEN result IN ({}) THEN 1 ELSE 0 END) ok"
                         " FROM calls WHERE started_at>=? AND started_at<?".format(pl),
                         [*OK_RES, ds + " 00:00:00", (day + timedelta(days=1)).isoformat() + " 00:00:00"])
            trend.append({"date": ds, "label": day.strftime("%d.%m"), "n": (r[0]["n"] or 0) if r else 0,
                          "ok": (r[0]["ok"] or 0) if r else 0})

    return {
        "period": {"from": start.isoformat(), "to": end.isoformat(), "custom": custom,
                   "label": "сегодня" if (not custom and start == end == today_d)
                   else (start.isoformat() + " – " + end.isoformat())},
        "today": {"n": n, "ok": ok, "ok_rate": round(100.0 * ok / n, 1) if n else 0.0,
                  "avg_dur": round(dur / n, 1) if n else 0.0},
        "by_result": [{"result": r["result"], "c": r["c"]} for r in by_result],
        "by_campaign": by_campaign,
        "by_number": by_number,
        "trend": trend,
    }


def _export_calls():
    rows = db.fetch("SELECT * FROM calls ORDER BY id DESC LIMIT 5000")
    out = io.StringIO()
    w = csv.writer(out, delimiter=";")
    w.writerow(["id", "started_at", "ended_at", "name", "phone", "caller_id", "campaign_id",
                "direction", "result", "detail", "duration_sec", "recording"])
    for x in rows:
        w.writerow([x["id"], x["started_at"], x["ended_at"], x["contact_name"], x["contact_phone"],
                    x["caller_id"], x["campaign_id"], x["direction"], x["result"], x["detail"],
                    x["duration_sec"], x["recording"]])
    raw = ("\ufeff" + out.getvalue()).encode("utf-8")
    return raw, 200


# ---------------- записи разговоров ----------------
AUDIO_EXT = {".mp3": "audio/mpeg", ".wav": "audio/wav", ".ogg": "audio/ogg", ".opus": "audio/ogg",
             ".m4a": "audio/mp4", ".aac": "audio/aac", ".flac": "audio/flac", ".oga": "audio/ogg"}


def _recording_upload(body):
    """Загрузить файл записи разговора и привязать к звонку (admin). Тело JSON:
    {call_id, filename, data_b64}. Файл сохраняется в data_v2/recordings/."""
    import os
    call_id = int(body.get("call_id", 0) or 0)
    fname = str(body.get("filename", "")).replace("\\", "/").split("/")[-1][-80:]
    ext = os.path.splitext(fname)[1].lower()
    if not call_id:
        return {"ok": False, "error": "call_id_required"}, 400
    if ext not in AUDIO_EXT:
        return {"ok": False, "error": "unsupported_audio"}, 400
    call = db.fetch1("SELECT * FROM calls WHERE id=?", (call_id,))
    if not call:
        return {"ok": False, "error": "call_not_found"}, 404
    try:
        raw = base64.b64decode(body.get("data_b64", "") or "")
    except Exception:
        return {"ok": False, "error": "bad_base64"}, 400
    if not raw or len(raw) > 50 * 1024 * 1024:
        return {"ok": False, "error": "empty_or_too_large"}, 400
    rec_dir = config.REC_DIR
    rec_dir.mkdir(parents=True, exist_ok=True)
    safe_name = "call_{}_{}".format(call_id, fname)
    (rec_dir / safe_name).write_bytes(raw)
    db.q("UPDATE calls SET recording=? WHERE id=?", (safe_name, call_id))
    return {"ok": True, "recording": safe_name}, 200
