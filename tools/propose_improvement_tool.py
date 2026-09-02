"""Read-only background-review tool for versioned improvement proposals."""

import json
from typing import Any, Dict

from tools.registry import registry, tool_error


PROPOSE_IMPROVEMENT_SCHEMA = {
    "name": "propose_improvement",
    "description": (
        "Propose a grounded memory, prompt, routing, or skill improvement for external review. "
        "This tool is read-only and never edits MEMORY.md, USER.md, or skill files."
    ),
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "target": {
                "type": "string",
                "enum": ["memory", "prompt", "routing", "skill"],
            },
            "title": {"type": "string", "maxLength": 160},
            "rationale": {"type": "string", "maxLength": 2000},
            "patch": {"type": "object", "additionalProperties": True},
            "evidence_refs": {
                "type": "array",
                "maxItems": 16,
                "items": {"type": "string", "maxLength": 512},
            },
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        },
        "required": [
            "target",
            "title",
            "rationale",
            "patch",
            "evidence_refs",
            "confidence",
        ],
    },
}


def propose_improvement(args: Dict[str, Any], **_kwargs) -> str:
    title = str(args.get("title", "")).strip()
    rationale = str(args.get("rationale", "")).strip()
    target = str(args.get("target", "")).strip()
    if (
        not title
        or not rationale
        or target not in {"memory", "prompt", "routing", "skill"}
    ):
        return tool_error("A valid target, title, and rationale are required")
    proposal = {
        "target": target,
        "title": title[:160],
        "rationale": rationale[:2000],
        "patch": args.get("patch") if isinstance(args.get("patch"), dict) else {},
        "evidence_refs": [
            str(item)[:512] for item in (args.get("evidence_refs") or [])[:16]
        ],
        "confidence": max(0.0, min(1.0, float(args.get("confidence", 0)))),
    }
    return json.dumps({
        "success": True,
        "message": "Improvement proposed",
        "proposal": proposal,
    })


registry.register(
    name="propose_improvement",
    toolset="review",
    schema=PROPOSE_IMPROVEMENT_SCHEMA,
    handler=propose_improvement,
    emoji="",
)
