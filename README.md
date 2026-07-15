# BPMN Workflow Engine

Motor de workflow inspirado en BPMN 2.0, construido como grafo dirigido: los procesos se modelan con `Task` (nodos) y dependencias/transiciones (aristas), con compuertas lógicas (`LogicGate`) embebidas en la tarea destino, orquestación reactiva vía cola + patrón Observer (sin cron), incidentes tipados con reset configurable, SLAs reactivos, multi-asignación de workers y ejecución concurrente.

**Curso:** Fundamentos de Programación y Frameworks Modernos para IA
**Stack:** Python 3.11+, `dataclasses`, `Enum`, `typing.Protocol`, `concurrent.futures`.

## Instalación

```bash
python -m venv .venv
source .venv/Scripts/activate   # Windows Git Bash / .venv/bin/activate en Unix
pip install -e ".[test]"
```

## Correr los tests (12 escenarios obligatorios)

```bash
pytest tests/ -v --cov=src/bpmn_engine
```

## Documentación

- [`docs/PRD.md`](docs/PRD.md) — qué se construye y por qué.
- [`docs/FSD.md`](docs/FSD.md) — cómo funciona, incluye comparación con Camunda/Activiti.
- [`docs/prompt_mappings.md`](docs/prompt_mappings.md) — trazabilidad de prompts usados durante el desarrollo.
- [`PR_implementation/`](PR_implementation/) — un documento por feature implementado, con las decisiones de diseño de esa parte.
- [`APORTES.md`](APORTES.md) — contribución individual por integrante (plantilla).

## Estructura

```
src/bpmn_engine/
  domain/          # Workflow, Task, LogicGate, ResourceSpec, Transition, Worker, enums (definición/plantilla)
  runtime/          # WorkflowInstance, TaskInstance, TraceEntry, Incident (instancia/ejecución)
  orchestration/    # ReadyQueue, Orchestrator (Observer), asignación de workers
  execution/        # Executor/Future (SequentialExecutor, ThreadPoolExecutor)
  persistence/       # Repository (Protocol) + InMemoryRepository
tests/scenarios/     # un archivo por escenario obligatorio (lineal, paralelo, AND/OR, ciclos, incidentes, SLA, concurrencia...)
```
