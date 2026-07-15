# FSD — Functional/Technical Spec: Motor de Workflow tipo BPMN 2.0

Complementa `docs/PRD.md`. Describe **como** se construyo el motor: modulos,
algoritmos, maquinas de estado y la comparacion con motores reales pedida
por el enunciado (S7).

## 1. Arquitectura de modulos

```
src/bpmn_engine/
  domain/          # DEFINICION (plantilla): Workflow, Task, LogicGate, Transition, Worker, GateEvaluator
  runtime/          # INSTANCIA (ejecucion): WorkflowInstance, TaskInstance, TraceEntry, Incident
  orchestration/    # ReadyQueue (Observer) + Orchestrator + asignacion skill-based -> least-loaded
  execution/        # Executor/Future (SequentialExecutor, ThreadPoolExecutor) + SlaMonitor
  persistence/       # Repository (Protocol) + InMemoryRepository
```

Separacion deliberada **definicion vs instancia** (igual que Camunda/Activiti):
una `Workflow` se define una vez; se ejecuta N veces como N `WorkflowInstance`
independientes, cada una con su propia traza, sus propios incidentes y su
propio conteo de reintentos.

Dependencias entre modulos (una sola direccion, sin ciclos de import):

```
persistence  <-  domain  <-  runtime  <-  orchestration  <-  execution
                                                (execution tambien se apoya
                                                 en orchestration.Orchestrator)
```

## 2. Modelo de dominio (definicion)

- **`Task`**: nodo del grafo. Tiene `task_type`, `logic_gate` (opcional),
  `resource_specs`, `required_role`, `completion_policy`, `sla_seconds`.
- **`Transition`**: arista dirigida `source_task_id -> target_task_id`, con
  `transition_type` (`FORWARD`/`BACKWARD`). Las `BACKWARD` exigen
  `max_retries` y `exhausted_status` (control de reintentos obligatorio).
- **`LogicGate`**: Strategy embebido en la tarea destino (no es un nodo
  propio del grafo). `gate_type` determina si se evalua con la logica fija
  (AND/OR/XOR) o con un `evaluator` custom (COMPLEX/SCRIPT/REST/LAMBDA).
- **`Workflow`**: agrega `tasks` + `transitions`. Al construirse valida:
  exactamente 1 tarea de inicio, el subgrafo FORWARD es aciclico (los
  ciclos solo se permiten via BACKWARD), y toda tarea con >1 entrada FORWARD
  tiene un `logic_gate` (regla de join).
- **`DependencyMatrix`**: vista derivada de adyacencia (solo aristas FORWARD)
  usada para calcular predecesores/sucesores y validar aciclicidad (DFS de 3
  colores).
- **`Worker`**: recurso asignable, con `skills` y `current_load`.

## 3. Runtime (instancia)

### 3.1 Maquina de estados de `TaskInstance`

```
PENDING --enqueue--> READY --assign_workers--> ASSIGNED --start--> IN_PROGRESS
                                                                       |--complete--> COMPLETED
                                                                       |--raise_incident--> FAILED
   \--skip--> SKIPPED                     (cualquier estado no terminal) --cancel--> CANCELLED
```

Toda transicion pasa por `WorkflowInstance._transition()`, que valida contra
una tabla `_ALLOWED_TRANSITIONS` y **registra cada cambio** en `trace`
(`TraceEntry`, append-only — nunca se edita ni se borra, analogo a una tabla
de auditoria solo-INSERT).

### 3.2 Ciclo de vida operado por `WorkflowInstance`

`enqueue -> assign_workers -> start -> assign_resources -> complete ->
navigate_to_targets -> raise_incident -> apply_reset`

`navigate_to_targets(task_id)` evalua, para cada sucesor FORWARD, si ya
puede arrancar (todas sus entradas tienen instancia y `GateEvaluator.can_start`
retorna `True`) y en ese caso crea una nueva `TaskInstance` (PENDING) y la
encola (READY).

### 3.3 Compuertas (`domain/gates.py::GateEvaluator`)

| GateType | Regla |
|---|---|
| (sin gate) | Equivalente a AND con 1 sola entrada |
| AND | Todas las predecesoras `COMPLETED` |
| OR | Al menos una predecesora `COMPLETED` |
| XOR | Exactamente una predecesora `COMPLETED` |
| COMPLEX | Delega en `evaluator(pred_statuses)` — logica de negocio arbitraria |
| SCRIPT | Delega en `evaluator` (script custom) |
| REST | Delega en `evaluator`, que tipicamente llama `mock_rest_call(endpoint, fixtures)` |
| LAMBDA | Delega en `evaluator`, que tipicamente llama `mock_lambda_invoke(fn_name, fixtures)` |

REST/LAMBDA son **mocks explicitos**: en vez de red real, resuelven contra
un diccionario de fixtures inyectado por quien arma el `LogicGate` — el
enunciado permite esto explicitamente ("basta demostrar el concepto").

### 3.4 Incidentes y reset (S4.6)

`raise_incident(task_id, reason, incident_type, reset_scope)`:
1. Exige `reason` no vacio (trazabilidad obligatoria).
2. Marca la `TaskInstance` actual como `FAILED` (queda como historial permanente).
3. Registra un `Incident` (`id`, `task_id`, `incident_type`, `reason`, `reset_scope`, `iteration`).

`apply_reset(incident)`:
1. Busca la `Transition` BACKWARD que sale de la tarea del incidente.
2. Incrementa el contador de reintentos para `(source, target)`.
3. Si `intentos > max_retries`: la `WorkflowInstance` pasa a `FAILED` y se
   retorna `None` (reintentos agotados -> ERROR, escenario obligatorio #9).
4. Si no: segun `reset_scope`,
   - `SPECIFIC`: solo se libera la tarea objetivo.
   - `ALL_DOWNSTREAM`: se cancela (si esta en curso) y se libera **todo** lo
     alcanzable hacia adelante desde el objetivo (incluida la tarea que
     origino el incidente) para que el flujo normal las vuelva a disparar.
5. Crea una nueva `TaskInstance` de la tarea objetivo con `iteration + 1` y
   la encola — el historial de iteraciones previas **no se borra** (se puede
   recorrer con `WorkflowInstance.history(task_id)`).

El "liberar" en el punto 4 usa un set interno `_live_target_ids`: mientras
una tarea tiene una instancia viva, `navigate_to_targets` no la vuelve a
disparar (evita doble-disparo en joins); el reset la remueve de ese set sin
tocar su historial en `_instances`.

## 4. Orquestacion

### 4.1 `ReadyQueue` (Observer, tipo SQS)

FIFO (`push`/`pop`) que notifica **sincronicamente** a sus observadores en
cada `push` — sin `sched`/`cron`/polling. El `Orchestrator` se suscribe y
emite el hook `onReady`.

### 4.2 `Orchestrator`

Coordina `WorkflowInstance` + `ReadyQueue` + el repositorio de `Worker`:

- `start_workflow()` — encola la tarea de inicio.
- `assign_next()` — extrae una tarea de la cola, selecciona workers
  (`select_workers`: filtro por skill -> desempate por `current_load`
  minimo, S5.2 del enunciado) y los asigna; si no hay suficientes, reencola
  (backpressure reactivo).
- `start_task()` / `complete_task()` — envuelven el ciclo de vida y liberan
  la carga de los workers al completar.
- `report_worker_complete(task_id, worker_id)` — cuenta reportes
  individuales y dispara `complete_task` cuando se alcanza el umbral de la
  `CompletionPolicy` de la tarea (`ALL`=todos, `ANY`=1, `QUORUM`=mayoria).
- `raise_incident()` / `resolve_incident()` — envuelven el algoritmo de
  incidentes/reset y emiten `onIncident`/`onReset`/`onRetryExhausted`.
- `run_task(task_id, fn)` — ejecuta `fn` en `self.executor`; al resolverse
  el `Future`, completa la tarea automaticamente o levanta un incidente con
  la excepcion como motivo.
- `check_sla_breaches(now)` — consulta `SlaMonitor` y emite `onSlaBreach`.

Hooks disponibles: `onReady, onAssign, onStart, onComplete, onIncident,
onReset, onRetryExhausted, onSlaBreach` — todos via `Orchestrator.on(event, callback)`.

## 5. SLA (reactivo, sin cron)

`execution/sla.py::SlaMonitor` es un min-heap de `(deadline, task_id)` con
borrado perezoso: `track()`/`untrack()` no reorganizan el heap, solo
invalidan entradas viejas via un dict `task_id -> deadline` vigente.
`check_breaches(now)` extrae del tope del heap todo lo vencido — se llama
**solo** desde puntos donde el motor ya esta reaccionando a un evento
(nunca desde un hilo/temporizador propio).

## 6. Concurrencia

Se reusa `concurrent.futures` de la stdlib (decision D2): `ThreadPoolExecutor`
ya implementa el contrato `submit() -> Future` con `.result()/.cancel()/.done()`
que pide el enunciado. `SequentialExecutor` (propio) cumple el mismo
protocolo pero ejecuta inline y sincrono — es el backend por defecto,
util para tests deterministas. En un reset, si la tarea que fallo tenia un
`Future` en vuelo, `Orchestrator.resolve_incident()` lo cancela (con la
limitacion conocida de Python: `cancel()` no interrumpe un hilo ya corriendo,
solo evita que un trabajo aun no iniciado arranque).

## 7. Comparacion con Camunda / Activiti

| Aspecto | Este motor | Camunda 7/8 | Activiti |
|---|---|---|---|
| Modelo de proceso | Grafo dirigido en codigo (`Task`/`Transition`), sin editor visual | BPMN 2.0 XML completo + editor visual (Modeler) | BPMN 2.0 XML + editor visual |
| Compuertas como nodos | No (embebidas en la tarea destino, decision D3) | Si, gateways son elementos BPMN de primera clase | Si, idem |
| Persistencia | En memoria (`InMemoryRepository`), abstraida via `Repository` | Motor de base de datos relacional obligatorio (persiste cada paso) | Idem, sobre JPA/Hibernate |
| Orquestacion | Cola + Observer, reactivo, in-process | Command executor + jobs asincronos sobre la base de datos (polling configurable) | Similar, job executor con polling |
| Distribuido / multi-nodo | No (single-process) | Si (cluster, Camunda 8 usa Zeebe, arquitectura de streaming) | Parcial (cluster con DB compartida) |
| SLA / timers | Min-heap reactivo, in-memory | Timers BPMN nativos, persistidos y recuperables tras un restart | Timers BPMN nativos, persistidos |
| Incidentes/retrabajo | `Transition` BACKWARD explicita + `max_retries` por transicion | "Incidents" nativos (fallos tecnicos) + boundary events para reglas de negocio | Similar (fallos + boundary events) |
| Motor de reglas de compuertas | `evaluator` Python arbitrario (Strategy) | DMN (Decision Model & Notation) integrado | Reglas via scripts (Groovy/JUEL) |
| Persistencia tras un crash | No sobrevive (in-memory) | Si, cada estado se persiste transaccionalmente | Si |
| Escala de uso previsto | Motor didactico / prueba de concepto | Produccion empresarial (BPM) | Produccion empresarial (BPM) |

**Conclusion:** el motor implementado cubre el **algoritmo central** de un
BPM engine (grafo + compuertas + orquestacion reactiva + incidentes + SLA +
concurrencia) con la misma separacion conceptual definicion/instancia que
usan Camunda y Activiti, pero deliberadamente sin persistencia durable ni
distribucion — el `Repository` abstraido es exactamente el punto de
extension que permitiria agregar eso despues sin rediseñar el motor.

## 8. Extensiones futuras (fuera de alcance de esta entrega)

- Gateways como nodos de primera clase del grafo (variante "100% al estandar").
- `Repository` real sobre SQLite/Postgres.
- Persistencia de la traza para recuperacion tras un crash del proceso.
- Timers BPMN nativos (actualmente el SLA es la unica forma de "timer").
