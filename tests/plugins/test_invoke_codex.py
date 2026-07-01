"""invoke_codex tool handler."""

from unittest.mock import patch

from plugins.openos_engineering.tools import handle_invoke_codex


@patch("plugins.openos_engineering.tools.run_codex_headless")
def test_handle_invoke_codex_ok(mock_run):
    mock_run.return_value = {"ok": True, "exit_code": 0, "summary": "patched", "cwd": "/tmp"}
    out = handle_invoke_codex({"prompt": "implement feature"})
    assert "Codex completed" in out
    assert "patched" in out


def test_handle_invoke_codex_missing_prompt():
    out = handle_invoke_codex({})
    assert "prompt is required" in out
