# -*- coding: utf-8 -*-
"""Durable inbox вебхуков (C2, P1-аудита).

До фикса: вебхук телефонии получал 200 сразу после записи события в
in-memory queue.Queue; крах процесса между 200 и обработкой терял событие
навсегда. Теперь: /webhooks/* персистентно пишет событие в webhook_inbox
ДО ответа 2xx; тик движка забирает события из inbox; идемпотентность —
уникальный fingerprint; обработка переживает рестарт.

Сценарии (по требованиям этапа C2):
1. один и тот же вебхук доставлен дважды — обрабатывается один раз;
2. «процесс упал» после записи, но до обработки — новый движок дообрабатывает;
3. ошибка БД до записи — НЕТ ложного 2xx (503, оператор повторит);
4. ошибка обработчика — retry, событие не теряется;
5. событие неизвестного звонка помечается orphan и не трогает чужие звонки;
6. невалидная подпись — 403 и пустой inbox.
"""
import json
import os
import tempfile
import unittest

_TMP = tempfile.mkdtemp(prefix="ats_test_inbox_")
os.environ["ATS_FAST"] = "1"
os.environ["ATS_DATA_DIR"] = _TMP
os.environ["ATS_ADMIN_PASSWORD"] = "TestAdmin123!"
os.environ["ATS_DEV_SEED"] = "1"

from app import api, config, db, events  # noqa: E402
from app.engine import Engine  # noqa: E402

db.init_db()
_s = db.get_settings()
if not _s.get("provider"):
    _s["provider"] = "sim"
    db.save_settings(_s)
_s = db.get_settings()
_saved_mc = dict(_s.get("multicom") or {})
_s["multicom"] = dict(_saved_mc, webhook_secret="MCSECRET", webhook_secret_env="")
db.save_settings(_s)

def _mk_call(ext, status="dialing"):
    # свежий таймстамп именно в момент вызова: полный прогон идёт ~60 с,
    # и «старый» started_at watchdog закрывает как no_operator по ACD-таймауту
    NOW = config.now_iso()
    contact_id = db.insert("contacts", {"name": "Инбокс", "phone": "79001110001",
                                        "consent": 1, "created": NOW, "updated": NOW})
    camp_id = db.insert("campaigns", {"name": "Инбокс-кампания", "status": "running",
                                      "flow": "operator", "created": NOW, "updated": NOW})
    item_id = db.insert("campaign_items", {"campaign_id": camp_id,
                                           "contact_id": contact_id,
                                           "contact_name": "Инбокс",
                                           "contact_phone": "79001110001",
                                           "status": status, "attempts": 1,
                                           "created": NOW, "updated": NOW})
    call_id = db.insert("calls", {"campaign_id": camp_id, "item_id": item_id,
                                  "contact_id": contact_id, "contact_phone": "79001110001",
                                  "contact_name": "Инбокс", "provider": "multicom",
                                  "external_call_id": ext, "direction": "out",
                                  "status": status, "started_at": NOW,
                                  "caller_id": "79001112233"})
    return camp_id, item_id, call_id


class WebhookInboxTest(unittest.TestCase):
    def setUp(self):
        db.q("DELETE FROM webhook_inbox")
        db.q("DELETE FROM provider_events")
        db.q("DELETE FROM calls WHERE contact_name='Инбокс'")
        db.q("DELETE FROM campaign_items WHERE contact_name='Инбокс'")
        db.q("DELETE FROM campaigns WHERE name='Инбокс-кампания'")
        db.q("DELETE FROM contacts WHERE name='Инбокс'")
        # тестовая БД и настройки одни на весь прогон: фиксируем на время теста
        # секрет вебхука и таймауты, от которых зависит FSM звонка (другие
        # модули оставляют ускоренные значения)
        s = db.get_settings()
        self._saved_settings = json.loads(json.dumps(s))
        s["multicom"] = dict(self._saved_mc_backup(), webhook_secret="MCSECRET",
                             webhook_secret_env="")
        s["provider"] = "sim"   # другие модули оставляют ami/multicom-моки
        s["acd_wait_timeout_sec"] = 60
        s["watchdog_timeout_min"] = 20
        s["vats_conversation_timeout_min"] = 30
        s["message_max_sec"] = 300
        db.save_settings(s)
        self._old_engine = api.ENGINE
        self.engine = Engine(auto_start=False)
        api.ENGINE = self.engine

    def _saved_mc_backup(self):
        s = db.get_settings()
        return dict(s.get("multicom") or {})

    def tearDown(self):
        db.save_settings(self._saved_settings)
        api.ENGINE = self._old_engine
        self.engine.stop()

    def _push(self, body, secret="MCSECRET"):
        return api._route("POST", "/api/v2/webhooks/multicom", body,
                          {"X-Multicom-Secret": secret})

    def test_duplicate_delivery_processed_once(self):
        """1) повторная доставка того же события — одна обработка."""
        _c, _i, call_id = _mk_call("mc-dup-1")
        payload, code = self._push({"call_id": "mc-dup-1", "status": "answered"})
        self.assertEqual(code, 200)
        self.assertEqual(payload.get("ok"), True)
        # вторая доставка того же события — до тика движка
        payload2, code2 = self._push({"call_id": "mc-dup-1", "status": "answered"})
        self.assertEqual(code2, 200)
        rows = db.fetch("SELECT * FROM webhook_inbox")
        self.assertEqual(len(rows), 1, "дубль не должен создавать вторую строку inbox")
        self.engine.tick_once()
        rows = db.fetch("SELECT * FROM webhook_inbox")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "done")
        # ровно один provider_events (дедуп на уровне провайдера не сломан)
        pe = db.fetch("SELECT * FROM provider_events WHERE provider='multicom'")
        self.assertEqual(len(pe), 1)
        call = db.fetch1("SELECT status FROM calls WHERE id=?", (call_id,))
        self.assertEqual(call["status"], "wait_operator")

    def test_survives_restart_after_persist_before_processing(self):
        """2) процесс «упал» после записи в inbox, но до обработки:
        новый экземпляр движка дообрабатывает событие из БД."""
        _c, _i, call_id = _mk_call("mc-crash-1")
        payload, code = self._push({"call_id": "mc-crash-1", "status": "answered"})
        self.assertEqual(code, 200)
        row = db.fetch1("SELECT * FROM webhook_inbox")
        self.assertEqual(row["status"], "new", "после 200 событие обязано лежать в inbox")
        # «краш»: этот движок больше не тикает; поднимаем новый
        self.engine.stop()
        self.engine = Engine(auto_start=False)
        api.ENGINE = self.engine
        self.engine.tick_once()
        row = db.fetch1("SELECT * FROM webhook_inbox")
        self.assertEqual(row["status"], "done", "событие обязано дожить до обработки")
        call = db.fetch1("SELECT status FROM calls WHERE id=?", (call_id,))
        self.assertEqual(call["status"], "wait_operator")

    def test_db_error_before_persist_is_not_2xx(self):
        """3) ошибка БД до персистентной записи — 503, а не ложный 200."""
        _c, _i, _call_id = _mk_call("mc-dberr-1")
        real_q = db.q

        def broken_q(sql, params=()):
            if "webhook_inbox" in sql:
                raise RuntimeError("db down")
            return real_q(sql, params)

        db.q = broken_q
        try:
            payload, code = self._push({"call_id": "mc-dberr-1", "status": "answered"})
        finally:
            db.q = real_q
        self.assertEqual(code, 503, "сбой записи не должен подтверждаться 2xx")
        self.assertEqual(payload["error"], "inbox_unavailable")
        rows = db.fetch("SELECT * FROM webhook_inbox")
        self.assertEqual(rows, [], "ни строки inbox, ни обработки — оператор повторит")

    def test_handler_error_retries_without_loss(self):
        """4) исключение обработчика — возврат в new, retry, событие не теряется."""
        _c, _i, call_id = _mk_call("mc-err-1")
        self._push({"call_id": "mc-err-1", "status": "answered"})
        real = self.engine.handle_event
        calls = {"n": 0}

        def flaky(ev):
            calls["n"] += 1
            if calls["n"] <= 2:
                raise RuntimeError("handler boom")
            return real(ev)

        self.engine.handle_event = flaky
        self.engine.tick_once()   # попытка 1 — исключение
        row = db.fetch1("SELECT * FROM webhook_inbox")
        self.assertEqual(row["status"], "new", "сбой обработчика обязан вернуть событие в очередь")
        self.assertEqual(row["attempts"], 1)
        self.assertIn("handler boom", row["last_error"])
        self.engine.tick_once()   # попытка 2 — снова исключение
        row = db.fetch1("SELECT * FROM webhook_inbox")
        self.assertEqual((row["status"], row["attempts"]), ("new", 2))
        self.engine.tick_once()   # попытка 3 — обработка проходит
        row = db.fetch1("SELECT * FROM webhook_inbox")
        self.assertEqual(row["status"], "done", "после устранения сбоя событие обработано")
        self.assertEqual(row["last_error"], "")
        call = db.fetch1("SELECT status FROM calls WHERE id=?", (call_id,))
        self.assertEqual(call["status"], "wait_operator")

    def test_failed_after_max_attempts_kept_for_forensics(self):
        """После INBOX_MAX_ATTEMPTS событие помечается failed и не теряется."""
        _c, _i, _call_id = _mk_call("mc-fail-1")
        self._push({"call_id": "mc-fail-1", "status": "answered"})
        self.engine.handle_event = lambda ev: (_ for _ in ()).throw(RuntimeError("always bad"))
        for _ in range(self.engine.INBOX_MAX_ATTEMPTS):
            self.engine.tick_once()
        row = db.fetch1("SELECT * FROM webhook_inbox")
        self.assertEqual(row["status"], "failed")
        self.assertEqual(row["attempts"], self.engine.INBOX_MAX_ATTEMPTS)
        self.assertIn("always bad", row["last_error"])

    def test_orphan_event_does_not_touch_other_calls(self):
        """5) событие с чужим/неизвестным external_call_id — orphan, чужие звонки не трогает."""
        _c, _i, call_id = _mk_call("mc-live-1")
        self._push({"call_id": "mc-ghost-999", "status": "completed"})
        self.engine.tick_once()
        row = db.fetch1("SELECT * FROM webhook_inbox")
        self.assertEqual(row["status"], "done")  # обработано и закрыто (orphan внутри провайдера)
        call = db.fetch1("SELECT * FROM calls WHERE id=?", (call_id,))
        self.assertEqual(call["status"], "dialing",
                         "чужое событие не должно менять состояние живого звонка")

    def test_bad_signature_leaves_inbox_empty(self):
        """6) неверный секрет — 401, в inbox ничего не попадает."""
        _c, _i, _call_id = _mk_call("mc-sig-1")
        payload, code = self._push({"call_id": "mc-sig-1", "status": "answered"},
                                   secret="WRONG")
        self.assertEqual(code, 401)
        rows = db.fetch("SELECT * FROM webhook_inbox")
        self.assertEqual(rows, [])

    def test_stale_claim_is_reclaimed(self):
        """Зависший claim (краш между claim и done) подбирается после таймаута."""
        _c, _i, call_id = _mk_call("mc-stale-1")
        self._push({"call_id": "mc-stale-1", "status": "answered"})
        # имитируем краш ровно между claim и обработкой
        db.q("UPDATE webhook_inbox SET status='claimed', claimed_at=?, "
             "attempts=0 WHERE 1=1", ("2020-01-01 00:00:00",))
        self.engine.tick_once()
        row = db.fetch1("SELECT * FROM webhook_inbox")
        self.assertEqual(row["status"], "done")
        call = db.fetch1("SELECT status FROM calls WHERE id=?", (call_id,))
        self.assertEqual(call["status"], "wait_operator")

    def test_uis_webhook_gets_fingerprint_and_dedup(self):
        """UIS-вебхук (провайдер без собственного fingerprint) тоже дедуплицируется."""
        s = db.get_settings()
        saved_uis = dict(s.get("uis") or {})
        s["uis"] = dict(saved_uis, webhook_secret="UISSECRET")
        db.save_settings(s)
        try:
            _c, _i, call_id = _mk_call("mc-u-1")
            db.q("UPDATE calls SET provider='uis' WHERE id=?", (call_id,))
            body = {"call_id": call_id, "status": "ring"}
            p1, c1 = api._route("POST", "/api/v2/webhooks/uis", body,
                                {"X-UIS-Secret": "UISSECRET"})
            p2, c2 = api._route("POST", "/api/v2/webhooks/uis", body,
                                {"X-UIS-Secret": "UISSECRET"})
            self.assertEqual((c1, c2), (200, 200))
            rows = db.fetch("SELECT * FROM webhook_inbox")
            self.assertEqual(len(rows), 1, "UIS-дубль обязан дедуплицироваться")
            self.assertTrue(rows[0]["fingerprint"])
            self.engine.tick_once()
            row = db.fetch1("SELECT * FROM webhook_inbox")
            self.assertEqual(row["status"], "done")
        finally:
            s = db.get_settings()
            s["uis"] = saved_uis
            db.save_settings(s)


if __name__ == "__main__":
    unittest.main()
