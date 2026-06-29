"""Session resume behaviour on interactive CLI/TUI launch."""

from __future__ import annotations

import os
import sys
from typing import Any

_VALID_MODES = frozenset({"new", "last", "prompt"})


def normalize_session_on_launch(value: Any) -> str:
    """Return a validated session_on_launch mode."""
    mode = str(value or "new").strip().lower()
    if mode not in _VALID_MODES:
        return "new"
    return mode


def _is_non_interactive_launch(args: Any) -> bool:
    if getattr(args, "query", None) or getattr(args, "quiet", False):
        return True
    try:
        return not sys.stdin.isatty()
    except (AttributeError, ValueError, OSError):
        return True


def _list_launch_sessions(*, source: str, limit: int) -> list[dict]:
    try:
        from openagents_state import SessionDB

        db = SessionDB()
        try:
            return db.list_sessions_rich(
                source=source,
                exclude_sources=["tool"],
                limit=max(1, int(limit)),
            )
        finally:
            db.close()
    except Exception:
        return []


def apply_session_on_launch(args: Any, *, use_tui: bool) -> None:
    """Apply ``display.session_on_launch`` when no explicit --resume/-c was given."""
    if getattr(args, "resume", None) or getattr(args, "continue_last", None):
        return
    if _is_non_interactive_launch(args):
        return

    try:
        from openagents_cli.config import load_config

        display = load_config().get("display") or {}
    except Exception:
        display = {}

    # TUI keeps its dedicated toggle for backward compatibility.
    if use_tui and bool(display.get("tui_auto_resume_recent")):
        mode = "last"
    else:
        mode = normalize_session_on_launch(display.get("session_on_launch", "new"))

    if mode == "new":
        return

    from openagents_cli.main import _resolve_last_session, _session_browse_picker

    source = "tui" if use_tui else "cli"
    limit = int(display.get("startup_sessions_limit", 50) or 50)

    if mode == "last":
        last_id = _resolve_last_session(source=source)
        if not last_id and use_tui:
            last_id = _resolve_last_session(source="cli")
        if last_id:
            args.resume = last_id
        return

    if mode == "prompt":
        sessions = _list_launch_sessions(source=source, limit=limit)
        if not sessions and use_tui:
            sessions = _list_launch_sessions(source="cli", limit=limit)
        if not sessions:
            return
        selected = _session_browse_picker(sessions)
        if selected:
            args.resume = selected
            os.environ["HERMES_SKIP_STARTUP_ANIMATION"] = "1"
        return
