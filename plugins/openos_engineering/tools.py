"""OpenOS engineering plugin tools."""

from __future__ import annotations

import os
from typing import Any, Dict

from plugins.openos_engineering.opencode_runner import run_opencode_headless, verify_opencode_binary
from openagentui.codex_runner import run_codex_headless, verify_codex_binary
from plugins.openos_engineering.rec_client import emit_rec_event
from plugins.openos_engineering.ticket_client import (
    build_task_prompt,
    get_ticket,
    update_ticket_status,
)

INVOKE_OPENCODE_SCHEMA: Dict[str, Any] = {
    "name": "invoke_opencode",
    "description": (
        "Delegate coding work to OpenOS OpenCode for a ticket. "
        "OpenCode loads ticket context via OPENTICKET_TICKET_ID; returns summary."
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
    return verify_opencode_binary()


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
    correlation_id = str(ticket.get("correlation_id") or "") or None
    prompt = build_task_prompt(ticket, mode)

    if mode == "implement" and ticket.get("status") == "todo":
        update_ticket_status(
            tid,
            "in_progress",
            actor_profile="developer",
            correlation_id=correlation_id,
        )

    result = run_opencode_headless(
        prompt,
        cwd=cwd,
        ticket_id=tid,
        correlation_id=correlation_id,
        max_turns=max_turns,
        resume_session_id=resume,
    )

    profile = "qa" if mode in {"review", "test"} else "developer"

    if result["ok"]:
        emit_rec_event(
            "agent.run.completed",
            {
                "ticket_id": tid,
                "ticket_key": ticket.get("ticket_key"),
                "mode": mode,
                "summary": result["summary"][:500],
            },
            correlation_id=correlation_id,
            agent_profile=profile,
            target_id=tid,
        )

    if not result["ok"]:
        return (
            f"OpenCode failed (exit {result['exit_code']}):\n"
            f"{result.get('stderr') or result.get('summary')}"
        )

    files_note = ""
    if result.get("files_edited"):
        files_note = f"\nFiles edited: {', '.join(result['files_edited'][:10])}"

    return (
        f"OpenCode completed for ticket {ticket.get('ticket_key', tid)}.\n"
        f"Ticket in_review transition is handled by OpenCode session-complete webhook.\n\n"
        f"{result['summary']}{files_note}"
    )


INVOKE_CODEX_SCHEMA: Dict[str, Any] = {
    "name": "invoke_codex",
    "description": (
        "Delegate coding work to OpenAI Codex CLI (`codex exec`). "
        "Use for headless implementation when OpenCode is not available."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "prompt": {"type": "string", "description": "Task instructions for Codex"},
            "cwd": {"type": "string", "description": "Working directory"},
            "full_auto": {
                "type": "boolean",
                "description": "Pass --full-auto to codex exec (default false)",
            },
            "sandbox": {
                "type": "string",
                "description": "Codex sandbox mode (default workspace-write)",
            },
        },
        "required": ["prompt"],
    },
}


def check_codex_available() -> bool:
    return verify_codex_binary()


def handle_invoke_codex(args: Dict[str, Any]) -> str:
    prompt = str(args.get("prompt") or "").strip()
    if not prompt:
        return "Error: prompt is required"

    cwd = args.get("cwd") or os.getcwd()
    full_auto = bool(args.get("full_auto"))
    sandbox = str(args.get("sandbox") or "workspace-write")

    result = run_codex_headless(
        prompt,
        cwd=cwd,
        sandbox=sandbox,
        full_auto=full_auto,
    )

    if not result["ok"]:
        return (
            f"Codex failed (exit {result['exit_code']}):\n"
            f"{result.get('stderr') or result.get('summary')}"
        )

    return f"Codex completed in {result['cwd']}.\n\n{result['summary']}"
