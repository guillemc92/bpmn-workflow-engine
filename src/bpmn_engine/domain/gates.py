"""Dispatcher de compuertas logicas (`can_start` por GateType) — Strategy pattern.

REST y LAMBDA son mocks explicitos (decision D3 del plan): en vez de hacer
una llamada HTTP o invocar una function real, `mock_rest_call`/`mock_lambda_invoke`
resuelven contra un diccionario de fixtures inyectado por quien construye el
`LogicGate`, dejando visible que el motor sabe *donde* conectaria la
integracion real sin necesitar infraestructura externa para la entrega.
"""

from __future__ import annotations

from typing import Callable

from bpmn_engine.domain.enums import GateType, TaskStatus
from bpmn_engine.domain.models import LogicGate, Task


def mock_rest_call(endpoint: str, fixtures: dict[str, bool]) -> bool:
    """Simula 'GET {endpoint} -> bool' consultando un dict de fixtures en vez de la red."""
    if endpoint not in fixtures:
        raise KeyError(f"mock_rest_call: no hay fixture para el endpoint '{endpoint}'")
    return fixtures[endpoint]


def mock_lambda_invoke(function_name: str, fixtures: dict[str, bool]) -> bool:
    """Simula invocar una funcion serverless que retorna bool, via fixtures."""
    if function_name not in fixtures:
        raise KeyError(f"mock_lambda_invoke: no hay fixture para la funcion '{function_name}'")
    return fixtures[function_name]


class GateEvaluator:
    """Evalua si una Task con LogicGate puede pasar a READY dado el estado de sus predecesoras."""

    @staticmethod
    def can_start(task: Task, pred_statuses: dict[str, TaskStatus]) -> bool:
        completed = {tid for tid, status in pred_statuses.items() if status is TaskStatus.COMPLETED}
        gate = task.logic_gate
        if gate is None:
            return len(completed) == len(pred_statuses)

        handler = _HANDLERS.get(gate.gate_type)
        if handler is not None:
            return handler(completed, pred_statuses)

        # SCRIPT / REST / LAMBDA: la validacion de LogicGate.__post_init__ ya
        # garantiza que 'evaluator' esta presente para estos tres tipos.
        return bool(gate.evaluator(pred_statuses))


def _and(completed: set[str], pred_statuses: dict[str, TaskStatus]) -> bool:
    return len(completed) == len(pred_statuses)


def _or(completed: set[str], pred_statuses: dict[str, TaskStatus]) -> bool:
    return len(completed) >= 1


def _xor(completed: set[str], pred_statuses: dict[str, TaskStatus]) -> bool:
    return len(completed) == 1


_HANDLERS: dict[GateType, Callable[[set[str], dict[str, TaskStatus]], bool]] = {
    GateType.AND: _and,
    GateType.OR: _or,
    GateType.XOR: _xor,
}
