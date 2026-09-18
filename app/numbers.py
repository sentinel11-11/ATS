# -*- coding: utf-8 -*-
"""Пул номеров (ротация Caller ID): суточные лимиты, «остывание», карантин, веса."""
import datetime

from . import db

def today():
    """Текущая дата. Вызывается каждый раз заново (не кэшируется),
    иначе после полуночи суточные лимиты считались бы по вчерашней дате."""
    return datetime.date.today().isoformat()


def reset_daily_if_needed():
    """Сброс дневных счётчиков при смене даты."""
    with db._lock:
        rows = db.fetch("SELECT id, daily_date, daily_count FROM numbers")
        for r in rows:
            if r["daily_date"] != today() and r["daily_count"]:
                db.q("UPDATE numbers SET daily_count=0, daily_date=? WHERE id=?", (today(), r["id"]))


def _now_iso():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def list_numbers(include_disabled=False):
    sql = "SELECT * FROM numbers"
    if not include_disabled:
        sql += " WHERE active=1"
    return db.fetch(sql + " ORDER BY id")


NUMBER_PROVIDERS = ("sim", "uis", "ami", "megafon_vats")


def add_number(number, label="", kind="mobile", provider="sim", daily_limit=100,
               carrier="", provider_ref="", enabled_outgoing=1):
    number = "".join(ch for ch in str(number).strip() if ch.isdigit() or ch == "+")
    if not number:
        return None, "empty"
    digits = number.lstrip("+")
    if not digits.isdigit() or not (3 <= len(digits) <= 16):
        return None, "bad_number"
    provider = str(provider or "sim").strip().lower()
    if provider not in NUMBER_PROVIDERS:
        return None, "bad_provider"
    try:
        daily_limit = int(daily_limit)
    except (TypeError, ValueError):
        return None, "bad_limit"
    if daily_limit < 1:
        return None, "bad_limit"
    try:
        db.insert("numbers", {"number": number, "label": str(label or "")[:200],
                              "kind": str(kind or "mobile")[:50], "provider": provider,
                              "carrier": str(carrier or "")[:50],
                              "provider_ref": str(provider_ref or "")[:64],
                              "enabled_outgoing": 1 if enabled_outgoing else 0,
                              "active": 1, "daily_limit": daily_limit, "weight": 1,
                              "quarantined": 0, "cooldown_until": "", "daily_date": today(),
                              "daily_count": 0, "created": _now_iso()})
        return True, "ok"
    except Exception:
        return None, "duplicate"


def update_number(nid, fields: dict):
    allowed = {"label", "active", "daily_limit", "weight", "quarantined", "provider", "kind"}
    data = {k: v for k, v in fields.items() if k in allowed}
    if "active" in data:
        data["active"] = 1 if data["active"] else 0
    if "quarantined" in data:
        data["quarantined"] = 1 if data["quarantined"] else 0
    # Некорректные значения при обновлении отбрасываем (не пишем мусор в пул).
    if "provider" in data:
        if str(data["provider"] or "").strip().lower() not in NUMBER_PROVIDERS:
            del data["provider"]
        else:
            data["provider"] = str(data["provider"]).strip().lower()
    for key in ("daily_limit", "weight"):
        if key in data:
            try:
                data[key] = int(data[key])
            except (TypeError, ValueError):
                del data[key]
                continue
            if data[key] < 1:
                del data[key]
    if data:
        db.update("numbers", data, "id=?", (nid,))
    return True


def quarantine(nid, on=True):
    db.q("UPDATE numbers SET quarantined=? WHERE id=?", (1 if on else 0, nid))


def acquire(provider="sim", cooldown_sec=0, exclude_ids=None):
    """Выбрать лучший номер: активный, не в карантине, лимит не исчерпан, остывание прошло.
    Приоритет: больший вес -> меньший дневной счётчик -> меньший id (round-robin по базе)."""
    reset_daily_if_needed()
    exclude = exclude_ids or []
    now = _now_iso()
    params = [provider, today()]
    excl = ""
    if exclude:
        excl = " AND id NOT IN ({})".format(",".join("?" * len(exclude)))
        params += list(exclude)
    rows = db.fetch(
        "SELECT * FROM numbers WHERE provider=? AND active=1 AND quarantined=0 "
        "AND enabled_outgoing=1"
        " AND (daily_date<>? OR daily_count<daily_limit)"
        " AND (cooldown_until='' OR cooldown_until<=?)" + excl +
        " ORDER BY weight DESC, daily_count ASC, id ASC LIMIT 1",
        params + [now])
    return rows[0] if rows else None


def mark_used(number_id, cooldown_sec):
    now = _now_iso()
    cd = ""
    if cooldown_sec and cooldown_sec > 0:
        dt = datetime.datetime.now() + datetime.timedelta(seconds=int(cooldown_sec))
        cd = dt.strftime("%Y-%m-%d %H:%M:%S")
    with db._lock:
        r = db.fetch1("SELECT daily_date, daily_count FROM numbers WHERE id=?", (number_id,))
        if not r:
            return
        dc = r["daily_count"] + 1 if r["daily_date"] == today() else 1
        db.q("UPDATE numbers SET daily_count=?, daily_date=?, cooldown_until=?, "
                 "dialed_total=dialed_total+1 WHERE id=?",
             (dc, today(), cd, number_id))


def mark_answered(number_id):
    """Зафиксировать ответ абонента по номеру (статистика CallerID пула)."""
    if not number_id:
        return
    db.q("UPDATE numbers SET answered_total=answered_total+1 WHERE id=?", (number_id,))


def pool_state():
    reset_daily_if_needed()
    rows = db.fetch("SELECT * FROM numbers ORDER BY id")
    now = _now_iso()
    out = []
    for r in rows:
        cooling = bool(r["cooldown_until"]) and r["cooldown_until"] > now
        out.append({
            "id": r["id"], "number": r["number"], "label": r["label"], "kind": r["kind"],
            "provider": r["provider"], "active": bool(r["active"]),
            "enabled_outgoing": bool(r["enabled_outgoing"]),
            "daily_limit": r["daily_limit"],
            "weight": r["weight"], "quarantined": bool(r["quarantined"]),
            "cooldown_until": r["cooldown_until"], "cooling": cooling,
            "daily_date": r["daily_date"], "daily_count": r["daily_count"],
            "dialed_total": r["dialed_total"], "answered_total": r["answered_total"],
        })
    return out
