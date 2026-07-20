"""HTTP client for OpenTicket REST API."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

from plugins.openos_mesh.contract_wrap import wrap_signed_hop

_delegate_subtasks: Dict[str, str] = {}

W4_PO_CREATE = "CC-W4-001"
W4_PO_PRODUCER = "OpenAgents [product_owner]"
W4_TICKET_CONSUMER = "OpenTicket"


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
    profile = os.environ.get("OPENTICKET_ACTOR_PROFILE", "product_owner").strip()
    if profile:
        headers["X-Actor-Profile"] = profile
    corr = correlation_id or os.environ.get("OPENTICKET_CORRELATION_ID", "").strip()
    if corr:
        headers["X-Correlation-Id"] = corr
    return headers


def _post_json(path: str, body: Dict[str, Any], correlation_id: Optional[str] = None) -> Dict[str, Any]:
    url = f"{_api_url()}{path}"
    payload = json.dumps(body).encode()
    headers = _request_headers(correlation_id)
    req = urllib.request.Request(url, data=payload, method="POST")
    for key, value in headers.items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode()
        raise RuntimeError(f"OpenTicket POST {path} failed ({exc.code}): {detail}") from exc


def _remember_correlation(ticket: Dict[str, Any]) -> Dict[str, Any]:
    corr = ticket.get("correlation_id")
    if corr:
        os.environ["OPENTICKET_CORRELATION_ID"] = str(corr)
    return ticket


def apply_task_context_env(ctx: Dict[str, Any]) -> None:
    """Propagate mesh task_context into OpenTicket env for ticket tools."""
    correlation_id = ctx.get("correlation_id")
    if correlation_id:
        os.environ["OPENTICKET_CORRELATION_ID"] = str(correlation_id)
    ticket_id = ctx.get("ticket_id")
    if ticket_id:
        os.environ["OPENTICKET_TICKET_ID"] = str(ticket_id)
    eta = ctx.get("eta") or ctx.get("deadline")
    if eta:
        os.environ["OPENTICKET_MISSION_ETA"] = str(eta)
    criteria = ctx.get("acceptance_criteria")
    if isinstance(criteria, list) and criteria:
        os.environ["OPENTICKET_ACCEPTANCE_CRITERIA"] = json.dumps(criteria)
    schema_url = ctx.get("response_schema_url")
    if isinstance(schema_url, str) and schema_url.strip():
        os.environ["OPENORCHESTRATOR_RESPONSE_SCHEMA_URL"] = schema_url.strip()
    schema_id = ctx.get("response_schema_id")
    if isinstance(schema_id, str) and schema_id.strip():
        os.environ["OPENORCHESTRATOR_RESPONSE_SCHEMA_ID"] = schema_id.strip()


def format_orchestrator_context(ctx: Dict[str, Any]) -> str:
    """Build mesh context block for agent system prompt."""
    if not ctx:
        return ""
    parts: List[str] = []
    brain = ctx.get("brain_summary")
    if isinstance(brain, str) and brain.strip():
        parts.append(f"Brain guidance:\n{brain.strip()[:1200]}")
    criteria = ctx.get("acceptance_criteria")
    if isinstance(criteria, list) and criteria:
        parts.append("Acceptance criteria:\n- " + "\n- ".join(str(c) for c in criteria))
    trace = ctx.get("openrec_trace")
    if trace:
        parts.append(f"Prior mesh trace:\n{json.dumps(trace)[:800]}")
    plan_obj = ctx.get("plan_objective")
    if isinstance(plan_obj, str) and plan_obj.strip():
        parts.append(f"Plan objective: {plan_obj.strip()}")
    goal_class = ctx.get("goal_class")
    if isinstance(goal_class, str) and goal_class.strip():
        parts.append(f"Goal class: {goal_class.strip()}")
    playbooks = ctx.get("playbooks")
    if isinstance(playbooks, list) and playbooks:
        names = []
        for book in playbooks[:5]:
            if isinstance(book, dict) and book.get("profile"):
                names.append(str(book["profile"]))
        if names:
            parts.append(f"Playbooks: {', '.join(names)}")
    return "\n\n".join(parts)


def merge_orchestrator_instructions(
    instructions: Optional[str],
    ctx: Dict[str, Any],
) -> Optional[str]:
    """Append task_context when instructions lack OpenOrchestrator context."""
    block = format_orchestrator_context(ctx)
    if not block:
        return instructions
    marker = "--- OpenOrchestrator context ---"
    if instructions and marker in instructions:
        return instructions
    if instructions:
        return f"{instructions}\n\n{marker}\n{block}"
    return block

def _mission_eta() -> Optional[str]:
    return os.environ.get("OPENTICKET_MISSION_ETA", "").strip() or None


def create_ticket(
    project_id: str,
    ticket_type: str,
    title: str,
    *,
    description: Optional[str] = None,
    acceptance_criteria: Optional[List[str]] = None,
    priority: Optional[str] = None,
    assignee_agent_profile: Optional[str] = None,
    execution_mode: Optional[str] = None,
    labels: Optional[List[str]] = None,
    components: Optional[List[str]] = None,
    epic_id: Optional[str] = None,
    sprint_id: Optional[str] = None,
    parent_ticket_id: Optional[str] = None,
    correlation_id: Optional[str] = None,
    eta: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a ticket via CC-W4-001 signed ContractEnvelope."""
    payload: Dict[str, Any] = {
        "project_id": project_id,
        "type": ticket_type,
        "title": title,
    }
    if description:
        payload["description"] = description
    if acceptance_criteria:
        payload["acceptance_criteria"] = acceptance_criteria
    if priority:
        payload["priority"] = priority
    if assignee_agent_profile:
        payload["assignee_agent_profile"] = assignee_agent_profile
    if execution_mode:
        payload["execution_mode"] = execution_mode
    if labels:
        payload["labels"] = labels
    if components:
        payload["components"] = components
    if epic_id:
        payload["epic_id"] = epic_id
    if sprint_id:
        payload["sprint_id"] = sprint_id
    if parent_ticket_id:
        payload["parent_ticket_id"] = parent_ticket_id
    resolved_eta = eta or _mission_eta()
    if resolved_eta:
        payload["eta"] = resolved_eta

    envelope = wrap_signed_hop(
        contract_id=W4_PO_CREATE,
        producer=W4_PO_PRODUCER,
        consumer=W4_TICKET_CONSUMER,
        payload=payload,
        correlation_id=correlation_id,
        signer_id="OpenAgents",
    )
    ticket = _post_json("/v1/tickets", envelope, correlation_id)
    return _remember_correlation(ticket)


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
    eta: Optional[str] = None,
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
    resolved_eta = eta or _mission_eta()
    if resolved_eta:
        body["eta"] = resolved_eta

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
    """Task prompt for invoke_opencode — includes OpenProtocol when implementing."""
    key = ticket.get("ticket_key") or ticket.get("key", "")
    ticket_ref = key or str(ticket.get("id") or "task")
    mode_line = {
        "implement": "Implement the ticket requirements and run tests.",
        "review": "Review the code changes for this ticket against acceptance criteria.",
        "test": "Run tests and verify acceptance criteria for this ticket.",
    }.get(mode, "Complete the requested work for this ticket.")
    base = f"Ticket {ticket_ref}: {mode_line}"
    if mode != "implement":
        return base
    if os.environ.get("OPENOS_WORKSPACE_ROOT", "").strip():
        return (
            f"{base}\n\n"
            "OpenProtocol CODER (managed OpenOS worktree):\n"
            "1. The runtime already created and checked out an isolated agent/ branch; "
            "do not fetch, pull, switch branches, create another worktree, or push\n"
            "2. Inspect the current worktree and implement only the ticket requirements\n"
            "3. Run the ticket test command and any relevant typecheck/build checks\n"
            "4. Stage only the files you changed and commit one logical conventional commit\n"
            "5. End with the current branch, commit SHA, touched files, checks, and risks"
        )
    return (
        f"{base}\n\n"
        "OpenProtocol CODER (mandatory — you are spawned by OpenAgents):\n"
        f"1. git fetch && git checkout main && git pull --ff-only origin main\n"
        f"2. git checkout -b agent/{ticket_ref}/<short-slug>\n"
        "3. Surgical changes only; run project test + typecheck + build\n"
        "4. Commit: <type>(<scope>): <subject ≤72 chars> — one logical change\n"
        "5. git push -u origin HEAD (feature branch only — never push main)\n"
        "6. End with OpenProtocol handoff (branch, checks, risk) for integrator\n"
        "Git auth: GITHUB_TOKEN or git-credentials only."
    )


def build_ticket_prompt(ticket: Dict[str, Any], mode: str) -> str:
    """Backward-compatible alias; prefer build_task_prompt for invoke_opencode."""
    return build_task_prompt(ticket, mode)


def set_ticket_eta(
    ticket_id: str,
    eta: Optional[str],
    *,
    correlation_id: Optional[str] = None,
    actor_profile: Optional[str] = None,
) -> Dict[str, Any]:
    fields: Dict[str, Any] = {"eta": eta}
    return patch_ticket(
        ticket_id,
        fields,
        correlation_id=correlation_id,
        actor_profile=actor_profile,
    )


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
