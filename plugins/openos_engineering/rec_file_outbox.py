"""Durable NDJSON outbox for OpenRec events — CC-W4-008."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict

from plugins.openos_engineering.rec_outbox_common import MAX_ATTEMPTS, post_rec_event


def _outbox_dir() -> Path:
    base = os.environ.get("OPENAGENTS_REC_OUTBOX_DIR") or os.path.join(
        os.environ.get("OPENAGENTS_HOME", os.path.expanduser("~/.openagents")),
        "outbox",
        "rec",
    )
    path = Path(base)
    path.mkdir(parents=True, exist_ok=True)
    (path / "pending").mkdir(exist_ok=True)
    (path / "sent").mkdir(exist_ok=True)
    (path / "failed").mkdir(exist_ok=True)
    return path


def enqueue_file_outbox(body: Dict[str, Any]) -> None:
    root = _outbox_dir()
    event_id = str(body.get("id", "unknown"))
    pending = root / "pending" / f"{event_id}.json"
    pending.write_text(json.dumps({"attempts": 0, "event": body}), encoding="utf-8")


def drain_file_outbox(max_items: int = 20) -> int:
    base_url = os.environ.get("OPENREC_URL", "").rstrip("/")
    if not base_url:
        return 0

    root = _outbox_dir()
    pending_dir = root / "pending"
    sent = 0
    for path in sorted(pending_dir.glob("*.json"))[:max_items]:
        row = json.loads(path.read_text(encoding="utf-8"))
        attempts = int(row.get("attempts", 0))
        event = row["event"]
        if post_rec_event(base_url, event):
            path.rename(root / "sent" / path.name)
            sent += 1
            continue
        attempts += 1
        if attempts >= MAX_ATTEMPTS:
            row["attempts"] = attempts
            (root / "failed" / path.name).write_text(json.dumps(row), encoding="utf-8")
            path.unlink(missing_ok=True)
        else:
            row["attempts"] = attempts
            path.write_text(json.dumps(row), encoding="utf-8")
        time.sleep(0.25)
    return sent
