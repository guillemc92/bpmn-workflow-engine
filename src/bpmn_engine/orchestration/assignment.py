"""Algoritmo de asignacion de workers: skill-based -> least-loaded (recomendado por el enunciado, S5.2).

Funcion pura e independiente del Orchestrator para que sea testeable en
aislamiento (decision D5 del plan).
"""

from __future__ import annotations

from typing import Optional

from bpmn_engine.domain.enums import Role
from bpmn_engine.domain.models import Worker


def select_workers(workers: list[Worker], required_role: Optional[Role], count: int = 1) -> list[Worker]:
    """Filtra por skill requerida y desempata por menor carga actual (current_load)."""
    if count < 1:
        raise ValueError("select_workers requiere count >= 1")
    candidates = [w for w in workers if w.has_skill(required_role)]
    candidates.sort(key=lambda w: (w.current_load, w.id))
    return candidates[:count]
