"""Agent-callable tools for triggering saved OpenAgentUI workflows.

Lets any agent/subagent (including delegated roles — see
``tools/delegate_tool.py``) run a saved visual-builder scenario mid-
conversation, e.g. "dm a company on tiktok and create an OpenPro account
with the downloaded video" once that scenario has been built and saved as
a workflow. Toolset ``openagentui`` — opt-in like ``openpro_prospection``,
not enabled by default.
"""

from __future__ import annotations

import json
from typing import Any, Dict

from tools.registry import registry, tool_error, tool_result


def check_openagentui_available() -> bool:
    try:
        import openagentui  # noqa: F401

        return True
    except ImportError:
        return False


def _resolve_workflow(identifier: str):
    from openagentui.store import find_workflow_by_name

    return find_workflow_by_name(identifier)


def handle_list_openagentui_workflows(args: Dict[str, Any], **_kwargs) -> str:
    from openagentui.store import list_workflows

    workflows = [
        {
            "id": w.id,
            "name": w.name,
            "description": w.description,
            "isTemplate": w.is_template,
        }
        for w in list_workflows()
    ]
    return tool_result({"workflows": workflows})


def handle_run_openagentui_workflow(args: Dict[str, Any], **_kwargs) -> str:
    workflow_ref = str(args.get("workflow") or args.get("workflow_id") or "").strip()
    if not workflow_ref:
        return tool_error("'workflow' (name or id) is required")

    workflow = _resolve_workflow(workflow_ref)
    if workflow is None:
        return tool_error(f"unknown OpenAgentUI workflow: {workflow_ref!r}")

    inputs = args.get("inputs")
    if inputs is not None and not isinstance(inputs, dict):
        return tool_error("'inputs' must be an object of variable_name -> value")

    from openagentui.engine import run_workflow

    execution = run_workflow(workflow, inputs=inputs or {})
    payload = {
        "executionId": execution.id,
        "workflowId": workflow.id,
        "workflowName": workflow.name,
        "status": execution.status,
        "variables": execution.variables,
    }
    if execution.status == "waiting-approval":
        payload["pendingApprovalId"] = execution.pending_approval_id
        payload["hint"] = (
            "Workflow paused for human approval. Tell the user, then call "
            "resolve_openagentui_approval once they decide, or have them run "
            f"`/OpenAgentConfig approve {execution.id}`."
        )
    elif execution.status == "failed":
        payload["error"] = execution.error
    return tool_result(payload)


def handle_resolve_openagentui_approval(args: Dict[str, Any], **_kwargs) -> str:
    execution_id = str(args.get("execution_id") or "").strip()
    decision = str(args.get("decision") or "").strip().lower()
    if not execution_id or decision not in {"approved", "rejected"}:
        return tool_error(
            "'execution_id' and decision ('approved'|'rejected') are required"
        )

    from openagentui.approvals import resolve_approval

    try:
        execution = resolve_approval(execution_id, decision)
    except ValueError as exc:
        return tool_error(str(exc))

    return tool_result({
        "executionId": execution.id,
        "status": execution.status,
        "variables": execution.variables,
        "error": execution.error,
    })


def handle_create_openagentui_workflow_from_yaml(
    args: Dict[str, Any], **_kwargs
) -> str:
    yaml_text = str(args.get("yaml") or args.get("content") or "").strip()
    if not yaml_text:
        return tool_error("'yaml' text is required")

    from openagentui.store import save_workflow
    from openagentui.yaml_io import workflow_from_yaml

    workflow_id = str(args.get("workflow_id") or args.get("id") or "").strip() or None
    try:
        workflow = workflow_from_yaml(yaml_text, workflow_id=workflow_id)
    except ValueError as exc:
        return tool_error(str(exc))

    saved = save_workflow(workflow)
    return tool_result({"created": True, "workflow": saved.to_dict()})


def handle_export_openagentui_workflow_yaml(args: Dict[str, Any], **_kwargs) -> str:
    workflow_ref = str(args.get("workflow") or args.get("workflow_id") or "").strip()
    if not workflow_ref:
        return tool_error("'workflow' (name or id) is required")

    workflow = _resolve_workflow(workflow_ref)
    if workflow is None:
        return tool_error(f"unknown OpenAgentUI workflow: {workflow_ref!r}")

    from openagentui.yaml_io import workflow_to_yaml_text

    return tool_result({
        "id": workflow.id,
        "name": workflow.name,
        "yaml": workflow_to_yaml_text(workflow),
    })


def handle_ensure_openagentui_workflow(args: Dict[str, Any], **_kwargs) -> str:
    """Create a workflow from YAML when missing; otherwise return the existing one."""
    name = str(args.get("name") or "").strip()
    yaml_text = str(args.get("yaml") or args.get("content") or "").strip()
    if not name or not yaml_text:
        return tool_error("'name' and 'yaml' are required")

    existing = _resolve_workflow(name)
    if existing is not None:
        return tool_result({"created": False, "workflow": existing.to_dict()})

    from openagentui.store import save_workflow
    from openagentui.yaml_io import workflow_from_yaml

    try:
        workflow = workflow_from_yaml(yaml_text)
    except ValueError as exc:
        return tool_error(str(exc))

    workflow.name = name
    saved = save_workflow(workflow)
    return tool_result({"created": True, "workflow": saved.to_dict()})


LIST_SCHEMA: Dict[str, Any] = {
    "name": "list_openagentui_workflows",
    "description": "List saved OpenAgentUI visual-builder workflows available to run.",
    "parameters": {"type": "object", "properties": {}},
}

RUN_SCHEMA: Dict[str, Any] = {
    "name": "run_openagentui_workflow",
    "description": (
        "Run a saved OpenAgentUI workflow (visual builder scenario) headlessly. "
        "Use for repeatable multi-step scenarios someone has already built, e.g. "
        "'DM a TikTok lead and provision an OpenPro account with their video'."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "workflow": {
                "type": "string",
                "description": "Workflow name or id (see list_openagentui_workflows).",
            },
            "inputs": {
                "type": "object",
                "description": "Variable name -> value seeding the workflow's start node.",
            },
        },
        "required": ["workflow"],
    },
}

RESOLVE_APPROVAL_SCHEMA: Dict[str, Any] = {
    "name": "resolve_openagentui_approval",
    "description": "Approve or reject a paused OpenAgentUI workflow's user-approval node and resume it.",
    "parameters": {
        "type": "object",
        "properties": {
            "execution_id": {"type": "string"},
            "decision": {"type": "string", "enum": ["approved", "rejected"]},
        },
        "required": ["execution_id", "decision"],
    },
}

CREATE_YAML_SCHEMA: Dict[str, Any] = {
    "name": "create_openagentui_workflow_from_yaml",
    "description": "Create or overwrite an OpenAgentUI workflow from YAML (headless builder, no UI).",
    "parameters": {
        "type": "object",
        "properties": {
            "yaml": {"type": "string", "description": "Workflow YAML document"},
            "workflow_id": {"type": "string", "description": "Optional id override"},
        },
        "required": ["yaml"],
    },
}

EXPORT_YAML_SCHEMA: Dict[str, Any] = {
    "name": "export_openagentui_workflow_yaml",
    "description": "Export a saved OpenAgentUI workflow as YAML for editing or version control.",
    "parameters": {
        "type": "object",
        "properties": {
            "workflow": {"type": "string", "description": "Workflow name or id"},
        },
        "required": ["workflow"],
    },
}

ENSURE_WORKFLOW_SCHEMA: Dict[str, Any] = {
    "name": "ensure_openagentui_workflow",
    "description": (
        "Ensure a named OpenAgentUI workflow exists — create from YAML if missing, "
        "otherwise return the existing workflow unchanged."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Human-readable workflow name"},
            "yaml": {
                "type": "string",
                "description": "YAML to create when the workflow is absent",
            },
        },
        "required": ["name", "yaml"],
    },
}

# NOTE: each registry.register() call must be a direct top-level statement
# (not inside a loop/function) — tools/registry.py's discover_builtin_tools()
# only auto-imports modules whose AST has a bare top-level `registry.register(...)`
# expression, so it can tell tool modules apart from plain helper modules.
registry.register(
    name=LIST_SCHEMA["name"],
    toolset="openagentui",
    schema=LIST_SCHEMA,
    handler=handle_list_openagentui_workflows,
    check_fn=check_openagentui_available,
    emoji="📋",
)
registry.register(
    name=RUN_SCHEMA["name"],
    toolset="openagentui",
    schema=RUN_SCHEMA,
    handler=handle_run_openagentui_workflow,
    check_fn=check_openagentui_available,
    emoji="🧩",
)
registry.register(
    name=RESOLVE_APPROVAL_SCHEMA["name"],
    toolset="openagentui",
    schema=RESOLVE_APPROVAL_SCHEMA,
    handler=handle_resolve_openagentui_approval,
    check_fn=check_openagentui_available,
    emoji="✅",
)
registry.register(
    name=CREATE_YAML_SCHEMA["name"],
    toolset="openagentui",
    schema=CREATE_YAML_SCHEMA,
    handler=handle_create_openagentui_workflow_from_yaml,
    check_fn=check_openagentui_available,
    emoji="📝",
)
registry.register(
    name=EXPORT_YAML_SCHEMA["name"],
    toolset="openagentui",
    schema=EXPORT_YAML_SCHEMA,
    handler=handle_export_openagentui_workflow_yaml,
    check_fn=check_openagentui_available,
    emoji="📤",
)
registry.register(
    name=ENSURE_WORKFLOW_SCHEMA["name"],
    toolset="openagentui",
    schema=ENSURE_WORKFLOW_SCHEMA,
    handler=handle_ensure_openagentui_workflow,
    check_fn=check_openagentui_available,
    emoji="🔧",
)
