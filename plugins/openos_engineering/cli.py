"""CLI: openagents openos {init-profiles|handle-run}"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict

from plugins.openos_engineering.profiles import ensure_profiles, init_profiles, list_profile_ids
from plugins.openos_engineering.ticket_client import apply_task_context_env
from plugins.openos_engineering.tools import handle_invoke_opencode


def register_cli(subparser: argparse.ArgumentParser) -> None:
    subs = subparser.add_subparsers(dest="openos_action")

    subs.add_parser(
        "init-profiles",
        help="Scaffold all OpenOS domain profiles (18 roles)",
    )

    ensure_p = subs.add_parser(
        "ensure-profiles",
        help="Create missing OpenOS profiles without overwriting existing",
    )
    ensure_p.add_argument(
        "--profiles",
        help="Comma-separated profile ids (default: all catalog profiles)",
        default="",
    )
    list_p = subs.add_parser("list-profiles", help="List catalog profile ids")
    list_p.add_argument("--json", action="store_true", help="Emit JSON array")

    run_p = subs.add_parser(
        "handle-run",
        help="Accept OpenOrchestrator POST /v1/runs payload and dispatch work",
    )
    run_p.add_argument(
        "--payload",
        help="JSON run payload (stdin if omitted)",
        default="",
    )


def _load_run_payload(args: argparse.Namespace) -> Dict[str, Any]:
    raw = args.payload.strip() if args.payload else sys.stdin.read().strip()
    if not raw:
        raise ValueError("handle-run requires JSON payload via --payload or stdin")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("payload must be a JSON object")
    return data


def _dispatch_run(payload: Dict[str, Any]) -> str:
    profile = str(payload.get("agent_profile") or "").strip()
    ctx = payload.get("task_context") or {}
    if not isinstance(ctx, dict):
        raise ValueError("task_context must be an object")

    ticket_id = str(ctx.get("ticket_id") or "").strip()
    if not ticket_id:
        raise ValueError("task_context.ticket_id is required")

    apply_task_context_env(ctx)

    if profile == "developer":
        mode = "implement"
    elif profile == "qa":
        mode = "test"
    else:
        return f"Skipped: unsupported agent_profile {profile!r}"

    return handle_invoke_opencode({"ticket_id": ticket_id, "mode": mode})


def openos_command(args: argparse.Namespace) -> int:
    action = getattr(args, "openos_action", None)
    if action == "init-profiles":
        names = init_profiles()
        print("OpenOS profiles ready: " + ", ".join(names))
        return 0
    if action == "ensure-profiles":
        raw = str(getattr(args, "profiles", "") or "").strip()
        targets = [p.strip() for p in raw.split(",") if p.strip()] or None
        results = ensure_profiles(targets)
        print(json.dumps(results, indent=2))
        return 0
    if action == "list-profiles":
        ids = list_profile_ids()
        if getattr(args, "json", False):
            print(json.dumps(ids))
        else:
            print("\n".join(ids))
        return 0
    if action == "handle-run":
        try:
            payload = _load_run_payload(args)
            print(_dispatch_run(payload))
            return 0
        except Exception as exc:
            print(f"handle-run failed: {exc}", file=sys.stderr)
            return 1

    print("Usage: openagents openos {init-profiles|ensure-profiles|list-profiles|handle-run}")
    return 2


def openos_init_profiles_command(_args) -> str:
    """Legacy handler — prefer openos_command with init-profiles subcommand."""
    names = init_profiles()
    return "Created OpenOS profiles: " + ", ".join(names)
