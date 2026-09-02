"""Resolve org secrets from OpenBrain — CC-BRAIN-007."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Optional


def _brain_base_url() -> str:
    return (
        os.environ.get("OPENBRAIN_URL", "").strip()
        or os.environ.get("OPENBRAIN_API_URL", "http://localhost:3001").strip()
    ).rstrip("/")


def resolve_brain_secret(
    name: str,
    *,
    workflow_run_id: str,
    correlation_id: str,
    organization_id: Optional[str] = None,
) -> Optional[str]:
    base = _brain_base_url()
    if not base:
        return None

    internal_key = os.environ.get("INTERNAL_SERVICE_KEY", "axon-internal-dev-key")
    org_id = organization_id or os.environ.get("OPENBRAIN_ORG_ID", "")

    body = json.dumps({
        "name": name,
        "workflow_run_id": workflow_run_id,
        "correlation_id": correlation_id,
    }).encode()
    headers = {
        "Content-Type": "application/json",
        "X-Internal-Service-Key": internal_key,
    }
    if org_id:
        headers["X-Organization-Id"] = org_id

    req = urllib.request.Request(
        f"{base}/api/v1/internal/secrets/resolve",
        data=body,
        method="POST",
        headers=headers,
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = json.load(resp)
        data = payload.get("data") or {}
        value = data.get("value")
        return str(value) if value else None
    except urllib.error.HTTPError:
        return None
