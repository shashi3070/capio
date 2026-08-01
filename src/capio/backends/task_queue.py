"""In-memory FIFO task queue backend (RFC-023 §3)."""

from __future__ import annotations

import queue
import threading
import time
import uuid
from typing import Any, Callable, Dict, List, Optional


class InMemoryTaskQueue:
    """Thread-safe FIFO task queue with optional background workers.

    ``put`` stores an envelope dict with ``id``/``task``/``args``/``kwargs``/``enqueued_at``.
    ``get`` blocks (with timeout) for the next task. ``process`` runs a worker loop that
    dispatches tasks to ``handler(task, envelope)``.
    """

    def __init__(self, maxsize: Optional[int] = None) -> None:
        self._queue: "queue.Queue[Dict[str, Any]]" = queue.Queue(maxsize=maxsize or 0)
        self._lock = threading.RLock()
        self._inflight: Dict[str, Any] = {}
        self._workers: List[threading.Thread] = []
        self._stop = threading.Event()

    def put(
        self,
        task: str,
        args: tuple = (),
        kwargs: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        envelope = {
            "id": str(uuid.uuid4()),
            "task": task,
            "args": args,
            "kwargs": dict(kwargs or {}),
            "enqueued_at": time.time(),
        }
        self._queue.put(envelope)
        return envelope

    def get(self, timeout: Optional[float] = None) -> Optional[Dict[str, Any]]:
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def task_done(self, envelope: Dict[str, Any]) -> None:
        self._queue.task_done()
        with self._lock:
            self._inflight.pop(envelope.get("id", ""), None)

    def mark_inflight(self, envelope: Dict[str, Any]) -> None:
        with self._lock:
            self._inflight[envelope.get("id", "")] = envelope

    def inflight(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [dict(e) for e in self._inflight.values()]

    @property
    def size(self) -> int:
        return self._queue.qsize()

    def start_workers(self, handler: Callable[[str, Dict[str, Any]], Any], count: int = 1) -> None:
        self._stop.clear()
        for _ in range(count):
            thread = threading.Thread(target=self._worker_loop, args=(handler,), daemon=True)
            thread.start()
            self._workers.append(thread)

    def stop_workers(self, timeout: Optional[float] = 2.0) -> None:
        self._stop.set()
        for thread in self._workers:
            if thread.is_alive():
                thread.join(timeout)

    def _worker_loop(self, handler: Callable[[str, Dict[str, Any]], Any]) -> None:
        while not self._stop.is_set():
            envelope = self.get(timeout=0.2)
            if envelope is None:
                continue
            self.mark_inflight(envelope)
            try:
                handler(envelope["task"], envelope)
            finally:
                self.task_done(envelope)

    def clear(self) -> None:
        with self._lock:
            while not self._queue.empty():
                try:
                    self._queue.get_nowait()
                    self._queue.task_done()
                except queue.Empty:
                    break
            self._inflight.clear()
