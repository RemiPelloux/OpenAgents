"""Tests for openagentui.codex_runner."""

from unittest.mock import MagicMock, patch

from openagentui.codex_runner import run_codex_headless, verify_codex_binary


@patch("openagentui.codex_runner.resolve_codex_binary", return_value=["/usr/bin/codex"])
@patch("openagentui.codex_runner.subprocess.run")
def test_run_codex_headless_ok(mock_run, _mock_bin):
    mock_run.return_value = MagicMock(returncode=0, stdout="done", stderr="")
    result = run_codex_headless("fix tests", cwd="/tmp/proj")
    assert result["ok"] is True
    assert result["summary"] == "done"
    mock_run.assert_called_once()
    args = mock_run.call_args[0][0]
    assert args[0] == "/usr/bin/codex"
    assert args[1] == "exec"
    assert args[-1] == "fix tests"


@patch("openagentui.codex_runner.resolve_codex_binary", return_value=["codex"])
@patch("openagentui.codex_runner.subprocess.run")
def test_run_codex_headless_failure(mock_run, _mock_bin):
    mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="boom")
    result = run_codex_headless("task")
    assert result["ok"] is False
    assert "boom" in result["summary"]


@patch("openagentui.codex_runner.resolve_codex_binary", return_value=["codex"])
@patch("openagentui.codex_runner.subprocess.run")
def test_verify_codex_binary(mock_run, _mock_bin):
    mock_run.return_value = MagicMock(returncode=0)
    assert verify_codex_binary() is True
