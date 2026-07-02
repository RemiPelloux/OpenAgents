"""Tests for codex and sub-workflow nodes."""

from unittest.mock import patch

from openagentui.nodes.base import NodeContext
from openagentui.nodes.codex_node import execute as execute_codex
from openagentui.schema import Workflow, WorkflowExecution, WorkflowNode


def _ctx(node_type: str, data: dict) -> NodeContext:
    wf = Workflow.from_dict({"id": "wf", "name": "t", "nodes": [], "edges": []})
    ex = WorkflowExecution(id="exec", workflow_id="wf", status="running", started_at="now")
    node = WorkflowNode.from_dict({"id": "n1", "type": node_type, "position": {"x": 0, "y": 0}, "data": data})
    return NodeContext(node=node, execution=ex)


def test_codex_node_requires_prompt():
    result = execute_codex(_ctx("codex", {}))
    assert result.status == "failed"


def test_codex_node_success():
    with patch(
        "openagentui.nodes.codex_node.run_codex_headless",
        return_value={"ok": True, "summary": "done"},
    ):
        result = execute_codex(_ctx("codex", {"prompt": "fix tests"}))
    assert result.status == "completed"
