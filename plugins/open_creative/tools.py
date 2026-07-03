"""OpenCreative tools — image generation workflow for OpenBrain missions."""

from __future__ import annotations

import json
import os
import uuid
from typing import Any, Dict, List, Optional

from plugins.open_creative.brain_client import resolve_brain_secret
from plugins.open_creative.deliverable_client import post_deliverables

GENERATE_IMAGES_SCHEMA: Dict[str, Any] = {
    "name": "generate_openai_images",
    "description": "Generate images via OpenAI Images API using Brain-stored openai_api_key.",
    "parameters": {
        "type": "object",
        "properties": {
            "prompts": {
                "type": "array",
                "items": {"type": "string"},
                "description": "One prompt per image",
            },
            "workflow_run_id": {"type": "string"},
            "correlation_id": {"type": "string"},
            "size": {"type": "string", "default": "1024x1024"},
        },
        "required": ["prompts", "workflow_run_id", "correlation_id"],
    },
}

POST_DELIVERABLES_SCHEMA: Dict[str, Any] = {
    "name": "post_brain_deliverables",
    "description": "Send generated image URLs to OpenBrain for user review in chat.",
    "parameters": {
        "type": "object",
        "properties": {
            "session_id": {"type": "string"},
            "workflow_run_id": {"type": "string"},
            "correlation_id": {"type": "string"},
            "images": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "url": {"type": "string"},
                        "prompt": {"type": "string"},
                    },
                    "required": ["id", "url"],
                },
            },
            "summary": {"type": "string"},
        },
        "required": ["session_id", "workflow_run_id", "correlation_id", "images"],
    },
}


def _openai_generate(prompt: str, api_key: str, size: str = "1024x1024") -> Optional[str]:
    import urllib.request

    payload = json.dumps(
        {
            "model": "dall-e-3",
            "prompt": prompt[:4000],
            "n": 1,
            "size": size,
        }
    ).encode()
    req = urllib.request.Request(
        "https://api.openai.com/v1/images/generations",
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.load(resp)
        items = data.get("data") or []
        if items and isinstance(items[0], dict):
            return items[0].get("url")
    except Exception:
        return None
    return None


def handle_generate_openai_images(args: Dict[str, Any]) -> str:
    prompts = args.get("prompts") or []
    if not isinstance(prompts, list) or not prompts:
        return "Error: prompts array required"

    run_id = str(args.get("workflow_run_id") or "")
    corr = str(args.get("correlation_id") or "")
    if not run_id or not corr:
        return "Error: workflow_run_id and correlation_id required"

    api_key = resolve_brain_secret("openai_api_key", workflow_run_id=run_id, correlation_id=corr)
    if not api_key:
        api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return "Error: openai_api_key not available (Brain vault or OPENAI_API_KEY env)"

    size = str(args.get("size") or "1024x1024")
    results: List[Dict[str, str]] = []
    for prompt in prompts[:10]:
        url = _openai_generate(str(prompt), api_key, size)
        if url:
            results.append({"id": str(uuid.uuid4()), "url": url, "prompt": str(prompt)[:500]})

    if not results:
        return "Error: no images generated"
    return json.dumps({"images": results}, indent=2)


def handle_post_brain_deliverables(args: Dict[str, Any]) -> str:
    session_id = str(args.get("session_id") or "")
    run_id = str(args.get("workflow_run_id") or "")
    corr = str(args.get("correlation_id") or "")
    images = args.get("images")
    if not session_id or not run_id or not corr or not isinstance(images, list):
        return "Error: session_id, workflow_run_id, correlation_id, images required"

    ok = post_deliverables(
        session_id=session_id,
        workflow_run_id=run_id,
        correlation_id=corr,
        images=images,
        summary=str(args.get("summary") or ""),
    )
    return "Deliverables posted to OpenBrain for review." if ok else "Failed to post deliverables."
