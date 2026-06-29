"""OpenPro ASCII mascot — animated face for the CLI status bar."""

from __future__ import annotations

import time

# OpenCode-style little monster expressions (cycle on status bar refresh).
_FACE_FRAMES = ("(◕‿◕)", "(•ᴥ•)", "(◔‿◔)", "(^‿^)", "(◠ω◠)", "(◕ω◕)")


def animated_face(*, interval: float = 0.45) -> str:
    """Return the current mascot face for this point in time."""
    if interval <= 0:
        interval = 0.45
    index = int(time.time() / interval) % len(_FACE_FRAMES)
    return _FACE_FRAMES[index]


def status_bar_prefix(brand: str = "") -> str:
    """Build the leading status-bar label, e.g. ``(◕‿◕) OpenPro``."""
    face = animated_face()
    label = (brand or "").strip()
    if label:
        return f"{face} {label}"
    return face
