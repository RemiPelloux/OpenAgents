"""Durable NDJSON outbox for OpenRec events — CC-W4-008."""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 5


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


def enqueue_rec_event(body: Dict[str, Any]) -> None:
    root = _outbox_dir()
    event_id = str(body.get("id", "unknown"))
    pending = root / "pending" / f"{event_id}.json"
    pending.write_text(json.dumps({"attempts": 0, "event": body}), encoding="utf-8")


def _post_event(base_url: str, body: Dict[str, Any]) -> bool:
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        f"{base_url}/v1/events",
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status in (200, 201, 202)
    except urllib.error.HTTPError as exc:
        return exc.code in (200, 201, 202)


def drain_rec_outbox(max_items: int = 20) -> int:
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
        if _post_event(base_url, event):
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
