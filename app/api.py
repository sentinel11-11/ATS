# -*- coding: utf-8 -*-
"""HTTP API v2 ATS (замена открытых эндпоинтов server.py). Авторизация: X-ATS-Token."""
import csv
import io
import json
import re
import uuid
from datetime import datetime
from urllib.parse import urlparse

from . import agent as agent_mod
from . import config, db, events, numbers as numbers_mod, security

ENGINE = None  # устанавливается при старте (см. run.py)

OK = {"ok": True}


def json_bytes(value):
    return json.dumps(value, ensure_ascii=False).encode("utf-8")


def now_iso():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def need_auth(headers, role=None):
    tok = headers.get("X-Ats-Token") or headers.get("X-Admin-Token") or ""
    s = security.get_session(tok)
    if not s:
        return None, {"ok": False, "error": "auth_required"}, 401
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
    # ---------- auth ----------
    if p == ["auth", "login"]:
        login = str(body.get("login", ""))
        password = str(body.get("password", ""))
        user = db.fetch1("SELECT * FROM users WHERE login=?", (login,))
        if user and security.hash_password(password, user["salt"]) == user["password_hash"]:
            tok = security.create_token(user["login"], user["role"])
            return {"ok": True, "token": tok, "role": user["role"]}, 200
        return {"ok": False, "error": "bad_login"}, 403
    if p == ["auth", "logout"]:
        security.drop_token(headers.get("X-Ats-Token", ""))
        return OK, 200
    if p == ["webhooks", "uis"] and method == "POST":
        s = db.get_settings()
        secret = (s.get("uis") or {}).get("webhook_secret", "")
        if secret and headers.get("X-UIS-Secret", "") != secret:
            return {"ok": False, "error": "bad_secret"}, 403
        from .telephony import map_uis_webhook
        ev = map_uis_webhook(body)
        if not ev:
            return {"ok": False, "error": "unrecognized"}, 422
        try:
            ENGINE.handle_event(ev)
            return OK, 200
        except Exception as e:
            return {"ok": False, "error": str(e)[:200]}, 500
    if p == ["auth", "me"]:
        sess2, err2, code2 = need_auth(headers)
        if err2:
            return err2, code2
        return {"ok": True, "login": sess2["login"], "role": sess2["role"]}, 200

    sess, err, code = need_auth(headers)
    if err:
        return err, code

    def admin_only():
        if sess["role"] != "admin":
            return {"ok": False, "error": "admin_required"}, 403
        return None

    # ---------- общедоступные чтения (обе роли) ----------
    if p == ["dashboard"]:
        return ENGINE.dashboard(), 200
    if p == ["contacts"]:
        return {"contacts": db.fetch("SELECT * FROM contacts ORDER BY id DESC LIMIT 5000")}, 200
    if p == ["numbers"]:
        return {"numbers": numbers_mod.pool_state()}, 200
    if p == ["campaigns"]:
        camps = db.fetch("SELECT * FROM campaigns ORDER BY id DESC")
        for c in camps:
            try:
                c["schedule"] = json.loads(c.get("schedule") or "{}")
            except Exception:
                c["schedule"] = {}
        tmpl = {t["id"]: t["name"] for t in db.fetch("SELECT id,name FROM templates")}
        for c in camps:
            c["template_name"] = tmpl.get(c["template_id"], "")
        return {"campaigns": camps}, 200
    if p and p[0] == "campaigns" and len(p) == 2 and p[1].isdigit():
        cid = int(p[1])
        camp = db.fetch1("SELECT * FROM campaigns WHERE id=?", (cid,))
        if camp:
            try:
                camp["schedule"] = json.loads(camp.get("schedule") or "{}")
            except Exception:
                camp["schedule"] = {}
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
    if p == ["blacklist"]:
        return {"blacklist": db.fetch("SELECT * FROM blacklist ORDER BY id DESC")}, 200
    if p == ["settings"]:
        return {"settings": db.public_settings()}, 200
    if p == ["settings", "raw"] and method == "GET":
        if sess["role"] != "admin":
            return {"ok": False, "error": "admin_required"}, 403
        s = db.get_settings()
        return {"provider_config": {k: s.get(k) for k in ("uis", "ami", "llm", "bitrix24", "crm")}}, 200
    if p == ["settings", "raw"] and method == "POST":
        if sess["role"] != "admin":
            return {"ok": False, "error": "admin_required"}, 403
        s = db.get_settings()
        cfg = body.get("provider_config") or body
        for k in ("uis", "ami", "llm", "bitrix24", "crm"):
            if isinstance(cfg.get(k), dict):
                s[k] = cfg[k]
        db.save_settings(s)
        return OK, 200
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

    if sess["role"] != "admin":
        # разрешённые операторские действия
        if p == ["operators", "status"] and method == "POST":
            return _operator_status(body, sess)
        if p == ["acd", "accept"] and method == "POST":
            return _acd_accept(body)
        if p == ["calls", "complete"] and method == "POST":
            return _call_complete(body)
        if p == ["contacts"] and method == "POST":
            return _contact_save(body)
        return {"ok": False, "error": "admin_required"}, 403

    # ================= администратор =================
    # контакты
    if p == ["contacts"] and method == "POST":
        return _contact_save(body)
    if p == ["contacts", "delete"]:
        ids = [str(x) for x in body.get("ids", [])]
        if ids:
            db.q("DELETE FROM contacts WHERE id IN ({})".format(",".join("?" * len(ids))), ids)
        return OK, 200
    if p == ["contacts", "import"]:
        return _contacts_import(body)
    # номера
    if p == ["numbers", "save"]:
        return _number_save(body)
    if p == ["numbers", "quarantine"]:
        numbers_mod.quarantine(int(body.get("id", 0)), bool(body.get("on", True)))
        return OK, 200
    if p == ["numbers", "reset"]:
        for n in numbers_mod.list_numbers(include_disabled=True):
            numbers_mod.update_number(n["id"], {"quarantined": False, "daily_count": 0, "cooldown_until": ""})
        return OK, 200
    # кампании
    if p == ["campaigns", "save"]:
        return _campaign_save(body)
    if p and p[0] == "campaigns" and len(p) == 2 and p[1].isdigit() and p[2:] == []:
        return {"campaign": db.fetch1("SELECT * FROM campaigns WHERE id=?", (int(p[1]),))}, 200
    if p and len(p) == 3 and p[0] == "campaigns":
        cid = int(p[1])
        act = p[2]
        if act == "start":
            db.q("UPDATE campaigns SET status='running', updated=? WHERE id=?", (now_iso(), cid))
            db.q("UPDATE campaign_items SET status='queued', next_attempt_at='' WHERE campaign_id=? AND status IN ('queued','exhausted','canceled')",
                 (cid,))
            events.publish("campaign", {"id": cid, "status": "running"})
            return OK, 200
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
            db.q("DELETE FROM campaign_items WHERE campaign_id=?", (cid,))
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
    # настройки / пароль
    if p == ["settings", "save"]:
        return _settings_save(body, sess)
    if p == ["sim", "script"]:
        return _sim_script(body)
    # операторы (админ тоже может)
    if p == ["operators", "status"]:
        return _operator_status(body, sess)
    if p == ["acd", "accept"]:
        return _acd_accept(body)
    if p == ["calls", "complete"]:
        return _call_complete(body)
    if p == ["acd", "miss"] and method == "POST":
        acd_id = int(body.get("id", 0))
        db.q("UPDATE acd SET status='missed', updated=? WHERE id=?", (now_iso(), acd_id))
        return OK, 200
    return {"ok": False, "error": "not_found"}, 404


# ---------------- реализации ----------------
def _export_contacts():
    out = io.StringIO()
    w = csv.writer(out, delimiter=";")
    w.writerow(["id", "name", "phone", "group", "note", "consent", "blacklisted", "complaints"])
    for x in db.fetch("SELECT * FROM contacts"):
        w.writerow([x["id"], x["name"], x["phone"], x["grp"], x["note"],
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


def _contact_save(body):
    item = dict(body)
    item["phone"] = _clean_phone(item.get("phone"))
    if not item.get("phone"):
        return {"ok": False, "error": "phone_required"}, 400
    cid = item.get("id")
    data = {"name": str(item.get("name", ""))[:200], "phone": item["phone"],
            "grp": str(item.get("group", item.get("grp", "")))[:200],
            "note": str(item.get("note", ""))[:500],
            "consent": 1 if item.get("consent") else 0,
            "blacklisted": 1 if item.get("blacklisted") else 0,
            "updated": now_iso()}
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
    rows = body.get("rows", [])
    added = 0
    dup = 0
    for r in rows:
        phone = _clean_phone(r.get("phone"))
        if not phone:
            continue
        consent = 1 if _truthy(r.get("consent")) else 0
        existing = db.fetch1("SELECT id FROM contacts WHERE phone=?", (phone,))
        try:
            if existing:
                db.q("UPDATE contacts SET name=?, grp=?, note=?, consent=?, updated=? WHERE id=?",
                     (str(r.get("name", ""))[:200], str(r.get("group", r.get("grp", "")))[:200],
                      str(r.get("note", ""))[:500], consent, now_iso(), existing["id"]))
                dup += 1
            else:
                db.insert("contacts", {"name": str(r.get("name", ""))[:200], "phone": phone,
                                       "grp": str(r.get("group", r.get("grp", "")))[:200],
                                       "note": str(r.get("note", ""))[:500], "consent": consent,
                                       "consent_source": "import", "blacklisted": 0, "complaints": 0,
                                       "created": now_iso(), "updated": now_iso()})
                added += 1
        except Exception:
            continue
    return {"ok": True, "added": added, "updated": dup}, 200


def _truthy(v):
    if isinstance(v, bool):
        return v
    return str(v or "").strip().lower() in ("1", "да", "yes", "true", "+")


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
    for k in ("provider", "max_channels", "consent_required", "retry_max", "retry_delay_min",
              "line_cooldown_sec", "watchdog_timeout_min", "acd_wait_timeout_sec",
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
        login = (sess or {}).get("login") or "admin"
        salt = security.new_salt()
        db.q("UPDATE users SET salt=?, password_hash=? WHERE login=?",
             (salt, security.hash_password(body["new_password"], salt), login))
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


def _operator_status(body, sess):
    uid = db.fetch1("SELECT id FROM users WHERE login=?", (sess["login"],))["id"]
    op = db.fetch1("SELECT * FROM operators WHERE user_id=?", (uid,))
    if not op:
        return {"ok": False, "error": "operator_not_found"}, 404
    ENGINE.operator_status(op["id"], body.get("status", "free"))
    return OK, 200


def _acd_accept(body):
    acd_id = int(body.get("id", 0))
    uid = body.get("operator_id")
    op = None
    if uid:
        op = db.fetch1("SELECT * FROM operators WHERE id=?", (int(uid),))
    if not op and uid is None:
        op = db.fetch1("SELECT * FROM operators WHERE status IN ('free','offline') ORDER BY id LIMIT 1")
    if not op:
        # создать виртуального оператора (демо без регистрации операторов)
        op_id = db.insert("operators", {"user_id": 0, "name": "Оператор #{}".format(uuid.uuid4().hex[:4]),
                                        "ext": "", "status": "busy", "updated": now_iso()})
        op = db.fetch1("SELECT * FROM operators WHERE id=?", (op_id,))
    ok, err = ENGINE.accept_acd(acd_id, op["id"])
    if not ok:
        return {"ok": False, "error": err}, 400
    return {"ok": True, "operator": op["name"]}, 200


def _call_complete(body):
    call_id = int(body.get("call_id", 0))
    op_id = int(body.get("operator_id", 0))
    ok, err = ENGINE.complete_operator_call(call_id, op_id or None)
    return (OK, 200) if ok else ({"ok": False, "error": err}, 400)
