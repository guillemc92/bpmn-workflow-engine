"""Tests unitarios de SequentialExecutor (T8): mismo protocolo que concurrent.futures.Executor."""

from __future__ import annotations

import pytest

from bpmn_engine.execution import SequentialExecutor


class TestSequentialExecutor:
    def test_submit_returns_resolved_future_with_result(self):
        executor = SequentialExecutor()
        future = executor.submit(lambda x, y: x + y, 2, 3)
        assert future.done() is True
        assert future.result() == 5

    def test_submit_captures_exception_in_future(self):
        executor = SequentialExecutor()

        def boom():
            raise ValueError("fallo simulado")

        future = executor.submit(boom)
        assert future.done() is True
        with pytest.raises(ValueError, match="fallo simulado"):
            future.result()

    def test_submit_supports_kwargs(self):
        executor = SequentialExecutor()
        future = executor.submit(lambda a, b=1: a + b, 10, b=5)
        assert future.result() == 15
