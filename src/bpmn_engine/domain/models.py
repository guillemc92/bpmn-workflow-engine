"""Modelo de dominio: DEFINICION de un workflow (plantilla, no instancia).

`Workflow` es un grafo dirigido: `Task` son los nodos, `Transition` son las
aristas. Las compuertas logicas (`LogicGate`) viven embebidas en la tarea
destino (no son nodos propios del grafo) siguiendo la decision D3 del plan.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from bpmn_engine.domain.enums import (
    CompletionPolicy,
    GateType,
    Role,
    ResourceType,
    TaskType,
    TransitionType,
    WorkflowStatus,
)


class WorkflowValidationError(ValueError):
    """Violacion de una regla de construccion del grafo (ver Workflow.__post_init__)."""


@dataclass(frozen=True)
class ResourceSpec:
    """Requisito de recurso que una tarea necesita para poder ejecutarse."""

    resource_type: ResourceType
    quantity: int = 1
    skills: tuple[Role, ...] = ()

    def __post_init__(self) -> None:
        if self.quantity < 1:
            raise WorkflowValidationError("ResourceSpec.quantity debe ser >= 1")


@dataclass(frozen=True)
class LogicGate:
    """Compuerta logica evaluada antes de disparar una tarea con multiples entradas.

    `evaluator` es obligatorio para SCRIPT/REST/LAMBDA (logica custom); para
    AND/OR/XOR/COMPLEX la evaluacion la hace el runtime a partir de
    `TaskInstance`s predecesoras, por lo que aqui puede ir en None.
    REST y LAMBDA son mocks explicitos (D3): `evaluator` simplemente retorna
    un valor fijo o consultado desde un dict de fixtures inyectado por el caller.
    """

    gate_type: GateType
    evaluator: Optional[Callable[..., bool]] = None

    def __post_init__(self) -> None:
        if self.gate_type in (GateType.SCRIPT, GateType.REST, GateType.LAMBDA) and self.evaluator is None:
            raise WorkflowValidationError(
                f"LogicGate de tipo {self.gate_type.name} requiere un 'evaluator'"
            )


@dataclass(frozen=True)
class Transition:
    """Arista dirigida entre dos tareas de la misma Workflow."""

    source_task_id: str
    target_task_id: str
    transition_type: TransitionType = TransitionType.FORWARD
    condition: Optional[Callable[..., bool]] = None
    max_retries: Optional[int] = None
    exhausted_status: Optional[str] = None

    def __post_init__(self) -> None:
        if self.source_task_id == self.target_task_id:
            raise WorkflowValidationError("Una Transition no puede apuntar a la misma tarea (self-loop)")
        if self.transition_type is TransitionType.BACKWARD:
            if self.max_retries is None or self.max_retries < 1:
                raise WorkflowValidationError(
                    "Transition BACKWARD requiere 'max_retries' >= 1 (control de reintentos, RN incidentes)"
                )
            if self.exhausted_status is None:
                raise WorkflowValidationError(
                    "Transition BACKWARD requiere 'exhausted_status' (estado al agotar reintentos)"
                )


@dataclass
class Task:
    """Nodo del grafo. `is_start`/`is_end` se derivan al construir la Workflow."""

    id: str
    name: str
    task_type: TaskType = TaskType.MANUAL
    logic_gate: Optional[LogicGate] = None
    resource_specs: tuple[ResourceSpec, ...] = ()
    required_role: Optional[Role] = None
    completion_policy: CompletionPolicy = CompletionPolicy.ALL
    sla_seconds: Optional[float] = None

    def __post_init__(self) -> None:
        if not self.id:
            raise WorkflowValidationError("Task.id no puede ser vacio")
        if self.sla_seconds is not None and self.sla_seconds <= 0:
            raise WorkflowValidationError("Task.sla_seconds debe ser > 0 si se especifica")


@dataclass
class Worker:
    """Recurso humano/sistema asignable a tareas via el algoritmo skill-based -> least-loaded."""

    id: str
    name: str
    skills: tuple[Role, ...] = ()
    current_load: int = 0

    def has_skill(self, role: Optional[Role]) -> bool:
        return role is None or role in self.skills


class DependencyMatrix:
    """Vista derivada de adyacencia sobre las transiciones FORWARD de una Workflow.

    Las transiciones BACKWARD (retrabajo) se excluyen deliberadamente: son
    ciclos intencionales manejados por el mecanismo de incidentes/reset, no
    por el grafo de avance normal, que debe seguir siendo un DAG.
    """

    def __init__(self, tasks: dict[str, Task], transitions: list[Transition]) -> None:
        self._tasks = tasks
        self._forward = [t for t in transitions if t.transition_type is TransitionType.FORWARD]
        self._successors: dict[str, list[str]] = {tid: [] for tid in tasks}
        self._predecessors: dict[str, list[str]] = {tid: [] for tid in tasks}
        for t in self._forward:
            self._successors[t.source_task_id].append(t.target_task_id)
            self._predecessors[t.target_task_id].append(t.source_task_id)

    def successors(self, task_id: str) -> list[str]:
        return list(self._successors.get(task_id, ()))

    def predecessors(self, task_id: str) -> list[str]:
        return list(self._predecessors.get(task_id, ()))

    def start_task_ids(self) -> list[str]:
        return [tid for tid, preds in self._predecessors.items() if not preds]

    def end_task_ids(self) -> list[str]:
        return [tid for tid, succs in self._successors.items() if not succs]

    def is_acyclic(self) -> bool:
        """DFS de 3 colores sobre el subgrafo FORWARD."""
        WHITE, GRAY, BLACK = 0, 1, 2
        color = {tid: WHITE for tid in self._tasks}

        def visit(node: str) -> bool:
            color[node] = GRAY
            for nxt in self._successors[node]:
                if color[nxt] == GRAY:
                    return False
                if color[nxt] == WHITE and not visit(nxt):
                    return False
            color[node] = BLACK
            return True

        return all(color[tid] != WHITE or visit(tid) for tid in self._tasks)


@dataclass
class Workflow:
    """DEFINICION (plantilla) de un proceso: grafo dirigido de `Task` + `Transition`."""

    id: str
    name: str
    tasks: dict[str, Task]
    transitions: list[Transition] = field(default_factory=list)
    version: int = 1
    status: WorkflowStatus = WorkflowStatus.DRAFT
    start_task_id: Optional[str] = None

    def __post_init__(self) -> None:
        self._validate_references()
        matrix = self.dependency_matrix()

        start_ids = matrix.start_task_ids()
        if len(start_ids) != 1:
            raise WorkflowValidationError(
                f"Workflow debe tener exactamente 1 tarea de inicio, se encontraron {len(start_ids)}: {start_ids}"
            )
        self.start_task_id = start_ids[0]

        if not matrix.is_acyclic():
            raise WorkflowValidationError(
                "El subgrafo de transiciones FORWARD tiene un ciclo; los ciclos solo se permiten via BACKWARD"
            )
        # Nota: un DAG finito no vacio siempre tiene >= 1 nodo sumidero (sin
        # salidas FORWARD), por lo que "al menos 1 tarea final" ya queda
        # garantizado por el chequeo de aciclicidad anterior.

        for task_id, preds in ((tid, matrix.predecessors(tid)) for tid in self.tasks):
            if len(preds) > 1 and self.tasks[task_id].logic_gate is None:
                raise WorkflowValidationError(
                    f"Task '{task_id}' tiene {len(preds)} entradas FORWARD y requiere un logic_gate (join)"
                )

    def _validate_references(self) -> None:
        for t in self.transitions:
            if t.source_task_id not in self.tasks:
                raise WorkflowValidationError(f"Transition.source_task_id desconocido: {t.source_task_id}")
            if t.target_task_id not in self.tasks:
                raise WorkflowValidationError(f"Transition.target_task_id desconocido: {t.target_task_id}")

    def dependency_matrix(self) -> DependencyMatrix:
        return DependencyMatrix(self.tasks, self.transitions)

    def outgoing(self, task_id: str, transition_type: Optional[TransitionType] = None) -> list[Transition]:
        return [
            t
            for t in self.transitions
            if t.source_task_id == task_id and (transition_type is None or t.transition_type is transition_type)
        ]

    def incoming(self, task_id: str, transition_type: Optional[TransitionType] = None) -> list[Transition]:
        return [
            t
            for t in self.transitions
            if t.target_task_id == task_id and (transition_type is None or t.transition_type is transition_type)
        ]
