<<<<<<< HEAD
"""
OpenAgents CLI - Unified command-line interface for OpenAgents.

Provides subcommands for:
- openagents chat          - Interactive chat
- openagents gateway       - Run gateway in foreground
- openagents gateway start - Start gateway service
- openagents gateway stop  - Stop gateway service
- openagents setup         - Interactive setup wizard
- openagents status        - Show status of all components
- openagents cron          - Manage cron jobs
"""
||||||| cf299e9a01
"""
OpenAgents CLI - Unified command-line interface for OpenAgents.

Provides subcommands for:
- hermes chat          - Interactive chat (same as ./hermes)
- hermes gateway       - Run gateway in foreground
- hermes gateway start - Start gateway service
- hermes gateway stop  - Stop gateway service
- hermes setup         - Interactive setup wizard
- hermes status        - Show status of all components
- hermes cron          - Manage cron jobs
"""
=======
"""OpenAgents CLI - Unified command-line interface for OpenAgents."""
>>>>>>> rb/tag

import os
import sys

__version__ = "0.21.5"
__release_date__ = "2026.9.24"


def _ensure_utf8():
    """Force UTF-8 stdout/stderr to prevent UnicodeEncodeError crashes.

    The CLI prints box-drawing characters and the ☤ glyph in the setup wizard, doctor, and status
    banners; under a non-UTF-8 codec that raises before the command can even start (e.g.
    `hermes setup` on a fresh Pi).
    """
    repaired = False
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None:
            continue
        try:
            if (getattr(stream, "encoding", "") or "").lower().replace("-", "") == "utf8":
                continue
            # Preferred: reconfigure in place, preserving object identity so code already holding
            # a reference to the old sys.stdout benefits from the repair too.
            reconfigure = getattr(stream, "reconfigure", None)
            if callable(reconfigure):
                reconfigure(encoding="utf-8", errors="replace")
            else:
                # No reconfigure(): reopen the fd as UTF-8 (closefd=False keeps the original fd open).
                new_stream = open(stream.fileno(), "w", encoding="utf-8", errors="replace",
                                  buffering=1, closefd=False)
                setattr(sys, stream_name, new_stream)
            repaired = True
        except (AttributeError, OSError, ValueError):
            pass
    # Only nudge child processes toward UTF-8 when a non-UTF-8 locale was actually detected; on a
    # healthy UTF-8 host children inherit it from the locale already.
    if repaired:
        os.environ.setdefault("PYTHONUTF8", "1")
        os.environ.setdefault("PYTHONIOENCODING", "utf-8")


_ensure_utf8()
