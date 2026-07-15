# PRD — Motor de Workflow tipo BPMN 2.0

**Curso:** Fundamentos de Programacion y Frameworks Modernos para IA
**Repositorio:** `bpmn-workflow-engine` (independiente de cualquier otro proyecto del cursante)

## 1. Problema

El enunciado pide disenar e implementar, desde cero, un motor de ejecucion de
workflows inspirado en BPMN 2.0: procesos modelados como grafos dirigidos,
con compuertas logicas, orquestacion reactiva (sin cron), manejo de
incidentes/retrabajo, SLAs y asignacion/ejecucion concurrente de tareas — y
comparar el resultado contra motores reales de la industria (Camunda,
Activiti).

## 2. Objetivo

Construir un motor funcional, testeado y documentado que demuestre:

1. Modelado de procesos como grafo dirigido (`Task` = nodo, `Transition` = arista).
2. Compuertas logicas embebidas en la tarea destino (AND/OR/XOR/COMPLEX/SCRIPT/REST/LAMBDA).
3. Orquestacion reactiva via cola + patron Observer (sin polling ni cron).
4. Incidentes con motivo obligatorio y retrabajo (ciclos BACKWARD) con control de reintentos.
5. SLAs con deteccion reactiva de incumplimiento (min-heap de deadlines).
6. Asignacion de workers skill-based -> least-loaded, con politicas de completion (ALL/ANY/QUORUM).
7. Ejecucion concurrente real de tareas independientes.

## 3. Alcance (in scope)

- Motor completo en Python 3.11+, sin frameworks externos de orquestacion.
- Persistencia en memoria, abstraida detras de un patron Repository.
- Los 12 escenarios obligatorios de la seccion S6 del enunciado, cubiertos con tests automatizados.
- Documentacion tecnica (este PRD, el FSD, mapeo de prompts y un documento por PR/feature).

## 4. Fuera de alcance (explicito)

- Persistencia real (SQLite/Postgres) — el `Repository` esta abstraido para permitirlo despues, pero no se implementa en esta entrega.
- Compuertas como nodos de primera clase del grafo (variante "100% al estandar BPMN") — se documenta como extension futura en el FSD.
- Integraciones REST/Lambda reales — se mockean explicitamente via fixtures inyectadas.
- UI / API HTTP — el motor se consume como libreria Python (`import bpmn_engine`).

## 5. Usuarios / consumidores

- El propio motor, consumido programaticamente (tests, notebooks, scripts) — no hay usuario final humano en esta entrega.
- El equipo docente, como evaluador de la arquitectura y el codigo.

## 6. Criterios de aceptacion

- Los 12 escenarios de S6 pasan como tests automatizados (`tests/scenarios/test_mandatory_scenarios.py`).
- Cobertura de tests >= 95% sobre `src/bpmn_engine` (verificado con `pytest --cov`).
- Cada fase del motor (dominio, runtime, gates, persistencia, orquestacion, incidentes, SLA/concurrencia) es su propio commit verificable, con tests en verde antes de avanzar a la siguiente.
- FSD incluye una comparacion explicita contra Camunda/Activiti (ver `docs/FSD.md` S7).

## 7. Riesgos y decisiones que los mitigan

| Riesgo | Mitigacion |
|---|---|
| Reinventar `Future`/`Executor` con bugs sutiles de concurrencia | Se reusa `concurrent.futures` de la stdlib (Python ya lo resolvio); solo se agrega `SequentialExecutor` propio para modo determinista. |
| Ciclos de retrabajo (BACKWARD) rompiendo la deteccion de ciclos del grafo principal | El grafo FORWARD debe ser un DAG (validado al construir la `Workflow`); BACKWARD se excluye deliberadamente de esa validacion. |
| SLA implementado con `cron`/polling, violando la restriccion explicita del enunciado | `SlaMonitor` es un min-heap de deadlines, consultado solo en los puntos donde el motor ya reacciona a un evento (`check_sla_breaches()`), nunca desde un hilo/temporizador propio. |
| Perder trazabilidad de reintentos/incidentes | Traza append-only (`WorkflowInstance.trace`) + `iteration` por `TaskInstance` + conteo de reintentos por transicion BACKWARD (`retry_count`). |
