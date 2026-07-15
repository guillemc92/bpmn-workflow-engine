"""Enumeraciones del modelo de dominio (definicion de workflow).

Nombres de clases/miembros en ingles (estandar del enunciado); los
comentarios que aclaran intencion quedan en espanol.
"""

from __future__ import annotations

from enum import Enum, auto


class WorkflowStatus(Enum):
    """Ciclo de vida de la DEFINICION (plantilla) de un workflow, no de una instancia."""

    DRAFT = auto()
    PUBLISHED = auto()
    DEPRECATED = auto()


class TaskStatus(Enum):
    """Estados de una TaskInstance en ejecucion (maquina de estados, ver runtime)."""

    PENDING = auto()
    READY = auto()
    ASSIGNED = auto()
    IN_PROGRESS = auto()
    COMPLETED = auto()
    FAILED = auto()
    SKIPPED = auto()
    CANCELLED = auto()


class GateType(Enum):
    """Tipo de compuerta logica embebida en la tarea destino (Strategy)."""

    AND = auto()
    OR = auto()
    XOR = auto()
    COMPLEX = auto()
    SCRIPT = auto()
    REST = auto()
    LAMBDA = auto()


class ResourceType(Enum):
    """Tipo de recurso que una tarea puede requerir para ejecutarse."""

    HUMAN = auto()
    MACHINE = auto()
    SYSTEM = auto()
    EQUIPMENT = auto()


class TaskType(Enum):
    """Naturaleza de la tarea, al estilo BPMN (tipos de actividad)."""

    MANUAL = auto()
    USER = auto()
    SERVICE = auto()
    SCRIPT = auto()
    AUTOMATED = auto()


class Role(Enum):
    """Skill/rol requerido por una tarea y poseido por un Worker (asignacion skill-based)."""

    ANALYST = auto()
    REVIEWER = auto()
    SUPERVISOR = auto()
    ADMIN = auto()
    SYSTEM = auto()


class TransitionType(Enum):
    """FORWARD = avance normal del flujo. BACKWARD = retrabajo/incidente (permite ciclos)."""

    FORWARD = auto()
    BACKWARD = auto()


class IncidentType(Enum):
    """Categoria de la causa de un incidente (obliga a declarar 'reason')."""

    TECHNICAL = auto()
    BUSINESS = auto()
    DATA_QUALITY = auto()
    EXTERNAL_DEPENDENCY = auto()
    MANUAL_REJECTION = auto()


class CompletionPolicy(Enum):
    """Como se considera completada una tarea con multiples workers asignados."""

    ALL = auto()
    ANY = auto()
    QUORUM = auto()


class ResetScope(Enum):
    """Alcance del reset al procesar una transicion BACKWARD.

    ALL_DOWNSTREAM: cancela (si estan en curso) todas las tareas alcanzables
    hacia adelante desde la tarea objetivo (incluida la propia tarea que
    genero el incidente) y las libera para que el flujo normal las vuelva a
    disparar cuando la tarea objetivo se re-complete. SPECIFIC: solo crea una
    nueva iteracion de la tarea objetivo; el resto del grafo no se toca.
    """

    ALL_DOWNSTREAM = auto()
    SPECIFIC = auto()


class WorkflowInstanceStatus(Enum):
    """Estado global de una WorkflowInstance (ejecucion concreta de una Workflow)."""

    RUNNING = auto()
    COMPLETED = auto()
    FAILED = auto()
    CANCELLED = auto()
