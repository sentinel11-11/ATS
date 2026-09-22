# -*- coding: utf-8 -*-
"""Административный аудит ATS.

Аудит намеренно отделён от прикладного журнала звонков: здесь хранятся
действия пользователей и ошибки API, а не содержимое разговоров. Любая ошибка
записи аудита не должна ломать исходную операцию.
"""
import json
import re
from datetime import datetime
from urllib.parse import urlparse

from . import db, security

MAX_DETAILS = 4000
SENSITIVE_KEYS = {
    "password", "password_hash", "salt", "token", "api_key", "apikey",
    "secret", "webhook_secret", "crm_token", "access_token", "authorization",
    "cookie", "raw_password", "new_password",
}


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _safe_value(value, depth=0):
    """Ограничивает audit details и удаляет секреты/слишком большие payloads."""
    if depth > 3:
        return "[truncated]"
    if isinstance(value, dict):
        out = {}
        for key, val in list(value.items())[:40]:
            key_text = str(key)
            if key_text.lower() in SENSITIVE_KEYS or any(
                    part in key_text.lower() for part in ("password", "token", "secret", "api_key")):
                out[key_text] = "[redacted]"
            else:
                out[key_text] = _safe_value(val, depth + 1)
        return out
    if isinstance(value, (list, tuple)):
        if len(value) > 20:
            return {"count": len(value), "value": "[list omitted]"}
        return [_safe_value(item, depth + 1) for item in value]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    text = str(value)
    return text if len(text) <= 500 else text[:497] + "..."


def _details(value):
    try:
        raw = json.dumps(_safe_value(value or {}), ensure_ascii=False, separators=(",", ":"))
    except Exception:
        raw = json.dumps({"value": "[unserializable]"})
    if len(raw) > MAX_DETAILS:
        raw = raw[:MAX_DETAILS - 20] + "...[truncated]"
    return raw


def actor_from_headers(headers):
    """Возвращает безопасный снимок текущего пользователя по X-Ats-Token."""
    token = (headers or {}).get("X-Ats-Token", "")
    if not token:
        return {}
    try:
        session = security.get_session(token)
        if not session:
            return {}
        user = db.fetch1("SELECT id, login, role FROM users WHERE login=?", (session["login"],))
        if not user:
            return {"login": session.get("login", ""), "role": session.get("role", "")}
        return {"user_id": user["id"], "login": user["login"], "role": user["role"]}
    except Exception:
        return {}


def _path_parts(path):
    return [part for part in urlparse(path or "").path.split("/")
            if part and part not in ("api", "v2")]


def _classification(method, path, status_code):
    parts = _path_parts(path)
    if not parts or parts[:1] == ["audit"]:
        return None
    root = parts[0]
    if root == "auth":
        event = "auth"
    elif root in ("settings", "users"):
        event = "settings"
    elif root in ("campaigns", "templates"):
        event = "campaign"
    elif root in ("contacts", "databases"):
        event = "contact"
    elif root in ("blacklist", "complaint"):
        event = "blacklist"
    elif root in ("operators", "acd", "calls"):
        event = "operator"
    elif root in ("megafon", "webhooks", "health", "sim"):
        event = "integration"
    elif root.startswith("export"):
        event = "export"
    else:
        return None

    if event == "auth":
        action = parts[1] if len(parts) > 1 else "request"
    elif len(parts) > 1 and parts[1].isdigit():
        action = parts[2] if len(parts) > 2 else "view"
    else:
        action = parts[1] if len(parts) > 1 else method.lower()
    action = re.sub(r"[^a-zA-Z0-9_.-]", "_", action)[:80] or "request"
    return event, action


def record_event(event_type, action, *, actor=None, status="success", entity_type="",
                 entity_id=None, details=None, error_code="", ip_address="", path=""):
    """Записать событие аудита. Ошибки записи проглатываются намеренно."""
    try:
        actor = actor or {}
        db.insert("audit_logs", {
            "created": _now(),
            "actor_user_id": actor.get("user_id"),
            "actor_login": str(actor.get("login") or "")[:120],
            "actor_role": str(actor.get("role") or "")[:40],
            "event_type": str(event_type or "system")[:60],
            "action": str(action or "request")[:100],
            "entity_type": str(entity_type or "")[:60],
            "entity_id": str(entity_id if entity_id is not None else "")[:120],
            "status": str(status or "success")[:20],
            "error_code": str(error_code or "")[:120],
            "ip_address": str(ip_address or "")[:120],
            "path": str(path or "")[:240],
            "details_json": _details(details),
        })
    except Exception:
        # Audit must be best effort and never become a single point of failure.
        return False
    return True


def record_http(method, path, body, headers, payload, status_code, actor_before=None):
    """Сформировать аудит для REST-запроса без записи паролей и токенов."""
    classification = _classification(method, path, status_code)
    if not classification:
        return
    event_type, action = classification
    # Чтение списков не является действием администратора; ошибки чтения
    # интеграций всё же сохраняем для диагностики.
    if (str(method).upper() == "GET" and status_code < 400
            and not (event_type == "auth" and action in ("login", "logout"))):
        return
    actor = actor_before or actor_from_headers(headers)
    parts = _path_parts(path)

    if event_type == "auth" and action == "login":
        if isinstance(payload, dict) and payload.get("ok") and payload.get("login"):
            actor = {"user_id": payload.get("user_id"), "login": payload.get("login"),
                     "role": payload.get("role", "")}
        elif not actor.get("login"):
            actor = {"login": str((body or {}).get("login") or "").strip().lower()}
    if event_type == "auth" and action == "logout" and not actor:
        actor = actor_from_headers(headers)

    if isinstance(payload, dict):
        ok = payload.get("ok") is not False and status_code < 400
        error_code = str(payload.get("error") or "")[:120]
        response_summary = {key: payload[key] for key in ("id", "user_id", "error", "status") if key in payload}
    else:
        ok = status_code < 400
        error_code = "" if ok else "http_error"
        response_summary = {"content": "[binary response]"}

    # Store identifiers and operation metadata, not full contact/scenario bodies.
    safe_body = {}
    for key in ("id", "user_id", "campaign_id", "contact_id", "call_id", "operator_id",
                "database_id", "flow", "status", "provider", "action", "kind", "filename"):
        if isinstance(body, dict) and key in body:
            safe_body[key] = _safe_value(body[key])
    if isinstance(body, dict) and body.get("phone"):
        safe_body["phone_present"] = True
    details = {"method": method, "path": urlparse(path or "").path,
               "request": safe_body, "response": response_summary}
    entity_type = event_type
    entity_id = None
    for key in ("id", "campaign_id", "contact_id", "call_id", "operator_id", "user_id", "database_id"):
        if key in safe_body:
            entity_id = safe_body[key]
            break
    if entity_id is None:
        entity_id = response_summary.get("id") or response_summary.get("user_id")
    if entity_id is None:
        for segment in parts[1:]:
            if segment.isdigit():
                entity_id = segment
                break

    ip_address = ((headers or {}).get("X-Forwarded-For")
                  or (headers or {}).get("X-Real-IP")
                  or (headers or {}).get("X-Ats-Client-IP") or "")
    record_event(event_type, action, actor=actor, status="success" if ok else "failure",
                 entity_type=entity_type, entity_id=entity_id, details=details,
                 error_code=error_code, ip_address=ip_address,
                 path=urlparse(path or "").path)
