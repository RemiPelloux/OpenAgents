"""Short OpenCode-style slime mascot animation before the CLI banner."""

from __future__ import annotations

import os
import sys
import time
from typing import Iterable, List, Sequence

# Bouncing slime with blinking eyes (OpenCode / opencode-pet inspired).
_SLIME_FRAMES: Sequence[Sequence[str]] = (
    (
        "      ▄▀▀▀▄     ",
        "     █ ◕ ◕ █    ",
        "      ▀▄▄▄▀     ",
        "        ╲╱      ",
    ),
    (
        "     ▄▀▀▀▄      ",
        "    █ ◕ ◕ █     ",
        "     ▀▄▄▄▀      ",
        "       ╲╱       ",
    ),
    (
        "      ▄▀▀▀▄     ",
        "     █ ◉ ◉ █    ",
        "      ▀▄▄▄▀     ",
        "       ──       ",
    ),
    (
        "     ▄▀▀▀▄      ",
        "    █ ◕ ◕ █     ",
        "     ▀▄▄▄▀      ",
        "      ╲╱        ",
    ),
    (
        "      ▄▀▀▀▄     ",
        "     █ ◔ ◔ █    ",
        "      ▀▄▄▄▀     ",
        "        ╲╱      ",
    ),
    (
        "    ▄▀▀▀▄       ",
        "   █ ◕ ◕ █      ",
        "    ▀▄▄▄▀       ",
        "      ╲╱        ",
    ),
)

_LOADING_VERBS: Sequence[str] = (
    "waking up",
    "stretching",
    "looking around",
    "ready",
)


def _is_interactive_tty() -> bool:
    try:
        return bool(sys.stdin.isatty() and sys.stdout.isatty())
    except (AttributeError, ValueError, OSError):
        return False


def _should_skip_animation() -> bool:
    if os.environ.get("HERMES_FAST_STARTUP_BANNER") == "1":
        return True
    if os.environ.get("HERMES_NO_STARTUP_ANIMATION") == "1":
        return True
    if os.environ.get("HERMES_SKIP_STARTUP_ANIMATION") == "1":
        return True
    return not _is_interactive_tty()


def _skin_palette() -> tuple[str, str, str]:
    try:
        from openagents_cli.skin_engine import get_active_skin

        skin = get_active_skin()
        return (
            skin.get_color("ui_accent", "#007aff"),
            skin.get_color("banner_text", "#f1eeee"),
            skin.get_color("banner_dim", "#9a9898"),
        )
    except Exception:
        return "#007aff", "#f1eeee", "#9a9898"


def _render_frame(
    lines: Iterable[str], *, accent: str, text: str, dim: str, verb: str
) -> str:
    body = "\n".join(f"[{text}]{line}[/]" for line in lines)
    return f"{body}\n[dim {dim}]  › little monster {verb}…[/]"


def play_startup_animation(
    *, enabled: bool = True, frame_interval: float = 0.11
) -> None:
    """Play a short mascot animation. Never raises."""
    if not enabled or _should_skip_animation():
        return

    accent, text, dim = _skin_palette()
    frames: List[Sequence[str]] = list(_SLIME_FRAMES)
    verbs = list(_LOADING_VERBS)

    try:
        from rich.console import Console
        from rich.live import Live
        from rich.text import Text

        console = Console(highlight=False, markup=True, soft_wrap=False)

        def _frame_markup(idx: int) -> str:
            verb = verbs[min(idx // 2, len(verbs) - 1)]
            return _render_frame(
                frames[idx % len(frames)], accent=accent, text=text, dim=dim, verb=verb
            )

        with Live(console=console, refresh_per_second=12, transient=True) as live:
            for idx in range(len(frames) + 2):
                live.update(Text.from_markup(_frame_markup(idx)))
                time.sleep(frame_interval)
    except Exception:
        # Plain fallback when Rich is unavailable or Live fails.
        try:
            out = sys.stdout
            for idx in range(len(frames) + 2):
                out.write("\033[2J\033[H")
                for line in frames[idx % len(frames)]:
                    out.write(line + "\n")
                verb = verbs[min(idx // 2, len(verbs) - 1)]
                out.write(f"  › little monster {verb}…\n")
                out.flush()
                time.sleep(frame_interval)
            out.write("\033[2J\033[H")
            out.flush()
        except Exception:
            pass
