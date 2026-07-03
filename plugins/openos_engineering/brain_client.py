"""Brain observation ingest — CC-BRAIN-001."""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def _brain_base_url() -> Optional[str]:
    raw = (
        os.environ.get("OPENBRAIN_URL", "").strip()
        or os.environ.get("OPENBRAIN_API_URL", "").strip()
    )
    return raw.rstrip("/") if raw else None


def _brain_api_key() -> Optional[str]:
    for key in (
        "OPENBRAIN_API_KEY",
        "AXON_AGENT_API_KEY",
        "OPENBRAIN_AGENT_API_KEY",
        "AXON_SERVICE_TOKEN",
    ):
        val = os.environ.get(key, "").strip()
        if val:
            return val
    return None


def build_agent_run_observation(
    event_type: str,
    *,
    ticket_id: str,
    ticket_key: Optional[str] = None,
    mode: str,
    agent_profile: str = "developer",
    agent_run_id: str,
    correlation_id: Optional[str] = None,
    summary: Optional[str] = None,
    exit_code: Optional[int] = None,
) -> Dict[str, Any]:
    display = ticket_key or ticket_id
    titles = {
        "agent.run.started": f"OpenAgents run started for ticket {display}",
        "agent.run.completed": f"OpenAgents run completed for ticket {display}",
        "agent.run.failed": f"OpenAgents run failed for ticket {display}",
    }
    parts = [f"mode={mode}", f"profile={agent_profile}", f"run_id={agent_run_id}"]
    if summary:
        parts.append(f"summary={summary[:500]}")
    if exit_code is not None:
        parts.append(f"exit_code={exit_code}")
    if correlation_id:
        parts.append(f"correlation={correlation_id}")

    obs_id = f"openagents:{event_type}:{agent_run_id}"
    return {
        "observationId": obs_id,
        "app": "openagents",
        "sourceType": "event",
        "title": titles.get(event_type, f"Agent run {event_type}"),
        "content": ". ".join(parts),
        "eventId": obs_id,
        "domain": "openos",
    }


def ingest_observation(body: Dict[str, Any]) -> None:
    base = _brain_base_url()
    if not base:
        return
    key = _brain_api_key()
    if not key:
        return

    data = json.dumps(body).encode()
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {key}"}
    req = urllib.request.Request(
        f"{base}/api/v1/brain/observations",
        data=data,
        method="POST",
        headers=headers,
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            if resp.status not in (200, 201, 202):
                logger.warning("Brain ingest returned %s", resp.status)
    except urllib.error.HTTPError as exc:
        if exc.code not in (200, 201, 202):
            logger.warning("Brain ingest HTTP error: %s", exc.code)
    except Exception as exc:
        logger.warning("Brain ingest failed: %s", exc)


def emit_agent_run_observation(
    event_type: str,
    *,
    ticket_id: str,
    ticket_key: Optional[str] = None,
    mode: str,
    agent_profile: str = "developer",
    agent_run_id: str,
    correlation_id: Optional[str] = None,
    summary: Optional[str] = None,
    exit_code: Optional[int] = None,
) -> None:
    body = build_agent_run_observation(
        event_type,
        ticket_id=ticket_id,
        ticket_key=ticket_key,
        mode=mode,
        agent_profile=agent_profile,
        agent_run_id=agent_run_id,
        correlation_id=correlation_id,
        summary=summary,
        exit_code=exit_code,
    )
    ingest_observation(body)
