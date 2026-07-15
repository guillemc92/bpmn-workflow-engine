# PR: Domain model (T2)

**Commit:** `4da2c6c` | **Archivos:** `src/bpmn_engine/domain/{enums,models}.py`, `tests/domain/test_models.py`

## Que se construyo

- Enums completos (S3.7 del enunciado): `WorkflowStatus`, `TaskStatus`,
  `GateType`, `ResourceType`, `TaskType`, `Role`, `TransitionType`,
  `IncidentType`, `CompletionPolicy`, `ResetScope` (+ `WorkflowInstanceStatus`,
  agregado en T3 pero definido aqui por cohesion).
- Dataclasses del enunciado (S3.8): `ResourceSpec`, `LogicGate`, `Transition`,
  `Task`, `Workflow`.
- Dos clases que el enunciado describe pero no codifica: `Worker`
  (recurso asignable con `skills`/`current_load`) y `DependencyMatrix`
  (vista de adyacencia derivada, usada tanto para validacion como por el
  runtime).

## Decisiones de diseño

- **Validaciones al construir, no al usar.** `Workflow.__post_init__` valida
  de una vez: exactamente 1 tarea de inicio, subgrafo FORWARD aciclico
  (DFS de 3 colores en `DependencyMatrix.is_acyclic`), y toda tarea con >1
  entrada FORWARD tiene `logic_gate`. Un `Workflow` invalido nunca llega a
  existir como objeto.
- **BACKWARD excluido del chequeo de ciclos.** El grafo de avance normal debe
  ser un DAG; los ciclos de retrabajo son intencionales y se manejan aparte
  (T7). `DependencyMatrix` los excluye por construccion.
- **`Transition` BACKWARD obliga `max_retries` + `exhausted_status`.** No es
  posible crear una arista de retrabajo sin declarar como se controla el
  reintento — evita que T7 tenga que validar esto en runtime.
- **Se descarto una validacion de "al menos 1 tarea final" como redundante**:
  se demostro que un DAG finito no vacio siempre tiene >=1 nodo sumidero, por
  lo que ya queda garantizado por el chequeo de aciclicidad (ver comentario
  en `Workflow.__post_init__`).

## Evidencia

25 tests, 100% cobertura en `domain/enums.py` y `domain/models.py`. Incluye
casos negativos para cada regla de construccion (gate faltante en join,
ciclo FORWARD, transicion a un id inexistente, BACKWARD sin `max_retries`, etc.).
