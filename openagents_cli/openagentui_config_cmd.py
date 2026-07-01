"""Shared ``/OpenAgentConfig`` command — run/list saved OpenAgentUI workflows.

Executes workflows headlessly through ``openagentui/engine.py`` directly —
the visual builder UI (``/OpenAgentUI``) does not need to be running.

Subcommands::

  /OpenAgentConfig                        list saved workflows
  /OpenAgentConfig list                   same as bare
  /OpenAgentConfig show <name-or-id>       show a workflow's nodes/edges
  /OpenAgentConfig run <name-or-id> [k=v ...]   run it (default action)
  /OpenAgentConfig <name-or-id> [k=v ...]  shorthand for `run`
  /OpenAgentConfig approve <execution_id>  resolve a pending approval and resume
  /OpenAgentConfig reject <execution_id>   reject a pending approval and resume
"""

from __future__ import annotations

import difflib
import logging
import shlex
from dataclasses import dataclass
from typing import Dict, List, Tuple

logger = logging.getLogger(__name__)

_KNOWN_VERBS = ("list", "show", "run", "approve", "reject")


@dataclass
class OpenAgentConfigCommandResult:
    text: str


def _parse_kv(tokens: List[str]) -> Tuple[Dict[str, str], List[str]]:
    values: Dict[str, str] = {}
    rest: List[str] = []
    for tok in tokens:
        if "=" in tok:
            k, _, v = tok.partition("=")
            if k.strip():
                values[k.strip()] = v.strip()
                continue
        rest.append(tok)
    return values, rest


def _format_list() -> str:
    from openagentui.store import list_workflows

    workflows = list_workflows()
    if not workflows:
        return (
            "No saved workflows yet. Create one with `/OpenAgentUI true` (visual builder) "
            "or import the bundled template: openagentui/templates/openpro_tiktok_prospection.json"
        )
    lines = ["Saved OpenAgentUI workflows:\n"]
    for wf in workflows:
        tag = " [template]" if wf.is_template else ""
        lines.append(f"  • {wf.name} ({wf.id}){tag} — {len(wf.nodes)} nodes")
    lines.append("\n`/OpenAgentConfig show <name>` for details · `/OpenAgentConfig run <name> key=value ...`")
    return "\n".join(lines)


def _format_show(name: str) -> str:
    from openagentui.store import find_workflow_by_name

    workflow = find_workflow_by_name(name)
    if workflow is None:
        return _unknown_workflow(name)
    node_lines = "\n".join(f"  - {n.id} ({n.type})" for n in workflow.nodes)
    return (
        f"**{workflow.name}** ({workflow.id})\n"
        f"{workflow.description or '(no description)'}\n\n"
        f"Nodes ({len(workflow.nodes)}):\n{node_lines}\n\n"
        f"Edges: {len(workflow.edges)}"
    )


def _unknown_workflow(name: str) -> str:
    from openagentui.store import list_workflows

    known = [w.name for w in list_workflows()]
    close = difflib.get_close_matches(name, known, n=3)
    hint = f" Did you mean: {', '.join(close)}?" if close else ""
    return f"Unknown workflow {name!r}.{hint}\n\n{_format_list()}"


def _format_node_progress(node_id: str, status: str, error: str = "") -> str:
    icon = {"completed": "✅", "failed": "❌", "pending-approval": "⏸️"}.get(status, "▶️")
    suffix = f" — {error}" if error else ""
    return f"{icon} {node_id}: {status}{suffix}"


def _run_workflow(name: str, inputs: Dict[str, str]) -> str:
    from openagentui.engine import run_workflow
    from openagentui.store import find_workflow_by_name

    workflow = find_workflow_by_name(name)
    if workflow is None:
        return _unknown_workflow(name)

    progress_lines: List[str] = []
    execution = run_workflow(
        workflow,
        inputs=inputs,
        on_node=lambda result: progress_lines.append(
            _format_node_progress(result.node_id, result.status, result.error or "")
        ),
    )

    header = f"Running **{workflow.name}** (execution {execution.id})\n"
    body = "\n".join(progress_lines)
    if execution.status == "waiting-approval":
        footer = (
            f"\n\n⏸️ Paused for approval. Resolve with "
            f"`/OpenAgentConfig approve {execution.id}` or `/OpenAgentConfig reject {execution.id}`."
        )
    elif execution.status == "failed":
        footer = f"\n\n❌ Failed: {execution.error}"
    else:
        footer = f"\n\n✅ Completed. Final variables: {execution.variables}"
    return header + body + footer


def _resolve_approval(execution_id: str, decision: str) -> str:
    """Resolve a pending approval. ``decision`` must be "approved" or "rejected"
    (the past-tense form ``resolve_approval`` requires), not the bare verb."""
    from openagentui.approvals import resolve_approval

    try:
        execution = resolve_approval(execution_id, decision)
    except ValueError as exc:
        return str(exc)

    label = decision.capitalize()
    if execution.status == "waiting-approval":
        return f"{label}. Workflow paused again at another approval (execution {execution.id})."
    if execution.status == "failed":
        return f"{label}. Workflow then failed: {execution.error}"
    return f"{label}. Workflow completed. Final variables: {execution.variables}"


def handle_openagentconfig_command(args: str) -> OpenAgentConfigCommandResult:
    raw = (args or "").strip()
    if not raw:
        return OpenAgentConfigCommandResult(text=_format_list())

    try:
        tokens = shlex.split(raw)
    except ValueError as exc:
        return OpenAgentConfigCommandResult(text=f"Could not parse arguments: {exc}")
    if not tokens:
        return OpenAgentConfigCommandResult(text=_format_list())

    first = tokens[0].lower()
    if first == "list":
        return OpenAgentConfigCommandResult(text=_format_list())
    if first == "show" and len(tokens) > 1:
        return OpenAgentConfigCommandResult(text=_format_show(" ".join(tokens[1:])))
    if first in {"approve", "reject"} and len(tokens) > 1:
        decision = "approved" if first == "approve" else "rejected"
        return OpenAgentConfigCommandResult(text=_resolve_approval(tokens[1], decision))
    if first == "run" and len(tokens) > 1:
        kv, rest = _parse_kv(tokens[2:])
        return OpenAgentConfigCommandResult(text=_run_workflow(tokens[1], kv))

    if first in _KNOWN_VERBS:
        return OpenAgentConfigCommandResult(
            text=f"Usage: /OpenAgentConfig {first} <name-or-id> [key=value ...]"
        )

    kv, rest = _parse_kv(tokens[1:])
    name = " ".join([tokens[0]] + rest) if rest else tokens[0]
    return OpenAgentConfigCommandResult(text=_run_workflow(name, kv))


# ---------------------------------------------------------------------------
# Terminal CLI — ``openagents openagent-config list|show|run|approve|reject``
# ---------------------------------------------------------------------------


def build_parser(parent_subparsers):
    parser = parent_subparsers.add_parser(
        "openagent-config",
        help="Run or manage saved OpenAgentUI workflows",
        description="Headlessly run/list/inspect OpenAgentUI workflows (no UI process required).",
    )
    sub = parser.add_subparsers(dest="openagentconfig_action")

    sub.add_parser("list", help="List saved workflows")
    show_p = sub.add_parser("show", help="Show a workflow's nodes/edges")
    show_p.add_argument("name")
    run_p = sub.add_parser("run", help="Run a workflow headlessly")
    run_p.add_argument("name")
    run_p.add_argument("inputs", nargs="*", help="key=value pairs seeding workflow variables")
    approve_p = sub.add_parser("approve", help="Approve a paused execution and resume it")
    approve_p.add_argument("execution_id")
    reject_p = sub.add_parser("reject", help="Reject a paused execution and resume it")
    reject_p.add_argument("execution_id")

    parser.set_defaults(_openagentconfig_parser=parser)
    return parser


def openagentconfig_command(args) -> int:
    action = getattr(args, "openagentconfig_action", None) or "list"
    if action == "show":
        print(_format_show(args.name))
    elif action == "run":
        kv, _ = _parse_kv(list(getattr(args, "inputs", []) or []))
        print(_run_workflow(args.name, kv))
    elif action == "approve":
        print(_resolve_approval(args.execution_id, "approved"))
    elif action == "reject":
        print(_resolve_approval(args.execution_id, "rejected"))
    else:
        print(_format_list())
    return 0
