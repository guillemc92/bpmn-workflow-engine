# PR: SLA + concurrencia (T8)

**Commit:** `adae379` | **Archivos:** `src/bpmn_engine/execution/{sla,executor}.py`, integracion en `orchestration/orchestrator.py`, `tests/execution/*`, `tests/orchestration/test_orchestrator_sla_concurrency.py`

## Que se construyo

- `SlaMonitor`: min-heap de `(deadline, task_id)` con borrado perezoso.
  `track()`/`untrack()` no reorganizan el heap; `check_breaches(now)` extrae
  del tope todo lo vencido, descartando entradas obsoletas al vuelo.
- `Executor` (`typing.Protocol`) + `SequentialExecutor`: mismo contrato que
  `concurrent.futures.Executor` (`submit() -> Future`). `SequentialExecutor`
  ejecuta inline y sincrono, envolviendo el resultado (o la excepcion) en un
  `Future` ya resuelto — backend determinista por defecto.
- `ThreadPoolExecutor` de la stdlib, reusado tal cual (no reimplementado)
  como backend de concurrencia real.
- `Orchestrator.run_task(task_id, fn)`: inicia la tarea, la ejecuta en
  `self.executor`, y al resolverse el `Future` completa la tarea
  automaticamente (o levanta un incidente con la excepcion como motivo).
- `Orchestrator.check_sla_breaches(now)`: consulta `SlaMonitor` y emite
  `onSlaBreach` por cada tarea vencida.

## Decisiones de diseño

- **Reusar `concurrent.futures` en vez de reinventarlo (decision D2).** El
  `Protocol` que pide el enunciado (`submit()->Future` con
  `.result()/.cancel()/.done()`) es casi identico al de la stdlib — Python
  ya resolvio los problemas de concurrencia real (thread-safety de `Future`,
  callbacks, cancelacion) mejor de lo que se podria reimplementar en el
  tiempo de esta entrega.
- **SLA sin cron: consulta reactiva, no un hilo propio.** `check_sla_breaches`
  se llama explicitamente desde el codigo cliente (o desde otro punto donde
  el motor ya reacciona a un evento) — nunca hay un `threading.Timer` ni un
  loop de polling corriendo en background.
- **Lock explicito (`threading.Lock`) alrededor de `complete_task`/`raise_incident`
  cuando se llaman desde el callback de un `Future`.** Con `ThreadPoolExecutor`
  real, el callback de `add_done_callback` corre en un hilo del pool; sin el
  lock, dos tareas completandose "al mismo tiempo" podrian pisarse al mutar
  `WorkflowInstance._instances`/`trace` (estructuras sin proteccion propia).
- **Cancelacion de `Future` en reset, con su limitacion documentada.**
  `resolve_incident` cancela el `Future` de la tarea que fallo si existia;
  Python no puede interrumpir un hilo ya corriendo (`cancel()` solo evita que
  un trabajo *pendiente* arranque), limitacion inherente que se documenta en
  vez de intentar resolverla con hacks fragiles (ej. `ctypes` para matar
  threads).

## Evidencia

30 tests nuevos (11 SlaMonitor, 3 SequentialExecutor, 16 integracion via
Orchestrator). Incluye un test con `ThreadPoolExecutor` real (dos hilos, con
`time.sleep` para forzar solapamiento) verificando que ambas tareas completan
correctamente sin condiciones de carrera visibles.
