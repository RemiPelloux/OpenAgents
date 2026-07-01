"""HTTP routes for OpenAgentUI, mounted by ``web_server``.

Kept out of ``web_server.py`` (already 14k+ lines) the same way memory-provider
OAuth routes are — see ``memory_oauth.py`` for the precedent. Every route here
inherits the dashboard's existing loopback-token auth middleware for free by
being registered on the same ``FastAPI`` app; no separate auth is invented.

Workflow/node JSON bodies are accepted as loose ``dict`` (not typed Pydantic
models) because ``NodeData`` has ~50 optional, node-type-dependent fields —
validation happens structurally in ``openagentui.schema`` instead.
"""

from __future__ import annotations

import asyncio
import json
import logging
import queue
import threading
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from openagentui import approvals, engine, store
from openagentui.schema import Workflow
from openagentui.tool_catalog import catalog_snapshot

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/openagentui")

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "openagentui" / "templates"
_STREAM_DONE = object()


# ---------------------------------------------------------------------------
# Workflow CRUD
# ---------------------------------------------------------------------------


@router.get("/workflows")
def list_workflows_route() -> Dict[str, Any]:
    return {"workflows": [w.to_dict() for w in store.list_workflows()]}


@router.post("/workflows")
def create_workflow_route(body: Dict[str, Any]) -> Dict[str, Any]:
    body.setdefault("id", store.new_id("wf"))
    workflow = store.save_workflow(Workflow.from_dict(body))
    return workflow.to_dict()


@router.get("/workflows/{workflow_id}")
def get_workflow_route(workflow_id: str) -> Dict[str, Any]:
    workflow = store.get_workflow(workflow_id)
    if workflow is None:
        raise HTTPException(status_code=404, detail=f"unknown workflow: {workflow_id}")
    return workflow.to_dict()


@router.put("/workflows/{workflow_id}")
def update_workflow_route(workflow_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
    body["id"] = workflow_id
    existing = store.get_workflow(workflow_id)
    if existing is not None:
        body.setdefault("createdAt", existing.created_at)
    workflow = store.save_workflow(Workflow.from_dict(body))
    return workflow.to_dict()


@router.delete("/workflows/{workflow_id}")
def delete_workflow_route(workflow_id: str) -> Dict[str, Any]:
    deleted = store.delete_workflow(workflow_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"unknown workflow: {workflow_id}")
    return {"deleted": True}


@router.get("/workflows/{workflow_id}/yaml")
def export_workflow_yaml_route(workflow_id: str) -> Dict[str, Any]:
    from openagentui.yaml_io import workflow_to_yaml_text

    workflow = store.get_workflow(workflow_id)
    if workflow is None:
        raise HTTPException(status_code=404, detail=f"unknown workflow: {workflow_id}")
    return {"id": workflow.id, "yaml": workflow_to_yaml_text(workflow)}


@router.post("/workflows/from-yaml")
def create_workflow_from_yaml_route(body: Dict[str, Any]) -> Dict[str, Any]:
    from openagentui.yaml_io import workflow_from_yaml

    yaml_text = str(body.get("yaml") or body.get("content") or "").strip()
    if not yaml_text:
        raise HTTPException(status_code=400, detail="'yaml' text is required")
    workflow_id = str(body.get("id") or "").strip() or None
    try:
        workflow = workflow_from_yaml(yaml_text, workflow_id=workflow_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    saved = store.save_workflow(workflow)
    return saved.to_dict()


@router.put("/workflows/{workflow_id}/from-yaml")
def upsert_workflow_from_yaml_route(workflow_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
    from openagentui.yaml_io import workflow_from_yaml

    yaml_text = str(body.get("yaml") or body.get("content") or "").strip()
    if not yaml_text:
        raise HTTPException(status_code=400, detail="'yaml' text is required")
    try:
        workflow = workflow_from_yaml(yaml_text, workflow_id=workflow_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    saved = store.save_workflow(workflow)
    return saved.to_dict()

# ---------------------------------------------------------------------------
# Templates (bundled + user-saved-as-template)
# ---------------------------------------------------------------------------


@router.get("/templates")
def list_templates_route() -> Dict[str, Any]:
    templates = []
    for path in sorted(_TEMPLATES_DIR.glob("*.json")):
        try:
            templates.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            logger.warning("openagentui: failed to parse bundled template %s", path)
    return {"templates": templates}


@router.post("/templates/{template_id}/install")
def install_template_route(template_id: str) -> Dict[str, Any]:
    path = _TEMPLATES_DIR / f"{template_id.replace('tpl_', '', 1)}.json"
    matches = [p for p in _TEMPLATES_DIR.glob("*.json") if json.loads(p.read_text(encoding="utf-8")).get("id") == template_id]
    if not matches:
        raise HTTPException(status_code=404, detail=f"unknown template: {template_id}")
    data = json.loads(matches[0].read_text(encoding="utf-8"))
    data["id"] = store.new_id("wf")
    data["isTemplate"] = False
    workflow = store.save_workflow(Workflow.from_dict(data))
    return workflow.to_dict()


# ---------------------------------------------------------------------------
# Tool/toolset/MCP catalog — powers the node config pickers
# ---------------------------------------------------------------------------


@router.get("/catalog")
def catalog_route() -> Dict[str, Any]:
    return catalog_snapshot()


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


@router.post("/workflows/{workflow_id}/run")
def run_workflow_route(workflow_id: str, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    workflow = store.get_workflow(workflow_id)
    if workflow is None:
        raise HTTPException(status_code=404, detail=f"unknown workflow: {workflow_id}")
    inputs = (body or {}).get("inputs") or {}
    execution = engine.run_workflow(workflow, inputs=inputs)
    return execution.to_dict()


@router.post("/workflows/{workflow_id}/execute-stream")
def execute_stream_route(workflow_id: str, body: Optional[Dict[str, Any]] = None) -> StreamingResponse:
    """Server-Sent Events stream: one ``event: node`` per completed node, then ``event: done``."""
    workflow = store.get_workflow(workflow_id)
    if workflow is None:
        raise HTTPException(status_code=404, detail=f"unknown workflow: {workflow_id}")
    inputs = (body or {}).get("inputs") or {}

    q: "queue.Queue[Any]" = queue.Queue()

    def _on_node(result) -> None:
        q.put(("node", result.to_dict()))

    def _worker() -> None:
        try:
            execution = engine.run_workflow(workflow, inputs=inputs, on_node=_on_node)
            q.put(("done", execution.to_dict()))
        except Exception as exc:  # pragma: no cover - defensive: never hang the stream
            logger.exception("openagentui: execute-stream worker failed")
            q.put(("error", {"error": str(exc)}))
        finally:
            q.put((None, _STREAM_DONE))

    threading.Thread(target=_worker, daemon=True).start()

    async def _events():
        loop = asyncio.get_event_loop()
        while True:
            event_name, payload = await loop.run_in_executor(None, q.get)
            if payload is _STREAM_DONE:
                break
            yield f"event: {event_name}\ndata: {json.dumps(payload)}\n\n"

    return StreamingResponse(_events(), media_type="text/event-stream")


@router.get("/workflows/{workflow_id}/executions")
def list_executions_route(workflow_id: str) -> Dict[str, Any]:
    return {"executions": [e.to_dict() for e in store.list_executions(workflow_id)]}


@router.get("/executions/{execution_id}")
def get_execution_route(execution_id: str) -> Dict[str, Any]:
    execution = store.get_execution(execution_id)
    if execution is None:
        raise HTTPException(status_code=404, detail=f"unknown execution: {execution_id}")
    return execution.to_dict()


@router.post("/executions/{execution_id}/approve")
def approve_execution_route(execution_id: str) -> Dict[str, Any]:
    try:
        return approvals.resolve_approval(execution_id, "approved").to_dict()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/executions/{execution_id}/reject")
def reject_execution_route(execution_id: str) -> Dict[str, Any]:
    try:
        return approvals.resolve_approval(execution_id, "rejected").to_dict()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
