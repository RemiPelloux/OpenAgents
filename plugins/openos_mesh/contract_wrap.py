"""Signed ContractEnvelope helpers for OpenAgents producer hops."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

DEV_KEYS: Dict[str, str] = {
    "OpenAgents": "ukrGocUC9EafNqcsHyB6zdjWhNH8aPsd9vyaWK2whiY=",
    "OpenTeam": "/tUs2obTPQJdgZyKeWLTGzWnIbg99aMO/8QJIfORSJo=",
}


def _dev_keys_enabled() -> bool:
    value = os.environ.get("OPENCONTRACT_DEV_KEYS", "").strip().lower()
    return value in ("1", "true", "yes")


def _resolve_signing_key(signer_id: str) -> Optional[str]:
    explicit = os.environ.get("OPENCONTRACT_SIGNING_KEY", "").strip()
    if explicit:
        return explicit
    env_identity = os.environ.get("OPENCONTRACT_IDENTITY", "").strip()
    if env_identity == signer_id and _dev_keys_enabled():
        return DEV_KEYS.get(signer_id)
    if _dev_keys_enabled():
        return DEV_KEYS.get(signer_id)
    return None


def _strict_signature_required() -> bool:
    value = os.environ.get("OPENCONTRACT_REQUIRE_SIGNATURE", "").strip().lower()
    return value in ("1", "true", "yes")


def wrap_signed_hop(
    *,
    contract_id: str,
    producer: str,
    consumer: str,
    payload: Dict[str, Any],
    correlation_id: Optional[str] = None,
    prerequisites: Optional[List[str]] = None,
    goal_met: bool = True,
    signer_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Return signed envelope, or raw payload when signing is unavailable (non-strict)."""
    contract_url = os.environ.get("OPENCONTRACT_URL", "").strip().rstrip("/")
    signer = signer_id or producer
    key = _resolve_signing_key(signer)
    if not contract_url or not key:
        if _strict_signature_required():
            raise RuntimeError(f"OpenContract signing required for {signer} on {contract_id}")
        return payload

    corr = correlation_id or str(payload.get("correlation_id") or "")
    body = json.dumps(
        {
            "contract_id": contract_id,
            "correlation_id": corr,
            "satisfied_prerequisites": prerequisites or [],
            "payload": payload,
            "goal_met": goal_met,
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
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            envelope = json.load(resp)["envelope"]
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode()
        raise RuntimeError(f"OpenContract wrap failed ({exc.code}): {detail}") from exc

    envelope["producer"] = producer
    envelope["consumer"] = consumer
    return envelope
