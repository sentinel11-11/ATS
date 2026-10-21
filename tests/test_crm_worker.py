# -*- coding: utf-8 -*-
"""CRM-доставка вынесена из цикла движка (C3, P1-аудита).

До фикса Engine.tick_once() синхронно вызывал _crm_outbox_tick(): сетевые
запросы к CRM (таймауты, 429, лежащий сервер) блокировали обработку
вебхуков, планирование кампаний и watchdog на десятки секунд.

Проверяем fault-injection:
- CRM отвечает 3 секунды — tick_once остаётся быстрым;
- на момент блокировки воркера движок продолжает обрабатывать inbox/события;
- задание остаётся в outbox и в итоге доставляется (воркер);
- run_until_idle дождётся дренажа outbox.
"""
import os
import tempfile
import threading
import time
import unittest

_TMP = tempfile.mkdtemp(prefix="ats_test_crmw_")
os.environ["ATS_FAST"] = "1"
os.environ["ATS_DATA_DIR"] = _TMP
os.environ["ATS_ADMIN_PASSWORD"] = "TestAdmin123!"
os.environ["ATS_DEV_SEED"] = "1"

from app import config, db, events  # noqa: E402
from app.engine import Engine  # noqa: E402
from app.telephony import SimProvider  # noqa: E402

db.init_db()
_s = db.get_settings()
if not _s.get("provider"):
    _s["provider"] = "sim"
    db.save_settings(_s)

NOW = config.now_iso()


class _SlowCrm:
    """CRM, которая «отвечает» SLEEP секунд — имитация деградации сети."""

    def __init__(self, sleep_sec=3.0):
        self.sleep_sec = sleep_sec
        self.calls = 0
        self.lock = threading.Lock()

    def push_result(self, call):
        with self.lock:
            self.calls += 1
        time.sleep(self.sleep_sec)


class _OkCrm:
    def __init__(self):
        self.pushed = []

    def push_result(self, call):
        self.pushed.append(call)


class CrmWorkerTest(unittest.TestCase):
    def setUp(self):
        db.q("DELETE FROM crm_outbox")
        # тестовая БД общая: прошлые модули оставляют недозавершённые items/звонки,
        # из-за них run_until_idle никогда не увидит «тишину» — прибираем
        db.q("UPDATE campaign_items SET status='done' "
             "WHERE status IN ('queued','dialing','agent','wait_operator','talk')")
        db.q("UPDATE calls SET ended_at=? WHERE (ended_at='' OR ended_at IS NULL) "
             "AND status NOT IN ('new')", (config.now_iso(),))
        self._saved = db.get_settings()
        cid = db.insert("calls", {
            "campaign_id": 0, "item_id": 0, "contact_id": 0,
            "contact_name": "Воркер Тест", "contact_phone": "+79260000098",
            "caller_id": "", "number_id": 0, "provider": "sim",
            "external_call_id": "", "direction": "out", "status": "done",
            "result": "operator_ok", "detail": "Тест", "agent_result": "",
            "recording": "", "started_at": config.now_iso(), "answered_at": "",
            "ended_at": config.now_iso(), "duration_sec": 10, "diversion": "",
            "provider_user": ""})
        self.call = db.fetch1("SELECT * FROM calls WHERE id=?", (cid,))

    def tearDown(self):
        db.save_settings(self._saved)
        db.q("DELETE FROM crm_outbox")
        if self.call:
            db.q("DELETE FROM calls WHERE id=?", (self.call["id"],))

    def test_tick_not_blocked_by_slow_crm(self):
        """Медленная CRM не задерживает tick_once (главный сценарий C3)."""
        db.crm_outbox_enqueue("push_result", self.call, error="test")
        eng = Engine(provider=SimProvider(), auto_start=False)
        eng.crm = _SlowCrm(sleep_sec=3.0)
        try:
            eng._start_crm_worker()
            deadline = time.time() + 5
            while time.time() < deadline:
                row = db.fetch1("SELECT status FROM crm_outbox")
                if row and row["status"] in ("pending", "done"):
                    break
                time.sleep(0.05)
            time.sleep(0.3)  # воркер гарантированно внутри медленного push
            t0 = time.time()
            eng.tick_once()   # inbox + события + планирование + watchdog
            dt = time.time() - t0
            self.assertLess(dt, 1.0,
                            "tick_once заблокирован CRM ({:.2f} с) — доставка "
                            "должна быть в отдельном воркере".format(dt))
            # задание не потерялось: воркер его доедет
            deadline = time.time() + 10
            delivered = False
            while time.time() < deadline:
                row = db.fetch1("SELECT status FROM crm_outbox")
                if row and row["status"] == "done":
                    delivered = True
                    break
                time.sleep(0.1)
            self.assertTrue(delivered, "задание outbox не доставлено после медленной CRM")
        finally:
            eng.stop()

    def test_engine_loop_runs_while_crm_hangs(self):
        """Пока воркер висит в CRM, основной цикл продолжает тиковать."""
        db.crm_outbox_enqueue("push_result", self.call, error="test")
        eng = Engine(provider=SimProvider(), auto_start=False)
        eng.crm = _SlowCrm(sleep_sec=5.0)
        ticks = {"n": 0}

        def ticker():
            while not eng._stop.is_set():
                eng.tick_once()
                ticks["n"] += 1
                time.sleep(0.05)

        try:
            eng._start_crm_worker()
            th = threading.Thread(target=ticker, daemon=True)
            th.start()
            time.sleep(2.0)   # CRM висит все эти 2 секунды
            self.assertGreater(ticks["n"], 5,
                               "основной цикл движка остановился из-за CRM")
            row = db.fetch1("SELECT status FROM crm_outbox")
            self.assertIsNotNone(row, "задание обязано оставаться в outbox")
        finally:
            eng.stop()

    def test_run_until_idle_waits_outbox(self):
        """run_until_idle считает незавершённую CRM-доставку работой."""
        db.crm_outbox_enqueue("push_result", self.call, error="test")
        eng = Engine(provider=SimProvider(), auto_start=False)
        ok_crm = _OkCrm()
        eng.crm = ok_crm
        try:
            eng._start_crm_worker()
            res = eng.run_until_idle(max_seconds=8, idle_ticks=2)
            self.assertTrue(res)
            self.assertEqual(len(ok_crm.pushed), 1,
                             "idle без доставки результата в CRM — не idle")
        finally:
            eng.stop()

    def test_no_crm_activity_in_tick_once(self):
        """Прямая проверка: tick_once не трогает CRM (вызова push нет)."""
        eng = Engine(provider=SimProvider(), auto_start=False)
        eng.crm = _SlowCrm(sleep_sec=10.0)
        try:
            t0 = time.time()
            eng.tick_once()
            dt = time.time() - t0
            self.assertLess(dt, 1.0)
            self.assertEqual(eng.crm.calls, 0,
                             "tick_once не должен вызывать CRM-доставку")
        finally:
            eng.stop()


if __name__ == "__main__":
    unittest.main()
