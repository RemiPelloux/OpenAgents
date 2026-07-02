"""Shared OpenRec outbox helpers — CC-W4-008."""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 5

DEV_KEYS = {
    "OpenAgents": "ukrGocUC9EafNqcsHyB6zdjWhNH8aPsd9vyaWK2whiY=",
}


def outbox_database_url() -> Optional[str]:
    return (
        os.environ.get("OPENAGENTS_OUTBOX_DATABASE_URL", "").strip()
        or os.environ.get("MESH_OUTBOX_DATABASE_URL", "").strip()
        or None
    )


def psycopg_available() -> bool:
    try:
        import psycopg  # noqa: F401

        return True
    except ImportError:
        return False


def pg_outbox_enabled() -> bool:
    if not outbox_database_url():
        return False
    if not psycopg_available():
        logger.warning(
            "MESH_OUTBOX_DATABASE_URL set but psycopg missing; install openagents[mesh]"
        )
        return False
    return True


def wrap_rec_event(event: Dict[str, Any]) -> Dict[str, Any]:
    contract_url = os.environ.get("OPENCONTRACT_URL", "").rstrip("/")
    if not contract_url:
        return event

    contract_id = os.environ.get("OPENREC_CONTRACT_ID", "CC-W4-008")
    signer = os.environ.get("OPENCONTRACT_IDENTITY", "OpenAgents")
    key = os.environ.get("OPENCONTRACT_SIGNING_KEY") or DEV_KEYS.get(signer)
    if not key:
        return event

    prereq = [
        p.strip()
        for p in os.environ.get("OPENREC_CONTRACT_PREREQ", "CC-W4-001").split(",")
        if p.strip()
    ]
    body = json.dumps(
        {
            "contract_id": contract_id,
            "correlation_id": str(event.get("correlation_id") or ""),
            "satisfied_prerequisites": prereq,
            "payload": event,
            "goal_met": True,
            "signer_id": signer,
            "signing_key": key,
        }
    ).encode()
    req = urllib.request.Request(
        f"{contract_url}/v1/contracts/{contract_id}/wrap",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        payload = json.load(resp)
    return payload["envelope"]


def post_rec_event(base_url: str, body: Dict[str, Any]) -> bool:
    payload = wrap_rec_event(body)
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{base_url}/v1/events",
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status in (200, 201, 202)
    except urllib.error.HTTPError as exc:
        return exc.code in (200, 201, 202)
