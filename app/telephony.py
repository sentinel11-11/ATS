# -*- coding: utf-8 -*-
"""Телефония ATS v2: единый интерфейс TelephonyProvider и адаптеры.

Реализации:
- SimProvider        — симуляция (тесты, демо, «сухой» пилот без модемов/UIS);
- UISCallApiProvider — адаптер UIS (Call API / SIP) — каркас, заполняется по ответу UIS (T01);
- AsteriskAmiProvider— адаптер медиа-слоя Asterisk/AMI (SIP-транк к оператору) — каркас.

Фабрика выбирает адаптер по settings["provider"]. Новые операторы (МТС и др.) = новые классы
с тем же интерфейсом (мультиоператорность, задача T38).
"""
import queue
import random
import threading
import time
import uuid

from . import config


class ProviderNotConfigured(RuntimeError):
    pass


# ---------- Базовый интерфейс ----------
class TelephonyProvider:
    name = "base"
    interactive = False  # True, если провайдер умеет «интерактивный канал» (опрос/DTMF/агент)

    def __init__(self):
        self._sink = None          # очередь событий движка
        self._stop = threading.Event()

    def attach(self, sink: "queue.Queue"):
        self._sink = sink

    def emit(self, ev: dict):
        ev = dict(ev)
        ev["provider"] = self.name
        if self._sink:
            self._sink.put(ev)

    def configure(self, settings: dict):
        """Проверка/применение настроек; кидает ProviderNotConfigured при нехватке данных."""
        raise NotImplementedError

    def dial(self, req: dict):
        """Инициировать исходящий звонок. req: call_id, phone, caller_id, flow, meta..."""
        raise NotImplementedError

    def hangup(self, call_id):
        raise NotImplementedError

    def make_channel(self, call_id, phone):
        """Интерактивный канал (для agent-потока). У провайдеров с interactive=False вернёт None."""
        return None

    def stop(self):
        self._stop.set()


# ---------- Симуляция ----------
class SimChannel:
    """Канал «абонент» для провайдера sim: ask() возвращает ответ из сценария."""
    def __init__(self, provider, call_id, phone):
        self.provider = provider
        self.call_id = call_id
        self.phone = phone
        self.log = []

    def say(self, text):
        self.log.append(("say", text))
        time.sleep(0.05 if config.FAST else 0.15)

    def ask(self, text, timeout=10):
        self.log.append(("ask", text))
        ans = self.provider.sim_answers.get(self.phone, self.provider.default_answer)
        self.log.append(("answer", ans))
        return ans

    def transcript(self):
        return " | ".join("{}: {}".format(k, v) for k, v in self.log)


class SimProvider(TelephonyProvider):
    name = "sim"
    interactive = True

    def __init__(self):
        super().__init__()
        self.outcome_override = {}    # phone -> результат (для тестов)
        self.sim_answers = {}         # phone -> ответ абонента
        self.default_answer = "1"
        self._events_per_call = {}
        self._lock = threading.RLock()
        self._threads = []
        self.profile = {"answered_human": 45, "machine": 10, "busy": 15,
                        "no_answer": 20, "failed": 8, "blocked": 2}

    def configure(self, settings: dict):
        self.profile = dict(settings.get("sim_outcome", self.profile))
        self.default_answer = str(settings.get("sim_answer", "1"))
        return self

    def _pick(self, phone):
        with self._lock:
            if phone in self.outcome_override:
                return self.outcome_override[phone]
        rnd = random.Random(phone)
        table = []
        for k, v in self.profile.items():
            table += [k] * max(1, int(v))
        return rnd.choice(table)

    def _wait_hangup(self, call_id, max_sec):
        with self._lock:
            ev = self._events_per_call.setdefault(call_id, threading.Event())
        ev.wait(max_sec)
        return ev.is_set()

    def dial(self, req: dict):
        t = threading.Thread(target=self._run, args=(dict(req),), daemon=True)
        self._threads.append(t)
        t.start()
        return True

    def hangup(self, call_id):
        with self._lock:
            ev = self._events_per_call.get(call_id)
        if ev:
            ev.set()

    def make_channel(self, call_id, phone):
        return SimChannel(self, call_id, phone)

    def set_outcome(self, phone, outcome):
        with self._lock:
            self.outcome_override[phone] = outcome

    def set_answer(self, phone, answer):
        with self._lock:
            self.sim_answers[phone] = answer

    def _run(self, req):
        call_id = req["call_id"]
        phone = req["phone"]
        ring_delay = 0.02 if config.FAST else 1.0
        time.sleep(ring_delay * 0.3)
        self.emit({"event": "ring", "call_id": call_id})
        time.sleep(ring_delay * 0.7)
        outcome = self._pick(phone)
        if outcome == "answered_human":
            self.emit({"event": "answered", "call_id": call_id, "human": True})
        elif outcome == "answered_machine":
            self.emit({"event": "answered", "call_id": call_id, "human": False})
        elif outcome == "busy":
            self.emit({"event": "status", "call_id": call_id, "status": "busy", "detail": "Занято"})
            return
        elif outcome == "no_answer":
            self.emit({"event": "status", "call_id": call_id, "status": "no_answer", "detail": "Нет ответа"})
            return
        elif outcome == "failed":
            self.emit({"event": "status", "call_id": call_id, "status": "failed", "detail": "Сбой оператора"})
            return
        elif outcome == "blocked":
            self.emit({"event": "status", "call_id": call_id, "status": "blocked", "detail": "Вызов отклонён (антиспам)"})
            return
        # Ответили: держим канал, пока движок не завершит (agent/message/operator)
        max_sec = 3 if config.FAST else 180
        if not self._wait_hangup(call_id, max_sec):
            self.emit({"event": "dropped", "call_id": call_id, "detail": "Таймаут удержания канала"})
            return
        self.emit({"event": "done", "call_id": call_id, "detail": "Разговор завершён"})

    def stop(self):
        super().stop()
        with self._lock:
            for ev in self._events_per_call.values():
                ev.set()


# ---------- UIS (Call API / SIP) — каркас ----------
class UISCallApiProvider(TelephonyProvider):
    """Адаптер UIS (МегаФон «Ювис»). Заполняется по ответу UIS (задача T01/дорожная карта).

    Здесь фиксируется ТОЛЬКО контракт вызова, чтобы ядро не зависело от UIS.
    Фактическая реализация: HTTP-запросы к Call API UIS (исходящий звонок, вебхуки статусов)
    или управление SIP-линией через медиа-слой (тогда используется AsteriskAmiProvider).
    """
    name = "uis"

    def configure(self, settings: dict):
        cfg = settings.get("uis", {}) or {}
        if not cfg.get("api_url") or not cfg.get("api_key"):
            raise ProviderNotConfigured(
                "UIS не настроен: заполните settings.uis.api_url и settings.uis.api_key "
                "(данные выдаёт UIS после запроса T01). Пока используется провайдер sim.")
        return self

    def dial(self, req: dict):
        # TODO(T13): POST {api_url}/call/outgoing {phone, caller_id, ...}
        # TODO(T13): вебхуки UIS -> self.emit({"event": ..., "call_id": req["call_id"]})
        raise ProviderNotConfigured("UIS dial: реализовать после получения доступа к Call API UIS (T01/T13).")


# ---------- Asterisk/AMI — каркас ----------
class AsteriskAmiProvider(TelephonyProvider):
    """Управление медиа-слоем (Asterisk) по AMI: originate/дозвон, AMD, трансфер на оператора.
    Используется, когда оператор дал SIP-транк/линии (вариант C/D плана). Каркас (T14/T38)."""
    name = "ami"

    def configure(self, settings: dict):
        cfg = settings.get("ami", {}) or {}
        if not cfg.get("user"):
            raise ProviderNotConfigured(
                "Asterisk AMI не настроен (settings.ami.user/secret). Пока используется провайдер sim.")
        return self

    def dial(self, req: dict):
        # TODO(T14): AMI Action=Originate  Channel=PJSIP/{caller_id}/+7..., Context=ats-out,
        #            Variable: CALL_ID=..., AMD=yes, агент/трансфер через ACD-контекст.
        raise ProviderNotConfigured("AMI dial: реализовать после настройки Asterisk и SIP-транка (T14).")


def make_provider(settings: dict) -> TelephonyProvider:
    name = settings.get("provider", "sim")
    if name == "uis":
        return UISCallApiProvider().configure(settings)
    if name == "ami":
        return AsteriskAmiProvider().configure(settings)
    return SimProvider().configure(settings)
