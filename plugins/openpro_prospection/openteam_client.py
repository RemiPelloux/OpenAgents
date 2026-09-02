"""HTTP client for OpenTeam prospection status callbacks."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional


def _api_url() -> str:
    return os.environ.get("OPENTEAM_API_URL", "http://localhost:8050").rstrip("/")


def _headers(correlation_id: Optional[str] = None) -> Dict[str, str]:
    headers = {"Content-Type": "application/json"}
    key = os.environ.get(
        "PROSPECTION_API_KEY", os.environ.get("WEBHOOK_SECRET", "")
    ).strip()
    if key:
        headers["X-Prospection-Api-Key"] = key
    corr = correlation_id or os.environ.get("PROSPECTION_CORRELATION_ID", "").strip()
    if corr:
        headers["X-Correlation-Id"] = corr
    return headers


def report_prospection_status(
    payload: Dict[str, Any], correlation_id: Optional[str] = None
) -> Dict[str, Any]:
    url = f"{_api_url()}/api/v1/prospection/leads/status"
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    for key, value in _headers(correlation_id).items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode()
        raise RuntimeError(
            f"OpenTeam prospection status failed ({exc.code}): {detail}"
        ) from exc
