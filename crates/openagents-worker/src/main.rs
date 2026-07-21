mod api;
mod config;
mod control_plane;
mod model;
mod runtime;

use std::{
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc,
    },
    time::Duration,
};

use anyhow::Context;
use contract_core::{IdentityRegistry, WorkerJob};
use reqwest::Client;
use tokio::{sync::Semaphore, time::interval};
use tower_http::{cors::CorsLayer, trace::TraceLayer};
use tracing_subscriber::EnvFilter;
use uuid::Uuid;

use crate::{
    config::Config,
    control_plane::ControlPlaneClient,
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
    let healthy = Arc::new(AtomicBool::new(runtime::runtime_healthy(&config).await));
    let state = AppState {
        config: config.clone(),
        client,
        runs: RunStore::new(),
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
            let healthy = runtime::runtime_healthy(&state.config).await;
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
                            .insert(RunRecord::new(
                                run_id,
                                job.job_id,
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
        let direct_skill_author = job.job_type == "agent.skill_author";
        state.runs.update(run_id, RunStatus::Running, "run.started", serde_json::json!({"job_id":job.job_id,"ticket_id":job.ticket_id,"adapter":if direct_skill_author { "skill_author" } else { "invoke_opencode" }})).await;
        let heartbeat_state = state.clone();
        let heartbeat_job = job.clone();
        let heartbeat = tokio::spawn(async move {
            let mut tick = interval(Duration::from_secs(20));
            loop {
                tick.tick().await;
                if heartbeat_state
                    .client
                    .heartbeat(&heartbeat_job, run_id)
                    .await
                    .is_err()
                {
                    break;
                }
            }
        });
        match runtime::execute(&job, run_id, &state.config, &state.runs).await {
            Ok(result) if direct_skill_author => {
                let value = serde_json::to_value(&result).unwrap_or_default();
                state
                    .runs
                    .update(
                        run_id,
                        RunStatus::Completed,
                        "run.completed",
                        serde_json::json!({"evidence_validated_by":"OpenAgents.skill_author"}),
                    )
                    .await;
                state
                    .runs
                    .terminal(run_id, RunStatus::Completed, Some(value), None)
                    .await;
            }
            Ok(result) => match state.client.complete(&job, result.clone()).await {
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
            },
            Err(error) => {
                let reason = error.to_string();
                if !state.runs.is_cancelled(run_id).await {
                    let _ = state
                        .client
                        .fail(&job, run_id, &reason, job.attempt < job.max_attempts)
                        .await;
                    terminal_failure(&state, run_id, reason).await;
                }
            }
        }
        heartbeat.abort();
        drop(permit);
    });
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
