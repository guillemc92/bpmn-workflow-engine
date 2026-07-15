# Mapeo de prompts -> entregables

Trazabilidad entre las instrucciones dadas al asistente de IA (Claude) y lo
que efectivamente se produjo, commit por commit.

| # | Prompt / instruccion (resumen) | Entregable producido | Commit(s) |
|---|---|---|---|
| P1 | Enunciado completo del trabajo pegado dos veces, pidiendo ayuda a implementarlo | Investigacion de contexto (confirmar que no existia proyecto previo relacionado) + plan de implementacion por fases (`sorted-seeking-thompson.md`) | — (fase de planificacion, sin commit) |
| P2 | Confirmaciones puntuales via preguntas dirigidas: ubicacion del proyecto (carpeta nueva), estrategia de persistencia (en memoria + Repository), y estrategia de `APORTES.md` (plantilla, motor construido en solitario) | 3 decisiones de diseño confirmadas, incorporadas al plan | — |
| P3 | "T1 (Bootstrap): crear la carpeta, git init, estructura base y pyproject.toml" | `README.md`, `.gitignore`, `pyproject.toml`, estructura de directorios (`src/bpmn_engine/*`, `tests/*`) | `372262e` |
| P4 | "T2 (Domain model) completo con tests unitarios propios antes de tocar runtime" | `domain/enums.py`, `domain/models.py` (Workflow, Task, LogicGate, Transition, Worker, DependencyMatrix) + `tests/domain/test_models.py` (25 tests, 100% cobertura) | `4da2c6c` |
| P5 | Continuar con T3 (runtime/instance) | `runtime/models.py` (WorkflowInstance, TaskInstance, TraceEntry, Incident) + `tests/runtime/test_workflow_instance.py` (38 tests) | `cbacbbb` |
| P6 | Continuar con T4 (logic gates) | `domain/gates.py` (`GateEvaluator`, mocks `mock_rest_call`/`mock_lambda_invoke`) + `tests/domain/test_gates.py` | `73c0bd8` |
| P7 | Continuar con T5 (persistencia) | `persistence/repository.py` (`Repository` Protocol, `InMemoryRepository`) + `tests/persistence/test_repository.py` | `5631332` |
| P8 | Continuar con T6 (orquestacion) | `orchestration/queue.py`, `orchestration/assignment.py`, `orchestration/orchestrator.py` + tests (Observer, skill-based -> least-loaded, politicas ALL/ANY/QUORUM) | `3181fd5` |
| P9 | Continuar con T7 (incidentes/reset/reintentos, algoritmo S4.6) | `WorkflowInstance.apply_reset`, `Orchestrator.resolve_incident`, hooks `onReset`/`onRetryExhausted` + tests dedicados | `0a4fc6d` |
| P10 | Continuar con T8 (SLA + concurrencia) | `execution/sla.py` (`SlaMonitor`, min-heap reactivo), `execution/executor.py` (`SequentialExecutor` + `ThreadPoolExecutor` reusado), integracion en `Orchestrator.run_task`/`check_sla_breaches` | `adae379` |
| P11 | Continuar con T9 (tests de los 12 escenarios obligatorios) | `tests/scenarios/test_mandatory_scenarios.py` — 12 clases, una por escenario de S6 | `16a0920` |
| P12 | Continuar con T10 (documentacion) | `docs/PRD.md`, `docs/FSD.md` (con comparacion Camunda/Activiti), este archivo, `PR_implementation/*.md`, `APORTES.md` | (este commit) |

## Nota metodologica

Cada fase T2-T8 se implemento y **se corrio la suite completa de tests**
(`pytest tests/ --cov=src/bpmn_engine`) antes de pasar a la siguiente, en
verde, antes de comitear — no hubo una fase de "corregir todo al final".
Dos bugs reales fueron encontrados y corregidos por los propios tests
durante el desarrollo (no por inspeccion manual):

1. `navigate_to_targets` reasignaba siempre `iteration=1` al respawnear una
   tarea, en vez de incrementar sobre la iteracion anterior — detectado por
   `test_rework_flows_forward_again_after_reset` (T7).
2. La combinacion de `run_task`/`test_helper` producia una transicion
   `READY -> READY` invalida en los tests de compuertas AND/OR/XOR porque
   `navigate_to_targets` ya encola automaticamente a las tareas con un solo
   predecesor — se ajusto el helper de test, no el motor (T3).
