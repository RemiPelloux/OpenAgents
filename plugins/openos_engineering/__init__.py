"""OpenOS engineering plugin — W4 OpenTicket + OpenCode + Codex integration."""

from __future__ import annotations

from plugins.openos_engineering.cli import openos_command, register_cli
from plugins.openos_engineering.subtask_delegate import on_subagent_start
from plugins.openos_engineering.tools import (
    CREATE_SUBTASK_SCHEMA,
    INVOKE_CODEX_SCHEMA,
    INVOKE_OPENCODE_SCHEMA,
    SUBMIT_TICKET_RESULT_SCHEMA,
    check_codex_available,
    check_openos_engineering_available,
    handle_create_subtask,
    handle_invoke_codex,
    handle_invoke_opencode,
    handle_submit_ticket_result,
)


def register(ctx) -> None:
    ctx.register_tool(
        name="invoke_opencode",
        toolset="openos_engineering",
        schema=INVOKE_OPENCODE_SCHEMA,
        handler=handle_invoke_opencode,
        check_fn=check_openos_engineering_available,
        emoji="⚡",
    )
    ctx.register_tool(
        name="invoke_codex",
        toolset="openos_engineering",
        schema=INVOKE_CODEX_SCHEMA,
        handler=handle_invoke_codex,
        check_fn=check_codex_available,
        emoji="🤖",
    )
    ctx.register_tool(
        name="submit_ticket_result",
        toolset="openos_engineering",
        schema=SUBMIT_TICKET_RESULT_SCHEMA,
        handler=handle_submit_ticket_result,
        emoji="📦",
    )
    ctx.register_tool(
        name="create_subtask",
        toolset="openos_engineering",
        schema=CREATE_SUBTASK_SCHEMA,
        handler=handle_create_subtask,
        emoji="🧩",
    )

    ctx.register_hook("subagent_start", on_subagent_start)

    ctx.register_cli_command(
        name="openos",
        help="OpenOS W4 engineering workflow utilities",
        setup_fn=register_cli,
        handler_fn=openos_command,
        description="Scaffold W4 profiles and handle OpenOrchestrator run dispatch.",
    )
