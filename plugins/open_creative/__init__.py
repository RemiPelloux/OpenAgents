"""OpenCreative plugin — Brain-led creative workflows."""

from __future__ import annotations

from plugins.open_creative.tools import (
    GENERATE_IMAGES_SCHEMA,
    POST_DELIVERABLES_SCHEMA,
    handle_generate_openai_images,
    handle_post_brain_deliverables,
)


def register(ctx) -> None:
    ctx.register_tool(
        name="generate_openai_images",
        schema=GENERATE_IMAGES_SCHEMA,
        handler=handle_generate_openai_images,
    )
    ctx.register_tool(
        name="post_brain_deliverables",
        schema=POST_DELIVERABLES_SCHEMA,
        handler=handle_post_brain_deliverables,
    )
