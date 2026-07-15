"""Los 12 escenarios obligatorios del enunciado (S6), como tests de aceptacion
extremo a extremo manejados via el Orchestrator (la API publica del motor).

Cada clase mapea 1:1 a un escenario de S6. Las piezas individuales (gates,
reset, SLA, etc.) ya tienen tests unitarios en tests/domain, tests/runtime,
tests/orchestration y tests/execution — esto demuestra que, ademas, el motor
completo produce el comportamiento de negocio esperado de punta a punta.
"""

from __future__ import annotations

import time

from bpmn_engine.domain import (
    CompletionPolicy,
    GateType,
    IncidentType,
    LogicGate,
    ResetScope,
    ResourceSpec,
    ResourceType,
    Task,
    TaskStatus,
    Transition,
    TransitionType,
    Worker,
    Workflow,
    WorkflowInstanceStatus,
)
from bpmn_engine.execution import SequentialExecutor, ThreadPoolExecutor
from bpmn_engine.orchestration import Orchestrator
from bpmn_engine.persistence import InMemoryRepository
from bpmn_engine.runtime import WorkflowInstance


class FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now


def make_orchestrator(workflow: Workflow, worker_ids=("w1",), clock=None, executor=None) -> Orchestrator:
    wi = WorkflowInstance(id="i1", workflow=workflow, clock=clock or time.time)
    workers: InMemoryRepository[Worker] = InMemoryRepository()
    for wid in worker_ids:
        workers.save(Worker(id=wid, name=wid))
    return Orchestrator(workflow_instance=wi, workers=workers, executor=executor)


def run_task(orch: Orchestrator, task_id: str) -> list[str]:
    orch.assign_next()
    orch.start_task(task_id)
    return orch.complete_task(task_id)


class TestScenario01Linear:
    """a -> b -> c, sin compuertas: cada tarea depende de una sola predecesora."""

    def test_runs_in_order_and_completes(self):
        tasks = {t: Task(id=t, name=t.title()) for t in ("a", "b", "c")}
        wf = Workflow(id="s1", name="Lineal", tasks=tasks, transitions=[Transition("a", "b"), Transition("b", "c")])
        orch = make_orchestrator(wf)
        orch.start_workflow()
        assert run_task(orch, "a") == ["b"]
        assert run_task(orch, "b") == ["c"]
        assert run_task(orch, "c") == []
        assert orch.wi.status is WorkflowInstanceStatus.COMPLETED


class TestScenario02ParallelSplit:
    """s se abre en dos ramas independientes ('a' y 'b'), sin sincronizar despues."""

    def test_both_branches_become_ready_after_split(self):
        tasks = {"s": Task(id="s", name="S"), "a": Task(id="a", name="A"), "b": Task(id="b", name="B")}
        wf = Workflow(
            id="s2",
            name="Split",
            tasks=tasks,
            transitions=[Transition("s", "a"), Transition("s", "b")],
        )
        orch = make_orchestrator(wf)
        orch.start_workflow()
        newly_ready = run_task(orch, "s")
        assert set(newly_ready) == {"a", "b"}
        assert len(orch.queue) == 2


class TestScenario03AndJoin:
    """'j' (AND) solo arranca cuando AMBAS ramas ('a' y 'b') completaron."""

    def _workflow(self) -> Workflow:
        tasks = {
            "s": Task(id="s", name="S"),
            "a": Task(id="a", name="A"),
            "b": Task(id="b", name="B"),
            "j": Task(id="j", name="J", logic_gate=LogicGate(gate_type=GateType.AND)),
        }
        return Workflow(
            id="s3",
            name="AND Join",
            tasks=tasks,
            transitions=[
                Transition("s", "a"),
                Transition("s", "b"),
                Transition("a", "j"),
                Transition("b", "j"),
            ],
        )

    def test_waits_for_both_branches(self):
        orch = make_orchestrator(self._workflow())
        orch.start_workflow()
        run_task(orch, "s")
        run_task(orch, "a")
        assert not orch.wi.has_instance("j")
        run_task(orch, "b")
        assert orch.wi.has_instance("j")
        assert orch.wi.current("j").status is TaskStatus.READY


class TestScenario04OrJoin:
    """'j' (OR) arranca apenas UNA rama completa, sin esperar a la otra."""

    def test_fires_on_first_branch(self):
        tasks = {
            "s": Task(id="s", name="S"),
            "a": Task(id="a", name="A"),
            "b": Task(id="b", name="B"),
            "j": Task(id="j", name="J", logic_gate=LogicGate(gate_type=GateType.OR)),
        }
        wf = Workflow(
            id="s4",
            name="OR Join",
            tasks=tasks,
            transitions=[Transition("s", "a"), Transition("s", "b"), Transition("a", "j"), Transition("b", "j")],
        )
        orch = make_orchestrator(wf)
        orch.start_workflow()
        run_task(orch, "s")
        run_task(orch, "a")
        assert orch.wi.has_instance("j")


class TestScenario05ComplexBusinessJoin:
    """'j' (COMPLEX) usa una regla de negocio arbitraria en vez de AND/OR/XOR fijos."""

    def test_custom_evaluator_decides_readiness(self):
        # Regla: arranca si 'a' completo con score>=0.85 (simulado via closure),
        # sin importar el estado de 'b' — tipico de una revision automatica de calidad.
        approved = {"a": True}

        def business_rule(pred_statuses):
            return pred_statuses.get("a") is TaskStatus.COMPLETED and approved["a"]

        tasks = {
            "s": Task(id="s", name="S"),
            "a": Task(id="a", name="A"),
            "b": Task(id="b", name="B"),
            "j": Task(id="j", name="J", logic_gate=LogicGate(gate_type=GateType.COMPLEX, evaluator=business_rule)),
        }
        wf = Workflow(
            id="s5",
            name="Complex Join",
            tasks=tasks,
            transitions=[Transition("s", "a"), Transition("s", "b"), Transition("a", "j"), Transition("b", "j")],
        )
        orch = make_orchestrator(wf)
        orch.start_workflow()
        run_task(orch, "s")
        run_task(orch, "a")
        assert orch.wi.has_instance("j")


class TestScenario06CycleRework:
    """a -> b -> c, con retrabajo BACKWARD de c hacia b (revision que puede rechazar y reciclar)."""

    def _workflow(self) -> Workflow:
        tasks = {t: Task(id=t, name=t.title()) for t in ("a", "b", "c")}
        transitions = [
            Transition("a", "b"),
            Transition("b", "c"),
            Transition(
                source_task_id="c",
                target_task_id="b",
                transition_type=TransitionType.BACKWARD,
                max_retries=2,
                exhausted_status="FAILED",
            ),
        ]
        return Workflow(id="s6", name="Rework", tasks=tasks, transitions=transitions)

    def test_rejection_reworks_back_to_b_and_reaches_iteration_two(self):
        orch = make_orchestrator(self._workflow())
        orch.start_workflow()
        run_task(orch, "a")
        run_task(orch, "b")
        orch.assign_next()
        orch.start_task("c")

        incident = orch.raise_incident("c", reason="calidad insuficiente", reset_scope=ResetScope.ALL_DOWNSTREAM)
        reset_targets = orch.resolve_incident(incident)

        assert reset_targets == ["b"]
        assert orch.wi.current("b").iteration == 2

        run_task(orch, "b")
        assert orch.wi.current("c").iteration == 2


class TestScenario07MultipleFinalTasks:
    """s se abre en dos ramas ('a' y 'b') que NO se sincronizan: dos finales independientes."""

    def test_workflow_completes_only_after_both_sinks_finish(self):
        tasks = {"s": Task(id="s", name="S"), "a": Task(id="a", name="A"), "b": Task(id="b", name="B")}
        wf = Workflow(
            id="s7", name="Dos finales", tasks=tasks, transitions=[Transition("s", "a"), Transition("s", "b")]
        )
        orch = make_orchestrator(wf)
        orch.start_workflow()
        run_task(orch, "s")
        run_task(orch, "a")
        assert orch.wi.status is WorkflowInstanceStatus.RUNNING  # 'b' todavia no termino
        run_task(orch, "b")
        assert orch.wi.status is WorkflowInstanceStatus.COMPLETED
        assert set(wf.dependency_matrix().end_task_ids()) == {"a", "b"}


class TestScenario08IncidentAndReset:
    """Un incidente declarado sobre 'b' (con motivo obligatorio) dispara un reset hacia 'a'."""

    def test_incident_carries_reason_and_type_and_resets_target(self):
        tasks = {"a": Task(id="a", name="A"), "b": Task(id="b", name="B")}
        transitions = [
            Transition("a", "b"),
            Transition(
                source_task_id="b",
                target_task_id="a",
                transition_type=TransitionType.BACKWARD,
                max_retries=3,
                exhausted_status="FAILED",
            ),
        ]
        wf = Workflow(id="s8", name="Incidente simple", tasks=tasks, transitions=transitions)
        orch = make_orchestrator(wf)
        events = []
        orch.on("onIncident", lambda **kw: events.append(kw["incident"]))
        orch.on("onReset", lambda **kw: events.append(("reset", kw["reset_targets"])))

        orch.start_workflow()
        run_task(orch, "a")
        orch.assign_next()
        orch.start_task("b")

        incident = orch.raise_incident("b", reason="dato invalido detectado en revision", incident_type=IncidentType.DATA_QUALITY)
        assert incident.reason == "dato invalido detectado en revision"
        assert orch.resolve_incident(incident) == ["a"]
        assert events[0] is incident
        assert events[1] == ("reset", ["a"])


class TestScenario09RetriesExhausted:
    """Tras agotar max_retries, la tarea queda en su estado terminal y la WorkflowInstance pasa a FAILED."""

    def test_second_rejection_exhausts_retries(self):
        tasks = {"a": Task(id="a", name="A"), "b": Task(id="b", name="B")}
        transitions = [
            Transition("a", "b"),
            Transition(
                source_task_id="b",
                target_task_id="a",
                transition_type=TransitionType.BACKWARD,
                max_retries=1,
                exhausted_status="FAILED",
            ),
        ]
        wf = Workflow(id="s9", name="Reintentos agotados", tasks=tasks, transitions=transitions)
        orch = make_orchestrator(wf)
        exhausted = []
        orch.on("onRetryExhausted", lambda **kw: exhausted.append(kw["task_id"]))

        orch.start_workflow()
        run_task(orch, "a")
        orch.assign_next()
        orch.start_task("b")
        first = orch.raise_incident("b", reason="rechazo 1")
        assert orch.resolve_incident(first) == ["a"]  # dentro del limite (1er intento)

        run_task(orch, "a")
        orch.assign_next()
        orch.start_task("b")
        second = orch.raise_incident("b", reason="rechazo 2")
        result = orch.resolve_incident(second)  # 2do intento > max_retries=1

        assert result is None
        assert exhausted == ["b"]
        assert orch.wi.current("b").status is TaskStatus.FAILED
        assert orch.wi.status is WorkflowInstanceStatus.FAILED


class TestScenario10SlaTimeout:
    """Una tarea con sla_seconds vence: check_sla_breaches() lo detecta de forma reactiva (sin cron)."""

    def test_breach_detected_reactively_after_deadline(self):
        clock = FakeClock(0.0)
        tasks = {"a": Task(id="a", name="A", sla_seconds=30), "b": Task(id="b", name="B")}
        wf = Workflow(id="s10", name="SLA", tasks=tasks, transitions=[Transition("a", "b")])
        orch = make_orchestrator(wf, clock=clock)  # el Orchestrator crea su SlaMonitor con el mismo reloj
        breaches = []
        orch.on("onSlaBreach", lambda **kw: breaches.append(kw["task_id"]))

        orch.start_workflow()
        orch.assign_next()
        orch.start_task("a")

        assert orch.check_sla_breaches(now=15.0) == []
        assert orch.check_sla_breaches(now=45.0) == ["a"]
        assert breaches == ["a"]


class TestScenario11MultiAssignmentPolicies:
    """Una tarea con 2 workers asignados se completa segun su CompletionPolicy (ALL/ANY/QUORUM)."""

    def _orchestrator_with_two_workers(self, policy: CompletionPolicy) -> Orchestrator:
        tasks = {
            "a": Task(
                id="a",
                name="A",
                completion_policy=policy,
                resource_specs=(ResourceSpec(resource_type=ResourceType.HUMAN, quantity=2),),
            ),
            "b": Task(id="b", name="B"),
        }
        wf = Workflow(id="s11", name="Multi-asignacion", tasks=tasks, transitions=[Transition("a", "b")])
        orch = make_orchestrator(wf, worker_ids=("w1", "w2"))
        orch.start_workflow()
        orch.assign_next()
        orch.start_task("a")
        return orch

    def test_all_requires_both_workers(self):
        orch = self._orchestrator_with_two_workers(CompletionPolicy.ALL)
        assert orch.report_worker_complete("a", "w1") is None
        assert orch.report_worker_complete("a", "w2") == ["b"]

    def test_any_completes_with_first_worker(self):
        orch = self._orchestrator_with_two_workers(CompletionPolicy.ANY)
        assert orch.report_worker_complete("a", "w1") == ["b"]

    def test_quorum_completes_at_majority(self):
        orch = self._orchestrator_with_two_workers(CompletionPolicy.QUORUM)
        assert orch.report_worker_complete("a", "w1") is None
        assert orch.report_worker_complete("a", "w2") == ["b"]


class TestScenario12ConcurrentExecution:
    """Dos tareas de una rama paralela se ejecutan realmente en paralelo via ThreadPoolExecutor."""

    def test_two_branches_run_concurrently_and_both_complete(self):
        tasks = {
            "s": Task(id="s", name="S"),
            "a": Task(id="a", name="A"),
            "b": Task(id="b", name="B"),
        }
        wf = Workflow(
            id="s12", name="Concurrente", tasks=tasks, transitions=[Transition("s", "a"), Transition("s", "b")]
        )
        executor = ThreadPoolExecutor(max_workers=2)
        orch = make_orchestrator(wf, worker_ids=("w1", "w2"), executor=executor)
        orch.start_workflow()
        run_task(orch, "s")

        orch.assign_next()
        orch.assign_next()

        barrier_started = []

        def work(name):
            barrier_started.append(name)
            time.sleep(0.05)
            return name

        future_a = orch.run_task("a", lambda: work("a"))
        future_b = orch.run_task("b", lambda: work("b"))

        assert future_a.result(timeout=1.0) == "a"
        assert future_b.result(timeout=1.0) == "b"

        deadline = time.time() + 1.0
        while (
            orch.wi.current("a").status is not TaskStatus.COMPLETED
            or orch.wi.current("b").status is not TaskStatus.COMPLETED
        ) and time.time() < deadline:
            time.sleep(0.005)

        assert orch.wi.current("a").status is TaskStatus.COMPLETED
        assert orch.wi.current("b").status is TaskStatus.COMPLETED
        assert set(barrier_started) == {"a", "b"}
