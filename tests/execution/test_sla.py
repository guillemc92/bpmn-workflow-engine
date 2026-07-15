"""Tests unitarios de SlaMonitor (T8): deadlines reactivos via min-heap, sin cron."""

from __future__ import annotations

from bpmn_engine.execution import SlaMonitor


class FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now


class TestSlaMonitor:
    def test_no_breach_before_deadline(self):
        clock = FakeClock(0.0)
        sla = SlaMonitor(clock=clock)
        sla.track("a", sla_seconds=10, started_at=0.0)
        assert sla.check_breaches(now=5.0) == []

    def test_breach_reported_once_deadline_passed(self):
        clock = FakeClock(0.0)
        sla = SlaMonitor(clock=clock)
        sla.track("a", sla_seconds=10, started_at=0.0)
        assert sla.check_breaches(now=11.0) == ["a"]

    def test_breach_is_not_reported_twice(self):
        sla = SlaMonitor()
        sla.track("a", sla_seconds=10, started_at=0.0)
        assert sla.check_breaches(now=20.0) == ["a"]
        assert sla.check_breaches(now=30.0) == []

    def test_untrack_prevents_future_breach_report(self):
        sla = SlaMonitor()
        sla.track("a", sla_seconds=10, started_at=0.0)
        sla.untrack("a")
        assert sla.check_breaches(now=20.0) == []

    def test_multiple_tasks_reported_in_deadline_order(self):
        sla = SlaMonitor()
        sla.track("late", sla_seconds=20, started_at=0.0)
        sla.track("early", sla_seconds=5, started_at=0.0)
        assert sla.check_breaches(now=100.0) == ["early", "late"]

    def test_retrack_replaces_previous_deadline(self):
        sla = SlaMonitor()
        sla.track("a", sla_seconds=5, started_at=0.0)  # deadline=5
        sla.track("a", sla_seconds=50, started_at=0.0)  # deadline=50, reemplaza
        assert sla.check_breaches(now=10.0) == []  # la entrada vieja (deadline=5) es obsoleta
        assert sla.check_breaches(now=60.0) == ["a"]

    def test_is_tracked(self):
        sla = SlaMonitor()
        assert sla.is_tracked("a") is False
        sla.track("a", sla_seconds=10, started_at=0.0)
        assert sla.is_tracked("a") is True

    def test_uses_default_clock_when_started_at_omitted(self):
        clock = FakeClock(100.0)
        sla = SlaMonitor(clock=clock)
        deadline = sla.track("a", sla_seconds=10)
        assert deadline == 110.0
