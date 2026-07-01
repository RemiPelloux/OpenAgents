"""Tests for openagentui.tool_catalog — node-picker data for the frontend."""

from openagentui import tool_catalog


def test_ensure_tools_loaded_is_idempotent():
    tool_catalog.ensure_tools_loaded()
    tool_catalog.ensure_tools_loaded()  # second call must not re-run discovery/raise


def test_list_toolsets_returns_entries_with_ids():
    toolsets = tool_catalog.list_toolsets()
    assert isinstance(toolsets, list)
    assert all("id" in t and "label" in t for t in toolsets)


def test_list_tools_returns_entries_with_ids():
    tools = tool_catalog.list_tools()
    assert isinstance(tools, list)
    assert all("id" in t for t in tools)
    assert any(t["id"] == "run_openagentui_workflow" for t in tools)


def test_list_mcp_servers_returns_list():
    servers = tool_catalog.list_mcp_servers()
    assert isinstance(servers, list)


def test_catalog_snapshot_has_all_sections():
    snapshot = tool_catalog.catalog_snapshot()
    assert set(snapshot.keys()) == {"toolsets", "tools", "mcpServers"}
