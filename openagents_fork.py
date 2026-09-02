"""OpenAgents fork metadata — distribution repo vs Hermes Agent upstream.

End users install from DISTRIBUTION_* and run ``openagents update`` (pulls
origin). Maintainers merge Hermes releases via ``scripts/sync_from_hermes.sh``.
"""

from __future__ import annotations

# Where ``openagents update`` expects origin to point (this fork on GitHub).
DISTRIBUTION_REPO_HTTPS = "https://github.com/RemiPelloux/OpenAgents.git"
DISTRIBUTION_REPO_SSH = "git@github.com:RemiPelloux/OpenAgents.git"
DISTRIBUTION_REPO_CANONICAL = "github.com/remipelloux/openagents"

DISTRIBUTION_REPO_URLS = frozenset({
    DISTRIBUTION_REPO_HTTPS,
    DISTRIBUTION_REPO_SSH,
    "https://github.com/RemiPelloux/OpenAgents",
    "git@github.com:RemiPelloux/OpenAgents",
})

# Hermes Agent — source of truth for feature releases (maintainer sync only).
HERMES_UPSTREAM_REPO_HTTPS = "https://github.com/NousResearch/Hermes-agent.git"
HERMES_UPSTREAM_REPO_CANONICAL = "github.com/nousresearch/hermes-agent"

# Rebrand layer on top of Hermes; never auto-merge raw Hermes on ``openagents update``.
IS_REBRANDED_HERMES_FORK = True

SYNC_FROM_HERMES_SCRIPT = "scripts/sync_from_hermes.sh"
INSTALL_LOCAL_SCRIPT = "scripts/install-local.sh"


def refresh_source_install_cli_links(project_root) -> bool:
    """Refresh ~/.local/bin launchers after a source-tree ``openagents update``.

    Returns True when links were refreshed successfully.
    """
    import subprocess
    from pathlib import Path

    from openagents_constants import get_openagents_home

    marker = get_openagents_home() / "source-install-root"
    if not marker.is_file():
        return False
    try:
        recorded = Path(marker.read_text(encoding="utf-8").strip())
    except OSError:
        return False
    root = Path(project_root).resolve()
    if recorded.resolve() != root:
        return False
    script = root / INSTALL_LOCAL_SCRIPT
    if not script.is_file():
        return False
    try:
        subprocess.run(
            ["/bin/bash", str(script), "--link-only"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        )
        return True
    except (OSError, subprocess.CalledProcessError):
        return False
