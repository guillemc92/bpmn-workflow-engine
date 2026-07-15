"""Tests de integracion Orchestrator + SLA/concurrencia (T8)."""

from __future__ import annotations

import threading
import time

from bpmn_engine.domain import Task, TaskStatus, Transition, Worker, Workflow
from bpmn_engine.execution import SequentialExecutor, SlaMonitor, ThreadPoolExecutor
from bpmn_engine.orchestration import Orchestrator
from bpmn_engine.persistence import InMemoryRepository
from bpmn_engine.runtime import WorkflowInstance


class FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now


def linear_workflow(sla_seconds=None) -> Workflow:
    tasks = {
        "a": Task(id="a", name="A", sla_seconds=sla_seconds),
        "b": Task(id="b", name="B"),
    }
    return Workflow(id="wf", name="Lineal", tasks=tasks, transitions=[Transition("a", "b")])


def make_orchestrator(workflow: Workflow, clock=None, executor=None) -> Orchestrator:
    clock = clock or time.time
    wi = WorkflowInstance(id="i1", workflow=workflow, clock=clock)
    workers: InMemoryRepository[Worker] = InMemoryRepository()
    workers.save(Worker(id="w1", name="W1"))
    sla = SlaMonitor(clock=clock)
    return Orchestrator(workflow_instance=wi, workers=workers, executor=executor, sla_monitor=sla)


class TestSlaBreach:
    def test_no_breach_reported_before_deadline(self):
        clock = FakeClock(0.0)
        orch = make_orchestrator(linear_workflow(sla_seconds=100), clock=clock)
        orch.start_workflow()
        orch.assign_next()
        orch.start_task("a")
        assert orch.check_sla_breaches(now=50.0) == []

    def test_breach_emits_hook_after_deadline(self):
        events = []
        clock = FakeClock(0.0)
        orch = make_orchestrator(linear_workflow(sla_seconds=10), clock=clock)
        orch.on("onSlaBreach", lambda **kw: events.append(kw["task_id"]))
        orch.start_workflow()
        orch.assign_next()
        orch.start_task("a")

        assert orch.check_sla_breaches(now=11.0) == ["a"]
        assert events == ["a"]

    def test_completing_before_deadline_prevents_breach(self):
        clock = FakeClock(0.0)
        orch = make_orchestrator(linear_workflow(sla_seconds=10), clock=clock)
        orch.start_workflow()
        orch.assign_next()
        orch.start_task("a")
        orch.complete_task("a")
        assert orch.check_sla_breaches(now=999.0) == []

    def test_tasks_without_sla_are_never_tracked(self):
        clock = FakeClock(0.0)
        orch = make_orchestrator(linear_workflow(sla_seconds=None), clock=clock)
        orch.start_workflow()
        orch.assign_next()
        orch.start_task("a")
        assert orch.sla.is_tracked("a") is False


class TestConcurrentExecution:
    def test_run_task_with_sequential_executor_autocompletes(self):
        orch = make_orchestrator(linear_workflow(), executor=SequentialExecutor())
        orch.start_workflow()
        orch.assign_next()

        future = orch.run_task("a", lambda: 42)

        assert future.result() == 42
        assert orch.wi.current("a").status is TaskStatus.COMPLETED
        assert len(orch.queue) == 1  # 'b' quedo lista

    def test_run_task_failure_raises_incident_automatically(self):
        orch = make_orchestrator(linear_workflow(), executor=SequentialExecutor())
        orch.start_workflow()
        orch.assign_next()

        orch.run_task("a", lambda: (_ for _ in ()).throw(RuntimeError("fallo de procesamiento")))

        assert orch.wi.current("a").status is TaskStatus.FAILED
        assert orch.wi.incidents[-1].reason == "fallo de procesamiento"

    def test_run_task_with_real_thread_pool_completes_concurrently(self):
        orch = make_orchestrator(linear_workflow(), executor=ThreadPoolExecutor(max_workers=2))
        orch.start_workflow()
        orch.assign_next()

        started = threading.Event()

        def slow_work():
            started.set()
            time.sleep(0.05)
            return "done"

        future = orch.run_task("a", slow_work)
        assert started.wait(timeout=1.0)
        assert future.result(timeout=1.0) == "done"

        # complete_task se dispara desde el callback del Future, en el hilo del pool
        deadline = time.time() + 1.0
        while orch.wi.current("a").status is not TaskStatus.COMPLETED and time.time() < deadline:
            time.sleep(0.005)
        assert orch.wi.current("a").status is TaskStatus.COMPLETED
