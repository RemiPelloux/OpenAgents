"""Minimal ``{{ ... }}`` variable substitution for workflow node fields.

Supports two reference forms, matching the upstream builder's convention:

* ``{{ someVar }}``            — looks up ``variables['someVar']``
* ``{{ nodes.<id>.output }}``  — looks up a previous node's output

Deliberately not a general template language (no loops/filters) — workflow
authors set logic via the ``if-else``/``while`` node types, not string
templates, so this only needs to fill placeholders in text fields.
"""

from __future__ import annotations

import re
from typing import Any, Dict

_PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_.]+)\s*\}\}")


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    import json

    try:
        return json.dumps(value, ensure_ascii=False)
    except TypeError:
        return str(value)


def _resolve_path(path: str, variables: Dict[str, Any], node_results: Dict[str, Any]) -> Any:
    parts = path.split(".")
    if parts[0] == "nodes" and len(parts) >= 2:
        node_id = parts[1]
        result = node_results.get(node_id)
        if result is None:
            return None
        value: Any = result.output if hasattr(result, "output") else result.get("output")
        # parts[2] is the literal "output" keyword in the "nodes.<id>.output[.field...]"
        # convention — already consumed above, so drilling continues from parts[3:].
        for part in parts[3:]:
            if isinstance(value, dict):
                value = value.get(part)
            else:
                return None
        return value

    value = variables
    for part in parts:
        if isinstance(value, dict) and part in value:
            value = value[part]
        else:
            return None
    return value


def render(text: Any, *, variables: Dict[str, Any], node_results: Dict[str, Any]) -> Any:
    """Substitute ``{{ path }}`` placeholders in ``text``.

    Non-string input is returned unchanged (node config fields are
    sometimes numbers/booleans/dicts already resolved by the builder UI).
    """
    if not isinstance(text, str):
        return text

    def _sub(match: "re.Match[str]") -> str:
        resolved = _resolve_path(match.group(1), variables, node_results)
        return _stringify(resolved) if resolved is not None else match.group(0)

    return _PLACEHOLDER_RE.sub(_sub, text)


def render_dict(data: Dict[str, Any], *, variables: Dict[str, Any], node_results: Dict[str, Any]) -> Dict[str, Any]:
    return {k: render(v, variables=variables, node_results=node_results) for k, v in data.items()}
