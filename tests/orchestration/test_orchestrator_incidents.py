"""Tests de integracion Orchestrator + incidentes/reset (T7 via Orchestrator)."""

from __future__ import annotations

from bpmn_engine.domain import (
    ResetScope,
    Task,
    TaskStatus,
    Transition,
    TransitionType,
    Worker,
    Workflow,
)
from bpmn_engine.orchestration import Orchestrator
from bpmn_engine.persistence import InMemoryRepository
from bpmn_engine.runtime import WorkflowInstance


def rework_workflow(max_retries: int = 1) -> Workflow:
    tasks = {t: Task(id=t, name=t.title()) for t in ("a", "b", "c")}
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
    return Workflow(id="wf", name="Retrabajo", tasks=tasks, transitions=transitions)


def make_orchestrator(workflow: Workflow) -> Orchestrator:
    wi = WorkflowInstance(id="i1", workflow=workflow)
    workers: InMemoryRepository[Worker] = InMemoryRepository()
    workers.save(Worker(id="w1", name="W1"))
    return Orchestrator(workflow_instance=wi, workers=workers)


def run_task(orch: Orchestrator, task_id: str) -> None:
    orch.assign_next()
    orch.start_task(task_id)


class TestResolveIncident:
    def test_reset_emits_onreset_and_requeues_target(self):
        events = []
        orch = make_orchestrator(rework_workflow())
        orch.on("onReset", lambda **kw: events.append(("onReset", kw["reset_targets"])))
        orch.start_workflow()
        run_task(orch, "a")
        orch.complete_task("a")
        run_task(orch, "b")
        orch.complete_task("b")
        run_task(orch, "c")

        incident = orch.raise_incident("c", reason="rechazo", reset_scope=ResetScope.SPECIFIC)
        result = orch.resolve_incident(incident)

        assert result == ["b"]
        assert events == [("onReset", ["b"])]
        assert len(orch.queue) == 1  # 'b' quedo re-encolada

    def test_retry_exhausted_emits_hook_and_returns_none(self):
        events = []
        orch = make_orchestrator(rework_workflow(max_retries=1))
        orch.on("onRetryExhausted", lambda **kw: events.append(kw["task_id"]))
        orch.start_workflow()
        run_task(orch, "a")
        orch.complete_task("a")

        # 1er ciclo: b -> c -> incidente -> reset (dentro del limite, max_retries=1)
        run_task(orch, "b")
        orch.complete_task("b")
        run_task(orch, "c")
        first_incident = orch.raise_incident("c", reason="rechazo #1")
        assert orch.resolve_incident(first_incident) == ["b"]

        # 2do ciclo: se reencolo 'b' automaticamente; lo re-ejecutamos y volvemos a fallar 'c'
        run_task(orch, "b")
        orch.complete_task("b")
        run_task(orch, "c")
        second_incident = orch.raise_incident("c", reason="rechazo #2")
        result = orch.resolve_incident(second_incident)

        assert result is None
        assert events == ["c"]
        assert orch.wi.current("c").status is TaskStatus.FAILED
