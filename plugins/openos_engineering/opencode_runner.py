"""Spawn OpenOS OpenCode headless and parse stream-json output."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional


def resolve_opencode_binary() -> List[str]:
    explicit = os.environ.get("OPENOS_OPENCODE_PATH", "").strip()
    if explicit:
        return [explicit]

    which = shutil.which("opencode")
    if which:
        return [which]

    openos_root = os.environ.get("OPENOS_ROOT", "")
    if openos_root:
        cli = Path(openos_root) / "OpenCode" / "entrypoints" / "cli.tsx"
        if cli.is_file():
            return ["bun", str(cli)]

    repo_guess = Path(__file__).resolve().parents[4] / "OpenCode" / "entrypoints" / "cli.tsx"
    if repo_guess.is_file():
        return ["bun", str(repo_guess)]

    raise RuntimeError(
        "OpenCode binary not found. Set OPENOS_OPENCODE_PATH or install opencode."
    )


def run_opencode_headless(
    prompt: str,
    *,
    cwd: Optional[str] = None,
    ticket_id: Optional[str] = None,
    max_turns: int = 50,
    resume_session_id: Optional[str] = None,
) -> Dict[str, Any]:
    cmd = resolve_opencode_binary() + [
        "-p",
        "--output-format",
        "text",
        "--max-turns",
        str(max_turns),
        "--bare",
    ]
    if resume_session_id:
        cmd.extend(["--resume", resume_session_id])

    env = os.environ.copy()
    env["OPENCODE_INVOKED_BY"] = "openagents"
    if ticket_id:
        env["OPENTICKET_TICKET_ID"] = ticket_id

    workdir = cwd or os.getcwd()
    proc = subprocess.run(
        cmd + [prompt],
        cwd=workdir,
        env=env,
        capture_output=True,
        text=True,
        timeout=3600,
    )

    summary = proc.stdout.strip() or proc.stderr.strip()
    return {
        "ok": proc.returncode == 0,
        "exit_code": proc.returncode,
        "summary": summary[:8000],
        "stderr": proc.stderr[:2000] if proc.stderr else "",
    }


def parse_stream_json_lines(raw: str) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events
