"""OpenOS engineering plugin tools."""

from __future__ import annotations

import os
from typing import Any, Dict

from plugins.openos_engineering.opencode_runner import run_opencode_headless
from plugins.openos_engineering.ticket_client import (
    build_ticket_prompt,
    get_ticket,
    update_ticket_status,
)

INVOKE_OPENCODE_SCHEMA: Dict[str, Any] = {
    "name": "invoke_opencode",
    "description": (
        "Delegate coding work to OpenOS OpenCode for a ticket. "
        "Fetches ticket context, runs headless OpenCode, returns summary."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "ticket_id": {
                "type": "string",
                "description": "Ticket UUID or key (e.g. OP-42)",
            },
            "mode": {
                "type": "string",
                "enum": ["implement", "review", "test"],
                "description": "Coding mode",
            },
            "cwd": {
                "type": "string",
                "description": "Working directory for OpenCode",
            },
            "max_turns": {
                "type": "integer",
                "description": "Max agent turns (default 50)",
            },
            "resume_session_id": {
                "type": "string",
                "description": "Optional OpenCode session to resume",
            },
        },
        "required": ["ticket_id"],
    },
}


def check_openos_engineering_available() -> bool:
    try:
        from plugins.openos_engineering.opencode_runner import resolve_opencode_binary

        resolve_opencode_binary()
        return True
    except RuntimeError:
        return False


def handle_invoke_opencode(args: Dict[str, Any]) -> str:
    ticket_id = str(args.get("ticket_id", "")).strip()
    if not ticket_id:
        return "Error: ticket_id is required"

    mode = str(args.get("mode") or "implement")
    cwd = args.get("cwd") or os.getcwd()
    max_turns = int(args.get("max_turns") or 50)
    resume = args.get("resume_session_id")

    ticket = get_ticket(ticket_id)
    tid = str(ticket.get("id") or ticket_id)
    prompt = build_ticket_prompt(ticket, mode)

    if mode == "implement" and ticket.get("status") == "todo":
        update_ticket_status(tid, "in_progress", actor_profile="developer")

    result = run_opencode_headless(
        prompt,
        cwd=cwd,
        ticket_id=tid,
        max_turns=max_turns,
        resume_session_id=resume,
    )

    if not result["ok"]:
        return (
            f"OpenCode failed (exit {result['exit_code']}):\n"
            f"{result.get('stderr') or result.get('summary')}"
        )

    return (
        f"OpenCode completed for ticket {ticket.get('ticket_key', tid)}.\n\n"
        f"{result['summary']}"
    )
