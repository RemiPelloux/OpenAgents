"""Round-trip tests for openagentui.schema — dict <-> dataclass fidelity."""

from openagentui.schema import (
    NodeExecutionResult,
    PendingApproval,
    Workflow,
    WorkflowEdge,
    WorkflowExecution,
    WorkflowNode,
)


def test_workflow_node_roundtrip():
    raw = {
        "id": "n1",
        "type": "agent",
        "position": {"x": 10, "y": 20},
        "data": {"label": "Hi"},
    }
    node = WorkflowNode.from_dict(raw)
    assert node.id == "n1"
    assert node.type == "agent"
    assert node.to_dict() == raw


def test_workflow_edge_roundtrip():
    raw = {"id": "e1", "source": "a", "target": "b", "sourceHandle": "true"}
    edge = WorkflowEdge.from_dict(raw)
    assert edge.source_handle == "true"
    assert edge.to_dict() == raw


def test_workflow_roundtrip():
    raw = {
        "id": "wf1",
        "name": "Test",
        "description": "desc",
        "category": "cat",
        "tags": ["a", "b"],
        "nodes": [
            {"id": "start", "type": "start", "position": {"x": 0, "y": 0}, "data": {}}
        ],
        "edges": [],
        "createdAt": "2026-01-01T00:00:00Z",
        "updatedAt": "2026-01-01T00:00:00Z",
        "isTemplate": False,
    }
    workflow = Workflow.from_dict(raw)
    assert workflow.name == "Test"
    assert len(workflow.nodes) == 1
    assert workflow.to_dict() == raw


def test_workflow_summary_from_raw_omits_graph():
    raw = {
        "id": "wf1",
        "name": "Test",
        "nodes": [
            {"id": "start", "type": "start", "position": {"x": 0, "y": 0}, "data": {}}
        ],
        "edges": [{"id": "e1", "source": "start", "target": "end"}],
    }
    summary = Workflow.summary_from_raw(raw)
    assert summary["nodeCount"] == 1
    assert summary["edgeCount"] == 1
    assert "nodes" not in summary


def test_workflow_helpers():
    workflow = Workflow.from_dict({
        "id": "wf1",
        "name": "Test",
        "nodes": [
            {"id": "a", "type": "start", "position": {"x": 0, "y": 0}, "data": {}},
            {"id": "b", "type": "end", "position": {"x": 0, "y": 0}, "data": {}},
        ],
        "edges": [{"id": "e1", "source": "a", "target": "b"}],
    })
    assert workflow.node_by_id("a").id == "a"
    assert workflow.node_by_id("missing") is None
    assert [e.target for e in workflow.outgoing_edges("a")] == ["b"]
    assert workflow.outgoing_edges("b") == []


def test_workflow_execution_roundtrip():
    execution = WorkflowExecution(
        id="exec1",
        workflow_id="wf1",
        status="completed",
        node_results={
            "a": NodeExecutionResult(node_id="a", status="completed", output={"x": 1})
        },
        variables={"foo": "bar"},
        started_at="2026-01-01T00:00:00Z",
        completed_at="2026-01-01T00:01:00Z",
    )
    raw = execution.to_dict()
    restored = WorkflowExecution.from_dict(raw)
    assert restored.id == "exec1"
    assert restored.node_results["a"].output == {"x": 1}
    assert restored.variables == {"foo": "bar"}


def test_pending_approval_roundtrip():
    approval = PendingApproval(
        approval_id="appr1",
        execution_id="exec1",
        node_id="n1",
        message="ok?",
        created_at="2026-01-01T00:00:00Z",
    )
    restored = PendingApproval.from_dict(approval.to_dict())
    assert restored.approval_id == "appr1"
    assert restored.status == "pending"
