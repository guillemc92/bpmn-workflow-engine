"""Persistencia: Repository Protocol + InMemoryRepository."""

from bpmn_engine.persistence.repository import (
    HasId,
    InMemoryRepository,
    Repository,
    RepositoryError,
)

__all__ = [
    "HasId",
    "InMemoryRepository",
    "Repository",
    "RepositoryError",
]
