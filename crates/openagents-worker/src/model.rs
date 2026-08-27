use std::{str::FromStr, sync::Arc};

#[cfg(test)]
use std::collections::HashMap;

use serde::{Deserialize, Serialize};
use serde_json::Value;
use sha2::{Digest, Sha256};
#[cfg(test)]
use tokio::sync::RwLock;
use tokio::sync::{broadcast, Mutex};
use tokio_postgres::{Client, NoTls, Row};
use uuid::Uuid;

const MAX_ERROR_BYTES: usize = 16 * 1024;
const MAX_RESULT_BYTES: usize = 1024 * 1024;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum RunStatus {
    Queued,
    Running,
    Completed,
    Failed,
    Cancelled,
    Interrupted,
}

impl RunStatus {
    fn as_str(self) -> &'static str {
        match self {
            Self::Queued => "queued",
            Self::Running => "running",
            Self::Completed => "completed",
            Self::Failed => "failed",
            Self::Cancelled => "cancelled",
            Self::Interrupted => "interrupted",
        }
    }

    pub fn is_terminal(self) -> bool {
        matches!(
            self,
            Self::Completed | Self::Failed | Self::Cancelled | Self::Interrupted
        )
    }
}

impl FromStr for RunStatus {
    type Err = anyhow::Error;

    fn from_str(value: &str) -> Result<Self, Self::Err> {
        match value {
            "queued" => Ok(Self::Queued),
            "running" => Ok(Self::Running),
            "completed" => Ok(Self::Completed),
            "failed" => Ok(Self::Failed),
            "cancelled" => Ok(Self::Cancelled),
            "interrupted" => Ok(Self::Interrupted),
            other => anyhow::bail!("unknown OpenAgents run status: {other}"),
        }
    }
}

#[derive(Debug, Clone, Serialize)]
pub struct RunEvent {
    pub sequence: u64,
    pub event_id: String,
    pub event: String,
    pub run_id: Uuid,
    pub correlation_id: Uuid,
    pub timestamp: chrono::DateTime<chrono::Utc>,
    pub data: Value,
}

#[derive(Debug, Clone, Serialize)]
pub struct RunRecord {
    pub organization_id: Uuid,
    pub run_id: Uuid,
    pub job_id: Uuid,
    pub attempt: u32,
    pub ticket_id: Uuid,
    pub correlation_id: Uuid,
    pub idempotency_key: String,
    pub status: RunStatus,
    pub recovered: bool,
    pub created_at: chrono::DateTime<chrono::Utc>,
    pub started_at: Option<chrono::DateTime<chrono::Utc>>,
    pub completed_at: Option<chrono::DateTime<chrono::Utc>>,
    pub cancelled_at: Option<chrono::DateTime<chrono::Utc>>,
    pub events: Vec<RunEvent>,
    pub result: Option<Value>,
    pub error: Option<String>,
}

impl RunRecord {
    #[cfg(test)]
    pub fn new(
        run_id: Uuid,
        job_id: Uuid,
        ticket_id: Uuid,
        correlation_id: Uuid,
        idempotency_key: String,
    ) -> Self {
        Self::with_attempt(
            Uuid::nil(),
            run_id,
            job_id,
            1,
            ticket_id,
            correlation_id,
            idempotency_key,
        )
    }

    pub fn with_attempt(
        organization_id: Uuid,
        run_id: Uuid,
        job_id: Uuid,
        attempt: u32,
        ticket_id: Uuid,
        correlation_id: Uuid,
        idempotency_key: String,
    ) -> Self {
        Self {
            organization_id,
            run_id,
            job_id,
            attempt: attempt.max(1),
            ticket_id,
            correlation_id,
            idempotency_key,
            status: RunStatus::Queued,
            recovered: false,
            created_at: chrono::Utc::now(),
            started_at: None,
            completed_at: None,
            cancelled_at: None,
            events: Vec::new(),
            result: None,
            error: None,
        }
    }
}

#[derive(Clone)]
pub struct RunStore {
    organization_id: Uuid,
    worker_id: String,
    backend: StoreBackend,
    events: broadcast::Sender<RunEvent>,
}

#[derive(Clone)]
enum StoreBackend {
    Postgres(Arc<Mutex<Client>>),
    #[cfg(test)]
    Memory(Arc<RwLock<HashMap<Uuid, RunRecord>>>),
}

impl RunStore {
    pub async fn connect(
        database_url: &str,
        organization_id: Uuid,
        worker_id: &str,
    ) -> anyhow::Result<Self> {
        let (mut client, connection) = tokio_postgres::connect(database_url, NoTls).await?;
        tokio::spawn(async move {
            if let Err(error) = connection.await {
                tracing::error!(%error, "OpenAgents run database connection failed");
            }
        });
        client
            .batch_execute(include_str!("../migrations/001_openagents_runs.sql"))
            .await?;
        let transaction = client.transaction().await?;
        let interrupted = transaction.query(
            "SELECT run_id,correlation_id FROM openagents_runs \
             WHERE organization_id=$1 AND worker_id=$2 AND status IN ('queued','running') FOR UPDATE",
            &[&organization_id, &worker_id],
        ).await?;
        for row in interrupted {
            let run_id: Uuid = row.get("run_id");
            let correlation_id: Uuid = row.get("correlation_id");
            let sequence: i64 = transaction
                .query_one(
                    "SELECT COALESCE(MAX(sequence),0)+1 FROM openagents_run_events WHERE run_id=$1",
                    &[&run_id],
                )
                .await?
                .get(0);
            let event_type = "run.interrupted";
            let event_id = stable_event_id(
                organization_id,
                correlation_id,
                run_id,
                sequence as u64,
                event_type,
            );
            transaction
                .execute(
                    "INSERT INTO openagents_run_events \
                 (organization_id,run_id,sequence,event_id,event_type,correlation_id,data) \
                 VALUES ($1,$2,$3,$4,$5,$6,$7)",
                    &[
                        &organization_id,
                        &run_id,
                        &sequence,
                        &event_id,
                        &event_type,
                        &correlation_id,
                        &serde_json::json!({"reason":"worker_restarted"}),
                    ],
                )
                .await?;
            transaction
                .execute(
                    "UPDATE openagents_runs SET status='interrupted', recovered=true, \
                 completed_at=COALESCE(completed_at, now()), updated_at=now(), \
                 error=COALESCE(error, 'worker_restarted') WHERE run_id=$1",
                    &[&run_id],
                )
                .await?;
        }
        transaction.commit().await?;
        let (events, _) = broadcast::channel(1024);
        Ok(Self {
            organization_id,
            worker_id: worker_id.to_string(),
            backend: StoreBackend::Postgres(Arc::new(Mutex::new(client))),
            events,
        })
    }

    #[cfg(test)]
    pub fn new() -> Self {
        let (events, _) = broadcast::channel(1024);
        Self {
            organization_id: Uuid::nil(),
            worker_id: "test-worker".into(),
            backend: StoreBackend::Memory(Arc::new(RwLock::new(HashMap::new()))),
            events,
        }
    }

    pub async fn healthy(&self) -> bool {
        match &self.backend {
            StoreBackend::Postgres(client) => {
                client.lock().await.simple_query("SELECT 1").await.is_ok()
            }
            #[cfg(test)]
            StoreBackend::Memory(_) => true,
        }
    }

    pub async fn insert(&self, mut record: RunRecord) {
        record.organization_id = self.organization_id;
        match &self.backend {
            StoreBackend::Postgres(client) => {
                client.lock().await.execute(
                    "INSERT INTO openagents_runs \
                     (organization_id,worker_id,run_id,job_id,attempt,ticket_id,correlation_id,idempotency_key,status) \
                     VALUES ($1,$2,$3,$4,$5,$6,$7,$8,'queued') ON CONFLICT (run_id) DO NOTHING",
                    &[&self.organization_id, &self.worker_id, &record.run_id, &record.job_id, &(record.attempt as i32),
                      &record.ticket_id, &record.correlation_id, &record.idempotency_key],
                ).await.unwrap_or_else(fatal_store_error);
            }
            #[cfg(test)]
            StoreBackend::Memory(records) => {
                records.write().await.insert(record.run_id, record);
            }
        }
    }

    pub async fn insert_idempotent(&self, mut record: RunRecord) -> (RunRecord, bool) {
        record.organization_id = self.organization_id;
        match &self.backend {
            StoreBackend::Postgres(client) => {
                let mut client = client.lock().await;
                let transaction = client.transaction().await.unwrap_or_else(fatal_store_error);
                let inserted = transaction.execute(
                    "INSERT INTO openagents_runs \
                     (organization_id,worker_id,run_id,job_id,attempt,ticket_id,correlation_id,idempotency_key,status) \
                     VALUES ($1,$2,$3,$4,$5,$6,$7,$8,'queued') \
                     ON CONFLICT (organization_id,idempotency_key) DO NOTHING",
                    &[&self.organization_id, &self.worker_id, &record.run_id, &record.job_id, &(record.attempt as i32),
                      &record.ticket_id, &record.correlation_id, &record.idempotency_key],
                ).await.unwrap_or_else(fatal_store_error) == 1;
                let row = transaction.query_one(
                    "SELECT * FROM openagents_runs WHERE organization_id=$1 AND idempotency_key=$2",
                    &[&self.organization_id, &record.idempotency_key],
                ).await.unwrap_or_else(fatal_store_error);
                transaction.commit().await.unwrap_or_else(fatal_store_error);
                (record_from_row(&row, Vec::new()), inserted)
            }
            #[cfg(test)]
            StoreBackend::Memory(records) => {
                let mut records = records.write().await;
                if let Some(existing) = records
                    .values()
                    .find(|item| item.idempotency_key == record.idempotency_key)
                    .cloned()
                {
                    return (existing, false);
                }
                records.insert(record.run_id, record.clone());
                (record, true)
            }
        }
    }

    pub async fn get(&self, id: Uuid) -> Option<RunRecord> {
        match &self.backend {
            StoreBackend::Postgres(client) => {
                let client = client.lock().await;
                let row = client
                    .query_opt(
                        "SELECT * FROM openagents_runs WHERE organization_id=$1 AND run_id=$2",
                        &[&self.organization_id, &id],
                    )
                    .await
                    .unwrap_or_else(fatal_store_error)?;
                let events = load_events(&client, self.organization_id, id, 0).await;
                Some(record_from_row(&row, events))
            }
            #[cfg(test)]
            StoreBackend::Memory(records) => records.read().await.get(&id).cloned(),
        }
    }

    pub async fn events_after(&self, id: Uuid, sequence: u64) -> Option<Vec<RunEvent>> {
        match &self.backend {
            StoreBackend::Postgres(client) => {
                let client = client.lock().await;
                let exists = client
                    .query_opt(
                        "SELECT 1 FROM openagents_runs WHERE organization_id=$1 AND run_id=$2",
                        &[&self.organization_id, &id],
                    )
                    .await
                    .unwrap_or_else(fatal_store_error)
                    .is_some();
                if exists {
                    Some(load_events(&client, self.organization_id, id, sequence).await)
                } else {
                    None
                }
            }
            #[cfg(test)]
            StoreBackend::Memory(records) => records.read().await.get(&id).map(|record| {
                record
                    .events
                    .iter()
                    .filter(|event| event.sequence > sequence)
                    .cloned()
                    .collect()
            }),
        }
    }

    pub async fn find_by_job(&self, job_id: Uuid) -> Option<RunRecord> {
        match &self.backend {
            StoreBackend::Postgres(client) => {
                let client = client.lock().await;
                client.query_opt(
                    "SELECT * FROM openagents_runs WHERE organization_id=$1 AND job_id=$2 ORDER BY created_at DESC LIMIT 1",
                    &[&self.organization_id, &job_id],
                ).await.unwrap_or_else(fatal_store_error).map(|row| record_from_row(&row, Vec::new()))
            }
            #[cfg(test)]
            StoreBackend::Memory(records) => records
                .read()
                .await
                .values()
                .find(|record| record.job_id == job_id)
                .cloned(),
        }
    }

    pub async fn is_cancelled(&self, id: Uuid) -> bool {
        self.get(id)
            .await
            .is_some_and(|record| record.status == RunStatus::Cancelled)
    }

    pub async fn update(&self, id: Uuid, status: RunStatus, event: &str, data: Value) {
        match &self.backend {
            StoreBackend::Postgres(client) => {
                let mut client = client.lock().await;
                let transaction = client.transaction().await.unwrap_or_else(fatal_store_error);
                let row = transaction
                    .query_opt(
                        "SELECT correlation_id,status FROM openagents_runs \
                     WHERE organization_id=$1 AND run_id=$2 FOR UPDATE",
                        &[&self.organization_id, &id],
                    )
                    .await
                    .unwrap_or_else(fatal_store_error);
                let Some(row) = row else {
                    return;
                };
                let current =
                    RunStatus::from_str(row.get::<_, &str>("status")).unwrap_or_else(|error| {
                        panic!("OpenAgents run store invariant failed: {error}")
                    });
                if current.is_terminal() {
                    return;
                }
                let sequence: i64 = transaction.query_one(
                    "SELECT COALESCE(MAX(sequence),0)+1 FROM openagents_run_events WHERE run_id=$1",
                    &[&id],
                ).await.unwrap_or_else(fatal_store_error).get(0);
                let correlation_id: Uuid = row.get("correlation_id");
                let event_id = stable_event_id(
                    self.organization_id,
                    correlation_id,
                    id,
                    sequence as u64,
                    event,
                );
                let now = chrono::Utc::now();
                transaction.execute(
                    "INSERT INTO openagents_run_events \
                     (organization_id,run_id,sequence,event_id,event_type,correlation_id,occurred_at,data) \
                     VALUES ($1,$2,$3,$4,$5,$6,$7,$8)",
                    &[&self.organization_id,&id,&sequence,&event_id,&event,&correlation_id,&now,&data],
                ).await.unwrap_or_else(fatal_store_error);
                transaction.execute(
                    "UPDATE openagents_runs SET status=$3, updated_at=$4, \
                     started_at=CASE WHEN $3='running' THEN COALESCE(started_at,$4) ELSE started_at END, \
                     completed_at=CASE WHEN $3 IN ('completed','failed','interrupted') THEN COALESCE(completed_at,$4) ELSE completed_at END, \
                     cancellation_requested_at=CASE WHEN $3='cancelled' THEN COALESCE(cancellation_requested_at,$4) ELSE cancellation_requested_at END, \
                     cancelled_at=CASE WHEN $3='cancelled' THEN COALESCE(cancelled_at,$4) ELSE cancelled_at END \
                     WHERE organization_id=$1 AND run_id=$2",
                    &[&self.organization_id,&id,&status.as_str(),&now],
                ).await.unwrap_or_else(fatal_store_error);
                transaction.commit().await.unwrap_or_else(fatal_store_error);
                let item = RunEvent {
                    sequence: sequence as u64,
                    event_id,
                    event: event.into(),
                    run_id: id,
                    correlation_id,
                    timestamp: now,
                    data,
                };
                let _ = self.events.send(item);
            }
            #[cfg(test)]
            StoreBackend::Memory(records) => {
                let mut records = records.write().await;
                let Some(record) = records.get_mut(&id) else {
                    return;
                };
                if record.status.is_terminal() {
                    return;
                }
                record.status = status;
                let sequence = record.events.len() as u64 + 1;
                let item = RunEvent {
                    sequence,
                    event_id: stable_event_id(
                        record.organization_id,
                        record.correlation_id,
                        id,
                        sequence,
                        event,
                    ),
                    event: event.into(),
                    run_id: id,
                    correlation_id: record.correlation_id,
                    timestamp: chrono::Utc::now(),
                    data,
                };
                record.events.push(item.clone());
                let _ = self.events.send(item);
            }
        }
    }

    pub async fn terminal(
        &self,
        id: Uuid,
        status: RunStatus,
        result: Option<Value>,
        error: Option<String>,
    ) {
        assert!(status.is_terminal(), "terminal requires a terminal status");
        let result = result.map(bound_result);
        let error = error.map(|value| bound_string(value, MAX_ERROR_BYTES));
        match &self.backend {
            StoreBackend::Postgres(client) => {
                client.lock().await.execute(
                    "UPDATE openagents_runs SET result=$4,error=$5,updated_at=now(), \
                     completed_at=CASE WHEN $3 IN ('completed','failed','interrupted') THEN COALESCE(completed_at,now()) ELSE completed_at END, \
                     cancelled_at=CASE WHEN $3='cancelled' THEN COALESCE(cancelled_at,now()) ELSE cancelled_at END \
                     WHERE organization_id=$1 AND run_id=$2 AND status=$3",
                    &[&self.organization_id,&id,&status.as_str(),&result,&error],
                ).await.unwrap_or_else(fatal_store_error);
            }
            #[cfg(test)]
            StoreBackend::Memory(records) => {
                if let Some(record) = records.write().await.get_mut(&id) {
                    if record.status == status || !record.status.is_terminal() {
                        record.status = status;
                        record.result = result;
                        record.error = error;
                    }
                }
            }
        }
    }

    pub fn subscribe(&self) -> broadcast::Receiver<RunEvent> {
        self.events.subscribe()
    }
}

async fn load_events(
    client: &Client,
    organization_id: Uuid,
    run_id: Uuid,
    after: u64,
) -> Vec<RunEvent> {
    client.query(
        "SELECT run_id,sequence,event_id,event_type,correlation_id,occurred_at,data \
         FROM openagents_run_events WHERE organization_id=$1 AND run_id=$2 AND sequence>$3 ORDER BY sequence",
        &[&organization_id,&run_id,&(after as i64)],
    ).await.unwrap_or_else(fatal_store_error).iter().map(event_from_row).collect()
}

fn record_from_row(row: &Row, events: Vec<RunEvent>) -> RunRecord {
    RunRecord {
        organization_id: row.get("organization_id"),
        run_id: row.get("run_id"),
        job_id: row.get("job_id"),
        attempt: row.get::<_, i32>("attempt") as u32,
        ticket_id: row.get("ticket_id"),
        correlation_id: row.get("correlation_id"),
        idempotency_key: row.get("idempotency_key"),
        status: RunStatus::from_str(row.get::<_, &str>("status")).expect("validated run status"),
        recovered: row.get("recovered"),
        created_at: row.get("created_at"),
        started_at: row.get("started_at"),
        completed_at: row.get("completed_at"),
        cancelled_at: row.get("cancelled_at"),
        events,
        result: row.get("result"),
        error: row.get("error"),
    }
}

fn event_from_row(row: &Row) -> RunEvent {
    RunEvent {
        sequence: row.get::<_, i64>("sequence") as u64,
        event_id: row.get("event_id"),
        event: row.get("event_type"),
        run_id: row.get("run_id"),
        correlation_id: row.get("correlation_id"),
        timestamp: row.get("occurred_at"),
        data: row.get("data"),
    }
}

fn stable_event_id(
    organization_id: Uuid,
    correlation_id: Uuid,
    run_id: Uuid,
    sequence: u64,
    event: &str,
) -> String {
    let mut digest = Sha256::new();
    digest.update(format!(
        "{organization_id}:{correlation_id}:{run_id}:{sequence}:{event}"
    ));
    format!("openagents:{:x}", digest.finalize())
}

fn bound_result(value: Value) -> Value {
    if serde_json::to_vec(&value).map_or(0, |bytes| bytes.len()) <= MAX_RESULT_BYTES {
        value
    } else {
        serde_json::json!({"truncated":true,"reason":"result_exceeded_1_mib"})
    }
}

fn bound_string(mut value: String, max: usize) -> String {
    if value.len() <= max {
        return value;
    }
    let mut boundary = max;
    while !value.is_char_boundary(boundary) {
        boundary -= 1;
    }
    value.truncate(boundary);
    value
}

fn fatal_store_error<T>(error: tokio_postgres::Error) -> T {
    panic!("OpenAgents run store unavailable: {error}")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn returns_the_existing_run_for_an_idempotency_key() {
        let store = RunStore::new();
        let correlation_id = Uuid::new_v4();
        let first = RunRecord::new(
            Uuid::new_v4(),
            Uuid::new_v4(),
            Uuid::new_v4(),
            correlation_id,
            "skill-author:org:correlation".into(),
        );
        let duplicate = RunRecord::new(
            Uuid::new_v4(),
            first.job_id,
            first.ticket_id,
            correlation_id,
            first.idempotency_key.clone(),
        );
        let (_, inserted) = store.insert_idempotent(first.clone()).await;
        let (existing, duplicate_inserted) = store.insert_idempotent(duplicate).await;
        assert!(inserted);
        assert!(!duplicate_inserted);
        assert_eq!(existing.run_id, first.run_id);
    }

    #[tokio::test]
    async fn terminal_state_cannot_be_overwritten() {
        let store = RunStore::new();
        let run = RunRecord::new(
            Uuid::new_v4(),
            Uuid::new_v4(),
            Uuid::new_v4(),
            Uuid::new_v4(),
            "terminal-cas".into(),
        );
        store.insert(run.clone()).await;
        store
            .update(
                run.run_id,
                RunStatus::Completed,
                "run.completed",
                Value::Null,
            )
            .await;
        store
            .update(run.run_id, RunStatus::Failed, "run.failed", Value::Null)
            .await;
        assert_eq!(
            store.get(run.run_id).await.unwrap().status,
            RunStatus::Completed
        );
    }

    #[tokio::test]
    async fn replays_only_events_after_last_event_id_sequence() {
        let store = RunStore::new();
        let run = RunRecord::new(
            Uuid::new_v4(),
            Uuid::new_v4(),
            Uuid::new_v4(),
            Uuid::new_v4(),
            "event-replay".into(),
        );
        store.insert(run.clone()).await;
        store
            .update(run.run_id, RunStatus::Running, "run.started", Value::Null)
            .await;
        store
            .update(run.run_id, RunStatus::Running, "run.progress", Value::Null)
            .await;
        let replay = store.events_after(run.run_id, 1).await.unwrap();
        assert_eq!(replay.len(), 1);
        assert_eq!(replay[0].sequence, 2);
        assert_eq!(replay[0].event, "run.progress");
        assert!(replay[0].event_id.starts_with("openagents:"));
    }
}

#[derive(Debug, Deserialize)]
pub struct CompatibilityRunRequest {
    pub job: contract_core::WorkerJob,
}
