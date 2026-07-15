"""Tests unitarios del modelo de dominio (T2): validaciones de construccion del grafo."""

from __future__ import annotations

import pytest

from bpmn_engine.domain import (
    CompletionPolicy,
    GateType,
    LogicGate,
    ResourceSpec,
    ResourceType,
    Role,
    Task,
    TaskType,
    Transition,
    TransitionType,
    Worker,
    Workflow,
    WorkflowValidationError,
)


def make_task(task_id: str, **kwargs) -> Task:
    return Task(id=task_id, name=task_id.title(), **kwargs)


class TestTask:
    def test_task_requires_id(self):
        with pytest.raises(WorkflowValidationError):
            Task(id="", name="Sin id")

    def test_task_sla_must_be_positive(self):
        with pytest.raises(WorkflowValidationError):
            make_task("a", sla_seconds=0)

    def test_task_defaults(self):
        t = make_task("a")
        assert t.task_type is TaskType.MANUAL
        assert t.completion_policy is CompletionPolicy.ALL
        assert t.logic_gate is None


class TestResourceSpec:
    def test_quantity_must_be_at_least_one(self):
        with pytest.raises(WorkflowValidationError):
            ResourceSpec(resource_type=ResourceType.HUMAN, quantity=0)

    def test_valid_resource_spec(self):
        spec = ResourceSpec(resource_type=ResourceType.HUMAN, quantity=2, skills=(Role.ANALYST,))
        assert spec.quantity == 2
        assert Role.ANALYST in spec.skills


class TestLogicGate:
    def test_script_gate_requires_evaluator(self):
        with pytest.raises(WorkflowValidationError):
            LogicGate(gate_type=GateType.SCRIPT)

    def test_rest_gate_requires_evaluator(self):
        with pytest.raises(WorkflowValidationError):
            LogicGate(gate_type=GateType.REST)

    def test_and_gate_does_not_require_evaluator(self):
        gate = LogicGate(gate_type=GateType.AND)
        assert gate.evaluator is None


class TestTransition:
    def test_no_self_loop(self):
        with pytest.raises(WorkflowValidationError):
            Transition(source_task_id="a", target_task_id="a")

    def test_backward_requires_max_retries(self):
        with pytest.raises(WorkflowValidationError):
            Transition(
                source_task_id="b",
                target_task_id="a",
                transition_type=TransitionType.BACKWARD,
                exhausted_status="FAILED",
            )

    def test_backward_requires_exhausted_status(self):
        with pytest.raises(WorkflowValidationError):
            Transition(
                source_task_id="b",
                target_task_id="a",
                transition_type=TransitionType.BACKWARD,
                max_retries=3,
            )

    def test_valid_backward_transition(self):
        t = Transition(
            source_task_id="b",
            target_task_id="a",
            transition_type=TransitionType.BACKWARD,
            max_retries=3,
            exhausted_status="FAILED",
        )
        assert t.max_retries == 3


class TestWorker:
    def test_has_skill_true_when_role_none(self):
        w = Worker(id="w1", name="Worker 1", skills=(Role.ANALYST,))
        assert w.has_skill(None) is True

    def test_has_skill_checks_membership(self):
        w = Worker(id="w1", name="Worker 1", skills=(Role.ANALYST,))
        assert w.has_skill(Role.ANALYST) is True
        assert w.has_skill(Role.SUPERVISOR) is False


class TestWorkflowConstruction:
    def test_linear_workflow_is_valid(self):
        tasks = {t: make_task(t) for t in ("a", "b", "c")}
        transitions = [
            Transition(source_task_id="a", target_task_id="b"),
            Transition(source_task_id="b", target_task_id="c"),
        ]
        wf = Workflow(id="wf1", name="Lineal", tasks=tasks, transitions=transitions)
        assert wf.start_task_id == "a"
        assert wf.dependency_matrix().end_task_ids() == ["c"]

    def test_requires_exactly_one_start_task(self):
        tasks = {t: make_task(t) for t in ("a", "b", "c")}
        transitions = [Transition(source_task_id="a", target_task_id="c")]
        # 'b' queda sin predecesores tambien -> 2 starts (a, b)
        with pytest.raises(WorkflowValidationError, match="tarea de inicio"):
            Workflow(id="wf2", name="Dos inicios", tasks=tasks, transitions=transitions)

    def test_end_task_is_derived_as_sink_of_forward_subgraph(self):
        tasks = {t: make_task(t) for t in ("a", "b")}
        transitions = [
            Transition(source_task_id="a", target_task_id="b"),
            Transition(
                source_task_id="b",
                target_task_id="a",
                transition_type=TransitionType.BACKWARD,
                max_retries=2,
                exhausted_status="FAILED",
            ),
        ]
        wf = Workflow(id="wf3", name="Retrabajo simple", tasks=tasks, transitions=transitions)
        assert wf.dependency_matrix().end_task_ids() == ["b"]

    def test_forward_cycle_is_rejected(self):
        # 'a' es el unico nodo con in-degree 0 (start valido); el ciclo real
        # esta entre b y c, para que el chequeo de aciclicidad sea el que
        # dispare (y no el conteo de tareas de inicio).
        tasks = {t: make_task(t) for t in ("a", "b", "c")}
        transitions = [
            Transition(source_task_id="a", target_task_id="b"),
            Transition(source_task_id="b", target_task_id="c"),
            Transition(source_task_id="c", target_task_id="b"),
        ]
        with pytest.raises(WorkflowValidationError, match="ciclo"):
            Workflow(id="wf4", name="Ciclo", tasks=tasks, transitions=transitions)

    def test_backward_transition_does_not_count_as_forward_cycle(self):
        tasks = {t: make_task(t) for t in ("a", "b", "c")}
        transitions = [
            Transition(source_task_id="a", target_task_id="b"),
            Transition(source_task_id="b", target_task_id="c"),
            Transition(
                source_task_id="c",
                target_task_id="b",
                transition_type=TransitionType.BACKWARD,
                max_retries=2,
                exhausted_status="FAILED",
            ),
        ]
        wf = Workflow(id="wf5", name="Retrabajo", tasks=tasks, transitions=transitions)
        assert wf.start_task_id == "a"

    def test_join_with_multiple_incoming_requires_logic_gate(self):
        tasks = {
            "s": make_task("s"),
            "a": make_task("a"),
            "b": make_task("b"),
            "c": make_task("c"),  # join sin logic_gate -> invalido
        }
        transitions = [
            Transition(source_task_id="s", target_task_id="a"),
            Transition(source_task_id="s", target_task_id="b"),
            Transition(source_task_id="a", target_task_id="c"),
            Transition(source_task_id="b", target_task_id="c"),
        ]
        with pytest.raises(WorkflowValidationError, match="logic_gate"):
            Workflow(id="wf6", name="Join sin gate", tasks=tasks, transitions=transitions)

    def test_join_with_logic_gate_is_valid(self):
        tasks = {
            "s": make_task("s"),
            "a": make_task("a"),
            "b": make_task("b"),
            "c": make_task("c", logic_gate=LogicGate(gate_type=GateType.AND)),
        }
        transitions = [
            Transition(source_task_id="s", target_task_id="a"),
            Transition(source_task_id="s", target_task_id="b"),
            Transition(source_task_id="a", target_task_id="c"),
            Transition(source_task_id="b", target_task_id="c"),
        ]
        wf = Workflow(id="wf7", name="Join AND", tasks=tasks, transitions=transitions)
        assert wf.start_task_id == "s"
        assert set(wf.dependency_matrix().successors("s")) == {"a", "b"}

    def test_unknown_target_transition_reference_is_rejected(self):
        tasks = {"a": make_task("a")}
        transitions = [Transition(source_task_id="a", target_task_id="ghost")]
        with pytest.raises(WorkflowValidationError, match="ghost"):
            Workflow(id="wf8", name="Referencia invalida", tasks=tasks, transitions=transitions)

    def test_unknown_source_transition_reference_is_rejected(self):
        tasks = {"a": make_task("a")}
        transitions = [Transition(source_task_id="ghost", target_task_id="a")]
        with pytest.raises(WorkflowValidationError, match="ghost"):
            Workflow(id="wf8b", name="Referencia invalida origen", tasks=tasks, transitions=transitions)

    def test_incoming_and_outgoing_helpers(self):
        tasks = {t: make_task(t) for t in ("a", "b", "c")}
        transitions = [
            Transition(source_task_id="a", target_task_id="b"),
            Transition(source_task_id="b", target_task_id="c"),
        ]
        wf = Workflow(id="wf9", name="Helpers", tasks=tasks, transitions=transitions)
        assert [t.target_task_id for t in wf.outgoing("a")] == ["b"]
        assert [t.source_task_id for t in wf.incoming("c")] == ["b"]


class TestDependencyMatrix:
    def test_parallel_split_and_join(self):
        tasks = {
            "a": make_task("a"),
            "b": make_task("b"),
            "c": make_task("c"),
            "d": make_task("d", logic_gate=LogicGate(gate_type=GateType.AND)),
        }
        transitions = [
            Transition(source_task_id="a", target_task_id="b"),
            Transition(source_task_id="a", target_task_id="c"),
            Transition(source_task_id="b", target_task_id="d"),
            Transition(source_task_id="c", target_task_id="d"),
        ]
        wf = Workflow(id="wf10", name="Split/Join", tasks=tasks, transitions=transitions)
        matrix = wf.dependency_matrix()
        assert set(matrix.successors("a")) == {"b", "c"}
        assert set(matrix.predecessors("d")) == {"b", "c"}
