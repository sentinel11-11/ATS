# -*- coding: utf-8 -*-
"""CRM-доставка полностью через durable outbox (P1 повторного ревью).

Раньше ПЕРВАЯ попытка push_result выполнялась синхронно в потоке обработки
звонка (сеть в цикле движка), а create_task вообще шёл напрямую — лежащая
CRM задерживала обработку событий, а сбой терял задачу навсегда.

Проверяем:
- тик движка не делает НИ ОДНОГО сетевого вызова CRM (задержка 20 с не
  мешает обработке терминального события);
- все действия (push_result + create_task) попадают в crm_outbox;
- повторная постановка того же действия игнорируется (idem_key);
- сбой доставки → retry c backoff, после успеха — done; dead-letter после
  исчерпания попыток + system-event;
- рестарт подхватывает pending-строки (воркер не теряет очередь);
- amoCRM-драйвер: маркер ATS#<id> + найденный дубль не создаёт вторую задачу.
"""
import json
import os
import tempfile
import threading
import time
import unittest
from unittest import mock

_TMP = tempfile.mkdtemp(prefix="ats_test_crmo_")
os.environ["ATS_FAST"] = "1"
os.environ["ATS_DATA_DIR"] = _TMP
os.environ["ATS_ADMIN_PASSWORD"] = "TestAdmin123!"
os.environ["ATS_DEV_SEED"] = "1"

from app import db, events  # noqa: E402
from app.engine import Engine  # noqa: E402

db.init_db()
_seed = db.get_settings()
if not _seed.get("provider"):
    _seed["provider"] = "sim"
    db.save_settings(_seed)


class SlowCrm:
    """CRM, каждый вызов которой спит CRM_SLEEP секунд и падает."""
    name = "slow"

    def __init__(self, sleep_sec=20.0, fail=False):
        self.sleep_sec = sleep_sec
        self.fail = fail
        self.calls = []

    def push_result(self, call):
        self.calls.append(("push_result", call.get("id")))
        time.sleep(self.sleep_sec)
        if self.fail:
            raise RuntimeError("crm down")
        return None

    def create_task(self, title, desc="", responsible="", call_id=None, phone=None):
        self.calls.append(("create_task", call_id))
        time.sleep(self.sleep_sec)
        if self.fail:
            raise RuntimeError("crm down")
        return {"ok": True}


def _mk_call(result="no_operator", phone="79001112233"):
    return db.insert("calls", {
        "campaign_id": 0, "item_id": 0, "contact_id": 0, "contact_name": "Тест",
        "contact_phone": phone, "caller_id": "", "number_id": 0,
        "provider": "sim", "external_call_id": "", "direction": "in",
        "status": "wait_operator", "result": "", "detail": "", "agent_result": "",
        "recording": "", "started_at": "2026-01-01 00:00:00", "answered_at": "",
        "ended_at": "", "duration_sec": 0})


class CrmOutboxOnlyTest(unittest.TestCase):
    def setUp(self):
        st = db.get_settings()
        if st.get("provider") != "sim":
            st["provider"] = "sim"
            db.save_settings(st)
        db.q("DELETE FROM crm_outbox")
        # движок без автозапуска: тики драйвим руками, CRM-воркер тоже наш
        self.engine = Engine(auto_start=False)

    def tearDown(self):
        self.engine.stop()

    def test_engine_tick_does_no_network_crm_calls(self):
        """20-секундная CRM не должна задерживать обработку звонка."""
        slow = SlowCrm(sleep_sec=20.0)
        self.engine.crm = slow
        call_id = _mk_call()
        call = db.fetch1("SELECT * FROM calls WHERE id=?", (call_id,))
        t0 = time.time()
        # терминальный переход: _no_operator_finish → статусы + 2 постановки
        self.engine._no_operator_finish(call, None, reason="тест")
        dt = time.time() - t0
        self.assertLess(dt, 2.0,
                        "тик движка заблокирован CRM на {:.1f} с — сеть в цикле "
                        "движка недопустима".format(dt))
        self.assertEqual(slow.calls, [], "прямые вызовы CRM из движка запрещены")
        row = db.fetch1("SELECT status, result FROM calls WHERE id=?", (call_id,))
        self.assertEqual((row["status"], row["result"]), ("done", "no_operator"),
                         "бизнес-статусы применены немедленно")
        kinds = {r["kind"] for r in db.fetch("SELECT kind FROM crm_outbox")}
        self.assertEqual(kinds, {"push_result", "create_task"},
                         "оба действия в очереди: {}".format(kinds))

    def test_idem_key_prevents_duplicate_enqueue(self):
        call = db.fetch1("SELECT * FROM calls WHERE id=?", (_mk_call(),))
        self.engine._push_crm(call, "missed")
        self.engine._push_crm(call, "missed")           # replay
        self.engine._crm_task("T", "D", call, "missed")
        self.engine._crm_task("T", "D", call, "missed")  # replay
        rows = db.fetch("SELECT kind, idem_key FROM crm_outbox")
        self.assertEqual(len(rows), 2, "дубли постановок обязаны игнорироваться: %r" % rows)
        # но другой результат того же звонка — отдельное действие
        self.engine._push_crm(call, "done_ok")
        self.assertEqual(len(db.fetch("SELECT id FROM crm_outbox")), 3)

    def test_retry_backoff_then_done(self):
        failing = SlowCrm(sleep_sec=0.01, fail=True)
        self.engine.crm = failing
        call = db.fetch1("SELECT * FROM calls WHERE id=?", (_mk_call(),))
        self.engine._push_crm(call, "missed")
        self.engine._crm_outbox_tick()
        row = db.fetch1("SELECT status, attempts, next_attempt_at FROM crm_outbox")
        self.assertEqual(row["status"], "pending", "сбой доставки → retry")
        self.assertEqual(row["attempts"], 1)
        self.assertTrue(str(row["next_attempt_at"] or "").strip() > "2000",
                        "повтор назначен с backoff: %r" % row["next_attempt_at"])
        # CRM «поднялась» — доставка проходит ровно один раз
        ok_crm = SlowCrm(sleep_sec=0.001)
        self.engine.crm = ok_crm
        db.q("UPDATE crm_outbox SET next_attempt_at='2000-01-01 00:00:00'")
        self.engine._crm_outbox_tick()
        row = db.fetch1("SELECT status FROM crm_outbox")
        self.assertEqual(row["status"], "done")
        self.assertEqual(ok_crm.calls, [("push_result", call["id"])])

    def test_dead_letter_after_max_attempts(self):
        self.engine.crm = SlowCrm(sleep_sec=0.001, fail=True)
        call = db.fetch1("SELECT * FROM calls WHERE id=?", (_mk_call(),))
        self.engine._push_crm(call, "missed")
        seen = []
        stream = events.EventStream()
        try:
            for _ in range(12):
                db.q("UPDATE crm_outbox SET next_attempt_at='2000-01-01 00:00:00'")
                self.engine._crm_outbox_tick()
            # собираем опубликованные system-события
            for _ in range(50):
                try:
                    seen.append(stream.q.get_nowait())
                except Exception:
                    break
        finally:
            stream.close()
        row = db.fetch1("SELECT status, attempts FROM crm_outbox")
        self.assertEqual(row["status"], "failed", "dead-letter после исчерпания попыток")
        self.assertGreaterEqual(row["attempts"], 8)
        self.assertTrue(any(e.get("type") == "system"
                            and (e.get("payload") or {}).get("kind") == "crm_outbox_failed"
                            for e in seen),
                        "dead-letter обязан публиковать system-event; seen=%r" % seen)

    def test_pending_survives_restart(self):
        """Рестарт (новый движок) подхватывает очередь CRM."""
        call = db.fetch1("SELECT * FROM calls WHERE id=?", (_mk_call(),))
        self.engine._push_crm(call, "missed")
        self.engine.stop()
        engine2 = Engine(auto_start=False)
        try:
            ok_crm = SlowCrm(sleep_sec=0.001)
            engine2.crm = ok_crm
            engine2._crm_outbox_tick()
            row = db.fetch1("SELECT status FROM crm_outbox")
            self.assertEqual(row["status"], "done", "pending пережил рестарт")
            self.assertEqual(ok_crm.calls, [("push_result", call["id"])])
        finally:
            engine2.stop()

    def test_amocrm_driver_dedupe_by_marker(self):
        """Ответ потерялся, задача создана: повтор не создаёт дубль."""
        from app.crm import AmoCrm

        class FakeClient:
            def __init__(self):
                self.created = []

            def search_contacts(self, phone):
                return [{"id": 77, "name": "X",
                         "custom_fields_values": [
                             {"values": [{"value": phone}]}]}]

            def find_task_by_marker(self, marker, entity_id=None):
                if self.created:
                    return {"id": 555, "text": "[{}] Перезвонить".format(marker)}
                return None

            def create_task(self, text, responsible_user_id=None, entity_id=None,
                            entity_type=None, task_type_id=1):
                self.created.append(text)
                return {"id": 555}

        crm = AmoCrm({"crm": {"driver": "amocrm", "subdomain": "x",
                              "access_token": "t"}})
        fake = FakeClient()
        with mock.patch.object(crm, "_client", return_value=fake), \
                mock.patch.object(crm, "_configured", return_value=True), \
                mock.patch.object(crm, "_mcfg", return_value={}):
            r1 = crm.create_task("Перезвонить", "desc", call_id=42,
                                 phone="79001112233")
            r2 = crm.create_task("Перезвонить", "desc", call_id=42,
                                 phone="79001112233")  # повтор после потери ответа
        self.assertEqual(len(fake.created), 1,
                         "повтор не должен создавать вторую задачу в amoCRM")
        self.assertIn("ATS#42", fake.created[0], "маркер идемпотентности в тексте")
        self.assertEqual(r1.get("id"), r2.get("id"))


if __name__ == "__main__":
    unittest.main()
