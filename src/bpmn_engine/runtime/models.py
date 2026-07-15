"""Runtime: ejecucion concreta (INSTANCIA) de una Workflow definida en domain/.

`WorkflowInstance` es la unica puerta de entrada para mutar el estado de una
ejecucion: valida la maquina de estados (`TaskStatus`) y registra cada cambio
en una traza append-only (`TraceEntry`), analoga en espiritu a la tabla
`edits` solo-INSERT del dominio clinico de origen de este arquitecto.
"""

from __future__ import annotations

import itertools
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from bpmn_engine.domain.enums import (
    GateType,
    IncidentType,
    ResetScope,
    TaskStatus,
    WorkflowInstanceStatus,
)
from bpmn_engine.domain.models import Task, Workflow


class TaskInstanceError(ValueError):
    """Uso invalido del ciclo de vida de una TaskInstance (transicion no permitida, datos faltantes)."""


_ALLOWED_TRANSITIONS: dict[TaskStatus, frozenset[TaskStatus]] = {
    TaskStatus.PENDING: frozenset({TaskStatus.READY, TaskStatus.SKIPPED, TaskStatus.CANCELLED}),
    TaskStatus.READY: frozenset({TaskStatus.ASSIGNED, TaskStatus.CANCELLED}),
    TaskStatus.ASSIGNED: frozenset({TaskStatus.IN_PROGRESS, TaskStatus.CANCELLED}),
    TaskStatus.IN_PROGRESS: frozenset({TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}),
    TaskStatus.COMPLETED: frozenset(),
    TaskStatus.FAILED: frozenset(),
    TaskStatus.SKIPPED: frozenset(),
    TaskStatus.CANCELLED: frozenset(),
}

_TERMINAL_STATUSES = frozenset(
    {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.SKIPPED, TaskStatus.CANCELLED}
)


@dataclass(frozen=True)
class TraceEntry:
    """Un renglon de la traza de ejecucion (append-only, nunca se modifica ni borra)."""

    seq: int
    task_id: str
    iteration: int
    from_status: Optional[TaskStatus]
    to_status: TaskStatus
    timestamp: float
    note: Optional[str] = None


@dataclass(frozen=True)
class ResourceInstance:
    """Recurso concreto asignado a una TaskInstance (materializa un ResourceSpec)."""

    resource_id: str
    resource_type: str
    quantity: int = 1


@dataclass(frozen=True)
class Incident:
    """Incidente levantado sobre una TaskInstance; obliga a declarar 'reason' (RN de trazabilidad)."""

    id: str
    task_id: str
    task_instance_id: str
    incident_type: IncidentType
    reason: str
    reset_scope: ResetScope
    iteration: int
    created_at: float


@dataclass
class TaskInstance:
    """Ejecucion concreta (una iteracion) de un `Task` de la definicion."""

    id: str
    task_id: str
    iteration: int = 1
    status: TaskStatus = TaskStatus.PENDING
    assigned_worker_ids: list[str] = field(default_factory=list)
    resource_instances: list[ResourceInstance] = field(default_factory=list)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None


class WorkflowInstance:
    """Ejecucion concreta de una `Workflow`. Unico punto de mutacion del runtime."""

    def __init__(self, id: str, workflow: Workflow, clock: Callable[[], float] = time.time) -> None:
        self.id = id
        self.workflow = workflow
        self.clock = clock
        self.status = WorkflowInstanceStatus.RUNNING
        self.trace: list[TraceEntry] = []
        self.incidents: list[Incident] = []
        self._instances: dict[str, list[TaskInstance]] = {}
        self._seq = itertools.count(1)
        self._spawn(workflow.start_task_id, iteration=1)

    # -- consulta -----------------------------------------------------

    def current(self, task_id: str) -> TaskInstance:
        instances = self._instances.get(task_id)
        if not instances:
            raise TaskInstanceError(f"No hay TaskInstance para '{task_id}' todavia")
        return instances[-1]

    def history(self, task_id: str) -> list[TaskInstance]:
        return list(self._instances.get(task_id, ()))

    def has_instance(self, task_id: str) -> bool:
        return task_id in self._instances

    # -- ciclo de vida (S4.2) ------------------------------------------

    def enqueue(self, task_id: str) -> TaskInstance:
        return self._transition(task_id, TaskStatus.READY, note="enqueued")

    def assign_workers(self, task_id: str, worker_ids: list[str]) -> TaskInstance:
        if not worker_ids:
            raise TaskInstanceError("assign_workers requiere al menos un worker_id")
        instance = self._transition(task_id, TaskStatus.ASSIGNED, note=f"workers={list(worker_ids)}")
        instance.assigned_worker_ids = list(worker_ids)
        return instance

    def start(self, task_id: str) -> TaskInstance:
        instance = self._transition(task_id, TaskStatus.IN_PROGRESS, note="started")
        instance.started_at = self.clock()
        return instance

    def assign_resources(self, task_id: str, resources: list[ResourceInstance]) -> TaskInstance:
        instance = self.current(task_id)
        if instance.status not in (TaskStatus.ASSIGNED, TaskStatus.IN_PROGRESS):
            raise TaskInstanceError(
                f"No se pueden asignar recursos a '{task_id}' en estado {instance.status.name}"
            )
        instance.resource_instances = [*instance.resource_instances, *resources]
        return instance

    def complete(self, task_id: str) -> list[str]:
        instance = self._transition(task_id, TaskStatus.COMPLETED, note="completed")
        instance.completed_at = self.clock()
        newly_ready = self.navigate_to_targets(task_id)
        if self._all_terminal():
            self.status = WorkflowInstanceStatus.COMPLETED
        return newly_ready

    def navigate_to_targets(self, task_id: str) -> list[str]:
        """Evalua las tareas sucesoras (FORWARD) de `task_id` y crea/encola las que ya pueden arrancar."""
        matrix = self.workflow.dependency_matrix()
        newly_ready: list[str] = []
        for target_id in matrix.successors(task_id):
            if target_id in self._instances:
                continue  # ya tiene instancia en esta iteracion (evita doble-disparo de joins)
            predecessors = matrix.predecessors(target_id)
            pred_statuses = {
                p: self.current(p).status for p in predecessors if p in self._instances
            }
            if len(pred_statuses) < len(predecessors):
                continue  # todavia falta que arranque/termine algun predecesor
            task_def = self.workflow.tasks[target_id]
            if self._gate_satisfied(task_def, pred_statuses):
                self._spawn(target_id, iteration=1)
                self.enqueue(target_id)
                newly_ready.append(target_id)
        return newly_ready

    def raise_incident(
        self,
        task_id: str,
        reason: str,
        incident_type: IncidentType = IncidentType.MANUAL_REJECTION,
        reset_scope: ResetScope = ResetScope.ALL_DOWNSTREAM,
    ) -> Incident:
        if not reason or not reason.strip():
            raise TaskInstanceError("raise_incident requiere 'reason' no vacio (trazabilidad obligatoria)")
        instance = self._transition(task_id, TaskStatus.FAILED, note=f"incident: {reason}")
        incident = Incident(
            id=f"incident-{len(self.incidents) + 1}",
            task_id=task_id,
            task_instance_id=instance.id,
            incident_type=incident_type,
            reason=reason,
            reset_scope=reset_scope,
            iteration=instance.iteration,
            created_at=self.clock(),
        )
        self.incidents.append(incident)
        return incident

    # -- internos -------------------------------------------------------

    def _spawn(self, task_id: str, iteration: int) -> TaskInstance:
        instance = TaskInstance(id=f"{task_id}#{iteration}", task_id=task_id, iteration=iteration)
        self._instances.setdefault(task_id, []).append(instance)
        self._record(instance, None, TaskStatus.PENDING, note="spawned")
        return instance

    def _record(
        self,
        instance: TaskInstance,
        from_status: Optional[TaskStatus],
        to_status: TaskStatus,
        note: Optional[str],
    ) -> None:
        self.trace.append(
            TraceEntry(
                seq=next(self._seq),
                task_id=instance.task_id,
                iteration=instance.iteration,
                from_status=from_status,
                to_status=to_status,
                timestamp=self.clock(),
                note=note,
            )
        )

    def _transition(self, task_id: str, to_status: TaskStatus, note: Optional[str] = None) -> TaskInstance:
        instance = self.current(task_id)
        allowed = _ALLOWED_TRANSITIONS[instance.status]
        if to_status not in allowed:
            raise TaskInstanceError(
                f"Transicion invalida para '{task_id}': {instance.status.name} -> {to_status.name}"
            )
        from_status = instance.status
        instance.status = to_status
        self._record(instance, from_status, to_status, note)
        return instance

    def _gate_satisfied(self, task: Task, pred_statuses: dict[str, TaskStatus]) -> bool:
        """Evaluacion base de compuertas (AND/OR/XOR) usada por navigate_to_targets.

        SCRIPT/REST/LAMBDA/COMPLEX se delegan al `evaluator` de la LogicGate si
        existe; el dispatcher completo por GateType se termina de resolver en
        el modulo de orquestacion (T4), que puede reemplazar este metodo via
        composicion sin tocar el resto del runtime.
        """
        completed = {tid for tid, status in pred_statuses.items() if status is TaskStatus.COMPLETED}
        if task.logic_gate is None:
            return len(completed) == len(pred_statuses)
        gate = task.logic_gate
        if gate.gate_type is GateType.AND:
            return len(completed) == len(pred_statuses)
        if gate.gate_type is GateType.OR:
            return len(completed) >= 1
        if gate.gate_type is GateType.XOR:
            return len(completed) == 1
        if gate.evaluator is not None:
            return bool(gate.evaluator(pred_statuses))
        return False

    def _all_terminal(self) -> bool:
        return all(self.current(tid).status in _TERMINAL_STATUSES for tid in self._instances)
