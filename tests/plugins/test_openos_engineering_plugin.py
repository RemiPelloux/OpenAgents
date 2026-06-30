"""Tests for openos_engineering plugin."""

from __future__ import annotations

from unittest.mock import patch

import pytest


def test_register_wires_invoke_opencode():
    import plugins.openos_engineering as plugin

    calls: list[str] = []

    class _Ctx:
        def register_tool(self, **kw):
            calls.append(kw["name"])

        def register_cli_command(self, **kw):
            calls.append(kw["name"])

    plugin.register(_Ctx())
    assert "invoke_opencode" in calls
    assert "openos" in calls


def test_build_ticket_prompt():
    from plugins.openos_engineering.ticket_client import build_ticket_prompt

    prompt = build_ticket_prompt(
        {
            "ticket_key": "OP-1",
            "title": "Add login",
            "description": "OAuth",
            "acceptance_criteria": ["Google login works"],
        },
        "implement",
    )
    assert "OP-1" in prompt
    assert "Google login works" in prompt


def test_handle_invoke_opencode_missing_id():
    from plugins.openos_engineering.tools import handle_invoke_opencode

    assert "required" in handle_invoke_opencode({}).lower()


def test_init_profiles(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAGENTS_HOME", str(tmp_path))
    from plugins.openos_engineering.profiles import init_profiles

    names = init_profiles(tmp_path)
    assert set(names) == {"product_owner", "developer", "qa"}
    assert (tmp_path / "profiles" / "developer" / "SOUL.md").is_file()
