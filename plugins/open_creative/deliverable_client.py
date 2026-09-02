"""Post workflow deliverables back to OpenBrain."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional


def post_deliverables(
    *,
    session_id: str,
    workflow_run_id: str,
    correlation_id: str,
    images: List[Dict[str, Any]],
    summary: Optional[str] = None,
) -> bool:
    base = (
        os.environ.get("OPENBRAIN_URL", "").strip()
        or os.environ.get("OPENBRAIN_API_URL", "http://localhost:3001").strip()
    ).rstrip("/")
    if not base:
        return False

    api_key = (
        os.environ.get("OPENBRAIN_API_KEY")
        or os.environ.get("AXON_AGENT_API_KEY")
        or os.environ.get("OPENBRAIN_AGENT_API_KEY")
    )
    if not api_key:
        return False

    body = json.dumps({
        "session_id": session_id,
        "workflow_run_id": workflow_run_id,
        "correlation_id": correlation_id,
        "images": images,
        "summary": summary,
    }).encode()
    req = urllib.request.Request(
        f"{base}/api/v1/workflows/deliverables",
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status in (200, 201, 202)
    except urllib.error.HTTPError:
        return False
