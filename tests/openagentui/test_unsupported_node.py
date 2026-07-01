"""Tests for the arcade/guardrails placeholder executor."""

from openagentui.nodes.base import NodeContext
from openagentui.nodes.unsupported_node import execute
from openagentui.schema import WorkflowExecution, WorkflowNode


def test_unsupported_node_fails_with_clear_message():
    node = WorkflowNode(id="n1", type="arcade", data={})
    execution = WorkflowExecution(id="exec1", workflow_id="wf1")
    result = execute(NodeContext(node=node, execution=execution))
    assert result.status == "failed"
    assert "arcade" in result.error
    assert "not supported" in result.error


def test_unsupported_node_guardrails():
    node = WorkflowNode(id="n1", type="guardrails", data={})
    execution = WorkflowExecution(id="exec1", workflow_id="wf1")
    result = execute(NodeContext(node=node, execution=execution))
    assert result.status == "failed"
    assert "guardrails" in result.error
