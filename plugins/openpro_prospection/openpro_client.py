"""HTTP client for OpenPro agent prospection API."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional


def _api_url() -> str:
    return os.environ.get("OPENPRO_API_URL", "https://api.openpro.ai").rstrip("/")


def _headers(correlation_id: Optional[str] = None) -> Dict[str, str]:
    headers = {"Content-Type": "application/json"}
    key = os.environ.get("OPENPRO_AGENT_API_KEY", "").strip()
    if key:
        headers["X-Agent-Api-Key"] = key
    corr = correlation_id or os.environ.get("PROSPECTION_CORRELATION_ID", "").strip()
    if corr:
        headers["X-Correlation-Id"] = corr
    return headers


def _post(path: str, body: Dict[str, Any], correlation_id: Optional[str] = None) -> Dict[str, Any]:
    url = f"{_api_url()}{path}"
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    for key, value in _headers(correlation_id).items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode()
        raise RuntimeError(f"OpenPro API failed ({exc.code}): {detail}") from exc


def check_company_duplicate(name: str, city: str, correlation_id: Optional[str] = None) -> Dict[str, Any]:
    return _post(
        "/api/agent/companies/check-duplicate",
        {"name": name, "city": city},
        correlation_id,
    )


def provision_from_lead(payload: Dict[str, Any], correlation_id: Optional[str] = None) -> Dict[str, Any]:
    return _post("/api/agent/companies/provision-from-lead", payload, correlation_id)


def create_job_post(payload: Dict[str, Any], correlation_id: Optional[str] = None) -> Dict[str, Any]:
    return _post("/api/agent/job-posts", payload, correlation_id)


def send_prospect_email(payload: Dict[str, Any], correlation_id: Optional[str] = None) -> Dict[str, Any]:
    return _post("/api/agent/outreach/email", payload, correlation_id)


def send_tiktok_dm(payload: Dict[str, Any], correlation_id: Optional[str] = None) -> Dict[str, Any]:
    return _post("/api/agent/outreach/tiktok-dm", payload, correlation_id)
