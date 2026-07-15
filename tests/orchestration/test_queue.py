"""Tests unitarios de ReadyQueue (T6): FIFO tipo SQS + notificacion sincronica (Observer)."""

from __future__ import annotations

from bpmn_engine.orchestration import ReadyQueue


class TestReadyQueue:
    def test_pop_on_empty_returns_none(self):
        queue = ReadyQueue()
        assert queue.pop() is None

    def test_fifo_order(self):
        queue = ReadyQueue()
        queue.push("a")
        queue.push("b")
        assert queue.pop() == "a"
        assert queue.pop() == "b"
        assert queue.pop() is None

    def test_len_and_bool(self):
        queue = ReadyQueue()
        assert len(queue) == 0
        assert bool(queue) is False
        queue.push("a")
        assert len(queue) == 1
        assert bool(queue) is True

    def test_subscribers_are_notified_synchronously_on_push(self):
        received = []
        queue = ReadyQueue()
        queue.subscribe(received.append)
        queue.push("a")
        assert received == ["a"]

    def test_multiple_subscribers_all_notified(self):
        received_1, received_2 = [], []
        queue = ReadyQueue()
        queue.subscribe(received_1.append)
        queue.subscribe(received_2.append)
        queue.push("x")
        assert received_1 == ["x"]
        assert received_2 == ["x"]
