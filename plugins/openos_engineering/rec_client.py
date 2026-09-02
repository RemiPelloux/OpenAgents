"""OpenRec emit via durable outbox + drain — CC-W4-008."""

from __future__ import annotations

import logging
import os
import hashlib
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from plugins.openos_engineering.rec_outbox import drain_rec_outbox, enqueue_rec_event

logger = logging.getLogger(__name__)
_RUN_SEQUENCES: dict[str, int] = {}


def emit_rec_event(
    event_type: str,
    payload: Dict[str, Any],
    *,
    correlation_id: Optional[str] = None,
    agent_profile: Optional[str] = None,
    agent_run_id: Optional[str] = None,
    target_type: str = "ticket",
    target_id: str = "",
    target_app: str = "openagents",
    severity: str = "info",
) -> None:
    organization_id = os.environ.get(
        "OPENREC_ORG_ID", "00000000-0000-4000-8000-000000000001"
    )
    run_key = agent_run_id or correlation_id or target_id or "unknown"
    sequence = _RUN_SEQUENCES.get(run_key, 0) + 1
    _RUN_SEQUENCES[run_key] = sequence
    stable_subject = f"{organization_id}:{correlation_id or ''}:{run_key}:{sequence}:{event_type}"
    event_id = "openagents:" + hashlib.sha256(stable_subject.encode()).hexdigest()
    body: Dict[str, Any] = {
        "id": event_id,
        "event_id": event_id,
        "sequence": sequence,
        "type": event_type,
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "tenant": {
            "org_id": organization_id,
            "environment": os.environ.get("OPENREC_ENV", "dev"),
        },
        "actor": {
            "type": "agent",
            "id": "openagents",
            "agent_profile": agent_profile or "developer",
        },
        "target": {
            "type": target_type,
            "id": target_id or "unknown",
            "app": target_app,
        },
        "severity": severity,
        "payload": payload,
    }
    if correlation_id:
        body["correlation_id"] = correlation_id
    if agent_run_id:
        body["agent_run_id"] = agent_run_id

    enqueue_rec_event(body)
    try:
        drain_rec_outbox(max_items=10)
    except Exception as exc:
        logger.warning("OpenRec outbox drain failed: %s", exc)
