# PR: Runtime / instance (T3)

**Commit:** `cbacbbb` | **Archivos:** `src/bpmn_engine/runtime/models.py`, `tests/runtime/test_workflow_instance.py`

## Que se construyo

- `TaskInstance`, `TraceEntry`, `ResourceInstance`, `Incident` (dataclasses).
- `WorkflowInstance`: unico punto de mutacion de una ejecucion. Implementa el
  ciclo de vida del enunciado (S4.2): `enqueue / assign_workers / start /
  assign_resources / complete / navigate_to_targets / raise_incident`
  (version basica; el algoritmo completo de reset se agrega en T7).
- Maquina de estados de `TaskStatus` (S4.3), validada contra una tabla
  `_ALLOWED_TRANSITIONS` — cualquier transicion no listada lanza
  `TaskInstanceError` en vez de mutar silenciosamente un estado invalido.
- Traza append-only (`WorkflowInstance.trace`): cada cambio de estado se
  registra con secuencia, iteracion, estado origen/destino y timestamp.
  Nunca se edita ni se borra — analogo a una tabla de auditoria solo-INSERT.

## Decisiones de diseño

- **`navigate_to_targets` fuerza la propagacion automatica.** Al completar
  una tarea, el runtime mismo crea y encola (`PENDING -> READY`) las tareas
  sucesoras cuyo gate ya se satisface — el llamador no tiene que "acordarse"
  de encolar manualmente el siguiente paso.
- **Reloj inyectable (`clock: Callable[[], float]`).** Permite tests
  deterministas con un `FakeClock` en vez de depender de `time.time()` real.
- **Evaluacion de gates delegada, no inline.** La primera version tenia la
  logica de AND/OR/XOR escrita directo en `WorkflowInstance`; se extrajo a
  `domain/gates.py` en T4 (ver `PR_logic_gates.md`) sin tocar el resto del
  runtime, gracias a que quedo aislada en un solo punto de llamada.

## Evidencia

38 tests. Cubre el ciclo de vida feliz completo, validacion de transiciones
invalidas, joins AND/OR/XOR/COMPLEX via `navigate_to_targets`, y el camino
basico de incidentes (marca `FAILED`, exige `reason` no vacio, registra el
`Incident`).
