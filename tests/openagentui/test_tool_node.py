"""Tests for the ``mcp`` (deterministic tool-call) node executor."""

import json

from openagentui.nodes.base import NodeContext
from openagentui.nodes.tool_node import execute
from openagentui.schema import WorkflowExecution, WorkflowNode
from tools.registry import registry, tool_error, tool_result


def _register_echo_tool():
    def handler(args, **_kwargs):
        if args.get("fail"):
            return tool_error("boom")
        return tool_result({"echoed": args.get("message")})

    if registry.get_entry("openagentui_test_echo") is None:
        registry.register(
            name="openagentui_test_echo",
            toolset="openagentui_test",
            schema={
                "name": "openagentui_test_echo",
                "description": "test",
                "parameters": {"type": "object"},
            },
            handler=handler,
        )


def _ctx(data: dict, variables: dict | None = None) -> NodeContext:
    node = WorkflowNode(id="n1", type="mcp", data=data)
    execution = WorkflowExecution(
        id="exec1", workflow_id="wf1", variables=dict(variables or {})
    )
    return NodeContext(node=node, execution=execution)


def test_tool_node_dispatches_registered_tool():
    _register_echo_tool()
    ctx = _ctx(
        {
            "mcpTool": "openagentui_test_echo",
            "mcpParams": {"message": "hi {{ name }}"},
            "outputField": "echo_out",
        },
        {"name": "bob"},
    )
    result = execute(ctx)
    assert result.status == "completed"
    assert result.output == {"echoed": "hi bob"}
    assert ctx.execution.variables["echo_out"] == {"echoed": "hi bob"}


def test_tool_node_missing_tool_name_fails():
    ctx = _ctx({})
    result = execute(ctx)
    assert result.status == "failed"
    assert "mcpTool" in result.error


def test_tool_node_unknown_tool_fails():
    ctx = _ctx({"mcpTool": "does_not_exist_tool_xyz"})
    result = execute(ctx)
    assert result.status == "failed"
    assert "unknown tool" in result.error


def test_tool_node_propagates_tool_error():
    _register_echo_tool()
    ctx = _ctx({"mcpTool": "openagentui_test_echo", "mcpParams": {"fail": True}})
    result = execute(ctx)
    assert result.status == "failed"
    assert "boom" in result.error


def test_tool_node_rejects_non_dict_params():
    ctx = _ctx({"mcpTool": "openagentui_test_echo", "mcpParams": "not-a-dict"})
    result = execute(ctx)
    assert result.status == "failed"
    assert "mcpParams" in result.error
