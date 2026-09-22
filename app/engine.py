# -*- coding: utf-8 -*-
"""Движок ATS v2 (замена dispatch_loop из server.py).

Поток-диспетчер: обработка событий провайдера, автодозвон (кампании -> попытки -> пул номеров),
повторы (busy/no_answer/machine/failed), watchdog «зависших» звонков, сценарии:
message (информирование), agent (ИИ-агент L1/L2), operator (перевод на сотрудника/ACD).
"""
import datetime
import json
import os
import queue
import threading
import time
import uuid

from . import agent as agent_mod
from . import config, db, events, numbers as numbers_mod
from .crm import make_crm
from .telephony import ProviderNotConfigured, make_provider

RETRYABLE = {"busy", "no_answer", "machine", "failed", "timeout"}
TERMINAL_OK = {"done_ok", "done_agent", "operator_ok", "not_qualified", "qualified_no"}
TERMINAL_BAD = {"blocked_no_consent", "blacklisted", "exhausted", "canceled", "no_operator"}


def now_iso():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _iso_to_dt(s):
    try:
        return datetime.datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None


def in_window(campaign_schedule, settings):
    sc = campaign_schedule or {}
    start = sc.get("start") or settings.get("window_start", "08:00")
    end = sc.get("end") or settings.get("window_end", "20:00")
    days = sc.get("days")
    if days is None:
        days = settings.get("window_days", [0, 1, 2, 3, 4, 5, 6])
    now = datetime.datetime.now()
    if now.weekday() not in [int(d) for d in days]:
        return False
    cur = now.strftime("%H:%M")
    return start <= cur <= end


class Engine:
    def __init__(self, provider=None, auto_start=True):
        self.settings = db.get_settings()
        self._evq = queue.Queue()
        try:
            self.provider = provider or make_provider(self.settings)
        except Exception as e:
            # Production: молчаливый переход на sim ЗАПРЕЩЁН — иначе оператор
            # решит, что звонки идут, а они будут симулироваться. Fallback
            # включается только явно (стенд/разработка): ATS_ALLOW_SIM_FALLBACK=1.
            if os.environ.get("ATS_ALLOW_SIM_FALLBACK") == "1":
                print("[engine] Провайдер недоступен, ЯВНЫЙ переход на sim:", e)
                from .telephony import SimProvider
                self.provider = SimProvider()
            else:
                print("[engine] КРИТИЧНО: провайдер '{}' недоступен: {}".format(
                    self.settings.get("provider", "?"), e))
                print("[engine] Сервер остановлен. Проверьте настройки провайдера "
                      "или задайте ATS_ALLOW_SIM_FALLBACK=1 для стенда.")
                raise
        self.provider.attach(self._evq)
        if getattr(self.provider, "name", "") == "sim":
            print("[engine] СТЕНД: провайдер 'sim' — звонки симулируются, "
                  "реальной телефонии нет.")
        self.crm = make_crm(self.settings)
        self._stop = threading.Event()
        self._thread = None
        self._lock = threading.RLock()
        if auto_start:
            self.start()

    # ---------- жизненный цикл ----------
    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="ats-engine")
        self._thread.start()

    def stop(self):
        self._stop.set()
        self.provider.stop()

    def _loop(self):
        while not self._stop.is_set():
            try:
                self.tick_once()
            except Exception as e:
                import traceback
                traceback.print_exc()
            time.sleep(0.3 if config.FAST else 1.0)

    # ---------- события провайдера ----------
    def push_event(self, ev):
        """Положить событие провайдера в очередь движка.

        Единственная точка входа для вебхуков: HTTP-хендлер только кладёт
        событие и сразу отвечает 200 — обработку делает тик движка.
        """
        self._evq.put(ev)

    def drain_events(self):
        got = []
        while True:
            try:
                got.append(self._evq.get_nowait())
            except queue.Empty:
                break
        return got

    def handle_event(self, ev):
        if ev.get("provider") == "megafon_vats" and ev.get("call_id") is None:
            # Нормализованное событие ВАТС: корреляция по external_call_id —
            # наш call_id ещё не резолвлен (см. _on_megafon).
            return self._on_megafon(ev)
        evt = ev.get("event")
        call_id = ev.get("call_id")
        call = db.fetch1("SELECT * FROM calls WHERE id=?", (call_id,))
        if not call:
            return
        item = db.fetch1("SELECT * FROM campaign_items WHERE id=?", (call["item_id"],)) if call["item_id"] else None
        if evt == "ring":
            db.q("UPDATE calls SET status='ringing' WHERE id=?", (call_id,))
            if item:
                db.update("campaign_items", {"status": "dialing"}, "id=?", (item["id"],))
            events.publish("call", {"id": call_id, "status": "ringing", "phone": call["contact_phone"]})
        elif evt == "answered":
            human = bool(ev.get("human", True))
            db.q("UPDATE calls SET status='answered', answered_at=? WHERE id=?",
                 (now_iso(), call_id))
            self._on_answered(call, item, human)
        elif evt == "status":
            status = ev.get("status")
            detail = ev.get("detail", "")
            self._finish_attempt(call, item, status, detail, retryable=(status in RETRYABLE))
        elif evt == "dropped":
            self._on_dropped(call, item)
        elif evt == "done":
            db.q("UPDATE calls SET status='done', ended_at=?, duration_sec=? WHERE id=?",
                 (now_iso(), self._duration(call), call_id))
            events.publish("call", {"id": call_id, "status": "done", "phone": call["contact_phone"]})

    # ---------- МегаФон ВАТС: нормализованные события (без нашего call_id) ----------
    def _on_megafon(self, ev):
        """Корреляция по external_call_id + дедуп по fingerprint (§21 ТЗ)."""
        fp = ev.get("fingerprint") or ""
        if fp and db.fetch1("SELECT id FROM provider_events WHERE fingerprint=?", (fp,)):
            print("[megafon] дубль вебхука {} — пропущен".format(fp[:12]))
            return
        if fp:
            db.insert("provider_events", {
                "provider": "megafon_vats", "fingerprint": fp,
                "external_call_id": ev.get("external_call_id", ""),
                "event_type": ev.get("raw_type") or ev.get("event", ""),
                "payload_json": json.dumps(ev.get("raw") or {}, ensure_ascii=False),
                "received_at": now_iso(), "processed_at": "", "status": "new"})
        kind = ev.get("event")
        if kind == "history":
            return self._megafon_history(ev, fp)
        if kind == "rating":
            return self._megafon_rating(ev, fp)
        if kind == "provider_webhook":
            print("[megafon] webhook {}: {}".format(ev.get("webhook_type"), ev.get("data")))
            events.publish("system", {"kind": "vats_webhook",
                                      "type": ev.get("webhook_type"),
                                      "data": ev.get("data")})
            return self._megafon_mark(fp, "done")
        call = self._megafon_resolve(ev.get("external_call_id"))
        if not call and kind == "ring" and ev.get("direction") == "in" \
                and ev.get("raw_type") == "INCOMING":
            call = self._megafon_incoming(ev)
        if not call:
            print("[megafon] orphan {} {}: звонок не найден".format(
                kind, ev.get("external_call_id")))
            return self._megafon_mark(fp, "orphan")
        item = db.fetch1("SELECT * FROM campaign_items WHERE id=?",
                         (call["item_id"],)) if call["item_id"] else None
        if kind == "ring":
            if call["status"] != "done":
                db.q("UPDATE calls SET status='ringing', diversion=?, provider_user=? "
                     "WHERE id=?", (ev.get("diversion", ""), ev.get("user", ""), call["id"]))
                if item and item.get("status") in ("queued", "dialing"):
                    db.update("campaign_items", {"status": "dialing"}, "id=?", (item["id"],))
                events.publish("call", {"id": call["id"], "status": "ringing",
                                        "phone": call["contact_phone"]})
            return self._megafon_mark(fp, "done")
        if kind == "answered":
            if call["status"] == "done":
                return self._megafon_mark(fp, "done")
            if (call.get("direction") or "out") == "in":
                # Входящий: разговор идёт на стороне ВАТС, flow нет (нет кампании).
                # Через _on_answered НЕ вести — там message-ветка «доставит»
                # сообщение и повесит трубку живого разговора.
                db.q("UPDATE calls SET status='answered', answered_at=? WHERE id=?",
                     (now_iso(), call["id"]))
                events.publish("call", {"id": call["id"], "status": "answered",
                                        "phone": call["contact_phone"], "direction": "in"})
                return self._megafon_mark(fp, "done")
            # Исходящий: клиент на линии (допущение: ACCEPTED для callback-плеча —
            # ответ клиента; сверяется на живом стенде, см. LIVE_TEST_MEGAFON.md).
            ev = dict(ev, call_id=call["id"], human=True)
            self.handle_event(ev)
            return self._megafon_mark(fp, "done")
        if kind == "done":
            return self._megafon_completed(call, item, ev, fp)
        if kind == "canceled":
            return self._megafon_canceled(call, item, ev, fp)
        if kind == "transferred":
            db.q("UPDATE calls SET detail=? WHERE id=?",
                 (("переведён (second_callid={})".format(ev.get("second_callid"))
                   if ev.get("second_callid") else "переведён на другого сотрудника")[:300],
                  call["id"]))
            events.publish("call", {"id": call["id"], "status": call["status"],
                                    "phone": call["contact_phone"], "transferred": True})
            return self._megafon_mark(fp, "done")
        print("[megafon] неизвестное нормализованное событие: {}".format(kind))
        return self._megafon_mark(fp, "done")

    def _megafon_mark(self, fp, status):
        if fp:
            db.q("UPDATE provider_events SET status=?, processed_at=? WHERE fingerprint=?",
                 (status, now_iso(), fp))

    def _megafon_resolve(self, external_id):
        if not external_id:
            return None
        return db.fetch1("SELECT * FROM calls WHERE provider='megafon_vats' "
                         "AND external_call_id=? ORDER BY id DESC LIMIT 1", (external_id,))

    def _megafon_contact(self, phone):
        from .providers.megafon_vats import phone_variants
        for variant in phone_variants(phone):
            if not variant:
                continue
            c = db.fetch1("SELECT * FROM contacts WHERE phone=?", (variant,))
            if c:
                return c
        return None

    def _megafon_incoming(self, ev):
        """Новый входящий звонок: карточка (SSE) + запись журнала. Без автосоздания
        контакта (не спамим базу) и без ACD (маршрутизирует сама ВАТС)."""
        if not self.settings.get("log_non_campaign_calls", True):
            return None
        contact = self._megafon_contact(ev.get("phone"))
        call_id = db.insert("calls", {
            "campaign_id": 0, "item_id": 0, "contact_id": contact["id"] if contact else 0,
            "contact_name": contact["name"] if contact else "",
            "contact_phone": ev.get("phone", ""), "caller_id": "", "number_id": 0,
            "provider": "megafon_vats", "external_call_id": ev.get("external_call_id", ""),
            "direction": "in", "status": "ringing", "result": "", "detail": "",
            "agent_result": "", "recording": "", "started_at": now_iso(), "answered_at": "",
            "ended_at": "", "duration_sec": 0, "diversion": ev.get("diversion", ""),
            "provider_user": ev.get("user", "")})
        events.publish("call", {"id": call_id, "status": "ringing",
                                "phone": ev.get("phone", ""), "direction": "in",
                                "contact_name": contact["name"] if contact else "",
                                "incoming": True})
        return db.fetch1("SELECT * FROM calls WHERE id=?", (call_id,))

    def _megafon_completed(self, call, item, ev, fp):
        call = db.fetch1("SELECT * FROM calls WHERE id=?", (call["id"],))
        if not call:
            return self._megafon_mark(fp, "done")
        if call["status"] == "done":
            self._megafon_heal_no_operator(call, "COMPLETED ВАТС")
            return self._megafon_mark(fp, "done")
        direction = call.get("direction") or "out"
        if direction == "in":
            if call.get("answered_at"):
                db.q("UPDATE calls SET status='done', ended_at=?, duration_sec=?, "
                     "result='done_ok', detail=? WHERE id=?",
                     (now_iso(), self._duration(call), "Входящий: разговор завершён",
                      call["id"]))
                self._push_crm(call, "done_ok")
                events.publish("call", {"id": call["id"], "status": "done",
                                        "phone": call["contact_phone"]})
            else:
                self._megafon_missed_in(call, "завершён без ответа (COMPLETED)")
            return self._megafon_mark(fp, "done")
        # Исходящий: разговор на стороне ВАТС завершён.
        if not call.get("answered_at") and call.get("status") in ("new", "dialing", "ringing"):
            self._finish_attempt(call, item, "failed", "Завершён ВАТС без ответа",
                                 retryable=True)
            return self._megafon_mark(fp, "done")
        campaign = db.fetch1("SELECT * FROM campaigns WHERE id=?",
                             (call["campaign_id"],)) if call["campaign_id"] else None
        flow = (campaign or {}).get("flow") or "message"
        if flow == "operator":
            db.q("UPDATE acd SET status='completed', updated=? "
                 "WHERE call_id=? AND status IN ('queued','offered','ringing','answered','accepted','bridged')", (now_iso(), call["id"]))
            self._finish_attempt(call, item, "operator_ok", "Разговор завершён (ВАТС)",
                                 ok=True)
        else:
            # agent финиширует на ответе; сюда попадает только при потере ACCEPTED.
            self._finish_attempt(call, item, "done_ok", "Разговор завершён (ВАТС)",
                                 ok=True)
        return self._megafon_mark(fp, "done")

    def _megafon_canceled(self, call, item, ev, fp):
        call = db.fetch1("SELECT * FROM calls WHERE id=?", (call["id"],))
        if not call or call["status"] == "done":
            return self._megafon_mark(fp, "done")
        if call.get("answered_at") or call.get("status") not in (
                "new", "dialing", "ringing", "wait_operator"):
            # Обрыв после ответа (или в очереди ACD после бриджа) — был разговор.
            return self._megafon_completed(call, item, ev, fp)
        if (call.get("direction") or "out") == "in":
            self._megafon_missed_in(call, "отменён до ответа (CANCELLED)")
        else:
            self._finish_attempt(call, item, "failed", "Отменён ВАТС до ответа (CANCELLED)",
                                 retryable=True)
        return self._megafon_mark(fp, "done")

    def _megafon_missed_in(self, call, reason):
        """Пропущенный входящий: журнал + задача «перезвонить» в CRM."""
        db.q("UPDATE calls SET status='done', ended_at=?, result='missed', detail=? "
             "WHERE id=?", (now_iso(), reason[:200], call["id"]))
        try:
            self.crm.create_task(
                "Перезвонить клиенту",
                "Пропущенный входящий {} {} (звонок #{}, ВАТС {}) — {}".format(
                    call.get("contact_name", ""), call.get("contact_phone", ""),
                    call["id"], call.get("external_call_id", ""), reason))
        except Exception as e:
            print("[crm] create_task:", e)
        self._push_crm(call, "missed")
        events.publish("call", {"id": call["id"], "status": "missed",
                                "phone": call["contact_phone"], "direction": "in"})

    def _megafon_heal_no_operator(self, call, source):
        """Гонка watchdog↔финал ВАТС: разговор состоялся (answered_at есть),
        но watchdog уже закрыл звонок как no_operator. Правим результат звонка
        по ground truth ВАТС. Позицию кампании и CRM не трогаем — та же
        философия, что у history-downgrade (не плодим дубли дозвонов/пушей)."""
        if not call or call.get("status") != "done" or call.get("result") != "no_operator" \
                or not call.get("answered_at"):
            return False
        if (call.get("direction") or "out") == "in":
            heal = "done_ok"
        else:
            campaign = db.fetch1("SELECT * FROM campaigns WHERE id=?",
                                 (call["campaign_id"],)) if call["campaign_id"] else None
            flow = (campaign or {}).get("flow") or "message"
            heal = "operator_ok" if flow == "operator" else "done_ok"
        db.q("UPDATE calls SET result=?, detail=? WHERE id=?",
             (heal, "разговор состоялся ({}), watchdog закрыл раньше".format(source)[:200],
              call["id"]))
        print("[megafon] heal звонка {}: no_operator → {} ({})".format(
            call["id"], heal, source))
        events.publish("call", {"id": call["id"], "status": "done", "result": heal,
                                "phone": call.get("contact_phone", "")})
        return True

    def _megafon_history(self, ev, fp):
        """History-пуш: ground truth от ВАТС. Топ-ап фактов + финализация,
        если движок её пропустил (потеряно COMPLETED)."""
        from .providers.megafon_vats import MEGAFON_HISTORY_MAP
        call = self._megafon_resolve(ev.get("external_call_id"))
        raw = ev.get("raw") or {}
        st = (ev.get("status") or "").lower()
        if not call:
            # Журнальная запись из пуша (напр. пропущенный без INCOMING):
            # звонок создаём, контакт — нет.
            contact = self._megafon_contact(ev.get("phone"))
            started = ev.get("start") or now_iso()
            try:
                import datetime as _dt
                ended = (_dt.datetime.strptime(started, "%Y-%m-%d %H:%M:%S")
                         + _dt.timedelta(seconds=int(ev.get("duration") or 0))
                         ).strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                ended = now_iso()
            result, detail = self._megafon_history_result(st, ev.get("direction"), None)
            call_id = db.insert("calls", {
                "campaign_id": 0, "item_id": 0,
                "contact_id": contact["id"] if contact else 0,
                "contact_name": contact["name"] if contact else "",
                "contact_phone": ev.get("phone", ""), "caller_id": "", "number_id": 0,
                "provider": "megafon_vats",
                "external_call_id": ev.get("external_call_id", ""),
                "direction": ev.get("direction") or "in", "status": "done",
                "result": result, "detail": detail, "agent_result": "", "recording": "",
                "started_at": started,
                "answered_at": started if result in ("ok", "done_ok", "operator_ok") else "",
                "ended_at": ended, "duration_sec": int(ev.get("duration") or 0),
                "diversion": ev.get("diversion", ""),
                "provider_user": ev.get("user", ""),
                "recording_url": ev.get("record_url", ""),
                "external_status": raw.get("status", ""),
                "wait_sec": int(ev.get("wait") or 0),
                "missed_status": ev.get("missed_status", ""),
                "rating": int(ev.get("rating") or 0)})
            call = db.fetch1("SELECT * FROM calls WHERE id=?", (call_id,))
            if result == "missed":
                try:
                    self.crm.create_task(
                        "Перезвонить клиенту",
                        "Пропущенный входящий {} {} (звонок #{}, ВАТС {})".format(
                            call.get("contact_name", ""), call.get("contact_phone", ""),
                            call["id"], call.get("external_call_id", "")))
                except Exception as e:
                    print("[crm] create_task:", e)
            self._push_crm(call, result)
            events.publish("call", {"id": call["id"], "status": result,
                                    "phone": call["contact_phone"]})
            return self._megafon_mark(fp, "done")
        # Топ-ап: непустое из ВАТС побеждает (ground truth точнее наших часов).
        upd, args = [], []
        for col, val in (("recording_url", ev.get("record_url")),
                         ("external_status", raw.get("status")),
                         ("diversion", ev.get("diversion")),
                         ("provider_user", ev.get("user")),
                         ("missed_status", ev.get("missed_status"))):
            if val:
                upd.append(col + "=?")
                args.append(str(val)[:500])
        for col, val in (("duration_sec", ev.get("duration")),
                         ("wait_sec", ev.get("wait")), ("rating", ev.get("rating"))):
            if int(val or 0) > 0:
                upd.append(col + "=?")
                args.append(int(val))
        if upd:
            args.append(call["id"])
            db.q("UPDATE calls SET {} WHERE id=?".format(", ".join(upd)), tuple(args))
        call = db.fetch1("SELECT * FROM calls WHERE id=?", (call["id"],))
        if call["status"] == "done":
            # Сверка: ВАТС говорит «провал», а мы зафиксировали «разговор» —
            # правим результат звонка (позицию кампании не переоткрываем,
            # чтобы не плодить дубли дозвонов и CRM-пушей).
            mapped = MEGAFON_HISTORY_MAP.get(st)
            if mapped and mapped[0] not in ("ok", "missed") and not mapped[1] \
                    and call.get("result") in ("operator_ok", "done_ok"):
                db.q("UPDATE calls SET result=?, detail=? WHERE id=?",
                     (mapped[0], "history ВАТС: {}".format(raw.get("status", ""))[:200],
                      call["id"]))
                print("[megafon] history скорректировала результат звонка {}: {} "
                      "(было {})".format(call["id"], mapped[0], call.get("result")))
            if mapped and mapped[0] == "ok":
                self._megafon_heal_no_operator(call, "history ВАТС")
            return self._megafon_mark(fp, "done")
        campaign = db.fetch1("SELECT * FROM campaigns WHERE id=?",
                             (call["campaign_id"],)) if call["campaign_id"] else None
        flow = (campaign or {}).get("flow") or "message"
        if st == "success" and flow == "message" and call.get("direction") == "out":
            # Сообщение не озвучивалось (нет медиа) — done_ok было бы ложью.
            self._finish_attempt(call, None, "no_media",
                                 "flow=message невозможен на ВАТС (нет аудио)", retryable=False)
            return self._megafon_mark(fp, "done")
        result, detail = self._megafon_history_result(
            st, call.get("direction"), flow if call.get("direction") == "out" else None)
        if result == "missed":
            self._megafon_missed_in(call, "пропущенный (history ВАТС)")
            return self._megafon_mark(fp, "done")
        item = db.fetch1("SELECT * FROM campaign_items WHERE id=?",
                         (call["item_id"],)) if call["item_id"] else None
        if result == "ok":
            ok_result = "operator_ok" if flow == "operator" else "done_ok"
            self._finish_attempt(call, item, ok_result, detail, ok=True)
        else:
            _m = MEGAFON_HISTORY_MAP.get(st) or ("failed", True)
            retryable = _m[1] if isinstance(_m, (list, tuple)) and len(_m) > 1 else True
            self._finish_attempt(call, item, result, detail, retryable=retryable)
        return self._megafon_mark(fp, "done")

    def _megafon_history_result(self, st, direction, flow):
        from .providers.megafon_vats import MEGAFON_HISTORY_MAP
        mapped = MEGAFON_HISTORY_MAP.get(st)
        if not mapped or not isinstance(mapped, (list, tuple)) or len(mapped) < 2:
            print("[megafon] неизвестный history-статус {!r} — failed+ретрай".format(st))
            return "failed", "Неизвестный статус history ВАТС: {}".format(st)
        result, _retryable = mapped
        if result == "ok":
            if direction == "in":
                return "done_ok", "Входящий: разговор завершён (history ВАТС)"
            if flow == "operator":
                return "ok", "Разговор завершён (history ВАТС)"
            return "done_ok", "Разговор завершён (history ВАТС)"
        if result == "missed":
            if direction == "out":
                return "no_answer", "Клиент не ответил (history ВАТС)"
            return "missed", "Пропущенный входящий (history ВАТС)"
        detail = {"failed": "Звонок не состоялся (history ВАТС: {})",
                  "busy": "Занято (history ВАТС)",
                  "no_answer": "Абонент недоступен (history ВАТС)"}.get(
                      result, "history ВАТС: {}")
        return result, detail.format(st)

    def _megafon_rating(self, ev, fp):
        call = self._megafon_resolve(ev.get("external_call_id"))
        if not call:
            print("[megafon] orphan rating {}: звонок не найден".format(
                ev.get("external_call_id")))
            return self._megafon_mark(fp, "orphan")
        if int(ev.get("rating") or 0) > 0:
            db.q("UPDATE calls SET rating=? WHERE id=?",
                 (int(ev.get("rating")), call["id"]))
        events.publish("call", {"id": call["id"], "status": call["status"],
                                "phone": call["contact_phone"],
                                "rating": int(ev.get("rating") or 0)})
        return self._megafon_mark(fp, "done")

    # ---------- МегаФон ВАТС: синки пула и справочников (стадия 3) ----------
    def _megafon_client(self, override_base=None, override_key=None):
        """Клиент ВАТС из настроек (синки работают независимо от глобального
        провайдера — пул можно готовить до переключения телефонии)."""
        from .providers.base import resolve_secret
        from .providers.megafon_vats import DEFAULT_API_KEY_ENV, MegafonVatsClient
        mcfg = (self.settings or {}).get("megafon_vats") or {}
        base = str(override_base or mcfg.get("base_url") or "").strip()
        key = str(override_key or resolve_secret(mcfg, "api_key", "api_key_env", DEFAULT_API_KEY_ENV) or "").strip()
        if not base or not key:
            raise ProviderNotConfigured(
                "megafon_vats не настроен: заполните Base URL и API Key в настройках МегаФон ВАТС.")
        return MegafonVatsClient(base, key, mcfg.get("timeout_sec", 15))

    def megafon_pool_sync(self, dry_run=False):
        """Сверить локальный пул с GET /caller-ids/telnums + GET /telnums.
        Ничего не удаляет, карантин и active (выключатель админа) не трогает;
        управляет только enabled_outgoing + добавляет пригодные номера."""
        from .providers.megafon_vats import plan_number_sync
        client = self._megafon_client()
        caller = client.get_caller_id_telnums() or []
        telnums = client.get_telnums_all()
        local = db.fetch("SELECT * FROM numbers WHERE provider='megafon_vats' ORDER BY id")
        plan = plan_number_sync(caller, telnums, local)
        by_id = {r["id"]: r["number"] for r in local}
        report = {"dry_run": bool(dry_run),
                  "added": [a["number"] for a in plan["add"]],
                  "enabled": [by_id.get(i, i) for i in plan["enable"]],
                  "disabled": [by_id.get(i, i) for i in plan["disable"]],
                  "errors": []}
        if dry_run:
            return report
        for a in plan["add"]:
            ok, err = numbers_mod.add_number(
                a["number"], label=a["label"], provider="megafon_vats",
                carrier=a["carrier"], provider_ref=a["provider_ref"])
            if not ok:
                report["errors"].append("{}: {}".format(a["number"], err))
        for nid in plan["enable"]:
            db.q("UPDATE numbers SET enabled_outgoing=1 WHERE id=?", (nid,))
        for nid in plan["disable"]:
            db.q("UPDATE numbers SET enabled_outgoing=0 WHERE id=?", (nid,))
            print("[megafon] пул: номер {} отключён от исходящих "
                  "(нет в caller-ids/enabled ВАТС)".format(by_id.get(nid, nid)))
        print("[megafon] синк пула: +{} вкл:{} выкл:{}".format(
            len(report["added"]), len(report["enabled"]), len(report["disabled"])))
        return report

    def megafon_users_sync(self):
        """Снапшот сотрудников ВАТС → vats_users + отчёт о маппинге на операторов
        ATS (ключ — operators.vats_login, задаёт админ в карточке пользователя)."""
        import json as _json
        client = self._megafon_client()
        users = client.get_users_all()
        upserted = 0
        for u in users:
            login = str((u or {}).get("login") or "").strip()
            if not login:
                continue
            db.q("INSERT INTO vats_users(login,name,position,email,ext,telnum,role,"
                 "mobile,status,raw_json,updated) VALUES (?,?,?,?,?,?,?,?,?,?,?) "
                 "ON CONFLICT(login) DO UPDATE SET name=excluded.name,"
                 "position=excluded.position,email=excluded.email,ext=excluded.ext,"
                 "telnum=excluded.telnum,role=excluded.role,mobile=excluded.mobile,"
                 "status=excluded.status,raw_json=excluded.raw_json,updated=excluded.updated",
                 (login, str(u.get("name") or "")[:200], str(u.get("position") or "")[:200],
                  str(u.get("email") or "")[:200], str(u.get("ext") or "")[:30],
                  str(u.get("telnum") or "")[:32], str(u.get("role") or "")[:64],
                  str(u.get("mobile") or "")[:32], str(u.get("status") or "")[:32],
                  _json.dumps(u, ensure_ascii=False)[:8000], now_iso()))
            upserted += 1
        vlogins = {str(r["login"]) for r in
                   db.fetch("SELECT login FROM vats_users")}
        ops = db.fetch("SELECT id, name, vats_login FROM operators")
        mapped = sorted({o["vats_login"] for o in ops
                         if (o.get("vats_login") or "") in vlogins})
        dangling = sorted("op#{}:{}".format(o["id"], o.get("vats_login"))
                          for o in ops if o.get("vats_login") and
                          o["vats_login"] not in vlogins)
        op_logins = {o.get("vats_login") or "" for o in ops}
        unmapped = sorted(vlogins - op_logins)
        print("[megafon] синк сотрудников: {} (операторов замаплено: {})".format(
            upserted, len(mapped)))
        return {"total": len(users), "upserted": upserted, "mapped": mapped,
                "dangling": dangling, "unmapped": unmapped}

    def megafon_groups_sync(self):
        """Снапшот отделов ВАТС → vats_groups (задел под внешние ACD-эндпоинты)."""
        import json as _json
        client = self._megafon_client()
        groups = client.get_groups_all()
        upserted = 0
        for g in groups:
            gid = str((g or {}).get("id") or "").strip()
            if not gid:
                continue
            db.q("INSERT INTO vats_groups(group_id,name,ext,call_order,users_json,updated)"
                 " VALUES (?,?,?,?,?,?) ON CONFLICT(group_id) DO UPDATE SET "
                 "name=excluded.name,ext=excluded.ext,call_order=excluded.call_order,"
                 "users_json=excluded.users_json,updated=excluded.updated",
                 (gid, str(g.get("name") or "")[:200], str(g.get("ext") or "")[:30],
                  str(g.get("call_order") or "")[:30],
                  _json.dumps(g.get("users") or [], ensure_ascii=False)[:8000],
                  now_iso()))
            upserted += 1
        print("[megafon] синк отделов: {}".format(upserted))
        return {"total": len(groups), "upserted": upserted}

    def reload_settings(self):
        """Перезагрузить настройки из БД в память и пересоздать провайдер/CRM при необходимости."""
        self.settings = db.get_settings()
        try:
            self.provider = make_provider(self.settings)
        except Exception as e:
            print("[engine] reload_settings provider update:", e)
        try:
            self.crm = make_crm(self.settings)
        except Exception as e:
            print("[engine] reload_settings crm update:", e)

    def is_running(self):
        t = self._thread
        return bool(t is not None and t.is_alive())

    def megafon_check(self, override_base=None, override_key=None):
        """Живая проверка связи с ВАТС (§51 ТЗ): три опорных чтения.
        Возвращает {"ok", "stages", ...}; первое упавшее чтение — в failed_stage
        с конкретным статусом/деталью (401/таймаут/DNS — из клиента)."""
        from .providers.base import ProviderApiError
        client = self._megafon_client(override_base, override_key)
        stages = {}
        for name, fn in (("users", lambda: client.get_users(limit=1)),
                         ("telnums", lambda: client.get_telnums(limit=1)),
                         ("caller_ids", client.get_caller_id_telnums)):
            try:
                stages[name] = {"ok": True, "sample": fn()}
            except ProviderApiError as e:
                stages[name] = {"ok": False, "status": e.status,
                                "detail": str(e)[:300]}
                return {"ok": False, "failed_stage": name, "detail": f"Ошибка на этапе '{name}': {str(e)[:200]}", "stages": stages}
            except Exception as e:
                stages[name] = {"ok": False, "detail": str(e)[:300]}
                return {"ok": False, "failed_stage": name, "detail": f"Ошибка соединения на этапе '{name}': {str(e)[:200]}", "stages": stages}
        return {"ok": True, "stages": stages}

    def _multicom_client(self, override_url=None, override_key=None):
        from .telephony import ProviderNotConfigured
        from .providers.base import resolve_secret
        from .providers.multicom import DEFAULT_API_KEY_ENV, MulticomClient
        mcfg = (self.settings or {}).get("multicom") or {}
        url = str(override_url or mcfg.get("api_url") or "https://api.multicom.ru/v1").strip()
        key = str(override_key or resolve_secret(mcfg, "api_key", "api_key_env", DEFAULT_API_KEY_ENV) or "").strip()
        if not key:
            raise ProviderNotConfigured(
                "Мультиком не настроен: заполните API Key в настройках Мультиком.")
        return MulticomClient(
            api_url=url,
            api_key=key,
            account_id=mcfg.get("account_id"),
            sip_host=mcfg.get("sip_host"),
            sip_user=mcfg.get("sip_user"),
            sip_secret=mcfg.get("sip_secret"),
            timeout_sec=mcfg.get("timeout_sec", 15)
        )

    def multicom_check(self, override_url=None, override_key=None):
        from .providers.base import ProviderApiError
        client = self._multicom_client(override_url, override_key)
        try:
            info = client.get_account_info()
            return {"ok": True, "account": info}
        except ProviderApiError as e:
            return {"ok": False, "detail": str(e)[:300]}
        except Exception as e:
            return {"ok": False, "detail": f"Ошибка соединения Мультиком: {str(e)[:200]}"}

    def multicom_pool_sync(self):
        client = self._multicom_client()
        nums = client.get_numbers()
        added = 0
        for n in nums:
            phone = str(n.get("number") or n.get("phone") or "").strip() if isinstance(n, dict) else str(n)
            if phone:
                exists = db.fetch1("SELECT id FROM numbers WHERE number=? OR phone=?", (phone, phone))
                if not exists:
                    db.insert("numbers", {
                        "number": phone,
                        "label": "multicom",
                        "provider": "multicom",
                        "enabled_outgoing": 1,
                        "daily_limit": 100,
                        "weight": 1
                    })
                    added += 1
        return {"ok": True, "added": added, "total_remote": len(nums)}

    def _duration(self, call):
        st = _iso_to_dt(call.get("answered_at") or call.get("started_at"))
        if not st:
            return 0
        return max(0, int((datetime.datetime.now() - st).total_seconds()))

    def _on_answered(self, call, item, human):
        if not human:
            db.q("UPDATE calls SET status='machine' WHERE id=?", (call["id"],))
            self.provider.hangup(call["id"])
            self._finish_attempt(call, item, "machine", "Автоответчик", retryable=True)
            return
        if call.get("number_id"):
            try:
                numbers_mod.mark_answered(call["number_id"])
            except Exception as e:
                print("[engine] mark_answered:", e)
        campaign = db.fetch1("SELECT * FROM campaigns WHERE id=?", (call["campaign_id"],)) if call["campaign_id"] else None
        flow = (campaign or {}).get("flow") or "message"
        if flow == "message" and (call.get("direction") or "out") == "out" \
                and getattr(self.provider, "name", "") == "megafon_vats":
            # REST API ВАТС не передаёт аудио: «доставить сообщение» нечем.
            # Честный терминальный результат вместо ложного done_ok (§65 ТЗ).
            self._finish_attempt(call, item, "no_media",
                                 "flow=message невозможен: REST API ВАТС не передаёт аудио "
                                 "(нужен Asterisk с медиа-слоем)", retryable=False)
            return
        if flow == "operator":
            self._to_operator_queue(call, item, campaign)
        elif flow == "agent":
            self._run_agent(call, item, campaign)
        else:  # message
            db.q("UPDATE calls SET status='talk' WHERE id=?", (call["id"],))
            if item:
                db.update("campaign_items", {"status": "talk"}, "id=?", (item["id"],))
            # имитация длительности озвучки (реальные провайдеры: медиа-слой/TTS)
            time.sleep(0.05 if config.FAST else 1.0)
            db.q("UPDATE calls SET status='talk_done' WHERE id=?", (call["id"],))
            self.provider.hangup(call["id"])
            self._finish_attempt(call, item, "done_ok", "Сообщение доставлено", ok=True)

    def _run_agent(self, call, item, campaign):
        db.q("UPDATE calls SET status='agent' WHERE id=?", (call["id"],))
        if item:
            db.update("campaign_items", {"status": "agent"}, "id=?", (item["id"],))
        contact = db.fetch1("SELECT * FROM contacts WHERE id=?", (call["contact_id"],)) if call["contact_id"] else {}
        contact = contact or {}
        llm = agent_mod.AgentLLM(self.settings)
        if llm.available():
            try:
                prompt = llm.build_prompt(contact, campaign.get("llm_criteria", ""))
                out = llm.chat(prompt)
                result = llm.parse_result(out)
                result["engine"] = "llm"
            except Exception as e:
                result = {"qualified": False, "engine": "llm_error", "summary": str(e)}
        else:
            ch = self.provider.make_channel(call["id"], call["contact_phone"])
            if ch is None:
                # Провайдер без интерактивного канала (не sim): голосовой диалог
                # невозможен — фиксируем честный результат вместо падения.
                result = {"qualified": False, "engine": "no_channel", "answers": [],
                          "summary": "Провайдер '{}' не поддерживает голосовой диалог".format(
                              getattr(self.provider, "name", "?")),
                          "transcript": ""}
            else:
                result = agent_mod.run_scripted(
                    ch,
                    campaign["template_id"],
                    contact,
                    scenario_override=campaign.get("scenario") or None,
                )
                result["engine"] = "scripted_l1"
        db.q("UPDATE calls SET agent_result=? WHERE id=?", (json.dumps(result, ensure_ascii=False), call["id"]))
        db.insert("agent_sessions", {"call_id": call["id"], "scenario": "{}",
                                     "result": json.dumps(result, ensure_ascii=False),
                                     "transcript": result.get("transcript", result.get("summary", "")),
                                     "created": now_iso(), "ended_at": now_iso()})
        qualified = bool(result.get("qualified"))
        if qualified and campaign.get("connect_on_qualify"):
            self._to_operator_queue(call, item, campaign, agent=result)
        else:
            db.q("UPDATE calls SET status='agent_done' WHERE id=?", (call["id"],))
            self.provider.hangup(call["id"])
            if item:
                db.update("campaign_items", {"status": "done_agent"},
                          "id=?", (item["id"],))
                db.q("UPDATE campaign_items SET completed_at=? WHERE id=?", (now_iso(), item["id"]))
            db.q("UPDATE calls SET status='done', ended_at=?, duration_sec=?, result=?, agent_result=? WHERE id=?",
                 (now_iso(), self._duration(call),
                  "qualified_yes" if qualified else "qualified_no",
                  json.dumps(result, ensure_ascii=False), call["id"]))
            self._push_crm(call, "done_agent")
            events.publish("agent", {"call_id": call["id"], "qualified": qualified})

    def _to_operator_queue(self, call, item, campaign, agent=None):
        if item:
            db.update("campaign_items", {"status": "wait_operator"}, "id=?", (item["id"],))
        db.q("UPDATE calls SET status='wait_operator' WHERE id=?", (call["id"],))
        contact = db.fetch1("SELECT * FROM contacts WHERE id=?", (call["contact_id"],)) if call["contact_id"] else {}
        contact = contact or {}
        acd_id = db.insert("acd", {"call_id": call["id"], "item_id": call["item_id"], "contact_id": call["contact_id"],
                                   "contact_name": contact.get("name", ""), "contact_phone": contact.get("phone", ""),
                                   "campaign_id": campaign["id"], "operator_id": 0, "status": "queued",
                                   "created": now_iso(), "updated": now_iso()})
        events.publish("acd", {"id": acd_id, "call_id": call["id"], "phone": contact.get("phone", ""),
                               "name": contact.get("name", ""), "campaign_id": campaign["id"],
                               "status": "queued", "agent": bool(agent)})

    def _on_dropped(self, call, item):
        # Канал закрылся, пока мы ждали оператора (нет принятия в срок) или истёк таймаут провайдера
        if call["status"] == "wait_operator":
            self._no_operator_finish(call, item, reason="Оператор не ответил")
        else:
            self._finish_attempt(call, item, "timeout", "Канал закрыт по таймауту", retryable=True)

    def _no_operator_finish(self, call, item, reason="Оператор не ответил"):
        """Завершить звонок, ждавший оператора: статусы + задача «перезвонить» в CRM."""
        call = db.fetch1("SELECT * FROM calls WHERE id=?", (call["id"],))
        if not call or call["status"] != "wait_operator":
            return
        ended = now_iso()
        db.q("UPDATE calls SET status='done', ended_at=?, duration_sec=?, result='no_operator', detail=? WHERE id=?",
             (ended, self._duration(call), reason[:200], call["id"]))
        if item:
            db.q("UPDATE campaign_items SET status='no_operator', completed_at=? WHERE id=?", (ended, item["id"]))
        db.q("UPDATE acd SET status='missed', updated=? WHERE call_id=?", (ended, call["id"]))
        try:
            self.crm.create_task("Перезвонить клиенту", "Клиент {} {} ждал оператора, звонок #{} — {}".format(
                call.get("contact_name", ""), call.get("contact_phone", ""), call["id"], reason))
        except Exception as e:
            print("[crm] create_task:", e)
        self._push_crm(call, "no_operator")
        events.publish("call", {"id": call["id"], "status": "no_operator"})

    def _push_crm(self, call, final_status):
        row = None
        try:
            row = db.fetch1("SELECT * FROM calls WHERE id=?", (call["id"],))
            if row:
                self.crm.push_result(row)
        except Exception as e:
            # Результат НЕ теряем: кладём в CRM outbox — движок повторит
            # доставку с backoff (_crm_outbox_tick). Для amoCRM это защита
            # от 401/429/5xx/сетевых сбоев и рестартов.
            print("[crm] push_result:", e)
            try:
                if row is None and call and call.get("id"):
                    row = db.fetch1("SELECT * FROM calls WHERE id=?", (call["id"],))
                if row:
                    db.crm_outbox_enqueue("push_result", row, error=e)
            except Exception as e2:
                print("[crm] outbox enqueue failed:", e2)

    # ---------- CRM outbox: повторная доставка результатов ----------
    def _crm_outbox_tick(self, limit=5):
        """Отправить созревшие записи CRM outbox (вызывается тиком движка).

        kind=push_result: payload — снапшот записи calls на момент сбоя.
        uniq звонка (ats-call-<id>) делает повтор идемпотентным на стороне
        amoCRM: дубль не создастся, даже если первый POST дошёл, а ответ
        потерялся по сети.
        """
        rows = db.crm_outbox_due(limit=limit)
        if not rows:
            return
        for row in rows:
            try:
                payload = json.loads(row.get("payload_json") or "{}")
            except Exception as e:
                db.crm_outbox_mark_retry(row["id"], int(row.get("attempts") or 0) + 1,
                                         f"bad payload json: {e}")
                continue
            try:
                self.crm.push_result(payload)
                db.crm_outbox_mark_done(row["id"])
                print(f"[crm] outbox #{row['id']} доставлен после "
                      f"{row.get('attempts', 0)} повторов")
            except Exception as e:
                ok = db.crm_outbox_mark_retry(
                    row["id"], int(row.get("attempts") or 0) + 1, e)
                if not ok:
                    print(f"[crm] outbox #{row['id']} НЕ доставлен "
                          f"(превышены повторы): {e}")

    # ---------- завершение попытки / автодозвон ----------
    def _finish_attempt(self, call, item, result, detail, ok=False, retryable=False):
        ended = now_iso()
        db.q("UPDATE calls SET status='done', ended_at=?, duration_sec=?, result=?, detail=? WHERE id=?",
             (ended, self._duration(call), result, detail[:300], call["id"]))
        if not item:
            return
        attempts = int(item.get("attempts") or 0)
        campaign = db.fetch1("SELECT * FROM campaigns WHERE id=?", (item["campaign_id"],)) if item["campaign_id"] else None
        retry_max = int((campaign or {}).get("retry_max", self.settings.get("retry_max", 2)))
        if retry_max < 0:
            retry_max = int(self.settings.get("retry_max", 2))
        if ok:
            new_status = result
            db.q("UPDATE campaign_items SET status=?, last_result=?, completed_at=? WHERE id=?",
                 (new_status, detail, ended, item["id"]))
        elif retryable and attempts < retry_max:
            new_status = "queued"
            delay = self._retry_delay(item, campaign, result)
            nxt = (datetime.datetime.now() + datetime.timedelta(minutes=delay)).strftime("%Y-%m-%d %H:%M:%S")
            db.q("UPDATE campaign_items SET status='queued', last_result=?, next_attempt_at=? WHERE id=?",
                 (detail, nxt, item["id"]))
        else:
            final = result if result != "timeout" else "exhausted"
            new_status = "exhausted" if retryable else final
            db.q("UPDATE campaign_items SET status=?, last_result=?, completed_at=? WHERE id=?",
                 (new_status, detail, ended, item["id"]))
        db.insert("attempts", {"call_id": call["id"], "item_id": item["id"], "attempt_no": attempts,
                               "started_at": call.get("started_at", ""), "ended_at": ended,
                               "result": result, "detail": detail[:300]})
        # Публикуем НОВЫЙ статус позиции, а не тот, что был на момент чтения
        # (item["status"] здесь протухший — например, 'dialing').
        events.publish("item", {"id": item["id"], "status": new_status})
        # CRM: финальные состояния (кроме промежуточных повторов)
        if ok or (not retryable) or attempts >= retry_max:
            self._push_crm(call, result)

    def _retry_delay(self, item, campaign, result=""):
        """Интервал повтора: приоритет у retry_map кампании по причине ({"busy":10,...}),
        иначе базовый retry_delay_min с эскалацией от числа попыток."""
        # 1) точная настройка по причине (кампания)
        if campaign:
            try:
                rm = json.loads(campaign.get("retry_map") or "{}")
            except Exception:
                rm = {}
            if result in rm and rm.get(result):
                return max(1, min(int(rm[result]), 24 * 60))
        # 2) базовый интервал с эскалацией
        base = int((campaign or {}).get("retry_delay_min", self.settings.get("retry_delay_min", 15)))
        if base < 0:
            base = int(self.settings.get("retry_delay_min", 15))
        attempts = int(item.get("attempts") or 0)
        return min(max(1, base) * (attempts + 1), 24 * 60)

    # ---------- распределение звонков ----------
    def active_channels(self):
        r = db.fetch1("SELECT COUNT(*) c FROM calls WHERE ended_at='' AND status NOT IN ('new')")
        return r["c"] if r else 0

    def _allocate(self):
        settings = db.get_settings()
        self.settings = settings
        numbers_mod.reset_daily_if_needed()
        camps = db.fetch("SELECT * FROM campaigns WHERE status='running' ORDER BY id")
        max_ch = int(settings.get("max_channels", 3))
        if max_ch < 1:
            max_ch = 1
        for camp in camps:
            try:
                schedule = json.loads(camp.get("schedule") or "{}")
            except Exception:
                schedule = {}
            if not in_window(schedule, settings):
                continue
            camp_max = int(camp.get("max_channels") or max_ch)
            camp_max = min(camp_max, max_ch)
            used = 0
            while self.active_channels() < max_ch and used < camp_max:
                used += 1
                if not self._start_one(camp, settings):
                    break

    def _start_one(self, camp, settings):
        due = db.fetch(
            "SELECT ci.* FROM campaign_items ci WHERE ci.campaign_id=? AND ci.status='queued'"
            " AND (ci.next_attempt_at='' OR ci.next_attempt_at<=?) ORDER BY ci.id LIMIT 1",
            (camp["id"], now_iso()))
        if not due:
            return False
        item = due[0]
        contact = db.fetch1("SELECT * FROM contacts WHERE id=?", (item["contact_id"],))
        if not contact:
            db.q("UPDATE campaign_items SET status='error', completed_at=? WHERE id=?", (now_iso(), item["id"]))
            return False
        if settings.get("consent_required", True) and not contact.get("consent"):
            db.q("UPDATE campaign_items SET status='blocked_no_consent', last_result='Нет согласия', completed_at=? WHERE id=?",
                 (now_iso(), item["id"]))
            events.publish("item", {"id": item["id"], "status": "blocked_no_consent"})
            return True
        if contact.get("blacklisted"):
            db.q("UPDATE campaign_items SET status='blacklisted', last_result='В чёрном списке', completed_at=? WHERE id=?",
                 (now_iso(), item["id"]))
            return True
        num = numbers_mod.acquire(provider=self.provider.name,
                                  cooldown_sec=int(settings.get("line_cooldown_sec", 5)))
        if not num:
            return False  # пул исчерпан/остывает — ждём следующего тика
        template = db.fetch1("SELECT * FROM templates WHERE id=?", (camp["template_id"],))
        text = ""
        if template:
            t = template.get("text") or ""
            for k, v in (("name", contact.get("name", "")), ("phone", contact.get("phone", "")),
                         ("group", contact.get("grp", "")), ("note", contact.get("note", ""))):
                t = t.replace("{" + k + "}", str(v or ""))
            text = t
        started = now_iso()
        call_id = db.insert("calls", {
            "campaign_id": camp["id"], "item_id": item["id"], "contact_id": contact["id"],
            "contact_name": contact.get("name", ""), "contact_phone": contact.get("phone", ""),
            "caller_id": num["number"], "number_id": num["id"], "provider": self.provider.name,
            "direction": "out", "status": "dialing", "result": "", "detail": "",
            "agent_result": "", "recording": "", "started_at": started, "answered_at": "",
            "ended_at": "", "duration_sec": 0})
        attempts = int(item.get("attempts") or 0) + 1
        db.q("UPDATE campaign_items SET status='dialing', attempts=?, next_attempt_at='', updated=? WHERE id=?",
             (attempts, started, item["id"]))
        try:
            self.provider.dial({"call_id": call_id, "phone": contact["phone"], "caller_id": num["number"],
                                "flow": camp["flow"], "text": text, "template_id": camp["template_id"]})
        except Exception as e:
            db.q("UPDATE calls SET status='done', ended_at=?, result='failed', detail=? WHERE id=?",
                 (now_iso(), str(e)[:300], call_id))
            self._finish_attempt(db.fetch1("SELECT * FROM calls WHERE id=?", (call_id,)), item, "failed", str(e)[:200], retryable=True)
            return True
        try:
            _eid = getattr(self.provider, "external_id", None)
            _ext = _eid(call_id) if callable(_eid) else {}
        except Exception:
            _ext = {}
        if isinstance(_ext, dict) and _ext.get("callid"):
            # Корреляция вебхуков переживает рестарт (иначе — orphan-события).
            db.q("UPDATE calls SET external_call_id=? WHERE id=?",
                 (str(_ext["callid"])[:64], call_id))
            if _ext.get("clid"):
                # Фактический исходящий номер от ВАТС честнее пулового.
                db.q("UPDATE calls SET caller_id=? WHERE id=?",
                     (str(_ext["clid"])[:32], call_id))
        numbers_mod.mark_used(num["id"], int(settings.get("line_cooldown_sec", 5)))
        events.publish("call", {"id": call_id, "status": "dialing", "phone": contact["phone"],
                                "caller_id": num["number"], "campaign": camp["name"]})
        return True

    # ---------- watchdog ----------
    def _watchdog(self):
        settings = db.get_settings()
        timeout_min = int(settings.get("watchdog_timeout_min", 20))
        stale = db.fetch(
            "SELECT * FROM calls WHERE ended_at='' AND status IN ('dialing','ringing')"
            " AND started_at<=?", (self._ago(timeout_min),))
        for c in stale:
            item = db.fetch1("SELECT * FROM campaign_items WHERE id=?", (c["item_id"],)) if c["item_id"] else None
            self._finish_attempt(c, item, "timeout", "Зависший вызов (watchdog)", retryable=True)
            try:
                self.provider.hangup(c["id"])
            except Exception:
                pass
        # ACD: оператор не принял звонок в срок -> no_operator + задача на перезвон.
        # Исключение — исходящие makecall-разговоры МегаФона (external_call_id
        # задан): там сотрудника с клиентом уже соединила сама ВАТС, «ожидания»
        # нет — финал придёт через COMPLETED/history. Короткий таймаут их не
        # трогает, иначе живой разговор ложно закроется как no_operator.
        acd_timeout_sec = int(settings.get("acd_wait_timeout_sec", 60))
        if acd_timeout_sec > 0:
            waiting = db.fetch(
                "SELECT * FROM calls WHERE ended_at='' AND status='wait_operator' AND started_at<=? "
                "AND NOT (provider='megafon_vats' AND external_call_id<>'')",
                (self._ago(acd_timeout_sec / 60.0),))
            for c in waiting:
                item = db.fetch1("SELECT * FROM campaign_items WHERE id=?", (c["item_id"],)) if c["item_id"] else None
                self._no_operator_finish(c, item, reason="Оператор не принял звонок за {} с".format(acd_timeout_sec))
                try:
                    self.provider.hangup(c["id"])
                except Exception:
                    pass
        # Страховка для VATS-разговоров: если ВАТС не прислала финал
        # (потерян вебхук, обрыв связи) — не висим в wait_operator вечно,
        # а закрываем timeout+ретрай. Нормальный путь — COMPLETED/history,
        # сюда попадаем только при потере событий.
        vats_stuck_min = int(settings.get("vats_conversation_timeout_min", 30))
        if vats_stuck_min > 0:
            stuck = db.fetch(
                "SELECT * FROM calls WHERE ended_at='' AND status='wait_operator' "
                "AND provider='megafon_vats' AND external_call_id<>'' AND started_at<=?",
                (self._ago(vats_stuck_min),))
            for c in stuck:
                item = db.fetch1("SELECT * FROM campaign_items WHERE id=?", (c["item_id"],)) if c["item_id"] else None
                print("[megafon] СТРАХОВКА: звонок {} в wait_operator дольше {} мин "
                      "без финала ВАТС — timeout+ретрай".format(c["id"], vats_stuck_min))
                db.q("UPDATE acd SET status='missed', updated=? WHERE call_id=?",
                     (now_iso(), c["id"]))
                self._finish_attempt(c, item, "timeout",
                                     "Нет финала от ВАТС за {} мин".format(vats_stuck_min),
                                     retryable=True)

        # ACD: accept завис между фазами (обрыв HTTP, падение провайдера) —
        # возвращаем звонок в очередь, оператора освобождаем (если не на бридже).
        if acd_timeout_sec > 0:
            stuck = db.fetch(
                "SELECT * FROM acd WHERE status IN ('ringing','answered') AND updated<=?",
                (self._ago(max(1.0, acd_timeout_sec / 60.0)),))
            for a in stuck:
                call = db.fetch1("SELECT * FROM calls WHERE id=?", (a["call_id"],))
                if call and call["status"] in ("operator_ringing", "operator_answered"):
                    db.q("UPDATE calls SET status='wait_operator' WHERE id=?", (call["id"],))
                db.q("UPDATE acd SET status='queued', operator_id=0, updated=? WHERE id=?",
                     (now_iso(), a["id"]))
                if a["operator_id"] and not self._op_has_live_claim(a["operator_id"], a["id"]):
                    db.q("UPDATE operators SET status='free', updated=? "
                         "WHERE id=? AND status='busy'", (now_iso(), a["operator_id"]))
                events.publish("acd", {"id": a["id"], "status": "requeued"})
                print("[acd] зависший accept {} возвращён в очередь".format(a["id"]))

    def _ago(self, minutes):
        # minutes может быть дробным (acd_timeout_sec/60.0) — int() обнулял бы
        # таймауты < 60 секунд, и watchdog снимал бы вызовы мгновенно.
        return (datetime.datetime.now() - datetime.timedelta(minutes=float(minutes))).strftime("%Y-%m-%d %H:%M:%S")

    # ---------- ACD (операторы) ----------
    def acd_queued(self):
        return db.fetch("SELECT * FROM acd WHERE status IN ('queued','offered') ORDER BY id")

    def operators(self):
        return db.fetch("SELECT * FROM operators ORDER BY id")

    def accept_acd(self, acd_id, operator_id=None):
        """ACD FSM (§36 ТЗ): queued → ringing → answered → bridged.

        Статусы «ок» — только после реального бриджа; провал/исключение —
        откат в очередь. 'accepted' — legacy-статус старых строк (читается
        наравне с 'bridged', новым кодом не выставляется)."""
        acd = db.fetch1("SELECT * FROM acd WHERE id=?", (acd_id,))
        if not acd or acd["status"] not in ("queued", "offered"):
            return False, "acd_not_queued"
        if operator_id:
            op = db.fetch1("SELECT * FROM operators WHERE id=?", (operator_id,))
            if not op:
                return False, "operator_not_found"
            if op.get("status") != "free":
                return False, "operator_not_free"
        else:
            op = db.fetch1("SELECT * FROM operators WHERE status='free' ORDER BY id LIMIT 1")
            if not op:
                return False, "no_free_operator"
        operator_id = op["id"]
        if getattr(self.provider, "needs_operator_ext", False) \
                and not (op.get("ext") or "").strip():
            # Без ext бридж построить не на чем — мгновенный «accept»
            # был бы ложным успехом.
            return False, "operator_no_ext"
        call = db.fetch1("SELECT * FROM calls WHERE id=?", (acd["call_id"],))
        if not call or call.get("ended_at") or call.get("status") == "done":
            return False, "call_ended"
        # Резервируем слоты ДО звонка провайдеру (иначе двойной accept).
        now = now_iso()
        db.q("UPDATE acd SET status='ringing', operator_id=?, updated=? WHERE id=?",
             (operator_id, now, acd_id))
        db.q("UPDATE calls SET status='operator_ringing' WHERE id=?", (call["id"],))
        db.q("UPDATE operators SET status='busy', updated=? WHERE id=?", (now, operator_id))
        events.publish("acd", {"id": acd_id, "status": "ringing",
                               "operator": op.get("name", "")})
        events.publish("call", {"id": call["id"], "status": "operator_ringing",
                                "phone": call.get("contact_phone", "")})

        def _progress(phase):
            if phase == "answered":
                db.q("UPDATE acd SET status='answered', updated=? WHERE id=?",
                     (now_iso(), acd_id))
                db.q("UPDATE calls SET status='operator_answered' WHERE id=?",
                     (call["id"],))
                events.publish("acd", {"id": acd_id, "status": "answered"})
                events.publish("call", {"id": call["id"], "status": "operator_answered",
                                        "phone": call.get("contact_phone", "")})
            elif phase == "ringing":
                events.publish("acd", {"id": acd_id, "status": "ringing"})

        connect = getattr(self.provider, "connect_operator", None)
        try:
            import inspect as _inspect
            _nparams = len(_inspect.signature(connect).parameters) if connect else 0
        except (TypeError, ValueError):
            _nparams = 3
        try:
            if not connect:
                bridged = True  # провайдер без бриджа — формально (sim/legacy)
            elif _nparams >= 3:
                bridged = connect(call["id"], op.get("ext") or op.get("vats_login") or "",
                                  _progress)
            else:
                bridged = connect(call["id"], op.get("ext") or "")
        except Exception as e:
            bridged = False
            print("[acd] connect_operator failed:", e)
        if bridged is False:
            self._acd_rollback(acd_id, call["id"], operator_id)
            events.publish("acd", {"id": acd_id, "status": "transfer_failed"})
            return False, "transfer_failed"
        now = now_iso()
        db.q("UPDATE acd SET status='bridged', updated=? WHERE id=?", (now, acd_id))
        db.q("UPDATE calls SET status='operator_connected', result='operator_ok' WHERE id=?",
             (call["id"],))
        if getattr(self.provider, "name", "") == "megafon_vats":
            vu = (op.get("vats_login") or "").strip()
            pu = (call.get("provider_user") or "").strip()
            if vu and pu and vu != pu:
                db.q("UPDATE calls SET detail=? WHERE id=?",
                     ((call.get("detail") or "") +
                       " (ВАТС-бридж на {}; принял {})".format(pu, vu))[:300],
                      call["id"])
        item = db.fetch1("SELECT * FROM campaign_items WHERE id=?", (acd["item_id"],)) if acd["item_id"] else None
        if item:
            db.q("UPDATE campaign_items SET status='operator_ok', completed_at=? WHERE id=?",
                 (now, item["id"]))
        self._sync_vats_presence(operator_id)
        events.publish("acd", {"id": acd_id, "status": "bridged",
                               "operator": op.get("name", "")})
        events.publish("call", {"id": call["id"], "status": "operator_connected",
                                "phone": call.get("contact_phone", "")})
        return True, "ok"

    def _op_has_live_claim(self, operator_id, exclude_acd_id=0):
        row = db.fetch1("SELECT id FROM acd WHERE operator_id=? AND id<>? "
                        "AND status IN ('ringing','answered','bridged','accepted') LIMIT 1",
                        (operator_id, exclude_acd_id))
        return bool(row)

    def _acd_rollback(self, acd_id, call_id, operator_id):
        """Откат принятия: только если статусы не ушли дальше (параллельно мог
        прийти COMPLETED — воскрешать завершённый звонок запрещено)."""
        now = now_iso()
        acd = db.fetch1("SELECT * FROM acd WHERE id=?", (acd_id,))
        if acd and acd["status"] in ("ringing", "answered"):
            db.q("UPDATE acd SET status='queued', operator_id=0, updated=? WHERE id=?",
                 (now, acd_id))
        call = db.fetch1("SELECT * FROM calls WHERE id=?", (call_id,))
        if call and call["status"] in ("operator_ringing", "operator_answered"):
            db.q("UPDATE calls SET status='wait_operator' WHERE id=?", (call_id,))
        op = db.fetch1("SELECT * FROM operators WHERE id=?", (operator_id,))
        if op and op.get("status") == "busy" \
                and not self._op_has_live_claim(operator_id, acd_id):
            db.q("UPDATE operators SET status='free', updated=? WHERE id=?",
                 (now, operator_id))

    def _sync_vats_presence(self, operator_id):
        """Best-effort presence: статус оператора ATS → приём звонков в ВАТС.
        free → доступен (POST dnd), остальное → недоступен (DELETE dnd).
        Только при глобальном провайдере megafon_vats и заданном vats_login.
        Ошибки ВАТС — в лог; статус в ATS не откатываем."""
        if not operator_id:
            return
        try:
            if getattr(self.provider, "name", "") != "megafon_vats":
                return
            op = db.fetch1("SELECT * FROM operators WHERE id=?", (operator_id,))
            login = (op.get("vats_login") or "").strip() if op else ""
            if not login:
                return
            client = getattr(self.provider, "client", None)
            if client is None:
                return
            client.set_dnd(login, (op.get("status") or "") == "free")
        except Exception as e:
            print("[megafon] presence sync failed:", e)

    def complete_operator_call(self, call_id, operator_id):
        call = db.fetch1("SELECT * FROM calls WHERE id=?", (call_id,))
        if not call:
            return False, "not_found"
        if call["ended_at"]:
            db.q("UPDATE operators SET status='free', updated=? WHERE id=?", (now_iso(), operator_id))
            self._sync_vats_presence(operator_id)
            return True, "already_ended"
        ended = now_iso()
        db.q("UPDATE calls SET status='done', ended_at=?, duration_sec=?, result='operator_ok' WHERE id=?",
             (ended, self._duration(call), call_id))
        db.q("UPDATE operators SET status='free', updated=? WHERE id=?", (now_iso(), operator_id))
        self._sync_vats_presence(operator_id)
        db.q("UPDATE acd SET status='completed', updated=? WHERE call_id=?", (ended, call_id))
        item = db.fetch1("SELECT * FROM campaign_items WHERE id=?", (call["item_id"],)) if call["item_id"] else None
        if item and item["status"] != "operator_ok":
            db.q("UPDATE campaign_items SET status='operator_ok', completed_at=? WHERE id=?", (ended, item["id"]))
        self.provider.hangup(call_id)
        self._push_crm(call, "operator_ok")
        events.publish("call", {"id": call_id, "status": "done"})
        return True, "ok"

    def operator_status(self, operator_id, status):
        st = status if status in ("free", "break", "offline", "busy") else "free"
        db.q("UPDATE operators SET status=?, updated=? WHERE id=?", (st, now_iso(), operator_id))
        self._sync_vats_presence(operator_id)

    # ---------- тик ----------
    def tick_once(self):
        for ev in self.drain_events():
            self.handle_event(ev)
        self._allocate()
        self._watchdog()
        try:
            self._crm_outbox_tick()
        except Exception as e:
            print("[crm] outbox tick:", e)

    def run_until_idle(self, max_seconds=10, idle_ticks=5):
        """Для тестов: крутить тики, пока не завершатся активные звонки."""
        deadline = time.time() + max_seconds
        quiet = 0
        while time.time() < deadline:
            self.tick_once()
            active = self.active_channels()
            queued = db.fetch("SELECT COUNT(*) c FROM campaign_items WHERE status IN ('queued','dialing','agent','wait_operator','talk')")[0]["c"]
            if active == 0 and queued == 0:
                quiet += 1
                if quiet >= idle_ticks:
                    return True
            else:
                quiet = 0
            time.sleep(0.05 if config.FAST else 0.2)
        return False

    # ---------- вспомогательные для UI/API ----------
    def dashboard(self):
        today = datetime.date.today().isoformat()
        calls_today = db.fetch("SELECT COUNT(*) c, SUM(CASE WHEN result IN ('done_ok','operator_ok','done_agent') THEN 1 ELSE 0 END) ok"
                               " FROM calls WHERE started_at>=?", (today,))
        items = db.fetch("SELECT status, COUNT(*) c FROM campaign_items GROUP BY status")
        active_camps = db.fetch("SELECT * FROM campaigns WHERE status='running'")
        active_calls = db.fetch(
            "SELECT * FROM calls WHERE (ended_at='' OR ended_at IS NULL) "
            "AND status NOT IN ('new') ORDER BY id DESC LIMIT 100"
        )
        active_channel_count = self.active_channels()
        number_rows = numbers_mod.pool_state()
        active_number_count = sum(
            1 for n in number_rows
            if n.get("active") and n.get("enabled_outgoing") and not n.get("quarantined")
        )
        return {
            "campaigns_running": len(active_camps),
            "calls_today": calls_today[0]["c"] if calls_today else 0,
            "ok_today": calls_today[0]["ok"] or 0,
            "items": {r["status"]: r["c"] for r in items},
            # Keep the original numeric counter for API compatibility and add
            # the actual rows needed by the React dashboard.
            "active_channels": active_channel_count,
            "active_channels_count": active_channel_count,
            "active_calls": active_calls,
            "numbers": number_rows,
            "numbers_summary": {
                "active_count": active_number_count,
                "total_count": len(number_rows),
            },
            "operators": self.operators(),
            "acd_queued": len(self.acd_queued()),
        }
