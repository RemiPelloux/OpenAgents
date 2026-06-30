"""Kanban worker lane for assignee ``opencode``.

The kanban dispatcher accepts a custom ``spawn_fn`` at ``dispatch_once`` time.
Use ``spawn_opencode_lane`` when wiring a board that assigns tasks to ``opencode``.
"""

from __future__ import annotations

import os
import subprocess
from typing import Any, Dict, Optional

from plugins.openos_engineering.opencode_runner import run_opencode_headless


def spawn_opencode_lane(
    task: Dict[str, Any],
    workspace: str,
    board: Optional[Dict[str, Any]] = None,
) -> Optional[int]:
    """Spawn OpenCode headless for a kanban task body.

    Returns None — OpenCode runs synchronously in this v0 lane; the caller
    should invoke ``kanban_complete`` / ``kanban_block`` based on the result dict
    stored in ``task`` metadata by the orchestrator profile.
    """
    del board
    body = str(task.get("body") or task.get("title") or "")
    ticket_id = os.environ.get("OPENTICKET_TICKET_ID")
    result = run_opencode_headless(body, cwd=workspace, ticket_id=ticket_id)
    task["_opencode_result"] = result
    return None
