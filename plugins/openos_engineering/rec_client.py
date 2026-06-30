"""Emit RecEvents to OpenRec ingest API."""

from __future__ import annotations

import json
import os
import uuid
import urllib.request
from typing import Any, Dict, Optional


def emit_rec_event(
    event_type: str,
    payload: Dict[str, Any],
    *,
    correlation_id: Optional[str] = None,
    agent_profile: Optional[str] = None,
    target_type: str = "ticket",
    target_id: str = "",
    target_app: str = "openagents",
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
        "severity": "info",
        "payload": payload,
    }
    if correlation_id:
        body["correlation_id"] = correlation_id

    data = json.dumps(body).encode()
    req = urllib.request.Request(
        f"{base_url}/v1/events",
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        urllib.request.urlopen(req, timeout=15)
    except Exception as exc:
        import logging

        logging.getLogger(__name__).warning("OpenRec emit failed: %s", exc)
