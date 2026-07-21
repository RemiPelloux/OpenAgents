use chrono::{Duration, Utc};
use contract_core::{
    sign_worker_envelope, verify_worker_envelope, IdentityRegistry, JobClaimRequest, JobCompletion,
    JobFailure, JobHeartbeat, SignedWorkerEnvelope, WorkerHealth, WorkerJob, WorkerMessageKind,
    WorkerRegistration,
};
use reqwest::Client;
use serde::{de::DeserializeOwned, Serialize};
use serde_json::Value;
use uuid::Uuid;

use crate::config::Config;

#[derive(Clone)]
pub struct ControlPlaneClient {
    http: Client,
    base_url: String,
    organization_id: Uuid,
    worker_id: String,
    signing_key: String,
    identities: IdentityRegistry,
}

impl ControlPlaneClient {
    pub fn new(http: Client, config: &Config, identities: IdentityRegistry) -> Self {
        Self {
            http,
            base_url: config.orchestrator_url.clone(),
            organization_id: config.organization_id,
            worker_id: config.worker_id.clone(),
            signing_key: config.signing_key.clone(),
            identities,
        }
    }

    pub fn verify_control_plane<T: Serialize + DeserializeOwned>(
        &self,
        envelope: &SignedWorkerEnvelope<T>,
        expected: WorkerMessageKind,
    ) -> anyhow::Result<()> {
        verify_worker_envelope(envelope, &self.identities, Utc::now())?;
        if envelope.kind != expected
            || envelope.organization_id != self.organization_id
            || envelope.producer != "OpenOrchestrator"
            || envelope.consumer != "OpenAgents"
        {
            anyhow::bail!("control plane envelope scope mismatch");
        }
        Ok(())
    }

    pub async fn register(&self, healthy: bool, capacity: u32) -> anyhow::Result<()> {
        let payload = WorkerRegistration {
            worker_id: self.worker_id.clone(),
            runtime_version: env!("CARGO_PKG_VERSION").into(),
            capabilities: if healthy {
                vec![
                    "invoke_opencode".into(),
                    "git_worktree".into(),
                    "test_execution".into(),
                    "skill_author".into(),
                    "web_search".into(),
                    "web_extract".into(),
                ]
            } else {
                vec![]
            },
            job_types: if healthy {
                vec!["engineering.opencode".into(), "agent.skill_author".into()]
            } else {
                vec![]
            },
            capacity,
            health: if healthy {
                WorkerHealth::Healthy
            } else {
                WorkerHealth::Unavailable
            },
            metadata: serde_json::json!({"runtime":"rust","adapter":"opencode","cost_per_success":1.0}),
        };
        let registration_key = format!("register:{}:{}", healthy, Utc::now().timestamp() / 30);
        let _: Value = self
            .send(
                "/v1/workers/register",
                WorkerMessageKind::Register,
                Uuid::new_v4(),
                &registration_key,
                payload,
            )
            .await?;
        Ok(())
    }

    pub async fn claim(&self, capacity: u32) -> anyhow::Result<Vec<WorkerJob>> {
        let payload = JobClaimRequest {
            worker_id: self.worker_id.clone(),
            capabilities: vec![
                "invoke_opencode".into(),
                "git_worktree".into(),
                "test_execution".into(),
                "skill_author".into(),
                "web_search".into(),
                "web_extract".into(),
            ],
            job_types: vec!["engineering.opencode".into(), "agent.skill_author".into()],
            limit: capacity,
            lease_seconds: 60,
        };
        let value: Value = self
            .send(
                "/v1/jobs/claim",
                WorkerMessageKind::Claim,
                Uuid::new_v4(),
                &format!("claim:{}", Uuid::new_v4()),
                payload,
            )
            .await?;
        Ok(serde_json::from_value(
            value
                .get("jobs")
                .cloned()
                .unwrap_or_else(|| serde_json::json!([])),
        )?)
    }

    pub async fn heartbeat(&self, job: &WorkerJob, run_id: Uuid) -> anyhow::Result<()> {
        let payload = JobHeartbeat {
            worker_id: self.worker_id.clone(),
            job_id: job.job_id,
            lease_token: job.lease_token,
            run_id,
            extend_seconds: 60,
        };
        let _: Value = self
            .send(
                "/v1/jobs/heartbeat",
                WorkerMessageKind::Heartbeat,
                job.correlation_id,
                &format!("heartbeat:{}:{}", job.job_id, Utc::now().timestamp() / 20),
                payload,
            )
            .await?;
        Ok(())
    }

    pub async fn complete(
        &self,
        job: &WorkerJob,
        result: contract_core::WorkerResult,
    ) -> anyhow::Result<()> {
        let payload = JobCompletion {
            worker_id: self.worker_id.clone(),
            job_id: job.job_id,
            lease_token: job.lease_token,
            result,
        };
        let _: Value = self
            .send(
                "/v1/jobs/complete",
                WorkerMessageKind::Complete,
                job.correlation_id,
                &format!("complete:{}", job.job_id),
                payload,
            )
            .await?;
        Ok(())
    }

    pub async fn fail(
        &self,
        job: &WorkerJob,
        run_id: Uuid,
        message: &str,
        retryable: bool,
    ) -> anyhow::Result<()> {
        let payload = JobFailure {
            worker_id: self.worker_id.clone(),
            job_id: job.job_id,
            lease_token: job.lease_token,
            run_id,
            error_code: "WORKER_EXECUTION_FAILED".into(),
            message: sanitize(message),
            retryable,
            stderr: None,
        };
        let _: Value = self
            .send(
                "/v1/jobs/fail",
                WorkerMessageKind::Fail,
                job.correlation_id,
                &format!("fail:{}:{}", job.job_id, job.attempt),
                payload,
            )
            .await?;
        Ok(())
    }

    async fn send<T, R>(
        &self,
        path: &str,
        kind: WorkerMessageKind,
        correlation_id: Uuid,
        idempotency_key: &str,
        payload: T,
    ) -> anyhow::Result<R>
    where
        T: Serialize,
        R: DeserializeOwned,
    {
        let mut envelope = SignedWorkerEnvelope::new(
            kind,
            self.organization_id,
            correlation_id,
            idempotency_key,
            "OpenAgents",
            "OpenOrchestrator",
            Utc::now() + Duration::minutes(2),
            payload,
        );
        sign_worker_envelope(&mut envelope, "OpenAgents", &self.signing_key)?;
        let response = self
            .http
            .post(format!("{}{}", self.base_url, path))
            .json(&envelope)
            .send()
            .await?;
        let status = response.status();
        let body: Value = response.json().await.unwrap_or_default();
        if !status.is_success() {
            anyhow::bail!("control plane returned {status}: {body}");
        }
        let signed: SignedWorkerEnvelope<Value> = serde_json::from_value(body)?;
        verify_worker_envelope(&signed, &self.identities, Utc::now())?;
        if signed.organization_id != self.organization_id
            || signed.producer != "OpenOrchestrator"
            || signed.consumer != "OpenAgents"
        {
            anyhow::bail!("control plane response scope mismatch");
        }
        Ok(serde_json::from_value(signed.payload)?)
    }
}

fn sanitize(message: &str) -> String {
    message
        .lines()
        .take(20)
        .map(|line| {
            if line.contains("sk-") || line.to_ascii_lowercase().contains("authorization") {
                "[redacted]"
            } else {
                line
            }
        })
        .collect::<Vec<_>>()
        .join("\n")
}
