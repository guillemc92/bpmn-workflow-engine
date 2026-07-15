"""ReadyQueue: cola FIFO tipo SQS (push/pop) que notifica a sus observadores (Observer pattern).

Nada de `sched`/`cron`/polling: cada `push` dispara sincronicamente a los
observadores suscritos (decision D4 del plan) — el Orchestrator reacciona al
evento en vez de sondear la cola periodicamente.
"""

from __future__ import annotations

from collections import deque
from typing import Callable, Optional


class ReadyQueue:
    def __init__(self) -> None:
        self._queue: deque[str] = deque()
        self._observers: list[Callable[[str], None]] = []

    def subscribe(self, observer: Callable[[str], None]) -> None:
        self._observers.append(observer)

    def push(self, task_id: str) -> None:
        self._queue.append(task_id)
        for observer in self._observers:
            observer(task_id)

    def pop(self) -> Optional[str]:
        if not self._queue:
            return None
        return self._queue.popleft()

    def __len__(self) -> int:
        return len(self._queue)

    def __bool__(self) -> bool:
        return bool(self._queue)
