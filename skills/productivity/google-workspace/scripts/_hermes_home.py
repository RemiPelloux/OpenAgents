"""Resolve OPENAGENTS_HOME for standalone skill scripts.

Skill scripts may run outside the OpenAgents process (e.g. system Python,
nix env, CI) where ``openagents_constants`` is not importable.  This module
provides the same ``get_openagents_home()`` and ``display_openagents_home()``
contracts as ``openagents_constants`` without requiring it on ``sys.path``.

When ``openagents_constants`` IS available it is used directly so that any
future enhancements (profile resolution, Docker detection, etc.) are
picked up automatically.  The fallback path replicates the core logic
from ``openagents_constants.py`` using only the stdlib.

All scripts under ``google-workspace/scripts/`` should import from here
instead of duplicating the ``OPENAGENTS_HOME = Path(os.getenv(...))`` pattern.
"""

from __future__ import annotations

import os
from pathlib import Path

try:
    from openagents_constants import display_openagents_home as display_openagents_home
    from openagents_constants import get_openagents_home as get_openagents_home
except (ModuleNotFoundError, ImportError):

    def get_openagents_home() -> Path:
        """Return the OpenAgents home directory (default: ~/.openagents).

        Mirrors ``openagents_constants.get_openagents_home()``."""
        val = os.environ.get("OPENAGENTS_HOME", "").strip()
        return Path(val) if val else Path.home() / ".hermes"

    def display_openagents_home() -> str:
        """Return a user-friendly ``~/``-shortened display string.

        Mirrors ``openagents_constants.display_openagents_home()``."""
        home = get_openagents_home()
        try:
            return "~/" + str(home.relative_to(Path.home()))
        except ValueError:
            return str(home)
