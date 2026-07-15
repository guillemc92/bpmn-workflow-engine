"""Ejemplo ejecutable de punta a punta: `python examples/quickstart.py`.

Modela un flujo de aprobacion tipico:

    intake -> review_legal   \\
           -> review_technical -> approve (AND join) -> archive
                                     ^              |
                                     |__ BACKWARD ___|  (rework si rechaza)

Demuestra, en un solo flujo:
  - split paralelo (review_legal / review_technical corren concurrentes,
    via ThreadPoolExecutor real).
  - join AND (approve espera a que ambas revisiones completen).
  - asignacion de workers skill-based -> least-loaded.
  - un incidente en 'approve' (rechazo tecnico) que dispara un reset
    BACKWARD hacia 'review_technical' (rework, iteracion 2).
  - los hooks del Orchestrator (onReady/onAssign/onStart/onComplete/
    onIncident/onReset), impresos como log en vivo.
"""

from __future__ import annotations

import time

from bpmn_engine.domain import (
    GateType,
    IncidentType,
    LogicGate,
    ResetScope,
    Role,
    Task,
    Transition,
    TransitionType,
    Worker,
    Workflow,
)
from bpmn_engine.execution import ThreadPoolExecutor
from bpmn_engine.orchestration import Orchestrator
from bpmn_engine.persistence import InMemoryRepository
from bpmn_engine.runtime import WorkflowInstance


def build_workflow() -> Workflow:
    tasks = {
        "intake": Task(id="intake", name="Recepcion de solicitud"),
        "review_legal": Task(id="review_legal", name="Revision legal", required_role=Role.ANALYST),
        "review_technical": Task(id="review_technical", name="Revision tecnica", required_role=Role.ANALYST),
        "approve": Task(
            id="approve",
            name="Aprobacion final",
            required_role=Role.SUPERVISOR,
            logic_gate=LogicGate(gate_type=GateType.AND),
        ),
        "archive": Task(id="archive", name="Archivar"),
    }
    transitions = [
        Transition("intake", "review_legal"),
        Transition("intake", "review_technical"),
        Transition("review_legal", "approve"),
        Transition("review_technical", "approve"),
        Transition("approve", "archive"),
        Transition(
            source_task_id="approve",
            target_task_id="review_technical",
            transition_type=TransitionType.BACKWARD,
            max_retries=2,
            exhausted_status="FAILED",
        ),
    ]
    return Workflow(id="wf-aprobacion", name="Aprobacion de solicitud", tasks=tasks, transitions=transitions)


def build_orchestrator(workflow: Workflow) -> Orchestrator:
    workers: InMemoryRepository[Worker] = InMemoryRepository()
    workers.save(Worker(id="ana", name="Ana", skills=(Role.ANALYST,)))
    workers.save(Worker(id="luis", name="Luis", skills=(Role.ANALYST,)))
    workers.save(Worker(id="carla", name="Carla", skills=(Role.SUPERVISOR,)))

    wi = WorkflowInstance(id="inst-1", workflow=workflow)
    orch = Orchestrator(workflow_instance=wi, workers=workers, executor=ThreadPoolExecutor(max_workers=2))

    orch.on("onReady", lambda **kw: print(f"  [onReady]    {kw['task_id']}"))
    orch.on("onAssign", lambda **kw: print(f"  [onAssign]   {kw['task_id']} -> {kw['worker_ids']}"))
    orch.on("onStart", lambda **kw: print(f"  [onStart]    {kw['task_id']}"))
    orch.on("onComplete", lambda **kw: print(f"  [onComplete] {kw['task_id']} (nuevas listas: {kw['newly_ready']})"))
    orch.on("onIncident", lambda **kw: print(f"  [onIncident] {kw['task_id']}: {kw['incident'].reason!r}"))
    orch.on("onReset", lambda **kw: print(f"  [onReset]    reset -> {kw['reset_targets']}"))
    return orch


def run_and_wait(orch: Orchestrator, task_id: str, work_seconds: float = 0.05) -> None:
    """Asigna, arranca y ejecuta `task_id` de forma sincrona (espera el resultado)."""
    orch.assign_next()
    future = orch.run_task(task_id, lambda: time.sleep(work_seconds) or f"{task_id}: ok")
    future.result(timeout=2.0)
    # run_task completa la tarea desde el callback del Future (puede correr
    # en otro hilo con ThreadPoolExecutor); esperamos a que se refleje.
    deadline = time.time() + 2.0
    while not orch.wi.current(task_id).status.name == "COMPLETED" and time.time() < deadline:
        time.sleep(0.005)


def main() -> None:
    workflow = build_workflow()
    orch = build_orchestrator(workflow)

    print("=== 1) Arranque del workflow ===")
    orch.start_workflow()

    print("\n=== 2) 'intake' (lineal) ===")
    run_and_wait(orch, "intake")

    print("\n=== 3) split paralelo: 'review_legal' + 'review_technical' (concurrentes, ThreadPoolExecutor) ===")
    orch.assign_next()  # review_legal
    orch.assign_next()  # review_technical
    future_legal = orch.run_task("review_legal", lambda: time.sleep(0.05) or "legal: ok")
    future_technical = orch.run_task("review_technical", lambda: time.sleep(0.08) or "technical: ok (v1, con un defecto)")
    future_legal.result(timeout=2.0)
    future_technical.result(timeout=2.0)
    deadline = time.time() + 2.0
    while (
        orch.wi.current("review_legal").status.name != "COMPLETED"
        or orch.wi.current("review_technical").status.name != "COMPLETED"
    ) and time.time() < deadline:
        time.sleep(0.005)
    print(f"  review_legal      -> {orch.wi.current('review_legal').status.name}")
    print(f"  review_technical  -> {orch.wi.current('review_technical').status.name}")

    print("\n=== 4) 'approve' (join AND) arranca solo porque AMBAS ramas completaron ===")
    orch.assign_next()
    orch.start_task("approve")

    print("\n=== 5) el supervisor rechaza por un defecto tecnico -> incidente + reset BACKWARD ===")
    incident = orch.raise_incident(
        "approve",
        reason="defecto tecnico detectado en la revision (v1)",
        incident_type=IncidentType.TECHNICAL,
        reset_scope=ResetScope.ALL_DOWNSTREAM,
    )
    orch.resolve_incident(incident)
    print(f"  review_technical ahora en iteracion {orch.wi.current('review_technical').iteration}")

    print("\n=== 6) se rehace 'review_technical' (iteracion 2, ya corregida) ===")
    run_and_wait(orch, "review_technical", work_seconds=0.05)

    print("\n=== 7) 'approve' se vuelve a disparar solo (AND ya satisfecho de nuevo) ===")
    run_and_wait(orch, "approve")

    print("\n=== 8) 'archive' (final) ===")
    run_and_wait(orch, "archive")

    print("\n=== Resultado final ===")
    print(f"  Estado de la WorkflowInstance: {orch.wi.status.name}")
    print(f"  Iteraciones de review_technical: {[ti.iteration for ti in orch.wi.history('review_technical')]}")
    print(f"  Reintentos usados (approve -> review_technical): {orch.wi.retry_count('approve', 'review_technical')}")
    print(f"  Entradas en la traza append-only: {len(orch.wi.trace)}")


if __name__ == "__main__":
    main()
