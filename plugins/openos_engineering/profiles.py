"""Scaffold PO / Dev / QA profiles for OpenOS W4 workflow."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict

PROFILE_SPECS: Dict[str, Dict[str, Any]] = {
    "product_owner": {
        "description": "Product Owner — writes tickets and acceptance criteria",
        "toolsets": ["delegation", "mcp"],
        "skills": ["open-ticket", "open-dev-workflow"],
        "soul": (
            "You are the Product Owner. Create and refine OpenTicket stories "
            "with clear acceptance criteria. Do not write code — delegate to developer."
        ),
    },
    "developer": {
        "description": "Developer — implements tickets via OpenCode",
        "toolsets": ["delegation", "terminal", "mcp", "openos_engineering"],
        "skills": ["open-code", "open-ticket", "open-dev-workflow"],
        "soul": (
            "You are the Developer. Read assigned tickets, invoke_opencode for "
            "all code changes, and move tickets through dev workflow."
        ),
    },
    "qa": {
        "description": "QA — validates tickets via OpenCode review/test",
        "toolsets": ["terminal", "mcp", "openos_engineering"],
        "skills": ["open-code", "open-ticket", "open-dev-workflow"],
        "soul": (
            "You are QA. Verify acceptance criteria using invoke_opencode in "
            "review/test mode. Only you may transition tickets to done."
        ),
    },
}


def init_profiles(home: Path | None = None) -> list[str]:
    base = home or Path(os.environ.get("OPENAGENTS_HOME", Path.home() / ".openagents"))
    profiles_dir = base / "profiles"
    created: list[str] = []

    for name, spec in PROFILE_SPECS.items():
        profile_dir = profiles_dir / name
        profile_dir.mkdir(parents=True, exist_ok=True)
        config_path = profile_dir / "config.yaml"
        if not config_path.exists():
            toolsets_yaml = ", ".join(spec["toolsets"])
            skills_yaml = ", ".join(spec["skills"])
            config_path.write_text(
                f"# OpenOS W4 profile: {name}\n"
                f"description: \"{spec['description']}\"\n"
                f"toolsets:\n"
                + "".join(f"  - {t}\n" for t in spec["toolsets"])
                + f"skills:\n"
                + "".join(f"  - {s}\n" for s in spec["skills"])
                + "mcp_servers:\n"
                + "  openticket:\n"
                + "    transport: stdio\n"
                + "    command: pnpm\n"
                + "    args:\n"
                + "      - exec\n"
                + "      - tsx\n"
                + "      - apps/mcp-server/src/index.ts\n"
                + "    cwd: \"${OPENOS_ROOT}/OpenTicket\"\n",
                encoding="utf-8",
            )
        soul_path = profile_dir / "SOUL.md"
        if not soul_path.exists():
            soul_path.write_text(spec["soul"] + "\n", encoding="utf-8")
        created.append(name)

    return created
