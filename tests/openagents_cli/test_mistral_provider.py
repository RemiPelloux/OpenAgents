"""Mistral provider registration in OpenAgents auth/models."""

from openagents_cli.auth import PROVIDER_REGISTRY
from openagents_cli.models import (
    CANONICAL_PROVIDERS,
    _PROVIDER_MODELS,
    get_default_model_for_provider,
)


def test_mistral_in_provider_registry():
    cfg = PROVIDER_REGISTRY["mistral"]
    assert cfg.inference_base_url == "https://api.mistral.ai/v1"
    assert "MISTRAL_API_KEY" in cfg.api_key_env_vars


def test_mistral_models_listed():
    models = _PROVIDER_MODELS["mistral"]
    assert "mistral-medium-latest" in models
    assert get_default_model_for_provider("mistral") == "mistral-medium-latest"


def test_mistral_in_canonical_providers():
    slugs = [p.slug for p in CANONICAL_PROVIDERS]
    assert "mistral" in slugs
