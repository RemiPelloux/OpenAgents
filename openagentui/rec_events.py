"""Emit OpenRec events for OpenAgentUI executions (CC-OA-OAUI registry types)."""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from openagentui.rec_outbox import drain_rec_outbox, enqueue_rec_event
from openagentui.schema import Workflow, WorkflowExecution

logger = logging.getLogger(__name__)


def _correlation_id() -> str:
    return (
        os.environ.get("X_CORRELATION_ID")
        or os.environ.get("OPENAGENTS_CORRELATION_ID")
        or str(uuid.uuid4())
    )


def emit_execution_rec_event(execution: WorkflowExecution, workflow: Workflow) -> None:
    """Best-effort RecEvent on terminal or paused execution states."""
    if not os.environ.get("OPENREC_URL", "").strip():
        return

    correlation_id = _correlation_id()
    base_payload: Dict[str, Any] = {
        "execution_id": execution.id,
        "workflow_id": workflow.id,
        "workflow_name": workflow.name,
        "status": execution.status,
        "error": execution.error,
        "correlation_id": correlation_id,
    }

    if execution.status == "waiting-approval":
        event_type = "openagentui.execution.paused"
        base_payload["pending_approval_id"] = execution.pending_approval_id
        approval_msg = ""
        if execution.pending_approval_id:
            from openagentui import store

            approval = store.get_approval(execution.pending_approval_id)
            if approval:
                approval_msg = approval.message
        base_payload["message"] = approval_msg
    elif execution.status in ("completed", "failed"):
        event_type = "openagentui.execution.completed"
    else:
        return

    body: Dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "type": event_type,
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "tenant": {
            "org_id": os.environ.get(
                "OPENREC_ORG_ID", "00000000-0000-4000-8000-000000000001"
            ),
            "environment": os.environ.get("OPENREC_ENV", "dev"),
        },
        "actor": {"type": "agent", "id": "openagents", "agent_profile": "openagentui"},
        "target": {"type": "workflow", "id": workflow.id, "app": "openagents"},
        "severity": "info" if execution.status == "completed" else "warning",
        "payload": base_payload,
        "correlation_id": correlation_id,
    }
    try:
        enqueue_rec_event(body)
        drain_rec_outbox(max_items=5)
    except Exception as exc:
        logger.warning("openagentui: RecEvent enqueue failed: %s", exc)
