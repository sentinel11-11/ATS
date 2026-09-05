# -*- coding: utf-8 -*-
"""Движок ATS v2 (замена dispatch_loop из server.py).

Поток-диспетчер: обработка событий провайдера, автодозвон (кампании -> попытки -> пул номеров),
повторы (busy/no_answer/machine/failed), watchdog «зависших» звонков, сценарии:
message (информирование), agent (ИИ-агент L1/L2), operator (перевод на сотрудника/ACD).
"""
import datetime
import json
import queue
import threading
import time
import uuid

from . import agent as agent_mod
from . import config, db, events, numbers as numbers_mod
from .crm import make_crm
from .telephony import make_provider

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
            print("[engine] Провайдер недоступен, переход на sim:", e)
            from .telephony import SimProvider
            self.provider = SimProvider()
        self.provider.attach(self._evq)
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
    def drain_events(self):
        got = []
        while True:
            try:
                got.append(self._evq.get_nowait())
            except queue.Empty:
                break
        return got

    def handle_event(self, ev):
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
        campaign = db.fetch1("SELECT * FROM campaigns WHERE id=?", (call["campaign_id"],)) if call["campaign_id"] else None
        flow = (campaign or {}).get("flow") or "message"
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
            result = agent_mod.run_scripted(ch, campaign["template_id"], contact)
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
        try:
            row = db.fetch1("SELECT * FROM calls WHERE id=?", (call["id"],))
            if row:
                self.crm.push_result(row)
        except Exception as e:
            print("[crm] push_result:", e)

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
            db.q("UPDATE campaign_items SET status=?, last_result=?, completed_at=? WHERE id=?",
                 (result, detail, ended, item["id"]))
        elif retryable and attempts < retry_max:
            delay = self._retry_delay(item, campaign, result)
            nxt = (datetime.datetime.now() + datetime.timedelta(minutes=delay)).strftime("%Y-%m-%d %H:%M:%S")
            db.q("UPDATE campaign_items SET status='queued', last_result=?, next_attempt_at=? WHERE id=?",
                 (detail, nxt, item["id"]))
        else:
            final = result if result != "timeout" else "exhausted"
            db.q("UPDATE campaign_items SET status=?, last_result=?, completed_at=? WHERE id=?",
                 ("exhausted" if retryable else final, detail, ended, item["id"]))
        db.insert("attempts", {"call_id": call["id"], "item_id": item["id"], "attempt_no": attempts,
                               "started_at": call.get("started_at", ""), "ended_at": ended,
                               "result": result, "detail": detail[:300]})
        events.publish("item", {"id": item["id"], "status": item["status"]})
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
        # ACD: оператор не принял звонок в срок -> no_operator + задача на перезвон
        acd_timeout_sec = int(settings.get("acd_wait_timeout_sec", 60))
        if acd_timeout_sec > 0:
            waiting = db.fetch(
                "SELECT * FROM calls WHERE ended_at='' AND status='wait_operator' AND started_at<=?",
                (self._ago(acd_timeout_sec / 60.0),))
            for c in waiting:
                item = db.fetch1("SELECT * FROM campaign_items WHERE id=?", (c["item_id"],)) if c["item_id"] else None
                self._no_operator_finish(c, item, reason="Оператор не принял звонок за {} с".format(acd_timeout_sec))
                try:
                    self.provider.hangup(c["id"])
                except Exception:
                    pass

    def _ago(self, minutes):
        return (datetime.datetime.now() - datetime.timedelta(minutes=int(minutes))).strftime("%Y-%m-%d %H:%M:%S")

    # ---------- ACD (операторы) ----------
    def acd_queued(self):
        return db.fetch("SELECT * FROM acd WHERE status IN ('queued','offered') ORDER BY id")

    def operators(self):
        return db.fetch("SELECT * FROM operators ORDER BY id")

    def accept_acd(self, acd_id, operator_id=None):
        acd = db.fetch1("SELECT * FROM acd WHERE id=?", (acd_id,))
        if not acd or acd["status"] not in ("queued", "offered"):
            return False, "acd_not_queued"
        if operator_id:
            op = db.fetch1("SELECT * FROM operators WHERE id=?", (operator_id,))
        else:
            op = db.fetch1("SELECT * FROM operators WHERE status IN ('free','offline') ORDER BY id LIMIT 1")
            if not op:  # демо: авто-оператор
                op_id = db.insert("operators", {"user_id": 0, "name": "Оператор #{}".format(uuid.uuid4().hex[:4]),
                                                "ext": "", "status": "busy", "updated": now_iso()})
                op = db.fetch1("SELECT * FROM operators WHERE id=?", (op_id,))
        if not op:
            return False, "operator_not_found"
        operator_id = op["id"]
        db.q("UPDATE acd SET status='accepted', operator_id=?, updated=? WHERE id=?",
             (operator_id, now_iso(), acd_id))
        db.q("UPDATE operators SET status='busy', updated=? WHERE id=?", (now_iso(), operator_id))
        db.q("UPDATE calls SET status='operator_connected', result='operator_ok' WHERE id=?",
             (acd["call_id"],))
        item = db.fetch1("SELECT * FROM campaign_items WHERE id=?", (acd["item_id"],)) if acd["item_id"] else None
        if item:
            db.q("UPDATE campaign_items SET status='operator_ok', completed_at=? WHERE id=?", (now_iso(), item["id"]))
        # Если провайдер умеет реально соединять с оператором (Asterisk/AMI) — инициируем бридж
        try:
            connect = getattr(self.provider, "connect_operator", None)
            if connect and op.get("ext"):
                connect(acd["call_id"], op["ext"])
        except Exception as e:
            print("[acd] connect_operator:", e)
        events.publish("acd", {"id": acd_id, "status": "accepted", "operator": op["name"]})
        return True, "ok"

    def complete_operator_call(self, call_id, operator_id):
        call = db.fetch1("SELECT * FROM calls WHERE id=?", (call_id,))
        if not call:
            return False, "not_found"
        if call["ended_at"]:
            db.q("UPDATE operators SET status='free', updated=? WHERE id=?", (now_iso(), operator_id))
            return True, "already_ended"
        ended = now_iso()
        db.q("UPDATE calls SET status='done', ended_at=?, duration_sec=?, result='operator_ok' WHERE id=?",
             (ended, self._duration(call), call_id))
        db.q("UPDATE operators SET status='free', updated=? WHERE id=?", (now_iso(), operator_id))
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

    # ---------- тик ----------
    def tick_once(self):
        for ev in self.drain_events():
            self.handle_event(ev)
        self._allocate()
        self._watchdog()

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
        return {
            "campaigns_running": len(active_camps),
            "calls_today": calls_today[0]["c"] if calls_today else 0,
            "ok_today": calls_today[0]["ok"] or 0,
            "items": {r["status"]: r["c"] for r in items},
            "active_channels": self.active_channels(),
            "numbers": numbers_mod.pool_state(),
            "operators": self.operators(),
            "acd_queued": len(self.acd_queued()),
        }
