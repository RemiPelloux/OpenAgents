"""Trivial node executors: start, end, note, set-state."""

from __future__ import annotations

from openagentui.nodes.base import NodeContext, ok
from openagentui.schema import NodeExecutionResult


def execute_start(ctx: NodeContext) -> NodeExecutionResult:
    """Seed workflow variables from the ``inputVariables`` declared on the node.

    Run-time inputs (passed by the caller — CLI ``key=value`` args, or the
    REST/MCP trigger payload) are merged into ``execution.variables`` before
    the graph walk starts, so this just records defaults for any variable
    the caller didn't supply and echoes the resulting input set.
    """
    for spec in ctx.data.get("inputVariables") or []:
        name = spec.get("name")
        if not name or name in ctx.execution.variables:
            continue
        if "defaultValue" in spec and spec["defaultValue"] not in (None, ""):
            ctx.set_variable(name, spec["defaultValue"])
        elif spec.get("required"):
            return NodeExecutionResult(
                node_id=ctx.node.id,
                status="failed",
                error=f"missing required input variable: {name}",
            )
    return ok(ctx.node.id, dict(ctx.execution.variables))


def execute_end(ctx: NodeContext) -> NodeExecutionResult:
    from openagentui.templating import render_dict

    output_mapping = ctx.data.get("outputMapping")
    if isinstance(output_mapping, dict) and output_mapping:
        output = render_dict(
            output_mapping,
            variables=ctx.execution.variables,
            node_results=ctx.execution.node_results,
        )
    else:
        output = dict(ctx.execution.variables)
    return ok(ctx.node.id, output)


def execute_note(ctx: NodeContext) -> NodeExecutionResult:
    """Notes are UI-only annotations; no-op at execution time."""
    return ok(ctx.node.id, None)


def execute_set_state(ctx: NodeContext) -> NodeExecutionResult:
    key = ctx.data.get("stateKey")
    if not key:
        return NodeExecutionResult(
            node_id=ctx.node.id,
            status="failed",
            error="set-state node missing stateKey",
        )
    value = ctx.rendered("stateValue")
    ctx.set_variable(key, value)
    return ok(ctx.node.id, {key: value})
