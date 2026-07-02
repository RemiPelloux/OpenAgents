"""Postgres durable outbox for OpenRec — CC-W4-008."""

from __future__ import annotations

import json
import time
from typing import Any, Dict

from plugins.openos_engineering.rec_outbox_common import (
    MAX_ATTEMPTS,
    outbox_database_url,
    post_rec_event,
)

PRODUCER = "openagents"


def enqueue_pg_outbox(body: Dict[str, Any]) -> None:
    import psycopg

    url = outbox_database_url()
    if not url:
        raise RuntimeError("OPENAGENTS_OUTBOX_DATABASE_URL not configured")

    row = json.dumps({"event": body})
    with psycopg.connect(url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO outbox_jobs (producer, job_type, payload, status)
                VALUES (%s, 'rec_event', %s::jsonb, 'pending')
                """,
                (PRODUCER, row),
            )
        conn.commit()


def drain_pg_outbox(max_items: int = 20) -> int:
    import os

    import psycopg

    base_url = os.environ.get("OPENREC_URL", "").rstrip("/")
    url = outbox_database_url()
    if not base_url or not url:
        return 0

    sent = 0
    with psycopg.connect(url) as conn:
        conn.autocommit = False
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, payload, attempts, max_attempts
                FROM outbox_jobs
                WHERE producer = %s
                  AND status = 'pending'
                  AND attempts < max_attempts
                ORDER BY created_at ASC
                LIMIT %s
                FOR UPDATE SKIP LOCKED
                """,
                (PRODUCER, max_items),
            )
            jobs = cur.fetchall()
            for job_id, payload, attempts, max_attempts in jobs:
                cur.execute(
                    """
                    UPDATE outbox_jobs
                    SET status = 'processing', attempts = attempts + 1
                    WHERE id = %s
                    """,
                    (job_id,),
                )
            conn.commit()

        for job_id, payload, attempts, max_attempts in jobs:
            event = payload.get("event", payload)
            if post_rec_event(base_url, event):
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE outbox_jobs
                        SET status = 'completed', processed_at = now()
                        WHERE id = %s
                        """,
                        (job_id,),
                    )
                conn.commit()
                sent += 1
                continue

            next_attempts = attempts + 1
            status = "failed" if next_attempts >= max_attempts else "pending"
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE outbox_jobs
                    SET status = %s, last_error = %s
                    WHERE id = %s
                    """,
                    (status, "openrec_post_failed", job_id),
                )
            conn.commit()
            time.sleep(0.25)
    return sent
