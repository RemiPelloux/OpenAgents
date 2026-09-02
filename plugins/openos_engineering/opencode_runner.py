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


def _managed_workspace(
    requested_cwd: Optional[str],
    *,
    correlation_id: Optional[str],
    run_id: Optional[str],
) -> tuple[Path, Optional[str], Optional[str]]:
    """Return an isolated worktree when the server has a managed workspace."""
    configured_root = os.environ.get("OPENOS_WORKSPACE_ROOT", "").strip()
    if not configured_root:
        return Path(requested_cwd or os.getcwd()).resolve(), None, None

    root = Path(configured_root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    requested = Path(requested_cwd or root).expanduser().resolve()
    try:
        requested.relative_to(root)
    except ValueError as exc:
        raise ValueError(
            f"workspace path {requested} is outside managed root {root}"
        ) from exc

    run_key = run_id or correlation_id or "local"
    safe_key = "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in run_key)[
        :96
    ]
    worktree = root / "runs" / safe_key
    worktree.parent.mkdir(parents=True, exist_ok=True)
    try:
        repo_root = Path(
            subprocess.check_output(
                ["git", "-C", str(requested), "rev-parse", "--show-toplevel"],
                text=True,
                stderr=subprocess.DEVNULL,
            ).strip()
        ).resolve()
    except (OSError, subprocess.CalledProcessError):
        repo_root = None

    branch = f"agent/{safe_key}"
    if repo_root and not worktree.exists():
        subprocess.run(
            [
                "git",
                "-C",
                str(repo_root),
                "worktree",
                "add",
                "-B",
                branch,
                str(worktree),
                "HEAD",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    elif not worktree.exists():
        worktree.mkdir(parents=True)
    return (
        worktree,
        branch if repo_root else None,
        str(repo_root) if repo_root else None,
    )


def _scope_prompt_to_worktree(
    prompt: str,
    *,
    requested_workdir: str,
    workdir: Path,
    source_repo: Optional[str],
) -> str:
    """Rewrite source-repository paths so OpenCode works in its managed worktree."""
    scoped = prompt
    workdir_resolved = workdir.resolve()
    for original in dict.fromkeys((requested_workdir, source_repo)):
        if not original:
            continue
        resolved = Path(original).expanduser().resolve()
        if resolved != workdir_resolved:
            scoped = scoped.replace(str(resolved), ".")

    return (
        "Managed OpenOS workspace: perform all repository inspection, edits, tests, "
        "branch operations, and commits in the current working directory. Any source "
        "repository path in the ticket has been mapped to this isolated Git worktree. "
        "Do not access or modify the source checkout outside the current working directory.\n\n"
        f"{scoped}"
    )


def _git_metadata(workdir: Path, branch: Optional[str]) -> Dict[str, Any]:
    metadata: Dict[str, Any] = {"branch": branch, "commit_sha": None, "git_clean": None}
    try:
        metadata["commit_sha"] = subprocess.check_output(
            ["git", "-C", str(workdir), "rev-parse", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        status = subprocess.check_output(
            ["git", "-C", str(workdir), "status", "--porcelain"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
        metadata["git_clean"] = not bool(status.strip())
    except (OSError, subprocess.CalledProcessError):
        pass
    return metadata


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

    repo_guess = (
        Path(__file__).resolve().parents[4] / "OpenCode" / "entrypoints" / "cli.tsx"
    )
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
    run_id: Optional[str] = None,
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
    if os.environ.get("OPENOS_WORKSPACE_ROOT", "").strip():
        # Server-mode runs have no interactive approval channel. This is only
        # enabled after _managed_workspace validates and isolates the checkout.
        cmd.append("--dangerously-skip-permissions")
    if resume_session_id:
        cmd.extend(["--resume", resume_session_id])

    env = os.environ.copy()
    env["OPENCODE_INVOKED_BY"] = "openagents"
    if ticket_id:
        env["OPENTICKET_TICKET_ID"] = ticket_id
    corr = correlation_id or env.get("OPENTICKET_CORRELATION_ID", "").strip()
    if corr:
        env["OPENTICKET_CORRELATION_ID"] = corr

    if cwd:
        requested_workdir = cwd
    else:
        from agent.runtime_cwd import resolve_agent_cwd

        requested_workdir = str(resolve_agent_cwd())
    workdir_path, branch, source_repo = _managed_workspace(
        requested_workdir,
        correlation_id=correlation_id,
        run_id=run_id or os.environ.get("OPENOS_AGENT_RUN_ID"),
    )
    scoped_prompt = _scope_prompt_to_worktree(
        prompt,
        requested_workdir=requested_workdir,
        workdir=workdir_path,
        source_repo=source_repo,
    )
    env["OPENCODE_BASE_URL"] = env.get("LLM_BASE_URL", env.get("OPENCODE_BASE_URL", ""))
    env["OPENCODE_API_KEY"] = env.get("LLM_API_KEY", env.get("OPENCODE_API_KEY", ""))
    env["OPENCODE_MODEL"] = env.get("LLM_MODEL", env.get("OPENCODE_MODEL", ""))
    env["OPENOS_WORKSPACE"] = str(workdir_path)

    timeout = max(30, int(os.environ.get("OPENOS_OPENCODE_TIMEOUT_SECONDS", "3600")))
    try:
        proc = subprocess.run(
            cmd + [scoped_prompt],
            cwd=str(workdir_path),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        metadata = _git_metadata(workdir_path, branch)
        return {
            "ok": False,
            "exit_code": None,
            "summary": "OpenCode timed out",
            "stderr": str(exc),
            "events": [],
            "files_edited": [],
            "session_id": None,
            "workdir": str(workdir_path),
            "source_repo": source_repo,
            **metadata,
        }

    events = parse_stream_json_lines(proc.stdout)
    summary = (
        extract_summary_from_stream(events)
        or proc.stdout.strip()
        or proc.stderr.strip()
    )

    return {
        "ok": proc.returncode == 0,
        "exit_code": proc.returncode,
        "summary": summary[:8000],
        "stderr": proc.stderr[:2000] if proc.stderr else "",
        "events": events[-20:],
        "files_edited": _extract_edited_files(events, workdir_path),
        "session_id": _extract_session_id(events),
        "workdir": str(workdir_path),
        "source_repo": source_repo,
        **_git_metadata(workdir_path, branch),
    }


def _extract_session_id(events: List[Dict[str, Any]]) -> Optional[str]:
    for event in events:
        value = event.get("session_id") or event.get("sessionId")
        if value:
            return str(value)
        message = event.get("message")
        if isinstance(message, dict) and message.get("session_id"):
            return str(message["session_id"])
    return None


def _extract_edited_files(
    events: List[Dict[str, Any]], workdir: Optional[Path] = None
) -> List[str]:
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
                    candidate = Path(str(path)).expanduser()
                    if workdir:
                        try:
                            files.append(
                                str(candidate.resolve().relative_to(workdir.resolve()))
                            )
                        except ValueError:
                            logger.warning(
                                "OpenCode reported edit outside workdir: %s", path
                            )
                    else:
                        files.append(str(path))
    return list(dict.fromkeys(files))
