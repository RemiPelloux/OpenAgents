#!/usr/bin/env python3
"""OpenAgents CLI — interactive terminal interface (``python cli.py --help`` for usage)."""

# Must be the very first import (UTF-8 stdio on Windows). Missing only mid-``hermes update``.
try:
    import openagents_bootstrap  # noqa: F401
except ModuleNotFoundError:
    pass

import logging
import os
import functools
import shutil  # noqa: F401 — tests patch shutil/time through the cli facade
import sys
import re
import atexit
import errno
import time  # noqa: F401 — see shutil
from collections import deque
from dataclasses import dataclass
from contextlib import contextmanager, suppress
from pathlib import Path
from datetime import datetime  # noqa: F401 — siblings import it lazily through cli
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

os.environ["OPENAGENTS_QUIET"] = "1"  # suppress our modules' startup chatter


from openagents_cli.cli_agent_setup_mixin import CLIAgentSetupMixin
from openagents_cli.cli_commands_mixin import CLICommandsMixin
from openagents_cli.cli_billing_mixin import CLIBillingMixin
from openagents_cli.cli_loops_mixin import CLILoopsMixin
from openagents_cli.cli_info_mixin import CLIInfoMixin
from openagents_cli.cli_terminal_mixin import CLITerminalMixin
from openagents_cli.cli_modal_mixin import CLIModalMixin
from openagents_cli.cli_stream_mixin import CLIStreamMixin
from openagents_cli.cli_session_mixin import CLISessionMixin
from openagents_cli.cli_model_switch_mixin import CLIModelSwitchMixin
from openagents_cli.cli_voice_mixin import CLIVoiceMixin
from openagents_cli.cli_status_bar_mixin import CLIStatusBarMixin
from openagents_cli.cli_tui_mixin import CLITuiMixin
from openagents_cli.cli_process_notifications import CLIProcessNotificationsMixin
from openagents_cli.cli_init_mixin import CLIInitMixin
from openagents_cli.cli_tui_runtime_mixin import CLITuiRuntimeMixin
# Extracted clusters (mechanical split, #116911); re-exported here so `cli.<name>` stays the seam.
from openagents_cli.cli_shutdown import (  # noqa: F401,E402
    _CLEANUP_STEPS,
    _arm_exit_watchdog,
    _emit_interrupted_session_end,
    _exit_watchdog_timeout,
    _finalize_single_query,
    _float_env,
    _flush_logging_and_stdio,
    _flush_one_shot_session_store,
    _interrupt_async_delegations,
    _invoke_interrupted_session_end,
    _notify_session_finalize,
    _notify_single_query_session_finalize,
    _oneshot_agent_and_session,
    _should_emit_cleanup_session_finalize,
    _shutdown_agent_memory_provider,
    _shutdown_cached_aux_clients,
    _shutdown_mcp_servers,
    _stop_cli_wake_word,
    _sync_process_session_id,
    _wait_for_oneshot_background_completions,
)
from openagents_cli.cli_auto_maintenance import (  # noqa: F401,E402
    _run_checkpoint_auto_maintenance,
    _run_state_db_auto_maintenance,
)
from openagents_cli.cli_render import (  # noqa: F401,E402
    ChatConsole,
    _ACCENT,
    _ACCENT_ANSI_DEFAULT,
    _BOLD,
    _DA1_REPLY_RE,
    _DIM,
    _FALSE_RE,
    _LIGHT_DEFAULT_TERM_PROGRAMS,
    _LIGHT_MODE_REMAP,
    _LIGHT_MODE_REMAP_UPPER,
    _REASONING_TAGS,
    _RST,
    _STREAM_PAD,
    _STREAM_PARTIAL_PREVIEW_LEN,
    _SkinAwareAnsi,
    _TOOL_CALL_TAGS,
    _TRUE_RE,
    _WINDOWS_PATH_WITH_DOT_SEGMENT_RE,
    _accent_hex,
    _add_suspect_rows,
    _append_blank_panel_line,
    _append_panel_line,
    _assistant_content_as_text,
    _assistant_copy_text,
    _b,
    _build_compact_banner,
    _clear_output_history,
    _cli_visible_print,
    _coerce_output_history_limit,
    _cprint,
    _d,
    _detect_light_mode_uncached,
    _heal_cooked_mode_drift,
    _hex_to_ansi,
    _install_skin_light_mode_hook,
    _line_rows,
    _luminance_from_hex,
    _maybe_remap_for_light_mode,
    _output_history_lines,
    _output_history_recording,
    _output_history_rows,
    _output_tail_fitting,
    _painted_columns,
    _PaintedLine,
    _panel_box_width,
    _post_stream_transform_output,
    _prepend_note_to_message,
    _preserve_windows_dot_segments_for_markdown,
    _pt_app_is_running,
    _pt_print_ansi,
    _query_osc11_background,
    _record_output_history,
    _record_output_history_entry,
    _release_paints,
    _render_final_assistant_content,
    _rich_text_from_ansi,
    _set_chrome_floor,
    _strip_markdown_syntax,
    _strip_reasoning_tags,
    _terminal_columns,
    _terminal_reflows,
    _terminal_width_for_streaming,
    _tty_wrap,
    _wrap_panel_text,
    _wrap_panel_text_keep_ws,
)
from openagents_cli.cli_config_load import (  # noqa: F401,E402
    _AUXILIARY_TASK_ENV,
    _CWD_PLACEHOLDERS,
    _TERMINAL_ENV_MAPPINGS,
    _cli_config_defaults,
    _init_logging_and_display_from_config,
    _load_prefill_messages,
    _merge_file_config,
    _mirror_config_to_env,
    _parse_reasoning_config,
    _parse_service_tier_config,
    _resolve_prefill_messages_file,
    load_cli_config,
)
from openagents_cli.cli_terminal_input import (  # noqa: F401,E402
    _BACKSLASH_LINE_CONTINUATION_RE,
    _DSR_CPR_ESC_RE,
    _DSR_CPR_VISIBLE_RE,
    _EXTENDED_ENTER_KEYS_SEQ,
    _IMAGE_EXTENSIONS,
    _KITTY_KEYBOARD_PUSH_SEQ,
    _MODIFY_OTHER_KEYS_SEQ,
    _SGR_MOUSE_BARE_RE,
    _SGR_MOUSE_ESC_RE,
    _SGR_MOUSE_VISIBLE_RE,
    _TERMINAL_INPUT_MODE_RESET_SEQ,
    _apply_backslash_line_continuation,
    _apply_bracketed_paste_timeout_patch,
    _bind_prompt_submit_keys,
    _build_cpr_disabled_output,
    _cli_multiline_shortcuts_enabled,
    _collect_query_images,
    _detect_file_drop,
    _disable_prompt_toolkit_cpr_warning,
    _enable_extended_enter_keys,
    _estimate_tui_input_height,
    _file_drop_result,
    _format_image_attachment_badges,
    _hermes_call_output_screen_diff,
    _is_backslash_line_continuation,
    _is_ghostty_terminal,
    _preserve_ctrl_enter_newline,
    _resolve_attachment_path,
    _select_classic_cli_pt_output,
    _should_auto_attach_clipboard_image_on_paste,
    _split_path_input,
    _status_bar_visible_from_display_config,
    _strip_leaked_terminal_responses_with_meta,
    _terminal_may_leak_cpr,
    _terminal_supports_extended_enter_keys,
    _termux_example_image_path,
)
from openagents_cli.cli_single_query import (  # noqa: F401,E402
    _TERMINAL_PROVIDER_REASONS,
    _TRANSIENT_PROVIDER_REASONS,
    _collect_kanban_task_images,
    _configure_quiet_agent,
    _install_single_query_signal_handlers,
    _int_or,
    _interrupt_agent_for_signal,
    _route_single_query_images,
    _run_kanban_goal_loop_chat,
    _run_kanban_goal_loop_q,
    _run_quiet_single_query,
    _run_single_query_mode,
    _single_query_exit_code,
    _sync_cli_session_id_from_agent,
)

from prompt_toolkit.patch_stdout import patch_stdout
try:
    from prompt_toolkit.enums import EditingMode
except ImportError:  # partial prompt_toolkit stubs in tests
    EditingMode = None
from prompt_toolkit import print_formatted_text as _pt_print
from prompt_toolkit.formatted_text import ANSI as _PT_ANSI
try:
    from prompt_toolkit.cursor_shapes import CursorShape
    _STEADY_CURSOR = CursorShape.BLOCK
except (ImportError, AttributeError):
    _STEADY_CURSOR = None

try:
    from openagents_cli import pt_input_extras as _pt_extras

    _pt_extras.install_shift_enter_alias()
    _pt_extras.install_ctrl_enter_alias()
    _pt_extras.install_cmd_backspace_alias()
    _pt_extras.install_modify_other_keys_aliases()
    _pt_extras.install_keypress_data_normalization()
    _pt_extras.install_ignored_terminal_sequences()
    del _pt_extras
except Exception:
    pass
import threading
import queue


def _lazy_shim(module: str, name: str, alias: str | None = None):
    """Import ``module.name`` on first call; keeps heavy imports off startup while ``cli.<name>`` stays patchable."""
    import importlib

    def shim(*args, **kwargs):
        return getattr(importlib.import_module(module), name)(*args, **kwargs)

    shim.__name__ = shim.__qualname__ = alias or name
    return shim


def format_duration_compact(*args, **kwargs):
    seconds = float(args[0] if args else kwargs.get("seconds", 0.0))
    if seconds < 60:
        return f"{seconds:.0f}s"
    minutes = seconds / 60
    if minutes < 60:
        return f"{minutes:.0f}m"
    hours = minutes / 60
    if hours < 24:
        remaining_min = int(minutes % 60)
        return f"{int(hours)}h {remaining_min}m" if remaining_min else f"{int(hours)}h"
    days = hours / 24
    return f"{days:.1f}d"


# model id -> shortest configured alias (process-lifetime cache; config is read once).
_REVERSE_ALIAS_CACHE: dict[str, str] | None = None


def _reverse_alias_for_display(model_name: str) -> str:
    """Shortest alias for ``model_name`` from ``model_aliases:`` or ``model.aliases:``, else ``model_name``."""
    global _REVERSE_ALIAS_CACHE
    if not model_name:
        return model_name
    if _REVERSE_ALIAS_CACHE is None:
        rmap: dict[str, str] = {}

        def _put(m: str, alias: str) -> None:
            if m and (m not in rmap or len(alias) < len(rmap[m])):
                rmap[m] = alias

        try:
            from openagents_cli.config import load_config
            cfg = load_config() or {}
            ma = cfg.get("model_aliases")
            if isinstance(ma, dict):
                for alias, entry in ma.items():
                    if isinstance(entry, dict):
                        _put(str(entry.get("model", "") or "").strip(), alias)
            mdl = cfg.get("model", {}) or {}
            if isinstance(mdl, dict):
                simple = mdl.get("aliases")
                if isinstance(simple, dict):
                    for alias, val in simple.items():
                        if isinstance(val, str) and val.strip():
                            v = val.strip()
                            _put(v.split("/", 1)[1] if "/" in v else v, alias)
        except Exception:
            pass
        _REVERSE_ALIAS_CACHE = rmap
    return _REVERSE_ALIAS_CACHE.get(model_name, model_name)


def format_token_count_compact(*args, **kwargs):
    value = int(args[0] if args else kwargs.get("value", 0))
    abs_value = abs(value)
    if abs_value < 1_000:
        return str(value)

    sign = "-" if value < 0 else ""
    units = ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "K"))
    for threshold, suffix in units:
        if abs_value >= threshold:
            scaled = abs_value / threshold
            text = f"{scaled:.{2 if scaled < 10 else 1 if scaled < 100 else 0}f}"
            if "." in text:
                text = text.rstrip("0").rstrip(".")
            return f"{sign}{text}{suffix}"

    return f"{value:,}"


realign_markdown_tables = _lazy_shim("agent.markdown_tables", "realign_markdown_tables")

_COMMAND_SPINNER_FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")


# ~/.openagents/.env first, project .env as dev fallback; user env files override stale shell exports.
from openagents_constants import get_openagents_home
from openagents_cli.env_loader import load_openagents_dotenv

_hermes_home = get_openagents_home()
_project_env = Path(__file__).parent / '.env'
load_openagents_dotenv(hermes_home=_hermes_home, project_env=_project_env)


CLI_CONFIG = load_cli_config()


_init_logging_and_display_from_config()

# Neuter AsyncHttpxClientWrapper.__del__ before any AsyncOpenAI client exists: it
# schedules aclose() on the running loop (prompt_toolkit's, during idle), closing
# transports bound to dead worker loops ("Event loop is closed" / "Press ENTER to
# continue..."). A meta_path finder patches ``openai._base_client`` at first import —
# eager import costs ~166ms/30MB cold, and the patch is guaranteed to land before
# instantiation. See ``agent.auxiliary_client.neuter_async_httpx_del``.
try:
    import sys as _httpx_neuter_sys
    import importlib.util as _httpx_neuter_imp_util

    class _AsyncHttpxDelNeuter:
        """Patch ``AsyncHttpxClientWrapper.__del__`` to a no-op when ``openai._base_client`` loads."""

        _armed = True

        def find_spec(self, fullname, path=None, target=None):
            if not self._armed or fullname != "openai._base_client":
                return None
            # Disarm before delegating so the recursive find_spec doesn't loop through us.
            self._armed = False
            try:
                _httpx_neuter_sys.meta_path.remove(self)
            except ValueError:
                pass
            spec = _httpx_neuter_imp_util.find_spec(fullname)
            if spec is None or spec.loader is None:
                return None
            _orig_exec = spec.loader.exec_module

            def _patched_exec(module):
                _orig_exec(module)
                try:
                    cls = getattr(module, "AsyncHttpxClientWrapper", None)
                    if cls is not None:
                        cls.__del__ = lambda self: None  # type: ignore[assignment]
                except Exception:
                    pass

            spec.loader.exec_module = _patched_exec  # type: ignore[method-assign]
            return spec

    _httpx_neuter_sys.meta_path.insert(0, _AsyncHttpxDelNeuter())
except Exception:
    pass


# Agent/tool systems load lazily: bare startup only needs the prompt.
def get_tool_definitions(*args, **kwargs):
    from openagents_cli.mcp_startup import wait_for_mcp_discovery
    from model_tools import get_tool_definitions as _get_tool_definitions

    wait_for_mcp_discovery()
    return _get_tool_definitions(*args, **kwargs)


validate_toolset = _lazy_shim("toolsets", "validate_toolset")


_cleanup_all_terminals = _lazy_shim("tools.terminal_tool", "cleanup_all_environments", "_cleanup_all_terminals")
set_sudo_password_callback = _lazy_shim("tools.terminal_tool", "set_sudo_password_callback")
set_approval_callback = _lazy_shim("tools.terminal_tool", "set_approval_callback")
set_secret_capture_callback = _lazy_shim("tools.skills_tool", "set_secret_capture_callback")
_cleanup_all_browsers = _lazy_shim("tools.browser_tool_lifecycle", "_emergency_cleanup_all_sessions", "_cleanup_all_browsers")

_cleanup_done = False  # _run_cleanup runs exactly once
_cleanup_in_progress = False
_cli_wake_owner = None
# One-shot finalization runs before process cleanup (plugins see the boundary while the
# agent is attached); atexit cleanup must not finalize those sessions again.
_single_query_finalize_attempted_session_ids: set[str | None] = set()
# /handoff sessions belong to the gateway: finalizing them here would stamp end_reason on
# a row the gateway just reopened, making the handoff leg vanish from history.
# Session IDs that were handed off to the gateway via /handoff. The CLI process exits after a successful
# handoff, but the gateway now owns the session lifecycle — _run_cleanup must NOT call finalize_session on
# these, because doing so sets end_reason on a row the gateway just reopened and is actively writing to
# (#88234). The race made the handoff leg vanish from session history and broke session_search recall for
# the handed-off session.
_handed_off_session_ids: set[str | None] = set()
_active_agent_ref = None  # active AIAgent, for memory-provider shutdown at exit
_deferred_agent_startup_done = False
# Set once the TUI app starts (focus reporting + mouse tracking on); gates the on-exit
# terminal reset so non-TUI one-shot runs never emit codes for modes they never enabled.
_tui_input_modes_active = False


# Set True once the TUI's prompt_toolkit app starts (which enables focus reporting + mouse tracking). Gates
# the on-exit terminal reset so non-TUI one-shot CLI runs — which also register _run_cleanup via atexit —
# don't emit escape codes for modes they never enabled (#36823).
def _mark_tui_input_modes_active() -> None:
    """Record that the TUI app started, so _run_cleanup resets input modes."""
    global _tui_input_modes_active
    _tui_input_modes_active = True


def _prepare_deferred_agent_startup() -> None:
    """Run Termux-deferred agent discovery before the first real agent turn."""
    global _deferred_agent_startup_done
    if _deferred_agent_startup_done:
        return
    if os.environ.get("HERMES_DEFER_AGENT_STARTUP") != "1":
        return
    _deferred_agent_startup_done = True
    _accept_hooks = os.environ.get("HERMES_ACCEPT_HOOKS", "").lower() in {"1", "true", "yes", "on"}
    try:
        from openagents_cli.plugins import discover_plugins

        discover_plugins()
    except Exception:
        logger.warning("plugin discovery failed at deferred CLI startup", exc_info=True)
    try:
        from openagents_cli.mcp_startup import start_background_mcp_discovery

        start_background_mcp_discovery(logger=logger, thread_name="termux-cli-mcp-discovery")
    except Exception:
        logger.debug("MCP tool discovery failed at deferred CLI startup", exc_info=True)
    try:
        from agent.shell_hooks import register_from_config
        from agent.outbound_webhooks import register_from_config as register_outbound_webhooks
        from openagents_cli.config import load_config

        _hooks_cfg = load_config()
        register_from_config(_hooks_cfg, accept_hooks=_accept_hooks)
        register_outbound_webhooks(_hooks_cfg)
    except Exception:
        logger.debug("shell-hook registration failed at deferred CLI startup", exc_info=True)


_signal_watchdog_armed = False


def _arm_exit_watchdog_on_shutdown_signal() -> None:
    """Arm the exit backstop the moment a termination signal arrives (idempotent; never raises).

    The graceful unwind has wedge points BEFORE ``_run_cleanup`` arms its own watchdog
    (main thread in a syscall, prompt_toolkit teardown never returning). Leash is 2x
    the cleanup timeout so a progressing cleanup is never cut short. Never arm at
    startup: the timer exits unconditionally.

    SIGTERM/SIGHUP establish unambiguous shutdown intent, but the graceful path from signal →
    ``agent.interrupt()`` → ``app.exit()`` / ``KeyboardInterrupt`` → ``finally`` → ``_run_cleanup`` has
    several wedge points BEFORE ``_run_cleanup`` arms the normal watchdog: a main thread parked in a syscall
    that never observes the unwind, a prompt_toolkit teardown that never returns, or an agent worker
    blocking the ``finally``. When that happens the process has NO backstop and a "dead" CLI lingers
    (observed: ``hermes --tui`` alive ~47 min at 4% CPU after terminal close — the #65998 class).
    """
    global _signal_watchdog_armed
    if _signal_watchdog_armed:
        return
    _signal_watchdog_armed = True
    base = _exit_watchdog_timeout()
    if base <= 0:
        return  # explicitly disabled
    with suppress(Exception):  # never let the backstop break signal handling
        _arm_exit_watchdog(timeout_s=base * 2, from_signal=True)


def _run_cleanup(*, notify_session_finalize: bool = True):
    """Run resource cleanup exactly once."""
    global _cleanup_done, _cleanup_in_progress
    if _cleanup_done:
        return
    _cleanup_done = True
    _cleanup_in_progress = True

    try:
        _arm_exit_watchdog()
        # Reset terminal input modes FIRST: teardown below can take seconds and a later
        # step raising must not skip the reset. No-op unless the TUI ran.
        # See #36823.
        _reset_terminal_input_modes_on_exit()

        for step, swallow in _CLEANUP_STEPS:
            with suppress(swallow):
                globals()[step]()
        if notify_session_finalize:
            cleanup_session_id = _active_agent_ref.session_id if _active_agent_ref else None
            if _should_emit_cleanup_session_finalize(cleanup_session_id):
                _notify_session_finalize(session_id=cleanup_session_id, platform="cli", reason="shutdown")
        try:
            _shutdown_agent_memory_provider(_active_agent_ref)
        except Exception as e:
            logger.warning("CLI cleanup memory shutdown failed: %s", e, exc_info=True)
    finally:
        _cleanup_in_progress = False


def _reset_terminal_input_modes_on_exit() -> None:
    """Disable focus reporting + mouse tracking on TUI exit (best-effort).

    Ctrl+C / SIGTERM / crashes bypass prompt_toolkit's unwind, leaving focus events and
    mouse reports as visible text in the next shell. Writes to stdout when it is the
    terminal, else /dev/tty (the TUI may have run with stdout redirected).

    Called from ``_run_cleanup`` (atexit-registered + invoked on the normal / EOF / interrupt exit paths)
    this covers normal quit, Ctrl+C and SIGTERM/SIGHUP. ``kill -9`` is uncatchable, and the kanban worker's
    ``os._exit(0)`` path bypasses ``atexit``; neither runs this — but both are non-TTY / non-TUI, so there
    is nothing to reset there. See #36823.
    """
    global _tui_input_modes_active
    if not _tui_input_modes_active:
        return
    # Clear first so a re-armed _run_cleanup doesn't re-emit.
    _tui_input_modes_active = False
    try:
        stream = sys.stdout
        if stream is not None and stream.isatty():
            stream.write(_TERMINAL_INPUT_MODE_RESET_SEQ)
            stream.flush()
            return
    except Exception:
        pass
    with suppress(Exception), open("/dev/tty", "w", encoding="ascii") as tty:
        tty.write(_TERMINAL_INPUT_MODE_RESET_SEQ)
        tty.flush()


from openagents_cli.worktree_ops import (
    _git_quiet,
    _git_repo_root,
    _maintain_pack_health,
    _prune_stale_worktrees,
    _repo_is_shallow,
    _setup_worktree,
    _worktree_has_unpushed_commits,
    release_lsp_clients,
)

# ============================================================================= Git Worktree Isolation
# (#652) =============================================================================
_active_worktree: Optional[Dict[str, str]] = None


def _cleanup_worktree(info: Dict[str, str] = None) -> None:
    """Remove a worktree and its branch on exit; kept only when it has unpushed commits."""
    global _active_worktree
    info = info or _active_worktree
    if not info:
        return

    wt_path, branch, repo_root = info["path"], info["branch"], info["repo_root"]
    if not Path(wt_path).exists():
        return

    if _worktree_has_unpushed_commits(wt_path, timeout=10):
        if _repo_is_shallow(repo_root):
            # Shallow boundary makes the unpushed verdict unreliable; the startup pruner reaps later.
            _cprint(f"\n\033[33m⚠ Shallow clone — cannot verify push state, keeping: {wt_path}\033[0m")
            print("  The next `hermes -w` session deepens the clone and prunes merged worktrees automatically.")
        else:
            _cprint(f"\n\033[33m⚠ Worktree has unpushed commits, keeping: {wt_path}\033[0m")
            print(f"  To clean up manually: git worktree remove --force {wt_path}")
        _active_worktree = None
        return

    # Release the tree's language servers while the path still exists, then unlock so `remove`
    # isn't blocked by the lock placed at creation. Fail-soft.
    release_lsp_clients(wt_path)
    _git_quiet(["worktree", "unlock", wt_path], repo_root, log="git worktree unlock failed (non-fatal)")
    _git_quiet(["worktree", "remove", wt_path, "--force"], repo_root, timeout=15, log="Failed to remove worktree")
    _git_quiet(["branch", "-D", branch], repo_root, log=f"Failed to delete branch {branch}")

    _active_worktree = None
    _cprint(f"\033[32m✓ Worktree cleaned up: {wt_path}\033[0m")


# Light/dark terminal detection (mirrors ui-tui/src/theme.ts detectLightMode()). Priority:
# HERMES_LIGHT/HERMES_TUI_LIGHT env, HERMES_TUI_THEME, HERMES_TUI_BACKGROUND, COLORFGBG
# (bg slot 7/15 = light), OSC 11 query, default dark. Cached so the terminal is queried once.
_LIGHT_MODE_CACHE: bool | None = None


def _detect_light_mode() -> bool:
    global _LIGHT_MODE_CACHE
    if _LIGHT_MODE_CACHE is not None:
        return _LIGHT_MODE_CACHE
    try:
        result = _detect_light_mode_uncached()
    except Exception:
        result = False
    _LIGHT_MODE_CACHE = result
    return result


_install_skin_light_mode_hook()


# Prime the light-mode cache when interactive so OSC 11 happens before prompt_toolkit owns the tty.
with suppress(Exception):
    if sys.stdin.isatty() and sys.stdout.isatty():
        _detect_light_mode()


_OUTPUT_HISTORY_ENABLED = True
_OUTPUT_HISTORY_REPLAYING = False
_OUTPUT_HISTORY_SUPPRESSED = False
_OUTPUT_HISTORY_MAX_LINES = 200
_OUTPUT_HISTORY = deque(maxlen=_OUTPUT_HISTORY_MAX_LINES)


def _configure_output_history(enabled: bool, max_lines=200) -> None:
    """Configure recent CLI output replayed after terminal redraws."""
    global _OUTPUT_HISTORY_ENABLED, _OUTPUT_HISTORY_MAX_LINES, _OUTPUT_HISTORY
    _OUTPUT_HISTORY_ENABLED = bool(enabled)
    _OUTPUT_HISTORY_MAX_LINES = _coerce_output_history_limit(max_lines)
    _OUTPUT_HISTORY = deque(maxlen=_OUTPUT_HISTORY_MAX_LINES)


@contextmanager
def _suspend_output_history():
    global _OUTPUT_HISTORY_SUPPRESSED
    old_value = _OUTPUT_HISTORY_SUPPRESSED
    _OUTPUT_HISTORY_SUPPRESSED = True
    try:
        yield
    finally:
        _OUTPUT_HISTORY_SUPPRESSED = old_value


def _replay_output_history(fit=None, output=None) -> None:
    """Repaint recent output above the prompt after a full screen clear.

    ``fit=(rows, columns, painted, top)`` replays only the newest lines whose wrapped height
    fits ``rows`` (see ``_output_tail_fitting``) — the older ones are still in scrollback
    (#95375) — from screen row ``top`` when known (``_set_chrome_floor``). ``output``: paint
    now, straight to this prompt_toolkit output, where the caller just erased the viewport and
    reset the renderer — ``run_in_terminal`` would first erase below the top row, which
    scroll-on-clear terminals (tmux) take as a clear and copy the blank screen into scrollback.
    """
    global _OUTPUT_HISTORY_REPLAYING
    if not _OUTPUT_HISTORY_ENABLED or not _OUTPUT_HISTORY:
        return
    _OUTPUT_HISTORY_REPLAYING = True
    try:
        rendered_lines = _output_history_lines()
        top = None
        if fit is not None:
            rows, columns, painted, top = fit
            rendered_lines = _output_tail_fitting(rendered_lines, rows, columns, painted)
        if rendered_lines:
            # One payload: per-line pt prints each force a sync redraw (a waterfall of old output).
            if output is None:
                _pt_print(_PT_ANSI("\n".join(rendered_lines)))
            else:
                from prompt_toolkit.renderer import print_formatted_text as _paint_formatted_text
                from prompt_toolkit.styles import Style
                _paint_formatted_text(output, _PT_ANSI("\n".join(rendered_lines) + "\n"), Style([]))
                size = output.get_size()
                if top is not None:  # the chrome's top is now this many rows down
                    top += sum(_line_rows(line, columns) for line in rendered_lines)
                    _set_chrome_floor(max(0, size.rows - top))
                    if size.columns != columns:
                        _add_suspect_rows(top + 1 - size.rows)
            width = _painted_columns() if fit is None else columns
            for line in rendered_lines:  # repainted: they wrap at today's width from now on
                if isinstance(line, _PaintedLine):
                    line.width = width
    except Exception:
        pass
    finally:
        _OUTPUT_HISTORY_REPLAYING = False


_strip_leaked_bracketed_paste_wrappers = _lazy_shim(
    "openagents_cli.input_sanitize", "strip_leaked_bracketed_paste_wrappers", "_strip_leaked_bracketed_paste_wrappers"
)


# OSC sequences (e.g. OSC-8 links): pt's ANSI parser strips the ESC but leaks the payload as text.
_OSC_ESCAPE_RE = re.compile(r"\x1b\][\s\S]*?(?:\x07|\x1b\\)")


<<<<<<< HEAD
class ChatConsole:
    """Rich Console adapter for prompt_toolkit's patch_stdout context.

    Captures Rich's rendered ANSI output and routes it through _cprint
    so colors and markup render correctly inside the interactive chat loop.
    Drop-in replacement for Rich Console — just pass this to any function
    that expects a console.print() interface.
    """

    def __init__(self):
        from io import StringIO
        self._buffer = StringIO()
        self._inner = Console(
            file=self._buffer,
            force_terminal=True,
            color_system="truecolor",
            highlight=False,
        )

    def print(self, *args, **kwargs):
        self._buffer.seek(0)
        self._buffer.truncate()
        # Read terminal width at render time so panels adapt to current size
        self._inner.width = shutil.get_terminal_size((80, 24)).columns
        self._inner.print(*args, **kwargs)
        output = self._buffer.getvalue()
        # Strip OSC escape sequences (e.g. OSC-8 hyperlinks) before
        # routing through prompt_toolkit's ANSI parser, which only
        # handles CSI/SGR and passes OSC payload through as literal text.
        output = _OSC_ESCAPE_RE.sub("", output)
        for line in output.rstrip("\n").split("\n"):
            _cprint(line)

    @contextmanager
    def status(self, *_args, **_kwargs):
        """Provide a no-op Rich-compatible status context.

        Some slash command helpers use ``console.status(...)`` when running in
        the standalone CLI. Interactive chat routes those helpers through
        ``ChatConsole()``, which historically only implemented ``print()``.
        Returning a silent context manager keeps slash commands compatible
        without duplicating the higher-level busy indicator already shown by
        ``HermesCLI._busy_command()``.
        """
        yield self

# ASCII Art - HERMES-AGENT logo (full width, single line - requires ~95 char terminal)
HERMES_AGENT_LOGO = """[bold #FFD700]██╗  ██╗███████╗██████╗ ███╗   ███╗███████╗███████╗       █████╗  ██████╗ ███████╗███╗   ██╗████████╗[/]
[bold #FFD700]██║  ██║██╔════╝██╔══██╗████╗ ████║██╔════╝██╔════╝      ██╔══██╗██╔════╝ ██╔════╝████╗  ██║╚══██╔══╝[/]
[#FFBF00]███████║█████╗  ██████╔╝██╔████╔██║█████╗  ███████╗█████╗███████║██║  ███╗█████╗  ██╔██╗ ██║   ██║[/]
[#FFBF00]██╔══██║██╔══╝  ██╔══██╗██║╚██╔╝██║██╔══╝  ╚════██║╚════╝██╔══██║██║   ██║██╔══╝  ██║╚██╗██║   ██║[/]
[#CD7F32]██║  ██║███████╗██║  ██║██║ ╚═╝ ██║███████╗███████║      ██║  ██║╚██████╔╝███████╗██║ ╚████║   ██║[/]
[#CD7F32]╚═╝  ╚═╝╚══════╝╚═╝  ╚═╝╚═╝     ╚═╝╚══════╝╚══════╝      ╚═╝  ╚═╝ ╚═════╝ ╚══════╝╚═╝  ╚═══╝   ╚═╝[/]"""

# ASCII Art - OpenAgents Caduceus (compact, fits in left panel)
HERMES_CADUCEUS = """[#CD7F32]⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣀⡀⠀⣀⣀⠀⢀⣀⡀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀[/]
[#CD7F32]⠀⠀⠀⠀⠀⠀⢀⣠⣴⣾⣿⣿⣇⠸⣿⣿⠇⣸⣿⣿⣷⣦⣄⡀⠀⠀⠀⠀⠀⠀[/]
[#FFBF00]⠀⢀⣠⣴⣶⠿⠋⣩⡿⣿⡿⠻⣿⡇⢠⡄⢸⣿⠟⢿⣿⢿⣍⠙⠿⣶⣦⣄⡀⠀[/]
[#FFBF00]⠀⠀⠉⠉⠁⠶⠟⠋⠀⠉⠀⢀⣈⣁⡈⢁⣈⣁⡀⠀⠉⠀⠙⠻⠶⠈⠉⠉⠀⠀[/]
[#FFD700]⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣴⣿⡿⠛⢁⡈⠛⢿⣿⣦⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀[/]
[#FFD700]⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠿⣿⣦⣤⣈⠁⢠⣴⣿⠿⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀[/]
[#FFBF00]⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠈⠉⠻⢿⣿⣦⡉⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀[/]
[#FFBF00]⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠘⢷⣦⣈⠛⠃⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀[/]
[#CD7F32]⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢠⣴⠦⠈⠙⠿⣦⡄⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀[/]
[#CD7F32]⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠸⣿⣤⡈⠁⢤⣿⠇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀[/]
[#B8860B]⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠉⠛⠷⠄⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀[/]
[#B8860B]⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣀⠑⢶⣄⡀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀[/]
[#B8860B]⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣿⠁⢰⡆⠈⡿⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀[/]
[#B8860B]⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠈⠳⠈⣡⠞⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀[/]
[#B8860B]⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠈⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀[/]"""



def _build_compact_banner() -> str:
    """Build a compact banner that fits the current terminal width."""
    try:
        from openagents_cli.skin_engine import get_active_skin
        _skin = get_active_skin()
    except Exception:
        _skin = None

    skin_name = getattr(_skin, "name", "default") if _skin else "default"
    border_color = _skin.get_color("banner_border", "#FFD700") if _skin else "#FFD700"
    title_color = _skin.get_color("banner_title", "#FFBF00") if _skin else "#FFBF00"
    dim_color = _skin.get_color("banner_dim", "#B8860B") if _skin else "#B8860B"

    if skin_name == "default":
        line1 = "⚕ NOUS HERMES - AI Agent Framework"
        tiny_line = "⚕ NOUS HERMES"
    elif skin_name == "opencode":
        line1 = "OpenAgents · OpenPro"
        tiny_line = "OpenAgents"
    else:
        agent_name = _skin.get_branding("agent_name", "OpenAgents") if _skin else "OpenAgents"
        line1 = f"{agent_name} - AI Agent Framework"
        tiny_line = agent_name

    if os.environ.get("HERMES_FAST_STARTUP_BANNER") == "1":
        from openagents_cli import __release_date__ as _release_date
        from openagents_cli import __version__ as _version

        version_line = f"OpenAgents v{_version} ({_release_date})"
    else:
        version_line = format_banner_version_label()

    w = min(shutil.get_terminal_size().columns - 2, 88)
    if w < 30:
        return f"\n[{title_color}]{tiny_line}[/] [dim {dim_color}]· OpenPro[/]\n"

    inner = w - 2  # inside the box border
    bar = "═" * w
    content_width = inner - 2

    # Truncate and pad to fit
    line1 = line1[:content_width].ljust(content_width)
    line2 = version_line[:content_width].ljust(content_width)

    return (
        f"\n[bold {border_color}]╔{bar}╗[/]\n"
        f"[bold {border_color}]║[/] [{title_color}]{line1}[/] [bold {border_color}]║[/]\n"
        f"[bold {border_color}]║[/] [dim {dim_color}]{line2}[/] [bold {border_color}]║[/]\n"
        f"[bold {border_color}]╚{bar}╝[/]\n"
    )



# ============================================================================
# Slash-command detection helper
# ============================================================================

||||||| cf299e9a01
class ChatConsole:
    """Rich Console adapter for prompt_toolkit's patch_stdout context.

    Captures Rich's rendered ANSI output and routes it through _cprint
    so colors and markup render correctly inside the interactive chat loop.
    Drop-in replacement for Rich Console — just pass this to any function
    that expects a console.print() interface.
    """

    def __init__(self):
        from io import StringIO
        self._buffer = StringIO()
        self._inner = Console(
            file=self._buffer,
            force_terminal=True,
            color_system="truecolor",
            highlight=False,
        )

    def print(self, *args, **kwargs):
        self._buffer.seek(0)
        self._buffer.truncate()
        # Read terminal width at render time so panels adapt to current size
        self._inner.width = shutil.get_terminal_size((80, 24)).columns
        self._inner.print(*args, **kwargs)
        output = self._buffer.getvalue()
        # Strip OSC escape sequences (e.g. OSC-8 hyperlinks) before
        # routing through prompt_toolkit's ANSI parser, which only
        # handles CSI/SGR and passes OSC payload through as literal text.
        output = _OSC_ESCAPE_RE.sub("", output)
        for line in output.rstrip("\n").split("\n"):
            _cprint(line)

    @contextmanager
    def status(self, *_args, **_kwargs):
        """Provide a no-op Rich-compatible status context.

        Some slash command helpers use ``console.status(...)`` when running in
        the standalone CLI. Interactive chat routes those helpers through
        ``ChatConsole()``, which historically only implemented ``print()``.
        Returning a silent context manager keeps slash commands compatible
        without duplicating the higher-level busy indicator already shown by
        ``HermesCLI._busy_command()``.
        """
        yield self

# ASCII Art - HERMES-AGENT logo (full width, single line - requires ~95 char terminal)
HERMES_AGENT_LOGO = """[bold #FFD700]██╗  ██╗███████╗██████╗ ███╗   ███╗███████╗███████╗       █████╗  ██████╗ ███████╗███╗   ██╗████████╗[/]
[bold #FFD700]██║  ██║██╔════╝██╔══██╗████╗ ████║██╔════╝██╔════╝      ██╔══██╗██╔════╝ ██╔════╝████╗  ██║╚══██╔══╝[/]
[#FFBF00]███████║█████╗  ██████╔╝██╔████╔██║█████╗  ███████╗█████╗███████║██║  ███╗█████╗  ██╔██╗ ██║   ██║[/]
[#FFBF00]██╔══██║██╔══╝  ██╔══██╗██║╚██╔╝██║██╔══╝  ╚════██║╚════╝██╔══██║██║   ██║██╔══╝  ██║╚██╗██║   ██║[/]
[#CD7F32]██║  ██║███████╗██║  ██║██║ ╚═╝ ██║███████╗███████║      ██║  ██║╚██████╔╝███████╗██║ ╚████║   ██║[/]
[#CD7F32]╚═╝  ╚═╝╚══════╝╚═╝  ╚═╝╚═╝     ╚═╝╚══════╝╚══════╝      ╚═╝  ╚═╝ ╚═════╝ ╚══════╝╚═╝  ╚═══╝   ╚═╝[/]"""

# ASCII Art - OpenAgents Caduceus (compact, fits in left panel)
HERMES_CADUCEUS = """[#CD7F32]⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣀⡀⠀⣀⣀⠀⢀⣀⡀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀[/]
[#CD7F32]⠀⠀⠀⠀⠀⠀⢀⣠⣴⣾⣿⣿⣇⠸⣿⣿⠇⣸⣿⣿⣷⣦⣄⡀⠀⠀⠀⠀⠀⠀[/]
[#FFBF00]⠀⢀⣠⣴⣶⠿⠋⣩⡿⣿⡿⠻⣿⡇⢠⡄⢸⣿⠟⢿⣿⢿⣍⠙⠿⣶⣦⣄⡀⠀[/]
[#FFBF00]⠀⠀⠉⠉⠁⠶⠟⠋⠀⠉⠀⢀⣈⣁⡈⢁⣈⣁⡀⠀⠉⠀⠙⠻⠶⠈⠉⠉⠀⠀[/]
[#FFD700]⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣴⣿⡿⠛⢁⡈⠛⢿⣿⣦⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀[/]
[#FFD700]⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠿⣿⣦⣤⣈⠁⢠⣴⣿⠿⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀[/]
[#FFBF00]⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠈⠉⠻⢿⣿⣦⡉⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀[/]
[#FFBF00]⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠘⢷⣦⣈⠛⠃⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀[/]
[#CD7F32]⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢠⣴⠦⠈⠙⠿⣦⡄⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀[/]
[#CD7F32]⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠸⣿⣤⡈⠁⢤⣿⠇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀[/]
[#B8860B]⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠉⠛⠷⠄⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀[/]
[#B8860B]⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣀⠑⢶⣄⡀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀[/]
[#B8860B]⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣿⠁⢰⡆⠈⡿⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀[/]
[#B8860B]⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠈⠳⠈⣡⠞⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀[/]
[#B8860B]⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠈⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀[/]"""



def _build_compact_banner() -> str:
    """Build a compact banner that fits the current terminal width."""
    try:
        from openagents_cli.skin_engine import get_active_skin
        _skin = get_active_skin()
    except Exception:
        _skin = None

    skin_name = getattr(_skin, "name", "default") if _skin else "default"
    border_color = _skin.get_color("banner_border", "#FFD700") if _skin else "#FFD700"
    title_color = _skin.get_color("banner_title", "#FFBF00") if _skin else "#FFBF00"
    dim_color = _skin.get_color("banner_dim", "#B8860B") if _skin else "#B8860B"

    if skin_name == "default":
        line1 = "⚕ NOUS HERMES - AI Agent Framework"
        tiny_line = "⚕ NOUS HERMES"
    else:
        agent_name = _skin.get_branding("agent_name", "OpenAgents") if _skin else "OpenAgents"
        line1 = f"{agent_name} - AI Agent Framework"
        tiny_line = agent_name

    if os.environ.get("HERMES_FAST_STARTUP_BANNER") == "1":
        from openagents_cli import __release_date__ as _release_date
        from openagents_cli import __version__ as _version

        version_line = f"OpenAgents v{_version} ({_release_date})"
    else:
        version_line = format_banner_version_label()

    w = min(shutil.get_terminal_size().columns - 2, 88)
    if w < 30:
        return f"\n[{title_color}]{tiny_line}[/] [dim {dim_color}]- Nous Research[/]\n"

    inner = w - 2  # inside the box border
    bar = "═" * w
    content_width = inner - 2

    # Truncate and pad to fit
    line1 = line1[:content_width].ljust(content_width)
    line2 = version_line[:content_width].ljust(content_width)

    return (
        f"\n[bold {border_color}]╔{bar}╗[/]\n"
        f"[bold {border_color}]║[/] [{title_color}]{line1}[/] [bold {border_color}]║[/]\n"
        f"[bold {border_color}]║[/] [dim {dim_color}]{line2}[/] [bold {border_color}]║[/]\n"
        f"[bold {border_color}]╚{bar}╝[/]\n"
    )



# ============================================================================
# Slash-command detection helper
# ============================================================================

=======
>>>>>>> rb/tag
def _looks_like_slash_command(text: str) -> bool:
    """``/help`` yes, ``/Users/x/file.md`` no: a command's first word has no further ``/``."""
    if not text or not text.startswith("/"):
        return False
    return "/" not in text.split()[0][1:]


_skill_commands = None
_skill_bundles = None


def _slash_args(cmd: str) -> str:
    """Text after the slash-command word, stripped ("" when absent)."""
    parts = cmd.split(None, 1)
    return parts[1].strip() if len(parts) > 1 else ""


def _ensure_skill_commands() -> dict:
    global _skill_commands
    if _skill_commands is None:
        from agent.skill_commands import scan_skill_commands

        _skill_commands = scan_skill_commands()
    return _skill_commands


def get_skill_commands() -> dict:
    return _ensure_skill_commands()


build_skill_invocation_message = _lazy_shim("agent.skill_commands", "build_skill_invocation_message")
build_preloaded_skills_prompt = _lazy_shim("agent.skill_commands", "build_preloaded_skills_prompt")


def get_skill_bundles() -> dict:
    global _skill_bundles
    if _skill_bundles is None:
        from agent.skill_bundles import get_skill_bundles as _impl

        _skill_bundles = _impl()
    return _skill_bundles


build_bundle_invocation_message = _lazy_shim("agent.skill_bundles", "build_bundle_invocation_message")


def _get_plugin_cmd_handler_names() -> set:
    """Return plugin command names (without slash prefix) for dispatch matching."""
    try:
        from openagents_cli.plugins import get_plugin_commands
        return set(get_plugin_commands().keys())
    except Exception:
        return set()


def _parse_skills_argument(skills: str | list[str] | tuple[str, ...] | None) -> list[str]:
    """Normalize a CLI skills flag into a deduplicated list of skill identifiers."""
    if not skills:
        return []
    raw_values = [str(item) for item in skills if item is not None] if isinstance(skills, (list, tuple)) else [str(skills)]
    parts = (p.strip() for raw in raw_values for p in raw.split(","))
    return list(dict.fromkeys(p for p in parts if p))


def save_config_value(key_path: str, value: any) -> bool:
    """Persist dot-separated ``key_path`` = value into OPENAGENTS_HOME/config.yaml; True on success.

    Never the repo's cli-config.yaml: no config reader loads it, so the value would vanish.
    """
    config_path = get_openagents_home() / 'config.yaml'

    try:
        from openagents_constants import mkdir_under_hermes_home
        mkdir_under_hermes_home(config_path.parent)
        from utils import atomic_roundtrip_yaml_update
        atomic_roundtrip_yaml_update(config_path, key_path, value)
        try:  # owner-only: config files contain API keys
            os.chmod(config_path, 0o600)
        except (OSError, NotImplementedError):
            pass
        return True
    except Exception as e:
        logger.error("Failed to save config: %s", e)
        return False


def _normalize_moa_model(model: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """``moa:<preset>`` -> ``("moa", preset)`` (same routing as ``/moa``); anything else -> ``(None, model)``.

    Returns ``("moa", "<preset>")`` when *model* selects the MoA virtual provider, otherwise ``(None,
    model)`` unchanged. This gives non-interactive ``hermes chat -Q -m moa:<preset>`` the same routing the
    interactive ``/moa`` command and the model picker already use: ``resolve_runtime_provider`` handles
    ``requested_provider == "moa"`` and ``agent_init`` builds the MoAClient off ``provider == "moa"``.
    Without this the raw ``moa:<preset>`` string is sent to the real provider and rejected with a 401/400
    "model not supported" (#56828).
    """
    if isinstance(model, str) and model.strip().lower().startswith("moa:"):
        preset = model.strip().split(":", 1)[1].strip()
        if preset:
            return "moa", preset
    return None, model

_split_model_config_default = _lazy_shim("openagents_cli.config", "split_model_config_default", "_split_model_config_default")


class _VoiceInputMessage:
    """Sentinel for voice-transcribed input so the concise voice prefix never applies to typed text.

    Distinguishes STT output from manually typed text while voice mode is active, so the
    concise-voice-response prefix is applied only to messages that actually came from the microphone
    (#65827).
    """

    __slots__ = ("text",)

    def __init__(self, text: str):
        self.text = text

    def __str__(self) -> str:
        return self.text


class _SeededQueryMessage:
    """Sentinel for a ``-q`` prompt seeded into an interactive session; treated LITERALLY (no slash/!/file-drop)."""

    __slots__ = ("text", "images")

    def __init__(self, text: str, images=None):
        self.text = text or ""
        self.images = list(images or [])

    def __str__(self) -> str:
        return self.text


def _should_seed_interactive(query, image, quiet: bool, oneshot: bool) -> bool:
    """``-q`` seeds an interactive session only on a real TTY without ``--oneshot``/``-Q`` (automation answers and exits)."""
    if not (query or image) or oneshot or quiet:
        return False
    try:
        return bool(sys.stdin.isatty() and sys.stdout.isatty())
    except Exception:
        return False


@dataclass
class _ChatTurn:
    """Per-turn state shared by the ``chat()`` phases and the agent worker thread.

    ``result`` is written by the worker and read after the join; ``tts_normal_exit`` is
    set only when the TTS worker drained on its own so the last sentence is never cut.
    """

    result: Optional[dict] = None
    mute_notification_reply: bool = False
    use_streaming_tts: bool = False
    box_opened: bool = False
    thinking_started: bool = False
    text_queue: Optional[queue.Queue] = None
    tts_thread: Optional[threading.Thread] = None
    stream_callback: Optional[Any] = None
    stop_event: Optional[threading.Event] = None
    tts_normal_exit: bool = False
    voice_prefix: str = ""
from openagents_cli.cli_chat_turn_mixin import CLIChatTurnMixin


_PASTE_REF_RE = re.compile(r'\[Pasted text #\d+: \d+ lines \u2192 (.+?)\]')


class HermesCLI(CLIInitMixin, CLITuiRuntimeMixin, CLIProcessNotificationsMixin, CLIAgentSetupMixin, CLICommandsMixin, CLIBillingMixin, CLITuiMixin, CLIStatusBarMixin, CLIVoiceMixin, CLIModelSwitchMixin, CLISessionMixin, CLIStreamMixin, CLIModalMixin, CLITerminalMixin, CLIInfoMixin, CLILoopsMixin, CLIChatTurnMixin):
    """Interactive REPL for the OpenAgents."""

    # Seeded -q first message (see _should_seed_interactive); run() re-creates
    # _pending_input, so it is enqueued only after the fresh queue exists.
    _seeded_first_message: Optional["_SeededQueryMessage"] = None
    # Inspection surfaces (banner, /tools, status line) read this on partially built instances too.
    disabled_toolsets: Optional[List[str]] = None

    def __init__(
        self,
        model: str = None,
        toolsets: List[str] = None,
        provider: str = None,
        reasoning: str = None,
        api_key: str = None,
        base_url: str = None,
        max_turns: int = None,
        run_budget: float = None,
        verbose: Optional[bool] = None,
        compact: bool = False,
        resume: str = None,
        checkpoints: bool = False,
        pass_session_id: bool = False,
        ignore_rules: bool = False,
    ):
        """CLI args win over config; ``reasoning`` is per-run only; ``resume`` restores history from SQLite."""
        self._init_display_options(verbose, compact)
        self._init_model_routing(model, toolsets, provider, reasoning, api_key, base_url, max_turns, run_budget,
                                 checkpoints, pass_session_id, ignore_rules)
        self._init_runtime_state(resume)


    def _claim_active_session(self, surface: str = "cli", *, stderr: bool = False) -> bool:
        """Claim a global active-session slot for this CLI process."""
        if self._active_session_lease is not None:
            return True
        try:
            from openagents_cli.active_sessions import format_refusal_stderr, try_acquire_active_session

            lease, message = try_acquire_active_session(
                session_id=self.session_id,
                surface=surface,
                config=self.config,
                # Writer identity: a re-claim by this process replaces its own entry.
                # See #94595.
                metadata={"live_session_id": str(self.session_id)},
            )
        except Exception as exc:
            logger.warning("Failed to claim active session slot: %s", exc)
            return True
        if message:
            print(format_refusal_stderr(message), file=sys.stderr) if stderr else self._console_print(f"[bold red]{message}[/]")
            return False
        self._active_session_lease = lease
        with suppress(Exception):
            atexit.register(self._release_active_session)
        return True

    def _release_active_session(self) -> None:
        lease = getattr(self, "_active_session_lease", None)
        if lease is None:
            return
        try:
            lease.release()
        except Exception:
            logger.debug("Failed to release active session slot", exc_info=True)
        finally:
            self._active_session_lease = None

<<<<<<< HEAD
    def _invalidate(self, min_interval: float = 0.25) -> None:
        """Throttled UI repaint for high-frequency background updates.

        Use this for spinner frames, streaming token flushes, and other
        repaints that can fire many times per second — the throttle prevents
        terminal blinking on slow/SSH connections, and the resize-recovery
        guard avoids stamping footer/status-bar chrome into scrollback while a
        SIGWINCH reflow is in flight.

        Do NOT use this for user-blocking modal prompts (approval / clarify /
        sudo). Those are rare, one-shot, user-blocking events that must paint
        immediately; route them through ``self._app.invalidate()`` directly, the
        same way the modal key-binding handlers already do. Sending a modal's
        entry paint through this throttle lets an unrelated background repaint
        within the 250ms window — or an in-flight resize — silently drop it, so
        the prompt never renders and times out unseen (#41098).
        """
        if getattr(self, "_resize_recovery_pending", False):
            return
        now = time.monotonic()
        if hasattr(self, "_app") and self._app and (now - getattr(self, "_last_invalidate", 0.0)) >= min_interval:
            self._last_invalidate = now
            self._app.invalidate()

    def _paint_now(self) -> None:
        """Immediate, unthrottled repaint for user-blocking modal prompts.

        Background-thread callbacks (approval / clarify / sudo) set their modal
        state then call this to make the panel visible at once. It deliberately
        bypasses the ``_invalidate`` throttle and resize-recovery guard — a
        modal the user is actively waiting on must never be dropped — mirroring
        the direct ``event.app.invalidate()`` the modal key-binding handlers
        already use. See ``_invalidate`` for why the throttle must not gate
        these paints (#41098).
        """
        app = getattr(self, "_app", None)
        if app is not None:
            try:
                app.invalidate()
            except Exception:
                pass

    def _force_full_redraw(self) -> None:
        """Force a clean full-screen repaint of the prompt_toolkit UI.

        Used to recover from terminal buffer drift caused by external
        redraws we can't detect — e.g. macOS cmux / tmux tab switches,
        ``clear`` issued from a subshell, or SSH window restores. These
        wipe or repaint the terminal without firing SIGWINCH, so
        prompt_toolkit's tracked ``_cursor_pos`` no longer matches reality
        and the next incremental redraw stacks on top of stale content
        (ghost status bars, duplicated prompts).

        Bound to Ctrl+L and exposed as the ``/redraw`` slash command,
        matching the standard terminal-UX convention (bash, zsh, fish,
        vim, htop).
        """
        app = getattr(self, "_app", None)
        if not app:
            return
        self._clear_prompt_toolkit_screen(app)
        _replay_output_history()
        try:
            app.invalidate()
        except Exception:
            pass

    def _recover_terminal_after_interrupt(self) -> None:
        """Recover the terminal after an interrupted agent turn (#33271).

        When the user interrupts a running turn by typing a new message,
        prompt_toolkit may have an in-flight ``CSI 6n`` cursor-position query
        whose reply (``ESC[<row>;<col>R``) arrives on stdin after the input
        parser has torn down. The reply then leaks as literal text
        (``^[[19;1R``) and the VT100 parser can stall in a partial-escape
        state, accepting no further keystrokes — the terminal appears frozen.

        Two steps recover a sane state:
          1. ``flush_stdin()`` drains stray escape bytes from the OS input
             buffer (``termios.tcflush(TCIFLUSH)``; no-op on non-TTY).
          2. ``_force_full_redraw()`` drops prompt_toolkit's cached
             screen/cursor state and forces a clean repaint.

        Both steps are independently safe and self-guard, so a failure of one
        never prevents the other.
        """
        try:
            from openagents_cli.curses_ui import flush_stdin
            flush_stdin()
        except Exception:
            pass
        self._force_full_redraw()

    def _clear_prompt_toolkit_screen(self, app, *, rebuild_scrollback: bool = False) -> None:
        """Clear the terminal and reset prompt_toolkit renderer state."""
        try:
            renderer = app.renderer
            out = renderer.output
            out.reset_attributes()
            out.erase_screen()
            if rebuild_scrollback:
                try:
                    out.write_raw("\x1b[3J")
                except Exception:
                    pass
            out.cursor_goto(0, 0)
            out.flush()
            # Drop prompt_toolkit's cached screen + cursor state so the
            # next _redraw() starts from a known (0, 0) origin and
            # re-renders every cell rather than diffing against stale.
            renderer.reset(leave_alternate_screen=False)
        except Exception:
            pass

    def _recover_after_resize(self, app, original_on_resize) -> None:
        """Recover a resized classic CLI without desynchronizing cursor state.

        Unlike _force_full_redraw, we do NOT clear the physical screen or
        scrollback here.  The startup banner and tool summary are printed
        before prompt_toolkit owns the live chrome, so they live in normal
        terminal scrollback.  Erasing the screen on SIGWINCH removes that
        startup UI and ``_replay_output_history`` cannot reconstruct it
        (the banner was never added to ``_OUTPUT_HISTORY``).

        Let prompt_toolkit's own resize path run with its renderer cursor
        cache intact. Its Application._on_resize() starts with
        renderer.erase(leave_alternate_screen=False), which needs the cached
        cursor position to move back to the live prompt origin before
        erase_down(). Resetting the renderer before that erase loses the
        origin and can leave stale prompt glyphs after a narrow resize.

        We also flag ``_status_bar_suppressed_after_resize`` so the dynamic
        status bar and input separator rules stay hidden while the terminal
        reflow settles.  On column shrink the terminal reflows already-rendered
        status bar rows into scrollback before prompt_toolkit can erase them;
        drawing a fresh full-width bar immediately makes the old and new
        versions look duplicated (#19280, #22976).

        Suppression alone is not enough on a WIDTH change.  prompt_toolkit's
        ``renderer.erase()`` does ``cursor_up(_cursor_pos.y)`` + ``erase_down()``
        using the ``_cursor_pos.y`` cached from the LAST render at the OLD
        width (renderer.py).  When the column count shrinks, the terminal
        reflows each already-painted full-width chrome row into 2+ physical
        rows, so the cached ``y`` undershoots: ``cursor_up`` does not climb
        past the reflowed rows and ``erase_down`` leaves the stale bar stranded
        ABOVE the live origin.  The next paint then stacks a fresh bar below it
        — the duplicated-status-bar report (two bars, two elapsed readings).
        Suppression hides the *new* bar but never erases the already-reflowed
        *old* one, so the ghost survives the whole suppression window.

        Fix: on a width change, wipe the visible viewport with ``erase_screen``
        (CSI 2J) BEFORE delegating to prompt_toolkit's resize, then let its
        repaint redraw from a clean origin.  This is banner-safe: 2J clears
        only the visible screen, NOT scrollback history (that is CSI 3J, which
        we do not send here — ``rebuild_scrollback=False``), so the startup
        banner that scrolled into history is preserved and
        ``_replay_output_history`` is not needed.  Row-count-only changes skip
        the clear (no reflow, so no ghost) to avoid an unnecessary repaint.

        The suppression is transient: a short follow-up timer clears it and
        repaints once the reflow has settled, so the bar returns on its own
        during idle.  Previously the flag was only cleared on the next
        *submitted* user input, so a resize/reflow (tmux pane change, SSH
        window restore, font zoom) followed by idle left the status bar hidden
        indefinitely even while the refresh clock kept ticking (the dynamic
        chrome rendered at height 0 on every repaint).  The next-submit clear
        at the input loop remains as a fast path.
        """
        self._status_bar_suppressed_after_resize = True
        # On a WIDTH change the terminal has already reflowed the old full-width
        # chrome into extra physical rows that prompt_toolkit's stale-cursor
        # erase (cursor_up(_cursor_pos.y) cached at the OLD width) will not
        # reach, leaving a duplicated status bar stranded above the live origin.
        # Ctrl+L / /redraw clears it cleanly, so route the resize path through
        # the SAME recovery: wipe the visible viewport (banner-safe — CSI 2J
        # only, never CSI 3J) and replay the transcript so nothing is lost.
        # Row-count-only changes skip this (no reflow → no ghost) to avoid an
        # unnecessary full repaint.
        try:
            new_width = self._get_tui_terminal_width()
        except Exception:
            new_width = None
        prev_width = getattr(self, "_last_resize_width", None)
        # First resize of the session has no prior width to compare against;
        # treat it as a change so an initial maximize/restore is covered too.
        width_changed = new_width is not None and new_width != prev_width
        if width_changed:
            try:
                self._clear_prompt_toolkit_screen(app, rebuild_scrollback=False)
                _replay_output_history()
            except Exception:
                pass
        if new_width is not None:
            self._last_resize_width = new_width
        original_on_resize()
        self._schedule_status_bar_unsuppress(app)

    def _schedule_status_bar_unsuppress(self, app, delay: float = 0.35) -> None:
        """Clear the post-resize status-bar suppression after the reflow settles.

        Debounced: a fresh resize cancels the pending unsuppress and restarts
        the timer, so a resize storm only repaints the bar once it stops.
        """
        try:
            old_timer = getattr(self, "_status_bar_unsuppress_timer", None)
            if old_timer is not None:
                try:
                    old_timer.cancel()
                except Exception:
                    pass

            def _clear():
                self._status_bar_suppressed_after_resize = False
                try:
                    app.invalidate()
                except Exception:
                    pass

            def _fire():
                try:
                    loop = getattr(app, "loop", None)
                except Exception:
                    loop = None
                if loop is not None:
                    try:
                        loop.call_soon_threadsafe(_clear)
                        return
                    except Exception:
                        pass
                _clear()

            timer = threading.Timer(delay, _fire)
            timer.daemon = True
            self._status_bar_unsuppress_timer = timer
            timer.start()
        except Exception:
            # Fail open: never leave the bar stuck hidden.
            self._status_bar_suppressed_after_resize = False

    def _schedule_resize_recovery(self, app, original_on_resize, delay: float = 0.12) -> None:
        """Debounce resize redraws so footer chrome is not stamped into scrollback."""
        try:
            old_timer = getattr(self, "_resize_recovery_timer", None)
            lock = getattr(self, "_resize_recovery_lock", None)
            if lock is None:
                lock = threading.Lock()
                self._resize_recovery_lock = lock

            def _timer_fired(timer_ref):
                def _run_recovery():
                    with lock:
                        if getattr(self, "_resize_recovery_timer", None) is not timer_ref:
                            return
                        self._resize_recovery_timer = None
                        self._resize_recovery_pending = False
                    self._recover_after_resize(app, original_on_resize)

                try:
                    loop = app.loop  # type: ignore[attr-defined]
                except Exception:
                    loop = None
                if loop is not None:
                    try:
                        loop.call_soon_threadsafe(_run_recovery)
                        return
                    except Exception:
                        pass
                _run_recovery()

            with lock:
                if old_timer is not None:
                    try:
                        old_timer.cancel()
                    except Exception:
                        pass
                self._resize_recovery_pending = True
                timer = threading.Timer(delay, lambda: _timer_fired(timer))
                timer.daemon = True
                self._resize_recovery_timer = timer
                timer.start()
        except Exception:
            self._resize_recovery_pending = False
            self._recover_after_resize(app, original_on_resize)

    def _status_bar_context_style(self, percent_used: Optional[int]) -> str:
        if percent_used is None:
            return "class:status-bar-dim"
        if percent_used >= 95:
            return "class:status-bar-critical"
        if percent_used > 80:
            return "class:status-bar-bad"
        if percent_used >= 50:
            return "class:status-bar-warn"
        return "class:status-bar-good"

    @staticmethod
    def _compression_count_style(count: int) -> str:
        """Return a style class reflecting context compression pressure."""
        if count >= 10:
            return "class:status-bar-bad"
        if count >= 5:
            return "class:status-bar-warn"
        return "class:status-bar-dim"

    def _build_context_bar(self, percent_used: Optional[int], width: int = 10) -> str:
        safe_percent = max(0, min(100, percent_used or 0))
        filled = round((safe_percent / 100) * width)
        return f"[{('█' * filled) + ('░' * max(0, width - filled))}]"

    @staticmethod
    def _format_prompt_elapsed(prompt_start_time: Optional[float], prompt_duration: float, live: bool = False) -> str:
        """Format per-prompt elapsed time for the status bar.

        Always returns a string — shows 0s on fresh start before first turn.
        Keeps seconds visible at all scales so it increments smoothly:
            59s → 1m → 1m 1s → ... → 1m 59s → 2m → 2m 1s → ...
            59m 59s → 1h → 1h 0m 1s → ...
            23h 59m 59s → 1d → 1d 0h 1m → ...

        Emoji prefix: ⏱ when turn is live, ⏲ when frozen or fresh start.
        Uses width-1 (no variation selector) glyphs so the status bar stays
        aligned in monospace terminals.
        """
        if prompt_start_time is None and prompt_duration == 0.0:
            return "⏲ 0s"
        elapsed = time.time() - prompt_start_time if prompt_start_time is not None else prompt_duration
        elapsed = max(0.0, elapsed)

        days = int(elapsed // 86400)
        remaining = elapsed % 86400
        hours = int(remaining // 3600)
        remaining = remaining % 3600
        minutes = int(remaining // 60)
        seconds = int(remaining % 60)

        if days > 0:
            time_str = f"{days}d {hours}h {minutes}m"
        elif hours > 0:
            time_str = f"{hours}h {minutes}m {seconds}s" if seconds else f"{hours}h {minutes}m"
        elif minutes > 0:
            time_str = f"{minutes}m {seconds}s" if seconds else f"{minutes}m"
        else:
            time_str = f"{int(elapsed)}s"

        emoji = "⏱" if live else "⏲"
        return f"{emoji} {time_str}"

    @staticmethod
    def _format_idle_since(last_finished_at: Optional[float], turn_live: bool) -> str:
        """Format time since the last final agent response for the status bar.

        Returns an empty string while a turn is live (the per-prompt elapsed
        timer covers that case) or before the first turn has completed.
        Compact read-out: ``✓ 42s`` / ``✓ 3m`` / ``✓ 1h 12m``.
        """
        if turn_live or last_finished_at is None:
            return ""
        idle = max(0.0, time.time() - last_finished_at)
        return f"✓ {format_duration_compact(idle)}"

    def _get_status_bar_snapshot(self) -> Dict[str, Any]:
        # Prefer the agent's model name — it updates on fallback.
        # self.model reflects the originally configured model and never
        # changes mid-session, so the TUI would show a stale name after
        # _try_activate_fallback() switches provider/model.
        agent = getattr(self, "agent", None)
        model_name = (getattr(agent, "model", None) or self.model or "unknown")
        model_short = model_name.split("/")[-1] if "/" in model_name else model_name
        if model_short.endswith(".gguf"):
            model_short = model_short[:-5]
        if len(model_short) > 26:
            model_short = f"{model_short[:23]}..."

        elapsed_seconds = max(0.0, (datetime.now() - self.session_start).total_seconds())
        snapshot = {
            "model_name": model_name,
            "model_short": model_short,
            "duration": format_duration_compact(elapsed_seconds),
            "prompt_elapsed": self._format_prompt_elapsed(
                getattr(self, "_prompt_start_time", None),
                getattr(self, "_prompt_duration", 0.0),
                live=getattr(self, "_prompt_start_time", None) is not None,
            ),
            "idle_since": self._format_idle_since(
                getattr(self, "_last_turn_finished_at", None),
                turn_live=getattr(self, "_prompt_start_time", None) is not None,
            ),
            "context_tokens": 0,
            "context_length": None,
            "context_percent": None,
            "session_input_tokens": 0,
            "session_output_tokens": 0,
            "session_cache_read_tokens": 0,
            "session_cache_write_tokens": 0,
            "session_prompt_tokens": 0,
            "session_completion_tokens": 0,
            "session_total_tokens": 0,
            "session_api_calls": 0,
            "compressions": 0,
            "active_background_tasks": 0,
            "active_background_processes": 0,
            "active_background_subagents": 0,
        }

        # Count live /background tasks. The dict entry is removed in the
        # task thread's finally block, so len() reflects truly-running tasks.
        # len() on a CPython dict is atomic; safe to read without a lock.
        try:
            bg_tasks = getattr(self, "_background_tasks", None)
            if bg_tasks:
                snapshot["active_background_tasks"] = len(bg_tasks)
        except Exception:
            pass

        # Count live background terminal processes (terminal tool background
        # sessions tracked by tools.process_registry). Cheap O(1) read.
        try:
            from tools.process_registry import process_registry
            snapshot["active_background_processes"] = process_registry.count_running()
        except Exception:
            pass

        # Count live background/async subagents (delegate_task batches and
        # background single delegations tracked by tools.async_delegation).
        # active_count() iterates an in-memory records dict under a lock —
        # cheap and only counts records still in the "running" state.
        try:
            from tools.async_delegation import active_count as _async_active_count
            snapshot["active_background_subagents"] = _async_active_count()
        except Exception:
            pass


        if not agent:
            return snapshot

        snapshot["session_input_tokens"] = getattr(agent, "session_input_tokens", 0) or 0
        snapshot["session_output_tokens"] = getattr(agent, "session_output_tokens", 0) or 0
        snapshot["session_cache_read_tokens"] = getattr(agent, "session_cache_read_tokens", 0) or 0
        snapshot["session_cache_write_tokens"] = getattr(agent, "session_cache_write_tokens", 0) or 0
        snapshot["session_prompt_tokens"] = getattr(agent, "session_prompt_tokens", 0) or 0
        snapshot["session_completion_tokens"] = getattr(agent, "session_completion_tokens", 0) or 0
        snapshot["session_total_tokens"] = getattr(agent, "session_total_tokens", 0) or 0
        snapshot["session_api_calls"] = getattr(agent, "session_api_calls", 0) or 0

        compressor = getattr(agent, "context_compressor", None)
        if compressor:
            # last_prompt_tokens is parked at the -1 sentinel right after a
            # compression, until the next real API call reports a prompt count
            # (awaiting_real_usage_after_compression). The status bar must not
            # render that sentinel verbatim — it produced "-1/200K" / "-1%".
            # Clamp it to 0 so the one transitional turn reads as empty context.
            context_tokens = getattr(compressor, "last_prompt_tokens", 0) or 0
            if context_tokens < 0:
                context_tokens = 0
            context_length = getattr(compressor, "context_length", 0) or 0
            if context_length < 0:
                context_length = 0
            snapshot["context_tokens"] = context_tokens
            snapshot["context_length"] = context_length or None
            snapshot["compressions"] = getattr(compressor, "compression_count", 0) or 0
            if context_length:
                snapshot["context_percent"] = max(0, min(100, round((context_tokens / context_length) * 100)))

        return snapshot

    @staticmethod
    def _status_bar_display_width(text: str) -> int:
        """Return terminal cell width for status-bar text.

        len() is not enough for prompt_toolkit layout decisions because some
        glyphs can render wider than one Python codepoint. Keeping the status
        bar within the real display width prevents it from wrapping onto a
        second line and leaving behind duplicate rows.
        """
        try:
            from prompt_toolkit.utils import get_cwidth
            return get_cwidth(text or "")
        except Exception:
            return len(text or "")

    @classmethod
    def _trim_status_bar_text(cls, text: str, max_width: int) -> str:
        """Trim status-bar text to a single terminal row."""
        if max_width <= 0:
            return ""
        try:
            from prompt_toolkit.utils import get_cwidth
        except Exception:
            get_cwidth = None

        if cls._status_bar_display_width(text) <= max_width:
            return text

        ellipsis = "..."
        ellipsis_width = cls._status_bar_display_width(ellipsis)
        if max_width <= ellipsis_width:
            return ellipsis[:max_width]

        out = []
        width = 0
        for ch in text:
            ch_width = get_cwidth(ch) if get_cwidth else len(ch)
            if width + ch_width + ellipsis_width > max_width:
                break
            out.append(ch)
            width += ch_width
        return "".join(out).rstrip() + ellipsis

    @staticmethod
    def _get_tui_terminal_width(default: tuple[int, int] = (80, 24)) -> int:
        """Return the live prompt_toolkit width, falling back to ``shutil``.

        The TUI layout can be narrower than ``shutil.get_terminal_size()`` reports,
        especially on Termux/mobile shells, so prefer prompt_toolkit's width whenever
        an app is active.
        """
        try:
            from prompt_toolkit.application import get_app
            return get_app().output.get_size().columns
        except Exception:
            return shutil.get_terminal_size(default).columns

    def _use_minimal_tui_chrome(self, width: Optional[int] = None) -> bool:
        """Hide low-value chrome on narrow/mobile terminals to preserve rows."""
        if width is None:
            width = self._get_tui_terminal_width()
        return width < 64

    @staticmethod
    def _scrollback_box_width(width: Optional[int] = None) -> int:
        """Return the full viewport width for printed scrollback box rules.

        Previously this clamped to ``max(32, min(width, 56))`` as a defense
        against terminal-emulator reflow on column-shrink (#25975, salvaging
        #24403).  That clamp made response/reasoning borders look stubby on
        any modern wide terminal.  We now trust the prompt_toolkit
        ``_output_screen_diff`` monkey-patch landed in #26137 (salvaging
        #25981) to keep chrome out of scrollback in the first place, and
        accept that an aggressive column-shrink may visually reflow already
        printed Panel borders — that's a cosmetic artifact of stamped
        scrollback history, not a live-render bug.

        A small floor (32 cols) is kept so the box still renders on tiny
        terminals without negative ``'─' * (w - 2)`` math.
        """
        if width is None:
            try:
                width = shutil.get_terminal_size((80, 24)).columns
            except Exception:
                width = 80
        return max(32, int(width or 80))

    def _tui_input_rule_height(self, position: str, width: Optional[int] = None) -> int:
        """Return the visible height for the top/bottom input separator rules."""
        if position not in {"top", "bottom"}:
            raise ValueError(f"Unknown input rule position: {position}")
        if getattr(self, "_status_bar_suppressed_after_resize", False):
            return 0
        if position == "top":
            return 1
        return 0 if self._use_minimal_tui_chrome(width=width) else 1

    def _agent_spacer_height(self, width: Optional[int] = None) -> int:
        """Return the spacer height shown above the status bar while the agent runs."""
        if not getattr(self, "_agent_running", False):
            return 0
        return 0 if self._use_minimal_tui_chrome(width=width) else 1

    def _spinner_widget_height(self, width: Optional[int] = None) -> int:
        """Return the visible height for the spinner/status text line above the status bar."""
        spinner_line = self._render_spinner_text()
        if not spinner_line:
            return 0
        if self._use_minimal_tui_chrome(width=width):
            return 0
        width = width or self._get_tui_terminal_width()
        if width and width > 10:
            import math
            text_width = self._status_bar_display_width(spinner_line)
            return max(1, math.ceil(text_width / width))
        return 1

    def _render_spinner_text(self) -> str:
        """Return the live spinner/status text exactly as rendered in the TUI."""
        txt = getattr(self, "_spinner_text", "")
        if not txt:
            return ""
        t0 = getattr(self, "_tool_start_time", 0) or 0
        if t0 > 0:
            elapsed = time.monotonic() - t0
            if elapsed >= 60:
                _m, _s = int(elapsed // 60), int(elapsed % 60)
                # Fixed-width timer to avoid status-line wrap jitter while
                # scrolling/repainting (e.g. 01m05s, 12m09s).
                elapsed_str = f"{_m:02d}m{_s:02d}s"
            else:
                # Keep width stable before the 60s rollover as well.
                elapsed_str = f"{elapsed:5.1f}s"
            return f"  {txt}  ({elapsed_str})"
        return f"  {txt}"

    def _status_bar_brand_label(self) -> str:
        """Animated mascot + brand for the status bar, or legacy ⚕."""
        try:
            from openagents_cli.skin_engine import get_active_status_brand
            from openagents_cli.mascot import status_bar_prefix

            brand = get_active_status_brand("")
            if brand:
                return status_bar_prefix(brand)
        except Exception:
            pass
        return "⚕"

    def _status_bar_brand_fragments(self) -> list:
        """prompt_toolkit fragments for the status-bar leading brand."""
        try:
            from openagents_cli.skin_engine import get_active_status_brand
            from openagents_cli.mascot import animated_face

            brand = get_active_status_brand("")
            if brand:
                return [
                    ("class:status-bar", " "),
                    ("class:status-bar-strong", animated_face()),
                    ("class:status-bar-dim", f" {brand} "),
                ]
        except Exception:
            pass
        return [("class:status-bar", " ⚕ ")]

    # ── Petdex mascot (base-CLI pet pane) ───────────────────────────────
    #
    # Parity with the TUI: a half-block sprite rendered as a prompt_toolkit
    # window above the prompt, reacting to agent state and animated by a timer
    # that calls ``app.invalidate()``. Half-blocks only — the crisp Kitty image
    # protocol can't coexist with prompt_toolkit's patch_stdout output layer
    # (raw image escapes get swallowed/mangled), so we use truecolor styled
    # text, which prompt_toolkit renders natively in any 24-bit terminal.

||||||| cf299e9a01
    def _invalidate(self, min_interval: float = 0.25) -> None:
        """Throttled UI repaint for high-frequency background updates.

        Use this for spinner frames, streaming token flushes, and other
        repaints that can fire many times per second — the throttle prevents
        terminal blinking on slow/SSH connections, and the resize-recovery
        guard avoids stamping footer/status-bar chrome into scrollback while a
        SIGWINCH reflow is in flight.

        Do NOT use this for user-blocking modal prompts (approval / clarify /
        sudo). Those are rare, one-shot, user-blocking events that must paint
        immediately; route them through ``self._app.invalidate()`` directly, the
        same way the modal key-binding handlers already do. Sending a modal's
        entry paint through this throttle lets an unrelated background repaint
        within the 250ms window — or an in-flight resize — silently drop it, so
        the prompt never renders and times out unseen (#41098).
        """
        if getattr(self, "_resize_recovery_pending", False):
            return
        now = time.monotonic()
        if hasattr(self, "_app") and self._app and (now - getattr(self, "_last_invalidate", 0.0)) >= min_interval:
            self._last_invalidate = now
            self._app.invalidate()

    def _paint_now(self) -> None:
        """Immediate, unthrottled repaint for user-blocking modal prompts.

        Background-thread callbacks (approval / clarify / sudo) set their modal
        state then call this to make the panel visible at once. It deliberately
        bypasses the ``_invalidate`` throttle and resize-recovery guard — a
        modal the user is actively waiting on must never be dropped — mirroring
        the direct ``event.app.invalidate()`` the modal key-binding handlers
        already use. See ``_invalidate`` for why the throttle must not gate
        these paints (#41098).
        """
        app = getattr(self, "_app", None)
        if app is not None:
            try:
                app.invalidate()
            except Exception:
                pass

    def _force_full_redraw(self) -> None:
        """Force a clean full-screen repaint of the prompt_toolkit UI.

        Used to recover from terminal buffer drift caused by external
        redraws we can't detect — e.g. macOS cmux / tmux tab switches,
        ``clear`` issued from a subshell, or SSH window restores. These
        wipe or repaint the terminal without firing SIGWINCH, so
        prompt_toolkit's tracked ``_cursor_pos`` no longer matches reality
        and the next incremental redraw stacks on top of stale content
        (ghost status bars, duplicated prompts).

        Bound to Ctrl+L and exposed as the ``/redraw`` slash command,
        matching the standard terminal-UX convention (bash, zsh, fish,
        vim, htop).
        """
        app = getattr(self, "_app", None)
        if not app:
            return
        self._clear_prompt_toolkit_screen(app)
        _replay_output_history()
        try:
            app.invalidate()
        except Exception:
            pass

    def _recover_terminal_after_interrupt(self) -> None:
        """Recover the terminal after an interrupted agent turn (#33271).

        When the user interrupts a running turn by typing a new message,
        prompt_toolkit may have an in-flight ``CSI 6n`` cursor-position query
        whose reply (``ESC[<row>;<col>R``) arrives on stdin after the input
        parser has torn down. The reply then leaks as literal text
        (``^[[19;1R``) and the VT100 parser can stall in a partial-escape
        state, accepting no further keystrokes — the terminal appears frozen.

        Two steps recover a sane state:
          1. ``flush_stdin()`` drains stray escape bytes from the OS input
             buffer (``termios.tcflush(TCIFLUSH)``; no-op on non-TTY).
          2. ``_force_full_redraw()`` drops prompt_toolkit's cached
             screen/cursor state and forces a clean repaint.

        Both steps are independently safe and self-guard, so a failure of one
        never prevents the other.
        """
        try:
            from openagents_cli.curses_ui import flush_stdin
            flush_stdin()
        except Exception:
            pass
        self._force_full_redraw()

    def _clear_prompt_toolkit_screen(self, app, *, rebuild_scrollback: bool = False) -> None:
        """Clear the terminal and reset prompt_toolkit renderer state."""
        try:
            renderer = app.renderer
            out = renderer.output
            out.reset_attributes()
            out.erase_screen()
            if rebuild_scrollback:
                try:
                    out.write_raw("\x1b[3J")
                except Exception:
                    pass
            out.cursor_goto(0, 0)
            out.flush()
            # Drop prompt_toolkit's cached screen + cursor state so the
            # next _redraw() starts from a known (0, 0) origin and
            # re-renders every cell rather than diffing against stale.
            renderer.reset(leave_alternate_screen=False)
        except Exception:
            pass

    def _recover_after_resize(self, app, original_on_resize) -> None:
        """Recover a resized classic CLI without desynchronizing cursor state.

        Unlike _force_full_redraw, we do NOT clear the physical screen or
        scrollback here.  The startup banner and tool summary are printed
        before prompt_toolkit owns the live chrome, so they live in normal
        terminal scrollback.  Erasing the screen on SIGWINCH removes that
        startup UI and ``_replay_output_history`` cannot reconstruct it
        (the banner was never added to ``_OUTPUT_HISTORY``).

        Let prompt_toolkit's own resize path run with its renderer cursor
        cache intact. Its Application._on_resize() starts with
        renderer.erase(leave_alternate_screen=False), which needs the cached
        cursor position to move back to the live prompt origin before
        erase_down(). Resetting the renderer before that erase loses the
        origin and can leave stale prompt glyphs after a narrow resize.

        We also flag ``_status_bar_suppressed_after_resize`` so the dynamic
        status bar and input separator rules stay hidden while the terminal
        reflow settles.  On column shrink the terminal reflows already-rendered
        status bar rows into scrollback before prompt_toolkit can erase them;
        drawing a fresh full-width bar immediately makes the old and new
        versions look duplicated (#19280, #22976).

        Suppression alone is not enough on a WIDTH change.  prompt_toolkit's
        ``renderer.erase()`` does ``cursor_up(_cursor_pos.y)`` + ``erase_down()``
        using the ``_cursor_pos.y`` cached from the LAST render at the OLD
        width (renderer.py).  When the column count shrinks, the terminal
        reflows each already-painted full-width chrome row into 2+ physical
        rows, so the cached ``y`` undershoots: ``cursor_up`` does not climb
        past the reflowed rows and ``erase_down`` leaves the stale bar stranded
        ABOVE the live origin.  The next paint then stacks a fresh bar below it
        — the duplicated-status-bar report (two bars, two elapsed readings).
        Suppression hides the *new* bar but never erases the already-reflowed
        *old* one, so the ghost survives the whole suppression window.

        Fix: on a width change, wipe the visible viewport with ``erase_screen``
        (CSI 2J) BEFORE delegating to prompt_toolkit's resize, then let its
        repaint redraw from a clean origin.  This is banner-safe: 2J clears
        only the visible screen, NOT scrollback history (that is CSI 3J, which
        we do not send here — ``rebuild_scrollback=False``), so the startup
        banner that scrolled into history is preserved and
        ``_replay_output_history`` is not needed.  Row-count-only changes skip
        the clear (no reflow, so no ghost) to avoid an unnecessary repaint.

        The suppression is transient: a short follow-up timer clears it and
        repaints once the reflow has settled, so the bar returns on its own
        during idle.  Previously the flag was only cleared on the next
        *submitted* user input, so a resize/reflow (tmux pane change, SSH
        window restore, font zoom) followed by idle left the status bar hidden
        indefinitely even while the refresh clock kept ticking (the dynamic
        chrome rendered at height 0 on every repaint).  The next-submit clear
        at the input loop remains as a fast path.
        """
        self._status_bar_suppressed_after_resize = True
        # On a WIDTH change the terminal has already reflowed the old full-width
        # chrome into extra physical rows that prompt_toolkit's stale-cursor
        # erase (cursor_up(_cursor_pos.y) cached at the OLD width) will not
        # reach, leaving a duplicated status bar stranded above the live origin.
        # Ctrl+L / /redraw clears it cleanly, so route the resize path through
        # the SAME recovery: wipe the visible viewport (banner-safe — CSI 2J
        # only, never CSI 3J) and replay the transcript so nothing is lost.
        # Row-count-only changes skip this (no reflow → no ghost) to avoid an
        # unnecessary full repaint.
        try:
            new_width = self._get_tui_terminal_width()
        except Exception:
            new_width = None
        prev_width = getattr(self, "_last_resize_width", None)
        # First resize of the session has no prior width to compare against;
        # treat it as a change so an initial maximize/restore is covered too.
        width_changed = new_width is not None and new_width != prev_width
        if width_changed:
            try:
                self._clear_prompt_toolkit_screen(app, rebuild_scrollback=False)
                _replay_output_history()
            except Exception:
                pass
        if new_width is not None:
            self._last_resize_width = new_width
        original_on_resize()
        self._schedule_status_bar_unsuppress(app)

    def _schedule_status_bar_unsuppress(self, app, delay: float = 0.35) -> None:
        """Clear the post-resize status-bar suppression after the reflow settles.

        Debounced: a fresh resize cancels the pending unsuppress and restarts
        the timer, so a resize storm only repaints the bar once it stops.
        """
        try:
            old_timer = getattr(self, "_status_bar_unsuppress_timer", None)
            if old_timer is not None:
                try:
                    old_timer.cancel()
                except Exception:
                    pass

            def _clear():
                self._status_bar_suppressed_after_resize = False
                try:
                    app.invalidate()
                except Exception:
                    pass

            def _fire():
                try:
                    loop = getattr(app, "loop", None)
                except Exception:
                    loop = None
                if loop is not None:
                    try:
                        loop.call_soon_threadsafe(_clear)
                        return
                    except Exception:
                        pass
                _clear()

            timer = threading.Timer(delay, _fire)
            timer.daemon = True
            self._status_bar_unsuppress_timer = timer
            timer.start()
        except Exception:
            # Fail open: never leave the bar stuck hidden.
            self._status_bar_suppressed_after_resize = False

    def _schedule_resize_recovery(self, app, original_on_resize, delay: float = 0.12) -> None:
        """Debounce resize redraws so footer chrome is not stamped into scrollback."""
        try:
            old_timer = getattr(self, "_resize_recovery_timer", None)
            lock = getattr(self, "_resize_recovery_lock", None)
            if lock is None:
                lock = threading.Lock()
                self._resize_recovery_lock = lock

            def _timer_fired(timer_ref):
                def _run_recovery():
                    with lock:
                        if getattr(self, "_resize_recovery_timer", None) is not timer_ref:
                            return
                        self._resize_recovery_timer = None
                        self._resize_recovery_pending = False
                    self._recover_after_resize(app, original_on_resize)

                try:
                    loop = app.loop  # type: ignore[attr-defined]
                except Exception:
                    loop = None
                if loop is not None:
                    try:
                        loop.call_soon_threadsafe(_run_recovery)
                        return
                    except Exception:
                        pass
                _run_recovery()

            with lock:
                if old_timer is not None:
                    try:
                        old_timer.cancel()
                    except Exception:
                        pass
                self._resize_recovery_pending = True
                timer = threading.Timer(delay, lambda: _timer_fired(timer))
                timer.daemon = True
                self._resize_recovery_timer = timer
                timer.start()
        except Exception:
            self._resize_recovery_pending = False
            self._recover_after_resize(app, original_on_resize)

    def _status_bar_context_style(self, percent_used: Optional[int]) -> str:
        if percent_used is None:
            return "class:status-bar-dim"
        if percent_used >= 95:
            return "class:status-bar-critical"
        if percent_used > 80:
            return "class:status-bar-bad"
        if percent_used >= 50:
            return "class:status-bar-warn"
        return "class:status-bar-good"

    @staticmethod
    def _compression_count_style(count: int) -> str:
        """Return a style class reflecting context compression pressure."""
        if count >= 10:
            return "class:status-bar-bad"
        if count >= 5:
            return "class:status-bar-warn"
        return "class:status-bar-dim"

    def _build_context_bar(self, percent_used: Optional[int], width: int = 10) -> str:
        safe_percent = max(0, min(100, percent_used or 0))
        filled = round((safe_percent / 100) * width)
        return f"[{('█' * filled) + ('░' * max(0, width - filled))}]"

    @staticmethod
    def _format_prompt_elapsed(prompt_start_time: Optional[float], prompt_duration: float, live: bool = False) -> str:
        """Format per-prompt elapsed time for the status bar.

        Always returns a string — shows 0s on fresh start before first turn.
        Keeps seconds visible at all scales so it increments smoothly:
            59s → 1m → 1m 1s → ... → 1m 59s → 2m → 2m 1s → ...
            59m 59s → 1h → 1h 0m 1s → ...
            23h 59m 59s → 1d → 1d 0h 1m → ...

        Emoji prefix: ⏱ when turn is live, ⏲ when frozen or fresh start.
        Uses width-1 (no variation selector) glyphs so the status bar stays
        aligned in monospace terminals.
        """
        if prompt_start_time is None and prompt_duration == 0.0:
            return "⏲ 0s"
        elapsed = time.time() - prompt_start_time if prompt_start_time is not None else prompt_duration
        elapsed = max(0.0, elapsed)

        days = int(elapsed // 86400)
        remaining = elapsed % 86400
        hours = int(remaining // 3600)
        remaining = remaining % 3600
        minutes = int(remaining // 60)
        seconds = int(remaining % 60)

        if days > 0:
            time_str = f"{days}d {hours}h {minutes}m"
        elif hours > 0:
            time_str = f"{hours}h {minutes}m {seconds}s" if seconds else f"{hours}h {minutes}m"
        elif minutes > 0:
            time_str = f"{minutes}m {seconds}s" if seconds else f"{minutes}m"
        else:
            time_str = f"{int(elapsed)}s"

        emoji = "⏱" if live else "⏲"
        return f"{emoji} {time_str}"

    @staticmethod
    def _format_idle_since(last_finished_at: Optional[float], turn_live: bool) -> str:
        """Format time since the last final agent response for the status bar.

        Returns an empty string while a turn is live (the per-prompt elapsed
        timer covers that case) or before the first turn has completed.
        Compact read-out: ``✓ 42s`` / ``✓ 3m`` / ``✓ 1h 12m``.
        """
        if turn_live or last_finished_at is None:
            return ""
        idle = max(0.0, time.time() - last_finished_at)
        return f"✓ {format_duration_compact(idle)}"

    def _get_status_bar_snapshot(self) -> Dict[str, Any]:
        # Prefer the agent's model name — it updates on fallback.
        # self.model reflects the originally configured model and never
        # changes mid-session, so the TUI would show a stale name after
        # _try_activate_fallback() switches provider/model.
        agent = getattr(self, "agent", None)
        model_name = (getattr(agent, "model", None) or self.model or "unknown")
        model_short = model_name.split("/")[-1] if "/" in model_name else model_name
        if model_short.endswith(".gguf"):
            model_short = model_short[:-5]
        if len(model_short) > 26:
            model_short = f"{model_short[:23]}..."

        elapsed_seconds = max(0.0, (datetime.now() - self.session_start).total_seconds())
        snapshot = {
            "model_name": model_name,
            "model_short": model_short,
            "duration": format_duration_compact(elapsed_seconds),
            "prompt_elapsed": self._format_prompt_elapsed(
                getattr(self, "_prompt_start_time", None),
                getattr(self, "_prompt_duration", 0.0),
                live=getattr(self, "_prompt_start_time", None) is not None,
            ),
            "idle_since": self._format_idle_since(
                getattr(self, "_last_turn_finished_at", None),
                turn_live=getattr(self, "_prompt_start_time", None) is not None,
            ),
            "context_tokens": 0,
            "context_length": None,
            "context_percent": None,
            "session_input_tokens": 0,
            "session_output_tokens": 0,
            "session_cache_read_tokens": 0,
            "session_cache_write_tokens": 0,
            "session_prompt_tokens": 0,
            "session_completion_tokens": 0,
            "session_total_tokens": 0,
            "session_api_calls": 0,
            "compressions": 0,
            "active_background_tasks": 0,
            "active_background_processes": 0,
            "active_background_subagents": 0,
        }

        # Count live /background tasks. The dict entry is removed in the
        # task thread's finally block, so len() reflects truly-running tasks.
        # len() on a CPython dict is atomic; safe to read without a lock.
        try:
            bg_tasks = getattr(self, "_background_tasks", None)
            if bg_tasks:
                snapshot["active_background_tasks"] = len(bg_tasks)
        except Exception:
            pass

        # Count live background terminal processes (terminal tool background
        # sessions tracked by tools.process_registry). Cheap O(1) read.
        try:
            from tools.process_registry import process_registry
            snapshot["active_background_processes"] = process_registry.count_running()
        except Exception:
            pass

        # Count live background/async subagents (delegate_task batches and
        # background single delegations tracked by tools.async_delegation).
        # active_count() iterates an in-memory records dict under a lock —
        # cheap and only counts records still in the "running" state.
        try:
            from tools.async_delegation import active_count as _async_active_count
            snapshot["active_background_subagents"] = _async_active_count()
        except Exception:
            pass


        if not agent:
            return snapshot

        snapshot["session_input_tokens"] = getattr(agent, "session_input_tokens", 0) or 0
        snapshot["session_output_tokens"] = getattr(agent, "session_output_tokens", 0) or 0
        snapshot["session_cache_read_tokens"] = getattr(agent, "session_cache_read_tokens", 0) or 0
        snapshot["session_cache_write_tokens"] = getattr(agent, "session_cache_write_tokens", 0) or 0
        snapshot["session_prompt_tokens"] = getattr(agent, "session_prompt_tokens", 0) or 0
        snapshot["session_completion_tokens"] = getattr(agent, "session_completion_tokens", 0) or 0
        snapshot["session_total_tokens"] = getattr(agent, "session_total_tokens", 0) or 0
        snapshot["session_api_calls"] = getattr(agent, "session_api_calls", 0) or 0

        compressor = getattr(agent, "context_compressor", None)
        if compressor:
            # last_prompt_tokens is parked at the -1 sentinel right after a
            # compression, until the next real API call reports a prompt count
            # (awaiting_real_usage_after_compression). The status bar must not
            # render that sentinel verbatim — it produced "-1/200K" / "-1%".
            # Clamp it to 0 so the one transitional turn reads as empty context.
            context_tokens = getattr(compressor, "last_prompt_tokens", 0) or 0
            if context_tokens < 0:
                context_tokens = 0
            context_length = getattr(compressor, "context_length", 0) or 0
            if context_length < 0:
                context_length = 0
            snapshot["context_tokens"] = context_tokens
            snapshot["context_length"] = context_length or None
            snapshot["compressions"] = getattr(compressor, "compression_count", 0) or 0
            if context_length:
                snapshot["context_percent"] = max(0, min(100, round((context_tokens / context_length) * 100)))

        return snapshot

    @staticmethod
    def _status_bar_display_width(text: str) -> int:
        """Return terminal cell width for status-bar text.

        len() is not enough for prompt_toolkit layout decisions because some
        glyphs can render wider than one Python codepoint. Keeping the status
        bar within the real display width prevents it from wrapping onto a
        second line and leaving behind duplicate rows.
        """
        try:
            from prompt_toolkit.utils import get_cwidth
            return get_cwidth(text or "")
        except Exception:
            return len(text or "")

    @classmethod
    def _trim_status_bar_text(cls, text: str, max_width: int) -> str:
        """Trim status-bar text to a single terminal row."""
        if max_width <= 0:
            return ""
        try:
            from prompt_toolkit.utils import get_cwidth
        except Exception:
            get_cwidth = None

        if cls._status_bar_display_width(text) <= max_width:
            return text

        ellipsis = "..."
        ellipsis_width = cls._status_bar_display_width(ellipsis)
        if max_width <= ellipsis_width:
            return ellipsis[:max_width]

        out = []
        width = 0
        for ch in text:
            ch_width = get_cwidth(ch) if get_cwidth else len(ch)
            if width + ch_width + ellipsis_width > max_width:
                break
            out.append(ch)
            width += ch_width
        return "".join(out).rstrip() + ellipsis

    @staticmethod
    def _get_tui_terminal_width(default: tuple[int, int] = (80, 24)) -> int:
        """Return the live prompt_toolkit width, falling back to ``shutil``.

        The TUI layout can be narrower than ``shutil.get_terminal_size()`` reports,
        especially on Termux/mobile shells, so prefer prompt_toolkit's width whenever
        an app is active.
        """
        try:
            from prompt_toolkit.application import get_app
            return get_app().output.get_size().columns
        except Exception:
            return shutil.get_terminal_size(default).columns

    def _use_minimal_tui_chrome(self, width: Optional[int] = None) -> bool:
        """Hide low-value chrome on narrow/mobile terminals to preserve rows."""
        if width is None:
            width = self._get_tui_terminal_width()
        return width < 64

    @staticmethod
    def _scrollback_box_width(width: Optional[int] = None) -> int:
        """Return the full viewport width for printed scrollback box rules.

        Previously this clamped to ``max(32, min(width, 56))`` as a defense
        against terminal-emulator reflow on column-shrink (#25975, salvaging
        #24403).  That clamp made response/reasoning borders look stubby on
        any modern wide terminal.  We now trust the prompt_toolkit
        ``_output_screen_diff`` monkey-patch landed in #26137 (salvaging
        #25981) to keep chrome out of scrollback in the first place, and
        accept that an aggressive column-shrink may visually reflow already
        printed Panel borders — that's a cosmetic artifact of stamped
        scrollback history, not a live-render bug.

        A small floor (32 cols) is kept so the box still renders on tiny
        terminals without negative ``'─' * (w - 2)`` math.
        """
        if width is None:
            try:
                width = shutil.get_terminal_size((80, 24)).columns
            except Exception:
                width = 80
        return max(32, int(width or 80))

    def _tui_input_rule_height(self, position: str, width: Optional[int] = None) -> int:
        """Return the visible height for the top/bottom input separator rules."""
        if position not in {"top", "bottom"}:
            raise ValueError(f"Unknown input rule position: {position}")
        if getattr(self, "_status_bar_suppressed_after_resize", False):
            return 0
        if position == "top":
            return 1
        return 0 if self._use_minimal_tui_chrome(width=width) else 1

    def _agent_spacer_height(self, width: Optional[int] = None) -> int:
        """Return the spacer height shown above the status bar while the agent runs."""
        if not getattr(self, "_agent_running", False):
            return 0
        return 0 if self._use_minimal_tui_chrome(width=width) else 1

    def _spinner_widget_height(self, width: Optional[int] = None) -> int:
        """Return the visible height for the spinner/status text line above the status bar."""
        spinner_line = self._render_spinner_text()
        if not spinner_line:
            return 0
        if self._use_minimal_tui_chrome(width=width):
            return 0
        width = width or self._get_tui_terminal_width()
        if width and width > 10:
            import math
            text_width = self._status_bar_display_width(spinner_line)
            return max(1, math.ceil(text_width / width))
        return 1

    def _render_spinner_text(self) -> str:
        """Return the live spinner/status text exactly as rendered in the TUI."""
        txt = getattr(self, "_spinner_text", "")
        if not txt:
            return ""
        t0 = getattr(self, "_tool_start_time", 0) or 0
        if t0 > 0:
            elapsed = time.monotonic() - t0
            if elapsed >= 60:
                _m, _s = int(elapsed // 60), int(elapsed % 60)
                # Fixed-width timer to avoid status-line wrap jitter while
                # scrolling/repainting (e.g. 01m05s, 12m09s).
                elapsed_str = f"{_m:02d}m{_s:02d}s"
            else:
                # Keep width stable before the 60s rollover as well.
                elapsed_str = f"{elapsed:5.1f}s"
            return f"  {txt}  ({elapsed_str})"
        return f"  {txt}"

    # ── Petdex mascot (base-CLI pet pane) ───────────────────────────────
    #
    # Parity with the TUI: a half-block sprite rendered as a prompt_toolkit
    # window above the prompt, reacting to agent state and animated by a timer
    # that calls ``app.invalidate()``. Half-blocks only — the crisp Kitty image
    # protocol can't coexist with prompt_toolkit's patch_stdout output layer
    # (raw image escapes get swallowed/mangled), so we use truecolor styled
    # text, which prompt_toolkit renders natively in any 24-bit terminal.

=======
>>>>>>> rb/tag
    _PET_FRAME_INTERVAL = 0.16
    _PET_CFG_INTERVAL = 2.5

<<<<<<< HEAD
    def _pet_resolve_config(self) -> None:
        """(Re)resolve the active pet from config — picks up live enable/disable/

        switch made via ``/pet`` or ``hermes pets`` without a restart, mirroring
        the TUI's steady poll. Cheap and fail-open: any problem disables the pet.
        """
        try:
            from agent.pet import constants, store
            from agent.pet.render import PetRenderer
            from openagents_cli.config import load_config

            cfg = load_config()
            display = cfg.get("display", {}) if isinstance(cfg.get("display"), dict) else {}
            pet_cfg = display.get("pet", {}) if isinstance(display.get("pet"), dict) else {}

            enabled = bool(pet_cfg.get("enabled"))
            slug = str(pet_cfg.get("slug", "") or "")
            scale = float(pet_cfg.get("scale", constants.DEFAULT_SCALE) or constants.DEFAULT_SCALE)
            cols = constants.resolve_cols(scale, pet_cfg.get("unicode_cols", 0))

            if not enabled:
                with self._pet_lock:
                    self._pet_enabled = False
                    self._pet_renderer = None
                    self._pet_frames_cache.clear()
                return

            pet = store.resolve_active_pet(slug)
            if pet is None or not pet.exists:
                with self._pet_lock:
                    self._pet_enabled = False
                    self._pet_renderer = None
                    self._pet_frames_cache.clear()
                return

            with self._pet_lock:
                # Rebuild only when the resolved pet or geometry changes.
                if (
                    self._pet_renderer is None
                    or self._pet_slug != pet.slug
                    or self._pet_cols != cols
                    or self._pet_scale != scale
                ):
                    self._pet_renderer = PetRenderer(
                        str(pet.spritesheet), mode="unicode", scale=scale, unicode_cols=cols
                    )
                    self._pet_slug = pet.slug
                    self._pet_cols = cols
                    self._pet_scale = scale
                    self._pet_frames_cache.clear()
                    self._pet_frame_idx = 0
                self._pet_enabled = True
        except Exception:
            with self._pet_lock:
                self._pet_enabled = False
                self._pet_renderer = None

    def _pet_flash(self, state: str, secs: float = 1.6) -> None:
        """Briefly force a transient reaction (wave/jump/failed) before resting."""
        self._pet_event = state
        self._pet_event_until = time.monotonic() + secs

    def _pet_react_turn_end(self) -> None:
        """Flash the end-of-turn beat: failed on error, jump on a finished plan, else wave."""
        if not self._pet_enabled:
            return
        from agent.pet.state import todos_all_done

        if self._pet_turn_error:
            self._pet_flash("failed")
            return
        try:
            store = getattr(self.agent, "_todo_store", None)
            done = todos_all_done(store.read()) if store else False
        except Exception:
            done = False
        self._pet_flash("jump" if done else "wave")

    def _derive_pet_state(self) -> str:
        """Map current CLI activity to a pet animation state.

        A transient reaction beat (wave/jump/failed) wins while it's live;
        otherwise the steady state comes from the shared
        :func:`agent.pet.state.derive_pet_state` so the CLI can't drift from the
        TUI/desktop priority order.
        """
        if self._pet_event and time.monotonic() < self._pet_event_until:
            return self._pet_event
        self._pet_event = ""
        from agent.pet.state import derive_pet_state

        # A live blocking modal (approval / clarify / sudo / secret / slash
        # confirm) means the agent is paused on the user → the `waiting` pose,
        # which outranks the in-flight signals in derive_pet_state.
        awaiting_input = bool(
            self._approval_state
            or self._clarify_state
            or self._sudo_state
            or self._secret_state
            or getattr(self, "_slash_confirm_state", None)
        )

        return derive_pet_state(
            awaiting_input=awaiting_input,
            busy=getattr(self, "_agent_running", False),
            reasoning=self._pet_reasoning,
        ).value

    def _pet_frames_for(self, state: str) -> list:
        """Return (and cache) the half-block grids for one state."""
        cached = self._pet_frames_cache.get(state)
        if cached is not None:
            return cached
        renderer = self._pet_renderer
        if renderer is None:
            return []
        try:
            count = renderer.frame_count(state) or 1
            grids = [renderer.cells(state, i, cols=self._pet_cols) for i in range(count)]
        except Exception:
            grids = []
        self._pet_frames_cache[state] = grids
        return grids

    def _pet_fragments(self):
        """Return prompt_toolkit FormattedText for the current pet frame, or []."""
        with self._pet_lock:
            if not self._pet_enabled or self._pet_renderer is None:
                return []
            state = self._derive_pet_state()
            grids = self._pet_frames_for(state)
            if not grids:
                return []
            grid = grids[self._pet_frame_idx % len(grids)]

        frags = []
        for y, row in enumerate(grid):
            if y:
                frags.append(("", "\n"))
            for top, bottom in row:
                tr, tg, tb, ta = top
                br, bg, bb, ba = bottom
                top_op = ta >= 32
                bot_op = ba >= 32
                if not top_op and not bot_op:
                    frags.append(("", " "))
                elif top_op and bot_op:
                    frags.append((f"fg:#{tr:02x}{tg:02x}{tb:02x} bg:#{br:02x}{bg:02x}{bb:02x}", "▀"))
                elif top_op:
                    # Upper half only — leave the lower half the terminal's bg
                    # instead of painting it black (cleaner on light themes).
                    frags.append((f"fg:#{tr:02x}{tg:02x}{tb:02x}", "▀"))
                else:
                    frags.append((f"fg:#{br:02x}{bg:02x}{bb:02x}", "▄"))
        return frags

    def _pet_widget_height(self) -> int:
        """Visible rows for the pet window — 0 collapses it when no pet shows."""
        with self._pet_lock:
            if not self._pet_enabled or self._pet_renderer is None:
                return 0
            grids = self._pet_frames_for(self._derive_pet_state())
            if not grids or not grids[0]:
                return 0
            return len(grids[0])

    def _pet_anim_loop(self) -> None:
        """Advance the frame + invalidate on a timer while a pet is enabled."""
        while self._pet_anim_running:
            time.sleep(self._PET_FRAME_INTERVAL)
            now = time.monotonic()
            if now - self._pet_cfg_checked >= self._PET_CFG_INTERVAL:
                self._pet_cfg_checked = now
                self._pet_resolve_config()
            if not self._pet_enabled:
                continue
            with self._pet_lock:
                self._pet_frame_idx += 1
            app = getattr(self, "_app", None)
            if app is not None:
                try:
                    app.invalidate()
                except Exception:
                    pass

    def _pet_start_anim(self) -> None:
        if self._pet_anim_running:
            return
        self._pet_resolve_config()
        self._pet_anim_running = True
        self._pet_anim_thread = threading.Thread(target=self._pet_anim_loop, daemon=True)
        self._pet_anim_thread.start()

    def _pet_stop_anim(self) -> None:
        self._pet_anim_running = False
        thread = self._pet_anim_thread
        if thread is not None:
            thread.join(timeout=0.3)
        self._pet_anim_thread = None

    def _voice_record_key_label(self) -> str:
        """Return the configured voice push-to-talk key formatted for UI.

        Shared helper so every voice-facing status line / placeholder /
        recording hint advertises the SAME label as the registered
        prompt_toolkit binding.

        Cached at startup (see ``set_voice_record_key_cache``) rather
        than re-read per render. Two reasons (Copilot round-13 on
        #19835):

        * The prompt_toolkit binding is registered once at session
          start via ``@kb.add(_voice_key)``; re-reading config per
          render meant the status bar could advertise a new shortcut
          after a config edit while the actual binding was still the
          startup chord — exactly the display/binding drift this PR
          is trying to eliminate.
        * The label is on the hot render path (status bar + composer
          placeholder invalidated every 150ms during recording), so
          reading config on every call added avoidable UI overhead.
        """
        return getattr(self, "_voice_record_key_display_cache", None) or "Ctrl+B"

    def set_voice_record_key_cache(self, raw_key: object) -> None:
        """Populate the voice label cache from a raw ``voice.record_key``.

        Called at CLI startup after the prompt_toolkit binding is
        registered so the cached label always matches the live binding.
        """
        try:
            from openagents_cli.voice import format_voice_record_key_for_status
            self._voice_record_key_display_cache = format_voice_record_key_for_status(raw_key)
        except Exception:
            self._voice_record_key_display_cache = "Ctrl+B"

    def _get_voice_status_fragments(self, width: Optional[int] = None):
        """Return the voice status bar fragments for the interactive TUI."""
        width = width or self._get_tui_terminal_width()
        compact = self._use_minimal_tui_chrome(width=width)
        label = self._voice_record_key_label()
        if self._voice_recording:
            if compact:
                return [("class:voice-status-recording", " ● REC ")]
            return [("class:voice-status-recording", f" ● REC  {label} to stop ")]
        if self._voice_processing:
            if compact:
                return [("class:voice-status", " ◉ STT ")]
            return [("class:voice-status", " ◉ Transcribing... ")]
        if compact:
            return [("class:voice-status", f" 🎤 {label} ")]
        tts = " | TTS on" if self._voice_tts else ""
        cont = " | Continuous" if self._voice_continuous else ""
        return [("class:voice-status", f" 🎤 Voice mode{tts}{cont}  —  {label} to record ")]

    def _build_status_bar_text(self, width: Optional[int] = None) -> str:
        """Return a compact one-line session status string for the TUI footer."""
        try:
            snapshot = self._get_status_bar_snapshot()
            if width is None:
                width = self._get_tui_terminal_width()
            percent = snapshot["context_percent"]
            percent_label = f"{percent}%" if percent is not None else "--"
            duration_label = snapshot["duration"]

            yolo_active = self._is_session_yolo_active()
            brand = self._status_bar_brand_label()
            if width < 52:
                text = f"{brand} · {snapshot['model_short']} · {duration_label}"
                if yolo_active:
                    text += " · ⚠ YOLO"
                return self._trim_status_bar_text(text, width)
            if width < 76:
                parts = [brand, snapshot['model_short'], percent_label]
                compressions = snapshot.get("compressions", 0)
                if compressions:
                    parts.append(f"🗜️ {compressions}")
                bg_count = snapshot.get("active_background_tasks", 0)
                if bg_count:
                    parts.append(f"▶ {bg_count}")
                bg_proc_count = snapshot.get("active_background_processes", 0)
                if bg_proc_count:
                    parts.append(f"⚙ {bg_proc_count}")
                bg_subagent_count = snapshot.get("active_background_subagents", 0)
                if bg_subagent_count:
                    parts.append(f"⛓ {bg_subagent_count}")
                parts.append(duration_label)
                if yolo_active:
                    parts.append("⚠ YOLO")
                return self._trim_status_bar_text(" · ".join(parts), width)

            if snapshot["context_length"]:
                ctx_total = _format_context_length(snapshot["context_length"])
                ctx_used = format_token_count_compact(snapshot["context_tokens"])
                context_label = f"{ctx_used}/{ctx_total}"
            else:
                context_label = "ctx --"

            compressions = snapshot.get("compressions", 0)
            parts = [brand, snapshot['model_short'], context_label, percent_label]
            if compressions:
                parts.append(f"🗜️ {compressions}")
            bg_count = snapshot.get("active_background_tasks", 0)
            if bg_count:
                parts.append(f"▶ {bg_count}")
            bg_proc_count = snapshot.get("active_background_processes", 0)
            if bg_proc_count:
                parts.append(f"⚙ {bg_proc_count}")
            bg_subagent_count = snapshot.get("active_background_subagents", 0)
            if bg_subagent_count:
                parts.append(f"⛓ {bg_subagent_count}")
            parts.append(duration_label)
            prompt_elapsed = snapshot.get("prompt_elapsed")
            if prompt_elapsed:
                parts.append(prompt_elapsed)
            idle_since = snapshot.get("idle_since")
            if idle_since:
                parts.append(idle_since)
            if yolo_active:
                parts.append("⚠ YOLO")
            return self._trim_status_bar_text(" │ ".join(parts), width)
        except Exception:
            return f"{self._status_bar_brand_label()} · {self.model if getattr(self, 'model', None) else 'OpenAgents'}"

    def _get_status_bar_fragments(self):
        if not self._status_bar_visible or getattr(self, '_model_picker_state', None):
            return []
        try:
            snapshot = self._get_status_bar_snapshot()
            # Use prompt_toolkit's own terminal width when running inside the
            # TUI — shutil.get_terminal_size() can return stale or fallback
            # values (especially on SSH) that differ from what prompt_toolkit
            # actually renders, causing the fragments to overflow to a second
            # line and produce duplicated status bar rows over long sessions.
            width = self._get_tui_terminal_width()
            duration_label = snapshot["duration"]
            yolo_active = self._is_session_yolo_active()

            if width < 52:
                frags = list(self._status_bar_brand_fragments())
                frags.extend([
                    ("class:status-bar-strong", snapshot["model_short"]),
                    ("class:status-bar-dim", " · "),
                    ("class:status-bar-dim", duration_label),
                ])
                if yolo_active:
                    frags.append(("class:status-bar-dim", " · "))
                    frags.append(("class:status-bar-yolo", "⚠ YOLO"))
                frags.append(("class:status-bar", " "))
            else:
                percent = snapshot["context_percent"]
                percent_label = f"{percent}%" if percent is not None else "--"
                if width < 76:
                    compressions = snapshot.get("compressions", 0)
                    bg_count = snapshot.get("active_background_tasks", 0)
                    bg_proc_count = snapshot.get("active_background_processes", 0)
                    bg_subagent_count = snapshot.get("active_background_subagents", 0)
                    frags = list(self._status_bar_brand_fragments())
                    frags.extend([
                        ("class:status-bar-strong", snapshot["model_short"]),
                        ("class:status-bar-dim", " · "),
                        (self._status_bar_context_style(percent), percent_label),
                    ])
                    if compressions:
                        frags.append(("class:status-bar-dim", " · "))
                        frags.append((self._compression_count_style(compressions), f"🗜️ {compressions}"))
                    if bg_count:
                        frags.append(("class:status-bar-dim", " · "))
                        frags.append(("class:status-bar-strong", f"▶ {bg_count}"))
                    if bg_proc_count:
                        frags.append(("class:status-bar-dim", " · "))
                        frags.append(("class:status-bar-strong", f"⚙ {bg_proc_count}"))
                    if bg_subagent_count:
                        frags.append(("class:status-bar-dim", " · "))
                        frags.append(("class:status-bar-strong", f"⛓ {bg_subagent_count}"))
                    frags.extend([
                        ("class:status-bar-dim", " · "),
                        ("class:status-bar-dim", duration_label),
                    ])
                    if yolo_active:
                        frags.append(("class:status-bar-dim", " · "))
                        frags.append(("class:status-bar-yolo", "⚠ YOLO"))
                    frags.append(("class:status-bar", " "))
                else:
                    if snapshot["context_length"]:
                        ctx_total = _format_context_length(snapshot["context_length"])
                        ctx_used = format_token_count_compact(snapshot["context_tokens"])
                        context_label = f"{ctx_used}/{ctx_total}"
                    else:
                        context_label = "ctx --"

                    bar_style = self._status_bar_context_style(percent)
                    compressions = snapshot.get("compressions", 0)
                    bg_count = snapshot.get("active_background_tasks", 0)
                    bg_proc_count = snapshot.get("active_background_processes", 0)
                    bg_subagent_count = snapshot.get("active_background_subagents", 0)
                    frags = list(self._status_bar_brand_fragments())
                    frags.extend([
                        ("class:status-bar-strong", snapshot["model_short"]),
                        ("class:status-bar-dim", " │ "),
                        ("class:status-bar-dim", context_label),
                        ("class:status-bar-dim", " │ "),
                        (bar_style, self._build_context_bar(percent)),
                        ("class:status-bar-dim", " "),
                        (bar_style, percent_label),
                    ])
                    if compressions:
                        frags.append(("class:status-bar-dim", " │ "))
                        frags.append((self._compression_count_style(compressions), f"🗜️ {compressions}"))
                    if bg_count:
                        frags.append(("class:status-bar-dim", " │ "))
                        frags.append(("class:status-bar-strong", f"▶ {bg_count}"))
                    if bg_proc_count:
                        frags.append(("class:status-bar-dim", " │ "))
                        frags.append(("class:status-bar-strong", f"⚙ {bg_proc_count}"))
                    if bg_subagent_count:
                        frags.append(("class:status-bar-dim", " │ "))
                        frags.append(("class:status-bar-strong", f"⛓ {bg_subagent_count}"))
                    frags.extend([
                        ("class:status-bar-dim", " │ "),
                        ("class:status-bar-dim", duration_label),
                    ])
                    # Position 7: per-prompt elapsed timer (live or frozen)
                    prompt_elapsed = snapshot.get("prompt_elapsed")
                    if prompt_elapsed:
                        frags.append(("class:status-bar-dim", " │ "))
                        frags.append(("class:status-bar-dim", prompt_elapsed))
                    # Position 8: idle time since the last final agent response
                    idle_since = snapshot.get("idle_since")
                    if idle_since:
                        frags.append(("class:status-bar-dim", " │ "))
                        frags.append(("class:status-bar-dim", idle_since))
                    if yolo_active:
                        frags.append(("class:status-bar-dim", " │ "))
                        frags.append(("class:status-bar-yolo", "⚠ YOLO"))
                    frags.append(("class:status-bar", " "))

            total_width = sum(self._status_bar_display_width(text) for _, text in frags)
            if total_width > width:
                plain_text = "".join(text for _, text in frags)
                trimmed = self._trim_status_bar_text(plain_text, width)
                return [("class:status-bar", trimmed)]
            return frags
        except Exception:
            return [("class:status-bar", f" {self._build_status_bar_text()} ")]

    def _normalize_model_for_provider(self, resolved_provider: str) -> bool:
        """Normalize provider-specific model IDs and routing."""
        current_model = (self.model or "").strip()
        changed = False

        try:
            from openagents_cli.model_normalize import (
                _AGGREGATOR_PROVIDERS,
                normalize_model_for_provider,
            )

            if resolved_provider not in _AGGREGATOR_PROVIDERS:
                normalized_model = normalize_model_for_provider(current_model, resolved_provider)
                if normalized_model and normalized_model != current_model:
                    if not self._model_is_default:
                        self._console_print(
                            f"[yellow]⚠️  Normalized model '{current_model}' to '{normalized_model}' for {resolved_provider}.[/]"
                        )
                    self.model = normalized_model
                    current_model = normalized_model
                    changed = True
        except Exception:
            pass

        if resolved_provider == "copilot":
            try:
                from openagents_cli.models import copilot_model_api_mode, normalize_copilot_model_id

                canonical = normalize_copilot_model_id(current_model, api_key=self.api_key)
                if canonical and canonical != current_model:
                    if not self._model_is_default:
                        self._console_print(
                            f"[yellow]⚠️  Normalized Copilot model '{current_model}' to '{canonical}'.[/]"
                        )
                    self.model = canonical
                    current_model = canonical
                    changed = True

                resolved_mode = copilot_model_api_mode(current_model, api_key=self.api_key)
                if resolved_mode != self.api_mode:
                    self.api_mode = resolved_mode
                    changed = True
            except Exception:
                pass
            return changed

        if resolved_provider in {"opencode-zen", "opencode-go"}:
            try:
                from openagents_cli.models import normalize_opencode_model_id, opencode_model_api_mode

                canonical = normalize_opencode_model_id(resolved_provider, current_model)
                if canonical and canonical != current_model:
                    if not self._model_is_default:
                        self._console_print(
                            f"[yellow]⚠️  Stripped provider prefix from '{current_model}'; using '{canonical}' for {resolved_provider}.[/]"
                        )
                    self.model = canonical
                    current_model = canonical
                    changed = True

                resolved_mode = opencode_model_api_mode(resolved_provider, current_model)
                if resolved_mode != self.api_mode:
                    self.api_mode = resolved_mode
                    changed = True
            except Exception:
                pass
            return changed

        if resolved_provider != "openai-codex":
            return changed

        # 1. Strip provider prefix ("openai/gpt-5.4" → "gpt-5.4")
        if "/" in current_model:
            slug = current_model.split("/", 1)[1]
            if not self._model_is_default:
                self._console_print(
                    f"[yellow]⚠️  Stripped provider prefix from '{current_model}'; "
                    f"using '{slug}' for OpenAI Codex.[/]"
                )
            self.model = slug
            current_model = slug
            changed = True

        # 2. Replace untouched default with a Codex model
        if self._model_is_default:
            fallback_model = "gpt-5.3-codex"
            try:
                from openagents_cli.codex_models import get_codex_model_ids

                available = get_codex_model_ids(
                    access_token=self.api_key if self.api_key else None,
                )
                if available:
                    fallback_model = available[0]
            except Exception:
                pass

            if current_model != fallback_model:
                self.model = fallback_model
                changed = True

        return changed

    def _on_thinking(self, text: str) -> None:
        """Called by agent when thinking starts/stops. Updates TUI spinner."""
        if not text:
            self._flush_reasoning_preview(force=True)
        self._spinner_text = text or ""
        self._tool_start_time = 0.0  # clear tool timer when switching to thinking
        self._invalidate()

    def _on_notice(self, notice) -> None:
        """Queue an out-of-band AgentNotice for rendering at the next clean boundary.

        Notices fire from inside the agent turn (cold-start seed during _init_agent,
        per-turn _capture_credits after the API call) — printing immediately races the
        streaming response and the line gets buried behind the prompt (see _cprint's
        bg-thread caveat). So we QUEUE here and flush in _flush_credit_notices(), called
        right after run_conversation returns. Fail-soft: never break the turn.
        """
        try:
            text = getattr(notice, "text", "") or ""
            if not text:
                return
            level = getattr(notice, "level", "info") or "info"
            if not hasattr(self, "_pending_credit_notices"):
                self._pending_credit_notices = []
            self._pending_credit_notices.append((level, text))
        except Exception:
            pass

    def _flush_credit_notices(self) -> None:
        """Print any queued credit notices as level-colored lines. Called at turn end
        (after run_conversation) where _cprint paints cleanly above the prompt."""
        try:
            pending = getattr(self, "_pending_credit_notices", None)
            if not pending:
                return
            self._pending_credit_notices = []
            for level, text in pending:
                color = {
                    "error": "\033[31m",
                    "warn": "\033[33m",
                    "success": "\033[32m",
                    "info": _DIM,
                }.get(level, _DIM)
                _cprint(f"  {color}{text}{_RST}")
        except Exception:
            pass

    def _on_notice_clear(self, key: str) -> None:
        """Notice cleared. The REPL prints lines (no persistent slot to wipe), so
        this drops any still-queued notice with that key is not tracked by key here;
        it's a no-op for rendering — kept so the agent's clear callback is bound
        symmetrically with the show callback (and so future REPL UIs can hook it)."""
        return

    # ── Streaming display ────────────────────────────────────────────────

    def _current_reasoning_callback(self):
        """Return the active reasoning display callback for the current mode."""
        if self.show_reasoning and self.streaming_enabled:
            return self._stream_reasoning_delta
        if self.verbose and not self.show_reasoning:
            return self._on_reasoning
        return None

    def _emit_reasoning_preview(self, reasoning_text: str) -> None:
        """Render a buffered reasoning preview as a single [thinking] block."""
        preview_text = reasoning_text.strip()
        if not preview_text:
            return

        try:
            term_width = shutil.get_terminal_size().columns
        except Exception:
            term_width = 80
        prefix = "  [thinking] "
        wrap_width = max(30, term_width - len(prefix) - 2)

        paragraphs = []
        raw_paragraphs = re.split(r"\n\s*\n+", preview_text.replace("\r\n", "\n"))
        for paragraph in raw_paragraphs:
            compact = " ".join(line.strip() for line in paragraph.splitlines() if line.strip())
            if compact:
                paragraphs.append(textwrap.fill(compact, width=wrap_width))
        preview_text = "\n".join(paragraphs)
        if not preview_text:
            return

        if self.verbose:
            _cprint(f"  {_DIM}[thinking] {preview_text}{_RST}")
            return

        lines = preview_text.splitlines()
        if len(lines) > 5:
            preview = "\n".join(lines[:5])
            preview += f"\n  ... ({len(lines) - 5} more lines)"
        else:
            preview = preview_text
        _cprint(f"  {_DIM}[thinking] {preview}{_RST}")

    def _flush_reasoning_preview(self, *, force: bool = False) -> None:
        """Flush buffered reasoning text at natural boundaries.

        Some providers stream reasoning in tiny word or punctuation chunks.
        Buffer them here so the preview path does not print one `[thinking]`
        line per token.
        """
        buf = getattr(self, "_reasoning_preview_buf", "")
        if not buf:
            return

        try:
            term_width = shutil.get_terminal_size().columns
        except Exception:
            term_width = 80
        target_width = max(40, term_width - len("  [thinking] ") - 4)

        flush_text = ""

        if force:
            flush_text = buf
            buf = ""
        else:
            line_break = buf.rfind("\n")
            min_newline_flush = max(16, target_width // 3)
            if line_break != -1 and (
                line_break >= min_newline_flush
                or buf.endswith("\n\n")
                or buf.endswith(".\n")
                or buf.endswith("!\n")
                or buf.endswith("?\n")
                or buf.endswith(":\n")
            ):
                flush_text = buf[: line_break + 1]
                buf = buf[line_break + 1 :]
            elif len(buf) >= target_width:
                search_start = max(20, target_width // 2)
                search_end = min(len(buf), max(target_width + (target_width // 3), target_width + 8))
                cut = -1
                for boundary in (" ", "\t", ".", "!", "?", ",", ";", ":"):
                    cut = max(cut, buf.rfind(boundary, search_start, search_end))
                if cut != -1:
                    flush_text = buf[: cut + 1]
                    buf = buf[cut + 1 :]

        self._reasoning_preview_buf = buf.lstrip() if flush_text else buf
        if flush_text:
            self._emit_reasoning_preview(flush_text)

    def _format_submitted_user_message_preview(self, user_input: str) -> str:
        """Format the submitted user-message scrollback preview."""
        ts_suffix = (
            f" [dim]{datetime.now().strftime('%H:%M')}[/]"
            if getattr(self, "show_timestamps", False) else ""
        )
        lines = user_input.split("\n")
        if len(lines) <= 1:
            return f"[bold {_accent_hex()}]●[/] [bold]{_escape(user_input)}[/]{ts_suffix}"

        first_lines = int(getattr(self, "user_message_preview_first_lines", 2))
        last_lines = int(getattr(self, "user_message_preview_last_lines", 2))
        first_lines = max(1, first_lines)
        last_lines = max(0, last_lines)
        head = lines[:first_lines]
        remaining_after_head = max(0, len(lines) - len(head))
        tail_count = min(last_lines, remaining_after_head)
        tail = lines[-tail_count:] if tail_count else []

        hidden_middle_count = len(lines) - len(head) - len(tail)
        if hidden_middle_count < 0:
            hidden_middle_count = 0
            tail = []

        preview_lines = [
            f"[bold {_accent_hex()}]●[/] [bold]{_escape(head[0])}[/]{ts_suffix}"
        ]
        preview_lines.extend(f"[bold]{_escape(line)}[/]" for line in head[1:])

        if hidden_middle_count > 0:
            noun = "line" if hidden_middle_count == 1 else "lines"
            preview_lines.append(f"[dim]... (+{hidden_middle_count} more {noun})[/]")

        preview_lines.extend(f"[bold]{_escape(line)}[/]" for line in tail)
        return "\n".join(preview_lines)

    def _expand_paste_references(self, text: str | None) -> str:
        """Expand [Pasted text #N -> file] placeholders into file contents."""
        if not isinstance(text, str) or "[Pasted text #" not in text:
            return text or ""
        paste_ref_re = re.compile(r'\[Pasted text #\d+: \d+ lines \u2192 (.+?)\]')

        def _expand_ref(match):
            path = Path(match.group(1))
            # Use try/except instead of path.exists() to avoid TOCTOU race:
            # the paste file may be deleted between check and read, causing
            # the input to be silently dropped (#17666).
            try:
                return path.read_text(encoding="utf-8")
            except (OSError, IOError):
                logger.warning("Paste file gone or unreadable, returning placeholder: %s", path)
                return match.group(0)

        return paste_ref_re.sub(_expand_ref, text)

    def _print_user_message_preview(self, user_input: str) -> None:
        """Render a user message using the normal chat scrollback style."""
        ChatConsole().print(f"[{_accent_hex()}]{'─' * 40}[/]")
        text = str(user_input or "")
        if "\n" in text:
            ChatConsole().print(self._format_submitted_user_message_preview(text))
        else:
            ChatConsole().print(f"[bold {_accent_hex()}]●[/] [bold]{_escape(text)}[/]")

    def _stream_reasoning_delta(self, text: str) -> None:
        """Stream reasoning/thinking tokens into a dim box above the response.

        Opens a dim reasoning box on first token, streams line-by-line.
        The box is closed automatically when content tokens start arriving
        (via _stream_delta → _emit_stream_text).

        Once the response box is open, suppress any further reasoning
        rendering — a late thinking block (e.g. after an interrupt) would
        otherwise draw a reasoning box inside the response box.
        """
        if not text:
            return
        self._reasoning_shown_this_turn = True
        if getattr(self, "_stream_box_opened", False):
            return

        # Open reasoning box on first reasoning token
        if not getattr(self, "_reasoning_box_opened", False):
            self._reasoning_box_opened = True
            w = self._scrollback_box_width()
            r_label = " Reasoning "
            r_fill = w - 2 - len(r_label)
            _cprint(f"\n{_DIM}┌─{r_label}{'─' * max(r_fill - 1, 0)}┐{_RST}")

        self._reasoning_buf = getattr(self, "_reasoning_buf", "") + text

        # Emit complete lines, and force-flush long partial lines so
        # reasoning is visible in real-time even without newlines.
        while "\n" in self._reasoning_buf:
            line, self._reasoning_buf = self._reasoning_buf.split("\n", 1)
            _cprint(f"{_DIM}{line}{_RST}")
        if len(self._reasoning_buf) > 80:
            _cprint(f"{_DIM}{self._reasoning_buf}{_RST}")
            self._reasoning_buf = ""

    def _close_reasoning_box(self) -> None:
        """Close the live reasoning box if it's open."""
        if getattr(self, "_reasoning_box_opened", False):
            # Flush remaining reasoning buffer
            buf = getattr(self, "_reasoning_buf", "")
            if buf:
                _cprint(f"{_DIM}{buf}{_RST}")
                self._reasoning_buf = ""
            w = self._scrollback_box_width()
            _cprint(f"{_DIM}└{'─' * (w - 2)}┘{_RST}")
            self._reasoning_box_opened = False

            # Flush any content that was deferred while reasoning was rendering.
            deferred = getattr(self, "_deferred_content", "")
            if deferred:
                self._deferred_content = ""
                self._emit_stream_text(deferred)

    def _stream_delta(self, text) -> None:
        """Line-buffered streaming callback for real-time token rendering.

        Receives text deltas from the agent as tokens arrive. Buffers
        partial lines and emits complete lines via _cprint to work
        reliably with prompt_toolkit's patch_stdout.

        Reasoning/thinking blocks (<REASONING_SCRATCHPAD>, <think>, etc.)
        are suppressed during streaming since they'd display raw XML tags.
        The agent strips them from the final response anyway.

        A ``None`` value signals an intermediate turn boundary (tools are
        about to execute).  Flushes any open boxes and resets state so
        tool feed lines render cleanly between turns.
        """
        if text is None:
            self._flush_stream()
            self._reset_stream_state()
            return
        if not text:
            return

        self._stream_started = True

        # ── Tag-based reasoning suppression ──
        # Track whether we're inside a reasoning/thinking block.
        # These tags are model-generated (system prompt tells the model
        # to use them) and get stripped from final_response. We must
        # suppress them during streaming too — unless show_reasoning is
        # enabled, in which case we route the inner content to the
        # reasoning display box instead of discarding it.
        _OPEN_TAGS = ("<REASONING_SCRATCHPAD>", "<think>", "<reasoning>", "<THINKING>", "<thinking>", "<thought>")
        _CLOSE_TAGS = ("</REASONING_SCRATCHPAD>", "</think>", "</reasoning>", "</THINKING>", "</thinking>", "</thought>")

        # Append to a pre-filter buffer first
        self._stream_prefilt = getattr(self, "_stream_prefilt", "") + text

        # Check if we're entering a reasoning block.
        # Only match tags that appear at a "block boundary": start of the
        # stream, after a newline (with optional whitespace), or when nothing
        # but whitespace has been emitted on the current line.
        # This prevents false positives when models *mention* tags in prose
        # like "(/think not producing <think> tags)".
        #
        # _stream_last_was_newline tracks whether the last character emitted
        # (or the start of the stream) is a line boundary.  It's True at
        # stream start and set True whenever emitted text ends with '\n'.
        if not hasattr(self, "_stream_last_was_newline"):
            self._stream_last_was_newline = True  # start of stream = boundary

        if not getattr(self, "_in_reasoning_block", False):
            for tag in _OPEN_TAGS:
                search_start = 0
                while True:
                    idx = self._stream_prefilt.find(tag, search_start)
                    if idx == -1:
                        break
                    # Check if this is a block boundary position
                    preceding = self._stream_prefilt[:idx]
                    if idx == 0:
                        # At buffer start — only a boundary if we're at
                        # a line start (stream start or last emit ended
                        # with newline)
                        is_block_boundary = getattr(self, "_stream_last_was_newline", True)
                    else:
                        # Find last newline in the buffer before the tag
                        last_nl = preceding.rfind("\n")
                        if last_nl == -1:
                            # No newline in buffer — boundary only if
                            # last emit was a newline AND only whitespace
                            # has accumulated before the tag
                            is_block_boundary = (
                                getattr(self, "_stream_last_was_newline", True)
                                and preceding.strip() == ""
                            )
                        else:
                            # Text between last newline and tag must be
                            # whitespace-only
                            is_block_boundary = preceding[last_nl + 1:].strip() == ""
                    if is_block_boundary:
                        # Emit everything before the tag
                        if preceding:
                            self._emit_stream_text(preceding)
                            self._stream_last_was_newline = preceding.endswith("\n")
                        self._in_reasoning_block = True
                        self._stream_prefilt = self._stream_prefilt[idx + len(tag):]
                        break
                    # Not a block boundary — keep searching after this occurrence
                    search_start = idx + 1
                if getattr(self, "_in_reasoning_block", False):
                    break

            # Could also be a partial open tag at the end — hold it back
            if not getattr(self, "_in_reasoning_block", False):
                # Check for partial tag match at the end
                safe = self._stream_prefilt
                for tag in _OPEN_TAGS:
                    for i in range(1, len(tag)):
                        if self._stream_prefilt.endswith(tag[:i]):
                            safe = self._stream_prefilt[:-i]
                            break
                if safe:
                    self._emit_stream_text(safe)
                    self._stream_last_was_newline = safe.endswith("\n")
                    self._stream_prefilt = self._stream_prefilt[len(safe):]
                return

        # Inside a reasoning block — look for close tag.
        # Keep accumulating _stream_prefilt because close tags can arrive
        # split across multiple tokens (e.g. "</REASONING_SCRATCH" + "PAD>...").
        if getattr(self, "_in_reasoning_block", False):
            for tag in _CLOSE_TAGS:
                idx = self._stream_prefilt.find(tag)
                if idx != -1:
                    self._in_reasoning_block = False
                    # When show_reasoning is on, route inner content to
                    # the reasoning display box instead of discarding.
                    if self.show_reasoning:
                        inner = self._stream_prefilt[:idx]
                        if inner:
                            self._stream_reasoning_delta(inner)
                    after = self._stream_prefilt[idx + len(tag):]
                    self._stream_prefilt = ""
                    # Process remaining text after close tag through full
                    # filtering (it could contain another open tag)
                    if after:
                        self._stream_delta(after)
                    return
            # When show_reasoning is on, stream reasoning content live
            # instead of silently accumulating. Keep only the tail that
            # could be a partial close tag prefix.
            max_tag_len = max(len(t) for t in _CLOSE_TAGS)
            if len(self._stream_prefilt) > max_tag_len:
                if self.show_reasoning:
                    # Route the safe prefix to reasoning display
                    safe_reasoning = self._stream_prefilt[:-max_tag_len]
                    self._stream_reasoning_delta(safe_reasoning)
                self._stream_prefilt = self._stream_prefilt[-max_tag_len:]
            return

    def _emit_stream_text(self, text: str) -> None:
        """Emit filtered text to the streaming display."""
        if not text:
            return

        # When show_reasoning is on and reasoning is still rendering,
        # defer content until the reasoning box closes.  This ensures the
        # reasoning block always appears BEFORE the response in the terminal.
        if self.show_reasoning and getattr(self, "_reasoning_box_opened", False):
            self._deferred_content = getattr(self, "_deferred_content", "") + text
            return

        # Close the live reasoning box before opening the response box
        self._close_reasoning_box()

        # Open the response box header on the very first visible text
        if not self._stream_box_opened:
            # Strip leading whitespace/newlines before first visible content
            text = text.lstrip("\n")
            if not text:
                return
            self._stream_box_opened = True
            try:
                from openagents_cli.skin_engine import get_active_skin
                _skin = get_active_skin()
                label = _skin.get_branding("response_label", "⚕ Hermes")
                _text_hex = _skin.get_color("banner_text", "#FFF8DC")
            except Exception:
                label = "⚕ Hermes"
                _text_hex = "#FFF8DC"
            # Build a true-color ANSI escape for the response text color
            # so streamed content matches the Rich Panel appearance.
            try:
                _r = int(_text_hex[1:3], 16)
                _g = int(_text_hex[3:5], 16)
                _b = int(_text_hex[5:7], 16)
                self._stream_text_ansi = f"\033[38;2;{_r};{_g};{_b}m"
            except (ValueError, IndexError):
                self._stream_text_ansi = ""
            if self.show_timestamps:
                label = f"{label} {datetime.now().strftime('%H:%M')}"
            w = self._scrollback_box_width()
            fill = w - 2 - HermesCLI._status_bar_display_width(label)
            _cprint(f"\n{_ACCENT}╭─{label}{'─' * max(fill - 1, 0)}╮{_RST}")

        self._stream_buf += text

        # Emit complete lines, keep partial remainder in buffer
        _tc = getattr(self, "_stream_text_ansi", "")

        def _emit_one(printed_line: str) -> None:
            _cprint(f"{_STREAM_PAD}{_tc}{printed_line}{_RST}" if _tc else f"{_STREAM_PAD}{printed_line}")

        def _flush_table_buf() -> None:
            buf = self._stream_table_buf
            self._stream_table_buf = []
            self._in_stream_table = False
            if not buf:
                return
            # Strip cell-level markdown (`code`, **bold**, ~~strike~~) FIRST
            # so the realigner pads to the final visible cell width, not
            # the marker-decorated source width.  Otherwise a body row
            # like `` | Bold | `**bold**` | `` lands narrower than its
            # header column once the markers are removed.
            joined = "\n".join(buf)
            if self.final_response_markdown == "strip":
                joined = _strip_markdown_syntax(joined)
            block = realign_markdown_tables(joined, _terminal_width_for_streaming())
            for ln in block.split("\n"):
                _emit_one(ln)

        while "\n" in self._stream_buf:
            line, self._stream_buf = self._stream_buf.split("\n", 1)

            # Hold table-shaped lines in a side-buffer so we can re-pad
            # the whole block once it ends.  Streaming line-by-line, we
            # cannot re-align mid-table without reflowing already-printed
            # rows; the cost is that the user sees the table appear in a
            # single batch when the block closes instead of row-by-row.
            if self._in_stream_table:
                if looks_like_table_row(line) or is_table_divider(line):
                    self._stream_table_buf.append(line)
                    continue
                # Block ended — flush the realigned table, then fall
                # through to print the current (non-table) line.
                _flush_table_buf()
            elif looks_like_table_row(line):
                self._stream_table_buf.append(line)
                self._in_stream_table = True
                continue

            if self.final_response_markdown == "strip":
                line = _strip_markdown_syntax(line)
            _emit_one(line)

    def _flush_stream(self) -> None:
        """Emit any remaining partial line from the stream buffer and close the box."""
        # If we're still inside a "reasoning block" at end-of-stream, it was
        # a false positive — the model mentioned a tag like <think> in prose
        # but never closed it.  Recover the buffered content as regular text.
        if getattr(self, "_in_reasoning_block", False) and getattr(self, "_stream_prefilt", ""):
            self._in_reasoning_block = False
            self._emit_stream_text(self._stream_prefilt)
            self._stream_prefilt = ""

        # Close reasoning box if still open (in case no content tokens arrived)
        self._close_reasoning_box()

        _tc = getattr(self, "_stream_text_ansi", "")

        # If the stream buffer has a trailing partial line that looks like
        # a table row, fold it into the table buffer so the whole block
        # gets re-aligned together.  Otherwise the final row prints raw
        # (with the model's original under-padded spacing) while the rows
        # above it are aligned.
        if (
            self._stream_buf
            and getattr(self, "_in_stream_table", False)
            and (looks_like_table_row(self._stream_buf) or is_table_divider(self._stream_buf))
        ):
            self._stream_table_buf.append(self._stream_buf)
            self._stream_buf = ""

        # Flush any buffered table rows first so their padding is
        # finalised before the stream remainder lands.
        if getattr(self, "_stream_table_buf", None):
            joined = "\n".join(self._stream_table_buf)
            self._stream_table_buf = []
            self._in_stream_table = False
            if self.final_response_markdown == "strip":
                joined = _strip_markdown_syntax(joined)
            block = realign_markdown_tables(joined, _terminal_width_for_streaming())
            for ln in block.split("\n"):
                _cprint(f"{_STREAM_PAD}{_tc}{ln}{_RST}" if _tc else f"{_STREAM_PAD}{ln}")

        if self._stream_buf:
            line = _strip_markdown_syntax(self._stream_buf) if self.final_response_markdown == "strip" else self._stream_buf
            _cprint(f"{_STREAM_PAD}{_tc}{line}{_RST}" if _tc else f"{_STREAM_PAD}{line}")
            self._stream_buf = ""

        # Close the response box
        if self._stream_box_opened:
            w = self._scrollback_box_width()
            _cprint(f"{_ACCENT}╰{'─' * (w - 2)}╯{_RST}")

    def _reset_stream_state(self) -> None:
        """Reset streaming state before each agent invocation."""
        self._stream_buf = ""
        self._stream_started = False
        self._stream_box_opened = False
        self._stream_text_ansi = ""
        self._stream_prefilt = ""
        self._in_reasoning_block = False
        self._stream_last_was_newline = True
        self._reasoning_box_opened = False
        self._reasoning_buf = ""
        self._reasoning_preview_buf = ""
        self._deferred_content = ""
        self._stream_table_buf = []
        self._in_stream_table = False

    def _slow_command_status(self, command: str) -> str:
        """Return a user-facing status message for slower slash commands."""
        cmd_lower = command.lower().strip()
        if cmd_lower.startswith("/skills search"):
            return "Searching skills..."
        if cmd_lower.startswith("/skills browse"):
            return "Loading skills..."
        if cmd_lower.startswith("/skills inspect"):
            return "Inspecting skill..."
        if cmd_lower.startswith("/skills install"):
            return "Installing skill..."
        if cmd_lower.startswith("/skills"):
            return "Processing skills command..."
        if cmd_lower == "/reload-mcp":
            return "Reloading MCP servers..."
        if cmd_lower == "/reload-skills" or cmd_lower == "/reload_skills":
            return "Reloading skills..."
        if cmd_lower.startswith("/browser"):
            return "Configuring browser..."
        return "Processing command..."

    def _command_spinner_frame(self) -> str:
        """Return the current spinner frame for slow slash commands."""
        frame_idx = int(time.monotonic() * 10) % len(_COMMAND_SPINNER_FRAMES)
        return _COMMAND_SPINNER_FRAMES[frame_idx]

    @contextmanager
    def _busy_command(self, status: str):
        """Expose a temporary busy state in the TUI while a slash command runs."""
        self._command_running = True
        self._command_status = status
        self._invalidate(min_interval=0.0)
        try:
            print(f"⏳ {status}")
            yield
        finally:
            self._command_running = False
            self._command_status = ""
            self._invalidate(min_interval=0.0)

    def _open_external_editor(self, buffer=None) -> bool:
        """Open the active input buffer in an external editor."""
        app = getattr(self, "_app", None)
        if not app:
            _cprint(f"{_DIM}External editor is only available inside the interactive CLI.{_RST}")
            return False
        if self._command_running:
            _cprint(f"{_DIM}Wait for the current command to finish before opening the editor.{_RST}")
            return False
        if self._sudo_state or self._secret_state or self._approval_state or getattr(self, "_slash_confirm_state", None) or self._clarify_state:
            _cprint(f"{_DIM}Finish the active prompt before opening the editor.{_RST}")
            return False
        target_buffer = buffer or getattr(app, "current_buffer", None)
        if target_buffer is None:
            _cprint(f"{_DIM}No active input buffer is available for the external editor.{_RST}")
            return False
        try:
            existing_text = getattr(target_buffer, "text", "")
            expanded_text = self._expand_paste_references(existing_text)
            if expanded_text != existing_text and hasattr(target_buffer, "text"):
                self._skip_paste_collapse = True
                target_buffer.text = expanded_text
                if hasattr(target_buffer, "cursor_position"):
                    target_buffer.cursor_position = len(expanded_text)
            # Set skip flag (again) so the text-change event fired when the
            # editor closes does not re-collapse the returned content.
            self._skip_paste_collapse = True
            # Open the editor, then submit the saved draft on a clean exit —
            # matching the TUI's Ctrl+G (openEditor), which sends the buffer
            # instead of requiring a second Enter. Submission in this CLI is
            # driven by the custom `enter` keybinding, NOT the buffer's
            # accept_handler, so validate_and_handle can't route through it;
            # chain a done-callback on the returned Task that re-uses the
            # real submit pipeline via _submit_editor_buffer().
            task = target_buffer.open_in_editor(validate_and_handle=False)
            if task is not None and hasattr(task, "add_done_callback"):
                task.add_done_callback(
                    lambda _t, b=target_buffer: self._submit_editor_buffer(b)
                )
            return True
        except Exception as exc:
            _cprint(f"{_DIM}Failed to open external editor: {exc}{_RST}")
            return False

    def _submit_editor_buffer(self, buffer) -> None:
        """Submit the draft an external editor left in ``buffer``.

        Invoked from the Ctrl+G done-callback so saving the editor sends the
        prompt (TUI parity) instead of leaving it sitting in the input area.
        Mirrors the idle/queue branches of the `enter` keybinding handler:
        an empty save is ignored (never submits a blank turn), a slash command
        is dispatched, otherwise the text is routed through the same input
        queues the normal Enter path uses. Runs on the prompt_toolkit event
        loop via the Task callback, so it must be cheap and non-blocking.
        """
        try:
            text = (getattr(buffer, "text", "") or "").strip()
        except Exception:
            return
        if not text:
            # Editor saved empty / was cleared — match the TUI, which drops
            # an empty draft instead of submitting a blank turn.
            return

        app = getattr(self, "_app", None)

        # Slash commands: dispatch directly, same as the Enter handler's
        # _looks_like_slash_command branch.
        if _looks_like_slash_command(text):
            try:
                if not self.process_command(text):
                    self._should_exit = True
                    if app is not None and app.is_running:
                        app.exit()
            except Exception as exc:
                _cprint(f"  {_DIM}Command failed: {exc}{_RST}")
            finally:
                self._reset_input_buffer(buffer)
                if app is not None:
                    app.invalidate()
            return

        # Regular prompt: route through the same queues the Enter handler uses.
        if self._agent_running:
            # Agent busy → honour the configured busy-input behaviour by
            # queueing for the next turn (the safe default; interrupt/steer
            # remain reachable via the normal Enter path).
            self._interrupt_queue.put(text) if self.busy_input_mode == "interrupt" else self._pending_input.put(text)
            preview = text[:80] + ("..." if len(text) > 80 else "")
            _cprint(f"  Queued for the next turn: {preview}")
        else:
            self._pending_input.put(text)

        self._reset_input_buffer(buffer)
        if app is not None:
            app.invalidate()

    def _reset_input_buffer(self, buffer) -> None:
        """Clear an input buffer after a programmatic submit (best-effort)."""
        try:
            buffer.reset(append_to_history=True)
        except Exception:
            try:
                buffer.text = ""
            except Exception:
                pass



||||||| cf299e9a01
    def _pet_resolve_config(self) -> None:
        """(Re)resolve the active pet from config — picks up live enable/disable/

        switch made via ``/pet`` or ``hermes pets`` without a restart, mirroring
        the TUI's steady poll. Cheap and fail-open: any problem disables the pet.
        """
        try:
            from agent.pet import constants, store
            from agent.pet.render import PetRenderer
            from openagents_cli.config import load_config

            cfg = load_config()
            display = cfg.get("display", {}) if isinstance(cfg.get("display"), dict) else {}
            pet_cfg = display.get("pet", {}) if isinstance(display.get("pet"), dict) else {}

            enabled = bool(pet_cfg.get("enabled"))
            slug = str(pet_cfg.get("slug", "") or "")
            scale = float(pet_cfg.get("scale", constants.DEFAULT_SCALE) or constants.DEFAULT_SCALE)
            cols = constants.resolve_cols(scale, pet_cfg.get("unicode_cols", 0))

            if not enabled:
                with self._pet_lock:
                    self._pet_enabled = False
                    self._pet_renderer = None
                    self._pet_frames_cache.clear()
                return

            pet = store.resolve_active_pet(slug)
            if pet is None or not pet.exists:
                with self._pet_lock:
                    self._pet_enabled = False
                    self._pet_renderer = None
                    self._pet_frames_cache.clear()
                return

            with self._pet_lock:
                # Rebuild only when the resolved pet or geometry changes.
                if (
                    self._pet_renderer is None
                    or self._pet_slug != pet.slug
                    or self._pet_cols != cols
                    or self._pet_scale != scale
                ):
                    self._pet_renderer = PetRenderer(
                        str(pet.spritesheet), mode="unicode", scale=scale, unicode_cols=cols
                    )
                    self._pet_slug = pet.slug
                    self._pet_cols = cols
                    self._pet_scale = scale
                    self._pet_frames_cache.clear()
                    self._pet_frame_idx = 0
                self._pet_enabled = True
        except Exception:
            with self._pet_lock:
                self._pet_enabled = False
                self._pet_renderer = None

    def _pet_flash(self, state: str, secs: float = 1.6) -> None:
        """Briefly force a transient reaction (wave/jump/failed) before resting."""
        self._pet_event = state
        self._pet_event_until = time.monotonic() + secs

    def _pet_react_turn_end(self) -> None:
        """Flash the end-of-turn beat: failed on error, jump on a finished plan, else wave."""
        if not self._pet_enabled:
            return
        from agent.pet.state import todos_all_done

        if self._pet_turn_error:
            self._pet_flash("failed")
            return
        try:
            store = getattr(self.agent, "_todo_store", None)
            done = todos_all_done(store.read()) if store else False
        except Exception:
            done = False
        self._pet_flash("jump" if done else "wave")

    def _derive_pet_state(self) -> str:
        """Map current CLI activity to a pet animation state.

        A transient reaction beat (wave/jump/failed) wins while it's live;
        otherwise the steady state comes from the shared
        :func:`agent.pet.state.derive_pet_state` so the CLI can't drift from the
        TUI/desktop priority order.
        """
        if self._pet_event and time.monotonic() < self._pet_event_until:
            return self._pet_event
        self._pet_event = ""
        from agent.pet.state import derive_pet_state

        # A live blocking modal (approval / clarify / sudo / secret / slash
        # confirm) means the agent is paused on the user → the `waiting` pose,
        # which outranks the in-flight signals in derive_pet_state.
        awaiting_input = bool(
            self._approval_state
            or self._clarify_state
            or self._sudo_state
            or self._secret_state
            or getattr(self, "_slash_confirm_state", None)
        )

        return derive_pet_state(
            awaiting_input=awaiting_input,
            busy=getattr(self, "_agent_running", False),
            reasoning=self._pet_reasoning,
        ).value

    def _pet_frames_for(self, state: str) -> list:
        """Return (and cache) the half-block grids for one state."""
        cached = self._pet_frames_cache.get(state)
        if cached is not None:
            return cached
        renderer = self._pet_renderer
        if renderer is None:
            return []
        try:
            count = renderer.frame_count(state) or 1
            grids = [renderer.cells(state, i, cols=self._pet_cols) for i in range(count)]
        except Exception:
            grids = []
        self._pet_frames_cache[state] = grids
        return grids

    def _pet_fragments(self):
        """Return prompt_toolkit FormattedText for the current pet frame, or []."""
        with self._pet_lock:
            if not self._pet_enabled or self._pet_renderer is None:
                return []
            state = self._derive_pet_state()
            grids = self._pet_frames_for(state)
            if not grids:
                return []
            grid = grids[self._pet_frame_idx % len(grids)]

        frags = []
        for y, row in enumerate(grid):
            if y:
                frags.append(("", "\n"))
            for top, bottom in row:
                tr, tg, tb, ta = top
                br, bg, bb, ba = bottom
                top_op = ta >= 32
                bot_op = ba >= 32
                if not top_op and not bot_op:
                    frags.append(("", " "))
                elif top_op and bot_op:
                    frags.append((f"fg:#{tr:02x}{tg:02x}{tb:02x} bg:#{br:02x}{bg:02x}{bb:02x}", "▀"))
                elif top_op:
                    # Upper half only — leave the lower half the terminal's bg
                    # instead of painting it black (cleaner on light themes).
                    frags.append((f"fg:#{tr:02x}{tg:02x}{tb:02x}", "▀"))
                else:
                    frags.append((f"fg:#{br:02x}{bg:02x}{bb:02x}", "▄"))
        return frags

    def _pet_widget_height(self) -> int:
        """Visible rows for the pet window — 0 collapses it when no pet shows."""
        with self._pet_lock:
            if not self._pet_enabled or self._pet_renderer is None:
                return 0
            grids = self._pet_frames_for(self._derive_pet_state())
            if not grids or not grids[0]:
                return 0
            return len(grids[0])

    def _pet_anim_loop(self) -> None:
        """Advance the frame + invalidate on a timer while a pet is enabled."""
        while self._pet_anim_running:
            time.sleep(self._PET_FRAME_INTERVAL)
            now = time.monotonic()
            if now - self._pet_cfg_checked >= self._PET_CFG_INTERVAL:
                self._pet_cfg_checked = now
                self._pet_resolve_config()
            if not self._pet_enabled:
                continue
            with self._pet_lock:
                self._pet_frame_idx += 1
            app = getattr(self, "_app", None)
            if app is not None:
                try:
                    app.invalidate()
                except Exception:
                    pass

    def _pet_start_anim(self) -> None:
        if self._pet_anim_running:
            return
        self._pet_resolve_config()
        self._pet_anim_running = True
        self._pet_anim_thread = threading.Thread(target=self._pet_anim_loop, daemon=True)
        self._pet_anim_thread.start()

    def _pet_stop_anim(self) -> None:
        self._pet_anim_running = False
        thread = self._pet_anim_thread
        if thread is not None:
            thread.join(timeout=0.3)
        self._pet_anim_thread = None

    def _voice_record_key_label(self) -> str:
        """Return the configured voice push-to-talk key formatted for UI.

        Shared helper so every voice-facing status line / placeholder /
        recording hint advertises the SAME label as the registered
        prompt_toolkit binding.

        Cached at startup (see ``set_voice_record_key_cache``) rather
        than re-read per render. Two reasons (Copilot round-13 on
        #19835):

        * The prompt_toolkit binding is registered once at session
          start via ``@kb.add(_voice_key)``; re-reading config per
          render meant the status bar could advertise a new shortcut
          after a config edit while the actual binding was still the
          startup chord — exactly the display/binding drift this PR
          is trying to eliminate.
        * The label is on the hot render path (status bar + composer
          placeholder invalidated every 150ms during recording), so
          reading config on every call added avoidable UI overhead.
        """
        return getattr(self, "_voice_record_key_display_cache", None) or "Ctrl+B"

    def set_voice_record_key_cache(self, raw_key: object) -> None:
        """Populate the voice label cache from a raw ``voice.record_key``.

        Called at CLI startup after the prompt_toolkit binding is
        registered so the cached label always matches the live binding.
        """
        try:
            from openagents_cli.voice import format_voice_record_key_for_status
            self._voice_record_key_display_cache = format_voice_record_key_for_status(raw_key)
        except Exception:
            self._voice_record_key_display_cache = "Ctrl+B"

    def _get_voice_status_fragments(self, width: Optional[int] = None):
        """Return the voice status bar fragments for the interactive TUI."""
        width = width or self._get_tui_terminal_width()
        compact = self._use_minimal_tui_chrome(width=width)
        label = self._voice_record_key_label()
        if self._voice_recording:
            if compact:
                return [("class:voice-status-recording", " ● REC ")]
            return [("class:voice-status-recording", f" ● REC  {label} to stop ")]
        if self._voice_processing:
            if compact:
                return [("class:voice-status", " ◉ STT ")]
            return [("class:voice-status", " ◉ Transcribing... ")]
        if compact:
            return [("class:voice-status", f" 🎤 {label} ")]
        tts = " | TTS on" if self._voice_tts else ""
        cont = " | Continuous" if self._voice_continuous else ""
        return [("class:voice-status", f" 🎤 Voice mode{tts}{cont}  —  {label} to record ")]

    def _build_status_bar_text(self, width: Optional[int] = None) -> str:
        """Return a compact one-line session status string for the TUI footer."""
        try:
            snapshot = self._get_status_bar_snapshot()
            if width is None:
                width = self._get_tui_terminal_width()
            percent = snapshot["context_percent"]
            percent_label = f"{percent}%" if percent is not None else "--"
            duration_label = snapshot["duration"]

            yolo_active = self._is_session_yolo_active()
            if width < 52:
                text = f"⚕ {snapshot['model_short']} · {duration_label}"
                if yolo_active:
                    text += " · ⚠ YOLO"
                return self._trim_status_bar_text(text, width)
            if width < 76:
                parts = [f"⚕ {snapshot['model_short']}", percent_label]
                compressions = snapshot.get("compressions", 0)
                if compressions:
                    parts.append(f"🗜️ {compressions}")
                bg_count = snapshot.get("active_background_tasks", 0)
                if bg_count:
                    parts.append(f"▶ {bg_count}")
                bg_proc_count = snapshot.get("active_background_processes", 0)
                if bg_proc_count:
                    parts.append(f"⚙ {bg_proc_count}")
                bg_subagent_count = snapshot.get("active_background_subagents", 0)
                if bg_subagent_count:
                    parts.append(f"⛓ {bg_subagent_count}")
                parts.append(duration_label)
                if yolo_active:
                    parts.append("⚠ YOLO")
                return self._trim_status_bar_text(" · ".join(parts), width)

            if snapshot["context_length"]:
                ctx_total = _format_context_length(snapshot["context_length"])
                ctx_used = format_token_count_compact(snapshot["context_tokens"])
                context_label = f"{ctx_used}/{ctx_total}"
            else:
                context_label = "ctx --"

            compressions = snapshot.get("compressions", 0)
            parts = [f"⚕ {snapshot['model_short']}", context_label, percent_label]
            if compressions:
                parts.append(f"🗜️ {compressions}")
            bg_count = snapshot.get("active_background_tasks", 0)
            if bg_count:
                parts.append(f"▶ {bg_count}")
            bg_proc_count = snapshot.get("active_background_processes", 0)
            if bg_proc_count:
                parts.append(f"⚙ {bg_proc_count}")
            bg_subagent_count = snapshot.get("active_background_subagents", 0)
            if bg_subagent_count:
                parts.append(f"⛓ {bg_subagent_count}")
            parts.append(duration_label)
            prompt_elapsed = snapshot.get("prompt_elapsed")
            if prompt_elapsed:
                parts.append(prompt_elapsed)
            idle_since = snapshot.get("idle_since")
            if idle_since:
                parts.append(idle_since)
            if yolo_active:
                parts.append("⚠ YOLO")
            return self._trim_status_bar_text(" │ ".join(parts), width)
        except Exception:
            return f"⚕ {self.model if getattr(self, 'model', None) else 'Hermes'}"

    def _get_status_bar_fragments(self):
        if not self._status_bar_visible or getattr(self, '_model_picker_state', None):
            return []
        try:
            snapshot = self._get_status_bar_snapshot()
            # Use prompt_toolkit's own terminal width when running inside the
            # TUI — shutil.get_terminal_size() can return stale or fallback
            # values (especially on SSH) that differ from what prompt_toolkit
            # actually renders, causing the fragments to overflow to a second
            # line and produce duplicated status bar rows over long sessions.
            width = self._get_tui_terminal_width()
            duration_label = snapshot["duration"]
            yolo_active = self._is_session_yolo_active()

            if width < 52:
                frags = [
                    ("class:status-bar", " ⚕ "),
                    ("class:status-bar-strong", snapshot["model_short"]),
                    ("class:status-bar-dim", " · "),
                    ("class:status-bar-dim", duration_label),
                ]
                if yolo_active:
                    frags.append(("class:status-bar-dim", " · "))
                    frags.append(("class:status-bar-yolo", "⚠ YOLO"))
                frags.append(("class:status-bar", " "))
            else:
                percent = snapshot["context_percent"]
                percent_label = f"{percent}%" if percent is not None else "--"
                if width < 76:
                    compressions = snapshot.get("compressions", 0)
                    bg_count = snapshot.get("active_background_tasks", 0)
                    bg_proc_count = snapshot.get("active_background_processes", 0)
                    bg_subagent_count = snapshot.get("active_background_subagents", 0)
                    frags = [
                        ("class:status-bar", " ⚕ "),
                        ("class:status-bar-strong", snapshot["model_short"]),
                        ("class:status-bar-dim", " · "),
                        (self._status_bar_context_style(percent), percent_label),
                    ]
                    if compressions:
                        frags.append(("class:status-bar-dim", " · "))
                        frags.append((self._compression_count_style(compressions), f"🗜️ {compressions}"))
                    if bg_count:
                        frags.append(("class:status-bar-dim", " · "))
                        frags.append(("class:status-bar-strong", f"▶ {bg_count}"))
                    if bg_proc_count:
                        frags.append(("class:status-bar-dim", " · "))
                        frags.append(("class:status-bar-strong", f"⚙ {bg_proc_count}"))
                    if bg_subagent_count:
                        frags.append(("class:status-bar-dim", " · "))
                        frags.append(("class:status-bar-strong", f"⛓ {bg_subagent_count}"))
                    frags.extend([
                        ("class:status-bar-dim", " · "),
                        ("class:status-bar-dim", duration_label),
                    ])
                    if yolo_active:
                        frags.append(("class:status-bar-dim", " · "))
                        frags.append(("class:status-bar-yolo", "⚠ YOLO"))
                    frags.append(("class:status-bar", " "))
                else:
                    if snapshot["context_length"]:
                        ctx_total = _format_context_length(snapshot["context_length"])
                        ctx_used = format_token_count_compact(snapshot["context_tokens"])
                        context_label = f"{ctx_used}/{ctx_total}"
                    else:
                        context_label = "ctx --"

                    bar_style = self._status_bar_context_style(percent)
                    compressions = snapshot.get("compressions", 0)
                    bg_count = snapshot.get("active_background_tasks", 0)
                    bg_proc_count = snapshot.get("active_background_processes", 0)
                    bg_subagent_count = snapshot.get("active_background_subagents", 0)
                    frags = [
                        ("class:status-bar", " ⚕ "),
                        ("class:status-bar-strong", snapshot["model_short"]),
                        ("class:status-bar-dim", " │ "),
                        ("class:status-bar-dim", context_label),
                        ("class:status-bar-dim", " │ "),
                        (bar_style, self._build_context_bar(percent)),
                        ("class:status-bar-dim", " "),
                        (bar_style, percent_label),
                    ]
                    if compressions:
                        frags.append(("class:status-bar-dim", " │ "))
                        frags.append((self._compression_count_style(compressions), f"🗜️ {compressions}"))
                    if bg_count:
                        frags.append(("class:status-bar-dim", " │ "))
                        frags.append(("class:status-bar-strong", f"▶ {bg_count}"))
                    if bg_proc_count:
                        frags.append(("class:status-bar-dim", " │ "))
                        frags.append(("class:status-bar-strong", f"⚙ {bg_proc_count}"))
                    if bg_subagent_count:
                        frags.append(("class:status-bar-dim", " │ "))
                        frags.append(("class:status-bar-strong", f"⛓ {bg_subagent_count}"))
                    frags.extend([
                        ("class:status-bar-dim", " │ "),
                        ("class:status-bar-dim", duration_label),
                    ])
                    # Position 7: per-prompt elapsed timer (live or frozen)
                    prompt_elapsed = snapshot.get("prompt_elapsed")
                    if prompt_elapsed:
                        frags.append(("class:status-bar-dim", " │ "))
                        frags.append(("class:status-bar-dim", prompt_elapsed))
                    # Position 8: idle time since the last final agent response
                    idle_since = snapshot.get("idle_since")
                    if idle_since:
                        frags.append(("class:status-bar-dim", " │ "))
                        frags.append(("class:status-bar-dim", idle_since))
                    if yolo_active:
                        frags.append(("class:status-bar-dim", " │ "))
                        frags.append(("class:status-bar-yolo", "⚠ YOLO"))
                    frags.append(("class:status-bar", " "))

            total_width = sum(self._status_bar_display_width(text) for _, text in frags)
            if total_width > width:
                plain_text = "".join(text for _, text in frags)
                trimmed = self._trim_status_bar_text(plain_text, width)
                return [("class:status-bar", trimmed)]
            return frags
        except Exception:
            return [("class:status-bar", f" {self._build_status_bar_text()} ")]

    def _normalize_model_for_provider(self, resolved_provider: str) -> bool:
        """Normalize provider-specific model IDs and routing."""
        current_model = (self.model or "").strip()
        changed = False

        try:
            from openagents_cli.model_normalize import (
                _AGGREGATOR_PROVIDERS,
                normalize_model_for_provider,
            )

            if resolved_provider not in _AGGREGATOR_PROVIDERS:
                normalized_model = normalize_model_for_provider(current_model, resolved_provider)
                if normalized_model and normalized_model != current_model:
                    if not self._model_is_default:
                        self._console_print(
                            f"[yellow]⚠️  Normalized model '{current_model}' to '{normalized_model}' for {resolved_provider}.[/]"
                        )
                    self.model = normalized_model
                    current_model = normalized_model
                    changed = True
        except Exception:
            pass

        if resolved_provider == "copilot":
            try:
                from openagents_cli.models import copilot_model_api_mode, normalize_copilot_model_id

                canonical = normalize_copilot_model_id(current_model, api_key=self.api_key)
                if canonical and canonical != current_model:
                    if not self._model_is_default:
                        self._console_print(
                            f"[yellow]⚠️  Normalized Copilot model '{current_model}' to '{canonical}'.[/]"
                        )
                    self.model = canonical
                    current_model = canonical
                    changed = True

                resolved_mode = copilot_model_api_mode(current_model, api_key=self.api_key)
                if resolved_mode != self.api_mode:
                    self.api_mode = resolved_mode
                    changed = True
            except Exception:
                pass
            return changed

        if resolved_provider in {"opencode-zen", "opencode-go"}:
            try:
                from openagents_cli.models import normalize_opencode_model_id, opencode_model_api_mode

                canonical = normalize_opencode_model_id(resolved_provider, current_model)
                if canonical and canonical != current_model:
                    if not self._model_is_default:
                        self._console_print(
                            f"[yellow]⚠️  Stripped provider prefix from '{current_model}'; using '{canonical}' for {resolved_provider}.[/]"
                        )
                    self.model = canonical
                    current_model = canonical
                    changed = True

                resolved_mode = opencode_model_api_mode(resolved_provider, current_model)
                if resolved_mode != self.api_mode:
                    self.api_mode = resolved_mode
                    changed = True
            except Exception:
                pass
            return changed

        if resolved_provider != "openai-codex":
            return changed

        # 1. Strip provider prefix ("openai/gpt-5.4" → "gpt-5.4")
        if "/" in current_model:
            slug = current_model.split("/", 1)[1]
            if not self._model_is_default:
                self._console_print(
                    f"[yellow]⚠️  Stripped provider prefix from '{current_model}'; "
                    f"using '{slug}' for OpenAI Codex.[/]"
                )
            self.model = slug
            current_model = slug
            changed = True

        # 2. Replace untouched default with a Codex model
        if self._model_is_default:
            fallback_model = "gpt-5.3-codex"
            try:
                from openagents_cli.codex_models import get_codex_model_ids

                available = get_codex_model_ids(
                    access_token=self.api_key if self.api_key else None,
                )
                if available:
                    fallback_model = available[0]
            except Exception:
                pass

            if current_model != fallback_model:
                self.model = fallback_model
                changed = True

        return changed

    def _on_thinking(self, text: str) -> None:
        """Called by agent when thinking starts/stops. Updates TUI spinner."""
        if not text:
            self._flush_reasoning_preview(force=True)
        self._spinner_text = text or ""
        self._tool_start_time = 0.0  # clear tool timer when switching to thinking
        self._invalidate()

    def _on_notice(self, notice) -> None:
        """Queue an out-of-band AgentNotice for rendering at the next clean boundary.

        Notices fire from inside the agent turn (cold-start seed during _init_agent,
        per-turn _capture_credits after the API call) — printing immediately races the
        streaming response and the line gets buried behind the prompt (see _cprint's
        bg-thread caveat). So we QUEUE here and flush in _flush_credit_notices(), called
        right after run_conversation returns. Fail-soft: never break the turn.
        """
        try:
            text = getattr(notice, "text", "") or ""
            if not text:
                return
            level = getattr(notice, "level", "info") or "info"
            if not hasattr(self, "_pending_credit_notices"):
                self._pending_credit_notices = []
            self._pending_credit_notices.append((level, text))
        except Exception:
            pass

    def _flush_credit_notices(self) -> None:
        """Print any queued credit notices as level-colored lines. Called at turn end
        (after run_conversation) where _cprint paints cleanly above the prompt."""
        try:
            pending = getattr(self, "_pending_credit_notices", None)
            if not pending:
                return
            self._pending_credit_notices = []
            for level, text in pending:
                color = {
                    "error": "\033[31m",
                    "warn": "\033[33m",
                    "success": "\033[32m",
                    "info": _DIM,
                }.get(level, _DIM)
                _cprint(f"  {color}{text}{_RST}")
        except Exception:
            pass

    def _on_notice_clear(self, key: str) -> None:
        """Notice cleared. The REPL prints lines (no persistent slot to wipe), so
        this drops any still-queued notice with that key is not tracked by key here;
        it's a no-op for rendering — kept so the agent's clear callback is bound
        symmetrically with the show callback (and so future REPL UIs can hook it)."""
        return

    # ── Streaming display ────────────────────────────────────────────────

    def _current_reasoning_callback(self):
        """Return the active reasoning display callback for the current mode."""
        if self.show_reasoning and self.streaming_enabled:
            return self._stream_reasoning_delta
        if self.verbose and not self.show_reasoning:
            return self._on_reasoning
        return None

    def _emit_reasoning_preview(self, reasoning_text: str) -> None:
        """Render a buffered reasoning preview as a single [thinking] block."""
        preview_text = reasoning_text.strip()
        if not preview_text:
            return

        try:
            term_width = shutil.get_terminal_size().columns
        except Exception:
            term_width = 80
        prefix = "  [thinking] "
        wrap_width = max(30, term_width - len(prefix) - 2)

        paragraphs = []
        raw_paragraphs = re.split(r"\n\s*\n+", preview_text.replace("\r\n", "\n"))
        for paragraph in raw_paragraphs:
            compact = " ".join(line.strip() for line in paragraph.splitlines() if line.strip())
            if compact:
                paragraphs.append(textwrap.fill(compact, width=wrap_width))
        preview_text = "\n".join(paragraphs)
        if not preview_text:
            return

        if self.verbose:
            _cprint(f"  {_DIM}[thinking] {preview_text}{_RST}")
            return

        lines = preview_text.splitlines()
        if len(lines) > 5:
            preview = "\n".join(lines[:5])
            preview += f"\n  ... ({len(lines) - 5} more lines)"
        else:
            preview = preview_text
        _cprint(f"  {_DIM}[thinking] {preview}{_RST}")

    def _flush_reasoning_preview(self, *, force: bool = False) -> None:
        """Flush buffered reasoning text at natural boundaries.

        Some providers stream reasoning in tiny word or punctuation chunks.
        Buffer them here so the preview path does not print one `[thinking]`
        line per token.
        """
        buf = getattr(self, "_reasoning_preview_buf", "")
        if not buf:
            return

        try:
            term_width = shutil.get_terminal_size().columns
        except Exception:
            term_width = 80
        target_width = max(40, term_width - len("  [thinking] ") - 4)

        flush_text = ""

        if force:
            flush_text = buf
            buf = ""
        else:
            line_break = buf.rfind("\n")
            min_newline_flush = max(16, target_width // 3)
            if line_break != -1 and (
                line_break >= min_newline_flush
                or buf.endswith("\n\n")
                or buf.endswith(".\n")
                or buf.endswith("!\n")
                or buf.endswith("?\n")
                or buf.endswith(":\n")
            ):
                flush_text = buf[: line_break + 1]
                buf = buf[line_break + 1 :]
            elif len(buf) >= target_width:
                search_start = max(20, target_width // 2)
                search_end = min(len(buf), max(target_width + (target_width // 3), target_width + 8))
                cut = -1
                for boundary in (" ", "\t", ".", "!", "?", ",", ";", ":"):
                    cut = max(cut, buf.rfind(boundary, search_start, search_end))
                if cut != -1:
                    flush_text = buf[: cut + 1]
                    buf = buf[cut + 1 :]

        self._reasoning_preview_buf = buf.lstrip() if flush_text else buf
        if flush_text:
            self._emit_reasoning_preview(flush_text)

    def _format_submitted_user_message_preview(self, user_input: str) -> str:
        """Format the submitted user-message scrollback preview."""
        ts_suffix = (
            f" [dim]{datetime.now().strftime('%H:%M')}[/]"
            if getattr(self, "show_timestamps", False) else ""
        )
        lines = user_input.split("\n")
        if len(lines) <= 1:
            return f"[bold {_accent_hex()}]●[/] [bold]{_escape(user_input)}[/]{ts_suffix}"

        first_lines = int(getattr(self, "user_message_preview_first_lines", 2))
        last_lines = int(getattr(self, "user_message_preview_last_lines", 2))
        first_lines = max(1, first_lines)
        last_lines = max(0, last_lines)
        head = lines[:first_lines]
        remaining_after_head = max(0, len(lines) - len(head))
        tail_count = min(last_lines, remaining_after_head)
        tail = lines[-tail_count:] if tail_count else []

        hidden_middle_count = len(lines) - len(head) - len(tail)
        if hidden_middle_count < 0:
            hidden_middle_count = 0
            tail = []

        preview_lines = [
            f"[bold {_accent_hex()}]●[/] [bold]{_escape(head[0])}[/]{ts_suffix}"
        ]
        preview_lines.extend(f"[bold]{_escape(line)}[/]" for line in head[1:])

        if hidden_middle_count > 0:
            noun = "line" if hidden_middle_count == 1 else "lines"
            preview_lines.append(f"[dim]... (+{hidden_middle_count} more {noun})[/]")

        preview_lines.extend(f"[bold]{_escape(line)}[/]" for line in tail)
        return "\n".join(preview_lines)

    def _expand_paste_references(self, text: str | None) -> str:
        """Expand [Pasted text #N -> file] placeholders into file contents."""
        if not isinstance(text, str) or "[Pasted text #" not in text:
            return text or ""
        paste_ref_re = re.compile(r'\[Pasted text #\d+: \d+ lines \u2192 (.+?)\]')

        def _expand_ref(match):
            path = Path(match.group(1))
            # Use try/except instead of path.exists() to avoid TOCTOU race:
            # the paste file may be deleted between check and read, causing
            # the input to be silently dropped (#17666).
            try:
                return path.read_text(encoding="utf-8")
            except (OSError, IOError):
                logger.warning("Paste file gone or unreadable, returning placeholder: %s", path)
                return match.group(0)

        return paste_ref_re.sub(_expand_ref, text)

    def _print_user_message_preview(self, user_input: str) -> None:
        """Render a user message using the normal chat scrollback style."""
        ChatConsole().print(f"[{_accent_hex()}]{'─' * 40}[/]")
        text = str(user_input or "")
        if "\n" in text:
            ChatConsole().print(self._format_submitted_user_message_preview(text))
        else:
            ChatConsole().print(f"[bold {_accent_hex()}]●[/] [bold]{_escape(text)}[/]")

    def _stream_reasoning_delta(self, text: str) -> None:
        """Stream reasoning/thinking tokens into a dim box above the response.

        Opens a dim reasoning box on first token, streams line-by-line.
        The box is closed automatically when content tokens start arriving
        (via _stream_delta → _emit_stream_text).

        Once the response box is open, suppress any further reasoning
        rendering — a late thinking block (e.g. after an interrupt) would
        otherwise draw a reasoning box inside the response box.
        """
        if not text:
            return
        self._reasoning_shown_this_turn = True
        if getattr(self, "_stream_box_opened", False):
            return

        # Open reasoning box on first reasoning token
        if not getattr(self, "_reasoning_box_opened", False):
            self._reasoning_box_opened = True
            w = self._scrollback_box_width()
            r_label = " Reasoning "
            r_fill = w - 2 - len(r_label)
            _cprint(f"\n{_DIM}┌─{r_label}{'─' * max(r_fill - 1, 0)}┐{_RST}")

        self._reasoning_buf = getattr(self, "_reasoning_buf", "") + text

        # Emit complete lines, and force-flush long partial lines so
        # reasoning is visible in real-time even without newlines.
        while "\n" in self._reasoning_buf:
            line, self._reasoning_buf = self._reasoning_buf.split("\n", 1)
            _cprint(f"{_DIM}{line}{_RST}")
        if len(self._reasoning_buf) > 80:
            _cprint(f"{_DIM}{self._reasoning_buf}{_RST}")
            self._reasoning_buf = ""

    def _close_reasoning_box(self) -> None:
        """Close the live reasoning box if it's open."""
        if getattr(self, "_reasoning_box_opened", False):
            # Flush remaining reasoning buffer
            buf = getattr(self, "_reasoning_buf", "")
            if buf:
                _cprint(f"{_DIM}{buf}{_RST}")
                self._reasoning_buf = ""
            w = self._scrollback_box_width()
            _cprint(f"{_DIM}└{'─' * (w - 2)}┘{_RST}")
            self._reasoning_box_opened = False

            # Flush any content that was deferred while reasoning was rendering.
            deferred = getattr(self, "_deferred_content", "")
            if deferred:
                self._deferred_content = ""
                self._emit_stream_text(deferred)

    def _stream_delta(self, text) -> None:
        """Line-buffered streaming callback for real-time token rendering.

        Receives text deltas from the agent as tokens arrive. Buffers
        partial lines and emits complete lines via _cprint to work
        reliably with prompt_toolkit's patch_stdout.

        Reasoning/thinking blocks (<REASONING_SCRATCHPAD>, <think>, etc.)
        are suppressed during streaming since they'd display raw XML tags.
        The agent strips them from the final response anyway.

        A ``None`` value signals an intermediate turn boundary (tools are
        about to execute).  Flushes any open boxes and resets state so
        tool feed lines render cleanly between turns.
        """
        if text is None:
            self._flush_stream()
            self._reset_stream_state()
            return
        if not text:
            return

        self._stream_started = True

        # ── Tag-based reasoning suppression ──
        # Track whether we're inside a reasoning/thinking block.
        # These tags are model-generated (system prompt tells the model
        # to use them) and get stripped from final_response. We must
        # suppress them during streaming too — unless show_reasoning is
        # enabled, in which case we route the inner content to the
        # reasoning display box instead of discarding it.
        _OPEN_TAGS = ("<REASONING_SCRATCHPAD>", "<think>", "<reasoning>", "<THINKING>", "<thinking>", "<thought>")
        _CLOSE_TAGS = ("</REASONING_SCRATCHPAD>", "</think>", "</reasoning>", "</THINKING>", "</thinking>", "</thought>")

        # Append to a pre-filter buffer first
        self._stream_prefilt = getattr(self, "_stream_prefilt", "") + text

        # Check if we're entering a reasoning block.
        # Only match tags that appear at a "block boundary": start of the
        # stream, after a newline (with optional whitespace), or when nothing
        # but whitespace has been emitted on the current line.
        # This prevents false positives when models *mention* tags in prose
        # like "(/think not producing <think> tags)".
        #
        # _stream_last_was_newline tracks whether the last character emitted
        # (or the start of the stream) is a line boundary.  It's True at
        # stream start and set True whenever emitted text ends with '\n'.
        if not hasattr(self, "_stream_last_was_newline"):
            self._stream_last_was_newline = True  # start of stream = boundary

        if not getattr(self, "_in_reasoning_block", False):
            for tag in _OPEN_TAGS:
                search_start = 0
                while True:
                    idx = self._stream_prefilt.find(tag, search_start)
                    if idx == -1:
                        break
                    # Check if this is a block boundary position
                    preceding = self._stream_prefilt[:idx]
                    if idx == 0:
                        # At buffer start — only a boundary if we're at
                        # a line start (stream start or last emit ended
                        # with newline)
                        is_block_boundary = getattr(self, "_stream_last_was_newline", True)
                    else:
                        # Find last newline in the buffer before the tag
                        last_nl = preceding.rfind("\n")
                        if last_nl == -1:
                            # No newline in buffer — boundary only if
                            # last emit was a newline AND only whitespace
                            # has accumulated before the tag
                            is_block_boundary = (
                                getattr(self, "_stream_last_was_newline", True)
                                and preceding.strip() == ""
                            )
                        else:
                            # Text between last newline and tag must be
                            # whitespace-only
                            is_block_boundary = preceding[last_nl + 1:].strip() == ""
                    if is_block_boundary:
                        # Emit everything before the tag
                        if preceding:
                            self._emit_stream_text(preceding)
                            self._stream_last_was_newline = preceding.endswith("\n")
                        self._in_reasoning_block = True
                        self._stream_prefilt = self._stream_prefilt[idx + len(tag):]
                        break
                    # Not a block boundary — keep searching after this occurrence
                    search_start = idx + 1
                if getattr(self, "_in_reasoning_block", False):
                    break

            # Could also be a partial open tag at the end — hold it back
            if not getattr(self, "_in_reasoning_block", False):
                # Check for partial tag match at the end
                safe = self._stream_prefilt
                for tag in _OPEN_TAGS:
                    for i in range(1, len(tag)):
                        if self._stream_prefilt.endswith(tag[:i]):
                            safe = self._stream_prefilt[:-i]
                            break
                if safe:
                    self._emit_stream_text(safe)
                    self._stream_last_was_newline = safe.endswith("\n")
                    self._stream_prefilt = self._stream_prefilt[len(safe):]
                return

        # Inside a reasoning block — look for close tag.
        # Keep accumulating _stream_prefilt because close tags can arrive
        # split across multiple tokens (e.g. "</REASONING_SCRATCH" + "PAD>...").
        if getattr(self, "_in_reasoning_block", False):
            for tag in _CLOSE_TAGS:
                idx = self._stream_prefilt.find(tag)
                if idx != -1:
                    self._in_reasoning_block = False
                    # When show_reasoning is on, route inner content to
                    # the reasoning display box instead of discarding.
                    if self.show_reasoning:
                        inner = self._stream_prefilt[:idx]
                        if inner:
                            self._stream_reasoning_delta(inner)
                    after = self._stream_prefilt[idx + len(tag):]
                    self._stream_prefilt = ""
                    # Process remaining text after close tag through full
                    # filtering (it could contain another open tag)
                    if after:
                        self._stream_delta(after)
                    return
            # When show_reasoning is on, stream reasoning content live
            # instead of silently accumulating. Keep only the tail that
            # could be a partial close tag prefix.
            max_tag_len = max(len(t) for t in _CLOSE_TAGS)
            if len(self._stream_prefilt) > max_tag_len:
                if self.show_reasoning:
                    # Route the safe prefix to reasoning display
                    safe_reasoning = self._stream_prefilt[:-max_tag_len]
                    self._stream_reasoning_delta(safe_reasoning)
                self._stream_prefilt = self._stream_prefilt[-max_tag_len:]
            return

    def _emit_stream_text(self, text: str) -> None:
        """Emit filtered text to the streaming display."""
        if not text:
            return

        # When show_reasoning is on and reasoning is still rendering,
        # defer content until the reasoning box closes.  This ensures the
        # reasoning block always appears BEFORE the response in the terminal.
        if self.show_reasoning and getattr(self, "_reasoning_box_opened", False):
            self._deferred_content = getattr(self, "_deferred_content", "") + text
            return

        # Close the live reasoning box before opening the response box
        self._close_reasoning_box()

        # Open the response box header on the very first visible text
        if not self._stream_box_opened:
            # Strip leading whitespace/newlines before first visible content
            text = text.lstrip("\n")
            if not text:
                return
            self._stream_box_opened = True
            try:
                from openagents_cli.skin_engine import get_active_skin
                _skin = get_active_skin()
                label = _skin.get_branding("response_label", "⚕ Hermes")
                _text_hex = _skin.get_color("banner_text", "#FFF8DC")
            except Exception:
                label = "⚕ Hermes"
                _text_hex = "#FFF8DC"
            # Build a true-color ANSI escape for the response text color
            # so streamed content matches the Rich Panel appearance.
            try:
                _r = int(_text_hex[1:3], 16)
                _g = int(_text_hex[3:5], 16)
                _b = int(_text_hex[5:7], 16)
                self._stream_text_ansi = f"\033[38;2;{_r};{_g};{_b}m"
            except (ValueError, IndexError):
                self._stream_text_ansi = ""
            if self.show_timestamps:
                label = f"{label} {datetime.now().strftime('%H:%M')}"
            w = self._scrollback_box_width()
            fill = w - 2 - HermesCLI._status_bar_display_width(label)
            _cprint(f"\n{_ACCENT}╭─{label}{'─' * max(fill - 1, 0)}╮{_RST}")

        self._stream_buf += text

        # Emit complete lines, keep partial remainder in buffer
        _tc = getattr(self, "_stream_text_ansi", "")

        def _emit_one(printed_line: str) -> None:
            _cprint(f"{_STREAM_PAD}{_tc}{printed_line}{_RST}" if _tc else f"{_STREAM_PAD}{printed_line}")

        def _flush_table_buf() -> None:
            buf = self._stream_table_buf
            self._stream_table_buf = []
            self._in_stream_table = False
            if not buf:
                return
            # Strip cell-level markdown (`code`, **bold**, ~~strike~~) FIRST
            # so the realigner pads to the final visible cell width, not
            # the marker-decorated source width.  Otherwise a body row
            # like `` | Bold | `**bold**` | `` lands narrower than its
            # header column once the markers are removed.
            joined = "\n".join(buf)
            if self.final_response_markdown == "strip":
                joined = _strip_markdown_syntax(joined)
            block = realign_markdown_tables(joined, _terminal_width_for_streaming())
            for ln in block.split("\n"):
                _emit_one(ln)

        while "\n" in self._stream_buf:
            line, self._stream_buf = self._stream_buf.split("\n", 1)

            # Hold table-shaped lines in a side-buffer so we can re-pad
            # the whole block once it ends.  Streaming line-by-line, we
            # cannot re-align mid-table without reflowing already-printed
            # rows; the cost is that the user sees the table appear in a
            # single batch when the block closes instead of row-by-row.
            if self._in_stream_table:
                if looks_like_table_row(line) or is_table_divider(line):
                    self._stream_table_buf.append(line)
                    continue
                # Block ended — flush the realigned table, then fall
                # through to print the current (non-table) line.
                _flush_table_buf()
            elif looks_like_table_row(line):
                self._stream_table_buf.append(line)
                self._in_stream_table = True
                continue

            if self.final_response_markdown == "strip":
                line = _strip_markdown_syntax(line)
            _emit_one(line)

    def _flush_stream(self) -> None:
        """Emit any remaining partial line from the stream buffer and close the box."""
        # If we're still inside a "reasoning block" at end-of-stream, it was
        # a false positive — the model mentioned a tag like <think> in prose
        # but never closed it.  Recover the buffered content as regular text.
        if getattr(self, "_in_reasoning_block", False) and getattr(self, "_stream_prefilt", ""):
            self._in_reasoning_block = False
            self._emit_stream_text(self._stream_prefilt)
            self._stream_prefilt = ""

        # Close reasoning box if still open (in case no content tokens arrived)
        self._close_reasoning_box()

        _tc = getattr(self, "_stream_text_ansi", "")

        # If the stream buffer has a trailing partial line that looks like
        # a table row, fold it into the table buffer so the whole block
        # gets re-aligned together.  Otherwise the final row prints raw
        # (with the model's original under-padded spacing) while the rows
        # above it are aligned.
        if (
            self._stream_buf
            and getattr(self, "_in_stream_table", False)
            and (looks_like_table_row(self._stream_buf) or is_table_divider(self._stream_buf))
        ):
            self._stream_table_buf.append(self._stream_buf)
            self._stream_buf = ""

        # Flush any buffered table rows first so their padding is
        # finalised before the stream remainder lands.
        if getattr(self, "_stream_table_buf", None):
            joined = "\n".join(self._stream_table_buf)
            self._stream_table_buf = []
            self._in_stream_table = False
            if self.final_response_markdown == "strip":
                joined = _strip_markdown_syntax(joined)
            block = realign_markdown_tables(joined, _terminal_width_for_streaming())
            for ln in block.split("\n"):
                _cprint(f"{_STREAM_PAD}{_tc}{ln}{_RST}" if _tc else f"{_STREAM_PAD}{ln}")

        if self._stream_buf:
            line = _strip_markdown_syntax(self._stream_buf) if self.final_response_markdown == "strip" else self._stream_buf
            _cprint(f"{_STREAM_PAD}{_tc}{line}{_RST}" if _tc else f"{_STREAM_PAD}{line}")
            self._stream_buf = ""

        # Close the response box
        if self._stream_box_opened:
            w = self._scrollback_box_width()
            _cprint(f"{_ACCENT}╰{'─' * (w - 2)}╯{_RST}")

    def _reset_stream_state(self) -> None:
        """Reset streaming state before each agent invocation."""
        self._stream_buf = ""
        self._stream_started = False
        self._stream_box_opened = False
        self._stream_text_ansi = ""
        self._stream_prefilt = ""
        self._in_reasoning_block = False
        self._stream_last_was_newline = True
        self._reasoning_box_opened = False
        self._reasoning_buf = ""
        self._reasoning_preview_buf = ""
        self._deferred_content = ""
        self._stream_table_buf = []
        self._in_stream_table = False

    def _slow_command_status(self, command: str) -> str:
        """Return a user-facing status message for slower slash commands."""
        cmd_lower = command.lower().strip()
        if cmd_lower.startswith("/skills search"):
            return "Searching skills..."
        if cmd_lower.startswith("/skills browse"):
            return "Loading skills..."
        if cmd_lower.startswith("/skills inspect"):
            return "Inspecting skill..."
        if cmd_lower.startswith("/skills install"):
            return "Installing skill..."
        if cmd_lower.startswith("/skills"):
            return "Processing skills command..."
        if cmd_lower == "/reload-mcp":
            return "Reloading MCP servers..."
        if cmd_lower == "/reload-skills" or cmd_lower == "/reload_skills":
            return "Reloading skills..."
        if cmd_lower.startswith("/browser"):
            return "Configuring browser..."
        return "Processing command..."

    def _command_spinner_frame(self) -> str:
        """Return the current spinner frame for slow slash commands."""
        frame_idx = int(time.monotonic() * 10) % len(_COMMAND_SPINNER_FRAMES)
        return _COMMAND_SPINNER_FRAMES[frame_idx]

    @contextmanager
    def _busy_command(self, status: str):
        """Expose a temporary busy state in the TUI while a slash command runs."""
        self._command_running = True
        self._command_status = status
        self._invalidate(min_interval=0.0)
        try:
            print(f"⏳ {status}")
            yield
        finally:
            self._command_running = False
            self._command_status = ""
            self._invalidate(min_interval=0.0)

    def _open_external_editor(self, buffer=None) -> bool:
        """Open the active input buffer in an external editor."""
        app = getattr(self, "_app", None)
        if not app:
            _cprint(f"{_DIM}External editor is only available inside the interactive CLI.{_RST}")
            return False
        if self._command_running:
            _cprint(f"{_DIM}Wait for the current command to finish before opening the editor.{_RST}")
            return False
        if self._sudo_state or self._secret_state or self._approval_state or getattr(self, "_slash_confirm_state", None) or self._clarify_state:
            _cprint(f"{_DIM}Finish the active prompt before opening the editor.{_RST}")
            return False
        target_buffer = buffer or getattr(app, "current_buffer", None)
        if target_buffer is None:
            _cprint(f"{_DIM}No active input buffer is available for the external editor.{_RST}")
            return False
        try:
            existing_text = getattr(target_buffer, "text", "")
            expanded_text = self._expand_paste_references(existing_text)
            if expanded_text != existing_text and hasattr(target_buffer, "text"):
                self._skip_paste_collapse = True
                target_buffer.text = expanded_text
                if hasattr(target_buffer, "cursor_position"):
                    target_buffer.cursor_position = len(expanded_text)
            # Set skip flag (again) so the text-change event fired when the
            # editor closes does not re-collapse the returned content.
            self._skip_paste_collapse = True
            # Open the editor, then submit the saved draft on a clean exit —
            # matching the TUI's Ctrl+G (openEditor), which sends the buffer
            # instead of requiring a second Enter. Submission in this CLI is
            # driven by the custom `enter` keybinding, NOT the buffer's
            # accept_handler, so validate_and_handle can't route through it;
            # chain a done-callback on the returned Task that re-uses the
            # real submit pipeline via _submit_editor_buffer().
            task = target_buffer.open_in_editor(validate_and_handle=False)
            if task is not None and hasattr(task, "add_done_callback"):
                task.add_done_callback(
                    lambda _t, b=target_buffer: self._submit_editor_buffer(b)
                )
            return True
        except Exception as exc:
            _cprint(f"{_DIM}Failed to open external editor: {exc}{_RST}")
            return False

    def _submit_editor_buffer(self, buffer) -> None:
        """Submit the draft an external editor left in ``buffer``.

        Invoked from the Ctrl+G done-callback so saving the editor sends the
        prompt (TUI parity) instead of leaving it sitting in the input area.
        Mirrors the idle/queue branches of the `enter` keybinding handler:
        an empty save is ignored (never submits a blank turn), a slash command
        is dispatched, otherwise the text is routed through the same input
        queues the normal Enter path uses. Runs on the prompt_toolkit event
        loop via the Task callback, so it must be cheap and non-blocking.
        """
        try:
            text = (getattr(buffer, "text", "") or "").strip()
        except Exception:
            return
        if not text:
            # Editor saved empty / was cleared — match the TUI, which drops
            # an empty draft instead of submitting a blank turn.
            return

        app = getattr(self, "_app", None)

        # Slash commands: dispatch directly, same as the Enter handler's
        # _looks_like_slash_command branch.
        if _looks_like_slash_command(text):
            try:
                if not self.process_command(text):
                    self._should_exit = True
                    if app is not None and app.is_running:
                        app.exit()
            except Exception as exc:
                _cprint(f"  {_DIM}Command failed: {exc}{_RST}")
            finally:
                self._reset_input_buffer(buffer)
                if app is not None:
                    app.invalidate()
            return

        # Regular prompt: route through the same queues the Enter handler uses.
        if self._agent_running:
            # Agent busy → honour the configured busy-input behaviour by
            # queueing for the next turn (the safe default; interrupt/steer
            # remain reachable via the normal Enter path).
            self._interrupt_queue.put(text) if self.busy_input_mode == "interrupt" else self._pending_input.put(text)
            preview = text[:80] + ("..." if len(text) > 80 else "")
            _cprint(f"  Queued for the next turn: {preview}")
        else:
            self._pending_input.put(text)

        self._reset_input_buffer(buffer)
        if app is not None:
            app.invalidate()

    def _reset_input_buffer(self, buffer) -> None:
        """Clear an input buffer after a programmatic submit (best-effort)."""
        try:
            buffer.reset(append_to_history=True)
        except Exception:
            try:
                buffer.text = ""
            except Exception:
                pass



=======
>>>>>>> rb/tag
    def _install_tool_callbacks(self) -> None:
        """Install tool callbacks that need the live prompt UI."""
        if self._tool_callbacks_installed:
            return
        set_sudo_password_callback(self._sudo_password_callback)
        set_approval_callback(self._approval_callback)
        set_secret_capture_callback(self._secret_capture_callback)
        from agent.vault_backends.unlock import set_code_prompt_callback, set_save_login_prompt_callback, set_unlock_prompt_callback
        set_unlock_prompt_callback(self._vault_unlock_callback)
        set_save_login_prompt_callback(self._vault_save_login_callback)
        set_code_prompt_callback(self._vault_code_callback)
        self._tool_callbacks_installed = True

    def _ensure_tirith_security(self) -> None:
<<<<<<< HEAD
        """Resolve tirith when enabled; confirm active scanners once at startup."""
        if getattr(self, "_tirith_security_checked", False):
||||||| cf299e9a01
        """Check tirith availability once before tools can run terminal commands."""
        if getattr(self, "_tirith_security_checked", False):
=======
        """Check tirith availability once before tools can run terminal commands."""
        if self._tirith_security_checked:
>>>>>>> rb/tag
            return
        self._tirith_security_checked = True
        security_cfg = self.config.get("security", {}) or {}
        tirith_enabled = security_cfg.get("tirith_enabled", True)
        builtin_on = security_cfg.get("builtin_command_scanner", True)
        try:
<<<<<<< HEAD
            from tools.builtin_command_security import is_enabled as builtin_enabled
            builtin_on = builtin_on and builtin_enabled()
||||||| cf299e9a01
            from tools.tirith_security import ensure_installed, is_platform_supported

            tirith_path = ensure_installed(log_failures=False)
            if tirith_path is None and is_platform_supported():
                security_cfg = self.config.get("security", {}) or {}
                tirith_enabled = security_cfg.get("tirith_enabled", True)
                if tirith_enabled:
                    _cprint(
                        f"  {_DIM}⚠ tirith security scanner enabled but not available "
                        f"— command scanning will use pattern matching only{_RST}"
                    )
=======
            from tools.tirith_security import ensure_installed, is_platform_supported

            if (
                ensure_installed(log_failures=False) is None and is_platform_supported()
                and (self.config.get("security", {}) or {}).get("tirith_enabled", True)
            ):
                _cprint(
                    f"  {_DIM}⚠ tirith security scanner enabled but not available "
                    f"— command scanning will use pattern matching only{_RST}"
                )
>>>>>>> rb/tag
        except Exception:
            pass

<<<<<<< HEAD
        tirith_path = None
        if tirith_enabled:
            try:
                from tools.tirith_security import ensure_installed

                tirith_path = ensure_installed(log_failures=False)
            except Exception:
                pass

        if not (tirith_path or builtin_on):
            return

        parts = []
        if tirith_path:
            parts.append("Tirith")
        if builtin_on:
            parts.append("built-in")
        if not parts:
            return
        try:
            from openagents_cli.skin_engine import get_active_status_brand

            brand = get_active_status_brand("OpenPro")
            label = brand or "OpenPro"
        except Exception:
            label = "OpenPro"
        scanners = " + ".join(parts)
        self._console_print(f"[dim]{label} · {scanners} scanner active[/]")

    
||||||| cf299e9a01
    
=======
>>>>>>> rb/tag
    def _show_security_advisories(self):
        """Startup banner for unacked security advisories, on stderr (piped stdout stays clean); 24h rate-limited."""
        try:
            from openagents_cli.security_advisories import detect_compromised, startup_banner

            banner = startup_banner(detect_compromised())
            if banner:
                print(banner, file=sys.stderr, flush=True)
        except Exception:
            pass  # never block startup

    def _show_browser_backend_notice(self):
        """Once-per-24h hint when the default Browser Use backend silently fell back to built-in tools."""
        try:
            from tools.browser_use_cli import default_downgrade_notice

            notice = default_downgrade_notice()
            if notice:
                from gateway.warning_notifications import render_notification
                render_notification(lambda: self._console_print(f"[yellow]⚠ {notice}[/yellow]"), platform="cli")
        except Exception:
            logger.debug("browser backend notice failed", exc_info=True)

    def finalize_preloaded_skills(self) -> None:
        """Join the background --skills preload and fold it into the prompt (idempotent).

        Raises ``ValueError`` only when EVERY requested skill was unknown.
        """
        if getattr(self, "_preload_skills_finalized", False):
            return
        thread = getattr(self, "_preload_skills_thread", None)
        if thread is None:
            self._preload_skills_finalized = True
            return
        thread.join(timeout=120)
        self._preload_skills_finalized = True
        err = getattr(self, "_preload_skills_error", None)
        if err is not None:
            raise err
        auto_result = getattr(self, "_auto_load_skills_result", None)
        if auto_result and auto_result[2]:
            logger.warning("skills.auto_load: skill(s) not found or disabled, skipped: %s", ", ".join(auto_result[2]))
        # auto_load names first, then explicit -s names that were not already pinned.
        self.preloaded_skills = list(auto_result[1]) if auto_result else []
        result = getattr(self, "_preload_skills_result", None)
        if not result:
            return
        skills_prompt, loaded_skills, missing_skills = result
        if missing_skills:
            missing_display = ", ".join(missing_skills)
            # A typo'd name must not crash a kanban worker; only a fully-missing set fails loudly.
            if loaded_skills:
                logger.warning(
                    "Unknown skill(s) requested, skipping: %s. "
                    "Continuing with: %s. "
                    "List available skills with `hermes skills list`.",
                    missing_display,
                    ", ".join(loaded_skills),
                )
            else:
                raise ValueError(f"Unknown skill(s): {missing_display}")
        if skills_prompt:
            self.system_prompt = "\n\n".join(p for p in (self.system_prompt, skills_prompt) if p).strip()
        self.preloaded_skills += [name for name in loaded_skills if name not in self.preloaded_skills]

    def _show_tool_availability_warnings(self):
        """Warn about toolsets switched off at startup (missing API keys, unusable terminal backend)."""
        try:
            # Runs on a daemon thread on the snapshot fast path: keep the imports to modules the
            # registry walk already loaded plus the pure notices module (a heavy import here races
            # importlib's module locks against the main thread).
            from model_tools import check_tool_availability
            from openagents_cli.tool_availability_notices import (
                current_terminal_backend, filter_to_enabled_toolsets, tool_availability_warning_lines,
            )
            from tools.terminal_tool import terminal_backend_unavailable_reason
            from toolsets import resolve_toolset

            _, unavailable = check_tool_availability()
            # Only toolsets this CLI session actually has. The selection is usually a composite bundle
            # (``hermes-cli``), so expand it to tool names before matching — a raw name comparison
            # matched nothing on a default install and silently dropped the terminal notice.
            unavailable = filter_to_enabled_toolsets(unavailable, self.enabled_toolsets or [], resolve_toolset)
            lines = tool_availability_warning_lines(
                unavailable, terminal_reason=terminal_backend_unavailable_reason(),
                terminal_backend=current_terminal_backend())
            if lines:
                self._console_print()
                for line in lines:
                    self._console_print(line)
        except Exception:
            pass

    def show_config(self):
        """Display current configuration with kawaii ASCII art."""
        terminal_env = os.getenv("TERMINAL_ENV", "local")
        terminal_cwd = os.getenv("TERMINAL_CWD", os.getcwd())
        terminal_timeout = os.getenv("TERMINAL_TIMEOUT", "60")

        config_path = _hermes_home / 'config.yaml'
        if not config_path.exists():
            config_path = Path(__file__).parent / 'cli-config.yaml'
        config_status = "(loaded)" if config_path.exists() else "(not found)"

        # ``api_key`` may be a callable (Entra ID bearer provider): never invoke it. Prefer the
        # LIVE agent's key: the constructor seeds self.api_key from env before provider
        # resolution, so on non-OpenAI providers it can be another vendor's key.
        from agent.azure_identity_adapter import is_token_provider

        display_key = self.api_key
        if self.agent is not None and getattr(self.agent, "api_key", None):
            display_key = self.agent.api_key
        if is_token_provider(display_key):
            api_key_display = "Microsoft Entra ID"
        elif isinstance(display_key, str) and len(display_key) > 12:
            api_key_display = f"{display_key[:8]}...{display_key[-4:]}"
        else:
            api_key_display = "Not set!"

        title = "(^_^) Configuration"
        width = 50
        pad = width - len(title)
        ssh_target = (
            f"{os.getenv('TERMINAL_SSH_USER', 'not set')}@{os.getenv('TERMINAL_SSH_HOST', 'not set')}"
            f":{os.getenv('TERMINAL_SSH_PORT', '22')}"
        ) if terminal_env == "ssh" else None
        sections = (
            ("Model", (("Model:    ", self.model), ("Base URL: ", self.base_url), ("API Key:  ", api_key_display))),
            ("Terminal", (
                ("Environment: ", terminal_env),
                *((("SSH Target:  ", ssh_target),) if ssh_target else ()),
                ("Working Dir: ", terminal_cwd),
                ("Timeout:     ", f"{terminal_timeout}s"),
            )),
            ("Agent", (
                ("Max Turns: ", self.max_turns),
                ("Toolsets:  ", ", ".join(self.enabled_toolsets) if self.enabled_toolsets else "all"),
                ("Verbose:   ", self.verbose),
            )),
            ("Session", (
                ("Started:    ", self.session_start.strftime("%Y-%m-%d %H:%M:%S")),
                ("Config File:", f"{config_path} {config_status}"),
            )),
        )
        print()
        print("+" + "-" * width + "+")
        print("|" + " " * (pad // 2) + title + " " * (pad - pad // 2) + "|")
        print("+" + "-" * width + "+")
<<<<<<< HEAD
        print()
        print("  -- Model --")
        print(f"  Model:     {self.model}")
        print(f"  Base URL:  {self.base_url}")
        print(f"  API Key:   {api_key_display}")
        print()
        print("  -- Terminal --")
        print(f"  Environment:  {terminal_env}")
        if terminal_env == "ssh":
            ssh_host = os.getenv("TERMINAL_SSH_HOST", "not set")
            ssh_user = os.getenv("TERMINAL_SSH_USER", "not set")
            ssh_port = os.getenv("TERMINAL_SSH_PORT", "22")
            print(f"  SSH Target:   {ssh_user}@{ssh_host}:{ssh_port}")
        print(f"  Working Dir:  {terminal_cwd}")
        print(f"  Timeout:      {terminal_timeout}s")
        print()
        print("  -- Agent --")
        print(f"  Max Turns:  {self.max_turns}")
        print(f"  Toolsets:   {', '.join(self.enabled_toolsets) if self.enabled_toolsets else 'all'}")
        print(f"  Verbose:    {self.verbose}")
        print()
        print("  -- Session --")
        print(f"  Started:     {self.session_start.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"  Config File: {config_path} {config_status}")
        print()
    
    def _list_recent_sessions(self, limit: int = 10) -> list[dict[str, Any]]:
        """Return recent CLI sessions for in-chat browsing/resume affordances."""
        if not self._session_db:
            return []
        try:
            from openagents_cli.session_listing import query_session_listing

            return query_session_listing(
                self._session_db,
                source="cli",
                current_session_id=self.session_id,
                include_all_sources=False,
                include_unnamed=True,
                limit=limit,
                exclude_sources=["tool"],
            )
        except Exception:
            return []

    def _show_recent_sessions(self, *, reason: str = "history", limit: int = 10) -> bool:
        """Render recent sessions inline from the active chat TUI.

        Returns True when something was shown, False if no session list was available.
        """
        sessions = self._list_recent_sessions(limit=limit)
        if not sessions:
            return False

        from openagents_cli.main import _relative_time

        print()
        if reason == "history":
            print("(._.) No messages in the current chat yet — here are recent sessions you can resume:")
        elif reason == "startup":
            print("(◕‿◕) Recent sessions — resume with /resume <number> or /resume <title>:")
        else:
            print("  Recent sessions:")
        print()
        print(f"  {'#':<3} {'Title':<32} {'Preview':<40} {'Last Active':<13} {'ID'}")
        print(f"  {'─' * 3} {'─' * 32} {'─' * 40} {'─' * 13} {'─' * 24}")
        for idx, session in enumerate(sessions, start=1):
            title = session.get("title") or "—"
            preview = (session.get("preview") or "")[:38]
            last_active = _relative_time(session.get("last_active"))
            print(f"  {idx:<3} {title:<32} {preview:<40} {last_active:<13} {session['id']}")
        print()
        print("  Use /resume <number>, /resume <session id>, or /resume <session title> to continue.")
        print("  Example: /resume 2")
        print()
        return True

    def show_history(self):
        """Display conversation history."""
        if not self.conversation_history:
            if not self._show_recent_sessions(reason="history"):
                print("(._.) No conversation history yet.")
            return

        preview_limit = 400
        visible_index = 0
        hidden_tool_messages = 0
        show_ts = bool(getattr(self, "show_timestamps", False))

        def _ts_suffix(message: dict) -> str:
            # Messages restored from SessionDB carry a unix `timestamp`; live
            # unsaved turns may not. Only annotate when both the toggle is on
            # and the turn actually has a stored time — never fabricate one.
            if not show_ts:
                return ""
            ts = message.get("timestamp")
            if not ts:
                return ""
            try:
                from datetime import datetime
                return f"  [{datetime.fromtimestamp(float(ts)).strftime('%H:%M')}]"
            except (ValueError, OSError, TypeError):
                return ""

        def flush_tool_summary():
            nonlocal hidden_tool_messages
            if not hidden_tool_messages:
                return

            noun = "message" if hidden_tool_messages == 1 else "messages"
            print("\n  [Tools]")
            print(f"    ({hidden_tool_messages} tool {noun} hidden)")
            hidden_tool_messages = 0

        print()
        print("+" + "-" * 50 + "+")
        print("|" + " " * 12 + "(^_^) Conversation History" + " " * 11 + "|")
        print("+" + "-" * 50 + "+")

        for msg in self.conversation_history:
            role = msg.get("role", "unknown")

            if role == "tool":
                hidden_tool_messages += 1
                continue

            if role not in {"user", "assistant"}:
                continue

            flush_tool_summary()
            visible_index += 1

            content = msg.get("content")
            content_text = "" if content is None else str(content)

            if role == "user":
                print(f"\n  [You #{visible_index}]{_ts_suffix(msg)}")
                print(
                    f"    {content_text[:preview_limit]}{'...' if len(content_text) > preview_limit else ''}"
                )
                continue

            print(f"\n  [Hermes #{visible_index}]{_ts_suffix(msg)}")
            tool_calls = msg.get("tool_calls") or []
            if content_text:
                preview = content_text[:preview_limit]
                suffix = "..." if len(content_text) > preview_limit else ""
            elif tool_calls:
                tool_count = len(tool_calls)
                noun = "call" if tool_count == 1 else "calls"
                preview = f"(requested {tool_count} tool {noun})"
                suffix = ""
            else:
                preview = "(no text response)"
                suffix = ""
            print(f"    {preview}{suffix}")

        flush_tool_summary()
        print()
    
    def _notify_session_boundary(self, event_type: str) -> None:
        """Fire a session-boundary plugin hook (on_session_finalize or on_session_reset).

        Non-blocking — errors are caught and logged.  Safe to call from any
        lifecycle point (shutdown, /new, /reset).
        """
        try:
            from openagents_cli.plugins import invoke_hook as _invoke_hook
            _invoke_hook(
                event_type,
                session_id=self.agent.session_id if self.agent else None,
                platform=getattr(self, "platform", None) or "cli",
                reason="new_session" if event_type == "on_session_reset" else "session_boundary",
            )
        except Exception:
            pass

    def _discard_session_if_empty(self, session_id: Optional[str]) -> bool:
        """Drop a just-ended session row when it never gained content.

        Starting the CLI and immediately quitting (or rotating with /new,
        /clear) used to leave an empty untitled row behind that clutters
        ``/resume`` and ``hermes sessions list``. Delegates the
        check-and-delete to ``SessionDB.delete_session_if_empty``, which
        only removes rows with no messages, no title, and no child
        sessions. Ported from google-gemini/gemini-cli#27770.
        """
        if not self._session_db or not session_id:
            return False
        # In-memory transcript is authoritative: if this CLI object holds
        # conversation messages (flushed to the DB or not), the session is
        # not empty. Protects against pruning a real conversation whose DB
        # flush failed or hasn't happened yet.
        if getattr(self, "conversation_history", None):
            return False
        try:
            from openagents_constants import get_openagents_home as _ghh
            return self._session_db.delete_session_if_empty(
                session_id, sessions_dir=_ghh() / "sessions"
            )
        except Exception:
            logger.debug(
                "Could not prune empty session %s", session_id, exc_info=True
            )
            return False

    def new_session(self, silent=False, title=None):
        """Start a fresh session with a new session ID and cleared agent state."""
        if self.agent and self.conversation_history:
            # Trigger memory extraction on the old session before session_id rotates.
            self.agent.commit_memory_session(self.conversation_history)
            self._notify_session_boundary("on_session_finalize")
        elif self.agent:
            # First session or empty history — still finalize the old session
            self._notify_session_boundary("on_session_finalize")

        old_session_id = self.session_id
        if self._session_db and old_session_id:
            # Flush any un-persisted messages from the current turn to the
            # old session *before* rotating.  /new can be called mid-turn
            # when _flush_messages_to_session_db() has not yet run — without
            # this, messages generated during the current turn are silently
            # lost on session rotation (#47202).
            if self.agent:
                try:
                    self.agent._flush_messages_to_session_db(
                        self.conversation_history
                    )
                except Exception:
                    pass  # best-effort
            try:
                self._session_db.end_session(old_session_id, "new_session")
            except Exception:
                pass
            # Don't let immediately-rotated empty sessions pile up in
            # /resume and `hermes sessions list` (gemini-cli#27770 port).
            self._discard_session_if_empty(old_session_id)

        self.session_start = datetime.now()
        timestamp_str = self.session_start.strftime("%Y%m%d_%H%M%S")
        short_uuid = uuid.uuid4().hex[:6]
        self.session_id = f"{timestamp_str}_{short_uuid}"
        self.conversation_history = []
        self._pending_title = None
        self._resumed = False
        _sync_process_session_id(self.session_id)

        if self.agent:
            self.agent.session_id = self.session_id
            self.agent.session_start = self.session_start
            self.agent.reset_session_state()
            if hasattr(self.agent, "_last_flushed_db_idx"):
                self.agent._last_flushed_db_idx = 0
            if hasattr(self.agent, "_todo_store"):
                try:
                    from tools.todo_tool import TodoStore
                    self.agent._todo_store = TodoStore()
                except Exception:
                    pass
            if hasattr(self.agent, "_invalidate_system_prompt"):
                self.agent._invalidate_system_prompt()

            if self._session_db:
                try:
                    self.agent._session_db_created = False
                    self._session_db.create_session(
                        session_id=self.session_id,
                        source=os.environ.get("HERMES_SESSION_SOURCE", "cli"),
                        model=self.model,
                        model_config={
                            "max_iterations": self.max_turns,
                            "reasoning_config": self.reasoning_config,
                        },
                    )
                    self.agent._session_db_created = True
                except Exception:
                    pass
                if title and self._session_db:
                    from openagents_state import SessionDB
                    try:
                        sanitized = SessionDB.sanitize_title(title)
                    except ValueError as e:
                        _cprint(f"  Title rejected: {e}")
                        sanitized = None
                        title = None
                    if sanitized:
                        try:
                            self._session_db.set_session_title(self.session_id, sanitized)
                            self._pending_title = None
                            title = sanitized
                        except ValueError as e:
                            _cprint(f"  {e} — session started untitled.")
                            title = None
                        except Exception:
                            title = None
                    elif title is not None:
                        # sanitize_title returned empty (whitespace-only / unprintable)
                        _cprint("  Title is empty after cleanup — session started untitled.")
                        title = None
            # Notify memory providers that session_id rotated to a fresh
            # conversation. reset=True signals providers to flush accumulated
            # per-session state (_session_turns, _turn_counter, _document_id).
            # Fires BEFORE the plugin on_session_reset hook (shell hooks only
            # see the new id; Python providers see the transition). See #6672.
            try:
                _mm = getattr(self.agent, "_memory_manager", None)
                if _mm is not None:
                    _mm.on_session_switch(
                        self.session_id,
                        parent_session_id=old_session_id or "",
                        reset=True,
                        reason="new_session",
                    )
            except Exception:
                pass
            self._notify_session_boundary("on_session_reset")

        if not silent:
            if title:
                print(f"(^_^)v New session started: {title}")
            else:
                print("(^_^)v New session started!")



    def _consume_pending_resume_selection(self, text: str) -> bool:
        """Resolve a bare numeric reply that follows a bare ``/resume`` prompt.

        After ``/resume`` (no args) prints the recent-sessions list it arms
        ``self._pending_resume_sessions``. The next submitted input is given
        one chance to be a bare session number (``3``); if so we resume that
        session here. Anything else (another command, free text, blank) simply
        disarms the prompt and is handled normally by the caller.

        Returns True if the input was consumed as a resume selection (caller
        must not treat it as chat); False otherwise. The pending state is
        always one-shot: it is cleared on the first submitted input regardless
        of outcome. See #34584.
        """
        pending = self._pending_resume_sessions
        if not pending:
            return False
        # One-shot: disarm now so a non-matching input can't leave the prompt
        # armed and hijack a later number the user meant as chat.
        self._pending_resume_sessions = None

        if not isinstance(text, str):
            return False
        stripped = text.strip()
        # Only a pure number selects; let "/resume 3", titles, or any other
        # text fall through to normal handling.
        if not stripped.isdigit():
            return False

        index = int(stripped)
        if index < 1 or index > len(pending):
            _cprint(f"  Resume index {index} is out of range.")
            _cprint("  Use /resume with no arguments to see available sessions.")
            return True

        self._handle_resume_command(f"/resume {index}")
        return True



    def save_conversation(self):
        """Save the current conversation to a JSON snapshot under ~/.openagents/sessions/saved/.

        The snapshot is a convenience export for sharing or off-line inspection;
        every message is already persisted incrementally to the SQLite session
        DB, so the live session remains resumable via ``hermes --resume <id>``
        regardless of whether the user ever runs ``/save``.
        """
        if not self.conversation_history:
            print("(;_;) No conversation to save.")
            return

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        saved_dir = get_openagents_home() / "sessions" / "saved"
        try:
            saved_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            print(f"(x_x) Failed to create save directory {saved_dir}: {e}")
            return
        path = saved_dir / f"hermes_conversation_{timestamp}.json"

        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump({
                    "model": self.model,
                    "session_id": self.session_id,
                    "session_start": self.session_start.isoformat(),
                    "messages": self.conversation_history,
                }, f, indent=2, ensure_ascii=False)
            print(f"(^_^)v Conversation snapshot saved to: {path}")
            if self.session_id:
                print(f"       Resume the live session with: hermes --resume {self.session_id}")
        except Exception as e:
            print(f"(x_x) Failed to save: {e}")
    
    def retry_last(self):
        """Retry the last user message by removing the last exchange and re-sending.
        
        Removes the last assistant response (and any tool-call messages) and
        the last user message, then re-sends that user message to the agent.
        Returns the message to re-send, or None if there's nothing to retry.
        """
        if not self.conversation_history:
            print("(._.) No messages to retry.")
            return None
        
        # Walk backwards to find the last user message
        last_user_idx = None
        for i in range(len(self.conversation_history) - 1, -1, -1):
            if self.conversation_history[i].get("role") == "user":
                last_user_idx = i
                break
        
        if last_user_idx is None:
            print("(._.) No user message found to retry.")
            return None
        
        # Extract the message text and remove everything from that point forward
        last_message = self.conversation_history[last_user_idx].get("content", "")
        self.conversation_history = self.conversation_history[:last_user_idx]
        
        print(f"(^_^)b Retrying: \"{last_message[:60]}{'...' if len(last_message) > 60 else ''}\"")
        return last_message
    
    def undo_last(self, n: int = 1, prefill: bool = True):
        """Back up N user turns: truncate history, soft-delete on disk, prefill.

        Walks backwards N user messages and discards everything from the
        Nth-from-last user message onward (its assistant response, tool
        calls, etc.). ``n`` defaults to 1 (the last exchange); ``/undo 3``
        backs up three user turns. If ``n`` exceeds the number of user
        turns, it backs up to the oldest one.

        Beyond the in-memory ``conversation_history`` slice, this also:
          • soft-deletes the truncated rows in SessionDB (``active=0``) so
            they're hidden from re-prompts and search but kept for audit;
          • notifies memory providers via ``on_session_switch(rewound=True)``;
          • mirrors /branch's agent surgery (system-prompt invalidation +
            flush-index reset);
          • when ``prefill`` is set and an input buffer is available,
            pre-fills the composer with the backed-up message text so it
            can be edited and resubmitted.

        ``prefill=False`` is used by callers that drive the undo
        programmatically (e.g. checkpoint rollback) and don't want to
        touch the user's input buffer.
        """
        if not self.conversation_history:
            print("(._.) No messages to undo.")
            return

        if n < 1:
            n = 1

        # Walk backwards collecting the indices of the last N user messages.
        user_indices = []
        for i in range(len(self.conversation_history) - 1, -1, -1):
            if self.conversation_history[i].get("role") == "user":
                user_indices.append(i)
                if len(user_indices) >= n:
                    break

        if not user_indices:
            print("(._.) No user message found to undo.")
            return

        # The oldest of the collected user messages is our truncation point.
        cut_idx = user_indices[-1]
        turns_undone = len(user_indices)

        removed_count = len(self.conversation_history) - cut_idx
        removed_msg = self.conversation_history[cut_idx].get("content", "")
        removed_text = self._undo_content_to_text(removed_msg)

        # Truncate the in-memory history to before that user message.
        self.conversation_history = self.conversation_history[:cut_idx]

        # Soft-delete the truncated rows on disk so re-prompts and search
        # see the clean transcript while the rows survive for audit.
        rewound_rows = 0
        if self._session_db is not None and self.session_id:
            try:
                recents = self._session_db.list_recent_user_messages(
                    self.session_id, limit=max(turns_undone, 10)
                )
                if recents:
                    target_idx = min(turns_undone - 1, len(recents) - 1)
                    target_id = recents[target_idx]["id"]
                    result = self._session_db.rewind_to_message(
                        self.session_id, target_id
                    )
                    rewound_rows = result.get("rewound_count", 0)
                    # Prefer the DB's decoded target text for the prefill —
                    # it's the canonical persisted copy.
                    db_text = self._undo_content_to_text(
                        (result.get("target_message") or {}).get("content")
                    )
                    if db_text:
                        removed_text = db_text
            except ValueError as e:
                # Non-user target / cross-session — keep the in-memory undo
                # but skip the soft-delete; surface a debug-level note.
                logger.debug("undo: soft-delete skipped: %s", e)
            except Exception as e:
                logger.debug("undo: soft-delete failed: %s", e)

        # Agent surgery: invalidate the system-prompt cache and reset the
        # flush index so the next turn re-flushes from the truncated head.
        if self.agent is not None:
            if hasattr(self.agent, "_invalidate_system_prompt"):
                try:
                    self.agent._invalidate_system_prompt()
                except Exception:
                    pass
            if hasattr(self.agent, "_last_flushed_db_idx"):
                try:
                    self.agent._last_flushed_db_idx = len(self.conversation_history)
                except Exception:
                    pass
            # Notify memory providers — same hook /branch fires, with the
            # rewound flag so per-turn document caches invalidate (#6672, #21910).
            try:
                _mm = getattr(self.agent, "_memory_manager", None)
                if _mm is not None and self.session_id:
                    _mm.on_session_switch(
                        self.session_id,
                        parent_session_id="",
                        reset=False,
                        rewound=True,
                    )
            except Exception:
                pass

        turn_word = "turn" if turns_undone == 1 else "turns"
        msg_count = rewound_rows or removed_count
        print(
            f"(^_^)b Undid {turns_undone} {turn_word} ({msg_count} message(s)). "
            f"Backed up to: \"{removed_text[:60]}{'...' if len(removed_text) > 60 else ''}\""
        )
        remaining = len(self.conversation_history)
        print(f"  {remaining} message(s) remaining in history.")

        # Pre-fill the composer with the backed-up message so the user can
        # edit and resubmit (Claude-Code-style). Editable, not auto-sent.
        if prefill and removed_text:
            self._prefill_input_buffer(removed_text)

    @staticmethod
    def _undo_content_to_text(content) -> str:
        """Flatten message content (str or content-part list) to plain text."""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = [
                p.get("text", "")
                for p in content
                if isinstance(p, dict) and p.get("type") == "text"
            ]
            return "\n".join(t for t in parts if t)
        return ""

    def _prefill_input_buffer(self, text: str) -> None:
        """Place ``text`` in the active prompt_toolkit buffer, editable."""
        app = getattr(self, "_app", None)
        if app is None:
            return
        try:
            buf = app.current_buffer
            buf.text = text
            if hasattr(buf, "cursor_position"):
                buf.cursor_position = len(text)
            app.invalidate()
        except Exception as e:
            logger.debug("undo: prefill buffer failed: %s", e)
    
    def _run_curses_picker(self, title: str, items: list[str], default_index: int = 0) -> int | None:
        """Run curses_single_select via run_in_terminal so prompt_toolkit handles terminal ownership cleanly."""
        import threading
        from openagents_cli.curses_ui import curses_single_select

        result = [None]

        def _pick():
            result[0] = curses_single_select(title, items, default_index=default_index)

        # run_in_terminal requires an asyncio event loop — only exists in the
        # main prompt_toolkit thread.  If we're in a background thread (e.g.
        # process_loop), fall back to direct curses call.
        in_main_thread = threading.current_thread() is threading.main_thread()

        if self._app and in_main_thread:
            from prompt_toolkit.application import run_in_terminal
            was_visible = self._status_bar_visible
            self._status_bar_visible = False
            self._app.invalidate()
            try:
                run_in_terminal(_pick)
            finally:
                self._status_bar_visible = was_visible
                self._app.invalidate()
        else:
            _pick()

        return result[0]

    def _prompt_text_input(self, prompt_text: str) -> str | None:
        """Prompt for free-text input safely inside or outside prompt_toolkit.

        Mirrors the thread-aware guard in ``_run_curses_picker``: ``run_in_terminal``
        returns a coroutine that must be awaited by the prompt_toolkit event loop,
        which only exists on the main thread.  Slash commands are dispatched from
        the ``process_loop`` daemon thread (see issue #23185), so calling
        ``run_in_terminal`` from there orphans the coroutine — ``_ask`` never runs,
        and user keystrokes leak into the composer instead.  Fall back to a direct
        ``input()`` when we're off the main thread.
        """
        import threading
        result = [None]

        def _ask():
            try:
                result[0] = input(prompt_text).strip() or None
            except (KeyboardInterrupt, EOFError):
                pass

        in_main_thread = threading.current_thread() is threading.main_thread()

        # Slash-worker guard (#23185 / billing auto-reload hang): when a
        # prompt_toolkit app is running but we're on a non-main thread (the
        # process_loop / TUI slash-worker daemon thread), stdin is owned by the
        # event loop / JSON-RPC pipe.  A bare input() there blocks forever until
        # the worker's 45s timeout fires.  We cannot safely prompt off the main
        # thread, so cancel cleanly (None) instead of hanging — mirrors the
        # _stdin_fallback discipline in _prompt_text_input_modal.
        if self._app and not in_main_thread:
            self._invalidate()
            return None

        if self._app and in_main_thread:
            from prompt_toolkit.application import run_in_terminal
            was_visible = self._status_bar_visible
            self._status_bar_visible = False
            self._app.invalidate()
            try:
                run_in_terminal(_ask)
            except Exception:
                # WSL / Warp / certain terminal emulators silently drop the
                # scheduled coroutine.  Fall back to a direct input() so the
                # user's keystrokes don't leak into the agent buffer.
                try:
                    _ask()
                except Exception:
                    pass
            finally:
                self._status_bar_visible = was_visible
                self._app.invalidate()
        else:
            _ask()
        return result[0]

    def _prompt_text_input_modal(
        self,
        *,
        title: str,
        detail: str,
        choices: list[tuple[str, str, str]],
        timeout: float = 120,
    ) -> str | None:
        """Prompt through the prompt_toolkit composer instead of raw input().

        This is for CLI slash-command confirmations.  The old raw input() path
        fought prompt_toolkit's active stdin ownership: in some terminals the
        prompt appeared above the TUI, choices were redrawn later, and Enter
        could be interpreted as EOF/exit.  A first-class modal state keeps the
        choices visible and lets the normal Enter key binding submit the typed
        or highlighted choice.

        **Platform note (Windows — issue #33961):**
        Earlier code bypassed the modal on ``sys.platform == "win32"`` and fell
        back to a raw ``input()`` prompt.  When the confirm was triggered from the
        ``process_loop`` daemon thread (the normal case) that ``input()`` ran off
        the main thread and deadlocked against prompt_toolkit's stdin ownership —
        the user saw a frozen cursor and Ctrl-C was swallowed (bare ``/reset``
        froze; ``/reset now`` worked only because it skips the prompt entirely).

        Native Windows now uses the same path as Linux/macOS: the modal is set up
        on ``self._app.loop`` via ``call_soon_threadsafe`` and answered by the
        normal prompt_toolkit key bindings (the same input channel that already
        handles ordinary typing on Windows).  The raw ``input()`` fallback is kept
        only for the genuinely safe cases: no running app (unit tests /
        non-interactive), no resolvable event loop, or a scheduling failure.
        """
        import threading
        import time as _time

        if not choices:
            return None

        # If prompt_toolkit is not running (unit tests / non-interactive calls),
        # keep the simple stdin fallback.
        if not getattr(self, "_app", None):
            return self._prompt_text_input("Choice [1/2/3]: ")

        try:
            app_loop = self._app.loop
        except Exception:
            app_loop = None

        in_main_thread = threading.current_thread() is threading.main_thread()

        def _stdin_fallback() -> str | None:
            # On native Windows a raw input() from a non-main thread deadlocks
            # against prompt_toolkit's stdin ownership (#33961).  With an app
            # running we cannot safely prompt off the main thread, so cancel
            # cleanly (None) rather than hang the terminal.
            if sys.platform == "win32" and not in_main_thread:
                self._invalidate()
                return None
            return self._prompt_text_input("Choice [1/2/3]: ")

        if not in_main_thread and app_loop is None:
            return _stdin_fallback()

        response_queue = queue.Queue()

        def _setup_modal() -> None:
            self._capture_modal_input_snapshot()
            self._slash_confirm_state = {
                "title": title,
                "detail": detail,
                "choices": choices,
                "selected": 0,
                "response_queue": response_queue,
            }
            self._slash_confirm_deadline = _time.monotonic() + timeout
            self._invalidate()

        def _teardown_modal() -> None:
            self._slash_confirm_state = None
            self._slash_confirm_deadline = 0
            self._restore_modal_input_snapshot()
            self._invalidate()

        def _run_on_app_loop(fn) -> bool:
            if in_main_thread or app_loop is None:
                fn()
                return True
            ready = threading.Event()

            def _wrapped() -> None:
                try:
                    fn()
                finally:
                    ready.set()

            try:
                app_loop.call_soon_threadsafe(_wrapped)
            except Exception:
                return False
            return ready.wait(timeout=5)

        if not _run_on_app_loop(_setup_modal):
            return _stdin_fallback()

        _last_countdown_refresh = _time.monotonic()
        try:
            while True:
                try:
                    result = response_queue.get(timeout=1)
                    _run_on_app_loop(_teardown_modal)
                    return result
                except queue.Empty:
                    remaining = self._slash_confirm_deadline - _time.monotonic()
                    if remaining <= 0:
                        break
                    now = _time.monotonic()
                    if now - _last_countdown_refresh >= 5.0:
                        _last_countdown_refresh = now
                        self._invalidate()
        finally:
            if self._slash_confirm_state is not None:
                _run_on_app_loop(_teardown_modal)
        return None

    def _submit_slash_confirm_response(self, value: str | None) -> None:
        state = self._slash_confirm_state
        if not state:
            return
        state["response_queue"].put(value)
        self._slash_confirm_state = None
        self._slash_confirm_deadline = 0
        self._invalidate()

    def _normalize_slash_confirm_choice(
        self,
        raw: str | None,
        choices: list[tuple[str, str, str]],
    ) -> str | None:
        if raw is None:
            return None
        choice_raw = raw.strip().lower()
        if not choice_raw:
            return None
        aliases = {
            "1": "once",
            "once": "once",
            "approve": "once",
            "yes": "once",
            "y": "once",
            "ok": "once",
            "2": "always",
            "always": "always",
            "remember": "always",
            "3": "cancel",
            "cancel": "cancel",
            "nevermind": "cancel",
            "no": "cancel",
            "n": "cancel",
        }
        allowed = {choice[0] for choice in choices}
        normalized = aliases.get(choice_raw)
        if normalized in allowed:
            return normalized
        if choice_raw in allowed:
            return choice_raw
        return None

    def _get_slash_confirm_display_fragments(self):
        """Render the /new-/clear-style confirmation panel."""
        state = self._slash_confirm_state
        if not state:
            return []

        title = state.get("title") or "Confirm action"
        detail = state.get("detail") or ""
        choices = state.get("choices") or []
        selected = state.get("selected", 0)

        def _panel_box_width(title_text: str, content_lines: list[str], min_width: int = 56, max_width: int = 86) -> int:
            term_cols = shutil.get_terminal_size((100, 20)).columns
            longest = max([len(title_text)] + [len(line) for line in content_lines] + [min_width - 4])
            inner = min(max(longest + 4, min_width - 2), max_width - 2, max(24, term_cols - 6))
            return inner + 2

        def _wrap_panel_text(text: str, width: int, subsequent_indent: str = "") -> list[str]:
            wrapped = textwrap.wrap(
                text,
                width=max(8, width),
                replace_whitespace=False,
                drop_whitespace=False,
                subsequent_indent=subsequent_indent,
            )
            return wrapped or [""]

        def _append_panel_line(lines, border_style: str, content_style: str, text: str, box_width: int) -> None:
            inner_width = max(0, box_width - 2)
            lines.append((border_style, "│ "))
            lines.append((content_style, text.ljust(inner_width)))
            lines.append((border_style, " │\n"))

        def _append_blank_panel_line(lines, border_style: str, box_width: int) -> None:
            lines.append((border_style, "│" + (" " * box_width) + "│\n"))

        preview_lines = []
        for line in detail.splitlines():
            preview_lines.extend(_wrap_panel_text(line, 72))
        for idx, (_value, label, desc) in enumerate(choices):
            marker = "❯" if idx == selected else " "
            preview_lines.extend(_wrap_panel_text(f"{marker} [{idx + 1}] {label} — {desc}", 72, subsequent_indent="    "))
        preview_lines.append("Type 1/2/3 or use ↑/↓ then Enter. ESC/Ctrl+C cancels.")

        box_width = _panel_box_width(title, preview_lines)
        inner_text_width = max(8, box_width - 2)
        detail_wrapped = []
        for line in detail.splitlines():
            detail_wrapped.extend(_wrap_panel_text(line, inner_text_width))
        choice_wrapped: list[tuple[int, str]] = []
        for idx, (_value, label, desc) in enumerate(choices):
            marker = "❯" if idx == selected else " "
            for wrapped in _wrap_panel_text(f"{marker} [{idx + 1}] {label} — {desc}", inner_text_width, subsequent_indent="    "):
                choice_wrapped.append((idx, wrapped))

        term_rows = shutil.get_terminal_size((100, 24)).lines
        reserved_below = 6
        chrome_full = 6
        available = max(0, term_rows - reserved_below)
        max_detail_rows = max(1, available - chrome_full - len(choice_wrapped))
        max_detail_rows = min(max_detail_rows, 8)
        if len(detail_wrapped) > max_detail_rows:
            keep = max(1, max_detail_rows - 1)
            detail_wrapped = detail_wrapped[:keep] + ["… (detail truncated)"]

        lines = []
        lines.append(('class:approval-border', '╭' + ('─' * box_width) + '╮\n'))
        _append_panel_line(lines, 'class:approval-border', 'class:approval-title', title, box_width)
        _append_blank_panel_line(lines, 'class:approval-border', box_width)
        for wrapped in detail_wrapped:
            _append_panel_line(lines, 'class:approval-border', 'class:approval-desc', wrapped, box_width)
        _append_blank_panel_line(lines, 'class:approval-border', box_width)
        for idx, wrapped in choice_wrapped:
            style = 'class:approval-selected' if idx == selected else 'class:approval-choice'
            _append_panel_line(lines, 'class:approval-border', style, wrapped, box_width)
        _append_blank_panel_line(lines, 'class:approval-border', box_width)
        _append_panel_line(lines, 'class:approval-border', 'class:approval-cmd', 'Type 1/2/3 or use ↑/↓ then Enter. ESC/Ctrl+C cancels.', box_width)
        lines.append(('class:approval-border', '╰' + ('─' * box_width) + '╯\n'))
        return lines

    def _open_model_picker(self, providers: list, current_model: str, current_provider: str, user_provs=None, custom_provs=None) -> None:
        """Open prompt_toolkit-native /model picker modal."""
        self._capture_modal_input_snapshot()
        default_idx = next((i for i, p in enumerate(providers) if p.get("is_current")), 0)
        self._model_picker_state = {
            "stage": "provider",
            "providers": providers,
            "selected": default_idx,
            "current_model": current_model,
            "current_provider": current_provider,
            "user_provs": user_provs,
            "custom_provs": custom_provs,
        }
        self._invalidate(min_interval=0.0)

    def _confirm_expensive_model_switch(self, result) -> bool:
        """Ask for explicit confirmation before applying costly model switches."""
        if not getattr(result, "success", False):
            return True
        try:
            from openagents_cli.model_cost_guard import expensive_model_warning

            warning = expensive_model_warning(
                result.new_model,
                provider=result.target_provider,
                base_url=result.base_url or self.base_url or "",
                api_key=result.api_key or self.api_key or "",
                model_info=result.model_info,
            )
        except Exception:
            warning = None
        if warning is None:
            return True

        choices = [
            ("once", "Switch anyway", "Use this model for the current OpenAgents session."),
            ("cancel", "Cancel", "Keep the current model."),
        ]
        raw = self._prompt_text_input_modal(
            title="!!! Expensive Model Warning !!!",
            detail=warning.message,
            choices=choices,
            timeout=120,
        )
        choice = self._normalize_slash_confirm_choice(raw, choices)
        return choice == "once"

    def _confirm_and_apply_model_switch_result(self, result, persist_global: bool) -> None:
        try:
            if result.success and not self._confirm_expensive_model_switch(result):
                _cprint("  Model switch cancelled.")
                return
            self._apply_model_switch_result(result, persist_global)
        except Exception as exc:
            _cprint(f"  ✗ Model selection failed: {exc}")

    def _close_model_picker(self) -> None:
        self._model_picker_state = None
        self._restore_modal_input_snapshot()
        self._invalidate(min_interval=0.0)

    @staticmethod
    def _compute_model_picker_viewport(
        selected: int,
        scroll_offset: int,
        n: int,
        term_rows: int,
        reserved_below: int = 6,
        panel_chrome: int = 6,
        min_visible: int = 3,
    ) -> tuple[int, int]:
        """Resolve (scroll_offset, visible) for the /model picker viewport.

        ``reserved_below`` matches the approval / clarify panels — input area,
        status bar, and separators below the panel. ``panel_chrome`` covers
        this panel's own borders + blanks + hint row. The remaining rows hold
        the scrollable list, with the offset slid to keep ``selected`` on screen.
        """
        max_visible = max(min_visible, term_rows - reserved_below - panel_chrome)
        if n <= max_visible:
            return 0, n
        visible = max_visible
        if selected < scroll_offset:
            scroll_offset = selected
        elif selected >= scroll_offset + visible:
            scroll_offset = selected - visible + 1
        scroll_offset = max(0, min(scroll_offset, n - visible))
        return scroll_offset, visible

    def _apply_model_switch_result(self, result, persist_global: bool) -> None:
        if not result.success:
            _cprint(f"  ✗ {result.error_message}")
            return

        if self.agent is not None:
            try:
                from openagents_cli.context_switch_guard import merge_preflight_compression_warning

                merge_preflight_compression_warning(
                    result,
                    agent=self.agent,
                    messages=list(self.conversation_history or []),
                    config_context_length=getattr(self.agent, "_config_context_length", None),
                )
            except Exception as exc:
                logger.debug("preflight-compression switch warning failed: %s", exc)

        old_model = self.model
        # Snapshot the CLI-level credential/runtime fields BEFORE mutating them
        # so a failed in-place agent swap can roll the whole CLI back to the old
        # working model.  Otherwise the broken credentials staged below leak into
        # the next turn's resolution even though the agent itself rolled back
        # (#50163).
        _cli_snapshot = {
            "model": self.model,
            "provider": self.provider,
            "requested_provider": self.requested_provider,
            "_explicit_api_key": getattr(self, "_explicit_api_key", None),
            "_explicit_base_url": getattr(self, "_explicit_base_url", None),
            "api_key": self.api_key,
            "base_url": self.base_url,
            "api_mode": self.api_mode,
        }
        self.model = result.new_model
        self.provider = result.target_provider
        self.requested_provider = result.target_provider
        # Always overwrite explicit overrides so stale credentials from the
        # previous provider (e.g. Ollama api_key/base_url) don't leak into
        # the new provider's credential resolution on the next turn.
        self._explicit_api_key = result.api_key
        self._explicit_base_url = result.base_url
        if result.api_key:
            self.api_key = result.api_key
        if result.base_url:
            self.base_url = result.base_url
        if result.api_mode:
            self.api_mode = result.api_mode

        if self.agent is not None:
            try:
                self.agent.switch_model(
                    new_model=result.new_model,
                    new_provider=result.target_provider,
                    api_key=result.api_key,
                    base_url=result.base_url,
                    api_mode=result.api_mode,
                )
            except Exception as exc:
                # The agent rolled itself back to the old working model/client.
                # Roll the CLI's own staged fields back too and abort the rest
                # of the commit (note + success print) so a failed switch is a
                # no-op rather than a dead session (#50163).
                for _k, _v in _cli_snapshot.items():
                    setattr(self, _k, _v)
                _cprint(
                    f"  ⚠ Model switch to {result.new_model} failed ({exc}); "
                    f"staying on {old_model}."
                )
                return

        self._pending_model_switch_note = (
            f"[Note: model was just switched from {old_model} to {result.new_model} "
            f"via {result.provider_label or result.target_provider}. "
            f"Adjust your self-identification accordingly.]"
        )

        provider_label = result.provider_label or result.target_provider
        _cprint(f"  ✓ Model switched: {result.new_model}")
        _cprint(f"    Provider: {provider_label}")

        # Context: always resolve via the provider-aware chain so Codex OAuth,
        # Copilot, and Nous-enforced caps win over the raw models.dev entry
        # (e.g. gpt-5.5 is 1.05M on openai but 272K on Codex OAuth).
        mi = result.model_info
        try:
            from openagents_cli.model_switch import resolve_display_context_length
            ctx = resolve_display_context_length(
                result.new_model,
                result.target_provider,
                base_url=result.base_url or self.base_url or "",
                api_key=result.api_key or self.api_key or "",
                model_info=mi,
                config_context_length=getattr(self.agent, "_config_context_length", None) if self.agent else None,
            )
            if ctx:
                _cprint(f"    Context: {ctx:,} tokens")
        except Exception:
            pass
        if mi:
            if mi.max_output:
                _cprint(f"    Max output: {mi.max_output:,} tokens")
            _cprint(f"    Capabilities: {mi.format_capabilities()}")

        cache_enabled = (
            (base_url_host_matches(result.base_url or "", "openrouter.ai") and "claude" in result.new_model.lower())
            or result.api_mode == "anthropic_messages"
        )
        if cache_enabled:
            _cprint("    Prompt caching: enabled")
        if result.warning_message:
            _cprint(f"    ⚠ {result.warning_message}")
        if persist_global:
            save_config_value("model.default", result.new_model)
            if result.provider_changed:
                save_config_value("model.provider", result.target_provider)
            _cprint("    Saved to config.yaml (--global)")
        else:
            _cprint("    (session only — add --global to persist)")

    def _handle_model_picker_selection(self, persist_global: bool = False) -> None:
        state = self._model_picker_state
        if not state:
            return
        selected = state.get("selected", 0)
        stage = state.get("stage")
        if stage == "provider":
            providers = state.get("providers") or []
            if selected >= len(providers):
                self._close_model_picker()
                return
            provider_data = providers[selected]
            # Use the curated model list from list_authenticated_providers()
            # (same lists as `hermes model` and gateway pickers).
            # Only fall back to the live provider catalog when the curated
            # list is empty (e.g. user-defined endpoints with no curated list).
            model_list = provider_data.get("models", [])
            if not model_list:
                try:
                    from openagents_cli.models import provider_model_ids
                    live = provider_model_ids(provider_data["slug"])
                    if live:
                        model_list = live
                except Exception:
                    pass
            state["stage"] = "model"
            state["provider_data"] = provider_data
            state["model_list"] = model_list
            state["selected"] = 0
            self._invalidate(min_interval=0.0)
            return
        if stage == "model":
            provider_data = state.get("provider_data") or {}
            model_list = state.get("model_list") or []
            back_idx = len(model_list)
            cancel_idx = len(model_list) + 1
            if selected == back_idx:
                state["stage"] = "provider"
                state["selected"] = next((i for i, p in enumerate(state.get("providers") or []) if p.get("slug") == provider_data.get("slug")), 0)
                self._invalidate(min_interval=0.0)
                return
            if selected >= cancel_idx:
                self._close_model_picker()
                return
            if selected < len(model_list):
                from openagents_cli.model_switch import switch_model
                chosen_model = model_list[selected]
                result = switch_model(
                    raw_input=chosen_model,
                    current_provider=self.provider or "",
                    current_model=self.model or "",
                    current_base_url=self.base_url or "",
                    current_api_key=self.api_key or "",
                    is_global=persist_global,
                    explicit_provider=provider_data.get("slug"),
                    user_providers=state.get("user_provs"),
                    custom_providers=state.get("custom_provs"),
                )
                self._close_model_picker()
                if getattr(self, "_app", None):
                    threading.Thread(
                        target=self._confirm_and_apply_model_switch_result,
                        args=(result, persist_global),
                        daemon=True,
                    ).start()
                else:
                    self._confirm_and_apply_model_switch_result(result, persist_global)
                return
            self._close_model_picker()

    def _handle_model_switch(self, cmd_original: str):
        """Handle /model command — switch model.

        Supports:
          /model                              — show current model + usage hints
          /model <name>                       — switch model (persists by default)
          /model <name> --session             — switch for this session only
          /model <name> --global              — switch and persist (explicit)
          /model <name> --provider <provider> — switch provider + model
          /model --provider <provider>        — switch to provider, auto-detect model

        Persistence defaults to on (``model.persist_switch_by_default`` in
        config.yaml, default True). Use ``--session`` for a one-off switch.
        """
        from openagents_cli.model_switch import (
            switch_model,
            parse_model_flags,
            resolve_persist_behavior,
        )
        from openagents_cli.providers import get_label

        # Parse args from the original command
        parts = cmd_original.split(None, 1)  # split off '/model'
        raw_args = parts[1].strip() if len(parts) > 1 else ""

        # Parse --provider, --global, --session, and --refresh flags
        (
            model_input,
            explicit_provider,
            is_global_flag,
            force_refresh,
            is_session,
        ) = parse_model_flags(raw_args)
        # Resolve the effective persistence once: --session overrides the
        # config-gated default, --global forces persist, otherwise defer to
        # model.persist_switch_by_default (defaults to True so /model survives
        # across sessions).
        persist_global = resolve_persist_behavior(is_global_flag, is_session)

        # --refresh: wipe the on-disk picker cache before building the
        # provider list. Forces a live re-fetch of every authed provider's
        # /v1/models endpoint on this open.
        if force_refresh:
            try:
                from openagents_cli.models import clear_provider_models_cache
                clear_provider_models_cache()
                _cprint("  Cleared model picker cache. Refreshing...")
            except Exception:
                pass

        # Single inventory context — replaces the inline config-slice the
        # dashboard / TUI used to duplicate. Overlay live session state
        # via with_overrides (truthy-only) so empty self.* attrs don't
        # clobber disk config.
        from openagents_cli.inventory import build_models_payload, load_picker_context

        try:
            ctx = load_picker_context().with_overrides(
                current_provider=self.provider or "",
                current_model=self.model or "",
                current_base_url=self.base_url or "",
            )
        except Exception:
            ctx = None

        # switch_model() + _open_model_picker still need the raw provider
        # dicts; ConfigContext is the canonical source for both.
        user_provs = ctx.user_providers if ctx is not None else None
        custom_provs = ctx.custom_providers if ctx is not None else None

        # No args at all: open prompt_toolkit-native picker modal
        if not model_input and not explicit_provider:
            model_display = self.model or "unknown"
            provider_display = get_label(self.provider) if self.provider else "unknown"

            try:
                if ctx is None:
                    raise RuntimeError("inventory context unavailable")
                providers = build_models_payload(ctx)["providers"]
            except Exception:
                providers = []

            if not providers:
                _cprint("  No authenticated providers found.")
                _cprint("")
                _cprint("  /model <name>                        switch model (persists)")
                _cprint("  /model <name> --session              switch for this session only")
                _cprint("  /model --provider <slug>             switch provider")
                _cprint("  /model --refresh                     re-fetch live model lists")
                return

            self._open_model_picker(
                providers,
                model_display,
                provider_display,
                user_provs=user_provs,
                custom_provs=custom_provs,
            )
            return

        # Perform the switch
        result = switch_model(
            raw_input=model_input,
            current_provider=self.provider or "",
            current_model=self.model or "",
            current_base_url=self.base_url or "",
            current_api_key=self.api_key or "",
            is_global=persist_global,
            explicit_provider=explicit_provider,
            user_providers=user_provs,
            custom_providers=custom_provs,
        )

        if not result.success:
            _cprint(f"  ✗ {result.error_message}")
            return

        if self.agent is not None:
            try:
                from openagents_cli.context_switch_guard import merge_preflight_compression_warning

                merge_preflight_compression_warning(
                    result,
                    agent=self.agent,
                    messages=list(self.conversation_history or []),
                    config_context_length=getattr(self.agent, "_config_context_length", None),
                )
            except Exception as exc:
                logger.debug("preflight-compression switch warning failed: %s", exc)

        if not self._confirm_expensive_model_switch(result):
            _cprint("  Model switch cancelled.")
            return

        # Apply to CLI state.
        # Update requested_provider so _ensure_runtime_credentials() doesn't
        # overwrite the switch on the next turn (it re-resolves from this).
        old_model = self.model
        # Snapshot CLI-level fields before mutation so a failed in-place swap
        # rolls the whole CLI back to the old working model (#50163).
        _cli_snapshot = {
            "model": self.model,
            "provider": self.provider,
            "requested_provider": self.requested_provider,
            "_explicit_api_key": getattr(self, "_explicit_api_key", None),
            "_explicit_base_url": getattr(self, "_explicit_base_url", None),
            "api_key": self.api_key,
            "base_url": self.base_url,
            "api_mode": self.api_mode,
        }
        self.model = result.new_model
        self.provider = result.target_provider
        self.requested_provider = result.target_provider
        # Always overwrite explicit overrides so stale credentials from the
        # previous provider (e.g. Ollama api_key/base_url) don't leak into
        # the new provider's credential resolution on the next turn.
        self._explicit_api_key = result.api_key
        self._explicit_base_url = result.base_url
        if result.api_key:
            self.api_key = result.api_key
        if result.base_url:
            self.base_url = result.base_url
        if result.api_mode:
            self.api_mode = result.api_mode

        # Apply to running agent (in-place swap)
        if self.agent is not None:
            try:
                self.agent.switch_model(
                    new_model=result.new_model,
                    new_provider=result.target_provider,
                    api_key=result.api_key,
                    base_url=result.base_url,
                    api_mode=result.api_mode,
                )
            except Exception as exc:
                # Agent rolled itself back; roll the CLI back too and abort so a
                # failed switch is a no-op rather than a dead session (#50163).
                for _k, _v in _cli_snapshot.items():
                    setattr(self, _k, _v)
                _cprint(
                    f"  ⚠ Model switch to {result.new_model} failed ({exc}); "
                    f"staying on {old_model}."
                )
                return

        # Store a note to prepend to the next user message so the model
        # knows a switch occurred (avoids injecting system messages mid-history
        # which breaks providers and prompt caching).
        self._pending_model_switch_note = (
            f"[Note: model was just switched from {old_model} to {result.new_model} "
            f"via {result.provider_label or result.target_provider}. "
            f"Adjust your self-identification accordingly.]"
        )

        # Display confirmation with full metadata
        provider_label = result.provider_label or result.target_provider
        _cprint(f"  ✓ Model switched: {result.new_model}")
        _cprint(f"    Provider: {provider_label}")

        # Context: always resolve via the provider-aware chain so Codex OAuth,
        # Copilot, and Nous-enforced caps win over the raw models.dev entry
        # (e.g. gpt-5.5 is 1.05M on openai but 272K on Codex OAuth).
        mi = result.model_info
        from openagents_cli.model_switch import resolve_display_context_length
        ctx = resolve_display_context_length(
            result.new_model,
            result.target_provider,
            base_url=result.base_url or self.base_url or "",
            api_key=result.api_key or self.api_key or "",
            model_info=mi,
            config_context_length=getattr(self.agent, "_config_context_length", None) if self.agent else None,
        )
        if ctx:
            _cprint(f"    Context: {ctx:,} tokens")
        if mi:
            if mi.max_output:
                _cprint(f"    Max output: {mi.max_output:,} tokens")
            _cprint(f"    Capabilities: {mi.format_capabilities()}")

        # Cache notice
        cache_enabled = (
            (base_url_host_matches(result.base_url or "", "openrouter.ai") and "claude" in result.new_model.lower())
            or result.api_mode == "anthropic_messages"
        )
        if cache_enabled:
            _cprint("    Prompt caching: enabled")

        # Warning from validation
        if result.warning_message:
            _cprint(f"    ⚠ {result.warning_message}")

        # Persistence
        if persist_global:
            save_config_value("model.default", result.new_model)
            if result.provider_changed:
                save_config_value("model.provider", result.target_provider)
            _cprint("    Saved to config.yaml")
        else:
            _cprint("    (session only — add --global to persist)")

    def _handle_codex_runtime(self, cmd_original: str) -> None:
        """Handle /codex-runtime — toggle the codex app-server runtime opt-in.

        Usage:
            /codex-runtime                       — show current state
            /codex-runtime auto                  — OpenAgents default (chat_completions)
            /codex-runtime codex_app_server      — hand turns to codex subprocess
            /codex-runtime on / off              — synonyms for the above
        """
        from openagents_cli import codex_runtime_switch as crs

        parts = cmd_original.split(None, 1)
        raw_args = parts[1].strip() if len(parts) > 1 else ""
        new_value, errors = crs.parse_args(raw_args)
        if errors:
            for err in errors:
                _cprint(f"❌ {err}")
            return

        # Load + persist via the existing config helpers
        try:
            from openagents_cli.config import load_config, save_config
        except Exception as exc:
            _cprint(f"❌ could not load config: {exc}")
            return
        cfg = load_config()

        result = crs.apply(
            cfg,
            new_value,
            persist_callback=(save_config if new_value is not None else None),
        )

        prefix = "✓" if result.success else "✗"
        for line in result.message.splitlines():
            _cprint(f"  {prefix} {line}" if line.startswith("openai_runtime")
                    else f"    {line}")
        if result.success and result.requires_new_session:
            _cprint("    Tip: `/reset` starts a new session immediately.")

    def _should_handle_model_command_inline(self, text: str, has_images: bool = False) -> bool:
        """Return True when /model should be handled immediately on the UI thread."""
        if not text or has_images or not _looks_like_slash_command(text):
            return False
        try:
            from openagents_cli.commands import resolve_command
            base = text.split(None, 1)[0].lower().lstrip('/')
            cmd = resolve_command(base)
            return bool(cmd and cmd.name == "model")
        except Exception:
            return False

    def _should_handle_steer_command_inline(self, text: str, has_images: bool = False) -> bool:
        """Return True when /steer should be dispatched immediately while the agent is running.

        /steer MUST bypass the normal _pending_input → process_loop path when
        the agent is active, because process_loop is blocked inside
        self.chat() for the duration of the run.  By the time the queued
        command is pulled from _pending_input, _agent_running has already
        flipped back to False, and process_command() takes the idle
        fallback — delivering the steer as a next-turn message instead of
        injecting it mid-run.  Dispatching inline on the UI thread calls
        agent.steer() directly, which is thread-safe (uses _pending_steer_lock).
        """
        if not text or has_images or not _looks_like_slash_command(text):
            return False
        if not getattr(self, "_agent_running", False):
            return False
        try:
            from openagents_cli.commands import resolve_command
            base = text.split(None, 1)[0].lower().lstrip('/')
            cmd = resolve_command(base)
            return bool(cmd and cmd.name == "steer")
        except Exception:
            return False

    def _output_console(self):
        """Use prompt_toolkit-safe Rich rendering once the TUI is live."""
        if getattr(self, "_app", None):
            return ChatConsole()
        return self.console

    def _console_print(self, *args, **kwargs):
        """Print through the active command-safe console."""
        self._output_console().print(*args, **kwargs)

    @staticmethod
    def _resolve_personality_prompt(value) -> str:
        """Accept string or dict personality value; return system prompt string."""
        if isinstance(value, dict):
            parts = [value.get("system_prompt", "")]
            if value.get("tone"):
                parts.append(f'Tone: {value["tone"]}' )
            if value.get("style"):
                parts.append(f'Style: {value["style"]}' )
            return "\n".join(p for p in parts if p)
        return str(value)


    



    def _show_gateway_status(self):
        """Show status of the gateway and connected messaging platforms."""
        from gateway.config import load_gateway_config, Platform
        
        print()
        print("+" + "-" * 60 + "+")
        print("|" + " " * 15 + "(✿◠‿◠) Gateway Status" + " " * 17 + "|")
        print("+" + "-" * 60 + "+")
        print()
        
        try:
            config = load_gateway_config()
            
            print("  Messaging Platform Configuration:")
            print("  " + "-" * 55)
            
            platform_status = {
                Platform.TELEGRAM: ("Telegram", "TELEGRAM_BOT_TOKEN"),
                Platform.DISCORD: ("Discord", "DISCORD_BOT_TOKEN"),
                Platform.SLACK: ("Slack", "SLACK_BOT_TOKEN"),
                Platform.WHATSAPP: ("WhatsApp", "WHATSAPP_ENABLED"),
            }
            
            for platform, (name, env_var) in platform_status.items():
                pconfig = config.platforms.get(platform)
                if pconfig and pconfig.enabled:
                    home = config.get_home_channel(platform)
                    home_str = f" → {home.name}" if home else ""
                    print(f"    ✓ {name:<12} Enabled{home_str}")
                else:
                    print(f"    ○ {name:<12} Not configured ({env_var})")
            
||||||| cf299e9a01
        print()
        print("  -- Model --")
        print(f"  Model:     {self.model}")
        print(f"  Base URL:  {self.base_url}")
        print(f"  API Key:   {api_key_display}")
        print()
        print("  -- Terminal --")
        print(f"  Environment:  {terminal_env}")
        if terminal_env == "ssh":
            ssh_host = os.getenv("TERMINAL_SSH_HOST", "not set")
            ssh_user = os.getenv("TERMINAL_SSH_USER", "not set")
            ssh_port = os.getenv("TERMINAL_SSH_PORT", "22")
            print(f"  SSH Target:   {ssh_user}@{ssh_host}:{ssh_port}")
        print(f"  Working Dir:  {terminal_cwd}")
        print(f"  Timeout:      {terminal_timeout}s")
        print()
        print("  -- Agent --")
        print(f"  Max Turns:  {self.max_turns}")
        print(f"  Toolsets:   {', '.join(self.enabled_toolsets) if self.enabled_toolsets else 'all'}")
        print(f"  Verbose:    {self.verbose}")
        print()
        print("  -- Session --")
        print(f"  Started:     {self.session_start.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"  Config File: {config_path} {config_status}")
        print()
    
    def _list_recent_sessions(self, limit: int = 10) -> list[dict[str, Any]]:
        """Return recent CLI sessions for in-chat browsing/resume affordances."""
        if not self._session_db:
            return []
        try:
            from openagents_cli.session_listing import query_session_listing

            return query_session_listing(
                self._session_db,
                source="cli",
                current_session_id=self.session_id,
                include_all_sources=False,
                include_unnamed=True,
                limit=limit,
                exclude_sources=["tool"],
            )
        except Exception:
            return []

    def _show_recent_sessions(self, *, reason: str = "history", limit: int = 10) -> bool:
        """Render recent sessions inline from the active chat TUI.

        Returns True when something was shown, False if no session list was available.
        """
        sessions = self._list_recent_sessions(limit=limit)
        if not sessions:
            return False

        from openagents_cli.main import _relative_time

        print()
        if reason == "history":
            print("(._.) No messages in the current chat yet — here are recent sessions you can resume:")
        else:
            print("  Recent sessions:")
        print()
        print(f"  {'#':<3} {'Title':<32} {'Preview':<40} {'Last Active':<13} {'ID'}")
        print(f"  {'─' * 3} {'─' * 32} {'─' * 40} {'─' * 13} {'─' * 24}")
        for idx, session in enumerate(sessions, start=1):
            title = session.get("title") or "—"
            preview = (session.get("preview") or "")[:38]
            last_active = _relative_time(session.get("last_active"))
            print(f"  {idx:<3} {title:<32} {preview:<40} {last_active:<13} {session['id']}")
        print()
        print("  Use /resume <number>, /resume <session id>, or /resume <session title> to continue.")
        print("  Example: /resume 2")
        print()
        return True

    def show_history(self):
        """Display conversation history."""
        if not self.conversation_history:
            if not self._show_recent_sessions(reason="history"):
                print("(._.) No conversation history yet.")
            return

        preview_limit = 400
        visible_index = 0
        hidden_tool_messages = 0
        show_ts = bool(getattr(self, "show_timestamps", False))

        def _ts_suffix(message: dict) -> str:
            # Messages restored from SessionDB carry a unix `timestamp`; live
            # unsaved turns may not. Only annotate when both the toggle is on
            # and the turn actually has a stored time — never fabricate one.
            if not show_ts:
                return ""
            ts = message.get("timestamp")
            if not ts:
                return ""
            try:
                from datetime import datetime
                return f"  [{datetime.fromtimestamp(float(ts)).strftime('%H:%M')}]"
            except (ValueError, OSError, TypeError):
                return ""

        def flush_tool_summary():
            nonlocal hidden_tool_messages
            if not hidden_tool_messages:
                return

            noun = "message" if hidden_tool_messages == 1 else "messages"
            print("\n  [Tools]")
            print(f"    ({hidden_tool_messages} tool {noun} hidden)")
            hidden_tool_messages = 0

        print()
        print("+" + "-" * 50 + "+")
        print("|" + " " * 12 + "(^_^) Conversation History" + " " * 11 + "|")
        print("+" + "-" * 50 + "+")

        for msg in self.conversation_history:
            role = msg.get("role", "unknown")

            if role == "tool":
                hidden_tool_messages += 1
                continue

            if role not in {"user", "assistant"}:
                continue

            flush_tool_summary()
            visible_index += 1

            content = msg.get("content")
            content_text = "" if content is None else str(content)

            if role == "user":
                print(f"\n  [You #{visible_index}]{_ts_suffix(msg)}")
                print(
                    f"    {content_text[:preview_limit]}{'...' if len(content_text) > preview_limit else ''}"
                )
                continue

            print(f"\n  [Hermes #{visible_index}]{_ts_suffix(msg)}")
            tool_calls = msg.get("tool_calls") or []
            if content_text:
                preview = content_text[:preview_limit]
                suffix = "..." if len(content_text) > preview_limit else ""
            elif tool_calls:
                tool_count = len(tool_calls)
                noun = "call" if tool_count == 1 else "calls"
                preview = f"(requested {tool_count} tool {noun})"
                suffix = ""
            else:
                preview = "(no text response)"
                suffix = ""
            print(f"    {preview}{suffix}")

        flush_tool_summary()
        print()
    
    def _notify_session_boundary(self, event_type: str) -> None:
        """Fire a session-boundary plugin hook (on_session_finalize or on_session_reset).

        Non-blocking — errors are caught and logged.  Safe to call from any
        lifecycle point (shutdown, /new, /reset).
        """
        try:
            from openagents_cli.plugins import invoke_hook as _invoke_hook
            _invoke_hook(
                event_type,
                session_id=self.agent.session_id if self.agent else None,
                platform=getattr(self, "platform", None) or "cli",
                reason="new_session" if event_type == "on_session_reset" else "session_boundary",
            )
        except Exception:
            pass

    def _discard_session_if_empty(self, session_id: Optional[str]) -> bool:
        """Drop a just-ended session row when it never gained content.

        Starting the CLI and immediately quitting (or rotating with /new,
        /clear) used to leave an empty untitled row behind that clutters
        ``/resume`` and ``hermes sessions list``. Delegates the
        check-and-delete to ``SessionDB.delete_session_if_empty``, which
        only removes rows with no messages, no title, and no child
        sessions. Ported from google-gemini/gemini-cli#27770.
        """
        if not self._session_db or not session_id:
            return False
        # In-memory transcript is authoritative: if this CLI object holds
        # conversation messages (flushed to the DB or not), the session is
        # not empty. Protects against pruning a real conversation whose DB
        # flush failed or hasn't happened yet.
        if getattr(self, "conversation_history", None):
            return False
        try:
            from openagents_constants import get_openagents_home as _ghh
            return self._session_db.delete_session_if_empty(
                session_id, sessions_dir=_ghh() / "sessions"
            )
        except Exception:
            logger.debug(
                "Could not prune empty session %s", session_id, exc_info=True
            )
            return False

    def new_session(self, silent=False, title=None):
        """Start a fresh session with a new session ID and cleared agent state."""
        if self.agent and self.conversation_history:
            # Trigger memory extraction on the old session before session_id rotates.
            self.agent.commit_memory_session(self.conversation_history)
            self._notify_session_boundary("on_session_finalize")
        elif self.agent:
            # First session or empty history — still finalize the old session
            self._notify_session_boundary("on_session_finalize")

        old_session_id = self.session_id
        if self._session_db and old_session_id:
            # Flush any un-persisted messages from the current turn to the
            # old session *before* rotating.  /new can be called mid-turn
            # when _flush_messages_to_session_db() has not yet run — without
            # this, messages generated during the current turn are silently
            # lost on session rotation (#47202).
            if self.agent:
                try:
                    self.agent._flush_messages_to_session_db(
                        self.conversation_history
                    )
                except Exception:
                    pass  # best-effort
            try:
                self._session_db.end_session(old_session_id, "new_session")
            except Exception:
                pass
            # Don't let immediately-rotated empty sessions pile up in
            # /resume and `hermes sessions list` (gemini-cli#27770 port).
            self._discard_session_if_empty(old_session_id)

        self.session_start = datetime.now()
        timestamp_str = self.session_start.strftime("%Y%m%d_%H%M%S")
        short_uuid = uuid.uuid4().hex[:6]
        self.session_id = f"{timestamp_str}_{short_uuid}"
        self.conversation_history = []
        self._pending_title = None
        self._resumed = False
        _sync_process_session_id(self.session_id)

        if self.agent:
            self.agent.session_id = self.session_id
            self.agent.session_start = self.session_start
            self.agent.reset_session_state()
            if hasattr(self.agent, "_last_flushed_db_idx"):
                self.agent._last_flushed_db_idx = 0
            if hasattr(self.agent, "_todo_store"):
                try:
                    from tools.todo_tool import TodoStore
                    self.agent._todo_store = TodoStore()
                except Exception:
                    pass
            if hasattr(self.agent, "_invalidate_system_prompt"):
                self.agent._invalidate_system_prompt()

            if self._session_db:
                try:
                    self.agent._session_db_created = False
                    self._session_db.create_session(
                        session_id=self.session_id,
                        source=os.environ.get("HERMES_SESSION_SOURCE", "cli"),
                        model=self.model,
                        model_config={
                            "max_iterations": self.max_turns,
                            "reasoning_config": self.reasoning_config,
                        },
                    )
                    self.agent._session_db_created = True
                except Exception:
                    pass
                if title and self._session_db:
                    from openagents_state import SessionDB
                    try:
                        sanitized = SessionDB.sanitize_title(title)
                    except ValueError as e:
                        _cprint(f"  Title rejected: {e}")
                        sanitized = None
                        title = None
                    if sanitized:
                        try:
                            self._session_db.set_session_title(self.session_id, sanitized)
                            self._pending_title = None
                            title = sanitized
                        except ValueError as e:
                            _cprint(f"  {e} — session started untitled.")
                            title = None
                        except Exception:
                            title = None
                    elif title is not None:
                        # sanitize_title returned empty (whitespace-only / unprintable)
                        _cprint("  Title is empty after cleanup — session started untitled.")
                        title = None
            # Notify memory providers that session_id rotated to a fresh
            # conversation. reset=True signals providers to flush accumulated
            # per-session state (_session_turns, _turn_counter, _document_id).
            # Fires BEFORE the plugin on_session_reset hook (shell hooks only
            # see the new id; Python providers see the transition). See #6672.
            try:
                _mm = getattr(self.agent, "_memory_manager", None)
                if _mm is not None:
                    _mm.on_session_switch(
                        self.session_id,
                        parent_session_id=old_session_id or "",
                        reset=True,
                        reason="new_session",
                    )
            except Exception:
                pass
            self._notify_session_boundary("on_session_reset")

        if not silent:
            if title:
                print(f"(^_^)v New session started: {title}")
            else:
                print("(^_^)v New session started!")



    def _consume_pending_resume_selection(self, text: str) -> bool:
        """Resolve a bare numeric reply that follows a bare ``/resume`` prompt.

        After ``/resume`` (no args) prints the recent-sessions list it arms
        ``self._pending_resume_sessions``. The next submitted input is given
        one chance to be a bare session number (``3``); if so we resume that
        session here. Anything else (another command, free text, blank) simply
        disarms the prompt and is handled normally by the caller.

        Returns True if the input was consumed as a resume selection (caller
        must not treat it as chat); False otherwise. The pending state is
        always one-shot: it is cleared on the first submitted input regardless
        of outcome. See #34584.
        """
        pending = self._pending_resume_sessions
        if not pending:
            return False
        # One-shot: disarm now so a non-matching input can't leave the prompt
        # armed and hijack a later number the user meant as chat.
        self._pending_resume_sessions = None

        if not isinstance(text, str):
            return False
        stripped = text.strip()
        # Only a pure number selects; let "/resume 3", titles, or any other
        # text fall through to normal handling.
        if not stripped.isdigit():
            return False

        index = int(stripped)
        if index < 1 or index > len(pending):
            _cprint(f"  Resume index {index} is out of range.")
            _cprint("  Use /resume with no arguments to see available sessions.")
            return True

        self._handle_resume_command(f"/resume {index}")
        return True



    def save_conversation(self):
        """Save the current conversation to a JSON snapshot under ~/.openagents/sessions/saved/.

        The snapshot is a convenience export for sharing or off-line inspection;
        every message is already persisted incrementally to the SQLite session
        DB, so the live session remains resumable via ``hermes --resume <id>``
        regardless of whether the user ever runs ``/save``.
        """
        if not self.conversation_history:
            print("(;_;) No conversation to save.")
            return

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        saved_dir = get_openagents_home() / "sessions" / "saved"
        try:
            saved_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            print(f"(x_x) Failed to create save directory {saved_dir}: {e}")
            return
        path = saved_dir / f"hermes_conversation_{timestamp}.json"

        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump({
                    "model": self.model,
                    "session_id": self.session_id,
                    "session_start": self.session_start.isoformat(),
                    "messages": self.conversation_history,
                }, f, indent=2, ensure_ascii=False)
            print(f"(^_^)v Conversation snapshot saved to: {path}")
            if self.session_id:
                print(f"       Resume the live session with: hermes --resume {self.session_id}")
        except Exception as e:
            print(f"(x_x) Failed to save: {e}")
    
    def retry_last(self):
        """Retry the last user message by removing the last exchange and re-sending.
        
        Removes the last assistant response (and any tool-call messages) and
        the last user message, then re-sends that user message to the agent.
        Returns the message to re-send, or None if there's nothing to retry.
        """
        if not self.conversation_history:
            print("(._.) No messages to retry.")
            return None
        
        # Walk backwards to find the last user message
        last_user_idx = None
        for i in range(len(self.conversation_history) - 1, -1, -1):
            if self.conversation_history[i].get("role") == "user":
                last_user_idx = i
                break
        
        if last_user_idx is None:
            print("(._.) No user message found to retry.")
            return None
        
        # Extract the message text and remove everything from that point forward
        last_message = self.conversation_history[last_user_idx].get("content", "")
        self.conversation_history = self.conversation_history[:last_user_idx]
        
        print(f"(^_^)b Retrying: \"{last_message[:60]}{'...' if len(last_message) > 60 else ''}\"")
        return last_message
    
    def undo_last(self, n: int = 1, prefill: bool = True):
        """Back up N user turns: truncate history, soft-delete on disk, prefill.

        Walks backwards N user messages and discards everything from the
        Nth-from-last user message onward (its assistant response, tool
        calls, etc.). ``n`` defaults to 1 (the last exchange); ``/undo 3``
        backs up three user turns. If ``n`` exceeds the number of user
        turns, it backs up to the oldest one.

        Beyond the in-memory ``conversation_history`` slice, this also:
          • soft-deletes the truncated rows in SessionDB (``active=0``) so
            they're hidden from re-prompts and search but kept for audit;
          • notifies memory providers via ``on_session_switch(rewound=True)``;
          • mirrors /branch's agent surgery (system-prompt invalidation +
            flush-index reset);
          • when ``prefill`` is set and an input buffer is available,
            pre-fills the composer with the backed-up message text so it
            can be edited and resubmitted.

        ``prefill=False`` is used by callers that drive the undo
        programmatically (e.g. checkpoint rollback) and don't want to
        touch the user's input buffer.
        """
        if not self.conversation_history:
            print("(._.) No messages to undo.")
            return

        if n < 1:
            n = 1

        # Walk backwards collecting the indices of the last N user messages.
        user_indices = []
        for i in range(len(self.conversation_history) - 1, -1, -1):
            if self.conversation_history[i].get("role") == "user":
                user_indices.append(i)
                if len(user_indices) >= n:
                    break

        if not user_indices:
            print("(._.) No user message found to undo.")
            return

        # The oldest of the collected user messages is our truncation point.
        cut_idx = user_indices[-1]
        turns_undone = len(user_indices)

        removed_count = len(self.conversation_history) - cut_idx
        removed_msg = self.conversation_history[cut_idx].get("content", "")
        removed_text = self._undo_content_to_text(removed_msg)

        # Truncate the in-memory history to before that user message.
        self.conversation_history = self.conversation_history[:cut_idx]

        # Soft-delete the truncated rows on disk so re-prompts and search
        # see the clean transcript while the rows survive for audit.
        rewound_rows = 0
        if self._session_db is not None and self.session_id:
            try:
                recents = self._session_db.list_recent_user_messages(
                    self.session_id, limit=max(turns_undone, 10)
                )
                if recents:
                    target_idx = min(turns_undone - 1, len(recents) - 1)
                    target_id = recents[target_idx]["id"]
                    result = self._session_db.rewind_to_message(
                        self.session_id, target_id
                    )
                    rewound_rows = result.get("rewound_count", 0)
                    # Prefer the DB's decoded target text for the prefill —
                    # it's the canonical persisted copy.
                    db_text = self._undo_content_to_text(
                        (result.get("target_message") or {}).get("content")
                    )
                    if db_text:
                        removed_text = db_text
            except ValueError as e:
                # Non-user target / cross-session — keep the in-memory undo
                # but skip the soft-delete; surface a debug-level note.
                logger.debug("undo: soft-delete skipped: %s", e)
            except Exception as e:
                logger.debug("undo: soft-delete failed: %s", e)

        # Agent surgery: invalidate the system-prompt cache and reset the
        # flush index so the next turn re-flushes from the truncated head.
        if self.agent is not None:
            if hasattr(self.agent, "_invalidate_system_prompt"):
                try:
                    self.agent._invalidate_system_prompt()
                except Exception:
                    pass
            if hasattr(self.agent, "_last_flushed_db_idx"):
                try:
                    self.agent._last_flushed_db_idx = len(self.conversation_history)
                except Exception:
                    pass
            # Notify memory providers — same hook /branch fires, with the
            # rewound flag so per-turn document caches invalidate (#6672, #21910).
            try:
                _mm = getattr(self.agent, "_memory_manager", None)
                if _mm is not None and self.session_id:
                    _mm.on_session_switch(
                        self.session_id,
                        parent_session_id="",
                        reset=False,
                        rewound=True,
                    )
            except Exception:
                pass

        turn_word = "turn" if turns_undone == 1 else "turns"
        msg_count = rewound_rows or removed_count
        print(
            f"(^_^)b Undid {turns_undone} {turn_word} ({msg_count} message(s)). "
            f"Backed up to: \"{removed_text[:60]}{'...' if len(removed_text) > 60 else ''}\""
        )
        remaining = len(self.conversation_history)
        print(f"  {remaining} message(s) remaining in history.")

        # Pre-fill the composer with the backed-up message so the user can
        # edit and resubmit (Claude-Code-style). Editable, not auto-sent.
        if prefill and removed_text:
            self._prefill_input_buffer(removed_text)

    @staticmethod
    def _undo_content_to_text(content) -> str:
        """Flatten message content (str or content-part list) to plain text."""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = [
                p.get("text", "")
                for p in content
                if isinstance(p, dict) and p.get("type") == "text"
            ]
            return "\n".join(t for t in parts if t)
        return ""

    def _prefill_input_buffer(self, text: str) -> None:
        """Place ``text`` in the active prompt_toolkit buffer, editable."""
        app = getattr(self, "_app", None)
        if app is None:
            return
        try:
            buf = app.current_buffer
            buf.text = text
            if hasattr(buf, "cursor_position"):
                buf.cursor_position = len(text)
            app.invalidate()
        except Exception as e:
            logger.debug("undo: prefill buffer failed: %s", e)
    
    def _run_curses_picker(self, title: str, items: list[str], default_index: int = 0) -> int | None:
        """Run curses_single_select via run_in_terminal so prompt_toolkit handles terminal ownership cleanly."""
        import threading
        from openagents_cli.curses_ui import curses_single_select

        result = [None]

        def _pick():
            result[0] = curses_single_select(title, items, default_index=default_index)

        # run_in_terminal requires an asyncio event loop — only exists in the
        # main prompt_toolkit thread.  If we're in a background thread (e.g.
        # process_loop), fall back to direct curses call.
        in_main_thread = threading.current_thread() is threading.main_thread()

        if self._app and in_main_thread:
            from prompt_toolkit.application import run_in_terminal
            was_visible = self._status_bar_visible
            self._status_bar_visible = False
            self._app.invalidate()
            try:
                run_in_terminal(_pick)
            finally:
                self._status_bar_visible = was_visible
                self._app.invalidate()
        else:
            _pick()

        return result[0]

    def _prompt_text_input(self, prompt_text: str) -> str | None:
        """Prompt for free-text input safely inside or outside prompt_toolkit.

        Mirrors the thread-aware guard in ``_run_curses_picker``: ``run_in_terminal``
        returns a coroutine that must be awaited by the prompt_toolkit event loop,
        which only exists on the main thread.  Slash commands are dispatched from
        the ``process_loop`` daemon thread (see issue #23185), so calling
        ``run_in_terminal`` from there orphans the coroutine — ``_ask`` never runs,
        and user keystrokes leak into the composer instead.  Fall back to a direct
        ``input()`` when we're off the main thread.
        """
        import threading
        result = [None]

        def _ask():
            try:
                result[0] = input(prompt_text).strip() or None
            except (KeyboardInterrupt, EOFError):
                pass

        in_main_thread = threading.current_thread() is threading.main_thread()

        # Slash-worker guard (#23185 / billing auto-reload hang): when a
        # prompt_toolkit app is running but we're on a non-main thread (the
        # process_loop / TUI slash-worker daemon thread), stdin is owned by the
        # event loop / JSON-RPC pipe.  A bare input() there blocks forever until
        # the worker's 45s timeout fires.  We cannot safely prompt off the main
        # thread, so cancel cleanly (None) instead of hanging — mirrors the
        # _stdin_fallback discipline in _prompt_text_input_modal.
        if self._app and not in_main_thread:
            self._invalidate()
            return None

        if self._app and in_main_thread:
            from prompt_toolkit.application import run_in_terminal
            was_visible = self._status_bar_visible
            self._status_bar_visible = False
            self._app.invalidate()
            try:
                run_in_terminal(_ask)
            except Exception:
                # WSL / Warp / certain terminal emulators silently drop the
                # scheduled coroutine.  Fall back to a direct input() so the
                # user's keystrokes don't leak into the agent buffer.
                try:
                    _ask()
                except Exception:
                    pass
            finally:
                self._status_bar_visible = was_visible
                self._app.invalidate()
        else:
            _ask()
        return result[0]

    def _prompt_text_input_modal(
        self,
        *,
        title: str,
        detail: str,
        choices: list[tuple[str, str, str]],
        timeout: float = 120,
    ) -> str | None:
        """Prompt through the prompt_toolkit composer instead of raw input().

        This is for CLI slash-command confirmations.  The old raw input() path
        fought prompt_toolkit's active stdin ownership: in some terminals the
        prompt appeared above the TUI, choices were redrawn later, and Enter
        could be interpreted as EOF/exit.  A first-class modal state keeps the
        choices visible and lets the normal Enter key binding submit the typed
        or highlighted choice.

        **Platform note (Windows — issue #33961):**
        Earlier code bypassed the modal on ``sys.platform == "win32"`` and fell
        back to a raw ``input()`` prompt.  When the confirm was triggered from the
        ``process_loop`` daemon thread (the normal case) that ``input()`` ran off
        the main thread and deadlocked against prompt_toolkit's stdin ownership —
        the user saw a frozen cursor and Ctrl-C was swallowed (bare ``/reset``
        froze; ``/reset now`` worked only because it skips the prompt entirely).

        Native Windows now uses the same path as Linux/macOS: the modal is set up
        on ``self._app.loop`` via ``call_soon_threadsafe`` and answered by the
        normal prompt_toolkit key bindings (the same input channel that already
        handles ordinary typing on Windows).  The raw ``input()`` fallback is kept
        only for the genuinely safe cases: no running app (unit tests /
        non-interactive), no resolvable event loop, or a scheduling failure.
        """
        import threading
        import time as _time

        if not choices:
            return None

        # If prompt_toolkit is not running (unit tests / non-interactive calls),
        # keep the simple stdin fallback.
        if not getattr(self, "_app", None):
            return self._prompt_text_input("Choice [1/2/3]: ")

        try:
            app_loop = self._app.loop
        except Exception:
            app_loop = None

        in_main_thread = threading.current_thread() is threading.main_thread()

        def _stdin_fallback() -> str | None:
            # On native Windows a raw input() from a non-main thread deadlocks
            # against prompt_toolkit's stdin ownership (#33961).  With an app
            # running we cannot safely prompt off the main thread, so cancel
            # cleanly (None) rather than hang the terminal.
            if sys.platform == "win32" and not in_main_thread:
                self._invalidate()
                return None
            return self._prompt_text_input("Choice [1/2/3]: ")

        if not in_main_thread and app_loop is None:
            return _stdin_fallback()

        response_queue = queue.Queue()

        def _setup_modal() -> None:
            self._capture_modal_input_snapshot()
            self._slash_confirm_state = {
                "title": title,
                "detail": detail,
                "choices": choices,
                "selected": 0,
                "response_queue": response_queue,
            }
            self._slash_confirm_deadline = _time.monotonic() + timeout
            self._invalidate()

        def _teardown_modal() -> None:
            self._slash_confirm_state = None
            self._slash_confirm_deadline = 0
            self._restore_modal_input_snapshot()
            self._invalidate()

        def _run_on_app_loop(fn) -> bool:
            if in_main_thread or app_loop is None:
                fn()
                return True
            ready = threading.Event()

            def _wrapped() -> None:
                try:
                    fn()
                finally:
                    ready.set()

            try:
                app_loop.call_soon_threadsafe(_wrapped)
            except Exception:
                return False
            return ready.wait(timeout=5)

        if not _run_on_app_loop(_setup_modal):
            return _stdin_fallback()

        _last_countdown_refresh = _time.monotonic()
        try:
            while True:
                try:
                    result = response_queue.get(timeout=1)
                    _run_on_app_loop(_teardown_modal)
                    return result
                except queue.Empty:
                    remaining = self._slash_confirm_deadline - _time.monotonic()
                    if remaining <= 0:
                        break
                    now = _time.monotonic()
                    if now - _last_countdown_refresh >= 5.0:
                        _last_countdown_refresh = now
                        self._invalidate()
        finally:
            if self._slash_confirm_state is not None:
                _run_on_app_loop(_teardown_modal)
        return None

    def _submit_slash_confirm_response(self, value: str | None) -> None:
        state = self._slash_confirm_state
        if not state:
            return
        state["response_queue"].put(value)
        self._slash_confirm_state = None
        self._slash_confirm_deadline = 0
        self._invalidate()

    def _normalize_slash_confirm_choice(
        self,
        raw: str | None,
        choices: list[tuple[str, str, str]],
    ) -> str | None:
        if raw is None:
            return None
        choice_raw = raw.strip().lower()
        if not choice_raw:
            return None
        aliases = {
            "1": "once",
            "once": "once",
            "approve": "once",
            "yes": "once",
            "y": "once",
            "ok": "once",
            "2": "always",
            "always": "always",
            "remember": "always",
            "3": "cancel",
            "cancel": "cancel",
            "nevermind": "cancel",
            "no": "cancel",
            "n": "cancel",
        }
        allowed = {choice[0] for choice in choices}
        normalized = aliases.get(choice_raw)
        if normalized in allowed:
            return normalized
        if choice_raw in allowed:
            return choice_raw
        return None

    def _get_slash_confirm_display_fragments(self):
        """Render the /new-/clear-style confirmation panel."""
        state = self._slash_confirm_state
        if not state:
            return []

        title = state.get("title") or "Confirm action"
        detail = state.get("detail") or ""
        choices = state.get("choices") or []
        selected = state.get("selected", 0)

        def _panel_box_width(title_text: str, content_lines: list[str], min_width: int = 56, max_width: int = 86) -> int:
            term_cols = shutil.get_terminal_size((100, 20)).columns
            longest = max([len(title_text)] + [len(line) for line in content_lines] + [min_width - 4])
            inner = min(max(longest + 4, min_width - 2), max_width - 2, max(24, term_cols - 6))
            return inner + 2

        def _wrap_panel_text(text: str, width: int, subsequent_indent: str = "") -> list[str]:
            wrapped = textwrap.wrap(
                text,
                width=max(8, width),
                replace_whitespace=False,
                drop_whitespace=False,
                subsequent_indent=subsequent_indent,
            )
            return wrapped or [""]

        def _append_panel_line(lines, border_style: str, content_style: str, text: str, box_width: int) -> None:
            inner_width = max(0, box_width - 2)
            lines.append((border_style, "│ "))
            lines.append((content_style, text.ljust(inner_width)))
            lines.append((border_style, " │\n"))

        def _append_blank_panel_line(lines, border_style: str, box_width: int) -> None:
            lines.append((border_style, "│" + (" " * box_width) + "│\n"))

        preview_lines = []
        for line in detail.splitlines():
            preview_lines.extend(_wrap_panel_text(line, 72))
        for idx, (_value, label, desc) in enumerate(choices):
            marker = "❯" if idx == selected else " "
            preview_lines.extend(_wrap_panel_text(f"{marker} [{idx + 1}] {label} — {desc}", 72, subsequent_indent="    "))
        preview_lines.append("Type 1/2/3 or use ↑/↓ then Enter. ESC/Ctrl+C cancels.")

        box_width = _panel_box_width(title, preview_lines)
        inner_text_width = max(8, box_width - 2)
        detail_wrapped = []
        for line in detail.splitlines():
            detail_wrapped.extend(_wrap_panel_text(line, inner_text_width))
        choice_wrapped: list[tuple[int, str]] = []
        for idx, (_value, label, desc) in enumerate(choices):
            marker = "❯" if idx == selected else " "
            for wrapped in _wrap_panel_text(f"{marker} [{idx + 1}] {label} — {desc}", inner_text_width, subsequent_indent="    "):
                choice_wrapped.append((idx, wrapped))

        term_rows = shutil.get_terminal_size((100, 24)).lines
        reserved_below = 6
        chrome_full = 6
        available = max(0, term_rows - reserved_below)
        max_detail_rows = max(1, available - chrome_full - len(choice_wrapped))
        max_detail_rows = min(max_detail_rows, 8)
        if len(detail_wrapped) > max_detail_rows:
            keep = max(1, max_detail_rows - 1)
            detail_wrapped = detail_wrapped[:keep] + ["… (detail truncated)"]

        lines = []
        lines.append(('class:approval-border', '╭' + ('─' * box_width) + '╮\n'))
        _append_panel_line(lines, 'class:approval-border', 'class:approval-title', title, box_width)
        _append_blank_panel_line(lines, 'class:approval-border', box_width)
        for wrapped in detail_wrapped:
            _append_panel_line(lines, 'class:approval-border', 'class:approval-desc', wrapped, box_width)
        _append_blank_panel_line(lines, 'class:approval-border', box_width)
        for idx, wrapped in choice_wrapped:
            style = 'class:approval-selected' if idx == selected else 'class:approval-choice'
            _append_panel_line(lines, 'class:approval-border', style, wrapped, box_width)
        _append_blank_panel_line(lines, 'class:approval-border', box_width)
        _append_panel_line(lines, 'class:approval-border', 'class:approval-cmd', 'Type 1/2/3 or use ↑/↓ then Enter. ESC/Ctrl+C cancels.', box_width)
        lines.append(('class:approval-border', '╰' + ('─' * box_width) + '╯\n'))
        return lines

    def _open_model_picker(self, providers: list, current_model: str, current_provider: str, user_provs=None, custom_provs=None) -> None:
        """Open prompt_toolkit-native /model picker modal."""
        self._capture_modal_input_snapshot()
        default_idx = next((i for i, p in enumerate(providers) if p.get("is_current")), 0)
        self._model_picker_state = {
            "stage": "provider",
            "providers": providers,
            "selected": default_idx,
            "current_model": current_model,
            "current_provider": current_provider,
            "user_provs": user_provs,
            "custom_provs": custom_provs,
        }
        self._invalidate(min_interval=0.0)

    def _confirm_expensive_model_switch(self, result) -> bool:
        """Ask for explicit confirmation before applying costly model switches."""
        if not getattr(result, "success", False):
            return True
        try:
            from openagents_cli.model_cost_guard import expensive_model_warning

            warning = expensive_model_warning(
                result.new_model,
                provider=result.target_provider,
                base_url=result.base_url or self.base_url or "",
                api_key=result.api_key or self.api_key or "",
                model_info=result.model_info,
            )
        except Exception:
            warning = None
        if warning is None:
            return True

        choices = [
            ("once", "Switch anyway", "Use this model for the current OpenAgents session."),
            ("cancel", "Cancel", "Keep the current model."),
        ]
        raw = self._prompt_text_input_modal(
            title="!!! Expensive Model Warning !!!",
            detail=warning.message,
            choices=choices,
            timeout=120,
        )
        choice = self._normalize_slash_confirm_choice(raw, choices)
        return choice == "once"

    def _confirm_and_apply_model_switch_result(self, result, persist_global: bool) -> None:
        try:
            if result.success and not self._confirm_expensive_model_switch(result):
                _cprint("  Model switch cancelled.")
                return
            self._apply_model_switch_result(result, persist_global)
        except Exception as exc:
            _cprint(f"  ✗ Model selection failed: {exc}")

    def _close_model_picker(self) -> None:
        self._model_picker_state = None
        self._restore_modal_input_snapshot()
        self._invalidate(min_interval=0.0)

    @staticmethod
    def _compute_model_picker_viewport(
        selected: int,
        scroll_offset: int,
        n: int,
        term_rows: int,
        reserved_below: int = 6,
        panel_chrome: int = 6,
        min_visible: int = 3,
    ) -> tuple[int, int]:
        """Resolve (scroll_offset, visible) for the /model picker viewport.

        ``reserved_below`` matches the approval / clarify panels — input area,
        status bar, and separators below the panel. ``panel_chrome`` covers
        this panel's own borders + blanks + hint row. The remaining rows hold
        the scrollable list, with the offset slid to keep ``selected`` on screen.
        """
        max_visible = max(min_visible, term_rows - reserved_below - panel_chrome)
        if n <= max_visible:
            return 0, n
        visible = max_visible
        if selected < scroll_offset:
            scroll_offset = selected
        elif selected >= scroll_offset + visible:
            scroll_offset = selected - visible + 1
        scroll_offset = max(0, min(scroll_offset, n - visible))
        return scroll_offset, visible

    def _apply_model_switch_result(self, result, persist_global: bool) -> None:
        if not result.success:
            _cprint(f"  ✗ {result.error_message}")
            return

        if self.agent is not None:
            try:
                from openagents_cli.context_switch_guard import merge_preflight_compression_warning

                merge_preflight_compression_warning(
                    result,
                    agent=self.agent,
                    messages=list(self.conversation_history or []),
                    config_context_length=getattr(self.agent, "_config_context_length", None),
                )
            except Exception as exc:
                logger.debug("preflight-compression switch warning failed: %s", exc)

        old_model = self.model
        # Snapshot the CLI-level credential/runtime fields BEFORE mutating them
        # so a failed in-place agent swap can roll the whole CLI back to the old
        # working model.  Otherwise the broken credentials staged below leak into
        # the next turn's resolution even though the agent itself rolled back
        # (#50163).
        _cli_snapshot = {
            "model": self.model,
            "provider": self.provider,
            "requested_provider": self.requested_provider,
            "_explicit_api_key": getattr(self, "_explicit_api_key", None),
            "_explicit_base_url": getattr(self, "_explicit_base_url", None),
            "api_key": self.api_key,
            "base_url": self.base_url,
            "api_mode": self.api_mode,
        }
        self.model = result.new_model
        self.provider = result.target_provider
        self.requested_provider = result.target_provider
        # Always overwrite explicit overrides so stale credentials from the
        # previous provider (e.g. Ollama api_key/base_url) don't leak into
        # the new provider's credential resolution on the next turn.
        self._explicit_api_key = result.api_key
        self._explicit_base_url = result.base_url
        if result.api_key:
            self.api_key = result.api_key
        if result.base_url:
            self.base_url = result.base_url
        if result.api_mode:
            self.api_mode = result.api_mode

        if self.agent is not None:
            try:
                self.agent.switch_model(
                    new_model=result.new_model,
                    new_provider=result.target_provider,
                    api_key=result.api_key,
                    base_url=result.base_url,
                    api_mode=result.api_mode,
                )
            except Exception as exc:
                # The agent rolled itself back to the old working model/client.
                # Roll the CLI's own staged fields back too and abort the rest
                # of the commit (note + success print) so a failed switch is a
                # no-op rather than a dead session (#50163).
                for _k, _v in _cli_snapshot.items():
                    setattr(self, _k, _v)
                _cprint(
                    f"  ⚠ Model switch to {result.new_model} failed ({exc}); "
                    f"staying on {old_model}."
                )
                return

        self._pending_model_switch_note = (
            f"[Note: model was just switched from {old_model} to {result.new_model} "
            f"via {result.provider_label or result.target_provider}. "
            f"Adjust your self-identification accordingly.]"
        )

        provider_label = result.provider_label or result.target_provider
        _cprint(f"  ✓ Model switched: {result.new_model}")
        _cprint(f"    Provider: {provider_label}")

        # Context: always resolve via the provider-aware chain so Codex OAuth,
        # Copilot, and Nous-enforced caps win over the raw models.dev entry
        # (e.g. gpt-5.5 is 1.05M on openai but 272K on Codex OAuth).
        mi = result.model_info
        try:
            from openagents_cli.model_switch import resolve_display_context_length
            ctx = resolve_display_context_length(
                result.new_model,
                result.target_provider,
                base_url=result.base_url or self.base_url or "",
                api_key=result.api_key or self.api_key or "",
                model_info=mi,
                config_context_length=getattr(self.agent, "_config_context_length", None) if self.agent else None,
            )
            if ctx:
                _cprint(f"    Context: {ctx:,} tokens")
        except Exception:
            pass
        if mi:
            if mi.max_output:
                _cprint(f"    Max output: {mi.max_output:,} tokens")
            _cprint(f"    Capabilities: {mi.format_capabilities()}")

        cache_enabled = (
            (base_url_host_matches(result.base_url or "", "openrouter.ai") and "claude" in result.new_model.lower())
            or result.api_mode == "anthropic_messages"
        )
        if cache_enabled:
            _cprint("    Prompt caching: enabled")
        if result.warning_message:
            _cprint(f"    ⚠ {result.warning_message}")
        if persist_global:
            save_config_value("model.default", result.new_model)
            if result.provider_changed:
                save_config_value("model.provider", result.target_provider)
            _cprint("    Saved to config.yaml (--global)")
        else:
            _cprint("    (session only — add --global to persist)")

    def _handle_model_picker_selection(self, persist_global: bool = False) -> None:
        state = self._model_picker_state
        if not state:
            return
        selected = state.get("selected", 0)
        stage = state.get("stage")
        if stage == "provider":
            providers = state.get("providers") or []
            if selected >= len(providers):
                self._close_model_picker()
                return
            provider_data = providers[selected]
            # Use the curated model list from list_authenticated_providers()
            # (same lists as `hermes model` and gateway pickers).
            # Only fall back to the live provider catalog when the curated
            # list is empty (e.g. user-defined endpoints with no curated list).
            model_list = provider_data.get("models", [])
            if not model_list:
                try:
                    from openagents_cli.models import provider_model_ids
                    live = provider_model_ids(provider_data["slug"])
                    if live:
                        model_list = live
                except Exception:
                    pass
            state["stage"] = "model"
            state["provider_data"] = provider_data
            state["model_list"] = model_list
            state["selected"] = 0
            self._invalidate(min_interval=0.0)
            return
        if stage == "model":
            provider_data = state.get("provider_data") or {}
            model_list = state.get("model_list") or []
            back_idx = len(model_list)
            cancel_idx = len(model_list) + 1
            if selected == back_idx:
                state["stage"] = "provider"
                state["selected"] = next((i for i, p in enumerate(state.get("providers") or []) if p.get("slug") == provider_data.get("slug")), 0)
                self._invalidate(min_interval=0.0)
                return
            if selected >= cancel_idx:
                self._close_model_picker()
                return
            if selected < len(model_list):
                from openagents_cli.model_switch import switch_model
                chosen_model = model_list[selected]
                result = switch_model(
                    raw_input=chosen_model,
                    current_provider=self.provider or "",
                    current_model=self.model or "",
                    current_base_url=self.base_url or "",
                    current_api_key=self.api_key or "",
                    is_global=persist_global,
                    explicit_provider=provider_data.get("slug"),
                    user_providers=state.get("user_provs"),
                    custom_providers=state.get("custom_provs"),
                )
                self._close_model_picker()
                if getattr(self, "_app", None):
                    threading.Thread(
                        target=self._confirm_and_apply_model_switch_result,
                        args=(result, persist_global),
                        daemon=True,
                    ).start()
                else:
                    self._confirm_and_apply_model_switch_result(result, persist_global)
                return
            self._close_model_picker()

    def _handle_model_switch(self, cmd_original: str):
        """Handle /model command — switch model.

        Supports:
          /model                              — show current model + usage hints
          /model <name>                       — switch model (persists by default)
          /model <name> --session             — switch for this session only
          /model <name> --global              — switch and persist (explicit)
          /model <name> --provider <provider> — switch provider + model
          /model --provider <provider>        — switch to provider, auto-detect model

        Persistence defaults to on (``model.persist_switch_by_default`` in
        config.yaml, default True). Use ``--session`` for a one-off switch.
        """
        from openagents_cli.model_switch import (
            switch_model,
            parse_model_flags,
            resolve_persist_behavior,
        )
        from openagents_cli.providers import get_label

        # Parse args from the original command
        parts = cmd_original.split(None, 1)  # split off '/model'
        raw_args = parts[1].strip() if len(parts) > 1 else ""

        # Parse --provider, --global, --session, and --refresh flags
        (
            model_input,
            explicit_provider,
            is_global_flag,
            force_refresh,
            is_session,
        ) = parse_model_flags(raw_args)
        # Resolve the effective persistence once: --session overrides the
        # config-gated default, --global forces persist, otherwise defer to
        # model.persist_switch_by_default (defaults to True so /model survives
        # across sessions).
        persist_global = resolve_persist_behavior(is_global_flag, is_session)

        # --refresh: wipe the on-disk picker cache before building the
        # provider list. Forces a live re-fetch of every authed provider's
        # /v1/models endpoint on this open.
        if force_refresh:
            try:
                from openagents_cli.models import clear_provider_models_cache
                clear_provider_models_cache()
                _cprint("  Cleared model picker cache. Refreshing...")
            except Exception:
                pass

        # Single inventory context — replaces the inline config-slice the
        # dashboard / TUI used to duplicate. Overlay live session state
        # via with_overrides (truthy-only) so empty self.* attrs don't
        # clobber disk config.
        from openagents_cli.inventory import build_models_payload, load_picker_context

        try:
            ctx = load_picker_context().with_overrides(
                current_provider=self.provider or "",
                current_model=self.model or "",
                current_base_url=self.base_url or "",
            )
        except Exception:
            ctx = None

        # switch_model() + _open_model_picker still need the raw provider
        # dicts; ConfigContext is the canonical source for both.
        user_provs = ctx.user_providers if ctx is not None else None
        custom_provs = ctx.custom_providers if ctx is not None else None

        # No args at all: open prompt_toolkit-native picker modal
        if not model_input and not explicit_provider:
            model_display = self.model or "unknown"
            provider_display = get_label(self.provider) if self.provider else "unknown"

            try:
                if ctx is None:
                    raise RuntimeError("inventory context unavailable")
                providers = build_models_payload(ctx)["providers"]
            except Exception:
                providers = []

            if not providers:
                _cprint("  No authenticated providers found.")
                _cprint("")
                _cprint("  /model <name>                        switch model (persists)")
                _cprint("  /model <name> --session              switch for this session only")
                _cprint("  /model --provider <slug>             switch provider")
                _cprint("  /model --refresh                     re-fetch live model lists")
                return

            self._open_model_picker(
                providers,
                model_display,
                provider_display,
                user_provs=user_provs,
                custom_provs=custom_provs,
            )
            return

        # Perform the switch
        result = switch_model(
            raw_input=model_input,
            current_provider=self.provider or "",
            current_model=self.model or "",
            current_base_url=self.base_url or "",
            current_api_key=self.api_key or "",
            is_global=persist_global,
            explicit_provider=explicit_provider,
            user_providers=user_provs,
            custom_providers=custom_provs,
        )

        if not result.success:
            _cprint(f"  ✗ {result.error_message}")
            return

        if self.agent is not None:
            try:
                from openagents_cli.context_switch_guard import merge_preflight_compression_warning

                merge_preflight_compression_warning(
                    result,
                    agent=self.agent,
                    messages=list(self.conversation_history or []),
                    config_context_length=getattr(self.agent, "_config_context_length", None),
                )
            except Exception as exc:
                logger.debug("preflight-compression switch warning failed: %s", exc)

        if not self._confirm_expensive_model_switch(result):
            _cprint("  Model switch cancelled.")
            return

        # Apply to CLI state.
        # Update requested_provider so _ensure_runtime_credentials() doesn't
        # overwrite the switch on the next turn (it re-resolves from this).
        old_model = self.model
        # Snapshot CLI-level fields before mutation so a failed in-place swap
        # rolls the whole CLI back to the old working model (#50163).
        _cli_snapshot = {
            "model": self.model,
            "provider": self.provider,
            "requested_provider": self.requested_provider,
            "_explicit_api_key": getattr(self, "_explicit_api_key", None),
            "_explicit_base_url": getattr(self, "_explicit_base_url", None),
            "api_key": self.api_key,
            "base_url": self.base_url,
            "api_mode": self.api_mode,
        }
        self.model = result.new_model
        self.provider = result.target_provider
        self.requested_provider = result.target_provider
        # Always overwrite explicit overrides so stale credentials from the
        # previous provider (e.g. Ollama api_key/base_url) don't leak into
        # the new provider's credential resolution on the next turn.
        self._explicit_api_key = result.api_key
        self._explicit_base_url = result.base_url
        if result.api_key:
            self.api_key = result.api_key
        if result.base_url:
            self.base_url = result.base_url
        if result.api_mode:
            self.api_mode = result.api_mode

        # Apply to running agent (in-place swap)
        if self.agent is not None:
            try:
                self.agent.switch_model(
                    new_model=result.new_model,
                    new_provider=result.target_provider,
                    api_key=result.api_key,
                    base_url=result.base_url,
                    api_mode=result.api_mode,
                )
            except Exception as exc:
                # Agent rolled itself back; roll the CLI back too and abort so a
                # failed switch is a no-op rather than a dead session (#50163).
                for _k, _v in _cli_snapshot.items():
                    setattr(self, _k, _v)
                _cprint(
                    f"  ⚠ Model switch to {result.new_model} failed ({exc}); "
                    f"staying on {old_model}."
                )
                return

        # Store a note to prepend to the next user message so the model
        # knows a switch occurred (avoids injecting system messages mid-history
        # which breaks providers and prompt caching).
        self._pending_model_switch_note = (
            f"[Note: model was just switched from {old_model} to {result.new_model} "
            f"via {result.provider_label or result.target_provider}. "
            f"Adjust your self-identification accordingly.]"
        )

        # Display confirmation with full metadata
        provider_label = result.provider_label or result.target_provider
        _cprint(f"  ✓ Model switched: {result.new_model}")
        _cprint(f"    Provider: {provider_label}")

        # Context: always resolve via the provider-aware chain so Codex OAuth,
        # Copilot, and Nous-enforced caps win over the raw models.dev entry
        # (e.g. gpt-5.5 is 1.05M on openai but 272K on Codex OAuth).
        mi = result.model_info
        from openagents_cli.model_switch import resolve_display_context_length
        ctx = resolve_display_context_length(
            result.new_model,
            result.target_provider,
            base_url=result.base_url or self.base_url or "",
            api_key=result.api_key or self.api_key or "",
            model_info=mi,
            config_context_length=getattr(self.agent, "_config_context_length", None) if self.agent else None,
        )
        if ctx:
            _cprint(f"    Context: {ctx:,} tokens")
        if mi:
            if mi.max_output:
                _cprint(f"    Max output: {mi.max_output:,} tokens")
            _cprint(f"    Capabilities: {mi.format_capabilities()}")

        # Cache notice
        cache_enabled = (
            (base_url_host_matches(result.base_url or "", "openrouter.ai") and "claude" in result.new_model.lower())
            or result.api_mode == "anthropic_messages"
        )
        if cache_enabled:
            _cprint("    Prompt caching: enabled")

        # Warning from validation
        if result.warning_message:
            _cprint(f"    ⚠ {result.warning_message}")

        # Persistence
        if persist_global:
            save_config_value("model.default", result.new_model)
            if result.provider_changed:
                save_config_value("model.provider", result.target_provider)
            _cprint("    Saved to config.yaml")
        else:
            _cprint("    (session only — add --global to persist)")

    def _handle_codex_runtime(self, cmd_original: str) -> None:
        """Handle /codex-runtime — toggle the codex app-server runtime opt-in.

        Usage:
            /codex-runtime                       — show current state
            /codex-runtime auto                  — OpenAgents default (chat_completions)
            /codex-runtime codex_app_server      — hand turns to codex subprocess
            /codex-runtime on / off              — synonyms for the above
        """
        from openagents_cli import codex_runtime_switch as crs

        parts = cmd_original.split(None, 1)
        raw_args = parts[1].strip() if len(parts) > 1 else ""
        new_value, errors = crs.parse_args(raw_args)
        if errors:
            for err in errors:
                _cprint(f"❌ {err}")
            return

        # Load + persist via the existing config helpers
        try:
            from openagents_cli.config import load_config, save_config
        except Exception as exc:
            _cprint(f"❌ could not load config: {exc}")
            return
        cfg = load_config()

        result = crs.apply(
            cfg,
            new_value,
            persist_callback=(save_config if new_value is not None else None),
        )

        prefix = "✓" if result.success else "✗"
        for line in result.message.splitlines():
            _cprint(f"  {prefix} {line}" if line.startswith("openai_runtime")
                    else f"    {line}")
        if result.success and result.requires_new_session:
            _cprint("    Tip: `/reset` starts a new session immediately.")

    def _should_handle_model_command_inline(self, text: str, has_images: bool = False) -> bool:
        """Return True when /model should be handled immediately on the UI thread."""
        if not text or has_images or not _looks_like_slash_command(text):
            return False
        try:
            from openagents_cli.commands import resolve_command
            base = text.split(None, 1)[0].lower().lstrip('/')
            cmd = resolve_command(base)
            return bool(cmd and cmd.name == "model")
        except Exception:
            return False

    def _should_handle_steer_command_inline(self, text: str, has_images: bool = False) -> bool:
        """Return True when /steer should be dispatched immediately while the agent is running.

        /steer MUST bypass the normal _pending_input → process_loop path when
        the agent is active, because process_loop is blocked inside
        self.chat() for the duration of the run.  By the time the queued
        command is pulled from _pending_input, _agent_running has already
        flipped back to False, and process_command() takes the idle
        fallback — delivering the steer as a next-turn message instead of
        injecting it mid-run.  Dispatching inline on the UI thread calls
        agent.steer() directly, which is thread-safe (uses _pending_steer_lock).
        """
        if not text or has_images or not _looks_like_slash_command(text):
            return False
        if not getattr(self, "_agent_running", False):
            return False
        try:
            from openagents_cli.commands import resolve_command
            base = text.split(None, 1)[0].lower().lstrip('/')
            cmd = resolve_command(base)
            return bool(cmd and cmd.name == "steer")
        except Exception:
            return False

    def _output_console(self):
        """Use prompt_toolkit-safe Rich rendering once the TUI is live."""
        if getattr(self, "_app", None):
            return ChatConsole()
        return self.console

    def _console_print(self, *args, **kwargs):
        """Print through the active command-safe console."""
        self._output_console().print(*args, **kwargs)

    @staticmethod
    def _resolve_personality_prompt(value) -> str:
        """Accept string or dict personality value; return system prompt string."""
        if isinstance(value, dict):
            parts = [value.get("system_prompt", "")]
            if value.get("tone"):
                parts.append(f'Tone: {value["tone"]}' )
            if value.get("style"):
                parts.append(f'Style: {value["style"]}' )
            return "\n".join(p for p in parts if p)
        return str(value)


    



    def _show_gateway_status(self):
        """Show status of the gateway and connected messaging platforms."""
        from gateway.config import load_gateway_config, Platform
        
        print()
        print("+" + "-" * 60 + "+")
        print("|" + " " * 15 + "(✿◠‿◠) Gateway Status" + " " * 17 + "|")
        print("+" + "-" * 60 + "+")
        print()
        
        try:
            config = load_gateway_config()
            
            print("  Messaging Platform Configuration:")
            print("  " + "-" * 55)
            
            platform_status = {
                Platform.TELEGRAM: ("Telegram", "TELEGRAM_BOT_TOKEN"),
                Platform.DISCORD: ("Discord", "DISCORD_BOT_TOKEN"),
                Platform.SLACK: ("Slack", "SLACK_BOT_TOKEN"),
                Platform.WHATSAPP: ("WhatsApp", "WHATSAPP_ENABLED"),
            }
            
            for platform, (name, env_var) in platform_status.items():
                pconfig = config.platforms.get(platform)
                if pconfig and pconfig.enabled:
                    home = config.get_home_channel(platform)
                    home_str = f" → {home.name}" if home else ""
                    print(f"    ✓ {name:<12} Enabled{home_str}")
                else:
                    print(f"    ○ {name:<12} Not configured ({env_var})")
            
=======
        for name, rows in sections:
>>>>>>> rb/tag
            print()
            print(f"  -- {name} --")
            for label, value in rows:
                print(f"  {label} {value}")
        print()

    # canonical command -> (method name, pass cmd_original?). Absent commands resolve to
    # ``_handle_<name>_command(cmd)``. Looked up via getattr at dispatch time so
    # monkeypatching works. A handler returning False exits the REPL.
    _SLASH_DISPATCH: dict[str, tuple[str, bool]] = {
        "exit": ("_cmd_exit", True), "quit": ("_cmd_exit", True), "help": ("_cmd_help", True),
        "palette": ("_open_command_palette", False), "whoami": ("_handle_whoami_command", False),
        "profile": ("_handle_profile_command", False), "toolsets": ("show_toolsets", False),
        "config": ("show_config", False), "redraw": ("_cmd_redraw", True), "clear": ("_cmd_clear", True),
        "history": ("show_history", False), "title": ("_cmd_title", True), "new": ("_cmd_new", True),
        "model": ("_handle_model_switch", True), "codex-runtime": ("_handle_codex_runtime", True),
        "retry": ("_cmd_retry", True), "prompt": ("_handle_prompt_compose_command", True),
        "undo": ("_cmd_undo", True), "save": ("save_conversation", True), "skills": ("_cmd_skills", True),
        "platforms": ("_show_gateway_status", False), "status": ("_show_session_status", False),
        "context": ("_show_context_breakdown", True), "egress": ("_cmd_egress", True),
        "statusbar": ("_cmd_statusbar", True), "verbose": ("_toggle_verbose", False), "yolo": ("_toggle_yolo", False),
        "compress": ("_manual_compress", True), "subscription": ("_show_subscription", False),
        "topup": ("_show_billing", True), "insights": ("_show_insights", True), "update": ("_cmd_update", True),
        "version": ("_cmd_version", True), "paste": ("_handle_paste_command", False), "reload": ("_cmd_reload", True),
        "reload-mcp": ("_confirm_and_reload_mcp", True), "reload-skills": ("_cmd_reload_skills", True),
        "plugins": ("_cmd_plugins", True), "stop": ("_handle_stop_command", False),
        "agents": ("_handle_agents_command", False), "bg": ("_handle_background_command", True),
        "queue": ("_cmd_queue", True), "steer": ("_cmd_steer", True), "moa": ("_cmd_moa", True),
    }

    @classmethod
    def _slash_handler(cls, canonical: str) -> tuple[str, bool] | None:
        """(method name, pass cmd_original?) for a registered command, else None."""
        entry = cls._SLASH_DISPATCH.get(canonical)
        if entry is None:
            name = f"_handle_{canonical.replace('-', '_')}_command"
            if callable(getattr(cls, name, None)):
                entry = (name, True)
        return entry

    def process_command(self, command: str) -> bool:
        """Dispatch a slash command; returns False to exit the REPL."""
        cmd_lower = command.lower().strip()  # lowercase only for matching; args keep their case
        cmd_original = command.strip()

        # Aliases resolve via the central registry (openagents_cli/commands.py).
        from openagents_cli.commands import resolve_command as _resolve_cmd
        _base_word = cmd_lower.split()[0].lstrip("/")
        _cmd_def = _resolve_cmd(_base_word)
        canonical = _cmd_def.name if _cmd_def else _base_word

        # Observer-only pre_command plugin hook (return values ignored; never raises).
        if _cmd_def is not None:
            from openagents_cli.plugins import fire_pre_command_hook
            fire_pre_command_hook(
                surface="cli", command=canonical, alias_used=_base_word, args_raw=_slash_args(cmd_original),
                session_key=getattr(self, "session_id", None), platform="cli",
            )

        # A bare `/resume` prompt is one-shot: any other command disarms it so a later
        # number isn't swallowed as a stale selection.
        # See #34584.
        if canonical not in {"resume", "sessions"}:
            # Armed when a bare `/resume` prints the recent-sessions list so the very next bare numeric
            # input (e.g. `3`) resolves to that session. Holds the exact list used for index resolution;
            # one-shot (cleared on the next submitted input, whether it's the selection or anything else).
            # See #34584.
            self._pending_resume_sessions = None

        entry = self._slash_handler(canonical)
        if entry is None:
            return self._process_unregistered_slash(cmd_original, cmd_lower)
        method_name, pass_arg = entry
        handler = getattr(self, method_name)
        result = handler(cmd_original) if pass_arg else handler()
        return result is not False

<<<<<<< HEAD
        elif canonical == "personality":
            # Use original case (handler lowercases the personality name itself)
            self._handle_personality_command(cmd_original)
        elif canonical == "pet":
            self._handle_pet_command(cmd_original)

        elif canonical == "hatch":
            self._handle_hatch_command(cmd_original)
        elif canonical == "retry":
            retry_msg = self.retry_last()
            if retry_msg and hasattr(self, '_pending_input'):
                # Re-queue the message so process_loop sends it to the agent
                self._pending_input.put(retry_msg)
        elif canonical == "prompt":
            self._handle_prompt_compose_command(cmd_original)
        elif canonical == "undo":
            # Parse optional turn count: "/undo" → 1, "/undo 3" → 3.
            _undo_n = 1
            _undo_parts = cmd_original.split()
            if len(_undo_parts) > 1:
                try:
                    _undo_n = int(_undo_parts[1])
                except ValueError:
                    print(f"(._.) Invalid count {_undo_parts[1]!r} — use /undo or /undo N.")
                    return
                if _undo_n < 1:
                    _undo_n = 1
            _undo_desc = (
                "This removes the last user/assistant exchange from history."
                if _undo_n == 1
                else f"This removes the last {_undo_n} user turns from history."
            )
            if self._confirm_destructive_slash(
                "undo",
                _undo_desc,
                cmd_original=cmd_original,
            ) is None:
                return True  # confirmation cancelled — command handled, keep REPL alive
            self.undo_last(_undo_n)
        elif canonical == "branch":
            self._handle_branch_command(cmd_original)
        elif canonical == "save":
            self.save_conversation()
        elif canonical == "cron":
            self._handle_cron_command(cmd_original)
        elif canonical == "suggestions":
            self._handle_suggestions_command(cmd_original)
        elif canonical == "company":
            self._handle_company_command(cmd_original)
        elif canonical == "openagentui":
            self._handle_openagentui_command(cmd_original)
        elif canonical == "openagentconfig":
            self._handle_openagentconfig_command(cmd_original)
        elif canonical == "blueprint":
            self._handle_blueprint_command(cmd_original)
        elif canonical == "curator":
            self._handle_curator_command(cmd_original)
        elif canonical == "kanban":
            self._handle_kanban_command(cmd_original)
        elif canonical == "skills":
            with self._busy_command(self._slow_command_status(cmd_original)):
                self._handle_skills_command(cmd_original)
        elif canonical == "learn":
            self._handle_learn_command(cmd_original)
        elif canonical == "memory":
            self._handle_memory_command(cmd_original)
        elif canonical == "platforms":
            self._show_gateway_status()
        elif canonical == "status":
            self._show_session_status()
        elif canonical == "statusbar":
            self._status_bar_visible = not self._status_bar_visible
            state = "visible" if self._status_bar_visible else "hidden"
            self._console_print(f"  Status bar {state}")
        elif canonical == "timestamps":
            self._handle_timestamps_command(cmd_original)
        elif canonical == "verbose":
            self._toggle_verbose()
        elif canonical == "footer":
            self._handle_footer_command(cmd_original)
        elif canonical == "yolo":
            self._toggle_yolo()
        elif canonical == "reasoning":
            self._handle_reasoning_command(cmd_original)
        elif canonical == "fast":
            self._handle_fast_command(cmd_original)
        elif canonical == "compress":
            self._manual_compress(cmd_original)
        elif canonical == "usage":
            self._show_usage()
        elif canonical == "credits":
            self._show_credits()
        elif canonical == "billing":
            self._show_billing(cmd_original)
        elif canonical == "insights":
            self._show_insights(cmd_original)
        elif canonical == "copy":
            self._handle_copy_command(cmd_original)
        elif canonical == "debug":
            self._handle_debug_command()
        elif canonical == "update":
            if self._handle_update_command():
                return False
        elif canonical == "version":
            from openagents_cli.main import _print_version_info

            _print_version_info(check_updates=True)
        elif canonical == "paste":
            self._handle_paste_command()
        elif canonical == "image":
            self._handle_image_command(cmd_original)
        elif canonical == "reload":
            from openagents_cli.config import reload_env
            count = reload_env()
            print(f"  Reloaded .env ({count} var(s) updated)")
        elif canonical == "reload-mcp":
            # Interactive reload: confirm first (unless the user has opted out).
            # The auto-reload path (file watcher) calls _reload_mcp directly
            # without this confirmation.
            self._confirm_and_reload_mcp(cmd_original)
        elif canonical == "reload-skills":
            with self._busy_command(self._slow_command_status(cmd_original)):
                self._reload_skills()
        elif canonical == "bundles":
            self._handle_bundles_command(cmd_original)
        elif canonical == "browser":
            self._handle_browser_command(cmd_original)
        elif canonical == "plugins":
            try:
                # Discover from disk (bundled + user), matching `hermes plugins
                # list` — so installed-but-not-enabled plugins are visible here
                # too. The plugin manager only knows about *loaded* plugins, so
                # using it alone made freshly-installed, not-yet-enabled plugins
                # look like "nothing installed".
                from openagents_cli.plugins_cmd import (
                    _discover_all_plugins,
                    _get_disabled_set,
                    _get_enabled_set,
                    _plugin_status,
                )

                entries = _discover_all_plugins()
                enabled = _get_enabled_set()
                disabled = _get_disabled_set()

                # `/plugins` is a quick glance — default to user-installed
                # plugins (what the user actually added). Bundled provider/
                # platform plugins are summarized on one line; the full
                # catalog lives behind `hermes plugins list`.
                user_entries = [e for e in entries if e[3] != "bundled"]
                bundled_count = len(entries) - len(user_entries)

                if not user_entries:
                    print("No user plugins installed.")
                    print("  Install one: hermes plugins install owner/repo")
                    print(f"  Or drop a plugin directory into {display_openagents_home()}/plugins/")
                    if bundled_count:
                        print(f"  ({bundled_count} bundled plugins available — see: hermes plugins list)")
                else:
                    # Loaded-plugin details (tools/hooks/commands counts, errors)
                    # keyed by name, when available.
                    loaded: dict = {}
                    try:
                        from openagents_cli.plugins import get_plugin_manager
                        for p in get_plugin_manager().list_plugins():
                            loaded[p["name"]] = p
                    except Exception:
                        loaded = {}

                    print(f"User plugins ({len(user_entries)}):")
                    for name, version, _desc, source, _dir, key in sorted(user_entries):
                        state = _plugin_status(name, enabled, disabled, key=key)
                        glyph = {"enabled": "✓", "disabled": "✗"}.get(state, "○")
                        ver = f" v{version}" if version else ""
                        info = loaded.get(name) or {}
                        bits = []
                        if info.get("tools"):
                            bits.append(f"{info['tools']} tools")
                        if info.get("hooks"):
                            bits.append(f"{info['hooks']} hooks")
                        if info.get("commands"):
                            bits.append(f"{info['commands']} commands")
                        detail = f" ({', '.join(bits)})" if bits else ""
                        label = "" if state == "enabled" else f" [{state}]"
                        error = f" — {info['error']}" if info.get("error") else ""
                        print(f"  {glyph} {name}{ver}{label}{detail}{error}")
                    if bundled_count:
                        print(f"  (+{bundled_count} bundled — see: hermes plugins list)")
                    print("  Enable/disable: hermes plugins enable/disable <name>")
            except Exception as e:
                print(f"Plugin system error: {e}")
        elif canonical == "rollback":
            self._handle_rollback_command(cmd_original)
        elif canonical == "snapshot":
            self._handle_snapshot_command(cmd_original)
        elif canonical == "stop":
            self._handle_stop_command()
        elif canonical == "agents":
            self._handle_agents_command()
        elif canonical == "background":
            self._handle_background_command(cmd_original)
        elif canonical == "queue":
            # Extract prompt after "/queue " or "/q "
            parts = cmd_original.split(None, 1)
            payload = parts[1].strip() if len(parts) > 1 else ""
            if not payload:
                _cprint("  Usage: /queue <prompt>")
            else:
                self._pending_input.put(payload)
                if self._agent_running:
                    _cprint(f"  Queued for the next turn: {payload[:80]}{'...' if len(payload) > 80 else ''}")
                else:
                    _cprint(f"  Queued: {payload[:80]}{'...' if len(payload) > 80 else ''}")
        elif canonical == "steer":
            # Inject a message after the next tool call without interrupting.
            # If the agent is actively running, push the text into the agent's
            # pending_steer slot — the drain hook in _execute_tool_calls_*
            # will append it to the next tool result's content. If no agent
            # is running, fall back to queue semantics (same as /queue).
            parts = cmd_original.split(None, 1)
            payload = parts[1].strip() if len(parts) > 1 else ""
            if not payload:
                _cprint("  Usage: /steer <prompt>")
            elif self._agent_running and self.agent is not None and hasattr(self.agent, "steer"):
                try:
                    accepted = self.agent.steer(payload)
                except Exception as exc:
                    _cprint(f"  Steer failed: {exc}")
                else:
                    if accepted:
                        _cprint(f"  ⏩ Steer queued — arrives after the next tool call: {payload[:80]}{'...' if len(payload) > 80 else ''}")
                    else:
                        _cprint("  Steer rejected (empty payload).")
            else:
                # No active run — treat as a normal next-turn message.
                self._pending_input.put(payload)
                _cprint(f"  No agent running; queued as next turn: {payload[:80]}{'...' if len(payload) > 80 else ''}")
        elif canonical == "goal":
            self._handle_goal_command(cmd_original)
        elif canonical == "moa":
            # /moa is one-shot sugar only: run a single prompt through the
            # default MoA preset, then restore the prior model. To *switch* to a
            # MoA preset for the session, pick it from the model picker (MoA
            # presets surface as a virtual "Mixture of Agents" provider).
            from openagents_cli.moa_config import (
                moa_usage,
                normalize_moa_config,
            )

            parts = cmd_original.split(None, 1)
            payload = parts[1].strip() if len(parts) > 1 else ""
            if not payload:
                _cprint(f"  {moa_usage()}")
                return True
            moa_cfg = self.config.get("moa") if isinstance(self.config, dict) else {}
            normalized = normalize_moa_config(moa_cfg)
            preset = normalized["default_preset"]
            self._pending_moa_restore_model = {
                "requested_provider": getattr(self, "requested_provider", None),
                "provider": getattr(self, "provider", None),
                "model": getattr(self, "model", None),
                "api_key": getattr(self, "api_key", None),
                "base_url": getattr(self, "base_url", None),
                "api_mode": getattr(self, "api_mode", None),
            }
            self.requested_provider = "moa"
            self.provider = "moa"
            self.model = preset
            self.api_key = "moa-virtual-provider"
            self.base_url = "moa://local"
            self.api_mode = "chat_completions"
            self.agent = None
            self._pending_moa_disable_after_turn = True
            self._pending_agent_seed = payload
            _cprint(f"  MoA one-shot queued with preset {preset}; previous model will be restored after this turn.")
        elif canonical == "subgoal":
            self._handle_subgoal_command(cmd_original)
        elif canonical == "skin":
            self._handle_skin_command(cmd_original)
        elif canonical == "voice":
            self._handle_voice_command(cmd_original)
        elif canonical == "busy":
            self._handle_busy_command(cmd_original)
||||||| cf299e9a01
        elif canonical == "personality":
            # Use original case (handler lowercases the personality name itself)
            self._handle_personality_command(cmd_original)
        elif canonical == "pet":
            self._handle_pet_command(cmd_original)

        elif canonical == "hatch":
            self._handle_hatch_command(cmd_original)
        elif canonical == "retry":
            retry_msg = self.retry_last()
            if retry_msg and hasattr(self, '_pending_input'):
                # Re-queue the message so process_loop sends it to the agent
                self._pending_input.put(retry_msg)
        elif canonical == "prompt":
            self._handle_prompt_compose_command(cmd_original)
        elif canonical == "undo":
            # Parse optional turn count: "/undo" → 1, "/undo 3" → 3.
            _undo_n = 1
            _undo_parts = cmd_original.split()
            if len(_undo_parts) > 1:
                try:
                    _undo_n = int(_undo_parts[1])
                except ValueError:
                    print(f"(._.) Invalid count {_undo_parts[1]!r} — use /undo or /undo N.")
                    return
                if _undo_n < 1:
                    _undo_n = 1
            _undo_desc = (
                "This removes the last user/assistant exchange from history."
                if _undo_n == 1
                else f"This removes the last {_undo_n} user turns from history."
            )
            if self._confirm_destructive_slash(
                "undo",
                _undo_desc,
                cmd_original=cmd_original,
            ) is None:
                return True  # confirmation cancelled — command handled, keep REPL alive
            self.undo_last(_undo_n)
        elif canonical == "branch":
            self._handle_branch_command(cmd_original)
        elif canonical == "save":
            self.save_conversation()
        elif canonical == "cron":
            self._handle_cron_command(cmd_original)
        elif canonical == "suggestions":
            self._handle_suggestions_command(cmd_original)
        elif canonical == "blueprint":
            self._handle_blueprint_command(cmd_original)
        elif canonical == "curator":
            self._handle_curator_command(cmd_original)
        elif canonical == "kanban":
            self._handle_kanban_command(cmd_original)
        elif canonical == "skills":
            with self._busy_command(self._slow_command_status(cmd_original)):
                self._handle_skills_command(cmd_original)
        elif canonical == "learn":
            self._handle_learn_command(cmd_original)
        elif canonical == "memory":
            self._handle_memory_command(cmd_original)
        elif canonical == "platforms":
            self._show_gateway_status()
        elif canonical == "status":
            self._show_session_status()
        elif canonical == "statusbar":
            self._status_bar_visible = not self._status_bar_visible
            state = "visible" if self._status_bar_visible else "hidden"
            self._console_print(f"  Status bar {state}")
        elif canonical == "timestamps":
            self._handle_timestamps_command(cmd_original)
        elif canonical == "verbose":
            self._toggle_verbose()
        elif canonical == "footer":
            self._handle_footer_command(cmd_original)
        elif canonical == "yolo":
            self._toggle_yolo()
        elif canonical == "reasoning":
            self._handle_reasoning_command(cmd_original)
        elif canonical == "fast":
            self._handle_fast_command(cmd_original)
        elif canonical == "compress":
            self._manual_compress(cmd_original)
        elif canonical == "usage":
            self._show_usage()
        elif canonical == "credits":
            self._show_credits()
        elif canonical == "billing":
            self._show_billing(cmd_original)
        elif canonical == "insights":
            self._show_insights(cmd_original)
        elif canonical == "copy":
            self._handle_copy_command(cmd_original)
        elif canonical == "debug":
            self._handle_debug_command()
        elif canonical == "update":
            if self._handle_update_command():
                return False
        elif canonical == "version":
            from openagents_cli.main import _print_version_info

            _print_version_info(check_updates=True)
        elif canonical == "paste":
            self._handle_paste_command()
        elif canonical == "image":
            self._handle_image_command(cmd_original)
        elif canonical == "reload":
            from openagents_cli.config import reload_env
            count = reload_env()
            print(f"  Reloaded .env ({count} var(s) updated)")
        elif canonical == "reload-mcp":
            # Interactive reload: confirm first (unless the user has opted out).
            # The auto-reload path (file watcher) calls _reload_mcp directly
            # without this confirmation.
            self._confirm_and_reload_mcp(cmd_original)
        elif canonical == "reload-skills":
            with self._busy_command(self._slow_command_status(cmd_original)):
                self._reload_skills()
        elif canonical == "bundles":
            self._handle_bundles_command(cmd_original)
        elif canonical == "browser":
            self._handle_browser_command(cmd_original)
        elif canonical == "plugins":
            try:
                # Discover from disk (bundled + user), matching `hermes plugins
                # list` — so installed-but-not-enabled plugins are visible here
                # too. The plugin manager only knows about *loaded* plugins, so
                # using it alone made freshly-installed, not-yet-enabled plugins
                # look like "nothing installed".
                from openagents_cli.plugins_cmd import (
                    _discover_all_plugins,
                    _get_disabled_set,
                    _get_enabled_set,
                    _plugin_status,
                )

                entries = _discover_all_plugins()
                enabled = _get_enabled_set()
                disabled = _get_disabled_set()

                # `/plugins` is a quick glance — default to user-installed
                # plugins (what the user actually added). Bundled provider/
                # platform plugins are summarized on one line; the full
                # catalog lives behind `hermes plugins list`.
                user_entries = [e for e in entries if e[3] != "bundled"]
                bundled_count = len(entries) - len(user_entries)

                if not user_entries:
                    print("No user plugins installed.")
                    print("  Install one: hermes plugins install owner/repo")
                    print(f"  Or drop a plugin directory into {display_openagents_home()}/plugins/")
                    if bundled_count:
                        print(f"  ({bundled_count} bundled plugins available — see: hermes plugins list)")
                else:
                    # Loaded-plugin details (tools/hooks/commands counts, errors)
                    # keyed by name, when available.
                    loaded: dict = {}
                    try:
                        from openagents_cli.plugins import get_plugin_manager
                        for p in get_plugin_manager().list_plugins():
                            loaded[p["name"]] = p
                    except Exception:
                        loaded = {}

                    print(f"User plugins ({len(user_entries)}):")
                    for name, version, _desc, source, _dir, key in sorted(user_entries):
                        state = _plugin_status(name, enabled, disabled, key=key)
                        glyph = {"enabled": "✓", "disabled": "✗"}.get(state, "○")
                        ver = f" v{version}" if version else ""
                        info = loaded.get(name) or {}
                        bits = []
                        if info.get("tools"):
                            bits.append(f"{info['tools']} tools")
                        if info.get("hooks"):
                            bits.append(f"{info['hooks']} hooks")
                        if info.get("commands"):
                            bits.append(f"{info['commands']} commands")
                        detail = f" ({', '.join(bits)})" if bits else ""
                        label = "" if state == "enabled" else f" [{state}]"
                        error = f" — {info['error']}" if info.get("error") else ""
                        print(f"  {glyph} {name}{ver}{label}{detail}{error}")
                    if bundled_count:
                        print(f"  (+{bundled_count} bundled — see: hermes plugins list)")
                    print("  Enable/disable: hermes plugins enable/disable <name>")
            except Exception as e:
                print(f"Plugin system error: {e}")
        elif canonical == "rollback":
            self._handle_rollback_command(cmd_original)
        elif canonical == "snapshot":
            self._handle_snapshot_command(cmd_original)
        elif canonical == "stop":
            self._handle_stop_command()
        elif canonical == "agents":
            self._handle_agents_command()
        elif canonical == "background":
            self._handle_background_command(cmd_original)
        elif canonical == "queue":
            # Extract prompt after "/queue " or "/q "
            parts = cmd_original.split(None, 1)
            payload = parts[1].strip() if len(parts) > 1 else ""
            if not payload:
                _cprint("  Usage: /queue <prompt>")
            else:
                self._pending_input.put(payload)
                if self._agent_running:
                    _cprint(f"  Queued for the next turn: {payload[:80]}{'...' if len(payload) > 80 else ''}")
                else:
                    _cprint(f"  Queued: {payload[:80]}{'...' if len(payload) > 80 else ''}")
        elif canonical == "steer":
            # Inject a message after the next tool call without interrupting.
            # If the agent is actively running, push the text into the agent's
            # pending_steer slot — the drain hook in _execute_tool_calls_*
            # will append it to the next tool result's content. If no agent
            # is running, fall back to queue semantics (same as /queue).
            parts = cmd_original.split(None, 1)
            payload = parts[1].strip() if len(parts) > 1 else ""
            if not payload:
                _cprint("  Usage: /steer <prompt>")
            elif self._agent_running and self.agent is not None and hasattr(self.agent, "steer"):
                try:
                    accepted = self.agent.steer(payload)
                except Exception as exc:
                    _cprint(f"  Steer failed: {exc}")
                else:
                    if accepted:
                        _cprint(f"  ⏩ Steer queued — arrives after the next tool call: {payload[:80]}{'...' if len(payload) > 80 else ''}")
                    else:
                        _cprint("  Steer rejected (empty payload).")
            else:
                # No active run — treat as a normal next-turn message.
                self._pending_input.put(payload)
                _cprint(f"  No agent running; queued as next turn: {payload[:80]}{'...' if len(payload) > 80 else ''}")
        elif canonical == "goal":
            self._handle_goal_command(cmd_original)
        elif canonical == "moa":
            # /moa is one-shot sugar only: run a single prompt through the
            # default MoA preset, then restore the prior model. To *switch* to a
            # MoA preset for the session, pick it from the model picker (MoA
            # presets surface as a virtual "Mixture of Agents" provider).
            from openagents_cli.moa_config import (
                moa_usage,
                normalize_moa_config,
            )

            parts = cmd_original.split(None, 1)
            payload = parts[1].strip() if len(parts) > 1 else ""
            if not payload:
                _cprint(f"  {moa_usage()}")
                return True
            moa_cfg = self.config.get("moa") if isinstance(self.config, dict) else {}
            normalized = normalize_moa_config(moa_cfg)
            preset = normalized["default_preset"]
            self._pending_moa_restore_model = {
                "requested_provider": getattr(self, "requested_provider", None),
                "provider": getattr(self, "provider", None),
                "model": getattr(self, "model", None),
                "api_key": getattr(self, "api_key", None),
                "base_url": getattr(self, "base_url", None),
                "api_mode": getattr(self, "api_mode", None),
            }
            self.requested_provider = "moa"
            self.provider = "moa"
            self.model = preset
            self.api_key = "moa-virtual-provider"
            self.base_url = "moa://local"
            self.api_mode = "chat_completions"
            self.agent = None
            self._pending_moa_disable_after_turn = True
            self._pending_agent_seed = payload
            _cprint(f"  MoA one-shot queued with preset {preset}; previous model will be restored after this turn.")
        elif canonical == "subgoal":
            self._handle_subgoal_command(cmd_original)
        elif canonical == "skin":
            self._handle_skin_command(cmd_original)
        elif canonical == "voice":
            self._handle_voice_command(cmd_original)
        elif canonical == "busy":
            self._handle_busy_command(cmd_original)
=======
    def _process_unregistered_slash(self, cmd_original: str, cmd_lower: str) -> bool:
        """Slash input with no built-in handler; precedence: quick_commands -> plugins -> bundles -> skills -> prefix expansion."""
        base_cmd = cmd_lower.split()[0]
        bare = base_cmd.lstrip("/")
        skill_commands = _ensure_skill_commands()
        skill_bundles = get_skill_bundles()
        quick_commands = self.config.get("quick_commands", {})
        user_args = cmd_original[len(base_cmd):].strip()
        if bare in quick_commands:
            return self._run_quick_command(base_cmd, quick_commands[bare], user_args)
        if bare in _get_plugin_cmd_handler_names():
            self._run_plugin_slash_command(base_cmd, user_args)
        elif base_cmd in skill_bundles:
            self._run_skill_bundle_command(base_cmd, skill_bundles[base_cmd], user_args)
        elif base_cmd in skill_commands:
            self._run_skill_slash_command(base_cmd, skill_commands[base_cmd], user_args)
>>>>>>> rb/tag
        else:
            return self._expand_slash_prefix(cmd_original, cmd_lower, skill_commands, skill_bundles)
        return True

    def _run_quick_command(self, base_cmd: str, qcmd: dict, user_args: str) -> bool:
        """User-defined quick command (config.yaml): ``exec`` runs a shell snippet, ``alias`` re-dispatches."""
        qtype = qcmd.get("type")
        if qtype == "alias":
            target = qcmd.get("target", "").strip()
            if target:
                target = target if target.startswith("/") else f"/{target}"
                return self.process_command(f"{target} {user_args}".strip())
            self._console_print(f"[bold red]Quick command '{base_cmd}' has no target defined[/]")
            return True
        if qtype != "exec":
            self._console_print(f"[bold red]Quick command '{base_cmd}' has unsupported type (supported: 'exec', 'alias')[/]")
            return True
        import subprocess
        exec_cmd = qcmd.get("command", "")
        if not exec_cmd:
            self._console_print(f"[bold red]Quick command '{base_cmd}' has no command defined[/]")
            return True
        try:
            # shell=True is intentional (user-authored config snippets, never LLM controlled);
            # the env is sanitized because this process holds every API key.
            from tools.environments.local import build_subprocess_env
            from openagents_cli._subprocess_compat import windows_hide_flags
            result = subprocess.run(
                exec_cmd, shell=True, capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=30, env=build_subprocess_env(),
                creationflags=windows_hide_flags(),  # no console flash on Windows (#56747)
            )
            # See #56747.
            output = result.stdout.strip() or result.stderr.strip()
            if output:
                from agent.redact import redact_sensitive_text
                self._console_print(_rich_text_from_ansi(redact_sensitive_text(output)))
            else:
                self._console_print("[dim]Command returned no output[/]")
        except subprocess.TimeoutExpired:
            self._console_print("[bold red]Quick command timed out (30s)[/]")
        except Exception as e:
            self._console_print(f"[bold red]Quick command error: {e}[/]")
        return True

    def _run_plugin_slash_command(self, base_cmd: str, user_args: str) -> None:
        from openagents_cli.plugins import get_plugin_command_handler, resolve_plugin_command_result

        plugin_handler = get_plugin_command_handler(base_cmd.lstrip("/"))
        if not plugin_handler:
            return
        try:
            result = resolve_plugin_command_result(plugin_handler(user_args))
            if result:
                _cprint(str(result))
        except Exception as e:
            _cprint(f"\033[1;31mPlugin command error: {e}{_RST}")

    def _queue_skill_message(self, msg) -> None:
        if hasattr(self, '_pending_input'):
            self._pending_input.put(msg)

    def _run_skill_bundle_command(self, base_cmd: str, bundle_info: dict, user_instruction: str) -> None:
        """``/<bundle>`` loads several skills at once (bundles win over same-named skills)."""
        bundle_result = build_bundle_invocation_message(base_cmd, user_instruction, task_id=self.session_id)
        if not bundle_result:
            ChatConsole().print(f"[bold red]Failed to load bundle for {base_cmd}[/]")
            return
        msg, loaded_names, missing = bundle_result
        self._queue_loaded_skills(msg, f"Loading bundle: {bundle_info['name']} ({len(loaded_names)} skills)", missing)

    def _queue_loaded_skills(self, msg, label: str, missing) -> None:
        print(f"\n⚡ {label}")
        if missing:
            ChatConsole().print(f"[yellow]Skipped missing skills: {', '.join(missing)}[/]")
        self._queue_skill_message(msg)

    def _run_skill_slash_command(self, base_cmd: str, skill_info: dict, rest: str) -> None:
        """``/<skill> ...``; stacked ``/skill-a /skill-b do XYZ`` loads every leading skill (up to 5)."""
        from agent.skill_commands import build_stacked_skill_invocation_message, split_stacked_skill_commands

        extra_keys, user_instruction = split_stacked_skill_commands(rest)
        if extra_keys:
            stacked_result = build_stacked_skill_invocation_message(
                [base_cmd, *extra_keys], user_instruction, task_id=self.session_id,
            )
            if not stacked_result:
                ChatConsole().print(f"[bold red]Failed to load stacked skills for {base_cmd}[/]")
                return
            msg, loaded_names, missing = stacked_result
            self._queue_loaded_skills(
                msg, f"Loading {len(loaded_names)} stacked skills: {', '.join(loaded_names)}", missing
            )
            return
        msg = build_skill_invocation_message(base_cmd, rest, task_id=self.session_id)
        if msg:
            self._queue_loaded_skills(msg, f"Loading skill: {skill_info['name']}", None)
        else:
            ChatConsole().print(f"[bold red]Failed to load skill for {base_cmd}[/]")

    def _expand_slash_prefix(self, cmd_original: str, cmd_lower: str, skill_commands, skill_bundles) -> bool:
        """Unique-prefix expansion against built-in COMMANDS + skill commands/bundles (agrees with tab-completion)."""
        from openagents_cli.commands import COMMANDS
        typed_base = cmd_lower.split()[0]
        all_known = set(COMMANDS) | set(skill_commands) | set(skill_bundles)
        matches = [c for c in all_known if c.startswith(typed_base)]
        if len(matches) > 1:
            if typed_base in matches:
                matches = [typed_base]
            else:
                # Unique shortest match wins: /qui -> /quit (5) over /quint-pipeline (15)
                min_len = min(len(c) for c in matches)
                shortest = [c for c in matches if len(c) == min_len]
                if len(shortest) == 1:
                    matches = shortest
        if len(matches) == 1 and matches[0] != typed_base:
            # Expand to the full name, preserving arguments.
            return self.process_command(matches[0] + cmd_original.strip()[len(typed_base):])
        if len(matches) > 1:
            _cprint(f"{_ACCENT}Ambiguous command: {cmd_lower}{_RST}")
            _cprint(f"{_DIM}Did you mean: {', '.join(sorted(matches))}?{_RST}")
        else:
            # Exact token with no handler (never re-dispatch the same token: recursion), or no match.
            from openagents_cli.cli_unknown_command import unknown_command_lines
            lead, pointer = unknown_command_lines(cmd_lower, all_known)
            _cprint(f"\033[1;31m{lead}{_RST}")
            _cprint(f"{_DIM}{_ACCENT}{pointer}{_RST}")
        return True

    def _drain_interrupt_queue_to_pending_input(self) -> None:
        """Move stray ``_interrupt_queue`` messages into ``_pending_input`` after every turn.

        Busy-time input lands in ``_interrupt_queue`` and is only drained by the explicit
        interrupt path; a turn that finishes naturally would otherwise strand it and the
        CLI appears to hang. Never raises.

        Called once at the end of every turn from ``process_loop``'s ``finally`` block. Catches and swallows
        ``Exception`` because the drain must never break the main loop. (#20271)
        """
        try:
            while not self._interrupt_queue.empty():
                stray = self._interrupt_queue.get_nowait()
                if stray:
                    self._pending_input.put(stray)
        except Exception:
            pass

    def _on_reasoning(self, reasoning_text: str):
        """Callback for intermediate reasoning display during tool-call loops."""
        if not reasoning_text:
            return
        self._reasoning_preview_buf = getattr(self, "_reasoning_preview_buf", "") + reasoning_text
        self._flush_reasoning_preview(force=False)

    # Inline tokens that bypass the destructive-slash confirmation modal (scripting, or
    # when the modal can't be marshaled onto the app loop).
    # A general escape hatch for non-interactive use (scripting/automation) and for the degraded path where
    # the modal can't be marshaled onto the app loop — lets users self-serve without flipping
    # approvals.destructive_slash_confirm in config. (Native Windows now drives the modal normally — see
    # #33961.)
    _DESTRUCTIVE_SKIP_TOKENS = frozenset({"now", "--yes", "-y"})


    def run(self):
        """Run the interactive CLI loop with persistent input at bottom."""
        if not self._claim_active_session("cli"):
            return

        self._tui_print_startup()
        self._tui_init_run_state()
        kb = self._tui_build_key_bindings()
        layout, style = self._tui_build_layout(kb)

<<<<<<< HEAD
        try:
            display_cfg = self.config.get("display") or {}
            if display_cfg.get("startup_animation", True):
                from openagents_cli.startup_animation import play_startup_animation

                play_startup_animation(enabled=True)
        except Exception:
            pass

        self.show_banner()
        # Surface any active supply-chain security advisories right after the
        # welcome banner. Quiet/single-query paths call this themselves.
        self._show_security_advisories()
        # If resuming a session, load history and display it immediately
        # so the user has context before typing their first message.
        if self._resumed:
            if self._preload_resumed_session():
                self._display_resumed_history()
        else:
            try:
                display_cfg = self.config.get("display") or {}
                if display_cfg.get("startup_show_sessions", True):
                    limit = int(display_cfg.get("startup_sessions_limit", 8) or 8)
                    self._show_recent_sessions(reason="startup", limit=limit)
            except Exception:
                pass

        try:
            from openagents_cli.skin_engine import get_active_skin
            _welcome_skin = get_active_skin()
            _welcome_text = _welcome_skin.get_branding("welcome", "Welcome to OpenAgents! Type your message or /help for commands.")
            _welcome_color = _welcome_skin.get_color("banner_text", "#FFF8DC")
        except Exception:
            _welcome_text = "Welcome to OpenAgents! Type your message or /help for commands."
            _welcome_color = "#FFF8DC"
        self._console_print(f"[{_welcome_color}]{_welcome_text}[/]")
        try:
            _tagline = _welcome_skin.get_branding("tagline", "")
            if _tagline:
                _dim = _welcome_skin.get_color("banner_dim", "#B8860B")
                self._console_print(f"[dim {_dim}]{_tagline}[/]")
        except Exception:
            pass

        # Warm the /model picker's provider-models cache off-thread during this
        # idle window (banner shown, user about to type). The no-args picker
        # otherwise blocks ~1-2s on serial /v1/models fetches the first time
        # it's opened in a session. Fire-and-forget, guarded once-per-process.
        try:
            from openagents_cli.model_switch import prewarm_picker_cache_async
            prewarm_picker_cache_async()
        except Exception:
            pass

        # Redaction opt-out warning (#17691): ON by default, loud when off.
        # The redactor snapshots its state at import time so any toggle now
        # won't affect the running process — we just want the operator to
        # see that they're running without the safety net.
        try:
            _redact_raw = os.getenv("HERMES_REDACT_SECRETS", "true")
            if _redact_raw.lower() not in {"1", "true", "yes", "on"}:
                self._console_print(
                    "[bold red]⚠  Secret redaction is DISABLED[/] "
                    f"(HERMES_REDACT_SECRETS={_redact_raw}). "
                    "API keys and tokens may appear verbatim in chat output, "
                    "session JSONs, and logs. Set "
                    "[cyan]security.redact_secrets: true[/] in config.yaml "
                    "to re-enable."
                )
        except Exception:
            pass
        # First-time OpenClaw-residue banner — fires once if ~/.openclaw/ exists
        # after an OpenClaw→Hermes migration (especially migrations done by
        # OpenClaw's own tool, which doesn't archive the source directory).
        try:
            from agent.onboarding import (
                OPENCLAW_RESIDUE_FLAG,
                detect_openclaw_residue,
                is_seen,
                mark_seen,
                openclaw_residue_hint_cli,
            )
            if not is_seen(self.config, OPENCLAW_RESIDUE_FLAG) and detect_openclaw_residue():
                try:
                    _resid_color = _welcome_skin.get_color("banner_dim", "#B8860B")
                except Exception:
                    _resid_color = "#B8860B"
                self._console_print(f"[{_resid_color}]{openclaw_residue_hint_cli()}[/]")
                try:
                    from openagents_cli.config import get_config_path as _get_cfg_path_resid
                    mark_seen(_get_cfg_path_resid(), OPENCLAW_RESIDUE_FLAG)
                except Exception:
                    pass  # best-effort — banner will fire again next session
        except Exception:
            pass  # banner is non-critical — never break startup
        # Show a random tip to help users discover features
        try:
            from openagents_cli.tips import get_random_tip
            _tip = get_random_tip()
            try:
                _tip_color = _welcome_skin.get_color("banner_dim", "#B8860B")
            except Exception:
                _tip_color = "#B8860B"
            self._console_print(f"[dim {_tip_color}]✦ Tip: {_tip}[/]")
        except Exception:
            pass  # Tips are non-critical — never break startup

        # Curator — kick off a background skill-maintenance pass on startup
        # if the schedule says we're due.  Runs in a daemon thread so it
        # never blocks the interactive loop.  Best-effort; any failure is
        # swallowed to avoid breaking session startup.
        try:
            from agent.curator import maybe_run_curator
            maybe_run_curator(
                idle_for_seconds=float("inf"),  # CLI startup = fully idle
                on_summary=lambda msg: self._console_print(
                    f"[dim #6b7684]💾 {msg}[/]"
                ),
            )
        except Exception:
            pass
        if self.preloaded_skills and not self._startup_skills_line_shown:
            skills_label = ", ".join(self.preloaded_skills)
            self._console_print(
                f"[bold {_accent_hex()}]Activated skills:[/] {skills_label}"
            )
            self._startup_skills_line_shown = True
        self._console_print()
        
        # State for async operation
        self._agent_running = False
        self._pending_input = queue.Queue()     # For normal input (commands + new queries)
        self._interrupt_queue = queue.Queue()   # For messages typed while agent is running
        # See constructor note. Mirrored here for the run() path that skips
        # the earlier __init__ branch.
        self._last_turn_interrupted = False
        self._should_exit = False
        self._last_ctrl_c_time = 0  # Track double Ctrl+C for force exit

        # Give plugin manager a CLI reference so plugins can inject messages
        from openagents_cli.plugins import get_plugin_manager
        get_plugin_manager()._cli_ref = self

        # Config file watcher — detect mcp_servers changes and auto-reload
        from openagents_cli.config import get_config_path as _get_config_path
        _cfg_path = _get_config_path()
        self._config_mtime: float = _cfg_path.stat().st_mtime if _cfg_path.exists() else 0.0
        self._config_mcp_servers: dict = self.config.get("mcp_servers") or {}
        self._last_config_check: float = 0.0  # monotonic time of last check

        # Clarify tool state: interactive question/answer with the user.
        # When the agent calls the clarify tool, _clarify_state is set and
        # the prompt_toolkit UI switches to a selection mode.
        self._clarify_state = None      # dict with question, choices, selected, response_queue
        self._clarify_freetext = False  # True when user chose "Other" and is typing
        self._clarify_deadline = 0      # monotonic timestamp when the clarify times out

        # Sudo password prompt state (similar mechanism to clarify)
        self._sudo_state = None         # dict with response_queue when active
        self._sudo_deadline = 0
        self._modal_input_snapshot = None

        # Dangerous command approval state (similar mechanism to clarify)
        self._approval_state = None     # dict with command, description, choices, selected, response_queue
        self._approval_deadline = 0
        self._approval_lock = threading.Lock()  # serialize concurrent approval prompts (delegation race fix)

        # Destructive slash-command confirmation state (/new, /clear, /undo).
        # These prompts are answered through the prompt_toolkit composer, not
        # raw input(), so the option labels stay visible and Enter does not EOF
        # the whole app.
        self._slash_confirm_state = None
        self._slash_confirm_deadline = 0

        # Slash command loading state
        self._command_running = False
        self._command_status = ""

        # Secure secret capture state for skill setup
        self._secret_state = None       # dict with var_name, prompt, metadata, response_queue
        self._secret_deadline = 0

        # Clipboard image attachments (paste images into the CLI)
        self._attached_images: list[Path] = []
        self._image_counter = 0

        # Voice mode state (protected by _voice_lock for cross-thread access)
        self._voice_lock = threading.Lock()
        self._voice_mode = False        # Whether voice mode is enabled
        self._voice_tts = False         # Whether TTS output is enabled
        self._voice_recorder = None     # AudioRecorder instance (lazy init)
        self._voice_recording = False   # Whether currently recording
        self._voice_processing = False  # Whether STT is in progress
        self._voice_continuous = False  # Whether to auto-restart after agent responds
        self._voice_tts_done = threading.Event()  # Signals TTS playback finished
        self._voice_tts_done.set()  # Initially "done" (no TTS pending)

        if os.environ.get("HERMES_DEFER_AGENT_STARTUP") != "1":
            self._install_tool_callbacks()

        if os.environ.get("HERMES_DEFER_AGENT_STARTUP") != "1":
            self._ensure_tirith_security()
        
        # Key bindings for the input area
        kb = KeyBindings()

        from prompt_toolkit.keys import Keys as _IgnoreKeys

        @kb.add(_IgnoreKeys.Ignore, eager=True)
        def handle_ignored_terminal_sequence(event):
            """Consume parser-level ignored terminal sequences before self-insert.

            install_ignored_terminal_sequences() in openagents_cli.pt_input_extras
            registers focus reports (CSI I / CSI O) as Keys.Ignore at the
            VT100 parser level. Without this no-op binding the default
            self-insert path would still fire and the bytes would land in
            the buffer.
            """
            return None

        def handle_enter(event):
            """Handle Enter key - submit input.
            
            Routes to the correct queue based on active UI state:
            - Sudo password prompt: password goes to sudo response queue
            - Approval selection: selected choice goes to approval response queue
            - Clarify freetext mode: answer goes to the clarify response queue
            - Clarify choice mode: selected choice goes to the clarify response queue
            - Agent running: goes to _interrupt_queue (chat() monitors this)
            - Agent idle: goes to _pending_input (process_loop monitors this)
            Commands (starting with /) always go to _pending_input so they're
            handled as commands, not sent as interrupt text to the agent.
            """
            # --- Sudo password prompt: submit the typed password ---
            if self._sudo_state:
                text = event.app.current_buffer.text
                self._sudo_state["response_queue"].put(text)
                self._sudo_state = None
                event.app.invalidate()
                return

            # --- Secret prompt: submit the typed secret ---
            if self._secret_state:
                text = event.app.current_buffer.text
                self._submit_secret_response(text)
                event.app.current_buffer.reset()
                event.app.invalidate()
                return

            # --- Approval selection: confirm the highlighted choice ---
            if self._approval_state:
                self._handle_approval_selection()
                event.app.invalidate()
                return

            # --- Slash-command confirmation: submit typed or highlighted choice ---
            if self._slash_confirm_state:
                text = event.app.current_buffer.text.strip()
                choices = self._slash_confirm_state.get("choices") or []
                choice = self._normalize_slash_confirm_choice(text, choices) if text else None
                if choice is None:
                    selected = self._slash_confirm_state.get("selected", 0)
                    if 0 <= selected < len(choices):
                        choice = choices[selected][0]
                self._submit_slash_confirm_response(choice or "cancel")
                event.app.current_buffer.reset()
                event.app.invalidate()
                return

            # --- /model picker modal ---
            if self._model_picker_state:
                try:
                    # Picker selections persist by default (same default as
                    # /model <name>); honour model.persist_switch_by_default.
                    from openagents_cli.model_switch import resolve_persist_behavior

                    self._handle_model_picker_selection(
                        persist_global=resolve_persist_behavior(False, False)
                    )
                except Exception as _exc:
                    _cprint(f"  ✗ Model selection failed: {_exc}")
                    self._close_model_picker()
                event.app.current_buffer.reset()
                event.app.invalidate()
                return

            # --- Clarify freetext mode: user typed their own answer ---
            if self._clarify_freetext and self._clarify_state:
                text = event.app.current_buffer.text.strip()
                if text:
                    self._clarify_state["response_queue"].put(text)
                    self._clarify_state = None
                    self._clarify_freetext = False
                    event.app.current_buffer.reset()
                    event.app.invalidate()
                return

            # --- Clarify choice mode: confirm the highlighted selection ---
            if self._clarify_state and not self._clarify_freetext:
                state = self._clarify_state
                selected = state["selected"]
                choices = state.get("choices") or []
                if selected < len(choices):
                    state["response_queue"].put(choices[selected])
                    self._clarify_state = None
                    event.app.invalidate()
                else:
                    # "Other" selected → switch to freetext
                    self._clarify_freetext = True
                    event.app.invalidate()
                return

            # --- Normal input routing ---
            text = event.app.current_buffer.text.strip()
            has_images = bool(self._attached_images)
            if text or has_images:
                # Handle /model directly on the UI thread so interactive pickers
                # can safely use prompt_toolkit terminal handoff helpers.
                if self._should_handle_model_command_inline(text, has_images=has_images):
                    if not self.process_command(text):
                        self._should_exit = True
                        if event.app.is_running:
                            event.app.exit()
                    event.app.current_buffer.reset(append_to_history=True)
                    # Force a repaint: process_command() prints through
                    # patch_stdout (scrolls output above the prompt) and never
                    # invalidates the app, so the just-cleared input area can
                    # keep showing the submitted text until some unrelated
                    # redraw fires. Every other early-return branch in this
                    # handler invalidates after reset — match them.
                    event.app.invalidate()
                    return

                # Handle /steer while the agent is running immediately on the
                # UI thread.  Queuing through _pending_input would deadlock the
                # steer until after the agent loop finishes (process_loop is
                # blocked inside self.chat()), which turns /steer into a
                # post-run next-turn message — defeating mid-run injection.
                # agent.steer() is thread-safe (holds _pending_steer_lock).
                if self._should_handle_steer_command_inline(text, has_images=has_images):
                    self.process_command(text)
                    event.app.current_buffer.reset(append_to_history=True)
                    # Force a repaint after clearing the buffer.  /steer is
                    # dispatched mid-run while the agent streams output through
                    # patch_stdout; process_command() never invalidates the
                    # app, so without this the submitted "/steer <text>" can
                    # linger in the input area (looking unsent) and invite an
                    # accidental re-submit. See issue #34569.
                    event.app.invalidate()
                    return

                # Snapshot and clear attached images
                images = list(self._attached_images)
                self._attached_images.clear()
                event.app.invalidate()
                # Bundle text + images as a tuple when images are present
                payload = (text, images) if images else text
                if self._agent_running and not (text and _looks_like_slash_command(text)):
                    _effective_mode = self.busy_input_mode
                    if _effective_mode == "steer":
                        # Route Enter through /steer — inject mid-run after the
                        # next tool call.  Images can't ride along (steer only
                        # appends text), so fall back to queue when images are
                        # attached.  If the agent lacks steer() or rejects the
                        # payload, also fall back to queue so nothing is lost.
                        if images or not text:
                            _effective_mode = "queue"
                        else:
                            accepted = False
                            try:
                                if self.agent is not None and hasattr(self.agent, "steer"):
                                    accepted = bool(self.agent.steer(text))
                            except Exception as exc:
                                _cprint(f"  {_DIM}Steer failed ({exc}) — queued for next turn.{_RST}")
                                accepted = False
                            if accepted:
                                preview = text[:80] + ("..." if len(text) > 80 else "")
                                _cprint(f"  {_ACCENT}⏩ Steered: '{preview}'{_RST}")
                            else:
                                _effective_mode = "queue"
                    if _effective_mode == "queue":
                        # Queue for the next turn instead of interrupting
                        self._pending_input.put(payload)
                        preview = text if text else f"[{len(images)} image{'s' if len(images) != 1 else ''} attached]"
                        _cprint(f"  Queued for the next turn: {preview[:80]}{'...' if len(preview) > 80 else ''}")
                    elif _effective_mode == "interrupt":
                        self._interrupt_queue.put(payload)
                        # Debug: log to file when message enters interrupt queue
                        try:
                            _dbg = _hermes_home / "interrupt_debug.log"
                            with open(_dbg, "a", encoding="utf-8") as _f:
                                _f.write(f"{time.strftime('%H:%M:%S')} ENTER: queued interrupt msg={str(payload)[:60]!r}, "
                                         f"agent_running={self._agent_running}\n")
                        except Exception:
                            pass
                    # First-touch onboarding: on the very first busy-while-running
                    # event for this install, print a one-line tip explaining the
                    # /busy knob.  Flag persists to config.yaml and never fires
                    # again.  Guarded for exceptions so onboarding can't break
                    # the input loop.
                    try:
                        from agent.onboarding import (
                            BUSY_INPUT_FLAG,
                            busy_input_hint_cli,
                            is_seen,
                            mark_seen,
                        )
                        if not is_seen(CLI_CONFIG, BUSY_INPUT_FLAG):
                            _cprint(f"  {_DIM}{busy_input_hint_cli(self.busy_input_mode)}{_RST}")
                            mark_seen(_hermes_home / "config.yaml", BUSY_INPUT_FLAG)
                            CLI_CONFIG.setdefault("onboarding", {}).setdefault("seen", {})[BUSY_INPUT_FLAG] = True
                    except Exception:
                        pass
                else:
                    self._pending_input.put(payload)
                event.app.current_buffer.reset(append_to_history=True)

        _bind_prompt_submit_keys(kb, handle_enter)
        
        @kb.add('escape', 'enter')
        def handle_alt_enter(event):
            """Alt+Enter inserts a newline for multi-line input.

            Works on mac/Linux/WSL. On Windows Terminal this keystroke is
            intercepted at the terminal layer (toggles fullscreen) and never
            reaches here — Windows users get newline via Ctrl+Enter instead
            (bound below as c-j, since WT delivers Ctrl+Enter as LF).
            """
            event.current_buffer.insert_text('\n')

        if _preserve_ctrl_enter_newline():
            @kb.add('c-j')
            def handle_ctrl_enter_newline(event):
                """Ctrl+Enter inserts a newline on Windows, WSL, SSH, and WT.

                Windows Terminal (incl. WSL/SSH sessions through it) delivers
                Ctrl+Enter as LF (c-j), distinct from plain Enter (c-m). This
                binding makes Ctrl+Enter the equivalent of Alt+Enter on those
                terminals, giving an Enter-involving newline keystroke
                without requiring terminal settings changes. Ctrl+J (the raw
                LF keystroke) also triggers this by virtue of being the same
                key code — a harmless side effect since Ctrl+J has no
                conflicting OpenAgents binding. See issue #22379.
                """
                event.current_buffer.insert_text('\n')

        # VSCode/Cursor bind Ctrl+G to "Find Next" at the editor level, so
        # the keystroke never reaches the embedded terminal. Alt+G is unbound
        # in those IDEs and arrives here as ('escape', 'g') — register it as
        # a fallback so the editor handoff works inside Cursor/VSCode too.
        _editor_filter = Condition(
            lambda: not self._clarify_state and not self._approval_state and not self._sudo_state and not self._secret_state
        )

        @kb.add('c-g', filter=_editor_filter)
        @kb.add('escape', 'g', filter=_editor_filter)
        def handle_open_in_editor(event):
            """Ctrl+G (or Alt+G in VSCode/Cursor) opens the current draft in an external editor."""
            cli_ref._open_external_editor(event.current_buffer)

        @kb.add('tab', eager=True)
        def handle_tab(event):
            """Tab: accept completion, auto-suggestion, or start completions.

            Priority:
            1. Completion menu open → accept selected completion
            2. Ghost text suggestion available → accept auto-suggestion
            3. Otherwise → start completion menu

            After accepting a provider like 'anthropic:', the completion menu
            closes and complete_while_typing doesn't fire (no keystroke).
            This binding re-triggers completions so stage-2 models appear
            immediately.
            """
            buf = event.current_buffer
            if buf.complete_state:
                # Completion menu is open — accept the selection
                completion = buf.complete_state.current_completion
                if completion is None:
                    # Menu open but nothing selected — select first then grab it
                    buf.go_to_completion(0)
                    completion = buf.complete_state and buf.complete_state.current_completion
                if completion is None:
                    return
                # Accept the selected completion
                buf.apply_completion(completion)
            elif buf.suggestion and buf.suggestion.text:
                # No completion menu, but there's a ghost text auto-suggestion — accept it
                buf.insert_text(buf.suggestion.text)
            else:
                # No menu and no suggestion — start completions from scratch
                buf.start_completion()

        # --- Clarify tool: arrow-key navigation for multiple-choice questions ---

        @kb.add('up', filter=Condition(lambda: bool(self._clarify_state) and not self._clarify_freetext))
        def clarify_up(event):
            """Move selection up in clarify choices."""
            if self._clarify_state:
                self._clarify_state["selected"] = max(0, self._clarify_state["selected"] - 1)
                event.app.invalidate()

        @kb.add('down', filter=Condition(lambda: bool(self._clarify_state) and not self._clarify_freetext))
        def clarify_down(event):
            """Move selection down in clarify choices."""
            if self._clarify_state:
                choices = self._clarify_state.get("choices") or []
                max_idx = len(choices)  # last index is the "Other" option
                self._clarify_state["selected"] = min(max_idx, self._clarify_state["selected"] + 1)
                event.app.invalidate()

        # Number keys for quick clarify selection (1-9, 0 for 10th item)
        def _make_clarify_number_handler(idx):
            def handler(event):
                if self._clarify_state and not self._clarify_freetext:
                    choices = self._clarify_state.get("choices") or []
                    # Map index to choice (treating "Other" as the last option)
                    if idx < len(choices):
                        # Select a numbered choice
                        self._clarify_state["response_queue"].put(choices[idx])
                        self._clarify_state = None
                        self._clarify_freetext = False
                        event.app.invalidate()
                    elif idx == len(choices):
                        # Select "Other" option
                        self._clarify_freetext = True
                        event.app.invalidate()
            return handler

        for _num in range(10):
            # 1-9 select items 0-8, 0 selects item 9 (10thitem)
            _idx = 9 if _num == 0 else _num - 1
            kb.add(str(_num), filter=Condition(lambda: bool(self._clarify_state) and not self._clarify_freetext))(_make_clarify_number_handler(_idx))

        # --- Dangerous command approval: arrow-key navigation ---

        @kb.add('up', filter=Condition(lambda: bool(self._approval_state)))
        def approval_up(event):
            if self._approval_state:
                self._approval_state["selected"] = max(0, self._approval_state["selected"] - 1)
                event.app.invalidate()

        @kb.add('down', filter=Condition(lambda: bool(self._approval_state)))
        def approval_down(event):
            if self._approval_state:
                max_idx = len(self._approval_state["choices"]) - 1
                self._approval_state["selected"] = min(max_idx, self._approval_state["selected"] + 1)
                event.app.invalidate()

        # --- Slash-command confirmation: arrow-key navigation ---
        @kb.add('up', filter=Condition(lambda: bool(self._slash_confirm_state)))
        def slash_confirm_up(event):
            if self._slash_confirm_state:
                self._slash_confirm_state["selected"] = max(0, self._slash_confirm_state.get("selected", 0) - 1)
                event.app.invalidate()

        @kb.add('down', filter=Condition(lambda: bool(self._slash_confirm_state)))
        def slash_confirm_down(event):
            if self._slash_confirm_state:
                max_idx = len(self._slash_confirm_state.get("choices") or []) - 1
                self._slash_confirm_state["selected"] = min(max_idx, self._slash_confirm_state.get("selected", 0) + 1)
                event.app.invalidate()

        # --- /model picker: arrow-key navigation ---
        @kb.add('up', filter=Condition(lambda: bool(self._model_picker_state)))
        def model_picker_up(event):
            if self._model_picker_state:
                self._model_picker_state["selected"] = max(0, self._model_picker_state.get("selected", 0) - 1)
                event.app.invalidate()

        @kb.add('down', filter=Condition(lambda: bool(self._model_picker_state)))
        def model_picker_down(event):
            state = self._model_picker_state
            if not state:
                return
            if state.get("stage") == "provider":
                max_idx = len(state.get("providers") or [])
            else:
                max_idx = len(state.get("model_list") or []) + 1
            state["selected"] = min(max_idx, state.get("selected", 0) + 1)
            event.app.invalidate()

        @kb.add('escape', filter=Condition(lambda: bool(self._model_picker_state)), eager=True)
        def model_picker_escape(event):
            """ESC closes the /model picker."""
            self._close_model_picker()
            event.app.current_buffer.reset()
            event.app.invalidate()

        # Number keys for quick approval selection (1-9, 0 for 10th item)
        def _make_approval_number_handler(idx):
            def handler(event):
                if self._approval_state and idx < len(self._approval_state["choices"]):
                    self._approval_state["selected"] = idx
                    self._handle_approval_selection()
                    event.app.invalidate()
            return handler

        for _num in range(10):
            # 1-9 select items 0-8, 0 selects item 9 (10th item)
            _idx = 9 if _num == 0 else _num - 1
            kb.add(str(_num), filter=Condition(lambda: bool(self._approval_state)))(_make_approval_number_handler(_idx))

        # Number keys for quick slash-confirm selection (1-9, 0 for 10th item)
        def _make_slash_confirm_number_handler(idx):
            def handler(event):
                if self._slash_confirm_state and idx < len(self._slash_confirm_state.get("choices") or []):
                    choice = self._slash_confirm_state["choices"][idx][0]
                    self._submit_slash_confirm_response(choice)
                    event.app.current_buffer.reset()
                    event.app.invalidate()
            return handler

        for _num in range(10):
            _idx = 9 if _num == 0 else _num - 1
            kb.add(str(_num), filter=Condition(lambda: bool(self._slash_confirm_state)))(_make_slash_confirm_number_handler(_idx))

        # --- History navigation: up/down browse history in normal input mode ---
        # The TextArea is multiline, so by default up/down only move the cursor.
        # Buffer.auto_up/auto_down handle both: cursor movement when multi-line,
        # history browsing when on the first/last line (or single-line input).
        _normal_input = Condition(
            lambda: not self._clarify_state and not self._approval_state and not self._slash_confirm_state and not self._sudo_state and not self._secret_state and not self._model_picker_state
        )

        @kb.add('up', filter=_normal_input)
        def history_up(event):
            """Up arrow: browse history when on first line, else move cursor up."""
            event.app.current_buffer.auto_up(count=event.arg)

        @kb.add('down', filter=_normal_input)
        def history_down(event):
            """Down arrow: browse history when on last line, else move cursor down."""
            event.app.current_buffer.auto_down(count=event.arg)

        @kb.add('c-l')
        def handle_ctrl_l(event):
            """Ctrl+L: force a clean full-screen repaint.

            Recovers the UI after external terminal buffer drift — tmux /
            cmux tab switches, ``clear`` from a subshell, SSH window
            restores, etc. — that prompt_toolkit can't detect on its own.
            Matches the universal bash/zsh/fish/vim/htop convention.
            """
            self._force_full_redraw()

        @kb.add('c-c')
        def handle_ctrl_c(event):
            """Handle Ctrl+C - cancel interactive prompts, interrupt agent, or exit.
            
            Priority:
            0. Cancel active voice recording
            1. Cancel active sudo/approval/clarify prompt
            2. Interrupt the running agent (first press)
            3. Force exit (second press within 2s, or when idle)
            """
            now = time.time()

            # Cancel active voice recording.
            # Run cancel() in a background thread to prevent blocking the
            # event loop if AudioRecorder._lock or CoreAudio takes time.
            _should_cancel_voice = False
            _recorder_ref = None
            with cli_ref._voice_lock:
                if cli_ref._voice_recording and cli_ref._voice_recorder:
                    _recorder_ref = cli_ref._voice_recorder
                    cli_ref._voice_recording = False
                    cli_ref._voice_continuous = False
                    _should_cancel_voice = True
            if _should_cancel_voice:
                _cprint(f"\n{_DIM}Recording cancelled.{_RST}")
                threading.Thread(
                    target=_recorder_ref.cancel, daemon=True
                ).start()
                event.app.invalidate()
                return

            # Cancel sudo prompt
            if self._sudo_state:
                self._sudo_state["response_queue"].put("")
                self._sudo_state = None
                event.app.invalidate()
                return

            # Cancel secret prompt
            if self._secret_state:
                self._cancel_secret_capture()
                event.app.current_buffer.reset()
                event.app.invalidate()
                return

            # Cancel approval prompt (deny)
            if self._approval_state:
                self._approval_state["response_queue"].put("deny")
                self._approval_state = None
                event.app.invalidate()
                return

            # Cancel slash confirmation prompt
            if self._slash_confirm_state:
                self._submit_slash_confirm_response("cancel")
                event.app.current_buffer.reset()
                event.app.invalidate()
                return

            # Cancel /model picker
            if self._model_picker_state:
                self._close_model_picker()
                event.app.current_buffer.reset()
                event.app.invalidate()
                return

            # Cancel clarify prompt
            if self._clarify_state:
                self._clarify_state["response_queue"].put(
                    "The user cancelled. Use your best judgement to proceed."
                )
                self._clarify_state = None
                self._clarify_freetext = False
                event.app.current_buffer.reset()
                event.app.invalidate()
                return

            if self._agent_running and self.agent:
                if now - self._last_ctrl_c_time < 2.0:
                    print("\n⚡ Force exiting...")
                    self._should_exit = True
                    event.app.exit()
                    return
                
                self._last_ctrl_c_time = now
                print("\n⚡ Interrupting agent... (press Ctrl+C again to force exit)")
                self.agent.interrupt()
            # If there's text or images, clear them (like bash).
            # If everything is already empty, exit.
            elif event.app.current_buffer.text or self._attached_images:
                event.app.current_buffer.reset()
                self._attached_images.clear()
                event.app.invalidate()
            else:
                self._should_exit = True
                event.app.exit()

        # Ctrl+Shift+C: no binding needed. Terminal emulators (GNOME Terminal,
        # iTerm2, kitty, Windows Terminal, etc.) intercept Ctrl+Shift+C before
        # the keystroke reaches the application's stdin — prompt_toolkit never
        # sees it, and prompt_toolkit's key spec parser doesn't even recognise
        # 'c-S-c' anyway (the Shift modifier is meaningless on control-sequence
        # keys). #19884 added a handler for this; #19895 patched the resulting
        # startup crash with try/except. Both were based on a misreading of how
        # terminal key events propagate. Deleting the dead handler outright.

        @kb.add('c-q')  # Ctrl+Q
        def handle_ctrl_q(event):
            """Alternative interrupt/exit shortcut (Ctrl+Q).

            Behaves like Ctrl+C: cancels active prompts, interrupts the
            running agent, or clears the input buffer. Does not support
            the double-press 'force exit' feature of Ctrl+C.
            """
            # Cancel active voice recording.
            _should_cancel_voice = False
            _recorder_ref = None
            with cli_ref._voice_lock:
                if cli_ref._voice_recording and cli_ref._voice_recorder:
                    _recorder_ref = cli_ref._voice_recorder
                    cli_ref._voice_recording = False
                    cli_ref._voice_continuous = False
                    _should_cancel_voice = True
            if _should_cancel_voice:
                _cprint(f"\n{_DIM}Recording cancelled.{_RST}")
                threading.Thread(
                    target=_recorder_ref.cancel, daemon=True
                ).start()
                event.app.invalidate()
                return

            # Cancel sudo prompt
            if self._sudo_state:
                self._sudo_state["response_queue"].put("")
                self._sudo_state = None
                event.app.invalidate()
                return

            # Cancel secret prompt
            if self._secret_state:
                self._cancel_secret_capture()
                event.app.current_buffer.reset()
                event.app.invalidate()
                return

            # Cancel approval prompt (deny)
            if self._approval_state:
                self._approval_state["response_queue"].put("deny")
                self._approval_state = None
                event.app.invalidate()
                return

            # Cancel slash confirmation prompt
            if self._slash_confirm_state:
                self._submit_slash_confirm_response("cancel")
                event.app.current_buffer.reset()
                event.app.invalidate()
                return

            # Cancel /model picker
            if self._model_picker_state:
                self._close_model_picker()
                event.app.current_buffer.reset()
                event.app.invalidate()
                return

            # Cancel clarify prompt
            if self._clarify_state:
                self._clarify_state["response_queue"].put(
                    "The user cancelled. Use your best judgement to proceed."
                )
                self._clarify_state = None
                self._clarify_freetext = False
                event.app.current_buffer.reset()
                event.app.invalidate()
                return

            if self._agent_running and self.agent:
                print("\n⚡ Interrupting agent...")
                self.agent.interrupt()
            elif event.app.current_buffer.text or self._attached_images:
                event.app.current_buffer.reset()
                self._attached_images.clear()
                event.app.invalidate()
            else:
                self._should_exit = True
                event.app.exit()

        @kb.add('c-d')
        def handle_ctrl_d(event):
            """Ctrl+D: delete char under cursor (standard readline behaviour).
            Only exit when the input is empty — same as bash/zsh. Pending
            attached images count as input and block the EOF-exit so the
            user doesn't lose them silently.
            """
            buf = event.app.current_buffer
            if buf.text:
                buf.delete()
            elif self._attached_images:
                # Empty text but pending attachments — no-op, don't exit.
                return
            else:
                self._should_exit = True
                event.app.exit()

        _modal_prompt_active = Condition(
            lambda: bool(self._secret_state or self._sudo_state or self._slash_confirm_state)
        )

        @kb.add('escape', filter=_modal_prompt_active, eager=True)
        def handle_escape_modal(event):
            """ESC cancels active secret/sudo prompts."""
            if self._secret_state:
                self._cancel_secret_capture()
                event.app.current_buffer.reset()
                event.app.invalidate()
                return
            if self._sudo_state:
                self._sudo_state["response_queue"].put("")
                self._sudo_state = None
                event.app.invalidate()
                return
            if self._slash_confirm_state:
                self._submit_slash_confirm_response("cancel")
                event.app.current_buffer.reset()
                event.app.invalidate()
                return

        @kb.add('c-z')
        def handle_ctrl_z(event):
            """Handle Ctrl+Z - suspend process to background (Unix only)."""
            if sys.platform == 'win32':
                _cprint(f"\n{_DIM}Suspend (Ctrl+Z) is not supported on Windows.{_RST}")
                event.app.invalidate()
                return
            import signal as _sig
            from prompt_toolkit.application import run_in_terminal
            from openagents_cli.skin_engine import get_active_skin
            agent_name = get_active_skin().get_branding("agent_name", "OpenAgents")
            msg = f"\n{agent_name} has been suspended. Run `fg` to bring {agent_name} back."
            def _suspend():
                os.write(1, msg.encode())
                os.kill(0, _sig.SIGTSTP)
            run_in_terminal(_suspend)

        # Voice push-to-talk key: configurable via config.yaml (voice.record_key)
        # Default: Ctrl+B (avoids conflict with Ctrl+R readline reverse-search).
        # Config spellings (ctrl/control/alt/option/opt) are normalized to
        # prompt_toolkit's c-x / a-x format via ``normalize_voice_record_key_for_prompt_toolkit``
        # so the same config value binds identically in the TUI and CLI
        # (Copilot round-9 review on #19835). ``super``/``win``/``windows``
        # configs silently fall back to the default here since prompt_toolkit
        # has no super modifier — log a warning so users notice the
        # TUI/CLI split instead of a silent mismatch (round-11).
        _raw_key: object = "ctrl+b"
        try:
            from openagents_cli.config import load_config
            from openagents_cli.voice import (
                normalize_voice_record_key_for_prompt_toolkit,
                voice_record_key_from_config,
            )
            _raw_key = voice_record_key_from_config(load_config())
            _voice_key = normalize_voice_record_key_for_prompt_toolkit(_raw_key)
            if (
                isinstance(_raw_key, str)
                and _raw_key.strip().lower().split("+", 1)[0].strip() in {"super", "win", "windows"}
                and _voice_key == "c-b"
            ):
                logger.warning(
                    "voice.record_key %r uses a TUI-only modifier (super/win); "
                    "CLI fell back to Ctrl+B. Use ctrl+<key> or alt+<key> for "
                    "cross-runtime parity.",
                    _raw_key,
                )
        except Exception:
            _voice_key = "c-b"

        # Cache the UI label here — same ``_raw_key`` that drives the
        # prompt_toolkit binding below. Every status / placeholder /
        # recording-hint render reads this cached value so display can
        # never drift from the live keybinding even if the user edits
        # voice.record_key mid-session (Copilot round-13 on #19835).
        self.set_voice_record_key_cache(_raw_key)

        @kb.add(_voice_key)
        def handle_voice_record(event):
            """Toggle voice recording when voice mode is active.

            IMPORTANT: This handler runs in prompt_toolkit's event-loop thread.
            Any blocking call here (locks, sd.wait, disk I/O) freezes the
            entire UI.  All heavy work is dispatched to daemon threads.
            """
            if not cli_ref._voice_mode:
                return
            # Always allow STOPPING a recording (even when agent is running)
            if cli_ref._voice_recording:
                # Manual stop via push-to-talk key: stop continuous mode
                with cli_ref._voice_lock:
                    cli_ref._voice_continuous = False
                # Flag clearing is handled atomically inside _voice_stop_and_transcribe
                event.app.invalidate()
                threading.Thread(
                    target=cli_ref._voice_stop_and_transcribe,
                    daemon=True,
                ).start()
            else:
                # Guard: don't START recording during agent run or interactive prompts
                if cli_ref._agent_running:
                    return
                if cli_ref._clarify_state or cli_ref._sudo_state or cli_ref._approval_state or cli_ref._slash_confirm_state:
                    return
                # Guard: don't start while a previous stop/transcribe cycle is
                # still running — recorder.stop() holds AudioRecorder._lock and
                # start() would block the event-loop thread waiting for it.
                if cli_ref._voice_processing:
                    return

                # Interrupt TTS if playing, so user can start talking.
                # stop_playback() is fast (just terminates a subprocess).
                if not cli_ref._voice_tts_done.is_set():
                    try:
                        from tools.voice_mode import stop_playback
                        stop_playback()
                        cli_ref._voice_tts_done.set()
                    except Exception:
                        pass

                with cli_ref._voice_lock:
                    cli_ref._voice_continuous = True

                # Dispatch to a daemon thread so play_beep(sd.wait),
                # AudioRecorder.start(lock acquire), and config I/O
                # never block the prompt_toolkit event loop.
                def _start_recording():
                    try:
                        cli_ref._voice_start_recording()
                        if hasattr(cli_ref, '_app') and cli_ref._app:
                            cli_ref._app.invalidate()
                    except Exception as e:
                        _cprint(f"\n{_DIM}Voice recording failed: {e}{_RST}")

                threading.Thread(target=_start_recording, daemon=True).start()
                event.app.invalidate()
        from prompt_toolkit.keys import Keys

        @kb.add(Keys.BracketedPaste, eager=True)
        def handle_paste(event):
            """Handle terminal paste — detect clipboard images.

            When the terminal supports bracketed paste, Ctrl+V / Cmd+V
            triggers this with the pasted text. We only auto-attach a
            clipboard image for image-only/empty paste gestures so text
            pastes and dictation do not accidentally attach stale images.

            Large pastes (5+ lines) are collapsed to a file reference
            placeholder while preserving any existing user text in the
            buffer.
            """
            # Diagnostic canary: measure how long the paste handler blocks
            # the prompt_toolkit event loop. If this exceeds ~500ms we log
            # it so recurring "CLI freezes on paste" reports (issue #16263,
            # macOS Tahoe 26 + iTerm2/Ghostty) arrive with data attached.
            _paste_handler_start = time.perf_counter()
            _paste_raw_size = len(event.data or "")
            pasted_text = event.data or ""
            # Normalise line endings — Windows \r\n and old Mac \r both become \n
            # so the 5-line collapse threshold and display are consistent.
            pasted_text = pasted_text.replace('\r\n', '\n').replace('\r', '\n')
            pasted_text = _strip_leaked_bracketed_paste_wrappers(pasted_text)
            pasted_text, _had_mouse_reports = _strip_leaked_terminal_responses_with_meta(pasted_text)
            if _had_mouse_reports:
                self._recover_terminal_input_modes(reason="mouse reports leaked into bracketed paste payload")
            if _should_auto_attach_clipboard_image_on_paste(pasted_text) and self._try_attach_clipboard_image():
                event.app.invalidate()
            if pasted_text:
                # Sanitize surrogate characters (e.g. from Word/Google Docs paste) before writing
                from run_agent import _sanitize_surrogates
                pasted_text = _sanitize_surrogates(pasted_text)
                line_count = pasted_text.count('\n')
                buf = event.current_buffer
                threshold = self.config.get("paste_collapse_threshold", 5)
                char_threshold = self.config.get("paste_collapse_char_threshold", 2000)
                lines_hit = threshold > 0 and line_count >= threshold
                chars_hit = char_threshold > 0 and len(pasted_text) >= char_threshold
                if (lines_hit or chars_hit) and not buf.text.strip().startswith('/'):
                    _paste_counter[0] += 1
                    paste_dir = _hermes_home / "pastes"
                    paste_dir.mkdir(parents=True, exist_ok=True)
                    paste_file = paste_dir / f"paste_{_paste_counter[0]}_{datetime.now().strftime('%H%M%S')}.txt"
                    paste_file.write_text(pasted_text, encoding="utf-8")
                    logger.info("Collapsed paste #%d: %d lines, %d chars -> %s", _paste_counter[0], line_count + 1, len(pasted_text), paste_file)
                    placeholder = f"[Pasted text #{_paste_counter[0]}: {line_count + 1} lines \u2192 {paste_file}]"
                    prefix = ""
                    if buf.cursor_position > 0 and buf.text[buf.cursor_position - 1] != '\n':
                        prefix = "\n"
                    _paste_just_collapsed[0] = True
                    buf.insert_text(prefix + placeholder)
                else:
                    buf.insert_text(pasted_text)
            _paste_handler_elapsed_ms = (time.perf_counter() - _paste_handler_start) * 1000.0
            if _paste_handler_elapsed_ms > 500.0:
                logger.warning(
                    "Slow bracketed-paste handler: %.1fms to process %d bytes "
                    "(%d lines) on %s. If the input becomes unresponsive after "
                    "this, attach this log line to the bug report.",
                    _paste_handler_elapsed_ms,
                    _paste_raw_size,
                    pasted_text.count('\n') + 1 if pasted_text else 0,
                    sys.platform,
                )

        @kb.add('c-v')
        def handle_ctrl_v(event):
            """Fallback image paste for terminals without bracketed paste.

            On Linux terminals (GNOME Terminal, Konsole, etc.), Ctrl+V
            sends raw byte 0x16 instead of triggering a paste.  This
            binding catches that and checks the clipboard for images.
            On terminals that DO intercept Ctrl+V for paste (macOS
            Terminal, iTerm2, VSCode, Windows Terminal), the bracketed
            paste handler fires instead and this binding never triggers.
            """
            if self._try_attach_clipboard_image():
                event.app.invalidate()

        @kb.add('escape', 'v')
        def handle_alt_v(event):
            """Alt+V — paste image from clipboard.

            Alt key combos pass through all terminal emulators (sent as
            ESC + key), unlike Ctrl+V which terminals intercept for text
            paste.  This is the reliable way to attach clipboard images
            on WSL2, VSCode, and any terminal over SSH where Ctrl+V
            can't reach the application for image-only clipboard.
            """
            if self._try_attach_clipboard_image():
                event.app.invalidate()
            else:
                # No image found — show a hint
                pass  # silent when no image (avoid noise on accidental press)

        # Dynamic prompt: shows OpenAgents symbol when agent is working,
        # or answer prompt when clarify freetext mode is active.
        cli_ref = self

        def get_prompt():
            return cli_ref._get_tui_prompt_fragments()

        # Create the input area with multiline (Alt+Enter), autocomplete, and paste handling
        from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
        from prompt_toolkit.completion import ThreadedCompleter


        _completer = SlashCommandCompleter(
            skill_commands_provider=lambda: get_skill_commands(),
            command_filter=cli_ref._command_available,
            skill_bundles_provider=lambda: get_skill_bundles(),
        )
        input_area = TextArea(
            height=Dimension(min=1, max=8, preferred=1),
            prompt=get_prompt,
            style='class:input-area',
            multiline=True,
            wrap_lines=True,
            read_only=Condition(lambda: bool(cli_ref._command_running)),
            history=FileHistory(str(self._history_file)),
            # complete_while_typing fires the completer on every keystroke. The
            # completer does blocking work — fuzzy @-file indexing shells out to
            # rg/fd (up to a 2s timeout) and path completion hits os.listdir/stat
            # — so running it inline would stall the render loop on each key (very
            # noticeable on WSL2/slow filesystems). ThreadedCompleter moves it off
            # the UI event loop, keeping typing responsive.
            completer=ThreadedCompleter(_completer),
            complete_while_typing=True,
            auto_suggest=SlashCommandAutoSuggest(
                history_suggest=AutoSuggestFromHistory(),
                completer=_completer,
            ),
        )
        # Keep prompt_toolkit on its simple tempfile path. Setting
        # buffer.tempfile = "prompt.md" triggers its complex-tempfile branch,
        # which tries to mkdir() the mkdtemp() directory again and raises
        # EEXIST. The suffix keeps markdown highlighting without that bug.
        input_area.buffer.tempfile_suffix = '.md'

        # Dynamic height: accounts for both explicit newlines AND visual
        # wrapping of long lines so the input area always fits its content.
        def _input_height():
            try:
                from prompt_toolkit.application import get_app

                doc = input_area.buffer.document
                try:
                    terminal_columns = get_app().output.get_size().columns
                except Exception:
                    terminal_columns = shutil.get_terminal_size((80, 24)).columns
                return _estimate_tui_input_height(
                    doc.lines,
                    self._get_tui_prompt_text(),
                    terminal_columns,
                )
            except Exception:
                return 1

        input_area.window.height = _input_height

        # Paste collapsing: detect large pastes and save to temp file
        _paste_counter = [0]
        _prev_text_len = [0]
        _prev_newline_count = [0]
        _paste_just_collapsed = [False]
        self._skip_paste_collapse = False

        def _on_text_changed(buf):
            """Detect large pastes and collapse them to a file reference.

            When bracketed paste is available, handle_paste collapses
            large pastes directly.  This handler is a fallback for
            terminals without bracketed paste support.

            Two heuristics (either triggers collapse):
            1. Many characters added at once (chars_added > 1) — works
               when the terminal delivers the paste in one event-loop tick.
            2. Newline count jumped by 4+ in a single text-change event —
               catches terminals that feed characters individually but
               still batch newlines.  Alt+Enter only adds 1 newline per
               event so it never triggers this.
            """
            text = _strip_leaked_bracketed_paste_wrappers(buf.text)
            text, _had_mouse_reports = _strip_leaked_terminal_responses_with_meta(text)
            if _had_mouse_reports:
                self._recover_terminal_input_modes(reason="mouse reports leaked into prompt buffer")
            if text != buf.text:
                cursor = min(buf.cursor_position, len(text))
                _paste_just_collapsed[0] = True
                buf.text = text
                buf.cursor_position = cursor
                _prev_text_len[0] = len(text)
                _prev_newline_count[0] = text.count('\n')
                return
            chars_added = len(text) - _prev_text_len[0]
            _prev_text_len[0] = len(text)
            if _paste_just_collapsed[0] or self._skip_paste_collapse:
                _paste_just_collapsed[0] = False
                self._skip_paste_collapse = False
                _prev_newline_count[0] = text.count('\n')
                return
            line_count = text.count('\n')
            newlines_added = line_count - _prev_newline_count[0]
            _prev_newline_count[0] = line_count
            is_paste = chars_added > 1 or newlines_added >= 4
            threshold = self.config.get("paste_collapse_threshold_fallback", 5)
            char_threshold = self.config.get("paste_collapse_char_threshold", 2000)
            lines_hit = threshold > 0 and line_count >= threshold
            chars_hit = char_threshold > 0 and len(text) >= char_threshold
            if (lines_hit or chars_hit) and is_paste and not text.startswith('/'):
                _paste_counter[0] += 1
                paste_dir = _hermes_home / "pastes"
                paste_dir.mkdir(parents=True, exist_ok=True)
                paste_file = paste_dir / f"paste_{_paste_counter[0]}_{datetime.now().strftime('%H%M%S')}.txt"
                paste_file.write_text(text, encoding="utf-8")
                logger.info("Collapsed paste #%d: %d lines, %d chars -> %s (fallback)", _paste_counter[0], line_count + 1, len(text), paste_file)
                _paste_just_collapsed[0] = True
                buf.text = f"[Pasted text #{_paste_counter[0]}: {line_count + 1} lines \u2192 {paste_file}]"
                buf.cursor_position = len(buf.text)

        input_area.buffer.on_text_changed += _on_text_changed

        # --- Input processors for password masking and inline placeholder ---

        # Mask input with '*' when the sudo password prompt is active
        input_area.control.input_processors.append(
            ConditionalProcessor(
                PasswordProcessor(),
                filter=Condition(
                    lambda: bool(cli_ref._sudo_state) or bool(cli_ref._secret_state)
                ),
            )
        )

        class _PlaceholderProcessor(Processor):
            """Render grayed-out placeholder text inside the input when empty."""
            def __init__(self, get_text):
                self._get_text = get_text

            def apply_transformation(self, ti):
                if not ti.document.text and ti.lineno == 0:
                    text = self._get_text()
                    if text:
                        # Append after existing fragments (preserves the ❯ prompt)
                        return Transformation(fragments=ti.fragments + [('class:placeholder', text)])
                return Transformation(fragments=ti.fragments)

        def _get_placeholder():
            if cli_ref._voice_recording:
                _label = cli_ref._voice_record_key_label()
                return f"recording... {_label} to stop, Ctrl+C to cancel"
            if cli_ref._voice_processing:
                return "transcribing..."
            if cli_ref._sudo_state:
                return "type password (hidden), Enter to submit · ESC to skip"
            if cli_ref._secret_state:
                return "type secret (hidden), Enter to submit · ESC to skip"
            if cli_ref._approval_state:
                return ""
            if cli_ref._slash_confirm_state:
                return "type 1/2/3, or use ↑/↓ then Enter"
            if cli_ref._clarify_freetext:
                return "type your answer here and press Enter"
            if cli_ref._clarify_state:
                return ""
            if cli_ref._command_running:
                frame = cli_ref._command_spinner_frame()
                status = cli_ref._command_status or "Processing command..."
                return f"{frame} {status}"
            if cli_ref._agent_running:
                return "msg=interrupt · /queue · /bg · /steer · Ctrl+C cancel"
            if cli_ref._voice_mode:
                _label = cli_ref._voice_record_key_label()
                return f"type or {_label} to record"
            return ""

        input_area.control.input_processors.append(_PlaceholderProcessor(_get_placeholder))

        # Hint line above input: shown only for interactive prompts that need
        # extra instructions (sudo countdown, approval navigation, clarify).
        # The agent-running interrupt hint is now an inline placeholder above.
        def get_hint_text():
            if cli_ref._sudo_state:
                remaining = max(0, int(cli_ref._sudo_deadline - time.monotonic()))
                return [
                    ('class:hint', '  password hidden · Enter to skip'),
                    ('class:clarify-countdown', f'  ({remaining}s)'),
                ]

            if cli_ref._secret_state:
                remaining = max(0, int(cli_ref._secret_deadline - time.monotonic()))
                return [
                    ('class:hint', '  secret hidden · Enter to skip'),
                    ('class:clarify-countdown', f'  ({remaining}s)'),
                ]

            if cli_ref._approval_state:
                remaining = max(0, int(cli_ref._approval_deadline - time.monotonic()))
                return [
                    ('class:hint', '  ↑/↓ to select, Enter to confirm'),
                    ('class:clarify-countdown', f'  ({remaining}s)'),
                ]

            if cli_ref._slash_confirm_state:
                remaining = max(0, int(cli_ref._slash_confirm_deadline - time.monotonic()))
                return [
                    ('class:hint', '  type 1/2/3, or ↑/↓ to select, Enter to confirm'),
                    ('class:clarify-countdown', f'  ({remaining}s)'),
                ]

            if cli_ref._clarify_state:
                remaining = max(0, int(cli_ref._clarify_deadline - time.monotonic()))
                countdown = f'  ({remaining}s)' if cli_ref._clarify_deadline else ''
                if cli_ref._clarify_freetext:
                    return [
                        ('class:hint', '  type your answer and press Enter'),
                        ('class:clarify-countdown', countdown),
                    ]
                return [
                    ('class:hint', '  ↑/↓ to select, Enter to confirm'),
                    ('class:clarify-countdown', countdown),
                ]

            if cli_ref._command_running:
                frame = cli_ref._command_spinner_frame()
                return [
                    ('class:hint', f'  {frame} command in progress · input temporarily disabled'),
                ]

            return []

        def get_hint_height():
            if cli_ref._sudo_state or cli_ref._secret_state or cli_ref._approval_state or cli_ref._slash_confirm_state or cli_ref._clarify_state or cli_ref._command_running:
                return 1
            # Keep a spacer while the agent runs on roomy terminals, but reclaim
            # the row on narrow/mobile screens where every line matters.
            return cli_ref._agent_spacer_height()

        def get_spinner_text():
            spinner_line = cli_ref._render_spinner_text()
            if not spinner_line:
                return []
            return [('class:hint', spinner_line)]

        def get_spinner_height():
            return cli_ref._spinner_widget_height()

        spinner_widget = Window(
            content=FormattedTextControl(get_spinner_text),
            height=get_spinner_height,
            wrap_lines=True,
        )

        # Petdex mascot — right-aligned half-block sprite above the prompt,
        # mirroring the TUI's PetPane. Collapses to height 0 when no pet is
        # enabled, so it's a no-op for everyone else. The _pet_anim_loop thread
        # advances frames + invalidates; align=RIGHT pins it to the edge.
        self._pet_widget = Window(
            content=FormattedTextControl(self._pet_fragments),
            height=self._pet_widget_height,
            align=WindowAlign.RIGHT,
        )

        spacer = Window(
            content=FormattedTextControl(get_hint_text),
            height=get_hint_height,
        )

        # --- Clarify tool: dynamic display widget for questions + choices ---

        def _panel_box_width(title: str, content_lines: list[str], min_width: int = 46, max_width: int = 76) -> int:
            """Choose a stable panel width wide enough for the title and content."""
            term_cols = shutil.get_terminal_size((100, 20)).columns
            longest = max([len(title)] + [len(line) for line in content_lines] + [min_width - 4])
            inner = min(max(longest + 4, min_width - 2), max_width - 2, max(24, term_cols - 6))
            return inner + 2  # account for the single leading/trailing spaces inside borders

        def _wrap_panel_text(text: str, width: int, subsequent_indent: str = "") -> list[str]:
            wrapped = textwrap.wrap(
                text,
                width=max(8, width),
                break_long_words=False,
                break_on_hyphens=False,
                subsequent_indent=subsequent_indent,
            )
            return wrapped or [""]

        def _append_panel_line(lines, border_style: str, content_style: str, text: str, box_width: int) -> None:
            inner_width = max(0, box_width - 2)
            lines.append((border_style, "│ "))
            lines.append((content_style, text.ljust(inner_width)))
            lines.append((border_style, " │\n"))

        def _append_blank_panel_line(lines, border_style: str, box_width: int) -> None:
            lines.append((border_style, "│" + (" " * box_width) + "│\n"))

        def _get_clarify_display():
            """Build styled text for the clarify question/choices panel.

            Layout priority: choices + Other option must always render even if
            the question is very long. The question is budgeted to leave enough
            rows for the choices and trailing chrome; anything over the budget
            is truncated with a marker.
            """
            state = cli_ref._clarify_state
            if not state:
                return []

            question = state["question"]
            choices = state.get("choices") or []
            selected = state.get("selected", 0)
            preview_lines = _wrap_panel_text(question, 60)
            for i, choice in enumerate(choices):
                # Show number prefix for quick selection (1-9 for items 1-9, 0 for 10th item)
                if i < 9:
                    num_prefix = str(i + 1)
                elif i == 9:
                    num_prefix = '0'
                else:
                    num_prefix = ' '
                if i == selected and not cli_ref._clarify_freetext:
                    prefix = f"❯ {num_prefix}. "
                else:
                    prefix = f"  {num_prefix}. "
                preview_lines.extend(_wrap_panel_text(f"{prefix}{choice}", 60, subsequent_indent="    "))
            # "Other" option in preview
            other_num = len(choices) + 1
            if other_num < 10:
                other_num_prefix = str(other_num)
            elif other_num == 10:
                other_num_prefix = '0'
            else:
                other_num_prefix = ' '
            other_label = (
                f"❯ {other_num_prefix}. Other (type below)" if cli_ref._clarify_freetext
                else f"❯ {other_num_prefix}. Other (type your answer)" if selected == len(choices)
                else f"  {other_num_prefix}. Other (type your answer)"
            )
            preview_lines.extend(_wrap_panel_text(other_label, 60, subsequent_indent="    "))
            box_width = _panel_box_width("Hermes needs your input", preview_lines)
            inner_text_width = max(8, box_width - 2)

            # Pre-wrap choices + Other option — these are mandatory.
            choice_wrapped: list[tuple[int, str]] = []
            if choices:
                for i, choice in enumerate(choices):
                    # Show number prefix for quick selection (1-9 for items 1-9, 0 for 10th item)
                    if i < 9:
                        num_prefix = str(i + 1)
                    elif i == 9:
                        num_prefix = '0'
                    else:
                        num_prefix = ' '
                    if i == selected and not cli_ref._clarify_freetext:
                        prefix = f'❯ {num_prefix}. '
                    else:
                        prefix = f'  {num_prefix}. '
                    for wrapped in _wrap_panel_text(f"{prefix}{choice}", inner_text_width, subsequent_indent="    "):
                        choice_wrapped.append((i, wrapped))
                # Trailing Other row(s)
                other_idx = len(choices)
                other_num = other_idx + 1
                if other_num < 10:
                    other_num_prefix = str(other_num)
                elif other_num == 10:
                    other_num_prefix = '0'
                else:
                    other_num_prefix = ' '
                if selected == other_idx and not cli_ref._clarify_freetext:
                    other_label_mand = f'❯ {other_num_prefix}. Other (type your answer)'
                elif cli_ref._clarify_freetext:
                    other_label_mand = f'❯ {other_num_prefix}. Other (type below)'
                else:
                    other_label_mand = f'  {other_num_prefix}. Other (type your answer)'
                other_wrapped = _wrap_panel_text(other_label_mand, inner_text_width, subsequent_indent="    ")
            elif cli_ref._clarify_freetext:
                # Freetext-only mode: the guidance line takes the place of choices.
                other_wrapped = _wrap_panel_text(
                    "Type your answer in the prompt below, then press Enter.",
                    inner_text_width,
                )
            else:
                other_wrapped = []

            # Budget the question so mandatory rows always render.
            # Chrome layouts:
            #   full : top border + blank_after_title + blank_after_question
            #          + blank_before_bottom + bottom border = 5 rows
            #   tight: top border + bottom border = 2 rows (drop all blanks)
            #
            # reserved_below matches the approval-panel budget (~6 rows for
            # spinner/tool-progress + status + input + separators + prompt).
            term_rows = shutil.get_terminal_size((100, 24)).lines
            chrome_full = 5
            chrome_tight = 2
            reserved_below = 6

            available = max(0, term_rows - reserved_below)
            # The compact decision must reserve room for at least one question
            # row on top of the choices, otherwise full chrome (3 blank
            # separators) gets kept when there is no room for it and the panel
            # overflows the viewport — HSplit then clips the panel's tail,
            # silently dropping the choices (the reported bug).
            mandatory_full = chrome_full + 1 + len(choice_wrapped) + len(other_wrapped)

            use_compact_chrome = mandatory_full > available
            chrome_rows = chrome_tight if use_compact_chrome else chrome_full

            max_question_rows = max(1, available - chrome_rows - len(choice_wrapped) - len(other_wrapped))
            max_question_rows = min(max_question_rows, 12)  # soft cap on huge terminals

            # When the choices alone (plus compact chrome) already exceed the
            # viewport, drop the question entirely — the choices are the only
            # thing the user must see to make a selection. Without this the
            # question would still claim its 1-row floor above and push the
            # tail of the choices off-screen (HSplit clips the overflow).
            choices_overflow = chrome_rows + len(choice_wrapped) + len(other_wrapped) >= available
            if choices_overflow:
                max_question_rows = 0

            question_wrapped = _wrap_panel_text(question, inner_text_width)
            if max_question_rows <= 0:
                question_wrapped = []
            elif len(question_wrapped) > max_question_rows:
                # The truncation marker is itself a row, so it must count
                # against the budget. With a 1-row budget there is no room for
                # both a question line and the marker — show the marker alone
                # so the rendered question never exceeds max_question_rows.
                keep = max(0, max_question_rows - 1)
                question_wrapped = question_wrapped[:keep] + ["… (question truncated)"]

            lines = []
            # Box top border
            lines.append(('class:clarify-border', '╭─ '))
            lines.append(('class:clarify-title', 'Hermes needs your input'))
            lines.append(('class:clarify-border', ' ' + ('─' * max(0, box_width - len("Hermes needs your input") - 3)) + '╮\n'))
            if not use_compact_chrome:
                _append_blank_panel_line(lines, 'class:clarify-border', box_width)

            # Question text (bounded)
            for wrapped in question_wrapped:
                _append_panel_line(lines, 'class:clarify-border', 'class:clarify-question', wrapped, box_width)
            if not use_compact_chrome:
                _append_blank_panel_line(lines, 'class:clarify-border', box_width)

            if cli_ref._clarify_freetext and not choices:
                for wrapped in other_wrapped:
                    _append_panel_line(lines, 'class:clarify-border', 'class:clarify-choice', wrapped, box_width)
                if not use_compact_chrome:
                    _append_blank_panel_line(lines, 'class:clarify-border', box_width)

            if choices:
                # Multiple-choice mode: show selectable options
                for i, wrapped in choice_wrapped:
                    style = 'class:clarify-selected' if i == selected and not cli_ref._clarify_freetext else 'class:clarify-choice'
                    _append_panel_line(lines, 'class:clarify-border', style, wrapped, box_width)

                # "Other" option (trailing row(s), only shown when choices exist)
                other_idx = len(choices)
                # Calculate number prefix for "Other" option
                other_num = other_idx + 1
                if other_num < 10:
                    other_num_prefix = str(other_num)
                elif other_num == 10:
                    other_num_prefix = '0'
                else:
                    other_num_prefix = ' '
                
                if selected == other_idx and not cli_ref._clarify_freetext:
                    other_style = 'class:clarify-selected'
                elif cli_ref._clarify_freetext:
                    other_style = 'class:clarify-active-other'
                else:
                    other_style = 'class:clarify-choice'
                for wrapped in other_wrapped:
                    _append_panel_line(lines, 'class:clarify-border', other_style, wrapped, box_width)

            if not use_compact_chrome:
                _append_blank_panel_line(lines, 'class:clarify-border', box_width)
            lines.append(('class:clarify-border', '╰' + ('─' * box_width) + '╯\n'))
            return lines

        clarify_widget = ConditionalContainer(
            Window(
                FormattedTextControl(_get_clarify_display),
                wrap_lines=True,
            ),
            filter=Condition(lambda: cli_ref._clarify_state is not None),
        )

        # --- Sudo password: display widget ---

        def _get_sudo_display():
            state = cli_ref._sudo_state
            if not state:
                return []
            title = '🔐 Sudo Password Required'
            body = 'Enter password below (hidden), or press Enter to skip'
            box_width = _panel_box_width(title, [body])
            lines = []
            lines.append(('class:sudo-border', '╭─ '))
            lines.append(('class:sudo-title', title))
            lines.append(('class:sudo-border', ' ' + ('─' * max(0, box_width - len(title) - 3)) + '╮\n'))
            _append_blank_panel_line(lines, 'class:sudo-border', box_width)
            _append_panel_line(lines, 'class:sudo-border', 'class:sudo-text', body, box_width)
            _append_blank_panel_line(lines, 'class:sudo-border', box_width)
            lines.append(('class:sudo-border', '╰' + ('─' * box_width) + '╯\n'))
            return lines

        sudo_widget = ConditionalContainer(
            Window(
                FormattedTextControl(_get_sudo_display),
                wrap_lines=True,
            ),
            filter=Condition(lambda: cli_ref._sudo_state is not None),
        )

        def _get_secret_display():
            state = cli_ref._secret_state
            if not state:
                return []

            title = '🔑 Skill Setup Required'
            prompt = state.get("prompt") or f"Enter value for {state.get('var_name', 'secret')}"
            metadata = state.get("metadata") or {}
            help_text = metadata.get("help")
            body = 'Enter secret below (hidden), ESC or Ctrl+C to skip'
            content_lines = [prompt, body]
            if help_text:
                content_lines.insert(1, str(help_text))
            box_width = _panel_box_width(title, content_lines)
            lines = []
            lines.append(('class:sudo-border', '╭─ '))
            lines.append(('class:sudo-title', title))
            lines.append(('class:sudo-border', ' ' + ('─' * max(0, box_width - len(title) - 3)) + '╮\n'))
            _append_blank_panel_line(lines, 'class:sudo-border', box_width)
            _append_panel_line(lines, 'class:sudo-border', 'class:sudo-text', prompt, box_width)
            if help_text:
                _append_panel_line(lines, 'class:sudo-border', 'class:sudo-text', str(help_text), box_width)
            _append_blank_panel_line(lines, 'class:sudo-border', box_width)
            _append_panel_line(lines, 'class:sudo-border', 'class:sudo-text', body, box_width)
            _append_blank_panel_line(lines, 'class:sudo-border', box_width)
            lines.append(('class:sudo-border', '╰' + ('─' * box_width) + '╯\n'))
            return lines

        secret_widget = ConditionalContainer(
            Window(
                FormattedTextControl(_get_secret_display),
                wrap_lines=True,
            ),
            filter=Condition(lambda: cli_ref._secret_state is not None),
        )

        # --- Dangerous command approval: display widget ---

        def _get_approval_display():
            return cli_ref._get_approval_display_fragments()

        approval_widget = ConditionalContainer(
            Window(
                FormattedTextControl(_get_approval_display),
                wrap_lines=True,
            ),
            filter=Condition(lambda: cli_ref._approval_state is not None),
        )

        def _get_slash_confirm_display():
            return cli_ref._get_slash_confirm_display_fragments()

        slash_confirm_widget = ConditionalContainer(
            Window(
                FormattedTextControl(_get_slash_confirm_display),
                wrap_lines=True,
            ),
            filter=Condition(lambda: cli_ref._slash_confirm_state is not None),
        )

        # --- /model picker: display widget ---
        def _get_model_picker_display():
            state = cli_ref._model_picker_state
            if not state:
                return []
            stage = state.get("stage", "provider")
            if stage == "provider":
                title = "⚙ Model Picker — Select Provider"
                choices = []
                _providers = state.get("providers")
                for p in _providers if isinstance(_providers, list) else []:
                    count = p.get("total_models", len(p.get("models", [])))
                    label = f"{p['name']} ({count} model{'s' if count != 1 else ''})"
                    if p.get("is_current"):
                        label += "  ← current"
                    choices.append(label)
                choices.append("Cancel")
                hint = f"Current: {state.get('current_model', 'unknown')} on {state.get('current_provider', 'unknown')}"
            else:
                provider_data = state.get("provider_data") or {}
                model_list = state.get("model_list") or []
                title = f"⚙ Model Picker — {provider_data.get('name', provider_data.get('slug', 'Provider'))}"
                choices = list(model_list) + ["← Back", "Cancel"]
                if model_list:
                    hint = f"Select a model ({len(model_list)} available)"
                else:
                    hint = "No models listed for this provider. Use Back or Cancel."

            box_width = _panel_box_width(title, [hint] + choices, min_width=46, max_width=84)
            inner_text_width = max(8, box_width - 6)
            selected = state.get("selected", 0)

            # Scrolling viewport: the panel renders into a Window with no max
            # height, so without limiting visible items the bottom border and
            # any items past the available terminal rows get clipped on long
            # provider catalogs (e.g. Ollama Cloud's 36+ models).
            try:
                from prompt_toolkit.application import get_app
                term_rows = get_app().output.get_size().rows
            except Exception:
                term_rows = shutil.get_terminal_size((100, 24)).lines
            scroll_offset, visible = HermesCLI._compute_model_picker_viewport(
                selected, state.get("_scroll_offset", 0), len(choices), term_rows,
            )
            state["_scroll_offset"] = scroll_offset

            lines = []
            lines.append(('class:clarify-border', '╭─ '))
            lines.append(('class:clarify-title', title))
            lines.append(('class:clarify-border', ' ' + ('─' * max(0, box_width - len(title) - 3)) + '╮\n'))
            _append_blank_panel_line(lines, 'class:clarify-border', box_width)
            _append_panel_line(lines, 'class:clarify-border', 'class:clarify-hint', hint, box_width)
            _append_blank_panel_line(lines, 'class:clarify-border', box_width)
            for idx in range(scroll_offset, scroll_offset + visible):
                choice = choices[idx]
                style = 'class:clarify-selected' if idx == selected else 'class:clarify-choice'
                prefix = '❯ ' if idx == selected else '  '
                for wrapped in _wrap_panel_text(prefix + choice, inner_text_width, subsequent_indent='  '):
                    _append_panel_line(lines, 'class:clarify-border', style, wrapped, box_width)
            _append_blank_panel_line(lines, 'class:clarify-border', box_width)
            lines.append(('class:clarify-border', '╰' + ('─' * box_width) + '╯\n'))
            return lines

        model_picker_widget = ConditionalContainer(
            Window(
                FormattedTextControl(_get_model_picker_display),
                wrap_lines=True,
            ),
            filter=Condition(lambda: cli_ref._model_picker_state is not None),
        )

        # Horizontal rules above and below the input.
        # On narrow/mobile terminals we keep the top separator for structure but
        # hide the bottom one to recover a full row for conversation content.
        input_rule_top = Window(
            char='─',
            height=lambda: cli_ref._tui_input_rule_height("top"),
            style='class:input-rule',
        )
        input_rule_bot = Window(
            char='─',
            height=lambda: cli_ref._tui_input_rule_height("bottom"),
            style='class:input-rule',
        )

        # Image attachment indicator — shows badges like [📎 Image #1] above input
        cli_ref = self

        def _get_image_bar():
            if not cli_ref._attached_images:
                return []
            badges = _format_image_attachment_badges(
                cli_ref._attached_images,
                cli_ref._image_counter,
            )
            return [("class:image-badge", f" {badges} ")]

        image_bar = Window(
            content=FormattedTextControl(_get_image_bar),
            height=Condition(lambda: bool(cli_ref._attached_images)),
        )

        # Persistent voice mode status bar (visible only when voice mode is on)
        def _get_voice_status():
            return cli_ref._get_voice_status_fragments()

        voice_status_bar = ConditionalContainer(
            Window(
                FormattedTextControl(_get_voice_status),
                height=1,
            ),
            filter=Condition(lambda: cli_ref._voice_mode),
        )

        status_bar = ConditionalContainer(
            Window(
                content=FormattedTextControl(lambda: cli_ref._get_status_bar_fragments()),
                height=1,
                # Prevent fragments that overflow the terminal width from
                # wrapping onto a second line, which causes the status bar to
                # appear duplicated (one full + one partial row) during long
                # sessions, especially on SSH where shutil.get_terminal_size
                # may return stale values.  _get_status_bar_fragments now reads
                # width from prompt_toolkit's own output object, so fragments
                # will always fit; wrap_lines=False is the belt-and-suspenders
                # guard against any future width mismatch.
                wrap_lines=False,
            ),
            filter=Condition(
                lambda: cli_ref._status_bar_visible
                and not getattr(cli_ref, "_status_bar_suppressed_after_resize", False)
            ),
        )

        # Allow wrapper CLIs to register extra keybindings.
        self._register_extra_tui_keybindings(kb, input_area=input_area)

        # Layout: interactive prompt widgets + ruled input at bottom.
        # The sudo, approval, and clarify widgets appear above the input when
        # the corresponding interactive prompt is active.
        completions_menu = CompletionsMenu(max_height=12, scroll_offset=1)

        layout = Layout(
            HSplit(
                self._build_tui_layout_children(
                    sudo_widget=sudo_widget,
                    secret_widget=secret_widget,
                    approval_widget=approval_widget,
                    slash_confirm_widget=slash_confirm_widget,
                    clarify_widget=clarify_widget,
                    model_picker_widget=model_picker_widget,
                    spinner_widget=spinner_widget,
                    spacer=spacer,
                    status_bar=status_bar,
                    input_rule_top=input_rule_top,
                    image_bar=image_bar,
                    input_area=input_area,
                    input_rule_bot=input_rule_bot,
                    voice_status_bar=voice_status_bar,
                    completions_menu=completions_menu,
                )
            )
        )
        
        # Style for the application
        self._tui_style_base = {
            # Input area / prompt: empty style strings inherit the
            # terminal's default foreground/background, so the typed
            # text is readable in both light and dark Terminal.app
            # color schemes.  (Hardcoding a near-white #FFF8DC made
            # input invisible on light backgrounds.)
            'input-area': '',
            'placeholder': '#888888 italic',
            'prompt': '',
            'prompt-working': '#888888 italic',
            'hint': '#888888 italic',
            'status-bar': 'bg:#1a1a2e #C0C0C0',
            'status-bar-strong': 'bg:#1a1a2e #FFD700 bold',
            'status-bar-dim': 'bg:#1a1a2e #8B8682',
            'status-bar-good': 'bg:#1a1a2e #8FBC8F bold',
            'status-bar-warn': 'bg:#1a1a2e #FFD700 bold',
            'status-bar-bad': 'bg:#1a1a2e #FF8C00 bold',
            'status-bar-critical': 'bg:#1a1a2e #FF6B6B bold',
            'status-bar-yolo': 'bg:#1a1a2e #FF4444 bold',
            # Bronze horizontal rules around the input area
            'input-rule': '#CD7F32',
            # Clipboard image attachment badges
            'image-badge': '#87CEEB bold',
            'completion-menu': 'bg:#1a1a2e #FFF8DC',
            'completion-menu.completion': 'bg:#1a1a2e #FFF8DC',
            'completion-menu.completion.current': 'bg:#333355 #FFD700',
            'completion-menu.meta.completion': 'bg:#1a1a2e #888888',
            'completion-menu.meta.completion.current': 'bg:#333355 #FFBF00',
            # Clarify question panel
            'clarify-border': '#CD7F32',
            'clarify-title': '#FFD700 bold',
            'clarify-question': '#FFF8DC bold',
            'clarify-choice': '#AAAAAA',
            'clarify-selected': '#FFD700 bold',
            'clarify-active-other': '#FFD700 italic',
            'clarify-countdown': '#CD7F32',
            # Sudo password panel
            'sudo-prompt': '#FF6B6B bold',
            'sudo-border': '#CD7F32',
            'sudo-title': '#FF6B6B bold',
            'sudo-text': '#FFF8DC',
            # Dangerous command approval panel
            'approval-border': '#CD7F32',
            'approval-title': '#FF8C00 bold',
            'approval-desc': '#FFF8DC bold',
            'approval-cmd': '#AAAAAA italic',
            'approval-choice': '#AAAAAA',
            'approval-selected': '#FFD700 bold',
            # Voice mode
            'voice-prompt': '#87CEEB',
            'voice-recording': '#FF4444 bold',
            'voice-processing': '#FFA500 italic',
            'voice-status': 'bg:#1a1a2e #87CEEB',
            'voice-status-recording': 'bg:#1a1a2e #FF4444 bold',
        }
        style = PTStyle.from_dict(self._build_tui_style_dict())

        # Disable CPR (Cursor Position Report) at the source so prompt_toolkit
        # never sends ESC[6n cursor-position queries — but only on terminals
        # where the reply is likely to leak. Over SSH/cloudflared tunnels and
        # slow PTYs the CPR replies (ESC[<row>;<col>R) leak into the display as
        # raw "20;1R21;1R" text and can stall the renderer's pending-CPR future,
        # freezing the prompt after the agent's final answer (#13870). CPR is a
        # layout hint, not a speed optimization, and it works fine locally, so we
        # leave prompt_toolkit's default untouched on local terminals and only
        # suppress it where the bug reproduces. None (local, or build failure)
        # falls back to the default output; the input-side scrubbing in
        # _strip_leaked_terminal_responses still guards against any leaks.
        _cpr_disabled_output = (
            _build_cpr_disabled_output(sys.stdout)
            if _terminal_may_leak_cpr()
            else None
        )

        # Create the application
        app = Application(
            layout=layout,
            key_bindings=kb,
            style=style,
            full_screen=False,
            mouse_support=False,
            **({"output": _cpr_disabled_output} if _cpr_disabled_output is not None else {}),
            # Read from display.cli_refresh_interval (default 0 = disabled).
            # When non-zero, prompt_toolkit redraws the UI on this cadence
            # during idle, keeping wall-clock status-bar read-outs ticking.
            # Set to 0 to suppress background redraws entirely — avoids
            # fighting terminal auto-scroll in non-fullscreen mode (Xshell,
            # iTerm2, Windows Terminal). See #48309.
            refresh_interval=float(CLI_CONFIG.get("display", {}).get("cli_refresh_interval", 0)),
            # Erase the live bottom chrome (status bar, input box, separator
            # rules) on exit instead of freezing a final copy into scrollback.
            # Without this, prompt_toolkit's render_as_done teardown repaints
            # the chrome one last time and leaves it stranded above the exit
            # summary — so a dead status bar + empty prompt sit between the
            # conversation transcript and the "Resume this session" block, and
            # stack with the next session's UI on resume (#38252). The actual
            # conversation transcript is printed through patch_stdout into
            # normal scrollback and is unaffected; only the managed chrome is
            # erased. Applies to every exit path (/exit, /quit, EOF, Ctrl+C).
            erase_when_done=True,
            **({'cursor': _STEADY_CURSOR} if _STEADY_CURSOR is not None else {}),
        )
||||||| cf299e9a01
        self.show_banner()
        # Surface any active supply-chain security advisories right after the
        # welcome banner. Quiet/single-query paths call this themselves.
        self._show_security_advisories()
        # If resuming a session, load history and display it immediately
        # so the user has context before typing their first message.
        if self._resumed:
            if self._preload_resumed_session():
                self._display_resumed_history()

        try:
            from openagents_cli.skin_engine import get_active_skin
            _welcome_skin = get_active_skin()
            _welcome_text = _welcome_skin.get_branding("welcome", "Welcome to OpenAgents! Type your message or /help for commands.")
            _welcome_color = _welcome_skin.get_color("banner_text", "#FFF8DC")
        except Exception:
            _welcome_text = "Welcome to OpenAgents! Type your message or /help for commands."
            _welcome_color = "#FFF8DC"
        self._console_print(f"[{_welcome_color}]{_welcome_text}[/]")

        # Warm the /model picker's provider-models cache off-thread during this
        # idle window (banner shown, user about to type). The no-args picker
        # otherwise blocks ~1-2s on serial /v1/models fetches the first time
        # it's opened in a session. Fire-and-forget, guarded once-per-process.
        try:
            from openagents_cli.model_switch import prewarm_picker_cache_async
            prewarm_picker_cache_async()
        except Exception:
            pass

        # Redaction opt-out warning (#17691): ON by default, loud when off.
        # The redactor snapshots its state at import time so any toggle now
        # won't affect the running process — we just want the operator to
        # see that they're running without the safety net.
        try:
            _redact_raw = os.getenv("HERMES_REDACT_SECRETS", "true")
            if _redact_raw.lower() not in {"1", "true", "yes", "on"}:
                self._console_print(
                    "[bold red]⚠  Secret redaction is DISABLED[/] "
                    f"(HERMES_REDACT_SECRETS={_redact_raw}). "
                    "API keys and tokens may appear verbatim in chat output, "
                    "session JSONs, and logs. Set "
                    "[cyan]security.redact_secrets: true[/] in config.yaml "
                    "to re-enable."
                )
        except Exception:
            pass
        # First-time OpenClaw-residue banner — fires once if ~/.openclaw/ exists
        # after an OpenClaw→Hermes migration (especially migrations done by
        # OpenClaw's own tool, which doesn't archive the source directory).
        try:
            from agent.onboarding import (
                OPENCLAW_RESIDUE_FLAG,
                detect_openclaw_residue,
                is_seen,
                mark_seen,
                openclaw_residue_hint_cli,
            )
            if not is_seen(self.config, OPENCLAW_RESIDUE_FLAG) and detect_openclaw_residue():
                try:
                    _resid_color = _welcome_skin.get_color("banner_dim", "#B8860B")
                except Exception:
                    _resid_color = "#B8860B"
                self._console_print(f"[{_resid_color}]{openclaw_residue_hint_cli()}[/]")
                try:
                    from openagents_cli.config import get_config_path as _get_cfg_path_resid
                    mark_seen(_get_cfg_path_resid(), OPENCLAW_RESIDUE_FLAG)
                except Exception:
                    pass  # best-effort — banner will fire again next session
        except Exception:
            pass  # banner is non-critical — never break startup
        # Show a random tip to help users discover features
        try:
            from openagents_cli.tips import get_random_tip
            _tip = get_random_tip()
            try:
                _tip_color = _welcome_skin.get_color("banner_dim", "#B8860B")
            except Exception:
                _tip_color = "#B8860B"
            self._console_print(f"[dim {_tip_color}]✦ Tip: {_tip}[/]")
        except Exception:
            pass  # Tips are non-critical — never break startup

        # Curator — kick off a background skill-maintenance pass on startup
        # if the schedule says we're due.  Runs in a daemon thread so it
        # never blocks the interactive loop.  Best-effort; any failure is
        # swallowed to avoid breaking session startup.
        try:
            from agent.curator import maybe_run_curator
            maybe_run_curator(
                idle_for_seconds=float("inf"),  # CLI startup = fully idle
                on_summary=lambda msg: self._console_print(
                    f"[dim #6b7684]💾 {msg}[/]"
                ),
            )
        except Exception:
            pass
        if self.preloaded_skills and not self._startup_skills_line_shown:
            skills_label = ", ".join(self.preloaded_skills)
            self._console_print(
                f"[bold {_accent_hex()}]Activated skills:[/] {skills_label}"
            )
            self._startup_skills_line_shown = True
        self._console_print()
        
        # State for async operation
        self._agent_running = False
        self._pending_input = queue.Queue()     # For normal input (commands + new queries)
        self._interrupt_queue = queue.Queue()   # For messages typed while agent is running
        # See constructor note. Mirrored here for the run() path that skips
        # the earlier __init__ branch.
        self._last_turn_interrupted = False
        self._should_exit = False
        self._last_ctrl_c_time = 0  # Track double Ctrl+C for force exit

        # Give plugin manager a CLI reference so plugins can inject messages
        from openagents_cli.plugins import get_plugin_manager
        get_plugin_manager()._cli_ref = self

        # Config file watcher — detect mcp_servers changes and auto-reload
        from openagents_cli.config import get_config_path as _get_config_path
        _cfg_path = _get_config_path()
        self._config_mtime: float = _cfg_path.stat().st_mtime if _cfg_path.exists() else 0.0
        self._config_mcp_servers: dict = self.config.get("mcp_servers") or {}
        self._last_config_check: float = 0.0  # monotonic time of last check

        # Clarify tool state: interactive question/answer with the user.
        # When the agent calls the clarify tool, _clarify_state is set and
        # the prompt_toolkit UI switches to a selection mode.
        self._clarify_state = None      # dict with question, choices, selected, response_queue
        self._clarify_freetext = False  # True when user chose "Other" and is typing
        self._clarify_deadline = 0      # monotonic timestamp when the clarify times out

        # Sudo password prompt state (similar mechanism to clarify)
        self._sudo_state = None         # dict with response_queue when active
        self._sudo_deadline = 0
        self._modal_input_snapshot = None

        # Dangerous command approval state (similar mechanism to clarify)
        self._approval_state = None     # dict with command, description, choices, selected, response_queue
        self._approval_deadline = 0
        self._approval_lock = threading.Lock()  # serialize concurrent approval prompts (delegation race fix)

        # Destructive slash-command confirmation state (/new, /clear, /undo).
        # These prompts are answered through the prompt_toolkit composer, not
        # raw input(), so the option labels stay visible and Enter does not EOF
        # the whole app.
        self._slash_confirm_state = None
        self._slash_confirm_deadline = 0

        # Slash command loading state
        self._command_running = False
        self._command_status = ""

        # Secure secret capture state for skill setup
        self._secret_state = None       # dict with var_name, prompt, metadata, response_queue
        self._secret_deadline = 0

        # Clipboard image attachments (paste images into the CLI)
        self._attached_images: list[Path] = []
        self._image_counter = 0

        # Voice mode state (protected by _voice_lock for cross-thread access)
        self._voice_lock = threading.Lock()
        self._voice_mode = False        # Whether voice mode is enabled
        self._voice_tts = False         # Whether TTS output is enabled
        self._voice_recorder = None     # AudioRecorder instance (lazy init)
        self._voice_recording = False   # Whether currently recording
        self._voice_processing = False  # Whether STT is in progress
        self._voice_continuous = False  # Whether to auto-restart after agent responds
        self._voice_tts_done = threading.Event()  # Signals TTS playback finished
        self._voice_tts_done.set()  # Initially "done" (no TTS pending)

        if os.environ.get("HERMES_DEFER_AGENT_STARTUP") != "1":
            self._install_tool_callbacks()

        if os.environ.get("HERMES_DEFER_AGENT_STARTUP") != "1":
            self._ensure_tirith_security()
        
        # Key bindings for the input area
        kb = KeyBindings()

        from prompt_toolkit.keys import Keys as _IgnoreKeys

        @kb.add(_IgnoreKeys.Ignore, eager=True)
        def handle_ignored_terminal_sequence(event):
            """Consume parser-level ignored terminal sequences before self-insert.

            install_ignored_terminal_sequences() in openagents_cli.pt_input_extras
            registers focus reports (CSI I / CSI O) as Keys.Ignore at the
            VT100 parser level. Without this no-op binding the default
            self-insert path would still fire and the bytes would land in
            the buffer.
            """
            return None

        def handle_enter(event):
            """Handle Enter key - submit input.
            
            Routes to the correct queue based on active UI state:
            - Sudo password prompt: password goes to sudo response queue
            - Approval selection: selected choice goes to approval response queue
            - Clarify freetext mode: answer goes to the clarify response queue
            - Clarify choice mode: selected choice goes to the clarify response queue
            - Agent running: goes to _interrupt_queue (chat() monitors this)
            - Agent idle: goes to _pending_input (process_loop monitors this)
            Commands (starting with /) always go to _pending_input so they're
            handled as commands, not sent as interrupt text to the agent.
            """
            # --- Sudo password prompt: submit the typed password ---
            if self._sudo_state:
                text = event.app.current_buffer.text
                self._sudo_state["response_queue"].put(text)
                self._sudo_state = None
                event.app.invalidate()
                return

            # --- Secret prompt: submit the typed secret ---
            if self._secret_state:
                text = event.app.current_buffer.text
                self._submit_secret_response(text)
                event.app.current_buffer.reset()
                event.app.invalidate()
                return

            # --- Approval selection: confirm the highlighted choice ---
            if self._approval_state:
                self._handle_approval_selection()
                event.app.invalidate()
                return

            # --- Slash-command confirmation: submit typed or highlighted choice ---
            if self._slash_confirm_state:
                text = event.app.current_buffer.text.strip()
                choices = self._slash_confirm_state.get("choices") or []
                choice = self._normalize_slash_confirm_choice(text, choices) if text else None
                if choice is None:
                    selected = self._slash_confirm_state.get("selected", 0)
                    if 0 <= selected < len(choices):
                        choice = choices[selected][0]
                self._submit_slash_confirm_response(choice or "cancel")
                event.app.current_buffer.reset()
                event.app.invalidate()
                return

            # --- /model picker modal ---
            if self._model_picker_state:
                try:
                    # Picker selections persist by default (same default as
                    # /model <name>); honour model.persist_switch_by_default.
                    from openagents_cli.model_switch import resolve_persist_behavior

                    self._handle_model_picker_selection(
                        persist_global=resolve_persist_behavior(False, False)
                    )
                except Exception as _exc:
                    _cprint(f"  ✗ Model selection failed: {_exc}")
                    self._close_model_picker()
                event.app.current_buffer.reset()
                event.app.invalidate()
                return

            # --- Clarify freetext mode: user typed their own answer ---
            if self._clarify_freetext and self._clarify_state:
                text = event.app.current_buffer.text.strip()
                if text:
                    self._clarify_state["response_queue"].put(text)
                    self._clarify_state = None
                    self._clarify_freetext = False
                    event.app.current_buffer.reset()
                    event.app.invalidate()
                return

            # --- Clarify choice mode: confirm the highlighted selection ---
            if self._clarify_state and not self._clarify_freetext:
                state = self._clarify_state
                selected = state["selected"]
                choices = state.get("choices") or []
                if selected < len(choices):
                    state["response_queue"].put(choices[selected])
                    self._clarify_state = None
                    event.app.invalidate()
                else:
                    # "Other" selected → switch to freetext
                    self._clarify_freetext = True
                    event.app.invalidate()
                return

            # --- Normal input routing ---
            text = event.app.current_buffer.text.strip()
            has_images = bool(self._attached_images)
            if text or has_images:
                # Handle /model directly on the UI thread so interactive pickers
                # can safely use prompt_toolkit terminal handoff helpers.
                if self._should_handle_model_command_inline(text, has_images=has_images):
                    if not self.process_command(text):
                        self._should_exit = True
                        if event.app.is_running:
                            event.app.exit()
                    event.app.current_buffer.reset(append_to_history=True)
                    # Force a repaint: process_command() prints through
                    # patch_stdout (scrolls output above the prompt) and never
                    # invalidates the app, so the just-cleared input area can
                    # keep showing the submitted text until some unrelated
                    # redraw fires. Every other early-return branch in this
                    # handler invalidates after reset — match them.
                    event.app.invalidate()
                    return

                # Handle /steer while the agent is running immediately on the
                # UI thread.  Queuing through _pending_input would deadlock the
                # steer until after the agent loop finishes (process_loop is
                # blocked inside self.chat()), which turns /steer into a
                # post-run next-turn message — defeating mid-run injection.
                # agent.steer() is thread-safe (holds _pending_steer_lock).
                if self._should_handle_steer_command_inline(text, has_images=has_images):
                    self.process_command(text)
                    event.app.current_buffer.reset(append_to_history=True)
                    # Force a repaint after clearing the buffer.  /steer is
                    # dispatched mid-run while the agent streams output through
                    # patch_stdout; process_command() never invalidates the
                    # app, so without this the submitted "/steer <text>" can
                    # linger in the input area (looking unsent) and invite an
                    # accidental re-submit. See issue #34569.
                    event.app.invalidate()
                    return

                # Snapshot and clear attached images
                images = list(self._attached_images)
                self._attached_images.clear()
                event.app.invalidate()
                # Bundle text + images as a tuple when images are present
                payload = (text, images) if images else text
                if self._agent_running and not (text and _looks_like_slash_command(text)):
                    _effective_mode = self.busy_input_mode
                    if _effective_mode == "steer":
                        # Route Enter through /steer — inject mid-run after the
                        # next tool call.  Images can't ride along (steer only
                        # appends text), so fall back to queue when images are
                        # attached.  If the agent lacks steer() or rejects the
                        # payload, also fall back to queue so nothing is lost.
                        if images or not text:
                            _effective_mode = "queue"
                        else:
                            accepted = False
                            try:
                                if self.agent is not None and hasattr(self.agent, "steer"):
                                    accepted = bool(self.agent.steer(text))
                            except Exception as exc:
                                _cprint(f"  {_DIM}Steer failed ({exc}) — queued for next turn.{_RST}")
                                accepted = False
                            if accepted:
                                preview = text[:80] + ("..." if len(text) > 80 else "")
                                _cprint(f"  {_ACCENT}⏩ Steered: '{preview}'{_RST}")
                            else:
                                _effective_mode = "queue"
                    if _effective_mode == "queue":
                        # Queue for the next turn instead of interrupting
                        self._pending_input.put(payload)
                        preview = text if text else f"[{len(images)} image{'s' if len(images) != 1 else ''} attached]"
                        _cprint(f"  Queued for the next turn: {preview[:80]}{'...' if len(preview) > 80 else ''}")
                    elif _effective_mode == "interrupt":
                        self._interrupt_queue.put(payload)
                        # Debug: log to file when message enters interrupt queue
                        try:
                            _dbg = _hermes_home / "interrupt_debug.log"
                            with open(_dbg, "a", encoding="utf-8") as _f:
                                _f.write(f"{time.strftime('%H:%M:%S')} ENTER: queued interrupt msg={str(payload)[:60]!r}, "
                                         f"agent_running={self._agent_running}\n")
                        except Exception:
                            pass
                    # First-touch onboarding: on the very first busy-while-running
                    # event for this install, print a one-line tip explaining the
                    # /busy knob.  Flag persists to config.yaml and never fires
                    # again.  Guarded for exceptions so onboarding can't break
                    # the input loop.
                    try:
                        from agent.onboarding import (
                            BUSY_INPUT_FLAG,
                            busy_input_hint_cli,
                            is_seen,
                            mark_seen,
                        )
                        if not is_seen(CLI_CONFIG, BUSY_INPUT_FLAG):
                            _cprint(f"  {_DIM}{busy_input_hint_cli(self.busy_input_mode)}{_RST}")
                            mark_seen(_hermes_home / "config.yaml", BUSY_INPUT_FLAG)
                            CLI_CONFIG.setdefault("onboarding", {}).setdefault("seen", {})[BUSY_INPUT_FLAG] = True
                    except Exception:
                        pass
                else:
                    self._pending_input.put(payload)
                event.app.current_buffer.reset(append_to_history=True)

        _bind_prompt_submit_keys(kb, handle_enter)
        
        @kb.add('escape', 'enter')
        def handle_alt_enter(event):
            """Alt+Enter inserts a newline for multi-line input.

            Works on mac/Linux/WSL. On Windows Terminal this keystroke is
            intercepted at the terminal layer (toggles fullscreen) and never
            reaches here — Windows users get newline via Ctrl+Enter instead
            (bound below as c-j, since WT delivers Ctrl+Enter as LF).
            """
            event.current_buffer.insert_text('\n')

        if _preserve_ctrl_enter_newline():
            @kb.add('c-j')
            def handle_ctrl_enter_newline(event):
                """Ctrl+Enter inserts a newline on Windows, WSL, SSH, and WT.

                Windows Terminal (incl. WSL/SSH sessions through it) delivers
                Ctrl+Enter as LF (c-j), distinct from plain Enter (c-m). This
                binding makes Ctrl+Enter the equivalent of Alt+Enter on those
                terminals, giving an Enter-involving newline keystroke
                without requiring terminal settings changes. Ctrl+J (the raw
                LF keystroke) also triggers this by virtue of being the same
                key code — a harmless side effect since Ctrl+J has no
                conflicting OpenAgents binding. See issue #22379.
                """
                event.current_buffer.insert_text('\n')

        # VSCode/Cursor bind Ctrl+G to "Find Next" at the editor level, so
        # the keystroke never reaches the embedded terminal. Alt+G is unbound
        # in those IDEs and arrives here as ('escape', 'g') — register it as
        # a fallback so the editor handoff works inside Cursor/VSCode too.
        _editor_filter = Condition(
            lambda: not self._clarify_state and not self._approval_state and not self._sudo_state and not self._secret_state
        )

        @kb.add('c-g', filter=_editor_filter)
        @kb.add('escape', 'g', filter=_editor_filter)
        def handle_open_in_editor(event):
            """Ctrl+G (or Alt+G in VSCode/Cursor) opens the current draft in an external editor."""
            cli_ref._open_external_editor(event.current_buffer)

        @kb.add('tab', eager=True)
        def handle_tab(event):
            """Tab: accept completion, auto-suggestion, or start completions.

            Priority:
            1. Completion menu open → accept selected completion
            2. Ghost text suggestion available → accept auto-suggestion
            3. Otherwise → start completion menu

            After accepting a provider like 'anthropic:', the completion menu
            closes and complete_while_typing doesn't fire (no keystroke).
            This binding re-triggers completions so stage-2 models appear
            immediately.
            """
            buf = event.current_buffer
            if buf.complete_state:
                # Completion menu is open — accept the selection
                completion = buf.complete_state.current_completion
                if completion is None:
                    # Menu open but nothing selected — select first then grab it
                    buf.go_to_completion(0)
                    completion = buf.complete_state and buf.complete_state.current_completion
                if completion is None:
                    return
                # Accept the selected completion
                buf.apply_completion(completion)
            elif buf.suggestion and buf.suggestion.text:
                # No completion menu, but there's a ghost text auto-suggestion — accept it
                buf.insert_text(buf.suggestion.text)
            else:
                # No menu and no suggestion — start completions from scratch
                buf.start_completion()

        # --- Clarify tool: arrow-key navigation for multiple-choice questions ---

        @kb.add('up', filter=Condition(lambda: bool(self._clarify_state) and not self._clarify_freetext))
        def clarify_up(event):
            """Move selection up in clarify choices."""
            if self._clarify_state:
                self._clarify_state["selected"] = max(0, self._clarify_state["selected"] - 1)
                event.app.invalidate()

        @kb.add('down', filter=Condition(lambda: bool(self._clarify_state) and not self._clarify_freetext))
        def clarify_down(event):
            """Move selection down in clarify choices."""
            if self._clarify_state:
                choices = self._clarify_state.get("choices") or []
                max_idx = len(choices)  # last index is the "Other" option
                self._clarify_state["selected"] = min(max_idx, self._clarify_state["selected"] + 1)
                event.app.invalidate()

        # Number keys for quick clarify selection (1-9, 0 for 10th item)
        def _make_clarify_number_handler(idx):
            def handler(event):
                if self._clarify_state and not self._clarify_freetext:
                    choices = self._clarify_state.get("choices") or []
                    # Map index to choice (treating "Other" as the last option)
                    if idx < len(choices):
                        # Select a numbered choice
                        self._clarify_state["response_queue"].put(choices[idx])
                        self._clarify_state = None
                        self._clarify_freetext = False
                        event.app.invalidate()
                    elif idx == len(choices):
                        # Select "Other" option
                        self._clarify_freetext = True
                        event.app.invalidate()
            return handler

        for _num in range(10):
            # 1-9 select items 0-8, 0 selects item 9 (10thitem)
            _idx = 9 if _num == 0 else _num - 1
            kb.add(str(_num), filter=Condition(lambda: bool(self._clarify_state) and not self._clarify_freetext))(_make_clarify_number_handler(_idx))

        # --- Dangerous command approval: arrow-key navigation ---

        @kb.add('up', filter=Condition(lambda: bool(self._approval_state)))
        def approval_up(event):
            if self._approval_state:
                self._approval_state["selected"] = max(0, self._approval_state["selected"] - 1)
                event.app.invalidate()

        @kb.add('down', filter=Condition(lambda: bool(self._approval_state)))
        def approval_down(event):
            if self._approval_state:
                max_idx = len(self._approval_state["choices"]) - 1
                self._approval_state["selected"] = min(max_idx, self._approval_state["selected"] + 1)
                event.app.invalidate()

        # --- Slash-command confirmation: arrow-key navigation ---
        @kb.add('up', filter=Condition(lambda: bool(self._slash_confirm_state)))
        def slash_confirm_up(event):
            if self._slash_confirm_state:
                self._slash_confirm_state["selected"] = max(0, self._slash_confirm_state.get("selected", 0) - 1)
                event.app.invalidate()

        @kb.add('down', filter=Condition(lambda: bool(self._slash_confirm_state)))
        def slash_confirm_down(event):
            if self._slash_confirm_state:
                max_idx = len(self._slash_confirm_state.get("choices") or []) - 1
                self._slash_confirm_state["selected"] = min(max_idx, self._slash_confirm_state.get("selected", 0) + 1)
                event.app.invalidate()

        # --- /model picker: arrow-key navigation ---
        @kb.add('up', filter=Condition(lambda: bool(self._model_picker_state)))
        def model_picker_up(event):
            if self._model_picker_state:
                self._model_picker_state["selected"] = max(0, self._model_picker_state.get("selected", 0) - 1)
                event.app.invalidate()

        @kb.add('down', filter=Condition(lambda: bool(self._model_picker_state)))
        def model_picker_down(event):
            state = self._model_picker_state
            if not state:
                return
            if state.get("stage") == "provider":
                max_idx = len(state.get("providers") or [])
            else:
                max_idx = len(state.get("model_list") or []) + 1
            state["selected"] = min(max_idx, state.get("selected", 0) + 1)
            event.app.invalidate()

        @kb.add('escape', filter=Condition(lambda: bool(self._model_picker_state)), eager=True)
        def model_picker_escape(event):
            """ESC closes the /model picker."""
            self._close_model_picker()
            event.app.current_buffer.reset()
            event.app.invalidate()

        # Number keys for quick approval selection (1-9, 0 for 10th item)
        def _make_approval_number_handler(idx):
            def handler(event):
                if self._approval_state and idx < len(self._approval_state["choices"]):
                    self._approval_state["selected"] = idx
                    self._handle_approval_selection()
                    event.app.invalidate()
            return handler

        for _num in range(10):
            # 1-9 select items 0-8, 0 selects item 9 (10th item)
            _idx = 9 if _num == 0 else _num - 1
            kb.add(str(_num), filter=Condition(lambda: bool(self._approval_state)))(_make_approval_number_handler(_idx))

        # Number keys for quick slash-confirm selection (1-9, 0 for 10th item)
        def _make_slash_confirm_number_handler(idx):
            def handler(event):
                if self._slash_confirm_state and idx < len(self._slash_confirm_state.get("choices") or []):
                    choice = self._slash_confirm_state["choices"][idx][0]
                    self._submit_slash_confirm_response(choice)
                    event.app.current_buffer.reset()
                    event.app.invalidate()
            return handler

        for _num in range(10):
            _idx = 9 if _num == 0 else _num - 1
            kb.add(str(_num), filter=Condition(lambda: bool(self._slash_confirm_state)))(_make_slash_confirm_number_handler(_idx))

        # --- History navigation: up/down browse history in normal input mode ---
        # The TextArea is multiline, so by default up/down only move the cursor.
        # Buffer.auto_up/auto_down handle both: cursor movement when multi-line,
        # history browsing when on the first/last line (or single-line input).
        _normal_input = Condition(
            lambda: not self._clarify_state and not self._approval_state and not self._slash_confirm_state and not self._sudo_state and not self._secret_state and not self._model_picker_state
        )

        @kb.add('up', filter=_normal_input)
        def history_up(event):
            """Up arrow: browse history when on first line, else move cursor up."""
            event.app.current_buffer.auto_up(count=event.arg)

        @kb.add('down', filter=_normal_input)
        def history_down(event):
            """Down arrow: browse history when on last line, else move cursor down."""
            event.app.current_buffer.auto_down(count=event.arg)

        @kb.add('c-l')
        def handle_ctrl_l(event):
            """Ctrl+L: force a clean full-screen repaint.

            Recovers the UI after external terminal buffer drift — tmux /
            cmux tab switches, ``clear`` from a subshell, SSH window
            restores, etc. — that prompt_toolkit can't detect on its own.
            Matches the universal bash/zsh/fish/vim/htop convention.
            """
            self._force_full_redraw()

        @kb.add('c-c')
        def handle_ctrl_c(event):
            """Handle Ctrl+C - cancel interactive prompts, interrupt agent, or exit.
            
            Priority:
            0. Cancel active voice recording
            1. Cancel active sudo/approval/clarify prompt
            2. Interrupt the running agent (first press)
            3. Force exit (second press within 2s, or when idle)
            """
            now = time.time()

            # Cancel active voice recording.
            # Run cancel() in a background thread to prevent blocking the
            # event loop if AudioRecorder._lock or CoreAudio takes time.
            _should_cancel_voice = False
            _recorder_ref = None
            with cli_ref._voice_lock:
                if cli_ref._voice_recording and cli_ref._voice_recorder:
                    _recorder_ref = cli_ref._voice_recorder
                    cli_ref._voice_recording = False
                    cli_ref._voice_continuous = False
                    _should_cancel_voice = True
            if _should_cancel_voice:
                _cprint(f"\n{_DIM}Recording cancelled.{_RST}")
                threading.Thread(
                    target=_recorder_ref.cancel, daemon=True
                ).start()
                event.app.invalidate()
                return

            # Cancel sudo prompt
            if self._sudo_state:
                self._sudo_state["response_queue"].put("")
                self._sudo_state = None
                event.app.invalidate()
                return

            # Cancel secret prompt
            if self._secret_state:
                self._cancel_secret_capture()
                event.app.current_buffer.reset()
                event.app.invalidate()
                return

            # Cancel approval prompt (deny)
            if self._approval_state:
                self._approval_state["response_queue"].put("deny")
                self._approval_state = None
                event.app.invalidate()
                return

            # Cancel slash confirmation prompt
            if self._slash_confirm_state:
                self._submit_slash_confirm_response("cancel")
                event.app.current_buffer.reset()
                event.app.invalidate()
                return

            # Cancel /model picker
            if self._model_picker_state:
                self._close_model_picker()
                event.app.current_buffer.reset()
                event.app.invalidate()
                return

            # Cancel clarify prompt
            if self._clarify_state:
                self._clarify_state["response_queue"].put(
                    "The user cancelled. Use your best judgement to proceed."
                )
                self._clarify_state = None
                self._clarify_freetext = False
                event.app.current_buffer.reset()
                event.app.invalidate()
                return

            if self._agent_running and self.agent:
                if now - self._last_ctrl_c_time < 2.0:
                    print("\n⚡ Force exiting...")
                    self._should_exit = True
                    event.app.exit()
                    return
                
                self._last_ctrl_c_time = now
                print("\n⚡ Interrupting agent... (press Ctrl+C again to force exit)")
                self.agent.interrupt()
            # If there's text or images, clear them (like bash).
            # If everything is already empty, exit.
            elif event.app.current_buffer.text or self._attached_images:
                event.app.current_buffer.reset()
                self._attached_images.clear()
                event.app.invalidate()
            else:
                self._should_exit = True
                event.app.exit()

        # Ctrl+Shift+C: no binding needed. Terminal emulators (GNOME Terminal,
        # iTerm2, kitty, Windows Terminal, etc.) intercept Ctrl+Shift+C before
        # the keystroke reaches the application's stdin — prompt_toolkit never
        # sees it, and prompt_toolkit's key spec parser doesn't even recognise
        # 'c-S-c' anyway (the Shift modifier is meaningless on control-sequence
        # keys). #19884 added a handler for this; #19895 patched the resulting
        # startup crash with try/except. Both were based on a misreading of how
        # terminal key events propagate. Deleting the dead handler outright.

        @kb.add('c-q')  # Ctrl+Q
        def handle_ctrl_q(event):
            """Alternative interrupt/exit shortcut (Ctrl+Q).

            Behaves like Ctrl+C: cancels active prompts, interrupts the
            running agent, or clears the input buffer. Does not support
            the double-press 'force exit' feature of Ctrl+C.
            """
            # Cancel active voice recording.
            _should_cancel_voice = False
            _recorder_ref = None
            with cli_ref._voice_lock:
                if cli_ref._voice_recording and cli_ref._voice_recorder:
                    _recorder_ref = cli_ref._voice_recorder
                    cli_ref._voice_recording = False
                    cli_ref._voice_continuous = False
                    _should_cancel_voice = True
            if _should_cancel_voice:
                _cprint(f"\n{_DIM}Recording cancelled.{_RST}")
                threading.Thread(
                    target=_recorder_ref.cancel, daemon=True
                ).start()
                event.app.invalidate()
                return

            # Cancel sudo prompt
            if self._sudo_state:
                self._sudo_state["response_queue"].put("")
                self._sudo_state = None
                event.app.invalidate()
                return

            # Cancel secret prompt
            if self._secret_state:
                self._cancel_secret_capture()
                event.app.current_buffer.reset()
                event.app.invalidate()
                return

            # Cancel approval prompt (deny)
            if self._approval_state:
                self._approval_state["response_queue"].put("deny")
                self._approval_state = None
                event.app.invalidate()
                return

            # Cancel slash confirmation prompt
            if self._slash_confirm_state:
                self._submit_slash_confirm_response("cancel")
                event.app.current_buffer.reset()
                event.app.invalidate()
                return

            # Cancel /model picker
            if self._model_picker_state:
                self._close_model_picker()
                event.app.current_buffer.reset()
                event.app.invalidate()
                return

            # Cancel clarify prompt
            if self._clarify_state:
                self._clarify_state["response_queue"].put(
                    "The user cancelled. Use your best judgement to proceed."
                )
                self._clarify_state = None
                self._clarify_freetext = False
                event.app.current_buffer.reset()
                event.app.invalidate()
                return

            if self._agent_running and self.agent:
                print("\n⚡ Interrupting agent...")
                self.agent.interrupt()
            elif event.app.current_buffer.text or self._attached_images:
                event.app.current_buffer.reset()
                self._attached_images.clear()
                event.app.invalidate()
            else:
                self._should_exit = True
                event.app.exit()

        @kb.add('c-d')
        def handle_ctrl_d(event):
            """Ctrl+D: delete char under cursor (standard readline behaviour).
            Only exit when the input is empty — same as bash/zsh. Pending
            attached images count as input and block the EOF-exit so the
            user doesn't lose them silently.
            """
            buf = event.app.current_buffer
            if buf.text:
                buf.delete()
            elif self._attached_images:
                # Empty text but pending attachments — no-op, don't exit.
                return
            else:
                self._should_exit = True
                event.app.exit()

        _modal_prompt_active = Condition(
            lambda: bool(self._secret_state or self._sudo_state or self._slash_confirm_state)
        )

        @kb.add('escape', filter=_modal_prompt_active, eager=True)
        def handle_escape_modal(event):
            """ESC cancels active secret/sudo prompts."""
            if self._secret_state:
                self._cancel_secret_capture()
                event.app.current_buffer.reset()
                event.app.invalidate()
                return
            if self._sudo_state:
                self._sudo_state["response_queue"].put("")
                self._sudo_state = None
                event.app.invalidate()
                return
            if self._slash_confirm_state:
                self._submit_slash_confirm_response("cancel")
                event.app.current_buffer.reset()
                event.app.invalidate()
                return

        @kb.add('c-z')
        def handle_ctrl_z(event):
            """Handle Ctrl+Z - suspend process to background (Unix only)."""
            if sys.platform == 'win32':
                _cprint(f"\n{_DIM}Suspend (Ctrl+Z) is not supported on Windows.{_RST}")
                event.app.invalidate()
                return
            import signal as _sig
            from prompt_toolkit.application import run_in_terminal
            from openagents_cli.skin_engine import get_active_skin
            agent_name = get_active_skin().get_branding("agent_name", "OpenAgents")
            msg = f"\n{agent_name} has been suspended. Run `fg` to bring {agent_name} back."
            def _suspend():
                os.write(1, msg.encode())
                os.kill(0, _sig.SIGTSTP)
            run_in_terminal(_suspend)

        # Voice push-to-talk key: configurable via config.yaml (voice.record_key)
        # Default: Ctrl+B (avoids conflict with Ctrl+R readline reverse-search).
        # Config spellings (ctrl/control/alt/option/opt) are normalized to
        # prompt_toolkit's c-x / a-x format via ``normalize_voice_record_key_for_prompt_toolkit``
        # so the same config value binds identically in the TUI and CLI
        # (Copilot round-9 review on #19835). ``super``/``win``/``windows``
        # configs silently fall back to the default here since prompt_toolkit
        # has no super modifier — log a warning so users notice the
        # TUI/CLI split instead of a silent mismatch (round-11).
        _raw_key: object = "ctrl+b"
        try:
            from openagents_cli.config import load_config
            from openagents_cli.voice import (
                normalize_voice_record_key_for_prompt_toolkit,
                voice_record_key_from_config,
            )
            _raw_key = voice_record_key_from_config(load_config())
            _voice_key = normalize_voice_record_key_for_prompt_toolkit(_raw_key)
            if (
                isinstance(_raw_key, str)
                and _raw_key.strip().lower().split("+", 1)[0].strip() in {"super", "win", "windows"}
                and _voice_key == "c-b"
            ):
                logger.warning(
                    "voice.record_key %r uses a TUI-only modifier (super/win); "
                    "CLI fell back to Ctrl+B. Use ctrl+<key> or alt+<key> for "
                    "cross-runtime parity.",
                    _raw_key,
                )
        except Exception:
            _voice_key = "c-b"

        # Cache the UI label here — same ``_raw_key`` that drives the
        # prompt_toolkit binding below. Every status / placeholder /
        # recording-hint render reads this cached value so display can
        # never drift from the live keybinding even if the user edits
        # voice.record_key mid-session (Copilot round-13 on #19835).
        self.set_voice_record_key_cache(_raw_key)

        @kb.add(_voice_key)
        def handle_voice_record(event):
            """Toggle voice recording when voice mode is active.

            IMPORTANT: This handler runs in prompt_toolkit's event-loop thread.
            Any blocking call here (locks, sd.wait, disk I/O) freezes the
            entire UI.  All heavy work is dispatched to daemon threads.
            """
            if not cli_ref._voice_mode:
                return
            # Always allow STOPPING a recording (even when agent is running)
            if cli_ref._voice_recording:
                # Manual stop via push-to-talk key: stop continuous mode
                with cli_ref._voice_lock:
                    cli_ref._voice_continuous = False
                # Flag clearing is handled atomically inside _voice_stop_and_transcribe
                event.app.invalidate()
                threading.Thread(
                    target=cli_ref._voice_stop_and_transcribe,
                    daemon=True,
                ).start()
            else:
                # Guard: don't START recording during agent run or interactive prompts
                if cli_ref._agent_running:
                    return
                if cli_ref._clarify_state or cli_ref._sudo_state or cli_ref._approval_state or cli_ref._slash_confirm_state:
                    return
                # Guard: don't start while a previous stop/transcribe cycle is
                # still running — recorder.stop() holds AudioRecorder._lock and
                # start() would block the event-loop thread waiting for it.
                if cli_ref._voice_processing:
                    return

                # Interrupt TTS if playing, so user can start talking.
                # stop_playback() is fast (just terminates a subprocess).
                if not cli_ref._voice_tts_done.is_set():
                    try:
                        from tools.voice_mode import stop_playback
                        stop_playback()
                        cli_ref._voice_tts_done.set()
                    except Exception:
                        pass

                with cli_ref._voice_lock:
                    cli_ref._voice_continuous = True

                # Dispatch to a daemon thread so play_beep(sd.wait),
                # AudioRecorder.start(lock acquire), and config I/O
                # never block the prompt_toolkit event loop.
                def _start_recording():
                    try:
                        cli_ref._voice_start_recording()
                        if hasattr(cli_ref, '_app') and cli_ref._app:
                            cli_ref._app.invalidate()
                    except Exception as e:
                        _cprint(f"\n{_DIM}Voice recording failed: {e}{_RST}")

                threading.Thread(target=_start_recording, daemon=True).start()
                event.app.invalidate()
        from prompt_toolkit.keys import Keys

        @kb.add(Keys.BracketedPaste, eager=True)
        def handle_paste(event):
            """Handle terminal paste — detect clipboard images.

            When the terminal supports bracketed paste, Ctrl+V / Cmd+V
            triggers this with the pasted text. We only auto-attach a
            clipboard image for image-only/empty paste gestures so text
            pastes and dictation do not accidentally attach stale images.

            Large pastes (5+ lines) are collapsed to a file reference
            placeholder while preserving any existing user text in the
            buffer.
            """
            # Diagnostic canary: measure how long the paste handler blocks
            # the prompt_toolkit event loop. If this exceeds ~500ms we log
            # it so recurring "CLI freezes on paste" reports (issue #16263,
            # macOS Tahoe 26 + iTerm2/Ghostty) arrive with data attached.
            _paste_handler_start = time.perf_counter()
            _paste_raw_size = len(event.data or "")
            pasted_text = event.data or ""
            # Normalise line endings — Windows \r\n and old Mac \r both become \n
            # so the 5-line collapse threshold and display are consistent.
            pasted_text = pasted_text.replace('\r\n', '\n').replace('\r', '\n')
            pasted_text = _strip_leaked_bracketed_paste_wrappers(pasted_text)
            pasted_text, _had_mouse_reports = _strip_leaked_terminal_responses_with_meta(pasted_text)
            if _had_mouse_reports:
                self._recover_terminal_input_modes(reason="mouse reports leaked into bracketed paste payload")
            if _should_auto_attach_clipboard_image_on_paste(pasted_text) and self._try_attach_clipboard_image():
                event.app.invalidate()
            if pasted_text:
                # Sanitize surrogate characters (e.g. from Word/Google Docs paste) before writing
                from run_agent import _sanitize_surrogates
                pasted_text = _sanitize_surrogates(pasted_text)
                line_count = pasted_text.count('\n')
                buf = event.current_buffer
                threshold = self.config.get("paste_collapse_threshold", 5)
                char_threshold = self.config.get("paste_collapse_char_threshold", 2000)
                lines_hit = threshold > 0 and line_count >= threshold
                chars_hit = char_threshold > 0 and len(pasted_text) >= char_threshold
                if (lines_hit or chars_hit) and not buf.text.strip().startswith('/'):
                    _paste_counter[0] += 1
                    paste_dir = _hermes_home / "pastes"
                    paste_dir.mkdir(parents=True, exist_ok=True)
                    paste_file = paste_dir / f"paste_{_paste_counter[0]}_{datetime.now().strftime('%H%M%S')}.txt"
                    paste_file.write_text(pasted_text, encoding="utf-8")
                    logger.info("Collapsed paste #%d: %d lines, %d chars -> %s", _paste_counter[0], line_count + 1, len(pasted_text), paste_file)
                    placeholder = f"[Pasted text #{_paste_counter[0]}: {line_count + 1} lines \u2192 {paste_file}]"
                    prefix = ""
                    if buf.cursor_position > 0 and buf.text[buf.cursor_position - 1] != '\n':
                        prefix = "\n"
                    _paste_just_collapsed[0] = True
                    buf.insert_text(prefix + placeholder)
                else:
                    buf.insert_text(pasted_text)
            _paste_handler_elapsed_ms = (time.perf_counter() - _paste_handler_start) * 1000.0
            if _paste_handler_elapsed_ms > 500.0:
                logger.warning(
                    "Slow bracketed-paste handler: %.1fms to process %d bytes "
                    "(%d lines) on %s. If the input becomes unresponsive after "
                    "this, attach this log line to the bug report.",
                    _paste_handler_elapsed_ms,
                    _paste_raw_size,
                    pasted_text.count('\n') + 1 if pasted_text else 0,
                    sys.platform,
                )

        @kb.add('c-v')
        def handle_ctrl_v(event):
            """Fallback image paste for terminals without bracketed paste.

            On Linux terminals (GNOME Terminal, Konsole, etc.), Ctrl+V
            sends raw byte 0x16 instead of triggering a paste.  This
            binding catches that and checks the clipboard for images.
            On terminals that DO intercept Ctrl+V for paste (macOS
            Terminal, iTerm2, VSCode, Windows Terminal), the bracketed
            paste handler fires instead and this binding never triggers.
            """
            if self._try_attach_clipboard_image():
                event.app.invalidate()

        @kb.add('escape', 'v')
        def handle_alt_v(event):
            """Alt+V — paste image from clipboard.

            Alt key combos pass through all terminal emulators (sent as
            ESC + key), unlike Ctrl+V which terminals intercept for text
            paste.  This is the reliable way to attach clipboard images
            on WSL2, VSCode, and any terminal over SSH where Ctrl+V
            can't reach the application for image-only clipboard.
            """
            if self._try_attach_clipboard_image():
                event.app.invalidate()
            else:
                # No image found — show a hint
                pass  # silent when no image (avoid noise on accidental press)

        # Dynamic prompt: shows OpenAgents symbol when agent is working,
        # or answer prompt when clarify freetext mode is active.
        cli_ref = self

        def get_prompt():
            return cli_ref._get_tui_prompt_fragments()

        # Create the input area with multiline (Alt+Enter), autocomplete, and paste handling
        from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
        from prompt_toolkit.completion import ThreadedCompleter


        _completer = SlashCommandCompleter(
            skill_commands_provider=lambda: get_skill_commands(),
            command_filter=cli_ref._command_available,
            skill_bundles_provider=lambda: get_skill_bundles(),
        )
        input_area = TextArea(
            height=Dimension(min=1, max=8, preferred=1),
            prompt=get_prompt,
            style='class:input-area',
            multiline=True,
            wrap_lines=True,
            read_only=Condition(lambda: bool(cli_ref._command_running)),
            history=FileHistory(str(self._history_file)),
            # complete_while_typing fires the completer on every keystroke. The
            # completer does blocking work — fuzzy @-file indexing shells out to
            # rg/fd (up to a 2s timeout) and path completion hits os.listdir/stat
            # — so running it inline would stall the render loop on each key (very
            # noticeable on WSL2/slow filesystems). ThreadedCompleter moves it off
            # the UI event loop, keeping typing responsive.
            completer=ThreadedCompleter(_completer),
            complete_while_typing=True,
            auto_suggest=SlashCommandAutoSuggest(
                history_suggest=AutoSuggestFromHistory(),
                completer=_completer,
            ),
        )
        # Keep prompt_toolkit on its simple tempfile path. Setting
        # buffer.tempfile = "prompt.md" triggers its complex-tempfile branch,
        # which tries to mkdir() the mkdtemp() directory again and raises
        # EEXIST. The suffix keeps markdown highlighting without that bug.
        input_area.buffer.tempfile_suffix = '.md'

        # Dynamic height: accounts for both explicit newlines AND visual
        # wrapping of long lines so the input area always fits its content.
        def _input_height():
            try:
                from prompt_toolkit.application import get_app

                doc = input_area.buffer.document
                try:
                    terminal_columns = get_app().output.get_size().columns
                except Exception:
                    terminal_columns = shutil.get_terminal_size((80, 24)).columns
                return _estimate_tui_input_height(
                    doc.lines,
                    self._get_tui_prompt_text(),
                    terminal_columns,
                )
            except Exception:
                return 1

        input_area.window.height = _input_height

        # Paste collapsing: detect large pastes and save to temp file
        _paste_counter = [0]
        _prev_text_len = [0]
        _prev_newline_count = [0]
        _paste_just_collapsed = [False]
        self._skip_paste_collapse = False

        def _on_text_changed(buf):
            """Detect large pastes and collapse them to a file reference.

            When bracketed paste is available, handle_paste collapses
            large pastes directly.  This handler is a fallback for
            terminals without bracketed paste support.

            Two heuristics (either triggers collapse):
            1. Many characters added at once (chars_added > 1) — works
               when the terminal delivers the paste in one event-loop tick.
            2. Newline count jumped by 4+ in a single text-change event —
               catches terminals that feed characters individually but
               still batch newlines.  Alt+Enter only adds 1 newline per
               event so it never triggers this.
            """
            text = _strip_leaked_bracketed_paste_wrappers(buf.text)
            text, _had_mouse_reports = _strip_leaked_terminal_responses_with_meta(text)
            if _had_mouse_reports:
                self._recover_terminal_input_modes(reason="mouse reports leaked into prompt buffer")
            if text != buf.text:
                cursor = min(buf.cursor_position, len(text))
                _paste_just_collapsed[0] = True
                buf.text = text
                buf.cursor_position = cursor
                _prev_text_len[0] = len(text)
                _prev_newline_count[0] = text.count('\n')
                return
            chars_added = len(text) - _prev_text_len[0]
            _prev_text_len[0] = len(text)
            if _paste_just_collapsed[0] or self._skip_paste_collapse:
                _paste_just_collapsed[0] = False
                self._skip_paste_collapse = False
                _prev_newline_count[0] = text.count('\n')
                return
            line_count = text.count('\n')
            newlines_added = line_count - _prev_newline_count[0]
            _prev_newline_count[0] = line_count
            is_paste = chars_added > 1 or newlines_added >= 4
            threshold = self.config.get("paste_collapse_threshold_fallback", 5)
            char_threshold = self.config.get("paste_collapse_char_threshold", 2000)
            lines_hit = threshold > 0 and line_count >= threshold
            chars_hit = char_threshold > 0 and len(text) >= char_threshold
            if (lines_hit or chars_hit) and is_paste and not text.startswith('/'):
                _paste_counter[0] += 1
                paste_dir = _hermes_home / "pastes"
                paste_dir.mkdir(parents=True, exist_ok=True)
                paste_file = paste_dir / f"paste_{_paste_counter[0]}_{datetime.now().strftime('%H%M%S')}.txt"
                paste_file.write_text(text, encoding="utf-8")
                logger.info("Collapsed paste #%d: %d lines, %d chars -> %s (fallback)", _paste_counter[0], line_count + 1, len(text), paste_file)
                _paste_just_collapsed[0] = True
                buf.text = f"[Pasted text #{_paste_counter[0]}: {line_count + 1} lines \u2192 {paste_file}]"
                buf.cursor_position = len(buf.text)

        input_area.buffer.on_text_changed += _on_text_changed

        # --- Input processors for password masking and inline placeholder ---

        # Mask input with '*' when the sudo password prompt is active
        input_area.control.input_processors.append(
            ConditionalProcessor(
                PasswordProcessor(),
                filter=Condition(
                    lambda: bool(cli_ref._sudo_state) or bool(cli_ref._secret_state)
                ),
            )
        )

        class _PlaceholderProcessor(Processor):
            """Render grayed-out placeholder text inside the input when empty."""
            def __init__(self, get_text):
                self._get_text = get_text

            def apply_transformation(self, ti):
                if not ti.document.text and ti.lineno == 0:
                    text = self._get_text()
                    if text:
                        # Append after existing fragments (preserves the ❯ prompt)
                        return Transformation(fragments=ti.fragments + [('class:placeholder', text)])
                return Transformation(fragments=ti.fragments)

        def _get_placeholder():
            if cli_ref._voice_recording:
                _label = cli_ref._voice_record_key_label()
                return f"recording... {_label} to stop, Ctrl+C to cancel"
            if cli_ref._voice_processing:
                return "transcribing..."
            if cli_ref._sudo_state:
                return "type password (hidden), Enter to submit · ESC to skip"
            if cli_ref._secret_state:
                return "type secret (hidden), Enter to submit · ESC to skip"
            if cli_ref._approval_state:
                return ""
            if cli_ref._slash_confirm_state:
                return "type 1/2/3, or use ↑/↓ then Enter"
            if cli_ref._clarify_freetext:
                return "type your answer here and press Enter"
            if cli_ref._clarify_state:
                return ""
            if cli_ref._command_running:
                frame = cli_ref._command_spinner_frame()
                status = cli_ref._command_status or "Processing command..."
                return f"{frame} {status}"
            if cli_ref._agent_running:
                return "msg=interrupt · /queue · /bg · /steer · Ctrl+C cancel"
            if cli_ref._voice_mode:
                _label = cli_ref._voice_record_key_label()
                return f"type or {_label} to record"
            return ""

        input_area.control.input_processors.append(_PlaceholderProcessor(_get_placeholder))

        # Hint line above input: shown only for interactive prompts that need
        # extra instructions (sudo countdown, approval navigation, clarify).
        # The agent-running interrupt hint is now an inline placeholder above.
        def get_hint_text():
            if cli_ref._sudo_state:
                remaining = max(0, int(cli_ref._sudo_deadline - time.monotonic()))
                return [
                    ('class:hint', '  password hidden · Enter to skip'),
                    ('class:clarify-countdown', f'  ({remaining}s)'),
                ]

            if cli_ref._secret_state:
                remaining = max(0, int(cli_ref._secret_deadline - time.monotonic()))
                return [
                    ('class:hint', '  secret hidden · Enter to skip'),
                    ('class:clarify-countdown', f'  ({remaining}s)'),
                ]

            if cli_ref._approval_state:
                remaining = max(0, int(cli_ref._approval_deadline - time.monotonic()))
                return [
                    ('class:hint', '  ↑/↓ to select, Enter to confirm'),
                    ('class:clarify-countdown', f'  ({remaining}s)'),
                ]

            if cli_ref._slash_confirm_state:
                remaining = max(0, int(cli_ref._slash_confirm_deadline - time.monotonic()))
                return [
                    ('class:hint', '  type 1/2/3, or ↑/↓ to select, Enter to confirm'),
                    ('class:clarify-countdown', f'  ({remaining}s)'),
                ]

            if cli_ref._clarify_state:
                remaining = max(0, int(cli_ref._clarify_deadline - time.monotonic()))
                countdown = f'  ({remaining}s)' if cli_ref._clarify_deadline else ''
                if cli_ref._clarify_freetext:
                    return [
                        ('class:hint', '  type your answer and press Enter'),
                        ('class:clarify-countdown', countdown),
                    ]
                return [
                    ('class:hint', '  ↑/↓ to select, Enter to confirm'),
                    ('class:clarify-countdown', countdown),
                ]

            if cli_ref._command_running:
                frame = cli_ref._command_spinner_frame()
                return [
                    ('class:hint', f'  {frame} command in progress · input temporarily disabled'),
                ]

            return []

        def get_hint_height():
            if cli_ref._sudo_state or cli_ref._secret_state or cli_ref._approval_state or cli_ref._slash_confirm_state or cli_ref._clarify_state or cli_ref._command_running:
                return 1
            # Keep a spacer while the agent runs on roomy terminals, but reclaim
            # the row on narrow/mobile screens where every line matters.
            return cli_ref._agent_spacer_height()

        def get_spinner_text():
            spinner_line = cli_ref._render_spinner_text()
            if not spinner_line:
                return []
            return [('class:hint', spinner_line)]

        def get_spinner_height():
            return cli_ref._spinner_widget_height()

        spinner_widget = Window(
            content=FormattedTextControl(get_spinner_text),
            height=get_spinner_height,
            wrap_lines=True,
        )

        # Petdex mascot — right-aligned half-block sprite above the prompt,
        # mirroring the TUI's PetPane. Collapses to height 0 when no pet is
        # enabled, so it's a no-op for everyone else. The _pet_anim_loop thread
        # advances frames + invalidates; align=RIGHT pins it to the edge.
        self._pet_widget = Window(
            content=FormattedTextControl(self._pet_fragments),
            height=self._pet_widget_height,
            align=WindowAlign.RIGHT,
        )

        spacer = Window(
            content=FormattedTextControl(get_hint_text),
            height=get_hint_height,
        )

        # --- Clarify tool: dynamic display widget for questions + choices ---

        def _panel_box_width(title: str, content_lines: list[str], min_width: int = 46, max_width: int = 76) -> int:
            """Choose a stable panel width wide enough for the title and content."""
            term_cols = shutil.get_terminal_size((100, 20)).columns
            longest = max([len(title)] + [len(line) for line in content_lines] + [min_width - 4])
            inner = min(max(longest + 4, min_width - 2), max_width - 2, max(24, term_cols - 6))
            return inner + 2  # account for the single leading/trailing spaces inside borders

        def _wrap_panel_text(text: str, width: int, subsequent_indent: str = "") -> list[str]:
            wrapped = textwrap.wrap(
                text,
                width=max(8, width),
                break_long_words=False,
                break_on_hyphens=False,
                subsequent_indent=subsequent_indent,
            )
            return wrapped or [""]

        def _append_panel_line(lines, border_style: str, content_style: str, text: str, box_width: int) -> None:
            inner_width = max(0, box_width - 2)
            lines.append((border_style, "│ "))
            lines.append((content_style, text.ljust(inner_width)))
            lines.append((border_style, " │\n"))

        def _append_blank_panel_line(lines, border_style: str, box_width: int) -> None:
            lines.append((border_style, "│" + (" " * box_width) + "│\n"))

        def _get_clarify_display():
            """Build styled text for the clarify question/choices panel.

            Layout priority: choices + Other option must always render even if
            the question is very long. The question is budgeted to leave enough
            rows for the choices and trailing chrome; anything over the budget
            is truncated with a marker.
            """
            state = cli_ref._clarify_state
            if not state:
                return []

            question = state["question"]
            choices = state.get("choices") or []
            selected = state.get("selected", 0)
            preview_lines = _wrap_panel_text(question, 60)
            for i, choice in enumerate(choices):
                # Show number prefix for quick selection (1-9 for items 1-9, 0 for 10th item)
                if i < 9:
                    num_prefix = str(i + 1)
                elif i == 9:
                    num_prefix = '0'
                else:
                    num_prefix = ' '
                if i == selected and not cli_ref._clarify_freetext:
                    prefix = f"❯ {num_prefix}. "
                else:
                    prefix = f"  {num_prefix}. "
                preview_lines.extend(_wrap_panel_text(f"{prefix}{choice}", 60, subsequent_indent="    "))
            # "Other" option in preview
            other_num = len(choices) + 1
            if other_num < 10:
                other_num_prefix = str(other_num)
            elif other_num == 10:
                other_num_prefix = '0'
            else:
                other_num_prefix = ' '
            other_label = (
                f"❯ {other_num_prefix}. Other (type below)" if cli_ref._clarify_freetext
                else f"❯ {other_num_prefix}. Other (type your answer)" if selected == len(choices)
                else f"  {other_num_prefix}. Other (type your answer)"
            )
            preview_lines.extend(_wrap_panel_text(other_label, 60, subsequent_indent="    "))
            box_width = _panel_box_width("Hermes needs your input", preview_lines)
            inner_text_width = max(8, box_width - 2)

            # Pre-wrap choices + Other option — these are mandatory.
            choice_wrapped: list[tuple[int, str]] = []
            if choices:
                for i, choice in enumerate(choices):
                    # Show number prefix for quick selection (1-9 for items 1-9, 0 for 10th item)
                    if i < 9:
                        num_prefix = str(i + 1)
                    elif i == 9:
                        num_prefix = '0'
                    else:
                        num_prefix = ' '
                    if i == selected and not cli_ref._clarify_freetext:
                        prefix = f'❯ {num_prefix}. '
                    else:
                        prefix = f'  {num_prefix}. '
                    for wrapped in _wrap_panel_text(f"{prefix}{choice}", inner_text_width, subsequent_indent="    "):
                        choice_wrapped.append((i, wrapped))
                # Trailing Other row(s)
                other_idx = len(choices)
                other_num = other_idx + 1
                if other_num < 10:
                    other_num_prefix = str(other_num)
                elif other_num == 10:
                    other_num_prefix = '0'
                else:
                    other_num_prefix = ' '
                if selected == other_idx and not cli_ref._clarify_freetext:
                    other_label_mand = f'❯ {other_num_prefix}. Other (type your answer)'
                elif cli_ref._clarify_freetext:
                    other_label_mand = f'❯ {other_num_prefix}. Other (type below)'
                else:
                    other_label_mand = f'  {other_num_prefix}. Other (type your answer)'
                other_wrapped = _wrap_panel_text(other_label_mand, inner_text_width, subsequent_indent="    ")
            elif cli_ref._clarify_freetext:
                # Freetext-only mode: the guidance line takes the place of choices.
                other_wrapped = _wrap_panel_text(
                    "Type your answer in the prompt below, then press Enter.",
                    inner_text_width,
                )
            else:
                other_wrapped = []

            # Budget the question so mandatory rows always render.
            # Chrome layouts:
            #   full : top border + blank_after_title + blank_after_question
            #          + blank_before_bottom + bottom border = 5 rows
            #   tight: top border + bottom border = 2 rows (drop all blanks)
            #
            # reserved_below matches the approval-panel budget (~6 rows for
            # spinner/tool-progress + status + input + separators + prompt).
            term_rows = shutil.get_terminal_size((100, 24)).lines
            chrome_full = 5
            chrome_tight = 2
            reserved_below = 6

            available = max(0, term_rows - reserved_below)
            # The compact decision must reserve room for at least one question
            # row on top of the choices, otherwise full chrome (3 blank
            # separators) gets kept when there is no room for it and the panel
            # overflows the viewport — HSplit then clips the panel's tail,
            # silently dropping the choices (the reported bug).
            mandatory_full = chrome_full + 1 + len(choice_wrapped) + len(other_wrapped)

            use_compact_chrome = mandatory_full > available
            chrome_rows = chrome_tight if use_compact_chrome else chrome_full

            max_question_rows = max(1, available - chrome_rows - len(choice_wrapped) - len(other_wrapped))
            max_question_rows = min(max_question_rows, 12)  # soft cap on huge terminals

            # When the choices alone (plus compact chrome) already exceed the
            # viewport, drop the question entirely — the choices are the only
            # thing the user must see to make a selection. Without this the
            # question would still claim its 1-row floor above and push the
            # tail of the choices off-screen (HSplit clips the overflow).
            choices_overflow = chrome_rows + len(choice_wrapped) + len(other_wrapped) >= available
            if choices_overflow:
                max_question_rows = 0

            question_wrapped = _wrap_panel_text(question, inner_text_width)
            if max_question_rows <= 0:
                question_wrapped = []
            elif len(question_wrapped) > max_question_rows:
                # The truncation marker is itself a row, so it must count
                # against the budget. With a 1-row budget there is no room for
                # both a question line and the marker — show the marker alone
                # so the rendered question never exceeds max_question_rows.
                keep = max(0, max_question_rows - 1)
                question_wrapped = question_wrapped[:keep] + ["… (question truncated)"]

            lines = []
            # Box top border
            lines.append(('class:clarify-border', '╭─ '))
            lines.append(('class:clarify-title', 'Hermes needs your input'))
            lines.append(('class:clarify-border', ' ' + ('─' * max(0, box_width - len("Hermes needs your input") - 3)) + '╮\n'))
            if not use_compact_chrome:
                _append_blank_panel_line(lines, 'class:clarify-border', box_width)

            # Question text (bounded)
            for wrapped in question_wrapped:
                _append_panel_line(lines, 'class:clarify-border', 'class:clarify-question', wrapped, box_width)
            if not use_compact_chrome:
                _append_blank_panel_line(lines, 'class:clarify-border', box_width)

            if cli_ref._clarify_freetext and not choices:
                for wrapped in other_wrapped:
                    _append_panel_line(lines, 'class:clarify-border', 'class:clarify-choice', wrapped, box_width)
                if not use_compact_chrome:
                    _append_blank_panel_line(lines, 'class:clarify-border', box_width)

            if choices:
                # Multiple-choice mode: show selectable options
                for i, wrapped in choice_wrapped:
                    style = 'class:clarify-selected' if i == selected and not cli_ref._clarify_freetext else 'class:clarify-choice'
                    _append_panel_line(lines, 'class:clarify-border', style, wrapped, box_width)

                # "Other" option (trailing row(s), only shown when choices exist)
                other_idx = len(choices)
                # Calculate number prefix for "Other" option
                other_num = other_idx + 1
                if other_num < 10:
                    other_num_prefix = str(other_num)
                elif other_num == 10:
                    other_num_prefix = '0'
                else:
                    other_num_prefix = ' '
                
                if selected == other_idx and not cli_ref._clarify_freetext:
                    other_style = 'class:clarify-selected'
                elif cli_ref._clarify_freetext:
                    other_style = 'class:clarify-active-other'
                else:
                    other_style = 'class:clarify-choice'
                for wrapped in other_wrapped:
                    _append_panel_line(lines, 'class:clarify-border', other_style, wrapped, box_width)

            if not use_compact_chrome:
                _append_blank_panel_line(lines, 'class:clarify-border', box_width)
            lines.append(('class:clarify-border', '╰' + ('─' * box_width) + '╯\n'))
            return lines

        clarify_widget = ConditionalContainer(
            Window(
                FormattedTextControl(_get_clarify_display),
                wrap_lines=True,
            ),
            filter=Condition(lambda: cli_ref._clarify_state is not None),
        )

        # --- Sudo password: display widget ---

        def _get_sudo_display():
            state = cli_ref._sudo_state
            if not state:
                return []
            title = '🔐 Sudo Password Required'
            body = 'Enter password below (hidden), or press Enter to skip'
            box_width = _panel_box_width(title, [body])
            lines = []
            lines.append(('class:sudo-border', '╭─ '))
            lines.append(('class:sudo-title', title))
            lines.append(('class:sudo-border', ' ' + ('─' * max(0, box_width - len(title) - 3)) + '╮\n'))
            _append_blank_panel_line(lines, 'class:sudo-border', box_width)
            _append_panel_line(lines, 'class:sudo-border', 'class:sudo-text', body, box_width)
            _append_blank_panel_line(lines, 'class:sudo-border', box_width)
            lines.append(('class:sudo-border', '╰' + ('─' * box_width) + '╯\n'))
            return lines

        sudo_widget = ConditionalContainer(
            Window(
                FormattedTextControl(_get_sudo_display),
                wrap_lines=True,
            ),
            filter=Condition(lambda: cli_ref._sudo_state is not None),
        )

        def _get_secret_display():
            state = cli_ref._secret_state
            if not state:
                return []

            title = '🔑 Skill Setup Required'
            prompt = state.get("prompt") or f"Enter value for {state.get('var_name', 'secret')}"
            metadata = state.get("metadata") or {}
            help_text = metadata.get("help")
            body = 'Enter secret below (hidden), ESC or Ctrl+C to skip'
            content_lines = [prompt, body]
            if help_text:
                content_lines.insert(1, str(help_text))
            box_width = _panel_box_width(title, content_lines)
            lines = []
            lines.append(('class:sudo-border', '╭─ '))
            lines.append(('class:sudo-title', title))
            lines.append(('class:sudo-border', ' ' + ('─' * max(0, box_width - len(title) - 3)) + '╮\n'))
            _append_blank_panel_line(lines, 'class:sudo-border', box_width)
            _append_panel_line(lines, 'class:sudo-border', 'class:sudo-text', prompt, box_width)
            if help_text:
                _append_panel_line(lines, 'class:sudo-border', 'class:sudo-text', str(help_text), box_width)
            _append_blank_panel_line(lines, 'class:sudo-border', box_width)
            _append_panel_line(lines, 'class:sudo-border', 'class:sudo-text', body, box_width)
            _append_blank_panel_line(lines, 'class:sudo-border', box_width)
            lines.append(('class:sudo-border', '╰' + ('─' * box_width) + '╯\n'))
            return lines

        secret_widget = ConditionalContainer(
            Window(
                FormattedTextControl(_get_secret_display),
                wrap_lines=True,
            ),
            filter=Condition(lambda: cli_ref._secret_state is not None),
        )

        # --- Dangerous command approval: display widget ---

        def _get_approval_display():
            return cli_ref._get_approval_display_fragments()

        approval_widget = ConditionalContainer(
            Window(
                FormattedTextControl(_get_approval_display),
                wrap_lines=True,
            ),
            filter=Condition(lambda: cli_ref._approval_state is not None),
        )

        def _get_slash_confirm_display():
            return cli_ref._get_slash_confirm_display_fragments()

        slash_confirm_widget = ConditionalContainer(
            Window(
                FormattedTextControl(_get_slash_confirm_display),
                wrap_lines=True,
            ),
            filter=Condition(lambda: cli_ref._slash_confirm_state is not None),
        )

        # --- /model picker: display widget ---
        def _get_model_picker_display():
            state = cli_ref._model_picker_state
            if not state:
                return []
            stage = state.get("stage", "provider")
            if stage == "provider":
                title = "⚙ Model Picker — Select Provider"
                choices = []
                _providers = state.get("providers")
                for p in _providers if isinstance(_providers, list) else []:
                    count = p.get("total_models", len(p.get("models", [])))
                    label = f"{p['name']} ({count} model{'s' if count != 1 else ''})"
                    if p.get("is_current"):
                        label += "  ← current"
                    choices.append(label)
                choices.append("Cancel")
                hint = f"Current: {state.get('current_model', 'unknown')} on {state.get('current_provider', 'unknown')}"
            else:
                provider_data = state.get("provider_data") or {}
                model_list = state.get("model_list") or []
                title = f"⚙ Model Picker — {provider_data.get('name', provider_data.get('slug', 'Provider'))}"
                choices = list(model_list) + ["← Back", "Cancel"]
                if model_list:
                    hint = f"Select a model ({len(model_list)} available)"
                else:
                    hint = "No models listed for this provider. Use Back or Cancel."

            box_width = _panel_box_width(title, [hint] + choices, min_width=46, max_width=84)
            inner_text_width = max(8, box_width - 6)
            selected = state.get("selected", 0)

            # Scrolling viewport: the panel renders into a Window with no max
            # height, so without limiting visible items the bottom border and
            # any items past the available terminal rows get clipped on long
            # provider catalogs (e.g. Ollama Cloud's 36+ models).
            try:
                from prompt_toolkit.application import get_app
                term_rows = get_app().output.get_size().rows
            except Exception:
                term_rows = shutil.get_terminal_size((100, 24)).lines
            scroll_offset, visible = HermesCLI._compute_model_picker_viewport(
                selected, state.get("_scroll_offset", 0), len(choices), term_rows,
            )
            state["_scroll_offset"] = scroll_offset

            lines = []
            lines.append(('class:clarify-border', '╭─ '))
            lines.append(('class:clarify-title', title))
            lines.append(('class:clarify-border', ' ' + ('─' * max(0, box_width - len(title) - 3)) + '╮\n'))
            _append_blank_panel_line(lines, 'class:clarify-border', box_width)
            _append_panel_line(lines, 'class:clarify-border', 'class:clarify-hint', hint, box_width)
            _append_blank_panel_line(lines, 'class:clarify-border', box_width)
            for idx in range(scroll_offset, scroll_offset + visible):
                choice = choices[idx]
                style = 'class:clarify-selected' if idx == selected else 'class:clarify-choice'
                prefix = '❯ ' if idx == selected else '  '
                for wrapped in _wrap_panel_text(prefix + choice, inner_text_width, subsequent_indent='  '):
                    _append_panel_line(lines, 'class:clarify-border', style, wrapped, box_width)
            _append_blank_panel_line(lines, 'class:clarify-border', box_width)
            lines.append(('class:clarify-border', '╰' + ('─' * box_width) + '╯\n'))
            return lines

        model_picker_widget = ConditionalContainer(
            Window(
                FormattedTextControl(_get_model_picker_display),
                wrap_lines=True,
            ),
            filter=Condition(lambda: cli_ref._model_picker_state is not None),
        )

        # Horizontal rules above and below the input.
        # On narrow/mobile terminals we keep the top separator for structure but
        # hide the bottom one to recover a full row for conversation content.
        input_rule_top = Window(
            char='─',
            height=lambda: cli_ref._tui_input_rule_height("top"),
            style='class:input-rule',
        )
        input_rule_bot = Window(
            char='─',
            height=lambda: cli_ref._tui_input_rule_height("bottom"),
            style='class:input-rule',
        )

        # Image attachment indicator — shows badges like [📎 Image #1] above input
        cli_ref = self

        def _get_image_bar():
            if not cli_ref._attached_images:
                return []
            badges = _format_image_attachment_badges(
                cli_ref._attached_images,
                cli_ref._image_counter,
            )
            return [("class:image-badge", f" {badges} ")]

        image_bar = Window(
            content=FormattedTextControl(_get_image_bar),
            height=Condition(lambda: bool(cli_ref._attached_images)),
        )

        # Persistent voice mode status bar (visible only when voice mode is on)
        def _get_voice_status():
            return cli_ref._get_voice_status_fragments()

        voice_status_bar = ConditionalContainer(
            Window(
                FormattedTextControl(_get_voice_status),
                height=1,
            ),
            filter=Condition(lambda: cli_ref._voice_mode),
        )

        status_bar = ConditionalContainer(
            Window(
                content=FormattedTextControl(lambda: cli_ref._get_status_bar_fragments()),
                height=1,
                # Prevent fragments that overflow the terminal width from
                # wrapping onto a second line, which causes the status bar to
                # appear duplicated (one full + one partial row) during long
                # sessions, especially on SSH where shutil.get_terminal_size
                # may return stale values.  _get_status_bar_fragments now reads
                # width from prompt_toolkit's own output object, so fragments
                # will always fit; wrap_lines=False is the belt-and-suspenders
                # guard against any future width mismatch.
                wrap_lines=False,
            ),
            filter=Condition(
                lambda: cli_ref._status_bar_visible
                and not getattr(cli_ref, "_status_bar_suppressed_after_resize", False)
            ),
        )

        # Allow wrapper CLIs to register extra keybindings.
        self._register_extra_tui_keybindings(kb, input_area=input_area)

        # Layout: interactive prompt widgets + ruled input at bottom.
        # The sudo, approval, and clarify widgets appear above the input when
        # the corresponding interactive prompt is active.
        completions_menu = CompletionsMenu(max_height=12, scroll_offset=1)

        layout = Layout(
            HSplit(
                self._build_tui_layout_children(
                    sudo_widget=sudo_widget,
                    secret_widget=secret_widget,
                    approval_widget=approval_widget,
                    slash_confirm_widget=slash_confirm_widget,
                    clarify_widget=clarify_widget,
                    model_picker_widget=model_picker_widget,
                    spinner_widget=spinner_widget,
                    spacer=spacer,
                    status_bar=status_bar,
                    input_rule_top=input_rule_top,
                    image_bar=image_bar,
                    input_area=input_area,
                    input_rule_bot=input_rule_bot,
                    voice_status_bar=voice_status_bar,
                    completions_menu=completions_menu,
                )
            )
        )
        
        # Style for the application
        self._tui_style_base = {
            # Input area / prompt: empty style strings inherit the
            # terminal's default foreground/background, so the typed
            # text is readable in both light and dark Terminal.app
            # color schemes.  (Hardcoding a near-white #FFF8DC made
            # input invisible on light backgrounds.)
            'input-area': '',
            'placeholder': '#888888 italic',
            'prompt': '',
            'prompt-working': '#888888 italic',
            'hint': '#888888 italic',
            'status-bar': 'bg:#1a1a2e #C0C0C0',
            'status-bar-strong': 'bg:#1a1a2e #FFD700 bold',
            'status-bar-dim': 'bg:#1a1a2e #8B8682',
            'status-bar-good': 'bg:#1a1a2e #8FBC8F bold',
            'status-bar-warn': 'bg:#1a1a2e #FFD700 bold',
            'status-bar-bad': 'bg:#1a1a2e #FF8C00 bold',
            'status-bar-critical': 'bg:#1a1a2e #FF6B6B bold',
            'status-bar-yolo': 'bg:#1a1a2e #FF4444 bold',
            # Bronze horizontal rules around the input area
            'input-rule': '#CD7F32',
            # Clipboard image attachment badges
            'image-badge': '#87CEEB bold',
            'completion-menu': 'bg:#1a1a2e #FFF8DC',
            'completion-menu.completion': 'bg:#1a1a2e #FFF8DC',
            'completion-menu.completion.current': 'bg:#333355 #FFD700',
            'completion-menu.meta.completion': 'bg:#1a1a2e #888888',
            'completion-menu.meta.completion.current': 'bg:#333355 #FFBF00',
            # Clarify question panel
            'clarify-border': '#CD7F32',
            'clarify-title': '#FFD700 bold',
            'clarify-question': '#FFF8DC bold',
            'clarify-choice': '#AAAAAA',
            'clarify-selected': '#FFD700 bold',
            'clarify-active-other': '#FFD700 italic',
            'clarify-countdown': '#CD7F32',
            # Sudo password panel
            'sudo-prompt': '#FF6B6B bold',
            'sudo-border': '#CD7F32',
            'sudo-title': '#FF6B6B bold',
            'sudo-text': '#FFF8DC',
            # Dangerous command approval panel
            'approval-border': '#CD7F32',
            'approval-title': '#FF8C00 bold',
            'approval-desc': '#FFF8DC bold',
            'approval-cmd': '#AAAAAA italic',
            'approval-choice': '#AAAAAA',
            'approval-selected': '#FFD700 bold',
            # Voice mode
            'voice-prompt': '#87CEEB',
            'voice-recording': '#FF4444 bold',
            'voice-processing': '#FFA500 italic',
            'voice-status': 'bg:#1a1a2e #87CEEB',
            'voice-status-recording': 'bg:#1a1a2e #FF4444 bold',
        }
        style = PTStyle.from_dict(self._build_tui_style_dict())

        # Disable CPR (Cursor Position Report) at the source so prompt_toolkit
        # never sends ESC[6n cursor-position queries — but only on terminals
        # where the reply is likely to leak. Over SSH/cloudflared tunnels and
        # slow PTYs the CPR replies (ESC[<row>;<col>R) leak into the display as
        # raw "20;1R21;1R" text and can stall the renderer's pending-CPR future,
        # freezing the prompt after the agent's final answer (#13870). CPR is a
        # layout hint, not a speed optimization, and it works fine locally, so we
        # leave prompt_toolkit's default untouched on local terminals and only
        # suppress it where the bug reproduces. None (local, or build failure)
        # falls back to the default output; the input-side scrubbing in
        # _strip_leaked_terminal_responses still guards against any leaks.
        _cpr_disabled_output = (
            _build_cpr_disabled_output(sys.stdout)
            if _terminal_may_leak_cpr()
            else None
        )

        # Create the application
        app = Application(
            layout=layout,
            key_bindings=kb,
            style=style,
            full_screen=False,
            mouse_support=False,
            **({"output": _cpr_disabled_output} if _cpr_disabled_output is not None else {}),
            # Read from display.cli_refresh_interval (default 0 = disabled).
            # When non-zero, prompt_toolkit redraws the UI on this cadence
            # during idle, keeping wall-clock status-bar read-outs ticking.
            # Set to 0 to suppress background redraws entirely — avoids
            # fighting terminal auto-scroll in non-fullscreen mode (Xshell,
            # iTerm2, Windows Terminal). See #48309.
            refresh_interval=float(CLI_CONFIG.get("display", {}).get("cli_refresh_interval", 0)),
            # Erase the live bottom chrome (status bar, input box, separator
            # rules) on exit instead of freezing a final copy into scrollback.
            # Without this, prompt_toolkit's render_as_done teardown repaints
            # the chrome one last time and leaves it stranded above the exit
            # summary — so a dead status bar + empty prompt sit between the
            # conversation transcript and the "Resume this session" block, and
            # stack with the next session's UI on resume (#38252). The actual
            # conversation transcript is printed through patch_stdout into
            # normal scrollback and is unaffected; only the managed chrome is
            # erased. Applies to every exit path (/exit, /quit, EOF, Ctrl+C).
            erase_when_done=True,
            **({'cursor': _STEADY_CURSOR} if _STEADY_CURSOR is not None else {}),
        )
=======
        app = self._tui_build_application(layout, kb, style)
>>>>>>> rb/tag
        _disable_prompt_toolkit_cpr_warning(app)
        app.after_render += self._pet_flush_kitty_frame
        self._app = app

        # Ghost status-bar lines on resize: pt's renderer scrolls the terminal after each
        # paint, pushing chrome into scrollback where a column-shrink reflows it into
        # duplicates. Wrapping _output_screen_diff keeps its reserve-space branch from firing.
        try:
            # Background: prompt_toolkit's renderer (renderer.py L232-242) explicitly moves the cursor to
            # the bottom of the canvas after painting "to make sure the terminal scrolls up, even when the
            # lower lines of the canvas just contain whitespace". In non-fullscreen mode this scrolls chrome
            # content (status bar, input rules) into terminal scrollback on every render. When the terminal
            # column-shrinks, the emulator reflows the previously rendered full-width rows into multiple
            # narrower rows that get pushed up — leaving ghost duplicates AND polluting scrollback. Same
            # issue as pt #29 (open since 2014), #1675, #1933. Surgical fix: wrap _output_screen_diff so
            # that when its internal `if current_height > previous_screen.height` branch fires (the one that
            # does the bottom-cursor-move), we make it fall through by inflating previous_screen.height
            # first.
            import prompt_toolkit.renderer as _pt_renderer
            from prompt_toolkit.renderer import _output_screen_diff as _orig_osd

            if not getattr(_pt_renderer, "_hermes_osd_patched", False):
                _pt_renderer._output_screen_diff = functools.partial(
                    _hermes_call_output_screen_diff, _orig_osd
                )
                _pt_renderer._hermes_osd_patched = True
        except Exception:
            pass

        _apply_bracketed_paste_timeout_patch()

        self._install_resize_recovery(app)

        threading.Thread(target=self._tui_spinner_loop, daemon=True).start()
        threading.Thread(target=self._tui_process_loop, daemon=True).start()
        # Wake word listener off-thread so a first-run engine install never blocks the prompt.
        threading.Thread(target=self._tui_wake_startup, daemon=True, name="wake-startup").start()

        atexit.register(_run_cleanup)
        self._tui_install_signal_handlers()

        if not self._tui_stdin_usable():
            _run_cleanup()
            self._print_exit_summary()
            return

        try:
            with patch_stdout():
                try:
                    # run_in_terminal() may return either: • a coroutine / Future (prompt_toolkit ≥ 3.0) —
                    # must be scheduled via ensure_future so the coroutine is actually awaited; calling it
                    # bare would leave it unawaited and silently drop the output (fixes #23185 Bug A). •
                    # None (some mocks / older PT builds) — just call the inner function directly since PT
                    # already executed it synchronously. Do NOT fall back to a bare _pt_print when
                    # ensure_future raises, because run_in_terminal already invoked the lambda in that case
                    # (the mock path), which would double-print the line.
                    import asyncio as _aio
                    _aio.get_running_loop().set_exception_handler(self._tui_suppress_closed_loop_errors)
                except Exception:
                    pass  # no running loop -- nothing to patch
                # Record that the app enables focus reporting + mouse tracking so _run_cleanup
                # resets them; extended key modes are popped by the same reset.
                # When multiline shortcuts are on, also ask supported terminals (e.g. iTerm2) to report
                # modified keys distinctly (kitty protocol + modifyOtherKeys); the cleanup reset pops both
                # modes. See #36823.
                _mark_tui_input_modes_active()
                if self._tui_multiline_shortcuts:
                    _enable_extended_enter_keys(app.output)
                self._pet_start_anim()
                app.run()
        except (EOFError, KeyboardInterrupt, BrokenPipeError):
            pass
        except (KeyError, OSError) as _stdin_err:
            # Selector registration failures from broken stdin and I/O errors from a
            # broken stdout during interrupt (EIO is suppressed).
            _errno = getattr(_stdin_err, "errno", None) if isinstance(_stdin_err, OSError) else None
            _msg = str(_stdin_err)
            if _errno == errno.EIO:
                pass
            elif _errno in {errno.EINVAL, errno.EBADF} or any(
                s in _msg for s in ("is not registered", "Bad file descriptor", "Invalid argument")
            ):
                print(
                    f"\nError: stdin is not usable ({_stdin_err}).\n"
                    "This can happen with certain Python installations (e.g. uv-managed cPython on macOS)\n"
                    "where kqueue cannot register fd 0.\n"
                    "Try reinstalling Python via pyenv or Homebrew, then re-run: hermes setup"
                )
            else:
                raise
        finally:
            # A resize right before exit leaves its recovery (and the paints it held) unrun.
            _release_paints()
            self._tui_shutdown()

        # /update relaunch happens here, after prompt_toolkit restored terminal modes, on the
        # main thread (the process_loop thread would skip cleanup / only exit itself on Windows).
        if self._pending_relaunch:
            from openagents_cli.relaunch import relaunch
            relaunch(self._pending_relaunch, preserve_inherited=False)


def _build_cli_from_args(model, toolsets, provider, reasoning, api_key, base_url, max_turns, run_budget, verbose, compact, resume, checkpoints, pass_session_id, ignore_rules, skills):
    """Resolve the toolset list (explicit / coding posture / platform default), construct HermesCLI, and start the background skills preload."""
    toolsets_list = None
    if isinstance(toolsets, str) and toolsets:
        toolsets_list = [t.strip() for t in toolsets.split(",")]
    elif isinstance(toolsets, (list, tuple)) and toolsets:
        # Fire may pass multiple --toolsets as a tuple
        toolsets_list = []
        for t in toolsets:
            toolsets_list.extend([x.strip() for x in t.split(",")] if isinstance(t, str) else [str(t)])
    elif not toolsets:
        # Coding posture inside a code workspace, else the shared platform resolver.
        try:
            from agent.coding_context import coding_selection
            toolsets_list = coding_selection(platform="cli", config=CLI_CONFIG)
        except Exception:
            toolsets_list = None
        if toolsets_list is None:
            from openagents_cli.tools_config import _get_platform_tools
            toolsets_list = sorted(_get_platform_tools(CLI_CONFIG, "cli"))

    parsed_skills = _parse_skills_argument(skills)

    try:
        cli = HermesCLI(
            model=model,
            toolsets=toolsets_list,
            provider=provider,
            reasoning=reasoning,
            api_key=api_key,
            base_url=base_url,
            max_turns=max_turns,
            run_budget=run_budget,
            verbose=verbose,
            compact=compact,
            resume=resume,
            checkpoints=checkpoints,
            pass_session_id=pass_session_id,
            ignore_rules=ignore_rules,
        )
    except ImportError as e:
        # Direct `python cli.py` bypasses cmd_chat's partial-update ImportError handler.
        from openagents_constants import emit_partial_update_hint

        if emit_partial_update_hint(e):
            sys.exit(1)
        raise

    # skills.auto_load rides the same background preload as -s; --ignore-rules skips it with
    # the rest of the auto-injected context. Resolved here (not lazily in the agent) so the
    # session id is real for ${HERMES_SESSION_ID} and -s can dedupe against it.
    from agent.skill_commands import build_auto_load_prompt, resolve_auto_load_skills
    auto_load_names = [] if getattr(cli, "ignore_rules", ignore_rules) else resolve_auto_load_skills(CLI_CONFIG)
    if not auto_load_names:
        cli._auto_load_skills_result = ("", [], [])
    if parsed_skills or auto_load_names:
        # Load the skill payloads in the background: skill_view walks the full skills
        # tree per skill (~0.5s for a large library) and the result is only consumed
        # at agent init, not by the banner. finalize_preloaded_skills() joins the
        # thread before any consumer reads cli.system_prompt.
        def _load_preloaded_skills() -> None:
            try:
                if auto_load_names:
                    cli._auto_load_skills_result = build_auto_load_prompt(task_id=cli.session_id, user_config=CLI_CONFIG)
                if parsed_skills:
                    cli._preload_skills_result = build_preloaded_skills_prompt(
                        parsed_skills, task_id=cli.session_id, excluded_loaded_names=set(cli._auto_load_skills_result[1]))
            except Exception as exc:  # surfaced by finalize
                cli._preload_skills_error = exc

        cli._preload_skills_requested = [*auto_load_names, *(s for s in parsed_skills if s not in auto_load_names)]
        cli._preload_skills_thread = threading.Thread(target=_load_preloaded_skills, name="skills-preload", daemon=True)
        cli._preload_skills_thread.start()
    return cli


def _run_legacy_gateway():
    """Legacy `cli.py --gateway` entry: arm the startup watchdog (before importing the gateway graph), then run it."""
    import asyncio
    with suppress(Exception):
        from openagents_startup_watchdog import arm_startup_watchdog
        arm_startup_watchdog()
    from gateway.run import start_gateway
    print("Starting OpenAgents Gateway (messaging platforms)...")
    asyncio.run(start_gateway())


def _start_worktree_setup(list_tools, list_toolsets, worktree, w):
    """Start isolated-worktree creation (+ tool prewarm) in the background.

    Returns a join callable that publishes ``_active_worktree``/TERMINAL_CWD and
    schedules stale-worktree GC, or None when no worktree is wanted.
    """
    if list_tools or list_toolsets or not (worktree or w or CLI_CONFIG.get("worktree", False)):
        return None
    # Overlap tool discovery with the I/O-bound worktree setup so show_banner() hits a warm
    # cache (~0.4s). Only on the -w path: plain `hermes` has no I/O wait to hide.
    def _prewarm_tools() -> None:
        try:
            import model_tools as _mt
            _mt.get_tool_definitions(quiet_mode=True)
        except Exception:
            logger.debug("tool prewarm failed", exc_info=True)

    threading.Thread(target=_prewarm_tools, name="tool-prewarm", daemon=True).start()
    _sync_base = CLI_CONFIG.get("worktree_sync", True)
    _wt_result: dict = {}

    def _create_worktree() -> None:
        try:
            _wt_result["info"] = _setup_worktree(sync_base=_sync_base)
        except Exception:
            logger.debug("worktree setup failed", exc_info=True)
            _wt_result["info"] = None

    _wt_thread = threading.Thread(target=_create_worktree, name="worktree-setup", daemon=True)
    _wt_thread.start()

    def _worktree_maintenance(repo: str) -> None:
        _prune_stale_worktrees(repo)
        _maintain_pack_health(repo)

    def _join_worktree() -> Optional[Dict[str, str]]:
        _wt_thread.join(timeout=120)
        info = _wt_result.get("info")
        if not info:
            return info
        global _active_worktree
        _active_worktree = info
        os.environ["TERMINAL_CWD"] = info["path"]
        atexit.register(_cleanup_worktree, info)
        # GC stale worktrees AFTER _setup_worktree so they never race on git's worktree
        # metadata (the new tree is immune: <24h age gate + live pid lock); then repack
        # once refs are final so lookups stay fast on multi-agent boxes.
        _repo = _git_repo_root()
        if _repo:
            threading.Thread(target=_worktree_maintenance, args=(_repo,), name="worktree-prune", daemon=True).start()
        return info

    return _join_worktree


def main(
    query: str = None,
    q: str = None,
    oneshot: bool = False,
    image: str = None,
    toolsets: str = None,
    skills: str | list[str] | tuple[str, ...] = None,
    model: str = None,
    provider: str = None,
    reasoning: str = None,
    api_key: str = None,
    base_url: str = None,
    max_turns: int = None,
    run_budget: float = None,
    verbose: Optional[bool] = None,
    quiet: bool = False,
    compact: bool = False,
    list_tools: bool = False,
    list_toolsets: bool = False,
    gateway: bool = False,
    resume: str = None,
    worktree: bool = False,
    w: bool = False,
    checkpoints: bool = False,
    pass_session_id: bool = False,
    output_format: str = "text",
    ignore_user_config: bool = False,
    ignore_rules: bool = False,
):
    """
    OpenAgents CLI - Interactive AI Assistant
    
    Args:
        query: Query to run. On a real TTY this seeds an interactive session
            (submitted literally as the first turn); with --oneshot/-Q or a
            non-TTY it answers and exits. Alias: -q
        q: Shorthand for --query
        oneshot: With -q: force the legacy answer-and-exit single-query mode
            even on a TTY.
        image: Optional local image path to attach to a single query
        toolsets: Comma-separated list of toolsets to enable (e.g., "web,terminal")
        skills: Comma-separated or repeated list of skills to preload for the session
        model: Model to use (default: anthropic/claude-opus-4-20250514)
        provider: Inference provider ("auto", "openrouter", "nous", "openai-codex", "zai", "kimi-coding", "minimax", "minimax-cn")
        reasoning: Reasoning effort for this run (none|minimal|low|medium|high|xhigh|max|ultra). Overrides agent.reasoning_effort.
        api_key: API key for authentication
        base_url: Base URL for the API
        max_turns: Maximum tool-calling iterations (default: 60)
        verbose: Enable verbose logging
        compact: Use compact display mode
        list_tools: List available tools and exit
        list_toolsets: List available toolsets and exit
        resume: Resume a previous session by its ID (e.g., 20260225_143052_a1b2c3)
        worktree: Run in an isolated git worktree (for parallel agents). Alias: -w
        w: Shorthand for --worktree
    
    Examples:
        python cli.py                            # Start interactive mode
        python cli.py --toolsets web,terminal    # Use specific toolsets
        python cli.py --skills openagents-dev,github-auth
        python cli.py -q "What is Python?"       # Single query mode
        python cli.py -q "Describe this" --image ~/storage/shared/Pictures/cat.png
        python cli.py --list-tools               # List tools and exit
        python cli.py --resume 20260225_143052_a1b2c3  # Resume session
        python cli.py -w                         # Start in isolated git worktree
        python cli.py -w -q "Fix issue #123"     # Single query in worktree
    """
    # UTF-8 stdio on Windows before any print (Rich box-drawing would UnicodeEncodeError on cp1252).
    with suppress(Exception):
        from openagents_cli.stdio import configure_windows_stdio
        configure_windows_stdio()

    os.environ["HERMES_INTERACTIVE"] = "1"  # terminal_tool: interactive sudo prompts with timeout
    # The banner names affected plugins; the raw per-name compat warnings would only duplicate it on stderr.
    with suppress(Exception):
        from openagents_cli.plugin_compat import quiet_for_interactive
        quiet_for_interactive()

    if gateway:
        _run_legacy_gateway()
        return

    _join_worktree = _start_worktree_setup(list_tools, list_toolsets, worktree, w)
    query = query or q
    # ``hermes chat`` already validated this; the direct Fire entry point gets the same contract.
    if output_format == "stream-json":
        if not query:
            raise ValueError("--format stream-json requires -q/--query")
        quiet = True
    cli = _build_cli_from_args(model, toolsets, provider, reasoning, api_key, base_url, max_turns, run_budget,
                               verbose, compact, resume, checkpoints, pass_session_id, ignore_rules, skills)

    # Join the background worktree creation before anything consumes TERMINAL_CWD.
    # A requested worktree whose setup failed aborts: never silently run without isolation.
    wt_info = _join_worktree() if _join_worktree is not None else None
    if _join_worktree is not None and not wt_info:
        return

    # Inject worktree context into agent's system prompt
    if wt_info:
        wt_note = (
            f"\n\n[System note: You are working in an isolated git worktree at "
            f"{wt_info['path']}. Your branch is `{wt_info['branch']}`. "
            f"Changes here do not affect the main working tree or other agents. "
            f"Remember to commit and push your changes, and create a PR if appropriate. "
            f"The original repo is at {wt_info['repo_root']}.]"
        )
        cli.system_prompt = (cli.system_prompt or "") + wt_note

    if list_tools or list_toolsets:
        cli.show_banner()
        (cli.show_tools if list_tools else cli.show_toolsets)()
        sys.exit(0)

    atexit.register(_run_cleanup)  # interactive mode registers again in run() (idempotent)
    _install_single_query_signal_handlers(cli)

    if query or image:
        _run_single_query_mode(cli, query, image, quiet, oneshot, stream_json=output_format == "stream-json")
        return
    cli.run()


if __name__ == "__main__":
    import fire

    fire.Fire(main)


# ---- BEGIN PLUGIN-COMPAT (revert-scheduled; see COMPAT_MANIFEST.md) ----
# Names external plugins imported from this module before the Sep 2026 decomposition.
# Internal code MUST NOT use these (scripts/check_compat_pointers.py fails CI if it does).
# The whole block is removed by reverting the commit that added it.
from prompt_toolkit.layout.menus import CompletionsMenu  # noqa: F401,E402
from prompt_toolkit.filters import Condition  # noqa: F401,E402
from prompt_toolkit.layout import ConditionalContainer  # noqa: F401,E402
from prompt_toolkit.layout.processors import ConditionalProcessor  # noqa: F401,E402
from prompt_toolkit.layout.dimension import Dimension  # noqa: F401,E402
from prompt_toolkit.history import FileHistory  # noqa: F401,E402
from prompt_toolkit.layout import FormattedTextControl  # noqa: F401,E402
from prompt_toolkit.layout import HSplit  # noqa: F401,E402
from prompt_toolkit.key_binding import KeyBindings  # noqa: F401,E402
from prompt_toolkit.layout import Layout  # noqa: F401,E402
from prompt_toolkit.styles import Style as PTStyle  # noqa: F401,E402
from rich.panel import Panel  # noqa: F401,E402
from prompt_toolkit.layout.processors import PasswordProcessor  # noqa: F401,E402
from prompt_toolkit.layout.processors import Processor  # noqa: F401,E402
from prompt_toolkit.widgets import TextArea  # noqa: F401,E402
from prompt_toolkit.layout.processors import Transformation  # noqa: F401,E402
from prompt_toolkit.layout import Window  # noqa: F401,E402
from prompt_toolkit.layout import WindowAlign  # noqa: F401,E402
import base64  # noqa: F401,E402
import concurrent.futures  # noqa: F401,E402
import copy  # noqa: F401,E402
from rich import box as rich_box  # noqa: F401,E402
import tempfile  # noqa: F401,E402

def AIAgent(*args, **kwargs):
    from run_agent import AIAgent as _AIAgent

    return _AIAgent(*args, **kwargs)

def CanonicalUsage(*args, **kwargs):
    from agent.usage_pricing import CanonicalUsage as _CanonicalUsage

    return _CanonicalUsage(*args, **kwargs)


_PLUGIN_COMPAT_LAZY = {
    'DEFAULT_BROWSER_CDP_URL': ('openagents_cli.browser_connect', 'DEFAULT_BROWSER_CDP_URL'),
    'HERMES_AGENT_LOGO': ('openagents_cli.banner', 'HERMES_AGENT_LOGO'),
    'HERMES_CADUCEUS': ('openagents_cli.banner', 'HERMES_CADUCEUS'),
    'SlashCommandAutoSuggest': ('openagents_cli.commands_completion', 'SlashCommandAutoSuggest'),
    'SlashCommandCompleter': ('openagents_cli.commands_completion', 'SlashCommandCompleter'),
    'build_welcome_banner': ('openagents_cli.banner', 'build_welcome_banner'),
    'display_openagents_home': ('openagents_constants', 'display_openagents_home'),
    'estimate_usage_cost': ('agent.usage_pricing', 'estimate_usage_cost'),
    'get_all_toolsets': ('toolsets', 'get_all_toolsets'),
    'get_job': ('cron.jobs', 'get_job'),
    'get_toolset_for_tool': ('model_tools', 'get_toolset_for_tool'),
    'get_toolset_info': ('toolsets', 'get_toolset_info'),
    'init_skin_from_config': ('openagents_cli.skin_engine', 'init_skin_from_config'),
    'is_browser_debug_ready': ('openagents_cli.browser_connect', 'is_browser_debug_ready'),
    'is_table_divider': ('agent.markdown_tables', 'is_table_divider'),
    'looks_like_table_row': ('agent.markdown_tables', 'looks_like_table_row'),
    'manual_chrome_debug_command': ('openagents_cli.browser_connect', 'manual_chrome_debug_command'),
    'print_config_warnings': ('openagents_cli.config', 'print_config_warnings'),
    'prompt_for_secret': ('openagents_cli.callbacks', 'prompt_for_secret'),
    'set_friendly_tool_labels': ('agent.display', 'set_friendly_tool_labels'),
    'set_tool_preview_max_len': ('agent.display', 'set_tool_preview_max_len'),
    'setup_logging': ('openagents_logging', 'setup_logging'),
}


def __getattr__(name):  # PEP 562 — lazy so no import cycles
    target = _PLUGIN_COMPAT_LAZY.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib
    from openagents_cli.plugin_compat import warn_once
    warn_once(__name__, name, *target)
    return getattr(importlib.import_module(target[0]), target[1])
# ---- END PLUGIN-COMPAT ----
