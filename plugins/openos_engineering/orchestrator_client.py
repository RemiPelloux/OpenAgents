"""Notify OpenOrchestrator when an agent run reaches a terminal state."""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

from plugins.openos_mesh.contract_wrap import wrap_signed_hop

logger = logging.getLogger(__name__)

ORCH_AGENT_OUTCOME = "CC-ORCH-003"
W4_ORCHESTRATOR_ASSIGN = "CC-W4-003"
W4_DEV_OPENCODE = "CC-W4-004"


def _orchestrator_base_url() -> str:
    return os.environ.get("ORCHESTRATOR_URL", "http://localhost:3050").rstrip("/")


def notify_task_outcome(
    *,
    task_id: str,
    correlation_id: Optional[str],
    success: bool,
    reason: str,
    cost_usd: Optional[float] = None,
    latency_ms: Optional[int] = None,
) -> None:
    """POST signed CC-ORCH-003 to OpenOrchestrator task outcome routes."""
    if not task_id:
        return
    if os.environ.get("ORCHESTRATOR_CALLBACKS_ENABLED", "1").strip().lower() in (
        "0",
        "false",
        "no",
    ):
        return

    payload: Dict[str, Any] = {"reason": reason[:500]}
    if cost_usd is not None:
        payload["cost_usd"] = cost_usd
    if latency_ms is not None:
        payload["latency_ms"] = latency_ms

    prereq = [W4_ORCHESTRATOR_ASSIGN]
    if success:
        prereq.append(W4_DEV_OPENCODE)

    body = wrap_signed_hop(
        contract_id=ORCH_AGENT_OUTCOME,
        producer="OpenAgents",
        consumer="OpenOrchestrator",
        payload=payload,
        correlation_id=correlation_id,
        prerequisites=prereq,
        goal_met=success,
        signer_id="OpenAgents",
    )

    path = "complete" if success else "fail"
    url = f"{_orchestrator_base_url()}/v1/tasks/{task_id}/{path}"
    headers = {"Content-Type": "application/json"}
    if correlation_id:
        headers["X-Correlation-Id"] = correlation_id

    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        method="POST",
        headers=headers,
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            if resp.status >= 400:
                logger.warning("orchestrator outcome returned %s", resp.status)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        logger.warning("orchestrator outcome failed (%s): %s", exc.code, detail[:200])
    except Exception:
        logger.exception("orchestrator outcome notify failed")
