"""Spawn OpenOS OpenCode headless and parse stream-json output."""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import warnings
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


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
            warnings.warn(
                "OpenCode: using bun entrypoints/cli.tsx fallback — "
                "set OPENOS_OPENCODE_PATH to compiled binary for faster cold start",
                stacklevel=2,
            )
            return ["bun", str(cli)]

    repo_guess = Path(__file__).resolve().parents[4] / "OpenCode" / "entrypoints" / "cli.tsx"
    if repo_guess.is_file():
        warnings.warn(
            "OpenCode: using bun entrypoints/cli.tsx fallback — "
            "set OPENOS_OPENCODE_PATH to compiled binary for faster cold start",
            stacklevel=2,
        )
        return ["bun", str(repo_guess)]

    raise RuntimeError(
        "OpenCode binary not found. Set OPENOS_OPENCODE_PATH or install opencode."
    )


def verify_opencode_binary() -> bool:
    try:
        cmd = resolve_opencode_binary() + ["--version"]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return proc.returncode == 0
    except (RuntimeError, subprocess.TimeoutExpired, OSError):
        return False


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


def extract_summary_from_stream(events: List[Dict[str, Any]]) -> str:
    for event in reversed(events):
        if event.get("type") == "result" and event.get("subtype") == "success":
            return str(event.get("result") or "")
        if event.get("type") == "result" and not event.get("is_error"):
            return str(event.get("result") or event.get("message") or "")
    return ""


def run_opencode_headless(
    prompt: str,
    *,
    cwd: Optional[str] = None,
    ticket_id: Optional[str] = None,
    correlation_id: Optional[str] = None,
    max_turns: int = 50,
    resume_session_id: Optional[str] = None,
) -> Dict[str, Any]:
    cmd = resolve_opencode_binary() + [
        "-p",
        "--output-format",
        "stream-json",
        "--verbose",
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
    corr = correlation_id or env.get("OPENTICKET_CORRELATION_ID", "").strip()
    if corr:
        env["OPENTICKET_CORRELATION_ID"] = corr

    workdir = cwd or os.getcwd()
    proc = subprocess.run(
        cmd + [prompt],
        cwd=workdir,
        env=env,
        capture_output=True,
        text=True,
        timeout=3600,
    )

    events = parse_stream_json_lines(proc.stdout)
    summary = extract_summary_from_stream(events) or proc.stdout.strip() or proc.stderr.strip()

    return {
        "ok": proc.returncode == 0,
        "exit_code": proc.returncode,
        "summary": summary[:8000],
        "stderr": proc.stderr[:2000] if proc.stderr else "",
        "events": events[-20:],
        "files_edited": _extract_edited_files(events),
    }


def _extract_edited_files(events: List[Dict[str, Any]]) -> List[str]:
    files: List[str] = []
    for event in events:
        if event.get("type") != "assistant":
            continue
        message = event.get("message") or {}
        for block in message.get("content") or []:
            if block.get("type") != "tool_use":
                continue
            name = str(block.get("name") or "")
            if "write" in name.lower() or "edit" in name.lower():
                inp = block.get("input") or {}
                path = inp.get("file_path") or inp.get("path")
                if path:
                    files.append(str(path))
    return list(dict.fromkeys(files))
