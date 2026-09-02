mod api;
mod config;
mod control_plane;
mod delivery;
mod model;
mod runtime;

use std::{
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc,
    },
    time::{Duration, Instant},
};

use anyhow::Context;
use contract_core::{IdentityRegistry, WorkerArtifact, WorkerJob, WorkerResult};
use reqwest::Client;
use tokio::{sync::Semaphore, time::interval};
use tower_http::{cors::CorsLayer, trace::TraceLayer};
use tracing_subscriber::EnvFilter;
use uuid::Uuid;

use crate::{
    config::Config,
    control_plane::{
        classify_execution_failure, classify_failure, ControlPlaneClient, ControlPlaneFailure,
    },
    model::{RunRecord, RunStatus, RunStore},
};

#[derive(Clone)]
pub struct AppState {
    config: Arc<Config>,
    client: ControlPlaneClient,
    runs: RunStore,
    healthy: Arc<AtomicBool>,
    capacity: Arc<Semaphore>,
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(
            EnvFilter::from_default_env().add_directive("openagents_worker=info".parse()?),
        )
        .init();
    let config = Arc::new(Config::from_env()?);
    let identities = IdentityRegistry::load_dir(&config.identity_registry)?;
    let http = Client::builder().timeout(config.request_timeout).build()?;
    let client = ControlPlaneClient::new(http, &config, identities);
    let runs = RunStore::connect(
        &config.database_url,
        config.organization_id,
        &config.worker_id,
    )
    .await
    .context("initialize OpenAgents PostgreSQL run store")?;
    let healthy = Arc::new(AtomicBool::new(
        worker_healthy(&config).await && runs.healthy().await,
    ));
    let state = AppState {
        config: config.clone(),
        client,
        runs,
        healthy,
        capacity: Arc::new(Semaphore::new(config.capacity as usize)),
    };
    spawn_poller(state.clone());
    let app = api::router(state)
        .layer(CorsLayer::permissive())
        .layer(TraceLayer::new_for_http());
    let listener = tokio::net::TcpListener::bind(config.bind)
        .await
        .context("bind OpenAgents Rust worker")?;
    tracing::info!(address=%config.bind, "Rust OpenAgents worker listening");
    axum::serve(listener, app)
        .with_graceful_shutdown(shutdown())
        .await?;
    Ok(())
}

fn spawn_poller(state: AppState) {
    tokio::spawn(async move {
        let mut tick = interval(Duration::from_secs(2));
        let mut registration_tick = 0u32;
        loop {
            tick.tick().await;
            let healthy = worker_healthy(&state.config).await && state.runs.healthy().await;
            state.healthy.store(healthy, Ordering::Relaxed);
            if registration_tick == 0 {
                if let Err(error) = state.client.register(healthy, state.config.capacity).await {
                    tracing::error!(%error, "worker registration failed");
                }
            }
            registration_tick = (registration_tick + 1) % 15;
            if !healthy {
                continue;
            }
            let available = state.capacity.available_permits();
            if available == 0 {
                continue;
            }
            match state.client.claim(available as u32).await {
                Ok(jobs) => {
                    for job in jobs {
                        let run_id = Uuid::new_v4();
                        state
                            .runs
                            .insert(RunRecord::with_attempt(
                                state.config.organization_id,
                                run_id,
                                job.job_id,
                                job.attempt,
                                job.ticket_id,
                                job.correlation_id,
                                job.idempotency_key.clone(),
                            ))
                            .await;
                        spawn_job(state.clone(), job, run_id);
                    }
                }
                Err(error) => tracing::warn!(%error, "job claim failed"),
            }
        }
    });
}

pub fn spawn_job(state: AppState, job: WorkerJob, run_id: Uuid) {
    tokio::spawn(async move {
        let permit = match state.capacity.clone().acquire_owned().await {
            Ok(value) => value,
            Err(_) => return,
        };
        let adapter = match job.job_type.as_str() {
            "agent.skill_author" => "skill_author",
            "engineering.delivery" => "trusted_delivery",
            "engineering.inspect" => "inspect_repository",
            _ => "invoke_opencode",
        };
        state.runs.update(run_id, RunStatus::Running, "run.started", serde_json::json!({"job_id":job.job_id,"ticket_id":job.ticket_id,"adapter":adapter})).await;
        let heartbeat_state = state.clone();
        let heartbeat_job = job.clone();
        let settlement_started = Arc::new(AtomicBool::new(false));
        let heartbeat_settlement = settlement_started.clone();
        let mut heartbeat = tokio::spawn(async move {
            maintain_lease(
                &heartbeat_state.client,
                &heartbeat_job,
                run_id,
                &heartbeat_settlement,
            )
            .await
        });
        let execution = tokio::select! {
            biased;
            result = execute_job(&state, &job, run_id, settlement_started) => Some(result),
            lease = &mut heartbeat => {
                let reason = match lease {
                    Ok(Err(error)) => error.to_string(),
                    Ok(Ok(())) => "LEASE_HEARTBEAT_STOPPED".into(),
                    Err(error) => format!("LEASE_HEARTBEAT_TASK_FAILED: {error}"),
                };
                terminal_failure(&state, run_id, reason).await;
                None
            }
        };
        if let Some(execution) = execution {
            match execution {
                Ok(mut result) => {
                    attach_run_timeline(&state.runs, run_id, &mut result).await;
                    match complete_job(&state.client, &job, &result).await {
                        Ok(_) => {
                            let value = serde_json::to_value(&result).unwrap_or_default();
                            state
                                .runs
                                .update(
                                    run_id,
                                    RunStatus::Completed,
                                    "run.completed",
                                    serde_json::json!({"evidence_validated_by":"OpenOrchestrator"}),
                                )
                                .await;
                            state
                                .runs
                                .terminal(run_id, RunStatus::Completed, Some(value), None)
                                .await;
                        }
                        Err(error) => {
                            terminal_failure(
                                &state,
                                run_id,
                                format!("completion callback failed: {error}"),
                            )
                            .await
                        }
                    }
                }
                Err(error) => {
                    let reason = error.to_string();
                    if !state.runs.is_cancelled(run_id).await {
                        let failure = classify_execution_failure(&error);
                        let _ = state
                            .client
                            .fail(&job, run_id, failure.code, &reason, failure.retryable)
                            .await;
                        terminal_failure(&state, run_id, reason).await;
                    }
                }
            }
        }
        heartbeat.abort();
        drop(permit);
    });
}

async fn attach_run_timeline(store: &RunStore, run_id: Uuid, result: &mut WorkerResult) {
    let Some(record) = store.get(run_id).await else {
        return;
    };
    result.artifacts.push(WorkerArtifact {
        kind: "run_timeline".into(),
        name: "Persisted OpenAgents execution timeline".into(),
        uri: format!("openagents://runs/{run_id}/events"),
        sha256: None,
        metadata: serde_json::json!({
            "schema":"openos.openagents-run-timeline/v1",
            "run_id":run_id,
            "correlation_id":record.correlation_id,
            "events":record.events,
        }),
    });
}

async fn execute_job(
    state: &AppState,
    job: &WorkerJob,
    run_id: Uuid,
    settlement_started: Arc<AtomicBool>,
) -> anyhow::Result<contract_core::WorkerResult> {
    if !state.config.role.permits(&job.job_type) {
        anyhow::bail!("WORKER_ROLE_JOB_TYPE_MISMATCH");
    }
    if job.job_type == "engineering.delivery" {
        delivery::execute(
            job,
            run_id,
            &state.config,
            &state.client,
            &state.runs,
            settlement_started,
        )
        .await
    } else {
        runtime::execute(job, run_id, &state.config, &state.runs).await
    }
}

async fn worker_healthy(config: &Config) -> bool {
    match config.role {
        config::WorkerRole::Coding => runtime::runtime_healthy(config).await,
        config::WorkerRole::Delivery => delivery::runtime_healthy(config).await,
    }
}

async fn complete_job(
    client: &ControlPlaneClient,
    job: &WorkerJob,
    result: &contract_core::WorkerResult,
) -> anyhow::Result<()> {
    if job.job_type == "engineering.delivery" {
        // The signed provider callback atomically settles this dependent job.
        Ok(())
    } else {
        complete_with_retry(client, job, result).await
    }
}

const HEARTBEAT_INTERVAL: Duration = Duration::from_secs(20);
const LEASE_SAFETY_WINDOW: Duration = Duration::from_secs(50);
const CONTROL_PLANE_RETRY_ATTEMPTS: usize = 4;

async fn maintain_lease(
    client: &ControlPlaneClient,
    job: &WorkerJob,
    run_id: Uuid,
    settlement_started: &AtomicBool,
) -> anyhow::Result<()> {
    let mut last_confirmed = Instant::now();
    loop {
        let mut attempt = 0usize;
        loop {
            let remaining = LEASE_SAFETY_WINDOW.saturating_sub(last_confirmed.elapsed());
            if remaining.is_zero() {
                if settlement_started.load(Ordering::Acquire) {
                    tokio::time::sleep(Duration::from_millis(100)).await;
                    continue;
                }
                anyhow::bail!("LEASE_HEARTBEAT_UNCONFIRMED: lease safety deadline expired");
            }
            let heartbeat = tokio::time::timeout(remaining, client.heartbeat(job, run_id)).await;
            match heartbeat {
                Err(_) => {
                    anyhow::bail!(
                        "LEASE_HEARTBEAT_UNCONFIRMED: heartbeat exceeded lease safety deadline"
                    )
                }
                Ok(Ok(())) => {
                    last_confirmed = Instant::now();
                    break;
                }
                Ok(Err(error)) => match classify_failure(&error) {
                    ControlPlaneFailure::LeaseLost => {
                        if settlement_started.load(Ordering::Acquire) {
                            tokio::time::sleep(Duration::from_millis(100)).await;
                            continue;
                        }
                        anyhow::bail!("WORKER_LEASE_LOST: {error}")
                    }
                    ControlPlaneFailure::Permanent => {
                        return Err(error.context("LEASE_HEARTBEAT_PERMANENT_FAILURE"));
                    }
                    ControlPlaneFailure::Transient => {
                        let delay = retry_delay(attempt);
                        if delay >= LEASE_SAFETY_WINDOW.saturating_sub(last_confirmed.elapsed()) {
                            anyhow::bail!("LEASE_HEARTBEAT_UNCONFIRMED: {error}");
                        }
                        tokio::time::sleep(delay).await;
                        attempt += 1;
                    }
                },
            }
        }
        tokio::time::sleep(HEARTBEAT_INTERVAL).await;
    }
}

async fn complete_with_retry(
    client: &ControlPlaneClient,
    job: &WorkerJob,
    result: &contract_core::WorkerResult,
) -> anyhow::Result<()> {
    for attempt in 0..CONTROL_PLANE_RETRY_ATTEMPTS {
        match client.complete(job, result.clone()).await {
            Ok(()) => return Ok(()),
            Err(error)
                if classify_failure(&error) == ControlPlaneFailure::Transient
                    && attempt + 1 < CONTROL_PLANE_RETRY_ATTEMPTS =>
            {
                tokio::time::sleep(retry_delay(attempt)).await;
            }
            Err(error) => return Err(error),
        }
    }
    unreachable!("bounded completion attempts always return")
}

fn retry_delay(attempt: usize) -> Duration {
    Duration::from_millis(250 * (1u64 << attempt.min(5)))
}

async fn terminal_failure(state: &AppState, run_id: Uuid, reason: String) {
    state
        .runs
        .update(
            run_id,
            RunStatus::Failed,
            "run.failed",
            serde_json::json!({"error":reason}),
        )
        .await;
    state
        .runs
        .terminal(run_id, RunStatus::Failed, None, Some(reason))
        .await;
}

async fn shutdown() {
    let _ = tokio::signal::ctrl_c().await;
}
