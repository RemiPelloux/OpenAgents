"""Local JSON-file persistence for OpenAgentUI.

Replaces the upstream project's Convex cloud database with plain files
under ``~/.openagents/openagentui/`` — no external account, no network
dependency. Each "table" from the original Convex schema (workflows,
executions, mcp_servers, approvals) is a directory of one JSON file per
record. Writes are atomic (write to a sibling ``.tmp`` file, then
``os.replace``) so a crash mid-write never leaves a half-written record.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from openagents_cli.config import get_openagents_home

from openagentui.schema import PendingApproval, Workflow, WorkflowExecution

logger = logging.getLogger(__name__)

_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,127}$")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _validate_id(record_id: str) -> str:
    """Reject anything that isn't a safe filename (no path traversal)."""
    if not _ID_RE.match(record_id or ""):
        raise ValueError(f"invalid id: {record_id!r}")
    return record_id


def openagentui_home() -> Path:
    home = get_openagents_home() / "openagentui"
    home.mkdir(parents=True, exist_ok=True)
    return home


def _table_dir(name: str) -> Path:
    path = openagentui_home() / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write_json(path: Path, data: Dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _list_records(table: str, *, predicate=None) -> List[Dict[str, Any]]:
    records = []
    for path in sorted(_table_dir(table).glob("*.json")):
        data = _read_json(path)
        if data is None:
            continue
        if predicate is not None and not predicate(data):
            continue
        records.append(data)
    return records


# ---------------------------------------------------------------------------
# Workflows
# ---------------------------------------------------------------------------


def save_workflow(workflow: Workflow) -> Workflow:
    _validate_id(workflow.id)
    if not workflow.created_at:
        workflow.created_at = _now_iso()
    workflow.updated_at = _now_iso()
    _write_json(_table_dir("workflows") / f"{workflow.id}.json", workflow.to_dict())
    return workflow


def get_workflow(workflow_id: str) -> Optional[Workflow]:
    _validate_id(workflow_id)
    data = _read_json(_table_dir("workflows") / f"{workflow_id}.json")
    return Workflow.from_dict(data) if data else None


def delete_workflow(workflow_id: str) -> bool:
    _validate_id(workflow_id)
    path = _table_dir("workflows") / f"{workflow_id}.json"
    if not path.exists():
        return False
    path.unlink()
    return True


def list_workflows() -> List[Workflow]:
    return [Workflow.from_dict(r) for r in _list_records("workflows")]


def list_workflow_summaries() -> List[Dict[str, Any]]:
    """List workflows without parsing node/edge graphs (list/home hot path)."""
    return [Workflow.summary_from_raw(r) for r in _list_records("workflows")]


def find_workflow_by_name(name: str) -> Optional[Workflow]:
    """Case-insensitive exact-name lookup, used by ``/OpenAgentConfig <name>``."""
    needle = name.strip().lower()
    for raw in _list_records("workflows"):
        wf_id = str(raw.get("id") or "")
        wf_name = str(raw.get("name") or "").strip().lower()
        if wf_name == needle or wf_id == name:
            return Workflow.from_dict(raw)
    return None


# ---------------------------------------------------------------------------
# Executions
# ---------------------------------------------------------------------------


def save_execution(execution: WorkflowExecution) -> WorkflowExecution:
    _validate_id(execution.id)
    _write_json(_table_dir("executions") / f"{execution.id}.json", execution.to_dict())
    return execution


def get_execution(execution_id: str) -> Optional[WorkflowExecution]:
    _validate_id(execution_id)
    data = _read_json(_table_dir("executions") / f"{execution_id}.json")
    return WorkflowExecution.from_dict(data) if data else None


def list_executions(workflow_id: Optional[str] = None) -> List[WorkflowExecution]:
    if workflow_id:
        _validate_id(workflow_id)
        predicate = lambda raw, wid=workflow_id: raw.get("workflowId") == wid
        records = _list_records("executions", predicate=predicate)
    else:
        records = _list_records("executions")
    executions = [WorkflowExecution.from_dict(r) for r in records]
    return sorted(executions, key=lambda e: e.started_at, reverse=True)


def execution_summary_from_raw(raw: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": str(raw["id"]),
        "workflowId": str(raw.get("workflowId", "")),
        "status": str(raw.get("status", "")),
        "startedAt": str(raw.get("startedAt", "")),
        "completedAt": raw.get("completedAt"),
        "error": raw.get("error"),
    }


def list_execution_summaries(workflow_id: Optional[str] = None, *, limit: int = 50) -> List[Dict[str, Any]]:
    if workflow_id:
        _validate_id(workflow_id)
        predicate = lambda raw, wid=workflow_id: raw.get("workflowId") == wid
        records = _list_records("executions", predicate=predicate)
    else:
        records = _list_records("executions")
    summaries = [execution_summary_from_raw(r) for r in records]
    summaries.sort(key=lambda row: row.get("startedAt") or "", reverse=True)
    return summaries[: max(1, int(limit))]


def prune_executions(*, keep_per_workflow: int = 20) -> int:
    """Delete oldest execution files beyond keep_per_workflow per workflow id."""
    if keep_per_workflow < 1:
        return 0
    by_workflow: Dict[str, List[Path]] = {}
    table = _table_dir("executions")
    for path in table.glob("*.json"):
        raw = _read_json(path)
        if raw is None:
            continue
        wid = str(raw.get("workflowId") or "")
        by_workflow.setdefault(wid, []).append(path)

    removed = 0
    for paths in by_workflow.values():
        rows = []
        for path in paths:
            raw = _read_json(path)
            if raw is None:
                continue
            rows.append((str(raw.get("startedAt") or ""), path))
        rows.sort(key=lambda item: item[0], reverse=True)
        for _, path in rows[keep_per_workflow:]:
            try:
                path.unlink()
                removed += 1
            except OSError:
                pass
    return removed


# ---------------------------------------------------------------------------
# Approvals (human-in-the-loop pause/resume)
# ---------------------------------------------------------------------------


def save_approval(approval: PendingApproval) -> PendingApproval:
    _validate_id(approval.approval_id)
    _write_json(_table_dir("approvals") / f"{approval.approval_id}.json", approval.to_dict())
    return approval


def get_approval(approval_id: str) -> Optional[PendingApproval]:
    _validate_id(approval_id)
    data = _read_json(_table_dir("approvals") / f"{approval_id}.json")
    return PendingApproval.from_dict(data) if data else None


def list_pending_approvals() -> List[PendingApproval]:
    approvals = [PendingApproval.from_dict(r) for r in _list_records("approvals")]
    return [a for a in approvals if a.status == "pending"]


# ---------------------------------------------------------------------------
# MCP server registry (subset of the upstream Convex ``mcpServers`` table —
# local, single-user, no per-user encryption since this is a loopback tool)
# ---------------------------------------------------------------------------


def save_mcp_server(server: Dict[str, Any]) -> Dict[str, Any]:
    server_id = _validate_id(str(server["id"]))
    _write_json(_table_dir("mcp_servers") / f"{server_id}.json", server)
    return server


def list_mcp_servers() -> List[Dict[str, Any]]:
    return _list_records("mcp_servers")


def delete_mcp_server(server_id: str) -> bool:
    _validate_id(server_id)
    path = _table_dir("mcp_servers") / f"{server_id}.json"
    if not path.exists():
        return False
    path.unlink()
    return True
