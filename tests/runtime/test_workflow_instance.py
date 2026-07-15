"""Tests unitarios del runtime (T3): ciclo de vida de WorkflowInstance/TaskInstance."""

from __future__ import annotations

import pytest

from bpmn_engine.domain import (
    GateType,
    IncidentType,
    LogicGate,
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


def linear_workflow() -> Workflow:
    tasks = {t: make_task(t) for t in ("a", "b", "c")}
    transitions = [
        Transition(source_task_id="a", target_task_id="b"),
        Transition(source_task_id="b", target_task_id="c"),
    ]
    return Workflow(id="wf-linear", name="Lineal", tasks=tasks, transitions=transitions)


def split_join_workflow(gate_type: GateType = GateType.AND, evaluator=None) -> Workflow:
    tasks = {
        "s": make_task("s"),
        "a": make_task("a"),
        "b": make_task("b"),
        "j": make_task("j", logic_gate=LogicGate(gate_type=gate_type, evaluator=evaluator)),
    }
    transitions = [
        Transition(source_task_id="s", target_task_id="a"),
        Transition(source_task_id="s", target_task_id="b"),
        Transition(source_task_id="a", target_task_id="j"),
        Transition(source_task_id="b", target_task_id="j"),
    ]
    return Workflow(id="wf-split", name="Split/Join", tasks=tasks, transitions=transitions)


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        self.value += 1.0
        return self.value


def run_task(wi: WorkflowInstance, task_id: str, workers=("w1",)) -> list[str]:
    # navigate_to_targets() ya deja encoladas (READY) las tareas con un solo
    # predecesor al completar ese predecesor, asi que solo encolamos aqui si
    # todavia esta PENDING (ej. la tarea de inicio, que nadie mas encola).
    if wi.current(task_id).status is TaskStatus.PENDING:
        wi.enqueue(task_id)
    wi.assign_workers(task_id, list(workers))
    wi.start(task_id)
    return wi.complete(task_id)


class TestLifecycleHappyPath:
    def test_spawns_start_task_as_pending(self):
        wi = WorkflowInstance(id="i1", workflow=linear_workflow(), clock=FakeClock())
        assert wi.current("a").status is TaskStatus.PENDING
        assert wi.status is WorkflowInstanceStatus.RUNNING

    def test_full_lifecycle_transitions(self):
        wi = WorkflowInstance(id="i1", workflow=linear_workflow(), clock=FakeClock())
        wi.enqueue("a")
        assert wi.current("a").status is TaskStatus.READY
        wi.assign_workers("a", ["w1"])
        assert wi.current("a").status is TaskStatus.ASSIGNED
        assert wi.current("a").assigned_worker_ids == ["w1"]
        wi.start("a")
        assert wi.current("a").status is TaskStatus.IN_PROGRESS
        assert wi.current("a").started_at is not None
        newly_ready = wi.complete("a")
        assert wi.current("a").status is TaskStatus.COMPLETED
        assert wi.current("a").completed_at is not None
        assert newly_ready == ["b"]

    def test_linear_workflow_completes_end_to_end(self):
        wi = WorkflowInstance(id="i1", workflow=linear_workflow(), clock=FakeClock())
        run_task(wi, "a")
        run_task(wi, "b")
        run_task(wi, "c")
        assert wi.status is WorkflowInstanceStatus.COMPLETED
        assert [t.to_status for t in wi.trace if t.task_id == "c"][-1] is TaskStatus.COMPLETED

    def test_assign_workers_requires_at_least_one(self):
        wi = WorkflowInstance(id="i1", workflow=linear_workflow(), clock=FakeClock())
        wi.enqueue("a")
        with pytest.raises(TaskInstanceError):
            wi.assign_workers("a", [])

    def test_invalid_transition_is_rejected(self):
        wi = WorkflowInstance(id="i1", workflow=linear_workflow(), clock=FakeClock())
        with pytest.raises(TaskInstanceError, match="PENDING -> ASSIGNED"):
            wi.assign_workers("a", ["w1"])

    def test_assign_resources_requires_assigned_or_in_progress(self):
        wi = WorkflowInstance(id="i1", workflow=linear_workflow(), clock=FakeClock())
        with pytest.raises(TaskInstanceError):
            wi.assign_resources("a", [])


class TestGates:
    def test_and_join_waits_for_both_branches(self):
        wi = WorkflowInstance(id="i1", workflow=split_join_workflow(GateType.AND), clock=FakeClock())
        run_task(wi, "s")
        assert not wi.has_instance("j")
        run_task(wi, "a")
        assert not wi.has_instance("j")  # falta 'b'
        run_task(wi, "b")
        assert wi.has_instance("j")
        assert wi.current("j").status is TaskStatus.READY

    def test_or_join_fires_with_a_single_branch(self):
        wi = WorkflowInstance(id="i1", workflow=split_join_workflow(GateType.OR), clock=FakeClock())
        run_task(wi, "s")
        run_task(wi, "a")
        assert wi.has_instance("j")

    def test_xor_join_fires_with_exactly_one_branch(self):
        wi = WorkflowInstance(id="i1", workflow=split_join_workflow(GateType.XOR), clock=FakeClock())
        run_task(wi, "s")
        run_task(wi, "a")
        assert wi.has_instance("j")

    def test_complex_gate_delegates_to_evaluator(self):
        calls = []

        def evaluator(pred_statuses):
            calls.append(dict(pred_statuses))
            return all(s is TaskStatus.COMPLETED for s in pred_statuses.values())

        wi = WorkflowInstance(
            id="i1",
            workflow=split_join_workflow(GateType.COMPLEX, evaluator=evaluator),
            clock=FakeClock(),
        )
        run_task(wi, "s")
        run_task(wi, "a")
        assert not wi.has_instance("j")
        run_task(wi, "b")
        assert wi.has_instance("j")
        assert calls  # el evaluator fue invocado


class TestIncidents:
    def test_raise_incident_requires_non_empty_reason(self):
        wi = WorkflowInstance(id="i1", workflow=linear_workflow(), clock=FakeClock())
        wi.enqueue("a")
        wi.assign_workers("a", ["w1"])
        wi.start("a")
        with pytest.raises(TaskInstanceError, match="reason"):
            wi.raise_incident("a", reason="   ")

    def test_raise_incident_marks_task_failed_and_records_incident(self):
        wi = WorkflowInstance(id="i1", workflow=linear_workflow(), clock=FakeClock())
        wi.enqueue("a")
        wi.assign_workers("a", ["w1"])
        wi.start("a")
        incident = wi.raise_incident(
            "a", reason="calidad de imagen insuficiente", incident_type=IncidentType.DATA_QUALITY
        )
        assert wi.current("a").status is TaskStatus.FAILED
        assert incident.reason == "calidad de imagen insuficiente"
        assert incident.reset_scope is ResetScope.ALL_DOWNSTREAM
        assert incident in wi.incidents


class TestTrace:
    def test_trace_is_append_only_and_ordered(self):
        wi = WorkflowInstance(id="i1", workflow=linear_workflow(), clock=FakeClock())
        run_task(wi, "a")
        seqs = [e.seq for e in wi.trace]
        assert seqs == sorted(seqs)
        assert wi.trace[0].from_status is None
        assert wi.trace[0].to_status is TaskStatus.PENDING
