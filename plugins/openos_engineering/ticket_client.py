"""HTTP client for OpenTicket REST API."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

_delegate_subtasks: Dict[str, str] = {}


def register_delegate_subtask(session_id: str, ticket_id: str) -> None:
    _delegate_subtasks[session_id] = ticket_id


def resolve_ticket_id(
    *,
    explicit: Optional[str] = None,
    parent_session_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> Optional[str]:
    if explicit:
        return explicit.strip() or None
    sid = (session_id or "").strip()
    if not sid:
        try:
            from gateway.session_context import get_session_env

            sid = get_session_env("HERMES_SESSION_ID", "").strip()
        except ImportError:
            sid = ""
    if sid and sid in _delegate_subtasks:
        return _delegate_subtasks[sid]
    env_id = os.environ.get("OPENTICKET_TICKET_ID", "").strip()
    if env_id:
        return env_id
    if parent_session_id and parent_session_id in _delegate_subtasks:
        return _delegate_subtasks[parent_session_id]
    return None


def _api_url() -> str:
    return os.environ.get("OPENTICKET_API_URL", "http://localhost:3020").rstrip("/")


def _request_headers(correlation_id: Optional[str] = None) -> Dict[str, str]:
    headers: Dict[str, str] = {"Content-Type": "application/json"}
    token = os.environ.get("OPENTICKET_API_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    profile = os.environ.get("OPENTICKET_ACTOR_PROFILE", "").strip()
    if profile:
        headers["X-Actor-Profile"] = profile
    corr = correlation_id or os.environ.get("OPENTICKET_CORRELATION_ID", "").strip()
    if corr:
        headers["X-Correlation-Id"] = corr
    return headers


def create_subtask(
    parent_ticket_id: str,
    title: str,
    *,
    description: Optional[str] = None,
    acceptance_criteria: Optional[list[str]] = None,
    priority: Optional[str] = None,
    assignee_agent_profile: Optional[str] = None,
    execution_mode: Optional[str] = None,
    labels: Optional[list[str]] = None,
    correlation_id: Optional[str] = None,
) -> Dict[str, Any]:
    url = (
        f"{_api_url()}/v1/tickets/"
        f"{urllib.request.quote(parent_ticket_id, safe='')}/subtasks"
    )
    body: Dict[str, Any] = {"title": title}
    if description:
        body["description"] = description
    if acceptance_criteria:
        body["acceptance_criteria"] = acceptance_criteria
    if priority:
        body["priority"] = priority
    if assignee_agent_profile:
        body["assignee_agent_profile"] = assignee_agent_profile
    if execution_mode:
        body["execution_mode"] = execution_mode
    if labels:
        body["labels"] = labels

    payload = json.dumps(body).encode()
    headers = _request_headers(correlation_id)
    req = urllib.request.Request(url, data=payload, method="POST")
    for key, value in headers.items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            ticket = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode()
        raise RuntimeError(f"OpenTicket create_subtask failed ({exc.code}): {detail}") from exc

    corr = ticket.get("correlation_id")
    if corr:
        os.environ["OPENTICKET_CORRELATION_ID"] = str(corr)
    return ticket


def get_ticket(ticket_id: str) -> Dict[str, Any]:
    resolved = resolve_ticket_id(explicit=ticket_id) or ticket_id
    url = f"{_api_url()}/v1/tickets/{urllib.request.quote(resolved, safe='')}"
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


def patch_ticket(
    ticket_id: str,
    fields: Dict[str, Any],
    *,
    correlation_id: Optional[str] = None,
    actor_profile: Optional[str] = None,
) -> Dict[str, Any]:
    url = f"{_api_url()}/v1/tickets/{urllib.request.quote(ticket_id, safe='')}"
    headers = _request_headers(correlation_id)
    if actor_profile:
        headers["X-Actor-Profile"] = actor_profile
    body = json.dumps(fields).encode()
    req = urllib.request.Request(url, data=body, method="PATCH")
    for key, value in headers.items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode()
        raise RuntimeError(f"OpenTicket patch failed ({exc.code}): {detail}") from exc


def add_ticket_comment(
    ticket_id: str,
    body: str,
    *,
    correlation_id: Optional[str] = None,
    actor_profile: Optional[str] = None,
) -> Dict[str, Any]:
    url = f"{_api_url()}/v1/tickets/{urllib.request.quote(ticket_id, safe='')}/comments"
    headers = _request_headers(correlation_id)
    if actor_profile:
        headers["X-Actor-Profile"] = actor_profile
    payload = json.dumps({"body": body}).encode()
    req = urllib.request.Request(url, data=payload, method="POST")
    for key, value in headers.items():
        req.add_header(key, value)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())
