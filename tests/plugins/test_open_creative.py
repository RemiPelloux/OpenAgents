"""Tests for OpenCreative brain client."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from plugins.open_creative.brain_client import resolve_brain_secret


def test_resolve_brain_secret_returns_value(monkeypatch):
    monkeypatch.setenv("OPENBRAIN_URL", "http://localhost:3001")
    monkeypatch.setenv("INTERNAL_SERVICE_KEY", "test-key")
    monkeypatch.setenv("OPENBRAIN_ORG_ID", "org-1")

    payload = b'{"data":{"value":"sk-test"}}'
    mock_resp = MagicMock()
    mock_resp.read.return_value = payload
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)

    with patch("urllib.request.urlopen", return_value=mock_resp):
        value = resolve_brain_secret(
            "openai_api_key",
            workflow_run_id="00000000-0000-0000-0000-000000000001",
            correlation_id="corr-1",
        )
    assert value == "sk-test"
