# PR: Logic gates (T4)

**Commit:** `73c0bd8` | **Archivos:** `src/bpmn_engine/domain/gates.py`, `tests/domain/test_gates.py`

## Que se construyo

- `GateEvaluator.can_start(task, pred_statuses) -> bool`: dispatcher por
  `GateType` (Strategy pattern), extraido de la logica que originalmente
  vivia inline en `WorkflowInstance` (T3).
- `mock_rest_call(endpoint, fixtures)` / `mock_lambda_invoke(fn_name, fixtures)`:
  mocks explicitos para los gates `REST`/`LAMBDA` — resuelven contra un dict
  de fixtures en vez de red real, dejando visible el punto exacto donde se
  conectaria una integracion real.

## Decisiones de diseño

- **AND/OR/XOR tienen implementacion fija; COMPLEX/SCRIPT/REST/LAMBDA
  delegan siempre en `LogicGate.evaluator`.** `LogicGate.__post_init__`
  (T2) ya garantiza que `evaluator` existe para esos tres tipos, asi que
  `GateEvaluator` no necesita re-validar nada — solo confia en la
  invariante que el dominio ya impuso.
- **Extraccion sin romper T3.** `WorkflowInstance._gate_satisfied` se borro
  y `navigate_to_targets` ahora llama `GateEvaluator.can_start` directamente
  — una linea de cambio en el runtime, sin tocar su maquina de estados ni
  sus tests existentes (los 38 tests de T3 siguieron pasando sin
  modificacion).

## Evidencia

17 tests nuevos, 100% cobertura en `domain/gates.py`. Incluye los 7
`GateType` (sin gate, AND, OR, XOR, COMPLEX con regla de negocio custom,
SCRIPT, REST y LAMBDA con fixtures, incluyendo el caso de fixture faltante).
