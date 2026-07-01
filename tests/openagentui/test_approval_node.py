"""Tests for the user-approval node executor's pause/resume shape."""

from openagentui import store
from openagentui.nodes import approval_node
from openagentui.nodes.base import NodeContext
from openagentui.schema import WorkflowExecution, WorkflowNode


def _ctx(execution_id="exec1"):
    node = WorkflowNode(id="approve1", type="user-approval", data={"approvalMessage": "Send it?"})
    execution = WorkflowExecution(id=execution_id, workflow_id="wf1")
    return NodeContext(node=node, execution=execution)


def test_first_visit_creates_pending_approval_and_pauses():
    ctx = _ctx()
    result = approval_node.execute(ctx)
    assert result.status == "pending-approval"
    assert ctx.execution.pending_approval_id == "appr_exec1_approve1"

    approval = store.get_approval("appr_exec1_approve1")
    assert approval is not None
    assert approval.status == "pending"
    assert approval.message == "Send it?"


def test_second_visit_still_pending_stays_paused():
    ctx = _ctx("exec2")
    approval_node.execute(ctx)
    result = approval_node.execute(ctx)
    assert result.status == "pending-approval"


def test_approved_decision_continues_with_branch():
    ctx = _ctx("exec3")
    approval_node.execute(ctx)
    approval = store.get_approval("appr_exec3_approve1")
    approval.status = "approved"
    store.save_approval(approval)

    result = approval_node.execute(ctx)
    assert result.status == "completed"
    assert result.output["branch"] == "approved"
    assert ctx.execution.pending_approval_id is None


def test_rejected_decision_fails_node():
    ctx = _ctx("exec4")
    approval_node.execute(ctx)
    approval = store.get_approval("appr_exec4_approve1")
    approval.status = "rejected"
    store.save_approval(approval)

    result = approval_node.execute(ctx)
    assert result.status == "failed"
    assert "rejected" in result.error
