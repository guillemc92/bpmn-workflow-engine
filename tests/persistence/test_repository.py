"""Tests unitarios del Repository pattern (T5): InMemoryRepository."""

from __future__ import annotations

import pytest

from bpmn_engine.domain import Role, Transition, Worker, Workflow
from bpmn_engine.persistence import InMemoryRepository, RepositoryError


def make_task(task_id: str):
    from bpmn_engine.domain import Task

    return Task(id=task_id, name=task_id.title())


def make_workflow(wf_id: str) -> Workflow:
    tasks = {"a": make_task("a"), "b": make_task("b")}
    return Workflow(id=wf_id, name="Demo", tasks=tasks, transitions=[Transition("a", "b")])


class TestInMemoryRepositoryWithWorkflow:
    def test_save_and_get(self):
        repo: InMemoryRepository[Workflow] = InMemoryRepository()
        wf = make_workflow("wf1")
        repo.save(wf)
        assert repo.get("wf1") is wf

    def test_get_missing_returns_none(self):
        repo: InMemoryRepository[Workflow] = InMemoryRepository()
        assert repo.get("missing") is None

    def test_require_missing_raises(self):
        repo: InMemoryRepository[Workflow] = InMemoryRepository()
        with pytest.raises(RepositoryError):
            repo.require("missing")

    def test_require_returns_entity(self):
        repo: InMemoryRepository[Workflow] = InMemoryRepository()
        wf = make_workflow("wf1")
        repo.save(wf)
        assert repo.require("wf1") is wf

    def test_list_returns_all_saved(self):
        repo: InMemoryRepository[Workflow] = InMemoryRepository()
        repo.save(make_workflow("wf1"))
        repo.save(make_workflow("wf2"))
        assert {wf.id for wf in repo.list()} == {"wf1", "wf2"}

    def test_save_overwrites_same_id(self):
        repo: InMemoryRepository[Workflow] = InMemoryRepository()
        wf1 = make_workflow("wf1")
        repo.save(wf1)
        wf1_updated = make_workflow("wf1")
        wf1_updated.version = 2
        repo.save(wf1_updated)
        assert len(repo) == 1
        assert repo.get("wf1").version == 2

    def test_delete_removes_entity(self):
        repo: InMemoryRepository[Workflow] = InMemoryRepository()
        repo.save(make_workflow("wf1"))
        repo.delete("wf1")
        assert repo.get("wf1") is None
        assert not repo.exists("wf1")

    def test_delete_missing_is_a_noop(self):
        repo: InMemoryRepository[Workflow] = InMemoryRepository()
        repo.delete("missing")  # no debe lanzar

    def test_exists(self):
        repo: InMemoryRepository[Workflow] = InMemoryRepository()
        assert repo.exists("wf1") is False
        repo.save(make_workflow("wf1"))
        assert repo.exists("wf1") is True

    def test_iteration_and_len(self):
        repo: InMemoryRepository[Workflow] = InMemoryRepository()
        repo.save(make_workflow("wf1"))
        repo.save(make_workflow("wf2"))
        assert len(repo) == 2
        assert {wf.id for wf in repo} == {"wf1", "wf2"}


class TestInMemoryRepositoryWithWorker:
    def test_isolated_from_other_repository_instances(self):
        workflows: InMemoryRepository[Workflow] = InMemoryRepository()
        workers: InMemoryRepository[Worker] = InMemoryRepository()
        workers.save(Worker(id="w1", name="Ana", skills=(Role.ANALYST,)))
        assert len(workflows) == 0
        assert len(workers) == 1
