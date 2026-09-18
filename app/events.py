# -*- coding: utf-8 -*-
"""Шина событий + SSE (Server-Sent Events) для обновления UI в реальном времени."""
import json
import queue
import threading
import time

from . import config

_subscribers = []
_lock = threading.RLock()


def publish(event_type: str, payload=None):
    ev = {"ts": time.time(), "type": event_type, "payload": payload or {}}
    with _lock:
        for q in _subscribers:
            try:
                q.put_nowait(ev)
            except queue.Full:
                try:
                    q.get_nowait()
                    q.put_nowait(ev)
                except Exception:
                    pass


class EventStream:
    """Один клиент SSE."""
    def __init__(self):
        self.q = queue.Queue(maxsize=200)
        with _lock:
            _subscribers.append(self.q)

    def close(self):
        with _lock:
            try:
                _subscribers.remove(self.q)
            except ValueError:
                pass

    def iter_events(self, keepalive=20.0):
        try:
            while True:
                try:
                    ev = self.q.get(timeout=keepalive)
                    yield "data: {}\n\n".format(json.dumps(ev, ensure_ascii=False))
                except queue.Empty:
                    yield ": ping\n\n"
        except GeneratorExit:
            self.close()
