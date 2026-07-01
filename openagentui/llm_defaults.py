"""Default LLM selection for OpenAgentUI agent nodes.

OpenAgentUI does not use xAI Grok / grok-build. Prefer Mistral when
``MISTRAL_API_KEY`` is set, then ``LLM_MODEL``, then OpenAgents config default.
"""

from __future__ import annotations

import os
from typing import Any, Dict

DEFAULT_MISTRAL_MODEL = "mistral-medium-latest"
DEFAULT_MISTRAL_BASE = "https://api.mistral.ai/v1"
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"


def resolve_agent_runtime_kwargs(explicit_model: str | None = None) -> Dict[str, Any]:
    """Build ``AIAgent`` kwargs for a workflow agent node."""
    model = (explicit_model or "").strip()
    provider = (os.environ.get("LLM_PROVIDER") or os.environ.get("OPENAGENTS_PROVIDER") or "").strip()

    if not model:
        model = (os.environ.get("LLM_MODEL") or os.environ.get("OPENAGENTS_MODEL") or "").strip()

    if not model and (os.environ.get("MISTRAL_API_KEY") or "").strip():
        model = DEFAULT_MISTRAL_MODEL
        provider = provider or "mistral"

    if not model and (os.environ.get("OPENAI_API_KEY") or "").strip():
        model = DEFAULT_OPENAI_MODEL
        provider = provider or "openai-api"

    kwargs: Dict[str, Any] = {"model": model}
    if provider:
        kwargs["provider"] = provider

    base_url = (os.environ.get("LLM_BASE_URL") or os.environ.get("MISTRAL_BASE_URL") or "").strip()
    if base_url:
        kwargs["base_url"] = base_url

    api_key = (os.environ.get("LLM_API_KEY") or os.environ.get("MISTRAL_API_KEY") or "").strip()
    if api_key:
        kwargs["api_key"] = api_key

    return kwargs
