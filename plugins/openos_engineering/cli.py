"""CLI: openagents openos {init-profiles|handle-run}"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict

from plugins.openos_engineering.profiles import init_profiles
from plugins.openos_engineering.tools import handle_invoke_opencode


def register_cli(subparser: argparse.ArgumentParser) -> None:
    subs = subparser.add_subparsers(dest="openos_action")

    subs.add_parser(
        "init-profiles",
        help="Scaffold product_owner, developer, and qa profiles for W4",
    )

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

    correlation_id = ctx.get("correlation_id")
    if correlation_id:
        import os

        os.environ["OPENTICKET_CORRELATION_ID"] = str(correlation_id)

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
        print("Created OpenOS profiles: " + ", ".join(names))
        return 0
    if action == "handle-run":
        try:
            payload = _load_run_payload(args)
            print(_dispatch_run(payload))
            return 0
        except Exception as exc:
            print(f"handle-run failed: {exc}", file=sys.stderr)
            return 1

    print("Usage: openagents openos {init-profiles|handle-run}")
    return 2


def openos_init_profiles_command(_args) -> str:
    """Legacy handler — prefer openos_command with init-profiles subcommand."""
    names = init_profiles()
    return "Created OpenOS profiles: " + ", ".join(names)
