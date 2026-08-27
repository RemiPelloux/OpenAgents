use chrono::{Duration, Utc};
use contract_core::{
    sign_worker_envelope, verify_worker_envelope, IdentityRegistry, JobClaimRequest, JobCompletion,
    JobFailure, JobHeartbeat, SignedWorkerEnvelope, WorkerHealth, WorkerJob, WorkerMessageKind,
    WorkerRegistration,
};
use reqwest::Client;
use reqwest::StatusCode;
use serde::{de::DeserializeOwned, Serialize};
use serde_json::Value;
use uuid::Uuid;

use crate::config::{Config, WorkerRole};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ControlPlaneFailure {
    Transient,
    LeaseLost,
    Permanent,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ExecutionFailure {
    pub code: &'static str,
    pub retryable: bool,
}

pub fn classify_execution_failure(error: &anyhow::Error) -> ExecutionFailure {
    let upper = error.to_string().to_ascii_uppercase();
    let transient = upper.contains("TIMEOUT")
        || upper.contains("TIMED OUT")
        || upper.contains("NETWORK")
        || upper.contains("CONNECTION")
        || upper.contains("ECONNRESET")
        || upper.contains("429")
        || (500..=599).any(|status| upper.contains(&status.to_string()))
        || upper.contains("LEASE_LOST")
        || upper.contains("LEASE_HEARTBEAT_UNCONFIRMED");
    if transient {
        return ExecutionFailure {
            code: "TRANSIENT_EXECUTION_FAILURE",
            retryable: true,
        };
    }
    let code = if upper.contains("OPENCODE_PRODUCED_NO_CHANGES") {
        "OPENCODE_PRODUCED_NO_CHANGES"
    } else if upper.contains("OPENCODE_PROTOCOL_CONTRADICTORY_TERMINAL") {
        "OPENCODE_PROTOCOL_CONTRADICTORY_TERMINAL"
    } else if upper.contains("OPENCODE_PROTOCOL_TERMINAL_MISSING")
        || upper.contains("OPENCODE_PROTOCOL_TERMINAL_MALFORMED")
        || upper.contains("OPENCODE_TERMINAL_REPORT_MISSING")
    {
        "OPENCODE_PROTOCOL_MALFORMED"
    } else if upper.contains("POLICY") || upper.contains("FORBIDDEN") {
        "POLICY_VIOLATION"
    } else if upper.contains("CONNECTOR") || upper.contains("REMOTE_REPOSITORY") {
        "CONNECTOR_INVALID"
    } else if upper.contains("EVIDENCE") || upper.contains("PROOF") {
        "EVIDENCE_MISSING_OR_INVALID"
    } else {
        "PERMANENT_EXECUTION_FAILURE"
    };
    ExecutionFailure {
        code,
        retryable: false,
    }
}

#[derive(Debug)]
struct ControlPlaneHttpError {
    status: StatusCode,
    body: String,
}

impl std::fmt::Display for ControlPlaneHttpError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(
            formatter,
            "control plane returned {}: {}",
            self.status, self.body
        )
    }
}

impl std::error::Error for ControlPlaneHttpError {}

pub fn classify_failure(error: &anyhow::Error) -> ControlPlaneFailure {
    if let Some(error) = error.downcast_ref::<ControlPlaneHttpError>() {
        return classify_status(error.status);
    }
    if let Some(error) = error.downcast_ref::<reqwest::Error>() {
        if error.is_timeout() || error.is_connect() {
            return ControlPlaneFailure::Transient;
        }
    }
    ControlPlaneFailure::Permanent
}

fn classify_status(status: StatusCode) -> ControlPlaneFailure {
    if status == StatusCode::CONFLICT || status == StatusCode::GONE {
        ControlPlaneFailure::LeaseLost
    } else if status == StatusCode::REQUEST_TIMEOUT
        || status == StatusCode::TOO_MANY_REQUESTS
        || status.is_server_error()
    {
        ControlPlaneFailure::Transient
    } else {
        ControlPlaneFailure::Permanent
    }
}

#[derive(Clone)]
pub struct ControlPlaneClient {
    http: Client,
    base_url: String,
    organization_id: Uuid,
    worker_id: String,
    role: WorkerRole,
    signing_key: String,
    internal_service_key: String,
    identities: IdentityRegistry,
}

impl ControlPlaneClient {
    pub fn new(http: Client, config: &Config, identities: IdentityRegistry) -> Self {
        Self {
            http,
            base_url: config.orchestrator_url.clone(),
            organization_id: config.organization_id,
            worker_id: config.worker_id.clone(),
            role: config.role,
            signing_key: config.signing_key.clone(),
            internal_service_key: config.internal_service_key.clone(),
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
        let capabilities = if healthy {
            worker_capabilities(self.role)
        } else {
            Vec::new()
        };
        let toolchains = capabilities
            .iter()
            .filter(|value| value.starts_with("toolchain."))
            .cloned()
            .collect::<Vec<_>>();
        let payload = WorkerRegistration {
            worker_id: self.worker_id.clone(),
            runtime_version: env!("CARGO_PKG_VERSION").into(),
            capabilities,
            job_types: healthy_job_types(self.role, healthy),
            capacity,
            health: if healthy {
                WorkerHealth::Healthy
            } else {
                WorkerHealth::Unavailable
            },
            metadata: serde_json::json!({
                "runtime":"rust",
                "role":self.role.as_str(),
                "adapters":role_adapters(self.role),
                "cost_per_success":1.0,
                "toolchains":toolchains
            }),
        };
        let registration_key = format!(
            "register:{}:{}:{}:{}",
            self.worker_id,
            healthy,
            capacity,
            Utc::now().timestamp() / 30
        );
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
            capabilities: worker_capabilities(self.role),
            job_types: healthy_job_types(self.role, true),
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
        error_code: &str,
        message: &str,
        retryable: bool,
    ) -> anyhow::Result<()> {
        let payload = JobFailure {
            worker_id: self.worker_id.clone(),
            job_id: job.job_id,
            lease_token: job.lease_token,
            run_id,
            error_code: error_code.into(),
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

    pub fn worker_id(&self) -> &str {
        &self.worker_id
    }

    pub async fn provider_callback(
        &self,
        path: &str,
        correlation_id: Uuid,
        idempotency_key: &str,
        expires_at: chrono::DateTime<Utc>,
        payload: Value,
    ) -> anyhow::Result<()> {
        let mut envelope = SignedWorkerEnvelope::new(
            WorkerMessageKind::Complete,
            self.organization_id,
            correlation_id,
            idempotency_key,
            "OpenAgents",
            "OpenOrchestrator",
            expires_at,
            payload,
        );
        sign_worker_envelope(&mut envelope, "OpenAgents", &self.signing_key)?;
        let response = self
            .http
            .post(format!("{}{}", self.base_url, path))
            .header("x-internal-service-key", &self.internal_service_key)
            .header("x-organization-id", self.organization_id.to_string())
            .json(&envelope)
            .send()
            .await?;
        let status = response.status();
        if !status.is_success() {
            let body = response.text().await.unwrap_or_default();
            return Err(ControlPlaneHttpError {
                status,
                body: sanitize(&body),
            }
            .into());
        }
        Ok(())
    }

    pub async fn confirm_provider_callback(
        &self,
        delivery_id: Uuid,
        correlation_id: Uuid,
        idempotency_key: &str,
        expected_payload: &Value,
    ) -> anyhow::Result<bool> {
        let response = self
            .http
            .get(format!("{}/v1/deliveries/{delivery_id}", self.base_url))
            .header("x-internal-service-key", &self.internal_service_key)
            .header("x-organization-id", self.organization_id.to_string())
            .send()
            .await?;
        if !response.status().is_success() {
            return Ok(false);
        }
        let response: Value = response.json().await?;
        let Some(delivery) = response.get("delivery") else {
            return Ok(false);
        };
        if delivery.get("status").and_then(Value::as_str) != Some("pr_open") {
            return Ok(false);
        }
        let Some(value) = delivery.get("provider_result").cloned() else {
            return Ok(false);
        };
        let recorded: SignedWorkerEnvelope<Value> = serde_json::from_value(value)?;
        verify_worker_envelope(&recorded, &self.identities, Utc::now())?;
        Ok(recorded.kind == WorkerMessageKind::Complete
            && recorded.organization_id == self.organization_id
            && recorded.correlation_id == correlation_id
            && recorded.idempotency_key == idempotency_key
            && recorded.producer == "OpenAgents"
            && recorded.consumer == "OpenOrchestrator"
            && recorded.payload == *expected_payload)
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
            return Err(ControlPlaneHttpError {
                status,
                body: sanitize(&body.to_string()),
            }
            .into());
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

fn worker_capabilities(role: WorkerRole) -> Vec<String> {
    if role == WorkerRole::Delivery {
        return vec!["engineering.delivery".into(), "trusted_delivery".into()];
    }
    let mut capabilities = vec![
        "invoke_opencode".into(),
        "inspect_repository".into(),
        "git_worktree".into(),
        "test_execution".into(),
        "skill_author".into(),
        "web_search".into(),
        "web_extract".into(),
    ];
    for (capability, commands) in [
        ("toolchain.node", &["node", "pnpm"][..]),
        ("toolchain.python", &["python"][..]),
        ("toolchain.rust", &["cargo", "rustc"][..]),
        ("toolchain.go", &["go"][..]),
        ("toolchain.java", &["java", "javac"][..]),
        ("toolchain.flutter", &["flutter"][..]),
    ] {
        if commands.iter().all(|command| command_available(command)) {
            capabilities.push(capability.into());
        }
    }
    if ["chromium", "chromium-browser", "google-chrome"]
        .iter()
        .any(|command| command_available(command))
    {
        capabilities.push("toolchain.browser".into());
    }
    capabilities
}

fn healthy_job_types(role: WorkerRole, healthy: bool) -> Vec<String> {
    if !healthy {
        return Vec::new();
    }
    role.job_types()
        .iter()
        .map(|value| (*value).into())
        .collect()
}

fn role_adapters(role: WorkerRole) -> &'static [&'static str] {
    match role {
        WorkerRole::Coding => &["opencode", "repository_inspector", "skill_author"],
        WorkerRole::Delivery => &["trusted_delivery"],
    }
}

fn command_available(name: &str) -> bool {
    let Some(path) = std::env::var_os("PATH") else {
        return false;
    };
    std::env::split_paths(&path).any(|directory| {
        #[cfg(windows)]
        {
            ["", ".exe", ".cmd", ".bat"]
                .iter()
                .any(|suffix| directory.join(format!("{name}{suffix}")).is_file())
        }
        #[cfg(not(windows))]
        {
            directory.join(name).is_file()
        }
    })
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn reports_only_detected_toolchains() {
        let capabilities = worker_capabilities(WorkerRole::Coding);
        assert!(capabilities.iter().any(|value| value == "invoke_opencode"));
        assert!(!capabilities
            .iter()
            .any(|value| value == "engineering.delivery"));
        for capability in capabilities
            .iter()
            .filter(|value| value.starts_with("toolchain."))
        {
            let known = [
                "toolchain.node",
                "toolchain.python",
                "toolchain.rust",
                "toolchain.go",
                "toolchain.java",
                "toolchain.flutter",
                "toolchain.browser",
            ];
            assert!(known.contains(&capability.as_str()));
        }
        assert_eq!(
            worker_capabilities(WorkerRole::Delivery),
            vec!["engineering.delivery", "trusted_delivery"]
        );
        assert_eq!(
            healthy_job_types(WorkerRole::Delivery, true),
            vec!["engineering.delivery"]
        );
    }

    #[test]
    fn classifies_retryable_transport_and_lease_failures() {
        assert_eq!(
            classify_status(StatusCode::SERVICE_UNAVAILABLE),
            ControlPlaneFailure::Transient
        );
        assert_eq!(
            classify_status(StatusCode::TOO_MANY_REQUESTS),
            ControlPlaneFailure::Transient
        );
        assert_eq!(
            classify_status(StatusCode::CONFLICT),
            ControlPlaneFailure::LeaseLost
        );
        assert_eq!(
            classify_status(StatusCode::UNPROCESSABLE_ENTITY),
            ControlPlaneFailure::Permanent
        );
    }

    #[test]
    fn classifies_execution_failures_without_attempt_count_heuristics() {
        let no_changes =
            classify_execution_failure(&anyhow::anyhow!("OPENCODE_PRODUCED_NO_CHANGES"));
        assert_eq!(no_changes.code, "OPENCODE_PRODUCED_NO_CHANGES");
        assert!(!no_changes.retryable);

        let malformed = classify_execution_failure(&anyhow::anyhow!(
            "OPENCODE_PROTOCOL_CONTRADICTORY_TERMINAL"
        ));
        assert_eq!(malformed.code, "OPENCODE_PROTOCOL_CONTRADICTORY_TERMINAL");
        assert!(!malformed.retryable);

        let timeout = classify_execution_failure(&anyhow::anyhow!("OPENCODE_TIMEOUT"));
        assert_eq!(timeout.code, "TRANSIENT_EXECUTION_FAILURE");
        assert!(timeout.retryable);

        let throttled = classify_execution_failure(&anyhow::anyhow!("provider returned HTTP 429"));
        assert!(throttled.retryable);
    }
}
