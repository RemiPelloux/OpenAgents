"""Postgres durable outbox for OpenRec — CC-W4-008."""

from __future__ import annotations

import json
import hashlib
from typing import Any, Dict

from plugins.openos_engineering.rec_outbox_common import (
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
            organization_id = str(body.get("tenant", {}).get("org_id", ""))
            correlation_id = str(body.get("correlation_id", ""))
            run_id = str(body.get("agent_run_id") or correlation_id or body.get("target", {}).get("id", "unknown"))
            event_type = str(body.get("type", "unknown"))
            sequence_key = f"{organization_id}:{correlation_id}:{run_id}"
            cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (sequence_key,))
            cur.execute(
                """
                SELECT COALESCE(MAX(
                    CASE WHEN payload->'event'->>'sequence' ~ '^[0-9]+$'
                         THEN (payload->'event'->>'sequence')::bigint END
                ), 0) + 1
                FROM outbox_jobs
                WHERE producer = %s
                  AND COALESCE(payload->'event'->>'agent_run_id', payload->'event'->>'correlation_id', payload->'event'->'target'->>'id') = %s
                """,
                (PRODUCER, run_id),
            )
            sequence = int(cur.fetchone()[0])
            stable_subject = f"{organization_id}:{correlation_id}:{run_id}:{sequence}:{event_type}"
            event_id = "openagents:" + hashlib.sha256(stable_subject.encode()).hexdigest()
            body = {**body, "id": event_id, "event_id": event_id, "sequence": sequence}
            row = json.dumps({"event": body})
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
                UPDATE outbox_jobs
                SET status = CASE
                      WHEN attempts >= max_attempts THEN 'dead_letter'
                      ELSE 'pending'
                    END,
                    processed_at = CASE
                      WHEN attempts >= max_attempts THEN now()
                      ELSE NULL
                    END,
                    available_at = CASE
                      WHEN attempts >= max_attempts THEN available_at
                      ELSE now()
                    END,
                    last_error = 'stale_claim_recovered'
                WHERE producer = %s
                  AND status = 'processing'
                  AND (processed_at IS NULL OR processed_at < now() - interval '15 minutes')
                """,
                (PRODUCER,),
            )
            cur.execute(
                """
                UPDATE outbox_jobs AS jobs
                SET status = 'processing', attempts = attempts + 1, processed_at = now()
                FROM (
                    SELECT id
                    FROM outbox_jobs
                    WHERE producer = %s
                      AND status = 'pending'
                      AND available_at <= now()
                      AND attempts < max_attempts
                    ORDER BY created_at ASC
                    LIMIT %s
                    FOR UPDATE SKIP LOCKED
                ) AS claim
                WHERE jobs.id = claim.id
                RETURNING jobs.id, jobs.payload, jobs.attempts, jobs.max_attempts
                """,
                (PRODUCER, max_items),
            )
            jobs = cur.fetchall()
            conn.commit()

        completed_ids = []
        retry_ids = []
        failed_ids = []
        for job_id, payload, attempts, max_attempts in jobs:
            event = payload.get("event", payload)
            if post_rec_event(base_url, event):
                completed_ids.append(job_id)
                sent += 1
                continue

            target = failed_ids if attempts >= max_attempts else retry_ids
            target.append(job_id)

        with conn.cursor() as cur:
            if completed_ids:
                cur.execute(
                    """
                    UPDATE outbox_jobs SET status = 'completed', processed_at = now(),
                        last_error = NULL WHERE id = ANY(%s)
                    """,
                    (completed_ids,),
                )
            if retry_ids:
                cur.execute(
                    """
                    UPDATE outbox_jobs SET status = 'pending',
                        processed_at = NULL, last_error = 'openrec_post_failed',
                        available_at = now() + make_interval(secs => LEAST(300, power(2, attempts)::int))
                    WHERE id = ANY(%s)
                    """,
                    (retry_ids,),
                )
            if failed_ids:
                cur.execute(
                    """
                    UPDATE outbox_jobs SET status = 'dead_letter', processed_at = now(),
                        last_error = 'openrec_post_failed' WHERE id = ANY(%s)
                    """,
                    (failed_ids,),
                )
        conn.commit()
    return sent
