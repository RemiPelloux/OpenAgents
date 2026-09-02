"""End-to-end graph-walking tests for openagentui.engine.WorkflowEngine.

Uses only deterministic, network-free node types (start/set-state/if-else/
user-approval/end) so these run hermetically without mocking an LLM or the
tool registry.
"""

from openagentui import store
from openagentui.approvals import resolve_approval
from openagentui.engine import resume_execution, run_workflow
from openagentui.schema import Workflow


def _linear_workflow() -> Workflow:
    return Workflow.from_dict({
        "id": "wf_linear",
        "name": "Linear",
        "nodes": [
            {"id": "start", "type": "start", "position": {"x": 0, "y": 0}, "data": {}},
            {
                "id": "set1",
                "type": "set-state",
                "position": {"x": 0, "y": 0},
                "data": {"stateKey": "greeting", "stateValue": "hi {{ name }}"},
            },
            {"id": "end", "type": "end", "position": {"x": 0, "y": 0}, "data": {}},
        ],
        "edges": [
            {"id": "e1", "source": "start", "target": "set1"},
            {"id": "e2", "source": "set1", "target": "end"},
        ],
    })


def _branching_workflow() -> Workflow:
    return Workflow.from_dict({
        "id": "wf_branch",
        "name": "Branch",
        "nodes": [
            {"id": "start", "type": "start", "position": {"x": 0, "y": 0}, "data": {}},
            {
                "id": "check",
                "type": "if-else",
                "position": {"x": 0, "y": 0},
                "data": {"condition": "count > 3"},
            },
            {
                "id": "big",
                "type": "set-state",
                "position": {"x": 0, "y": 0},
                "data": {"stateKey": "size", "stateValue": "big"},
            },
            {
                "id": "small",
                "type": "set-state",
                "position": {"x": 0, "y": 0},
                "data": {"stateKey": "size", "stateValue": "small"},
            },
            {"id": "end", "type": "end", "position": {"x": 0, "y": 0}, "data": {}},
        ],
        "edges": [
            {"id": "e1", "source": "start", "target": "check"},
            {"id": "e2", "source": "check", "target": "big", "sourceHandle": "true"},
            {"id": "e3", "source": "check", "target": "small", "sourceHandle": "false"},
            {"id": "e4", "source": "big", "target": "end"},
            {"id": "e5", "source": "small", "target": "end"},
        ],
    })


def _approval_workflow() -> Workflow:
    return Workflow.from_dict({
        "id": "wf_approval",
        "name": "Approval",
        "nodes": [
            {"id": "start", "type": "start", "position": {"x": 0, "y": 0}, "data": {}},
            {
                "id": "gate",
                "type": "user-approval",
                "position": {"x": 0, "y": 0},
                "data": {"approvalMessage": "ok?"},
            },
            {"id": "end", "type": "end", "position": {"x": 0, "y": 0}, "data": {}},
        ],
        "edges": [
            {"id": "e1", "source": "start", "target": "gate"},
            {"id": "e2", "source": "gate", "target": "end", "sourceHandle": "approved"},
        ],
    })


def test_run_workflow_completes_linear_graph():
    execution = run_workflow(_linear_workflow(), inputs={"name": "bob"})
    assert execution.status == "completed"
    assert execution.variables["greeting"] == "hi bob"
    assert execution.node_results["end"].status == "completed"
    assert execution.completed_at


def test_run_workflow_follows_true_branch():
    execution = run_workflow(_branching_workflow(), inputs={"count": 10})
    assert execution.status == "completed"
    assert execution.variables["size"] == "big"


def test_run_workflow_follows_false_branch():
    execution = run_workflow(_branching_workflow(), inputs={"count": 1})
    assert execution.status == "completed"
    assert execution.variables["size"] == "small"


def test_run_workflow_pauses_on_approval_and_resumes_on_approve():
    workflow = _approval_workflow()
    store.save_workflow(workflow)
    execution = run_workflow(workflow)
    assert execution.status == "waiting-approval"
    assert execution.pending_approval_id

    resumed = resolve_approval(execution.id, "approved")
    assert resumed.status == "completed"
    assert resumed.node_results["end"].status == "completed"


def test_run_workflow_fails_on_reject():
    workflow = _approval_workflow()
    store.save_workflow(workflow)
    execution = run_workflow(workflow)
    resumed = resolve_approval(execution.id, "rejected")
    assert resumed.status == "failed"
    assert "rejected" in resumed.error


def test_resume_execution_unknown_id_raises():
    import pytest

    with pytest.raises(ValueError):
        resume_execution("exec_does_not_exist")


def test_run_workflow_missing_start_node_fails():
    workflow = Workflow.from_dict({
        "id": "wf_empty",
        "name": "Empty",
        "nodes": [],
        "edges": [],
    })
    execution = run_workflow(workflow)
    assert execution.status == "failed"
    assert "no nodes" in execution.error or "start" in execution.error


def test_on_node_callback_invoked_for_every_step():
    seen = []
    run_workflow(
        _linear_workflow(),
        inputs={"name": "x"},
        on_node=lambda r: seen.append((r.node_id, r.status)),
    )
    assert seen == [
        ("start", "running"),
        ("start", "completed"),
        ("set1", "running"),
        ("set1", "completed"),
        ("end", "running"),
        ("end", "completed"),
    ]
