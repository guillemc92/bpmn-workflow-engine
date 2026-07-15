"""Orchestrator: coordina WorkflowInstance + ReadyQueue + asignacion de workers.

Observer sobre la ReadyQueue: cuando una tarea entra a la cola (`push`), el
Orchestrator recibe el evento sincronicamente y emite el hook `onReady`. El
resto de hooks (`onAssign/onStart/onComplete/onIncident`) se emiten desde los
metodos publicos que envuelven las operaciones de ciclo de vida de
`WorkflowInstance`, agregandoles la semantica de negocio de orquestacion
(asignacion de workers, liberacion de carga, politica de completion).

`onReset`/`onRetryExhausted` (S4.6) y `onSlaBreach` (deadlines reactivos, sin
cron) reusan el mismo `self._emit`. `run_task` integra el Executor real
(`SequentialExecutor` por defecto, o `ThreadPoolExecutor` para concurrencia
de verdad) con el ciclo de vida: al resolverse el Future completa la tarea o
levanta un incidente automaticamente.
"""

from __future__ import annotations

import threading
from collections import defaultdict
from typing import Any, Callable, Optional

from bpmn_engine.domain.enums import CompletionPolicy, IncidentType, ResetScope
from bpmn_engine.domain.models import Worker
from bpmn_engine.execution.executor import Executor, Future, SequentialExecutor
from bpmn_engine.execution.sla import SlaMonitor
from bpmn_engine.orchestration.assignment import select_workers
from bpmn_engine.orchestration.queue import ReadyQueue
from bpmn_engine.persistence.repository import InMemoryRepository
from bpmn_engine.runtime.models import Incident, WorkflowInstance

_COMPLETION_THRESHOLD: dict[CompletionPolicy, Callable[[int], int]] = {
    CompletionPolicy.ALL: lambda total: total,
    CompletionPolicy.ANY: lambda total: 1,
    CompletionPolicy.QUORUM: lambda total: (total // 2) + 1,
}


class OrchestratorError(ValueError):
    """Uso invalido del Orchestrator (worker no asignado, cola vacia, etc.)."""


class Orchestrator:
    def __init__(
        self,
        workflow_instance: WorkflowInstance,
        workers: InMemoryRepository[Worker],
        executor: Optional[Executor] = None,
        sla_monitor: Optional[SlaMonitor] = None,
    ) -> None:
        self.wi = workflow_instance
        self.workers = workers
        self.queue = ReadyQueue()
        self.executor: Executor = executor if executor is not None else SequentialExecutor()
        self.sla = sla_monitor if sla_monitor is not None else SlaMonitor(clock=workflow_instance.clock)
        self._hooks: dict[str, list[Callable[..., None]]] = defaultdict(list)
        self._worker_reports: dict[str, set[str]] = {}
        self._futures: dict[str, Future] = {}
        self._lock = threading.Lock()
        self.queue.subscribe(self._on_task_pushed)

    # -- hooks ------------------------------------------------------------

    def on(self, event: str, callback: Callable[..., None]) -> None:
        self._hooks[event].append(callback)

    def _emit(self, event: str, **payload) -> None:
        for callback in self._hooks.get(event, ()):
            callback(**payload)

    def _on_task_pushed(self, task_id: str) -> None:
        self._emit("onReady", task_id=task_id)

    # -- orquestacion -------------------------------------------------------

    def start_workflow(self) -> None:
        """Encola la tarea de inicio de la Workflow (unico punto de entrada manual)."""
        task_id = self.wi.workflow.start_task_id
        self.wi.enqueue(task_id)
        self.queue.push(task_id)

    def assign_next(self) -> Optional[str]:
        """Extrae una tarea de la ReadyQueue y le asigna workers (skill-based -> least-loaded).

        Si no hay workers disponibles con la skill requerida, reencola la
        tarea (backpressure reactivo) y retorna None.
        """
        task_id = self.queue.pop()
        if task_id is None:
            return None
        task_def = self.wi.workflow.tasks[task_id]
        count = max((spec.quantity for spec in task_def.resource_specs), default=1)
        chosen = select_workers(self.workers.list(), task_def.required_role, count=count)
        if len(chosen) < count:
            self.queue.push(task_id)
            return None
        worker_ids = [w.id for w in chosen]
        self.wi.assign_workers(task_id, worker_ids)
        for worker in chosen:
            worker.current_load += 1
        self._emit("onAssign", task_id=task_id, worker_ids=worker_ids)
        return task_id

    def start_task(self, task_id: str) -> None:
        self.wi.start(task_id)
        task_def = self.wi.workflow.tasks[task_id]
        if task_def.sla_seconds is not None:
            self.sla.track(task_id, task_def.sla_seconds, started_at=self.wi.current(task_id).started_at)
        self._emit("onStart", task_id=task_id)

    def check_sla_breaches(self, now: Optional[float] = None) -> list[str]:
        """Revision reactiva de deadlines (sin cron): se llama en los puntos donde el motor ya reacciona a eventos."""
        breached = self.sla.check_breaches(now)
        for task_id in breached:
            self._emit("onSlaBreach", task_id=task_id)
        return breached

    def run_task(self, task_id: str, fn: Callable[[], Any]) -> Future:
        """Inicia la tarea y ejecuta `fn` en `self.executor` (secuencial o concurrente de verdad).

        Al resolverse el Future, completa la tarea automaticamente si `fn` no
        lanzo excepcion, o levanta un incidente con la excepcion como razon.
        """
        self.start_task(task_id)
        future = self.executor.submit(fn)
        self._futures[task_id] = future

        def _on_done(f: Future) -> None:
            self._futures.pop(task_id, None)
            if f.cancelled():
                return
            exc = f.exception()
            with self._lock:
                if exc is not None:
                    self.raise_incident(task_id, reason=str(exc))
                else:
                    self.complete_task(task_id)

        future.add_done_callback(_on_done)
        return future

    def report_worker_complete(self, task_id: str, worker_id: str) -> Optional[list[str]]:
        """Registra que `worker_id` termino su parte de `task_id`.

        La tarea se completa (dispara `complete_task`) recien cuando se
        alcanza el umbral de la `CompletionPolicy` de la Task (ALL/ANY/QUORUM).
        """
        instance = self.wi.current(task_id)
        if worker_id not in instance.assigned_worker_ids:
            raise OrchestratorError(f"Worker '{worker_id}' no esta asignado a la tarea '{task_id}'")
        reports = self._worker_reports.setdefault(task_id, set())
        reports.add(worker_id)
        task_def = self.wi.workflow.tasks[task_id]
        total = len(instance.assigned_worker_ids)
        threshold = _COMPLETION_THRESHOLD[task_def.completion_policy](total)
        if len(reports) >= threshold:
            return self.complete_task(task_id)
        return None

    def complete_task(self, task_id: str) -> list[str]:
        instance = self.wi.current(task_id)
        for worker_id in instance.assigned_worker_ids:
            worker = self.workers.get(worker_id)
            if worker is not None:
                worker.current_load = max(0, worker.current_load - 1)
        self._worker_reports.pop(task_id, None)
        self.sla.untrack(task_id)
        newly_ready = self.wi.complete(task_id)
        self._emit("onComplete", task_id=task_id, newly_ready=newly_ready)
        for ready_task_id in newly_ready:
            self.queue.push(ready_task_id)
        return newly_ready

    def raise_incident(
        self,
        task_id: str,
        reason: str,
        incident_type: IncidentType = IncidentType.MANUAL_REJECTION,
        reset_scope: ResetScope = ResetScope.ALL_DOWNSTREAM,
    ) -> Incident:
        self.sla.untrack(task_id)
        incident = self.wi.raise_incident(task_id, reason, incident_type, reset_scope)
        self._emit("onIncident", task_id=task_id, incident=incident)
        return incident

    def resolve_incident(self, incident: Incident) -> Optional[list[str]]:
        """Aplica el reset (S4.6) para `incident`; emite onReset o onRetryExhausted segun corresponda.

        Si la tarea que genero el incidente tenia un Future en vuelo (ejecucion
        concurrente via `run_task`), se cancela aqui — Python no puede forzar
        la interrupcion de un hilo ya corriendo, pero si evita que su resultado
        se procese despues de que el reset ya movio el estado hacia adelante.
        """
        future = self._futures.pop(incident.task_id, None)
        if future is not None:
            future.cancel()
        reset_targets = self.wi.apply_reset(incident)
        if reset_targets is None:
            self._emit("onRetryExhausted", task_id=incident.task_id, incident=incident)
            return None
        self._emit("onReset", task_id=incident.task_id, reset_targets=reset_targets, incident=incident)
        for target_id in reset_targets:
            self.queue.push(target_id)
        return reset_targets
