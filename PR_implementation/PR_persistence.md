# PR: Persistencia (T5)

**Commit:** `5631332` | **Archivos:** `src/bpmn_engine/persistence/repository.py`, `tests/persistence/test_repository.py`

## Que se construyo

- `Repository[T]` (`typing.Protocol`): contrato minimo (`save/get/require/list/delete/exists`)
  que el resto del motor consume — nunca una implementacion concreta directamente.
- `InMemoryRepository[T]`: unica implementacion de esta entrega, un `dict`
  keyed por `entity.id`, generico sobre cualquier entidad con atributo `id`
  (`Workflow`, `Worker`, etc., via el Protocol estructural `HasId`).

## Decisiones de diseño

- **Repository pattern desde el inicio, aunque solo haya una implementacion.**
  Decision D6 del plan: prioriza demostrar el algoritmo del motor (grafo,
  compuertas, incidentes, concurrencia) sobre infraestructura de
  persistencia, pero deja el punto de extension listo — cambiar a SQLite
  despues implica escribir un `Repository` nuevo, sin tocar
  domain/runtime/orchestration.
- **`require()` ademas de `get()`.** `get()` retorna `Optional[T]` (para
  chequeos); `require()` lanza `RepositoryError` si no existe — evita que
  cada consumidor repita el mismo `if x is None: raise`.
- **Generico via `TypeVar(bound=HasId)`, no una clase por entidad.** Un solo
  `InMemoryRepository` sirve para `Workflow`, `Worker`, o cualquier entidad
  futura con `.id`, sin duplicar codigo.

## Evidencia

12 tests, 100% cobertura. Incluye aislamiento entre instancias de repositorio
distintas, sobre-escritura por mismo id, y el camino de error de `require()`.
