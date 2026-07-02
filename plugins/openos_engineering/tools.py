"""OpenOS engineering plugin tools."""

from __future__ import annotations

import os
from typing import Any, Dict

from plugins.openos_engineering.opencode_runner import run_opencode_headless, verify_opencode_binary
from openagentui.codex_runner import run_codex_headless, verify_codex_binary
from plugins.openos_engineering.rec_client import emit_rec_event
from plugins.openos_engineering.ticket_client import (
    add_ticket_comment,
    build_task_prompt,
    get_ticket,
    patch_ticket,
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

    profile = "qa" if mode in {"review", "test"} else "developer"
    agent_run_id = str(args.get("run_id") or tid)

    emit_rec_event(
        "agent.run.started",
        {"ticket_id": tid, "ticket_key": ticket.get("ticket_key"), "mode": mode},
        correlation_id=correlation_id,
        agent_profile=profile,
        agent_run_id=agent_run_id,
        target_id=tid,
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
            agent_run_id=agent_run_id,
            target_id=tid,
        )
    else:
        emit_rec_event(
            "agent.run.failed",
            {
                "ticket_id": tid,
                "mode": mode,
                "exit_code": result.get("exit_code"),
            },
            correlation_id=correlation_id,
            agent_profile=profile,
            agent_run_id=agent_run_id,
            target_id=tid,
            severity="error",
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


SUBMIT_TICKET_RESULT_SCHEMA: Dict[str, Any] = {
    "name": "submit_ticket_result",
    "description": (
        "Submit research/ops deliverable for a ticket: PATCH deliverables, optional comment, "
        "transition to in_review for QA validation."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "ticket_id": {"type": "string", "description": "Ticket UUID or key"},
            "deliverables": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "kind": {"type": "string"},
                        "uri": {"type": "string"},
                        "summary": {"type": "string"},
                        "checksum": {"type": "string"},
                    },
                    "required": ["kind", "summary"],
                },
            },
            "comment": {"type": "string", "description": "Summary comment for reviewers"},
            "agent_run_id": {"type": "string", "description": "OpenAgents run id for provenance"},
            "move_to_in_review": {
                "type": "boolean",
                "description": "Transition ticket to in_review after submit (default true)",
            },
        },
        "required": ["ticket_id", "deliverables"],
    },
}


def handle_submit_ticket_result(args: Dict[str, Any]) -> str:
    ticket_id = str(args.get("ticket_id", "")).strip()
    if not ticket_id:
        return "Error: ticket_id is required"

    deliverables = args.get("deliverables")
    if not isinstance(deliverables, list) or not deliverables:
        return "Error: deliverables array required"

    ticket = get_ticket(ticket_id)
    tid = str(ticket.get("id") or ticket_id)
    correlation_id = str(ticket.get("correlation_id") or "") or None
    actor = os.environ.get("OPENTICKET_ACTOR_PROFILE", "researcher").strip() or "researcher"

    patch_fields: Dict[str, Any] = {"deliverables": deliverables}
    run_id = args.get("agent_run_id")
    if run_id:
        existing = ticket.get("linked_agent_run_ids") or []
        patch_fields["linked_agent_run_ids"] = list(dict.fromkeys([*existing, str(run_id)]))

    updated = patch_ticket(tid, patch_fields, correlation_id=correlation_id, actor_profile=actor)

    comment = str(args.get("comment") or "").strip()
    if comment:
        add_ticket_comment(tid, comment, correlation_id=correlation_id, actor_profile=actor)

    move = args.get("move_to_in_review", True)
    if move and updated.get("status") == "in_progress":
        update_ticket_status(
            tid,
            "in_review",
            reason="Research deliverable submitted",
            actor_profile=actor,
            correlation_id=correlation_id,
        )

    key = updated.get("ticket_key") or ticket.get("ticket_key") or tid
    return f"Submitted deliverable for ticket {key} ({len(deliverables)} item(s))."


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
