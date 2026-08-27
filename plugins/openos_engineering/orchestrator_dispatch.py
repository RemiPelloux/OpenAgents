"""Unwrap and verify signed OpenOrchestrator → OpenAgents run dispatch envelopes."""

from __future__ import annotations

import base64
import json
import os
from typing import Any, Dict, Mapping, MutableMapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

DISPATCH_RUN_CONTRACTS: Dict[str, tuple[str, str]] = {
    "CC-ORCH-004": ("OpenOrchestrator", "OpenAgents"),
    "CC-OT-001": ("OpenTeam", "OpenAgents"),
}

MESH_IDENTITIES: Dict[str, str] = {
    "OpenOrchestrator": "2gcbZ39WzoPCS3Jrh1QHSo3YCYCmIoyESHINVI+0toE=",
    "OpenTeam": "FsrWcHmOoewkBeVhVTcj7RTe2KwiSo6k5MIzIKGbJcs=",
}


def _identity_registry() -> Dict[str, str]:
    raw = os.environ.get("OPENCONTRACT_IDENTITIES", "").strip()
    if not raw:
        if _dev_keys_relaxed() or not _strict_signature_required():
            return MESH_IDENTITIES
        raise ValueError("OPENCONTRACT_IDENTITIES required for strict signature verification")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("OPENCONTRACT_IDENTITIES must be valid JSON") from exc
    if not isinstance(parsed, dict) or not parsed:
        raise ValueError("OPENCONTRACT_IDENTITIES must be a non-empty object")
    configured: Dict[str, Any]
    records = parsed.get("identities")
    if records is not None:
        if not isinstance(records, list):
            raise ValueError("OPENCONTRACT_IDENTITIES identities must be an array")
        configured = {}
        for record in records:
            if not isinstance(record, dict):
                raise ValueError("OPENCONTRACT_IDENTITIES contains an invalid identity")
            identity = record.get("id")
            public_key = record.get("public_key")
            if not isinstance(identity, str) or not isinstance(public_key, str):
                raise ValueError("OPENCONTRACT_IDENTITIES contains an invalid identity")
            if identity in configured:
                raise ValueError(f"duplicate identity: {identity}")
            configured[identity] = public_key
    else:
        configured = parsed
    identities: Dict[str, str] = {}
    for identity, public_key in configured.items():
        if not isinstance(identity, str) or not identity or not isinstance(public_key, str):
            raise ValueError("OPENCONTRACT_IDENTITIES contains an invalid identity")
        try:
            decoded = base64.b64decode(public_key, validate=True)
        except ValueError as exc:
            raise ValueError(f"invalid public key for {identity}") from exc
        if len(decoded) != 32:
            raise ValueError(f"invalid public key for {identity}")
        identities[identity] = public_key
    return identities


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
    # Match TypeScript JSON.stringify: emit Unicode characters directly rather
    # than escaping them as ASCII, otherwise signed prompts fail verification.
    return json.dumps(canonical, separators=(",", ":"), sort_keys=True, ensure_ascii=False).encode("utf-8")


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

    public_key_b64 = _identity_registry().get(str(signer_id))
    if not public_key_b64:
        raise ValueError(f"unknown identity: {signer_id}")

    verify_key = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key_b64))
    sig = base64.b64decode(str(signature.get("value", "")))
    message = _envelope_signing_bytes(envelope)
    try:
        verify_key.verify(sig, message)
    except InvalidSignature as exc:
        raise ValueError("invalid contract signature") from exc


def unwrap_orchestrator_run_body(body: Dict[str, Any]) -> Dict[str, Any]:
    """Return plain /v1/runs JSON from optional CC-ORCH-004 envelope."""
    if not _is_envelope(body):
        if _strict_signature_required():
            raise ValueError("CONTRACT_ENVELOPE_REQUIRED")
        return body

    contract_id = body.get("contract_id")
    parties = DISPATCH_RUN_CONTRACTS.get(str(contract_id))
    if parties is None:
        expected = ", ".join(sorted(DISPATCH_RUN_CONTRACTS))
        raise ValueError(f"expected one of {expected}, got {contract_id}")

    expected_producer, expected_consumer = parties
    if _normalize_party_identity(str(body.get("producer", ""))) != expected_producer:
        raise ValueError(f"expected producer {expected_producer}")
    if _normalize_party_identity(str(body.get("consumer", ""))) != expected_consumer:
        raise ValueError(f"expected consumer {expected_consumer}")

    _verify_envelope_signature(body)

    payload = body.get("payload")
    if not isinstance(payload, dict):
        raise ValueError("envelope payload must be an object")
    return payload
