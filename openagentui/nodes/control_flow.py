"""``if-else`` and ``while`` node executors.

Routing (which outgoing edge the engine follows next) is not decided here —
``engine.py`` reads ``result.output["branch"]`` after execution and matches
it against each outgoing edge's ``sourceHandle``. This module only decides
*which branch value* applies, using the constrained evaluator in
``safe_eval.py`` (never a bare ``eval()``).
"""

from __future__ import annotations

from openagentui.nodes.base import NodeContext, failed, ok
from openagentui.safe_eval import UnsafeExpressionError, evaluate_condition
from openagentui.schema import NodeExecutionResult

_DEFAULT_MAX_ITERATIONS = 100
_LOOP_COUNTER_PREFIX = "__loop_count__"


def execute_if_else(ctx: NodeContext) -> NodeExecutionResult:
    condition = ctx.data.get("condition") or ""
    try:
        branch = (
            "true"
            if evaluate_condition(condition, ctx.execution.variables)
            else "false"
        )
    except UnsafeExpressionError as exc:
        return failed(ctx.node.id, f"invalid condition: {exc}", input_value=condition)
    except (TypeError, ValueError, ZeroDivisionError) as exc:
        return failed(
            ctx.node.id, f"condition evaluation error: {exc}", input_value=condition
        )
    return ok(
        ctx.node.id, {"branch": branch, "condition": condition}, input_value=condition
    )


def execute_while(ctx: NodeContext) -> NodeExecutionResult:
    condition = ctx.data.get("condition") or ""
    max_iterations = int(ctx.data.get("maxIterations") or _DEFAULT_MAX_ITERATIONS)
    counter_key = f"{_LOOP_COUNTER_PREFIX}{ctx.node.id}"
    count = int(ctx.execution.variables.get(counter_key, 0))

    if count >= max_iterations:
        ctx.execution.variables.pop(counter_key, None)
        return ok(
            ctx.node.id,
            {"branch": "exit", "condition": condition, "iterations": count},
            input_value=condition,
        )

    try:
        should_continue = evaluate_condition(condition, ctx.execution.variables)
    except UnsafeExpressionError as exc:
        return failed(ctx.node.id, f"invalid condition: {exc}", input_value=condition)
    except (TypeError, ValueError, ZeroDivisionError) as exc:
        return failed(
            ctx.node.id, f"condition evaluation error: {exc}", input_value=condition
        )

    if not should_continue:
        ctx.execution.variables.pop(counter_key, None)
        return ok(
            ctx.node.id,
            {"branch": "exit", "condition": condition, "iterations": count},
            input_value=condition,
        )

    ctx.execution.variables[counter_key] = count + 1
    return ok(
        ctx.node.id,
        {"branch": "loop", "condition": condition, "iterations": count + 1},
        input_value=condition,
    )
