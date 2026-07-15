# PR: Incidentes / reset / reintentos (T7, algoritmo S4.6)

**Commit:** `0a4fc6d` | **Archivos:** `src/bpmn_engine/runtime/models.py` (`apply_reset` y helpers), `src/bpmn_engine/orchestration/orchestrator.py` (`resolve_incident`), `tests/runtime/test_incidents_reset.py`, `tests/orchestration/test_orchestrator_incidents.py`

## Que se construyo

`WorkflowInstance.apply_reset(incident)`, implementando literal el algoritmo
del enunciado (S4.6):

1. Busca la `Transition` BACKWARD que sale de la tarea del incidente.
2. Controla `max_retries` por par `(source_task_id, target_task_id)`.
3. Si se agotaron los reintentos: la `WorkflowInstance` pasa a `FAILED` y se
   retorna `None` (escenario obligatorio #9, "reintentos agotados -> ERROR").
4. Si no: aplica el `reset_scope` (`SPECIFIC` vs `ALL_DOWNSTREAM`), crea una
   nueva iteracion de la tarea objetivo y la reencola.

`Orchestrator.resolve_incident(incident)` envuelve esto: cancela el `Future`
en vuelo de la tarea (si lo habia, via `run_task`), emite `onReset` o
`onRetryExhausted`, y reencola en la `ReadyQueue` lo que corresponda.

## Decisiones de diseño (y un bug real que los tests atraparon)

- **El historial nunca se reescribe.** Una `TaskInstance` que termino en
  `FAILED` sigue apareciendo en `WorkflowInstance.history(task_id)` con ese
  estado, incluso despues del reset — solo se le suma una **nueva**
  `TaskInstance` con `iteration + 1`. El reset "libera" la tarea para que
  vuelva a dispararse (via un set interno `_live_target_ids`), pero no borra
  ni edita lo que ya paso.
- **`SPECIFIC` vs `ALL_DOWNSTREAM` difieren en que se libera, no en que se
  reescribe.** `SPECIFIC` solo genera una nueva iteracion del objetivo;
  `ALL_DOWNSTREAM` ademas libera (y cancela si estaban en curso) todas las
  tareas alcanzables hacia adelante desde el objetivo, para que el flujo
  normal las vuelva a disparar cuando corresponda.
- **Bug real encontrado por los tests, no por inspeccion:**
  `navigate_to_targets` (T3) siempre llamaba `_spawn(target_id, iteration=1)`,
  ignorando iteraciones previas. El primer intento de
  `test_rework_flows_forward_again_after_reset` fallo con
  `iteration == 1` en vez de `2` — se corrigio calculando
  `next_iteration = current(target_id).iteration + 1 if target_id in self._instances else 1`.
  Se documenta aca porque es exactamente el tipo de bug que una demo manual
  no hubiera detectado (el flujo "se veia bien"), pero un assert especifico si.

## Evidencia

18 tests nuevos (10 en runtime, 8 en orchestration). Cubre: reset exitoso con
ambos `reset_scope`, conteo de reintentos por transicion, agotamiento de
reintentos con transicion de la `WorkflowInstance` a `FAILED`, y el reset sin
`Transition` BACKWARD definida (error explicito, no fallo silencioso).
