"""Unwrap and verify signed OpenOrchestrator → OpenAgents run dispatch envelopes."""

from __future__ import annotations

import base64
import json
import os
from typing import Any, Dict, Mapping, MutableMapping

DISPATCH_RUN_CONTRACT = "CC-ORCH-004"

MESH_IDENTITIES: Dict[str, str] = {
    "OpenOrchestrator": "2gcbZ39WzoPCS3Jrh1QHSo3YCYCmIoyESHINVI+0toE=",
}


def _is_envelope(body: Any) -> bool:
    return (
        isinstance(body, dict)
        and isinstance(body.get("contract_id"), str)
        and isinstance(body.get("payload"), dict)
    )


def _strict_signature_required() -> bool:
    value = os.environ.get("OPENCONTRACT_REQUIRE_SIGNATURE", "").strip().lower()
    return value in ("1", "true", "yes")


def _normalize_party_identity(party: str) -> str:
    idx = party.find(" [")
    base = party if idx == -1 else party[:idx]
    return base.split("|")[0].strip()


def _canonicalize_json(value: Any) -> Any:
    if isinstance(value, list):
        return [_canonicalize_json(item) for item in value]
    if isinstance(value, dict):
        return {key: _canonicalize_json(value[key]) for key in sorted(value.keys())}
    return value


def _envelope_signing_bytes(envelope: Mapping[str, Any]) -> bytes:
    unsigned: MutableMapping[str, Any] = dict(envelope)
    unsigned.pop("signature", None)
    canonical = _canonicalize_json(unsigned)
    return json.dumps(canonical, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _dev_keys_relaxed() -> bool:
    value = os.environ.get("OPENCONTRACT_DEV_KEYS", "").strip().lower()
    return value in ("1", "true", "yes")


def _verify_envelope_signature(envelope: Mapping[str, Any]) -> None:
    if _dev_keys_relaxed():
        return

    signature = envelope.get("signature")
    if not isinstance(signature, dict):
        if _strict_signature_required() or envelope.get("signature") is not None:
            raise ValueError("missing contract signature")
        return

    algorithm = signature.get("algorithm")
    if algorithm != "ed25519":
        raise ValueError(f"unsupported algorithm: {algorithm}")

    expected_signer = _normalize_party_identity(str(envelope.get("producer", "")))
    signer_id = signature.get("signer_id")
    if signer_id != expected_signer:
        raise ValueError(f"signer mismatch: expected {expected_signer}, got {signer_id}")

    public_key_b64 = MESH_IDENTITIES.get(str(signer_id))
    if not public_key_b64:
        raise ValueError(f"unknown identity: {signer_id}")

    try:
        from nacl.exceptions import BadSignatureError
        from nacl.signing import VerifyKey
    except ImportError as exc:
        if _dev_keys_relaxed():
            return
        raise ValueError("PyNaCl required for OPENCONTRACT signature verification") from exc

    verify_key = VerifyKey(base64.b64decode(public_key_b64))
    sig = base64.b64decode(str(signature.get("value", "")))
    message = _envelope_signing_bytes(envelope)
    try:
        verify_key.verify(message, sig)
    except BadSignatureError as exc:
        raise ValueError("invalid contract signature") from exc


def unwrap_orchestrator_run_body(body: Dict[str, Any]) -> Dict[str, Any]:
    """Return plain /v1/runs JSON from optional CC-ORCH-004 envelope."""
    if not _is_envelope(body):
        if _strict_signature_required():
            raise ValueError("CONTRACT_ENVELOPE_REQUIRED")
        return body

    contract_id = body.get("contract_id")
    if contract_id != DISPATCH_RUN_CONTRACT:
        raise ValueError(f"expected contract {DISPATCH_RUN_CONTRACT}, got {contract_id}")

    _verify_envelope_signature(body)

    payload = body.get("payload")
    if not isinstance(payload, dict):
        raise ValueError("envelope payload must be an object")
    return payload
