#!/usr/bin/env python3
"""OpenAgents CLI - Main entry point.

Usage:
    hermes                     # Interactive chat (default)
    hermes chat / gateway / setup / status / cron / doctor / update / ...
    hermes --version           # Show version and update status
    hermes <cmd> --help        # Per-command help
"""

# openagents_bootstrap must be the very first import — it sets up UTF-8 stdio on
# Windows (no-op on POSIX). Guarded: after a ``git pull`` / interrupted
# ``hermes update`` the editable install's ``.pth`` may not list it yet; crashing
# here would block ``hermes update``.
try:
    import openagents_bootstrap  # noqa: F401
except ModuleNotFoundError:
    pass

# A `hermes update` killed while git was writing the new tree leaves a mix of old and new files that
# fails at the next import, whichever it is — put the old tree back before importing anything else
# from the checkout, then rerun the command (this module may itself be one of the new files).
# ``_early_recovery`` is stdlib-only and imported unguarded on purpose: same package
# dir, so if IT can't import nothing in openagents_cli can.
from openagents_cli import _early_recovery as _early_recovery_mod

if _early_recovery_mod.restore_interrupted_pull():
    _early_recovery_mod.relaunch_after_restore()

# Windows: neutralize CPython's ``platform._syscmd_ver`` before anything else
# imports — it shells out ``cmd /c ver`` and flashes a console when this
# process is windowless (pythonw gateway, kanban workers). No-op on POSIX.
from openagents_cli._subprocess_compat import suppress_platform_ver_console

suppress_platform_ver_console()

import os
import re
import sys

# Inline path math so ``python openagents_cli/main.py`` (script mode: sys.path[0]
# is openagents_cli/, not the repo root) can import openagents_cli._startup_fast.
_bootstrap_root = os.path.realpath(os.path.join(os.path.dirname(__file__), os.pardir))
if _bootstrap_root not in sys.path:
    sys.path.insert(0, _bootstrap_root)
from openagents_cli import _startup_fast  # noqa: E402

# A literal ``~``/``$VAR`` in OPENAGENTS_HOME (fish, or any quoted value) must become absolute
# before the first reader — otherwise it resolves against cwd and scaffolds <cwd>/~/.openagents.
_startup_fast.normalize_hermes_home_env()

# Early venv self-heal — MUST run before any third-party import below. A prior
# ``hermes update`` may have left a recovery marker with a core package wiped;
# the openagents_cli.config/env_loader imports further down would then crash before
# main() reaches _recover_from_interrupted_install(). ``_early_recovery`` is
# stdlib-only (safe on a corrupted venv) and repairs just enough to finish this
# import; the marker lifecycle stays with the full recovery path.
# It is also the canonical home of the probe/repair tables reused by the full recovery path below. See
# #57828.
try:
    _early_recovery_mod.recover_if_needed()
except Exception:
    pass


# Startup-liveness watchdog: for gateway runs, arm BEFORE the heavy import
# graph below — an import-time deadlock (native-extension init, contended
# import lock) is exactly the "wedged before the event loop, no logs, live
# PID" class it exists for. ``openagents_startup_watchdog`` is stdlib-only so it
# cannot itself wedge. The match requires the ADJACENT pair ``gateway run``
# (wherever global flags like ``-p <profile>`` put it) so unrelated commands
# mentioning both words never arm a 300s hard-exit timer. Foreground runs arm
# too — a pre-loop wedge is just as dead without a supervisor; GatewayRunner
# disarms once the event loop is live.
def _argv_is_gateway_run(argv: list) -> bool:
    return any(a == "gateway" and b == "run" for a, b in zip(argv, argv[1:]))


if _argv_is_gateway_run(sys.argv[1:]):
    try:
        from openagents_startup_watchdog import arm_startup_watchdog as _arm_sw

        _arm_sw()
        del _arm_sw
    except Exception:
        pass


def _exit_after_oneshot(rc: object) -> None:
    """Exit one-shot mode without letting late native finalizers change rc.

    The SIGABRT this guards against fires in a native-extension finalizer
    during ``Py_FinalizeEx``, *after* the response printed. Flush, shut down
    file logging, then ``os._exit`` past finalization. The ``atexit`` chain is
    deliberately skipped — several handlers re-enter native code that may be
    the abort source; stateful cleanup lives in ``_cleanup_oneshot_runtime``.

    See #30387, #43055.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except Exception:
            pass
    try:
        logging.shutdown()
    except Exception:
        pass
    os._exit(rc if isinstance(rc, int) else (0 if rc is None else 1))


_oneshot_cleanup_done = False
# (module, attr, kwargs, exceptions swallowed). MCP shutdown may raise
# BaseException-derived errors from executor teardown; the rest are Exception.
_ONESHOT_CLEANUPS = (
    ("tools.terminal_tool", "cleanup_all_environments", {}, Exception),
    ("tools.async_delegation", "interrupt_all", {"reason": "oneshot shutdown"}, Exception),
    ("tools.browser_tool_lifecycle", "_emergency_cleanup_all_sessions", {}, Exception),
    ("tools.mcp_tool_lifecycle", "shutdown_mcp_servers", {}, BaseException),
    ("agent.auxiliary_client", "shutdown_cached_clients", {}, Exception),
)


def _cleanup_oneshot_runtime() -> None:
    """Best-effort process-global cleanup before one-shot hard exit.

    ``run_oneshot`` owns the agent-local cleanup (memory provider, agent.close,
    session_db.close — all in ``_run_agent``'s finally block). This mirrors the
    process-global pieces from ``cli.py:_run_cleanup()`` that would otherwise
    be skipped by ``os._exit``.
    """
    global _oneshot_cleanup_done
    if _oneshot_cleanup_done:
        return
    _oneshot_cleanup_done = True
    import importlib

    for module, attr, kwargs, swallow in _ONESHOT_CLEANUPS:
        try:
            getattr(importlib.import_module(module), attr)(**kwargs)
        except swallow:
            pass


def _run_and_exit_oneshot(
    prompt: str,
    *,
    model: object = None,
    provider: object = None,
    toolsets: object = None,
    skills: object = None,
    usage_file: object = None,
    resume: object = None,
    reasoning: object = None,
) -> None:
    try:
        from openagents_cli.oneshot import run_oneshot

        rc = run_oneshot(
            prompt,
            model=model,
            provider=provider,
            toolsets=toolsets,
            skills=skills,
            usage_file=usage_file,
            resume=resume,
            reasoning=reasoning,
        )
    except KeyboardInterrupt:
        rc = 130
    except SystemExit as exc:
        if exc.code is not None and not isinstance(exc.code, int):
            print(exc.code, file=sys.stderr)
            rc = 1
        else:
            rc = exc.code
    except BaseException:
        # ``run_oneshot`` already maps agent failures to an int rc; anything
        # still escaping means it malfunctioned. Print it but never fall
        # through to interpreter teardown (the SIGABRT path this routine fixes).
        import traceback
        try:
            traceback.print_exc()
        except Exception:
            pass
        rc = 1
    try:
        _cleanup_oneshot_runtime()
    finally:
        # Even an interrupt during cleanup must not fall back into interpreter
        # finalization, where the native SIGABRT occurs.
        # The hard exit is the safety boundary for #43055.
        _exit_after_oneshot(rc)


def _warn_if_unsupervised_pid1(pid: "int | None" = None) -> None:
    """Warn when this process is PID 1 with nothing above it to reap orphans.

    The official image's ENTRYPOINT (``docker/entrypoint-dispatch.sh`` -> s6-overlay's
    ``/init``) is the reaper for orphaned grandchildren (browser tooling, MCP servers, shell
    children). A Compose service that overrides ``entrypoint:`` to invoke hermes directly makes
    hermes itself PID 1 — nothing then ``wait()``s on those orphans and they accumulate as
    zombies without bound (#111577). Outside a container a user process is never PID 1, so
    this is quiet everywhere else; it mirrors the dispatcher's own non-PID-1 warning.
    """
    if (pid if pid is not None else os.getpid()) != 1:
        return
    print(
        "[hermes] WARNING: this process is PID 1 with no init above it "
        "(entrypoint override?). Orphaned child processes will not be "
        "reaped and will accumulate as zombies. Use the image's default "
        "ENTRYPOINT (docker/entrypoint-dispatch.sh) instead of overriding "
        "it, or run with `docker run --init` / `init: true` in Compose.",
        file=sys.stderr,
    )


def _set_process_title() -> None:
    """Cosmetic: show 'openagents' instead of 'python3.xx' in ps/top/htop.

    Order: opt-in ``setproctitle`` dep; ctypes ``prctl(PR_SET_NAME)`` (Linux,
    15-char limit); ``pthread_setname_np`` (macOS — lldb/top only, not ``ps
    aux``); no-op on Windows (the .exe is already ``hermes.exe``). Never fatal.
    """
    try:
        import setproctitle  # type: ignore[import-untyped]

        setproctitle.setproctitle("openagents")
        return
    except ImportError:
        pass

    import ctypes
    import platform

    try:
        system = platform.system()
        if system == "Linux":
            libc = ctypes.CDLL("libc.so.6", use_errno=True)
            libc.prctl(15, b"openagents", 0, 0, 0)  # PR_SET_NAME = 15
        elif system == "Darwin":
            libc = ctypes.CDLL("libc.dylib", use_errno=True)
            libc.pthread_setname_np(b"openagents")
    except Exception:
        pass


# Cheap read of `display.interface` for the earliest hot-path decisions
# (mouse-residue suppression, Termux fast launch) that run before
# openagents_cli.config is importable. Cached per config path so early callers
# don't re-parse YAML, and so the answer follows the home the process ends up
# in: mouse-residue suppression reads this BEFORE `_apply_profile_override()`
# sets OPENAGENTS_HOME, and a cache keyed on nothing pinned every later caller to
# the default home's interface for the whole run (#116902).
_EARLY_INTERFACE_CACHE: "tuple[str, str] | None" = None


def _early_interface_config_path() -> str:
    """config.yaml of the home this process is currently pointed at."""
    home = os.environ.get("OPENAGENTS_HOME")
    if home:
        return os.path.join(home, "config.yaml")
    return os.path.join(os.path.expanduser("~"), ".hermes", "config.yaml")


def _config_default_interface_early() -> str:
    """Return the configured default interface ("cli"/"tui") via a minimal
    YAML read. Best-effort: any error falls back to "cli" (legacy behavior)."""
    global _EARLY_INTERFACE_CACHE
    cfg_path = _early_interface_config_path()
    if _EARLY_INTERFACE_CACHE is not None and _EARLY_INTERFACE_CACHE[0] == cfg_path:
        return _EARLY_INTERFACE_CACHE[1]
    value = "cli"
    try:
        if os.path.exists(cfg_path):
            import yaml as _yaml_iface

            with open(cfg_path, encoding="utf-8") as _f:
                raw = _yaml_iface.load(
                    _f, Loader=getattr(_yaml_iface, "CSafeLoader", None) or _yaml_iface.SafeLoader
                ) or {}
            disp = raw.get("display", {})
            if isinstance(disp, dict):
                iface = disp.get("interface")
                if isinstance(iface, str) and iface.strip().lower() == "tui":
                    value = "tui"
    except Exception:
        value = "cli"  # best-effort — default to classic REPL on any error
    _EARLY_INTERFACE_CACHE = (cfg_path, value)
    return value


def _wants_tui_early(argv: "list[str] | None" = None) -> bool:
    """Earliest TUI decision, usable before argparse/config imports.

    Precedence: ``--cli`` wins, then ``--tui``/``HERMES_TUI=1``, then a
    real-TTY gate, then ``display.interface``. The TTY gate is load-bearing
    for headless spawners (kanban workers, cron, pipes running ``chat -q``):
    a ``display.interface: tui`` default used to boot the TUI here, whose
    no-TTY bail-out exits 0 without doing the task. An explicit ``--tui``
    still reaches that informative bail-out.
    """
    if argv is None:
        argv = sys.argv[1:]
    if "--cli" in argv:
        return False
    if os.environ.get("HERMES_TUI") == "1" or "--tui" in argv:
        return True
    try:
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            return False
    except Exception:
        return False
    return _config_default_interface_early() == "tui"


# Mouse-tracking residue suppression — runs BEFORE every other import on the
# TUI hot path: while the launcher is still importing (~100-300ms, cooked+echo
# mode, before the Node TUI takes stdin raw) incoming SGR/X10 mouse reports
# echo into the shell scrollback as ``^[[<…M``. entry.tsx's
# `resetTerminalModes()` is the later cousin. ``HERMES_TUI_NO_EARLY_DISABLE``
# escapes the behaviour for diagnostics.
def _suppress_mouse_residue_early() -> None:
    if os.environ.get("HERMES_TUI_NO_EARLY_DISABLE") == "1":
        return
    if not _wants_tui_early():
        return
    try:
        if not os.isatty(1):  # redirected stdout: raw CSI would pollute the log
            return
        # Every mouse-tracking variant we know about; idempotent.
        os.write(
            1,
            b"\x1b[?1003l\x1b[?1002l\x1b[?1001l\x1b[?1000l\x1b[?9l"
            b"\x1b[?1006l\x1b[?1005l\x1b[?1015l\x1b[?1016l\x1b[?2029l",
        )
    except OSError:
        pass


_suppress_mouse_residue_early()


_startup_fast.ensure_project_root_on_path()

# ``hermes --version`` is answered before config/logging imports.
if _startup_fast.try_fast_version():
    raise SystemExit(0)

import argparse
import contextlib
import json
import shutil
import subprocess
from pathlib import Path
from typing import Optional


<<<<<<< HEAD
from openagents_fork import (
    DISTRIBUTION_REPO_HTTPS,
    DISTRIBUTION_REPO_URLS,
    IS_REBRANDED_HERMES_FORK,
    SYNC_FROM_HERMES_SCRIPT,
)
from openagents_cli.subcommands._shared import add_accept_hooks_flag as _add_accept_hooks_flag
||||||| cf299e9a01
from openagents_cli.subcommands._shared import add_accept_hooks_flag as _add_accept_hooks_flag
=======
>>>>>>> rb/tag
from openagents_cli.subcommands.cron import build_cron_parser
from openagents_cli.subcommands.sync import build_sync_parser
from openagents_cli.subcommands.gateway import build_gateway_parser
from openagents_cli.subcommands.profile import build_profile_parser
from openagents_cli.subcommands.model import build_model_parser
from openagents_cli.subcommands.setup import build_setup_parser

from openagents_cli.subcommands.whatsapp import build_whatsapp_parser, build_whatsapp_cloud_parser
from openagents_cli.subcommands.slack import build_slack_parser
from openagents_cli.subcommands.login import build_login_parser
from openagents_cli.subcommands.logout import build_logout_parser
from openagents_cli.subcommands.auth import build_auth_parser
from openagents_cli.subcommands.status import build_status_parser
from openagents_cli.subcommands.pause import build_pause_parser
from openagents_cli.subcommands.webhook import build_webhook_parser
from openagents_cli.subcommands.hooks import build_hooks_parser
from openagents_cli.subcommands.doctor import build_doctor_parser
from openagents_cli.subcommands.verify import build_verify_parser
from openagents_cli.subcommands.security import build_security_parser
from openagents_cli.subcommands.approvals import build_approvals_parser
from openagents_cli.subcommands.dump import build_dump_parser
from openagents_cli.subcommands.debug import build_debug_parser
from openagents_cli.subcommands.backup import build_backup_parser
from openagents_cli.subcommands.import_cmd import build_import_cmd_parser
from openagents_cli.subcommands.import_agent import build_import_agent_parser
from openagents_cli.subcommands.config import build_config_parser
from openagents_cli.subcommands.skin import build_skin_parser
from openagents_cli.subcommands.console import build_console_parser
from openagents_cli.subcommands.update import build_update_parser
from openagents_cli.subcommands.uninstall import build_uninstall_parser
from openagents_cli.subcommands.dashboard import build_dashboard_parser, build_serve_parser
from openagents_cli.subcommands.gui import build_gui_parser
from openagents_cli.subcommands.logs import build_logs_parser
from openagents_cli.subcommands.prompt_size import build_prompt_size_parser
from openagents_cli.subcommands.memory import build_memory_parser
from openagents_cli.subcommands.acp import build_acp_parser
from openagents_cli.subcommands.tools import build_tools_parser
from openagents_cli.subcommands.insights import build_insights_parser
from openagents_cli.subcommands.usage import build_usage_parser
from openagents_cli.subcommands.monitoring import build_monitoring_parser
from openagents_cli.subcommands.skills import build_skills_parser
from openagents_cli.subcommands.pairing import build_pairing_parser
from openagents_cli.subcommands.plugins import build_plugins_parser
from openagents_cli.subcommands.mcp import build_mcp_parser
from openagents_cli.subcommands.claw import build_claw_parser
from openagents_cli.subcommands.vault import build_vault_parser
from openagents_cli.subcommands.moa import build_moa_parser
from openagents_cli.subcommands.fallback import build_fallback_parser
from openagents_cli.subcommands.worktree import build_worktree_parser
from openagents_cli.subcommands.browser import build_browser_parser
from openagents_cli.subcommands.secrets import build_secrets_parser
from openagents_cli.subcommands.codex_runtime import build_codex_runtime_parser
from openagents_cli.subcommands.egress import build_egress_parser
from openagents_cli.subcommands.migrate import build_migrate_parser
from openagents_cli.subcommands.checkpoints import build_checkpoints_parser
from openagents_cli.subcommands.bundles import build_bundles_parser
from openagents_cli.subcommands.curator import build_curator_parser
from openagents_cli.subcommands.pets import build_pets_parser
from openagents_cli.subcommands.journey import build_journey_parser
from openagents_cli.subcommands.computer_use import build_computer_use_parser
from openagents_cli.subcommands.sessions import build_sessions_parser
from openagents_cli.subcommands.completion import build_completion_parser


def _require_tty(command_name: str) -> None:
    """Exit 1 if stdin is not a terminal: curses/input() prompts spin at 100% CPU on a pipe."""
    if not sys.stdin.isatty():
        print(
            f"Error: 'hermes {command_name}' requires an interactive terminal.\n"
            f"It cannot be run through a pipe or non-interactive subprocess.\n"
            f"Run it directly in your terminal instead.",
            file=sys.stderr,
        )
        sys.exit(1)


PROJECT_ROOT = Path(_startup_fast.project_root_str())
_startup_fast.ensure_project_root_on_path()


# Profile override — MUST happen before any hermes module import: many modules
# cache OPENAGENTS_HOME at import time. --profile/-p is pre-parsed from sys.argv,
# OPENAGENTS_HOME set, and the flag stripped so argparse never sees it. Falls back
# to ~/.openagents/active_profile for the sticky default.
_PROFILE_NAME_RE = r"^[a-z0-9][a-z0-9_-]{0,63}$"  # mirrors openagents_cli.profiles._PROFILE_ID_RE


def _inside_mcp_add_args(argv: list, index: int) -> bool:
    """True once argv reaches `hermes mcp add ... --args <command argv>`.

    ``mcp add --args`` is command-argv passthrough. Flags after that point
    belong to the child MCP command (for example Docker MCP Toolkit's
    ``--profile``), not to Hermes' own profile selector.
    """
    try:
        mcp_index = argv.index("mcp", 0, index)
        argv.index("add", mcp_index + 1, index)
    except ValueError:
        return False
    return True


def _looks_like_hermes_invocation() -> bool:
    """False when ``sys.argv`` belongs to a test runner rather than a ``hermes`` run.

    pytest's own ``-p no:xdist`` reaches ``_scan_profile_flag`` through ``sys.argv`` at import
    time; it must stay a silent skip, while a real ``hermes -p 'Work Bot'`` must fail loudly.
    """
    return "pytest" not in (sys.argv[0] or "")


def _exit_invalid_profile_name(value: str) -> None:
    from openagents_cli.profiles import _invalid_profile_name_error

    print(f"Error: {_invalid_profile_name_error(value)}", file=sys.stderr)
    print("Run `hermes profile list` to see your profiles.", file=sys.stderr)
    sys.exit(2)


def _looks_like_option_value(value: str) -> bool:
    """A ``-p`` value that clearly belongs to some other tool (pytest's ``-p no:xdist``, a
    third-party ``-p --flag``), never a mistyped profile name."""
    return value.startswith("-") or ":" in value


def _scan_profile_flag(argv: list) -> tuple:
    """Find -p/--profile/--profile= in argv -> (name, tokens_consumed, index).

    Historically the flag worked even after the subcommand (`hermes chat -p
    coder`), so scan broadly; stop at ``--`` and at the `mcp add --args`
    passthrough region. The value is normalised (strip + casefold, matching
    ``profiles.normalize_profile_name``) before validation so ``-p Work`` selects
    ``work``. A value that cannot be a profile name is rejected so
    resolve_profile_env never sys.exits on it; the rejection is explained (exit 2)
    only when the flag comes BEFORE the first subcommand token under a real
    ``hermes`` run — after a subcommand, ``-p`` may belong to that subcommand or a
    plugin (`hermes kanban ... -p 8080`), and option-looking values (``no:xdist``,
    ``--flag``) are always a silent skip.
    """
    from openagents_cli._parser import top_level_value_flag_sets

    value_flags, optional_value_flags = top_level_value_flag_sets()
    i = 0
    saw_subcommand = False
    while i < len(argv):
        arg = argv[i]
        if arg == "--" or (arg == "--args" and _inside_mcp_add_args(argv, i)):
            break
        if arg in {"--profile", "-p"} and i + 1 < len(argv):
            raw = argv[i + 1]
            value = raw.strip().casefold()
            if re.match(_PROFILE_NAME_RE, value):
                return value, 2, i
            if not saw_subcommand and not _looks_like_option_value(raw) and _looks_like_hermes_invocation():
                _exit_invalid_profile_name(raw)
            break
        if arg.startswith("--profile="):
            return arg.split("=", 1)[1].strip().casefold(), 1, i
        takes_value = "=" not in arg and i + 1 < len(argv) and (
            arg in value_flags
            or (arg in optional_value_flags and not argv[i + 1].startswith("-"))
        )
        if not takes_value and not arg.startswith("-"):
            saw_subcommand = True
        i += 2 if takes_value else 1
    return None, 0, None


def _resolve_sudo_user_profile_env(name: str) -> str | None:
    """Resolve `sudo hermes -p <name>` against the invoking user's home.

    This runs before argparse, so `--run-as-user` is not available yet. For
    sudo invocations the best signal is SUDO_USER: root is only doing the
    privileged install/start action; the profile store belongs to the user.
    """
    if name == "default":
        return None
    from openagents_constants import named_profile_is_live, sudo_invoker_default_home

    sudo_home = sudo_invoker_default_home()
    if sudo_home is None:
        return None
    candidate = sudo_home / "profiles" / name
    return str(candidate) if named_profile_is_live(candidate) else None


def _under_gateway_supervisor(argv: list) -> bool:
    """A supervisor-launched gateway child must NOT follow the sticky active_profile.

    Each supervised slot has a fixed profile identity: named slots pass
    ``-p <name>`` or pin OPENAGENTS_HOME to the profile dir; a bare invocation
    means "the root OPENAGENTS_HOME profile". If a supervised default-profile
    child read active_profile, switching the active profile (dashboard,
    ``hermes profile use``) would silently redirect the default gateway into
    that profile — adopting its credentials and double-polling a Telegram
    token already owned by that profile's own gateway (#74872).

    Markers (see gateway/restart.py ``is_gateway_supervisor_process``):
    HERMES_SUPERVISED_CHILD (systemd unit / launchd plist / Windows task),
    HERMES_S6_SUPERVISED_CHILD (legacy s6 container), INVOCATION_ID (systemd
    service children only — consulted ONLY for gateway commands because it is
    inherited by every descendant of a systemd-launched process, e.g.
    self-hosted CI runners), HERMES_GATEWAY_EXTERNAL_SUPERVISOR (explicit
    opt-in). XPC_SERVICE_NAME is deliberately NOT consulted: interactive macOS
    terminals set it too.
    """
    if os.environ.get("HERMES_SUPERVISED_CHILD") or os.environ.get("HERMES_S6_SUPERVISED_CHILD"):
        return True
    is_gateway_cmd = next((a for a in argv if not a.startswith("-")), None) == "gateway"
    if is_gateway_cmd and os.environ.get("INVOCATION_ID"):
        return True
    return os.environ.get(
        "HERMES_GATEWAY_EXTERNAL_SUPERVISOR", ""
    ).strip().lower() in {"1", "true", "yes", "on"}


def _s6_supervised_gateway_run(argv: list) -> bool:
    """A bare ``gateway run`` inside the s6 image names the ``gateway-default`` slot too.

    ``_maybe_redirect_run_to_s6_supervision`` turns it into a start of the supervised slot for the
    current profile, and it is the image's own CMD. Following the sticky ``active_profile`` there
    started that profile's named slot on every container boot: the one the boot reconciler just
    registered down, because a started named slot is a second gateway beside the multiplexer.
    ``--no-supervise`` keeps the foreground run, which follows ``active_profile`` as before (#22502).
    """
    words = [a for a in argv if not a.startswith("-")]
    if words[:2] != ["gateway", "run"] or "--no-supervise" in argv:
        return False
    if os.environ.get("HERMES_GATEWAY_NO_SUPERVISE", "").lower() in ("1", "true", "yes"):
        return False
    from openagents_cli.service_manager import _s6_running
    return _s6_running()


def _apply_profile_override() -> None:
    """Pre-parse --profile/-p and set OPENAGENTS_HOME before imports."""
    argv = sys.argv[1:]
    profile_name, consume, profile_index = _scan_profile_flag(argv)

    # OPENAGENTS_HOME already set with no explicit flag: trust it only when it
    # points at a specific profile dir ("profiles" as immediate parent). If it
    # points at the hermes root (systemd hardcodes OPENAGENTS_HOME=/root/.hermes)
    # we must still read active_profile — the user may have run
    # `hermes profile use` and the gateway should honour it (#22502).
    hermes_home_env = os.environ.get("OPENAGENTS_HOME", "")
    if profile_name is None and hermes_home_env and Path(hermes_home_env).parent.name == "profiles":
        return
    # The post-swap updater child inherits the home its parent already resolved (possibly the
    # root for `-p default`); re-reading the sticky active_profile here would finish the update
    # — receipt, config migration, exit code — in another profile's home.
    if profile_name is None and hermes_home_env and os.environ.get("HERMES_UPDATE_POST_SWAP") == "1":
        return

    if (profile_name is None and not _under_gateway_supervisor(argv)
            and not _startup_fast.is_desktop_ssh_backend_argv(argv)
            and not _s6_supervised_gateway_run(argv)):
        try:
            from openagents_constants import get_default_openagents_root

            active_path = get_default_openagents_root() / "active_profile"
            if active_path.exists():
                name = active_path.read_text(encoding="utf-8").strip()
                if name and name != "default":
                    profile_name = name  # consume stays 0: nothing to strip
        except (UnicodeDecodeError, OSError):
            pass  # corrupted file, skip

    if profile_name is None:
        return
    try:
        from openagents_cli.profiles import resolve_profile_env

        hermes_home = resolve_profile_env(profile_name)
    except FileNotFoundError as exc:
        hermes_home = _resolve_sudo_user_profile_env(profile_name)
        if not hermes_home:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        # A bug in profiles.py must NEVER prevent hermes from starting
        print(f"Warning: profile override failed ({exc}), using default", file=sys.stderr)
        return
    os.environ["OPENAGENTS_HOME"] = hermes_home
    # Strip the flag from argv so argparse doesn't choke
    if consume > 0 and profile_index is not None:
        start = profile_index + 1  # +1 because argv is sys.argv[1:]
        sys.argv = sys.argv[:start] + sys.argv[start + consume :]


_apply_profile_override()
# ``-p``/active_profile re-homed the process after openagents_bootstrap ran: re-point the temp vars
# at THIS home's scratch dir (a user-set TMPDIR is still left alone).
try:
    from openagents_constants import export_scratch_tmp_env as _export_scratch_tmp_env

    _export_scratch_tmp_env()
except Exception:
    pass  # an unwritable home leaves the system temp dir in place; never block startup

# Windows launcher self-heal — the ``hermes`` command is a COPY of the venv
# console script staged into the managed bin dir (outside the checkout, since
# ``hermes update``'s autostash once swept ``<checkout>\bin`` copies off disk;
# venv\Scripts must stay off PATH as it shadows the user's ``python``).
# Re-staging at process start reaches already-broken installs via the desktop
# app's ``python -m openagents_cli.main`` spawn. Gates fail toward inaction. Sits
# AFTER the profile override on purpose — no hermes module may import before
# profiles resolve; the helper anchors on the DEFAULT root, so profile
# sessions heal the same shared dir.
# That dir lives OUTSIDE the git checkout precisely because an earlier layout staged the copies at
# ``<checkout>\bin``, where ``hermes update``'s autostash (``git stash push --include-untracked``) swept
# them off disk; with the desktop updater's ``--keep-stash`` nothing restored them and ``hermes`` stopped
# resolving in every new terminal (venv\Scripts itself must stay off PATH — it shadows the user's
# ``python``, #83797). Costs a few stat calls when healthy; gates fail toward inaction so source checkouts
# are untouched.
if sys.platform == "win32":
    try:
        from openagents_cli import _install_repair as _install_repair_mod

        _install_repair_mod.ensure_windows_bin_launchers(_bootstrap_root)
    except Exception:
        pass

# Load .env from ~/.openagents/.env first, then project root as dev fallback.
# User-managed env files should override stale shell exports on restart.
from openagents_cli.config import get_openagents_home
from openagents_cli.env_loader import load_openagents_dotenv

# ``update`` must not resolve external secret sources (Windows self-lock via cryptography, slow
# helpers inside the import probe) — ``_early_recovery._should_skip_external_secret_sources``
# owns that argv check for every dotenv load in the process. See #73381.
load_openagents_dotenv(project_env=PROJECT_ROOT / ".env")

# Bridge security.redact_secrets → HERMES_REDACT_SECRETS BEFORE openagents_logging
# imports agent.redact, which snapshots the flag exactly once at import. A
# .env value still wins — this is config.yaml fallback only. network.force_ipv4
# is read from the same parse to avoid a second full load_config() (~17ms).
_FORCE_IPV4_EARLY = False
try:
    # The effective-config cache (shared raw parse with read_raw_config()) means this SAME parse
    # serves openagents_logging, openagents_time and later raw reads: 3-4 config.yaml parses become one.
    # Managed overlay included: administrator-pinned redact_secrets / force_ipv4 win here too.
    from openagents_cli.config_effective import load_user_config_effective as _load_effective_early

    _cfg_path = get_openagents_home() / "config.yaml"
    if _cfg_path.exists():
        _early_cfg_raw = _load_effective_early(_cfg_path)
        if "HERMES_REDACT_SECRETS" not in os.environ:
            _early_sec_cfg = _early_cfg_raw.get("security", {})
            if isinstance(_early_sec_cfg, dict):
                _early_redact = _early_sec_cfg.get("redact_secrets")
                if _early_redact is not None:
                    os.environ["HERMES_REDACT_SECRETS"] = str(_early_redact).lower()
        _early_net_cfg = _early_cfg_raw.get("network", {})
        if isinstance(_early_net_cfg, dict) and _early_net_cfg.get("force_ipv4"):
            _FORCE_IPV4_EARLY = True
        del _early_cfg_raw
    del _cfg_path
except Exception:
    pass  # best-effort — redaction stays at default (enabled) on config errors

# Centralized file logging for every subcommand (agent.log + errors.log).
# Dashboard entrypoints use GUI mode so gui.log captures pre-dispatch failures.
try:
    from openagents_logging import setup_logging as _setup_logging

    _setup_logging(
        mode=(
            "gui"
            if next((arg for arg in sys.argv[1:] if not arg.startswith("-")), "")
            in {"dashboard", "serve", "gui", "desktop"}
            else "cli"
        )
    )
except Exception:
    pass  # best-effort — don't crash the CLI if logging setup fails

# Apply IPv4 preference before any HTTP client is created.
if _FORCE_IPV4_EARLY:
    try:
        from openagents_constants import apply_ipv4_preference as _apply_ipv4

        _apply_ipv4(force=True)
    except Exception:
        pass  # best-effort — don't crash if openagents_constants not importable yet

import logging
import threading
from datetime import datetime

from openagents_cli import __version__, __release_date__

from openagents_cli.model_setup_flows import (
    _model_flow_openrouter,
    _model_flow_nous,
    _model_flow_openai_codex,
    _model_flow_xai_oauth,
    _model_flow_qwen_oauth,
    _model_flow_minimax_oauth,
    _model_flow_custom,
    _model_flow_azure_foundry,
    _model_flow_named_custom,
    _model_flow_copilot,
    _model_flow_copilot_acp,
    _model_flow_kimi,
    _model_flow_stepfun,
    _model_flow_bedrock,
    _model_flow_vertex,
    _model_flow_api_key_provider,
    _model_flow_anthropic,
    _model_flow_moa,
    _model_flow_ai_gateway,
    _model_flow_plugin_provider,
    _is_profile_plugin_flow_provider,
)
logger = logging.getLogger(__name__)
from openagents_cli.main_agent_cmds import (
    cmd_acp,
    cmd_insights,
    cmd_memory,
    cmd_monitoring,
    cmd_skills,
    cmd_tools,
)
from openagents_cli.main_platform_setup import (
    cmd_slack,
    cmd_sync,
    cmd_whatsapp,
    cmd_whatsapp_cloud,
)
from openagents_cli.process_identity import is_desktop_owned_backend as _is_desktop_owned_backend
from openagents_cli.main_dashboard import (
    _attach_to_host_backend,
    _finalize_update_output,
    _find_stale_dashboard_pids,
    _install_hangup_protection,
    _is_electron_packaged_web_dist,
    _maybe_setup_dashboard_auth_interactively,
    _read_ssh_session_token_file,
    _report_dashboard_status,
    _resolve_dashboard_web_dist,
    _route_named_profile_dashboard,
)
from openagents_cli.main_dashboard import (  # frozen updater surface: update_cmd*.py resolve these via _m()
    _respawn_dashboard_processes,
)
from openagents_cli.main_provider_setup import (
    _GENERIC_API_KEY_PROVIDERS,
    _aux_config_menu,
    _build_provider_picker_rows,
    _clear_stale_openai_base_url,
    _is_profile_api_key_provider,
    _named_custom_provider_map,
    _offer_reasoning_after_pick,
    _prompt_main_reasoning_effort,
    _prompt_provider_choice,
    _remove_custom_provider,
)
from openagents_cli.main_install_repair import (
    _cleanup_quarantined_exes,
    _recover_from_interrupted_install,
)
from openagents_cli.main_install_repair import (  # frozen updater surface: update_cmd*.py resolve these via _m()
    ShimQuarantineError,
    _UPDATE_REEXEC_ENV,
    _clear_lazy_refresh_incomplete_marker,
    _clear_marker_file,
    _clear_update_incomplete_marker,
    _install_python_dependencies_with_optional_fallback,
    _is_termux_env,
    _is_windows,
    _is_windows_npm_path,
    _lazy_refresh_marker_path,
    _pytest_owns_live_checkout,
    _reexec_dependency_sync_off_windows_shim,
    _repair_venv_via_import_probes,
    _resolve_install_target_python,
    _resolve_node_runtime_npm,
    _resolve_update_branch,
    _run_install_with_heartbeat,
    _run_package_only_install,
    _update_marker_path,
    _venv_scripts_dir,
    _verify_console_scripts_installed,
    _verify_core_dependencies_installed,
)
from openagents_cli.main_desktop import (
    cmd_gui,
)
from openagents_cli.main_desktop import (  # frozen updater surface: update_cmd*.py resolve these via _m()
    _desktop_build_needed,
    _desktop_dist_exists,
    _desktop_macos_relaunchable_fixup,
    _desktop_packaged_executable,
    _desktop_stamp_path,
    _install_rebuilt_desktop_app,
)
from openagents_cli.main_web_build import (
    _sweep_stale_bytecode_if_checkout_changed,
)
from openagents_cli.main_web_build import (  # frozen updater surface: update_cmd*.py resolve these via _m()
    _build_web_ui,
    _nixos_build_env,
    _record_bytecode_fingerprint,
    _run_npm_install_deterministic,
)
from openagents_cli.main_tui_launch import (
    _launch_tui,
    _pin_kanban_board_env,
    _resolve_use_tui,
    _sync_bundled_skills_quietly,
)


def _is_termux_startup_environment(env: dict[str, str] | None = None) -> bool:
    """Import-safe Termux check for cold-start-sensitive CLI paths."""
    check = env or os.environ
    prefix = str(check.get("PREFIX", ""))
    return bool(
        check.get("TERMUX_VERSION")
        or "com.termux/files/usr" in prefix
        or prefix.startswith("/data/data/com.termux/")
    )


def _read_packed_ref(common_dir: Path, ref: str) -> str | None:
    """Look up a ref in .git/packed-refs without spawning git.

    packed-refs lines look like ``<sha> <ref>`` with optional ``^<sha>``
    peel lines and ``#``-prefixed comments / ``# pack-refs with:`` header.
    """
    try:
        text = (common_dir / "packed-refs").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for line in text.splitlines():
        if not line or line.startswith("#") or line.startswith("^"):
            continue
        parts = line.split(" ", 1)
        if len(parts) == 2 and parts[1].strip() == ref:
            return parts[0].strip()
    return None


def _read_git_revision_fingerprint(repo_root: Path) -> str | None:
    """Return a cheap checkout fingerprint without spawning git."""
    git_dir = repo_root / ".git"
    try:
        if git_dir.is_file():
            for line in git_dir.read_text(encoding="utf-8", errors="replace").splitlines():
                key, _, value = line.partition(":")
                if key.strip() == "gitdir" and value.strip():
                    git_dir = (repo_root / value.strip()).resolve()
                    break
        # Worktrees point HEAD at a per-worktree gitdir but pack their refs
        # in the main repo's gitdir (referenced via ``commondir``). Resolve
        # that up front so packed-refs lookups hit the right file.
        common_dir = git_dir
        commondir_file = git_dir / "commondir"
        if commondir_file.exists():
            try:
                rel = commondir_file.read_text(encoding="utf-8", errors="replace").strip()
                if rel:
                    common_dir = (git_dir / rel).resolve()
            except OSError:
                pass
        head = (git_dir / "HEAD").read_text(encoding="utf-8", errors="replace").strip()
        if head.startswith("ref:"):
            ref = head.split(":", 1)[1].strip()
            # Loose refs may live in the worktree gitdir OR the common dir
            # (branches created via `git worktree add` typically live in the
            # common dir's refs/heads/).
            for candidate in (git_dir, common_dir):
                ref_file = candidate / ref
                if ref_file.exists():
                    return f"git:{ref}:{ref_file.read_text(encoding='utf-8', errors='replace').strip()}"
            packed_sha = _read_packed_ref(common_dir, ref)
            if packed_sha:
                return f"git:{ref}:{packed_sha}"
            # Ref name is known but unresolved — still stable across launches,
            # and the version/release fallback in the caller will invalidate
            # after `hermes update`.
            return f"git:{ref}:unresolved"
        return f"git:HEAD:{head}"
    except OSError:
        return None


def _termux_bundled_skills_fingerprint() -> str:
    """Cheap invalidation key for Termux bundled-skill startup sync."""
    git_fp = _read_git_revision_fingerprint(PROJECT_ROOT)
    if git_fp:
        return git_fp
    skills_dir = PROJECT_ROOT / "skills"
    try:
        stat = skills_dir.stat()
        return f"skills:{__version__}:{__release_date__}:{stat.st_mtime_ns}:{stat.st_size}"
    except OSError:
        return f"skills:{__version__}:{__release_date__}:missing"


def _termux_bundled_skills_stamp_path() -> Path:
    return get_openagents_home() / "skills" / ".termux_bundled_sync_stamp"


def _termux_bundled_skills_sync_needed() -> bool:
    if not _is_termux_startup_environment():
        return True
    if os.environ.get("HERMES_TERMUX_FORCE_SKILLS_SYNC") == "1":
        return True
    try:
        stamp = _termux_bundled_skills_stamp_path()
        return stamp.read_text(encoding="utf-8").strip() != _termux_bundled_skills_fingerprint()
    except OSError:
        return True


def _mark_termux_bundled_skills_synced() -> None:
    if not _is_termux_startup_environment():
        return
    try:
        stamp = _termux_bundled_skills_stamp_path()
        stamp.parent.mkdir(parents=True, exist_ok=True)
        stamp.write_text(_termux_bundled_skills_fingerprint() + "\n", encoding="utf-8")
    except OSError:
        pass


def _sync_bundled_skills_for_startup() -> bool:
    """Sync bundled skills, but skip unchanged Termux checkouts cheaply.

    Hashing every bundled skill is safe but expensive on older Android
    storage. The git/ref stamp keeps post-update correctness: a changed
    checkout revision forces one real sync, then later starts skip it.
    """
    if _is_termux_startup_environment() and not _termux_bundled_skills_sync_needed():
        return False

    from tools.skills_sync import sync_skills

    sync_skills(quiet=True)
    _mark_termux_bundled_skills_synced()
    return True


def _termux_should_prefetch_update_check() -> bool:
    if not _is_termux_startup_environment():
        return True
    return os.environ.get("HERMES_TERMUX_PREFETCH_UPDATES") == "1"


def _dotenv_has_provider_key(env_file: Path, provider_env_vars: set) -> bool:
    """True if ~/.openagents/.env assigns a non-empty value to any provider key."""
    if not env_file.exists():
        return False
    try:
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("#") or "=" not in line:
                continue
            if line.startswith("export "):
                # Strip the bash-compatible ``export `` prefix so lines like ``export API_KEY=...`` parse as
                # ``API_KEY`` rather than being stored under the wrong key ``"export API_KEY"`` (#6659).
                line = line[7:]
            key, _, val = line.partition("=")
            if key.strip() in provider_env_vars and val.strip().strip("'\""):
                return True
    except Exception:
        pass
    return False


def _auth_store_logged_in(auth_file: Path, registry, strict_profile_scope: bool) -> bool:
    """True if auth.json's active provider is logged in (api_key providers ignored under strict scope)."""
    from openagents_cli.auth import get_auth_status

    if not auth_file.exists():
        return False
    try:
        auth = json.loads(auth_file.read_text(encoding="utf-8-sig"))
        active = auth.get("active_provider")
        active_config = registry.get(str(active or "").strip().lower())
        if active and not (
            strict_profile_scope and active_config and active_config.auth_type == "api_key"
        ):
            return bool(get_auth_status(active).get("logged_in"))
    except Exception:
        pass
    return False


def _has_any_provider_configured(*, strict_profile_scope: bool = False) -> bool:
    """Check if at least one inference provider is usable. Never creates one: the Nous free tier
    counts only once its identity exists, and the boot bootstrap (``openagents_cli.free_tier_bootstrap``)
    is the only thing that creates it; ``cmd_chat`` runs the bootstrap before asking.

    ``strict_profile_scope``: the caller has bound a NAMED profile's home and
    secret scope and wants an answer for that profile only — launch-process
    env and host-wide fallbacks (gh auth, Claude Code credentials) must not
    make it appear ready. Unscoped callers keep the legacy behavior.
    """
    from openagents_cli.config import DEFAULT_CONFIG, get_env_path, get_openagents_home, load_config
    from openagents_cli.auth import PROVIDER_REGISTRY, get_auth_status

    cfg = load_config()
    model_cfg = cfg.get("model")
    _model_name = model_cfg if isinstance(model_cfg, str) else ""
    if isinstance(model_cfg, dict):
        _model_name = model_cfg.get("default") or ""
        if isinstance(_model_name, dict):
            from openagents_cli.config import split_model_config_default
            _model_name, _ = split_model_config_default(_model_name)
    _model_name = str(_model_name).strip()
    # "Explicitly configured" = model differs from the hardcoded default; gates
    # Claude Code credentials so they don't skip setup on a fresh install.
    _has_hermes_config = _model_name and _model_name != DEFAULT_CONFIG.get("model", "")

    # Env vars (.env or shell). OPENAI_BASE_URL alone counts — local models
    # (vLLM, llama.cpp) often need no API key.
    provider_env_vars = {
        "OPENROUTER_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_TOKEN",
        "OPENAI_BASE_URL",
    }
    for pconfig in PROVIDER_REGISTRY.values():
        if pconfig.auth_type == "api_key":
            provider_env_vars.update(pconfig.api_key_env_vars)
    if strict_profile_scope:
        from agent.secret_scope import current_secret_scope

        read_provider_env = (current_secret_scope() or {}).get
    else:
        read_provider_env = os.getenv
    if any(read_provider_env(v) for v in provider_env_vars):
        return True
    if _dotenv_has_provider_key(get_env_path(), provider_env_vars):
        return True

    # Cheap on-disk checks (auth.json, config.yaml) first: the PROVIDER_REGISTRY
    # sweep below spawns subprocesses (gh) and can take 15-20s — long enough
    # that desktop setup.status calls time out.
    if _auth_store_logged_in(get_openagents_home() / "auth.json", PROVIDER_REGISTRY, strict_profile_scope):
        return True

    # model as a dict with provider/base_url/api_key means setup ran (fresh
    # installs have a plain string); also covers custom endpoints kept in config.
    if isinstance(model_cfg, dict) and any(
        (model_cfg.get(k) or "").strip() for k in ("provider", "base_url", "api_key")
    ):
        return True

    # Provider-specific auth fallbacks (e.g. Copilot via gh auth).
    if not strict_profile_scope:
        try:
            if any(
                get_auth_status(pid).get("logged_in")
                for pid, pconfig in PROVIDER_REGISTRY.items()
                if pconfig.auth_type == "api_key"
            ):
                return True
        except Exception:
            pass

    # Claude Code OAuth credentials count only once OpenAgents is explicitly
    # configured — having Claude Code installed isn't consent to use its tokens.
    if _has_hermes_config and not strict_profile_scope:
        try:
            from agent.anthropic_credentials import read_claude_code_credentials, is_claude_code_token_valid

            creds = read_claude_code_credentials()
            if creds and (
                is_claude_code_token_valid(creds) or creds.get("refreshToken")
            ):
                return True
        except Exception:
            pass

    # Nothing explicit anywhere: an existing Nous free-tier identity counts while the tier is on.
    try:
        from openagents_cli.anon_auth import guest_enabled, has_guest
        return guest_enabled() and has_guest()
    except Exception as exc:
        logger.debug("free tier check on first run skipped: %s", exc)
    return False


def _confirm_startup_expensive_model_override(args) -> None:
    """Guard startup -m/--provider overrides before the first API call."""
    explicit_model = (getattr(args, "model", None) or "").strip()
    explicit_provider = (getattr(args, "provider", None) or "").strip()
    if not explicit_model and not explicit_provider:
        return

    try:
        from openagents_cli.config import load_config
        from openagents_cli.model_selection_guards import (
            combined_message,
            selection_warnings,
        )
    except Exception as exc:
        logger.warning("startup model cost guard unavailable: %s", exc)
        return

    try:
        config = load_config()
    except Exception as exc:
        logger.warning("startup model cost guard could not load config: %s", exc)
        config = {}
    _dict = lambda v: v if isinstance(v, dict) else {}  # noqa: E731
    config = _dict(config)
    model_cfg = _dict(config.get("model"))
    security_cfg = _dict(config.get("security"))

    model = explicit_model or (model_cfg.get("default") or "").strip()
    if not model:
        return
    provider = (explicit_provider or model_cfg.get("provider") or "").strip()
    try:
        # Unified registry: cost guard + id-keyed guards (e.g. the
        # data-training-tier warning) all fire at startup too.
        warnings = selection_warnings(
            model,
            provider=provider,
            base_url=(model_cfg.get("base_url") or ""),
            api_key=(model_cfg.get("api_key") or ""),
        )
    except Exception as exc:
        logger.warning("startup model cost guard failed for %s/%s: %s", provider, model, exc)
        return
    if not warnings:
        return

    # Intentionally independent of --yolo / --accept-hooks: those approve local
    # command risk, not paid aggregator spend or a surprising provider route.
    is_interactive = sys.stdin.isatty()
    if not is_interactive and security_cfg.get("allow_data_training_tiers_noninteractive") is True:
        acknowledged = [w for w in warnings if w.kind == "data_policy"]
        if acknowledged:
            sys.stderr.write(combined_message(acknowledged) + "\n")
            sys.stderr.write(
                "Proceeding in non-interactive mode because "
                "security.allow_data_training_tiers_noninteractive is true.\n"
            )
            warnings = [w for w in warnings if w.kind != "data_policy"]
            if not warnings:
                return

    message = combined_message(warnings)
    if not is_interactive:
        sys.stderr.write(message + "\n")
        if any(warning.kind == "data_policy" for warning in warnings):
            sys.stderr.write(
                "To acknowledge data-training tiers for unattended runs, set "
                "security.allow_data_training_tiers_noninteractive to true "
                "in config.yaml.\n"
            )
        sys.stderr.write(
            "Refusing this startup model override in non-interactive mode. "
            "Run interactively and confirm if you intend to use it.\n"
        )
        raise SystemExit(1)

    sys.stderr.write(message + "\n")
    try:
        reply = input("Use this model for this invocation? [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        reply = ""
    if reply not in {"y", "yes"}:
        sys.stderr.write("Model override cancelled.\n")
        raise SystemExit(1)


def _resolve_workspace_key() -> Optional[str]:
    """The current workspace identity for cwd-scoped resume.

    Git repo root when CWD is inside a repo (so all sessions across its
    subdirs/worktrees group together), else the CWD itself. Returns None when
    neither can be determined — callers fall back to the global MRU then.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return os.path.abspath(result.stdout.strip())
    except Exception:
        pass
    try:
        return os.getcwd()
    except Exception:
        return None


@contextlib.contextmanager
def _session_db():
    """Yield a read-only ``SessionDB`` (lazy import, so test patches on ``openagents_state``
    intercept). Every caller is a lookup (last session, title → id, recorded cwd), so it
    never opens a writer beside the one the CLI acquires from the registry a moment later.
    Open failures yield None and any error raised by the ``with`` body is swallowed —
    callers fall through to their ``return None``."""
    db = None
    try:
        from openagents_state import SessionDB

        db = SessionDB(read_only=True)
    except Exception:
        pass
    try:
        yield db  # body errors (incl. AttributeError on a None db) are swallowed
    except Exception:
        pass
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:
                pass


def _latest_session_id(use_tui: bool) -> Optional[str]:
    """MRU session for the active interface; a TUI launch falls back to the CLI MRU."""
    last_id = _resolve_last_session(source="tui" if use_tui else "cli")
    if not last_id and use_tui:
        last_id = _resolve_last_session(source="cli")
    return last_id


def _resolve_last_session(source: str = "cli") -> Optional[str]:
    """Look up the most recently-used session ID for a source.

    Scoped to the current workspace first (git repo root, else cwd) so
    ``hermes -c`` from repo A continues repo A's last session rather than the
    global MRU. Falls back to the unscoped MRU when no session matches the
    current workspace, preserving the old behaviour for fresh directories.
    """
    # A finite `hermes -z`/`chat -q` run is CLI history too: `hermes -z … --resume latest` chains on it.
    if source == "cli":
        from run_agent import CLI_FAMILY_SOURCES
        source = sorted(CLI_FAMILY_SOURCES)
    with _session_db() as db:
        ws_key = _resolve_workspace_key()
        if ws_key:
            sessions = db.search_sessions(source=source, limit=1, workspace_key=ws_key)
            if sessions:
                return sessions[0]["id"]
        # Fallback: global MRU for this source.
        sessions = db.search_sessions(source=source, limit=1)
        return sessions[0]["id"] if sessions else None
    return None


def _probe_container(cmd: list, backend: str, via_sudo: bool = False):
    """Run a container inspect probe, returning the CompletedProcess.

    Catches TimeoutExpired specifically for a human-readable message;
    all other exceptions propagate naturally.
    """
    try:
        return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15)
    except subprocess.TimeoutExpired:
        label = f"sudo {backend}" if via_sudo else backend
        print(
            f"Error: timed out waiting for {label} to respond.\n"
            f"The {backend} daemon may be unresponsive or starting up.",
            file=sys.stderr,
        )
        sys.exit(1)


def _exec_in_container(container_info: dict, cli_args: list):
    """Replace the current process with a command inside the managed container.

    Probes whether sudo is needed (rootful containers), then os.execvp
    into the container. On success the Python process is replaced entirely
    and the container's exit code becomes the process exit code (OS semantics).
    On failure, OSError propagates naturally.

    Args:
        container_info: dict with backend, container_name, exec_user, hermes_bin
        cli_args: the original CLI arguments (everything after 'openagents')
    """

    backend = container_info["backend"]
    container_name = container_info["container_name"]
    exec_user = container_info["exec_user"]
    hermes_bin = container_info["hermes_bin"]

    runtime = shutil.which(backend)
    if not runtime:
        print(
            f"Error: {backend} not found on PATH. Cannot route to container.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Rootful containers (NixOS systemd service) are invisible to unprivileged
    # users — Podman uses per-user namespaces, Docker needs group access.
    # Probe whether the runtime can see the container; if not, try via sudo.
    inspect_cmd = [runtime, "inspect", "--format", "ok", container_name]
    cmd_prefix = [runtime]
    if _probe_container(inspect_cmd, backend).returncode != 0:
        sudo_path = shutil.which("sudo")
        if not sudo_path:
            print(
                f"Error: container '{container_name}' not found via {backend}.\n"
                f"The container may be running under root. Try: sudo hermes {' '.join(cli_args)}",
                file=sys.stderr,
            )
            sys.exit(1)
        cmd_prefix = [sudo_path, "-n", runtime]
        if _probe_container(cmd_prefix[:2] + inspect_cmd, backend, via_sudo=True).returncode != 0:
            print(
                f"Error: container '{container_name}' not found via {backend}.\n"
                f"\n"
                f"The container is likely running as root. Your user cannot see it\n"
                f"because {backend} uses per-user namespaces. Grant passwordless\n"
                f"sudo for {backend} — the -n (non-interactive) flag is required\n"
                f"because a password prompt would hang or break piped commands.\n"
                f"\n"
                f"On NixOS:\n"
                f"\n"
                f"  security.sudo.extraRules = [{{\n"
                f'    users = [ "{os.getenv("USER", "your-user")}" ];\n'
                f'    commands = [{{ command = "{runtime}"; options = [ "NOPASSWD" ]; }}];\n'
                f"  }}];\n"
                f"\n"
                f"Or run: sudo hermes {' '.join(cli_args)}",
                file=sys.stderr,
            )
            sys.exit(1)

    env_flags = []
    for var in ("TERM", "COLORTERM", "LANG", "LC_ALL"):
        val = os.environ.get(var)
        if val:
            env_flags.extend(["-e", f"{var}={val}"])

    exec_cmd = (
        cmd_prefix
        + ["exec", "-it" if sys.stdin.isatty() else "-i", "-u", exec_user]
        + env_flags
        + [container_name, hermes_bin]
        + cli_args
    )
    os.execvp(exec_cmd[0], exec_cmd)


def _resolve_session_by_name_or_id(name_or_id: str) -> Optional[str]:
    """Resolve a session title or ID to a session ID (None if neither matches).

    A compression root is followed forward to its latest continuation so an
    old root ID (exit summary, notes) resumes at the live tip.
    """
    with _session_db() as db:
        # Exact session ID first, then title (with auto-latest for lineage).
        session = db.get_session(name_or_id)
        resolved_id = session["id"] if session else db.resolve_session_by_title(name_or_id)
        if resolved_id:
            # Project forward through compression chain so resumes land on
            # the live tip instead of a dead compressed parent.
            try:
                resolved_id = db.get_compression_tip(resolved_id) or resolved_id
            except Exception:
                pass
        return resolved_id
    return None


def _create_titled_session(title: str) -> Optional[str]:
    """Create a fresh titled session (``chat -c <title> --create-if-missing``).

    Same timestamp+uuid id shape the CLI uses; the title is recorded with
    user provenance so auto-titling never overwrites it.

    Used by ``chat -c <title> --create-if-missing`` (#86794): programmatic callers (plugins, scripts) that
    want "send to this named thread, making it if needed" get a deterministic outcome instead of a silent
    no-op.
    """
    db = None
    try:
        from openagents_state_ids import new_session_id as mint_session_id
        from openagents_state_registry import acquire

        new_session_id = mint_session_id()
        # The CLI acquires the registry handle for this same path moments later; share it
        # instead of minting a second writer for one INSERT (close() releases the refcount).
        db = acquire()
        db.create_session(new_session_id, source="cli")
        db.set_session_title(new_session_id, title)
        return new_session_id
    except Exception:
        # Programmatic callers rely on --create-if-missing being deterministic;
        # swallow the failure but log the cause so it lands in errors.log
        # (DB lock, I/O error, import error — all otherwise invisible).
        # See #86794.
        logger.exception("Failed to create titled session %r", title)
        return None
    finally:
        if db is not None:
            try:
<<<<<<< HEAD
                from openagents_cli.dep_ensure import ensure_dependency
                if ensure_dependency("node"):
                    path = shutil.which("node")
            except Exception:
                pass
        if not path:
            print(f"{bin} not found — install Node.js to use the TUI.")
            sys.exit(1)
        return path

    # Footgun: --dev against a prebuilt bundle that has no source/node_modules.
    ext_dir = os.environ.get("HERMES_TUI_DIR")
    if tui_dev and ext_dir:
        print(
            f"Error: --dev is incompatible with HERMES_TUI_DIR={ext_dir}\n"
            f"The prebuilt TUI has no source code to hot-reload.\n"
            f"Unset HERMES_TUI_DIR (e.g. `unset HERMES_TUI_DIR`) to use --dev from a checkout.",
            file=sys.stderr,
        )
        sys.exit(1)

    if not ext_dir:
        _ensure_tui_workspace(tui_dir)

    # 1. Prebuilt bundle (nix / packaged release): just run it.
    if not tui_dev:
        if ext_dir:
            p = Path(ext_dir)
            if (p / "dist" / "entry.js").is_file():
                node = _node_bin("node")
                return [node, "--expose-gc", str(p / "dist" / "entry.js")], p

        # 1b. Bundled in wheel (pip install)
        bundled = _find_bundled_tui()
        if bundled is not None:
            node = _node_bin("node")
            return [node, "--expose-gc", str(bundled)], bundled.parent

    # 2. Normal flow: npm install if needed, always esbuild, then node dist/entry.js.
    #    --dev flow: npm install if needed, then tsx src/entry.tsx.
    #    Existing desktop behaviour runs npm from the workspace root.  Termux
    #    scopes the install to ui-tui so launch does not pull desktop/web
    #    dependencies into the hot path.
    did_install = False
    termux_startup = _is_termux_startup_environment()
    termux_need_rebuild = False
    if termux_startup and not tui_dev:
        termux_need_rebuild = _tui_need_rebuild(tui_dir)

    skip_install_for_fresh_termux_bundle = (
        termux_startup and not tui_dev and not termux_need_rebuild
    )
    if (
        not skip_install_for_fresh_termux_bundle
        and _tui_need_npm_install(tui_dir)
    ):
        npm = _node_bin("npm")
        if not os.environ.get("OPENAGENTS_QUIET"):
            print("Installing TUI dependencies…")
        npm_cwd = _workspace_root(tui_dir)
        # --workspace ui-tui avoids resolving apps/desktop (Electron + node-pty).
        # See #38772.
        # When ui-tui/ has its own package-lock.json (e.g. curl install),
        # _workspace_root() returns tui_dir itself.  Passing --workspace in
        # that case fails because npm cannot find a workspace named "ui-tui"
        # inside ui-tui/.  See #42973.
        npm_workspace_args: tuple[str, ...] = () if npm_cwd == tui_dir else ("--workspace", "ui-tui")
        if termux_startup:
            npm_cwd, npm_workspace_args = _termux_workspace_install_context(
                tui_dir,
                include_child_workspaces=True,
            )
        result = subprocess.run(
            [
                npm,
                "install",
                *npm_workspace_args,
                "--silent",
                "--no-fund",
                "--no-audit",
                "--progress=false",
            ],
            cwd=str(npm_cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env={**os.environ, "CI": "1"},
        )
        if result.returncode != 0:
            combined = f"{result.stdout or ''}\n{result.stderr or ''}".strip()
            preview = "\n".join(combined.splitlines()[-30:])
            print("npm install failed.")
            if preview:
                print(preview)
            sys.exit(1)
        did_install = True

    if tui_dev:
        # Keep the local @hermes/ink package exports in sync with source.
        # --dev runs src/entry.tsx directly, but @hermes/ink resolves through
        # packages/hermes-ink/dist/entry-exports.js. If that dist bundle is
        # stale after a pull, newer hooks/components can exist in src while
        # being missing at runtime (e.g. useCursorAdvance). Prebuild it here.
        npm = _node_bin("npm")
        ink_dir = tui_dir / "packages" / "hermes-ink"
        result = subprocess.run(
            [npm, "run", "build"],
            cwd=str(ink_dir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode != 0:
            combined = f"{result.stdout or ''}{result.stderr or ''}".strip()
            preview = "\n".join(combined.splitlines()[-30:])
            print("TUI dev prebuild failed.")
            if preview:
                print(preview)
            sys.exit(1)

        tsx = tui_dir / "node_modules" / ".bin" / "tsx"
        if tsx.exists():
            return [str(tsx), "src/entry.tsx"], tui_dir
        return [npm, "start"], tui_dir

    # Desktop/dev launches retain the historical "always rebuild" behaviour.
    # Termux cold starts use the freshness check because esbuild startup is
    # expensive on old mobile CPUs.
    should_build = True
    if termux_startup:
        should_build = did_install or termux_need_rebuild

    if should_build:
        npm = _node_bin("npm")
        result = subprocess.run(
            [npm, "run", "build"],
            cwd=str(tui_dir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode != 0:
            combined = f"{result.stdout or ''}{result.stderr or ''}".strip()
            preview = "\n".join(combined.splitlines()[-30:])
            print("TUI build failed.")
            if preview:
                print(preview)
            sys.exit(1)

    node = _node_bin("node")
    return [node, "--expose-gc", str(tui_dir / "dist" / "entry.js")], tui_dir


def _normalize_tui_toolsets(toolsets: object) -> list[str]:
    """Normalize argparse/Fire-style toolset input for the TUI subprocess."""
    try:
        from openagents_cli.oneshot import _normalize_toolsets

        return _normalize_toolsets(toolsets) or []
    except (AttributeError, ImportError):
        if not toolsets:
            return []

        raw_items = [toolsets] if isinstance(toolsets, str) else toolsets
        if not isinstance(raw_items, (list, tuple)):
            raw_items = [raw_items]

        normalized: list[str] = []
        for item in raw_items:
            if isinstance(item, str):
                normalized.extend(part.strip() for part in item.split(","))
            else:
                normalized.append(str(item).strip())

        return [item for item in normalized if item]


def _read_cgroup_memory_limit() -> Optional[int]:
    """Return the container memory limit in bytes, or None if unconstrained.

    Node's V8 heap is NOT cgroup-aware: with a flat ``--max-old-space-size=8192``
    it happily grows the heap toward 8GB regardless of the container's real
    memory limit.  In a Docker/k8s container capped below ~9-10GB, the cgroup
    OOM-killer SIGKILLs Node before V8's own heap monitor ever fires — which
    runs no JS handler, writes no ``[tui-parent]`` breadcrumb, and the user
    sees only a bare gateway ``stdin EOF``.  Reading the real cgroup limit lets
    us size the heap cap below it so V8 GCs/exits gracefully instead of being
    reaped silently.

    Checks cgroup v2 (``/sys/fs/cgroup/memory.max``) then v1
    (``/sys/fs/cgroup/memory/memory.limit_in_bytes``).  A literal ``max`` (v2)
    or the v1 "unlimited" sentinel (a huge near-INT64 value) means no limit.
    """
    candidates = (
        "/sys/fs/cgroup/memory.max",  # cgroup v2
        "/sys/fs/cgroup/memory/memory.limit_in_bytes",  # cgroup v1
    )
    for path in candidates:
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = f.read().strip()
        except (OSError, ValueError):
            continue
        if raw == "max":
            return None
        if not raw:
            # Blank/empty file: no usable value here. Fall through to the next
            # candidate (don't mistake an empty v2 file for "unlimited").
            continue
        try:
            limit = int(raw)
        except ValueError:
            continue
        if limit <= 0:
            continue
        # cgroup v1 reports "unlimited" as a huge value (often
        # 0x7FFFFFFFFFFFF000 ≈ 9.2 EB, sometimes PAGE_COUNTER_MAX). Anything
        # at/above ~1 PB is effectively unconstrained — treat as no limit.
        if limit >= (1 << 50):
            return None
        return limit
    return None


def _resolve_tui_heap_mb(default_mb: int = 8192) -> int:
    """Pick a V8 ``--max-old-space-size`` (MB) that fits the container.

    Returns ``default_mb`` (8192) when unconstrained or when the box is large
    enough that 8GB fits.  In a memory-limited container, returns ~75% of the
    cgroup limit so the heap + non-heap RSS stays under the cgroup ceiling,
    clamped to a sane floor (1536MB — below this V8 GC-thrashes and the TUI
    is barely usable).  Never exceeds ``default_mb``.
    """
    limit = _read_cgroup_memory_limit()
    if not limit:
        return default_mb
    limit_mb = limit // (1024 * 1024)
    # Leave headroom for non-heap RSS (Node internals, buffers, the Python
    # gateway child shares the same cgroup): cap the heap at 75% of the limit.
    sized = int(limit_mb * 0.75)
    if sized >= default_mb:
        return default_mb
    # Floor so a tiny limit doesn't drive V8 into constant GC. If the container
    # is smaller than the floor, honor the limit-derived value anyway (better a
    # graceful V8 exit than a silent cgroup kill).
    return max(1536, sized) if limit_mb > 2048 else sized


def _launch_tui(
    resume_session_id: Optional[str] = None,
    tui_dev: bool = False,
    model: Optional[str] = None,
    provider: Optional[str] = None,
    toolsets: object = None,
    skills: object = None,
    verbose: Optional[bool] = None,
    quiet: bool = False,
    query: Optional[str] = None,
    image: Optional[str] = None,
    worktree: bool = False,
    checkpoints: bool = False,
    pass_session_id: bool = False,
    max_turns: Optional[int] = None,
    accept_hooks: bool = False,
):
    """Replace current process with the TUI."""
    if not quiet and not query:
        try:
            from openagents_cli.config import load_config
            from openagents_cli.skin_engine import init_skin_from_config
            from openagents_cli.startup_animation import play_startup_animation

            _cfg = load_config()
            init_skin_from_config(_cfg)
            _display = _cfg.get("display") or {}
            play_startup_animation(enabled=bool(_display.get("startup_animation", True)))
        except Exception:
            pass

    tui_dir = PROJECT_ROOT / "ui-tui"

    import tempfile

    env = os.environ.copy()
    try:
        from openagents_cli.config import apply_terminal_config_to_env
        apply_terminal_config_to_env(env=env)
    except Exception:
        logger.debug("Failed to apply terminal config bridge for TUI launch", exc_info=True)
    active_session_fd, active_session_file = tempfile.mkstemp(
        prefix="hermes-tui-active-session-", suffix=".json"
    )
    os.close(active_session_fd)
    env["HERMES_TUI_ACTIVE_SESSION_FILE"] = active_session_file
    env["HERMES_PYTHON_SRC_ROOT"] = os.environ.get(
        "HERMES_PYTHON_SRC_ROOT", str(PROJECT_ROOT)
    )
    env.setdefault("HERMES_PYTHON", sys.executable)
    env.setdefault("HERMES_CWD", os.getcwd())
    env.setdefault("NODE_ENV", "development" if tui_dev else "production")

    wt_info = None
    if worktree:
        try:
            from cli import (
                _cleanup_worktree,
                _git_repo_root,
                _prune_stale_worktrees,
                _setup_worktree,
            )

            repo = _git_repo_root()
            if repo:
                _prune_stale_worktrees(repo)
            wt_info = _setup_worktree()
        except Exception as exc:
            print(f"✗ Failed to create TUI worktree: {exc}", file=sys.stderr)
            wt_info = None
        if not wt_info:
            sys.exit(1)
        env["HERMES_CWD"] = wt_info["path"]
        env["TERMINAL_CWD"] = wt_info["path"]

    if model:
        env["HERMES_MODEL"] = model
        env["HERMES_INFERENCE_MODEL"] = model
    if provider:
        env["HERMES_TUI_PROVIDER"] = provider
        env["HERMES_INFERENCE_PROVIDER"] = provider
    tui_toolsets = _normalize_tui_toolsets(toolsets)
    if tui_toolsets:
        env["HERMES_TUI_TOOLSETS"] = ",".join(tui_toolsets)
    if skills:
        if isinstance(skills, (list, tuple)):
            flattened = []
            for item in skills:
                flattened.extend(
                    part.strip() for part in str(item).split(",") if part.strip()
                )
            if flattened:
                env["HERMES_TUI_SKILLS"] = ",".join(flattened)
        else:
            value = str(skills).strip()
            if value:
                env["HERMES_TUI_SKILLS"] = value
    if query:
        env["HERMES_TUI_QUERY"] = query
    if image:
        env["HERMES_TUI_IMAGE"] = image
    if checkpoints:
        env["HERMES_TUI_CHECKPOINTS"] = "1"
    if pass_session_id:
        env["HERMES_TUI_PASS_SESSION_ID"] = "1"
    if max_turns is not None:
        env["HERMES_TUI_MAX_TURNS"] = str(max_turns)
    if verbose:
        env["HERMES_TUI_TOOL_PROGRESS"] = "verbose"
    elif quiet:
        env["HERMES_TUI_TOOL_PROGRESS"] = "off"
    if accept_hooks:
        env["HERMES_ACCEPT_HOOKS"] = "1"
    # Guarantee a generous V8 heap for the TUI. Default node cap is ~1.5–4GB
    # depending on version and can fatal-OOM on long sessions with large
    # transcripts / reasoning blobs. We target 8GB on an unconstrained host,
    # but V8 is NOT cgroup-aware: in a memory-limited Docker/k8s container a
    # flat 8GB heap grows past the container limit and the cgroup OOM-killer
    # SIGKILLs Node — running no JS handler, writing no breadcrumb, leaving the
    # user with only a bare gateway `stdin EOF`. _resolve_tui_heap_mb() reads
    # the real cgroup limit and sizes the cap below it so V8 GCs/exits
    # gracefully (and the memory monitor's onCritical breadcrumb can fire)
    # instead of being reaped silently. Token-level merge: respect any
    # user-supplied --max-old-space-size (they may have set it higher).
    # --expose-gc is *not* added here: Node rejects it in NODE_OPTIONS
    # ("--expose-gc is not allowed in NODE_OPTIONS") and refuses to start.
    # It is passed as a direct argv flag in _make_tui_argv() instead.
    _tokens = env.get("NODE_OPTIONS", "").split()
    if not any(t.startswith("--max-old-space-size=") for t in _tokens):
        _tokens.append(f"--max-old-space-size={_resolve_tui_heap_mb()}")
    env["NODE_OPTIONS"] = " ".join(_tokens)
    # HERMES_TUI_RESUME is an internal hand-off from the Python wrapper to the
    # Ink app.  Because we start from os.environ.copy(), an exported/stale value
    # in the user's shell would otherwise make a plain `hermes --tui` try to
    # resume a non-existent session and leave the UI at "error: session not
    # found" with no live session.  Only forward a resume id that argparse
    # resolved for this invocation; direct `node ui-tui/dist/entry.js` users can
    # still set HERMES_TUI_RESUME themselves.
    env.pop("HERMES_TUI_RESUME", None)
    if resume_session_id:
        env["HERMES_TUI_RESUME"] = resume_session_id

    argv, cwd = _make_tui_argv(tui_dir, tui_dev)
    code: Optional[int] = None
    try:
        try:
            code = subprocess.call(argv, cwd=str(cwd), env=env)
        except KeyboardInterrupt:
            code = 130

        if code in {0, 130}:
            _print_tui_exit_summary(resume_session_id, active_session_file)
    finally:
        try:
            os.unlink(active_session_file)
        except OSError:
            pass
        if wt_info:
            try:
                _cleanup_worktree(wt_info)
||||||| cf299e9a01
                from openagents_cli.dep_ensure import ensure_dependency
                if ensure_dependency("node"):
                    path = shutil.which("node")
            except Exception:
                pass
        if not path:
            print(f"{bin} not found — install Node.js to use the TUI.")
            sys.exit(1)
        return path

    # Footgun: --dev against a prebuilt bundle that has no source/node_modules.
    ext_dir = os.environ.get("HERMES_TUI_DIR")
    if tui_dev and ext_dir:
        print(
            f"Error: --dev is incompatible with HERMES_TUI_DIR={ext_dir}\n"
            f"The prebuilt TUI has no source code to hot-reload.\n"
            f"Unset HERMES_TUI_DIR (e.g. `unset HERMES_TUI_DIR`) to use --dev from a checkout.",
            file=sys.stderr,
        )
        sys.exit(1)

    if not ext_dir:
        _ensure_tui_workspace(tui_dir)

    # 1. Prebuilt bundle (nix / packaged release): just run it.
    if not tui_dev:
        if ext_dir:
            p = Path(ext_dir)
            if (p / "dist" / "entry.js").is_file():
                node = _node_bin("node")
                return [node, "--expose-gc", str(p / "dist" / "entry.js")], p

        # 1b. Bundled in wheel (pip install)
        bundled = _find_bundled_tui()
        if bundled is not None:
            node = _node_bin("node")
            return [node, "--expose-gc", str(bundled)], bundled.parent

    # 2. Normal flow: npm install if needed, always esbuild, then node dist/entry.js.
    #    --dev flow: npm install if needed, then tsx src/entry.tsx.
    #    Existing desktop behaviour runs npm from the workspace root.  Termux
    #    scopes the install to ui-tui so launch does not pull desktop/web
    #    dependencies into the hot path.
    did_install = False
    termux_startup = _is_termux_startup_environment()
    termux_need_rebuild = False
    if termux_startup and not tui_dev:
        termux_need_rebuild = _tui_need_rebuild(tui_dir)

    skip_install_for_fresh_termux_bundle = (
        termux_startup and not tui_dev and not termux_need_rebuild
    )
    if (
        not skip_install_for_fresh_termux_bundle
        and _tui_need_npm_install(tui_dir)
    ):
        npm = _node_bin("npm")
        if not os.environ.get("OPENAGENTS_QUIET"):
            print("Installing TUI dependencies…")
        npm_cwd = _workspace_root(tui_dir)
        # --workspace ui-tui avoids resolving apps/desktop (Electron + node-pty).
        # See #38772.
        # When ui-tui/ has its own package-lock.json (e.g. curl install),
        # _workspace_root() returns tui_dir itself.  Passing --workspace in
        # that case fails because npm cannot find a workspace named "ui-tui"
        # inside ui-tui/.  See #42973.
        npm_workspace_args: tuple[str, ...] = () if npm_cwd == tui_dir else ("--workspace", "ui-tui")
        if termux_startup:
            npm_cwd, npm_workspace_args = _termux_workspace_install_context(
                tui_dir,
                include_child_workspaces=True,
            )
        result = subprocess.run(
            [
                npm,
                "install",
                *npm_workspace_args,
                "--silent",
                "--no-fund",
                "--no-audit",
                "--progress=false",
            ],
            cwd=str(npm_cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env={**os.environ, "CI": "1"},
        )
        if result.returncode != 0:
            combined = f"{result.stdout or ''}\n{result.stderr or ''}".strip()
            preview = "\n".join(combined.splitlines()[-30:])
            print("npm install failed.")
            if preview:
                print(preview)
            sys.exit(1)
        did_install = True

    if tui_dev:
        # Keep the local @hermes/ink package exports in sync with source.
        # --dev runs src/entry.tsx directly, but @hermes/ink resolves through
        # packages/hermes-ink/dist/entry-exports.js. If that dist bundle is
        # stale after a pull, newer hooks/components can exist in src while
        # being missing at runtime (e.g. useCursorAdvance). Prebuild it here.
        npm = _node_bin("npm")
        ink_dir = tui_dir / "packages" / "hermes-ink"
        result = subprocess.run(
            [npm, "run", "build"],
            cwd=str(ink_dir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode != 0:
            combined = f"{result.stdout or ''}{result.stderr or ''}".strip()
            preview = "\n".join(combined.splitlines()[-30:])
            print("TUI dev prebuild failed.")
            if preview:
                print(preview)
            sys.exit(1)

        tsx = tui_dir / "node_modules" / ".bin" / "tsx"
        if tsx.exists():
            return [str(tsx), "src/entry.tsx"], tui_dir
        return [npm, "start"], tui_dir

    # Desktop/dev launches retain the historical "always rebuild" behaviour.
    # Termux cold starts use the freshness check because esbuild startup is
    # expensive on old mobile CPUs.
    should_build = True
    if termux_startup:
        should_build = did_install or termux_need_rebuild

    if should_build:
        npm = _node_bin("npm")
        result = subprocess.run(
            [npm, "run", "build"],
            cwd=str(tui_dir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode != 0:
            combined = f"{result.stdout or ''}{result.stderr or ''}".strip()
            preview = "\n".join(combined.splitlines()[-30:])
            print("TUI build failed.")
            if preview:
                print(preview)
            sys.exit(1)

    node = _node_bin("node")
    return [node, "--expose-gc", str(tui_dir / "dist" / "entry.js")], tui_dir


def _normalize_tui_toolsets(toolsets: object) -> list[str]:
    """Normalize argparse/Fire-style toolset input for the TUI subprocess."""
    try:
        from openagents_cli.oneshot import _normalize_toolsets

        return _normalize_toolsets(toolsets) or []
    except (AttributeError, ImportError):
        if not toolsets:
            return []

        raw_items = [toolsets] if isinstance(toolsets, str) else toolsets
        if not isinstance(raw_items, (list, tuple)):
            raw_items = [raw_items]

        normalized: list[str] = []
        for item in raw_items:
            if isinstance(item, str):
                normalized.extend(part.strip() for part in item.split(","))
            else:
                normalized.append(str(item).strip())

        return [item for item in normalized if item]


def _read_cgroup_memory_limit() -> Optional[int]:
    """Return the container memory limit in bytes, or None if unconstrained.

    Node's V8 heap is NOT cgroup-aware: with a flat ``--max-old-space-size=8192``
    it happily grows the heap toward 8GB regardless of the container's real
    memory limit.  In a Docker/k8s container capped below ~9-10GB, the cgroup
    OOM-killer SIGKILLs Node before V8's own heap monitor ever fires — which
    runs no JS handler, writes no ``[tui-parent]`` breadcrumb, and the user
    sees only a bare gateway ``stdin EOF``.  Reading the real cgroup limit lets
    us size the heap cap below it so V8 GCs/exits gracefully instead of being
    reaped silently.

    Checks cgroup v2 (``/sys/fs/cgroup/memory.max``) then v1
    (``/sys/fs/cgroup/memory/memory.limit_in_bytes``).  A literal ``max`` (v2)
    or the v1 "unlimited" sentinel (a huge near-INT64 value) means no limit.
    """
    candidates = (
        "/sys/fs/cgroup/memory.max",  # cgroup v2
        "/sys/fs/cgroup/memory/memory.limit_in_bytes",  # cgroup v1
    )
    for path in candidates:
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = f.read().strip()
        except (OSError, ValueError):
            continue
        if raw == "max":
            return None
        if not raw:
            # Blank/empty file: no usable value here. Fall through to the next
            # candidate (don't mistake an empty v2 file for "unlimited").
            continue
        try:
            limit = int(raw)
        except ValueError:
            continue
        if limit <= 0:
            continue
        # cgroup v1 reports "unlimited" as a huge value (often
        # 0x7FFFFFFFFFFFF000 ≈ 9.2 EB, sometimes PAGE_COUNTER_MAX). Anything
        # at/above ~1 PB is effectively unconstrained — treat as no limit.
        if limit >= (1 << 50):
            return None
        return limit
    return None


def _resolve_tui_heap_mb(default_mb: int = 8192) -> int:
    """Pick a V8 ``--max-old-space-size`` (MB) that fits the container.

    Returns ``default_mb`` (8192) when unconstrained or when the box is large
    enough that 8GB fits.  In a memory-limited container, returns ~75% of the
    cgroup limit so the heap + non-heap RSS stays under the cgroup ceiling,
    clamped to a sane floor (1536MB — below this V8 GC-thrashes and the TUI
    is barely usable).  Never exceeds ``default_mb``.
    """
    limit = _read_cgroup_memory_limit()
    if not limit:
        return default_mb
    limit_mb = limit // (1024 * 1024)
    # Leave headroom for non-heap RSS (Node internals, buffers, the Python
    # gateway child shares the same cgroup): cap the heap at 75% of the limit.
    sized = int(limit_mb * 0.75)
    if sized >= default_mb:
        return default_mb
    # Floor so a tiny limit doesn't drive V8 into constant GC. If the container
    # is smaller than the floor, honor the limit-derived value anyway (better a
    # graceful V8 exit than a silent cgroup kill).
    return max(1536, sized) if limit_mb > 2048 else sized


def _launch_tui(
    resume_session_id: Optional[str] = None,
    tui_dev: bool = False,
    model: Optional[str] = None,
    provider: Optional[str] = None,
    toolsets: object = None,
    skills: object = None,
    verbose: Optional[bool] = None,
    quiet: bool = False,
    query: Optional[str] = None,
    image: Optional[str] = None,
    worktree: bool = False,
    checkpoints: bool = False,
    pass_session_id: bool = False,
    max_turns: Optional[int] = None,
    accept_hooks: bool = False,
):
    """Replace current process with the TUI."""
    tui_dir = PROJECT_ROOT / "ui-tui"

    import tempfile

    env = os.environ.copy()
    try:
        from openagents_cli.config import apply_terminal_config_to_env
        apply_terminal_config_to_env(env=env)
    except Exception:
        logger.debug("Failed to apply terminal config bridge for TUI launch", exc_info=True)
    active_session_fd, active_session_file = tempfile.mkstemp(
        prefix="hermes-tui-active-session-", suffix=".json"
    )
    os.close(active_session_fd)
    env["HERMES_TUI_ACTIVE_SESSION_FILE"] = active_session_file
    env["HERMES_PYTHON_SRC_ROOT"] = os.environ.get(
        "HERMES_PYTHON_SRC_ROOT", str(PROJECT_ROOT)
    )
    env.setdefault("HERMES_PYTHON", sys.executable)
    env.setdefault("HERMES_CWD", os.getcwd())
    env.setdefault("NODE_ENV", "development" if tui_dev else "production")

    wt_info = None
    if worktree:
        try:
            from cli import (
                _cleanup_worktree,
                _git_repo_root,
                _prune_stale_worktrees,
                _setup_worktree,
            )

            repo = _git_repo_root()
            if repo:
                _prune_stale_worktrees(repo)
            wt_info = _setup_worktree()
        except Exception as exc:
            print(f"✗ Failed to create TUI worktree: {exc}", file=sys.stderr)
            wt_info = None
        if not wt_info:
            sys.exit(1)
        env["HERMES_CWD"] = wt_info["path"]
        env["TERMINAL_CWD"] = wt_info["path"]

    if model:
        env["HERMES_MODEL"] = model
        env["HERMES_INFERENCE_MODEL"] = model
    if provider:
        env["HERMES_TUI_PROVIDER"] = provider
        env["HERMES_INFERENCE_PROVIDER"] = provider
    tui_toolsets = _normalize_tui_toolsets(toolsets)
    if tui_toolsets:
        env["HERMES_TUI_TOOLSETS"] = ",".join(tui_toolsets)
    if skills:
        if isinstance(skills, (list, tuple)):
            flattened = []
            for item in skills:
                flattened.extend(
                    part.strip() for part in str(item).split(",") if part.strip()
                )
            if flattened:
                env["HERMES_TUI_SKILLS"] = ",".join(flattened)
        else:
            value = str(skills).strip()
            if value:
                env["HERMES_TUI_SKILLS"] = value
    if query:
        env["HERMES_TUI_QUERY"] = query
    if image:
        env["HERMES_TUI_IMAGE"] = image
    if checkpoints:
        env["HERMES_TUI_CHECKPOINTS"] = "1"
    if pass_session_id:
        env["HERMES_TUI_PASS_SESSION_ID"] = "1"
    if max_turns is not None:
        env["HERMES_TUI_MAX_TURNS"] = str(max_turns)
    if verbose:
        env["HERMES_TUI_TOOL_PROGRESS"] = "verbose"
    elif quiet:
        env["HERMES_TUI_TOOL_PROGRESS"] = "off"
    if accept_hooks:
        env["HERMES_ACCEPT_HOOKS"] = "1"
    # Guarantee a generous V8 heap for the TUI. Default node cap is ~1.5–4GB
    # depending on version and can fatal-OOM on long sessions with large
    # transcripts / reasoning blobs. We target 8GB on an unconstrained host,
    # but V8 is NOT cgroup-aware: in a memory-limited Docker/k8s container a
    # flat 8GB heap grows past the container limit and the cgroup OOM-killer
    # SIGKILLs Node — running no JS handler, writing no breadcrumb, leaving the
    # user with only a bare gateway `stdin EOF`. _resolve_tui_heap_mb() reads
    # the real cgroup limit and sizes the cap below it so V8 GCs/exits
    # gracefully (and the memory monitor's onCritical breadcrumb can fire)
    # instead of being reaped silently. Token-level merge: respect any
    # user-supplied --max-old-space-size (they may have set it higher).
    # --expose-gc is *not* added here: Node rejects it in NODE_OPTIONS
    # ("--expose-gc is not allowed in NODE_OPTIONS") and refuses to start.
    # It is passed as a direct argv flag in _make_tui_argv() instead.
    _tokens = env.get("NODE_OPTIONS", "").split()
    if not any(t.startswith("--max-old-space-size=") for t in _tokens):
        _tokens.append(f"--max-old-space-size={_resolve_tui_heap_mb()}")
    env["NODE_OPTIONS"] = " ".join(_tokens)
    # HERMES_TUI_RESUME is an internal hand-off from the Python wrapper to the
    # Ink app.  Because we start from os.environ.copy(), an exported/stale value
    # in the user's shell would otherwise make a plain `hermes --tui` try to
    # resume a non-existent session and leave the UI at "error: session not
    # found" with no live session.  Only forward a resume id that argparse
    # resolved for this invocation; direct `node ui-tui/dist/entry.js` users can
    # still set HERMES_TUI_RESUME themselves.
    env.pop("HERMES_TUI_RESUME", None)
    if resume_session_id:
        env["HERMES_TUI_RESUME"] = resume_session_id

    argv, cwd = _make_tui_argv(tui_dir, tui_dev)
    code: Optional[int] = None
    try:
        try:
            code = subprocess.call(argv, cwd=str(cwd), env=env)
        except KeyboardInterrupt:
            code = 130

        if code in {0, 130}:
            _print_tui_exit_summary(resume_session_id, active_session_file)
    finally:
        try:
            os.unlink(active_session_file)
        except OSError:
            pass
        if wt_info:
            try:
                _cleanup_worktree(wt_info)
=======
                db.close()
>>>>>>> rb/tag
            except Exception:
                pass


def _resolve_continue_arg(args, *, use_tui: bool) -> None:
    """Resolve ``-c/--continue`` into ``args.resume``.

    ``-c <name>``: resolve by title/ID; on miss fail loudly on **stderr** (exit
    1) so programmatic callers see it even under quiet mode, or with
    ``--create-if-missing`` create a fresh titled session. Bare ``-c``: this
    terminal's breadcrumb session if valid, else the MRU session.

    Handles both forms: See #86794.
    """
    continue_val = getattr(args, "continue_last", None)
    if continue_val and not getattr(args, "resume", None):
        if isinstance(continue_val, str):
            resolved = _resolve_session_by_name_or_id(continue_val)
            if resolved:
                args.resume = resolved
            elif getattr(args, "create_if_missing", False):
                # "send to this named thread, making it if needed" — without it
                # a quiet send to a not-yet-existing session silently no-ops.
                # --create-if-missing: no session matches the title — create a new session with that title
                # and proceed. See #86794.
                new_sid = _create_titled_session(continue_val)
                if new_sid:
                    args.resume = new_sid
                else:
                    print(
                        f"No session found matching '{continue_val}' and "
                        "a new titled session could not be created.",
                        file=sys.stderr,
                    )
                    sys.exit(1)
            else:
                print(f"No session found matching '{continue_val}'.", file=sys.stderr)
                print(
                    "Use 'hermes sessions list' to see available sessions, or "
                    "pass --create-if-missing to start a new session with that title.",
                    file=sys.stderr,
                )
                sys.exit(1)
        else:
            # Bare -c: this terminal's breadcrumb (so side-by-side terminals
            # each continue their own conversation), else the MRU session
            # (also when session.terminal_continue is false).
            if getattr(args, "create_if_missing", False):
                # Nothing to create without a name — surface the no-op.
                print(
                    "--create-if-missing requires a session name: "
                    "`-c <name> --create-if-missing`",
                    file=sys.stderr,
                )
            try:
                from openagents_cli.terminal_breadcrumbs import resolve_breadcrumb_session

                _crumb_id = resolve_breadcrumb_session()
            except Exception:
                _crumb_id = None
            if _crumb_id:
                args.resume = _crumb_id
            else:
                # No valid breadcrumb — continue the most recent session
                last_id = _latest_session_id(use_tui)
                if last_id:
                    args.resume = last_id
                else:
                    kind = "TUI" if use_tui else "CLI"
                    print(
                        f"No previous {kind} session to continue. Start a new one with "
                        "`hermes`, or list sessions with `hermes sessions list`.",
                        file=sys.stderr,
                    )
                    sys.exit(1)


def _apply_in_dir(args) -> None:
    """--in DIR: chdir first so workspace-scoped lookups key off DIR; pins the session there."""
    in_dir = getattr(args, "in_dir", None)
    if not in_dir:
        return
    # Git Bash / MSYS hands us POSIX-style paths (`--in ~` → `/c/Users/x`);
    # translate drive-root spellings to native Windows form. No-op elsewhere.
    from tools.environments.local import _msys_to_windows_path

    _target_dir = os.path.abspath(os.path.expanduser(_msys_to_windows_path(in_dir)))
    if not os.path.isdir(_target_dir):
        print(f"Error: --in directory not found: {in_dir}")
        sys.exit(1)
    try:
        os.chdir(_target_dir)
    except OSError as e:
        print(f"Error: cannot enter --in directory {in_dir}: {e}")
        sys.exit(1)
    # Every cwd consumer (resolve_agent_cwd -> Codex app-server thread cwd, the
    # terminal tool, context-file discovery) prefers TERMINAL_CWD over the process
    # cwd, so a value inherited from a parent surface, the shell or .env outlives
    # this chdir and re-homes the session in the old directory (#106220). Refresh
    # it. An unset variable stays unset: the backends then derive from the new
    # process cwd (local exports it at cli import, docker mounts it, ssh and
    # container backends keep their own remote/sandbox default).
    if os.environ.get("TERMINAL_CWD", "").strip():
        os.environ["TERMINAL_CWD"] = _target_dir
    args.no_restore_cwd = True


def _import_foreign_resume(args) -> None:
    """--resume @claude / @codex: import a foreign session and resume it."""
    _resume_foreign = getattr(args, "resume", None)
    if not (isinstance(_resume_foreign, str) and _resume_foreign.strip().lower() in ("@claude", "@codex")):
        return
    from openagents_cli.foreign_sessions import import_foreign_session, pick_foreign_session

    _picked = pick_foreign_session(_resume_foreign.strip().lower().lstrip("@"))
    if _picked is None:
        sys.exit(1)
    try:
        _imported_id = import_foreign_session(_picked.source, _picked.path)
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)
    print(f"✓ Imported as {_imported_id} — resuming it now.")
    print(f"  (later: hermes --resume {_imported_id})")
    args.resume = _imported_id


def _resolve_chat_session_args(args, use_tui: bool) -> None:
    """Normalize --in / --resume / --continue on ``args`` before agent init.

    Order matters: ``--in DIR`` chdirs first so workspace-scoped "latest"/-c
    lookups key off DIR (and pins the session there, skipping cwd restore);
    then ``--resume latest`` → MRU id, ``--continue`` → ``--resume``,
    ``--resume @claude/@codex`` → imported session id, title → id; finally
    cd back into a resumed session's recorded cwd (best-effort, opt-out via
    --no-restore-cwd, skipped under --worktree).
    """
    _apply_in_dir(args)

    # --resume latest: same resolution as bare `-c`. The keyword wins over a
    # session literally titled "latest" (still reachable by ID or `-c latest`).
    _resume_raw = getattr(args, "resume", None)
    if isinstance(_resume_raw, str) and _resume_raw.strip().lower() == "latest":
        _last_id = _latest_session_id(use_tui)
        if _last_id:
            args.resume = _last_id
        else:
            kind = "TUI" if use_tui else "CLI"
            print(f"No previous {kind} session found to resume.")
            print("Use 'hermes sessions list' to see available sessions.")
            sys.exit(1)

    _resolve_continue_arg(args, use_tui=use_tui)

    _import_foreign_resume(args)

    resume_val = getattr(args, "resume", None)
    if resume_val:
        # On miss keep the original so _init_agent reports "Session not found" with it.
        args.resume = _resolve_session_by_name_or_id(resume_val) or resume_val

<<<<<<< HEAD
    try:
        from openagents_cli.session_launch import apply_session_on_launch

        apply_session_on_launch(args, use_tui=use_tui)
    except Exception:
        pass

    # xAI retirement warning — one-shot, non-blocking, never fails startup
||||||| cf299e9a01
    # xAI retirement warning — one-shot, non-blocking, never fails startup
=======
    # cd back into a resumed session's recorded cwd (opt out: --no-restore-cwd;
    # --worktree owns its own dir). A missing dir warns and stays put.
    if (
        getattr(args, "resume", None)
        and not getattr(args, "no_restore_cwd", False)
        and not getattr(args, "worktree", False)
    ):
        with _session_db() as db:  # never let cwd-restore break a resume
            _saved_cwd = ((db.get_session(args.resume) or {}).get("cwd") or "").strip()
            if _saved_cwd and not os.path.isdir(_saved_cwd):
                print(f"⚠ session's recorded dir is gone ({_saved_cwd}); staying in {os.getcwd()}")
            elif _saved_cwd and os.path.realpath(_saved_cwd) != os.path.realpath(os.getcwd()):
                os.chdir(_saved_cwd)
                print(f"↪ restored workspace dir: {_saved_cwd}")


def _warn_retired_xai_models() -> None:
    """One-shot xAI retirement warning on stderr; non-blocking, never fails startup."""
>>>>>>> rb/tag
    try:
        from openagents_cli.xai_retirement import (
            MIGRATION_GUIDE_URL,
            RETIREMENT_DATE,
            find_retired_xai_refs,
            format_issue,
        )
        from openagents_cli.config import load_config as _load_config_for_xai_check

        _retired_xai_refs = find_retired_xai_refs(_load_config_for_xai_check())
        if _retired_xai_refs:
            sys.stderr.write(
                f"\033[33m⚠ xAI retires {len(_retired_xai_refs)} model(s) "
                f"in your config on {RETIREMENT_DATE}:\033[0m\n"
            )
            for _ref in _retired_xai_refs:
                sys.stderr.write(f"  \033[33m⚠\033[0m {format_issue(_ref)}\n")
            sys.stderr.write(f"  \033[2mMigration guide: {MIGRATION_GUIDE_URL}\033[0m\n")
            sys.stderr.write("  \033[2mRun 'hermes doctor' for details.\033[0m\n\n")
    except Exception:
        pass


def _start_chat_background_prefetch() -> None:
    """Kick off the update-check/banner prefetch and the bundled-skills sync.

    Update check is opt-in on Termux (it imports rich/prompt_toolkit in the
    foreground and competes for CPU on single-core devices). The skills sync
    is idempotent and hash-gated (~120-170ms of rglob/hashing) so it normally
    runs in a daemon thread — skill loading happens at agent init, long after.
    The ONE exception is an unseeded ~/.openagents/skills: there the banner
    prefetch races the sync and caches an empty index ("No skills installed"
    on the very first launch), so the first run syncs in the foreground and
    drops the banner's skills cache.
    """
    if _termux_should_prefetch_update_check():
        try:
            from openagents_cli.banner import prefetch_banner_data, prefetch_update_check

            prefetch_update_check()
            prefetch_banner_data()  # git banner state + skills index off-thread
        except Exception:
            pass

    def _skills_dir_is_unseeded() -> bool:
        try:
            from openagents_cli.config import get_openagents_home
            skills_dir = Path(get_openagents_home()) / "skills"
            if not skills_dir.is_dir():
                return True
            return next(skills_dir.rglob("SKILL.md"), None) is None
        except Exception:
            return False

    def _skills_sync_bg() -> None:
        try:
            _sync_bundled_skills_for_startup()
        except Exception:
            pass

    if _skills_dir_is_unseeded():
        _skills_sync_bg()
        # Drop the banner's possibly-empty skills cache so it recomputes.
        try:
            import openagents_cli.banner as _banner_mod
            _banner_mod._available_skills_cache = None
        except Exception:
            pass
    else:
        threading.Thread(
            target=_skills_sync_bg, name="bundled-skills-sync", daemon=True
        ).start()


def _first_run_setup_guard(args) -> None:
    """No provider configured: offer `hermes setup` (TTY) or exit 1 with guidance."""
    print()
    print(
        "It looks like OpenAgents isn't configured yet -- no API keys or providers found."
    )
    print()
    print("  Run:  hermes setup")
    print()

    from openagents_cli.setup import (
        is_interactive_stdin,
        print_noninteractive_setup_guidance,
    )

    if not is_interactive_stdin():
        print_noninteractive_setup_guidance(
            "No interactive TTY detected for the first-run setup prompt."
        )
        sys.exit(1)

    try:
        reply = input("Run setup now? [Y/n] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        reply = "n"
    if reply in {"", "y", "yes"}:
        cmd_setup(args)
        return
    print()
    print("You can run 'hermes setup' at any time to configure.")
    sys.exit(1)


def _read_query_file(args) -> None:
    """--query-file: read the single query from a file (or stdin via '-').

    Callers never have to shell-quote message bodies — this is the transport
    the Bot Mode DM protocol uses; interpolating arbitrary text into a
    double-quoted shell argument truncates on quotes and executes $(...)
    (see tools/bot_mode_probe.py).
    """
    _qfile = getattr(args, "query_file", None)
    if not _qfile:
        return
    if args.query:
        # argparse's mutually-exclusive group catches the normal CLI path;
        # this guards programmatic callers that fill the namespace directly.
        print("Error: -q/--query and --query-file are mutually exclusive", file=sys.stderr)
        sys.exit(2)
    try:
        if _qfile == "-":
            args.query = sys.stdin.read()
        else:
            with open(_qfile, "r", encoding="utf-8", errors="replace") as _fh:
                args.query = _fh.read()
    except OSError as _e:
        print(f"Error: cannot read --query-file {_qfile}: {_e}", file=sys.stderr)
        sys.exit(2)
    if not (args.query or "").strip():
        print(f"Error: --query-file {_qfile} is empty", file=sys.stderr)
        sys.exit(2)


# args attr -> (kwarg, default) passed through to _launch_tui / cli.main.
_CHAT_PASSTHROUGH = (
    ("provider", None), ("toolsets", None), ("skills", None), ("verbose", None),
    ("quiet", False), ("query", None), ("image", None), ("resume", None),
    ("worktree", False), ("checkpoints", False), ("pass_session_id", False),
    ("max_turns", None),
)


def cmd_chat(args):
    """Run interactive chat CLI."""
    _apply_safe_mode(args)
    _apply_user_config_bypass(args)
    _guard_noninteractive_user_config(args)
    from openagents_cli.stream_json import stream_json_requested
    # Structured stdout is a non-interactive protocol: it overrides HERMES_TUI/display.interface too.
    use_tui = False if stream_json_requested(args) else _resolve_use_tui(args)

    _resolve_chat_session_args(args, use_tui)

    _warn_retired_xai_models()

    # First-run guard: the free-tier bootstrap runs first (synchronously here; it is the only thing
    # that may create the identity), then the inventory decides whether setup is needed.
    from openagents_cli.free_tier_bootstrap import run_bootstrap

    run_bootstrap(announce=False)
    if not _has_any_provider_configured():
        _first_run_setup_guard(args)
        return

    _start_chat_background_prefetch()

    # --yolo: bypass all dangerous command approvals. main() also sets this
    # before _prepare_agent_startup() — the authoritative site, since it runs
    # before tool imports freeze _YOLO_MODE_FROZEN. This is a safety net for
    # callers that invoke cmd_chat directly (e.g. subcommand dispatch).
    if getattr(args, "yolo", False):
        os.environ["HERMES_YOLO_MODE"] = "1"
    # --ignore-rules: skip AGENTS.md/SOUL.md/.cursorrules injection, memory
    # entries and preloaded skills (AIAgent(skip_context_files, skip_memory)).
    if getattr(args, "ignore_rules", False):
        os.environ["HERMES_IGNORE_RULES"] = "1"
    # --source: tag session source for filtering (e.g. 'tool' for integrations)
    if getattr(args, "source", None):
        os.environ["HERMES_SESSION_SOURCE"] = args.source
        # Explicit flag, not a label inherited from a parent TUI/Desktop session — one-shot
        # runs must keep it (see run_agent._session_source_for_agent).
        os.environ["HERMES_SESSION_SOURCE_EXPLICIT"] = "1"

    _pin_kanban_board_env()
    _confirm_startup_expensive_model_override(args)

    passthrough = {k: getattr(args, k, d) for k, d in _CHAT_PASSTHROUGH}
    if use_tui:
        _launch_tui(
            passthrough.pop("resume"),
            tui_dev=getattr(args, "tui_dev", False),
            model=getattr(args, "model", None),
            accept_hooks=getattr(args, "accept_hooks", False),
            **passthrough,
        )

    _read_query_file(args)

    safe_mode = getattr(args, "safe_mode", False)
    kwargs = {
        "model": args.model,
        "reasoning": getattr(args, "reasoning", None),
        "toolsets": args.toolsets,
        "query": args.query,
        "oneshot": bool(getattr(args, "oneshot_exit", False)),
        "run_budget": getattr(args, "run_budget", None),
        "output_format": getattr(args, "output_format", "text"),
        "ignore_rules": getattr(args, "ignore_rules", False) or safe_mode,
        "ignore_user_config": getattr(args, "ignore_user_config", False) or safe_mode,
        "compact": getattr(args, "compact", False),
        **{k: getattr(args, k, d) for k, d in _CHAT_PASSTHROUGH},
    }
    kwargs = {k: v for k, v in kwargs.items() if v is not None}

    try:
        from cli import main as cli_main

        cli_main(**kwargs)
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)
    except ImportError as e:
        # Mixed-version installs (new cli.py, older openagents_cli.config) crash
        # here — e.g. missing resolve_turn_limit / split_model_config_default
        # (#96900). The agent-setup mixin prints this hint too late: HermesCLI
        # construction already failed. Fast-chat launch also goes through
        # cmd_chat, so this one catch covers `hermes` / `hermes chat`.
        from openagents_constants import emit_partial_update_hint

        if emit_partial_update_hint(e):
            sys.exit(1)
        raise


def cmd_gateway(args):
    """Gateway management commands."""
    _sync_bundled_skills_quietly()

    from openagents_cli.gateway import gateway_command

    gateway_command(args)


def cmd_proxy(args):
    """Local OpenAI-compatible proxy to OAuth providers."""
    # aiohttp is an extras install; keep it off the common path.
    from openagents_cli.proxy.cli import cmd_proxy as _cmd_proxy

    rc = _cmd_proxy(args)
    if isinstance(rc, int) and rc != 0:
        raise SystemExit(rc)


def _forward_command(name: str, module: str, attr: str, *, forward_return: bool = False, doc: str = ""):
    """A ``hermes <cmd>`` handler that hands ``args`` to ``<module>.<attr>``.

    Imports at CALL time so fast paths never pay for it and
    ``patch("<module>.<attr>")`` keeps intercepting. ``forward_return``
    surfaces the return code to ``main()`` (kanban/project/mcp propagate).
    """

    def _cmd(args):
        import importlib

        result = getattr(importlib.import_module(module), attr)(args)
        return result if forward_return else None

    _cmd.__name__ = _cmd.__qualname__ = name
    _cmd.__doc__ = doc or None
    return _cmd


cmd_setup = _forward_command("cmd_setup", "openagents_cli.setup", "run_setup_wizard", doc='Interactive setup wizard.')
cmd_login = _forward_command("cmd_login", "openagents_cli.auth", "login_command", doc='Authenticate OpenAgents CLI with a provider.')
cmd_logout = _forward_command("cmd_logout", "openagents_cli.auth", "logout_command", doc='Clear provider authentication.')
cmd_auth = _forward_command("cmd_auth", "openagents_cli.auth_commands", "auth_command", doc='Manage pooled credentials.')
cmd_status = _forward_command("cmd_status", "openagents_cli.status", "show_status", doc='Show status of all components.')
cmd_cron = _forward_command("cmd_cron", "openagents_cli.cron", "cron_command", forward_return=True, doc='Cron job management.')
cmd_webhook = _forward_command("cmd_webhook", "openagents_cli.webhook", "webhook_command", doc='Webhook subscription management.')
cmd_kanban = _forward_command("cmd_kanban", "openagents_cli.kanban", "kanban_command", forward_return=True, doc='Multi-profile collaboration board.')
cmd_project = _forward_command("cmd_project", "openagents_cli.projects_cmd", "projects_command", forward_return=True, doc='Manage projects (named, multi-folder workspaces).')
cmd_hooks = _forward_command("cmd_hooks", "openagents_cli.hooks", "hooks_command", doc='Shell-hook inspection and management.')
cmd_doctor = _forward_command("cmd_doctor", "openagents_cli.doctor", "run_doctor", forward_return=True, doc='Check configuration and dependencies.')
cmd_dump = _forward_command("cmd_dump", "openagents_cli.dump", "run_dump", doc='Dump setup summary for support/debugging.')
cmd_debug = _forward_command("cmd_debug", "openagents_cli.debug", "run_debug", doc='Debug tools (share report, etc.).')
cmd_skin = _forward_command("cmd_skin", "openagents_cli.skin_cmd", "skin_command", doc='Skin management (list / use / set).')
cmd_import = _forward_command("cmd_import", "openagents_cli.backup", "run_import", doc='Restore a OpenAgents backup from a zip file.')
cmd_dashboard_register = _forward_command("cmd_dashboard_register", "openagents_cli.dashboard_register", "cmd_dashboard_register", doc='Register a self-hosted dashboard OAuth client with Nous Portal.')
cmd_gateway_enroll = _forward_command("cmd_gateway_enroll", "openagents_cli.gateway_enroll", "cmd_gateway_enroll", doc='Enroll a self-hosted gateway with a relay connector.')
cmd_prompt_size = _forward_command("cmd_prompt_size", "openagents_cli.prompt_size", "cmd_prompt_size", doc='Show a byte/char breakdown of the system prompt + tool schemas.')
cmd_pairing = _forward_command("cmd_pairing", "openagents_cli.pairing", "pairing_command")
cmd_plugins = _forward_command("cmd_plugins", "openagents_cli.plugins_cmd", "plugins_command")
cmd_mcp = _forward_command("cmd_mcp", "openagents_cli.mcp_config", "mcp_command", forward_return=True)
cmd_claw = _forward_command("cmd_claw", "openagents_cli.claw", "claw_command")
cmd_import_agent = _forward_command("cmd_import_agent", "openagents_cli.agent_import", "import_agent_command")


def cmd_model(args):
    """Select default model — starts with provider selection, then model picker."""
    _require_tty("model")
    if getattr(args, "refresh", False):
        try:
            from openagents_cli.models import clear_provider_models_cache
            clear_provider_models_cache()
            print("  Cleared model picker cache.")
        except Exception:
            pass
    from openagents_cli.setup import run_setup_action_with_navigation

    run_setup_action_with_navigation(
        "Model & Provider",
        lambda: select_provider_and_model(args=args),
        cancelled_message="No change.",
    )


# Provider id -> flow(config, current_model, args). Lambdas resolve the
# _model_flow_* names at call time so test monkeypatches keep intercepting.
# ``custom:*``, remove-custom and the generic API-key set are the fallthrough
# branches in select_provider_and_model.
_PROVIDER_MODEL_FLOWS = {
    "openrouter": lambda c, m, a: _model_flow_openrouter(c, m),
    "moa": lambda c, m, a: _model_flow_moa(c, m),
    "ai-gateway": lambda c, m, a: _model_flow_ai_gateway(c, m),
    "nous": lambda c, m, a: _model_flow_nous(c, m, args=a),
    "openai-codex": lambda c, m, a: _model_flow_openai_codex(c, m),
    "xai-oauth": lambda c, m, a: _model_flow_xai_oauth(c, m, args=a),
    "qwen-oauth": lambda c, m, a: _model_flow_qwen_oauth(c, m),
    "minimax-oauth": lambda c, m, a: _model_flow_minimax_oauth(c, m, args=a),
    "copilot-acp": lambda c, m, a: _model_flow_copilot_acp(c, m),
    "copilot": lambda c, m, a: _model_flow_copilot(c, m),
    "custom": lambda c, m, a: _model_flow_custom(c),
    "anthropic": lambda c, m, a: _model_flow_anthropic(c, m),
    "kimi-coding": lambda c, m, a: _model_flow_kimi(c, m),
    "stepfun": lambda c, m, a: _model_flow_stepfun(c, m),
    "bedrock": lambda c, m, a: _model_flow_bedrock(c, m),
    "vertex": lambda c, m, a: _model_flow_vertex(c, m),
    "azure-foundry": lambda c, m, a: _model_flow_azure_foundry(c, m),
}


def _norm_base_url(url: str) -> str:
    return str(url or "").strip().rstrip("/").lower()


def _resolve_active_provider(config, model_cfg, effective_provider, custom_provider_map):
    """Provider slug currently in effect (the picker's default row), or None.

    Order: a saved custom provider whose base_url matches model.base_url →
    the configured/env provider (named custom → canonical map key) → auto
    detection. Unknown/unauthenticated providers warn and fall back to auto.
    """
    from openagents_cli.auth import AuthError, format_auth_error, resolve_provider
    from openagents_cli.config import get_compatible_custom_providers, get_env_value
    from openagents_cli.providers import custom_provider_aliases, resolve_provider_full

    active = ""
    if effective_provider == "custom" and isinstance(model_cfg, dict):
        current_base = _norm_base_url(model_cfg.get("base_url", ""))
        if current_base:
            active = next(
                (k for k, info in custom_provider_map.items()
                 if _norm_base_url(info.get("base_url", "")) == current_base),
                "",
            )
    if not active and effective_provider != "auto":
        active_def = resolve_provider_full(
            effective_provider,
            config.get("providers"),
            get_compatible_custom_providers(config),
        )
        if active_def is not None:
            active = active_def.id
            if active_def.source == "user-config":
                requested = str(active or "").strip().lower()
                active = next(
                    (k for k, info in custom_provider_map.items()
                     if requested in custom_provider_aliases(
                         info.get("name", ""), info.get("provider_key", ""))),
                    active,
                )
        else:
            print(
                f"Warning: Unknown provider '{effective_provider}'. Check 'hermes model' for "
                "available providers, or run 'hermes doctor' to diagnose config "
                "issues. Falling back to auto provider detection."
            )
    if not active:
        try:
            active = resolve_provider("auto")
        except AuthError as exc:
            if exc.code == "no_provider_configured":
                # The picker that is about to open IS the fix; a warning that says
                # "run `hermes model`" from inside `hermes model` is circular.
                print("No provider is set up yet — pick one below. (Nous Portal works without an API key.)")
            elif effective_provider == "auto":
                print(f"Warning: {format_auth_error(exc)} Falling back to auto provider detection.")
            active = None  # no provider yet; default to first in list

    # Detect custom endpoint
    if active == "openrouter" and get_env_value("OPENAI_BASE_URL"):
        active = "custom"
    return active


def _pick_provider(config, active, provider_labels, custom_provider_map):
    """Provider picker (+ group member sub-picker) -> concrete slug, or None on cancel."""
    # Group rows drill into a member sub-picker that resolves back to a
    # concrete slug, so the flow dispatch is unchanged.
    ordered, default_idx = _build_provider_picker_rows(
        config, active, provider_labels, custom_provider_map
    )
    provider_idx = _prompt_provider_choice(
        [label for _, label, _ in ordered],
        default=default_idx,
    )
    if provider_idx is None or ordered[provider_idx][0] == "cancel":
        return None
    selected_key, group_label, selected_members = ordered[provider_idx]
    if not selected_members:
        return selected_key
    # Default to the active member when it lives in this group. The group row
    # carries the descriptive text, so member rows show only their short label.
    member_idx = _prompt_provider_choice(
        [provider_labels.get(m, m) for m in selected_members],
        default=selected_members.index(active) if active in selected_members else 0,
        title=f"Select {group_label.split(' ▸', 1)[0]} provider:",
    )
    return None if member_idx is None else selected_members[member_idx]


def select_provider_and_model(args=None):
    """Core provider selection + model picking logic.

    Shared by ``cmd_model`` (``hermes model``) and the setup wizard
    (``setup_model_provider`` in setup.py).  Handles the full flow:
    provider picker, credential prompting, model selection, and config
    persistence.
    """
    from openagents_cli.config import load_config

    config = load_config()
    model_cfg = config.get("model")
    current_model = model_cfg.get("default", "") if isinstance(model_cfg, dict) else model_cfg
    current_model = current_model or "(not set)"

    # Effective provider the same way the CLI resolves it at startup:
    # config.yaml model.provider > env var > auto-detect
    config_provider = model_cfg.get("provider") if isinstance(model_cfg, dict) else None
    effective_provider = config_provider or os.getenv("HERMES_INFERENCE_PROVIDER") or "auto"

    # User-defined custom providers from config.yaml: key → {name, base_url, api_key}
    _custom_provider_map = _named_custom_provider_map(config)
    active = _resolve_active_provider(config, model_cfg, effective_provider, _custom_provider_map)

    from openagents_cli.models import _PROVIDER_LABELS

    provider_labels = dict(_PROVIDER_LABELS)  # derive from canonical list
    if active and active in _custom_provider_map:
        active_label = _custom_provider_map[active]["name"]
    else:
        active_label = provider_labels.get(active, active) if active else "none"

    print()
    print(f"  Current model:    {current_model}")
    print(f"  Active provider:  {active_label}")
    print()

    selected_provider = _pick_provider(config, active, provider_labels, _custom_provider_map)
    if selected_provider is None:
        print("No change.")
        return
    if selected_provider == "aux-config":
        _aux_config_menu()
        return
    if selected_provider == "reasoning":
        # Effort for the CURRENT default model, no model change.
        _prompt_main_reasoning_effort(current_model, active or "")
        return

    # Provider-specific setup + model selection. Flows resolve the
    # _model_flow_* names at call time so test monkeypatches on
    # openagents_cli.main keep intercepting.
    flow = _PROVIDER_MODEL_FLOWS.get(selected_provider)
    if flow is None and _is_profile_plugin_flow_provider(selected_provider):
        # Registered plugin profile with no bespoke flow: the generic one, keyed by its auth_type.
        flow = lambda c, m, a: _model_flow_plugin_provider(c, selected_provider, m)  # noqa: E731
    if flow is not None:
        flow(config, current_model, args)
    elif (
        selected_provider.startswith("custom:")
        or selected_provider in _custom_provider_map
    ):
        provider_info = _named_custom_provider_map(load_config()).get(selected_provider)
        if provider_info is None:
            print(
                "Warning: the selected saved custom provider is no longer available. "
                "It may have been removed from config.yaml. No change."
            )
            return
        _model_flow_named_custom(config, provider_info)
    elif selected_provider == "remove-custom":
        _remove_custom_provider(config)
    elif (
        selected_provider in _GENERIC_API_KEY_PROVIDERS
        or _is_profile_api_key_provider(selected_provider)
    ):
        _model_flow_api_key_provider(config, selected_provider, current_model)

    # Every flow persists through _save_model_choice; a changed model.default means a pick
    # landed, so offer its reasoning effort here once instead of inside each flow.
    _offer_reasoning_after_pick(current_model)

    # Post-switch cleanup: switching to a named provider (anything except
    # "custom") leaves a stale OPENAI_BASE_URL in ~/.openagents/.env that poisons
    # auxiliary clients using provider:auto — clear it proactively. (#5161)
    if selected_provider not in {
        "custom",
        "cancel",
        "remove-custom",
    } and not selected_provider.startswith("custom:"):
        _clear_stale_openai_base_url()


# Frozen updater surface (PEP 562 ``__getattr__`` below): the frozen
# ``openagents_cli/update_cmd*.py`` files resolve these names via ``_m().<name>``
# on openagents_cli.main; importing update_cmd eagerly would cost every ``hermes``
# invocation ~50-100ms, so they resolve on first read. Nothing else may be
# added here — internal import paths are not a stable API.
_FROZEN_UPDATER_SURFACE: dict[str, tuple[str, ...]] = {
    "openagents_cli.update_cmd": (
        "_abort_dependency_sync_if_self_locked", "_assess_parked_branch_switch",
        "_capture_active_lazy_features", "_capture_active_tool_dependencies",
        "_cold_start_windows_gateway_after_update", "_defer_update_for_self_lock",
        "_dependency_sync_would_rewrite", "_detect_self_loaded_native_modules",
        "_detect_venv_python_processes", "_discard_stashed_changes",
        "_filter_non_gateway_concurrent_instances", "_fleet_probe_expected_runtimes",
        "_get_origin_url", "_handoff_reapable_backend_pids", "_ledger_manual_serve_holders",
        "_ledger_reapable_backend_pids", "_leftover_pausable_gateway_pids", "_npm_lockfile_changed",
        "_orphaned_desktop_backend_pids", "_park_stashed_changes",
        "_pause_windows_gateways_for_update", "_print_parked_branch_kept_notice",
        "_print_parked_branch_skip_warning", "_reapply_plugin_python_dependencies",
        "_refresh_active_lazy_features", "_refresh_active_memory_provider_dependencies",
        "_refresh_bootstrap_cache_scripts", "_refresh_windows_gateway_launchers",
        "_relaunch_stopped_serves",
        "_restore_active_tool_dependencies", "_restore_stashed_changes",
        "_resume_windows_gateways_after_update", "_run_logged_subprocess", "_run_pre_update_backup",
        "_stash_local_changes_if_needed", "_stop_process_trees", "_sync_with_upstream_if_needed",
        "_upgrade_pip_before_lazy_refresh", "_venv_launcher_ancestors",
        "_wait_for_windows_update_gateway_exit", "_warn_orphaned_update_autostashes",
        "_write_update_incomplete_marker",
    ),
    "openagents_cli.dashboard_procs": (
        "_detect_concurrent_hermes_instances", "_kill_stale_dashboard_processes",
    ),
}
_FROZEN_ATTR_SOURCES: dict[str, str] = {
    attr: module for module, attrs in _FROZEN_UPDATER_SURFACE.items() for attr in attrs
}


def __getattr__(name):
    """Resolve the frozen updater surface on first read (see _FROZEN_UPDATER_SURFACE)."""
    module = _FROZEN_ATTR_SOURCES.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    value = getattr(importlib.import_module(module), name)
    globals()[name] = value  # cache: later accesses skip __getattr__
    return value


def cmd_verify(args):
    """Detect a project's run recipe and smoke-test it."""
    from openagents_cli.verify_cmd import run_verify_command

<<<<<<< HEAD
def _set_reasoning_effort(config, effort: str) -> None:
    agent_cfg = config.get("agent")
    if not isinstance(agent_cfg, dict):
        agent_cfg = {}
        config["agent"] = agent_cfg
    agent_cfg["reasoning_effort"] = effort


def _prompt_reasoning_effort_selection(efforts, current_effort=""):
    """Prompt for a reasoning effort. Returns effort, 'none', or None to keep current."""
    deduped = list(
        dict.fromkeys(
            str(effort).strip().lower() for effort in efforts if str(effort).strip()
        )
    )
    canonical_order = ("minimal", "low", "medium", "high", "xhigh")
    ordered = [effort for effort in canonical_order if effort in deduped]
    ordered.extend(effort for effort in deduped if effort not in canonical_order)
    if not ordered:
        return None

    def _label(effort):
        if effort == current_effort:
            return f"{effort}  ← currently in use"
        return effort

    disable_label = "Disable reasoning"
    skip_label = "Skip (keep current)"

    if current_effort == "none":
        default_idx = len(ordered)
    elif current_effort in ordered:
        default_idx = ordered.index(current_effort)
    elif "medium" in ordered:
        default_idx = ordered.index("medium")
    else:
        default_idx = 0

    try:
        from openagents_cli.curses_ui import curses_radiolist

        choices = [_label(effort) for effort in ordered]
        choices.append(disable_label)
        choices.append(skip_label)
        idx = curses_radiolist(
            "Select reasoning effort:",
            choices,
            selected=default_idx,
            cancel_returns=-1,
        )
        if idx < 0:
            return None
        print()
        if idx < len(ordered):
            return ordered[idx]
        if idx == len(ordered):
            return "none"
        return None
    except (ImportError, NotImplementedError, OSError, subprocess.SubprocessError):
        pass

    print("Select reasoning effort:")
    for i, effort in enumerate(ordered, 1):
        print(f"  {i}. {_label(effort)}")
    n = len(ordered)
    print(f"  {n + 1}. {disable_label}")
    print(f"  {n + 2}. {skip_label}")
    print()

    while True:
        try:
            choice = input(f"Choice [1-{n + 2}] (default: keep current): ").strip()
            if not choice:
                return None
            idx = int(choice)
            if 1 <= idx <= n:
                return ordered[idx - 1]
            if idx == n + 1:
                return "none"
            if idx == n + 2:
                return None
            print(f"Please enter 1-{n + 2}")
        except ValueError:
            print("Please enter a number")
        except (KeyboardInterrupt, EOFError):
            return None






def _prompt_api_key(pconfig, existing_key: str, provider_id: str = "") -> tuple:
    """Shared API-key entry point for ``hermes setup`` / ``hermes model``.

    Handles both first-time entry and the already-configured case.  When a key
    is already present, offers [K]eep / [R]eplace / [C]lear so the user can
    recover from a malformed paste without editing ``~/.openagents/.env`` by hand.

    Returns ``(resolved_key, abort)``.  ``abort=True`` means the caller should
    ``return`` immediately — the user cancelled entry, declined to replace, or
    cleared the key and is now unconfigured.
    """
    from openagents_cli.auth import LMSTUDIO_NOAUTH_PLACEHOLDER
    from openagents_cli.config import save_env_value
    from openagents_cli.secret_prompt import masked_secret_prompt

    key_env = pconfig.api_key_env_vars[0] if pconfig.api_key_env_vars else ""

    def _prompt_new_key(*, allow_lmstudio_default: bool) -> str:
        if provider_id == "lmstudio" and allow_lmstudio_default:
            prompt = f"{key_env} (Enter for no-auth default {LMSTUDIO_NOAUTH_PLACEHOLDER!r}): "
        else:
            prompt = f"{key_env} (or Enter to cancel): "
        try:
            entered = masked_secret_prompt(prompt).strip()
        except (KeyboardInterrupt, EOFError):
            print()
            return ""
        if not entered and provider_id == "lmstudio" and allow_lmstudio_default:
            return LMSTUDIO_NOAUTH_PLACEHOLDER
        return entered

    # First-time entry ────────────────────────────────────────────────────
    if not existing_key:
        print(f"No {pconfig.name} API key configured.")
        if not key_env:
            return "", True
        new_key = _prompt_new_key(allow_lmstudio_default=True)
        if not new_key:
            print("Cancelled.")
            return "", True
        save_env_value(key_env, new_key)
        print("API key saved.")
        print()
        return new_key, False

    # Already configured — offer K / R / C ────────────────────────────────
    from openagents_cli.env_loader import format_secret_source_suffix

    source_suffix = format_secret_source_suffix(key_env) if key_env else ""
    print(f"  {pconfig.name} API key: {existing_key[:8]}... ✓{source_suffix}")
    if not key_env:
        # Nothing we can rewrite; just acknowledge and move on.
        print()
        return existing_key, False
    try:
        choice = input("  [K]eep / [R]eplace / [C]lear (default K): ").strip().lower()
    except (KeyboardInterrupt, EOFError):
        print()
        choice = "k"

    if choice.startswith("r"):
        new_key = _prompt_new_key(allow_lmstudio_default=False)
        if not new_key:
            print("  No change.")
            print()
            return existing_key, False
        save_env_value(key_env, new_key)
        print("  API key updated.")
        print()
        return new_key, False

    if choice.startswith("c"):
        save_env_value(key_env, "")
        print(
            f"  API key cleared.  Re-run `hermes setup` to configure {pconfig.name} again."
        )
        return "", True

    # Keep (default, or any other input)
    print()
    return existing_key, False




def _infer_stepfun_region(base_url: str) -> str:
    """Infer the current StepFun region from the configured endpoint."""
    normalized = (base_url or "").strip().lower()
    if "api.stepfun.com" in normalized:
        return "china"
    return "international"


def _stepfun_base_url_for_region(region: str) -> str:
    from openagents_cli.auth import (
        STEPFUN_STEP_PLAN_CN_BASE_URL,
        STEPFUN_STEP_PLAN_INTL_BASE_URL,
    )

    return (
        STEPFUN_STEP_PLAN_CN_BASE_URL
        if region == "china"
        else STEPFUN_STEP_PLAN_INTL_BASE_URL
    )










def _run_anthropic_oauth_flow(save_env_value):
    """Run the Claude OAuth setup-token flow. Returns True if credentials were saved."""
    from agent.anthropic_adapter import (
        run_oauth_setup_token,
        read_claude_code_credentials,
        is_claude_code_token_valid,
    )
    from openagents_cli.config import (
        save_anthropic_oauth_token,
        use_anthropic_claude_code_credentials,
    )

    def _activate_claude_code_credentials_if_available() -> bool:
        try:
            creds = read_claude_code_credentials()
        except Exception:
            creds = None
        if creds and (
            is_claude_code_token_valid(creds) or bool(creds.get("refreshToken"))
        ):
            use_anthropic_claude_code_credentials(save_fn=save_env_value)
            print("  ✓ Claude Code credentials linked.")
            from openagents_constants import display_openagents_home as _dhh_fn

            print(
                f"    OpenAgents will use Claude's credential store directly instead of copying a setup-token into {_dhh_fn()}/.env."
            )
            return True
        return False

    try:
        print()
        print("  Running 'claude setup-token' — follow the prompts below.")
        print("  A browser window will open for you to authorize access.")
        print()
        token = run_oauth_setup_token()
        if token:
            if _activate_claude_code_credentials_if_available():
                return True
            save_anthropic_oauth_token(token, save_fn=save_env_value)
            print("  ✓ OAuth credentials saved.")
            return True

        # Subprocess completed but no token auto-detected — ask user to paste
        print()
        print("  If the setup-token was displayed above, paste it here:")
        print()
        from openagents_cli.secret_prompt import masked_secret_prompt

        try:
            manual_token = masked_secret_prompt(
                "  Paste setup-token (or Enter to cancel): "
            ).strip()
        except (KeyboardInterrupt, EOFError):
            print()
            return False
        if manual_token:
            save_anthropic_oauth_token(manual_token, save_fn=save_env_value)
            print("  ✓ Setup-token saved.")
            return True

        print("  ⚠ Could not detect saved credentials.")
        return False

    except FileNotFoundError:
        # Claude CLI not installed — guide user through manual setup
        print()
        print("  The 'claude' CLI is required for OAuth login.")
        print()
        print("  To install and authenticate:")
        print()
        print("    1. Install Claude Code:  npm install -g @anthropic-ai/claude-code")
        print("    2. Run:                  claude setup-token")
        print("    3. Follow the browser prompts to authorize")
        print("    4. Re-run:               hermes model")
        print()
        print("  Or paste an existing setup-token now (sk-ant-oat-...):")
        print()
        from openagents_cli.secret_prompt import masked_secret_prompt

        try:
            token = masked_secret_prompt("  Setup-token (or Enter to cancel): ").strip()
        except (KeyboardInterrupt, EOFError):
            print()
            return False
        if token:
            save_anthropic_oauth_token(token, save_fn=save_env_value)
            print("  ✓ Setup-token saved.")
            return True
        print("  Cancelled — install Claude Code and try again.")
        return False




def cmd_login(args):
    """Authenticate OpenAgents CLI with a provider."""
    from openagents_cli.auth import login_command

    login_command(args)


def cmd_logout(args):
    """Clear provider authentication."""
    from openagents_cli.auth import logout_command

    logout_command(args)


def cmd_auth(args):
    """Manage pooled credentials."""
    from openagents_cli.auth_commands import auth_command

    auth_command(args)


def cmd_status(args):
    """Show status of all components."""
    from openagents_cli.status import show_status

    show_status(args)


def cmd_cron(args):
    """Cron job management."""
    from openagents_cli.cron import cron_command

    cron_command(args)


def cmd_webhook(args):
    """Webhook subscription management."""
    from openagents_cli.webhook import webhook_command

    webhook_command(args)


def cmd_slack(args):
    """Slack integration helpers.

    Dispatches ``hermes slack <subcommand>``. Currently supports:
      manifest — print or write a Slack app manifest with every gateway
                 command registered as a first-class slash.
    """
    sub = getattr(args, "slack_command", None)
    if sub in {None, ""}:
        # No subcommand — print usage hint.
        print(
            "usage: hermes slack <subcommand>\n"
            "\n"
            "subcommands:\n"
            "  manifest   Generate a Slack app manifest with every gateway\n"
            "             command registered as a native slash\n"
            "\n"
            "Run `hermes slack manifest -h` for details.",
            file=sys.stderr,
        )
        return 1

    if sub == "manifest":
        from openagents_cli.slack_cli import slack_manifest_command

        return slack_manifest_command(args)

    print(f"Unknown slack subcommand: {sub}", file=sys.stderr)
    return 1


def cmd_kanban(args):
    """Multi-profile collaboration board."""
    from openagents_cli.kanban import kanban_command

    return kanban_command(args)


def cmd_project(args):
    """Manage projects (named, multi-folder workspaces)."""
    from openagents_cli.projects_cmd import projects_command

    return projects_command(args)


def cmd_company(args):
    """Create multi-agent company workspaces."""
    from openagents_cli.company_cmd import company_command

    return company_command(args)


def cmd_openagentui(args):
    """Start/stop the local OpenAgentUI visual workflow builder."""
    from openagents_cli.openagentui_cmd import openagentui_command

    return openagentui_command(args)


def cmd_openagentconfig(args):
    """Run/list/inspect saved OpenAgentUI workflows headlessly."""
    from openagents_cli.openagentui_config_cmd import openagentconfig_command

    return openagentconfig_command(args)


def cmd_hooks(args):
    """Shell-hook inspection and management."""
    from openagents_cli.hooks import hooks_command

    hooks_command(args)


def cmd_doctor(args):
    """Check configuration and dependencies."""
    from openagents_cli.doctor import run_doctor

    run_doctor(args)
||||||| cf299e9a01
def _set_reasoning_effort(config, effort: str) -> None:
    agent_cfg = config.get("agent")
    if not isinstance(agent_cfg, dict):
        agent_cfg = {}
        config["agent"] = agent_cfg
    agent_cfg["reasoning_effort"] = effort


def _prompt_reasoning_effort_selection(efforts, current_effort=""):
    """Prompt for a reasoning effort. Returns effort, 'none', or None to keep current."""
    deduped = list(
        dict.fromkeys(
            str(effort).strip().lower() for effort in efforts if str(effort).strip()
        )
    )
    canonical_order = ("minimal", "low", "medium", "high", "xhigh")
    ordered = [effort for effort in canonical_order if effort in deduped]
    ordered.extend(effort for effort in deduped if effort not in canonical_order)
    if not ordered:
        return None

    def _label(effort):
        if effort == current_effort:
            return f"{effort}  ← currently in use"
        return effort

    disable_label = "Disable reasoning"
    skip_label = "Skip (keep current)"

    if current_effort == "none":
        default_idx = len(ordered)
    elif current_effort in ordered:
        default_idx = ordered.index(current_effort)
    elif "medium" in ordered:
        default_idx = ordered.index("medium")
    else:
        default_idx = 0

    try:
        from openagents_cli.curses_ui import curses_radiolist

        choices = [_label(effort) for effort in ordered]
        choices.append(disable_label)
        choices.append(skip_label)
        idx = curses_radiolist(
            "Select reasoning effort:",
            choices,
            selected=default_idx,
            cancel_returns=-1,
        )
        if idx < 0:
            return None
        print()
        if idx < len(ordered):
            return ordered[idx]
        if idx == len(ordered):
            return "none"
        return None
    except (ImportError, NotImplementedError, OSError, subprocess.SubprocessError):
        pass

    print("Select reasoning effort:")
    for i, effort in enumerate(ordered, 1):
        print(f"  {i}. {_label(effort)}")
    n = len(ordered)
    print(f"  {n + 1}. {disable_label}")
    print(f"  {n + 2}. {skip_label}")
    print()

    while True:
        try:
            choice = input(f"Choice [1-{n + 2}] (default: keep current): ").strip()
            if not choice:
                return None
            idx = int(choice)
            if 1 <= idx <= n:
                return ordered[idx - 1]
            if idx == n + 1:
                return "none"
            if idx == n + 2:
                return None
            print(f"Please enter 1-{n + 2}")
        except ValueError:
            print("Please enter a number")
        except (KeyboardInterrupt, EOFError):
            return None






def _prompt_api_key(pconfig, existing_key: str, provider_id: str = "") -> tuple:
    """Shared API-key entry point for ``hermes setup`` / ``hermes model``.

    Handles both first-time entry and the already-configured case.  When a key
    is already present, offers [K]eep / [R]eplace / [C]lear so the user can
    recover from a malformed paste without editing ``~/.openagents/.env`` by hand.

    Returns ``(resolved_key, abort)``.  ``abort=True`` means the caller should
    ``return`` immediately — the user cancelled entry, declined to replace, or
    cleared the key and is now unconfigured.
    """
    from openagents_cli.auth import LMSTUDIO_NOAUTH_PLACEHOLDER
    from openagents_cli.config import save_env_value
    from openagents_cli.secret_prompt import masked_secret_prompt

    key_env = pconfig.api_key_env_vars[0] if pconfig.api_key_env_vars else ""

    def _prompt_new_key(*, allow_lmstudio_default: bool) -> str:
        if provider_id == "lmstudio" and allow_lmstudio_default:
            prompt = f"{key_env} (Enter for no-auth default {LMSTUDIO_NOAUTH_PLACEHOLDER!r}): "
        else:
            prompt = f"{key_env} (or Enter to cancel): "
        try:
            entered = masked_secret_prompt(prompt).strip()
        except (KeyboardInterrupt, EOFError):
            print()
            return ""
        if not entered and provider_id == "lmstudio" and allow_lmstudio_default:
            return LMSTUDIO_NOAUTH_PLACEHOLDER
        return entered

    # First-time entry ────────────────────────────────────────────────────
    if not existing_key:
        print(f"No {pconfig.name} API key configured.")
        if not key_env:
            return "", True
        new_key = _prompt_new_key(allow_lmstudio_default=True)
        if not new_key:
            print("Cancelled.")
            return "", True
        save_env_value(key_env, new_key)
        print("API key saved.")
        print()
        return new_key, False

    # Already configured — offer K / R / C ────────────────────────────────
    from openagents_cli.env_loader import format_secret_source_suffix

    source_suffix = format_secret_source_suffix(key_env) if key_env else ""
    print(f"  {pconfig.name} API key: {existing_key[:8]}... ✓{source_suffix}")
    if not key_env:
        # Nothing we can rewrite; just acknowledge and move on.
        print()
        return existing_key, False
    try:
        choice = input("  [K]eep / [R]eplace / [C]lear (default K): ").strip().lower()
    except (KeyboardInterrupt, EOFError):
        print()
        choice = "k"

    if choice.startswith("r"):
        new_key = _prompt_new_key(allow_lmstudio_default=False)
        if not new_key:
            print("  No change.")
            print()
            return existing_key, False
        save_env_value(key_env, new_key)
        print("  API key updated.")
        print()
        return new_key, False

    if choice.startswith("c"):
        save_env_value(key_env, "")
        print(
            f"  API key cleared.  Re-run `hermes setup` to configure {pconfig.name} again."
        )
        return "", True

    # Keep (default, or any other input)
    print()
    return existing_key, False




def _infer_stepfun_region(base_url: str) -> str:
    """Infer the current StepFun region from the configured endpoint."""
    normalized = (base_url or "").strip().lower()
    if "api.stepfun.com" in normalized:
        return "china"
    return "international"


def _stepfun_base_url_for_region(region: str) -> str:
    from openagents_cli.auth import (
        STEPFUN_STEP_PLAN_CN_BASE_URL,
        STEPFUN_STEP_PLAN_INTL_BASE_URL,
    )

    return (
        STEPFUN_STEP_PLAN_CN_BASE_URL
        if region == "china"
        else STEPFUN_STEP_PLAN_INTL_BASE_URL
    )










def _run_anthropic_oauth_flow(save_env_value):
    """Run the Claude OAuth setup-token flow. Returns True if credentials were saved."""
    from agent.anthropic_adapter import (
        run_oauth_setup_token,
        read_claude_code_credentials,
        is_claude_code_token_valid,
    )
    from openagents_cli.config import (
        save_anthropic_oauth_token,
        use_anthropic_claude_code_credentials,
    )

    def _activate_claude_code_credentials_if_available() -> bool:
        try:
            creds = read_claude_code_credentials()
        except Exception:
            creds = None
        if creds and (
            is_claude_code_token_valid(creds) or bool(creds.get("refreshToken"))
        ):
            use_anthropic_claude_code_credentials(save_fn=save_env_value)
            print("  ✓ Claude Code credentials linked.")
            from openagents_constants import display_openagents_home as _dhh_fn

            print(
                f"    OpenAgents will use Claude's credential store directly instead of copying a setup-token into {_dhh_fn()}/.env."
            )
            return True
        return False

    try:
        print()
        print("  Running 'claude setup-token' — follow the prompts below.")
        print("  A browser window will open for you to authorize access.")
        print()
        token = run_oauth_setup_token()
        if token:
            if _activate_claude_code_credentials_if_available():
                return True
            save_anthropic_oauth_token(token, save_fn=save_env_value)
            print("  ✓ OAuth credentials saved.")
            return True

        # Subprocess completed but no token auto-detected — ask user to paste
        print()
        print("  If the setup-token was displayed above, paste it here:")
        print()
        from openagents_cli.secret_prompt import masked_secret_prompt

        try:
            manual_token = masked_secret_prompt(
                "  Paste setup-token (or Enter to cancel): "
            ).strip()
        except (KeyboardInterrupt, EOFError):
            print()
            return False
        if manual_token:
            save_anthropic_oauth_token(manual_token, save_fn=save_env_value)
            print("  ✓ Setup-token saved.")
            return True

        print("  ⚠ Could not detect saved credentials.")
        return False

    except FileNotFoundError:
        # Claude CLI not installed — guide user through manual setup
        print()
        print("  The 'claude' CLI is required for OAuth login.")
        print()
        print("  To install and authenticate:")
        print()
        print("    1. Install Claude Code:  npm install -g @anthropic-ai/claude-code")
        print("    2. Run:                  claude setup-token")
        print("    3. Follow the browser prompts to authorize")
        print("    4. Re-run:               hermes model")
        print()
        print("  Or paste an existing setup-token now (sk-ant-oat-...):")
        print()
        from openagents_cli.secret_prompt import masked_secret_prompt

        try:
            token = masked_secret_prompt("  Setup-token (or Enter to cancel): ").strip()
        except (KeyboardInterrupt, EOFError):
            print()
            return False
        if token:
            save_anthropic_oauth_token(token, save_fn=save_env_value)
            print("  ✓ Setup-token saved.")
            return True
        print("  Cancelled — install Claude Code and try again.")
        return False




def cmd_login(args):
    """Authenticate OpenAgents CLI with a provider."""
    from openagents_cli.auth import login_command

    login_command(args)


def cmd_logout(args):
    """Clear provider authentication."""
    from openagents_cli.auth import logout_command

    logout_command(args)


def cmd_auth(args):
    """Manage pooled credentials."""
    from openagents_cli.auth_commands import auth_command

    auth_command(args)


def cmd_status(args):
    """Show status of all components."""
    from openagents_cli.status import show_status

    show_status(args)


def cmd_cron(args):
    """Cron job management."""
    from openagents_cli.cron import cron_command

    cron_command(args)


def cmd_webhook(args):
    """Webhook subscription management."""
    from openagents_cli.webhook import webhook_command

    webhook_command(args)


def cmd_slack(args):
    """Slack integration helpers.

    Dispatches ``hermes slack <subcommand>``. Currently supports:
      manifest — print or write a Slack app manifest with every gateway
                 command registered as a first-class slash.
    """
    sub = getattr(args, "slack_command", None)
    if sub in {None, ""}:
        # No subcommand — print usage hint.
        print(
            "usage: hermes slack <subcommand>\n"
            "\n"
            "subcommands:\n"
            "  manifest   Generate a Slack app manifest with every gateway\n"
            "             command registered as a native slash\n"
            "\n"
            "Run `hermes slack manifest -h` for details.",
            file=sys.stderr,
        )
        return 1

    if sub == "manifest":
        from openagents_cli.slack_cli import slack_manifest_command

        return slack_manifest_command(args)

    print(f"Unknown slack subcommand: {sub}", file=sys.stderr)
    return 1


def cmd_kanban(args):
    """Multi-profile collaboration board."""
    from openagents_cli.kanban import kanban_command

    return kanban_command(args)


def cmd_project(args):
    """Manage projects (named, multi-folder workspaces)."""
    from openagents_cli.projects_cmd import projects_command

    return projects_command(args)


def cmd_hooks(args):
    """Shell-hook inspection and management."""
    from openagents_cli.hooks import hooks_command

    hooks_command(args)


def cmd_doctor(args):
    """Check configuration and dependencies."""
    from openagents_cli.doctor import run_doctor

    run_doctor(args)
=======
    sys.exit(run_verify_command(args))
>>>>>>> rb/tag


def cmd_security(args):
    """Dispatch `hermes security <subcmd>`."""
    sub = getattr(args, "security_command", None)
    if sub in ("audit", None):
        from openagents_cli.security_audit import cmd_security_audit

        # Default subcommand is `audit` when no subcmd is given.
        code = cmd_security_audit(args)
        sys.exit(int(code or 0))
    print(f"unknown security subcommand: {sub}", file=sys.stderr)
    sys.exit(2)


def cmd_approvals(args):
    """Dispatch `hermes approvals <subcmd>`."""
    from openagents_cli.approvals_suggest import approvals_command

    status = approvals_command(args)
    if status:
        sys.exit(status)
    return status


def cmd_config(args):
    """Configuration management."""
    from openagents_cli.config import config_command

    try:
        config_command(args)
    except RuntimeError as exc:
        # Fail-closed config write guard (require_readable_config_before_write);
        # covers migrate and future write subcommands so none end in a traceback.
        print(f"✗ {exc}", file=sys.stderr)
        sys.exit(1)


def cmd_backup(args):
    """Back up OpenAgents home directory to a zip file."""
    from openagents_cli import backup

    if getattr(args, "quick", False):
        backup.run_quick_backup(args)
    elif not backup.run_backup(args):
        raise SystemExit(1)  # archive written but incomplete: never shell-success for a timer


def _print_version_info(*, check_updates: bool = True) -> None:
    # Shared with the `hermes --version` pre-import fast path.
    _startup_fast.print_fast_version_info(check_updates=check_updates)


def cmd_version(args):
    """Show version (--version/-V flag)."""
    _print_version_info(check_updates=True)


def cmd_uninstall(args):
    """Uninstall OpenAgents (or just the Chat GUI with --gui).

    ``--yes`` paths run from the desktop app's non-interactive cleanup scripts,
    so the TTY gate applies only when we actually need to prompt.
    """
    # Machine-readable snapshot for the desktop uninstall UI; before any TTY gate.
    if getattr(args, "gui_summary", False):
        from openagents_cli.gui_uninstall import gui_install_summary

        print(json.dumps(gui_install_summary()))
        return

    if getattr(args, "gui", False):
        if not getattr(args, "yes", False):
            _require_tty("uninstall --gui")
        from openagents_cli.uninstall import run_gui_uninstall

        run_gui_uninstall(args)
        return

    if not getattr(args, "yes", False):
        _require_tty("uninstall")
    from openagents_cli.uninstall import run_uninstall

    run_uninstall(args)


def _clear_bytecode_cache(root: Path) -> int:
    """Remove all __pycache__ dirs under *root* (stale .pyc → ImportError after updates).

    Returns the number of directories removed.
    """
    removed = 0
    for dirpath, dirnames, _ in os.walk(root):
        dirnames[:] = [
            d
            for d in dirnames
            if d not in {"venv", ".venv", "node_modules", ".git", ".worktrees"}
        ]
        if os.path.basename(dirpath) == "__pycache__":
            try:
                shutil.rmtree(dirpath)
                removed += 1
            except OSError:
                pass
            dirnames.clear()  # nothing left to recurse into
    return removed


def _finalize_update_receipt(code: int, reason: str) -> None:
    """Best-effort receipt close at the command boundary; no-op if already finalized."""
    try:
        # Receipt boundary (#91283 review): the impl has many early sys.exit paths (concurrent-instance
        # preflight, venv-holder refusal, head-pinned no-op, fetch failure) that never reach an inner
        # finalize. Persist any still-open receipt with the real exit code, then let the exit proceed
        # unchanged. No-op when an inner path already finalized (exactly-once by construction).
        from openagents_cli.update_receipt import finalize_pending_update_receipt

        finalize_pending_update_receipt(code, reason)
    except Exception:
        pass


<<<<<<< HEAD
def _format_time_ago(iso_ts: str) -> str:
    """Render an ISO timestamp as `Xh ago` / `Xd ago` / `Xm ago`. Best effort."""
    try:
        from datetime import datetime, timezone
        ts = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - ts
        secs = int(delta.total_seconds())
        if secs < 60:
            return "just now"
        if secs < 3600:
            return f"{secs // 60}m ago"
        if secs < 86400:
            return f"{secs // 3600}h ago"
        return f"{secs // 86400}d ago"
    except Exception:
        return "recently"


def _kill_stale_dashboard_processes(
    reason: str = "the running backend no longer matches the updated frontend",
) -> None:
    """Kill running ``hermes dashboard`` processes.

    Called at the end of ``hermes update`` (default ``reason``) and also
    from ``hermes dashboard --stop`` (which overrides ``reason``).  The
    dashboard has no service manager, so after a code update the running
    process is guaranteed to be serving stale Python against a
    freshly-updated JS bundle.  Leaving it alive produces silent
    frontend/backend mismatches (new auth headers the old backend doesn't
    recognise → every API call 401s).

    POSIX: SIGTERM, wait up to ~3s for graceful exit, SIGKILL any survivors.
    Windows: ``taskkill /PID <pid> /F`` since there's no clean SIGTERM
    equivalent for background console apps.

    The dashboard isn't auto-restarted because we don't know the original
    launch args (--host, --port, --insecure, --tui, --no-open).  The user
    restarts it manually; a hint is printed.
    """
    # When the OpenAgents Desktop Electron app spawns this dashboard as a
    # backend child, it sets HERMES_DESKTOP_CHILD_PID so that the update
    # path can skip killing the desktop-managed process.  (#37532)
    exclude: set[int] | None = None
    raw_pid = os.environ.get("HERMES_DESKTOP_CHILD_PID")
    if raw_pid:
        # The desktop may manage several backends (one per active profile) and
        # passes them comma-separated; a lone int still parses for back-compat.
        parsed: set[int] = set()
        for part in raw_pid.split(","):
            part = part.strip()
            if not part:
                continue
            try:
                parsed.add(int(part))
            except (ValueError, TypeError):
                pass
        if parsed:
            exclude = parsed

    pids = _find_stale_dashboard_pids(exclude_pids=exclude)
    if not pids:
        return

    print()
    print(f"⟲ Stopping {len(pids)} dashboard process(es) ({reason})")

    killed: list[int] = []
    failed: list[tuple[int, str]] = []

    if sys.platform == "win32":
        for pid in pids:
            try:
                result = subprocess.run(
                    ["taskkill", "/PID", str(pid), "/F"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                if result.returncode == 0:
                    killed.append(pid)
                else:
                    failed.append((pid, (result.stderr or result.stdout or "").strip()))
            except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as e:
                failed.append((pid, str(e)))
    else:
        import signal as _signal
        import time as _time

        # SIGTERM first — give each process a chance to shut down cleanly
        # (uvicorn closes its socket, flushes logs, etc.).
        for pid in pids:
            try:
                os.kill(pid, _signal.SIGTERM)
            except ProcessLookupError:
                # Already gone — count as killed.
                killed.append(pid)
            except (PermissionError, OSError) as e:
                failed.append((pid, str(e)))

        # Poll for exit up to ~3s total.
        deadline = _time.monotonic() + 3.0
        pending = [
            p for p in pids if p not in killed and p not in {f[0] for f in failed}
        ]
        while pending and _time.monotonic() < deadline:
            _time.sleep(0.1)
            still_pending = []
            # On Windows, os.kill(pid, 0) is NOT a no-op. Route through
            # the cross-platform existence check.
            from gateway.status import _pid_exists
            for pid in pending:
                if _pid_exists(pid):
                    still_pending.append(pid)
                else:
                    killed.append(pid)
            pending = still_pending

        # SIGKILL any survivors.
        for pid in pending:
            try:
                os.kill(pid, _signal.SIGKILL)
                killed.append(pid)
            except ProcessLookupError:
                killed.append(pid)
            except (PermissionError, OSError) as e:
                failed.append((pid, str(e)))

    for pid in killed:
        print(f"    ✓ stopped PID {pid}")
    for pid, err_msg in failed:
        print(f"    ✗ failed to stop PID {pid}: {err_msg}")

    if killed:
        print("  Restart the dashboard when you're ready:")
        print("    hermes dashboard --port <port>")


# Back-compat alias: some tests and any external callers may import the old
# warn-only name.  The new behaviour (kill stale processes) replaces it.
_warn_stale_dashboard_processes = _kill_stale_dashboard_processes


def _atomic_replace_dir(src: str, dst: str) -> None:
    """Replace directory *dst* with *src* without leaving *dst* half-deleted.

    The naive ``rmtree(dst); copytree(src, dst)`` has a destructive window: if
    the copy fails partway (common on the Windows ZIP-update path, which only
    runs because file I/O is already flaky on that machine), the old directory
    is already gone and nothing replaced it — the install is left with a
    deleted tree (issue #49145, where ``ui-tui/`` vanished and broke the TUI).

    Instead, stage the new copy into a sibling temp dir first; only once that
    fully succeeds do we swap it in. A failure during staging raises with the
    original *dst* still intact.
    """
    staging = f"{dst}.hermes-update-staging"
    backup = f"{dst}.hermes-update-old"
    # Clear any leftovers from a previously-interrupted update.
    for leftover in (staging, backup):
        if os.path.exists(leftover):
            shutil.rmtree(leftover, ignore_errors=True)

    # 1. Stage the new copy. If this fails, dst is untouched.
    shutil.copytree(src, staging)
    # 2. Swap: move the live dir aside, move staging into place. Both moves are
    #    same-filesystem renames; if the second fails we restore the backup.
    if os.path.exists(dst):
        os.rename(dst, backup)
    try:
        os.rename(staging, dst)
    except OSError:
        if os.path.exists(backup) and not os.path.exists(dst):
            os.rename(backup, dst)  # roll back to the original
        raise
    # 3. New dir is in place; drop the old one (best-effort — never fatal).
    if os.path.exists(backup):
        shutil.rmtree(backup, ignore_errors=True)


def _update_via_zip(args):
    """Update OpenAgents by downloading a ZIP archive.

    Used on Windows when git file I/O is broken (antivirus, NTFS filter
    drivers causing 'Invalid argument' errors on file creation).
    """
    import tempfile
    import zipfile
    from urllib.request import urlretrieve

    # The ZIP fallback exists for Windows git-file-I/O breakage. It pulls a
    # static archive from GitHub, which is fine for the default "main"
    # channel but would silently ignore --branch and update from main even
    # if the user asked for something else — exactly the silent-divergence
    # bug --branch was added to prevent. Refuse to proceed in that case
    # rather than lie.
    branch = _resolve_update_branch(args)
    if branch != "main":
        print(
            f"✗ --branch={branch} is not supported on the Windows ZIP-fallback "
            "update path."
        )
        print(
            "  This path runs when git file I/O is broken on the system. "
            "Either resolve the git-side breakage (typically an antivirus "
            "or NTFS filter holding files open) and rerun `hermes update "
            f"--branch {branch}`, or update against main with `hermes update`."
        )
        sys.exit(1)
    zip_url = (
        f"https://github.com/NousResearch/openagents/archive/refs/heads/{branch}.zip"
    )

    print("→ Downloading latest version...")
    tmp_dir = tempfile.mkdtemp(prefix="hermes-update-")
    try:
        zip_path = os.path.join(tmp_dir, f"openagents-{branch}.zip")
        urlretrieve(zip_url, zip_path)

        print("→ Extracting...")
        import stat as _stat
        with zipfile.ZipFile(zip_path, "r") as zf:
            # Validate paths to prevent zip-slip (path traversal) AND reject
            # symlink members. A GitHub source ZIP for openagents itself
            # should never contain symlinks — they'd point outside the
            # extracted tree and let an attacker who can compromise the
            # update mirror plant arbitrary files via the update path.
            tmp_dir_real = os.path.realpath(tmp_dir)
            for member in zf.infolist():
                member_path = os.path.realpath(os.path.join(tmp_dir, member.filename))
                if (
                    not member_path.startswith(tmp_dir_real + os.sep)
                    and member_path != tmp_dir_real
                ):
                    raise ValueError(
                        f"Zip-slip detected: {member.filename} escapes extraction directory"
                    )
                # Unix mode lives in the upper 16 bits of external_attr;
                # mask to the file-type bits.
                mode = (member.external_attr >> 16) & 0o170000
                if _stat.S_ISLNK(mode):
                    raise ValueError(
                        f"ZIP contains unsupported symlink member: {member.filename}"
                    )
            zf.extractall(tmp_dir)

        # GitHub ZIPs extract to openagents-<branch>/
        extracted = os.path.join(tmp_dir, f"openagents-{branch}")
        if not os.path.isdir(extracted):
            # Try to find it
            for d in os.listdir(tmp_dir):
                candidate = os.path.join(tmp_dir, d)
                if os.path.isdir(candidate) and d != "__MACOSX":
                    extracted = candidate
                    break

        # Copy updated files over existing installation, preserving venv/node_modules/.git
        preserve = {"venv", "node_modules", ".git", ".env"}
        update_count = 0
        for item in os.listdir(extracted):
            if item in preserve:
                continue
            src = os.path.join(extracted, item)
            dst = os.path.join(str(PROJECT_ROOT), item)
            if os.path.isdir(src):
                # Atomic-ish replace: never leave dst half-deleted if the copy
                # fails partway (the failure mode behind #49145 on Windows).
                _atomic_replace_dir(src, dst)
            else:
                shutil.copy2(src, dst)
            update_count += 1

        print(f"✓ Updated {update_count} items from ZIP")

    except Exception as e:
        print(f"✗ ZIP update failed: {e}")
        sys.exit(1)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    # Clear stale bytecode after ZIP extraction
    removed = _clear_bytecode_cache(PROJECT_ROOT)
    if removed:
        print(
            f"  ✓ Cleared {removed} stale __pycache__ director{'y' if removed == 1 else 'ies'}"
        )

    # Reinstall Python dependencies. Prefer .[all], but if one optional extra
    # breaks on this machine, keep base deps and reinstall the remaining extras
    # individually so update does not silently strip working capabilities.
    print("→ Updating Python dependencies...")

    from openagents_cli.managed_uv import ensure_uv, update_managed_uv

    # Keep managed uv current — runs `uv self update` if we already have one.
    update_managed_uv()

    uv_bin = ensure_uv()

    pip_cmd = [sys.executable, "-m", "pip"]
    if not uv_bin:
        uv_bin = _ensure_uv_for_termux(pip_cmd)
    if uv_bin:
        uv_env = {**os.environ, "VIRTUAL_ENV": str(PROJECT_ROOT / "venv")}
        if _is_termux_env(uv_env):
            uv_env.pop("PYTHONPATH", None)
            uv_env.pop("PYTHONHOME", None)
        _install_python_dependencies_with_optional_fallback([uv_bin, "pip"], env=uv_env)
    else:
        # Use sys.executable to explicitly call the venv's pip module,
        # avoiding PEP 668 'externally-managed-environment' errors on Debian/Ubuntu.
        # Some environments lose pip inside the venv; bootstrap it back with
        # ensurepip before trying the editable install.
        try:
            subprocess.run(
                pip_cmd + ["--version"],
                cwd=PROJECT_ROOT,
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError:
            subprocess.run(
                [sys.executable, "-m", "ensurepip", "--upgrade", "--default-pip"],
                cwd=PROJECT_ROOT,
                check=True,
            )
        _install_python_dependencies_with_optional_fallback(pip_cmd)

    _update_node_dependencies()
    _build_web_ui(PROJECT_ROOT / "web")

    # Sync skills
    try:
        from tools.skills_sync import sync_skills

        print("→ Syncing bundled skills...")
        result = sync_skills(quiet=True)
        if result["copied"]:
            print(f"  + {len(result['copied'])} new: {', '.join(result['copied'])}")
        if result.get("updated"):
            print(
                f"  ↑ {len(result['updated'])} updated: {', '.join(result['updated'])}"
            )
        if result.get("user_modified"):
            print(f"  ~ {len(result['user_modified'])} user-modified (kept)")
            print(
                "    → see them: hermes skills list-modified  "
                "(diff/reset to resume updates)"
            )
        if result.get("cleaned"):
            print(f"  − {len(result['cleaned'])} removed from manifest")
        if not result["copied"] and not result.get("updated"):
            print("  ✓ Skills are up to date")
    except Exception:
        pass

    # Seed the model-catalog disk cache from the freshly-unpacked checkout
    # (same rationale as the git-pull path in _cmd_update_impl). Non-fatal.
    try:
        from openagents_cli.model_catalog import seed_cache_from_checkout

        if seed_cache_from_checkout(PROJECT_ROOT):
            print("  ✓ Model catalog cache refreshed from checkout")
    except Exception as e:
        logger.debug("Model catalog seed during zip update failed: %s", e)

    print()
    print("✓ Update complete!")
    try:
        _print_curator_first_run_notice()
    except Exception as e:
        logger.debug("Curator first-run notice failed: %s", e)
    try:
        _print_curator_recent_run_notice()
    except Exception as e:
        logger.debug("Curator recent-run notice failed: %s", e)
    _kill_stale_dashboard_processes()


def _stash_local_changes_if_needed(git_cmd: list[str], cwd: Path) -> Optional[str]:
    status = subprocess.run(
        git_cmd + ["status", "--porcelain"],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    if not status.stdout.strip():
        return None

    # If the index has unmerged entries (e.g. from an interrupted merge/rebase),
    # git stash will fail with "needs merge / could not write index".  Clear the
    # conflict state with `git reset` so the stash can proceed.  Working-tree
    # changes are preserved; only the index conflict markers are dropped.
    unmerged = subprocess.run(
        git_cmd + ["ls-files", "--unmerged"],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    if unmerged.stdout.strip():
        print("→ Clearing unmerged index entries from a previous conflict...")
        subprocess.run(git_cmd + ["reset"], cwd=cwd, capture_output=True)

    from datetime import datetime, timezone

    stash_name = datetime.now(timezone.utc).strftime(
        "hermes-update-autostash-%Y%m%d-%H%M%S"
    )
    print("→ Local changes detected — stashing before update...")
    subprocess.run(
        git_cmd + ["stash", "push", "--include-untracked", "-m", stash_name],
        cwd=cwd,
        check=True,
    )
    stash_ref = subprocess.run(
        git_cmd + ["rev-parse", "--verify", "refs/stash"],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return stash_ref


def _resolve_stash_selector(
    git_cmd: list[str], cwd: Path, stash_ref: str
) -> Optional[str]:
    stash_list = subprocess.run(
        git_cmd + ["stash", "list", "--format=%gd %H"],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    for line in stash_list.stdout.splitlines():
        selector, _, commit = line.partition(" ")
        if commit.strip() == stash_ref:
            return selector.strip()
    return None


def _print_stash_cleanup_guidance(
    stash_ref: str, stash_selector: Optional[str] = None
) -> None:
    print(
        "  Check `git status` first so you don't accidentally reapply the same change twice."
    )
    print("  Find the saved entry with: git stash list --format='%gd %H %s'")
    if stash_selector:
        print(f"  Remove it with: git stash drop {stash_selector}")
    else:
        print(
            f"  Look for commit {stash_ref}, then drop its selector with: git stash drop stash@{{N}}"
        )


def _restore_stashed_changes(
    git_cmd: list[str],
    cwd: Path,
    stash_ref: str,
    prompt_user: bool = False,
    input_fn=None,
) -> bool:
    if prompt_user:
        print()
        print("⚠ Local changes were stashed before updating.")
        print(
            "  Restoring them may reapply local customizations onto the updated codebase."
        )
        print("  Review the result afterward if OpenAgents behaves unexpectedly.")
        print("Restore local changes now? [Y/n]")
        if input_fn is not None:
            response = input_fn("Restore local changes now? [Y/n]", "y")
        else:
            response = input().strip().lower()
        if response not in {"", "y", "yes"}:
            print("Skipped restoring local changes.")
            print("Your changes are still preserved in git stash.")
            print(f"Restore manually with: git stash apply {stash_ref}")
            return False

    print("→ Restoring local changes...")
    restore = subprocess.run(
        git_cmd + ["stash", "apply", stash_ref],
        cwd=cwd,
        capture_output=True,
        text=True,
    )

    # Check for unmerged (conflicted) files — can happen even when returncode is 0
    unmerged = subprocess.run(
        git_cmd + ["diff", "--name-only", "--diff-filter=U"],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    has_conflicts = bool(unmerged.stdout.strip())

    if restore.returncode != 0 or has_conflicts:
        print("✗ Update pulled new code, but restoring local changes hit conflicts.")
        if restore.stdout.strip():
            print(restore.stdout.strip())
        if restore.stderr.strip():
            print(restore.stderr.strip())

        # Show which files conflicted
        conflicted_files = unmerged.stdout.strip()
        if conflicted_files:
            print("\nConflicted files:")
            for f in conflicted_files.splitlines():
                print(f"  • {f}")

        print("\nYour stashed changes are preserved — nothing is lost.")
        print(f"  Stash ref: {stash_ref}")

        # Always reset to clean state — leaving conflict markers in source
        # files makes hermes completely unrunnable (SyntaxError on import).
        # The user's changes are safe in the stash for manual recovery.
        subprocess.run(
            git_cmd + ["reset", "--hard", "HEAD"],
            cwd=cwd,
            capture_output=True,
        )
        print("Working tree reset to clean state.")
        print(f"Restore your changes later with: git stash apply {stash_ref}")
        # Don't sys.exit — the code update itself succeeded, only the stash
        # restore had conflicts.  Let cmd_update continue with pip install,
        # skill sync, and gateway restart.
        return False

    stash_selector = _resolve_stash_selector(git_cmd, cwd, stash_ref)
    if stash_selector is None:
        print(
            "⚠ Local changes were restored, but OpenAgents couldn't find the stash entry to drop."
        )
        print(
            "  The stash was left in place. You can remove it manually after checking the result."
        )
        _print_stash_cleanup_guidance(stash_ref)
    else:
        drop = subprocess.run(
            git_cmd + ["stash", "drop", stash_selector],
            cwd=cwd,
            capture_output=True,
            text=True,
        )
        if drop.returncode != 0:
            print(
                "⚠ Local changes were restored, but OpenAgents couldn't drop the saved stash entry."
            )
            if drop.stdout.strip():
                print(drop.stdout.strip())
            if drop.stderr.strip():
                print(drop.stderr.strip())
            print(
                "  The stash was left in place. You can remove it manually after checking the result."
            )
            _print_stash_cleanup_guidance(stash_ref, stash_selector)

    print("⚠ Local changes were restored on top of the updated codebase.")
    print("  Review `git diff` / `git status` if OpenAgents behaves unexpectedly.")
    return True


def _discard_stashed_changes(
    git_cmd: list[str],
    cwd: Path,
    stash_ref: str,
) -> bool:
    """Throw away a stash created before an update, without applying it.

    Used only on a NON-interactive update when the user has set
    ``updates.non_interactive_local_changes: discard`` — i.e. they've opted out
    of keeping local source edits on this machine. Drops the stash entry
    instead of re-applying it, so the working tree stays clean at the freshly
    pulled HEAD. Unlike ``git reset --hard`` + ``git clean -fd``, this only
    affects what was stashed (tracked changes + the untracked files we
    explicitly captured) — ignored paths like node_modules/venv/build outputs
    are never touched, since they were never stashed.

    Returns True if the stash was dropped, False on a git failure (in which
    case the stash is left in place for safety).
    """
    stash_selector = _resolve_stash_selector(git_cmd, cwd, stash_ref)
    if stash_selector is None:
        print(
            "⚠ Configured to discard local changes on non-interactive update, "
            "but OpenAgents couldn't find the stash entry to drop."
        )
        _print_stash_cleanup_guidance(stash_ref)
        return False

    drop = subprocess.run(
        git_cmd + ["stash", "drop", stash_selector],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    if drop.returncode != 0:
        print(
            "⚠ Configured to discard local changes, but OpenAgents couldn't drop "
            "the saved stash entry."
        )
        if drop.stderr.strip():
            print(f"  {drop.stderr.strip().splitlines()[0]}")
        _print_stash_cleanup_guidance(stash_ref, stash_selector)
        return False

    print("→ Discarded local source changes (updates.non_interactive_local_changes=discard).")
    return True


# =========================================================================
# Fork detection and upstream management for `hermes update`
# =========================================================================

OFFICIAL_REPO_URLS = DISTRIBUTION_REPO_URLS
OFFICIAL_REPO_URL = DISTRIBUTION_REPO_HTTPS
SKIP_UPSTREAM_PROMPT_FILE = ".skip_upstream_prompt"


def _get_origin_url(git_cmd: list[str], cwd: Path) -> Optional[str]:
    """Get the URL of the origin remote, or None if not set."""
    try:
        result = subprocess.run(
            git_cmd + ["remote", "get-url", "origin"],
            cwd=cwd,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None


def _is_fork(origin_url: Optional[str]) -> bool:
    """Check if the origin remote points to a fork (not the official repo)."""
    if not origin_url:
        return False
    # Normalize URL for comparison (strip trailing .git if present)
    normalized = origin_url.rstrip("/")
    if normalized.endswith(".git"):
        normalized = normalized[:-4]
    for official in OFFICIAL_REPO_URLS:
        official_normalized = official.rstrip("/")
        if official_normalized.endswith(".git"):
            official_normalized = official_normalized[:-4]
        if normalized == official_normalized:
            return False
    return True


def _has_upstream_remote(git_cmd: list[str], cwd: Path) -> bool:
    """Check if an 'upstream' remote already exists."""
    try:
        result = subprocess.run(
            git_cmd + ["remote", "get-url", "upstream"],
            cwd=cwd,
            capture_output=True,
            text=True,
        )
        return result.returncode == 0
    except Exception:
        return False


def _get_upstream_url(git_cmd: list[str], cwd: Path) -> Optional[str]:
    """Return the URL of the upstream remote, if configured."""
    try:
        result = subprocess.run(
            git_cmd + ["remote", "get-url", "upstream"],
            cwd=cwd,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None


def _add_upstream_remote(git_cmd: list[str], cwd: Path) -> bool:
    """Add the official repo as the 'upstream' remote. Returns True on success."""
    try:
        result = subprocess.run(
            git_cmd + ["remote", "add", "upstream", OFFICIAL_REPO_URL],
            cwd=cwd,
            capture_output=True,
            text=True,
        )
        return result.returncode == 0
    except Exception:
        return False


def _count_commits_between(git_cmd: list[str], cwd: Path, base: str, head: str) -> int:
    """Count commits on `head` that are not on `base`. Returns -1 on error."""
    try:
        result = subprocess.run(
            git_cmd + ["rev-list", "--count", f"{base}..{head}"],
            cwd=cwd,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return int(result.stdout.strip())
    except Exception:
        pass
    return -1


def _should_skip_upstream_prompt() -> bool:
    """Check if user previously declined to add upstream."""
    from openagents_constants import get_openagents_home

    return (get_openagents_home() / SKIP_UPSTREAM_PROMPT_FILE).exists()


def _mark_skip_upstream_prompt():
    """Create marker file to skip future upstream prompts."""
    try:
        from openagents_constants import get_openagents_home

        (get_openagents_home() / SKIP_UPSTREAM_PROMPT_FILE).touch()
    except Exception:
        pass


def _sync_fork_with_upstream(git_cmd: list[str], cwd: Path) -> bool:
    """Attempt to push updated main to origin (sync fork).

    Returns True if push succeeded, False otherwise.
    """
    try:
        result = subprocess.run(
            git_cmd + ["push", "origin", "main", "--force-with-lease"],
            cwd=cwd,
            capture_output=True,
            text=True,
        )
        return result.returncode == 0
    except Exception:
        return False


def _sync_with_upstream_if_needed(git_cmd: list[str], cwd: Path) -> None:
    """Check if fork is behind upstream and sync if safe.

    This implements the fork upstream sync logic:
    - If upstream remote doesn't exist, ask user if they want to add it
    - Compare origin/main with upstream/main
    - If origin/main is strictly behind upstream/main, pull from upstream
    - Try to sync fork back to origin if possible
    """
    has_upstream = _has_upstream_remote(git_cmd, cwd)

    if not has_upstream:
        # Check if user previously declined
        if _should_skip_upstream_prompt():
            return

        # Ask user if they want to add upstream
        print()
        print("ℹ Your fork is not tracking the official OpenAgents repository.")
        print(f"  This means you may miss updates from {DISTRIBUTION_REPO_HTTPS}.")
        print()
        try:
            response = (
                input("Add official repo as 'upstream' remote? [Y/n]: ").strip().lower()
            )
        except (EOFError, KeyboardInterrupt):
            print()
            response = "n"

        if response in {"", "y", "yes"}:
            print("→ Adding upstream remote...")
            if _add_upstream_remote(git_cmd, cwd):
                print(f"  ✓ Added upstream: {OFFICIAL_REPO_URL}")
                has_upstream = True
            else:
                print("  ✗ Failed to add upstream remote. Skipping upstream sync.")
                return
        else:
            print(f"  Skipped. Run 'git remote add upstream {OFFICIAL_REPO_URL}' to add later.")
            _mark_skip_upstream_prompt()
            return

    # Fetch upstream main only. This sync compares upstream/main with
    # origin/main, so there's no reason to pull every upstream ref — and a bare
    # fetch drags in thousands of auto-generated branches.
    print()
    print("→ Fetching upstream...")
    try:
        subprocess.run(
            git_cmd + ["fetch", "upstream", "main", "--quiet"],
            cwd=cwd,
            capture_output=True,
            check=True,
        )
    except subprocess.CalledProcessError:
        print("  ✗ Failed to fetch upstream. Skipping upstream sync.")
        return

    # Compare origin/main with upstream/main
    origin_ahead = _count_commits_between(git_cmd, cwd, "upstream/main", "origin/main")
    upstream_ahead = _count_commits_between(
        git_cmd, cwd, "origin/main", "upstream/main"
    )

    if origin_ahead < 0 or upstream_ahead < 0:
        print("  ✗ Could not compare branches. Skipping upstream sync.")
        return

    # If origin/main has commits not on upstream, don't trample
    if origin_ahead > 0:
        print()
        print(f"ℹ Your fork has {origin_ahead} commit(s) not on upstream.")
        print("  Skipping upstream sync to preserve your changes.")
        if IS_REBRANDED_HERMES_FORK:
            upstream_url = _get_upstream_url(git_cmd, cwd)
            if upstream_url and "Hermes-agent" in upstream_url:
                print("  To merge a new Hermes Agent release into OpenAgents, run:")
                print(f"    ./{SYNC_FROM_HERMES_SCRIPT}")
            else:
                print("  To pull the latest OpenAgents release, run:")
                print("    openagents update")
        else:
            print("  If you want to merge upstream changes, run:")
            print("    git pull upstream main")
        return

    # If upstream is not ahead, fork is up to date
    if upstream_ahead == 0:
        print("  ✓ Fork is up to date with upstream")
        return

    # origin/main is strictly behind upstream/main (can fast-forward)
    print()
    print(f"→ Fork is {upstream_ahead} commit(s) behind upstream")
    print("→ Pulling from upstream...")

    try:
        subprocess.run(
            git_cmd + ["pull", "--ff-only", "upstream", "main"],
            cwd=cwd,
            check=True,
        )
    except subprocess.CalledProcessError:
        print(
            "  ✗ Failed to pull from upstream. You may need to resolve conflicts manually."
        )
        return

    print("  ✓ Updated from upstream")

    # Try to sync fork back to origin
    print("→ Syncing fork...")
    if _sync_fork_with_upstream(git_cmd, cwd):
        print("  ✓ Fork synced with upstream")
    else:
        print(
            "  ℹ Got updates from upstream but couldn't push to fork (no write access?)"
        )
        print("    Your local repo is updated, but your fork on GitHub may be behind.")


def _invalidate_update_cache():
    """Delete the update-check cache for ALL profiles so no banner
    reports a stale "commits behind" count after a successful update.

    The git repo is shared across profiles — when one profile runs
    ``hermes update``, every profile is now current.
    """
    homes = []
    # Default profile home (Docker-aware — uses /opt/data in Docker)
    from openagents_constants import get_default_openagents_root

    default_home = get_default_openagents_root()
    homes.append(default_home)
    # Named profiles under <root>/profiles/
    profiles_root = default_home / "profiles"
    if profiles_root.is_dir():
        for entry in profiles_root.iterdir():
            if entry.is_dir():
                homes.append(entry)
    for home in homes:
        try:
            cache_file = home / ".update_check"
            if cache_file.exists():
                cache_file.unlink()
        except Exception:
            pass


def _load_installable_optional_extras(group: str = "all") -> list[str]:
    """Return optional extras referenced by a dependency group.

    ``group`` is usually ``all`` (desktop/server broad install) or
    ``termux-all`` (Termux-compatible broad install).
    """
    try:
        import tomllib

        with (PROJECT_ROOT / "pyproject.toml").open("rb") as handle:
            project = tomllib.load(handle).get("project", {})
    except Exception:
        return []

    optional_deps = project.get("optional-dependencies", {})
    if not isinstance(optional_deps, dict):
        return []

    refs = optional_deps.get(group, [])
    referenced: list[str] = []
    for ref in refs:
        if "[" in ref and "]" in ref:
            name = ref.split("[", 1)[1].split("]", 1)[0]
            if name in optional_deps:
                referenced.append(name)

    return referenced


# Install-scoped breadcrumb dropped right before ``hermes update`` mutates the
# venv and cleared only after the dependency install verifies clean.  If a user
# kills the update mid-install (Ctrl-C, terminal close, WSL OOM), the marker
# survives and the next ``hermes`` launch finishes the install instead of
# limping along on a half-built venv (e.g. pip wiped, a core dep like Pillow
# never landed).  Lives next to the venv (not under $OPENAGENTS_HOME) because the
# venv is shared across all profiles, so a single marker covers every profile.
def _update_marker_path() -> Path:
    return PROJECT_ROOT / ".update-incomplete"


def _write_update_incomplete_marker() -> None:
    """Drop the interrupted-install breadcrumb. Never raises."""
    try:
        _update_marker_path().write_text(
            f"started={_time.time()}\npid={os.getpid()}\n", encoding="utf-8"
        )
    except OSError as exc:
        logger.debug("Could not write update-incomplete marker: %s", exc)


def _clear_update_incomplete_marker() -> None:
    """Remove the interrupted-install breadcrumb. Never raises."""
    try:
        _update_marker_path().unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        logger.debug("Could not clear update-incomplete marker: %s", exc)


def _recover_from_interrupted_install() -> None:
    """Finish a dependency install that a prior ``hermes update`` left half-done.

    Triggered on launch when ``.update-incomplete`` is present — meaning the
    code was pulled but the dep install was killed before it verified clean.
    Unconditionally bootstraps pip via ``ensurepip`` (a killed ``pip install``
    can wipe pip from the venv entirely, which blocks the venv from recovering
    on its own), then re-runs the editable ``.[all]`` install + core-dependency
    verification, then clears the marker.

    Never raises: a recovery failure must not block launch.  If it can't
    self-heal it prints the one-line manual command and leaves the marker so
    the next launch tries again.

    Concurrency: the marker lives next to the shared venv, so a gateway start
    plus a CLI launch (or two profiles starting at once) can both see it.  An
    ``O_EXCL`` lockfile ensures only one process runs the reinstall; the
    others skip and let the winner clear the marker.

    Output: everything — our status lines AND the streamed pip/uv install
    (which inherits fd 1) — is routed to stderr.  Launches whose stdout is a
    protocol stream (``hermes acp`` speaks JSON-RPC on stdout) must never get
    install noise on stdout.
    """
    if not _update_marker_path().exists():
        return

    # Skip in managed/Docker installs and on PyPI installs with no git checkout:
    # those don't run the source-tree update path, so a stray marker is not ours
    # to act on. Just clear it.
    if not (PROJECT_ROOT / "pyproject.toml").is_file():
        _clear_update_incomplete_marker()
        return

    # Single-flight guard: atomically claim the recovery lock. If another
    # process holds it, skip — it is running the same reinstall into the same
    # shared venv right now. A crashed holder leaves a stale lock; break it
    # after an hour (well past any realistic install) so recovery can't be
    # wedged forever.
    lock_path = PROJECT_ROOT / ".update-incomplete.lock"
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, f"{os.getpid()}\n".encode())
        os.close(fd)
    except FileExistsError:
        try:
            if _time.time() - lock_path.stat().st_mtime > 3600:
                lock_path.unlink()
        except OSError:
            pass
        return
    except OSError as exc:
        # Couldn't create the lock (read-only fs, perms). Proceed unlocked —
        # the install itself will surface the real problem.
        logger.debug("Could not create install-recovery lock: %s", exc)

    # Windows self-lock guard: if hermes.exe is the launcher that spawned
    # this Python process, any attempt to pip-install will fail with
    # "拒绝访问 / WinError 32" because the running .exe cannot be replaced.
    # Rather than entering the permanent retry loop described in issue
    # #45542, clear the marker and give the user an offline recovery command.
    if _is_windows():
        scripts_dir = _venv_scripts_dir()
        if scripts_dir is not None:
            shims = _hermes_exe_shims(scripts_dir)
            if shims:
                _shim_set: set[str] = set()
                for _s in shims:
                    try:
                        _shim_set.add(str(_s.resolve()).lower())
                    except OSError:
                        _shim_set.add(str(_s).lower())
                try:
                    import psutil
                    _me = psutil.Process()
                    for _anc in [_me] + list(_me.parents()):
                        try:
                            _anc_exe = _anc.exe()
                            _anc_norm = str(Path(_anc_exe).resolve()).lower()
                        except Exception:
                            continue
                        if _anc_norm in _shim_set:
                            print(
                                "✗ OpenAgents is running from the binary that "
                                "needs to be replaced — the auto-recovery "
                                "cannot overwrite a running executable."
                            )
                            print(
                                "  Restart OpenAgents from a different terminal, "
                                "then run the manual recovery command below:"
                            )
                            print(f'    cd /d "{PROJECT_ROOT}"')
                            print(
                                f'    "{sys.executable}" -m pip install '
                                '-e ".[all]"'
                            )
                            _clear_update_incomplete_marker()
                            try:
                                lock_path.unlink()
                            except OSError:
                                pass
                            return
                except Exception:
                    pass  # psutil is best-effort; fall through to install

    saved_stdout_fd = None
    saved_sys_stdout = sys.stdout
    try:
        # Route Python-level prints AND subprocess-inherited fd 1 to stderr
        # for the duration of recovery (see docstring: ACP stdout safety).
        try:
            saved_stdout_fd = os.dup(1)
            os.dup2(2, 1)
        except OSError:
            saved_stdout_fd = None
        sys.stdout = sys.stderr

        print(
            "⚠ A previous `hermes update` was interrupted mid-install — "
            "finishing dependency installation now..."
        )

        try:
            from openagents_cli.managed_uv import ensure_uv

            # Always bootstrap pip first: a killed install can leave the venv with
            # no pip module at all, and uv may also be gone. ensurepip restores a
            # known-good pip so at least the plain-pip path below can proceed.
            try:
                subprocess.run(
                    [sys.executable, "-m", "ensurepip", "--upgrade", "--default-pip"],
                    cwd=PROJECT_ROOT,
                    capture_output=True,
                )
            except Exception as exc:
                logger.debug("ensurepip during install recovery failed: %s", exc)

            uv_bin = ensure_uv()
            if uv_bin:
                uv_env = {**os.environ, "VIRTUAL_ENV": str(PROJECT_ROOT / "venv")}
                if _is_termux_env(uv_env):
                    uv_env.pop("PYTHONPATH", None)
                    uv_env.pop("PYTHONHOME", None)
                _install_python_dependencies_with_optional_fallback(
                    [uv_bin, "pip"],
                    env=uv_env,
                    group="termux-all" if _is_termux_env(uv_env) else "all",
                )
            else:
                _install_python_dependencies_with_optional_fallback(
                    [sys.executable, "-m", "pip"],
                    group="termux-all" if _is_termux_env() else "all",
                )

            _clear_update_incomplete_marker()
            print("✓ Dependency installation recovered — your install is healthy again.")
        except Exception as exc:
            # Leave the marker in place so the next launch retries. Give the user
            # the exact manual recovery command in the meantime.
            logger.debug("Interrupted-install recovery failed: %s", exc)
            print("✗ Could not auto-recover the interrupted install.")
            print("  Recover manually with:")
            print(f"    cd {PROJECT_ROOT}")
            print(f"    {sys.executable} -m ensurepip --upgrade")
            print(f"    {sys.executable} -m pip install -e '.[all]'")
    finally:
        sys.stdout = saved_sys_stdout
        if saved_stdout_fd is not None:
            try:
                os.dup2(saved_stdout_fd, 1)
                os.close(saved_stdout_fd)
            except OSError:
                pass
        try:
            lock_path.unlink()
        except OSError:
            pass


def _run_install_with_heartbeat(
    cmd: list[str],
    *,
    env: dict[str, str] | None = None,
    heartbeat_interval_seconds: int = 30,
) -> None:
    """Run dependency install command with periodic heartbeat output.

    Some resolvers/build backends (especially when compiling Rust/C extensions)
    can stay quiet for minutes. Emit a simple elapsed-time heartbeat so users
    know ``hermes update`` is still progressing even if pip/uv itself is silent.
    """
    done = threading.Event()
    start = _time.time()

    def _heartbeat() -> None:
        # Wait first, then print, so short installs don't emit noise.
        while not done.wait(heartbeat_interval_seconds):
            elapsed = int(_time.time() - start)
            print(
                f"  … still installing dependencies ({elapsed}s elapsed)"
                " — compiling Rust/C extensions can take several minutes",
                flush=True,
            )

    t = threading.Thread(target=_heartbeat, daemon=True)
    t.start()
    try:
        subprocess.run(
            cmd,
            cwd=PROJECT_ROOT,
            check=True,
            env=env,
        )
    finally:
        done.set()
        t.join(timeout=0.2)


def _is_windows() -> bool:
    return sys.platform == "win32"


def _venv_scripts_dir() -> Path | None:
    """Return the venv Scripts directory if we're running inside the project venv."""
    venv_dir = PROJECT_ROOT / "venv"
    if not venv_dir.is_dir():
        return None
    scripts = venv_dir / ("Scripts" if _is_windows() else "bin")
    return scripts if scripts.is_dir() else None


def _hermes_exe_shims(scripts_dir: Path) -> list[Path]:
    """Entry-point shims that uv may try to rewrite during ``pip install -e .``.

    On Windows these are .exe launchers generated by setuptools/uv. On POSIX
    they're regular Python scripts which can be replaced atomically — no
    self-replacement hazard exists outside Windows.
    """
    if not _is_windows():
        return []

    names = set(_load_console_script_names()) or {"openagents", "openagents", "openagents-acp"}
    # The gateway shim is not a [project.scripts] entry point, but older
    # update/install paths still rewrite and quarantine it.
    names.add("openagents-gateway")
    return [scripts_dir / f"{name}.exe" for name in sorted(names)]


def _detect_concurrent_hermes_instances(
    scripts_dir: Path, *, exclude_pid: int | None = None
) -> list[tuple[int, str]]:
    """Find other live processes whose .exe is one of our entry-point shims.

    Windows blocks DELETE/REPLACE on a running .exe — and even RENAME on the
    same .exe when another process opened it without ``FILE_SHARE_DELETE``.
    The OpenAgents Desktop Electron app spawns ``hermes.EXE`` as a backend child,
    so during ``hermes update`` the user-invoked process and the desktop's
    child both hold the same file. The quarantine rename then fails with
    ``[WinError 32]`` and uv inherits the lock.

    This helper enumerates processes whose ``exe`` matches one of the venv's
    shims (``hermes.exe`` / ``openagents-gateway.exe``) and returns ``(pid,
    process_name)`` pairs. The caller's own PID and its entire ancestor
    chain are excluded so the running ``hermes update`` invocation never
    reports itself — this matters on Windows where the setuptools .exe
    launcher (``hermes.exe``) is a separate process from the Python
    interpreter it loads (``python.exe``).

    Returns an empty list off-Windows, on missing psutil, or when no other
    instances exist. Never raises — process enumeration is best-effort.
    """
    if not _is_windows():
        return []

    try:
        import psutil
    except Exception:
        return []

    # Resolve every shim path to its canonical form once for cheap comparison.
    shim_paths: set[str] = set()
    for shim in _hermes_exe_shims(scripts_dir):
        try:
            shim_paths.add(str(shim.resolve()).lower())
        except OSError:
            shim_paths.add(str(shim).lower())
    if not shim_paths:
        return []

    # Build a set of PIDs to exclude: the Python process itself plus every
    # ancestor whose executable is one of our shims. On Windows the
    # setuptools-generated hermes.exe launcher is a separate native process
    # that spawns python.exe (the interpreter that runs our code).
    # os.getpid() returns the Python PID, but the launcher (which holds the
    # file lock) is the parent. Without excluding it, every ``hermes update``
    # reports its own launcher as a concurrent instance — a false positive
    # (issues #29341, #34795).
    #
    # Two robustness points learned from the field:
    #   1. Use ``proc.parents()`` — it returns the WHOLE ancestor list in one
    #      call. The earlier per-hop ``current.parent()`` loop bailed on the
    #      first psutil error (AccessDenied/NoSuchProcess is common on Windows
    #      across session/elevation boundaries), leaving the launcher shim in
    #      the candidate set and re-triggering the false positive.
    #   2. Only exclude ancestors whose exe is itself a shim. A genuine second
    #      hermes.exe sitting *under* a non-Hermes parent (e.g. a Hermes
    #      Desktop backend child) must still be flagged, so we don't blanket-
    #      exclude unrelated ancestors like the shell or terminal.
    # Broad ``except Exception`` guards against partially-stubbed psutil in
    # unit tests; this helper is documented as "never raises".
    if exclude_pid is not None:
        exclude_pids: set[int] = {int(exclude_pid)}
    else:
        exclude_pids = {os.getpid()}
    try:
        seed = next(iter(exclude_pids))
        try:
            ancestors = psutil.Process(seed).parents()
        except Exception:
            ancestors = []
        for ancestor in ancestors:
            try:
                anc_exe = ancestor.exe()
            except Exception:
                continue
            if not anc_exe:
                continue
            try:
                anc_norm = str(Path(anc_exe).resolve()).lower()
            except (OSError, ValueError):
                anc_norm = str(anc_exe).lower()
            if anc_norm in shim_paths:
                try:
                    exclude_pids.add(int(ancestor.pid))
                except Exception:
                    continue
    except Exception:
        pass

    matches: list[tuple[int, str]] = []
    try:
        proc_iter = psutil.process_iter(["pid", "exe", "name"])
    except Exception:
        return []

    for proc in proc_iter:
        try:
            info = proc.info
        except Exception:
            continue
        pid = info.get("pid")
        exe = info.get("exe")
        if not exe or pid is None or pid in exclude_pids:
            continue
        try:
            exe_norm = str(Path(exe).resolve()).lower()
        except (OSError, ValueError):
            exe_norm = str(exe).lower()
        if exe_norm in shim_paths:
            name = info.get("name") or Path(exe).name
            matches.append((int(pid), str(name)))

    return matches


def _format_concurrent_instances_message(
    matches: list[tuple[int, str]], scripts_dir: Path
) -> str:
    """Build a human-readable explanation + remediation hint for the user."""
    shim = scripts_dir / "hermes.exe"
    lines = ["✗ Another hermes.exe is running:"]
    for pid, name in matches:
        lines.append(f"    PID {pid}  {name}")
    lines.append("")
    lines.append(f"  Updating now would fail to overwrite {shim} because")
    lines.append("  Windows blocks REPLACE on a running executable.")
    lines.append("")
    lines.append("  Close OpenAgents Desktop, exit any open `hermes` REPLs, and")
    lines.append("  stop the gateway (`hermes gateway stop`) before retrying.")
    lines.append("")
    if matches:
        pid_args = " ".join(f"/PID {pid}" for pid, _ in matches)
        lines.append("  If you've already closed everything and these PIDs are")
        lines.append("  stale, terminate them directly, then retry the update:")
        lines.append(f"      taskkill {pid_args} /F")
        lines.append("")
    lines.append("  Override with `hermes update --force` if you've already")
    lines.append("  confirmed those processes will not write to the venv.")
    return "\n".join(lines)


def _quarantine_running_hermes_exe(
    scripts_dir: Path, *, max_attempts: int = 4
) -> list[tuple[Path, Path]]:
    """Pre-empt Windows file lock on the running ``hermes.exe``.

    Windows allows RENAMING a mapped/running executable (the kernel tracks the
    file by handle, not path), but blocks DELETE/REPLACE while it's loaded. uv
    needs to overwrite the entry-point shims during ``pip install -e .``;
    when ``hermes update`` runs, ``hermes.exe`` IS the live process, and uv
    fails with ``Access is denied. (os error 5)``.

    We rename live shims to ``hermes.exe.old.<unix-ms>`` first. uv then writes
    fresh shims at the original paths. The ``.old`` files are cleaned up on
    the next hermes invocation by ``_cleanup_quarantined_exes``.

    Rename can still fail when *another* process has opened the .exe without
    ``FILE_SHARE_DELETE`` — typically AV real-time scanners with transient
    handles (recovers in <1s), or the OpenAgents Desktop backend child process
    (won't recover until the user closes it). We mitigate:

    1. Retry up to ``max_attempts`` times with exponential backoff
       (100/250/500/1000 ms). Handles the AV-scanner case.
    2. If all retries fail, schedule the .exe for replacement on next
       reboot via ``MoveFileExW(MOVEFILE_DELAY_UNTIL_REBOOT)``. This still
       lets uv create a fresh shim at the original path (Windows will keep
       the old file's content under a new name until the reboot), so the
       update can complete; the user just needs to reboot to fully unload
       the stale image.
    3. Print a clear warning naming the most likely culprit (running
       OpenAgents Desktop / gateway / REPL) and pointing to ``--force``.

    Returns the list of (original, quarantined) pairs so the caller can roll
    back if the install itself fails before uv writes a replacement. Pairs
    where we used ``MOVEFILE_DELAY_UNTIL_REBOOT`` are NOT returned — they
    are already deferred and roll-back is meaningless.
    """
    moved: list[tuple[Path, Path]] = []
    if not _is_windows():
        return moved

    import time

    stamp = int(time.time() * 1000)
    # Backoff schedule: first attempt is immediate, subsequent ones sleep.
    # 100ms / 250ms / 500ms covers the typical AV scanner re-scan window.
    backoff_ms = [0, 100, 250, 500, 1000]
    attempts = max(1, min(max_attempts, len(backoff_ms)))

    for shim in _hermes_exe_shims(scripts_dir):
        if not shim.exists():
            continue
        target = shim.with_suffix(shim.suffix + f".old.{stamp}")

        last_exc: OSError | None = None
        for attempt in range(attempts):
            delay = backoff_ms[attempt] / 1000.0
            if delay:
                time.sleep(delay)
            try:
                shim.rename(target)
                moved.append((shim, target))
                last_exc = None
                break
            except OSError as e:
                last_exc = e
                continue

        if last_exc is None:
            continue

        # All in-process renames failed. Try MoveFileEx with
        # MOVEFILE_DELAY_UNTIL_REBOOT as a last resort. This succeeds in the
        # exact case where the inline rename failed (another process holds
        # the handle without share-delete), at the cost of requiring a
        # reboot to fully reclaim the old .exe.
        scheduled = _schedule_replace_on_reboot(shim, target)
        if scheduled:
            print(
                f"  ⚠ {shim.name} is locked by another process; scheduled "
                f"replacement on next reboot."
            )
            print(
                "    The new shim was written at the same path, but a "
                "reboot is needed to fully unload the old one."
            )
            # Do NOT append to ``moved``: we don't want roll-back to undo a
            # reboot-deferred operation.
            continue

        # Truly couldn't budge the .exe. Print an actionable warning and let
        # uv try its luck — sometimes uv's own retry handling pulls through.
        print(
            f"  ⚠ Could not quarantine {shim.name} ({last_exc.__class__.__name__}: "
            f"another process is holding it open)."
        )
        print(
            "    Close OpenAgents Desktop, exit other `hermes` REPLs, stop the "
            "gateway, or pause AV scanning, then re-run `hermes update`."
        )

    return moved


def _schedule_replace_on_reboot(shim: Path, quarantine_target: Path) -> bool:
    """Schedule ``shim`` -> ``quarantine_target`` via PendingFileRenameOperations.

    Uses Win32 ``MoveFileExW`` with ``MOVEFILE_REPLACE_EXISTING |
    MOVEFILE_DELAY_UNTIL_REBOOT``. The OS persists the rename in
    ``HKLM\\System\\CurrentControlSet\\Control\\Session Manager\\
    PendingFileRenameOperations`` and applies it before any user-mode code
    runs on next boot — at which point no process can hold the .exe.

    Returns ``True`` if the schedule call succeeded, ``False`` otherwise
    (non-Windows, ctypes failure, lack of privilege, etc.). Never raises.
    """
    if not _is_windows():
        return False
    try:
        import ctypes
        from ctypes import wintypes

        MOVEFILE_REPLACE_EXISTING = 0x1
        MOVEFILE_DELAY_UNTIL_REBOOT = 0x4

        MoveFileExW = ctypes.windll.kernel32.MoveFileExW
        MoveFileExW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]
        MoveFileExW.restype = wintypes.BOOL

        ok = MoveFileExW(
            str(shim),
            str(quarantine_target),
            MOVEFILE_REPLACE_EXISTING | MOVEFILE_DELAY_UNTIL_REBOOT,
        )
        return bool(ok)
    except Exception:
        return False


def _restore_quarantined_exes(moved: list[tuple[Path, Path]]) -> None:
    """Roll back ``_quarantine_running_hermes_exe`` if uv didn't write replacements."""
    for original, quarantined in moved:
        try:
            if not original.exists() and quarantined.exists():
                quarantined.rename(original)
        except OSError:
            pass


def _run_quarantined_install(
    cmd: list[str],
    *,
    env: dict[str, str] | None = None,
    scripts_dir: Path | None = None,
) -> None:
    """Run an editable install, quarantining the running ``hermes.exe`` first.

    Any ``pip install -e .`` (or ``--reinstall``) rewrites the entry-point
    shims, and on Windows the live ``hermes.exe`` is the running process —
    pip can neither delete nor overwrite it, so without quarantine the shim
    is left missing and ``hermes`` drops off PATH. This wraps
    :func:`_run_install_with_heartbeat` with the same rename-out-of-the-way /
    restore-on-failure dance that the primary install path uses, so EVERY
    install that touches the shims is protected — including the
    verification-repair reinstalls in
    :func:`_verify_core_dependencies_installed`, which previously called
    ``_run_install_with_heartbeat`` directly and bypassed quarantine.

    Off-Windows (``scripts_dir is None``) this is a thin pass-through.
    """
    moved: list[tuple[Path, Path]] = []
    if scripts_dir is not None:
        moved = _quarantine_running_hermes_exe(scripts_dir)
    try:
        _run_install_with_heartbeat(cmd, env=env)
    except BaseException:
        # Restore shims if pip/uv didn't write replacements (e.g. install
        # failed before the entry-points step). Don't swallow the error.
        if scripts_dir is not None:
            _restore_quarantined_exes(moved)
        raise


def _cleanup_quarantined_exes(scripts_dir: Path | None = None) -> None:
    """Sweep ``hermes.exe.old.*`` left by prior updates.

    Called early on every hermes invocation. The .old files are unlocked once
    their owning process exited, so deletion succeeds the next run. Silent
    no-op when nothing's there or on file-locked / permission errors.
    """
    if not _is_windows():
        return
    if scripts_dir is None:
        scripts_dir = _venv_scripts_dir()
    if scripts_dir is None:
        return
    try:
        for stale in scripts_dir.glob("*.exe.old.*"):
            try:
                stale.unlink()
            except OSError:
                pass  # still locked or in use — try again next run
    except OSError:
        pass


def _refresh_active_lazy_features() -> None:
    """Refresh lazy-installed backends after a code update.

    When pyproject.toml's ``[all]`` extra was slimmed down (May 2026), most
    optional backends moved to ``tools/lazy_deps.py`` and only install on
    first use. ``hermes update`` runs ``uv pip install -e .[all]`` which
    leaves those packages untouched — so if we bump a pin in
    :data:`LAZY_DEPS` (CVE response, transitive bug fix), users who already
    activated the backend keep the stale version forever.

    This function asks lazy_deps which features the user has previously
    activated and reinstalls them under the current pins. Features the
    user never enabled stay quiet — no churn for cold backends.

    Never raises. A failure here must not block the rest of the update.
    """
    try:
        from tools import lazy_deps
    except Exception as exc:
        logger.debug("Lazy refresh skipped (import failed): %s", exc)
        return

    try:
        active = lazy_deps.active_features()
    except Exception as exc:
        logger.debug("Lazy refresh skipped (active_features failed): %s", exc)
        return

    if not active:
        return

    print()
    print(f"→ Refreshing {len(active)} active lazy backend(s)...")

    try:
        results = lazy_deps.refresh_active_features(prompt=False)
    except Exception as exc:
        # refresh_active_features is documented as never-raise, but defend
        # the update flow against future regressions.
        print(f"  ⚠ Lazy refresh failed unexpectedly: {exc}")
        return

    refreshed = [f for f, s in results.items() if s == "refreshed"]
    current = [f for f, s in results.items() if s == "current"]
    failed = [(f, s) for f, s in results.items() if s.startswith("failed:")]
    skipped = [(f, s) for f, s in results.items() if s.startswith("skipped:")]

    if refreshed:
        print(f"  ↑ {len(refreshed)} refreshed: {', '.join(refreshed)}")
    if current:
        print(f"  ✓ {len(current)} already current")
    if skipped:
        # Most common reason: security.allow_lazy_installs=false. Show one
        # line so the user knows why; not an error.
        names = ", ".join(f for f, _ in skipped)
        reason = skipped[0][1].split(": ", 1)[-1]
        print(f"  · {len(skipped)} skipped ({reason}): {names}")
    if failed:
        for feature, status in failed:
            reason = status.split(": ", 1)[-1]
            # Clip noisy pip stderr to keep update output legible.
            if len(reason) > 200:
                reason = reason[:200] + "..."
            print(f"  ⚠ {feature} failed to refresh: {reason}")
        print("  Backends keep their previously-installed version; rerun")
        print("  `hermes update` once the upstream issue is resolved.")


def _install_python_dependencies_with_optional_fallback(
    install_cmd_prefix: list[str],
    *,
    env: dict[str, str] | None = None,
    group: str = "all",
) -> None:
    """Install base deps plus as many optional extras as the environment supports.

    By default this targets ``.[all]``; Termux callers can pass
    ``group='termux-all'`` to use the curated Android-compatible profile.

    On Windows, pre-renames live ``hermes.exe`` / ``openagents-gateway.exe`` shims
    in the venv Scripts dir before each install attempt so uv can write fresh
    copies (Windows blocks REPLACE on a running .exe but allows RENAME). See
    ``_quarantine_running_hermes_exe`` for the rationale.
    """
    scripts_dir = _venv_scripts_dir() if _is_windows() else None

    def _install(args: list[str]) -> None:
        _run_quarantined_install(
            install_cmd_prefix + args, env=env, scripts_dir=scripts_dir
        )

    try:
        _install(["install", "-e", f".[{group}]"])
        _verify_console_scripts_installed(install_cmd_prefix, env=env)
        return
    except subprocess.CalledProcessError:
        print(
            "  ⚠ Optional extras failed, reinstalling base dependencies and retrying extras individually..."
        )

    _install(["install", "-e", "."])

    failed_extras: list[str] = []
    installed_extras: list[str] = []
    for extra in _load_installable_optional_extras(group=group):
        try:
            _install(["install", "-e", f".[{extra}]"])
            installed_extras.append(extra)
        except subprocess.CalledProcessError:
            failed_extras.append(extra)

    if installed_extras:
        print(
            f"  ✓ Reinstalled optional extras individually: {', '.join(installed_extras)}"
        )
    if failed_extras:
        print(
            f"  ⚠ Skipped optional extras that still failed: {', '.join(failed_extras)}"
        )

    # Belt-and-suspenders: verify every declared core dependency from
    # pyproject.toml's [project.dependencies] is actually importable in the
    # target venv. uv's incremental resolver has — in the wild — produced
    # partial installs where a newly added base dep (e.g. ``pathspec``)
    # silently fails to land on top of a half-stale venv, and the only
    # symptom is a downstream subprocess crashing with ModuleNotFoundError
    # hours later inside ``hermes update``'s desktop-rebuild or skill-sync
    # stage. Reinstall with --reinstall to force resolution if anything is
    # missing, then re-verify so the failure surfaces here instead of
    # downstream.
    _verify_core_dependencies_installed(install_cmd_prefix, env=env, group=group)
    _verify_console_scripts_installed(install_cmd_prefix, env=env)


def _load_console_script_names() -> list[str]:
    """Return ``[project.scripts]`` entry-point names from pyproject.toml."""
    try:
        import tomllib  # Python 3.11+
    except ImportError:  # pragma: no cover
        return []

    pyproject = PROJECT_ROOT / "pyproject.toml"
    if not pyproject.is_file():
        return []

    try:
        with open(pyproject, "rb") as f:
            data = tomllib.load(f)
        scripts = data.get("project", {}).get("scripts", {}) or {}
        return [str(name) for name in scripts if name]
    except Exception as e:
        logger.debug("console script verification: failed to read pyproject.toml: %s", e)
        return []


def _verify_console_scripts_installed(
    install_cmd_prefix: list[str],
    *,
    env: dict[str, str] | None = None,
) -> None:
    """Ensure every declared console_script shim exists on disk after install.

    On Windows, ``uv pip install -e .`` can register ``hermes.exe`` in the
    wheel RECORD while the file never lands on disk — typically when the live
    ``hermes.exe`` shim is locked during ``hermes update``, or when uv/distlib
    skips a launcher write. The symptom is ``openagents.exe`` and
    ``openagents-acp.exe`` present but ``hermes.exe`` missing, so ``hermes`` drops
    off PATH even though the install reported success (issue #52931).

    If any shim is missing we reinstall with ``--reinstall -e .`` under the
    same quarantine dance as the primary install path, then re-check.
    """
    if not _is_windows():
        return

    scripts_dir = _venv_scripts_dir()
    if scripts_dir is None:
        return

    names = _load_console_script_names()
    if not names:
        return

    def _missing() -> list[str]:
        return [
            name
            for name in names
            if not (scripts_dir / f"{name}.exe").is_file()
        ]

    missing = _missing()
    if not missing:
        return

    print(
        f"  ⚠ Verification: {len(missing)} console script(s) missing on disk: "
        f"{', '.join(missing)}"
    )
    print("  → Reinstalling entry points with --reinstall...")

    try:
        _run_quarantined_install(
            install_cmd_prefix + ["install", "--reinstall", "-e", "."],
            env=env,
            scripts_dir=scripts_dir,
        )
    except subprocess.CalledProcessError as e:
        logger.warning("console script verification: repair install failed: %s", e)
        print(
            "  ⚠ Entry point repair failed; try `hermes update --force` after "
            "closing other hermes processes."
        )
        return

    still_missing = _missing()
    if still_missing:
        print(
            f"  ⚠ Still missing after repair: {', '.join(still_missing)}. "
            "Workaround: python -m openagents_cli.main <command>"
        )
    else:
        print("  ✓ All console entry points restored")


def _verify_core_dependencies_installed(
    install_cmd_prefix: list[str],
    *,
    env: dict[str, str] | None = None,
    group: str = "all",
) -> None:
    """Check that every base dep from pyproject.toml is importable; if not, retry.

    Reads ``pyproject.toml`` directly (so we don't trust the venv's stale
    metadata), filters out deps gated by ``;`` environment markers that don't
    apply to this platform, and runs ``importlib.metadata.version()`` in the
    venv interpreter for each one. If anything is missing we reinstall the
    base group with ``--reinstall`` to force uv to re-resolve, then check
    again. We treat the final state as a warning rather than a hard failure
    so a single broken-on-PyPI dep can't block an otherwise-successful
    update — but the warning makes the partial install visible at the spot
    that caused it, instead of hours later in a downstream subprocess.
    """
    try:
        import tomllib  # Python 3.11+
    except ImportError:  # pragma: no cover — Python < 3.11 unsupported but be safe
        return

    pyproject = PROJECT_ROOT / "pyproject.toml"
    if not pyproject.is_file():
        return

    try:
        with open(pyproject, "rb") as f:
            data = tomllib.load(f)
        raw_deps = data.get("project", {}).get("dependencies", []) or []
    except Exception as e:
        logger.debug("dep verification: failed to read pyproject.toml: %s", e)
        return

    # Parse each "name OP version ; marker" string into (dist_name, marker_obj).
    # We use packaging.requirements when available (it ships with pip/uv envs),
    # falling back to a naive split that's good enough for the canonical
    # ``name==version[; marker]`` style this repo uses.
    deps: list[tuple[str, "object | None"]] = []
    try:
        from packaging.requirements import Requirement  # type: ignore

        for spec in raw_deps:
            try:
                req = Requirement(spec)
                deps.append((req.name, req.marker))
            except Exception:
                continue
    except Exception:
        for spec in raw_deps:
            head = spec.split(";", 1)[0]
            for op in ("==", ">=", "<=", "~=", ">", "<", "!="):
                if op in head:
                    head = head.split(op, 1)[0]
                    break
            name = head.strip().split("[", 1)[0].strip()
            if name:
                deps.append((name, None))

    # Apply environment markers to drop deps that don't apply on this platform
    # (e.g. ``ptyprocess ; sys_platform != 'win32'`` is correctly skipped on
    # Windows). Without markers we'd false-positive every cross-platform exclusion.
    applicable: list[str] = []
    for name, marker in deps:
        if marker is None:
            applicable.append(name)
            continue
        try:
            if marker.evaluate():  # type: ignore[union-attr]
                applicable.append(name)
        except Exception:
            applicable.append(name)

    if not applicable:
        return

    # Run the check inside the venv Python — sys.executable here may be the
    # outer Python that drove ``hermes update``, not the venv we just wrote
    # to. The uv install_cmd_prefix encodes which environment we targeted
    # (either ``[uv, pip]`` with VIRTUAL_ENV in env, or
    # ``[sys.executable, -m, pip]`` for the in-process Python); resolve the
    # right interpreter for the verification.
    venv_python = _resolve_install_target_python(install_cmd_prefix, env)
    if venv_python is None:
        return

    def _missing_deps() -> list[str]:
        check_script = (
            "import importlib.metadata as md, sys\n"
            "missing=[]\n"
            "for name in sys.argv[1:]:\n"
            "    try: md.version(name)\n"
            "    except md.PackageNotFoundError: missing.append(name)\n"
            "print('\\n'.join(missing))\n"
        )
        try:
            result = subprocess.run(
                [str(venv_python), "-c", check_script, *applicable],
                capture_output=True,
                text=True,
                check=False,
                env=env,
            )
        except Exception as e:
            logger.debug("dep verification: subprocess failed: %s", e)
            return []
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]

    missing = _missing_deps()
    if not missing:
        return

    print(
        f"  ⚠ Verification: {len(missing)} declared dep(s) missing after install: "
        f"{', '.join(missing[:8])}{'...' if len(missing) > 8 else ''}"
    )
    print("  → Reinstalling base group with --reinstall to repair...")

    # Reinstall base group with --reinstall so uv re-resolves from scratch
    # against the current pyproject. We don't pass ``[{group}]`` here on
    # purpose — the missing dep is in *base* deps; rerunning the full all-
    # extras install can cost minutes and trips on whatever optional extra
    # was already broken upstream. Base is fast and is what's actually wrong.
    #
    # Quarantine the running ``hermes.exe`` first: ``--reinstall -e .``
    # rewrites the entry-point shims, and on Windows pip can't overwrite the
    # live launcher, which would leave ``hermes`` off PATH.
    scripts_dir = _venv_scripts_dir() if _is_windows() else None
    repair_args = ["install", "--reinstall", "-e", "."]
    try:
        _run_quarantined_install(
            install_cmd_prefix + repair_args, env=env, scripts_dir=scripts_dir
        )
    except subprocess.CalledProcessError as e:
        logger.warning("dep verification: repair install failed: %s", e)
        print("  ⚠ Repair install failed; check `hermes update` output above.")
        return

    still_missing = _missing_deps()
    if not still_missing:
        print("  ✓ All declared core dependencies now installed")
        return

    # Last-ditch: install each remaining missing dep with its pin directly.
    # Useful when uv's resolver thinks the env is satisfied but the on-disk
    # package metadata says otherwise (rare but observed).
    name_to_spec = {}
    for spec in raw_deps:
        head = spec.split(";", 1)[0].strip()
        bare = head
        for op in ("==", ">=", "<=", "~=", ">", "<", "!="):
            if op in bare:
                bare = bare.split(op, 1)[0]
                break
        name_to_spec[bare.strip().split("[", 1)[0].strip()] = head

    specs = [name_to_spec.get(n, n) for n in still_missing]
    print(
        f"  → Force-installing remaining missing dep(s): {', '.join(specs)}"
    )
    try:
        _run_install_with_heartbeat(
            install_cmd_prefix + ["install", "--reinstall", *specs], env=env
        )
    except subprocess.CalledProcessError as e:
        logger.warning("dep verification: per-package repair failed: %s", e)
        print(
            f"  ⚠ Could not install: {', '.join(still_missing)}. "
            "Run `hermes update --force` after closing other hermes processes."
        )
        return

    final_missing = _missing_deps()
    if final_missing:
        print(
            f"  ⚠ Still missing after repair: {', '.join(final_missing)}. "
            "Run `hermes update --force` after closing other hermes processes."
        )
    else:
        print("  ✓ All declared core dependencies now installed")


def _resolve_install_target_python(
    install_cmd_prefix: list[str], env: dict[str, str] | None
) -> Path | None:
    """Figure out which Python interpreter the install just targeted.

    ``_install_python_dependencies_with_optional_fallback`` is called with
    either ``[uv, pip]`` (and a ``VIRTUAL_ENV`` env var pointing at the
    target venv) or ``[sys.executable, -m, pip]`` (the in-process Python).
    The verification step needs the *resulting* environment's Python so
    ``importlib.metadata`` queries the right site-packages.
    """
    if env and "VIRTUAL_ENV" in env:
        venv_root = Path(env["VIRTUAL_ENV"])
        scripts = venv_root / ("Scripts" if _is_windows() else "bin")
        candidate = scripts / ("python.exe" if _is_windows() else "python")
        if candidate.exists():
            return candidate

    # Fallback: assume install_cmd_prefix[0] is the python interpreter (the
    # ``[sys.executable, -m, pip]`` shape). Skip if it looks like ``uv``.
    if install_cmd_prefix:
        first = Path(install_cmd_prefix[0])
        if first.exists() and "uv" not in first.name.lower():
            return first

    return None


def _is_termux_env(env: dict[str, str] | None = None) -> bool:
    return _is_termux_startup_environment(env)


def _is_android_python() -> bool:
    return sys.platform == "android"


def _install_psutil_android_compat(
    install_cmd_prefix: list[str],
    *,
    env: dict[str, str] | None = None,
) -> None:
    """Install psutil on Android by patching upstream platform detection.

    psutil's setup currently gates Linux sources behind
    ``sys.platform.startswith('linux')``. On Termux Python reports
    ``sys.platform == 'android'``, so setup aborts with
    "platform android is not supported" despite compiling fine when using the
    Linux source path.

    We patch only the extracted build tree used for this install attempt;
    nothing is persisted in the repository.

    Stopgap: remove this once https://github.com/giampaolo/psutil/pull/2762
    merges and ships in a release. The standalone installer script uses the
    same shared helper and should be removed together.
    """
    import tempfile
    import urllib.request
    from openagents_cli.psutil_android import PSUTIL_URL, prepare_patched_psutil_sdist

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        archive = tmp_path / "psutil.tar.gz"
        urllib.request.urlretrieve(PSUTIL_URL, archive)
        src_root = prepare_patched_psutil_sdist(archive, tmp_path)

        _run_install_with_heartbeat(
            install_cmd_prefix + ["install", "--no-build-isolation", str(src_root)],
            env=env,
        )


def _ensure_uv_for_termux(pip_cmd: list[str]) -> str | None:
    """Best-effort uv bootstrap on Termux for faster update installs.

    The normal path (``ensure_uv()`` in managed_uv) installs the managed
    standalone uv into ``$OPENAGENTS_HOME/bin/uv``, but on Termux the official
    installer may not work (glibc vs bionic).  Prefer a uv already on PATH
    (e.g. ``pkg install uv``); only if there is none do we fall back to a
    wheel-only ``pip install uv`` so we never source-build the Rust crate.
    """
    from openagents_cli.managed_uv import resolve_uv

    existing = resolve_uv()
    if existing:
        return existing
    if not _is_termux_env():
        return None
    # A Termux-packaged uv lands on PATH but not in the managed bin dir, so
    # resolve_uv() misses it. Use it before pip, which has no Android wheel and
    # would otherwise build uv from source on a low-memory device.
    system_uv = shutil.which("uv")
    if system_uv:
        return system_uv
    try:
        print("  → Termux detected: trying to install uv for faster dependency updates...")
        result = subprocess.run(
            pip_cmd + ["install", "uv", "--only-binary", ":all:"],
            cwd=PROJECT_ROOT,
            check=False,
        )
        if result.returncode != 0:
            return None
    except Exception:
        pass
    # After pip install, check managed path first, then PATH
    return resolve_uv() or shutil.which("uv")


def _update_node_dependencies() -> None:
    from openagents_constants import find_node_executable, with_hermes_node_path

    npm = find_node_executable("npm")
    if not npm:
        return

    if not (PROJECT_ROOT / "package.json").exists():
        return

    # With a single workspace lockfile the root install would cover ALL
    # workspaces — but apps/desktop pulls in Electron as a devDependency,
    # and its postinstall downloads a ~200MB binary.  Most users don't
    # need desktop during `hermes update`, so we install root-only first
    # then add just the workspaces the CLI/TUI/web build actually requires.
    # Desktop deps are installed on demand by the desktop launcher
    # (see _desktop_build_needed).
    print("→ Updating Node.js dependencies...")
    extra_args = ["--no-fund", "--no-audit", "--progress=false"]

    nixos_env = with_hermes_node_path(_nixos_build_env())

    # Step 1: root install (no workspace recursion).
    # NOTE: capture_output=False here is deliberate (#18840) — optional
    # postinstall scripts (e.g. @askjo/camofox-browser's browser-binary fetch)
    # print download progress, and capturing it makes a long download look
    # hung. The chatty npm-deprecation noise during `hermes update` comes from
    # the *desktop* build, not this step; that one is captured to update.log.
    root_args = [*extra_args, "--workspaces=false"]
    root_result = _run_npm_install_deterministic(
        npm,
        PROJECT_ROOT,
        extra_args=tuple(root_args),
        capture_output=False,
        env=nixos_env,
    )
    if root_result.returncode != 0:
        print("  ⚠ npm install failed in repo root")
        stderr = (root_result.stderr or "").strip() if root_result.stderr else ""
        if stderr:
            print(f"    {stderr.splitlines()[-1]}")
        return

    # Step 2: install only the workspaces update needs (ui-tui, web).
    # --workspace selects specific workspaces; the rest (desktop) are skipped.
    ws_args = [*extra_args, "--workspace", "ui-tui", "--workspace", "web"]
    ws_result = _run_npm_install_deterministic(
        npm,
        PROJECT_ROOT,
        extra_args=tuple(ws_args),
        capture_output=False,
        env=nixos_env,
    )
    if ws_result.returncode == 0:
        print("  ✓ repo root + ui-tui, web workspaces (desktop skipped)")
    else:
        print("  ⚠ npm workspace install failed")
        stderr = (ws_result.stderr or "").strip() if ws_result.stderr else ""
        if stderr:
            print(f"    {stderr.splitlines()[-1]}")


class _UpdateOutputStream:
    """Stream wrapper used during ``hermes update`` to survive terminal loss.

    Wraps the process's original stdout/stderr so that:

    * Every write is also mirrored to an append-only log file
      (``~/.openagents/logs/update.log``) that users can inspect after the
      terminal disconnects.
    * Writes to the original stream that fail with ``BrokenPipeError`` /
      ``OSError`` / ``ValueError`` (closed file) no longer cascade into
      process exit — the update keeps going, only the on-screen output
      stops.

    Combined with ``SIGHUP -> SIG_IGN`` installed by
    ``_install_hangup_protection``, this makes ``hermes update`` safe to
    run in a plain SSH session that might disconnect mid-install.
    """

    def __init__(self, original, log_file):
        self._original = original
        self._log = log_file
        self._original_broken = False

    def write(self, data):
        # Mirror to the log file first — it's the most reliable destination.
        if self._log is not None:
            try:
                self._log.write(data)
            except Exception:
                # Log errors should never abort the update.
                pass

        if self._original_broken:
            return len(data) if isinstance(data, (str, bytes)) else 0

        try:
            return self._original.write(data)
        except (BrokenPipeError, OSError, ValueError):
            # Terminal vanished (SSH disconnect, shell close).  Stop trying
            # to write to it, but keep the update running.
            self._original_broken = True
            return len(data) if isinstance(data, (str, bytes)) else 0

    def flush(self):
        if self._log is not None:
            try:
                self._log.flush()
            except Exception:
                pass
        if self._original_broken:
            return
        try:
            self._original.flush()
        except (BrokenPipeError, OSError, ValueError):
            self._original_broken = True

    def isatty(self):
        if self._original_broken:
            return False
        try:
            return self._original.isatty()
        except Exception:
            return False

    def fileno(self):
        # Some tools probe fileno(); defer to the underlying stream and let
        # callers handle failures (same behaviour as the unwrapped stream).
        return self._original.fileno()

    def __getattr__(self, name):
        return getattr(self._original, name)


def _install_hangup_protection(gateway_mode: bool = False):
    """Protect ``cmd_update`` from SIGHUP and broken terminal pipes.

    Users commonly run ``hermes update`` in an SSH session or a terminal
    that may close mid-install.  Without protection, ``SIGHUP`` from the
    terminal kills the Python process during ``pip install`` and leaves
    the venv half-installed; the documented workaround ("use screen /
    tmux") shouldn't be required for something as routine as an update.

    Protections installed:

    1. ``SIGHUP`` is set to ``SIG_IGN``.  POSIX preserves ``SIG_IGN``
       across ``exec()``, so pip and git subprocesses also stop dying on
       hangup.
    2. ``sys.stdout`` / ``sys.stderr`` are wrapped to mirror output to
       ``~/.openagents/logs/update.log`` and to silently absorb
       ``BrokenPipeError`` when the terminal vanishes.

    ``SIGINT`` (Ctrl-C) and ``SIGTERM`` (systemd shutdown) are
    **intentionally left alone** — those are legitimate cancellation
    signals the user or OS sent on purpose.

    In gateway mode (``hermes update --gateway``) the update is already
    spawned detached from a terminal, so this function is a no-op.

    Returns a dict that ``cmd_update`` can pass to
    ``_finalize_update_output`` on exit.  Returning a dict rather than a
    tuple keeps the call site forward-compatible with future additions.
    """
    state = {
        "prev_stdout": sys.stdout,
        "prev_stderr": sys.stderr,
        "log_file": None,
        "installed": False,
    }

    if gateway_mode:
        return state

    import signal as _signal

    # (1) Ignore SIGHUP for the remainder of this process.
    if hasattr(_signal, "SIGHUP"):
        try:
            _signal.signal(_signal.SIGHUP, _signal.SIG_IGN)
        except (ValueError, OSError):
            # Called from a non-main thread — not fatal.  The update still
            # runs, just without hangup protection.
            pass

    # (2) Mirror output to update.log and wrap stdio for broken-pipe
    # tolerance.  Any failure here is non-fatal; we just skip the wrap.
    try:
        # Late-bound import so tests can monkeypatch
        # openagents_cli.config.get_openagents_home to simulate setup failure.
        from openagents_cli.config import get_openagents_home as _get_openagents_home

        logs_dir = _get_openagents_home() / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        log_path = logs_dir / "update.log"
        log_file = open(log_path, "a", buffering=1, encoding="utf-8")

        import datetime as _dt

        log_file.write(
            f"\n=== hermes update started "
            f"{_dt.datetime.now().isoformat(timespec='seconds')} ===\n"
        )

        state["log_file"] = log_file
        sys.stdout = _UpdateOutputStream(state["prev_stdout"], log_file)
        sys.stderr = _UpdateOutputStream(state["prev_stderr"], log_file)
        state["installed"] = True
    except Exception:
        # Leave stdio untouched on any setup failure.  Update continues
        # without mirroring.
        state["log_file"] = None

    return state


def _log_only_write(text: str) -> None:
    """Write ``text`` to ``~/.openagents/logs/update.log`` only, never the terminal.

    During ``hermes update`` ``sys.stdout`` is an ``_UpdateOutputStream`` that
    mirrors to both the terminal and ``update.log``. Loud, low-signal
    subprocess output (npm installs, the Electron/vite build, the cua-driver
    installer's "Next steps" wall) should be captured and tucked into the log
    so failures stay debuggable, without flooding the user's terminal. This
    reaches past the mirroring stream straight to the underlying log handle.
    """
    if not text:
        return
    stream = sys.stdout
    log_file = getattr(stream, "_log", None)
    if log_file is None:
        return
    try:
        log_file.write(text if text.endswith("\n") else text + "\n")
        log_file.flush()
    except Exception:
        pass


def _run_logged_subprocess(cmd, *, cwd=None, env=None):
    """Run ``cmd`` capturing combined output into update.log (not the terminal).

    Returns the ``CompletedProcess`` (with ``stdout`` populated) so the caller
    can decide whether to surface the captured output on failure.
    """
    result = subprocess.run(
        cmd,
        cwd=cwd,
        env=env,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    _log_only_write(result.stdout or "")
    return result


def _finalize_update_output(state):
    """Restore stdio and close the update.log handle opened by ``_install_hangup_protection``."""
    if not state:
        return
    if state.get("installed"):
        try:
            sys.stdout = state.get("prev_stdout", sys.stdout)
        except Exception:
            pass
        try:
            sys.stderr = state.get("prev_stderr", sys.stderr)
        except Exception:
            pass
    log_file = state.get("log_file")
    if log_file is not None:
        try:
            log_file.flush()
            log_file.close()
        except Exception:
            pass


def _resolve_update_branch(args) -> str:
    """Normalize ``args.branch`` into a non-empty branch name.

    Centralizes the "default to main, accept --branch override, treat empty
    or whitespace-only values as the default" parsing so every consumer of
    ``--branch`` (check path, git-update path, ZIP-fallback path) agrees on
    the same answer.
    """
    return (getattr(args, "branch", None) or "main").strip() or "main"


def _cmd_update_check(branch: str = "main", *, branch_explicit: bool = False):
    """Implement ``hermes update --check``: fetch and report without installing.

    ``branch`` selects which branch the check compares against. Default is
    "main"; callers can pass another branch to ask "are there new commits
    on origin/<branch>?" without performing the update.

    ``branch_explicit`` is True iff the caller passed --branch on the CLI.
    PyPI installs can't honor non-default branches, so when this is True
    on a PyPI install we surface a one-line notice instead of silently
    dropping the flag.
    """
    from openagents_cli.config import detect_install_method
    method = detect_install_method(PROJECT_ROOT)
    if method == "docker":
        # Docker can't ``git fetch`` from within the container.  Surface the
        # same long-form ``docker pull`` guidance ``hermes update`` (apply
        # path) uses — telling the user to "reinstall via curl" or that
        # ".git is missing" would point them at the wrong remediation.
        from openagents_cli.config import format_docker_update_message
        print(format_docker_update_message())
        sys.exit(1)
    if method == "pip":
        from openagents_cli.config import recommended_update_command
        from openagents_cli.banner import check_via_pypi
        if branch_explicit and branch != "main":
            print(f"⚠ --branch is ignored for PyPI installs (would have checked '{branch}').")
        result = check_via_pypi()
        if result is None:
            print("✗ Could not reach PyPI to check for updates.")
            sys.exit(1)
        elif result == 0:
            print("✓ Already up to date.")
        else:
            print("⚕ Update available on PyPI.")
            print(f"  Run '{recommended_update_command()}' to install.")
        return

    git_dir = PROJECT_ROOT / ".git"
    if not git_dir.exists():
        print("✗ Not a git repository — cannot check for updates.")
        sys.exit(1)

    git_cmd = ["git"]
    if sys.platform == "win32":
        git_cmd = ["git", "-c", "windows.appendAtomically=false"]

    # Fetch only the branch we compare against; prefer upstream as the canonical
    # reference. A bare `git fetch <remote>` pulls every ref, and this repo has
    # thousands of auto-generated branches, so scope the fetch to <branch>.
    # Note: upstream/<branch> may not exist for non-main branches (a fork's
    # bb/gui has no upstream counterpart), so when the caller picks a
    # non-default branch we skip the upstream probe and use origin directly.
    # Installer checkouts are shallow (`git clone --depth 1`). A plain
    # `git fetch` would unshallow the repo (dragging in the whole history —
    # the exact cost the shallow clone avoided) and the rev-list count below
    # would then report a huge bogus "behind" number. Detect shallow up front:
    # fetch with --depth 1 to preserve the boundary and report presence-only.
    is_shallow = (
        subprocess.run(
            git_cmd + ["rev-parse", "--is-shallow-repository"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
        ).stdout.strip()
        == "true"
    )
    depth_args = ["--depth", "1"] if is_shallow else []

    if branch == "main":
        print("→ Fetching from upstream...")
        fetch_result = subprocess.run(
            git_cmd + ["fetch"] + depth_args + ["upstream", branch],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
        )
        if fetch_result.returncode != 0:
            # Fallback to origin if upstream doesn't exist
            print("→ Fetching from origin...")
            fetch_result = subprocess.run(
                git_cmd + ["fetch"] + depth_args + ["origin", branch],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
            )
            upstream_exists = False
            compare_branch = f"origin/{branch}"
        else:
            upstream_exists = True
            compare_branch = f"upstream/{branch}"
    else:
        # Non-default branch: compare against origin/<branch> directly.
        print("→ Fetching from origin...")
        fetch_result = subprocess.run(
            git_cmd + ["fetch"] + depth_args + ["origin", branch],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
        )
        upstream_exists = False
        compare_branch = f"origin/{branch}"

    if fetch_result.returncode != 0:
        stderr = fetch_result.stderr.strip()
        if "Could not resolve host" in stderr or "unable to access" in stderr:
            print("✗ Network error — cannot reach the remote repository.")
        elif "Authentication failed" in stderr or "could not read Username" in stderr:
            print("✗ Authentication failed — check your git credentials or SSH key.")
        else:
            print("✗ Failed to fetch.")
            if stderr:
                print(f"  {stderr.splitlines()[0]}")
        sys.exit(1)

    # Verify the compare ref actually exists before asking rev-list about it.
    # Without this, `git rev-list HEAD..origin/<bogus> --count` exits 128 and
    # (with check=True) raises CalledProcessError, surfacing a Python
    # traceback. Friendlier to detect-and-report.
    verify_result = subprocess.run(
        git_cmd + ["rev-parse", "--verify", "--quiet", compare_branch],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )
    if verify_result.returncode != 0:
        print(f"✗ Branch '{branch}' not found on {compare_branch.split('/', 1)[0]}.")
        sys.exit(1)

    if is_shallow:
        # No history to count across the shallow boundary. Compare tip SHAs and
        # report presence-only (mirrors the banner's _check_via_local_git).
        head_sha = subprocess.run(
            git_cmd + ["rev-parse", "HEAD"],
            cwd=PROJECT_ROOT, capture_output=True, text=True,
        ).stdout.strip()
        target_sha = subprocess.run(
            git_cmd + ["rev-parse", compare_branch],
            cwd=PROJECT_ROOT, capture_output=True, text=True,
        ).stdout.strip()
        if head_sha and target_sha and head_sha == target_sha:
            print("✓ Already up to date.")
        else:
            print(f"⚕ Update available (behind {compare_branch}).")
            from openagents_cli.config import recommended_update_command

            print(f"  Run '{recommended_update_command()}' to install.")
        return

    rev_result = subprocess.run(
        git_cmd + ["rev-list", f"HEAD..{compare_branch}", "--count"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    behind = int(rev_result.stdout.strip())

    if behind == 0:
        print("✓ Already up to date.")
    else:
        commits_word = "commit" if behind == 1 else "commits"
        print(f"⚕ Update available: {behind} {commits_word} behind {compare_branch}.")
        from openagents_cli.config import recommended_update_command

        print(f"  Run '{recommended_update_command()}' to install.")


def _ensure_fhs_path_guard() -> None:
    """Ensure /usr/local/bin is on PATH for RHEL-family root non-login shells.

    Mirrors the post-symlink probe added to ``scripts/install.sh`` so that
    existing FHS-layout root installs on RHEL/CentOS/Rocky/Alma 8+ get
    repaired on ``hermes update`` without requiring a reinstall.  The
    installer's assumption that ``/usr/local/bin`` is on PATH for every
    standard shell breaks on those distros in non-login interactive shells
    (su, sudo -s, tmux panes, some web terminals): /etc/bashrc doesn't
    add /usr/local/bin and /root/.bash_profile doesn't either.  Symptom:
    ``hermes`` prints ``command not found`` even though the symlink lives
    at /usr/local/bin/hermes.

    Silent no-op on: non-Linux, non-root, non-FHS installs, and any system
    where ``bash -i -c 'command -v hermes'`` already resolves.  Idempotent.
    """
    if sys.platform != "linux":
        return
    try:
        if os.geteuid() != 0:  # windows-footgun: ok — Linux FHS helper, guarded by sys.platform == "linux" above + AttributeError catch
            return
    except AttributeError:
        return
    # Only act when this is actually an FHS-layout install (command link at
    # /usr/local/bin/hermes, code at /usr/local/lib/openagents).
    fhs_link = Path("/usr/local/bin/hermes")
    if not fhs_link.is_symlink() and not fhs_link.exists():
        return

    # Probe a fresh non-login interactive bash the way the user will use it.
    # ``bash -i -c`` sources ~/.bashrc but NOT ~/.bash_profile or /etc/profile,
    # which is the exact scenario where RHEL root loses /usr/local/bin.
    home = os.environ.get("HOME") or "/root"
    try:
        probe = subprocess.run(
            [
                "env",
                "-i",
                f"HOME={home}",
                f"TERM={os.environ.get('TERM', 'dumb')}",
                "bash",
                "-i",
                "-c",
                "command -v hermes",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return  # no bash or probe hung — don't block update on this
    if probe.returncode == 0:
        return  # already on PATH, nothing to do

    path_line = 'export PATH="/usr/local/bin:$PATH"'
    path_comment = (
        "# OpenAgents — ensure /usr/local/bin is on PATH " "(RHEL non-login shells)"
    )
    wrote_any = False
    for candidate in (".bashrc", ".bash_profile"):
        cfg = Path(home) / candidate
        if not cfg.is_file():
            continue
        try:
            existing = cfg.read_text(errors="replace")
        except OSError:
            continue
        # Idempotency: skip if any uncommented PATH= line already references
        # /usr/local/bin.  Mirrors the grep pattern used by install.sh.
        already_guarded = any(
            "/usr/local/bin" in line
            and "PATH" in line
            and not line.lstrip().startswith("#")
            for line in existing.splitlines()
        )
        if already_guarded:
            continue
        try:
            with cfg.open("a", encoding="utf-8") as f:
                f.write("\n" + path_comment + "\n" + path_line + "\n")
        except OSError as e:
            print(f"  ⚠ Could not update {cfg}: {e}")
            continue
        print(f"  ✓ Added /usr/local/bin to PATH in {cfg}")
        wrote_any = True
    if wrote_any:
        print("    (reload your shell or run 'source ~/.bashrc' to pick it up)")


def _run_pre_update_backup(args) -> None:
    """Create a full zip backup of OPENAGENTS_HOME before running the update.

    Gated on ``updates.pre_update_backup`` in config (default false).  Off
    by default because the zip can add minutes to every update on large
    OPENAGENTS_HOME directories.  The ``--backup`` flag on ``hermes update``
    opts in for a single run; ``--no-backup`` forces it off when config
    has it enabled.  Never raises — a backup failure should not block the
    update itself.
    """
    # CLI flags win over config.  --no-backup beats --backup if both are set.
    if getattr(args, "no_backup", False):
        print("◆ Pre-update backup: skipped (--no-backup)")
        print()
        return

    force_backup = bool(getattr(args, "backup", False))

    try:
        from openagents_cli.config import load_config

        cfg = load_config()
    except Exception as exc:
        logging.getLogger(__name__).debug(
            "Could not load config for pre-update backup: %s", exc
        )
        cfg = {}

    updates_cfg = cfg.get("updates", {}) if isinstance(cfg, dict) else {}
    # The default config ships with ``pre_update_backup: false`` (see
    # ``openagents_cli/config.py``). Fall back to false if the key is missing
    # so the default behaviour matches the shipped config: zipping a large
    # OPENAGENTS_HOME can add minutes to every update. Users who want the
    # #48200 safety net opt in via the config knob or ``--backup``.
    enabled = updates_cfg.get("pre_update_backup", False)
    keep = updates_cfg.get("backup_keep", 5)

    if not enabled and not force_backup:
        # Silent by default — the backup is off, most users don't need to
        # hear about it on every update.  They can opt in via --backup
        # or by flipping the config knob.
        return

    try:
        from openagents_cli.backup import create_pre_update_backup
    except Exception as exc:
        print(
            f"⚠ Pre-update backup: could not load backup module ({exc}); continuing update."
        )
        print()
        return

    print("◆ Creating pre-update backup...")
    t0 = _time.monotonic()
    try:
        out_path = create_pre_update_backup(keep=int(keep))
    except Exception as exc:  # defensive — helper already swallows, but just in case
        print(f"  ⚠ Backup failed: {exc}")
        print("  Continuing with update.")
        print()
        return

    elapsed = _time.monotonic() - t0

    if out_path is None:
        print("  ⚠ Backup skipped (no files found or write failed); continuing update.")
        print()
        return

    try:
        size_bytes = out_path.stat().st_size
    except OSError:
        size_bytes = 0

    # Human-readable size
    size_str = f"{size_bytes} B"
    for unit in ("KB", "MB", "GB"):
        if size_bytes < 1024:
            break
        size_bytes /= 1024
        size_str = f"{size_bytes:.1f} {unit}"

    # Render path using display_openagents_home so the user sees ~/.openagents/...
    try:
        from openagents_constants import get_openagents_home, display_openagents_home

        home = get_openagents_home()
        try:
            display_path = f"{display_openagents_home()}/{out_path.relative_to(home)}"
        except ValueError:
            display_path = str(out_path)
    except Exception:
        display_path = str(out_path)

    print(f"  Saved:    {display_path} ({size_str}, {elapsed:.1f}s)")
    print(f"  Restore:  hermes import {out_path}")
    print(f"  Disable:  omit --backup (backups are off by default)")
    print(f"            set updates.pre_update_backup: false in config.yaml")
    print()


def _write_update_planned_stop_marker(profile_path: Path, pid: int) -> bool:
    """Write a planned-stop marker into a specific profile home."""
    try:
        from datetime import timezone

        from gateway.status import _get_process_start_time
        from utils import atomic_json_write

        record = {
            "target_pid": pid,
            "target_start_time": _get_process_start_time(pid),
            "stopper_pid": os.getpid(),
            "written_at": datetime.now(timezone.utc).isoformat(),
        }
        atomic_json_write(
            Path(profile_path) / ".gateway-planned-stop.json",
            record,
            indent=None,
            separators=(",", ":"),
        )
        return True
    except (OSError, PermissionError):
        return False


def _wait_for_windows_update_gateway_exit(
    pids: list[int], *, timeout: float
) -> set[int]:
    """Wait for the given gateway PIDs to exit, returning survivors."""
    if not pids:
        return set()

    from gateway.status import _pid_exists

    remaining = set(pids)
    deadline = _time.monotonic() + max(timeout, 0.0)
    while remaining and _time.monotonic() < deadline:
        for pid in list(remaining):
            try:
                if not _pid_exists(pid):
                    remaining.discard(pid)
            except Exception:
                remaining.discard(pid)
        if remaining:
            _time.sleep(0.25)

    survivors: set[int] = set()
    for pid in remaining:
        try:
            if _pid_exists(pid):
                survivors.add(pid)
        except Exception:
            pass
    return survivors


def _pause_windows_gateways_for_update() -> dict | None:
    """Stop running Windows gateways before mutating the checkout or venv.

    Windows scheduled/startup gateways run through pythonw.exe, so the generic
    hermes.exe concurrent-instance guard does not see them. They still import
    from the checkout and can keep files locked while ``git`` or ``uv`` updates
    the install. Stop only PIDs that the gateway discovery code identifies.
    """
    if not _is_windows():
        return None

    try:
        from gateway.status import terminate_pid
        from openagents_cli.gateway import (
            _capture_gateway_argv,
            _get_restart_drain_timeout,
            find_gateway_pids,
            find_profile_gateway_processes,
        )
    except Exception as exc:
        logger.debug("Could not prepare Windows gateway pause for update: %s", exc)
        return None

    try:
        running_pids = list(dict.fromkeys(find_gateway_pids(all_profiles=True)))
    except Exception as exc:
        logger.debug("Could not discover Windows gateway PIDs before update: %s", exc)
        return None
    if not running_pids:
        # No gateway is running right now, but the user may have installed an
        # autostart entry (Scheduled Task or Startup-folder login item) — that
        # is an explicit "I want a gateway" signal. A gateway that died between
        # updates (e.g. the spawning terminal/TUI closed, taking its child with
        # it) would otherwise never come back: the autostart entry only fires on
        # the next login, and the update flow's resume path only relaunched
        # gateways that were running when the update began. Cold-start one after
        # the update so an installed gateway is actually up post-update. Users
        # who run gateway-less (no autostart entry) get nothing forced on them.
        try:
            from openagents_cli import gateway_windows

            if gateway_windows.is_installed():
                return {
                    "resume_needed": True,
                    "profiles": {},
                    "unmapped_pids": [],
                    "unmapped": [],
                    "cold_start_if_installed": True,
                }
        except Exception as exc:
            logger.debug(
                "Could not check Windows gateway autostart state before update: %s",
                exc,
            )
        return None

    profile_processes = {}
    try:
        profile_processes = {
            proc.pid: proc for proc in find_profile_gateway_processes()
        }
    except Exception as exc:
        logger.debug("Could not map Windows gateway PIDs to profiles: %s", exc)

    profiles: dict[str, int] = {}
    mapped_pids = []
    for pid in running_pids:
        proc = profile_processes.get(pid)
        if proc is None:
            continue
        profiles[str(proc.profile)] = int(pid)
        mapped_pids.append(int(pid))
        _write_update_planned_stop_marker(Path(proc.path), int(pid))

    print("→ Stopping Windows gateway process(es) before updating Hermes...")
    try:
        drain_timeout = max(float(_get_restart_drain_timeout()), 1.0)
    except Exception:
        drain_timeout = 10.0
    survivors = _wait_for_windows_update_gateway_exit(
        mapped_pids,
        timeout=drain_timeout,
    )
    unmapped_pids = [pid for pid in running_pids if pid not in profile_processes]

    # Snapshot each unmapped gateway's command line *before* we force-kill it,
    # so ``_resume_windows_gateways_after_update`` can respawn it by replaying
    # its own argv. Unmapped gateways are ones with no profile→PID-file mapping
    # — e.g. a Windows Scheduled Task running ``pythonw.exe -m openagents_cli.main
    # gateway run``. Without this snapshot they were force-killed and never
    # restarted (the "Restart manually after update" dead-end from #50090).
    unmapped: list[dict] = []
    for pid in unmapped_pids:
        argv = None
        try:
            argv = _capture_gateway_argv(int(pid))
        except Exception as exc:
            logger.debug("Could not capture argv for unmapped gateway %s: %s", pid, exc)
        unmapped.append({"pid": int(pid), "argv": argv})

    force_killed = []
    for pid in sorted(set(survivors).union(unmapped_pids)):
        try:
            terminate_pid(int(pid), force=True)
            force_killed.append(int(pid))
        except (ProcessLookupError, PermissionError, OSError):
            pass

    if profiles:
        print(f"  ✓ Paused gateway profile(s): {', '.join(sorted(profiles))}")
    if force_killed:
        print(f"  → Force-stopped {len(force_killed)} gateway process(es)")

    if unmapped_pids:
        respawnable = sum(1 for u in unmapped if u.get("argv"))
        print(
            f"  → Stopped {len(unmapped_pids)} gateway process(es) without profile mapping"
        )
        if respawnable < len(unmapped_pids):
            # Some had no recoverable command line (psutil missing, access
            # denied, already gone): those still need a manual restart.
            print("    Restart manually after update: hermes gateway run")

    return {
        "resume_needed": True,
        "profiles": profiles,
        "unmapped_pids": unmapped_pids,
        "unmapped": unmapped,
    }


def _cold_start_windows_gateway_after_update() -> None:
    """Start a fresh detached gateway after update when one is installed but down.

    Invoked from ``_resume_windows_gateways_after_update`` for the
    ``cold_start_if_installed`` case: no gateway was running when the update
    began, but an autostart entry (Scheduled Task / Startup-folder login item)
    is installed, signalling the user wants a gateway. Unlike the relaunch
    paths — which watch an old PID and respawn once it exits — this is a direct
    fresh spawn via the same windowless ``pythonw`` + breakaway path that
    ``hermes gateway start`` uses (``gateway_windows._spawn_detached``).

    Best-effort and idempotent: re-checks that nothing is running first so a
    concurrent start (e.g. the autostart entry firing) can't produce a
    duplicate gateway.
    """
    if not _is_windows():
        return
    try:
        from openagents_cli import gateway_windows
        from openagents_cli.gateway import find_gateway_pids
    except Exception as exc:
        logger.debug("Could not load Windows gateway cold-start helpers: %s", exc)
        return

    # Re-check liveness right before spawning — between pause and resume the
    # autostart entry may have already brought a gateway up, or a leftover
    # process may have re-registered. Don't double-start.
    try:
        if list(find_gateway_pids(all_profiles=True)):
            return
    except Exception as exc:
        logger.debug("Could not re-check gateway liveness before cold-start: %s", exc)
        return

    try:
        pid = gateway_windows._spawn_detached()
    except Exception as exc:
        logger.debug("Could not cold-start Windows gateway after update: %s", exc)
        return

    if pid:
        print()
        print(f"  ✓ Starting Windows gateway after update (PID {pid})")


def _resume_windows_gateways_after_update(token: dict | None) -> None:
    """Restart Windows profile gateways previously paused for update."""
    if not token or not token.get("resume_needed"):
        return
    token["resume_needed"] = False
    if not _is_windows():
        return

    profiles = token.get("profiles") or {}
    unmapped = token.get("unmapped") or []
    cold_start = bool(token.get("cold_start_if_installed"))
    if not profiles and not any(u.get("argv") for u in unmapped):
        if cold_start:
            _cold_start_windows_gateway_after_update()
        return

    try:
        from openagents_cli.gateway import (
            launch_detached_gateway_restart_by_cmdline,
            launch_detached_profile_gateway_restart,
        )
    except Exception as exc:
        logger.debug("Could not load Windows gateway restart helper: %s", exc)
        return

    relaunched = []
    for profile, old_pid in sorted(profiles.items()):
        try:
            if launch_detached_profile_gateway_restart(str(profile), int(old_pid)):
                relaunched.append(str(profile))
        except Exception as exc:
            logger.debug(
                "Could not restart Windows gateway profile %s after update: %s",
                profile,
                exc,
            )

    # Respawn unmapped gateways (no profile→PID-file mapping, e.g. a Scheduled
    # Task) by replaying the argv we snapshotted before force-killing them.
    unmapped_relaunched = 0
    for entry in unmapped:
        argv = entry.get("argv")
        old_pid = entry.get("pid")
        if not argv or not old_pid:
            continue
        try:
            if launch_detached_gateway_restart_by_cmdline(int(old_pid), list(argv)):
                unmapped_relaunched += 1
        except Exception as exc:
            logger.debug(
                "Could not restart unmapped Windows gateway (pid %s) after update: %s",
                old_pid,
                exc,
            )

    if relaunched:
        print()
        print(f"  ✓ Restarting Windows gateway profile(s): {', '.join(relaunched)}")
    if unmapped_relaunched:
        if not relaunched:
            print()
        print(
            f"  ✓ Restarting {unmapped_relaunched} unmapped Windows gateway process(es)"
        )


def _discard_lockfile_churn(git_cmd, repo_root):
    """Restore tracked ``package-lock.json`` files that npm dirtied locally.

    npm rewrites lockfiles non-deterministically at install/build time. On a
    managed install those diffs are never intentional, so we discard them so
    ``hermes update`` sees a clean tree instead of autostashing every run.
    Best-effort; only ever touches files named ``package-lock.json``.
    """
    try:
        diff = subprocess.run(
            git_cmd + ["diff", "--name-only"],
            cwd=repo_root,
            capture_output=True,
            text=True,
        )
        if diff.returncode != 0:
            return
        dirty_package_dirs = {
            Path(line.strip()).parent
            for line in diff.stdout.splitlines()
            if line.strip().endswith("package.json")
        }
        dirty = [
            line.strip()
            for line in diff.stdout.splitlines()
            if line.strip().endswith("package-lock.json")
            and Path(line.strip()).parent not in dirty_package_dirs
        ]
        if not dirty:
            return
        subprocess.run(
            git_cmd + ["checkout", "--", *dirty],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        print(f"→ Discarded npm lockfile churn ({len(dirty)} file(s))")
    except Exception:
        # Never let lockfile cleanup block an update.
        pass


def cmd_update(args):
    """Update OpenAgents to the latest version.

    Thin wrapper around ``_cmd_update_impl``: installs hangup protection,
    runs the update, then restores stdio on the way out (even on
    ``sys.exit`` or unhandled exceptions).
    """
    from openagents_cli.config import (
        detect_install_method,
        format_docker_update_message,
        is_managed,
        managed_error,
    )
||||||| cf299e9a01
def _format_time_ago(iso_ts: str) -> str:
    """Render an ISO timestamp as `Xh ago` / `Xd ago` / `Xm ago`. Best effort."""
    try:
        from datetime import datetime, timezone
        ts = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - ts
        secs = int(delta.total_seconds())
        if secs < 60:
            return "just now"
        if secs < 3600:
            return f"{secs // 60}m ago"
        if secs < 86400:
            return f"{secs // 3600}h ago"
        return f"{secs // 86400}d ago"
    except Exception:
        return "recently"


def _kill_stale_dashboard_processes(
    reason: str = "the running backend no longer matches the updated frontend",
) -> None:
    """Kill running ``hermes dashboard`` processes.

    Called at the end of ``hermes update`` (default ``reason``) and also
    from ``hermes dashboard --stop`` (which overrides ``reason``).  The
    dashboard has no service manager, so after a code update the running
    process is guaranteed to be serving stale Python against a
    freshly-updated JS bundle.  Leaving it alive produces silent
    frontend/backend mismatches (new auth headers the old backend doesn't
    recognise → every API call 401s).

    POSIX: SIGTERM, wait up to ~3s for graceful exit, SIGKILL any survivors.
    Windows: ``taskkill /PID <pid> /F`` since there's no clean SIGTERM
    equivalent for background console apps.

    The dashboard isn't auto-restarted because we don't know the original
    launch args (--host, --port, --insecure, --tui, --no-open).  The user
    restarts it manually; a hint is printed.
    """
    # When the OpenAgents Desktop Electron app spawns this dashboard as a
    # backend child, it sets HERMES_DESKTOP_CHILD_PID so that the update
    # path can skip killing the desktop-managed process.  (#37532)
    exclude: set[int] | None = None
    raw_pid = os.environ.get("HERMES_DESKTOP_CHILD_PID")
    if raw_pid:
        # The desktop may manage several backends (one per active profile) and
        # passes them comma-separated; a lone int still parses for back-compat.
        parsed: set[int] = set()
        for part in raw_pid.split(","):
            part = part.strip()
            if not part:
                continue
            try:
                parsed.add(int(part))
            except (ValueError, TypeError):
                pass
        if parsed:
            exclude = parsed

    pids = _find_stale_dashboard_pids(exclude_pids=exclude)
    if not pids:
        return

    print()
    print(f"⟲ Stopping {len(pids)} dashboard process(es) ({reason})")

    killed: list[int] = []
    failed: list[tuple[int, str]] = []

    if sys.platform == "win32":
        for pid in pids:
            try:
                result = subprocess.run(
                    ["taskkill", "/PID", str(pid), "/F"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                if result.returncode == 0:
                    killed.append(pid)
                else:
                    failed.append((pid, (result.stderr or result.stdout or "").strip()))
            except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as e:
                failed.append((pid, str(e)))
    else:
        import signal as _signal
        import time as _time

        # SIGTERM first — give each process a chance to shut down cleanly
        # (uvicorn closes its socket, flushes logs, etc.).
        for pid in pids:
            try:
                os.kill(pid, _signal.SIGTERM)
            except ProcessLookupError:
                # Already gone — count as killed.
                killed.append(pid)
            except (PermissionError, OSError) as e:
                failed.append((pid, str(e)))

        # Poll for exit up to ~3s total.
        deadline = _time.monotonic() + 3.0
        pending = [
            p for p in pids if p not in killed and p not in {f[0] for f in failed}
        ]
        while pending and _time.monotonic() < deadline:
            _time.sleep(0.1)
            still_pending = []
            # On Windows, os.kill(pid, 0) is NOT a no-op. Route through
            # the cross-platform existence check.
            from gateway.status import _pid_exists
            for pid in pending:
                if _pid_exists(pid):
                    still_pending.append(pid)
                else:
                    killed.append(pid)
            pending = still_pending

        # SIGKILL any survivors.
        for pid in pending:
            try:
                os.kill(pid, _signal.SIGKILL)
                killed.append(pid)
            except ProcessLookupError:
                killed.append(pid)
            except (PermissionError, OSError) as e:
                failed.append((pid, str(e)))

    for pid in killed:
        print(f"    ✓ stopped PID {pid}")
    for pid, err_msg in failed:
        print(f"    ✗ failed to stop PID {pid}: {err_msg}")

    if killed:
        print("  Restart the dashboard when you're ready:")
        print("    hermes dashboard --port <port>")


# Back-compat alias: some tests and any external callers may import the old
# warn-only name.  The new behaviour (kill stale processes) replaces it.
_warn_stale_dashboard_processes = _kill_stale_dashboard_processes


def _atomic_replace_dir(src: str, dst: str) -> None:
    """Replace directory *dst* with *src* without leaving *dst* half-deleted.

    The naive ``rmtree(dst); copytree(src, dst)`` has a destructive window: if
    the copy fails partway (common on the Windows ZIP-update path, which only
    runs because file I/O is already flaky on that machine), the old directory
    is already gone and nothing replaced it — the install is left with a
    deleted tree (issue #49145, where ``ui-tui/`` vanished and broke the TUI).

    Instead, stage the new copy into a sibling temp dir first; only once that
    fully succeeds do we swap it in. A failure during staging raises with the
    original *dst* still intact.
    """
    staging = f"{dst}.hermes-update-staging"
    backup = f"{dst}.hermes-update-old"
    # Clear any leftovers from a previously-interrupted update.
    for leftover in (staging, backup):
        if os.path.exists(leftover):
            shutil.rmtree(leftover, ignore_errors=True)

    # 1. Stage the new copy. If this fails, dst is untouched.
    shutil.copytree(src, staging)
    # 2. Swap: move the live dir aside, move staging into place. Both moves are
    #    same-filesystem renames; if the second fails we restore the backup.
    if os.path.exists(dst):
        os.rename(dst, backup)
    try:
        os.rename(staging, dst)
    except OSError:
        if os.path.exists(backup) and not os.path.exists(dst):
            os.rename(backup, dst)  # roll back to the original
        raise
    # 3. New dir is in place; drop the old one (best-effort — never fatal).
    if os.path.exists(backup):
        shutil.rmtree(backup, ignore_errors=True)


def _update_via_zip(args):
    """Update OpenAgents by downloading a ZIP archive.

    Used on Windows when git file I/O is broken (antivirus, NTFS filter
    drivers causing 'Invalid argument' errors on file creation).
    """
    import tempfile
    import zipfile
    from urllib.request import urlretrieve

    # The ZIP fallback exists for Windows git-file-I/O breakage. It pulls a
    # static archive from GitHub, which is fine for the default "main"
    # channel but would silently ignore --branch and update from main even
    # if the user asked for something else — exactly the silent-divergence
    # bug --branch was added to prevent. Refuse to proceed in that case
    # rather than lie.
    branch = _resolve_update_branch(args)
    if branch != "main":
        print(
            f"✗ --branch={branch} is not supported on the Windows ZIP-fallback "
            "update path."
        )
        print(
            "  This path runs when git file I/O is broken on the system. "
            "Either resolve the git-side breakage (typically an antivirus "
            "or NTFS filter holding files open) and rerun `hermes update "
            f"--branch {branch}`, or update against main with `hermes update`."
        )
        sys.exit(1)
    zip_url = (
        f"https://github.com/NousResearch/openagents/archive/refs/heads/{branch}.zip"
    )

    print("→ Downloading latest version...")
    tmp_dir = tempfile.mkdtemp(prefix="hermes-update-")
    try:
        zip_path = os.path.join(tmp_dir, f"openagents-{branch}.zip")
        urlretrieve(zip_url, zip_path)

        print("→ Extracting...")
        import stat as _stat
        with zipfile.ZipFile(zip_path, "r") as zf:
            # Validate paths to prevent zip-slip (path traversal) AND reject
            # symlink members. A GitHub source ZIP for openagents itself
            # should never contain symlinks — they'd point outside the
            # extracted tree and let an attacker who can compromise the
            # update mirror plant arbitrary files via the update path.
            tmp_dir_real = os.path.realpath(tmp_dir)
            for member in zf.infolist():
                member_path = os.path.realpath(os.path.join(tmp_dir, member.filename))
                if (
                    not member_path.startswith(tmp_dir_real + os.sep)
                    and member_path != tmp_dir_real
                ):
                    raise ValueError(
                        f"Zip-slip detected: {member.filename} escapes extraction directory"
                    )
                # Unix mode lives in the upper 16 bits of external_attr;
                # mask to the file-type bits.
                mode = (member.external_attr >> 16) & 0o170000
                if _stat.S_ISLNK(mode):
                    raise ValueError(
                        f"ZIP contains unsupported symlink member: {member.filename}"
                    )
            zf.extractall(tmp_dir)

        # GitHub ZIPs extract to openagents-<branch>/
        extracted = os.path.join(tmp_dir, f"openagents-{branch}")
        if not os.path.isdir(extracted):
            # Try to find it
            for d in os.listdir(tmp_dir):
                candidate = os.path.join(tmp_dir, d)
                if os.path.isdir(candidate) and d != "__MACOSX":
                    extracted = candidate
                    break

        # Copy updated files over existing installation, preserving venv/node_modules/.git
        preserve = {"venv", "node_modules", ".git", ".env"}
        update_count = 0
        for item in os.listdir(extracted):
            if item in preserve:
                continue
            src = os.path.join(extracted, item)
            dst = os.path.join(str(PROJECT_ROOT), item)
            if os.path.isdir(src):
                # Atomic-ish replace: never leave dst half-deleted if the copy
                # fails partway (the failure mode behind #49145 on Windows).
                _atomic_replace_dir(src, dst)
            else:
                shutil.copy2(src, dst)
            update_count += 1

        print(f"✓ Updated {update_count} items from ZIP")

    except Exception as e:
        print(f"✗ ZIP update failed: {e}")
        sys.exit(1)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    # Clear stale bytecode after ZIP extraction
    removed = _clear_bytecode_cache(PROJECT_ROOT)
    if removed:
        print(
            f"  ✓ Cleared {removed} stale __pycache__ director{'y' if removed == 1 else 'ies'}"
        )

    # Reinstall Python dependencies. Prefer .[all], but if one optional extra
    # breaks on this machine, keep base deps and reinstall the remaining extras
    # individually so update does not silently strip working capabilities.
    print("→ Updating Python dependencies...")

    from openagents_cli.managed_uv import ensure_uv, update_managed_uv

    # Keep managed uv current — runs `uv self update` if we already have one.
    update_managed_uv()

    uv_bin = ensure_uv()

    pip_cmd = [sys.executable, "-m", "pip"]
    if not uv_bin:
        uv_bin = _ensure_uv_for_termux(pip_cmd)
    if uv_bin:
        uv_env = {**os.environ, "VIRTUAL_ENV": str(PROJECT_ROOT / "venv")}
        if _is_termux_env(uv_env):
            uv_env.pop("PYTHONPATH", None)
            uv_env.pop("PYTHONHOME", None)
        _install_python_dependencies_with_optional_fallback([uv_bin, "pip"], env=uv_env)
    else:
        # Use sys.executable to explicitly call the venv's pip module,
        # avoiding PEP 668 'externally-managed-environment' errors on Debian/Ubuntu.
        # Some environments lose pip inside the venv; bootstrap it back with
        # ensurepip before trying the editable install.
        try:
            subprocess.run(
                pip_cmd + ["--version"],
                cwd=PROJECT_ROOT,
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError:
            subprocess.run(
                [sys.executable, "-m", "ensurepip", "--upgrade", "--default-pip"],
                cwd=PROJECT_ROOT,
                check=True,
            )
        _install_python_dependencies_with_optional_fallback(pip_cmd)

    _update_node_dependencies()
    _build_web_ui(PROJECT_ROOT / "web")

    # Sync skills
    try:
        from tools.skills_sync import sync_skills

        print("→ Syncing bundled skills...")
        result = sync_skills(quiet=True)
        if result["copied"]:
            print(f"  + {len(result['copied'])} new: {', '.join(result['copied'])}")
        if result.get("updated"):
            print(
                f"  ↑ {len(result['updated'])} updated: {', '.join(result['updated'])}"
            )
        if result.get("user_modified"):
            print(f"  ~ {len(result['user_modified'])} user-modified (kept)")
            print(
                "    → see them: hermes skills list-modified  "
                "(diff/reset to resume updates)"
            )
        if result.get("cleaned"):
            print(f"  − {len(result['cleaned'])} removed from manifest")
        if not result["copied"] and not result.get("updated"):
            print("  ✓ Skills are up to date")
    except Exception:
        pass

    # Seed the model-catalog disk cache from the freshly-unpacked checkout
    # (same rationale as the git-pull path in _cmd_update_impl). Non-fatal.
    try:
        from openagents_cli.model_catalog import seed_cache_from_checkout

        if seed_cache_from_checkout(PROJECT_ROOT):
            print("  ✓ Model catalog cache refreshed from checkout")
    except Exception as e:
        logger.debug("Model catalog seed during zip update failed: %s", e)

    print()
    print("✓ Update complete!")
    try:
        _print_curator_first_run_notice()
    except Exception as e:
        logger.debug("Curator first-run notice failed: %s", e)
    try:
        _print_curator_recent_run_notice()
    except Exception as e:
        logger.debug("Curator recent-run notice failed: %s", e)
    _kill_stale_dashboard_processes()


def _stash_local_changes_if_needed(git_cmd: list[str], cwd: Path) -> Optional[str]:
    status = subprocess.run(
        git_cmd + ["status", "--porcelain"],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    if not status.stdout.strip():
        return None

    # If the index has unmerged entries (e.g. from an interrupted merge/rebase),
    # git stash will fail with "needs merge / could not write index".  Clear the
    # conflict state with `git reset` so the stash can proceed.  Working-tree
    # changes are preserved; only the index conflict markers are dropped.
    unmerged = subprocess.run(
        git_cmd + ["ls-files", "--unmerged"],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    if unmerged.stdout.strip():
        print("→ Clearing unmerged index entries from a previous conflict...")
        subprocess.run(git_cmd + ["reset"], cwd=cwd, capture_output=True)

    from datetime import datetime, timezone

    stash_name = datetime.now(timezone.utc).strftime(
        "hermes-update-autostash-%Y%m%d-%H%M%S"
    )
    print("→ Local changes detected — stashing before update...")
    subprocess.run(
        git_cmd + ["stash", "push", "--include-untracked", "-m", stash_name],
        cwd=cwd,
        check=True,
    )
    stash_ref = subprocess.run(
        git_cmd + ["rev-parse", "--verify", "refs/stash"],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return stash_ref


def _resolve_stash_selector(
    git_cmd: list[str], cwd: Path, stash_ref: str
) -> Optional[str]:
    stash_list = subprocess.run(
        git_cmd + ["stash", "list", "--format=%gd %H"],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    for line in stash_list.stdout.splitlines():
        selector, _, commit = line.partition(" ")
        if commit.strip() == stash_ref:
            return selector.strip()
    return None


def _print_stash_cleanup_guidance(
    stash_ref: str, stash_selector: Optional[str] = None
) -> None:
    print(
        "  Check `git status` first so you don't accidentally reapply the same change twice."
    )
    print("  Find the saved entry with: git stash list --format='%gd %H %s'")
    if stash_selector:
        print(f"  Remove it with: git stash drop {stash_selector}")
    else:
        print(
            f"  Look for commit {stash_ref}, then drop its selector with: git stash drop stash@{{N}}"
        )


def _restore_stashed_changes(
    git_cmd: list[str],
    cwd: Path,
    stash_ref: str,
    prompt_user: bool = False,
    input_fn=None,
) -> bool:
    if prompt_user:
        print()
        print("⚠ Local changes were stashed before updating.")
        print(
            "  Restoring them may reapply local customizations onto the updated codebase."
        )
        print("  Review the result afterward if OpenAgents behaves unexpectedly.")
        print("Restore local changes now? [Y/n]")
        if input_fn is not None:
            response = input_fn("Restore local changes now? [Y/n]", "y")
        else:
            response = input().strip().lower()
        if response not in {"", "y", "yes"}:
            print("Skipped restoring local changes.")
            print("Your changes are still preserved in git stash.")
            print(f"Restore manually with: git stash apply {stash_ref}")
            return False

    print("→ Restoring local changes...")
    restore = subprocess.run(
        git_cmd + ["stash", "apply", stash_ref],
        cwd=cwd,
        capture_output=True,
        text=True,
    )

    # Check for unmerged (conflicted) files — can happen even when returncode is 0
    unmerged = subprocess.run(
        git_cmd + ["diff", "--name-only", "--diff-filter=U"],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    has_conflicts = bool(unmerged.stdout.strip())

    if restore.returncode != 0 or has_conflicts:
        print("✗ Update pulled new code, but restoring local changes hit conflicts.")
        if restore.stdout.strip():
            print(restore.stdout.strip())
        if restore.stderr.strip():
            print(restore.stderr.strip())

        # Show which files conflicted
        conflicted_files = unmerged.stdout.strip()
        if conflicted_files:
            print("\nConflicted files:")
            for f in conflicted_files.splitlines():
                print(f"  • {f}")

        print("\nYour stashed changes are preserved — nothing is lost.")
        print(f"  Stash ref: {stash_ref}")

        # Always reset to clean state — leaving conflict markers in source
        # files makes hermes completely unrunnable (SyntaxError on import).
        # The user's changes are safe in the stash for manual recovery.
        subprocess.run(
            git_cmd + ["reset", "--hard", "HEAD"],
            cwd=cwd,
            capture_output=True,
        )
        print("Working tree reset to clean state.")
        print(f"Restore your changes later with: git stash apply {stash_ref}")
        # Don't sys.exit — the code update itself succeeded, only the stash
        # restore had conflicts.  Let cmd_update continue with pip install,
        # skill sync, and gateway restart.
        return False

    stash_selector = _resolve_stash_selector(git_cmd, cwd, stash_ref)
    if stash_selector is None:
        print(
            "⚠ Local changes were restored, but OpenAgents couldn't find the stash entry to drop."
        )
        print(
            "  The stash was left in place. You can remove it manually after checking the result."
        )
        _print_stash_cleanup_guidance(stash_ref)
    else:
        drop = subprocess.run(
            git_cmd + ["stash", "drop", stash_selector],
            cwd=cwd,
            capture_output=True,
            text=True,
        )
        if drop.returncode != 0:
            print(
                "⚠ Local changes were restored, but OpenAgents couldn't drop the saved stash entry."
            )
            if drop.stdout.strip():
                print(drop.stdout.strip())
            if drop.stderr.strip():
                print(drop.stderr.strip())
            print(
                "  The stash was left in place. You can remove it manually after checking the result."
            )
            _print_stash_cleanup_guidance(stash_ref, stash_selector)

    print("⚠ Local changes were restored on top of the updated codebase.")
    print("  Review `git diff` / `git status` if OpenAgents behaves unexpectedly.")
    return True


def _discard_stashed_changes(
    git_cmd: list[str],
    cwd: Path,
    stash_ref: str,
) -> bool:
    """Throw away a stash created before an update, without applying it.

    Used only on a NON-interactive update when the user has set
    ``updates.non_interactive_local_changes: discard`` — i.e. they've opted out
    of keeping local source edits on this machine. Drops the stash entry
    instead of re-applying it, so the working tree stays clean at the freshly
    pulled HEAD. Unlike ``git reset --hard`` + ``git clean -fd``, this only
    affects what was stashed (tracked changes + the untracked files we
    explicitly captured) — ignored paths like node_modules/venv/build outputs
    are never touched, since they were never stashed.

    Returns True if the stash was dropped, False on a git failure (in which
    case the stash is left in place for safety).
    """
    stash_selector = _resolve_stash_selector(git_cmd, cwd, stash_ref)
    if stash_selector is None:
        print(
            "⚠ Configured to discard local changes on non-interactive update, "
            "but OpenAgents couldn't find the stash entry to drop."
        )
        _print_stash_cleanup_guidance(stash_ref)
        return False

    drop = subprocess.run(
        git_cmd + ["stash", "drop", stash_selector],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    if drop.returncode != 0:
        print(
            "⚠ Configured to discard local changes, but OpenAgents couldn't drop "
            "the saved stash entry."
        )
        if drop.stderr.strip():
            print(f"  {drop.stderr.strip().splitlines()[0]}")
        _print_stash_cleanup_guidance(stash_ref, stash_selector)
        return False

    print("→ Discarded local source changes (updates.non_interactive_local_changes=discard).")
    return True


# =========================================================================
# Fork detection and upstream management for `hermes update`
# =========================================================================

OFFICIAL_REPO_URLS = {
    "https://github.com/NousResearch/openagents.git",
    "git@github.com:NousResearch/openagents.git",
    "https://github.com/NousResearch/openagents",
    "git@github.com:NousResearch/openagents",
}
OFFICIAL_REPO_URL = "https://github.com/NousResearch/openagents.git"
SKIP_UPSTREAM_PROMPT_FILE = ".skip_upstream_prompt"


def _get_origin_url(git_cmd: list[str], cwd: Path) -> Optional[str]:
    """Get the URL of the origin remote, or None if not set."""
    try:
        result = subprocess.run(
            git_cmd + ["remote", "get-url", "origin"],
            cwd=cwd,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None


def _is_fork(origin_url: Optional[str]) -> bool:
    """Check if the origin remote points to a fork (not the official repo)."""
    if not origin_url:
        return False
    # Normalize URL for comparison (strip trailing .git if present)
    normalized = origin_url.rstrip("/")
    if normalized.endswith(".git"):
        normalized = normalized[:-4]
    for official in OFFICIAL_REPO_URLS:
        official_normalized = official.rstrip("/")
        if official_normalized.endswith(".git"):
            official_normalized = official_normalized[:-4]
        if normalized == official_normalized:
            return False
    return True


def _has_upstream_remote(git_cmd: list[str], cwd: Path) -> bool:
    """Check if an 'upstream' remote already exists."""
    try:
        result = subprocess.run(
            git_cmd + ["remote", "get-url", "upstream"],
            cwd=cwd,
            capture_output=True,
            text=True,
        )
        return result.returncode == 0
    except Exception:
        return False


def _add_upstream_remote(git_cmd: list[str], cwd: Path) -> bool:
    """Add the official repo as the 'upstream' remote. Returns True on success."""
    try:
        result = subprocess.run(
            git_cmd + ["remote", "add", "upstream", OFFICIAL_REPO_URL],
            cwd=cwd,
            capture_output=True,
            text=True,
        )
        return result.returncode == 0
    except Exception:
        return False


def _count_commits_between(git_cmd: list[str], cwd: Path, base: str, head: str) -> int:
    """Count commits on `head` that are not on `base`. Returns -1 on error."""
    try:
        result = subprocess.run(
            git_cmd + ["rev-list", "--count", f"{base}..{head}"],
            cwd=cwd,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return int(result.stdout.strip())
    except Exception:
        pass
    return -1


def _should_skip_upstream_prompt() -> bool:
    """Check if user previously declined to add upstream."""
    from openagents_constants import get_openagents_home

    return (get_openagents_home() / SKIP_UPSTREAM_PROMPT_FILE).exists()


def _mark_skip_upstream_prompt():
    """Create marker file to skip future upstream prompts."""
    try:
        from openagents_constants import get_openagents_home

        (get_openagents_home() / SKIP_UPSTREAM_PROMPT_FILE).touch()
    except Exception:
        pass


def _sync_fork_with_upstream(git_cmd: list[str], cwd: Path) -> bool:
    """Attempt to push updated main to origin (sync fork).

    Returns True if push succeeded, False otherwise.
    """
    try:
        result = subprocess.run(
            git_cmd + ["push", "origin", "main", "--force-with-lease"],
            cwd=cwd,
            capture_output=True,
            text=True,
        )
        return result.returncode == 0
    except Exception:
        return False


def _sync_with_upstream_if_needed(git_cmd: list[str], cwd: Path) -> None:
    """Check if fork is behind upstream and sync if safe.

    This implements the fork upstream sync logic:
    - If upstream remote doesn't exist, ask user if they want to add it
    - Compare origin/main with upstream/main
    - If origin/main is strictly behind upstream/main, pull from upstream
    - Try to sync fork back to origin if possible
    """
    has_upstream = _has_upstream_remote(git_cmd, cwd)

    if not has_upstream:
        # Check if user previously declined
        if _should_skip_upstream_prompt():
            return

        # Ask user if they want to add upstream
        print()
        print("ℹ Your fork is not tracking the official OpenAgents repository.")
        print("  This means you may miss updates from NousResearch/openagents.")
        print()
        try:
            response = (
                input("Add official repo as 'upstream' remote? [Y/n]: ").strip().lower()
            )
        except (EOFError, KeyboardInterrupt):
            print()
            response = "n"

        if response in {"", "y", "yes"}:
            print("→ Adding upstream remote...")
            if _add_upstream_remote(git_cmd, cwd):
                print(
                    "  ✓ Added upstream: https://github.com/NousResearch/openagents.git"
                )
                has_upstream = True
            else:
                print("  ✗ Failed to add upstream remote. Skipping upstream sync.")
                return
        else:
            print(
                "  Skipped. Run 'git remote add upstream https://github.com/NousResearch/openagents.git' to add later."
            )
            _mark_skip_upstream_prompt()
            return

    # Fetch upstream main only. This sync compares upstream/main with
    # origin/main, so there's no reason to pull every upstream ref — and a bare
    # fetch drags in thousands of auto-generated branches.
    print()
    print("→ Fetching upstream...")
    try:
        subprocess.run(
            git_cmd + ["fetch", "upstream", "main", "--quiet"],
            cwd=cwd,
            capture_output=True,
            check=True,
        )
    except subprocess.CalledProcessError:
        print("  ✗ Failed to fetch upstream. Skipping upstream sync.")
        return

    # Compare origin/main with upstream/main
    origin_ahead = _count_commits_between(git_cmd, cwd, "upstream/main", "origin/main")
    upstream_ahead = _count_commits_between(
        git_cmd, cwd, "origin/main", "upstream/main"
    )

    if origin_ahead < 0 or upstream_ahead < 0:
        print("  ✗ Could not compare branches. Skipping upstream sync.")
        return

    # If origin/main has commits not on upstream, don't trample
    if origin_ahead > 0:
        print()
        print(f"ℹ Your fork has {origin_ahead} commit(s) not on upstream.")
        print("  Skipping upstream sync to preserve your changes.")
        print("  If you want to merge upstream changes, run:")
        print("    git pull upstream main")
        return

    # If upstream is not ahead, fork is up to date
    if upstream_ahead == 0:
        print("  ✓ Fork is up to date with upstream")
        return

    # origin/main is strictly behind upstream/main (can fast-forward)
    print()
    print(f"→ Fork is {upstream_ahead} commit(s) behind upstream")
    print("→ Pulling from upstream...")

    try:
        subprocess.run(
            git_cmd + ["pull", "--ff-only", "upstream", "main"],
            cwd=cwd,
            check=True,
        )
    except subprocess.CalledProcessError:
        print(
            "  ✗ Failed to pull from upstream. You may need to resolve conflicts manually."
        )
        return

    print("  ✓ Updated from upstream")

    # Try to sync fork back to origin
    print("→ Syncing fork...")
    if _sync_fork_with_upstream(git_cmd, cwd):
        print("  ✓ Fork synced with upstream")
    else:
        print(
            "  ℹ Got updates from upstream but couldn't push to fork (no write access?)"
        )
        print("    Your local repo is updated, but your fork on GitHub may be behind.")


def _invalidate_update_cache():
    """Delete the update-check cache for ALL profiles so no banner
    reports a stale "commits behind" count after a successful update.

    The git repo is shared across profiles — when one profile runs
    ``hermes update``, every profile is now current.
    """
    homes = []
    # Default profile home (Docker-aware — uses /opt/data in Docker)
    from openagents_constants import get_default_openagents_root

    default_home = get_default_openagents_root()
    homes.append(default_home)
    # Named profiles under <root>/profiles/
    profiles_root = default_home / "profiles"
    if profiles_root.is_dir():
        for entry in profiles_root.iterdir():
            if entry.is_dir():
                homes.append(entry)
    for home in homes:
        try:
            cache_file = home / ".update_check"
            if cache_file.exists():
                cache_file.unlink()
        except Exception:
            pass


def _load_installable_optional_extras(group: str = "all") -> list[str]:
    """Return optional extras referenced by a dependency group.

    ``group`` is usually ``all`` (desktop/server broad install) or
    ``termux-all`` (Termux-compatible broad install).
    """
    try:
        import tomllib

        with (PROJECT_ROOT / "pyproject.toml").open("rb") as handle:
            project = tomllib.load(handle).get("project", {})
    except Exception:
        return []

    optional_deps = project.get("optional-dependencies", {})
    if not isinstance(optional_deps, dict):
        return []

    refs = optional_deps.get(group, [])
    referenced: list[str] = []
    for ref in refs:
        if "[" in ref and "]" in ref:
            name = ref.split("[", 1)[1].split("]", 1)[0]
            if name in optional_deps:
                referenced.append(name)

    return referenced


# Install-scoped breadcrumb dropped right before ``hermes update`` mutates the
# venv and cleared only after the dependency install verifies clean.  If a user
# kills the update mid-install (Ctrl-C, terminal close, WSL OOM), the marker
# survives and the next ``hermes`` launch finishes the install instead of
# limping along on a half-built venv (e.g. pip wiped, a core dep like Pillow
# never landed).  Lives next to the venv (not under $OPENAGENTS_HOME) because the
# venv is shared across all profiles, so a single marker covers every profile.
def _update_marker_path() -> Path:
    return PROJECT_ROOT / ".update-incomplete"


def _write_update_incomplete_marker() -> None:
    """Drop the interrupted-install breadcrumb. Never raises."""
    try:
        _update_marker_path().write_text(
            f"started={_time.time()}\npid={os.getpid()}\n", encoding="utf-8"
        )
    except OSError as exc:
        logger.debug("Could not write update-incomplete marker: %s", exc)


def _clear_update_incomplete_marker() -> None:
    """Remove the interrupted-install breadcrumb. Never raises."""
    try:
        _update_marker_path().unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        logger.debug("Could not clear update-incomplete marker: %s", exc)


def _recover_from_interrupted_install() -> None:
    """Finish a dependency install that a prior ``hermes update`` left half-done.

    Triggered on launch when ``.update-incomplete`` is present — meaning the
    code was pulled but the dep install was killed before it verified clean.
    Unconditionally bootstraps pip via ``ensurepip`` (a killed ``pip install``
    can wipe pip from the venv entirely, which blocks the venv from recovering
    on its own), then re-runs the editable ``.[all]`` install + core-dependency
    verification, then clears the marker.

    Never raises: a recovery failure must not block launch.  If it can't
    self-heal it prints the one-line manual command and leaves the marker so
    the next launch tries again.

    Concurrency: the marker lives next to the shared venv, so a gateway start
    plus a CLI launch (or two profiles starting at once) can both see it.  An
    ``O_EXCL`` lockfile ensures only one process runs the reinstall; the
    others skip and let the winner clear the marker.

    Output: everything — our status lines AND the streamed pip/uv install
    (which inherits fd 1) — is routed to stderr.  Launches whose stdout is a
    protocol stream (``hermes acp`` speaks JSON-RPC on stdout) must never get
    install noise on stdout.
    """
    if not _update_marker_path().exists():
        return

    # Skip in managed/Docker installs and on PyPI installs with no git checkout:
    # those don't run the source-tree update path, so a stray marker is not ours
    # to act on. Just clear it.
    if not (PROJECT_ROOT / "pyproject.toml").is_file():
        _clear_update_incomplete_marker()
        return

    # Single-flight guard: atomically claim the recovery lock. If another
    # process holds it, skip — it is running the same reinstall into the same
    # shared venv right now. A crashed holder leaves a stale lock; break it
    # after an hour (well past any realistic install) so recovery can't be
    # wedged forever.
    lock_path = PROJECT_ROOT / ".update-incomplete.lock"
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, f"{os.getpid()}\n".encode())
        os.close(fd)
    except FileExistsError:
        try:
            if _time.time() - lock_path.stat().st_mtime > 3600:
                lock_path.unlink()
        except OSError:
            pass
        return
    except OSError as exc:
        # Couldn't create the lock (read-only fs, perms). Proceed unlocked —
        # the install itself will surface the real problem.
        logger.debug("Could not create install-recovery lock: %s", exc)

    # Windows self-lock guard: if hermes.exe is the launcher that spawned
    # this Python process, any attempt to pip-install will fail with
    # "拒绝访问 / WinError 32" because the running .exe cannot be replaced.
    # Rather than entering the permanent retry loop described in issue
    # #45542, clear the marker and give the user an offline recovery command.
    if _is_windows():
        scripts_dir = _venv_scripts_dir()
        if scripts_dir is not None:
            shims = _hermes_exe_shims(scripts_dir)
            if shims:
                _shim_set: set[str] = set()
                for _s in shims:
                    try:
                        _shim_set.add(str(_s.resolve()).lower())
                    except OSError:
                        _shim_set.add(str(_s).lower())
                try:
                    import psutil
                    _me = psutil.Process()
                    for _anc in [_me] + list(_me.parents()):
                        try:
                            _anc_exe = _anc.exe()
                            _anc_norm = str(Path(_anc_exe).resolve()).lower()
                        except Exception:
                            continue
                        if _anc_norm in _shim_set:
                            print(
                                "✗ OpenAgents is running from the binary that "
                                "needs to be replaced — the auto-recovery "
                                "cannot overwrite a running executable."
                            )
                            print(
                                "  Restart OpenAgents from a different terminal, "
                                "then run the manual recovery command below:"
                            )
                            print(f'    cd /d "{PROJECT_ROOT}"')
                            print(
                                f'    "{sys.executable}" -m pip install '
                                '-e ".[all]"'
                            )
                            _clear_update_incomplete_marker()
                            try:
                                lock_path.unlink()
                            except OSError:
                                pass
                            return
                except Exception:
                    pass  # psutil is best-effort; fall through to install

    saved_stdout_fd = None
    saved_sys_stdout = sys.stdout
    try:
        # Route Python-level prints AND subprocess-inherited fd 1 to stderr
        # for the duration of recovery (see docstring: ACP stdout safety).
        try:
            saved_stdout_fd = os.dup(1)
            os.dup2(2, 1)
        except OSError:
            saved_stdout_fd = None
        sys.stdout = sys.stderr

        print(
            "⚠ A previous `hermes update` was interrupted mid-install — "
            "finishing dependency installation now..."
        )

        try:
            from openagents_cli.managed_uv import ensure_uv

            # Always bootstrap pip first: a killed install can leave the venv with
            # no pip module at all, and uv may also be gone. ensurepip restores a
            # known-good pip so at least the plain-pip path below can proceed.
            try:
                subprocess.run(
                    [sys.executable, "-m", "ensurepip", "--upgrade", "--default-pip"],
                    cwd=PROJECT_ROOT,
                    capture_output=True,
                )
            except Exception as exc:
                logger.debug("ensurepip during install recovery failed: %s", exc)

            uv_bin = ensure_uv()
            if uv_bin:
                uv_env = {**os.environ, "VIRTUAL_ENV": str(PROJECT_ROOT / "venv")}
                if _is_termux_env(uv_env):
                    uv_env.pop("PYTHONPATH", None)
                    uv_env.pop("PYTHONHOME", None)
                _install_python_dependencies_with_optional_fallback(
                    [uv_bin, "pip"],
                    env=uv_env,
                    group="termux-all" if _is_termux_env(uv_env) else "all",
                )
            else:
                _install_python_dependencies_with_optional_fallback(
                    [sys.executable, "-m", "pip"],
                    group="termux-all" if _is_termux_env() else "all",
                )

            _clear_update_incomplete_marker()
            print("✓ Dependency installation recovered — your install is healthy again.")
        except Exception as exc:
            # Leave the marker in place so the next launch retries. Give the user
            # the exact manual recovery command in the meantime.
            logger.debug("Interrupted-install recovery failed: %s", exc)
            print("✗ Could not auto-recover the interrupted install.")
            print("  Recover manually with:")
            print(f"    cd {PROJECT_ROOT}")
            print(f"    {sys.executable} -m ensurepip --upgrade")
            print(f"    {sys.executable} -m pip install -e '.[all]'")
    finally:
        sys.stdout = saved_sys_stdout
        if saved_stdout_fd is not None:
            try:
                os.dup2(saved_stdout_fd, 1)
                os.close(saved_stdout_fd)
            except OSError:
                pass
        try:
            lock_path.unlink()
        except OSError:
            pass


def _run_install_with_heartbeat(
    cmd: list[str],
    *,
    env: dict[str, str] | None = None,
    heartbeat_interval_seconds: int = 30,
) -> None:
    """Run dependency install command with periodic heartbeat output.

    Some resolvers/build backends (especially when compiling Rust/C extensions)
    can stay quiet for minutes. Emit a simple elapsed-time heartbeat so users
    know ``hermes update`` is still progressing even if pip/uv itself is silent.
    """
    done = threading.Event()
    start = _time.time()

    def _heartbeat() -> None:
        # Wait first, then print, so short installs don't emit noise.
        while not done.wait(heartbeat_interval_seconds):
            elapsed = int(_time.time() - start)
            print(
                f"  … still installing dependencies ({elapsed}s elapsed)"
                " — compiling Rust/C extensions can take several minutes",
                flush=True,
            )

    t = threading.Thread(target=_heartbeat, daemon=True)
    t.start()
    try:
        subprocess.run(
            cmd,
            cwd=PROJECT_ROOT,
            check=True,
            env=env,
        )
    finally:
        done.set()
        t.join(timeout=0.2)


def _is_windows() -> bool:
    return sys.platform == "win32"


def _venv_scripts_dir() -> Path | None:
    """Return the venv Scripts directory if we're running inside the project venv."""
    venv_dir = PROJECT_ROOT / "venv"
    if not venv_dir.is_dir():
        return None
    scripts = venv_dir / ("Scripts" if _is_windows() else "bin")
    return scripts if scripts.is_dir() else None


def _hermes_exe_shims(scripts_dir: Path) -> list[Path]:
    """Entry-point shims that uv may try to rewrite during ``pip install -e .``.

    On Windows these are .exe launchers generated by setuptools/uv. On POSIX
    they're regular Python scripts which can be replaced atomically — no
    self-replacement hazard exists outside Windows.
    """
    if not _is_windows():
        return []

    names = set(_load_console_script_names()) or {"openagents", "openagents", "openagents-acp"}
    # The gateway shim is not a [project.scripts] entry point, but older
    # update/install paths still rewrite and quarantine it.
    names.add("openagents-gateway")
    return [scripts_dir / f"{name}.exe" for name in sorted(names)]


def _detect_concurrent_hermes_instances(
    scripts_dir: Path, *, exclude_pid: int | None = None
) -> list[tuple[int, str]]:
    """Find other live processes whose .exe is one of our entry-point shims.

    Windows blocks DELETE/REPLACE on a running .exe — and even RENAME on the
    same .exe when another process opened it without ``FILE_SHARE_DELETE``.
    The OpenAgents Desktop Electron app spawns ``hermes.EXE`` as a backend child,
    so during ``hermes update`` the user-invoked process and the desktop's
    child both hold the same file. The quarantine rename then fails with
    ``[WinError 32]`` and uv inherits the lock.

    This helper enumerates processes whose ``exe`` matches one of the venv's
    shims (``hermes.exe`` / ``openagents-gateway.exe``) and returns ``(pid,
    process_name)`` pairs. The caller's own PID and its entire ancestor
    chain are excluded so the running ``hermes update`` invocation never
    reports itself — this matters on Windows where the setuptools .exe
    launcher (``hermes.exe``) is a separate process from the Python
    interpreter it loads (``python.exe``).

    Returns an empty list off-Windows, on missing psutil, or when no other
    instances exist. Never raises — process enumeration is best-effort.
    """
    if not _is_windows():
        return []

    try:
        import psutil
    except Exception:
        return []

    # Resolve every shim path to its canonical form once for cheap comparison.
    shim_paths: set[str] = set()
    for shim in _hermes_exe_shims(scripts_dir):
        try:
            shim_paths.add(str(shim.resolve()).lower())
        except OSError:
            shim_paths.add(str(shim).lower())
    if not shim_paths:
        return []

    # Build a set of PIDs to exclude: the Python process itself plus every
    # ancestor whose executable is one of our shims. On Windows the
    # setuptools-generated hermes.exe launcher is a separate native process
    # that spawns python.exe (the interpreter that runs our code).
    # os.getpid() returns the Python PID, but the launcher (which holds the
    # file lock) is the parent. Without excluding it, every ``hermes update``
    # reports its own launcher as a concurrent instance — a false positive
    # (issues #29341, #34795).
    #
    # Two robustness points learned from the field:
    #   1. Use ``proc.parents()`` — it returns the WHOLE ancestor list in one
    #      call. The earlier per-hop ``current.parent()`` loop bailed on the
    #      first psutil error (AccessDenied/NoSuchProcess is common on Windows
    #      across session/elevation boundaries), leaving the launcher shim in
    #      the candidate set and re-triggering the false positive.
    #   2. Only exclude ancestors whose exe is itself a shim. A genuine second
    #      hermes.exe sitting *under* a non-Hermes parent (e.g. a Hermes
    #      Desktop backend child) must still be flagged, so we don't blanket-
    #      exclude unrelated ancestors like the shell or terminal.
    # Broad ``except Exception`` guards against partially-stubbed psutil in
    # unit tests; this helper is documented as "never raises".
    if exclude_pid is not None:
        exclude_pids: set[int] = {int(exclude_pid)}
    else:
        exclude_pids = {os.getpid()}
    try:
        seed = next(iter(exclude_pids))
        try:
            ancestors = psutil.Process(seed).parents()
        except Exception:
            ancestors = []
        for ancestor in ancestors:
            try:
                anc_exe = ancestor.exe()
            except Exception:
                continue
            if not anc_exe:
                continue
            try:
                anc_norm = str(Path(anc_exe).resolve()).lower()
            except (OSError, ValueError):
                anc_norm = str(anc_exe).lower()
            if anc_norm in shim_paths:
                try:
                    exclude_pids.add(int(ancestor.pid))
                except Exception:
                    continue
    except Exception:
        pass

    matches: list[tuple[int, str]] = []
    try:
        proc_iter = psutil.process_iter(["pid", "exe", "name"])
    except Exception:
        return []

    for proc in proc_iter:
        try:
            info = proc.info
        except Exception:
            continue
        pid = info.get("pid")
        exe = info.get("exe")
        if not exe or pid is None or pid in exclude_pids:
            continue
        try:
            exe_norm = str(Path(exe).resolve()).lower()
        except (OSError, ValueError):
            exe_norm = str(exe).lower()
        if exe_norm in shim_paths:
            name = info.get("name") or Path(exe).name
            matches.append((int(pid), str(name)))

    return matches


def _format_concurrent_instances_message(
    matches: list[tuple[int, str]], scripts_dir: Path
) -> str:
    """Build a human-readable explanation + remediation hint for the user."""
    shim = scripts_dir / "hermes.exe"
    lines = ["✗ Another hermes.exe is running:"]
    for pid, name in matches:
        lines.append(f"    PID {pid}  {name}")
    lines.append("")
    lines.append(f"  Updating now would fail to overwrite {shim} because")
    lines.append("  Windows blocks REPLACE on a running executable.")
    lines.append("")
    lines.append("  Close OpenAgents Desktop, exit any open `hermes` REPLs, and")
    lines.append("  stop the gateway (`hermes gateway stop`) before retrying.")
    lines.append("")
    if matches:
        pid_args = " ".join(f"/PID {pid}" for pid, _ in matches)
        lines.append("  If you've already closed everything and these PIDs are")
        lines.append("  stale, terminate them directly, then retry the update:")
        lines.append(f"      taskkill {pid_args} /F")
        lines.append("")
    lines.append("  Override with `hermes update --force` if you've already")
    lines.append("  confirmed those processes will not write to the venv.")
    return "\n".join(lines)


def _quarantine_running_hermes_exe(
    scripts_dir: Path, *, max_attempts: int = 4
) -> list[tuple[Path, Path]]:
    """Pre-empt Windows file lock on the running ``hermes.exe``.

    Windows allows RENAMING a mapped/running executable (the kernel tracks the
    file by handle, not path), but blocks DELETE/REPLACE while it's loaded. uv
    needs to overwrite the entry-point shims during ``pip install -e .``;
    when ``hermes update`` runs, ``hermes.exe`` IS the live process, and uv
    fails with ``Access is denied. (os error 5)``.

    We rename live shims to ``hermes.exe.old.<unix-ms>`` first. uv then writes
    fresh shims at the original paths. The ``.old`` files are cleaned up on
    the next hermes invocation by ``_cleanup_quarantined_exes``.

    Rename can still fail when *another* process has opened the .exe without
    ``FILE_SHARE_DELETE`` — typically AV real-time scanners with transient
    handles (recovers in <1s), or the OpenAgents Desktop backend child process
    (won't recover until the user closes it). We mitigate:

    1. Retry up to ``max_attempts`` times with exponential backoff
       (100/250/500/1000 ms). Handles the AV-scanner case.
    2. If all retries fail, schedule the .exe for replacement on next
       reboot via ``MoveFileExW(MOVEFILE_DELAY_UNTIL_REBOOT)``. This still
       lets uv create a fresh shim at the original path (Windows will keep
       the old file's content under a new name until the reboot), so the
       update can complete; the user just needs to reboot to fully unload
       the stale image.
    3. Print a clear warning naming the most likely culprit (running
       OpenAgents Desktop / gateway / REPL) and pointing to ``--force``.

    Returns the list of (original, quarantined) pairs so the caller can roll
    back if the install itself fails before uv writes a replacement. Pairs
    where we used ``MOVEFILE_DELAY_UNTIL_REBOOT`` are NOT returned — they
    are already deferred and roll-back is meaningless.
    """
    moved: list[tuple[Path, Path]] = []
    if not _is_windows():
        return moved

    import time

    stamp = int(time.time() * 1000)
    # Backoff schedule: first attempt is immediate, subsequent ones sleep.
    # 100ms / 250ms / 500ms covers the typical AV scanner re-scan window.
    backoff_ms = [0, 100, 250, 500, 1000]
    attempts = max(1, min(max_attempts, len(backoff_ms)))

    for shim in _hermes_exe_shims(scripts_dir):
        if not shim.exists():
            continue
        target = shim.with_suffix(shim.suffix + f".old.{stamp}")

        last_exc: OSError | None = None
        for attempt in range(attempts):
            delay = backoff_ms[attempt] / 1000.0
            if delay:
                time.sleep(delay)
            try:
                shim.rename(target)
                moved.append((shim, target))
                last_exc = None
                break
            except OSError as e:
                last_exc = e
                continue

        if last_exc is None:
            continue

        # All in-process renames failed. Try MoveFileEx with
        # MOVEFILE_DELAY_UNTIL_REBOOT as a last resort. This succeeds in the
        # exact case where the inline rename failed (another process holds
        # the handle without share-delete), at the cost of requiring a
        # reboot to fully reclaim the old .exe.
        scheduled = _schedule_replace_on_reboot(shim, target)
        if scheduled:
            print(
                f"  ⚠ {shim.name} is locked by another process; scheduled "
                f"replacement on next reboot."
            )
            print(
                "    The new shim was written at the same path, but a "
                "reboot is needed to fully unload the old one."
            )
            # Do NOT append to ``moved``: we don't want roll-back to undo a
            # reboot-deferred operation.
            continue

        # Truly couldn't budge the .exe. Print an actionable warning and let
        # uv try its luck — sometimes uv's own retry handling pulls through.
        print(
            f"  ⚠ Could not quarantine {shim.name} ({last_exc.__class__.__name__}: "
            f"another process is holding it open)."
        )
        print(
            "    Close OpenAgents Desktop, exit other `hermes` REPLs, stop the "
            "gateway, or pause AV scanning, then re-run `hermes update`."
        )

    return moved


def _schedule_replace_on_reboot(shim: Path, quarantine_target: Path) -> bool:
    """Schedule ``shim`` -> ``quarantine_target`` via PendingFileRenameOperations.

    Uses Win32 ``MoveFileExW`` with ``MOVEFILE_REPLACE_EXISTING |
    MOVEFILE_DELAY_UNTIL_REBOOT``. The OS persists the rename in
    ``HKLM\\System\\CurrentControlSet\\Control\\Session Manager\\
    PendingFileRenameOperations`` and applies it before any user-mode code
    runs on next boot — at which point no process can hold the .exe.

    Returns ``True`` if the schedule call succeeded, ``False`` otherwise
    (non-Windows, ctypes failure, lack of privilege, etc.). Never raises.
    """
    if not _is_windows():
        return False
    try:
        import ctypes
        from ctypes import wintypes

        MOVEFILE_REPLACE_EXISTING = 0x1
        MOVEFILE_DELAY_UNTIL_REBOOT = 0x4

        MoveFileExW = ctypes.windll.kernel32.MoveFileExW
        MoveFileExW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]
        MoveFileExW.restype = wintypes.BOOL

        ok = MoveFileExW(
            str(shim),
            str(quarantine_target),
            MOVEFILE_REPLACE_EXISTING | MOVEFILE_DELAY_UNTIL_REBOOT,
        )
        return bool(ok)
    except Exception:
        return False


def _restore_quarantined_exes(moved: list[tuple[Path, Path]]) -> None:
    """Roll back ``_quarantine_running_hermes_exe`` if uv didn't write replacements."""
    for original, quarantined in moved:
        try:
            if not original.exists() and quarantined.exists():
                quarantined.rename(original)
        except OSError:
            pass


def _run_quarantined_install(
    cmd: list[str],
    *,
    env: dict[str, str] | None = None,
    scripts_dir: Path | None = None,
) -> None:
    """Run an editable install, quarantining the running ``hermes.exe`` first.

    Any ``pip install -e .`` (or ``--reinstall``) rewrites the entry-point
    shims, and on Windows the live ``hermes.exe`` is the running process —
    pip can neither delete nor overwrite it, so without quarantine the shim
    is left missing and ``hermes`` drops off PATH. This wraps
    :func:`_run_install_with_heartbeat` with the same rename-out-of-the-way /
    restore-on-failure dance that the primary install path uses, so EVERY
    install that touches the shims is protected — including the
    verification-repair reinstalls in
    :func:`_verify_core_dependencies_installed`, which previously called
    ``_run_install_with_heartbeat`` directly and bypassed quarantine.

    Off-Windows (``scripts_dir is None``) this is a thin pass-through.
    """
    moved: list[tuple[Path, Path]] = []
    if scripts_dir is not None:
        moved = _quarantine_running_hermes_exe(scripts_dir)
    try:
        _run_install_with_heartbeat(cmd, env=env)
    except BaseException:
        # Restore shims if pip/uv didn't write replacements (e.g. install
        # failed before the entry-points step). Don't swallow the error.
        if scripts_dir is not None:
            _restore_quarantined_exes(moved)
        raise


def _cleanup_quarantined_exes(scripts_dir: Path | None = None) -> None:
    """Sweep ``hermes.exe.old.*`` left by prior updates.

    Called early on every hermes invocation. The .old files are unlocked once
    their owning process exited, so deletion succeeds the next run. Silent
    no-op when nothing's there or on file-locked / permission errors.
    """
    if not _is_windows():
        return
    if scripts_dir is None:
        scripts_dir = _venv_scripts_dir()
    if scripts_dir is None:
        return
    try:
        for stale in scripts_dir.glob("*.exe.old.*"):
            try:
                stale.unlink()
            except OSError:
                pass  # still locked or in use — try again next run
    except OSError:
        pass


def _refresh_active_lazy_features() -> None:
    """Refresh lazy-installed backends after a code update.

    When pyproject.toml's ``[all]`` extra was slimmed down (May 2026), most
    optional backends moved to ``tools/lazy_deps.py`` and only install on
    first use. ``hermes update`` runs ``uv pip install -e .[all]`` which
    leaves those packages untouched — so if we bump a pin in
    :data:`LAZY_DEPS` (CVE response, transitive bug fix), users who already
    activated the backend keep the stale version forever.

    This function asks lazy_deps which features the user has previously
    activated and reinstalls them under the current pins. Features the
    user never enabled stay quiet — no churn for cold backends.

    Never raises. A failure here must not block the rest of the update.
    """
    try:
        from tools import lazy_deps
    except Exception as exc:
        logger.debug("Lazy refresh skipped (import failed): %s", exc)
        return

    try:
        active = lazy_deps.active_features()
    except Exception as exc:
        logger.debug("Lazy refresh skipped (active_features failed): %s", exc)
        return

    if not active:
        return

    print()
    print(f"→ Refreshing {len(active)} active lazy backend(s)...")

    try:
        results = lazy_deps.refresh_active_features(prompt=False)
    except Exception as exc:
        # refresh_active_features is documented as never-raise, but defend
        # the update flow against future regressions.
        print(f"  ⚠ Lazy refresh failed unexpectedly: {exc}")
        return

    refreshed = [f for f, s in results.items() if s == "refreshed"]
    current = [f for f, s in results.items() if s == "current"]
    failed = [(f, s) for f, s in results.items() if s.startswith("failed:")]
    skipped = [(f, s) for f, s in results.items() if s.startswith("skipped:")]

    if refreshed:
        print(f"  ↑ {len(refreshed)} refreshed: {', '.join(refreshed)}")
    if current:
        print(f"  ✓ {len(current)} already current")
    if skipped:
        # Most common reason: security.allow_lazy_installs=false. Show one
        # line so the user knows why; not an error.
        names = ", ".join(f for f, _ in skipped)
        reason = skipped[0][1].split(": ", 1)[-1]
        print(f"  · {len(skipped)} skipped ({reason}): {names}")
    if failed:
        for feature, status in failed:
            reason = status.split(": ", 1)[-1]
            # Clip noisy pip stderr to keep update output legible.
            if len(reason) > 200:
                reason = reason[:200] + "..."
            print(f"  ⚠ {feature} failed to refresh: {reason}")
        print("  Backends keep their previously-installed version; rerun")
        print("  `hermes update` once the upstream issue is resolved.")


def _install_python_dependencies_with_optional_fallback(
    install_cmd_prefix: list[str],
    *,
    env: dict[str, str] | None = None,
    group: str = "all",
) -> None:
    """Install base deps plus as many optional extras as the environment supports.

    By default this targets ``.[all]``; Termux callers can pass
    ``group='termux-all'`` to use the curated Android-compatible profile.

    On Windows, pre-renames live ``hermes.exe`` / ``openagents-gateway.exe`` shims
    in the venv Scripts dir before each install attempt so uv can write fresh
    copies (Windows blocks REPLACE on a running .exe but allows RENAME). See
    ``_quarantine_running_hermes_exe`` for the rationale.
    """
    scripts_dir = _venv_scripts_dir() if _is_windows() else None

    def _install(args: list[str]) -> None:
        _run_quarantined_install(
            install_cmd_prefix + args, env=env, scripts_dir=scripts_dir
        )

    try:
        _install(["install", "-e", f".[{group}]"])
        _verify_console_scripts_installed(install_cmd_prefix, env=env)
        return
    except subprocess.CalledProcessError:
        print(
            "  ⚠ Optional extras failed, reinstalling base dependencies and retrying extras individually..."
        )

    _install(["install", "-e", "."])

    failed_extras: list[str] = []
    installed_extras: list[str] = []
    for extra in _load_installable_optional_extras(group=group):
        try:
            _install(["install", "-e", f".[{extra}]"])
            installed_extras.append(extra)
        except subprocess.CalledProcessError:
            failed_extras.append(extra)

    if installed_extras:
        print(
            f"  ✓ Reinstalled optional extras individually: {', '.join(installed_extras)}"
        )
    if failed_extras:
        print(
            f"  ⚠ Skipped optional extras that still failed: {', '.join(failed_extras)}"
        )

    # Belt-and-suspenders: verify every declared core dependency from
    # pyproject.toml's [project.dependencies] is actually importable in the
    # target venv. uv's incremental resolver has — in the wild — produced
    # partial installs where a newly added base dep (e.g. ``pathspec``)
    # silently fails to land on top of a half-stale venv, and the only
    # symptom is a downstream subprocess crashing with ModuleNotFoundError
    # hours later inside ``hermes update``'s desktop-rebuild or skill-sync
    # stage. Reinstall with --reinstall to force resolution if anything is
    # missing, then re-verify so the failure surfaces here instead of
    # downstream.
    _verify_core_dependencies_installed(install_cmd_prefix, env=env, group=group)
    _verify_console_scripts_installed(install_cmd_prefix, env=env)


def _load_console_script_names() -> list[str]:
    """Return ``[project.scripts]`` entry-point names from pyproject.toml."""
    try:
        import tomllib  # Python 3.11+
    except ImportError:  # pragma: no cover
        return []

    pyproject = PROJECT_ROOT / "pyproject.toml"
    if not pyproject.is_file():
        return []

    try:
        with open(pyproject, "rb") as f:
            data = tomllib.load(f)
        scripts = data.get("project", {}).get("scripts", {}) or {}
        return [str(name) for name in scripts if name]
    except Exception as e:
        logger.debug("console script verification: failed to read pyproject.toml: %s", e)
        return []


def _verify_console_scripts_installed(
    install_cmd_prefix: list[str],
    *,
    env: dict[str, str] | None = None,
) -> None:
    """Ensure every declared console_script shim exists on disk after install.

    On Windows, ``uv pip install -e .`` can register ``hermes.exe`` in the
    wheel RECORD while the file never lands on disk — typically when the live
    ``hermes.exe`` shim is locked during ``hermes update``, or when uv/distlib
    skips a launcher write. The symptom is ``openagents.exe`` and
    ``openagents-acp.exe`` present but ``hermes.exe`` missing, so ``hermes`` drops
    off PATH even though the install reported success (issue #52931).

    If any shim is missing we reinstall with ``--reinstall -e .`` under the
    same quarantine dance as the primary install path, then re-check.
    """
    if not _is_windows():
        return

    scripts_dir = _venv_scripts_dir()
    if scripts_dir is None:
        return

    names = _load_console_script_names()
    if not names:
        return

    def _missing() -> list[str]:
        return [
            name
            for name in names
            if not (scripts_dir / f"{name}.exe").is_file()
        ]

    missing = _missing()
    if not missing:
        return

    print(
        f"  ⚠ Verification: {len(missing)} console script(s) missing on disk: "
        f"{', '.join(missing)}"
    )
    print("  → Reinstalling entry points with --reinstall...")

    try:
        _run_quarantined_install(
            install_cmd_prefix + ["install", "--reinstall", "-e", "."],
            env=env,
            scripts_dir=scripts_dir,
        )
    except subprocess.CalledProcessError as e:
        logger.warning("console script verification: repair install failed: %s", e)
        print(
            "  ⚠ Entry point repair failed; try `hermes update --force` after "
            "closing other hermes processes."
        )
        return

    still_missing = _missing()
    if still_missing:
        print(
            f"  ⚠ Still missing after repair: {', '.join(still_missing)}. "
            "Workaround: python -m openagents_cli.main <command>"
        )
    else:
        print("  ✓ All console entry points restored")


def _verify_core_dependencies_installed(
    install_cmd_prefix: list[str],
    *,
    env: dict[str, str] | None = None,
    group: str = "all",
) -> None:
    """Check that every base dep from pyproject.toml is importable; if not, retry.

    Reads ``pyproject.toml`` directly (so we don't trust the venv's stale
    metadata), filters out deps gated by ``;`` environment markers that don't
    apply to this platform, and runs ``importlib.metadata.version()`` in the
    venv interpreter for each one. If anything is missing we reinstall the
    base group with ``--reinstall`` to force uv to re-resolve, then check
    again. We treat the final state as a warning rather than a hard failure
    so a single broken-on-PyPI dep can't block an otherwise-successful
    update — but the warning makes the partial install visible at the spot
    that caused it, instead of hours later in a downstream subprocess.
    """
    try:
        import tomllib  # Python 3.11+
    except ImportError:  # pragma: no cover — Python < 3.11 unsupported but be safe
        return

    pyproject = PROJECT_ROOT / "pyproject.toml"
    if not pyproject.is_file():
        return

    try:
        with open(pyproject, "rb") as f:
            data = tomllib.load(f)
        raw_deps = data.get("project", {}).get("dependencies", []) or []
    except Exception as e:
        logger.debug("dep verification: failed to read pyproject.toml: %s", e)
        return

    # Parse each "name OP version ; marker" string into (dist_name, marker_obj).
    # We use packaging.requirements when available (it ships with pip/uv envs),
    # falling back to a naive split that's good enough for the canonical
    # ``name==version[; marker]`` style this repo uses.
    deps: list[tuple[str, "object | None"]] = []
    try:
        from packaging.requirements import Requirement  # type: ignore

        for spec in raw_deps:
            try:
                req = Requirement(spec)
                deps.append((req.name, req.marker))
            except Exception:
                continue
    except Exception:
        for spec in raw_deps:
            head = spec.split(";", 1)[0]
            for op in ("==", ">=", "<=", "~=", ">", "<", "!="):
                if op in head:
                    head = head.split(op, 1)[0]
                    break
            name = head.strip().split("[", 1)[0].strip()
            if name:
                deps.append((name, None))

    # Apply environment markers to drop deps that don't apply on this platform
    # (e.g. ``ptyprocess ; sys_platform != 'win32'`` is correctly skipped on
    # Windows). Without markers we'd false-positive every cross-platform exclusion.
    applicable: list[str] = []
    for name, marker in deps:
        if marker is None:
            applicable.append(name)
            continue
        try:
            if marker.evaluate():  # type: ignore[union-attr]
                applicable.append(name)
        except Exception:
            applicable.append(name)

    if not applicable:
        return

    # Run the check inside the venv Python — sys.executable here may be the
    # outer Python that drove ``hermes update``, not the venv we just wrote
    # to. The uv install_cmd_prefix encodes which environment we targeted
    # (either ``[uv, pip]`` with VIRTUAL_ENV in env, or
    # ``[sys.executable, -m, pip]`` for the in-process Python); resolve the
    # right interpreter for the verification.
    venv_python = _resolve_install_target_python(install_cmd_prefix, env)
    if venv_python is None:
        return

    def _missing_deps() -> list[str]:
        check_script = (
            "import importlib.metadata as md, sys\n"
            "missing=[]\n"
            "for name in sys.argv[1:]:\n"
            "    try: md.version(name)\n"
            "    except md.PackageNotFoundError: missing.append(name)\n"
            "print('\\n'.join(missing))\n"
        )
        try:
            result = subprocess.run(
                [str(venv_python), "-c", check_script, *applicable],
                capture_output=True,
                text=True,
                check=False,
                env=env,
            )
        except Exception as e:
            logger.debug("dep verification: subprocess failed: %s", e)
            return []
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]

    missing = _missing_deps()
    if not missing:
        return

    print(
        f"  ⚠ Verification: {len(missing)} declared dep(s) missing after install: "
        f"{', '.join(missing[:8])}{'...' if len(missing) > 8 else ''}"
    )
    print("  → Reinstalling base group with --reinstall to repair...")

    # Reinstall base group with --reinstall so uv re-resolves from scratch
    # against the current pyproject. We don't pass ``[{group}]`` here on
    # purpose — the missing dep is in *base* deps; rerunning the full all-
    # extras install can cost minutes and trips on whatever optional extra
    # was already broken upstream. Base is fast and is what's actually wrong.
    #
    # Quarantine the running ``hermes.exe`` first: ``--reinstall -e .``
    # rewrites the entry-point shims, and on Windows pip can't overwrite the
    # live launcher, which would leave ``hermes`` off PATH.
    scripts_dir = _venv_scripts_dir() if _is_windows() else None
    repair_args = ["install", "--reinstall", "-e", "."]
    try:
        _run_quarantined_install(
            install_cmd_prefix + repair_args, env=env, scripts_dir=scripts_dir
        )
    except subprocess.CalledProcessError as e:
        logger.warning("dep verification: repair install failed: %s", e)
        print("  ⚠ Repair install failed; check `hermes update` output above.")
        return

    still_missing = _missing_deps()
    if not still_missing:
        print("  ✓ All declared core dependencies now installed")
        return

    # Last-ditch: install each remaining missing dep with its pin directly.
    # Useful when uv's resolver thinks the env is satisfied but the on-disk
    # package metadata says otherwise (rare but observed).
    name_to_spec = {}
    for spec in raw_deps:
        head = spec.split(";", 1)[0].strip()
        bare = head
        for op in ("==", ">=", "<=", "~=", ">", "<", "!="):
            if op in bare:
                bare = bare.split(op, 1)[0]
                break
        name_to_spec[bare.strip().split("[", 1)[0].strip()] = head

    specs = [name_to_spec.get(n, n) for n in still_missing]
    print(
        f"  → Force-installing remaining missing dep(s): {', '.join(specs)}"
    )
    try:
        _run_install_with_heartbeat(
            install_cmd_prefix + ["install", "--reinstall", *specs], env=env
        )
    except subprocess.CalledProcessError as e:
        logger.warning("dep verification: per-package repair failed: %s", e)
        print(
            f"  ⚠ Could not install: {', '.join(still_missing)}. "
            "Run `hermes update --force` after closing other hermes processes."
        )
        return

    final_missing = _missing_deps()
    if final_missing:
        print(
            f"  ⚠ Still missing after repair: {', '.join(final_missing)}. "
            "Run `hermes update --force` after closing other hermes processes."
        )
    else:
        print("  ✓ All declared core dependencies now installed")


def _resolve_install_target_python(
    install_cmd_prefix: list[str], env: dict[str, str] | None
) -> Path | None:
    """Figure out which Python interpreter the install just targeted.

    ``_install_python_dependencies_with_optional_fallback`` is called with
    either ``[uv, pip]`` (and a ``VIRTUAL_ENV`` env var pointing at the
    target venv) or ``[sys.executable, -m, pip]`` (the in-process Python).
    The verification step needs the *resulting* environment's Python so
    ``importlib.metadata`` queries the right site-packages.
    """
    if env and "VIRTUAL_ENV" in env:
        venv_root = Path(env["VIRTUAL_ENV"])
        scripts = venv_root / ("Scripts" if _is_windows() else "bin")
        candidate = scripts / ("python.exe" if _is_windows() else "python")
        if candidate.exists():
            return candidate

    # Fallback: assume install_cmd_prefix[0] is the python interpreter (the
    # ``[sys.executable, -m, pip]`` shape). Skip if it looks like ``uv``.
    if install_cmd_prefix:
        first = Path(install_cmd_prefix[0])
        if first.exists() and "uv" not in first.name.lower():
            return first

    return None


def _is_termux_env(env: dict[str, str] | None = None) -> bool:
    return _is_termux_startup_environment(env)


def _is_android_python() -> bool:
    return sys.platform == "android"


def _install_psutil_android_compat(
    install_cmd_prefix: list[str],
    *,
    env: dict[str, str] | None = None,
) -> None:
    """Install psutil on Android by patching upstream platform detection.

    psutil's setup currently gates Linux sources behind
    ``sys.platform.startswith('linux')``. On Termux Python reports
    ``sys.platform == 'android'``, so setup aborts with
    "platform android is not supported" despite compiling fine when using the
    Linux source path.

    We patch only the extracted build tree used for this install attempt;
    nothing is persisted in the repository.

    Stopgap: remove this once https://github.com/giampaolo/psutil/pull/2762
    merges and ships in a release. The standalone installer script uses the
    same shared helper and should be removed together.
    """
    import tempfile
    import urllib.request
    from openagents_cli.psutil_android import PSUTIL_URL, prepare_patched_psutil_sdist

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        archive = tmp_path / "psutil.tar.gz"
        urllib.request.urlretrieve(PSUTIL_URL, archive)
        src_root = prepare_patched_psutil_sdist(archive, tmp_path)

        _run_install_with_heartbeat(
            install_cmd_prefix + ["install", "--no-build-isolation", str(src_root)],
            env=env,
        )


def _ensure_uv_for_termux(pip_cmd: list[str]) -> str | None:
    """Best-effort uv bootstrap on Termux for faster update installs.

    The normal path (``ensure_uv()`` in managed_uv) installs the managed
    standalone uv into ``$OPENAGENTS_HOME/bin/uv``, but on Termux the official
    installer may not work (glibc vs bionic).  Prefer a uv already on PATH
    (e.g. ``pkg install uv``); only if there is none do we fall back to a
    wheel-only ``pip install uv`` so we never source-build the Rust crate.
    """
    from openagents_cli.managed_uv import resolve_uv

    existing = resolve_uv()
    if existing:
        return existing
    if not _is_termux_env():
        return None
    # A Termux-packaged uv lands on PATH but not in the managed bin dir, so
    # resolve_uv() misses it. Use it before pip, which has no Android wheel and
    # would otherwise build uv from source on a low-memory device.
    system_uv = shutil.which("uv")
    if system_uv:
        return system_uv
    try:
        print("  → Termux detected: trying to install uv for faster dependency updates...")
        result = subprocess.run(
            pip_cmd + ["install", "uv", "--only-binary", ":all:"],
            cwd=PROJECT_ROOT,
            check=False,
        )
        if result.returncode != 0:
            return None
    except Exception:
        pass
    # After pip install, check managed path first, then PATH
    return resolve_uv() or shutil.which("uv")


def _update_node_dependencies() -> None:
    from openagents_constants import find_node_executable, with_hermes_node_path

    npm = find_node_executable("npm")
    if not npm:
        return

    if not (PROJECT_ROOT / "package.json").exists():
        return

    # With a single workspace lockfile the root install would cover ALL
    # workspaces — but apps/desktop pulls in Electron as a devDependency,
    # and its postinstall downloads a ~200MB binary.  Most users don't
    # need desktop during `hermes update`, so we install root-only first
    # then add just the workspaces the CLI/TUI/web build actually requires.
    # Desktop deps are installed on demand by the desktop launcher
    # (see _desktop_build_needed).
    print("→ Updating Node.js dependencies...")
    extra_args = ["--no-fund", "--no-audit", "--progress=false"]

    nixos_env = with_hermes_node_path(_nixos_build_env())

    # Step 1: root install (no workspace recursion).
    # NOTE: capture_output=False here is deliberate (#18840) — optional
    # postinstall scripts (e.g. @askjo/camofox-browser's browser-binary fetch)
    # print download progress, and capturing it makes a long download look
    # hung. The chatty npm-deprecation noise during `hermes update` comes from
    # the *desktop* build, not this step; that one is captured to update.log.
    root_args = [*extra_args, "--workspaces=false"]
    root_result = _run_npm_install_deterministic(
        npm,
        PROJECT_ROOT,
        extra_args=tuple(root_args),
        capture_output=False,
        env=nixos_env,
    )
    if root_result.returncode != 0:
        print("  ⚠ npm install failed in repo root")
        stderr = (root_result.stderr or "").strip() if root_result.stderr else ""
        if stderr:
            print(f"    {stderr.splitlines()[-1]}")
        return

    # Step 2: install only the workspaces update needs (ui-tui, web).
    # --workspace selects specific workspaces; the rest (desktop) are skipped.
    ws_args = [*extra_args, "--workspace", "ui-tui", "--workspace", "web"]
    ws_result = _run_npm_install_deterministic(
        npm,
        PROJECT_ROOT,
        extra_args=tuple(ws_args),
        capture_output=False,
        env=nixos_env,
    )
    if ws_result.returncode == 0:
        print("  ✓ repo root + ui-tui, web workspaces (desktop skipped)")
    else:
        print("  ⚠ npm workspace install failed")
        stderr = (ws_result.stderr or "").strip() if ws_result.stderr else ""
        if stderr:
            print(f"    {stderr.splitlines()[-1]}")


class _UpdateOutputStream:
    """Stream wrapper used during ``hermes update`` to survive terminal loss.

    Wraps the process's original stdout/stderr so that:

    * Every write is also mirrored to an append-only log file
      (``~/.openagents/logs/update.log``) that users can inspect after the
      terminal disconnects.
    * Writes to the original stream that fail with ``BrokenPipeError`` /
      ``OSError`` / ``ValueError`` (closed file) no longer cascade into
      process exit — the update keeps going, only the on-screen output
      stops.

    Combined with ``SIGHUP -> SIG_IGN`` installed by
    ``_install_hangup_protection``, this makes ``hermes update`` safe to
    run in a plain SSH session that might disconnect mid-install.
    """

    def __init__(self, original, log_file):
        self._original = original
        self._log = log_file
        self._original_broken = False

    def write(self, data):
        # Mirror to the log file first — it's the most reliable destination.
        if self._log is not None:
            try:
                self._log.write(data)
            except Exception:
                # Log errors should never abort the update.
                pass

        if self._original_broken:
            return len(data) if isinstance(data, (str, bytes)) else 0

        try:
            return self._original.write(data)
        except (BrokenPipeError, OSError, ValueError):
            # Terminal vanished (SSH disconnect, shell close).  Stop trying
            # to write to it, but keep the update running.
            self._original_broken = True
            return len(data) if isinstance(data, (str, bytes)) else 0

    def flush(self):
        if self._log is not None:
            try:
                self._log.flush()
            except Exception:
                pass
        if self._original_broken:
            return
        try:
            self._original.flush()
        except (BrokenPipeError, OSError, ValueError):
            self._original_broken = True

    def isatty(self):
        if self._original_broken:
            return False
        try:
            return self._original.isatty()
        except Exception:
            return False

    def fileno(self):
        # Some tools probe fileno(); defer to the underlying stream and let
        # callers handle failures (same behaviour as the unwrapped stream).
        return self._original.fileno()

    def __getattr__(self, name):
        return getattr(self._original, name)


def _install_hangup_protection(gateway_mode: bool = False):
    """Protect ``cmd_update`` from SIGHUP and broken terminal pipes.

    Users commonly run ``hermes update`` in an SSH session or a terminal
    that may close mid-install.  Without protection, ``SIGHUP`` from the
    terminal kills the Python process during ``pip install`` and leaves
    the venv half-installed; the documented workaround ("use screen /
    tmux") shouldn't be required for something as routine as an update.

    Protections installed:

    1. ``SIGHUP`` is set to ``SIG_IGN``.  POSIX preserves ``SIG_IGN``
       across ``exec()``, so pip and git subprocesses also stop dying on
       hangup.
    2. ``sys.stdout`` / ``sys.stderr`` are wrapped to mirror output to
       ``~/.openagents/logs/update.log`` and to silently absorb
       ``BrokenPipeError`` when the terminal vanishes.

    ``SIGINT`` (Ctrl-C) and ``SIGTERM`` (systemd shutdown) are
    **intentionally left alone** — those are legitimate cancellation
    signals the user or OS sent on purpose.

    In gateway mode (``hermes update --gateway``) the update is already
    spawned detached from a terminal, so this function is a no-op.

    Returns a dict that ``cmd_update`` can pass to
    ``_finalize_update_output`` on exit.  Returning a dict rather than a
    tuple keeps the call site forward-compatible with future additions.
    """
    state = {
        "prev_stdout": sys.stdout,
        "prev_stderr": sys.stderr,
        "log_file": None,
        "installed": False,
    }

    if gateway_mode:
        return state

    import signal as _signal

    # (1) Ignore SIGHUP for the remainder of this process.
    if hasattr(_signal, "SIGHUP"):
        try:
            _signal.signal(_signal.SIGHUP, _signal.SIG_IGN)
        except (ValueError, OSError):
            # Called from a non-main thread — not fatal.  The update still
            # runs, just without hangup protection.
            pass

    # (2) Mirror output to update.log and wrap stdio for broken-pipe
    # tolerance.  Any failure here is non-fatal; we just skip the wrap.
    try:
        # Late-bound import so tests can monkeypatch
        # openagents_cli.config.get_openagents_home to simulate setup failure.
        from openagents_cli.config import get_openagents_home as _get_openagents_home

        logs_dir = _get_openagents_home() / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        log_path = logs_dir / "update.log"
        log_file = open(log_path, "a", buffering=1, encoding="utf-8")

        import datetime as _dt

        log_file.write(
            f"\n=== hermes update started "
            f"{_dt.datetime.now().isoformat(timespec='seconds')} ===\n"
        )

        state["log_file"] = log_file
        sys.stdout = _UpdateOutputStream(state["prev_stdout"], log_file)
        sys.stderr = _UpdateOutputStream(state["prev_stderr"], log_file)
        state["installed"] = True
    except Exception:
        # Leave stdio untouched on any setup failure.  Update continues
        # without mirroring.
        state["log_file"] = None

    return state


def _log_only_write(text: str) -> None:
    """Write ``text`` to ``~/.openagents/logs/update.log`` only, never the terminal.

    During ``hermes update`` ``sys.stdout`` is an ``_UpdateOutputStream`` that
    mirrors to both the terminal and ``update.log``. Loud, low-signal
    subprocess output (npm installs, the Electron/vite build, the cua-driver
    installer's "Next steps" wall) should be captured and tucked into the log
    so failures stay debuggable, without flooding the user's terminal. This
    reaches past the mirroring stream straight to the underlying log handle.
    """
    if not text:
        return
    stream = sys.stdout
    log_file = getattr(stream, "_log", None)
    if log_file is None:
        return
    try:
        log_file.write(text if text.endswith("\n") else text + "\n")
        log_file.flush()
    except Exception:
        pass


def _run_logged_subprocess(cmd, *, cwd=None, env=None):
    """Run ``cmd`` capturing combined output into update.log (not the terminal).

    Returns the ``CompletedProcess`` (with ``stdout`` populated) so the caller
    can decide whether to surface the captured output on failure.
    """
    result = subprocess.run(
        cmd,
        cwd=cwd,
        env=env,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    _log_only_write(result.stdout or "")
    return result


def _finalize_update_output(state):
    """Restore stdio and close the update.log handle opened by ``_install_hangup_protection``."""
    if not state:
        return
    if state.get("installed"):
        try:
            sys.stdout = state.get("prev_stdout", sys.stdout)
        except Exception:
            pass
        try:
            sys.stderr = state.get("prev_stderr", sys.stderr)
        except Exception:
            pass
    log_file = state.get("log_file")
    if log_file is not None:
        try:
            log_file.flush()
            log_file.close()
        except Exception:
            pass


def _resolve_update_branch(args) -> str:
    """Normalize ``args.branch`` into a non-empty branch name.

    Centralizes the "default to main, accept --branch override, treat empty
    or whitespace-only values as the default" parsing so every consumer of
    ``--branch`` (check path, git-update path, ZIP-fallback path) agrees on
    the same answer.
    """
    return (getattr(args, "branch", None) or "main").strip() or "main"


def _cmd_update_check(branch: str = "main", *, branch_explicit: bool = False):
    """Implement ``hermes update --check``: fetch and report without installing.

    ``branch`` selects which branch the check compares against. Default is
    "main"; callers can pass another branch to ask "are there new commits
    on origin/<branch>?" without performing the update.

    ``branch_explicit`` is True iff the caller passed --branch on the CLI.
    PyPI installs can't honor non-default branches, so when this is True
    on a PyPI install we surface a one-line notice instead of silently
    dropping the flag.
    """
    from openagents_cli.config import detect_install_method
    method = detect_install_method(PROJECT_ROOT)
    if method == "docker":
        # Docker can't ``git fetch`` from within the container.  Surface the
        # same long-form ``docker pull`` guidance ``hermes update`` (apply
        # path) uses — telling the user to "reinstall via curl" or that
        # ".git is missing" would point them at the wrong remediation.
        from openagents_cli.config import format_docker_update_message
        print(format_docker_update_message())
        sys.exit(1)
    if method == "pip":
        from openagents_cli.config import recommended_update_command
        from openagents_cli.banner import check_via_pypi
        if branch_explicit and branch != "main":
            print(f"⚠ --branch is ignored for PyPI installs (would have checked '{branch}').")
        result = check_via_pypi()
        if result is None:
            print("✗ Could not reach PyPI to check for updates.")
            sys.exit(1)
        elif result == 0:
            print("✓ Already up to date.")
        else:
            print("⚕ Update available on PyPI.")
            print(f"  Run '{recommended_update_command()}' to install.")
        return

    git_dir = PROJECT_ROOT / ".git"
    if not git_dir.exists():
        print("✗ Not a git repository — cannot check for updates.")
        sys.exit(1)

    git_cmd = ["git"]
    if sys.platform == "win32":
        git_cmd = ["git", "-c", "windows.appendAtomically=false"]

    # Fetch only the branch we compare against; prefer upstream as the canonical
    # reference. A bare `git fetch <remote>` pulls every ref, and this repo has
    # thousands of auto-generated branches, so scope the fetch to <branch>.
    # Note: upstream/<branch> may not exist for non-main branches (a fork's
    # bb/gui has no upstream counterpart), so when the caller picks a
    # non-default branch we skip the upstream probe and use origin directly.
    # Installer checkouts are shallow (`git clone --depth 1`). A plain
    # `git fetch` would unshallow the repo (dragging in the whole history —
    # the exact cost the shallow clone avoided) and the rev-list count below
    # would then report a huge bogus "behind" number. Detect shallow up front:
    # fetch with --depth 1 to preserve the boundary and report presence-only.
    is_shallow = (
        subprocess.run(
            git_cmd + ["rev-parse", "--is-shallow-repository"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
        ).stdout.strip()
        == "true"
    )
    depth_args = ["--depth", "1"] if is_shallow else []

    if branch == "main":
        print("→ Fetching from upstream...")
        fetch_result = subprocess.run(
            git_cmd + ["fetch"] + depth_args + ["upstream", branch],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
        )
        if fetch_result.returncode != 0:
            # Fallback to origin if upstream doesn't exist
            print("→ Fetching from origin...")
            fetch_result = subprocess.run(
                git_cmd + ["fetch"] + depth_args + ["origin", branch],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
            )
            upstream_exists = False
            compare_branch = f"origin/{branch}"
        else:
            upstream_exists = True
            compare_branch = f"upstream/{branch}"
    else:
        # Non-default branch: compare against origin/<branch> directly.
        print("→ Fetching from origin...")
        fetch_result = subprocess.run(
            git_cmd + ["fetch"] + depth_args + ["origin", branch],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
        )
        upstream_exists = False
        compare_branch = f"origin/{branch}"

    if fetch_result.returncode != 0:
        stderr = fetch_result.stderr.strip()
        if "Could not resolve host" in stderr or "unable to access" in stderr:
            print("✗ Network error — cannot reach the remote repository.")
        elif "Authentication failed" in stderr or "could not read Username" in stderr:
            print("✗ Authentication failed — check your git credentials or SSH key.")
        else:
            print("✗ Failed to fetch.")
            if stderr:
                print(f"  {stderr.splitlines()[0]}")
        sys.exit(1)

    # Verify the compare ref actually exists before asking rev-list about it.
    # Without this, `git rev-list HEAD..origin/<bogus> --count` exits 128 and
    # (with check=True) raises CalledProcessError, surfacing a Python
    # traceback. Friendlier to detect-and-report.
    verify_result = subprocess.run(
        git_cmd + ["rev-parse", "--verify", "--quiet", compare_branch],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )
    if verify_result.returncode != 0:
        print(f"✗ Branch '{branch}' not found on {compare_branch.split('/', 1)[0]}.")
        sys.exit(1)

    if is_shallow:
        # No history to count across the shallow boundary. Compare tip SHAs and
        # report presence-only (mirrors the banner's _check_via_local_git).
        head_sha = subprocess.run(
            git_cmd + ["rev-parse", "HEAD"],
            cwd=PROJECT_ROOT, capture_output=True, text=True,
        ).stdout.strip()
        target_sha = subprocess.run(
            git_cmd + ["rev-parse", compare_branch],
            cwd=PROJECT_ROOT, capture_output=True, text=True,
        ).stdout.strip()
        if head_sha and target_sha and head_sha == target_sha:
            print("✓ Already up to date.")
        else:
            print(f"⚕ Update available (behind {compare_branch}).")
            from openagents_cli.config import recommended_update_command

            print(f"  Run '{recommended_update_command()}' to install.")
        return

    rev_result = subprocess.run(
        git_cmd + ["rev-list", f"HEAD..{compare_branch}", "--count"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    behind = int(rev_result.stdout.strip())

    if behind == 0:
        print("✓ Already up to date.")
    else:
        commits_word = "commit" if behind == 1 else "commits"
        print(f"⚕ Update available: {behind} {commits_word} behind {compare_branch}.")
        from openagents_cli.config import recommended_update_command

        print(f"  Run '{recommended_update_command()}' to install.")


def _ensure_fhs_path_guard() -> None:
    """Ensure /usr/local/bin is on PATH for RHEL-family root non-login shells.

    Mirrors the post-symlink probe added to ``scripts/install.sh`` so that
    existing FHS-layout root installs on RHEL/CentOS/Rocky/Alma 8+ get
    repaired on ``hermes update`` without requiring a reinstall.  The
    installer's assumption that ``/usr/local/bin`` is on PATH for every
    standard shell breaks on those distros in non-login interactive shells
    (su, sudo -s, tmux panes, some web terminals): /etc/bashrc doesn't
    add /usr/local/bin and /root/.bash_profile doesn't either.  Symptom:
    ``hermes`` prints ``command not found`` even though the symlink lives
    at /usr/local/bin/hermes.

    Silent no-op on: non-Linux, non-root, non-FHS installs, and any system
    where ``bash -i -c 'command -v hermes'`` already resolves.  Idempotent.
    """
    if sys.platform != "linux":
        return
    try:
        if os.geteuid() != 0:  # windows-footgun: ok — Linux FHS helper, guarded by sys.platform == "linux" above + AttributeError catch
            return
    except AttributeError:
        return
    # Only act when this is actually an FHS-layout install (command link at
    # /usr/local/bin/hermes, code at /usr/local/lib/openagents).
    fhs_link = Path("/usr/local/bin/hermes")
    if not fhs_link.is_symlink() and not fhs_link.exists():
        return

    # Probe a fresh non-login interactive bash the way the user will use it.
    # ``bash -i -c`` sources ~/.bashrc but NOT ~/.bash_profile or /etc/profile,
    # which is the exact scenario where RHEL root loses /usr/local/bin.
    home = os.environ.get("HOME") or "/root"
    try:
        probe = subprocess.run(
            [
                "env",
                "-i",
                f"HOME={home}",
                f"TERM={os.environ.get('TERM', 'dumb')}",
                "bash",
                "-i",
                "-c",
                "command -v hermes",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return  # no bash or probe hung — don't block update on this
    if probe.returncode == 0:
        return  # already on PATH, nothing to do

    path_line = 'export PATH="/usr/local/bin:$PATH"'
    path_comment = (
        "# OpenAgents — ensure /usr/local/bin is on PATH " "(RHEL non-login shells)"
    )
    wrote_any = False
    for candidate in (".bashrc", ".bash_profile"):
        cfg = Path(home) / candidate
        if not cfg.is_file():
            continue
        try:
            existing = cfg.read_text(errors="replace")
        except OSError:
            continue
        # Idempotency: skip if any uncommented PATH= line already references
        # /usr/local/bin.  Mirrors the grep pattern used by install.sh.
        already_guarded = any(
            "/usr/local/bin" in line
            and "PATH" in line
            and not line.lstrip().startswith("#")
            for line in existing.splitlines()
        )
        if already_guarded:
            continue
        try:
            with cfg.open("a", encoding="utf-8") as f:
                f.write("\n" + path_comment + "\n" + path_line + "\n")
        except OSError as e:
            print(f"  ⚠ Could not update {cfg}: {e}")
            continue
        print(f"  ✓ Added /usr/local/bin to PATH in {cfg}")
        wrote_any = True
    if wrote_any:
        print("    (reload your shell or run 'source ~/.bashrc' to pick it up)")


def _run_pre_update_backup(args) -> None:
    """Create a full zip backup of OPENAGENTS_HOME before running the update.

    Gated on ``updates.pre_update_backup`` in config (default false).  Off
    by default because the zip can add minutes to every update on large
    OPENAGENTS_HOME directories.  The ``--backup`` flag on ``hermes update``
    opts in for a single run; ``--no-backup`` forces it off when config
    has it enabled.  Never raises — a backup failure should not block the
    update itself.
    """
    # CLI flags win over config.  --no-backup beats --backup if both are set.
    if getattr(args, "no_backup", False):
        print("◆ Pre-update backup: skipped (--no-backup)")
        print()
        return

    force_backup = bool(getattr(args, "backup", False))

    try:
        from openagents_cli.config import load_config

        cfg = load_config()
    except Exception as exc:
        logging.getLogger(__name__).debug(
            "Could not load config for pre-update backup: %s", exc
        )
        cfg = {}

    updates_cfg = cfg.get("updates", {}) if isinstance(cfg, dict) else {}
    # The default config ships with ``pre_update_backup: false`` (see
    # ``openagents_cli/config.py``). Fall back to false if the key is missing
    # so the default behaviour matches the shipped config: zipping a large
    # OPENAGENTS_HOME can add minutes to every update. Users who want the
    # #48200 safety net opt in via the config knob or ``--backup``.
    enabled = updates_cfg.get("pre_update_backup", False)
    keep = updates_cfg.get("backup_keep", 5)

    if not enabled and not force_backup:
        # Silent by default — the backup is off, most users don't need to
        # hear about it on every update.  They can opt in via --backup
        # or by flipping the config knob.
        return

    try:
        from openagents_cli.backup import create_pre_update_backup
    except Exception as exc:
        print(
            f"⚠ Pre-update backup: could not load backup module ({exc}); continuing update."
        )
        print()
        return

    print("◆ Creating pre-update backup...")
    t0 = _time.monotonic()
    try:
        out_path = create_pre_update_backup(keep=int(keep))
    except Exception as exc:  # defensive — helper already swallows, but just in case
        print(f"  ⚠ Backup failed: {exc}")
        print("  Continuing with update.")
        print()
        return

    elapsed = _time.monotonic() - t0

    if out_path is None:
        print("  ⚠ Backup skipped (no files found or write failed); continuing update.")
        print()
        return

    try:
        size_bytes = out_path.stat().st_size
    except OSError:
        size_bytes = 0

    # Human-readable size
    size_str = f"{size_bytes} B"
    for unit in ("KB", "MB", "GB"):
        if size_bytes < 1024:
            break
        size_bytes /= 1024
        size_str = f"{size_bytes:.1f} {unit}"

    # Render path using display_openagents_home so the user sees ~/.openagents/...
    try:
        from openagents_constants import get_openagents_home, display_openagents_home

        home = get_openagents_home()
        try:
            display_path = f"{display_openagents_home()}/{out_path.relative_to(home)}"
        except ValueError:
            display_path = str(out_path)
    except Exception:
        display_path = str(out_path)

    print(f"  Saved:    {display_path} ({size_str}, {elapsed:.1f}s)")
    print(f"  Restore:  hermes import {out_path}")
    print(f"  Disable:  omit --backup (backups are off by default)")
    print(f"            set updates.pre_update_backup: false in config.yaml")
    print()


def _write_update_planned_stop_marker(profile_path: Path, pid: int) -> bool:
    """Write a planned-stop marker into a specific profile home."""
    try:
        from datetime import timezone

        from gateway.status import _get_process_start_time
        from utils import atomic_json_write

        record = {
            "target_pid": pid,
            "target_start_time": _get_process_start_time(pid),
            "stopper_pid": os.getpid(),
            "written_at": datetime.now(timezone.utc).isoformat(),
        }
        atomic_json_write(
            Path(profile_path) / ".gateway-planned-stop.json",
            record,
            indent=None,
            separators=(",", ":"),
        )
        return True
    except (OSError, PermissionError):
        return False


def _wait_for_windows_update_gateway_exit(
    pids: list[int], *, timeout: float
) -> set[int]:
    """Wait for the given gateway PIDs to exit, returning survivors."""
    if not pids:
        return set()

    from gateway.status import _pid_exists

    remaining = set(pids)
    deadline = _time.monotonic() + max(timeout, 0.0)
    while remaining and _time.monotonic() < deadline:
        for pid in list(remaining):
            try:
                if not _pid_exists(pid):
                    remaining.discard(pid)
            except Exception:
                remaining.discard(pid)
        if remaining:
            _time.sleep(0.25)

    survivors: set[int] = set()
    for pid in remaining:
        try:
            if _pid_exists(pid):
                survivors.add(pid)
        except Exception:
            pass
    return survivors


def _pause_windows_gateways_for_update() -> dict | None:
    """Stop running Windows gateways before mutating the checkout or venv.

    Windows scheduled/startup gateways run through pythonw.exe, so the generic
    hermes.exe concurrent-instance guard does not see them. They still import
    from the checkout and can keep files locked while ``git`` or ``uv`` updates
    the install. Stop only PIDs that the gateway discovery code identifies.
    """
    if not _is_windows():
        return None

    try:
        from gateway.status import terminate_pid
        from openagents_cli.gateway import (
            _capture_gateway_argv,
            _get_restart_drain_timeout,
            find_gateway_pids,
            find_profile_gateway_processes,
        )
    except Exception as exc:
        logger.debug("Could not prepare Windows gateway pause for update: %s", exc)
        return None

    try:
        running_pids = list(dict.fromkeys(find_gateway_pids(all_profiles=True)))
    except Exception as exc:
        logger.debug("Could not discover Windows gateway PIDs before update: %s", exc)
        return None
    if not running_pids:
        # No gateway is running right now, but the user may have installed an
        # autostart entry (Scheduled Task or Startup-folder login item) — that
        # is an explicit "I want a gateway" signal. A gateway that died between
        # updates (e.g. the spawning terminal/TUI closed, taking its child with
        # it) would otherwise never come back: the autostart entry only fires on
        # the next login, and the update flow's resume path only relaunched
        # gateways that were running when the update began. Cold-start one after
        # the update so an installed gateway is actually up post-update. Users
        # who run gateway-less (no autostart entry) get nothing forced on them.
        try:
            from openagents_cli import gateway_windows

            if gateway_windows.is_installed():
                return {
                    "resume_needed": True,
                    "profiles": {},
                    "unmapped_pids": [],
                    "unmapped": [],
                    "cold_start_if_installed": True,
                }
        except Exception as exc:
            logger.debug(
                "Could not check Windows gateway autostart state before update: %s",
                exc,
            )
        return None

    profile_processes = {}
    try:
        profile_processes = {
            proc.pid: proc for proc in find_profile_gateway_processes()
        }
    except Exception as exc:
        logger.debug("Could not map Windows gateway PIDs to profiles: %s", exc)

    profiles: dict[str, int] = {}
    mapped_pids = []
    for pid in running_pids:
        proc = profile_processes.get(pid)
        if proc is None:
            continue
        profiles[str(proc.profile)] = int(pid)
        mapped_pids.append(int(pid))
        _write_update_planned_stop_marker(Path(proc.path), int(pid))

    print("→ Stopping Windows gateway process(es) before updating Hermes...")
    try:
        drain_timeout = max(float(_get_restart_drain_timeout()), 1.0)
    except Exception:
        drain_timeout = 10.0
    survivors = _wait_for_windows_update_gateway_exit(
        mapped_pids,
        timeout=drain_timeout,
    )
    unmapped_pids = [pid for pid in running_pids if pid not in profile_processes]

    # Snapshot each unmapped gateway's command line *before* we force-kill it,
    # so ``_resume_windows_gateways_after_update`` can respawn it by replaying
    # its own argv. Unmapped gateways are ones with no profile→PID-file mapping
    # — e.g. a Windows Scheduled Task running ``pythonw.exe -m openagents_cli.main
    # gateway run``. Without this snapshot they were force-killed and never
    # restarted (the "Restart manually after update" dead-end from #50090).
    unmapped: list[dict] = []
    for pid in unmapped_pids:
        argv = None
        try:
            argv = _capture_gateway_argv(int(pid))
        except Exception as exc:
            logger.debug("Could not capture argv for unmapped gateway %s: %s", pid, exc)
        unmapped.append({"pid": int(pid), "argv": argv})

    force_killed = []
    for pid in sorted(set(survivors).union(unmapped_pids)):
        try:
            terminate_pid(int(pid), force=True)
            force_killed.append(int(pid))
        except (ProcessLookupError, PermissionError, OSError):
            pass

    if profiles:
        print(f"  ✓ Paused gateway profile(s): {', '.join(sorted(profiles))}")
    if force_killed:
        print(f"  → Force-stopped {len(force_killed)} gateway process(es)")

    if unmapped_pids:
        respawnable = sum(1 for u in unmapped if u.get("argv"))
        print(
            f"  → Stopped {len(unmapped_pids)} gateway process(es) without profile mapping"
        )
        if respawnable < len(unmapped_pids):
            # Some had no recoverable command line (psutil missing, access
            # denied, already gone): those still need a manual restart.
            print("    Restart manually after update: hermes gateway run")

    return {
        "resume_needed": True,
        "profiles": profiles,
        "unmapped_pids": unmapped_pids,
        "unmapped": unmapped,
    }


def _cold_start_windows_gateway_after_update() -> None:
    """Start a fresh detached gateway after update when one is installed but down.

    Invoked from ``_resume_windows_gateways_after_update`` for the
    ``cold_start_if_installed`` case: no gateway was running when the update
    began, but an autostart entry (Scheduled Task / Startup-folder login item)
    is installed, signalling the user wants a gateway. Unlike the relaunch
    paths — which watch an old PID and respawn once it exits — this is a direct
    fresh spawn via the same windowless ``pythonw`` + breakaway path that
    ``hermes gateway start`` uses (``gateway_windows._spawn_detached``).

    Best-effort and idempotent: re-checks that nothing is running first so a
    concurrent start (e.g. the autostart entry firing) can't produce a
    duplicate gateway.
    """
    if not _is_windows():
        return
    try:
        from openagents_cli import gateway_windows
        from openagents_cli.gateway import find_gateway_pids
    except Exception as exc:
        logger.debug("Could not load Windows gateway cold-start helpers: %s", exc)
        return

    # Re-check liveness right before spawning — between pause and resume the
    # autostart entry may have already brought a gateway up, or a leftover
    # process may have re-registered. Don't double-start.
    try:
        if list(find_gateway_pids(all_profiles=True)):
            return
    except Exception as exc:
        logger.debug("Could not re-check gateway liveness before cold-start: %s", exc)
        return

    try:
        pid = gateway_windows._spawn_detached()
    except Exception as exc:
        logger.debug("Could not cold-start Windows gateway after update: %s", exc)
        return

    if pid:
        print()
        print(f"  ✓ Starting Windows gateway after update (PID {pid})")


def _resume_windows_gateways_after_update(token: dict | None) -> None:
    """Restart Windows profile gateways previously paused for update."""
    if not token or not token.get("resume_needed"):
        return
    token["resume_needed"] = False
    if not _is_windows():
        return

    profiles = token.get("profiles") or {}
    unmapped = token.get("unmapped") or []
    cold_start = bool(token.get("cold_start_if_installed"))
    if not profiles and not any(u.get("argv") for u in unmapped):
        if cold_start:
            _cold_start_windows_gateway_after_update()
        return

    try:
        from openagents_cli.gateway import (
            launch_detached_gateway_restart_by_cmdline,
            launch_detached_profile_gateway_restart,
        )
    except Exception as exc:
        logger.debug("Could not load Windows gateway restart helper: %s", exc)
        return

    relaunched = []
    for profile, old_pid in sorted(profiles.items()):
        try:
            if launch_detached_profile_gateway_restart(str(profile), int(old_pid)):
                relaunched.append(str(profile))
        except Exception as exc:
            logger.debug(
                "Could not restart Windows gateway profile %s after update: %s",
                profile,
                exc,
            )

    # Respawn unmapped gateways (no profile→PID-file mapping, e.g. a Scheduled
    # Task) by replaying the argv we snapshotted before force-killing them.
    unmapped_relaunched = 0
    for entry in unmapped:
        argv = entry.get("argv")
        old_pid = entry.get("pid")
        if not argv or not old_pid:
            continue
        try:
            if launch_detached_gateway_restart_by_cmdline(int(old_pid), list(argv)):
                unmapped_relaunched += 1
        except Exception as exc:
            logger.debug(
                "Could not restart unmapped Windows gateway (pid %s) after update: %s",
                old_pid,
                exc,
            )

    if relaunched:
        print()
        print(f"  ✓ Restarting Windows gateway profile(s): {', '.join(relaunched)}")
    if unmapped_relaunched:
        if not relaunched:
            print()
        print(
            f"  ✓ Restarting {unmapped_relaunched} unmapped Windows gateway process(es)"
        )


def _discard_lockfile_churn(git_cmd, repo_root):
    """Restore tracked ``package-lock.json`` files that npm dirtied locally.

    npm rewrites lockfiles non-deterministically at install/build time. On a
    managed install those diffs are never intentional, so we discard them so
    ``hermes update`` sees a clean tree instead of autostashing every run.
    Best-effort; only ever touches files named ``package-lock.json``.
    """
    try:
        diff = subprocess.run(
            git_cmd + ["diff", "--name-only"],
            cwd=repo_root,
            capture_output=True,
            text=True,
        )
        if diff.returncode != 0:
            return
        dirty_package_dirs = {
            Path(line.strip()).parent
            for line in diff.stdout.splitlines()
            if line.strip().endswith("package.json")
        }
        dirty = [
            line.strip()
            for line in diff.stdout.splitlines()
            if line.strip().endswith("package-lock.json")
            and Path(line.strip()).parent not in dirty_package_dirs
        ]
        if not dirty:
            return
        subprocess.run(
            git_cmd + ["checkout", "--", *dirty],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        print(f"→ Discarded npm lockfile churn ({len(dirty)} file(s))")
    except Exception:
        # Never let lockfile cleanup block an update.
        pass


def cmd_update(args):
    """Update OpenAgents to the latest version.

    Thin wrapper around ``_cmd_update_impl``: installs hangup protection,
    runs the update, then restores stdio on the way out (even on
    ``sys.exit`` or unhandled exceptions).
    """
    from openagents_cli.config import (
        detect_install_method,
        format_docker_update_message,
        is_managed,
        managed_error,
    )
=======
def _update_preflight_handled(args) -> bool:
    """Managed-install refusal, --plan, admission gate, --check. True = nothing more to do."""
    from openagents_cli.config import is_managed, managed_error
>>>>>>> rb/tag

    if is_managed():
        managed_error("update OpenAgents")
        return True

    # --plan is read-only and deployment-kind aware, so it runs BEFORE the
    # docker/nix/apt refusal gates: on an image/package-managed install the
    # plan itself reports "not updatable in place" plus the right mechanism.
    if getattr(args, "plan", False):
        # Read-only plan phase (#91277 Phase 2): inventory every running OpenAgents runtime across profiles, its
        # supervisor, and its running code version — without mutating anything. Safe on a live fleet.
        from openagents_cli.update_inventory import (
            collect_runtime_inventory,
            print_update_plan,
        )

        print_update_plan(collect_runtime_inventory())
        return True

    if getattr(args, "list_venv_holders", False):
        # Read-only twin of the Windows venv-holder refusal (#117246): same scan and classifiers,
        # machine-readable, exit 3 when holders remain so automation can stop those PIDs and retry.
        import json

        from openagents_cli.update_cmd_windows import VENV_HOLDERS_EXIT, list_venv_holders

        holders = list_venv_holders()
        print(json.dumps(holders, indent=2))
        if holders:
            sys.exit(VENV_HOLDERS_EXIT)
        return True

    # Image/package-managed admission gate: baked provenance marker first
    # (fail-closed on malformed), then docker/nix/apt heuristics. Records a
    # `refused` receipt and exits 2 (refused-by-contract, distinct from errors).
    # Image-managed / package-managed admission gate (#91277 Phase 3): one shared decision for every
    # mutation surface. Prints the real update command, records a `refused` receipt so fleet tooling sees
    # the blocked attempt, and exits 2 (refused-by-contract, distinct from exit 1 errors).
    # Shared admission gate (#91277 Phase 3): same marker-first decision as the apply path, so --check can
    # never report git state for an install whose real update mechanism is an image pull.
    # The response keeps the pre-existing per-kind error codes the dashboard UI already keys on. See #91277.
    from openagents_cli.update_contract import (
        evaluate_update_admission,
        record_refusal_receipt,
    )

    refusal = evaluate_update_admission(PROJECT_ROOT)
    if refusal is not None:
        print(refusal.message)
        record_refusal_receipt(refusal)
        sys.exit(2)

    if getattr(args, "check", False):
        # --check honors --branch so its answer matches what update would pull.
        branch = _resolve_update_branch(args)
        from openagents_cli.update_cmd import _cmd_update_check

        _cmd_update_check(
            branch=branch,
            branch_explicit=bool(getattr(args, "branch", None)),
        )
        return True
    return False


def cmd_update(args):
    """Update OpenAgents: hangup protection + update lock around ``_cmd_update_impl``."""
    if _update_preflight_handled(args):
        return
    gateway_mode = getattr(args, "gateway", False)

    _update_io_state = _install_hangup_protection(gateway_mode=gateway_mode)
    # Cross-process mutual exclusion: dashboard Update button, Tauri updater
    # and this command all mutate one checkout; two at once strand it
    # half-updated. Shares the marker the Tauri/Electron updaters already use.
    from openagents_cli.update_lock import (
        UPDATE_EXIT_CONCURRENT,
        UpdateLock,
        describe_holder,
    )

    # A child spawned off hermes.exe: the parent still holds the shim (and the venv python)
    # until it exits — nothing below may scan holders, pause gateways or rename shims before.
    # Waiting BEFORE the lock matters: the parent's exit releases ITS marker, so a child that
    # merely ran under the parent's claim would finish the install with no lock at all
    # (#101600); once the parent is gone the child claims a marker of its own.
    from openagents_cli.update_handoff import wait_for_shim_parent_exit

    wait_for_shim_parent_exit()

    _update_lock = UpdateLock()
    if not _update_lock.acquire():
        print(describe_holder(_update_lock.holder))
        _finalize_update_output(_update_io_state)
        sys.exit(UPDATE_EXIT_CONCURRENT)

    # Exit code for the Windows hand-off child's hard exit (see finally); None
    # = not SystemExit-shaped, so real exceptions keep their traceback.
    _update_handoff_exit_code: int | None = None
    from openagents_cli.update_cmd import _cmd_update_impl

    try:
        _cmd_update_impl(args, gateway_mode=gateway_mode)
    except SystemExit as _update_exit:
        # Receipt boundary: the impl has many early sys.exit paths that never
        # reach an inner finalize. Persist any still-open receipt with the real
        # exit code (no-op if already finalized), then let the exit proceed.
        _code = _update_exit.code if isinstance(_update_exit.code, int) else 1
        _finalize_update_receipt(_code, f"sys.exit({_code})")
        _update_handoff_exit_code = (
            _update_exit.code if isinstance(_update_exit.code, int) else 0
        )
        raise
    except BaseException as _update_exc:
        _finalize_update_receipt(1, f"{type(_update_exc).__name__}: {_update_exc}")
        raise
    else:
        from openagents_cli.update_receipt import COMMAND_BOUNDARY_STOP_REASON

        _finalize_update_receipt(0, COMMAND_BOUNDARY_STOP_REASON)
        _update_handoff_exit_code = 0
    finally:
        _update_lock.release()
        _finalize_update_output(_update_io_state)
        # Windows hand-off child: a leftover non-daemon thread from the update
        # tail would freeze the PowerShell window for minutes after the receipt
        # is durable. Every durable step is done by now, so on the hand-off
        # path only (marker env set solely by
        # _reexec_dependency_sync_off_windows_shim) flush and exit hard.
        # By this point every durable step is done (receipt finalized above, lock released, stdio restored),
        # so on the hand-off path only, flush and exit hard instead of waiting for the interpreter to unwind
        # — the same treatment #79040's cron workaround applies.
        if _update_handoff_exit_code is not None and os.environ.get(_UPDATE_REEXEC_ENV) == "1":
            logger.debug(
                "Update hand-off child %s exiting via os._exit(%s)",
                os.getpid(), _update_handoff_exit_code,
            )
<<<<<<< HEAD
            sys.exit(1)

    # On Windows, git can fail with "unable to write loose object file: Invalid argument"
    # due to filesystem atomicity issues. Set the recommended workaround.
    if sys.platform == "win32" and git_dir.exists():
        subprocess.run(
            [
                "git",
                "-c",
                "windows.appendAtomically=false",
                "config",
                "windows.appendAtomically",
                "false",
            ],
            cwd=PROJECT_ROOT,
            check=False,
            capture_output=True,
        )

    # Build git command once — reused for fork detection and the update itself.
    git_cmd = ["git"]
    if sys.platform == "win32":
        git_cmd = ["git", "-c", "windows.appendAtomically=false"]

    # Discard npm lockfile churn before any stash/branch logic. npm rewrites
    # tracked package-lock.json files non-deterministically at install/build
    # time (platform-specific optional deps, ideallyInert annotations, etc.),
    # which is never an intentional edit on a managed install but leaves the
    # tree dirty — forcing an autostash on every update and making branch
    # switches fragile. Restoring them first lets the common case (only
    # lockfile churn) update with a clean tree.
    _discard_lockfile_churn(git_cmd, PROJECT_ROOT)

    # Detect if we're updating from a fork (before any branch logic)
    origin_url = _get_origin_url(git_cmd, PROJECT_ROOT)
    is_fork = _is_fork(origin_url)

    if is_fork:
        print("⚠ Updating from fork:")
        print(f"  {origin_url}")
        print()

    if use_zip_update:
        # ZIP-based update for Windows when git is broken
        try:
            _update_via_zip(args)
        finally:
            _resume_windows_gateways_after_update(_windows_gateway_resume)
        return

    # Fetch and pull
    try:

        # Resolve the target branch up front so the fetch can be scoped to it.
        # A bare `git fetch origin` pulls every ref, and this repo carries
        # thousands of auto-generated branches — an unscoped fetch can stall for
        # minutes on a non-single-branch checkout. Fetch only what we update
        # against.
        branch = _resolve_update_branch(args)

        print("→ Fetching updates...")
        fetch_result = subprocess.run(
            git_cmd + ["fetch", "origin", branch],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
        )
        if fetch_result.returncode != 0:
            stderr = fetch_result.stderr.strip()
            if "Could not resolve host" in stderr or "unable to access" in stderr:
                print("✗ Network error — cannot reach the remote repository.")
                print(f"  {stderr.splitlines()[0]}" if stderr else "")
            elif (
                "Authentication failed" in stderr or "could not read Username" in stderr
            ):
                print(
                    "✗ Authentication failed — check your git credentials or SSH key."
                )
            else:
                print(f"✗ Failed to fetch updates from origin.")
                if stderr:
                    print(f"  {stderr.splitlines()[0]}")
            sys.exit(1)

        # Get current branch (returns literal "HEAD" when detached)
        result = subprocess.run(
            git_cmd + ["rev-parse", "--abbrev-ref", "HEAD"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        current_branch = result.stdout.strip()

        # If user is on a different branch than the update target, switch
        # to the target. When the target is "main" this is the historical
        # "always update against main" behavior; for any other target it's
        # the same thing — get HEAD onto the requested branch first, then
        # fast-forward.
        if current_branch != branch:
            label = (
                "detached HEAD"
                if current_branch == "HEAD"
                else f"branch '{current_branch}'"
            )
            print(f"  ⚠ Currently on {label} — switching to {branch} for update...")
            # Stash before checkout so uncommitted work isn't lost
            auto_stash_ref = _stash_local_changes_if_needed(git_cmd, PROJECT_ROOT)
            checkout_result = subprocess.run(
                git_cmd + ["checkout", branch],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
            )
            if checkout_result.returncode != 0:
                # Local checkout doesn't have this branch yet. Try to set
                # it up as a tracking branch of origin/<branch>. This is
                # the common case when the requested branch exists upstream
                # but was never checked out locally.
                track_result = subprocess.run(
                    git_cmd + ["checkout", "-B", branch, f"origin/{branch}"],
                    cwd=PROJECT_ROOT,
                    capture_output=True,
                    text=True,
                )
                if track_result.returncode != 0:
                    # Restore the user's prior branch + stash before bailing
                    # so we don't leave them stranded in a weird state.
                    if auto_stash_ref is not None:
                        _restore_stashed_changes(
                            git_cmd,
                            PROJECT_ROOT,
                            auto_stash_ref,
                            prompt_user=False,
                            input_fn=gw_input_fn,
                        )
                    print(f"✗ Branch '{branch}' does not exist locally or on origin.")
                    if track_result.stderr.strip():
                        print(f"  {track_result.stderr.strip().splitlines()[0]}")
                    sys.exit(1)
        else:
            auto_stash_ref = _stash_local_changes_if_needed(git_cmd, PROJECT_ROOT)

        prompt_for_restore = (
            auto_stash_ref is not None
            and not assume_yes
            and (gateway_mode or (sys.stdin.isatty() and sys.stdout.isatty()))
        )

        # Check if there are updates
        result = subprocess.run(
            git_cmd + ["rev-list", f"HEAD..origin/{branch}", "--count"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        commit_count = int(result.stdout.strip())

        if commit_count == 0:
            _invalidate_update_cache()

            # Even if origin is up to date, the fork may be behind upstream
            if is_fork and branch == "main":
                _sync_with_upstream_if_needed(git_cmd, PROJECT_ROOT)

            # Restore stash and switch back to original branch if we moved
            if auto_stash_ref is not None:
                _restore_stashed_changes(
                    git_cmd,
                    PROJECT_ROOT,
                    auto_stash_ref,
                    prompt_user=prompt_for_restore,
                    input_fn=gw_input_fn,
                )
            if current_branch not in {branch, "HEAD"}:
                subprocess.run(
                    git_cmd + ["checkout", current_branch],
                    cwd=PROJECT_ROOT,
                    capture_output=True,
                    text=True,
                    check=False,
                )
            print("✓ Already up to date!")
            _resume_windows_gateways_after_update(_windows_gateway_resume)
            return

        print(f"→ Found {commit_count} new commit(s)")

        # Snapshot critical state (state.db, config, pairing JSONs, etc.)
        # before pulling so a user can recover if something goes wrong.
        # Issue #15733 reported missing pairing data after an update; even
        # though `git pull` can't touch $OPENAGENTS_HOME, this is cheap
        # belt-and-suspenders insurance and gives the user something to
        # restore from via `/snapshot list` / `/snapshot restore <id>`.
        pre_update_snapshot_id = None
        try:
            from openagents_cli.backup import create_quick_snapshot

            pre_update_snapshot_id = create_quick_snapshot(label="pre-update", keep=1)
            if pre_update_snapshot_id:
                print(f"  ✓ Pre-update snapshot: {pre_update_snapshot_id}")
        except Exception as exc:
            # Never let a snapshot failure block an update.
            logger.debug("Pre-update snapshot failed: %s", exc)

        print("→ Pulling updates...")
        update_succeeded = False
        # Capture the pre-pull SHA so we can auto-roll-back if the new code
        # has a syntax error in a critical-path file (PR #28452 incident:
        # orphan merge-conflict markers in openagents_cli/config.py bricked
        # every user who ran ``hermes update`` for the 7 minutes between
        # the bad commit and the fix landing).
        pre_pull_sha = _capture_head_sha(git_cmd, PROJECT_ROOT)
        try:
            pull_result = subprocess.run(
                git_cmd + ["pull", "--ff-only", "origin", branch],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
            )
            if pull_result.returncode != 0:
                # ff-only failed — local and remote have diverged (e.g. upstream
                # force-pushed or rebase).  Since local changes are already
                # stashed, reset to match the remote exactly.
                print(
                    "  ⚠ Fast-forward not possible (history diverged), resetting to match remote..."
                )
                reset_result = subprocess.run(
                    git_cmd + ["reset", "--hard", f"origin/{branch}"],
                    cwd=PROJECT_ROOT,
                    capture_output=True,
                    text=True,
                )
                if reset_result.returncode != 0:
                    print(f"✗ Failed to reset to origin/{branch}.")
                    if reset_result.stderr.strip():
                        print(f"  {reset_result.stderr.strip()}")
                    print(
                        f"  Try manually: git fetch origin && git reset --hard origin/{branch}"
                    )
                    sys.exit(1)

            # Post-pull syntax guard: validate critical-path files actually
            # parse before declaring the update successful. If a bad commit
            # made it through CI (e.g. admin-merge bypass of a failing
            # ruff check), this catches it on the user side and rolls back
            # so the CLI stays bootable. The user can then retry ``hermes
            # update`` later once a fix lands upstream.
            syntax_ok, failing_path, syntax_error = _validate_critical_files_syntax(
                PROJECT_ROOT
            )
            if not syntax_ok:
                print()
                print("✗ Pulled code has a syntax error in a critical file:")
                print(f"  {failing_path}")
                if syntax_error:
                    # py_compile errors can be multi-line; show the first
                    # ~6 lines so the user sees the actual SyntaxError text.
                    for line in str(syntax_error).splitlines()[:6]:
                        print(f"    {line}")
                if pre_pull_sha:
                    print()
                    print(f"→ Rolling back to {pre_pull_sha[:10]}...")
                    rollback_result = subprocess.run(
                        git_cmd + ["reset", "--hard", pre_pull_sha],
                        cwd=PROJECT_ROOT,
                        capture_output=True,
                        text=True,
                    )
                    if rollback_result.returncode == 0:
                        print("  ✓ Rollback complete — your install is unchanged.")
                        print("  Try ``hermes update`` again later once a fix lands.")
                    else:
                        print("  ✗ Rollback failed. Recover manually with:")
                        print(f"    cd {PROJECT_ROOT} && git reset --hard {pre_pull_sha}")
                        if rollback_result.stderr.strip():
                            print(f"    ({rollback_result.stderr.strip().splitlines()[0]})")
                else:
                    print()
                    print("  Could not capture pre-pull SHA — recover manually with:")
                    print(f"    cd {PROJECT_ROOT} && git reflog && git reset --hard <prev-sha>")
                sys.exit(1)

            update_succeeded = True
        finally:
            if auto_stash_ref is not None:
                # Don't attempt stash restore if the code update itself failed —
                # working tree is in an unknown state.
                if not update_succeeded:
                    print(
                        f"  ℹ️  Local changes preserved in stash (ref: {auto_stash_ref})"
                    )
                    print(f"  Restore manually with: git stash apply")
                elif discard_local_changes:
                    # Non-interactive update + user opted into discarding local
                    # source edits (updates.non_interactive_local_changes:
                    # discard). Throw the stash away instead of re-applying it.
                    _discard_stashed_changes(
                        git_cmd,
                        PROJECT_ROOT,
                        auto_stash_ref,
                    )
                else:
                    _restore_stashed_changes(
                        git_cmd,
                        PROJECT_ROOT,
                        auto_stash_ref,
                        prompt_user=prompt_for_restore,
                        input_fn=gw_input_fn,
                    )

        _invalidate_update_cache()

        # Clear stale .pyc bytecode cache — prevents ImportError on gateway
        # restart when updated source references names that didn't exist in
        # the old bytecode (e.g. get_openagents_home added to openagents_constants).
        removed = _clear_bytecode_cache(PROJECT_ROOT)
        if removed:
            print(
                f"  ✓ Cleared {removed} stale __pycache__ director{'y' if removed == 1 else 'ies'}"
            )

        # Fork upstream sync logic (only for main branch on forks)
        if is_fork and branch == "main":
            _sync_with_upstream_if_needed(git_cmd, PROJECT_ROOT)

        # Reinstall Python dependencies. Prefer .[all], but if one optional extra
        # breaks on this machine, keep base deps and reinstall the remaining extras
        # individually so update does not silently strip working capabilities.
        #
        # Drop the interrupted-install breadcrumb BEFORE touching the venv. If
        # the install is killed mid-flight (Ctrl-C, terminal close, WSL OOM),
        # the marker survives and the next ``hermes`` launch finishes the
        # install via ``_recover_from_interrupted_install``. Cleared only after
        # the install + core-dependency verification completes below.
        _write_update_incomplete_marker()
        print("→ Updating Python dependencies...")
        from openagents_cli.managed_uv import ensure_uv, update_managed_uv

        # Keep managed uv current — runs `uv self update` if we already have one.
        update_managed_uv()

        uv_bin = ensure_uv()

        pip_cmd = [sys.executable, "-m", "pip"]
        if not uv_bin:
            uv_bin = _ensure_uv_for_termux(pip_cmd)
        install_group = "all"

        if uv_bin:
            uv_env = {**os.environ, "VIRTUAL_ENV": str(PROJECT_ROOT / "venv")}
            if _is_termux_env(uv_env):
                uv_env.pop("PYTHONPATH", None)
                uv_env.pop("PYTHONHOME", None)
                install_group = "termux-all"
                print("  → Termux detected: using uv + curated termux-all optional profile...")
            if _is_termux_env(uv_env) and _is_android_python():
                print("  → Termux/Android detected: prebuilding psutil with Linux source path compatibility...")
                _install_psutil_android_compat([uv_bin, "pip"], env=uv_env)
            _install_python_dependencies_with_optional_fallback(
                [uv_bin, "pip"], env=uv_env, group=install_group
            )
        else:
            # Use sys.executable to explicitly call the venv's pip module,
            # avoiding PEP 668 'externally-managed-environment' errors on Debian/Ubuntu.
            # Some environments lose pip inside the venv; bootstrap it back with
            # ensurepip before trying the editable install.
            pip_cmd = [sys.executable, "-m", "pip"]
            try:
                subprocess.run(
                    pip_cmd + ["--version"],
                    cwd=PROJECT_ROOT,
                    check=True,
                    capture_output=True,
                )
            except subprocess.CalledProcessError:
                subprocess.run(
                    [sys.executable, "-m", "ensurepip", "--upgrade", "--default-pip"],
                    cwd=PROJECT_ROOT,
                    check=True,
                )
            if _is_termux_env():
                install_group = "termux-all"
                print("  → Termux detected: using curated termux-all optional profile...")
            if _is_termux_env() and _is_android_python():
                print("  → Termux/Android detected: prebuilding psutil with Linux source path compatibility...")
                _install_psutil_android_compat(pip_cmd)
            _install_python_dependencies_with_optional_fallback(pip_cmd, group=install_group)

        # Core Python deps installed AND verified (the fallback helper runs
        # _verify_core_dependencies_installed). Clear the interrupted-install
        # breadcrumb now — the remaining steps (lazy refresh, node deps, web
        # UI, desktop rebuild) are non-core and can't brick the venv.
        _clear_update_incomplete_marker()

        try:
            from openagents_fork import refresh_source_install_cli_links

            if refresh_source_install_cli_links(PROJECT_ROOT):
                print("  ✓ Refreshed ~/.local/bin CLI links")
        except Exception:
            logger.debug("source-install CLI link refresh failed", exc_info=True)

        _refresh_active_lazy_features()

        _update_node_dependencies()
        _build_web_ui(PROJECT_ROOT / "web")

        # Rebuild the desktop app if the source tree changed since the last
        # build.  ``hermes desktop --build-only`` uses the content-hash stamp
        # internally, so this is effectively a no-op when nothing changed.
        # Only bother if the user has a desktop app installed (indicated by
        # an existing packaged executable or desktop dist); people who have
        # never run ``hermes desktop`` shouldn't be forced into a full
        # Electron build by ``hermes update``.
        desktop_dir = PROJECT_ROOT / "apps" / "desktop"
        has_desktop_app = _desktop_packaged_executable(desktop_dir) is not None or _desktop_dist_exists(desktop_dir)
        from openagents_constants import find_node_executable

        if (desktop_dir / "package.json").exists() and find_node_executable("npm") and has_desktop_app:
            print("→ Checking if desktop app needs rebuilding...")
            _desktop_build_cmd = [sys.executable, "-m", "openagents_cli.main", "desktop", "--build-only"]
            # Capture the (very loud) Electron/vite build output into
            # update.log instead of streaming it to the terminal. On the rare
            # nonzero exit, retry once after waiting again for the venv — this
            # covers a still-settling rebuild window the first wait didn't fully
            # catch — then surface the captured tail so the failure is
            # debuggable.
            build_result = _run_logged_subprocess(_desktop_build_cmd, cwd=PROJECT_ROOT)
            if build_result.returncode != 0:
                build_result = _run_logged_subprocess(_desktop_build_cmd, cwd=PROJECT_ROOT)
            if build_result.returncode != 0:
                print("  ⚠ Desktop build failed (non-fatal; run `hermes desktop` to retry)")
                tail = "\n".join((build_result.stdout or "").strip().splitlines()[-15:])
                if tail:
                    print(tail)
                from openagents_constants import display_openagents_home as _dhh
                print(f"  Full build log: {_dhh()}/logs/update.log")
            else:
                print("  ✓ Desktop app up to date")

        print()
        print("✓ Code updated!")

        # Seed the model-catalog disk cache from the freshly-pulled checkout.
        # The repo ships the canonical catalog at
        # website/static/api/model-catalog.json, and `git pull` just made it
        # current — so copy it straight over ~/.openagents/cache/model_catalog.json
        # instead of waiting on a network fetch (which can be bot-gated or hit a
        # Portal hiccup). Keeps the model picker's curated/free lists in sync
        # with the version the user just installed. Non-fatal on failure: the
        # normal network refresh still applies on the next picker open.
        try:
            from openagents_cli.model_catalog import seed_cache_from_checkout

            if seed_cache_from_checkout(PROJECT_ROOT):
                print("  ✓ Model catalog cache refreshed from checkout")
        except Exception as e:
            logger.debug("Model catalog seed during update failed: %s", e)

        # After git pull, source files on disk are newer than cached Python
        # modules in this process.  Reload openagents_constants so that any lazy
        # import executed below (skills sync, gateway restart) sees new
        # attributes like display_openagents_home() added since the last release.
        try:
            import importlib
            import openagents_constants as _hc

            importlib.reload(_hc)
        except Exception:
            pass  # non-fatal — worst case a lazy import fails gracefully

        # Sync bundled skills (copies new, updates changed, respects user deletions)
        try:
            from tools.skills_sync import sync_skills

            print()
            print("→ Syncing bundled skills...")
            result = sync_skills(quiet=True)
            if result["copied"]:
                print(f"  + {len(result['copied'])} new: {', '.join(result['copied'])}")
            if result.get("updated"):
                print(
                    f"  ↑ {len(result['updated'])} updated: {', '.join(result['updated'])}"
                )
            if result.get("user_modified"):
                print(f"  ~ {len(result['user_modified'])} user-modified (kept)")
                print(
                    "    → see them: hermes skills list-modified  "
                    "(diff/reset to resume updates)"
                )
            if result.get("cleaned"):
                print(f"  − {len(result['cleaned'])} removed from manifest")
            if not result["copied"] and not result.get("updated"):
                print("  ✓ Skills are up to date")
        except Exception as e:
            logger.debug("Skills sync during update failed: %s", e)

        # Sync bundled skills to all profiles (including the active one).
        # seed_profile_skills() uses subprocess with an explicit OPENAGENTS_HOME so
        # it is not affected by sync_skills()'s module-level OPENAGENTS_HOME cache,
        # which means the active profile is reliably synced regardless of whether
        # the caller's OPENAGENTS_HOME env var points at the default or a named profile.
        try:
            from openagents_cli.profiles import (
                list_profiles,
                seed_profile_skills,
            )

            all_profiles = list_profiles()
            if all_profiles:
                print()
                print("→ Syncing bundled skills to all profiles...")
                for p in all_profiles:
                    try:
                        r = seed_profile_skills(p.path, quiet=True)
                        if r and r.get("skipped_opt_out"):
                            status = "opted out (--no-skills)"
                        elif r:
                            copied = len(r.get("copied", []))
                            updated = len(r.get("updated", []))
                            modified = len(r.get("user_modified", []))
                            parts = []
                            if copied:
                                parts.append(f"+{copied} new")
                            if updated:
                                parts.append(f"↑{updated} updated")
                            if modified:
                                parts.append(f"~{modified} user-modified")
                            status = ", ".join(parts) if parts else "up to date"
                        else:
                            status = "sync failed"
                        print(f"  {p.name}: {status}")
                    except Exception as pe:
                        print(f"  {p.name}: error ({pe})")
        except Exception:
            pass  # profiles module not available or no profiles

        # Backfill per-profile .env files for profiles created before the
        # .env-seeding fix (#44792). Copies the default install's .env so
        # those profiles keep the credentials they were effectively using.
        try:
            from openagents_cli.profiles import backfill_profile_envs

            backfilled = backfill_profile_envs(quiet=True)
            if backfilled:
                print()
                print(
                    f"→ Seeded .env for {len(backfilled)} profile(s) "
                    f"(copied from default): {', '.join(backfilled)}"
                )
        except Exception:
            pass  # profiles module not available or no profiles

        # Sync Honcho host blocks to all profiles
        try:
            from plugins.memory.honcho.cli import sync_honcho_profiles_quiet

            synced = sync_honcho_profiles_quiet()
            if synced:
                print(f"\n-> Honcho: synced {synced} profile(s)")
        except Exception:
            pass  # honcho plugin not installed or not configured

        # Check for config migrations
        print()
        print("→ Checking configuration for new options...")

        from openagents_cli.config import (
            get_missing_env_vars,
            get_missing_config_fields,
            check_config_version,
            migrate_config,
        )

        missing_env = get_missing_env_vars(required_only=True)
        missing_config = get_missing_config_fields()
        current_ver, latest_ver = check_config_version()

        has_new_options = bool(missing_env or missing_config)
        version_bump_only = (
            not has_new_options and current_ver < latest_ver
        )
        needs_migration = has_new_options or current_ver < latest_ver

        if version_bump_only:
            # Nothing for the user to fill in — only the config format version
            # changed (new defaults already merge in transparently). Asking
            # "configure new options now?" here is misleading: saying yes just
            # bumps the version and looks like a no-op (issue: ScottFive /
            # Tt2021). Apply it silently and say what actually happened.
            print()
            print(
                f"  ℹ Updating config format (v{current_ver} → v{latest_ver})…"
            )
            try:
                migrate_config(interactive=False, quiet=True)
                print("  ✓ Config format updated (no new settings to configure)")
            except Exception as _mig_err:
                print(f"  ⚠️  Config format update failed: {_mig_err}")
                print("     Run 'hermes config migrate' to retry.")
        elif needs_migration:
            print()
            # Show WHAT changed, not just a count, so the user can make an
            # informed yes/no decision (previously the prompt named nothing).
            def _print_items(items, label, key, fallback_key=None):
                if not items:
                    return
                print(f"  {label}:")
                shown = items[:8]
                for it in shown:
                    if isinstance(it, dict):
                        name = it.get(key) or (fallback_key and it.get(fallback_key)) or "?"
                        desc = (it.get("description") or "").strip()
                    else:
                        # Defensive: some callers/mocks pass bare name strings.
                        name = str(it)
                        desc = ""
                    if desc:
                        print(f"      • {name} — {desc}")
                    else:
                        print(f"      • {name}")
                extra = len(items) - len(shown)
                if extra > 0:
                    print(f"      … and {extra} more")

            if missing_env:
                print(
                    f"  ⚠️  {len(missing_env)} new required setting(s) need configuration"
                )
                _print_items(missing_env, "New settings", "name")
            if missing_config:
                print(f"  ℹ️  {len(missing_config)} new config option(s) available")
                _print_items(missing_config, "New options", "key")

            print()
            if assume_yes:
                print(
                    "  ℹ --yes: auto-applying config migration (skipping API-key prompts)."
                )
                response = "y"
            elif gateway_mode:
                response = (
                    _gateway_prompt(
                        "Would you like to configure new options now? [Y/n]", "n"
                    )
                    .strip()
                    .lower()
                )
            elif not (sys.stdin.isatty() and sys.stdout.isatty()):
                print("  ℹ Non-interactive session — applying safe config migrations.")
                response = "auto"
            else:
                try:
                    response = (
                        input("Would you like to configure them now? [Y/n]: ")
                        .strip()
                        .lower()
                    )
                except EOFError:
                    response = "n"

            if response in {"", "y", "yes", "auto"}:
                print()
                # Gateway mode, --yes, and non-interactive update contexts
                # (dashboard / web server actions) cannot prompt for API keys.
                # Still run the non-interactive migration pass before restarting
                # so new default config fields and version bumps are written
                # before the freshly updated gateway validates config at startup.
                interactive_migration = not (
                    gateway_mode or assume_yes or response == "auto"
                )
                results = migrate_config(interactive=interactive_migration, quiet=False)

                if results["env_added"] or results["config_added"]:
                    print()
                    print("✓ Configuration updated!")
                if (gateway_mode or assume_yes or response == "auto") and missing_env:
                    print("  ℹ API keys require manual entry: hermes config migrate")
            else:
                print()
                print("Skipped. Run 'hermes config migrate' later to configure.")
        else:
            print("  ✓ Configuration is up to date")

        # Safety net: config-version migrations have been observed to leave
        # cron/jobs.json valid-but-empty, silently dropping every scheduled
        # job (issue #34600). The desktop scheduler can also overwrite with
        # its own small set, causing partial loss (issue #52144). If the
        # live file now has fewer jobs than the pre-update snapshot, restore
        # it and warn loudly.
        try:
            from openagents_cli.backup import restore_cron_jobs_if_emptied

            cron_restore = restore_cron_jobs_if_emptied(pre_update_snapshot_id)
            if cron_restore:
                print()
                print(
                    "  ⚠️  cron/jobs.json lost jobs during this update — "
                    f"restored {cron_restore['job_count']} job(s) from "
                    f"pre-update snapshot {cron_restore['snapshot_id']}."
                )
        except Exception as exc:
            # Never let the cron safety net break an otherwise-good update.
            logger.debug("Cron jobs auto-restore check failed: %s", exc)

        print()
        print("✓ Update complete!")

        # Curator first-run heads-up. Only prints when curator is enabled AND
        # has never run — i.e. the window where the ticker would otherwise
        # have fired against a fresh skill library. Kept silent on steady
        # state so we don't nag.
        try:
            _print_curator_first_run_notice()
        except Exception as e:
            logger.debug("Curator first-run notice failed: %s", e)

        # Most-recent curator run notice — show-once per run. Surfaces the
        # rename map (`old-name → umbrella`) on the high-attention update
        # surface so users learn about consolidations without having to
        # check `hermes curator status`. Self-stamps after printing so it
        # never repeats for the same run.
        try:
            _print_curator_recent_run_notice()
        except Exception as e:
            logger.debug("Curator recent-run notice failed: %s", e)

        # Repair RHEL-family root installs where /usr/local/bin isn't on PATH
        # for non-login interactive shells.  No-op on every other platform.
        try:
            _ensure_fhs_path_guard()
        except Exception as e:
            logger.debug("FHS PATH guard check failed: %s", e)

        # Refresh the cua-driver binary used by the Computer Use toolset.
        # The upstream installer is gated on supported platforms and on the
        # binary already being on PATH, so this is a no-op for users who
        # don't have it. Tying the refresh to ``hermes update`` gives users a
        # predictable cadence (matches when they pull new agent code) without
        # adding startup latency or a per-launch GitHub API call.
        try:
            if sys.platform in ("darwin", "win32", "linux") and shutil.which("cua-driver"):
                from openagents_cli.tools_config import install_cua_driver

                print()
                print("→ Refreshing cua-driver (Computer Use)...")
                install_cua_driver(upgrade=True)
        except Exception as e:
            logger.debug("cua-driver refresh failed: %s", e)

        # Write exit code *before* the gateway restart attempt.
        # When running as ``hermes update --gateway`` (spawned by the gateway's
        # /update command), this process lives inside the gateway's systemd
        # cgroup.  A graceful SIGUSR1 restart keeps the drain loop alive long
        # enough for the exit-code marker to be written below, but the
        # fallback ``systemctl restart`` path (see below) kills everything in
        # the cgroup (KillMode=mixed → SIGKILL to remaining processes),
        # including us and the wrapping bash shell.  The shell never reaches
        # its ``printf $status > .update_exit_code`` epilogue, so the
        # exit-code marker file would never be created.  The new gateway's
        # update watcher would then poll for 30 minutes and send a spurious
        # timeout message.
        #
        # Writing the marker here — after git pull + pip install succeed but
        # before we attempt the restart — ensures the new gateway sees it
        # regardless of how we die.
        if gateway_mode:
            _exit_code_path = get_openagents_home() / ".update_exit_code"
            try:
                _exit_code_path.write_text("0")
            except OSError:
                pass

        # Auto-restart ALL gateways after update.
        # The code update (git pull) is shared across all profiles, so every
        # running gateway needs restarting to pick up the new code.
        try:
            from openagents_cli.gateway import (
                is_macos,
                supports_systemd_services,
                _ensure_user_systemd_env,
                find_gateway_pids,
                find_profile_gateway_processes,
                launch_detached_profile_gateway_restart,
                _get_service_pids,
                _graceful_restart_via_sigusr1,
                _wait_for_gateway_exit,
            )
            import signal as _signal

            def _wait_for_service_active(
                scope_cmd_: list,
                svc_name_: str,
                timeout: float = 10.0,
            ) -> bool:
                """Poll ``systemctl is-active`` until the unit reports active.

                systemd's Stopped -> Started transition after a graceful exit
                (or a hard restart) is not instantaneous; a one-shot check
                races that window and falsely reports the unit as down.
                Poll every 0.5s up to ``timeout`` seconds before giving up.
                """
                deadline = _time.monotonic() + max(timeout, 0.5)
                while True:
                    try:
                        _verify = subprocess.run(
                            scope_cmd_ + ["is-active", svc_name_],
                            capture_output=True,
                            text=True,
                            timeout=5,
                        )
                        if _verify.stdout.strip() == "active":
                            return True
                    except (FileNotFoundError, subprocess.TimeoutExpired):
                        pass
                    if _time.monotonic() >= deadline:
                        return False
                    _time.sleep(0.5)

            def _service_restart_sec(
                scope_cmd_: list,
                svc_name_: str,
                default: float = 0.0,
            ) -> float:
                """Read the unit's ``RestartUSec`` (RestartSec) in seconds.

                After a graceful exit-75, systemd waits ``RestartSec`` before
                respawning the unit.  Callers that poll for ``is-active``
                must use a timeout >= ``RestartSec`` + transition slack, or
                they'll give up *during* the cooldown window and wrongly
                conclude the unit didn't relaunch.
                """
                try:
                    _show = subprocess.run(
                        scope_cmd_
                        + [
                            "show",
                            svc_name_,
                            "--property=RestartUSec",
                            "--value",
                        ],
                        capture_output=True,
                        text=True,
                        timeout=5,
                    )
                except (FileNotFoundError, subprocess.TimeoutExpired):
                    return default
                raw = (_show.stdout or "").strip()
                # systemd emits values like "30s", "100ms", "1min 30s", or
                # "infinity".  Parse conservatively; on any miss return default.
                if not raw or raw == "infinity":
                    return default
                total = 0.0
                matched = False
                for part in raw.split():
                    for _suf, _mult in (
                        ("ms", 0.001),
                        ("us", 0.000001),
                        ("min", 60.0),
                        ("s", 1.0),
                    ):
                        if part.endswith(_suf):
                            try:
                                total += float(part[: -len(_suf)]) * _mult
                                matched = True
                            except ValueError:
                                pass
                            break
                return total if matched else default

            _manage_cmd_cache: dict = {}

            def _resolve_manage_cmd(scope_: str, scope_cmd_: list, svc_name_: str):
                """Resolve the command prefix for manage-units operations.

                Read-only systemctl calls (``is-active``, ``show``,
                ``list-units``) work unprivileged, but manage-units verbs
                (``reset-failed``, ``start``, ``restart``) on a *system*
                service trigger a polkit ``org.freedesktop.systemd1.manage-units``
                authentication prompt when run as a non-root user.  That
                interactive prompt runs inside our captured subprocess with a
                10-15s timeout — the user sees the prompt flash and "exit
                directly" before they can answer, and the resulting
                TimeoutExpired used to be swallowed silently.

                Strategy: if root, plain systemctl.  If not root, try
                non-interactive sudo (``sudo -n``) — first a blanket probe,
                then a targeted ``systemctl reset-failed`` probe so a
                least-privilege sudoers entry scoped to
                ``systemctl ... openagents-gateway*`` also qualifies
                (``reset-failed`` is an idempotent no-op we run before every
                privileged restart anyway).  If neither works, return None —
                the caller must SKIP the restart (without draining the
                gateway first!) and tell the user how to restart manually.
                ``--no-ask-password`` guarantees polkit can never hang a
                captured subprocess on this path.
                """
                if scope_ in _manage_cmd_cache:
                    return _manage_cmd_cache[scope_]
                cmd = scope_cmd_ + ["--no-ask-password"]
                if (
                    scope_ == "system"
                    and hasattr(os, "geteuid")
                    and os.geteuid() != 0  # windows-footgun: ok — systemd path, Linux-only
                ):
                    sudo_cmd = ["sudo", "-n"] + scope_cmd_ + ["--no-ask-password"]
                    sudo_ok = False
                    try:
                        _probe = subprocess.run(
                            ["sudo", "-n", "true"],
                            capture_output=True,
                            timeout=5,
                        )
                        sudo_ok = _probe.returncode == 0
                        if not sudo_ok:
                            # Blanket sudo refused — a targeted sudoers entry
                            # (NOPASSWD for systemctl ... openagents-gateway*)
                            # may still allow the exact commands we need.
                            _probe = subprocess.run(
                                sudo_cmd + ["reset-failed", svc_name_],
                                capture_output=True,
                                timeout=5,
                            )
                            sudo_ok = _probe.returncode == 0
                    except (FileNotFoundError, subprocess.TimeoutExpired):
                        sudo_ok = False
                    cmd = sudo_cmd if sudo_ok else None
                _manage_cmd_cache[scope_] = cmd
                return cmd

            # Drain budget for graceful SIGUSR1 restarts.  The gateway drains
            # for up to ``agent.restart_drain_timeout`` (default 60s) before
            # exiting with code 75; we wait slightly longer so the drain
            # completes before we fall back to a hard restart.  On older
            # systemd units without SIGUSR1 wiring this wait just times out
            # and we fall back to ``systemctl restart`` (the old behaviour).
            try:
                from openagents_constants import (
                    DEFAULT_GATEWAY_RESTART_DRAIN_TIMEOUT as _DEFAULT_DRAIN,
                )
            except Exception:
                _DEFAULT_DRAIN = 60.0
            _cfg_drain = None
            try:
                from openagents_cli.config import load_config

                _cfg_agent = load_config().get("agent") or {}
                _cfg_drain = _cfg_agent.get("restart_drain_timeout")
            except Exception:
                pass
            try:
                _drain_budget = (
                    float(_cfg_drain)
                    if _cfg_drain is not None
                    else float(_DEFAULT_DRAIN)
                )
            except (TypeError, ValueError):
                _drain_budget = float(_DEFAULT_DRAIN)
            # Add a 15s margin so the drain loop + final exit finish before
            # we escalate to ``systemctl restart`` / SIGTERM.
            _drain_budget = max(_drain_budget, 30.0) + 15.0

            restarted_services = []
            killed_pids = set()
            relaunched_profiles = []

            # --- Systemd services (Linux) ---
            # Discover all openagents-gateway* units (default + profiles)
            if supports_systemd_services():
                try:
                    _ensure_user_systemd_env()
                except Exception:
                    pass

                for scope, scope_cmd in [
                    ("user", ["systemctl", "--user"]),
                    ("system", ["systemctl"]),
                ]:
                    try:
                        result = subprocess.run(
                            scope_cmd
                            + [
                                "list-units",
                                "openagents-gateway*",
                                "--plain",
                                "--no-legend",
                                "--no-pager",
                            ],
                            capture_output=True,
                            text=True,
                            timeout=10,
                        )
                        for line in result.stdout.strip().splitlines():
                            parts = line.split()
                            if not parts:
                                continue
                            unit = parts[
                                0
                            ]  # e.g. openagents-gateway.service or openagents-gateway-coder.service
                            if not unit.endswith(".service"):
                                continue
                            svc_name = unit.removesuffix(".service")
                            # Check if active
                            check = subprocess.run(
                                scope_cmd + ["is-active", svc_name],
                                capture_output=True,
                                text=True,
                                timeout=5,
                            )
                            if check.stdout.strip() != "active":
                                continue

                            # Resolve how we may run manage-units verbs
                            # (reset-failed/start/restart) for this scope.
                            # None ⇒ no non-interactive privilege path; we
                            # must avoid those verbs entirely or polkit will
                            # throw an interactive auth prompt inside our
                            # captured 10-15s subprocess (the user sees it
                            # flash and "exit directly" — reported June 2026).
                            _manage_cmd = _resolve_manage_cmd(
                                scope, scope_cmd, svc_name
                            )

                            # Prefer a graceful SIGUSR1 restart so in-flight
                            # agent runs drain instead of being SIGKILLed.
                            # The gateway's SIGUSR1 handler calls
                            # request_restart(via_service=True) → drain →
                            # exit; systemd's Restart=always respawns the unit.
                            _main_pid = 0
                            try:
                                _show = subprocess.run(
                                    scope_cmd
                                    + [
                                        "show",
                                        svc_name,
                                        "--property=MainPID",
                                        "--value",
                                    ],
                                    capture_output=True,
                                    text=True,
                                    timeout=5,
                                )
                                _main_pid = int((_show.stdout or "").strip() or 0)
                            except (
                                ValueError,
                                subprocess.TimeoutExpired,
                                FileNotFoundError,
                            ):
                                _main_pid = 0

                            _graceful_ok = False
                            if _main_pid > 0:
                                print(
                                    f"  → {svc_name}: draining (up to {int(_drain_budget)}s)..."
                                )
                                _graceful_ok = _graceful_restart_via_sigusr1(
                                    _main_pid,
                                    drain_timeout=_drain_budget,
                                )

                            if _graceful_ok:
                                # Gateway exited after a planned restart.
                                # ``Restart=always`` means systemd WILL respawn
                                # the unit — but only after
                                # ``RestartSec`` (default 60s on our unit
                                # file). That 60s wait is a crash-loop guard,
                                # and is the right default when the gateway
                                # dies unexpectedly. For a voluntary restart
                                # on update, it's dead time the user watches.
                                #
                                # Shortcut it: ``reset-failed`` + ``start``
                                # skips RestartSec entirely (we're manually
                                # initiating the unit, not waiting for
                                # systemd's auto-restart logic). Takes about
                                # as long as the process takes to come up
                                # (~1-3s on a warm box).
                                #
                                # If the unit is already active because
                                # RestartSec elapsed while we were draining,
                                # ``start`` is a no-op and we fall through to
                                # the poll below. Either way we collapse the
                                # 60s+ delay to a ~5s one.
                                #
                                # The shortcut needs manage-units privileges.
                                # Without them (system service, non-root, no
                                # passwordless sudo) skip it — systemd's own
                                # auto-restart still relaunches the unit after
                                # RestartSec, no privileges required.
                                if _manage_cmd is not None:
                                    subprocess.run(
                                        _manage_cmd + ["reset-failed", svc_name],
                                        capture_output=True,
                                        text=True,
                                        timeout=10,
                                    )
                                    subprocess.run(
                                        _manage_cmd + ["start", svc_name],
                                        capture_output=True,
                                        text=True,
                                        timeout=15,
                                    )
                                    # Short poll: the gateway should be up
                                    # within a few seconds now that we
                                    # bypassed RestartSec.
                                    if _wait_for_service_active(
                                        scope_cmd,
                                        svc_name,
                                        timeout=10.0,
                                    ):
                                        restarted_services.append(svc_name)
                                        continue
                                # Passive poll: systemd's auto-restart fires
                                # after RestartSec regardless of privileges.
                                # This is the primary path when _manage_cmd is
                                # None, and the fallback when the explicit
                                # start didn't take.
                                _restart_sec = _service_restart_sec(
                                    scope_cmd,
                                    svc_name,
                                    default=0.0,
                                )
                                _post_drain_timeout = max(
                                    10.0,
                                    _restart_sec + 10.0,
                                )
                                if _manage_cmd is None and _restart_sec > 5.0:
                                    print(
                                        f"  → {svc_name}: waiting for systemd "
                                        f"auto-restart (~{int(_restart_sec)}s; "
                                        "no root for an immediate restart)..."
                                    )
                                if _wait_for_service_active(
                                    scope_cmd,
                                    svc_name,
                                    timeout=_post_drain_timeout,
                                ):
                                    restarted_services.append(svc_name)
                                    continue
                                # Process exited but wasn't respawned (older
                                # unit without Restart=on-failure or
                                # RestartForceExitStatus=75).  Fall through
                                # to systemctl start/restart.
                                print(
                                    f"  ⚠ {svc_name} drained but didn't relaunch — forcing restart"
                                )

                            # Forcing a restart requires manage-units
                            # privileges.  Without a non-interactive path,
                            # running systemctl here would spawn a polkit
                            # auth prompt inside a captured 10-15s subprocess
                            # — it flashes and dies before the user can
                            # answer.  Skip with clear instructions instead.
                            if _manage_cmd is None:
                                print(
                                    f"  ⚠ {svc_name} is a system service and restarting it needs root.\n"
                                    f"    Restart it manually to load the new version:\n"
                                    f"      sudo systemctl restart {svc_name}\n"
                                    f"    To let `hermes update` restart it automatically, allow\n"
                                    f"    passwordless sudo for systemctl, or run updates with sudo."
                                )
                                continue

                            # Fallback: blunt systemctl restart.  This is
                            # what the old code always did; we get here only
                            # when the graceful path failed (unit missing
                            # SIGUSR1 wiring, drain exceeded the budget,
                            # restart-policy mismatch).
                            #
                            # Always `reset-failed` first.  If systemd's own
                            # auto-restart attempts already parked the unit
                            # in a failed state (transient CHDIR / OOM /
                            # filesystem race after our drain + exit-75),
                            # a plain `systemctl restart` can wedge against
                            # the RestartSec backoff and leave the unit
                            # dead.  Clearing the failed state first makes
                            # the restart idempotent.  Mirrors the recovery
                            # path in `hermes gateway restart`
                            # (`systemd_restart()`) as of PR #20949.
                            subprocess.run(
                                _manage_cmd + ["reset-failed", svc_name],
                                capture_output=True,
                                text=True,
                                timeout=10,
                            )
                            restart = subprocess.run(
                                _manage_cmd + ["restart", svc_name],
                                capture_output=True,
                                text=True,
                                timeout=15,
                            )
                            if restart.returncode == 0:
                                # Verify the service actually survived the
                                # restart.  systemctl restart returns 0 even
                                # if the new process crashes immediately.
                                if _wait_for_service_active(
                                    scope_cmd,
                                    svc_name,
                                    timeout=10.0,
                                ):
                                    restarted_services.append(svc_name)
                                else:
                                    # Retry once — transient startup failures
                                    # (stale module cache, import race) often
                                    # resolve on the second attempt.  Again
                                    # clear any failed state first so the
                                    # retry isn't blocked by the previous
                                    # crash.
                                    print(
                                        f"  ⚠ {svc_name} died after restart, retrying..."
                                    )
                                    subprocess.run(
                                        _manage_cmd + ["reset-failed", svc_name],
                                        capture_output=True,
                                        text=True,
                                        timeout=10,
                                    )
                                    subprocess.run(
                                        _manage_cmd + ["restart", svc_name],
                                        capture_output=True,
                                        text=True,
                                        timeout=15,
                                    )
                                    if _wait_for_service_active(
                                        scope_cmd,
                                        svc_name,
                                        timeout=10.0,
                                    ):
                                        restarted_services.append(svc_name)
                                        print(f"  ✓ {svc_name} recovered on retry")
                                    else:
                                        _scope_flag = "--user " if scope == "user" else ""
                                        _sudo_hint = "sudo " if scope == "system" else ""
                                        print(
                                            f"  ✗ {svc_name} failed to stay running after restart.\n"
                                            f"    Check logs: {_sudo_hint}journalctl {_scope_flag}-u {svc_name} --since '2 min ago'\n"
                                            f"    Recover manually:\n"
                                            f"      {_sudo_hint}systemctl {_scope_flag}reset-failed {svc_name}\n"
                                            f"      {_sudo_hint}systemctl {_scope_flag}restart {svc_name}"
                                        )
                            else:
                                print(
                                    f"  ⚠ Failed to restart {svc_name}: {restart.stderr.strip()}"
                                )
                    except FileNotFoundError:
                        pass
                    except subprocess.TimeoutExpired as exc:
                        # Don't swallow this silently — a wedged systemctl
                        # call here used to make the whole restart phase
                        # vanish with no output (June 2026 report).
                        print(
                            f"  ⚠ systemctl timed out during the {scope}-scope "
                            f"gateway restart ({exc.cmd if exc.cmd else 'unknown command'}). "
                            f"Check the gateway with: hermes gateway status"
                        )

            # --- Launchd services (macOS) ---
            if is_macos():
                try:
                    from openagents_cli.gateway import (
                        launchd_restart,
                        get_launchd_label,
                        get_launchd_plist_path,
                    )

                    plist_path = get_launchd_plist_path()
                    if plist_path.exists():
                        check = subprocess.run(
                            ["launchctl", "list", get_launchd_label()],
                            capture_output=True,
                            text=True,
                            timeout=5,
                        )
                        if check.returncode == 0:
                            try:
                                launchd_restart()
                                restarted_services.append(get_launchd_label())
                            except subprocess.CalledProcessError as e:
                                stderr = (getattr(e, "stderr", "") or "").strip()
                                print(f"  ⚠ Gateway restart failed: {stderr}")
                except (FileNotFoundError, subprocess.TimeoutExpired, ImportError):
                    pass

            # --- Manual (non-service) gateways ---
            # Kill any remaining gateway processes not managed by a service.
            # Exclude PIDs that belong to just-restarted services so we don't
            # immediately kill the process that systemd/launchd just spawned.
            service_pids = _get_service_pids()
            manual_pids = find_gateway_pids(
                exclude_pids=service_pids, all_profiles=True
            )
            profile_processes = {
                proc.pid: proc
                for proc in find_profile_gateway_processes(exclude_pids=service_pids)
                if proc.pid in manual_pids
            }
            for pid, proc in profile_processes.items():
                if not launch_detached_profile_gateway_restart(proc.profile, pid):
                    continue
                # Prefer a graceful SIGUSR1 drain so in-flight agent runs
                # finish before the watcher respawns the gateway.  If the
                # gateway doesn't support SIGUSR1 or doesn't exit within
                # the drain budget, fall back to SIGTERM — the watcher
                # still sees the exit and relaunches either way.
                # Announce the drain first: this wait can hold for the full
                # budget per gateway with no other output, and on surfaces
                # that stream update progress (the desktop updater most of
                # all) the silence reads as a hung update (#44515).
                print(
                    f"  → {proc.profile}: draining gateway PID {pid} "
                    f"(up to {int(_drain_budget)}s)..."
                )
                drained = _graceful_restart_via_sigusr1(
                    pid,
                    drain_timeout=_drain_budget,
                )
                if not drained:
                    try:
                        os.kill(pid, _signal.SIGTERM)
                    except (ProcessLookupError, PermissionError):
                        pass
                # Wait for the old process to fully exit before the watcher
                # spawns the new gateway.  Telegram holds the previous
                # getUpdates long-poll session open on its servers for up to
                # ~30s after the client disconnects.  If the new gateway
                # connects before that window expires it receives a 409
                # Conflict, which _handle_polling_conflict() recovers from
                # via back-off retries — but a brief wait here reduces the
                # chance of hitting that path at all, especially on fast
                # machines where the watcher loop restarts in < 1s.
                # We wait up to 5s for the process to exit (the OS-level
                # close, not the Telegram server-side expiry), then let the
                # watcher take over.  The Telegram adapter's retry logic
                # handles any remaining 409s if the server session is still
                # live when the new gateway polls.
                _wait_for_gateway_exit(timeout=5.0, force_after=None)
                killed_pids.add(pid)
                relaunched_profiles.append(proc.profile)

            for pid in manual_pids:
                if pid in profile_processes:
                    continue
                try:
                    os.kill(pid, _signal.SIGTERM)
                    killed_pids.add(pid)
                except (ProcessLookupError, PermissionError):
                    pass

            if restarted_services or killed_pids:
                print()
                for svc in restarted_services:
                    print(f"  ✓ Restarted {svc}")
                if relaunched_profiles:
                    names = ", ".join(relaunched_profiles)
                    print(f"  ✓ Restarting manual gateway profile(s): {names}")
                unmapped_count = len(killed_pids) - len(relaunched_profiles)
                if unmapped_count:
                    print(f"  → Stopped {unmapped_count} manual gateway process(es)")
                    print("    Restart manually: hermes gateway run")
                    if unmapped_count > 1:
                        print(
                            "    (or: hermes -p <profile> gateway run  for each profile)"
                        )

            if not restarted_services and not killed_pids:
                # No gateways were running — nothing to do
                pass

            # --- Post-restart survivor sweep -----------------------------
            # Issue #17648: some gateways ignore SIGTERM (stuck drain,
            # blocked I/O, PID dead but zombie).  The detached profile
            # watchers wait 120s for the old PID to exit — if it never
            # does, no respawn happens and the user keeps hitting
            # ImportError against a stale sys.modules.  Give the
            # graceful paths a brief window to complete, then SIGKILL
            # any remaining pre-update PIDs so the watcher / service
            # manager can relaunch with fresh code.
            try:
                _time.sleep(3.0)
                _service_pids_after = _get_service_pids()
                _surviving = find_gateway_pids(
                    exclude_pids=_service_pids_after,
                    all_profiles=True,
                )
                # Scope to PIDs we already tried to kill during this
                # update (killed_pids).  Anything new is a gateway that
                # started AFTER our restart attempt — respecting user
                # intent, we don't kill those.
                _stuck = [pid for pid in _surviving if pid in killed_pids]
                if _stuck:
                    print()
                    print(
                        f"  ⚠ {len(_stuck)} gateway process(es) ignored SIGTERM — force-killing"
                    )
                    from gateway.status import terminate_pid as _terminate_pid
                    for pid in _stuck:
                        try:
                            # Routes through taskkill /T /F on Windows,
                            # SIGKILL on POSIX — _signal.SIGKILL doesn't
                            # exist on Windows so the old raw os.kill call
                            # used to crash the entire update path.
                            _terminate_pid(pid, force=True)
                        except (ProcessLookupError, PermissionError, OSError):
                            pass
                    # Give the OS a beat to reap the processes so the
                    # watchers see them exit and respawn.
                    _time.sleep(1.5)
            except Exception as _sweep_exc:
                logger.debug("Post-restart survivor sweep failed: %s", _sweep_exc)

        except Exception as e:
            logger.debug("Gateway restart during update failed: %s", e)

        _resume_windows_gateways_after_update(_windows_gateway_resume)

        # Warn if legacy OpenAgents gateway unit files are still installed.
        # When both hermes.service (from a pre-rename install) and the
        # current openagents-gateway.service are enabled, they SIGTERM-fight
        # for the same bot token (see PR #11909). Flagging here means
        # every `hermes update` surfaces the issue until the user migrates.
        try:
            from openagents_cli.gateway import (
                has_legacy_hermes_units,
                _find_legacy_hermes_units,
                supports_systemd_services,
            )

            if supports_systemd_services() and has_legacy_hermes_units():
                print()
                print("⚠ Legacy OpenAgents gateway unit(s) detected:")
                for name, path, is_sys in _find_legacy_hermes_units():
                    scope = "system" if is_sys else "user"
                    print(f"    {path}  ({scope} scope)")
                print()
                print("  These pre-rename units (hermes.service) fight the current")
                print("  openagents-gateway.service for the bot token and cause SIGTERM")
                print("  flap loops. Remove them with:")
                print()
                print("    hermes gateway migrate-legacy")
                print()
                print("  (add `sudo` if any are in system scope)")
        except Exception as e:
            logger.debug("Legacy unit check during update failed: %s", e)

        # Kill stale dashboard processes — the dashboard has no service
        # manager, so leaving it alive after a code update produces a
        # silent frontend/backend mismatch.  We can't auto-restart it
        # (no saved launch args) but we can stop it, and a hint is
        # printed for the user to re-launch.
        _kill_stale_dashboard_processes()

        print()
        print("Tip: You can now select a provider and model:")
        print("  hermes model              # Select provider and model")

    except subprocess.CalledProcessError as e:
        if sys.platform == "win32":
            print(f"⚠ Git update failed: {e}")
            print("→ Falling back to ZIP download...")
            print()
            _update_via_zip(args)
        else:
            print(f"✗ Update failed: {e}")
            sys.exit(1)
||||||| cf299e9a01
            sys.exit(1)

    # On Windows, git can fail with "unable to write loose object file: Invalid argument"
    # due to filesystem atomicity issues. Set the recommended workaround.
    if sys.platform == "win32" and git_dir.exists():
        subprocess.run(
            [
                "git",
                "-c",
                "windows.appendAtomically=false",
                "config",
                "windows.appendAtomically",
                "false",
            ],
            cwd=PROJECT_ROOT,
            check=False,
            capture_output=True,
        )

    # Build git command once — reused for fork detection and the update itself.
    git_cmd = ["git"]
    if sys.platform == "win32":
        git_cmd = ["git", "-c", "windows.appendAtomically=false"]

    # Discard npm lockfile churn before any stash/branch logic. npm rewrites
    # tracked package-lock.json files non-deterministically at install/build
    # time (platform-specific optional deps, ideallyInert annotations, etc.),
    # which is never an intentional edit on a managed install but leaves the
    # tree dirty — forcing an autostash on every update and making branch
    # switches fragile. Restoring them first lets the common case (only
    # lockfile churn) update with a clean tree.
    _discard_lockfile_churn(git_cmd, PROJECT_ROOT)

    # Detect if we're updating from a fork (before any branch logic)
    origin_url = _get_origin_url(git_cmd, PROJECT_ROOT)
    is_fork = _is_fork(origin_url)

    if is_fork:
        print("⚠ Updating from fork:")
        print(f"  {origin_url}")
        print()

    if use_zip_update:
        # ZIP-based update for Windows when git is broken
        try:
            _update_via_zip(args)
        finally:
            _resume_windows_gateways_after_update(_windows_gateway_resume)
        return

    # Fetch and pull
    try:

        # Resolve the target branch up front so the fetch can be scoped to it.
        # A bare `git fetch origin` pulls every ref, and this repo carries
        # thousands of auto-generated branches — an unscoped fetch can stall for
        # minutes on a non-single-branch checkout. Fetch only what we update
        # against.
        branch = _resolve_update_branch(args)

        print("→ Fetching updates...")
        fetch_result = subprocess.run(
            git_cmd + ["fetch", "origin", branch],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
        )
        if fetch_result.returncode != 0:
            stderr = fetch_result.stderr.strip()
            if "Could not resolve host" in stderr or "unable to access" in stderr:
                print("✗ Network error — cannot reach the remote repository.")
                print(f"  {stderr.splitlines()[0]}" if stderr else "")
            elif (
                "Authentication failed" in stderr or "could not read Username" in stderr
            ):
                print(
                    "✗ Authentication failed — check your git credentials or SSH key."
                )
            else:
                print(f"✗ Failed to fetch updates from origin.")
                if stderr:
                    print(f"  {stderr.splitlines()[0]}")
            sys.exit(1)

        # Get current branch (returns literal "HEAD" when detached)
        result = subprocess.run(
            git_cmd + ["rev-parse", "--abbrev-ref", "HEAD"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        current_branch = result.stdout.strip()

        # If user is on a different branch than the update target, switch
        # to the target. When the target is "main" this is the historical
        # "always update against main" behavior; for any other target it's
        # the same thing — get HEAD onto the requested branch first, then
        # fast-forward.
        if current_branch != branch:
            label = (
                "detached HEAD"
                if current_branch == "HEAD"
                else f"branch '{current_branch}'"
            )
            print(f"  ⚠ Currently on {label} — switching to {branch} for update...")
            # Stash before checkout so uncommitted work isn't lost
            auto_stash_ref = _stash_local_changes_if_needed(git_cmd, PROJECT_ROOT)
            checkout_result = subprocess.run(
                git_cmd + ["checkout", branch],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
            )
            if checkout_result.returncode != 0:
                # Local checkout doesn't have this branch yet. Try to set
                # it up as a tracking branch of origin/<branch>. This is
                # the common case when the requested branch exists upstream
                # but was never checked out locally.
                track_result = subprocess.run(
                    git_cmd + ["checkout", "-B", branch, f"origin/{branch}"],
                    cwd=PROJECT_ROOT,
                    capture_output=True,
                    text=True,
                )
                if track_result.returncode != 0:
                    # Restore the user's prior branch + stash before bailing
                    # so we don't leave them stranded in a weird state.
                    if auto_stash_ref is not None:
                        _restore_stashed_changes(
                            git_cmd,
                            PROJECT_ROOT,
                            auto_stash_ref,
                            prompt_user=False,
                            input_fn=gw_input_fn,
                        )
                    print(f"✗ Branch '{branch}' does not exist locally or on origin.")
                    if track_result.stderr.strip():
                        print(f"  {track_result.stderr.strip().splitlines()[0]}")
                    sys.exit(1)
        else:
            auto_stash_ref = _stash_local_changes_if_needed(git_cmd, PROJECT_ROOT)

        prompt_for_restore = (
            auto_stash_ref is not None
            and not assume_yes
            and (gateway_mode or (sys.stdin.isatty() and sys.stdout.isatty()))
        )

        # Check if there are updates
        result = subprocess.run(
            git_cmd + ["rev-list", f"HEAD..origin/{branch}", "--count"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        commit_count = int(result.stdout.strip())

        if commit_count == 0:
            _invalidate_update_cache()

            # Even if origin is up to date, the fork may be behind upstream
            if is_fork and branch == "main":
                _sync_with_upstream_if_needed(git_cmd, PROJECT_ROOT)

            # Restore stash and switch back to original branch if we moved
            if auto_stash_ref is not None:
                _restore_stashed_changes(
                    git_cmd,
                    PROJECT_ROOT,
                    auto_stash_ref,
                    prompt_user=prompt_for_restore,
                    input_fn=gw_input_fn,
                )
            if current_branch not in {branch, "HEAD"}:
                subprocess.run(
                    git_cmd + ["checkout", current_branch],
                    cwd=PROJECT_ROOT,
                    capture_output=True,
                    text=True,
                    check=False,
                )
            print("✓ Already up to date!")
            _resume_windows_gateways_after_update(_windows_gateway_resume)
            return

        print(f"→ Found {commit_count} new commit(s)")

        # Snapshot critical state (state.db, config, pairing JSONs, etc.)
        # before pulling so a user can recover if something goes wrong.
        # Issue #15733 reported missing pairing data after an update; even
        # though `git pull` can't touch $OPENAGENTS_HOME, this is cheap
        # belt-and-suspenders insurance and gives the user something to
        # restore from via `/snapshot list` / `/snapshot restore <id>`.
        pre_update_snapshot_id = None
        try:
            from openagents_cli.backup import create_quick_snapshot

            pre_update_snapshot_id = create_quick_snapshot(label="pre-update", keep=1)
            if pre_update_snapshot_id:
                print(f"  ✓ Pre-update snapshot: {pre_update_snapshot_id}")
        except Exception as exc:
            # Never let a snapshot failure block an update.
            logger.debug("Pre-update snapshot failed: %s", exc)

        print("→ Pulling updates...")
        update_succeeded = False
        # Capture the pre-pull SHA so we can auto-roll-back if the new code
        # has a syntax error in a critical-path file (PR #28452 incident:
        # orphan merge-conflict markers in openagents_cli/config.py bricked
        # every user who ran ``hermes update`` for the 7 minutes between
        # the bad commit and the fix landing).
        pre_pull_sha = _capture_head_sha(git_cmd, PROJECT_ROOT)
        try:
            pull_result = subprocess.run(
                git_cmd + ["pull", "--ff-only", "origin", branch],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
            )
            if pull_result.returncode != 0:
                # ff-only failed — local and remote have diverged (e.g. upstream
                # force-pushed or rebase).  Since local changes are already
                # stashed, reset to match the remote exactly.
                print(
                    "  ⚠ Fast-forward not possible (history diverged), resetting to match remote..."
                )
                reset_result = subprocess.run(
                    git_cmd + ["reset", "--hard", f"origin/{branch}"],
                    cwd=PROJECT_ROOT,
                    capture_output=True,
                    text=True,
                )
                if reset_result.returncode != 0:
                    print(f"✗ Failed to reset to origin/{branch}.")
                    if reset_result.stderr.strip():
                        print(f"  {reset_result.stderr.strip()}")
                    print(
                        f"  Try manually: git fetch origin && git reset --hard origin/{branch}"
                    )
                    sys.exit(1)

            # Post-pull syntax guard: validate critical-path files actually
            # parse before declaring the update successful. If a bad commit
            # made it through CI (e.g. admin-merge bypass of a failing
            # ruff check), this catches it on the user side and rolls back
            # so the CLI stays bootable. The user can then retry ``hermes
            # update`` later once a fix lands upstream.
            syntax_ok, failing_path, syntax_error = _validate_critical_files_syntax(
                PROJECT_ROOT
            )
            if not syntax_ok:
                print()
                print("✗ Pulled code has a syntax error in a critical file:")
                print(f"  {failing_path}")
                if syntax_error:
                    # py_compile errors can be multi-line; show the first
                    # ~6 lines so the user sees the actual SyntaxError text.
                    for line in str(syntax_error).splitlines()[:6]:
                        print(f"    {line}")
                if pre_pull_sha:
                    print()
                    print(f"→ Rolling back to {pre_pull_sha[:10]}...")
                    rollback_result = subprocess.run(
                        git_cmd + ["reset", "--hard", pre_pull_sha],
                        cwd=PROJECT_ROOT,
                        capture_output=True,
                        text=True,
                    )
                    if rollback_result.returncode == 0:
                        print("  ✓ Rollback complete — your install is unchanged.")
                        print("  Try ``hermes update`` again later once a fix lands.")
                    else:
                        print("  ✗ Rollback failed. Recover manually with:")
                        print(f"    cd {PROJECT_ROOT} && git reset --hard {pre_pull_sha}")
                        if rollback_result.stderr.strip():
                            print(f"    ({rollback_result.stderr.strip().splitlines()[0]})")
                else:
                    print()
                    print("  Could not capture pre-pull SHA — recover manually with:")
                    print(f"    cd {PROJECT_ROOT} && git reflog && git reset --hard <prev-sha>")
                sys.exit(1)

            update_succeeded = True
        finally:
            if auto_stash_ref is not None:
                # Don't attempt stash restore if the code update itself failed —
                # working tree is in an unknown state.
                if not update_succeeded:
                    print(
                        f"  ℹ️  Local changes preserved in stash (ref: {auto_stash_ref})"
                    )
                    print(f"  Restore manually with: git stash apply")
                elif discard_local_changes:
                    # Non-interactive update + user opted into discarding local
                    # source edits (updates.non_interactive_local_changes:
                    # discard). Throw the stash away instead of re-applying it.
                    _discard_stashed_changes(
                        git_cmd,
                        PROJECT_ROOT,
                        auto_stash_ref,
                    )
                else:
                    _restore_stashed_changes(
                        git_cmd,
                        PROJECT_ROOT,
                        auto_stash_ref,
                        prompt_user=prompt_for_restore,
                        input_fn=gw_input_fn,
                    )

        _invalidate_update_cache()

        # Clear stale .pyc bytecode cache — prevents ImportError on gateway
        # restart when updated source references names that didn't exist in
        # the old bytecode (e.g. get_openagents_home added to openagents_constants).
        removed = _clear_bytecode_cache(PROJECT_ROOT)
        if removed:
            print(
                f"  ✓ Cleared {removed} stale __pycache__ director{'y' if removed == 1 else 'ies'}"
            )

        # Fork upstream sync logic (only for main branch on forks)
        if is_fork and branch == "main":
            _sync_with_upstream_if_needed(git_cmd, PROJECT_ROOT)

        # Reinstall Python dependencies. Prefer .[all], but if one optional extra
        # breaks on this machine, keep base deps and reinstall the remaining extras
        # individually so update does not silently strip working capabilities.
        #
        # Drop the interrupted-install breadcrumb BEFORE touching the venv. If
        # the install is killed mid-flight (Ctrl-C, terminal close, WSL OOM),
        # the marker survives and the next ``hermes`` launch finishes the
        # install via ``_recover_from_interrupted_install``. Cleared only after
        # the install + core-dependency verification completes below.
        _write_update_incomplete_marker()
        print("→ Updating Python dependencies...")
        from openagents_cli.managed_uv import ensure_uv, update_managed_uv

        # Keep managed uv current — runs `uv self update` if we already have one.
        update_managed_uv()

        uv_bin = ensure_uv()

        pip_cmd = [sys.executable, "-m", "pip"]
        if not uv_bin:
            uv_bin = _ensure_uv_for_termux(pip_cmd)
        install_group = "all"

        if uv_bin:
            uv_env = {**os.environ, "VIRTUAL_ENV": str(PROJECT_ROOT / "venv")}
            if _is_termux_env(uv_env):
                uv_env.pop("PYTHONPATH", None)
                uv_env.pop("PYTHONHOME", None)
                install_group = "termux-all"
                print("  → Termux detected: using uv + curated termux-all optional profile...")
            if _is_termux_env(uv_env) and _is_android_python():
                print("  → Termux/Android detected: prebuilding psutil with Linux source path compatibility...")
                _install_psutil_android_compat([uv_bin, "pip"], env=uv_env)
            _install_python_dependencies_with_optional_fallback(
                [uv_bin, "pip"], env=uv_env, group=install_group
            )
        else:
            # Use sys.executable to explicitly call the venv's pip module,
            # avoiding PEP 668 'externally-managed-environment' errors on Debian/Ubuntu.
            # Some environments lose pip inside the venv; bootstrap it back with
            # ensurepip before trying the editable install.
            pip_cmd = [sys.executable, "-m", "pip"]
            try:
                subprocess.run(
                    pip_cmd + ["--version"],
                    cwd=PROJECT_ROOT,
                    check=True,
                    capture_output=True,
                )
            except subprocess.CalledProcessError:
                subprocess.run(
                    [sys.executable, "-m", "ensurepip", "--upgrade", "--default-pip"],
                    cwd=PROJECT_ROOT,
                    check=True,
                )
            if _is_termux_env():
                install_group = "termux-all"
                print("  → Termux detected: using curated termux-all optional profile...")
            if _is_termux_env() and _is_android_python():
                print("  → Termux/Android detected: prebuilding psutil with Linux source path compatibility...")
                _install_psutil_android_compat(pip_cmd)
            _install_python_dependencies_with_optional_fallback(pip_cmd, group=install_group)

        # Core Python deps installed AND verified (the fallback helper runs
        # _verify_core_dependencies_installed). Clear the interrupted-install
        # breadcrumb now — the remaining steps (lazy refresh, node deps, web
        # UI, desktop rebuild) are non-core and can't brick the venv.
        _clear_update_incomplete_marker()

        _refresh_active_lazy_features()

        _update_node_dependencies()
        _build_web_ui(PROJECT_ROOT / "web")

        # Rebuild the desktop app if the source tree changed since the last
        # build.  ``hermes desktop --build-only`` uses the content-hash stamp
        # internally, so this is effectively a no-op when nothing changed.
        # Only bother if the user has a desktop app installed (indicated by
        # an existing packaged executable or desktop dist); people who have
        # never run ``hermes desktop`` shouldn't be forced into a full
        # Electron build by ``hermes update``.
        desktop_dir = PROJECT_ROOT / "apps" / "desktop"
        has_desktop_app = _desktop_packaged_executable(desktop_dir) is not None or _desktop_dist_exists(desktop_dir)
        from openagents_constants import find_node_executable

        if (desktop_dir / "package.json").exists() and find_node_executable("npm") and has_desktop_app:
            print("→ Checking if desktop app needs rebuilding...")
            _desktop_build_cmd = [sys.executable, "-m", "openagents_cli.main", "desktop", "--build-only"]
            # Capture the (very loud) Electron/vite build output into
            # update.log instead of streaming it to the terminal. On the rare
            # nonzero exit, retry once after waiting again for the venv — this
            # covers a still-settling rebuild window the first wait didn't fully
            # catch — then surface the captured tail so the failure is
            # debuggable.
            build_result = _run_logged_subprocess(_desktop_build_cmd, cwd=PROJECT_ROOT)
            if build_result.returncode != 0:
                build_result = _run_logged_subprocess(_desktop_build_cmd, cwd=PROJECT_ROOT)
            if build_result.returncode != 0:
                print("  ⚠ Desktop build failed (non-fatal; run `hermes desktop` to retry)")
                tail = "\n".join((build_result.stdout or "").strip().splitlines()[-15:])
                if tail:
                    print(tail)
                from openagents_constants import display_openagents_home as _dhh
                print(f"  Full build log: {_dhh()}/logs/update.log")
            else:
                print("  ✓ Desktop app up to date")

        print()
        print("✓ Code updated!")

        # Seed the model-catalog disk cache from the freshly-pulled checkout.
        # The repo ships the canonical catalog at
        # website/static/api/model-catalog.json, and `git pull` just made it
        # current — so copy it straight over ~/.openagents/cache/model_catalog.json
        # instead of waiting on a network fetch (which can be bot-gated or hit a
        # Portal hiccup). Keeps the model picker's curated/free lists in sync
        # with the version the user just installed. Non-fatal on failure: the
        # normal network refresh still applies on the next picker open.
        try:
            from openagents_cli.model_catalog import seed_cache_from_checkout

            if seed_cache_from_checkout(PROJECT_ROOT):
                print("  ✓ Model catalog cache refreshed from checkout")
        except Exception as e:
            logger.debug("Model catalog seed during update failed: %s", e)

        # After git pull, source files on disk are newer than cached Python
        # modules in this process.  Reload openagents_constants so that any lazy
        # import executed below (skills sync, gateway restart) sees new
        # attributes like display_openagents_home() added since the last release.
        try:
            import importlib
            import openagents_constants as _hc

            importlib.reload(_hc)
        except Exception:
            pass  # non-fatal — worst case a lazy import fails gracefully

        # Sync bundled skills (copies new, updates changed, respects user deletions)
        try:
            from tools.skills_sync import sync_skills

            print()
            print("→ Syncing bundled skills...")
            result = sync_skills(quiet=True)
            if result["copied"]:
                print(f"  + {len(result['copied'])} new: {', '.join(result['copied'])}")
            if result.get("updated"):
                print(
                    f"  ↑ {len(result['updated'])} updated: {', '.join(result['updated'])}"
                )
            if result.get("user_modified"):
                print(f"  ~ {len(result['user_modified'])} user-modified (kept)")
                print(
                    "    → see them: hermes skills list-modified  "
                    "(diff/reset to resume updates)"
                )
            if result.get("cleaned"):
                print(f"  − {len(result['cleaned'])} removed from manifest")
            if not result["copied"] and not result.get("updated"):
                print("  ✓ Skills are up to date")
        except Exception as e:
            logger.debug("Skills sync during update failed: %s", e)

        # Sync bundled skills to all profiles (including the active one).
        # seed_profile_skills() uses subprocess with an explicit OPENAGENTS_HOME so
        # it is not affected by sync_skills()'s module-level OPENAGENTS_HOME cache,
        # which means the active profile is reliably synced regardless of whether
        # the caller's OPENAGENTS_HOME env var points at the default or a named profile.
        try:
            from openagents_cli.profiles import (
                list_profiles,
                seed_profile_skills,
            )

            all_profiles = list_profiles()
            if all_profiles:
                print()
                print("→ Syncing bundled skills to all profiles...")
                for p in all_profiles:
                    try:
                        r = seed_profile_skills(p.path, quiet=True)
                        if r and r.get("skipped_opt_out"):
                            status = "opted out (--no-skills)"
                        elif r:
                            copied = len(r.get("copied", []))
                            updated = len(r.get("updated", []))
                            modified = len(r.get("user_modified", []))
                            parts = []
                            if copied:
                                parts.append(f"+{copied} new")
                            if updated:
                                parts.append(f"↑{updated} updated")
                            if modified:
                                parts.append(f"~{modified} user-modified")
                            status = ", ".join(parts) if parts else "up to date"
                        else:
                            status = "sync failed"
                        print(f"  {p.name}: {status}")
                    except Exception as pe:
                        print(f"  {p.name}: error ({pe})")
        except Exception:
            pass  # profiles module not available or no profiles

        # Backfill per-profile .env files for profiles created before the
        # .env-seeding fix (#44792). Copies the default install's .env so
        # those profiles keep the credentials they were effectively using.
        try:
            from openagents_cli.profiles import backfill_profile_envs

            backfilled = backfill_profile_envs(quiet=True)
            if backfilled:
                print()
                print(
                    f"→ Seeded .env for {len(backfilled)} profile(s) "
                    f"(copied from default): {', '.join(backfilled)}"
                )
        except Exception:
            pass  # profiles module not available or no profiles

        # Sync Honcho host blocks to all profiles
        try:
            from plugins.memory.honcho.cli import sync_honcho_profiles_quiet

            synced = sync_honcho_profiles_quiet()
            if synced:
                print(f"\n-> Honcho: synced {synced} profile(s)")
        except Exception:
            pass  # honcho plugin not installed or not configured

        # Check for config migrations
        print()
        print("→ Checking configuration for new options...")

        from openagents_cli.config import (
            get_missing_env_vars,
            get_missing_config_fields,
            check_config_version,
            migrate_config,
        )

        missing_env = get_missing_env_vars(required_only=True)
        missing_config = get_missing_config_fields()
        current_ver, latest_ver = check_config_version()

        has_new_options = bool(missing_env or missing_config)
        version_bump_only = (
            not has_new_options and current_ver < latest_ver
        )
        needs_migration = has_new_options or current_ver < latest_ver

        if version_bump_only:
            # Nothing for the user to fill in — only the config format version
            # changed (new defaults already merge in transparently). Asking
            # "configure new options now?" here is misleading: saying yes just
            # bumps the version and looks like a no-op (issue: ScottFive /
            # Tt2021). Apply it silently and say what actually happened.
            print()
            print(
                f"  ℹ Updating config format (v{current_ver} → v{latest_ver})…"
            )
            try:
                migrate_config(interactive=False, quiet=True)
                print("  ✓ Config format updated (no new settings to configure)")
            except Exception as _mig_err:
                print(f"  ⚠️  Config format update failed: {_mig_err}")
                print("     Run 'hermes config migrate' to retry.")
        elif needs_migration:
            print()
            # Show WHAT changed, not just a count, so the user can make an
            # informed yes/no decision (previously the prompt named nothing).
            def _print_items(items, label, key, fallback_key=None):
                if not items:
                    return
                print(f"  {label}:")
                shown = items[:8]
                for it in shown:
                    if isinstance(it, dict):
                        name = it.get(key) or (fallback_key and it.get(fallback_key)) or "?"
                        desc = (it.get("description") or "").strip()
                    else:
                        # Defensive: some callers/mocks pass bare name strings.
                        name = str(it)
                        desc = ""
                    if desc:
                        print(f"      • {name} — {desc}")
                    else:
                        print(f"      • {name}")
                extra = len(items) - len(shown)
                if extra > 0:
                    print(f"      … and {extra} more")

            if missing_env:
                print(
                    f"  ⚠️  {len(missing_env)} new required setting(s) need configuration"
                )
                _print_items(missing_env, "New settings", "name")
            if missing_config:
                print(f"  ℹ️  {len(missing_config)} new config option(s) available")
                _print_items(missing_config, "New options", "key")

            print()
            if assume_yes:
                print(
                    "  ℹ --yes: auto-applying config migration (skipping API-key prompts)."
                )
                response = "y"
            elif gateway_mode:
                response = (
                    _gateway_prompt(
                        "Would you like to configure new options now? [Y/n]", "n"
                    )
                    .strip()
                    .lower()
                )
            elif not (sys.stdin.isatty() and sys.stdout.isatty()):
                print("  ℹ Non-interactive session — applying safe config migrations.")
                response = "auto"
            else:
                try:
                    response = (
                        input("Would you like to configure them now? [Y/n]: ")
                        .strip()
                        .lower()
                    )
                except EOFError:
                    response = "n"

            if response in {"", "y", "yes", "auto"}:
                print()
                # Gateway mode, --yes, and non-interactive update contexts
                # (dashboard / web server actions) cannot prompt for API keys.
                # Still run the non-interactive migration pass before restarting
                # so new default config fields and version bumps are written
                # before the freshly updated gateway validates config at startup.
                interactive_migration = not (
                    gateway_mode or assume_yes or response == "auto"
                )
                results = migrate_config(interactive=interactive_migration, quiet=False)

                if results["env_added"] or results["config_added"]:
                    print()
                    print("✓ Configuration updated!")
                if (gateway_mode or assume_yes or response == "auto") and missing_env:
                    print("  ℹ API keys require manual entry: hermes config migrate")
            else:
                print()
                print("Skipped. Run 'hermes config migrate' later to configure.")
        else:
            print("  ✓ Configuration is up to date")

        # Safety net: config-version migrations have been observed to leave
        # cron/jobs.json valid-but-empty, silently dropping every scheduled
        # job (issue #34600). The desktop scheduler can also overwrite with
        # its own small set, causing partial loss (issue #52144). If the
        # live file now has fewer jobs than the pre-update snapshot, restore
        # it and warn loudly.
        try:
            from openagents_cli.backup import restore_cron_jobs_if_emptied

            cron_restore = restore_cron_jobs_if_emptied(pre_update_snapshot_id)
            if cron_restore:
                print()
                print(
                    "  ⚠️  cron/jobs.json lost jobs during this update — "
                    f"restored {cron_restore['job_count']} job(s) from "
                    f"pre-update snapshot {cron_restore['snapshot_id']}."
                )
        except Exception as exc:
            # Never let the cron safety net break an otherwise-good update.
            logger.debug("Cron jobs auto-restore check failed: %s", exc)

        print()
        print("✓ Update complete!")

        # Curator first-run heads-up. Only prints when curator is enabled AND
        # has never run — i.e. the window where the ticker would otherwise
        # have fired against a fresh skill library. Kept silent on steady
        # state so we don't nag.
        try:
            _print_curator_first_run_notice()
        except Exception as e:
            logger.debug("Curator first-run notice failed: %s", e)

        # Most-recent curator run notice — show-once per run. Surfaces the
        # rename map (`old-name → umbrella`) on the high-attention update
        # surface so users learn about consolidations without having to
        # check `hermes curator status`. Self-stamps after printing so it
        # never repeats for the same run.
        try:
            _print_curator_recent_run_notice()
        except Exception as e:
            logger.debug("Curator recent-run notice failed: %s", e)

        # Repair RHEL-family root installs where /usr/local/bin isn't on PATH
        # for non-login interactive shells.  No-op on every other platform.
        try:
            _ensure_fhs_path_guard()
        except Exception as e:
            logger.debug("FHS PATH guard check failed: %s", e)

        # Refresh the cua-driver binary used by the Computer Use toolset.
        # The upstream installer is gated on supported platforms and on the
        # binary already being on PATH, so this is a no-op for users who
        # don't have it. Tying the refresh to ``hermes update`` gives users a
        # predictable cadence (matches when they pull new agent code) without
        # adding startup latency or a per-launch GitHub API call.
        try:
            if sys.platform in ("darwin", "win32", "linux") and shutil.which("cua-driver"):
                from openagents_cli.tools_config import install_cua_driver

                print()
                print("→ Refreshing cua-driver (Computer Use)...")
                install_cua_driver(upgrade=True)
        except Exception as e:
            logger.debug("cua-driver refresh failed: %s", e)

        # Write exit code *before* the gateway restart attempt.
        # When running as ``hermes update --gateway`` (spawned by the gateway's
        # /update command), this process lives inside the gateway's systemd
        # cgroup.  A graceful SIGUSR1 restart keeps the drain loop alive long
        # enough for the exit-code marker to be written below, but the
        # fallback ``systemctl restart`` path (see below) kills everything in
        # the cgroup (KillMode=mixed → SIGKILL to remaining processes),
        # including us and the wrapping bash shell.  The shell never reaches
        # its ``printf $status > .update_exit_code`` epilogue, so the
        # exit-code marker file would never be created.  The new gateway's
        # update watcher would then poll for 30 minutes and send a spurious
        # timeout message.
        #
        # Writing the marker here — after git pull + pip install succeed but
        # before we attempt the restart — ensures the new gateway sees it
        # regardless of how we die.
        if gateway_mode:
            _exit_code_path = get_openagents_home() / ".update_exit_code"
            try:
                _exit_code_path.write_text("0")
            except OSError:
                pass

        # Auto-restart ALL gateways after update.
        # The code update (git pull) is shared across all profiles, so every
        # running gateway needs restarting to pick up the new code.
        try:
            from openagents_cli.gateway import (
                is_macos,
                supports_systemd_services,
                _ensure_user_systemd_env,
                find_gateway_pids,
                find_profile_gateway_processes,
                launch_detached_profile_gateway_restart,
                _get_service_pids,
                _graceful_restart_via_sigusr1,
                _wait_for_gateway_exit,
            )
            import signal as _signal

            def _wait_for_service_active(
                scope_cmd_: list,
                svc_name_: str,
                timeout: float = 10.0,
            ) -> bool:
                """Poll ``systemctl is-active`` until the unit reports active.

                systemd's Stopped -> Started transition after a graceful exit
                (or a hard restart) is not instantaneous; a one-shot check
                races that window and falsely reports the unit as down.
                Poll every 0.5s up to ``timeout`` seconds before giving up.
                """
                deadline = _time.monotonic() + max(timeout, 0.5)
                while True:
                    try:
                        _verify = subprocess.run(
                            scope_cmd_ + ["is-active", svc_name_],
                            capture_output=True,
                            text=True,
                            timeout=5,
                        )
                        if _verify.stdout.strip() == "active":
                            return True
                    except (FileNotFoundError, subprocess.TimeoutExpired):
                        pass
                    if _time.monotonic() >= deadline:
                        return False
                    _time.sleep(0.5)

            def _service_restart_sec(
                scope_cmd_: list,
                svc_name_: str,
                default: float = 0.0,
            ) -> float:
                """Read the unit's ``RestartUSec`` (RestartSec) in seconds.

                After a graceful exit-75, systemd waits ``RestartSec`` before
                respawning the unit.  Callers that poll for ``is-active``
                must use a timeout >= ``RestartSec`` + transition slack, or
                they'll give up *during* the cooldown window and wrongly
                conclude the unit didn't relaunch.
                """
                try:
                    _show = subprocess.run(
                        scope_cmd_
                        + [
                            "show",
                            svc_name_,
                            "--property=RestartUSec",
                            "--value",
                        ],
                        capture_output=True,
                        text=True,
                        timeout=5,
                    )
                except (FileNotFoundError, subprocess.TimeoutExpired):
                    return default
                raw = (_show.stdout or "").strip()
                # systemd emits values like "30s", "100ms", "1min 30s", or
                # "infinity".  Parse conservatively; on any miss return default.
                if not raw or raw == "infinity":
                    return default
                total = 0.0
                matched = False
                for part in raw.split():
                    for _suf, _mult in (
                        ("ms", 0.001),
                        ("us", 0.000001),
                        ("min", 60.0),
                        ("s", 1.0),
                    ):
                        if part.endswith(_suf):
                            try:
                                total += float(part[: -len(_suf)]) * _mult
                                matched = True
                            except ValueError:
                                pass
                            break
                return total if matched else default

            _manage_cmd_cache: dict = {}

            def _resolve_manage_cmd(scope_: str, scope_cmd_: list, svc_name_: str):
                """Resolve the command prefix for manage-units operations.

                Read-only systemctl calls (``is-active``, ``show``,
                ``list-units``) work unprivileged, but manage-units verbs
                (``reset-failed``, ``start``, ``restart``) on a *system*
                service trigger a polkit ``org.freedesktop.systemd1.manage-units``
                authentication prompt when run as a non-root user.  That
                interactive prompt runs inside our captured subprocess with a
                10-15s timeout — the user sees the prompt flash and "exit
                directly" before they can answer, and the resulting
                TimeoutExpired used to be swallowed silently.

                Strategy: if root, plain systemctl.  If not root, try
                non-interactive sudo (``sudo -n``) — first a blanket probe,
                then a targeted ``systemctl reset-failed`` probe so a
                least-privilege sudoers entry scoped to
                ``systemctl ... openagents-gateway*`` also qualifies
                (``reset-failed`` is an idempotent no-op we run before every
                privileged restart anyway).  If neither works, return None —
                the caller must SKIP the restart (without draining the
                gateway first!) and tell the user how to restart manually.
                ``--no-ask-password`` guarantees polkit can never hang a
                captured subprocess on this path.
                """
                if scope_ in _manage_cmd_cache:
                    return _manage_cmd_cache[scope_]
                cmd = scope_cmd_ + ["--no-ask-password"]
                if (
                    scope_ == "system"
                    and hasattr(os, "geteuid")
                    and os.geteuid() != 0  # windows-footgun: ok — systemd path, Linux-only
                ):
                    sudo_cmd = ["sudo", "-n"] + scope_cmd_ + ["--no-ask-password"]
                    sudo_ok = False
                    try:
                        _probe = subprocess.run(
                            ["sudo", "-n", "true"],
                            capture_output=True,
                            timeout=5,
                        )
                        sudo_ok = _probe.returncode == 0
                        if not sudo_ok:
                            # Blanket sudo refused — a targeted sudoers entry
                            # (NOPASSWD for systemctl ... openagents-gateway*)
                            # may still allow the exact commands we need.
                            _probe = subprocess.run(
                                sudo_cmd + ["reset-failed", svc_name_],
                                capture_output=True,
                                timeout=5,
                            )
                            sudo_ok = _probe.returncode == 0
                    except (FileNotFoundError, subprocess.TimeoutExpired):
                        sudo_ok = False
                    cmd = sudo_cmd if sudo_ok else None
                _manage_cmd_cache[scope_] = cmd
                return cmd

            # Drain budget for graceful SIGUSR1 restarts.  The gateway drains
            # for up to ``agent.restart_drain_timeout`` (default 60s) before
            # exiting with code 75; we wait slightly longer so the drain
            # completes before we fall back to a hard restart.  On older
            # systemd units without SIGUSR1 wiring this wait just times out
            # and we fall back to ``systemctl restart`` (the old behaviour).
            try:
                from openagents_constants import (
                    DEFAULT_GATEWAY_RESTART_DRAIN_TIMEOUT as _DEFAULT_DRAIN,
                )
            except Exception:
                _DEFAULT_DRAIN = 60.0
            _cfg_drain = None
            try:
                from openagents_cli.config import load_config

                _cfg_agent = load_config().get("agent") or {}
                _cfg_drain = _cfg_agent.get("restart_drain_timeout")
            except Exception:
                pass
            try:
                _drain_budget = (
                    float(_cfg_drain)
                    if _cfg_drain is not None
                    else float(_DEFAULT_DRAIN)
                )
            except (TypeError, ValueError):
                _drain_budget = float(_DEFAULT_DRAIN)
            # Add a 15s margin so the drain loop + final exit finish before
            # we escalate to ``systemctl restart`` / SIGTERM.
            _drain_budget = max(_drain_budget, 30.0) + 15.0

            restarted_services = []
            killed_pids = set()
            relaunched_profiles = []

            # --- Systemd services (Linux) ---
            # Discover all openagents-gateway* units (default + profiles)
            if supports_systemd_services():
                try:
                    _ensure_user_systemd_env()
                except Exception:
                    pass

                for scope, scope_cmd in [
                    ("user", ["systemctl", "--user"]),
                    ("system", ["systemctl"]),
                ]:
                    try:
                        result = subprocess.run(
                            scope_cmd
                            + [
                                "list-units",
                                "openagents-gateway*",
                                "--plain",
                                "--no-legend",
                                "--no-pager",
                            ],
                            capture_output=True,
                            text=True,
                            timeout=10,
                        )
                        for line in result.stdout.strip().splitlines():
                            parts = line.split()
                            if not parts:
                                continue
                            unit = parts[
                                0
                            ]  # e.g. openagents-gateway.service or openagents-gateway-coder.service
                            if not unit.endswith(".service"):
                                continue
                            svc_name = unit.removesuffix(".service")
                            # Check if active
                            check = subprocess.run(
                                scope_cmd + ["is-active", svc_name],
                                capture_output=True,
                                text=True,
                                timeout=5,
                            )
                            if check.stdout.strip() != "active":
                                continue

                            # Resolve how we may run manage-units verbs
                            # (reset-failed/start/restart) for this scope.
                            # None ⇒ no non-interactive privilege path; we
                            # must avoid those verbs entirely or polkit will
                            # throw an interactive auth prompt inside our
                            # captured 10-15s subprocess (the user sees it
                            # flash and "exit directly" — reported June 2026).
                            _manage_cmd = _resolve_manage_cmd(
                                scope, scope_cmd, svc_name
                            )

                            # Prefer a graceful SIGUSR1 restart so in-flight
                            # agent runs drain instead of being SIGKILLed.
                            # The gateway's SIGUSR1 handler calls
                            # request_restart(via_service=True) → drain →
                            # exit; systemd's Restart=always respawns the unit.
                            _main_pid = 0
                            try:
                                _show = subprocess.run(
                                    scope_cmd
                                    + [
                                        "show",
                                        svc_name,
                                        "--property=MainPID",
                                        "--value",
                                    ],
                                    capture_output=True,
                                    text=True,
                                    timeout=5,
                                )
                                _main_pid = int((_show.stdout or "").strip() or 0)
                            except (
                                ValueError,
                                subprocess.TimeoutExpired,
                                FileNotFoundError,
                            ):
                                _main_pid = 0

                            _graceful_ok = False
                            if _main_pid > 0:
                                print(
                                    f"  → {svc_name}: draining (up to {int(_drain_budget)}s)..."
                                )
                                _graceful_ok = _graceful_restart_via_sigusr1(
                                    _main_pid,
                                    drain_timeout=_drain_budget,
                                )

                            if _graceful_ok:
                                # Gateway exited after a planned restart.
                                # ``Restart=always`` means systemd WILL respawn
                                # the unit — but only after
                                # ``RestartSec`` (default 60s on our unit
                                # file). That 60s wait is a crash-loop guard,
                                # and is the right default when the gateway
                                # dies unexpectedly. For a voluntary restart
                                # on update, it's dead time the user watches.
                                #
                                # Shortcut it: ``reset-failed`` + ``start``
                                # skips RestartSec entirely (we're manually
                                # initiating the unit, not waiting for
                                # systemd's auto-restart logic). Takes about
                                # as long as the process takes to come up
                                # (~1-3s on a warm box).
                                #
                                # If the unit is already active because
                                # RestartSec elapsed while we were draining,
                                # ``start`` is a no-op and we fall through to
                                # the poll below. Either way we collapse the
                                # 60s+ delay to a ~5s one.
                                #
                                # The shortcut needs manage-units privileges.
                                # Without them (system service, non-root, no
                                # passwordless sudo) skip it — systemd's own
                                # auto-restart still relaunches the unit after
                                # RestartSec, no privileges required.
                                if _manage_cmd is not None:
                                    subprocess.run(
                                        _manage_cmd + ["reset-failed", svc_name],
                                        capture_output=True,
                                        text=True,
                                        timeout=10,
                                    )
                                    subprocess.run(
                                        _manage_cmd + ["start", svc_name],
                                        capture_output=True,
                                        text=True,
                                        timeout=15,
                                    )
                                    # Short poll: the gateway should be up
                                    # within a few seconds now that we
                                    # bypassed RestartSec.
                                    if _wait_for_service_active(
                                        scope_cmd,
                                        svc_name,
                                        timeout=10.0,
                                    ):
                                        restarted_services.append(svc_name)
                                        continue
                                # Passive poll: systemd's auto-restart fires
                                # after RestartSec regardless of privileges.
                                # This is the primary path when _manage_cmd is
                                # None, and the fallback when the explicit
                                # start didn't take.
                                _restart_sec = _service_restart_sec(
                                    scope_cmd,
                                    svc_name,
                                    default=0.0,
                                )
                                _post_drain_timeout = max(
                                    10.0,
                                    _restart_sec + 10.0,
                                )
                                if _manage_cmd is None and _restart_sec > 5.0:
                                    print(
                                        f"  → {svc_name}: waiting for systemd "
                                        f"auto-restart (~{int(_restart_sec)}s; "
                                        "no root for an immediate restart)..."
                                    )
                                if _wait_for_service_active(
                                    scope_cmd,
                                    svc_name,
                                    timeout=_post_drain_timeout,
                                ):
                                    restarted_services.append(svc_name)
                                    continue
                                # Process exited but wasn't respawned (older
                                # unit without Restart=on-failure or
                                # RestartForceExitStatus=75).  Fall through
                                # to systemctl start/restart.
                                print(
                                    f"  ⚠ {svc_name} drained but didn't relaunch — forcing restart"
                                )

                            # Forcing a restart requires manage-units
                            # privileges.  Without a non-interactive path,
                            # running systemctl here would spawn a polkit
                            # auth prompt inside a captured 10-15s subprocess
                            # — it flashes and dies before the user can
                            # answer.  Skip with clear instructions instead.
                            if _manage_cmd is None:
                                print(
                                    f"  ⚠ {svc_name} is a system service and restarting it needs root.\n"
                                    f"    Restart it manually to load the new version:\n"
                                    f"      sudo systemctl restart {svc_name}\n"
                                    f"    To let `hermes update` restart it automatically, allow\n"
                                    f"    passwordless sudo for systemctl, or run updates with sudo."
                                )
                                continue

                            # Fallback: blunt systemctl restart.  This is
                            # what the old code always did; we get here only
                            # when the graceful path failed (unit missing
                            # SIGUSR1 wiring, drain exceeded the budget,
                            # restart-policy mismatch).
                            #
                            # Always `reset-failed` first.  If systemd's own
                            # auto-restart attempts already parked the unit
                            # in a failed state (transient CHDIR / OOM /
                            # filesystem race after our drain + exit-75),
                            # a plain `systemctl restart` can wedge against
                            # the RestartSec backoff and leave the unit
                            # dead.  Clearing the failed state first makes
                            # the restart idempotent.  Mirrors the recovery
                            # path in `hermes gateway restart`
                            # (`systemd_restart()`) as of PR #20949.
                            subprocess.run(
                                _manage_cmd + ["reset-failed", svc_name],
                                capture_output=True,
                                text=True,
                                timeout=10,
                            )
                            restart = subprocess.run(
                                _manage_cmd + ["restart", svc_name],
                                capture_output=True,
                                text=True,
                                timeout=15,
                            )
                            if restart.returncode == 0:
                                # Verify the service actually survived the
                                # restart.  systemctl restart returns 0 even
                                # if the new process crashes immediately.
                                if _wait_for_service_active(
                                    scope_cmd,
                                    svc_name,
                                    timeout=10.0,
                                ):
                                    restarted_services.append(svc_name)
                                else:
                                    # Retry once — transient startup failures
                                    # (stale module cache, import race) often
                                    # resolve on the second attempt.  Again
                                    # clear any failed state first so the
                                    # retry isn't blocked by the previous
                                    # crash.
                                    print(
                                        f"  ⚠ {svc_name} died after restart, retrying..."
                                    )
                                    subprocess.run(
                                        _manage_cmd + ["reset-failed", svc_name],
                                        capture_output=True,
                                        text=True,
                                        timeout=10,
                                    )
                                    subprocess.run(
                                        _manage_cmd + ["restart", svc_name],
                                        capture_output=True,
                                        text=True,
                                        timeout=15,
                                    )
                                    if _wait_for_service_active(
                                        scope_cmd,
                                        svc_name,
                                        timeout=10.0,
                                    ):
                                        restarted_services.append(svc_name)
                                        print(f"  ✓ {svc_name} recovered on retry")
                                    else:
                                        _scope_flag = "--user " if scope == "user" else ""
                                        _sudo_hint = "sudo " if scope == "system" else ""
                                        print(
                                            f"  ✗ {svc_name} failed to stay running after restart.\n"
                                            f"    Check logs: {_sudo_hint}journalctl {_scope_flag}-u {svc_name} --since '2 min ago'\n"
                                            f"    Recover manually:\n"
                                            f"      {_sudo_hint}systemctl {_scope_flag}reset-failed {svc_name}\n"
                                            f"      {_sudo_hint}systemctl {_scope_flag}restart {svc_name}"
                                        )
                            else:
                                print(
                                    f"  ⚠ Failed to restart {svc_name}: {restart.stderr.strip()}"
                                )
                    except FileNotFoundError:
                        pass
                    except subprocess.TimeoutExpired as exc:
                        # Don't swallow this silently — a wedged systemctl
                        # call here used to make the whole restart phase
                        # vanish with no output (June 2026 report).
                        print(
                            f"  ⚠ systemctl timed out during the {scope}-scope "
                            f"gateway restart ({exc.cmd if exc.cmd else 'unknown command'}). "
                            f"Check the gateway with: hermes gateway status"
                        )

            # --- Launchd services (macOS) ---
            if is_macos():
                try:
                    from openagents_cli.gateway import (
                        launchd_restart,
                        get_launchd_label,
                        get_launchd_plist_path,
                    )

                    plist_path = get_launchd_plist_path()
                    if plist_path.exists():
                        check = subprocess.run(
                            ["launchctl", "list", get_launchd_label()],
                            capture_output=True,
                            text=True,
                            timeout=5,
                        )
                        if check.returncode == 0:
                            try:
                                launchd_restart()
                                restarted_services.append(get_launchd_label())
                            except subprocess.CalledProcessError as e:
                                stderr = (getattr(e, "stderr", "") or "").strip()
                                print(f"  ⚠ Gateway restart failed: {stderr}")
                except (FileNotFoundError, subprocess.TimeoutExpired, ImportError):
                    pass

            # --- Manual (non-service) gateways ---
            # Kill any remaining gateway processes not managed by a service.
            # Exclude PIDs that belong to just-restarted services so we don't
            # immediately kill the process that systemd/launchd just spawned.
            service_pids = _get_service_pids()
            manual_pids = find_gateway_pids(
                exclude_pids=service_pids, all_profiles=True
            )
            profile_processes = {
                proc.pid: proc
                for proc in find_profile_gateway_processes(exclude_pids=service_pids)
                if proc.pid in manual_pids
            }
            for pid, proc in profile_processes.items():
                if not launch_detached_profile_gateway_restart(proc.profile, pid):
                    continue
                # Prefer a graceful SIGUSR1 drain so in-flight agent runs
                # finish before the watcher respawns the gateway.  If the
                # gateway doesn't support SIGUSR1 or doesn't exit within
                # the drain budget, fall back to SIGTERM — the watcher
                # still sees the exit and relaunches either way.
                # Announce the drain first: this wait can hold for the full
                # budget per gateway with no other output, and on surfaces
                # that stream update progress (the desktop updater most of
                # all) the silence reads as a hung update (#44515).
                print(
                    f"  → {proc.profile}: draining gateway PID {pid} "
                    f"(up to {int(_drain_budget)}s)..."
                )
                drained = _graceful_restart_via_sigusr1(
                    pid,
                    drain_timeout=_drain_budget,
                )
                if not drained:
                    try:
                        os.kill(pid, _signal.SIGTERM)
                    except (ProcessLookupError, PermissionError):
                        pass
                # Wait for the old process to fully exit before the watcher
                # spawns the new gateway.  Telegram holds the previous
                # getUpdates long-poll session open on its servers for up to
                # ~30s after the client disconnects.  If the new gateway
                # connects before that window expires it receives a 409
                # Conflict, which _handle_polling_conflict() recovers from
                # via back-off retries — but a brief wait here reduces the
                # chance of hitting that path at all, especially on fast
                # machines where the watcher loop restarts in < 1s.
                # We wait up to 5s for the process to exit (the OS-level
                # close, not the Telegram server-side expiry), then let the
                # watcher take over.  The Telegram adapter's retry logic
                # handles any remaining 409s if the server session is still
                # live when the new gateway polls.
                _wait_for_gateway_exit(timeout=5.0, force_after=None)
                killed_pids.add(pid)
                relaunched_profiles.append(proc.profile)

            for pid in manual_pids:
                if pid in profile_processes:
                    continue
                try:
                    os.kill(pid, _signal.SIGTERM)
                    killed_pids.add(pid)
                except (ProcessLookupError, PermissionError):
                    pass

            if restarted_services or killed_pids:
                print()
                for svc in restarted_services:
                    print(f"  ✓ Restarted {svc}")
                if relaunched_profiles:
                    names = ", ".join(relaunched_profiles)
                    print(f"  ✓ Restarting manual gateway profile(s): {names}")
                unmapped_count = len(killed_pids) - len(relaunched_profiles)
                if unmapped_count:
                    print(f"  → Stopped {unmapped_count} manual gateway process(es)")
                    print("    Restart manually: hermes gateway run")
                    if unmapped_count > 1:
                        print(
                            "    (or: hermes -p <profile> gateway run  for each profile)"
                        )

            if not restarted_services and not killed_pids:
                # No gateways were running — nothing to do
                pass

            # --- Post-restart survivor sweep -----------------------------
            # Issue #17648: some gateways ignore SIGTERM (stuck drain,
            # blocked I/O, PID dead but zombie).  The detached profile
            # watchers wait 120s for the old PID to exit — if it never
            # does, no respawn happens and the user keeps hitting
            # ImportError against a stale sys.modules.  Give the
            # graceful paths a brief window to complete, then SIGKILL
            # any remaining pre-update PIDs so the watcher / service
            # manager can relaunch with fresh code.
            try:
                _time.sleep(3.0)
                _service_pids_after = _get_service_pids()
                _surviving = find_gateway_pids(
                    exclude_pids=_service_pids_after,
                    all_profiles=True,
                )
                # Scope to PIDs we already tried to kill during this
                # update (killed_pids).  Anything new is a gateway that
                # started AFTER our restart attempt — respecting user
                # intent, we don't kill those.
                _stuck = [pid for pid in _surviving if pid in killed_pids]
                if _stuck:
                    print()
                    print(
                        f"  ⚠ {len(_stuck)} gateway process(es) ignored SIGTERM — force-killing"
                    )
                    from gateway.status import terminate_pid as _terminate_pid
                    for pid in _stuck:
                        try:
                            # Routes through taskkill /T /F on Windows,
                            # SIGKILL on POSIX — _signal.SIGKILL doesn't
                            # exist on Windows so the old raw os.kill call
                            # used to crash the entire update path.
                            _terminate_pid(pid, force=True)
                        except (ProcessLookupError, PermissionError, OSError):
                            pass
                    # Give the OS a beat to reap the processes so the
                    # watchers see them exit and respawn.
                    _time.sleep(1.5)
            except Exception as _sweep_exc:
                logger.debug("Post-restart survivor sweep failed: %s", _sweep_exc)

        except Exception as e:
            logger.debug("Gateway restart during update failed: %s", e)

        _resume_windows_gateways_after_update(_windows_gateway_resume)

        # Warn if legacy OpenAgents gateway unit files are still installed.
        # When both hermes.service (from a pre-rename install) and the
        # current openagents-gateway.service are enabled, they SIGTERM-fight
        # for the same bot token (see PR #11909). Flagging here means
        # every `hermes update` surfaces the issue until the user migrates.
        try:
            from openagents_cli.gateway import (
                has_legacy_hermes_units,
                _find_legacy_hermes_units,
                supports_systemd_services,
            )

            if supports_systemd_services() and has_legacy_hermes_units():
                print()
                print("⚠ Legacy OpenAgents gateway unit(s) detected:")
                for name, path, is_sys in _find_legacy_hermes_units():
                    scope = "system" if is_sys else "user"
                    print(f"    {path}  ({scope} scope)")
                print()
                print("  These pre-rename units (hermes.service) fight the current")
                print("  openagents-gateway.service for the bot token and cause SIGTERM")
                print("  flap loops. Remove them with:")
                print()
                print("    hermes gateway migrate-legacy")
                print()
                print("  (add `sudo` if any are in system scope)")
        except Exception as e:
            logger.debug("Legacy unit check during update failed: %s", e)

        # Kill stale dashboard processes — the dashboard has no service
        # manager, so leaving it alive after a code update produces a
        # silent frontend/backend mismatch.  We can't auto-restart it
        # (no saved launch args) but we can stop it, and a hint is
        # printed for the user to re-launch.
        _kill_stale_dashboard_processes()

        print()
        print("Tip: You can now select a provider and model:")
        print("  hermes model              # Select provider and model")

    except subprocess.CalledProcessError as e:
        if sys.platform == "win32":
            print(f"⚠ Git update failed: {e}")
            print("→ Falling back to ZIP download...")
            print()
            _update_via_zip(args)
        else:
            print(f"✗ Update failed: {e}")
            sys.exit(1)
=======
            sys.stdout.flush()
            sys.stderr.flush()
            os._exit(_update_handoff_exit_code)
>>>>>>> rb/tag


def _coalesce_session_name_args(argv: list) -> list:
    """Join unquoted multi-word session names after -c/--continue and -r/--resume.

    ``hermes -c Pokemon Agent Dev`` → ``['-c', 'Pokemon Agent Dev']``; tokens
    are collected until the next flag (``-*``) or known top-level subcommand.
    """
    _SUBCOMMANDS = {
        "chat", "model", "gateway", "setup", "whatsapp", "whatsapp-cloud", "login", "logout",
        "auth", "status", "cron", "doctor", "config", "pairing", "skills", "tools", "mcp",
        "sessions", "insights", "update", "uninstall", "profile", "dashboard", "serve",
        "desktop", "gui", "honcho", "claw", "plugins", "security", "acp", "webhook", "peer",
        "memory", "dump", "debug", "backup", "import", "completion", "logs", "usage",
    }
    _SESSION_FLAGS = {"-c", "--continue", "-r", "--resume"}

    result = []
    i = 0
    while i < len(argv):
        token = argv[i]
        if token in _SESSION_FLAGS:
            result.append(token)
            i += 1
            # Collect subsequent non-flag, non-subcommand tokens as one name
            parts: list = []
            while (
                i < len(argv)
                and not argv[i].startswith("-")
                and argv[i] not in _SUBCOMMANDS
            ):
                parts.append(argv[i])
                i += 1
            if parts:
                result.append(" ".join(parts))
        else:
            result.append(token)
            i += 1
    return result


from openagents_cli.profile_cmd import cmd_profile


def _dashboard_lifecycle_flags(args, token_file) -> None:
    """--status / --stop: report or kill running dashboards and exit (no deps needed)."""
    if token_file and (getattr(args, "status", False) or getattr(args, "stop", False)):
        raise SystemExit("--ssh-session-token-file cannot be used with --status or --stop")
    if getattr(args, "status", False):
        _report_dashboard_status()
        sys.exit(0)  # status is informational, always 0
    if getattr(args, "stop", False):
        # Scoped to the invoking home (`-p` applied by _apply_profile_override): another
        # install's or profile's backend on this machine is never a target (#113978).
        from openagents_constants import get_openagents_home

        own_home = str(get_openagents_home())
        if not _find_stale_dashboard_pids(scope_home=own_home):
            print("No hermes dashboard processes running for this profile.")
            sys.exit(0)
        # Reuse the same SIGTERM-grace-SIGKILL path used after `hermes update`;
        # it prints outcomes itself. Exit 1 only if a pid was unkillable — judged
        # from the kill result, not a re-scan: a launchd KeepAlive job respawns
        # its backend on a fresh PID, which is not a failed stop.
        from openagents_cli.dashboard_procs import _kill_stale_dashboard_processes

        result = _kill_stale_dashboard_processes(reason="requested via --stop", scope_home=own_home)
        sys.exit(1 if result["failed"] else 0)


def _dashboard_validate_serve_args(args, headless_backend, token_file):
    """Headless-serve argument checks -> ssh_owner_nonce (or None)."""
    # `hermes serve` is headless/non-interactive: fail closed on a corrupt
    # config.yaml instead of silently starting on defaults where provider
    # auto-detection can adopt unnamed .env credentials (issue #81952).
    # Same policy + escape hatch as _guard_noninteractive_user_config.
    if headless_backend:
        from openagents_cli.config import (
            InvalidUserConfigError,
            require_parseable_user_config,
        )

        try:
            require_parseable_user_config(
                ignore_user_config=bool(getattr(args, "ignore_user_config", False))
            )
        except InvalidUserConfigError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            raise SystemExit(2) from exc
    ssh_owner_nonce = getattr(args, "ssh_owner_nonce", None)
    if ssh_owner_nonce and not re.fullmatch(r"[0-9a-f]{16}", ssh_owner_nonce):
        raise SystemExit("--ssh-owner-nonce must be 16 lowercase hex characters")
    if token_file and not headless_backend:
        raise SystemExit("--ssh-session-token-file is only valid with hermes serve")
    return ssh_owner_nonce


def _dashboard_sanitize_desktop_env(headless_backend) -> None:
    """Strip Desktop-inherited env that hijacks a standalone launch.

    Desktop Electron spawns its backend with HERMES_DESKTOP=1 plus
    HERMES_WEB_DIST=<packaged app.asar[/unpacked]/dist> (and often
    HERMES_SERVE_HEADLESS=1). A shell inheriting those then running
    `hermes dashboard` would serve the desktop renderer ("Desktop IPC bridge
    is unavailable", #52945) or disable the SPA. Only Electron-packaged
    WEB_DIST contamination is stripped from browser dashboards — caller-managed
    overrides (dev / custom builds) must still work, while headless `serve`
    keeps the packaged path used by the Desktop backend. Headless `serve`
    re-sets HERMES_SERVE_HEADLESS itself.

    The Desktop's legacy fallback spawn (`dashboard --no-open`, taken when the
    `serve --help` probe times out on a cold host) is not headless yet must keep
    its packaged dist: it is told apart by the per-spawn
    HERMES_DASHBOARD_SESSION_TOKEN, which the terminal pane never receives and
    the terminal tool's env policy strips from agent children.
    """
    desktop_owned_child = _is_desktop_owned_backend()
    if (
        not headless_backend
        and not desktop_owned_child
        and _is_electron_packaged_web_dist(os.environ.get("HERMES_WEB_DIST", ""))
    ):
        os.environ.pop("HERMES_WEB_DIST", None)
    if not headless_backend:
        os.environ.pop("HERMES_SERVE_HEADLESS", None)


def _dashboard_prepare_runtime(args, headless_backend) -> bool:
    """Deps check, skills seed, terminal env bridge, plugins, MCP discovery.

    Returns ``start_mcp_discovery_after_bind`` for start_server.
    """
    # Attach gui.log early so dashboard startup/build failures are captured in
    # the same logs directory as every other OpenAgents surface.
    try:
        from openagents_logging import setup_logging as _setup_logging_gui
        _setup_logging_gui(mode="gui")
    except Exception:
        pass

    try:
        import fastapi  # noqa: F401
        import uvicorn  # noqa: F401
    except ImportError as e:
        from openagents_cli.main_dep_hints import missing_optional_deps_message

        print(missing_optional_deps_message("dashboard", "its web-server packages (fastapi, uvicorn)", "all"))
        print(f"Details: {e}")
        sys.exit(1)

    # Seed bundled skills on first dashboard launch so the desktop GUI's
    # skills picker / agent skill discovery sees the bundled library.
    _sync_bundled_skills_quietly()

    # Bridge terminal.* config into TERMINAL_* env for THIS process, like the
    # CLI (cli.py env_mappings) and gateway (_terminal_env_map) do. The
    # dashboard/serve backend runs agents in-process (tui_gateway.ws →
    # server._make_agent) and ticks cron itself when desktop-spawned; without
    # this those consumers saw an unset TERMINAL_ENV and ran every command on
    # the host even under `terminal.backend: docker` (#63141, #54449).
    try:
        # PTY chat spawns already bridge their child env copy; this covers the in-process consumers. See
        # #61115, #65696.
        from openagents_cli.config import apply_terminal_config_to_env

        apply_terminal_config_to_env()
    except Exception:
        logger.debug("terminal config → env bridge failed for dashboard/serve",
                     exc_info=True)

    _resolve_dashboard_web_dist(args, headless_backend)
    # Load plugins so any DashboardAuthProvider plugin registers BEFORE
    # start_server's fail-closed gate check. Argparse setup skips discovery
    # for built-in subcommands (~500ms), but the dashboard's server-side
    # runtime depends on plugin-registered providers (image_gen, web,
    # dashboard_auth, …).
    try:
        from openagents_cli.plugins import discover_plugins
        discover_plugins()
    except Exception as exc:
        # Must not block startup; the gate's fail-closed branch surfaces a
        # missing provider if it matters.
        print(f"⚠ Plugin discovery failed: {exc}", file=sys.stderr)

    # Desktop chat uses the in-process /api/ws gateway (tui_gateway.server
    # ._make_agent), which only snapshots the tool registry and never starts
    # MCP discovery — so configured MCP servers would never connect. Spawn
    # discovery in the background here so a slow/dead server can't block
    # startup. Desktop-spawned headless backends start it AFTER the socket
    # binds instead (start_server's ready path): the thread's first act is the
    # ~350ms `mcp` SDK import, which holds the GIL against the web_server
    # import and delays the READY sentinel; _make_agent's bounded
    # wait_for_mcp_discovery covers a server still connecting at first turn.
    # A standalone (non-Desktop) dashboard may sit idle and unvisited for days
    # (#58733): it arms discovery instead and the first /api/ws client fires it.
    desktop = _is_desktop_owned_backend()
    if headless_backend and desktop:
        return True
    try:
        from openagents_cli.mcp_startup import (
            defer_background_mcp_discovery,
            start_background_mcp_discovery,
        )

        if desktop:
            start_background_mcp_discovery(logger=logger, thread_name="dashboard-mcp-discovery")
        else:
            defer_background_mcp_discovery(logger=logger, thread_name="dashboard-mcp-discovery", delay=None)
    except Exception:
        logger.debug(
            "Background MCP tool discovery failed at dashboard startup",
            exc_info=True,
        )
    return False


def cmd_dashboard(args):
    """Start the web UI server, or (with --stop/--status) manage running ones."""
    _token_file = getattr(args, "ssh_session_token_file", None)
    _dashboard_lifecycle_flags(args, _token_file)

    # `serve` is the headless backend: no UI build, no SPA mount, neutral
    # ready sentinel. Resolved once and threaded through the re-exec, the
    # build gate, and start_server.
    _headless_backend = getattr(args, "headless_backend", False)
    _ssh_owner_nonce = _dashboard_validate_serve_args(args, _headless_backend, _token_file)
    _dashboard_sanitize_desktop_env(_headless_backend)

    _attach_to_host_backend(args, _headless_backend)
    _route_named_profile_dashboard(args, _headless_backend, _ssh_owner_nonce, _token_file)

    # Apply the final process/profile policy after dashboard routing, but before
    # importing the web server or opening dashboard state. Applying it before a
    # named-profile re-exec could leak that profile's higher limit into the
    # machine/default dashboard, whose lower policy intentionally cannot undo it.
    # This also covers Desktop SSH's isolated `serve` child, which does not route.
    from openagents_cli.resource_limits import apply_nofile_soft_limit

    apply_nofile_soft_limit()

    _ssh_session_token = _read_ssh_session_token_file(_token_file) if _token_file else None
    _mcp_discovery_after_bind = _dashboard_prepare_runtime(args, _headless_backend)

    from openagents_cli.web_server import start_server

    # Interactive auth setup: if this bind will engage the auth gate but no
    # provider is registered yet, offer to configure one here (TTY only)
    # instead of hard-failing inside start_server. Non-interactive callers
    # (Docker/s6, CI, --no-open pipelines) fall through to start_server's
    # fail-closed SystemExit unchanged.
    _maybe_setup_dashboard_auth_interactively(args)

    # The in-browser Chat tab (embedded TUI over PTY/WebSocket) is always
    # available — desktop and dashboard both rely on `/api/ws` + `/api/pty`.
    start_server(
        host=args.host,
        port=args.port,
        open_browser=not args.no_open,
        allow_public=getattr(args, "insecure", False),
        initial_profile=getattr(args, "open_profile", "") or "",
        headless=_headless_backend,
        ssh_session_token=_ssh_session_token,
        ssh_owner_nonce=_ssh_owner_nonce,
        start_mcp_discovery_after_bind=_mcp_discovery_after_bind,
    )


def cmd_completion(args, parser=None):
    """Print shell completion script."""
    from openagents_cli import completion

    shell = getattr(args, "shell", "bash")
    generate = {"zsh": completion.generate_zsh, "fish": completion.generate_fish}.get(
        shell, completion.generate_bash
    )
    print(generate(parser))


def cmd_logs(args):
    """View and filter OpenAgents log files."""
    from openagents_cli.logs import tail_log, list_logs

    log_name = getattr(args, "log_name", "agent") or "agent"

    if log_name == "list":
        list_logs()
        return

    tail_log(
        log_name,
        num_lines=getattr(args, "lines", 50),
        follow=getattr(args, "follow", False),
        level=getattr(args, "level", None),
        session=getattr(args, "session", None),
        since=getattr(args, "since", None),
        component=getattr(args, "component", None),
    )


def cmd_console(args):
    """Open the safe OpenAgents command console."""
    from openagents_cli.console_engine import run_console_repl

    return run_console_repl()


# Top-level subcommands known WITHOUT plugin discovery (which costs 500ms+ of
# eager plugin imports). Keep in sync with the add_parser calls in
# _build_cli_parser: a missing entry only costs a one-time discovery; an extra
# entry would let a plugin command silently fail to parse.
_BUILTIN_SUBCOMMANDS = frozenset(
    {
        "acp", "approvals", "auth", "backup", "bundles", "checkpoints", "claw", "codex-runtime", "completion",
        "computer-use",
        "config", "console", "cron", "curator", "dashboard", "serve", "debug", "doctor",
        "dump", "egress", "fallback", "gateway", "hooks", "import", "import-agent", "insights",
        "gui", "desktop", "kanban", "login", "logout", "logs", "lsp", "mcp", "memory", "migrate", "moa",
        "journey", "memory-graph", "learning",
        "model", "monitoring", "pairing", "pause", "peer", "pets", "plugins", "portal", "profile",
        "project", "proxy",
        "prompt-size",
        "resume",
        "send", "sessions", "setup",
        "skin", "skills", "slack", "status", "sync", "tools", "uninstall", "update",
        "usage", "vault",
        "webhook", "whatsapp", "whatsapp-cloud", "worktree", "chat", "secrets", "security",
        "browser",
        "verify",
        # Plugin commands missing from top-level --help is an accepted trade-off.
        "help",
    }
)


def _first_positional_argv() -> str | None:
    """First non-flag, non-flag-value token in ``sys.argv[1:]`` (skips values of known flags).

    Not a full argparse simulation: an unknown ``--foo bar`` may classify
    ``bar`` as positional, which at worst forces a one-time plugin discovery.
    """
    from openagents_cli._parser import top_level_value_flag_sets

    required_value_flags, optional_value_flags = top_level_value_flag_sets()
    value_flags = required_value_flags | optional_value_flags
    argv = sys.argv[1:]
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok == "--":  # everything after is positional
            return argv[i + 1] if i + 1 < len(argv) else None
        if not tok.startswith("-"):
            return tok
        # ``--flag=value`` is a single token; a known value flag consumes the next.
        i += 2 if ("=" not in tok and tok in value_flags and i + 1 < len(argv)) else 1
    return None


def _plugin_cli_discovery_needed() -> bool:
    """True when the CLI might be invoking a plugin-registered subcommand.

    False skips plugin discovery at argparse setup (~500-650ms). An unknown
    first token could be a plugin command OR a chat prompt — either way
    discovery is needed; for a prompt its cost amortizes over the agent run.
    """
    first = _first_positional_argv()  # None = bare ``hermes`` → chat
    return first is not None and first not in _BUILTIN_SUBCOMMANDS


def _resolve_deferred_platform_cli_command(command_name: str | None) -> None:
    """Materialize the deferred platform whose top-level CLI command matches.

    Bundled platforms are *deferred* entries (no gateway SDK imports at
    startup), so a platform's ``register_cli_command`` side effect only runs
    on import; ``discover_plugins()`` alone leaves ``hermes photon`` failing
    with ``invalid choice``. Importing just the matching platform keeps
    startup cheap.

    On the unknown-top-level-command slow path, ``discover_plugins()`` records the deferred loader but does
    not import it, so the CLI registration never happens and ``hermes photon`` fails with argparse ``invalid
    choice`` (issue #54678).
    """
    if not command_name:
        return
    try:
        from gateway.platform_registry import platform_registry

        platform_registry.get(command_name)
    except Exception as exc:
        logging.getLogger(__name__).debug(
            "Deferred platform CLI resolution failed for %s: %s",
            command_name,
            exc,
        )


_AGENT_COMMANDS = {None, "chat", "acp", "rl"}
_AGENT_SUBCOMMANDS = {
    "cron": ("cron_command", {"run", "tick"}),
    "gateway": ("gateway_command", {"run"}),
    "mcp": ("mcp_action", {"serve"}),
}


def _is_tui_chat_launch(args) -> bool:
    if getattr(args, "tui", False) or os.environ.get("HERMES_TUI") == "1":
        return True
    # The chat path decides TUI-vs-classic via _resolve_use_tui (--cli/--tui
    # flags, TTY gate, HERMES_TUI env, display.interface config). Bare
    # `hermes`/`hermes chat` with a TUI display config was previously missed
    # here, so the wrapper pre-warmed its own MCP discovery while the TUI
    # gateway (spawned moments later) ran a second one — an idle stdio MCP
    # server copy held dead for the whole session. Only chat commands can
    # launch the TUI; other commands (mcp serve, gateway, acp, cron) keep
    # their own discovery behavior untouched.
    if getattr(args, "command", None) not in {None, "chat"}:
        return False
    return _resolve_use_tui(args)


def _agent_subcommand_selected(args) -> bool:
    """True for ``cron run/tick``, ``gateway run``, ``mcp serve`` (see _AGENT_SUBCOMMANDS)."""
    _sub_attr, _sub_set = _AGENT_SUBCOMMANDS.get(args.command, (None, None))
    return bool(_sub_attr and getattr(args, _sub_attr, None) in _sub_set)


def _command_has_dedicated_mcp_startup(args) -> bool:
    """acp / gateway run / cron run|tick own their MCP startup on the runtime path."""
    return args.command == "acp" or (
        args.command != "mcp" and _agent_subcommand_selected(args)
    )


def _should_background_mcp_startup(args) -> bool:
    return not _is_tui_chat_launch(args) and args.command in {None, "chat", "rl"}


def _prepare_agent_startup(args) -> None:
    """Discover plugins/MCP/hooks for commands that can run an agent turn."""
    # --yolo chokepoint: HERMES_YOLO_MODE must be set before any discovery
    # below imports tools.approval, which freezes _YOLO_MODE_FROZEN at import.
    # main() sets it earlier too, but other launchers (Termux fast-CLI) reach
    # here directly, so the guarantee lives where the import is triggered.
    # See #7994.
    if getattr(args, "yolo", False):
        os.environ["HERMES_YOLO_MODE"] = "1"
    _apply_safe_mode(args)
    _apply_user_config_bypass(args)
    _guard_noninteractive_user_config(args)

    if not (args.command in _AGENT_COMMANDS or _agent_subcommand_selected(args)):
        return

    _accept_hooks = bool(getattr(args, "accept_hooks", False))
    if not _is_tui_chat_launch(args):
        # The TUI backend does its own discovery; the launcher only spawns Node.
        try:
            from openagents_cli.plugins import start_background_plugin_discovery

            # Daemon thread: ~150ms of manifest scanning overlaps the rest of
            # startup. Every synchronous reader goes through discover_plugins(),
            # which joins this thread first (incl. model_tools at import time).
            start_background_plugin_discovery()
        except Exception:
            logger.warning(
                "plugin discovery failed at CLI startup",
                exc_info=True,
            )
    # -t/--toolsets narrows which configured MCP servers get spawned, on
    # every discovery path (inline below, background thread, TUI/desktop
    # deferred start). Built-in toolset names never match a server key, so
    # `-t terminal` simply spawns nothing; `-t all` keeps the full set.
    try:
        from openagents_cli.mcp_startup import set_mcp_server_filter

        set_mcp_server_filter(getattr(args, "toolsets", None))
    except Exception:
        logger.debug("MCP server filter setup failed", exc_info=True)

    # TUI launches hand off to a startup path that backgrounds MCP discovery
    # with a bounded join; acp/gateway/cron do their own on the runtime path.
    _run_inline_mcp_discovery = not (
        _is_tui_chat_launch(args) or _command_has_dedicated_mcp_startup(args)
    )
    if _run_inline_mcp_discovery and _should_background_mcp_startup(args):
        try:
            from openagents_cli.mcp_startup import start_background_mcp_discovery

            start_background_mcp_discovery(
                logger=logger,
                thread_name="cli-mcp-discovery",
            )
        except Exception:
            logger.debug(
                "Background MCP tool discovery failed at CLI startup",
                exc_info=True,
            )
        _run_inline_mcp_discovery = False
    if _run_inline_mcp_discovery:
        try:  # synchronous for entrypoints without a later bounded startup path
            from openagents_cli.mcp_startup import get_mcp_server_filter
            from tools.mcp_tool_discovery import discover_mcp_tools

            _mcp_filter = get_mcp_server_filter()
            if _mcp_filter is None:
                discover_mcp_tools()
            else:
                discover_mcp_tools(allowed_mcp_names=_mcp_filter)
        except Exception:
            logger.debug(
                "MCP tool discovery failed at CLI startup",
                exc_info=True,
            )
    try:
        from openagents_cli.config import load_config
        from agent.shell_hooks import register_from_config

        _hooks_cfg = load_config()
        register_from_config(_hooks_cfg, accept_hooks=_accept_hooks)

        from agent.outbound_webhooks import (
            register_from_config as register_outbound_webhooks,
        )

        register_outbound_webhooks(_hooks_cfg)
    except Exception:
        logger.debug(
            "shell-hook registration failed at CLI startup",
            exc_info=True,
        )


def _apply_safe_mode(args) -> None:
    if not getattr(args, "safe_mode", False):
        return
    os.environ["HERMES_SAFE_MODE"] = "1"
    os.environ["HERMES_IGNORE_USER_CONFIG"] = "1"
    os.environ["HERMES_IGNORE_RULES"] = "1"


def _apply_user_config_bypass(args) -> None:
    """Apply the explicit config bypass before any startup config reads."""
    if getattr(args, "ignore_user_config", False):
        os.environ["HERMES_IGNORE_USER_CONFIG"] = "1"


def _guard_noninteractive_user_config(args) -> None:
    """Fail closed before a non-interactive invocation initializes providers."""
    if getattr(args, "_noninteractive_config_validated", False):
        return

    is_noninteractive = (
        bool(getattr(args, "oneshot", None))
        or bool(getattr(args, "query", None))
    )
    if not is_noninteractive:
        return

    from openagents_cli.config import (
        InvalidUserConfigError,
        require_parseable_user_config,
    )

    try:
        require_parseable_user_config(
            ignore_user_config=bool(
                getattr(args, "ignore_user_config", False)
                or getattr(args, "safe_mode", False)
            )
        )
    except InvalidUserConfigError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc

    setattr(args, "_noninteractive_config_validated", True)


def _set_chat_arg_defaults(args) -> None:
    """Fill the chat-parser attrs cmd_chat reads when chat was not parsed."""
    for attr, default in [
        ("query", None),
        ("model", None),
        ("provider", None),
        ("toolsets", None),
        ("verbose", False),
        ("resume", None),
        ("continue_last", None),
        ("worktree", False),
    ]:
        if not hasattr(args, attr):
            setattr(args, attr, default)


def _run_oneshot_from_args(args) -> None:
    """Top-level --oneshot / -z: single-shot mode, stdout = final response only.

    Bypasses cli.py entirely; _run_and_exit_oneshot never returns.
    """
    _confirm_startup_expensive_model_override(args)
    # -z honors --resume/-c/--in exactly like chat (#105892): normalize BEFORE the
    # oneshot exit path takes over, else the flags parse fine but silently do nothing
    # and the turn starts a fresh session (every wire request loses all history).
    _resolve_chat_session_args(args, use_tui=False)
    _run_and_exit_oneshot(
        args.oneshot,
        model=getattr(args, "model", None),
        provider=getattr(args, "provider", None),
        toolsets=getattr(args, "toolsets", None),
        skills=getattr(args, "skills", None),
        usage_file=getattr(args, "usage_file", None),
        resume=getattr(args, "resume", None),
        reasoning=getattr(args, "reasoning", None),
    )


def _light_chat_parser():
    """Top-level + chat parser only (no subcommand tree); chat dispatches to cmd_chat."""
    from openagents_cli._parser import build_top_level_parser

    parser, _subparsers, chat_parser = build_top_level_parser()
    chat_parser.set_defaults(func=cmd_chat)
    return parser


def _promote_top_level_resume(args) -> None:
    """Top-level --resume/--continue with no subcommand is a chat shortcut."""
    if (args.resume or args.continue_last) and args.command is None:
        args.command = "chat"


def _try_fast_serve_launch() -> bool:
    """Dispatch an unambiguous built-in ``serve`` without the full CLI tree.

    Desktop runs this on every cold start; building every other parser costs
    thousands of filesystem-backed lookups on Windows. Unknown or global
    arguments fall back to normal parsing so error reporting is unchanged.
    """
    if os.environ.get("HERMES_DISABLE_FAST_SERVE_LAUNCH") == "1":
        return False

    argv = sys.argv[1:]
    if not argv or argv[0] != "serve" or "-h" in argv or "--help" in argv:
        return False

    # Container routing is top-level policy and must run before host dispatch.
    try:
        from openagents_cli.config import get_container_exec_info

        if get_container_exec_info():
            return False
    except Exception:
        return False

    parser = build_serve_parser(
        cmd_dashboard=cmd_dashboard,
        add_help=False,
        exit_on_error=False,
    )
    try:
        args, unknown = parser.parse_known_args(argv[1:])
    except (argparse.ArgumentError, ValueError):
        return False
    if unknown:
        return False

    cmd_dashboard(args)
    return True


def _try_fast_chat_launch() -> bool:
    """Fast path for unambiguous interactive chat launches (all hosts).

    Building all ~40 subcommand parsers costs ~140ms the chat path never
    uses. Bails out (False) whenever the invocation is not certainly a chat
    launch — subcommand positional, ``--help``, unknown flags. Mirrors
    ``_try_termux_fast_cli_launch`` minus the Termux deferred startup; kept
    separate so phone-tuned behavior doesn't leak to desktops.
    """
    if os.environ.get("HERMES_DISABLE_FAST_CHAT_LAUNCH") == "1":
        return False
    argv = sys.argv[1:]
    if "-h" in argv or "--help" in argv:
        return False
    # Container routing must win: NixOS container mode forwards EVERY invocation.
    try:
        from openagents_cli.config import get_container_exec_info
        if get_container_exec_info():
            return False
    except Exception:
        return False
    # TUI launches keep full dispatch outside Termux (own startup path).
    if _wants_tui_early(argv):
        return False
    if _first_positional_argv() not in {None, "chat"}:
        return False

    parser = _light_chat_parser()
    try:
        args, unknown = parser.parse_known_args(_coalesce_session_name_args(argv))
    except SystemExit:
        return False
    if unknown:  # plugin subcommand or full-parser-only flag → full dispatch
        return False
    if getattr(args, "version", False):
        return False
    if getattr(args, "command", None) not in {None, "chat"}:
        return False

    if getattr(args, "yolo", False):
        os.environ["HERMES_YOLO_MODE"] = "1"
    _prepare_agent_startup(args)

    if getattr(args, "oneshot", None):
        _run_oneshot_from_args(args)

    _promote_top_level_resume(args)
    _set_chat_arg_defaults(args)
    cmd_chat(args)
    return True


def _try_termux_fast_cli_launch() -> bool:
    """Run obvious Termux non-TUI chat/oneshot/version paths on a light parser."""
    if not _is_termux_startup_environment():
        return False
    if os.environ.get("HERMES_TERMUX_DISABLE_FAST_CLI") == "1":
        return False

    argv = sys.argv[1:]
    if "-h" in argv or "--help" in argv:
        return False
    if _wants_tui_early(argv):  # TUI fast path / full dispatch owns those
        return False

    if _startup_fast.is_termux_fast_version_argv(argv):
        _print_version_info(check_updates=True)
        return True

    first = _first_positional_argv()
    has_oneshot = any(
        arg == "-z" or arg == "--oneshot" or arg.startswith("--oneshot=")
        for arg in argv
    )
    if not has_oneshot and first not in {None, "chat"}:
        return False

    parser = _light_chat_parser()
    args = parser.parse_args(_coalesce_session_name_args(argv))

    if getattr(args, "version", False):
        _print_version_info(check_updates=True)
        return True

    if getattr(args, "oneshot", None):
        _prepare_agent_startup(args)
        _run_oneshot_from_args(args)

    _promote_top_level_resume(args)
    if args.command in {None, "chat"}:
        _set_chat_arg_defaults(args)
        interactive_prompt = not getattr(args, "query", None) and not getattr(args, "image", None)
        if interactive_prompt:
            # Reach the prompt first; agent-only discovery on the first turn.
            setattr(args, "compact", True)
            os.environ["HERMES_DEFER_AGENT_STARTUP"] = "1"
            os.environ["HERMES_FAST_STARTUP_BANNER"] = "1"
            if getattr(args, "accept_hooks", False):
                os.environ["HERMES_ACCEPT_HOOKS"] = "1"
        else:
            _prepare_agent_startup(args)
        cmd_chat(args)
        return True

    return False


def _try_termux_fast_tui_launch() -> bool:
    """Launch obvious Termux TUI invocations before building every subparser.

    `hermes --tui` is the hot path on phones and the TUI immediately execs
    Node, so the full parser's command-module imports are pure waste there.
    """
    if not _is_termux_startup_environment():
        return False

    if "-h" in sys.argv[1:] or "--help" in sys.argv[1:]:
        return False

    wants_tui = _wants_tui_early(sys.argv[1:])
    if not wants_tui:
        return False

    first = _first_positional_argv()
    if first not in {None, "chat"}:
        return False

    parser = _light_chat_parser()
    args = parser.parse_args(_coalesce_session_name_args(sys.argv[1:]))

    # Preserve top-level behaviours whose semantics are not "launch chat/TUI".
    if getattr(args, "version", False) or getattr(args, "oneshot", None):
        return False
    if getattr(args, "command", None) not in {None, "chat"}:
        return False
    if not _resolve_use_tui(args):
        return False

    cmd_chat(args)
    return True


def _advertise_agent_env() -> None:
    """Advertise the agent harness to child processes.

    ``AI_AGENT`` is the cross-agent standard (huggingface_hub reads it); the
    value must be our id in the public agent-harness registry
    (``openagents``) — matching is exact. ``HERMES_AGENT`` is the
    Hermes-specific marker. setdefault: never clobber an outer harness.

    ``AI_AGENT`` is the emerging cross-agent standard (huggingface_hub's agent detection reads it; pi and
    other agents set it — earendil-works/pi#7493) so generic tooling can attribute subprocesses to the
    harness that spawned them. OpenAgents running inside another agent's terminal).
    """
    os.environ.setdefault("AI_AGENT", "openagents")
    os.environ.setdefault("HERMES_AGENT", "true")


def _attach_plugin_cli_command(subparsers, cmd_info) -> None:
    """Register one plugin-provided top-level command from its descriptor."""
    plugin_parser = subparsers.add_parser(
        cmd_info["name"],
        help=cmd_info["help"],
        description=cmd_info.get("description", ""),
        formatter_class=__import__("argparse").RawDescriptionHelpFormatter,
    )
    cmd_info["setup_fn"](plugin_parser)
    if cmd_info.get("handler_fn") is not None:
        plugin_parser.set_defaults(func=cmd_info["handler_fn"])


def _register_plugin_cli_commands(subparsers) -> None:
    """Register plugin-provided top-level commands (each plugin builds its own argparse tree).

    Skipped when the invocation targets a known built-in — eagerly importing
    every bundled plugin module costs 500-650ms.
    """
    if not _plugin_cli_discovery_needed():
        return
    try:
        from plugins.memory import discover_plugin_cli_commands
        from openagents_cli.plugins import discover_plugins, get_plugin_manager

        seen_plugin_commands = set()
        for cmd_info in discover_plugin_cli_commands():
            _attach_plugin_cli_command(subparsers, cmd_info)
            seen_plugin_commands.add(cmd_info["name"])

        discover_plugins()
        # The invoked platform may still be a deferred entry; import it so its
        # register_cli_command side effect runs before we read _cli_commands.
        # See #54678.
        _resolve_deferred_platform_cli_command(_first_positional_argv())
        for cmd_info in get_plugin_manager()._cli_commands.values():
            if cmd_info["name"] not in seen_plugin_commands:
                _attach_plugin_cli_command(subparsers, cmd_info)
    except Exception as _exc:
        logging.getLogger(__name__).debug("Plugin CLI discovery failed: %s", _exc)


def _cmd_sessions_lazy(args, **kwargs):
    """``hermes sessions`` handler; sessions_cmd imports only when the subcommand runs."""
    from openagents_cli.sessions_cmd import cmd_sessions

    return cmd_sessions(args, **kwargs)


def _build_cli_parser():
    """Build the full ``hermes`` argparse tree -> ``(parser, subparsers)``.

    Registration ORDER is the ``hermes --help`` order; keep it stable. Groups
    live in ``openagents_cli/subcommands/<group>.py`` with handlers injected so
    those modules never import main.
    """
    from openagents_cli._parser import build_top_level_parser

    parser, subparsers, chat_parser = build_top_level_parser()
    chat_parser.set_defaults(func=cmd_chat)

    build_model_parser(subparsers, cmd_model=cmd_model)
    build_moa_parser(subparsers)
    build_fallback_parser(subparsers)
    build_worktree_parser(subparsers)
    build_browser_parser(subparsers)
    build_secrets_parser(subparsers)
    # OUTBOUND egress firewall; ``hermes proxy`` (gateway group) is the INBOUND one.
    build_egress_parser(subparsers)
    build_migrate_parser(subparsers)
    build_codex_runtime_parser(subparsers)
    build_gateway_parser(
        subparsers, cmd_gateway=cmd_gateway, cmd_proxy=cmd_proxy, cmd_gateway_enroll=cmd_gateway_enroll
    )

    # LSP is optional — a registration failure must not break the CLI.
    try:
        from agent.lsp.cli import register_subparser as _lsp_register
        _lsp_register(subparsers)
    except Exception as _lsp_err:  # noqa: BLE001
        logger.debug("LSP CLI registration failed: %s", _lsp_err)

    build_setup_parser(subparsers, cmd_setup=cmd_setup)
    build_whatsapp_parser(subparsers, cmd_whatsapp=cmd_whatsapp)
    build_whatsapp_cloud_parser(subparsers, cmd_whatsapp_cloud=cmd_whatsapp_cloud)
    build_slack_parser(subparsers, cmd_slack=cmd_slack)

    from openagents_cli.send_cmd import register_send_subparser
    register_send_subparser(subparsers)

    build_login_parser(subparsers, cmd_login=cmd_login)
    build_logout_parser(subparsers, cmd_logout=cmd_logout)
    build_auth_parser(subparsers, cmd_auth=cmd_auth)
    build_status_parser(subparsers, cmd_status=cmd_status)
    build_pause_parser(subparsers)
    build_cron_parser(subparsers, cmd_cron=cmd_cron)
    build_sync_parser(subparsers, cmd_sync=cmd_sync)
    build_webhook_parser(subparsers, cmd_webhook=cmd_webhook)

    from openagents_cli.subcommands.peer import build_peer_parser
    build_peer_parser(subparsers)

    from openagents_cli.portal_cli import add_parser as _add_portal_parser
    _add_portal_parser(subparsers)

    from openagents_cli.kanban import build_parser as _build_kanban_parser
    _build_kanban_parser(subparsers).set_defaults(func=cmd_kanban)

    from openagents_cli.projects_cmd import build_parser as _build_project_parser
    _build_project_parser(subparsers).set_defaults(func=cmd_project)

    build_hooks_parser(subparsers, cmd_hooks=cmd_hooks)
    build_doctor_parser(subparsers, cmd_doctor=cmd_doctor)
    build_verify_parser(subparsers, cmd_verify=cmd_verify)
    build_security_parser(subparsers, cmd_security=cmd_security)
    build_approvals_parser(subparsers, cmd_approvals=cmd_approvals)
    build_dump_parser(subparsers, cmd_dump=cmd_dump)
    build_debug_parser(subparsers, cmd_debug=cmd_debug)
    build_backup_parser(subparsers, cmd_backup=cmd_backup)
    build_checkpoints_parser(subparsers)
    build_import_cmd_parser(subparsers, cmd_import=cmd_import)
    build_import_agent_parser(subparsers, cmd_import_agent=cmd_import_agent)
    build_config_parser(subparsers, cmd_config=cmd_config)
    build_skin_parser(subparsers, cmd_skin=cmd_skin)
    build_console_parser(subparsers, cmd_console=cmd_console)
    build_pairing_parser(subparsers, cmd_pairing=cmd_pairing)
    build_skills_parser(subparsers, cmd_skills=cmd_skills)
    build_bundles_parser(subparsers)
    build_plugins_parser(subparsers, cmd_plugins=cmd_plugins)

    _register_plugin_cli_commands(subparsers)

    build_curator_parser(subparsers)
    build_pets_parser(subparsers)
    build_journey_parser(subparsers)
    build_memory_parser(subparsers, cmd_memory=cmd_memory)
    build_tools_parser(subparsers, cmd_tools=cmd_tools)
    build_computer_use_parser(subparsers)
    build_mcp_parser(subparsers, cmd_mcp=cmd_mcp)
    build_sessions_parser(subparsers, cmd_sessions=_cmd_sessions_lazy)
    build_insights_parser(subparsers, cmd_insights=cmd_insights)
    build_usage_parser(subparsers)
    build_monitoring_parser(subparsers, cmd_monitoring=cmd_monitoring)
    build_claw_parser(subparsers, cmd_claw=cmd_claw)
    build_vault_parser(subparsers)
    build_update_parser(subparsers, cmd_update=cmd_update)
    build_uninstall_parser(subparsers, cmd_uninstall=cmd_uninstall)
    build_acp_parser(subparsers, cmd_acp=cmd_acp)
    build_profile_parser(subparsers, cmd_profile=cmd_profile)
    build_completion_parser(subparsers, cmd_completion=cmd_completion, parser=parser)
    build_dashboard_parser(
        subparsers,
        cmd_dashboard=cmd_dashboard,
        cmd_dashboard_register=cmd_dashboard_register,
    )
    # "desktop" is canonical (Hermes-Setup.exe tells users to run it, so it
    # must be the name --help shows); "gui" is a deprecated alias.
    build_gui_parser(subparsers, cmd_gui=cmd_gui)
    build_logs_parser(subparsers, cmd_logs=cmd_logs)
    build_prompt_size_parser(subparsers, cmd_prompt_size=cmd_prompt_size)
    return parser, subparsers


def _parse_cli_args(parser, subparsers, argv):
    """Parse ``argv`` with the bpo-9338 subparser-routing workaround.

    On Python <3.11 argparse fails to route subcommand tokens when the parent
    has nargs='?' optionals (--continue): "unrecognized arguments: model". When
    argv holds a known subcommand token, set subparsers.required=True to force
    routing; if that fails (``hermes -c model`` — 'model' is the session name)
    fall back to the default behaviour.
    """
    import io as _io

    _processed_argv = _coalesce_session_name_args(argv)
    _known_cmds = (
        set(subparsers.choices.keys()) if hasattr(subparsers, "choices") else set()
    )
    _has_cmd_token = any(
        t in _known_cmds for t in _processed_argv if not t.startswith("-")
    )
    if not _has_cmd_token:
        subparsers.required = False
        return parser.parse_args(_processed_argv)

    subparsers.required = True
    _saved_stderr = sys.stderr
    try:
        sys.stderr = _io.StringIO()
        args = parser.parse_args(_processed_argv)
        sys.stderr = _saved_stderr
    except SystemExit as exc:
        sys.stderr = _saved_stderr
        if exc.code == 0:  # help/version already printed; don't print twice
            raise
        # Subcommand consumed as a flag value (e.g. -c model): normal parse.
        subparsers.required = False
        args = parser.parse_args(_processed_argv)
    return args


def _default_to_chat(args) -> None:
    """No subcommand given: run chat."""
    _promote_top_level_resume(args)
    _set_chat_arg_defaults(args)
    cmd_chat(args)


def main():
    """Main entry point for hermes CLI."""
    _set_process_title()
    _warn_if_unsupervised_pid1()
    _advertise_agent_env()

    # Force UTF-8 stdio on Windows before anything prints.  No-op elsewhere.
    try:
        from openagents_cli.stdio import configure_windows_stdio
        configure_windows_stdio()
    except Exception:
        pass

    # Sweep stale ``hermes.exe.old.*`` quarantine files from previous Windows
    # updates (see ``_quarantine_running_hermes_exe``). No-op elsewhere.
    try:
        _cleanup_quarantined_exes()
    except Exception:
        pass

    # Checkout changed since last launch → sweep stale __pycache__ once so no
    # process resolves fresh source against old bytecode. Never raises.
    _sweep_stale_bytecode_if_checkout_changed()

    # Self-heal a venv left half-built by an interrupted ``hermes update``, and
    # hint (never restart) about a fleet the interrupted update never
    # restarted. Both skipped while the user is *running* update — that flow
    # owns its marker and a recovery install must not race the real one. The
    # substring match is deliberately loose: over-matching (``hermes skills
    # install update``) only defers recovery one launch; under-matching
    # (``hermes -p work update``) would race. Never raises.
    # See #95294.
    if "update" not in sys.argv[1:]:
        try:
            _recover_from_interrupted_install()
        except Exception:
            pass
        try:
            from openagents_cli.update_cmd_fleet import _warn_pending_fleet_restart_on_startup

            _warn_pending_fleet_restart_on_startup()
        except Exception:
            pass

    if _try_termux_fast_tui_launch():
        return
    if _try_termux_fast_cli_launch():
        return
    if _try_fast_serve_launch():
        return
    if _try_fast_chat_launch():
        return

    parser, subparsers = _build_cli_parser()

<<<<<<< HEAD
    parser, subparsers, chat_parser = build_top_level_parser()
    chat_parser.set_defaults(func=cmd_chat)

    # =========================================================================
    # model command  (parser built in openagents_cli/subcommands/model.py)
    # =========================================================================
    build_model_parser(subparsers, cmd_model=cmd_model)

    from openagents_cli.moa_cmd import cmd_moa

    moa_parser = subparsers.add_parser(
        "moa",
        help="Configure Mixture of Agents provider/model slots",
        description="Configure the provider/model set used by /moa <prompt>.",
    )
    moa_subparsers = moa_parser.add_subparsers(dest="moa_command")
    moa_subparsers.add_parser("list", aliases=["ls"], help="Show current MoA model slots")
    moa_configure = moa_subparsers.add_parser("configure", aliases=["config"], help="Interactively pick MoA models")
    moa_configure.add_argument("name", nargs="?", help="Preset name to create or update")
    moa_delete = moa_subparsers.add_parser("delete", aliases=["rm"], help="Delete a MoA preset")
    moa_delete.add_argument("name", help="Preset name to delete")
    moa_parser.set_defaults(func=cmd_moa)

    # =========================================================================
    # fallback command — manage the fallback provider chain
    # =========================================================================
    from openagents_cli.fallback_cmd import cmd_fallback

    fallback_parser = subparsers.add_parser(
        "fallback",
        help="Manage fallback providers (tried when the primary model fails)",
        description=(
            "Manage the fallback provider chain.  Fallback providers are tried "
            "in order when the primary model fails with rate-limit, overload, or "
            "connection errors.  See: "
            "https://openagents.nousresearch.com/docs/user-guide/features/fallback-providers"
        ),
    )
    fallback_subparsers = fallback_parser.add_subparsers(dest="fallback_command")
    fallback_subparsers.add_parser(
        "list",
        aliases=["ls"],
        help="Show the current fallback chain (default when no subcommand)",
    )
    fallback_subparsers.add_parser(
        "add",
        help="Pick a provider + model (same picker as `hermes model`) and append to the chain",
    )
    fallback_subparsers.add_parser(
        "remove",
        aliases=["rm"],
        help="Pick an entry to delete from the chain",
    )
    fallback_subparsers.add_parser(
        "clear",
        help="Remove all fallback entries",
    )
    fallback_parser.set_defaults(func=cmd_fallback)

    # =========================================================================
    # secrets command — external secret managers (currently: Bitwarden)
    # =========================================================================
    secrets_parser = subparsers.add_parser(
        "secrets",
        help="Manage external secret sources (Bitwarden Secrets Manager)",
        description=(
            "Pull API keys from an external secret manager at process startup "
            "instead of storing them in ~/.openagents/.env.  Currently supports "
            "Bitwarden Secrets Manager.  See: "
            "https://openagents.nousresearch.com/docs/user-guide/secrets/bitwarden"
        ),
    )
    secrets_subparsers = secrets_parser.add_subparsers(dest="secrets_command")

    secrets_bw = secrets_subparsers.add_parser(
        "bitwarden",
        aliases=["bw"],
        help="Bitwarden Secrets Manager integration",
    )

    # Lazy import — only pays for itself when this subcommand is actually used.
    from openagents_cli import secrets_cli as _secrets_cli

    _secrets_cli.register_cli(secrets_bw)

    def _dispatch_secrets(args):  # noqa: ANN001
        sub = getattr(args, "secrets_command", None)
        bw_sub = getattr(args, "secrets_bw_command", None)
        if sub in ("bitwarden", "bw") and bw_sub is not None:
            return args.func(args)
        secrets_parser.print_help()
        return 0

    secrets_parser.set_defaults(func=_dispatch_secrets)

    # =========================================================================
    # migrate command
    # =========================================================================
    from openagents_cli.migrate import cmd_migrate, cmd_migrate_xai

    migrate_parser = subparsers.add_parser(
        "migrate",
        help="Migrate configuration for retired models or deprecated settings",
        description=(
            "Diagnose and (optionally) rewrite the active config.yaml to "
            "replace references to retired models or deprecated settings."
        ),
    )
    migrate_subparsers = migrate_parser.add_subparsers(dest="migrate_type")

    migrate_xai = migrate_subparsers.add_parser(
        "xai",
        help="Migrate xAI models scheduled for retirement on May 15, 2026",
        description=(
            "Scan config.yaml for references to xAI models retiring on "
            "May 15, 2026 and, with --apply, rewrite them in-place to the "
            "official replacements per the xAI migration guide. The original "
            "config.yaml is backed up before any rewrite."
        ),
    )
    migrate_xai.add_argument(
        "--apply",
        action="store_true",
        help="Rewrite config.yaml in-place (default: dry-run, no writes)",
    )
    migrate_xai.add_argument(
        "--no-backup",
        action="store_true",
        help="Skip the timestamped backup of config.yaml when applying",
    )
    migrate_xai.set_defaults(func=cmd_migrate_xai)
    migrate_parser.set_defaults(func=cmd_migrate)

    # =========================================================================
    # gateway + proxy commands  (parsers built in openagents_cli/subcommands/gateway.py)
    # =========================================================================
    build_gateway_parser(
        subparsers, cmd_gateway=cmd_gateway, cmd_proxy=cmd_proxy, cmd_gateway_enroll=cmd_gateway_enroll
    )

    # =========================================================================
    # lsp command
    # =========================================================================
    try:
        from agent.lsp.cli import register_subparser as _lsp_register
        _lsp_register(subparsers)
    except Exception as _lsp_err:  # noqa: BLE001
        # LSP is optional infrastructure — never let a registration
        # failure break the CLI overall.
        logger.debug("LSP CLI registration failed: %s", _lsp_err)

    # =========================================================================
    # setup command  (parser built in openagents_cli/subcommands/setup.py)
    # =========================================================================
    build_setup_parser(subparsers, cmd_setup=cmd_setup)

    # =========================================================================
    # postinstall command  (parser built in openagents_cli/subcommands/postinstall.py)
    # =========================================================================
    build_postinstall_parser(subparsers, cmd_postinstall=cmd_postinstall)

    # =========================================================================
    # whatsapp command  (parser built in openagents_cli/subcommands/whatsapp.py)
    # =========================================================================
    build_whatsapp_parser(subparsers, cmd_whatsapp=cmd_whatsapp)

    # =========================================================================
    # whatsapp-cloud command (official Meta Cloud API; complement to Baileys)
    # =========================================================================
    whatsapp_cloud_parser = subparsers.add_parser(
        "whatsapp-cloud",
        help="Set up WhatsApp Business Cloud API integration",
        description=(
            "Configure the official Meta WhatsApp Business Cloud API "
            "adapter (Business account required, public webhook URL "
            "required). Distinct from `hermes whatsapp` which sets up "
            "the Baileys bridge for personal accounts."
        ),
    )
    whatsapp_cloud_parser.set_defaults(func=cmd_whatsapp_cloud)

    # =========================================================================
    # slack command  (parser built in openagents_cli/subcommands/slack.py)
    # =========================================================================
    build_slack_parser(subparsers, cmd_slack=cmd_slack)

    # =========================================================================
    # send command — pipe shell-script output to any configured platform
    # =========================================================================
    from openagents_cli.send_cmd import register_send_subparser
    register_send_subparser(subparsers)

    # =========================================================================
    # login command  (parser built in openagents_cli/subcommands/login.py)
    # =========================================================================
    build_login_parser(subparsers, cmd_login=cmd_login)

    # =========================================================================
    # logout command  (parser built in openagents_cli/subcommands/logout.py)
    # =========================================================================
    build_logout_parser(subparsers, cmd_logout=cmd_logout)

    # =========================================================================
    # auth command  (parser built in openagents_cli/subcommands/auth.py)
    # =========================================================================
    build_auth_parser(subparsers, cmd_auth=cmd_auth)

    # =========================================================================
    # status command  (parser built in openagents_cli/subcommands/status.py)
    # =========================================================================
    build_status_parser(subparsers, cmd_status=cmd_status)

    # =========================================================================
    # cron command  (parser built in openagents_cli/subcommands/cron.py)
    # =========================================================================
    build_cron_parser(subparsers, cmd_cron=cmd_cron)

    # =========================================================================
    # webhook command  (parser built in openagents_cli/subcommands/webhook.py)
    # =========================================================================
    build_webhook_parser(subparsers, cmd_webhook=cmd_webhook)

    # =========================================================================
    # portal command — Nous Portal status + Tool Gateway routing
    # =========================================================================
    from openagents_cli.portal_cli import add_parser as _add_portal_parser
    _add_portal_parser(subparsers)

    # =========================================================================
    # kanban command — multi-profile collaboration board
    # =========================================================================
    from openagents_cli.kanban import build_parser as _build_kanban_parser

    kanban_parser = _build_kanban_parser(subparsers)
    kanban_parser.set_defaults(func=cmd_kanban)

    # =========================================================================
    # project command — named, multi-folder workspaces
    # =========================================================================
    from openagents_cli.projects_cmd import build_parser as _build_project_parser

    project_parser = _build_project_parser(subparsers)
    project_parser.set_defaults(func=cmd_project)

    # =========================================================================
    # company command — multi-agent company workspace scaffolding
    # =========================================================================
    from openagents_cli.company_cmd import build_parser as _build_company_parser

    company_parser = _build_company_parser(subparsers)
    company_parser.set_defaults(func=cmd_company)

    # =========================================================================
    # openagentui / openagent-config — visual workflow builder
    # =========================================================================
    from openagents_cli.openagentui_cmd import build_parser as _build_openagentui_parser
    from openagents_cli.openagentui_config_cmd import build_parser as _build_openagentconfig_parser

    openagentui_parser = _build_openagentui_parser(subparsers)
    openagentui_parser.set_defaults(func=cmd_openagentui)

    openagentconfig_parser = _build_openagentconfig_parser(subparsers)
    openagentconfig_parser.set_defaults(func=cmd_openagentconfig)

    # =========================================================================
    # hooks command — shell-hook inspection and management
    # =========================================================================
    # hooks command  (parser built in openagents_cli/subcommands/hooks.py)
    # =========================================================================
    build_hooks_parser(subparsers, cmd_hooks=cmd_hooks)

    # =========================================================================
    # doctor command  (parser built in openagents_cli/subcommands/doctor.py)
    # =========================================================================
    build_doctor_parser(subparsers, cmd_doctor=cmd_doctor)

    # =========================================================================
    # security command — on-demand supply-chain audit
    # =========================================================================
    # security command  (parser built in openagents_cli/subcommands/security.py)
    # =========================================================================
    build_security_parser(subparsers, cmd_security=cmd_security)

    # =========================================================================
    # dump command  (parser built in openagents_cli/subcommands/dump.py)
    # =========================================================================
    build_dump_parser(subparsers, cmd_dump=cmd_dump)

    # =========================================================================
    # debug command  (parser built in openagents_cli/subcommands/debug.py)
    # =========================================================================
    build_debug_parser(subparsers, cmd_debug=cmd_debug)

    # =========================================================================
    # backup command  (parser built in openagents_cli/subcommands/backup.py)
    # =========================================================================
    build_backup_parser(subparsers, cmd_backup=cmd_backup)

    # =========================================================================
    # checkpoints command
    # =========================================================================
    checkpoints_parser = subparsers.add_parser(
        "checkpoints",
        help="Inspect / prune / clear ~/.openagents/checkpoints/",
        description="Manage the filesystem checkpoint store — the shadow git "
        "repo hermes uses to snapshot working directories before "
        "write_file/patch/terminal calls. Lets you see how much "
        "space checkpoints occupy, force a prune, or wipe the base.",
    )
    from openagents_cli.checkpoints import register_cli as _register_checkpoints_cli
    _register_checkpoints_cli(checkpoints_parser)

    # =========================================================================
    # import command  (parser built in openagents_cli/subcommands/import_cmd.py)
    # =========================================================================
    build_import_cmd_parser(subparsers, cmd_import=cmd_import)

    # =========================================================================
    # config command  (parser built in openagents_cli/subcommands/config.py)
    # =========================================================================
    build_config_parser(subparsers, cmd_config=cmd_config)

    # =========================================================================
    # pairing command  (parser built in openagents_cli/subcommands/pairing.py)
    # =========================================================================
    build_pairing_parser(subparsers, cmd_pairing=cmd_pairing)

    # =========================================================================
    # skills command  (parser built in openagents_cli/subcommands/skills.py)
    # =========================================================================
    build_skills_parser(subparsers, cmd_skills=cmd_skills)

    # =========================================================================
    # bundles command — skill bundles (alias /<name> for multiple skills)
    # =========================================================================
    bundles_parser = subparsers.add_parser(
        "bundles",
        help="Create, list, and manage skill bundles (aliases for multiple skills)",
        description=(
            "Skill bundles let you load several skills under one slash "
            "command. `/<bundle>` from the CLI or gateway loads every "
            "referenced skill at once."
        ),
    )
    from openagents_cli.bundles import register_cli as _bundles_register, bundles_command
    _bundles_register(bundles_parser)
    bundles_parser.set_defaults(func=bundles_command)

    # =========================================================================
    # plugins command  (parser built in openagents_cli/subcommands/plugins.py)
    # =========================================================================
    build_plugins_parser(subparsers, cmd_plugins=cmd_plugins)

    # =========================================================================
    # Plugin CLI commands — dynamically registered by memory/general plugins.
    # Plugins provide a register_cli(subparser) function that builds their
    # own argparse tree.  No hardcoded plugin commands in main.py.
    #
    # Skipped when the invocation is already targeting a known built-in
    # subcommand — ``hermes --help``, ``hermes version``, ``hermes logs``,
    # etc.  This avoids eagerly importing every bundled plugin module
    # (google.cloud.pubsub_v1, aiohttp, grpc, PIL …) which costs
    # 500-650ms on typical installs.
    # =========================================================================
    if _plugin_cli_discovery_needed():
        try:
            from plugins.memory import discover_plugin_cli_commands
            from openagents_cli.plugins import discover_plugins, get_plugin_manager

            seen_plugin_commands = set()
            for cmd_info in discover_plugin_cli_commands():
                plugin_parser = subparsers.add_parser(
                    cmd_info["name"],
                    help=cmd_info["help"],
                    description=cmd_info.get("description", ""),
                    formatter_class=__import__("argparse").RawDescriptionHelpFormatter,
                )
                cmd_info["setup_fn"](plugin_parser)
                if cmd_info.get("handler_fn") is not None:
                    plugin_parser.set_defaults(func=cmd_info["handler_fn"])
                seen_plugin_commands.add(cmd_info["name"])

            discover_plugins()
            for cmd_info in get_plugin_manager()._cli_commands.values():
                if cmd_info["name"] in seen_plugin_commands:
                    continue
                plugin_parser = subparsers.add_parser(
                    cmd_info["name"],
                    help=cmd_info["help"],
                    description=cmd_info.get("description", ""),
                    formatter_class=__import__("argparse").RawDescriptionHelpFormatter,
                )
                cmd_info["setup_fn"](plugin_parser)
                if cmd_info.get("handler_fn") is not None:
                    plugin_parser.set_defaults(func=cmd_info["handler_fn"])
        except Exception as _exc:
            logging.getLogger(__name__).debug("Plugin CLI discovery failed: %s", _exc)

    # =========================================================================
    # curator command — background skill maintenance
    # =========================================================================
    curator_parser = subparsers.add_parser(
        "curator",
        help="Background skill maintenance (curator) — status, run, pause, pin",
        description=(
            "The curator is an auxiliary-model background task that "
            "periodically reviews agent-created skills, prunes stale ones, "
            "consolidates overlaps, and archives obsolete skills. "
            "Bundled and hub-installed skills are never touched. "
            "Archives are recoverable; auto-deletion never happens."
        ),
    )
    try:
        from openagents_cli.curator import register_cli as _register_curator_cli

        _register_curator_cli(curator_parser)
    except Exception as _exc:
        logging.getLogger(__name__).debug("curator CLI wiring failed: %s", _exc)

    # =========================================================================
    # pets command — petdex animated mascots (CLI / TUI / desktop display)
    # =========================================================================
    pets_parser = subparsers.add_parser(
        "pets",
        help="Browse, install, and select petdex animated pets",
        description=(
            "Petdex (https://github.com/crafter-station/petdex) is a public "
            "gallery of animated sprite pets for coding agents. Install one "
            "and OpenAgents shows it reacting to agent activity across the CLI, "
            "TUI, and desktop app."
        ),
    )
    try:
        from openagents_cli.pets import register_cli as _register_pets_cli

        _register_pets_cli(pets_parser)
    except Exception as _exc:
        logging.getLogger(__name__).debug("pets CLI wiring failed: %s", _exc)

    # =========================================================================
    # memory command  (parser built in openagents_cli/subcommands/memory.py)
    # =========================================================================
    build_memory_parser(subparsers, cmd_memory=cmd_memory)

    # =========================================================================
    # tools command  (parser built in openagents_cli/subcommands/tools.py)
    # =========================================================================
    build_tools_parser(subparsers, cmd_tools=cmd_tools)

    # =========================================================================
    # computer-use command — manage Computer Use (cua-driver) on macOS
    # =========================================================================
    computer_use_parser = subparsers.add_parser(
        "computer-use",
        help="Manage the Computer Use (cua-driver) backend (macOS/Windows/Linux)",
        description=(
            "Install or check the cua-driver binary used by the\n"
            "`computer_use` toolset. Supported on macOS, Windows, and\n"
            "Linux.\n\n"
            "Use `hermes computer-use install` to fetch and run the\n"
            "upstream cua-driver installer. This is equivalent to the\n"
            "post-setup hook that `hermes tools` runs when you first\n"
            "enable the Computer Use toolset, and is a stable target\n"
            "for re-running the install if it didn't fire (e.g. when\n"
            "toggling the toolset on a returning-user setup).\n\n"
            "Use `hermes computer-use doctor` to run cua-driver's\n"
            "`health_report` MCP tool and surface its check matrix\n"
            "(TCC, bundle identity, version, platform support, ...)\n"
            "in human-readable form."
        ),
    )
    computer_use_sub = computer_use_parser.add_subparsers(dest="computer_use_action")

    computer_use_install = computer_use_sub.add_parser(
        "install",
        help="Install or repair the cua-driver binary (macOS/Windows/Linux)",
    )
    computer_use_install.add_argument(
        "--upgrade",
        action="store_true",
        help=(
            "Re-run the upstream installer even if cua-driver is already on "
            "PATH. The upstream install.sh always pulls the latest release, "
            "so this performs an in-place upgrade."
        ),
    )
    computer_use_sub.add_parser(
        "status",
        help="Print whether cua-driver is installed and on PATH",
    )
    computer_use_doctor = computer_use_sub.add_parser(
        "doctor",
        help="Run cua-driver `health_report` and surface the check matrix",
        description=(
            "Drive cua-driver's stable `health_report` MCP tool and render\n"
            "its check matrix (TCC permissions, bundle identity, version,\n"
            "platform support, screenshot probe, …) as human-readable\n"
            "output. cua-driver owns the health model; this command stays\n"
            "thin so new checks added upstream surface here without code\n"
            "changes. Exits 0 when overall=ok, 1 when degraded/failed, 2\n"
            "when the binary is missing or unreachable."
        ),
    )
    computer_use_doctor.add_argument(
        "--include",
        action="append",
        default=[],
        metavar="CHECK",
        help=(
            "Run only the listed checks. Repeat for multiple "
            "(e.g. --include tcc_accessibility --include bundle_identity). "
            "Unknown names are reported by cua-driver."
        ),
    )
    computer_use_doctor.add_argument(
        "--skip",
        action="append",
        default=[],
        metavar="CHECK",
        help="Skip the listed checks. Repeat for multiple. Wins over --include.",
    )
    computer_use_doctor.add_argument(
        "--json",
        action="store_true",
        help="Emit the raw structured payload as JSON (same shape as `tools/call`).",
    )
    computer_use_perms = computer_use_sub.add_parser(
        "permissions",
        help="Check or grant macOS Accessibility + Screen Recording (macOS)",
        description=(
            "Computer Use drives the Mac through cua-driver, whose TCC grants\n"
            "attach to cua-driver's own identity (com.trycua.driver) — not the\n"
            "terminal or the OpenAgents app. `status` reports the driver's grant\n"
            "state; `grant` launches CuaDriver via LaunchServices so the macOS\n"
            "permission dialog is attributed to the process that does the work."
        ),
    )
    computer_use_perms_sub = computer_use_perms.add_subparsers(
        dest="computer_use_perms_action"
    )
    computer_use_perms_status = computer_use_perms_sub.add_parser(
        "status",
        help="Report Accessibility + Screen Recording grant state (read-only)",
    )
    computer_use_perms_status.add_argument(
        "--json",
        action="store_true",
        help="Emit the normalized permission payload as JSON.",
    )
    computer_use_perms_sub.add_parser(
        "grant",
        help="Request the grants (opens the dialog attributed to CuaDriver)",
    )

    def cmd_computer_use(args):
        action = getattr(args, "computer_use_action", None)
        if action == "install":
            from openagents_cli.tools_config import install_cua_driver
            install_cua_driver(upgrade=bool(getattr(args, "upgrade", False)))
            return
        if action == "status":
            import shutil
            import subprocess
            from openagents_cli.tools_config import _cua_driver_cmd
            # Honor HERMES_CUA_DRIVER_CMD for local-build testing — same
            # resolver `install_cua_driver` and the runtime backend use,
            # so `status` reports what `computer_use` will actually invoke.
            driver_cmd = _cua_driver_cmd()
            path = shutil.which(driver_cmd)
            if path:
                version = ""
                try:
                    from openagents_cli.tools_config import _cua_driver_env
                    version = subprocess.run(
                        [path, "--version"],
                        capture_output=True, text=True, timeout=5,
                        env=_cua_driver_env(),
                    ).stdout.strip()
                except Exception:
                    pass
                if version:
                    print(f"cua-driver: installed at {path} ({version})")
                else:
                    print(f"cua-driver: installed at {path}")
                try:
                    from tools.computer_use.cua_backend import cua_driver_update_check
                    st = cua_driver_update_check()
                    if st and st.get("update_available"):
                        latest = st.get("latest_version") or "?"
                        print(f"  ⬆ Update available: cua-driver {latest}.")
                        print("    Run: hermes computer-use install --upgrade")
                    elif st:
                        print("  ✓ Up to date.")
                    else:
                        # Older driver (no check-update verb) or offline.
                        print("  Refresh to latest: hermes computer-use install --upgrade")
                except Exception:
                    print("  Refresh to latest: hermes computer-use install --upgrade")
                return
            print("cua-driver: not installed")
            print("  Run: hermes computer-use install")
            return
        if action == "doctor":
            from tools.computer_use.doctor import run_doctor
            code = run_doctor(
                include=list(getattr(args, "include", []) or []),
                skip=list(getattr(args, "skip", []) or []),
                json_output=bool(getattr(args, "json", False)),
            )
            sys.exit(code)
        if action == "permissions":
            perms_action = getattr(args, "computer_use_perms_action", None)
            if perms_action == "grant":
                from tools.computer_use.permissions import request_permissions_grant
                sys.exit(request_permissions_grant())
            if perms_action == "status":
                import json as _json
                from tools.computer_use.permissions import computer_use_status
                st = computer_use_status()
                if bool(getattr(args, "json", False)):
                    print(_json.dumps(st, indent=2, sort_keys=True))
                    sys.exit(0 if st["ready"] else 1)
                if not st["platform_supported"]:
                    print(f"Computer Use is not supported on {st['platform']}.")
                    sys.exit(1)
                if not st["installed"]:
                    print("cua-driver: not installed. Run: hermes computer-use install")
                    sys.exit(1)
                glyph = lambda v: "✅" if v is True else ("❌" if v is False else "•")  # noqa: E731
                print(f"cua-driver: {st['version'] or 'installed'} ({st['platform']})")
                if st["can_grant"]:  # macOS TCC permissions
                    print(f"  {glyph(st['accessibility'])} Accessibility")
                    print(f"  {glyph(st['screen_recording'])} Screen Recording")
                    if not st["ready"]:
                        print("  Grant: hermes computer-use permissions grant")
                else:  # no TCC model — readiness is driver health
                    print(f"  {glyph(st['ready'])} driver health (no permission toggles on {st['platform']})")
                for c in st["checks"]:
                    if c["status"] != "ok":
                        print(f"  ⚠ {c['label']}: {c['message']}")
                if st["error"]:
                    print(f"  ⚠ {st['error']}")
                sys.exit(0 if st["ready"] else 1)
            computer_use_perms.print_help()
            return
        # No subcommand → show help
        computer_use_parser.print_help()

    computer_use_parser.set_defaults(func=cmd_computer_use)
    # =========================================================================
    # mcp command  (parser built in openagents_cli/subcommands/mcp.py)
    # =========================================================================
    build_mcp_parser(subparsers, cmd_mcp=cmd_mcp)

    # =========================================================================
    # sessions command
    # =========================================================================
    sessions_parser = subparsers.add_parser(
        "sessions",
        help="Manage session history (list, rename, export, prune, delete)",
        description="View and manage the SQLite session store",
    )
    sessions_subparsers = sessions_parser.add_subparsers(dest="sessions_action")

    sessions_list = sessions_subparsers.add_parser("list", help="List recent sessions")
    sessions_list.add_argument(
        "--source", help="Filter by source (cli, telegram, discord, etc.)"
    )
    sessions_list.add_argument(
        "--limit", type=int, default=20, help="Max sessions to show"
    )

    sessions_export = sessions_subparsers.add_parser(
        "export", help="Export sessions to a JSONL file"
    )
    sessions_export.add_argument(
        "output", help="Output JSONL file path (use - for stdout)"
    )
    sessions_export.add_argument("--source", help="Filter by source")
    sessions_export.add_argument("--session-id", help="Export a specific session")

    sessions_delete = sessions_subparsers.add_parser(
        "delete", help="Delete a specific session"
    )
    sessions_delete.add_argument("session_id", help="Session ID to delete")
    sessions_delete.add_argument(
        "--yes", "-y", action="store_true", help="Skip confirmation"
    )

    sessions_prune = sessions_subparsers.add_parser("prune", help="Delete old sessions")
    sessions_prune.add_argument(
        "--older-than",
        type=int,
        default=90,
        help="Delete sessions older than N days (default: 90)",
    )
    sessions_prune.add_argument("--source", help="Only prune sessions from this source")
    sessions_prune.add_argument(
        "--yes", "-y", action="store_true", help="Skip confirmation"
    )

    sessions_subparsers.add_parser(
        "optimize",
        help="Reclaim disk space: merge FTS5 segments + VACUUM (no data change)",
    )

    sessions_repair = sessions_subparsers.add_parser(
        "repair",
        help="Repair a malformed state.db schema so hidden sessions reappear",
        description=(
            "Recover a state.db whose schema is malformed (e.g. 'table "
            "messages_fts already exists'), which makes Desktop/Dashboard show "
            "no sessions. A backup is made first; sessions and messages are "
            "preserved and the FTS search index is rebuilt if needed."
        ),
    )
    sessions_repair.add_argument(
        "--check-only",
        action="store_true",
        help="Only report whether the database opens cleanly; do not modify it",
    )
    sessions_repair.add_argument(
        "--no-backup",
        action="store_true",
        help="Skip the timestamped backup copy (not recommended)",
    )

    sessions_subparsers.add_parser("stats", help="Show session store statistics")

    sessions_rename = sessions_subparsers.add_parser(
        "rename", help="Set or change a session's title"
    )
    sessions_rename.add_argument("session_id", help="Session ID to rename")
    sessions_rename.add_argument("title", nargs="+", help="New title for the session")

    sessions_browse = sessions_subparsers.add_parser(
        "browse",
        help="Interactive session picker — browse, search, and resume sessions",
    )
    sessions_browse.add_argument(
        "--source", help="Filter by source (cli, telegram, discord, etc.)"
    )
    sessions_browse.add_argument(
        "--limit", type=int, default=500, help="Max sessions to load (default: 500)"
    )

    def _confirm_prompt(prompt: str) -> bool:
        """Prompt for y/N confirmation, safe against non-TTY environments."""
        try:
            return input(prompt).strip().lower() in {"y", "yes"}
        except (EOFError, KeyboardInterrupt):
            return False

    def cmd_sessions(args):
        import json as _json

        action = args.sessions_action

        # 'repair' must run BEFORE opening SessionDB(): a malformed schema is
        # exactly the case where SessionDB() can't open, so it operates on the
        # raw file path instead.
        if action == "repair":
            from openagents_state import (
                DEFAULT_DB_PATH,
                _db_opens_cleanly,
                repair_state_db_schema,
            )

            db_path = DEFAULT_DB_PATH
            if not db_path.exists():
                print(f"No session database at {db_path} (nothing to repair).")
                return
            reason = _db_opens_cleanly(db_path)
            if reason is None:
                print(f"✓ {db_path} opens cleanly — no repair needed.")
                return
            print(f"✗ {db_path} does not open cleanly: {reason}")
            if getattr(args, "check_only", False):
                return
            print("Repairing (a backup copy is made first)…")
            report = repair_state_db_schema(
                db_path, backup=not getattr(args, "no_backup", False)
            )
            if report.get("repaired"):
                if report.get("backup_path"):
                    print(f"  backup: {report['backup_path']}")
                print(f"  strategy: {report.get('strategy')}")
                try:
                    from openagents_state import SessionDB

                    n = SessionDB()._conn.execute(
                        "SELECT COUNT(*) FROM sessions"
                    ).fetchone()[0]
                    print(f"✓ Repaired — {n} sessions recovered.")
                except Exception:
                    print("✓ Repaired.")
            else:
                print(f"✗ Repair failed: {report.get('error')}")
                if report.get("backup_path"):
                    print(f"  A backup is preserved at: {report['backup_path']}")
                print("  Keep state.db and the backup; do not delete them.")
            return

        try:
            from openagents_state import SessionDB

            db = SessionDB()
        except Exception as e:
            print(f"Error: Could not open session database: {e}")
            return

        # Hide third-party tool sessions by default, but honour explicit --source
        _source = getattr(args, "source", None)
        _exclude = None if _source else ["tool"]

        if action == "list":
            sessions = db.list_sessions_rich(
                source=args.source, exclude_sources=_exclude, limit=args.limit
            )
            if not sessions:
                print("No sessions found.")
                return
            has_titles = any(s.get("title") for s in sessions)
            if has_titles:
                print(f"{'Title':<32} {'Preview':<40} {'Last Active':<13} {'ID'}")
                print("─" * 110)
            else:
                print(f"{'Preview':<50} {'Last Active':<13} {'Src':<6} {'ID'}")
                print("─" * 95)
            for s in sessions:
                last_active = _relative_time(s.get("last_active"))
                preview = (
                    s.get("preview", "")[:38]
                    if has_titles
                    else s.get("preview", "")[:48]
                )
                if has_titles:
                    title = (s.get("title") or "—")[:30]
                    sid = s["id"]
                    print(f"{title:<32} {preview:<40} {last_active:<13} {sid}")
                else:
                    sid = s["id"]
                    print(f"{preview:<50} {last_active:<13} {s['source']:<6} {sid}")

        elif action == "export":
            if args.session_id:
                resolved_session_id = db.resolve_session_id(args.session_id)
                if not resolved_session_id:
                    print(f"Session '{args.session_id}' not found.")
                    return
                data = db.export_session(resolved_session_id)
                if not data:
                    print(f"Session '{args.session_id}' not found.")
                    return
                line = _json.dumps(data, ensure_ascii=False) + "\n"
                if args.output == "-":

                    sys.stdout.write(line)
                else:
                    with open(args.output, "w", encoding="utf-8") as f:
                        f.write(line)
                    print(f"Exported 1 session to {args.output}")
            else:
                sessions = db.export_all(source=args.source)
                if args.output == "-":

                    for s in sessions:
                        sys.stdout.write(_json.dumps(s, ensure_ascii=False) + "\n")
                else:
                    with open(args.output, "w", encoding="utf-8") as f:
                        for s in sessions:
                            f.write(_json.dumps(s, ensure_ascii=False) + "\n")
                    print(f"Exported {len(sessions)} sessions to {args.output}")

        elif action == "delete":
            resolved_session_id = db.resolve_session_id(args.session_id)
            if not resolved_session_id:
                print(f"Session '{args.session_id}' not found.")
                return
            if not args.yes:
                if not _confirm_prompt(
                    f"Delete session '{resolved_session_id}' and all its messages? [y/N] "
                ):
                    print("Cancelled.")
                    return
            sessions_dir = get_openagents_home() / "sessions"
            if db.delete_session(resolved_session_id, sessions_dir=sessions_dir):
                print(f"Deleted session '{resolved_session_id}'.")
            else:
                print(f"Session '{args.session_id}' not found.")

        elif action == "prune":
            days = args.older_than
            source_msg = f" from '{args.source}'" if args.source else ""
            if not args.yes:
                if not _confirm_prompt(
                    f"Delete all ended sessions older than {days} days{source_msg}? [y/N] "
                ):
                    print("Cancelled.")
                    return
            sessions_dir = get_openagents_home() / "sessions"
            count = db.prune_sessions(
                older_than_days=days, source=args.source, sessions_dir=sessions_dir
            )
            print(f"Pruned {count} session(s).")

        elif action == "rename":
            resolved_session_id = db.resolve_session_id(args.session_id)
            if not resolved_session_id:
                print(f"Session '{args.session_id}' not found.")
                return
            title = " ".join(args.title)
            try:
                if db.set_session_title(resolved_session_id, title):
                    print(f"Session '{resolved_session_id}' renamed to: {title}")
                else:
                    print(f"Session '{args.session_id}' not found.")
            except ValueError as e:
                print(f"Error: {e}")

        elif action == "browse":
            limit = getattr(args, "limit", 500) or 500
            source = getattr(args, "source", None)
            _browse_exclude = None if source else ["tool"]
            sessions = db.list_sessions_rich(
                source=source, exclude_sources=_browse_exclude, limit=limit
            )
            db.close()
            if not sessions:
                print("No sessions found.")
                return

            selected_id = _session_browse_picker(sessions)
            if not selected_id:
                print("Cancelled.")
                return

            # Launch hermes --resume <id> by replacing the current process
            print(f"Resuming session: {selected_id}")
            from openagents_cli.relaunch import relaunch

            relaunch(["--resume", selected_id])
            return  # won't reach here after execvp

        elif action == "optimize":
            db_path = db.db_path
            before_mb = (
                os.path.getsize(db_path) / (1024 * 1024)
                if db_path.exists()
                else 0.0
            )
            print("Optimizing session store (FTS merge + VACUUM)…")
            try:
                # vacuum() merges FTS5 segments (optimize_fts) then VACUUMs,
                # and returns the number of indexes it merged.
                n = db.vacuum()
            except Exception as e:
                print(f"Error: optimization failed: {e}")
                db.close()
                return
            after_mb = (
                os.path.getsize(db_path) / (1024 * 1024)
                if db_path.exists()
                else 0.0
            )
            saved = before_mb - after_mb
            print(f"Optimized {n} FTS index(es).")
            print(
                f"Database size: {before_mb:.1f} MB -> {after_mb:.1f} MB "
                f"(reclaimed {saved:.1f} MB)"
            )

        elif action == "stats":
            total = db.session_count()
            msgs = db.message_count()
            print(f"Total sessions: {total}")
            print(f"Total messages: {msgs}")
            for src in ["cli", "telegram", "discord", "whatsapp", "slack"]:
                c = db.session_count(source=src)
                if c > 0:
                    print(f"  {src}: {c} sessions")
            db_path = db.db_path
            if db_path.exists():
                size_mb = os.path.getsize(db_path) / (1024 * 1024)
                print(f"Database size: {size_mb:.1f} MB")

        else:
            sessions_parser.print_help()

        db.close()

    sessions_parser.set_defaults(func=cmd_sessions)

    # =========================================================================
    # insights command  (parser built in openagents_cli/subcommands/insights.py)
    # =========================================================================
    build_insights_parser(subparsers, cmd_insights=cmd_insights)

    # =========================================================================
    # claw command  (parser built in openagents_cli/subcommands/claw.py)
    # =========================================================================
    build_claw_parser(subparsers, cmd_claw=cmd_claw)

    # =========================================================================
    # version command  (parser built in openagents_cli/subcommands/version.py)
    # =========================================================================
    build_version_parser(subparsers, cmd_version=cmd_version)

    # =========================================================================
    # update command  (parser built in openagents_cli/subcommands/update.py)
    # =========================================================================
    build_update_parser(subparsers, cmd_update=cmd_update)

    # =========================================================================
    # uninstall command  (parser built in openagents_cli/subcommands/uninstall.py)
    # =========================================================================
    build_uninstall_parser(subparsers, cmd_uninstall=cmd_uninstall)

    # =========================================================================
    # acp command  (parser built in openagents_cli/subcommands/acp.py)
    # =========================================================================
    build_acp_parser(subparsers, cmd_acp=cmd_acp)

    # =========================================================================
    # profile command  (parser built in openagents_cli/subcommands/profile.py)
    # =========================================================================
    build_profile_parser(subparsers, cmd_profile=cmd_profile)

    # =========================================================================
    # completion command
    # =========================================================================
    completion_parser = subparsers.add_parser(
        "completion",
        help="Print shell completion script (bash, zsh, or fish)",
    )
    completion_parser.add_argument(
        "shell",
        nargs="?",
        default="bash",
        choices=["bash", "zsh", "fish"],
        help="Shell type (default: bash)",
    )
    completion_parser.set_defaults(func=lambda args: cmd_completion(args, parser))

    # =========================================================================
    # dashboard command  (parser built in openagents_cli/subcommands/dashboard.py)
    # =========================================================================
    build_dashboard_parser(
        subparsers,
        cmd_dashboard=cmd_dashboard,
        cmd_dashboard_register=cmd_dashboard_register,
    )


    # =========================================================================
    # desktop (a.k.a. gui) command
    #
    # The canonical name is "desktop"; "gui" is kept as a deprecated alias
    # for one release. The Hermes-Setup.exe success screen tells users to
    # run `hermes desktop` from a terminal, so the canonical name needs
    # to be the one that appears in --help (argparse promotes the primary
    # name; aliases stay hidden).
    # =========================================================================
    # gui command  (parser built in openagents_cli/subcommands/gui.py)
    # =========================================================================
    build_gui_parser(subparsers, cmd_gui=cmd_gui)

    # =========================================================================
    # logs command  (parser built in openagents_cli/subcommands/logs.py)
    # =========================================================================
    build_logs_parser(subparsers, cmd_logs=cmd_logs)

    # =========================================================================
    # prompt-size command  (parser built in openagents_cli/subcommands/prompt_size.py)
    # =========================================================================
    build_prompt_size_parser(subparsers, cmd_prompt_size=cmd_prompt_size)

    # =========================================================================
    # Parse and execute
    # =========================================================================
    # Pre-process argv so unquoted multi-word session names after -c / -r
    # are merged into a single token before argparse sees them.
    # e.g. ``hermes -c Pokemon Agent Dev`` → ``hermes -c 'Pokemon Agent Dev'``
    # ── Container-aware routing ────────────────────────────────────────
    # When NixOS container mode is active, route ALL subcommands into
    # the managed container.  This MUST run before parse_args() so that
    # --help, unrecognised flags, and every subcommand are forwarded
    # transparently instead of being intercepted by argparse on the host.
||||||| cf299e9a01
    parser, subparsers, chat_parser = build_top_level_parser()
    chat_parser.set_defaults(func=cmd_chat)

    # =========================================================================
    # model command  (parser built in openagents_cli/subcommands/model.py)
    # =========================================================================
    build_model_parser(subparsers, cmd_model=cmd_model)

    from openagents_cli.moa_cmd import cmd_moa

    moa_parser = subparsers.add_parser(
        "moa",
        help="Configure Mixture of Agents provider/model slots",
        description="Configure the provider/model set used by /moa <prompt>.",
    )
    moa_subparsers = moa_parser.add_subparsers(dest="moa_command")
    moa_subparsers.add_parser("list", aliases=["ls"], help="Show current MoA model slots")
    moa_configure = moa_subparsers.add_parser("configure", aliases=["config"], help="Interactively pick MoA models")
    moa_configure.add_argument("name", nargs="?", help="Preset name to create or update")
    moa_delete = moa_subparsers.add_parser("delete", aliases=["rm"], help="Delete a MoA preset")
    moa_delete.add_argument("name", help="Preset name to delete")
    moa_parser.set_defaults(func=cmd_moa)

    # =========================================================================
    # fallback command — manage the fallback provider chain
    # =========================================================================
    from openagents_cli.fallback_cmd import cmd_fallback

    fallback_parser = subparsers.add_parser(
        "fallback",
        help="Manage fallback providers (tried when the primary model fails)",
        description=(
            "Manage the fallback provider chain.  Fallback providers are tried "
            "in order when the primary model fails with rate-limit, overload, or "
            "connection errors.  See: "
            "https://openagents.nousresearch.com/docs/user-guide/features/fallback-providers"
        ),
    )
    fallback_subparsers = fallback_parser.add_subparsers(dest="fallback_command")
    fallback_subparsers.add_parser(
        "list",
        aliases=["ls"],
        help="Show the current fallback chain (default when no subcommand)",
    )
    fallback_subparsers.add_parser(
        "add",
        help="Pick a provider + model (same picker as `hermes model`) and append to the chain",
    )
    fallback_subparsers.add_parser(
        "remove",
        aliases=["rm"],
        help="Pick an entry to delete from the chain",
    )
    fallback_subparsers.add_parser(
        "clear",
        help="Remove all fallback entries",
    )
    fallback_parser.set_defaults(func=cmd_fallback)

    # =========================================================================
    # secrets command — external secret managers (currently: Bitwarden)
    # =========================================================================
    secrets_parser = subparsers.add_parser(
        "secrets",
        help="Manage external secret sources (Bitwarden Secrets Manager)",
        description=(
            "Pull API keys from an external secret manager at process startup "
            "instead of storing them in ~/.openagents/.env.  Currently supports "
            "Bitwarden Secrets Manager.  See: "
            "https://openagents.nousresearch.com/docs/user-guide/secrets/bitwarden"
        ),
    )
    secrets_subparsers = secrets_parser.add_subparsers(dest="secrets_command")

    secrets_bw = secrets_subparsers.add_parser(
        "bitwarden",
        aliases=["bw"],
        help="Bitwarden Secrets Manager integration",
    )

    # Lazy import — only pays for itself when this subcommand is actually used.
    from openagents_cli import secrets_cli as _secrets_cli

    _secrets_cli.register_cli(secrets_bw)

    def _dispatch_secrets(args):  # noqa: ANN001
        sub = getattr(args, "secrets_command", None)
        bw_sub = getattr(args, "secrets_bw_command", None)
        if sub in ("bitwarden", "bw") and bw_sub is not None:
            return args.func(args)
        secrets_parser.print_help()
        return 0

    secrets_parser.set_defaults(func=_dispatch_secrets)

    # =========================================================================
    # migrate command
    # =========================================================================
    from openagents_cli.migrate import cmd_migrate, cmd_migrate_xai

    migrate_parser = subparsers.add_parser(
        "migrate",
        help="Migrate configuration for retired models or deprecated settings",
        description=(
            "Diagnose and (optionally) rewrite the active config.yaml to "
            "replace references to retired models or deprecated settings."
        ),
    )
    migrate_subparsers = migrate_parser.add_subparsers(dest="migrate_type")

    migrate_xai = migrate_subparsers.add_parser(
        "xai",
        help="Migrate xAI models scheduled for retirement on May 15, 2026",
        description=(
            "Scan config.yaml for references to xAI models retiring on "
            "May 15, 2026 and, with --apply, rewrite them in-place to the "
            "official replacements per the xAI migration guide. The original "
            "config.yaml is backed up before any rewrite."
        ),
    )
    migrate_xai.add_argument(
        "--apply",
        action="store_true",
        help="Rewrite config.yaml in-place (default: dry-run, no writes)",
    )
    migrate_xai.add_argument(
        "--no-backup",
        action="store_true",
        help="Skip the timestamped backup of config.yaml when applying",
    )
    migrate_xai.set_defaults(func=cmd_migrate_xai)
    migrate_parser.set_defaults(func=cmd_migrate)

    # =========================================================================
    # gateway + proxy commands  (parsers built in openagents_cli/subcommands/gateway.py)
    # =========================================================================
    build_gateway_parser(
        subparsers, cmd_gateway=cmd_gateway, cmd_proxy=cmd_proxy, cmd_gateway_enroll=cmd_gateway_enroll
    )

    # =========================================================================
    # lsp command
    # =========================================================================
    try:
        from agent.lsp.cli import register_subparser as _lsp_register
        _lsp_register(subparsers)
    except Exception as _lsp_err:  # noqa: BLE001
        # LSP is optional infrastructure — never let a registration
        # failure break the CLI overall.
        logger.debug("LSP CLI registration failed: %s", _lsp_err)

    # =========================================================================
    # setup command  (parser built in openagents_cli/subcommands/setup.py)
    # =========================================================================
    build_setup_parser(subparsers, cmd_setup=cmd_setup)

    # =========================================================================
    # postinstall command  (parser built in openagents_cli/subcommands/postinstall.py)
    # =========================================================================
    build_postinstall_parser(subparsers, cmd_postinstall=cmd_postinstall)

    # =========================================================================
    # whatsapp command  (parser built in openagents_cli/subcommands/whatsapp.py)
    # =========================================================================
    build_whatsapp_parser(subparsers, cmd_whatsapp=cmd_whatsapp)

    # =========================================================================
    # whatsapp-cloud command (official Meta Cloud API; complement to Baileys)
    # =========================================================================
    whatsapp_cloud_parser = subparsers.add_parser(
        "whatsapp-cloud",
        help="Set up WhatsApp Business Cloud API integration",
        description=(
            "Configure the official Meta WhatsApp Business Cloud API "
            "adapter (Business account required, public webhook URL "
            "required). Distinct from `hermes whatsapp` which sets up "
            "the Baileys bridge for personal accounts."
        ),
    )
    whatsapp_cloud_parser.set_defaults(func=cmd_whatsapp_cloud)

    # =========================================================================
    # slack command  (parser built in openagents_cli/subcommands/slack.py)
    # =========================================================================
    build_slack_parser(subparsers, cmd_slack=cmd_slack)

    # =========================================================================
    # send command — pipe shell-script output to any configured platform
    # =========================================================================
    from openagents_cli.send_cmd import register_send_subparser
    register_send_subparser(subparsers)

    # =========================================================================
    # login command  (parser built in openagents_cli/subcommands/login.py)
    # =========================================================================
    build_login_parser(subparsers, cmd_login=cmd_login)

    # =========================================================================
    # logout command  (parser built in openagents_cli/subcommands/logout.py)
    # =========================================================================
    build_logout_parser(subparsers, cmd_logout=cmd_logout)

    # =========================================================================
    # auth command  (parser built in openagents_cli/subcommands/auth.py)
    # =========================================================================
    build_auth_parser(subparsers, cmd_auth=cmd_auth)

    # =========================================================================
    # status command  (parser built in openagents_cli/subcommands/status.py)
    # =========================================================================
    build_status_parser(subparsers, cmd_status=cmd_status)

    # =========================================================================
    # cron command  (parser built in openagents_cli/subcommands/cron.py)
    # =========================================================================
    build_cron_parser(subparsers, cmd_cron=cmd_cron)

    # =========================================================================
    # webhook command  (parser built in openagents_cli/subcommands/webhook.py)
    # =========================================================================
    build_webhook_parser(subparsers, cmd_webhook=cmd_webhook)

    # =========================================================================
    # portal command — Nous Portal status + Tool Gateway routing
    # =========================================================================
    from openagents_cli.portal_cli import add_parser as _add_portal_parser
    _add_portal_parser(subparsers)

    # =========================================================================
    # kanban command — multi-profile collaboration board
    # =========================================================================
    from openagents_cli.kanban import build_parser as _build_kanban_parser

    kanban_parser = _build_kanban_parser(subparsers)
    kanban_parser.set_defaults(func=cmd_kanban)

    # =========================================================================
    # project command — named, multi-folder workspaces
    # =========================================================================
    from openagents_cli.projects_cmd import build_parser as _build_project_parser

    project_parser = _build_project_parser(subparsers)
    project_parser.set_defaults(func=cmd_project)

    # =========================================================================
    # hooks command — shell-hook inspection and management
    # =========================================================================
    # hooks command  (parser built in openagents_cli/subcommands/hooks.py)
    # =========================================================================
    build_hooks_parser(subparsers, cmd_hooks=cmd_hooks)

    # =========================================================================
    # doctor command  (parser built in openagents_cli/subcommands/doctor.py)
    # =========================================================================
    build_doctor_parser(subparsers, cmd_doctor=cmd_doctor)

    # =========================================================================
    # security command — on-demand supply-chain audit
    # =========================================================================
    # security command  (parser built in openagents_cli/subcommands/security.py)
    # =========================================================================
    build_security_parser(subparsers, cmd_security=cmd_security)

    # =========================================================================
    # dump command  (parser built in openagents_cli/subcommands/dump.py)
    # =========================================================================
    build_dump_parser(subparsers, cmd_dump=cmd_dump)

    # =========================================================================
    # debug command  (parser built in openagents_cli/subcommands/debug.py)
    # =========================================================================
    build_debug_parser(subparsers, cmd_debug=cmd_debug)

    # =========================================================================
    # backup command  (parser built in openagents_cli/subcommands/backup.py)
    # =========================================================================
    build_backup_parser(subparsers, cmd_backup=cmd_backup)

    # =========================================================================
    # checkpoints command
    # =========================================================================
    checkpoints_parser = subparsers.add_parser(
        "checkpoints",
        help="Inspect / prune / clear ~/.openagents/checkpoints/",
        description="Manage the filesystem checkpoint store — the shadow git "
        "repo hermes uses to snapshot working directories before "
        "write_file/patch/terminal calls. Lets you see how much "
        "space checkpoints occupy, force a prune, or wipe the base.",
    )
    from openagents_cli.checkpoints import register_cli as _register_checkpoints_cli
    _register_checkpoints_cli(checkpoints_parser)

    # =========================================================================
    # import command  (parser built in openagents_cli/subcommands/import_cmd.py)
    # =========================================================================
    build_import_cmd_parser(subparsers, cmd_import=cmd_import)

    # =========================================================================
    # config command  (parser built in openagents_cli/subcommands/config.py)
    # =========================================================================
    build_config_parser(subparsers, cmd_config=cmd_config)

    # =========================================================================
    # pairing command  (parser built in openagents_cli/subcommands/pairing.py)
    # =========================================================================
    build_pairing_parser(subparsers, cmd_pairing=cmd_pairing)

    # =========================================================================
    # skills command  (parser built in openagents_cli/subcommands/skills.py)
    # =========================================================================
    build_skills_parser(subparsers, cmd_skills=cmd_skills)

    # =========================================================================
    # bundles command — skill bundles (alias /<name> for multiple skills)
    # =========================================================================
    bundles_parser = subparsers.add_parser(
        "bundles",
        help="Create, list, and manage skill bundles (aliases for multiple skills)",
        description=(
            "Skill bundles let you load several skills under one slash "
            "command. `/<bundle>` from the CLI or gateway loads every "
            "referenced skill at once."
        ),
    )
    from openagents_cli.bundles import register_cli as _bundles_register, bundles_command
    _bundles_register(bundles_parser)
    bundles_parser.set_defaults(func=bundles_command)

    # =========================================================================
    # plugins command  (parser built in openagents_cli/subcommands/plugins.py)
    # =========================================================================
    build_plugins_parser(subparsers, cmd_plugins=cmd_plugins)

    # =========================================================================
    # Plugin CLI commands — dynamically registered by memory/general plugins.
    # Plugins provide a register_cli(subparser) function that builds their
    # own argparse tree.  No hardcoded plugin commands in main.py.
    #
    # Skipped when the invocation is already targeting a known built-in
    # subcommand — ``hermes --help``, ``hermes version``, ``hermes logs``,
    # etc.  This avoids eagerly importing every bundled plugin module
    # (google.cloud.pubsub_v1, aiohttp, grpc, PIL …) which costs
    # 500-650ms on typical installs.
    # =========================================================================
    if _plugin_cli_discovery_needed():
        try:
            from plugins.memory import discover_plugin_cli_commands
            from openagents_cli.plugins import discover_plugins, get_plugin_manager

            seen_plugin_commands = set()
            for cmd_info in discover_plugin_cli_commands():
                plugin_parser = subparsers.add_parser(
                    cmd_info["name"],
                    help=cmd_info["help"],
                    description=cmd_info.get("description", ""),
                    formatter_class=__import__("argparse").RawDescriptionHelpFormatter,
                )
                cmd_info["setup_fn"](plugin_parser)
                if cmd_info.get("handler_fn") is not None:
                    plugin_parser.set_defaults(func=cmd_info["handler_fn"])
                seen_plugin_commands.add(cmd_info["name"])

            discover_plugins()
            for cmd_info in get_plugin_manager()._cli_commands.values():
                if cmd_info["name"] in seen_plugin_commands:
                    continue
                plugin_parser = subparsers.add_parser(
                    cmd_info["name"],
                    help=cmd_info["help"],
                    description=cmd_info.get("description", ""),
                    formatter_class=__import__("argparse").RawDescriptionHelpFormatter,
                )
                cmd_info["setup_fn"](plugin_parser)
                if cmd_info.get("handler_fn") is not None:
                    plugin_parser.set_defaults(func=cmd_info["handler_fn"])
        except Exception as _exc:
            logging.getLogger(__name__).debug("Plugin CLI discovery failed: %s", _exc)

    # =========================================================================
    # curator command — background skill maintenance
    # =========================================================================
    curator_parser = subparsers.add_parser(
        "curator",
        help="Background skill maintenance (curator) — status, run, pause, pin",
        description=(
            "The curator is an auxiliary-model background task that "
            "periodically reviews agent-created skills, prunes stale ones, "
            "consolidates overlaps, and archives obsolete skills. "
            "Bundled and hub-installed skills are never touched. "
            "Archives are recoverable; auto-deletion never happens."
        ),
    )
    try:
        from openagents_cli.curator import register_cli as _register_curator_cli

        _register_curator_cli(curator_parser)
    except Exception as _exc:
        logging.getLogger(__name__).debug("curator CLI wiring failed: %s", _exc)

    # =========================================================================
    # pets command — petdex animated mascots (CLI / TUI / desktop display)
    # =========================================================================
    pets_parser = subparsers.add_parser(
        "pets",
        help="Browse, install, and select petdex animated pets",
        description=(
            "Petdex (https://github.com/crafter-station/petdex) is a public "
            "gallery of animated sprite pets for coding agents. Install one "
            "and OpenAgents shows it reacting to agent activity across the CLI, "
            "TUI, and desktop app."
        ),
    )
    try:
        from openagents_cli.pets import register_cli as _register_pets_cli

        _register_pets_cli(pets_parser)
    except Exception as _exc:
        logging.getLogger(__name__).debug("pets CLI wiring failed: %s", _exc)

    # =========================================================================
    # memory command  (parser built in openagents_cli/subcommands/memory.py)
    # =========================================================================
    build_memory_parser(subparsers, cmd_memory=cmd_memory)

    # =========================================================================
    # tools command  (parser built in openagents_cli/subcommands/tools.py)
    # =========================================================================
    build_tools_parser(subparsers, cmd_tools=cmd_tools)

    # =========================================================================
    # computer-use command — manage Computer Use (cua-driver) on macOS
    # =========================================================================
    computer_use_parser = subparsers.add_parser(
        "computer-use",
        help="Manage the Computer Use (cua-driver) backend (macOS/Windows/Linux)",
        description=(
            "Install or check the cua-driver binary used by the\n"
            "`computer_use` toolset. Supported on macOS, Windows, and\n"
            "Linux.\n\n"
            "Use `hermes computer-use install` to fetch and run the\n"
            "upstream cua-driver installer. This is equivalent to the\n"
            "post-setup hook that `hermes tools` runs when you first\n"
            "enable the Computer Use toolset, and is a stable target\n"
            "for re-running the install if it didn't fire (e.g. when\n"
            "toggling the toolset on a returning-user setup).\n\n"
            "Use `hermes computer-use doctor` to run cua-driver's\n"
            "`health_report` MCP tool and surface its check matrix\n"
            "(TCC, bundle identity, version, platform support, ...)\n"
            "in human-readable form."
        ),
    )
    computer_use_sub = computer_use_parser.add_subparsers(dest="computer_use_action")

    computer_use_install = computer_use_sub.add_parser(
        "install",
        help="Install or repair the cua-driver binary (macOS/Windows/Linux)",
    )
    computer_use_install.add_argument(
        "--upgrade",
        action="store_true",
        help=(
            "Re-run the upstream installer even if cua-driver is already on "
            "PATH. The upstream install.sh always pulls the latest release, "
            "so this performs an in-place upgrade."
        ),
    )
    computer_use_sub.add_parser(
        "status",
        help="Print whether cua-driver is installed and on PATH",
    )
    computer_use_doctor = computer_use_sub.add_parser(
        "doctor",
        help="Run cua-driver `health_report` and surface the check matrix",
        description=(
            "Drive cua-driver's stable `health_report` MCP tool and render\n"
            "its check matrix (TCC permissions, bundle identity, version,\n"
            "platform support, screenshot probe, …) as human-readable\n"
            "output. cua-driver owns the health model; this command stays\n"
            "thin so new checks added upstream surface here without code\n"
            "changes. Exits 0 when overall=ok, 1 when degraded/failed, 2\n"
            "when the binary is missing or unreachable."
        ),
    )
    computer_use_doctor.add_argument(
        "--include",
        action="append",
        default=[],
        metavar="CHECK",
        help=(
            "Run only the listed checks. Repeat for multiple "
            "(e.g. --include tcc_accessibility --include bundle_identity). "
            "Unknown names are reported by cua-driver."
        ),
    )
    computer_use_doctor.add_argument(
        "--skip",
        action="append",
        default=[],
        metavar="CHECK",
        help="Skip the listed checks. Repeat for multiple. Wins over --include.",
    )
    computer_use_doctor.add_argument(
        "--json",
        action="store_true",
        help="Emit the raw structured payload as JSON (same shape as `tools/call`).",
    )
    computer_use_perms = computer_use_sub.add_parser(
        "permissions",
        help="Check or grant macOS Accessibility + Screen Recording (macOS)",
        description=(
            "Computer Use drives the Mac through cua-driver, whose TCC grants\n"
            "attach to cua-driver's own identity (com.trycua.driver) — not the\n"
            "terminal or the OpenAgents app. `status` reports the driver's grant\n"
            "state; `grant` launches CuaDriver via LaunchServices so the macOS\n"
            "permission dialog is attributed to the process that does the work."
        ),
    )
    computer_use_perms_sub = computer_use_perms.add_subparsers(
        dest="computer_use_perms_action"
    )
    computer_use_perms_status = computer_use_perms_sub.add_parser(
        "status",
        help="Report Accessibility + Screen Recording grant state (read-only)",
    )
    computer_use_perms_status.add_argument(
        "--json",
        action="store_true",
        help="Emit the normalized permission payload as JSON.",
    )
    computer_use_perms_sub.add_parser(
        "grant",
        help="Request the grants (opens the dialog attributed to CuaDriver)",
    )

    def cmd_computer_use(args):
        action = getattr(args, "computer_use_action", None)
        if action == "install":
            from openagents_cli.tools_config import install_cua_driver
            install_cua_driver(upgrade=bool(getattr(args, "upgrade", False)))
            return
        if action == "status":
            import shutil
            import subprocess
            from openagents_cli.tools_config import _cua_driver_cmd
            # Honor HERMES_CUA_DRIVER_CMD for local-build testing — same
            # resolver `install_cua_driver` and the runtime backend use,
            # so `status` reports what `computer_use` will actually invoke.
            driver_cmd = _cua_driver_cmd()
            path = shutil.which(driver_cmd)
            if path:
                version = ""
                try:
                    from openagents_cli.tools_config import _cua_driver_env
                    version = subprocess.run(
                        [path, "--version"],
                        capture_output=True, text=True, timeout=5,
                        env=_cua_driver_env(),
                    ).stdout.strip()
                except Exception:
                    pass
                if version:
                    print(f"cua-driver: installed at {path} ({version})")
                else:
                    print(f"cua-driver: installed at {path}")
                try:
                    from tools.computer_use.cua_backend import cua_driver_update_check
                    st = cua_driver_update_check()
                    if st and st.get("update_available"):
                        latest = st.get("latest_version") or "?"
                        print(f"  ⬆ Update available: cua-driver {latest}.")
                        print("    Run: hermes computer-use install --upgrade")
                    elif st:
                        print("  ✓ Up to date.")
                    else:
                        # Older driver (no check-update verb) or offline.
                        print("  Refresh to latest: hermes computer-use install --upgrade")
                except Exception:
                    print("  Refresh to latest: hermes computer-use install --upgrade")
                return
            print("cua-driver: not installed")
            print("  Run: hermes computer-use install")
            return
        if action == "doctor":
            from tools.computer_use.doctor import run_doctor
            code = run_doctor(
                include=list(getattr(args, "include", []) or []),
                skip=list(getattr(args, "skip", []) or []),
                json_output=bool(getattr(args, "json", False)),
            )
            sys.exit(code)
        if action == "permissions":
            perms_action = getattr(args, "computer_use_perms_action", None)
            if perms_action == "grant":
                from tools.computer_use.permissions import request_permissions_grant
                sys.exit(request_permissions_grant())
            if perms_action == "status":
                import json as _json
                from tools.computer_use.permissions import computer_use_status
                st = computer_use_status()
                if bool(getattr(args, "json", False)):
                    print(_json.dumps(st, indent=2, sort_keys=True))
                    sys.exit(0 if st["ready"] else 1)
                if not st["platform_supported"]:
                    print(f"Computer Use is not supported on {st['platform']}.")
                    sys.exit(1)
                if not st["installed"]:
                    print("cua-driver: not installed. Run: hermes computer-use install")
                    sys.exit(1)
                glyph = lambda v: "✅" if v is True else ("❌" if v is False else "•")  # noqa: E731
                print(f"cua-driver: {st['version'] or 'installed'} ({st['platform']})")
                if st["can_grant"]:  # macOS TCC permissions
                    print(f"  {glyph(st['accessibility'])} Accessibility")
                    print(f"  {glyph(st['screen_recording'])} Screen Recording")
                    if not st["ready"]:
                        print("  Grant: hermes computer-use permissions grant")
                else:  # no TCC model — readiness is driver health
                    print(f"  {glyph(st['ready'])} driver health (no permission toggles on {st['platform']})")
                for c in st["checks"]:
                    if c["status"] != "ok":
                        print(f"  ⚠ {c['label']}: {c['message']}")
                if st["error"]:
                    print(f"  ⚠ {st['error']}")
                sys.exit(0 if st["ready"] else 1)
            computer_use_perms.print_help()
            return
        # No subcommand → show help
        computer_use_parser.print_help()

    computer_use_parser.set_defaults(func=cmd_computer_use)
    # =========================================================================
    # mcp command  (parser built in openagents_cli/subcommands/mcp.py)
    # =========================================================================
    build_mcp_parser(subparsers, cmd_mcp=cmd_mcp)

    # =========================================================================
    # sessions command
    # =========================================================================
    sessions_parser = subparsers.add_parser(
        "sessions",
        help="Manage session history (list, rename, export, prune, delete)",
        description="View and manage the SQLite session store",
    )
    sessions_subparsers = sessions_parser.add_subparsers(dest="sessions_action")

    sessions_list = sessions_subparsers.add_parser("list", help="List recent sessions")
    sessions_list.add_argument(
        "--source", help="Filter by source (cli, telegram, discord, etc.)"
    )
    sessions_list.add_argument(
        "--limit", type=int, default=20, help="Max sessions to show"
    )

    sessions_export = sessions_subparsers.add_parser(
        "export", help="Export sessions to a JSONL file"
    )
    sessions_export.add_argument(
        "output", help="Output JSONL file path (use - for stdout)"
    )
    sessions_export.add_argument("--source", help="Filter by source")
    sessions_export.add_argument("--session-id", help="Export a specific session")

    sessions_delete = sessions_subparsers.add_parser(
        "delete", help="Delete a specific session"
    )
    sessions_delete.add_argument("session_id", help="Session ID to delete")
    sessions_delete.add_argument(
        "--yes", "-y", action="store_true", help="Skip confirmation"
    )

    sessions_prune = sessions_subparsers.add_parser("prune", help="Delete old sessions")
    sessions_prune.add_argument(
        "--older-than",
        type=int,
        default=90,
        help="Delete sessions older than N days (default: 90)",
    )
    sessions_prune.add_argument("--source", help="Only prune sessions from this source")
    sessions_prune.add_argument(
        "--yes", "-y", action="store_true", help="Skip confirmation"
    )

    sessions_subparsers.add_parser(
        "optimize",
        help="Reclaim disk space: merge FTS5 segments + VACUUM (no data change)",
    )

    sessions_repair = sessions_subparsers.add_parser(
        "repair",
        help="Repair a malformed state.db schema so hidden sessions reappear",
        description=(
            "Recover a state.db whose schema is malformed (e.g. 'table "
            "messages_fts already exists'), which makes Desktop/Dashboard show "
            "no sessions. A backup is made first; sessions and messages are "
            "preserved and the FTS search index is rebuilt if needed."
        ),
    )
    sessions_repair.add_argument(
        "--check-only",
        action="store_true",
        help="Only report whether the database opens cleanly; do not modify it",
    )
    sessions_repair.add_argument(
        "--no-backup",
        action="store_true",
        help="Skip the timestamped backup copy (not recommended)",
    )

    sessions_subparsers.add_parser("stats", help="Show session store statistics")

    sessions_rename = sessions_subparsers.add_parser(
        "rename", help="Set or change a session's title"
    )
    sessions_rename.add_argument("session_id", help="Session ID to rename")
    sessions_rename.add_argument("title", nargs="+", help="New title for the session")

    sessions_browse = sessions_subparsers.add_parser(
        "browse",
        help="Interactive session picker — browse, search, and resume sessions",
    )
    sessions_browse.add_argument(
        "--source", help="Filter by source (cli, telegram, discord, etc.)"
    )
    sessions_browse.add_argument(
        "--limit", type=int, default=500, help="Max sessions to load (default: 500)"
    )

    def _confirm_prompt(prompt: str) -> bool:
        """Prompt for y/N confirmation, safe against non-TTY environments."""
        try:
            return input(prompt).strip().lower() in {"y", "yes"}
        except (EOFError, KeyboardInterrupt):
            return False

    def cmd_sessions(args):
        import json as _json

        action = args.sessions_action

        # 'repair' must run BEFORE opening SessionDB(): a malformed schema is
        # exactly the case where SessionDB() can't open, so it operates on the
        # raw file path instead.
        if action == "repair":
            from openagents_state import (
                DEFAULT_DB_PATH,
                _db_opens_cleanly,
                repair_state_db_schema,
            )

            db_path = DEFAULT_DB_PATH
            if not db_path.exists():
                print(f"No session database at {db_path} (nothing to repair).")
                return
            reason = _db_opens_cleanly(db_path)
            if reason is None:
                print(f"✓ {db_path} opens cleanly — no repair needed.")
                return
            print(f"✗ {db_path} does not open cleanly: {reason}")
            if getattr(args, "check_only", False):
                return
            print("Repairing (a backup copy is made first)…")
            report = repair_state_db_schema(
                db_path, backup=not getattr(args, "no_backup", False)
            )
            if report.get("repaired"):
                if report.get("backup_path"):
                    print(f"  backup: {report['backup_path']}")
                print(f"  strategy: {report.get('strategy')}")
                try:
                    from openagents_state import SessionDB

                    n = SessionDB()._conn.execute(
                        "SELECT COUNT(*) FROM sessions"
                    ).fetchone()[0]
                    print(f"✓ Repaired — {n} sessions recovered.")
                except Exception:
                    print("✓ Repaired.")
            else:
                print(f"✗ Repair failed: {report.get('error')}")
                if report.get("backup_path"):
                    print(f"  A backup is preserved at: {report['backup_path']}")
                print("  Keep state.db and the backup; do not delete them.")
            return

        try:
            from openagents_state import SessionDB

            db = SessionDB()
        except Exception as e:
            print(f"Error: Could not open session database: {e}")
            return

        # Hide third-party tool sessions by default, but honour explicit --source
        _source = getattr(args, "source", None)
        _exclude = None if _source else ["tool"]

        if action == "list":
            sessions = db.list_sessions_rich(
                source=args.source, exclude_sources=_exclude, limit=args.limit
            )
            if not sessions:
                print("No sessions found.")
                return
            has_titles = any(s.get("title") for s in sessions)
            if has_titles:
                print(f"{'Title':<32} {'Preview':<40} {'Last Active':<13} {'ID'}")
                print("─" * 110)
            else:
                print(f"{'Preview':<50} {'Last Active':<13} {'Src':<6} {'ID'}")
                print("─" * 95)
            for s in sessions:
                last_active = _relative_time(s.get("last_active"))
                preview = (
                    s.get("preview", "")[:38]
                    if has_titles
                    else s.get("preview", "")[:48]
                )
                if has_titles:
                    title = (s.get("title") or "—")[:30]
                    sid = s["id"]
                    print(f"{title:<32} {preview:<40} {last_active:<13} {sid}")
                else:
                    sid = s["id"]
                    print(f"{preview:<50} {last_active:<13} {s['source']:<6} {sid}")

        elif action == "export":
            if args.session_id:
                resolved_session_id = db.resolve_session_id(args.session_id)
                if not resolved_session_id:
                    print(f"Session '{args.session_id}' not found.")
                    return
                data = db.export_session(resolved_session_id)
                if not data:
                    print(f"Session '{args.session_id}' not found.")
                    return
                line = _json.dumps(data, ensure_ascii=False) + "\n"
                if args.output == "-":

                    sys.stdout.write(line)
                else:
                    with open(args.output, "w", encoding="utf-8") as f:
                        f.write(line)
                    print(f"Exported 1 session to {args.output}")
            else:
                sessions = db.export_all(source=args.source)
                if args.output == "-":

                    for s in sessions:
                        sys.stdout.write(_json.dumps(s, ensure_ascii=False) + "\n")
                else:
                    with open(args.output, "w", encoding="utf-8") as f:
                        for s in sessions:
                            f.write(_json.dumps(s, ensure_ascii=False) + "\n")
                    print(f"Exported {len(sessions)} sessions to {args.output}")

        elif action == "delete":
            resolved_session_id = db.resolve_session_id(args.session_id)
            if not resolved_session_id:
                print(f"Session '{args.session_id}' not found.")
                return
            if not args.yes:
                if not _confirm_prompt(
                    f"Delete session '{resolved_session_id}' and all its messages? [y/N] "
                ):
                    print("Cancelled.")
                    return
            sessions_dir = get_openagents_home() / "sessions"
            if db.delete_session(resolved_session_id, sessions_dir=sessions_dir):
                print(f"Deleted session '{resolved_session_id}'.")
            else:
                print(f"Session '{args.session_id}' not found.")

        elif action == "prune":
            days = args.older_than
            source_msg = f" from '{args.source}'" if args.source else ""
            if not args.yes:
                if not _confirm_prompt(
                    f"Delete all ended sessions older than {days} days{source_msg}? [y/N] "
                ):
                    print("Cancelled.")
                    return
            sessions_dir = get_openagents_home() / "sessions"
            count = db.prune_sessions(
                older_than_days=days, source=args.source, sessions_dir=sessions_dir
            )
            print(f"Pruned {count} session(s).")

        elif action == "rename":
            resolved_session_id = db.resolve_session_id(args.session_id)
            if not resolved_session_id:
                print(f"Session '{args.session_id}' not found.")
                return
            title = " ".join(args.title)
            try:
                if db.set_session_title(resolved_session_id, title):
                    print(f"Session '{resolved_session_id}' renamed to: {title}")
                else:
                    print(f"Session '{args.session_id}' not found.")
            except ValueError as e:
                print(f"Error: {e}")

        elif action == "browse":
            limit = getattr(args, "limit", 500) or 500
            source = getattr(args, "source", None)
            _browse_exclude = None if source else ["tool"]
            sessions = db.list_sessions_rich(
                source=source, exclude_sources=_browse_exclude, limit=limit
            )
            db.close()
            if not sessions:
                print("No sessions found.")
                return

            selected_id = _session_browse_picker(sessions)
            if not selected_id:
                print("Cancelled.")
                return

            # Launch hermes --resume <id> by replacing the current process
            print(f"Resuming session: {selected_id}")
            from openagents_cli.relaunch import relaunch

            relaunch(["--resume", selected_id])
            return  # won't reach here after execvp

        elif action == "optimize":
            db_path = db.db_path
            before_mb = (
                os.path.getsize(db_path) / (1024 * 1024)
                if db_path.exists()
                else 0.0
            )
            print("Optimizing session store (FTS merge + VACUUM)…")
            try:
                # vacuum() merges FTS5 segments (optimize_fts) then VACUUMs,
                # and returns the number of indexes it merged.
                n = db.vacuum()
            except Exception as e:
                print(f"Error: optimization failed: {e}")
                db.close()
                return
            after_mb = (
                os.path.getsize(db_path) / (1024 * 1024)
                if db_path.exists()
                else 0.0
            )
            saved = before_mb - after_mb
            print(f"Optimized {n} FTS index(es).")
            print(
                f"Database size: {before_mb:.1f} MB -> {after_mb:.1f} MB "
                f"(reclaimed {saved:.1f} MB)"
            )

        elif action == "stats":
            total = db.session_count()
            msgs = db.message_count()
            print(f"Total sessions: {total}")
            print(f"Total messages: {msgs}")
            for src in ["cli", "telegram", "discord", "whatsapp", "slack"]:
                c = db.session_count(source=src)
                if c > 0:
                    print(f"  {src}: {c} sessions")
            db_path = db.db_path
            if db_path.exists():
                size_mb = os.path.getsize(db_path) / (1024 * 1024)
                print(f"Database size: {size_mb:.1f} MB")

        else:
            sessions_parser.print_help()

        db.close()

    sessions_parser.set_defaults(func=cmd_sessions)

    # =========================================================================
    # insights command  (parser built in openagents_cli/subcommands/insights.py)
    # =========================================================================
    build_insights_parser(subparsers, cmd_insights=cmd_insights)

    # =========================================================================
    # claw command  (parser built in openagents_cli/subcommands/claw.py)
    # =========================================================================
    build_claw_parser(subparsers, cmd_claw=cmd_claw)

    # =========================================================================
    # version command  (parser built in openagents_cli/subcommands/version.py)
    # =========================================================================
    build_version_parser(subparsers, cmd_version=cmd_version)

    # =========================================================================
    # update command  (parser built in openagents_cli/subcommands/update.py)
    # =========================================================================
    build_update_parser(subparsers, cmd_update=cmd_update)

    # =========================================================================
    # uninstall command  (parser built in openagents_cli/subcommands/uninstall.py)
    # =========================================================================
    build_uninstall_parser(subparsers, cmd_uninstall=cmd_uninstall)

    # =========================================================================
    # acp command  (parser built in openagents_cli/subcommands/acp.py)
    # =========================================================================
    build_acp_parser(subparsers, cmd_acp=cmd_acp)

    # =========================================================================
    # profile command  (parser built in openagents_cli/subcommands/profile.py)
    # =========================================================================
    build_profile_parser(subparsers, cmd_profile=cmd_profile)

    # =========================================================================
    # completion command
    # =========================================================================
    completion_parser = subparsers.add_parser(
        "completion",
        help="Print shell completion script (bash, zsh, or fish)",
    )
    completion_parser.add_argument(
        "shell",
        nargs="?",
        default="bash",
        choices=["bash", "zsh", "fish"],
        help="Shell type (default: bash)",
    )
    completion_parser.set_defaults(func=lambda args: cmd_completion(args, parser))

    # =========================================================================
    # dashboard command  (parser built in openagents_cli/subcommands/dashboard.py)
    # =========================================================================
    build_dashboard_parser(
        subparsers,
        cmd_dashboard=cmd_dashboard,
        cmd_dashboard_register=cmd_dashboard_register,
    )


    # =========================================================================
    # desktop (a.k.a. gui) command
    #
    # The canonical name is "desktop"; "gui" is kept as a deprecated alias
    # for one release. The Hermes-Setup.exe success screen tells users to
    # run `hermes desktop` from a terminal, so the canonical name needs
    # to be the one that appears in --help (argparse promotes the primary
    # name; aliases stay hidden).
    # =========================================================================
    # gui command  (parser built in openagents_cli/subcommands/gui.py)
    # =========================================================================
    build_gui_parser(subparsers, cmd_gui=cmd_gui)

    # =========================================================================
    # logs command  (parser built in openagents_cli/subcommands/logs.py)
    # =========================================================================
    build_logs_parser(subparsers, cmd_logs=cmd_logs)

    # =========================================================================
    # prompt-size command  (parser built in openagents_cli/subcommands/prompt_size.py)
    # =========================================================================
    build_prompt_size_parser(subparsers, cmd_prompt_size=cmd_prompt_size)

    # =========================================================================
    # Parse and execute
    # =========================================================================
    # Pre-process argv so unquoted multi-word session names after -c / -r
    # are merged into a single token before argparse sees them.
    # e.g. ``hermes -c Pokemon Agent Dev`` → ``hermes -c 'Pokemon Agent Dev'``
    # ── Container-aware routing ────────────────────────────────────────
    # When NixOS container mode is active, route ALL subcommands into
    # the managed container.  This MUST run before parse_args() so that
    # --help, unrecognised flags, and every subcommand are forwarded
    # transparently instead of being intercepted by argparse on the host.
=======
    # NixOS container mode routes ALL invocations into the managed container.
    # MUST run before parse_args() so --help, unrecognised flags and every
    # subcommand are forwarded instead of intercepted by argparse on the host.
>>>>>>> rb/tag
    from openagents_cli.config import get_container_exec_info

    container_info = get_container_exec_info()
    if container_info:
        _exec_in_container(container_info, sys.argv[1:])
        sys.exit(1)  # unreachable: execvp replaces the process or raises

    args = _parse_cli_args(parser, subparsers, sys.argv[1:])

    if args.version:
        cmd_version(args)
        return

    # --yolo must be set *before* plugin discovery: tools.approval freezes
    # _YOLO_MODE_FROZEN at import; set later (inside cmd_chat) it does nothing.
    if getattr(args, "yolo", False):
        os.environ["HERMES_YOLO_MODE"] = "1"

    # Plugin discovery + shell hooks once, gated so introspection commands
    # (hooks list, cron list, gateway status, ...) pay no discovery cost and
    # trigger no consent prompts for hooks the user is still inspecting.
    _prepare_agent_startup(args)

    if getattr(args, "oneshot", None):
        _run_oneshot_from_args(args)

    # No subcommand (optionally with top-level --resume / --continue) → chat.
    if args.command is None:
        _default_to_chat(args)
        return

    # A handler's int return code becomes the exit code (None = success).
    if hasattr(args, "func"):
        rc = args.func(args)
        if isinstance(rc, int) and rc != 0:
            sys.exit(rc)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()


# ---- BEGIN PLUGIN-COMPAT (revert-scheduled; see COMPAT_MANIFEST.md) ----
# Names external plugins imported from this module before the Sep 2026 decomposition.
# Internal code MUST NOT use these (scripts/check_compat_pointers.py fails CI if it does).
# The whole block is removed by reverting the commit that added it.
import hashlib  # noqa: F401,E402
import shlex  # noqa: F401,E402
import stat  # noqa: F401,E402
import tempfile  # noqa: F401,E402


_PLUGIN_COMPAT_LAZY = {
    'line_input': ('openagents_cli.cli_output', 'line_input'),
}

_plugin_compat_prev_getattr = __getattr__


def __getattr__(name):  # PEP 562 — chained onto the module's own __getattr__
    target = _PLUGIN_COMPAT_LAZY.get(name)
    if target is None:
        return _plugin_compat_prev_getattr(name)
    import importlib
    from openagents_cli.plugin_compat import warn_once
    warn_once(__name__, name, *target)
    return getattr(importlib.import_module(target[0]), target[1])
# ---- END PLUGIN-COMPAT ----
