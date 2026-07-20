"""Scaffold and ensure OpenOS agent profiles for the mesh."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List

from plugins.openos_engineering.profile_catalog import (
    MCP_SERVER_DEFS,
    PROFILE_SPECS,
    get_profile_spec,
    list_profile_ids,
)


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


def _render_profile_config(name: str, spec: Dict[str, Any]) -> str:
    return (
        f"# OpenOS profile: {name}\n"
        f"description: \"{spec['description']}\"\n"
        f"toolsets:\n"
        + "".join(f"  - {t}\n" for t in spec["toolsets"])
        + "skills:\n"
        + "".join(f"  - {s}\n" for s in spec["skills"])
        + _render_mcp_servers(spec.get("mcp_servers", []))
    )


def _write_profile_config(profile_dir: Path, name: str, spec: Dict[str, Any]) -> None:
    (profile_dir / "config.yaml").write_text(
        _render_profile_config(name, spec),
        encoding="utf-8",
    )


def profile_exists(home: Path, profile_id: str) -> bool:
    return (home / "profiles" / profile_id / "config.yaml").is_file()


def ensure_profile(profile_id: str, home: Path | None = None) -> bool:
    """Create one profile directory when missing. Returns True if created."""
    spec = get_profile_spec(profile_id)
    if not spec:
        raise ValueError(f"unknown profile {profile_id!r}")

    base = home or Path(os.environ.get("OPENAGENTS_HOME", Path.home() / ".openagents"))
    profile_dir = base / "profiles" / profile_id
    profile_dir.mkdir(parents=True, exist_ok=True)

    config_path = profile_dir / "config.yaml"
    desired_config = _render_profile_config(profile_id, spec)
    created = not config_path.exists()
    current_config = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    managed = current_config.startswith("# OpenOS")
    if created or (managed and current_config != desired_config):
        config_path.write_text(desired_config, encoding="utf-8")
        created = True

    soul_path = profile_dir / "SOUL.md"
    if not soul_path.exists():
        soul_path.write_text(spec["soul"] + "\n", encoding="utf-8")
        created = True

    return created


def ensure_profiles(
    profile_ids: List[str] | None = None,
    home: Path | None = None,
) -> Dict[str, str]:
    """Ensure profiles exist. Returns {profile_id: created|exists}."""
    base = home or Path(os.environ.get("OPENAGENTS_HOME", Path.home() / ".openagents"))
    targets = profile_ids or list_profile_ids()
    results: Dict[str, str] = {}

    for profile_id in targets:
        if not get_profile_spec(profile_id):
            results[profile_id] = "unknown"
            continue
        created = ensure_profile(profile_id, home=base)
        results[profile_id] = "created" if created else "exists"

    return results


def init_profiles(home: Path | None = None) -> list[str]:
    """Scaffold every catalog profile (non-destructive)."""
    ensure_profiles(list_profile_ids(), home=home)
    return list_profile_ids()
