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
    IncidentType,
    ResetScope,
    TaskStatus,
    TransitionType,
    WorkflowInstanceStatus,
)
from bpmn_engine.domain.gates import GateEvaluator
from bpmn_engine.domain.models import Workflow


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
        self._live_target_ids: set[str] = set()
        self._retry_counts: dict[tuple[str, str], int] = {}
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
            self.status = self._final_status()
        return newly_ready

    def navigate_to_targets(self, task_id: str) -> list[str]:
        """Evalua las tareas sucesoras (FORWARD) de `task_id` y crea/encola las que ya pueden arrancar."""
        matrix = self.workflow.dependency_matrix()
        newly_ready: list[str] = []
        for target_id in matrix.successors(task_id):
            if target_id in self._live_target_ids:
                continue  # ya tiene instancia viva (evita doble-disparo de joins); un reset la vuelve a liberar
            predecessors = matrix.predecessors(target_id)
            pred_statuses = {
                p: self.current(p).status for p in predecessors if p in self._instances
            }
            if len(pred_statuses) < len(predecessors):
                continue  # todavia falta que arranque/termine algun predecesor
            task_def = self.workflow.tasks[target_id]
            if GateEvaluator.can_start(task_def, pred_statuses):
                next_iteration = self.current(target_id).iteration + 1 if target_id in self._instances else 1
                self._spawn(target_id, iteration=next_iteration)
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

    def apply_reset(self, incident: Incident) -> Optional[list[str]]:
        """Aplica el algoritmo de incidentes/reset (S4.6) para `incident`.

        Busca la Transition BACKWARD que sale de la tarea del incidente,
        controla `max_retries` por (source, target), y si todavia hay
        reintentos disponibles cancela el alcance indicado por
        `incident.reset_scope` y crea una nueva iteracion de la tarea
        objetivo (READY). Retorna la lista de tareas reseteadas, o `None`
        si los reintentos ya estaban agotados (la tarea queda en su estado
        `exhausted_status` terminal y la WorkflowInstance pasa a FAILED).
        """
        backward = self.workflow.outgoing(incident.task_id, TransitionType.BACKWARD)
        if not backward:
            raise TaskInstanceError(
                f"No hay Transition BACKWARD definida desde '{incident.task_id}' para aplicar reset"
            )
        transition = backward[0]
        key = (transition.source_task_id, transition.target_task_id)
        attempt = self._retry_counts.get(key, 0) + 1
        self._retry_counts[key] = attempt

        if attempt > transition.max_retries:
            self.status = WorkflowInstanceStatus.FAILED
            return None

        reset_targets = self._resolve_reset_targets(transition.target_task_id, incident.reset_scope)
        for tid in reset_targets:
            if tid != transition.target_task_id:
                self._cancel_if_active(tid)

        next_iteration = self.current(transition.target_task_id).iteration + 1
        self._spawn(transition.target_task_id, iteration=next_iteration)
        self.enqueue(transition.target_task_id)
        self.status = WorkflowInstanceStatus.RUNNING
        return [transition.target_task_id]

    def retry_count(self, source_task_id: str, target_task_id: str) -> int:
        return self._retry_counts.get((source_task_id, target_task_id), 0)

    # -- internos -------------------------------------------------------

    def _resolve_reset_targets(self, target_task_id: str, reset_scope: ResetScope) -> list[str]:
        if reset_scope is ResetScope.SPECIFIC:
            return [target_task_id]
        matrix = self.workflow.dependency_matrix()
        seen: set[str] = set()
        stack = [target_task_id]
        while stack:
            tid = stack.pop()
            if tid in seen:
                continue
            seen.add(tid)
            stack.extend(matrix.successors(tid))
        return list(seen)

    def _cancel_if_active(self, task_id: str) -> None:
        if task_id not in self._instances:
            return
        instance = self.current(task_id)
        if instance.status not in _TERMINAL_STATUSES:
            self._transition(task_id, TaskStatus.CANCELLED, note="cancelado por reset de incidente")
        self._live_target_ids.discard(task_id)

    def _final_status(self) -> WorkflowInstanceStatus:
        statuses = {self.current(tid).status for tid in self._instances}
        if TaskStatus.FAILED in statuses or TaskStatus.CANCELLED in statuses:
            return WorkflowInstanceStatus.FAILED
        return WorkflowInstanceStatus.COMPLETED

    def _spawn(self, task_id: str, iteration: int) -> TaskInstance:
        instance = TaskInstance(id=f"{task_id}#{iteration}", task_id=task_id, iteration=iteration)
        self._instances.setdefault(task_id, []).append(instance)
        self._live_target_ids.add(task_id)
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

    def _all_terminal(self) -> bool:
        return all(self.current(tid).status in _TERMINAL_STATUSES for tid in self._instances)
