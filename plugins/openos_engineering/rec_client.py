"""OpenRec emit with retry — CC-W4-008."""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3


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
    base_url = os.environ.get("OPENREC_URL", "").rstrip("/")
    if not base_url:
        return

    body = {
        "id": str(uuid.uuid4()),
        "type": event_type,
        "timestamp": __import__("datetime").datetime.utcnow().isoformat() + "Z",
        "tenant": {
            "org_id": os.environ.get("OPENREC_ORG_ID", "default"),
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

    data = json.dumps(body).encode()
    for attempt in range(1, MAX_ATTEMPTS + 1):
        req = urllib.request.Request(
            f"{base_url}/v1/events",
            data=data,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            urllib.request.urlopen(req, timeout=15)
            return
        except urllib.error.HTTPError as exc:
            if exc.code in (200, 201, 202):
                return
            logger.warning("OpenRec emit HTTP %s attempt %s", exc.code, attempt)
        except Exception as exc:
            logger.warning("OpenRec emit failed attempt %s: %s", attempt, exc)
        time.sleep(0.5 * attempt)
