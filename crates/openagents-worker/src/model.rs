use std::{collections::HashMap, sync::Arc};

use serde::{Deserialize, Serialize};
use serde_json::Value;
use tokio::sync::{broadcast, RwLock};
use uuid::Uuid;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum RunStatus {
    Queued,
    Running,
    Completed,
    Failed,
    Cancelled,
}

#[derive(Debug, Clone, Serialize)]
pub struct RunEvent {
    pub sequence: u64,
    pub event: String,
    pub run_id: Uuid,
    pub correlation_id: Uuid,
    pub timestamp: chrono::DateTime<chrono::Utc>,
    pub data: Value,
}

#[derive(Debug, Clone, Serialize)]
pub struct RunRecord {
    pub run_id: Uuid,
    pub job_id: Uuid,
    pub ticket_id: Uuid,
    pub correlation_id: Uuid,
    pub status: RunStatus,
    pub events: Vec<RunEvent>,
    pub result: Option<Value>,
    pub error: Option<String>,
}

impl RunRecord {
    pub fn new(run_id: Uuid, job_id: Uuid, ticket_id: Uuid, correlation_id: Uuid) -> Self {
        Self {
            run_id,
            job_id,
            ticket_id,
            correlation_id,
            status: RunStatus::Queued,
            events: Vec::new(),
            result: None,
            error: None,
        }
    }
}

#[derive(Clone)]
pub struct RunStore {
    records: Arc<RwLock<HashMap<Uuid, RunRecord>>>,
    events: broadcast::Sender<RunEvent>,
}

impl RunStore {
    pub fn new() -> Self {
        let (events, _) = broadcast::channel(1024);
        Self {
            records: Arc::new(RwLock::new(HashMap::new())),
            events,
        }
    }

    pub async fn insert(&self, record: RunRecord) {
        self.records.write().await.insert(record.run_id, record);
    }

    pub async fn get(&self, id: Uuid) -> Option<RunRecord> {
        self.records.read().await.get(&id).cloned()
    }

    pub async fn find_by_job(&self, job_id: Uuid) -> Option<RunRecord> {
        self.records
            .read()
            .await
            .values()
            .find(|record| record.job_id == job_id)
            .cloned()
    }

    pub async fn is_cancelled(&self, id: Uuid) -> bool {
        self.records
            .read()
            .await
            .get(&id)
            .is_some_and(|record| record.status == RunStatus::Cancelled)
    }

    pub async fn update(&self, id: Uuid, status: RunStatus, event: &str, data: Value) {
        let mut records = self.records.write().await;
        let Some(record) = records.get_mut(&id) else {
            return;
        };
        record.status = status;
        let item = RunEvent {
            sequence: record.events.len() as u64 + 1,
            event: event.into(),
            run_id: id,
            correlation_id: record.correlation_id,
            timestamp: chrono::Utc::now(),
            data,
        };
        record.events.push(item.clone());
        let _ = self.events.send(item);
    }

    pub async fn terminal(
        &self,
        id: Uuid,
        status: RunStatus,
        result: Option<Value>,
        error: Option<String>,
    ) {
        if let Some(record) = self.records.write().await.get_mut(&id) {
            record.status = status;
            record.result = result;
            record.error = error;
        }
    }

    pub fn subscribe(&self) -> broadcast::Receiver<RunEvent> {
        self.events.subscribe()
    }
}

#[derive(Debug, Deserialize)]
pub struct CompatibilityRunRequest {
    pub job: contract_core::WorkerJob,
}
