# PR: Orquestacion — ReadyQueue + Orchestrator (T6)

**Commit:** `3181fd5` | **Archivos:** `src/bpmn_engine/orchestration/{queue,assignment,orchestrator}.py`, `tests/orchestration/{test_queue,test_assignment,test_orchestrator}.py`

## Que se construyo

- `ReadyQueue`: FIFO tipo SQS (`push`/`pop`) que notifica **sincronicamente**
  a sus observadores en cada `push` (patron Observer) — nada de `sched`,
  `cron` ni polling.
- `select_workers(workers, required_role, count)`: funcion **pura**,
  independiente del `Orchestrator`, que implementa skill-based -> least-loaded
  (S5.2 del enunciado), con desempate deterministico por `worker.id`.
- `Orchestrator`: se suscribe a la `ReadyQueue` y coordina
  `WorkflowInstance` + repositorio de `Worker`. Expone el ciclo de vida
  completo (`start_workflow / assign_next / start_task / report_worker_complete
  / complete_task / raise_incident`) mas un sistema de hooks (`on(event, callback)`).
- Politicas de completion multi-worker (`ALL/ANY/QUORUM`) en
  `report_worker_complete`: cuenta reportes individuales y solo llama
  `complete_task` al alcanzar el umbral de la `CompletionPolicy` de la tarea.

## Decisiones de diseño

- **`select_workers` como funcion pura, no un metodo del Orchestrator**
  (decision D5) — permite testearla en aislamiento total, sin construir un
  `WorkflowInstance` completo.
- **Backpressure reactivo, no bloqueante.** Si `assign_next()` no encuentra
  workers calificados suficientes, reencola la tarea y retorna `None` — el
  caller decide cuando reintentar (tipicamente, cuando otro worker se libera).
- **`ReadyQueue.push`/`pop` en vez de reusar los nombres `enqueue`/`dequeue`
  de `WorkflowInstance`.** Evita confundir "encolar en la cola de trabajo del
  orquestador" con "transicionar `PENDING -> READY` en la maquina de estados"
  — son dos conceptos relacionados pero distintos.

## Evidencia

31 tests. Incluye flujo feliz de punta a punta (start -> assign -> start_task
-> complete, encadenado), reencolado por falta de skill, y las 3 politicas de
completion con 2 workers.
