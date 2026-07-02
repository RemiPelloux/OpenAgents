"""Lists real OpenAgents toolsets, tools, and MCP servers for the builder UI.

Backs the node config pickers (``agent`` node "tools" field, ``mcp`` node
"tool" field) so OpenAgentUI workflows compose OpenAgents' *actual*
capabilities instead of the upstream Arcade/Firecrawl-specific catalogs.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_loaded = False
_catalog_cache: Optional[Dict[str, Any]] = None
_catalog_cached_at: float = 0.0
_CATALOG_TTL_SECONDS = 300.0


def ensure_tools_loaded() -> None:
    """Idempotently discover built-in tools + enabled plugins.

    Safe to call on every request — ``discover_plugins()`` and
    ``discover_builtin_tools()`` are themselves idempotent (re-importing an
    already-imported module is a cheap no-op), but we still guard with a
    module-level flag to skip the filesystem globbing on the hot path.
    """
    global _loaded
    if _loaded:
        return
    try:
        from tools.registry import discover_builtin_tools

        discover_builtin_tools()
    except Exception:
        logger.exception("openagentui: failed to discover built-in tools")
    try:
        from openagents_cli.plugins import discover_plugins

        discover_plugins()
    except Exception:
        logger.exception("openagentui: failed to discover plugins")
    _loaded = True


def list_toolsets() -> List[Dict[str, Any]]:
    """Toolsets available to the ``agent`` node's "tools" picker."""
    ensure_tools_loaded()
    from toolsets import get_all_toolsets

    out = []
    for name, definition in sorted(get_all_toolsets().items()):
        out.append(
            {
                "id": name,
                "label": definition.get("name", name) if isinstance(definition, dict) else name,
                "description": definition.get("description", "") if isinstance(definition, dict) else "",
            }
        )
    return out


def list_tools() -> List[Dict[str, Any]]:
    """Individual registered tools available to the ``mcp`` (deterministic call) node."""
    ensure_tools_loaded()
    from tools.registry import registry

    out = []
    for name in registry.get_all_tool_names():
        out.append(
            {
                "id": name,
                "toolset": registry.get_toolset_for_tool(name),
                "emoji": registry.get_emoji(name),
            }
        )
    return out


def list_mcp_servers() -> List[Dict[str, Any]]:
    """Installed/configured MCP servers (OpenAgents' own catalog, not Arcade)."""
    try:
        from openagents_cli.mcp_catalog import installed_servers

        return [
            {"id": name, **({"config": cfg} if isinstance(cfg, dict) else {})}
            for name, cfg in installed_servers().items()
        ]
    except Exception:
        logger.exception("openagentui: failed to list MCP servers")
        return []


def catalog_snapshot(*, force: bool = False) -> Dict[str, Any]:
    """Everything the frontend's node pickers need in one call (cached 5 min)."""
    global _catalog_cache, _catalog_cached_at
    now = time.time()
    if not force and _catalog_cache is not None and (now - _catalog_cached_at) < _CATALOG_TTL_SECONDS:
        return _catalog_cache
    snapshot = {
        "toolsets": list_toolsets(),
        "tools": list_tools(),
        "mcpServers": list_mcp_servers(),
    }
    _catalog_cache = snapshot
    _catalog_cached_at = now
    return snapshot


def invalidate_catalog_cache() -> None:
    global _catalog_cache, _catalog_cached_at
    _catalog_cache = None
    _catalog_cached_at = 0.0
