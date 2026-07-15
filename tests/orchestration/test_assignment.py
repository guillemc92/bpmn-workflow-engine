"""Tests unitarios del algoritmo de asignacion skill-based -> least-loaded (T6, D5)."""

from __future__ import annotations

import pytest

from bpmn_engine.domain import Role, Worker
from bpmn_engine.orchestration import select_workers


def worker(id_, role, load=0):
    return Worker(id=id_, name=id_, skills=(role,), current_load=load)


class TestSelectWorkers:
    def test_filters_by_required_skill(self):
        workers = [worker("w1", Role.SUPERVISOR), worker("w2", Role.ANALYST)]
        chosen = select_workers(workers, Role.ANALYST, count=1)
        assert [w.id for w in chosen] == ["w2"]

    def test_no_required_role_accepts_any_worker(self):
        workers = [worker("w1", Role.SUPERVISOR), worker("w2", Role.ANALYST)]
        chosen = select_workers(workers, None, count=1)
        assert len(chosen) == 1

    def test_picks_least_loaded_among_qualified(self):
        workers = [
            worker("w1", Role.ANALYST, load=3),
            worker("w2", Role.ANALYST, load=1),
            worker("w3", Role.ANALYST, load=2),
        ]
        chosen = select_workers(workers, Role.ANALYST, count=1)
        assert chosen[0].id == "w2"

    def test_tiebreak_is_deterministic_by_id(self):
        workers = [worker("w2", Role.ANALYST, load=1), worker("w1", Role.ANALYST, load=1)]
        chosen = select_workers(workers, Role.ANALYST, count=1)
        assert chosen[0].id == "w1"

    def test_returns_fewer_than_count_when_not_enough_qualified(self):
        workers = [worker("w1", Role.ANALYST)]
        chosen = select_workers(workers, Role.ANALYST, count=3)
        assert len(chosen) == 1

    def test_count_must_be_at_least_one(self):
        with pytest.raises(ValueError):
            select_workers([], None, count=0)

    def test_selects_multiple_least_loaded(self):
        workers = [
            worker("w1", Role.ANALYST, load=0),
            worker("w2", Role.ANALYST, load=5),
            worker("w3", Role.ANALYST, load=1),
        ]
        chosen = select_workers(workers, Role.ANALYST, count=2)
        assert {w.id for w in chosen} == {"w1", "w3"}
