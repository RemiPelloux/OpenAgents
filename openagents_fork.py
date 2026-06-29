"""OpenAgents fork metadata — distribution repo vs Hermes Agent upstream.

End users install from DISTRIBUTION_* and run ``openagents update`` (pulls
origin). Maintainers merge Hermes releases via ``scripts/sync_from_hermes.sh``.
"""

from __future__ import annotations

# Where ``openagents update`` expects origin to point (this fork on GitHub).
DISTRIBUTION_REPO_HTTPS = "https://github.com/RemiPelloux/OpenAgents.git"
DISTRIBUTION_REPO_SSH = "git@github.com:RemiPelloux/OpenAgents.git"
DISTRIBUTION_REPO_CANONICAL = "github.com/remipelloux/openagents"

DISTRIBUTION_REPO_URLS = frozenset(
    {
        DISTRIBUTION_REPO_HTTPS,
        DISTRIBUTION_REPO_SSH,
        "https://github.com/RemiPelloux/OpenAgents",
        "git@github.com:RemiPelloux/OpenAgents",
    }
)

# Hermes Agent — source of truth for feature releases (maintainer sync only).
HERMES_UPSTREAM_REPO_HTTPS = "https://github.com/NousResearch/Hermes-agent.git"
HERMES_UPSTREAM_REPO_CANONICAL = "github.com/nousresearch/hermes-agent"

# Rebrand layer on top of Hermes; never auto-merge raw Hermes on ``openagents update``.
IS_REBRANDED_HERMES_FORK = True

SYNC_FROM_HERMES_SCRIPT = "scripts/sync_from_hermes.sh"
