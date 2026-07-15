"""SlaMonitor: deadlines reactivos via min-heap, sin cron ni polling en background.

`check_breaches(now)` se invoca desde los puntos naturales donde el motor ya
esta reaccionando a un evento (ej. al asignar/completar una tarea) — nunca
desde un hilo/temporizador propio. Usa borrado perezoso (lazy deletion): al
re-trackear o destrackear una tarea, la entrada vieja del heap queda
'huerfana' y se descarta silenciosamente cuando sale a la superficie.
"""

from __future__ import annotations

import heapq
import time
from typing import Callable, Optional


class SlaMonitor:
    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self.clock = clock
        self._heap: list[tuple[float, str]] = []
        self._deadlines: dict[str, float] = {}

    def track(self, task_id: str, sla_seconds: float, started_at: Optional[float] = None) -> float:
        start = started_at if started_at is not None else self.clock()
        deadline = start + sla_seconds
        self._deadlines[task_id] = deadline
        heapq.heappush(self._heap, (deadline, task_id))
        return deadline

    def untrack(self, task_id: str) -> None:
        self._deadlines.pop(task_id, None)

    def is_tracked(self, task_id: str) -> bool:
        return task_id in self._deadlines

    def check_breaches(self, now: Optional[float] = None) -> list[str]:
        """Extrae del heap (y retorna) los task_ids cuyo deadline ya paso."""
        now = now if now is not None else self.clock()
        breached: list[str] = []
        while self._heap and self._heap[0][0] <= now:
            deadline, task_id = heapq.heappop(self._heap)
            if self._deadlines.get(task_id) != deadline:
                continue  # entrada obsoleta: la tarea ya fue destrackeada o re-trackeada
            del self._deadlines[task_id]
            breached.append(task_id)
        return breached
