"""Scaffold tiktok_prospector profile."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict

PROFILE_SPEC: Dict[str, Any] = {
    "description": "TikTok prospector — OpenTeam harvest to OpenPro accounts",
    "toolsets": ["delegation", "mcp", "openpro_prospection"],
    "skills": ["openpro-tiktok-prospection", "open-ecosystem-hub"],
    "soul": (
        "You prospect TikTok recruitment leads into OpenPro. "
        "Never recreate duplicate companies. Always report status per lead."
    ),
}


def init_tiktok_prospector_profile(home: Path | None = None) -> str:
    base = home or Path(os.environ.get("OPENAGENTS_HOME", Path.home() / ".openagents"))
    profile_dir = base / "profiles" / "tiktok_prospector"
    profile_dir.mkdir(parents=True, exist_ok=True)
    config_path = profile_dir / "config.yaml"
    if not config_path.exists():
        config_path.write_text(
            "description: \"TikTok prospector — OpenTeam harvest to OpenPro accounts\"\n"
            "toolsets:\n"
            "  - delegation\n"
            "  - mcp\n"
            "  - openpro_prospection\n"
            "skills:\n"
            "  - openpro-tiktok-prospection\n"
            "  - open-ecosystem-hub\n",
            encoding="utf-8",
        )
    soul_path = profile_dir / "SOUL.md"
    if not soul_path.exists():
        soul_path.write_text(PROFILE_SPEC["soul"] + "\n", encoding="utf-8")
    return "tiktok_prospector"
