"""JSON-file persistence tests for openagentui.store.

OPENAGENTS_HOME is redirected to a per-test tempdir by the autouse
``_hermetic_environment`` fixture in tests/conftest.py, so these tests
never touch a developer's real ~/.openagents/.
"""

import pytest

from openagentui import store
from openagentui.schema import PendingApproval, Workflow, WorkflowExecution


def _workflow(id_="wf_test") -> Workflow:
    return Workflow.from_dict({"id": id_, "name": "My Workflow", "nodes": [], "edges": []})


def test_save_and_get_workflow():
    saved = store.save_workflow(_workflow())
    assert saved.created_at
    assert saved.updated_at

    fetched = store.get_workflow("wf_test")
    assert fetched is not None
    assert fetched.name == "My Workflow"


def test_get_missing_workflow_returns_none():
    assert store.get_workflow("does_not_exist") is None


def test_list_and_delete_workflow():
    store.save_workflow(_workflow("wf_a"))
    store.save_workflow(_workflow("wf_b"))
    ids = {w.id for w in store.list_workflows()}
    assert {"wf_a", "wf_b"} <= ids

    assert store.delete_workflow("wf_a") is True
    assert store.delete_workflow("wf_a") is False
    assert "wf_a" not in {w.id for w in store.list_workflows()}


def test_find_workflow_by_name_case_insensitive():
    store.save_workflow(_workflow("wf_named"))
    found = store.find_workflow_by_name("my workflow")
    assert found is not None
    assert found.id == "wf_named"
    assert store.find_workflow_by_name("nope") is None


def test_invalid_id_rejected():
    with pytest.raises(ValueError):
        store.get_workflow("../../etc/passwd")
    with pytest.raises(ValueError):
        store.save_workflow(_workflow("../escape"))


def test_execution_save_get_list():
    execution = WorkflowExecution(id="exec_1", workflow_id="wf_test", status="running", started_at="now")
    store.save_execution(execution)
    fetched = store.get_execution("exec_1")
    assert fetched is not None
    assert fetched.status == "running"

    execution2 = WorkflowExecution(id="exec_2", workflow_id="wf_other", status="completed", started_at="now")
    store.save_execution(execution2)
    assert {e.id for e in store.list_executions()} >= {"exec_1", "exec_2"}
    assert [e.id for e in store.list_executions("wf_test")] == ["exec_1"]


def test_approval_save_get_and_pending_filter():
    pending = PendingApproval(approval_id="appr_1", execution_id="exec_1", node_id="n1", message="ok?", status="pending")
    resolved = PendingApproval(approval_id="appr_2", execution_id="exec_2", node_id="n2", message="ok?", status="approved")
    store.save_approval(pending)
    store.save_approval(resolved)

    assert store.get_approval("appr_1").status == "pending"
    pending_only = {a.approval_id for a in store.list_pending_approvals()}
    assert "appr_1" in pending_only
    assert "appr_2" not in pending_only


def test_mcp_server_crud():
    store.save_mcp_server({"id": "srv_1", "name": "Test server"})
    assert any(s["id"] == "srv_1" for s in store.list_mcp_servers())
    assert store.delete_mcp_server("srv_1") is True
    assert store.delete_mcp_server("srv_1") is False


def test_new_id_is_unique_and_prefixed():
    a = store.new_id("wf")
    b = store.new_id("wf")
    assert a != b
    assert a.startswith("wf_")
