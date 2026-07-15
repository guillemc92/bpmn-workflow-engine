"""Tests de integracion del Orchestrator (T6): ReadyQueue + WorkflowInstance + asignacion de workers."""

from __future__ import annotations

import pytest

from bpmn_engine.domain import (
    CompletionPolicy,
    IncidentType,
    ResourceSpec,
    ResourceType,
    Role,
    Task,
    TaskStatus,
    Transition,
    Worker,
    Workflow,
)
from bpmn_engine.orchestration import Orchestrator, OrchestratorError
from bpmn_engine.persistence import InMemoryRepository
from bpmn_engine.runtime import WorkflowInstance


def linear_workflow(required_role=None, completion_policy=CompletionPolicy.ALL, resource_specs=()) -> Workflow:
    tasks = {
        "a": Task(id="a", name="A", required_role=required_role, completion_policy=completion_policy, resource_specs=resource_specs),
        "b": Task(id="b", name="B"),
    }
    return Workflow(id="wf", name="Lineal", tasks=tasks, transitions=[Transition("a", "b")])


def make_orchestrator(workflow: Workflow, workers: list[Worker]) -> Orchestrator:
    wi = WorkflowInstance(id="i1", workflow=workflow)
    repo: InMemoryRepository[Worker] = InMemoryRepository()
    for w in workers:
        repo.save(w)
    return Orchestrator(workflow_instance=wi, workers=repo)


class TestEndToEndLinearFlow:
    def test_drives_linear_workflow_to_completion(self):
        orch = make_orchestrator(linear_workflow(), [Worker(id="w1", name="W1")])
        orch.start_workflow()
        assert len(orch.queue) == 1

        task_id = orch.assign_next()
        assert task_id == "a"
        assert orch.wi.current("a").status is TaskStatus.ASSIGNED
        assert orch.workers.get("w1").current_load == 1

        orch.start_task("a")
        assert orch.wi.current("a").status is TaskStatus.IN_PROGRESS

        newly_ready = orch.complete_task("a")
        assert newly_ready == ["b"]
        assert orch.workers.get("w1").current_load == 0  # liberado
        assert len(orch.queue) == 1

        task_id_2 = orch.assign_next()
        assert task_id_2 == "b"
        orch.start_task("b")
        orch.complete_task("b")
        assert orch.wi.status.name == "COMPLETED"

    def test_assign_next_on_empty_queue_returns_none(self):
        orch = make_orchestrator(linear_workflow(), [])
        assert orch.assign_next() is None


class TestSkillBasedAssignment:
    def test_requeues_when_no_qualified_worker(self):
        orch = make_orchestrator(linear_workflow(required_role=Role.ANALYST), [Worker(id="w1", name="W1")])
        orch.start_workflow()
        result = orch.assign_next()
        assert result is None
        assert len(orch.queue) == 1  # reencolada, no perdida

    def test_assigns_when_qualified_worker_appears(self):
        orch = make_orchestrator(
            linear_workflow(required_role=Role.ANALYST),
            [Worker(id="w1", name="W1", skills=(Role.ANALYST,))],
        )
        orch.start_workflow()
        assert orch.assign_next() == "a"


class TestMultiWorkerCompletionPolicy:
    def _two_worker_orchestrator(self, policy: CompletionPolicy) -> Orchestrator:
        wf = linear_workflow(
            completion_policy=policy,
            resource_specs=(ResourceSpec(resource_type=ResourceType.HUMAN, quantity=2),),
        )
        orch = make_orchestrator(wf, [Worker(id="w1", name="W1"), Worker(id="w2", name="W2")])
        orch.start_workflow()
        orch.assign_next()
        orch.start_task("a")
        return orch

    def test_all_policy_requires_every_worker_to_report(self):
        orch = self._two_worker_orchestrator(CompletionPolicy.ALL)
        assert orch.report_worker_complete("a", "w1") is None
        assert orch.wi.current("a").status is TaskStatus.IN_PROGRESS
        result = orch.report_worker_complete("a", "w2")
        assert result == ["b"]

    def test_any_policy_completes_on_first_report(self):
        orch = self._two_worker_orchestrator(CompletionPolicy.ANY)
        result = orch.report_worker_complete("a", "w1")
        assert result == ["b"]

    def test_quorum_policy_completes_at_majority(self):
        orch = self._two_worker_orchestrator(CompletionPolicy.QUORUM)
        # quorum de 2 workers = (2//2)+1 = 2 -> equivalente a ALL en este caso
        assert orch.report_worker_complete("a", "w1") is None
        assert orch.report_worker_complete("a", "w2") == ["b"]

    def test_report_from_unassigned_worker_raises(self):
        orch = self._two_worker_orchestrator(CompletionPolicy.ALL)
        with pytest.raises(OrchestratorError):
            orch.report_worker_complete("a", "ghost")


class TestHooks:
    def test_hooks_fire_in_order_with_expected_payload(self):
        events = []
        orch = make_orchestrator(linear_workflow(), [Worker(id="w1", name="W1")])
        orch.on("onReady", lambda **kw: events.append(("onReady", kw["task_id"])))
        orch.on("onAssign", lambda **kw: events.append(("onAssign", kw["task_id"], kw["worker_ids"])))
        orch.on("onStart", lambda **kw: events.append(("onStart", kw["task_id"])))
        orch.on("onComplete", lambda **kw: events.append(("onComplete", kw["task_id"], kw["newly_ready"])))

        orch.start_workflow()
        orch.assign_next()
        orch.start_task("a")
        orch.complete_task("a")

        assert events == [
            ("onReady", "a"),
            ("onAssign", "a", ["w1"]),
            ("onStart", "a"),
            ("onComplete", "a", ["b"]),
            ("onReady", "b"),
        ]

    def test_incident_hook_fires_with_incident_object(self):
        events = []
        orch = make_orchestrator(linear_workflow(), [Worker(id="w1", name="W1")])
        orch.on("onIncident", lambda **kw: events.append(kw["incident"]))
        orch.start_workflow()
        orch.assign_next()
        orch.start_task("a")
        incident = orch.raise_incident("a", reason="dato invalido", incident_type=IncidentType.DATA_QUALITY)
        assert events == [incident]
        assert orch.wi.current("a").status is TaskStatus.FAILED
