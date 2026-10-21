# -*- coding: utf-8 -*-
"""Идемпотентность обработки вебхуков (P1 повторного ревью).

Раньше claim → handle_event → done были отдельными запросами: падение
процесса между бизнес-изменением и отметкой done (или потеря ответа с
последующей повторной доставкой) могло применить бизнес-эффект дважды.
Теперь всё — в одной транзакции + таблица processed_events.

Проверяем:
- crash-injection: исключение ПОСЛЕ применения side effect → транзакция
  откатывает и бизнес-изменения, и claim; повторная обработка после
  «рестарта» применяет эффект ровно один раз;
- повторная доставка того же логического события с ДРУГИМ fingerprint
  (другой payload) → done без повторного бизнес-эффекта (processed_events);
- неизвестный call_id → status='orphan' с причиной, а не done;
- retry при сбое обработчика: attempts растёт, строка возвращается в new.
"""
import json
import os
import tempfile
import unittest

_TMP = tempfile.mkdtemp(prefix="ats_test_whid_")
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


def _mk_call(status="dialing"):
    # кампания + позиция: терминальный переход пишет строку attempts —
    # это детектор двойного применения бизнес-эффекта
    camp = db.insert("campaigns", {"name": "test-idem", "flow": "message",
                                   "status": "running", "template_id": 0,
                                   "created": "2026-01-01 00:00:00",
                                   "updated": "2026-01-01 00:00:00"})
    item = db.insert("campaign_items", {
        "campaign_id": camp, "contact_id": 0, "contact_name": "И",
        "contact_phone": "79001112233",
        "status": status, "attempts": 0, "last_result": "", "next_attempt_at": "",
        "created": "2026-01-01 00:00:00", "updated": "2026-01-01 00:00:00",
        "completed_at": ""})
    return db.insert("calls", {
        "campaign_id": camp, "item_id": item, "contact_id": 0, "contact_name": "И",
        "contact_phone": "79001112233", "caller_id": "", "number_id": 0,
        "provider": "uis", "external_call_id": "", "direction": "out",
        "status": status, "result": "", "detail": "", "agent_result": "",
        "recording": "", "started_at": "2026-01-01 00:00:00", "answered_at": "",
        "ended_at": "", "duration_sec": 0})


class WebhookIdempotencyTest(unittest.TestCase):
    def setUp(self):
        st = db.get_settings()
        if st.get("provider") != "sim":
            st["provider"] = "sim"
            db.save_settings(st)
        db.q("DELETE FROM webhook_inbox")
        db.q("DELETE FROM processed_events")
        self.engine = Engine(auto_start=False)

    def tearDown(self):
        self.engine.stop()

    def _ingest(self, ev):
        self.engine.ingest_webhook(ev)
        return db.fetch1("SELECT * FROM webhook_inbox ORDER BY id DESC LIMIT 1")

    def test_crash_between_side_effect_and_done(self):
        """Падение после side effect: откат всего, повтор — один эффект."""
        call_id = _mk_call(status="ringing")
        row = self._ingest({"provider": "uis", "event": "answered",
                            "call_id": call_id, "human": True,
                            "fingerprint": "fp-crash-1"})
        # «падение процесса» сразу после применения side effect: бизнес-UPDATE
        # отработал, но до отметки done исключение
        calls_handled = []

        def crashing_handle_event(ev):
            result = self.engine.__class__.handle_event(self.engine, ev)
            calls_handled.append(ev.get("event"))
            raise RuntimeError("CRASH после side effect")

        orig = self.engine.handle_event
        self.engine.handle_event = crashing_handle_event
        self.engine._drain_inbox()
        self.engine.handle_event = orig

        inbox = db.fetch1("SELECT status, attempts FROM webhook_inbox WHERE id=?",
                          (row["id"],))
        self.assertEqual(inbox["status"], "new",
                         "транзакция обязана откатить claim при сбое")
        self.assertEqual(inbox["attempts"], 1)
        call = db.fetch1("SELECT status, answered_at FROM calls WHERE id=?", (call_id,))
        self.assertEqual(call["status"], "ringing",
                         "side effect применён и ОТКАЧЕН вместе с транзакцией")
        self.assertFalse(events_row(call_id),
                         "после отката событие НЕ должно числиться обработанным")
        n_attempts = db.fetch1(
            "SELECT COUNT(*) c FROM attempts WHERE call_id=?", (call_id,))["c"]
        self.assertEqual(n_attempts, 0, "откатил и попытку, не только статус")

        # «рестарт»: здоровая обработка — эффект применяется ровно один раз
        self.engine._drain_inbox()
        inbox = db.fetch1("SELECT status FROM webhook_inbox WHERE id=?", (row["id"],))
        self.assertEqual(inbox["status"], "done")
        call = db.fetch1("SELECT status, result, answered_at FROM calls WHERE id=?",
                         (call_id,))
        # детерминированный итог answered для голого звонка — терминальный
        # статус; главное — эффект применился ровно один раз (см. attempts)
        self.assertEqual(call["status"], "done")
        self.assertTrue(call["answered_at"], "answered_at установлен")
        # детектор двойного применения: попытка записывается _finish_attempt;
        # будь эффект применён дважды (без отката) — было бы 2 строки
        n_attempts = db.fetch1(
            "SELECT COUNT(*) c FROM attempts WHERE call_id=?", (call_id,))["c"]
        self.assertEqual(n_attempts, 1,
                         "бизнес-эффект применён ровно один раз (попыток: {})".format(n_attempts))
        pe = db.fetch1("SELECT 1 FROM processed_events WHERE event_key=?",
                       ("uis:{}:answered".format(call_id),))
        self.assertIsNotNone(pe, "событие зафиксировано в processed_events")

    def test_duplicate_delivery_other_fingerprint_suppressed(self):
        """Та же логика, другой payload → done без повторного эффекта."""
        call_id = _mk_call(status="ringing")
        self._ingest({"provider": "uis", "event": "ring", "call_id": call_id,
                      "fingerprint": "fp-ring-a"})
        self.engine._drain_inbox()
        self.assertEqual(
            db.fetch1("SELECT status FROM calls WHERE id=?", (call_id,))["status"],
            "ringing")
        # повторная доставка с другим payload (тот же смысл): ring снова
        self._ingest({"provider": "uis", "event": "ring", "call_id": call_id,
                      "fingerprint": "fp-ring-b", "extra_field": "x"})
        self.engine._drain_inbox()
        rows = db.fetch("SELECT status, last_error FROM webhook_inbox "
                        "ORDER BY id")
        self.assertEqual([r["status"] for r in rows], ["done", "done"])
        # бизнес-эффект ring не повторялся — проверяем через processed_events:
        # единственная строка ключа
        n = db.fetch1("SELECT COUNT(*) c FROM processed_events WHERE event_key=?",
                      ("uis:{}:ring".format(call_id),))["c"]
        self.assertEqual(n, 1)
        # и повторная доставка answered дважды не применит эффект
        self._ingest({"provider": "uis", "event": "answered", "call_id": call_id,
                      "human": True, "fingerprint": "fp-ans-a"})
        self.engine._drain_inbox()
        first_answered = db.fetch1("SELECT answered_at FROM calls WHERE id=?",
                                   (call_id,))["answered_at"]
        self.assertTrue(first_answered)
        self._ingest({"provider": "uis", "event": "answered", "call_id": call_id,
                      "human": True, "fingerprint": "fp-ans-b"})
        self.engine._drain_inbox()
        second = db.fetch1("SELECT answered_at FROM calls WHERE id=?",
                           (call_id,))["answered_at"]
        self.assertEqual(first_answered, second,
                         "повторная доставка не должна менять бизнес-состояние")

    def test_unknown_call_id_becomes_orphan(self):
        stream = events.EventStream()
        try:
            self._ingest({"provider": "uis", "event": "answered",
                          "call_id": 999999, "human": True,
                          "fingerprint": "fp-orphan-1"})
            self.engine._drain_inbox()
            inbox = db.fetch1("SELECT status, last_error FROM webhook_inbox "
                              "ORDER BY id DESC LIMIT 1")
            self.assertEqual(inbox["status"], "orphan",
                             "неизвестный call_id — orphan, а не done")
            self.assertIn("999999", inbox["last_error"])
            seen = []
            for _ in range(20):
                try:
                    seen.append(stream.q.get_nowait())
                except Exception:
                    break
            self.assertTrue(any(e.get("type") == "system"
                                and (e.get("payload") or {}).get("kind") ==
                                "webhook_inbox_orphan" for e in seen),
                            "orphan публикует system-event; seen=%r" % seen)
        finally:
            stream.close()

    def test_handler_failure_retries_and_eventually_fails(self):
        call_id = _mk_call(status="ringing")
        self._ingest({"provider": "uis", "event": "ring", "call_id": call_id,
                      "fingerprint": "fp-retry-1"})

        def failing_handle(ev):
            raise RuntimeError("handler down")

        orig = self.engine.handle_event
        self.engine.handle_event = failing_handle
        for _ in range(12):
            self.engine._drain_inbox()
        self.engine.handle_event = orig
        inbox = db.fetch1("SELECT status, attempts FROM webhook_inbox "
                          "ORDER BY id DESC LIMIT 1")
        self.assertEqual(inbox["status"], "failed",
                         "после MAX_ATTEMPTS — failed (не теряется молча)")
        self.assertGreaterEqual(inbox["attempts"], 10)


def events_row(call_id):
    return db.fetch1("SELECT 1 FROM processed_events WHERE event_key=?",
                     ("uis:{}:answered".format(call_id),))


if __name__ == "__main__":
    unittest.main()
