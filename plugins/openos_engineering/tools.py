"""OpenOS engineering plugin tools."""

from __future__ import annotations

import os
import json
from typing import Any, Dict

from plugins.openos_engineering.opencode_runner import run_opencode_headless, verify_opencode_binary
from openagentui.codex_runner import run_codex_headless, verify_codex_binary
from plugins.openos_engineering.brain_client import emit_agent_run_observation
from plugins.openos_engineering.rec_client import emit_rec_event
from plugins.openos_engineering.ticket_client import (
    add_ticket_comment,
    apply_task_context_env,
    build_task_prompt,
    create_subtask,
    create_ticket,
    get_ticket,
    patch_ticket,
    set_ticket_eta,
    update_ticket_status,
)
from plugins.openos_engineering.ticket_dod_loop import (
    format_dod_loop_result,
    run_ticket_dod_loop,
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


def invoke_opencode_once(
    *,
    ticket_id: str,
    mode: str = "implement",
    cwd: str | None = None,
    max_turns: int = 50,
    resume_session_id: str | None = None,
    run_id: str | None = None,
) -> Dict[str, Any]:
    """Single traced OpenCode session for a ticket."""
    ticket = get_ticket(ticket_id)
    tid = str(ticket.get("id") or ticket_id)
    correlation_id = str(ticket.get("correlation_id") or "") or None
    prompt = build_task_prompt(ticket, mode)
    workdir = cwd or os.getcwd()

    status = str(ticket.get("status") or "")
    if mode == "implement" and status == "backlog":
        ticket = update_ticket_status(
            tid,
            "todo",
            reason="OpenOrchestrator implementation task accepted",
            actor_profile="product_owner",
            correlation_id=correlation_id,
        )
        status = str(ticket.get("status") or "todo")
    if mode == "implement" and status == "todo":
        ticket = update_ticket_status(
            tid,
            "in_progress",
            reason="OpenCode implementation started",
            actor_profile="developer",
            correlation_id=correlation_id,
        )
    elif mode in {"review", "test"} and status == "in_review":
        ticket = update_ticket_status(
            tid,
            "qa",
            reason="OpenCode verification started",
            actor_profile="qa",
            correlation_id=correlation_id,
        )

    profile = "qa" if mode in {"review", "test"} else "developer"
    agent_run_id = str(run_id or tid)

    emit_rec_event(
        "agent.run.started",
        {"ticket_id": tid, "ticket_key": ticket.get("ticket_key"), "mode": mode},
        correlation_id=correlation_id,
        agent_profile=profile,
        agent_run_id=agent_run_id,
        target_id=tid,
    )
    emit_agent_run_observation(
        "agent.run.started",
        ticket_id=tid,
        ticket_key=ticket.get("ticket_key"),
        mode=mode,
        agent_profile=profile,
        agent_run_id=agent_run_id,
        correlation_id=correlation_id,
    )

    result = run_opencode_headless(
        prompt,
        cwd=workdir,
        ticket_id=tid,
        correlation_id=correlation_id,
        max_turns=max_turns,
        resume_session_id=resume_session_id,
        run_id=agent_run_id,
    )

    if result["ok"] and mode in {"review", "test"}:
        latest = get_ticket(tid)
        if latest.get("status") == "qa":
            update_ticket_status(
                tid,
                "done",
                reason="OpenCode verification passed",
                actor_profile="qa",
                correlation_id=correlation_id,
            )

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
        emit_agent_run_observation(
            "agent.run.completed",
            ticket_id=tid,
            ticket_key=ticket.get("ticket_key"),
            mode=mode,
            agent_profile=profile,
            agent_run_id=agent_run_id,
            correlation_id=correlation_id,
            summary=result["summary"],
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
        emit_agent_run_observation(
            "agent.run.failed",
            ticket_id=tid,
            ticket_key=ticket.get("ticket_key"),
            mode=mode,
            agent_profile=profile,
            agent_run_id=agent_run_id,
            correlation_id=correlation_id,
            exit_code=result.get("exit_code"),
        )

    return {
        "ok": result["ok"],
        "ticket_id": tid,
        "ticket_key": ticket.get("ticket_key"),
        "mode": mode,
        "summary": result.get("summary") or "",
        "stderr": result.get("stderr") or "",
        "exit_code": result.get("exit_code"),
        "files_edited": result.get("files_edited") or [],
        "resume_session_id": resume_session_id,
        "session_id": result.get("session_id"),
        "workdir": result.get("workdir"),
        "branch": result.get("branch"),
        "commit_sha": result.get("commit_sha"),
        "git_clean": result.get("git_clean"),
        "error": None if result["ok"] else "opencode_failed",
    }


def handle_invoke_opencode(args: Dict[str, Any], **_: Any) -> str:
    ticket_id = str(args.get("ticket_id", "")).strip()
    if not ticket_id:
        return "Error: ticket_id is required"

    result = invoke_opencode_once(
        ticket_id=ticket_id,
        mode=str(args.get("mode") or "implement"),
        cwd=args.get("cwd") or os.getcwd(),
        max_turns=int(args.get("max_turns") or 50),
        resume_session_id=args.get("resume_session_id"),
        run_id=str(args.get("run_id") or "") or None,
    )

    if not result["ok"]:
        return (
            f"OpenCode failed (exit {result.get('exit_code')}):\n"
            f"{result.get('stderr') or result.get('summary')}"
        )

    files_note = ""
    if result.get("files_edited"):
        files_note = f"\nFiles edited: {', '.join(result['files_edited'][:10])}"

    key = result.get("ticket_key") or result["ticket_id"]
    evidence = json.dumps({
        "ticket_id": result.get("ticket_id"),
        "ticket_key": result.get("ticket_key"),
        "session_id": result.get("session_id"),
        "workdir": result.get("workdir"),
        "branch": result.get("branch"),
        "commit_sha": result.get("commit_sha"),
        "git_clean": result.get("git_clean"),
        "files_edited": result.get("files_edited") or [],
        "exit_code": result.get("exit_code"),
        "stderr": result.get("stderr") or "",
        "summary": result.get("summary") or "",
    }, sort_keys=True)
    return (
        f"OpenCode completed for ticket {key}.\n"
        f"Ticket in_review transition is handled by OpenCode session-complete webhook.\n\n"
        f"{result['summary']}{files_note}\nEvidence: {evidence}"
    )


RUN_TICKET_DOD_LOOP_SCHEMA: Dict[str, Any] = {
    "name": "run_ticket_dod_loop",
    "description": (
        "Loop OpenCode sessions on a ticket until mesh DoD: developer until in_review, "
        "QA until done. Requires OpenTicket task trace."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "ticket_id": {"type": "string", "description": "Ticket UUID or key"},
            "agent_profile": {
                "type": "string",
                "enum": ["developer", "qa"],
                "description": "Which W4 phase to loop",
            },
            "cwd": {"type": "string", "description": "Working directory for OpenCode"},
            "max_iterations": {
                "type": "integer",
                "description": "Max OpenCode sessions (default OPENTICKET_DOD_MAX_ITERATIONS)",
            },
        },
        "required": ["ticket_id", "agent_profile"],
    },
}


def handle_run_ticket_dod_loop(args: Dict[str, Any], **_: Any) -> str:
    ticket_id = str(args.get("ticket_id", "")).strip()
    profile = str(args.get("agent_profile") or "").strip()
    if not ticket_id:
        return "Error: ticket_id is required"
    if profile not in {"developer", "qa"}:
        return "Error: agent_profile must be developer or qa"

    def _once(**kwargs: Any) -> Dict[str, Any]:
        return invoke_opencode_once(
            ticket_id=kwargs["ticket_id"],
            mode=kwargs["mode"],
            cwd=kwargs.get("cwd"),
            resume_session_id=kwargs.get("resume_session_id"),
        )

    loop = run_ticket_dod_loop(
        ticket_id,
        profile=profile,
        invoke_once=_once,
        cwd=args.get("cwd") or os.getcwd(),
        max_iterations=args.get("max_iterations"),
    )
    key = get_ticket(ticket_id).get("ticket_key") or ticket_id
    return format_dod_loop_result(loop, str(key))


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


def handle_submit_ticket_result(args: Dict[str, Any], **_: Any) -> str:
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


CREATE_SUBTASK_SCHEMA: Dict[str, Any] = {
    "name": "create_subtask",
    "description": (
        "Create a child OpenTicket task under a parent ticket. "
        "Inherits the parent correlation_id for mesh tracing."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "parent_ticket_id": {"type": "string", "description": "Parent ticket UUID or key"},
            "title": {"type": "string", "description": "Subtask title"},
            "description": {"type": "string"},
            "acceptance_criteria": {"type": "array", "items": {"type": "string"}},
            "priority": {"type": "string", "enum": ["low", "medium", "high", "critical"]},
            "assignee_agent_profile": {"type": "string"},
            "execution_mode": {
                "type": "string",
                "enum": ["code", "research", "ops", "security"],
            },
            "labels": {"type": "array", "items": {"type": "string"}},
            "eta": {"type": "string", "description": "ISO deadline; auto-default by priority if omitted"},
        },
        "required": ["parent_ticket_id", "title"],
    },
}


def handle_create_subtask(args: Dict[str, Any], **_: Any) -> str:
    parent_id = str(args.get("parent_ticket_id") or "").strip()
    title = str(args.get("title") or "").strip()
    if not parent_id or not title:
        return "Error: parent_ticket_id and title are required"

    parent = get_ticket(parent_id)
    correlation_id = str(parent.get("correlation_id") or "") or None
    subtask = create_subtask(
        str(parent.get("id") or parent_id),
        title,
        description=str(args.get("description") or "").strip() or None,
        acceptance_criteria=args.get("acceptance_criteria"),
        priority=args.get("priority"),
        assignee_agent_profile=args.get("assignee_agent_profile"),
        execution_mode=args.get("execution_mode"),
        labels=args.get("labels"),
        correlation_id=correlation_id,
        eta=args.get("eta"),
    )
    key = subtask.get("ticket_key") or subtask.get("id")
    eta_line = subtask.get("eta") or (subtask.get("metadata") or {}).get("eta")
    eta_note = f" eta={eta_line}" if eta_line else ""
    return f"Created subtask {key} under {parent.get('ticket_key', parent_id)}.{eta_note}"


CREATE_TICKET_SCHEMA: Dict[str, Any] = {
    "name": "create_ticket",
    "description": (
        "Create a new OpenTicket story/bug/task (CC-W4-001 signed mesh hop). "
        "Requires project_id and acceptance_criteria for PO workflows."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "project_id": {"type": "string", "description": "OpenTicket project UUID"},
            "type": {
                "type": "string",
                "enum": ["story", "bug", "task", "epic", "spike"],
            },
            "title": {"type": "string"},
            "description": {"type": "string"},
            "acceptance_criteria": {"type": "array", "items": {"type": "string"}},
            "priority": {"type": "string", "enum": ["low", "medium", "high", "critical"]},
            "assignee_agent_profile": {"type": "string"},
            "execution_mode": {
                "type": "string",
                "enum": ["code", "research", "ops", "security"],
            },
            "labels": {"type": "array", "items": {"type": "string"}},
            "components": {"type": "array", "items": {"type": "string"}},
            "correlation_id": {"type": "string", "description": "Optional mesh correlation id"},
            "eta": {"type": "string", "description": "ISO deadline; uses mission eta or auto-default"},
        },
        "required": ["project_id", "type", "title"],
    },
}


def handle_create_ticket(args: Dict[str, Any], **_: Any) -> str:
    project_id = str(args.get("project_id") or "").strip()
    ticket_type = str(args.get("type") or "").strip()
    title = str(args.get("title") or "").strip()
    if not project_id or not ticket_type or not title:
        return "Error: project_id, type, and title are required"

    correlation_id = str(args.get("correlation_id") or "").strip() or None
    ticket = create_ticket(
        project_id,
        ticket_type,
        title,
        description=str(args.get("description") or "").strip() or None,
        acceptance_criteria=args.get("acceptance_criteria"),
        priority=args.get("priority"),
        assignee_agent_profile=args.get("assignee_agent_profile"),
        execution_mode=args.get("execution_mode"),
        labels=args.get("labels"),
        components=args.get("components"),
        correlation_id=correlation_id,
        eta=args.get("eta"),
    )
    key = ticket.get("ticket_key") or ticket.get("id")
    corr = ticket.get("correlation_id") or correlation_id or ""
    eta_line = ticket.get("eta") or (ticket.get("metadata") or {}).get("eta")
    eta_note = f" eta={eta_line}" if eta_line else ""
    return f"Created ticket {key} (correlation_id={corr}).{eta_note}"


SET_TICKET_ETA_SCHEMA: Dict[str, Any] = {
    "name": "set_ticket_eta",
    "description": "Set or clear OpenTicket ETA (ISO datetime).",
    "parameters": {
        "type": "object",
        "properties": {
            "ticket_id": {"type": "string", "description": "Ticket UUID or key"},
            "eta": {
                "type": ["string", "null"],
                "description": "ISO deadline, or null to clear",
            },
        },
        "required": ["ticket_id", "eta"],
    },
}


def handle_set_ticket_eta(args: Dict[str, Any], **_: Any) -> str:
    ticket_id = str(args.get("ticket_id") or "").strip()
    if not ticket_id:
        return "Error: ticket_id is required"
    if "eta" not in args:
        return "Error: eta is required (use null to clear)"

    ticket = get_ticket(ticket_id)
    tid = str(ticket.get("id") or ticket_id)
    correlation_id = str(ticket.get("correlation_id") or "") or None
    eta = args.get("eta")
    updated = set_ticket_eta(
        tid,
        None if eta is None else str(eta),
        correlation_id=correlation_id,
    )
    key = updated.get("ticket_key") or ticket.get("ticket_key") or tid
    shown = updated.get("eta") or "cleared"
    return f"Updated ETA for {key}: {shown}"


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


def handle_invoke_codex(args: Dict[str, Any], **_: Any) -> str:
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
