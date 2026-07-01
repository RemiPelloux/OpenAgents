"""Resolve a paused ``user-approval`` node and resume its execution.

Shared by ``/OpenAgentConfig approve|reject`` and the REST approval-resolve
endpoint (see ``openagents_cli/openagentui_server.py``).
"""

from __future__ import annotations

import time
from typing import Optional

from openagentui import engine, store
from openagentui.schema import WorkflowExecution

_VALID_DECISIONS = {"approved", "rejected"}


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def resolve_approval(execution_id: str, decision: str) -> WorkflowExecution:
    """Approve/reject the pending approval on ``execution_id`` and resume the run."""
    decision = decision.strip().lower()
    if decision not in _VALID_DECISIONS:
        raise ValueError(f"decision must be one of {sorted(_VALID_DECISIONS)}, got {decision!r}")

    execution = store.get_execution(execution_id)
    if execution is None:
        raise ValueError(f"unknown execution: {execution_id}")
    if not execution.pending_approval_id:
        raise ValueError(f"execution {execution_id} has no pending approval")

    approval = store.get_approval(execution.pending_approval_id)
    if approval is None:
        raise ValueError(f"unknown approval: {execution.pending_approval_id}")
    if approval.status != "pending":
        raise ValueError(f"approval {approval.approval_id} already resolved as {approval.status!r}")

    approval.status = decision
    approval.responded_at = _now_iso()
    store.save_approval(approval)

    return engine.resume_execution(execution_id)


def find_pending_approval(execution_id: str) -> Optional[str]:
    execution = store.get_execution(execution_id)
    return execution.pending_approval_id if execution else None
