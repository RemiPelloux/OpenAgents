"""OpenOS engineering plugin — W4 OpenTicket + OpenCode integration."""

from __future__ import annotations

from plugins.openos_engineering.cli import openos_command, register_cli
from plugins.openos_engineering.tools import (
    INVOKE_OPENCODE_SCHEMA,
    check_openos_engineering_available,
    handle_invoke_opencode,
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

    ctx.register_cli_command(
        name="openos",
        help="OpenOS W4 engineering workflow utilities",
        setup_fn=register_cli,
        handler_fn=openos_command,
        description="Scaffold W4 profiles and handle OpenOrchestrator run dispatch.",
    )
