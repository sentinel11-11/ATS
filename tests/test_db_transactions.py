# -*- coding: utf-8 -*-
"""Транзакции БД (C5, P1-аудита).

Серия db.q() с commit'ом на каждый вызов не образует атомарную операцию:
между вызовами другой поток успевает прочитать полусобранное состояние,
а сбой посередине оставляет «звонок done, попытка не записана».

Проверяем:
- transaction(): commit в конце, rollback при исключении;
- q() внутри transaction() не коммитит (соединение в транзакции до выхода,
  глобальный RLock не пускает другие потоки к соединению);
- вложенные transaction() через SAVEPOINT;
- delete_database атомарен (сбой на последнем DELETE откатывает всё);
- конкурентный accept_acd: ровно один победитель, слоты не портятся.
"""
import os
import tempfile
import threading
import time
import unittest

_TMP = tempfile.mkdtemp(prefix="ats_test_tx_")
os.environ["ATS_FAST"] = "1"
os.environ["ATS_DATA_DIR"] = _TMP
os.environ["ATS_ADMIN_PASSWORD"] = "TestAdmin123!"
os.environ["ATS_DEV_SEED"] = "1"

from app import config, db  # noqa: E402
from app.engine import Engine  # noqa: E402
from app.telephony import SimProvider  # noqa: E402

db.init_db()
_s = db.get_settings()
if not _s.get("provider"):
    _s["provider"] = "sim"
    db.save_settings(_s)

_NOW = config.now_iso


def _ins_db(name):
    return db.q("INSERT INTO databases(name, filename, notes, created, updated) "
                "VALUES(?, '', '', ?, ?)", (name, _NOW(), _NOW()))


class TransactionTest(unittest.TestCase):
    def setUp(self):
        db.q("DELETE FROM databases WHERE name LIKE 'tx-%'")
        db.q("DELETE FROM contacts WHERE grp='tx'")

    def test_commit_and_rollback(self):
        with db.transaction():
            _ins_db("tx-a")
        self.assertIsNotNone(db.fetch1("SELECT id FROM databases WHERE name='tx-a'"))

        with self.assertRaises(RuntimeError):
            with db.transaction():
                _ins_db("tx-b")
                raise RuntimeError("boom")
        self.assertIsNone(db.fetch1("SELECT id FROM databases WHERE name='tx-b'"),
                          "rollback не откатил вставку")

    def test_q_does_not_commit_inside_transaction(self):
        """Внутри transaction() q() не коммитит: транзакция открыта до выхода
        из блока; другие потоки ждут глобального RLock (одно соединение)."""
        state = {}

        def writer():
            with db.transaction():
                _ins_db("tx-c")
                state["in_tx"] = db.connect().in_transaction
            state["after_tx"] = db.connect().in_transaction

        th = threading.Thread(target=writer)
        th.start()
        th.join(0.05)               # писатель внутри транзакции
        t0 = time.time()
        db.fetch1("SELECT id FROM databases WHERE name='tx-c'")  # ждёт COMMIT
        waited = time.time() - t0
        th.join(5)
        self.assertTrue(state.get("in_tx"),
                        "внутри transaction() соединение обязано быть в транзакции")
        self.assertFalse(state.get("after_tx"),
                         "после выхода транзакция обязана закрыться")
        self.assertGreater(waited, 0.0,
                           "доступ к БД во время чужой transaction() не блокировался")

    def test_nested_transaction_savepoint(self):
        try:
            with db.transaction():
                _ins_db("tx-n1")
                try:
                    with db.transaction():   # savepoint
                        _ins_db("tx-n2")
                        raise RuntimeError("inner boom")
                except RuntimeError:
                    pass  # внутренний откат, внешняя транзакция жива
                _ins_db("tx-n3")
        except RuntimeError:
            self.fail("внешняя транзакция не должна падать от внутренней")
        names = {r["name"] for r in db.fetch(
            "SELECT name FROM databases WHERE name LIKE 'tx-n%'")}
        self.assertEqual(names, {"tx-n1", "tx-n3"},
                         "savepoint-откат не сработал: %s" % names)

    def test_delete_database_atomic(self):
        """Сбой на последнем DELETE откатывает всё: ни база, ни контакты
        не остаются в полусобранном состоянии."""
        did = db.insert("databases", {"name": "tx-del", "filename": "", "notes": "",
                                      "created": _NOW(), "updated": _NOW()})
        for i in range(3):
            db.insert("contacts", {"name": "c%d" % i, "phone": "7900999000%d" % i,
                                   "grp": "tx", "note": "", "consent": 1,
                                   "consent_source": "t", "blacklisted": 0,
                                   "complaints": 0, "database_id": did,
                                   "created": _NOW(), "updated": _NOW()})
        real_q = db.q

        def failing_q(sql, params=()):
            if "DELETE FROM databases" in sql:
                raise RuntimeError("db exploded mid-delete")
            return real_q(sql, params)

        db.q = failing_q
        try:
            with self.assertRaises(RuntimeError):
                db.delete_database(did, delete_contacts=True)
        finally:
            db.q = real_q
        self.assertIsNotNone(db.fetch1("SELECT id FROM databases WHERE id=?", (did,)),
                             "база удалена, а контакты нет — полусобранное состояние")
        self.assertEqual(
            len(db.fetch("SELECT id FROM contacts WHERE database_id=?", (did,))), 3,
            "контакты удалены без удаления базы — откат не сработал")
        real_q("DELETE FROM contacts WHERE database_id=?", (did,))
        real_q("DELETE FROM databases WHERE id=?", (did,))


class ConcurrentAcceptAcdTest(unittest.TestCase):
    """Двойной accept одного ACD-звонка: ровно один победитель."""

    def setUp(self):
        db.q("DELETE FROM acd WHERE contact_name='tx-acd'")
        db.q("DELETE FROM calls WHERE contact_name='tx-acd'")
        db.q("DELETE FROM operators WHERE name IN ('tx-op','tx-op2')")
        self.op1 = db.insert("operators", {"name": "tx-op", "ext": "801",
                                           "status": "free", "updated": _NOW()})
        self.op2 = db.insert("operators", {"name": "tx-op2", "ext": "802",
                                           "status": "free", "updated": _NOW()})
        self.call_id = db.insert("calls", {
            "campaign_id": 0, "item_id": 0, "contact_id": 0,
            "contact_name": "tx-acd", "contact_phone": "+79009990101",
            "caller_id": "", "number_id": 0, "provider": "sim",
            "external_call_id": "", "direction": "out", "status": "wait_operator",
            "result": "", "detail": "", "agent_result": "", "recording": "",
            "started_at": _NOW(), "answered_at": "", "ended_at": "",
            "duration_sec": 0, "diversion": "", "provider_user": ""})
        self.acd_id = db.insert("acd", {
            "call_id": self.call_id, "item_id": 0, "contact_id": 0,
            "contact_name": "tx-acd", "contact_phone": "+79009990101",
            "campaign_id": 0, "operator_id": 0, "status": "queued",
            "created": _NOW(), "updated": _NOW()})

    def tearDown(self):
        db.q("DELETE FROM acd WHERE call_id=?", (self.call_id,))
        db.q("DELETE FROM calls WHERE id=?", (self.call_id,))
        db.q("DELETE FROM operators WHERE id IN (?,?)", (self.op1, self.op2))

    def test_concurrent_accept_single_winner(self):
        eng = Engine(provider=SimProvider(), auto_start=False)
        try:
            results = []
            errs = []
            barrier = threading.Barrier(2)

            def run(op_id):
                try:
                    barrier.wait()
                    ok, err = eng.accept_acd(self.acd_id, operator_id=op_id)
                    results.append((op_id, ok, err))
                except Exception as e:  # noqa: BLE001
                    errs.append(e)

            t1 = threading.Thread(target=run, args=(self.op1,))
            t2 = threading.Thread(target=run, args=(self.op2,))
            t1.start()
            t2.start()
            t1.join(10)
            t2.join(10)
            self.assertEqual(errs, [], "accept не должен бросать исключения: %r" % errs)
            winners = [r for r in results if r[1]]
            self.assertEqual(len(winners), 1,
                             "ровно один accept должен победить: %r" % results)
            losers = [r for r in results if not r[1]]
            self.assertTrue(all(err in ("acd_not_queued", "operator_not_free")
                                for _, _, err in losers),
                            "проигравший должен получить явную причину: %r" % results)
            acd = db.fetch1("SELECT * FROM acd WHERE id=?", (self.acd_id,))
            # sim-провайдер бриджит сразу: любой живой статус FSM допустим
            self.assertIn(acd["status"], ("ringing", "answered", "bridged"))
            busy = db.fetch("SELECT id FROM operators WHERE id IN (?,?) AND status='busy'",
                            (self.op1, self.op2))
            self.assertEqual(len(busy), 1, "занят ровно один оператор")
        finally:
            eng.stop()


if __name__ == "__main__":
    unittest.main()
