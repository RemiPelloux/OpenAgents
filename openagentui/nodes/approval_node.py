"""``user-approval`` node — pauses the run for a human decision.

Mirrors the interrupt/resume shape already used by
``gateway/platforms/api_server.py``'s ``/v1/runs/{id}/approval`` endpoint:
the first visit creates a ``PendingApproval`` record and the engine parks
the execution in ``paused``/``waiting-approval`` status; a later call to
``/OpenAgentConfig approve <execution_id>`` (or the dashboard) resolves the
record, and re-running the workflow from where it left off re-visits this
node, which now sees the resolved decision and continues.
"""

from __future__ import annotations

import time

from openagentui import store
from openagentui.nodes.base import NodeContext, failed, ok, pending_approval
from openagentui.schema import NodeExecutionResult, PendingApproval


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _approval_id_for(execution_id: str, node_id: str) -> str:
    return f"appr_{execution_id}_{node_id}"


def execute(ctx: NodeContext) -> NodeExecutionResult:
    approval_id = _approval_id_for(ctx.execution.id, ctx.node.id)
    approval = store.get_approval(approval_id)

    if approval is None:
        message = ctx.rendered("approvalMessage") or f"Approve step '{ctx.node.id}'?"
        approval = PendingApproval(
            approval_id=approval_id,
            execution_id=ctx.execution.id,
            node_id=ctx.node.id,
            message=message,
            status="pending",
            created_at=_now_iso(),
        )
        store.save_approval(approval)
        ctx.execution.pending_approval_id = approval_id
        return pending_approval(ctx.node.id)

    if approval.status == "pending":
        ctx.execution.pending_approval_id = approval_id
        return pending_approval(ctx.node.id)

    if approval.status == "approved":
        ctx.execution.pending_approval_id = None
        return ok(ctx.node.id, {"branch": "approved", "message": approval.message})

    ctx.execution.pending_approval_id = None
    return failed(ctx.node.id, f"rejected by reviewer: {approval.message}")
