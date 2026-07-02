"""Unwrap signed OpenOrchestrator → OpenAgents run dispatch envelopes."""

from __future__ import annotations

from typing import Any, Dict

DISPATCH_RUN_CONTRACT = "CC-ORCH-004"


def _is_envelope(body: Any) -> bool:
    return (
        isinstance(body, dict)
        and isinstance(body.get("contract_id"), str)
        and isinstance(body.get("payload"), dict)
    )


def unwrap_orchestrator_run_body(body: Dict[str, Any]) -> Dict[str, Any]:
    """Return plain /v1/runs JSON from optional CC-ORCH-004 envelope."""
    if not _is_envelope(body):
        return body

    contract_id = body.get("contract_id")
    if contract_id != DISPATCH_RUN_CONTRACT:
        raise ValueError(f"expected contract {DISPATCH_RUN_CONTRACT}, got {contract_id}")

    payload = body.get("payload")
    if not isinstance(payload, dict):
        raise ValueError("envelope payload must be an object")
    return payload
