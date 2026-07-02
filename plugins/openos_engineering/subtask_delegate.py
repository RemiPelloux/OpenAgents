"""Spawn OpenTicket subtasks when delegate_task splits work on a ticket."""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

from plugins.openos_engineering.ticket_client import (
    apply_task_context_env,
    create_subtask,
    get_ticket,
    register_delegate_subtask,
    resolve_ticket_id,
)

logger = logging.getLogger(__name__)


def _spawn_enabled() -> bool:
    flag = os.environ.get("OPENOS_DELEGATE_SPAWN_SUBTASK", "1").strip().lower()
    return flag not in {"0", "false", "no", "off"}


def maybe_spawn_delegate_subtask(
    *,
    parent_session_id: Optional[str],
    child_session_id: Optional[str],
    child_goal: str,
    child_role: str = "leaf",
) -> Optional[Dict[str, Any]]:
    """Create a child ticket for a delegated subagent when a parent ticket is active."""
    if not _spawn_enabled():
        return None
    if not child_session_id or child_role != "leaf":
        return None

    parent_ticket_id = resolve_ticket_id(parent_session_id=parent_session_id)
    if not parent_ticket_id:
        return None

    title = (child_goal or "Delegated subtask").strip()[:200]
    try:
        parent = get_ticket(parent_ticket_id)
        correlation_id = str(parent.get("correlation_id") or "") or None
        subtask = create_subtask(
            parent_ticket_id=str(parent.get("id") or parent_ticket_id),
            title=title,
            description=f"Auto-created from delegate_task.\n\nGoal: {child_goal}",
            correlation_id=correlation_id,
        )
    except Exception as exc:
        logger.warning("OpenTicket subtask spawn failed: %s", exc)
        return None

    subtask_id = str(subtask.get("id") or "")
    if subtask_id:
        register_delegate_subtask(child_session_id, subtask_id)
    return subtask


def on_subagent_start(**kwargs: Any) -> None:
    maybe_spawn_delegate_subtask(
        parent_session_id=kwargs.get("parent_session_id"),
        child_session_id=kwargs.get("child_session_id"),
        child_goal=str(kwargs.get("child_goal") or ""),
        child_role=str(kwargs.get("child_role") or "leaf"),
    )
