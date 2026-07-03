"""Loop OpenCode sessions on a ticket until mesh Definition of Done."""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from plugins.openos_engineering.ticket_client import get_ticket

DEFAULT_MAX_ITERATIONS = 8


def max_dod_iterations(override: Optional[int] = None) -> int:
    if override is not None and override > 0:
        return int(override)
    raw = os.environ.get("OPENTICKET_DOD_MAX_ITERATIONS", str(DEFAULT_MAX_ITERATIONS))
    try:
        parsed = int(raw)
        return parsed if parsed > 0 else DEFAULT_MAX_ITERATIONS
    except ValueError:
        return DEFAULT_MAX_ITERATIONS


def is_ticket_dod(ticket: Dict[str, Any]) -> bool:
    return str(ticket.get("status") or "") == "done"


def dev_phase_complete(ticket: Dict[str, Any]) -> bool:
    return str(ticket.get("status") or "") in {"in_review", "qa", "done"}


def qa_phase_complete(ticket: Dict[str, Any]) -> bool:
    return is_ticket_dod(ticket)


def opencode_mode_for_profile(profile: str, ticket: Dict[str, Any]) -> Optional[str]:
    status = str(ticket.get("status") or "")
    if profile == "developer":
        if dev_phase_complete(ticket):
            return None
        return "implement"
    if profile == "qa":
        if qa_phase_complete(ticket):
            return None
        if status == "qa":
            return "test"
        if status in {"in_review", "in_progress"}:
            return "review"
        return None
    return None


def run_ticket_dod_loop(
    ticket_id: str,
    *,
    profile: str,
    invoke_once,
    cwd: Optional[str] = None,
    max_iterations: Optional[int] = None,
) -> Dict[str, Any]:
    """Run invoke_opencode repeatedly until ticket DoD for profile phase."""
    limit = max_dod_iterations(max_iterations)
    tid = ticket_id.strip()
    resume: Optional[str] = None
    summaries: List[str] = []
    iterations = 0

    while iterations < limit:
        ticket = get_ticket(tid)
        tid = str(ticket.get("id") or tid)

        if is_ticket_dod(ticket):
            return {
                "ok": True,
                "iterations": iterations,
                "ticket_status": "done",
                "summaries": summaries,
            }

        mode = opencode_mode_for_profile(profile, ticket)
        if mode is None:
            return {
                "ok": True,
                "iterations": iterations,
                "ticket_status": str(ticket.get("status") or ""),
                "summaries": summaries,
            }

        result = invoke_once(
            ticket_id=tid,
            mode=mode,
            cwd=cwd,
            resume_session_id=resume,
        )
        iterations += 1
        if result.get("summary"):
            summaries.append(str(result["summary"])[:500])

        if not result.get("ok"):
            return {
                "ok": False,
                "iterations": iterations,
                "ticket_status": str(ticket.get("status") or ""),
                "error": result.get("error") or "opencode_failed",
                "summaries": summaries,
            }

        refreshed = get_ticket(tid)
        tid = str(refreshed.get("id") or tid)
        resume = result.get("resume_session_id")

    ticket = get_ticket(tid)
    return {
        "ok": False,
        "iterations": iterations,
        "ticket_status": str(ticket.get("status") or ""),
        "error": "max_iterations_exceeded",
        "summaries": summaries,
    }


def format_dod_loop_result(result: Dict[str, Any], ticket_key: str) -> str:
    status = result.get("ticket_status") or "unknown"
    iters = result.get("iterations", 0)
    if result.get("ok"):
        tail = (result.get("summaries") or [""])[-1]
        return (
            f"Ticket DoD loop complete for {ticket_key} after {iters} OpenCode session(s). "
            f"Status: {status}.\n\n{tail}"
        )
    err = result.get("error", "failed")
    return (
        f"Ticket DoD loop stopped for {ticket_key} after {iters} session(s). "
        f"Status: {status}. Reason: {err}."
    )
