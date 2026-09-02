"""``mcp`` node — deterministic single-tool call, no LLM reasoning.

Named ``mcp`` for schema compatibility with the upstream builder (whose only
deterministic-tool node type was MCP-backed), but in OpenAgentUI it calls
*any* tool registered in OpenAgents' own registry — native tools, MCP-catalog
tools, or plugin tools (e.g. ``openpro_prospection``'s
``provision_openpro_company``) — via the exact same dispatch path the agent
loop itself uses.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from openagentui.nodes.base import NodeContext, failed, ok
from openagentui.schema import NodeExecutionResult
from openagentui.templating import render_dict

logger = logging.getLogger(__name__)


def execute(ctx: NodeContext) -> NodeExecutionResult:
    from openagentui.tool_catalog import ensure_tools_loaded

    tool_name = ctx.data.get("mcpTool") or ctx.data.get("mcpAction")
    if not tool_name:
        return failed(ctx.node.id, "mcp node has no 'mcpTool' configured")

    raw_params = ctx.data.get("mcpParams") or {}
    if not isinstance(raw_params, dict):
        return failed(ctx.node.id, "mcp node 'mcpParams' must be an object")
    params = render_dict(
        raw_params,
        variables=ctx.execution.variables,
        node_results=ctx.execution.node_results,
    )

    ensure_tools_loaded()

    from tools.registry import registry

    if registry.get_entry(tool_name) is None:
        return failed(
            ctx.node.id,
            f"unknown tool: {tool_name!r} (check the tool catalog)",
            input_value=params,
        )

    try:
        raw_result = registry.dispatch(tool_name, params)
    except Exception as exc:
        logger.exception("openagentui: tool node %s failed", ctx.node.id)
        return failed(ctx.node.id, f"tool dispatch failed: {exc}", input_value=params)

    output: Any = raw_result
    if isinstance(raw_result, str):
        try:
            output = json.loads(raw_result)
        except (json.JSONDecodeError, TypeError):
            output = raw_result

    if isinstance(output, dict) and output.get("error"):
        return failed(ctx.node.id, str(output["error"]), input_value=params)

    output_field = ctx.data.get("outputField")
    if output_field:
        ctx.set_variable(output_field, output)

    return ok(ctx.node.id, output, input_value=params)
