"""Resolve OPENAGENTS_HOME for standalone skill scripts.

Skill scripts may run outside the OpenAgents process (system Python, nix env,
CI) where ``openagents_constants`` is not importable.  This module provides the
same ``get_openagents_home()`` contract without requiring it on ``sys.path``.

When ``openagents_constants`` IS available it is used directly so profile
resolution and any future enhancements are picked up automatically.
"""

from __future__ import annotations

import os
from pathlib import Path

try:
    from openagents_constants import get_openagents_home as get_openagents_home
except (ModuleNotFoundError, ImportError):

    def get_openagents_home() -> Path:
        """Return the OpenAgents home directory (default: ``~/.openagents``)."""
        val = os.environ.get("OPENAGENTS_HOME", "").strip()
        return Path(val) if val else Path.home() / ".hermes"
