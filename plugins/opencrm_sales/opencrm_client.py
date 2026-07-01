"""HTTP client for the OpenCRM REST API (mirrors ticket_client.py)."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional


def _api_url() -> str:
    return os.environ.get("OPENCRM_API_URL", "http://localhost:3010").rstrip("/")


def _headers(correlation_id: Optional[str] = None) -> Dict[str, str]:
    headers = {"Content-Type": "application/json"}
    corr = correlation_id or os.environ.get("OPENCRM_CORRELATION_ID", "").strip()
    if corr:
        headers["X-Correlation-Id"] = corr
    return headers


def _get(path: str) -> Dict[str, Any]:
    url = f"{_api_url()}{path}"
    req = urllib.request.Request(url, headers=_headers())
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def _post(path: str, body: Dict[str, Any], correlation_id: Optional[str] = None) -> Dict[str, Any]:
    url = f"{_api_url()}{path}"
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    for key, value in _headers(correlation_id).items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode()
        raise RuntimeError(f"OpenCRM API failed ({exc.code}): {detail}") from exc


def search_accounts(company_name: str, city: Optional[str] = None) -> Dict[str, Any]:
    params = {"company_name": company_name}
    if city:
        params["city"] = city
    return _get(f"/v1/accounts?{urllib.parse.urlencode(params)}")


def check_account_duplicate(company_name: str, city: Optional[str] = None) -> Dict[str, Any]:
    """CC-W1-006 — fuzzy duplicate check used by prospection + sales skills.

    Returns `{"duplicate": False}` (instead of raising) when OpenCRM is unreachable,
    so callers that treat OpenCRM as an optional signal degrade gracefully.
    """
    try:
        result = search_accounts(company_name, city)
    except (urllib.error.URLError, TimeoutError, OSError):
        return {"duplicate": False, "opencrm_unavailable": True}
    accounts = result.get("accounts", [])
    return {"duplicate": len(accounts) > 0, "account": accounts[0] if accounts else None}


def get_account(account_id: str) -> Dict[str, Any]:
    return _get(f"/v1/accounts/{urllib.parse.quote(account_id, safe='')}")


def upsert_from_prospection_lead(
    *,
    video_url: str,
    company_name: str,
    city: Optional[str] = None,
    email: Optional[str] = None,
    tiktok_account: Optional[str] = None,
    correlation_id: Optional[str] = None,
) -> Dict[str, Any]:
    """CC-W1-004 — OpenTeam prospection lead → OpenCRM account/opportunity upsert.

    Called by the agent right after `enrich_tiktok_lead`, so OpenCRM (source of truth for
    commercial state) holds the account even before/in parallel with OpenPro provisioning.
    """
    body: Dict[str, Any] = {"video_url": video_url, "company_name": company_name}
    if city:
        body["city"] = city
    if email:
        body["email"] = email
    if tiktok_account:
        body["tiktok_account"] = tiktok_account
    if correlation_id:
        body["correlation_id"] = correlation_id
    return _post("/v1/webhooks/openteam/prospection-lead", body, correlation_id)


def propose_crm_update(
    entity_type: str,
    entity_id: str,
    payload: Dict[str, Any],
    *,
    org_id: str,
    agent_profile: str = "sales-followup",
    correlation_id: Optional[str] = None,
) -> Dict[str, Any]:
    body = {
        "org_id": org_id,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "payload": payload,
        "requested_by": {"type": "agent", "id": "openagents", "agent_profile": agent_profile},
    }
    if correlation_id:
        body["correlation_id"] = correlation_id
    return _post("/v1/staging", body, correlation_id)
