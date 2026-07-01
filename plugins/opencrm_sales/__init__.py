"""OpenCRM sales-followup plugin — W1 agent surface (get_account, search, staging)."""

from __future__ import annotations

from plugins.opencrm_sales.tools import (
    CHECK_DUPLICATE_SCHEMA,
    GET_ACCOUNT_SCHEMA,
    PROPOSE_UPDATE_SCHEMA,
    SEARCH_ACCOUNTS_SCHEMA,
    check_opencrm_available,
    handle_check_account_duplicate,
    handle_get_account,
    handle_propose_crm_update,
    handle_search_accounts,
)


def register(ctx) -> None:
    tools = [
        (SEARCH_ACCOUNTS_SCHEMA, handle_search_accounts, "🔍"),
        (CHECK_DUPLICATE_SCHEMA, handle_check_account_duplicate, "🔁"),
        (GET_ACCOUNT_SCHEMA, handle_get_account, "🏢"),
        (PROPOSE_UPDATE_SCHEMA, handle_propose_crm_update, "📝"),
    ]
    for schema, handler, emoji in tools:
        ctx.register_tool(
            name=schema["name"],
            toolset="opencrm_sales",
            schema=schema,
            handler=handler,
            check_fn=check_opencrm_available,
            emoji=emoji,
        )
