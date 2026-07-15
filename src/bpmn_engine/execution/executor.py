"""Executor/Future: se reusa `concurrent.futures` de la stdlib (decision D2 del
plan) en vez de reinventar el protocolo — `ThreadPoolExecutor` ya implementa
`submit() -> Future` con `.result()/.cancel()/.done()` casi al caracter de lo
que pide el enunciado. `SequentialExecutor` es el backend determinista por
defecto (ejecuta inline, util para tests reproducibles); `ThreadPoolExecutor`
se usa tal cual para concurrencia real (escenario obligatorio #12).
"""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable, Protocol, TypeVar

T = TypeVar("T")


class Executor(Protocol):
    """Mismo contrato que `concurrent.futures.Executor` — ambos backends lo cumplen."""

    def submit(self, fn: Callable[..., T], *args: Any, **kwargs: Any) -> Future: ...


class SequentialExecutor:
    """Ejecuta `fn` de forma sincrona e inline, devolviendo un Future ya resuelto."""

    def submit(self, fn: Callable[..., T], *args: Any, **kwargs: Any) -> Future:
        future: Future = Future()
        if not future.set_running_or_notify_cancel():
            return future
        try:
            result = fn(*args, **kwargs)
        except BaseException as exc:  # noqa: BLE001 - se reporta via el Future, no se traga
            future.set_exception(exc)
        else:
            future.set_result(result)
        return future


__all__ = ["Executor", "SequentialExecutor", "Future", "ThreadPoolExecutor"]
