"""HTTP client for OpenTicket REST API."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Dict, Optional


def _api_url() -> str:
    return os.environ.get("OPENTICKET_API_URL", "http://localhost:3020").rstrip("/")


def get_ticket(ticket_id: str) -> Dict[str, Any]:
    url = f"{_api_url()}/v1/tickets/{urllib.request.quote(ticket_id, safe='')}"
    with urllib.request.urlopen(url, timeout=30) as resp:
        return json.loads(resp.read().decode())


def update_ticket_status(
    ticket_id: str,
    to_status: str,
    *,
    reason: str = "",
    actor_profile: str = "developer",
) -> Dict[str, Any]:
    url = f"{_api_url()}/v1/tickets/{urllib.request.quote(ticket_id, safe='')}/transition"
    body = json.dumps(
        {"to_status": to_status, "reason": reason, "actor_profile": actor_profile}
    ).encode()
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode()
        raise RuntimeError(f"OpenTicket transition failed ({exc.code}): {detail}") from exc


def build_ticket_prompt(ticket: Dict[str, Any], mode: str) -> str:
    key = ticket.get("ticket_key") or ticket.get("key", "")
    ac = ticket.get("acceptance_criteria") or []
    ac_block = "\n".join(f"- {item}" for item in ac) if ac else "- (none specified)"
    mode_line = {
        "implement": "Implement the ticket requirements and run tests.",
        "review": "Review the code changes for this ticket against acceptance criteria.",
        "test": "Run tests and verify acceptance criteria for this ticket.",
    }.get(mode, "Complete the requested work for this ticket.")

    return (
        f"Ticket {key}: {ticket.get('title', '')}\n\n"
        f"Description:\n{ticket.get('description', '')}\n\n"
        f"Acceptance criteria:\n{ac_block}\n\n"
        f"Task: {mode_line}"
    )
