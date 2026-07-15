"""Tests unitarios del algoritmo de incidentes/reset/reintentos (T7, S4.6)."""

from __future__ import annotations

import pytest

from bpmn_engine.domain import (
    IncidentType,
    ResetScope,
    Task,
    TaskStatus,
    Transition,
    TransitionType,
    Workflow,
    WorkflowInstanceStatus,
)
from bpmn_engine.runtime import TaskInstanceError, WorkflowInstance


def make_task(task_id: str, **kwargs) -> Task:
    return Task(id=task_id, name=task_id.title(), **kwargs)


def rework_workflow(max_retries: int = 2) -> Workflow:
    """a -> b -> c, con retrabajo BACKWARD de c hacia b (revision que puede rechazar)."""
    tasks = {t: make_task(t) for t in ("a", "b", "c")}
    transitions = [
        Transition(source_task_id="a", target_task_id="b"),
        Transition(source_task_id="b", target_task_id="c"),
        Transition(
            source_task_id="c",
            target_task_id="b",
            transition_type=TransitionType.BACKWARD,
            max_retries=max_retries,
            exhausted_status="FAILED",
        ),
    ]
    return Workflow(id="wf-rework", name="Retrabajo", tasks=tasks, transitions=transitions)


def run_to_task_in_progress(wi: WorkflowInstance, task_id: str) -> None:
    if wi.current(task_id).status is TaskStatus.PENDING:
        wi.enqueue(task_id)
    wi.assign_workers(task_id, ["w1"])
    wi.start(task_id)


def complete_chain(wi: WorkflowInstance, task_ids: list[str]) -> None:
    for tid in task_ids:
        run_to_task_in_progress(wi, tid)
        wi.complete(tid)


class TestApplyResetHappyPath:
    def test_reset_spawns_new_iteration_of_target_task(self):
        wi = WorkflowInstance(id="i1", workflow=rework_workflow())
        complete_chain(wi, ["a", "b"])
        run_to_task_in_progress(wi, "c")
        incident = wi.raise_incident("c", reason="calidad insuficiente", reset_scope=ResetScope.SPECIFIC)

        reset_targets = wi.apply_reset(incident)

        assert reset_targets == ["b"]
        assert wi.current("b").iteration == 2
        assert wi.current("b").status is TaskStatus.READY
        assert wi.status is WorkflowInstanceStatus.RUNNING

    def test_specific_scope_does_not_touch_the_failed_task(self):
        wi = WorkflowInstance(id="i1", workflow=rework_workflow())
        complete_chain(wi, ["a", "b"])
        run_to_task_in_progress(wi, "c")
        incident = wi.raise_incident("c", reason="rechazo", reset_scope=ResetScope.SPECIFIC)
        wi.apply_reset(incident)
        # 'c' (SPECIFIC no lo incluye) permanece en su estado FAILED original, como historial
        assert wi.current("c").status is TaskStatus.FAILED

    def test_all_downstream_scope_keeps_failed_history_but_frees_it_for_respawn(self):
        wi = WorkflowInstance(id="i1", workflow=rework_workflow())
        complete_chain(wi, ["a", "b"])
        run_to_task_in_progress(wi, "c")
        incident = wi.raise_incident("c", reason="rechazo", reset_scope=ResetScope.ALL_DOWNSTREAM)
        wi.apply_reset(incident)
        # El historial de 'c' (FAILED) no se reescribe (auditoria), pero ya no bloquea
        # que se le cree una nueva iteracion cuando 'b' se re-complete.
        assert wi.current("c").status is TaskStatus.FAILED
        assert wi.current("c").iteration == 1

        run_to_task_in_progress(wi, "b")
        newly_ready = wi.complete("b")
        assert newly_ready == ["c"]
        assert wi.current("c").iteration == 2

    def test_rework_flows_forward_again_after_reset(self):
        # ALL_DOWNSTREAM libera tambien a 'c' (alcanzable desde el target 'b'),
        # asi el flujo normal puede volver a dispararlo cuando 'b' se re-completa.
        wi = WorkflowInstance(id="i1", workflow=rework_workflow())
        complete_chain(wi, ["a", "b"])
        run_to_task_in_progress(wi, "c")
        incident = wi.raise_incident("c", reason="rechazo", reset_scope=ResetScope.ALL_DOWNSTREAM)
        wi.apply_reset(incident)

        run_to_task_in_progress(wi, "b")
        newly_ready = wi.complete("b")
        assert newly_ready == ["c"]
        assert wi.current("c").iteration == 2


class TestRetryExhaustion:
    def test_retries_exhausted_returns_none_and_marks_workflow_failed(self):
        wi = WorkflowInstance(id="i1", workflow=rework_workflow(max_retries=1))
        complete_chain(wi, ["a", "b"])
        run_to_task_in_progress(wi, "c")
        incident = wi.raise_incident("c", reason="rechazo #1")
        assert wi.apply_reset(incident) == ["b"]  # 1er intento, dentro del limite

        run_to_task_in_progress(wi, "b")
        wi.complete("b")
        run_to_task_in_progress(wi, "c")
        incident_2 = wi.raise_incident("c", reason="rechazo #2")
        result = wi.apply_reset(incident_2)  # 2do intento, excede max_retries=1

        assert result is None
        assert wi.status is WorkflowInstanceStatus.FAILED
        assert wi.current("c").status is TaskStatus.FAILED  # queda en su estado terminal

    def test_retry_count_increments_per_transition(self):
        wi = WorkflowInstance(id="i1", workflow=rework_workflow(max_retries=3))
        assert wi.retry_count("c", "b") == 0
        complete_chain(wi, ["a", "b"])
        run_to_task_in_progress(wi, "c")
        incident = wi.raise_incident("c", reason="rechazo")
        wi.apply_reset(incident)
        assert wi.retry_count("c", "b") == 1


class TestRaiseIncidentValidation:
    def test_apply_reset_without_backward_transition_raises(self):
        tasks = {"a": make_task("a"), "b": make_task("b")}
        wf = Workflow(id="wf-linear", name="Lineal", tasks=tasks, transitions=[Transition("a", "b")])
        wi = WorkflowInstance(id="i1", workflow=wf)
        run_to_task_in_progress(wi, "a")
        incident = wi.raise_incident("a", reason="sin punto de retrabajo definido")
        with pytest.raises(TaskInstanceError, match="BACKWARD"):
            wi.apply_reset(incident)

    def test_incident_default_type_and_scope(self):
        wi = WorkflowInstance(id="i1", workflow=rework_workflow())
        complete_chain(wi, ["a", "b"])
        run_to_task_in_progress(wi, "c")
        incident = wi.raise_incident("c", reason="motivo")
        assert incident.incident_type is IncidentType.MANUAL_REJECTION
        assert incident.reset_scope is ResetScope.ALL_DOWNSTREAM
