"""Tests for start/end/note/set-state node executors."""

from openagentui.nodes.base import NodeContext
from openagentui.nodes.simple_nodes import (
    execute_end,
    execute_note,
    execute_set_state,
    execute_start,
)
from openagentui.schema import WorkflowExecution, WorkflowNode


def _ctx(node_type: str, data: dict, variables: dict | None = None) -> NodeContext:
    node = WorkflowNode(id="n1", type=node_type, data=data)
    execution = WorkflowExecution(
        id="exec1", workflow_id="wf1", variables=dict(variables or {})
    )
    return NodeContext(node=node, execution=execution)


def test_start_seeds_default_variables():
    ctx = _ctx(
        "start", {"inputVariables": [{"name": "greeting", "defaultValue": "hi"}]}
    )
    result = execute_start(ctx)
    assert result.status == "completed"
    assert ctx.execution.variables["greeting"] == "hi"


def test_start_keeps_supplied_runtime_input():
    ctx = _ctx(
        "start",
        {"inputVariables": [{"name": "greeting", "defaultValue": "hi"}]},
        {"greeting": "yo"},
    )
    execute_start(ctx)
    assert ctx.execution.variables["greeting"] == "yo"


def test_start_missing_required_variable_fails():
    ctx = _ctx("start", {"inputVariables": [{"name": "must_have", "required": True}]})
    result = execute_start(ctx)
    assert result.status == "failed"
    assert "must_have" in result.error


def test_end_uses_output_mapping_with_templating():
    ctx = _ctx(
        "end", {"outputMapping": {"final": "{{ greeting }}"}}, {"greeting": "hi"}
    )
    result = execute_end(ctx)
    assert result.output == {"final": "hi"}


def test_end_defaults_to_all_variables():
    ctx = _ctx("end", {}, {"a": 1, "b": 2})
    result = execute_end(ctx)
    assert result.output == {"a": 1, "b": 2}


def test_note_is_noop():
    ctx = _ctx("note", {})
    result = execute_note(ctx)
    assert result.status == "completed"
    assert result.output is None


def test_set_state_sets_variable():
    ctx = _ctx("set-state", {"stateKey": "status", "stateValue": "ready"})
    result = execute_set_state(ctx)
    assert result.status == "completed"
    assert ctx.execution.variables["status"] == "ready"


def test_set_state_missing_key_fails():
    ctx = _ctx("set-state", {"stateValue": "ready"})
    result = execute_set_state(ctx)
    assert result.status == "failed"
