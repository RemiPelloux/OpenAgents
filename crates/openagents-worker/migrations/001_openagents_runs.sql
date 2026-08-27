CREATE TABLE IF NOT EXISTS openagents_runs (
  organization_id UUID NOT NULL,
  run_id UUID PRIMARY KEY,
  worker_id TEXT NOT NULL,
  job_id UUID NOT NULL,
  attempt INTEGER NOT NULL DEFAULT 1 CHECK (attempt > 0),
  ticket_id UUID NOT NULL,
  correlation_id UUID NOT NULL,
  idempotency_key TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN (
    'queued', 'running', 'completed', 'failed', 'cancelled', 'interrupted'
  )),
  recovered BOOLEAN NOT NULL DEFAULT false,
  cancellation_requested_at TIMESTAMPTZ,
  result JSONB,
  error TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  started_at TIMESTAMPTZ,
  completed_at TIMESTAMPTZ,
  cancelled_at TIMESTAMPTZ,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (organization_id, idempotency_key)
);

ALTER TABLE openagents_runs ADD COLUMN IF NOT EXISTS worker_id TEXT;
UPDATE openagents_runs SET worker_id = 'legacy-unknown' WHERE worker_id IS NULL;
ALTER TABLE openagents_runs ALTER COLUMN worker_id SET NOT NULL;

CREATE INDEX IF NOT EXISTS idx_openagents_runs_job
  ON openagents_runs (organization_id, job_id);
CREATE INDEX IF NOT EXISTS idx_openagents_runs_worker_active
  ON openagents_runs (organization_id, worker_id, status);

CREATE TABLE IF NOT EXISTS openagents_run_events (
  organization_id UUID NOT NULL,
  run_id UUID NOT NULL REFERENCES openagents_runs(run_id) ON DELETE CASCADE,
  sequence BIGINT NOT NULL CHECK (sequence > 0),
  event_id TEXT NOT NULL,
  event_type TEXT NOT NULL,
  correlation_id UUID NOT NULL,
  occurred_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  data JSONB NOT NULL DEFAULT '{}'::jsonb,
  PRIMARY KEY (run_id, sequence),
  UNIQUE (organization_id, event_id)
);

CREATE INDEX IF NOT EXISTS idx_openagents_events_replay
  ON openagents_run_events (organization_id, run_id, sequence);
