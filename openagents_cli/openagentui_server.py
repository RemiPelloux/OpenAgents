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
from openagentui.validation import validate_workflow
from openagentui.yaml_io import workflow_from_yaml, workflow_to_yaml_text

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/openagentui")

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "openagentui" / "templates"
_STREAM_DONE = object()
_TEMPLATE_INDEX: Optional[Dict[str, Dict[str, Any]]] = None
_TEMPLATE_INDEX_MTIME: float = 0.0


# ---------------------------------------------------------------------------
# Workflow CRUD
# ---------------------------------------------------------------------------


def _template_index() -> Dict[str, Dict[str, Any]]:
    """Load bundled templates once per directory mtime (avoids N+1 reads)."""
    global _TEMPLATE_INDEX, _TEMPLATE_INDEX_MTIME
    try:
        mtime = max((p.stat().st_mtime for p in _TEMPLATES_DIR.glob("*.json")), default=0.0)
    except OSError:
        mtime = 0.0
    if _TEMPLATE_INDEX is not None and mtime == _TEMPLATE_INDEX_MTIME:
        return _TEMPLATE_INDEX

    index: Dict[str, Dict[str, Any]] = {}
    for path in sorted(_TEMPLATES_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.warning("openagentui: failed to parse bundled template %s", path)
            continue
        template_id = str(data.get("id") or path.stem)
        index[template_id] = data
    _TEMPLATE_INDEX = index
    _TEMPLATE_INDEX_MTIME = mtime
    return index


def _template_cards() -> List[Dict[str, Any]]:
    return [Workflow.template_card_from_raw(data) for data in _template_index().values()]


@router.get("/workflows")
def list_workflows_route() -> Dict[str, Any]:
    return {"workflows": store.list_workflow_summaries()}


@router.get("/home")
def home_bootstrap_route() -> Dict[str, Any]:
    """Single round-trip payload for the workflow list landing page."""
    return {
        "workflows": store.list_workflow_summaries(),
        "templates": _template_cards(),
    }


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


@router.get("/workflows/{workflow_id}/editor")
def editor_bootstrap_route(workflow_id: str) -> Dict[str, Any]:
    """Workflow + tool catalog + workflow picker list in one call."""
    workflow = store.get_workflow(workflow_id)
    if workflow is None:
        raise HTTPException(status_code=404, detail=f"unknown workflow: {workflow_id}")
    return {
        "workflow": workflow.to_dict(),
        "catalog": catalog_snapshot(),
        "workflows": store.list_workflow_summaries(),
    }


@router.post("/workflows/{workflow_id}/duplicate")
def duplicate_workflow_route(workflow_id: str) -> Dict[str, Any]:
    workflow = store.get_workflow(workflow_id)
    if workflow is None:
        raise HTTPException(status_code=404, detail=f"unknown workflow: {workflow_id}")
    data = workflow.to_dict()
    data["id"] = store.new_id("wf")
    data["name"] = f"{workflow.name} (copy)"
    saved = store.save_workflow(Workflow.from_dict(data))
    return saved.to_dict()


@router.post("/workflows/{workflow_id}/validate")
def validate_workflow_route(workflow_id: str) -> Dict[str, Any]:
    workflow = store.get_workflow(workflow_id)
    if workflow is None:
        raise HTTPException(status_code=404, detail=f"unknown workflow: {workflow_id}")
    errors = validate_workflow(workflow)
    return {"ok": not errors, "errors": errors}


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
    workflow = store.get_workflow(workflow_id)
    if workflow is None:
        raise HTTPException(status_code=404, detail=f"unknown workflow: {workflow_id}")
    return {"id": workflow.id, "yaml": workflow_to_yaml_text(workflow)}


@router.post("/workflows/from-yaml")
def create_workflow_from_yaml_route(body: Dict[str, Any]) -> Dict[str, Any]:
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
    return {"templates": list(_template_index().values())}


@router.post("/templates/{template_id}/install")
def install_template_route(template_id: str) -> Dict[str, Any]:
    data = _template_index().get(template_id)
    if data is None:
        raise HTTPException(status_code=404, detail=f"unknown template: {template_id}")
    data = dict(data)
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
    try:
        store.prune_executions()
    except Exception:
        pass
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
    return {"executions": store.list_execution_summaries(workflow_id)}


@router.post("/executions/prune")
def prune_executions_route(body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    keep = int((body or {}).get("keepPerWorkflow") or 20)
    removed = store.prune_executions(keep_per_workflow=keep)
    return {"removed": removed}


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
