"""HTTP client for the OpenCRM REST API (mirrors ticket_client.py)."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional

from plugins.openos_mesh.contract_wrap import wrap_signed_hop

W1_MEETING_TO_CRM = "CC-W1-001"
W1_AGENT_FOLLOWUP = "CC-W1-003"
W1_PROSPECTION_TO_CRM = "CC-W1-004"


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


def _post_signed_hop(
    path: str,
    *,
    contract_id: str,
    producer: str,
    consumer: str,
    payload: Dict[str, Any],
    correlation_id: Optional[str] = None,
    prerequisites: Optional[list[str]] = None,
    goal_met: bool = True,
    signer_id: Optional[str] = None,
) -> Dict[str, Any]:
    envelope = wrap_signed_hop(
        contract_id=contract_id,
        producer=producer,
        consumer=consumer,
        payload=payload,
        correlation_id=correlation_id,
        prerequisites=prerequisites,
        goal_met=goal_met,
        signer_id=signer_id,
    )
    return _post(path, envelope, correlation_id)


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


def get_customer_context(
    *,
    account_id: Optional[str] = None,
    company_name: Optional[str] = None,
    org_id: Optional[str] = None,
    city: Optional[str] = None,
    contact_id: Optional[str] = None,
    email: Optional[str] = None,
) -> Dict[str, Any]:
    """Agent read — full commercial snapshot (MCP get_customer_context parity)."""
    params: Dict[str, str] = {}
    if account_id:
        params["account_id"] = account_id
    if company_name:
        params["company_name"] = company_name
    if org_id:
        params["org_id"] = org_id
    if city:
        params["city"] = city
    if contact_id:
        params["contact_id"] = contact_id
    if email:
        params["email"] = email
    return _get(f"/v1/query/customer?{urllib.parse.urlencode(params)}")


def list_hot_leads(
    *,
    org_id: Optional[str] = None,
    limit: int = 5,
    min_score: int = 50,
) -> Dict[str, Any]:
    """Agent read — ranked hot leads (MCP list_hot_leads parity)."""
    params: Dict[str, str] = {"limit": str(limit), "min_score": str(min_score)}
    if org_id:
        params["org_id"] = org_id
    return _get(f"/v1/leads/hot?{urllib.parse.urlencode(params)}")


def upsert_from_prospection_lead(
    *,
    video_url: str,
    company_name: str,
    city: Optional[str] = None,
    email: Optional[str] = None,
    tiktok_account: Optional[str] = None,
    correlation_id: Optional[str] = None,
) -> Dict[str, Any]:
    """CC-W1-004 — OpenTeam prospection lead → OpenCRM account/opportunity upsert."""
    body: Dict[str, Any] = {"video_url": video_url, "company_name": company_name}
    if city:
        body["city"] = city
    if email:
        body["email"] = email
    if tiktok_account:
        body["tiktok_account"] = tiktok_account
    if correlation_id:
        body["correlation_id"] = correlation_id
    return _post_signed_hop(
        "/v1/webhooks/openteam/prospection-lead",
        contract_id=W1_PROSPECTION_TO_CRM,
        producer="OpenTeam",
        consumer="OpenCRM",
        payload=body,
        correlation_id=correlation_id,
        signer_id="OpenTeam",
    )


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
    return _post_signed_hop(
        "/v1/staging",
        contract_id=W1_AGENT_FOLLOWUP,
        producer="OpenAgents",
        consumer="OpenCRM",
        payload=body,
        correlation_id=correlation_id,
        prerequisites=[W1_MEETING_TO_CRM],
        goal_met=False,
        signer_id="OpenAgents",
    )
