"""Headless OpenAI Codex CLI runner — mirrors ``plugins/openos_engineering/opencode_runner``."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def resolve_codex_binary() -> List[str]:
    explicit = os.environ.get("OPENOS_CODEX_PATH", "").strip()
    if explicit:
        return [explicit]
    which = shutil.which("codex")
    if which:
        return [which]
    raise RuntimeError(
        "Codex CLI not found. Install with `npm install -g @openai/codex` or set OPENOS_CODEX_PATH."
    )


def verify_codex_binary() -> bool:
    try:
        cmd = resolve_codex_binary() + ["--version"]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        return proc.returncode == 0
    except (RuntimeError, subprocess.TimeoutExpired, OSError):
        return False


def run_codex_headless(
    prompt: str,
    *,
    cwd: Optional[str] = None,
    sandbox: str = "workspace-write",
    full_auto: bool = False,
    timeout_seconds: int = 3600,
) -> Dict[str, Any]:
    """Run ``codex exec`` and return stdout summary."""
    cmd = resolve_codex_binary() + ["exec"]
    if full_auto:
        cmd.append("--full-auto")
    if sandbox:
        cmd.extend(["--sandbox", sandbox])
    cmd.append(prompt)

    workdir = cwd or os.getcwd()
    env = os.environ.copy()
    env.setdefault("OPENCODE_INVOKED_BY", "openagents")

    proc = subprocess.run(
        cmd,
        cwd=workdir,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
    )
    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()
    summary = stdout or stderr
    return {
        "ok": proc.returncode == 0,
        "exit_code": proc.returncode,
        "summary": summary[:8000],
        "stderr": stderr[:2000],
        "cwd": str(Path(workdir).resolve()),
    }
