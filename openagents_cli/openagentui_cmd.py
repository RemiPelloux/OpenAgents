"""Shared ``/OpenAgentUI`` command — start/stop the local visual builder UI.

Launches the rebranded Next.js frontend (``apps/openagentui``) as a
background subprocess. Its ``/api/openagentui/*`` calls are rewritten (see
``apps/openagentui/next.config.js``) to the FastAPI routes mounted on the
existing OpenAgents dashboard server (``openagents dashboard``, default
port 9119) — this command only manages the frontend process; it does not
start the dashboard itself.

Subcommands::

  /OpenAgentUI true|start   launch the builder UI and open a browser
  /OpenAgentUI false|stop   stop the builder UI process
  /OpenAgentUI status       report whether it's running, and its URL
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import time
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from openagents_cli.config import get_openagents_home
from utils import TRUTHY_STRINGS

logger = logging.getLogger(__name__)

DEFAULT_PORT = 4173
STARTUP_GRACE_SECONDS = 1.5
_FALSY_STRINGS = frozenset({"0", "false", "no", "off"})


@dataclass
class OpenAgentUiCommandResult:
    text: str


def _app_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "apps" / "openagentui"


def _state_path() -> Path:
    home = get_openagents_home() / "openagentui"
    home.mkdir(parents=True, exist_ok=True)
    return home / "server.json"


def _log_path() -> Path:
    return _state_path().parent / "server.log"


def _read_state() -> Optional[Dict[str, Any]]:
    path = _state_path()
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _write_state(state: Dict[str, Any]) -> None:
    _state_path().write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except (OSError, ProcessLookupError):
        return False
    except PermissionError:
        return True
    return True


def _npm_command() -> Optional[str]:
    return shutil.which("npm")


def _has_production_build(app_dir: Path) -> bool:
    return (app_dir / ".next" / "BUILD_ID").is_file()


def start_openagentui(port: int = DEFAULT_PORT, open_browser: bool = True) -> OpenAgentUiCommandResult:
    app_dir = _app_dir()
    if not app_dir.is_dir():
        return OpenAgentUiCommandResult(
            text=f"OpenAgentUI frontend not found at {app_dir}. Expected apps/openagentui to be vendored."
        )

    state = _read_state()
    if state and _pid_alive(int(state.get("pid", -1))):
        url = f"http://127.0.0.1:{state.get('port', port)}"
        if open_browser:
            webbrowser.open(url)
        return OpenAgentUiCommandResult(text=f"OpenAgentUI already running at {url}")

    npm = _npm_command()
    if npm is None:
        return OpenAgentUiCommandResult(text="npm not found on PATH — required to run the OpenAgentUI frontend.")

    if not (app_dir / "node_modules").is_dir():
        return OpenAgentUiCommandResult(
            text=f"Dependencies not installed. Run `npm install` in {app_dir} once, then retry `/OpenAgentUI true`."
        )

    use_dev = not _has_production_build(app_dir)
    script = "dev" if use_dev else "start"
    args = [npm, "run", script, "--", "-p", str(port)]

    log_file = _log_path()
    log_handle = log_file.open("a", encoding="utf-8")
    log_handle.write(f"\n--- OpenAgentUI launch {time.strftime('%Y-%m-%dT%H:%M:%SZ')} ---\n")
    log_handle.flush()

    kwargs: Dict[str, Any] = {"cwd": str(app_dir), "stdout": log_handle, "stderr": log_handle}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True

    try:
        proc = subprocess.Popen(args, **kwargs)
    except OSError as exc:
        return OpenAgentUiCommandResult(text=f"Failed to launch OpenAgentUI: {exc}")

    time.sleep(STARTUP_GRACE_SECONDS)
    if proc.poll() is not None:
        return OpenAgentUiCommandResult(
            text=f"OpenAgentUI exited immediately (code {proc.returncode}) — see {log_file}"
        )

    _write_state({"pid": proc.pid, "port": port, "mode": script, "started_at": time.time()})
    url = f"http://127.0.0.1:{port}"
    if open_browser:
        webbrowser.open(url)
    return OpenAgentUiCommandResult(
        text=(
            f"OpenAgentUI started ({script} mode) at {url} — pid {proc.pid}\n"
            "Make sure `openagents dashboard` is also running so its API calls resolve.\n"
            f"Logs: {log_file}"
        )
    )


def stop_openagentui() -> OpenAgentUiCommandResult:
    state = _read_state()
    if not state:
        return OpenAgentUiCommandResult(text="OpenAgentUI is not running.")
    pid = int(state.get("pid", -1))
    if not _pid_alive(pid):
        _state_path().unlink(missing_ok=True)
        return OpenAgentUiCommandResult(text="OpenAgentUI is not running.")
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError as exc:
        return OpenAgentUiCommandResult(text=f"Failed to stop OpenAgentUI (pid {pid}): {exc}")
    _state_path().unlink(missing_ok=True)
    return OpenAgentUiCommandResult(text=f"OpenAgentUI stopped (pid {pid}).")


def status_openagentui() -> OpenAgentUiCommandResult:
    state = _read_state()
    if not state or not _pid_alive(int(state.get("pid", -1))):
        return OpenAgentUiCommandResult(text="OpenAgentUI: stopped")
    url = f"http://127.0.0.1:{state.get('port', DEFAULT_PORT)}"
    return OpenAgentUiCommandResult(
        text=f"OpenAgentUI: running ({state.get('mode', 'start')} mode) at {url} — pid {state.get('pid')}"
    )


def handle_openagentui_command(args: str) -> OpenAgentUiCommandResult:
    tokens = (args or "").strip().split()
    verb = tokens[0].lower() if tokens else "status"

    if verb in {"true", "start", "up", "on"}:
        return start_openagentui()
    if verb in {"false", "stop", "down", "off"}:
        return stop_openagentui()
    if verb in {"status", ""}:
        return status_openagentui()
    if verb in TRUTHY_STRINGS:
        return start_openagentui()
    if verb in _FALSY_STRINGS:
        return stop_openagentui()

    return OpenAgentUiCommandResult(
        text=(
            "Usage: /OpenAgentUI true|start | false|stop | status\n"
            f"Unknown argument: {verb!r}"
        )
    )


# ---------------------------------------------------------------------------
# Terminal CLI — ``openagents openagentui start|stop|status``
# ---------------------------------------------------------------------------


def build_parser(parent_subparsers):
    parser = parent_subparsers.add_parser(
        "openagentui",
        help="Start/stop the local OpenAgentUI visual workflow builder",
        description="Launch or stop the rebranded Next.js OpenAgentUI frontend.",
    )
    sub = parser.add_subparsers(dest="openagentui_action")

    start_p = sub.add_parser("start", help="Launch the builder UI")
    start_p.add_argument("--port", type=int, default=DEFAULT_PORT)
    start_p.add_argument("--no-open", action="store_true")

    sub.add_parser("stop", help="Stop the builder UI")
    sub.add_parser("status", help="Report whether the builder UI is running")

    parser.set_defaults(_openagentui_parser=parser)
    return parser


def openagentui_command(args) -> int:
    action = getattr(args, "openagentui_action", None) or "status"
    if action == "start":
        result = start_openagentui(port=getattr(args, "port", DEFAULT_PORT), open_browser=not getattr(args, "no_open", False))
    elif action == "stop":
        result = stop_openagentui()
    else:
        result = status_openagentui()
    print(result.text)
    return 0
