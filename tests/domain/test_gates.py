"""Tests unitarios del dispatcher de compuertas (T4): GateEvaluator.can_start por GateType."""

from __future__ import annotations

import pytest

from bpmn_engine.domain import (
    GateEvaluator,
    GateType,
    LogicGate,
    Task,
    TaskStatus,
    mock_lambda_invoke,
    mock_rest_call,
)


def make_task(gate: LogicGate | None) -> Task:
    return Task(id="j", name="Join", logic_gate=gate)


class TestNoGate:
    def test_no_gate_behaves_as_and(self):
        task = make_task(None)
        preds = {"a": TaskStatus.COMPLETED, "b": TaskStatus.READY}
        assert GateEvaluator.can_start(task, preds) is False

    def test_no_gate_single_predecessor_completed(self):
        task = make_task(None)
        assert GateEvaluator.can_start(task, {"a": TaskStatus.COMPLETED}) is True


class TestAndOrXor:
    @pytest.mark.parametrize(
        "statuses,expected",
        [
            ({"a": TaskStatus.COMPLETED, "b": TaskStatus.COMPLETED}, True),
            ({"a": TaskStatus.COMPLETED, "b": TaskStatus.READY}, False),
            ({"a": TaskStatus.READY, "b": TaskStatus.READY}, False),
        ],
    )
    def test_and_requires_all_completed(self, statuses, expected):
        task = make_task(LogicGate(gate_type=GateType.AND))
        assert GateEvaluator.can_start(task, statuses) is expected

    @pytest.mark.parametrize(
        "statuses,expected",
        [
            ({"a": TaskStatus.COMPLETED, "b": TaskStatus.READY}, True),
            ({"a": TaskStatus.COMPLETED, "b": TaskStatus.COMPLETED}, True),
            ({"a": TaskStatus.READY, "b": TaskStatus.READY}, False),
        ],
    )
    def test_or_requires_at_least_one_completed(self, statuses, expected):
        task = make_task(LogicGate(gate_type=GateType.OR))
        assert GateEvaluator.can_start(task, statuses) is expected

    @pytest.mark.parametrize(
        "statuses,expected",
        [
            ({"a": TaskStatus.COMPLETED, "b": TaskStatus.READY}, True),
            ({"a": TaskStatus.COMPLETED, "b": TaskStatus.COMPLETED}, False),
            ({"a": TaskStatus.READY, "b": TaskStatus.READY}, False),
        ],
    )
    def test_xor_requires_exactly_one_completed(self, statuses, expected):
        task = make_task(LogicGate(gate_type=GateType.XOR))
        assert GateEvaluator.can_start(task, statuses) is expected


class TestComplexAndScript:
    def test_complex_gate_uses_custom_business_evaluator(self):
        def business_rule(preds: dict[str, TaskStatus]) -> bool:
            # regla arbitraria: solo arranca si 'a' completo, sin importar 'b'
            return preds.get("a") is TaskStatus.COMPLETED

        task = make_task(LogicGate(gate_type=GateType.COMPLEX, evaluator=business_rule))
        assert GateEvaluator.can_start(task, {"a": TaskStatus.COMPLETED, "b": TaskStatus.READY}) is True
        assert GateEvaluator.can_start(task, {"a": TaskStatus.READY, "b": TaskStatus.COMPLETED}) is False

    def test_script_gate_uses_evaluator(self):
        task = make_task(LogicGate(gate_type=GateType.SCRIPT, evaluator=lambda preds: True))
        assert GateEvaluator.can_start(task, {}) is True


class TestRestAndLambdaMocks:
    def test_rest_gate_resolves_via_fixtures(self):
        fixtures = {"/api/quality-check": True}
        gate = LogicGate(
            gate_type=GateType.REST,
            evaluator=lambda preds: mock_rest_call("/api/quality-check", fixtures),
        )
        task = make_task(gate)
        assert GateEvaluator.can_start(task, {}) is True

    def test_rest_mock_raises_on_missing_fixture(self):
        with pytest.raises(KeyError):
            mock_rest_call("/unknown", {})

    def test_lambda_gate_resolves_via_fixtures(self):
        fixtures = {"validate-karyotype": False}
        gate = LogicGate(
            gate_type=GateType.LAMBDA,
            evaluator=lambda preds: mock_lambda_invoke("validate-karyotype", fixtures),
        )
        task = make_task(gate)
        assert GateEvaluator.can_start(task, {}) is False

    def test_lambda_mock_raises_on_missing_fixture(self):
        with pytest.raises(KeyError):
            mock_lambda_invoke("unknown-fn", {})
