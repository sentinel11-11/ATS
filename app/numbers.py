# -*- coding: utf-8 -*-
"""Пул номеров (ротация Caller ID): суточные лимиты, «остывание», карантин, веса."""
import datetime

from . import db

TODAY = datetime.date.today().isoformat()


def reset_daily_if_needed():
    """Сброс дневных счётчиков при смене даты."""
    with db._lock:
        rows = db.fetch("SELECT id, daily_date, daily_count FROM numbers")
        for r in rows:
            if r["daily_date"] != TODAY and r["daily_count"]:
                db.q("UPDATE numbers SET daily_count=0, daily_date=? WHERE id=?", (TODAY, r["id"]))


def _now_iso():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def list_numbers(include_disabled=False):
    sql = "SELECT * FROM numbers"
    if not include_disabled:
        sql += " WHERE active=1"
    return db.fetch(sql + " ORDER BY id")


def add_number(number, label="", kind="mobile", provider="sim", daily_limit=100):
    number = "".join(ch for ch in str(number).strip() if ch.isdigit() or ch == "+")
    if not number:
        return None, "empty"
    try:
        db.insert("numbers", {"number": number, "label": label, "kind": kind, "provider": provider,
                              "active": 1, "daily_limit": int(daily_limit), "weight": 1,
                              "quarantined": 0, "cooldown_until": "", "daily_date": TODAY,
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
    params = [provider, TODAY]
    excl = ""
    if exclude:
        excl = " AND id NOT IN ({})".format(",".join("?" * len(exclude)))
        params += list(exclude)
    rows = db.fetch(
        "SELECT * FROM numbers WHERE provider=? AND active=1 AND quarantined=0"
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
        dc = r["daily_count"] + 1 if r["daily_date"] == TODAY else 1
        db.q("UPDATE numbers SET daily_count=?, daily_date=?, cooldown_until=? WHERE id=?",
             (dc, TODAY, cd, number_id))


def pool_state():
    reset_daily_if_needed()
    rows = db.fetch("SELECT * FROM numbers ORDER BY id")
    now = _now_iso()
    out = []
    for r in rows:
        cooling = bool(r["cooldown_until"]) and r["cooldown_until"] > now
        out.append({
            "id": r["id"], "number": r["number"], "label": r["label"], "kind": r["kind"],
            "provider": r["provider"], "active": bool(r["active"]), "daily_limit": r["daily_limit"],
            "weight": r["weight"], "quarantined": bool(r["quarantined"]),
            "cooldown_until": r["cooldown_until"], "cooling": cooling,
            "daily_date": r["daily_date"], "daily_count": r["daily_count"],
        })
    return out
