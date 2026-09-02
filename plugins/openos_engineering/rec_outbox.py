"""Durable outbox facade for OpenRec events — CC-W4-008."""

from __future__ import annotations

from typing import Any, Dict

from plugins.openos_engineering.rec_file_outbox import (
    drain_file_outbox,
    enqueue_file_outbox,
)
from plugins.openos_engineering.rec_outbox_common import (
    file_outbox_allowed,
    pg_outbox_enabled,
    require_mesh_outbox_configured,
)
from plugins.openos_engineering.rec_pg_outbox import drain_pg_outbox, enqueue_pg_outbox


def enqueue_rec_event(body: Dict[str, Any]) -> None:
    if pg_outbox_enabled():
        enqueue_pg_outbox(body)
        return
    if file_outbox_allowed():
        enqueue_file_outbox(body)
        return
    require_mesh_outbox_configured()


def drain_rec_outbox(max_items: int = 20) -> int:
    if pg_outbox_enabled():
        return drain_pg_outbox(max_items)
    if file_outbox_allowed():
        return drain_file_outbox(max_items)
    return 0
