"""OpenOS engineering plugin — W4 OpenTicket + OpenCode integration."""

from __future__ import annotations

from plugins.openos_engineering.cli import openos_init_profiles_command
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
        handler_fn=openos_init_profiles_command,
        description="Scaffold product_owner, developer, and qa profiles for W4.",
    )
