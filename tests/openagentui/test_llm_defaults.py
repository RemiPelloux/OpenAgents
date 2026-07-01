"""Tests for openagentui.llm_defaults."""

import os

from openagentui.llm_defaults import (
    DEFAULT_MISTRAL_MODEL,
    resolve_agent_runtime_kwargs,
)


def test_prefers_explicit_model(monkeypatch):
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    kwargs = resolve_agent_runtime_kwargs("gpt-4o")
    assert kwargs["model"] == "gpt-4o"


def test_mistral_when_api_key_set(monkeypatch):
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    monkeypatch.delenv("LLM_MODEL", raising=False)
    kwargs = resolve_agent_runtime_kwargs(None)
    assert kwargs["model"] == DEFAULT_MISTRAL_MODEL
    assert kwargs["provider"] == "mistral"
    assert kwargs["api_key"] == "test-key"


def test_llm_model_env_overrides_default(monkeypatch):
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    monkeypatch.setenv("LLM_MODEL", "mistral-large-latest")
    kwargs = resolve_agent_runtime_kwargs("")
    assert kwargs["model"] == "mistral-large-latest"


def test_llm_base_url_passed_through(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "https://api.mistral.ai/v1")
    kwargs = resolve_agent_runtime_kwargs("mistral-medium-latest")
    assert kwargs["base_url"] == "https://api.mistral.ai/v1"
