"""``workflow`` node — run another saved workflow as a sub-graph."""

from __future__ import annotations

from openagentui import store
from openagentui.nodes.base import NodeContext, failed, ok
from openagentui.schema import NodeExecutionResult


def execute(ctx: NodeContext) -> NodeExecutionResult:
    sub_id = str(ctx.data.get("subWorkflowId") or "").strip()
    if not sub_id:
        return failed(ctx.node.id, "sub-workflow node missing subWorkflowId")

    sub = store.get_workflow(sub_id)
    if sub is None:
        return failed(ctx.node.id, f"unknown sub-workflow: {sub_id}")

    inputs = ctx.data.get("inputs") or {}
    if isinstance(inputs, dict):
        from openagentui.templating import render_dict

        inputs = render_dict(
            inputs,
            variables=ctx.execution.variables,
            node_results=ctx.execution.node_results,
        )
    else:
        inputs = {}

    try:
        from openagentui import engine

        child = engine.run_workflow(
            sub, inputs=inputs if isinstance(inputs, dict) else {}
        )
    except Exception as exc:
        return failed(ctx.node.id, f"sub-workflow run failed: {exc}")

    if child.status != "completed":
        return failed(
            ctx.node.id,
            child.error or f"sub-workflow ended with status {child.status}",
            input_value={"subWorkflowId": sub_id},
        )

    end_output = None
    for result in child.node_results.values():
        if result.status == "completed" and isinstance(result.output, dict):
            end_output = result.output
    output_field = ctx.data.get("outputField")
    if output_field and end_output is not None:
        ctx.set_variable(str(output_field), end_output)
    return ok(ctx.node.id, {"subExecutionId": child.id, "output": end_output})
