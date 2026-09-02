"""Tests for if-else/while node executors."""

from openagentui.nodes.base import NodeContext
from openagentui.nodes.control_flow import execute_if_else, execute_while
from openagentui.schema import WorkflowExecution, WorkflowNode


def _ctx(data: dict, variables: dict | None = None) -> NodeContext:
    node = WorkflowNode(id="n1", type="if-else", data=data)
    execution = WorkflowExecution(
        id="exec1", workflow_id="wf1", variables=dict(variables or {})
    )
    return NodeContext(node=node, execution=execution)


def test_if_else_true_branch():
    ctx = _ctx({"condition": "count > 3"}, {"count": 5})
    result = execute_if_else(ctx)
    assert result.status == "completed"
    assert result.output["branch"] == "true"


def test_if_else_false_branch():
    ctx = _ctx({"condition": "count > 3"}, {"count": 1})
    result = execute_if_else(ctx)
    assert result.output["branch"] == "false"


def test_if_else_invalid_condition_fails():
    ctx = _ctx({"condition": "__import__('os')"}, {})
    result = execute_if_else(ctx)
    assert result.status == "failed"
    assert "invalid condition" in result.error


def test_while_loops_until_condition_false():
    ctx = _ctx({"condition": "count < 3"}, {"count": 0})
    ctx.node.id = "loop1"

    # Simulate three engine visits, mutating `count` between them like a
    # body node would.
    result = execute_while(ctx)
    assert result.output["branch"] == "loop"
    ctx.execution.variables["count"] = 1

    result = execute_while(ctx)
    assert result.output["branch"] == "loop"
    ctx.execution.variables["count"] = 3

    result = execute_while(ctx)
    assert result.output["branch"] == "exit"
    # Loop counter is cleared once the loop exits.
    assert f"__loop_count__loop1" not in ctx.execution.variables


def test_while_respects_max_iterations():
    ctx = _ctx({"condition": "True", "maxIterations": 2})
    ctx.node.id = "loop2"

    first = execute_while(ctx)
    assert first.output["branch"] == "loop"
    second = execute_while(ctx)
    assert second.output["branch"] == "loop"
    third = execute_while(ctx)
    assert third.output["branch"] == "exit"
