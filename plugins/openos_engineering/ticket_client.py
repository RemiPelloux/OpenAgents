"""HTTP client for OpenTicket REST API."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Dict, Optional


def _api_url() -> str:
    return os.environ.get("OPENTICKET_API_URL", "http://localhost:3020").rstrip("/")


def _request_headers(correlation_id: Optional[str] = None) -> Dict[str, str]:
    headers: Dict[str, str] = {"Content-Type": "application/json"}
    token = os.environ.get("OPENTICKET_API_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    corr = correlation_id or os.environ.get("OPENTICKET_CORRELATION_ID", "").strip()
    if corr:
        headers["X-Correlation-Id"] = corr
    return headers


def get_ticket(ticket_id: str) -> Dict[str, Any]:
    url = f"{_api_url()}/v1/tickets/{urllib.request.quote(ticket_id, safe='')}"
    req = urllib.request.Request(url, headers=_request_headers())
    with urllib.request.urlopen(req, timeout=30) as resp:
        ticket = json.loads(resp.read().decode())
    correlation_id = ticket.get("correlation_id")
    if correlation_id:
        os.environ["OPENTICKET_CORRELATION_ID"] = str(correlation_id)
    return ticket


def update_ticket_status(
    ticket_id: str,
    to_status: str,
    *,
    reason: str = "",
    actor_profile: str = "developer",
    correlation_id: Optional[str] = None,
) -> Dict[str, Any]:
    url = f"{_api_url()}/v1/tickets/{urllib.request.quote(ticket_id, safe='')}/transition"
    body = json.dumps(
        {"to_status": to_status, "reason": reason, "actor_profile": actor_profile}
    ).encode()
    headers = _request_headers(correlation_id)
    req = urllib.request.Request(url, data=body, method="POST")
    for key, value in headers.items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode()
        raise RuntimeError(f"OpenTicket transition failed ({exc.code}): {detail}") from exc


def build_task_prompt(ticket: Dict[str, Any], mode: str) -> str:
    """Minimal task prompt — full ticket body is loaded by OpenCode via OPENTICKET_TICKET_ID."""
    key = ticket.get("ticket_key") or ticket.get("key", "")
    mode_line = {
        "implement": "Implement the ticket requirements and run tests.",
        "review": "Review the code changes for this ticket against acceptance criteria.",
        "test": "Run tests and verify acceptance criteria for this ticket.",
    }.get(mode, "Complete the requested work for this ticket.")
    return f"Ticket {key}: {mode_line}"


def build_ticket_prompt(ticket: Dict[str, Any], mode: str) -> str:
    """Backward-compatible alias; prefer build_task_prompt for invoke_opencode."""
    return build_task_prompt(ticket, mode)
