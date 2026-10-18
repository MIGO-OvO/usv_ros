#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Threaded command worker for the inlet/injection pump."""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass
from typing import Callable, Dict, Optional


@dataclass(frozen=True)
class InjectionPumpWorkItem:
    command: str
    enabled: Optional[bool]
    speed: Optional[int]
    done: Optional[threading.Event]
    result: Dict[str, bool]
    generation: int
    context: object
    deadline: float
    cancelled: threading.Event


class InjectionPumpWorker:
    """Run injection pump serial commands outside the sampling step thread."""

    def __init__(
        self,
        send_command: Callable[[str, Callable[[], bool]], bool],
        on_success: Callable[[str, Optional[bool], Optional[int]], None],
        on_failure: Callable[[str], None],
        max_queue_size: int = 8,
        guard_lock=None,
        context: Callable[[], object] = lambda: None,
    ) -> None:
        self._send_command = send_command
        self._on_success = on_success
        self._on_failure = on_failure
        # Shared with the node's final serial-write/connection-swap boundary.
        self._guard_lock = guard_lock if guard_lock is not None else threading.RLock()
        self._context = context
        self._generation = 0
        self._queue: "queue.Queue[Optional[InjectionPumpWorkItem]]" = queue.Queue(maxsize=max_queue_size)
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._run, name="injection-pump-worker", daemon=True)
        self._thread.start()

    def submit(
        self,
        command: str,
        enabled: Optional[bool] = None,
        speed: Optional[int] = None,
        wait: bool = True,
        timeout: float = 2.0,
    ) -> bool:
        done = threading.Event() if wait else None
        result = {"success": False}
        deadline = time.monotonic() + max(0.0, timeout)
        with self._guard_lock:
            item = InjectionPumpWorkItem(
                command, enabled, speed, done, result, self._generation,
                self._context(), deadline, threading.Event())
        if not self._enqueue(item, wait=wait, timeout=timeout):
            with self._guard_lock:
                if self._same_context(item):
                    self._on_failure("queue full: {}".format(command))
            return False
        if done is None:
            return True
        if not done.wait(max(0.0, deadline - time.monotonic())):
            item.cancelled.set()
            with self._guard_lock:
                if self._same_context(item):
                    self._on_failure("timeout; execution unconfirmed: {}".format(command))
            return False
        return bool(result["success"])

    def stop(self, timeout: float = 1.0) -> None:
        self._stop_event.set()
        self.invalidate()
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        self._thread.join(timeout)

    def invalidate(self) -> None:
        """Cancel queued AND already dequeued work without waiting for dispatch."""
        with self._guard_lock:
            self._generation += 1
            while True:
                try:
                    item = self._queue.get_nowait()
                except queue.Empty:
                    break
                if item is not None:
                    item.cancelled.set()
                    if item.done is not None:
                        item.done.set()

    def _same_context(self, item):
        return item.generation == self._generation and item.context == self._context()

    def _current(self, item):
        with self._guard_lock:
            return (not self._stop_event.is_set() and self._same_context(item)
                    and not item.cancelled.is_set() and time.monotonic() < item.deadline)

    def _enqueue(self, item: InjectionPumpWorkItem, wait: bool, timeout: float) -> bool:
        if wait:
            try:
                self._queue.put(item, timeout=max(0.0, item.deadline - time.monotonic()))
                return True
            except queue.Full:
                return False
        try:
            self._queue.put_nowait(item)
            return True
        except queue.Full:
            return False  # Never evict a queued OFF to make room for an ON.

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                item = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue
            if item is None:
                return
            try:
                success = self._current(item) and self._send_command(
                    item.command, lambda: self._current(item))
                with self._guard_lock:
                    current = self._current(item)
                    item.result["success"] = bool(success and current)
                    if current:
                        if success:
                            self._on_success(item.command, item.enabled, item.speed)
                        else:
                            self._on_failure("send failed: {}".format(item.command))
            except Exception as exc:
                with self._guard_lock:
                    if self._current(item):
                        self._on_failure("send failed: {}: {}".format(item.command, exc))
            finally:
                if item.done is not None:
                    item.done.set()
