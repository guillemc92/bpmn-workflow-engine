"""Repository pattern: abstrae el almacenamiento para que el motor no dependa
de una tecnologia concreta. `InMemoryRepository` es la unica implementacion
de esta entrega (decision D6 del plan) — cambiar a SQLite/Postgres despues
implica escribir un nuevo Repository, sin tocar domain/runtime/orchestration.
"""

from __future__ import annotations

from typing import Generic, Iterator, Optional, Protocol, TypeVar


class HasId(Protocol):
    id: str


T = TypeVar("T", bound=HasId)


class Repository(Protocol[T]):
    """Contrato minimo de persistencia que domain/runtime/orchestration consumen."""

    def save(self, entity: T) -> None: ...

    def get(self, entity_id: str) -> Optional[T]: ...

    def list(self) -> list[T]: ...

    def delete(self, entity_id: str) -> None: ...

    def exists(self, entity_id: str) -> bool: ...


class RepositoryError(KeyError):
    """La entidad solicitada no existe en el repositorio."""


class InMemoryRepository(Generic[T]):
    """Implementacion de referencia: un dict en memoria, keyed por `entity.id`."""

    def __init__(self) -> None:
        self._store: dict[str, T] = {}

    def save(self, entity: T) -> None:
        self._store[entity.id] = entity

    def get(self, entity_id: str) -> Optional[T]:
        return self._store.get(entity_id)

    def require(self, entity_id: str) -> T:
        entity = self._store.get(entity_id)
        if entity is None:
            raise RepositoryError(f"No existe entidad con id '{entity_id}'")
        return entity

    def list(self) -> list[T]:
        return list(self._store.values())

    def delete(self, entity_id: str) -> None:
        self._store.pop(entity_id, None)

    def exists(self, entity_id: str) -> bool:
        return entity_id in self._store

    def __len__(self) -> int:
        return len(self._store)

    def __iter__(self) -> Iterator[T]:
        return iter(self._store.values())
