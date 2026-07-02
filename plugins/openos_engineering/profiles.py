"""Scaffold PO / Dev / QA profiles for OpenOS W4 workflow."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List

MCP_SERVER_DEFS: Dict[str, Dict[str, Any]] = {
    "openticket": {
        "cwd": "${OPENOS_ROOT}/OpenTicket",
        "args": ["exec", "tsx", "apps/mcp-server/src/index.ts"],
    },
    "openorchestrator": {
        "cwd": "${OPENOS_ROOT}/OpenOrchestrator",
        "args": ["exec", "tsx", "apps/mcp-server/src/index.ts"],
        "env": {"ORCHESTRATOR_URL": "http://localhost:3050"},
    },
}


def _render_mcp_servers(names: List[str]) -> str:
    lines = ["mcp_servers:"]
    for name in names:
        spec = MCP_SERVER_DEFS[name]
        lines.append(f"  {name}:")
        lines.append("    transport: stdio")
        lines.append("    command: pnpm")
        lines.append("    args:")
        for arg in spec["args"]:
            lines.append(f"      - {arg}")
        lines.append(f'    cwd: "{spec["cwd"]}"')
        env = spec.get("env", {})
        if env:
            lines.append("    env:")
            for key, value in env.items():
                lines.append(f'      {key}: "{value}"')
    return "\n".join(lines) + "\n"


PROFILE_SPECS: Dict[str, Dict[str, Any]] = {
    "planner": {
        "description": "Planner — decomposes objectives into orchestrated steps",
        "toolsets": ["delegation", "mcp"],
        "skills": ["open-orchestrator-plan", "open-ecosystem-hub"],
        "mcp_servers": ["openorchestrator", "openticket"],
        "soul": (
            "You are the OpenOrchestrator planner. Break objectives into ordered "
            "steps with required_skills. Respond with JSON only when asked to decompose."
        ),
    },
    "intent_classifier": {
        "description": "Intent classifier — NL → NormalizedGoal JSON for orchestrator",
        "toolsets": ["mcp"],
        "skills": ["open-orchestrator-intent", "open-ecosystem-hub"],
        "mcp_servers": ["openorchestrator"],
        "soul": (
            "You are the OpenOrchestrator intent classifier. Parse natural language "
            "into strict NormalizedGoal JSON. Return JSON only — no markdown."
        ),
    },
    "skill_author": {
        "description": "Skill Author — drafts org skill patches when gaps are detected",
        "toolsets": ["skills", "mcp"],
        "skills": ["open-orchestrator-plan", "open-brain"],
        "mcp_servers": ["openorchestrator"],
        "soul": (
            "You author or patch org-scoped skills when orchestrator reports a skill gap. "
            "Never modify bundled OpenOS skills — org overlay only."
        ),
    },
    "product_owner": {
        "description": "Product Owner — writes tickets and acceptance criteria",
        "toolsets": ["delegation", "mcp"],
        "skills": ["open-ticket", "open-dev-workflow"],
        "mcp_servers": ["openticket", "openorchestrator"],
        "soul": (
            "You are the Product Owner. Create and refine OpenTicket stories "
            "with clear acceptance criteria. Do not write code — delegate to developer."
        ),
    },
    "developer": {
        "description": "Developer — implements tickets via OpenCode",
        "toolsets": ["delegation", "terminal", "mcp", "openos_engineering"],
        "skills": ["open-code", "open-ticket", "open-dev-workflow"],
        "mcp_servers": ["openticket"],
        "soul": (
            "You are the Developer. Read assigned tickets, invoke_opencode for "
            "all code changes, and move tickets through dev workflow."
        ),
    },
    "qa": {
        "description": "QA — validates tickets via OpenCode review/test",
        "toolsets": ["terminal", "mcp", "openos_engineering"],
        "skills": ["open-code", "open-ticket", "open-dev-workflow"],
        "mcp_servers": ["openticket"],
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
                + _render_mcp_servers(spec.get("mcp_servers", ["openticket"])),
                encoding="utf-8",
            )
        soul_path = profile_dir / "SOUL.md"
        if not soul_path.exists():
            soul_path.write_text(spec["soul"] + "\n", encoding="utf-8")
        created.append(name)

    return created
