# -*- coding: utf-8 -*-
"""Тесты ACD FSM (ТЗ §11): queued → ringing → answered → bridged.

Проверяем на движке с управляемым провайдером-стабом:
- счастливый путь и видимые промежуточные фазы;
- guard'ы (нужен ext; завершённый звонок не принимается);
- откат при провале бриджа без «воскрешения» звонка;
- возврат зависших фаз в очередь по watchdog;
- синхронизацию presence (ВАТС: приём звонков выкл, пока оператор занят).

Запуск: python -m unittest discover -s tests -v   (из корня репозитория)
"""
import os
import tempfile
import unittest

_TMP = tempfile.mkdtemp(prefix="ats_test_acd_")
os.environ["ATS_FAST"] = "1"
os.environ["ATS_DATA_DIR"] = _TMP
os.environ["ATS_ADMIN_PASSWORD"] = "TestAdmin123!"
os.environ["ATS_DEV_SEED"] = "1"

from app import config, db  # noqa: E402
from app.engine import Engine  # noqa: E402
from app.telephony import SimProvider  # noqa: E402

db.init_db()


class ScriptedProvider(SimProvider):
    """Sim-провайдер с программируемым connect_operator."""
    name = "sim-acd-test"

    def __init__(self):
        super().__init__()
        self.connect_result = True
        self.phases = []          # фазы, о которых сообщил connect_operator
        self.seen_status = []     # статусы acd, видимые сразу после progress
        self.acd_id = 0
        self.fail = None

    def connect_operator(self, call_id, operator_ext, progress=None):
        for ph in ("ringing", "answered"):
            self.phases.append(ph)
            if progress is not None:
                progress(ph)
            if self.acd_id:
                row = db.fetch1("SELECT status FROM acd WHERE id=?", (self.acd_id,))
                self.seen_status.append(row["status"] if row else None)
        if self.fail:
            raise RuntimeError(self.fail)
        return self.connect_result


class NeedExtProvider(ScriptedProvider):
    needs_operator_ext = True


class DndRecorder:
    def __init__(self):
        self.calls = []  # (login, enabled)

    def set_dnd(self, login, enabled):
        self.calls.append((login, bool(enabled)))


class MegafonStubProvider(ScriptedProvider):
    name = "megafon_vats"

    def __init__(self):
        super().__init__()
        self.client = DndRecorder()


def _call_row(status="talk", phone="79997770201", ended=""):
    return {"campaign_id": 0, "item_id": 0, "contact_id": 0,
            "contact_name": "ACD", "contact_phone": phone,
            "caller_id": "", "number_id": 0, "provider": "sim",
            "direction": "out", "status": status, "result": "",
            "detail": "", "agent_result": "", "recording": "",
            "started_at": config.now_iso(), "answered_at": "",
            "ended_at": ended, "duration_sec": 0}


def _op(name, ext="101", status="free"):
    return db.insert("operators", {"user_id": 0, "name": name, "ext": ext,
                                   "status": status, "updated": config.now_iso()})


def _acd(call_id, item_id=0):
    return db.insert("acd", {"call_id": call_id, "item_id": item_id,
                             "operator_id": 0, "status": "queued",
                             "created": config.now_iso(), "updated": config.now_iso()})


class TestAcdFsm(unittest.TestCase):
    def setUp(self):
        self.prov = ScriptedProvider()
        self.engine = Engine(provider=self.prov, auto_start=False)

    def tearDown(self):
        # Общая БД на все модули сьюта: выносим за собой свои фикстуры,
        # чтобы E2E следующих модулей стартовали на чистом состоянии.
        db.q("DELETE FROM acd WHERE call_id IN "
             "(SELECT id FROM calls WHERE contact_name='ACD')")
        db.q("DELETE FROM calls WHERE contact_name='ACD'")
        db.q("DELETE FROM operators WHERE name LIKE 'ACD-Оператор%'")

    def test_happy_path_ring_answered_bridged(self):
        """Счастливый путь: фазы видны в БД, финал — bridged."""
        op_id = _op("ACD-Оператор-1", ext="201")
        call_id = db.insert("calls", _call_row("talk", "79997770211"))
        acd_id = _acd(call_id)
        self.prov.acd_id = acd_id
        ok, reason = self.engine.accept_acd(acd_id, op_id)
        self.assertTrue(ok, reason)
        self.assertEqual(self.prov.phases, ["ringing", "answered"])
        self.assertEqual(self.prov.seen_status, ["ringing", "answered"])
        acd = db.fetch1("SELECT * FROM acd WHERE id=?", (acd_id,))
        self.assertEqual(acd["status"], "bridged")
        self.assertEqual(acd["operator_id"], op_id)
        op = db.fetch1("SELECT * FROM operators WHERE id=?", (op_id,))
        self.assertEqual(op["status"], "busy")
        call = db.fetch1("SELECT * FROM calls WHERE id=?", (call_id,))
        self.assertEqual(call["status"], "operator_connected")
        self.assertEqual(call["result"], "operator_ok")

    def test_sim_without_bridge_still_bridges(self):
        """Провайдер без connect_operator (None): бридж пропускается, accept успешен."""
        op_id = _op("ACD-Оператор-sim", ext="202")
        call_id = db.insert("calls", _call_row("talk", "79997770212"))
        acd_id = _acd(call_id)
        eng = Engine(provider=SimProvider(), auto_start=False)
        ok, reason = eng.accept_acd(acd_id, op_id)
        self.assertTrue(ok, reason)
        acd = db.fetch1("SELECT * FROM acd WHERE id=?", (acd_id,))
        self.assertEqual(acd["status"], "bridged")

    def test_need_ext_guard(self):
        """Без ext при needs_operator_ext — отказ, очередь не тронута."""
        self.engine.provider = NeedExtProvider()
        op_id = _op("ACD-Оператор-без-ext", ext="")
        call_id = db.insert("calls", _call_row("talk", "79997770213"))
        acd_id = _acd(call_id)
        ok, reason = self.engine.accept_acd(acd_id, op_id)
        self.assertFalse(ok)
        self.assertEqual(reason, "operator_no_ext")
        acd = db.fetch1("SELECT * FROM acd WHERE id=?", (acd_id,))
        self.assertEqual(acd["status"], "queued")
        self.assertEqual(acd["operator_id"], 0)
        op = db.fetch1("SELECT * FROM operators WHERE id=?", (op_id,))
        self.assertEqual(op["status"], "free")
        call = db.fetch1("SELECT * FROM calls WHERE id=?", (call_id,))
        self.assertEqual(call["status"], "talk")

    def test_finished_call_rejected(self):
        """Завершённый звонок принять нельзя: отказ, оператор свободен."""
        op_id = _op("ACD-Оператор-2", ext="203")
        call_id = db.insert("calls", _call_row("done", "79997770214",
                                               ended=config.now_iso()))
        acd_id = _acd(call_id)
        ok, reason = self.engine.accept_acd(acd_id, op_id)
        self.assertFalse(ok)
        self.assertEqual(reason, "call_ended")
        acd = db.fetch1("SELECT * FROM acd WHERE id=?", (acd_id,))
        self.assertEqual(acd["status"], "queued")
        op = db.fetch1("SELECT * FROM operators WHERE id=?", (op_id,))
        self.assertEqual(op["status"], "free")

    def test_bridge_failure_rolls_back(self):
        """Провал бриджа: очередь/оператор откачены, звонок НЕ завершён."""
        self.prov.connect_result = False
        op_id = _op("ACD-Оператор-3", ext="204")
        call_id = db.insert("calls", _call_row("talk", "79997770215"))
        acd_id = _acd(call_id)
        ok, reason = self.engine.accept_acd(acd_id, op_id)
        self.assertFalse(ok)
        self.assertEqual(reason, "transfer_failed")
        acd = db.fetch1("SELECT * FROM acd WHERE id=?", (acd_id,))
        self.assertEqual(acd["status"], "queued")
        self.assertEqual(acd["operator_id"], 0)
        op = db.fetch1("SELECT * FROM operators WHERE id=?", (op_id,))
        self.assertEqual(op["status"], "free")
        call = db.fetch1("SELECT * FROM calls WHERE id=?", (call_id,))
        self.assertEqual(call["status"], "wait_operator")
        self.assertEqual(call["result"], "")
        self.assertEqual(call["ended_at"], "")

    def test_watchdog_requeues_stuck_phase(self):
        """Зависшая фаза ringing возвращается в очередь, оператор освобождается."""
        op_id = _op("ACD-Оператор-4", ext="205", status="busy")
        call_id = db.insert("calls", _call_row("operator_ringing", "79997770216"))
        acd_id = _acd(call_id)
        stale = self.engine._ago(10)
        db.q("UPDATE acd SET status='ringing', operator_id=?, updated=? WHERE id=?",
             (op_id, stale, acd_id))
        self.engine.tick_once()
        acd = db.fetch1("SELECT * FROM acd WHERE id=?", (acd_id,))
        self.assertEqual(acd["status"], "queued")
        self.assertEqual(acd["operator_id"], 0)
        op = db.fetch1("SELECT * FROM operators WHERE id=?", (op_id,))
        self.assertEqual(op["status"], "free")
        call = db.fetch1("SELECT * FROM calls WHERE id=?", (call_id,))
        self.assertEqual(call["status"], "wait_operator")

    def test_presence_follows_busy(self):
        """Presence ВАТС: busy → приём выкл, завершение → приём вкл."""
        stub = MegafonStubProvider()
        self.engine.provider = stub
        op_id = _op("ACD-Оператор-5", ext="206")
        db.q("UPDATE operators SET vats_login='acd.op' WHERE id=?", (op_id,))
        call_id = db.insert("calls", _call_row("talk", "79997770217"))
        acd_id = _acd(call_id)
        ok, reason = self.engine.accept_acd(acd_id, op_id)
        self.assertTrue(ok, reason)
        self.assertIn(("acd.op", False), stub.client.calls)
        self.engine.complete_operator_call(call_id, op_id)
        self.assertIn(("acd.op", True), stub.client.calls)


if __name__ == "__main__":
    unittest.main()
