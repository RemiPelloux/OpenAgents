"""Shared OpenRec outbox helpers — CC-W4-008."""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
import urllib.parse
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 5
_OAUTH_TOKEN: tuple[str, float] | None = None

DEV_KEYS = {
    "OpenAgents": "ukrGocUC9EafNqcsHyB6zdjWhNH8aPsd9vyaWK2whiY=",
}


def _enabled(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes")


def _resolve_signing_key(signer: str) -> Optional[str]:
    explicit = os.environ.get("OPENCONTRACT_SIGNING_KEY", "").strip()
    if explicit:
        return explicit
    if _enabled("OPENCONTRACT_DEV_KEYS"):
        return DEV_KEYS.get(signer)
    return None


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


def file_outbox_allowed() -> bool:
    return os.environ.get("OPENOS_DEV_OUTBOX_FILE", "").strip() == "1"


def require_mesh_outbox_configured() -> None:
    if pg_outbox_enabled() or file_outbox_allowed():
        return
    raise RuntimeError(
        "MESH_OUTBOX_DATABASE_URL required; set OPENOS_DEV_OUTBOX_FILE=1 for local file fallback only"
    )


def oauth_authorization() -> str:
    global _OAUTH_TOKEN
    if _OAUTH_TOKEN and _OAUTH_TOKEN[1] > time.time():
        return f"Bearer {_OAUTH_TOKEN[0]}"
    auth_url = os.environ.get("PLATFORM_AUTH_URL", "").rstrip("/")
    client_id = os.environ.get("PLATFORM_AUTH_CLIENT_ID", "").strip()
    client_secret = os.environ.get("PLATFORM_AUTH_CLIENT_SECRET", "").strip()
    org_id = os.environ.get("OPENREC_ORG_ID", "").strip()
    if not all((auth_url, client_id, client_secret, org_id)):
        return ""
    data = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
        "audience": "openrec",
        "organization_id": org_id,
        "scope": "rec:read rec:write",
    }).encode()
    req = urllib.request.Request(
        f"{auth_url}/oauth/token",
        data=data,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        payload = json.load(resp)
    token = str(payload.get("access_token", ""))
    if not token:
        return ""
    ttl = max(1, int(payload.get("expires_in", 300)) - 30)
    _OAUTH_TOKEN = (token, time.time() + ttl)
    return f"Bearer {token}"


def wrap_rec_event(event: Dict[str, Any]) -> Dict[str, Any]:
    contract_url = os.environ.get("OPENCONTRACT_URL", "").rstrip("/")
    if not contract_url:
        if _enabled("OPENCONTRACT_REQUIRE_SIGNATURE"):
            raise RuntimeError(
                "OpenContract URL required when signatures are mandatory"
            )
        return event

    contract_id = os.environ.get("OPENREC_CONTRACT_ID", "CC-W4-008")
    signer = os.environ.get("OPENCONTRACT_IDENTITY", "OpenAgents")
    key = _resolve_signing_key(signer)
    if not key:
        if _enabled("OPENCONTRACT_REQUIRE_SIGNATURE"):
            raise RuntimeError(f"OpenContract signing key required for {signer}")
        return event

    prereq = [
        p.strip()
        for p in os.environ.get("OPENREC_CONTRACT_PREREQ", "CC-W4-001").split(",")
        if p.strip()
    ]
    body = json.dumps({
        "contract_id": contract_id,
        "correlation_id": str(event.get("correlation_id") or ""),
        "satisfied_prerequisites": prereq,
        "payload": event,
        "goal_met": True,
        "signer_id": signer,
        "signing_key": key,
    }).encode()
    req = urllib.request.Request(
        f"{contract_url}/v1/contracts/{contract_id}/wrap",
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": oauth_authorization(),
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        payload = json.load(resp)
    return payload["envelope"]


def post_rec_event(base_url: str, body: Dict[str, Any]) -> bool:
    try:
        payload = wrap_rec_event(body)
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            f"{base_url}/v1/events",
            data=data,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": oauth_authorization(),
            },
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status in (200, 201, 202)
    except urllib.error.HTTPError as exc:
        return exc.code in (200, 201, 202)
    except (OSError, RuntimeError, ValueError):
        logger.exception("OpenRec event delivery failed")
        return False
