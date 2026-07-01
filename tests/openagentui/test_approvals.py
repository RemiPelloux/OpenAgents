"""Tests for openagentui.approvals resolve/find helpers (edge cases not
already covered by the engine-level pause/resume test in test_engine.py).
"""

import pytest

from openagentui import store
from openagentui.approvals import find_pending_approval, resolve_approval
from openagentui.schema import WorkflowExecution


def test_resolve_approval_rejects_invalid_decision():
    with pytest.raises(ValueError):
        resolve_approval("exec1", "maybe")


def test_resolve_approval_unknown_execution_raises():
    with pytest.raises(ValueError):
        resolve_approval("exec_missing", "approved")


def test_resolve_approval_no_pending_approval_raises():
    execution = WorkflowExecution(id="exec_nopending", workflow_id="wf1", status="running", started_at="now")
    store.save_execution(execution)
    with pytest.raises(ValueError, match="no pending approval"):
        resolve_approval("exec_nopending", "approved")


def test_find_pending_approval_returns_id_or_none():
    execution = WorkflowExecution(
        id="exec_findable", workflow_id="wf1", started_at="now", pending_approval_id="appr_x",
    )
    store.save_execution(execution)
    assert find_pending_approval("exec_findable") == "appr_x"
    assert find_pending_approval("exec_missing") is None
