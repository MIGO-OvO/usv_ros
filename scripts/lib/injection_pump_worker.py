#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Threaded command worker for the inlet/injection pump."""

from __future__ import annotations

import queue
import threading
from dataclasses import dataclass
from typing import Callable, Dict, Optional


@dataclass(frozen=True)
class InjectionPumpWorkItem:
    command: str
    enabled: Optional[bool]
    speed: Optional[int]
    done: Optional[threading.Event]
    result: Dict[str, bool]


class InjectionPumpWorker:
    """Run injection pump serial commands outside the sampling step thread."""

    def __init__(
        self,
        send_command: Callable[[str], bool],
        on_success: Callable[[str, Optional[bool], Optional[int]], None],
        on_failure: Callable[[str], None],
        max_queue_size: int = 8,
    ) -> None:
        self._send_command = send_command
        self._on_success = on_success
        self._on_failure = on_failure
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
        item = InjectionPumpWorkItem(
            command=command,
            enabled=enabled,
            speed=speed,
            done=done,
            result=result,
        )
        if not self._enqueue(item, wait=wait, timeout=timeout):
            self._on_failure("queue full: {}".format(command))
            return False
        if done is None:
            return True
        if not done.wait(timeout):
            self._on_failure("timeout: {}".format(command))
            return False
        return bool(result["success"])

    def stop(self, timeout: float = 1.0) -> None:
        self._stop_event.set()
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        self._thread.join(timeout)

    def _enqueue(self, item: InjectionPumpWorkItem, wait: bool, timeout: float) -> bool:
        if wait:
            try:
                self._queue.put(item, timeout=timeout)
                return True
            except queue.Full:
                return False
        try:
            self._queue.put_nowait(item)
            return True
        except queue.Full:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                return False
            self._queue.put_nowait(item)
            return True

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                item = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue
            if item is None:
                return
            success = self._send_command(item.command)
            if success:
                self._on_success(item.command, item.enabled, item.speed)
            else:
                self._on_failure("send failed: {}".format(item.command))
            if item.done is not None:
                item.result["success"] = success
                item.done.set()
