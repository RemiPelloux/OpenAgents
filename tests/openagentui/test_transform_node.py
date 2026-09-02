"""Tests for the ``transform`` node executor (sandboxed Python)."""

import json

from openagentui.nodes.base import NodeContext
from openagentui.nodes import transform_node
from openagentui.schema import WorkflowExecution, WorkflowNode


def _ctx(data: dict, variables: dict | None = None) -> NodeContext:
    node = WorkflowNode(id="n1", type="transform", data=data)
    execution = WorkflowExecution(
        id="exec1", workflow_id="wf1", variables=dict(variables or {})
    )
    return NodeContext(node=node, execution=execution)


def _fake_execute_code(sandbox_output):
    def fake(script, task_id=None):
        return json.dumps({"status": "success", "output": sandbox_output})

    return fake


def test_transform_missing_script_fails():
    ctx = _ctx({})
    result = transform_node.execute(ctx)
    assert result.status == "failed"
    assert "transformScript" in result.error


def test_transform_success_parses_json_stdout(monkeypatch):
    monkeypatch.setattr(
        "tools.code_execution_tool.execute_code",
        _fake_execute_code(json.dumps({"doubled": 10})),
    )
    ctx = _ctx({
        "transformScript": "print('{\"doubled\": 10}')",
        "outputField": "result",
    })
    result = transform_node.execute(ctx)
    assert result.status == "completed"
    assert result.output == {"doubled": 10}
    assert ctx.execution.variables["result"] == {"doubled": 10}


def test_transform_success_falls_back_to_raw_text(monkeypatch):
    monkeypatch.setattr(
        "tools.code_execution_tool.execute_code", _fake_execute_code("plain text")
    )
    ctx = _ctx({"transformScript": "print('plain text')"})
    result = transform_node.execute(ctx)
    assert result.status == "completed"
    assert result.output == "plain text"


def test_transform_sandbox_error_status_fails(monkeypatch):
    def fake(script, task_id=None):
        return json.dumps({"status": "error", "output": "traceback..."})

    monkeypatch.setattr("tools.code_execution_tool.execute_code", fake)
    ctx = _ctx({"transformScript": "raise ValueError('boom')"})
    result = transform_node.execute(ctx)
    assert result.status == "failed"


def test_transform_explicit_error_field_fails(monkeypatch):
    def fake(script, task_id=None):
        return json.dumps({"error": "sandbox crashed"})

    monkeypatch.setattr("tools.code_execution_tool.execute_code", fake)
    ctx = _ctx({"transformScript": "1/0"})
    result = transform_node.execute(ctx)
    assert result.status == "failed"
    assert "sandbox crashed" in result.error
