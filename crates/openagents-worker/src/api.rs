use std::convert::Infallible;

use axum::{
    extract::{Path, State},
    http::StatusCode,
    response::{
        sse::{Event, KeepAlive, Sse},
        IntoResponse, Response,
    },
    routing::{get, post},
    Json, Router,
};
use contract_core::{JobCancellation, SignedWorkerEnvelope, WorkerMessageKind};
use serde_json::{json, Value};
use tokio_stream::{wrappers::BroadcastStream, StreamExt};
use uuid::Uuid;

use crate::{
    model::{CompatibilityRunRequest, RunRecord, RunStatus},
    AppState,
};

pub fn router(state: AppState) -> Router {
    Router::new()
        .route("/v1/health", get(health))
        .route("/v1/capabilities", get(capabilities))
        .route("/v1/runs", post(start_run))
        .route("/v1/runs/{id}", get(get_run))
        .route("/v1/runs/{id}/events", get(run_events))
        .route("/v1/runs/{id}/stop", post(stop_run))
        .route("/v1/runs/{id}/approval", post(approval))
        .route("/v1/jobs/cancel", post(cancel_job))
        .with_state(state)
}

async fn health(State(state): State<AppState>) -> Response {
    let healthy = state.healthy.load(std::sync::atomic::Ordering::Relaxed);
    let status = if healthy {
        StatusCode::OK
    } else {
        StatusCode::SERVICE_UNAVAILABLE
    };
    (
        status,
        Json(json!({"ok":healthy,"runtime":"rust","worker_id":state.config.worker_id})),
    )
        .into_response()
}

async fn capabilities(State(state): State<AppState>) -> Json<Value> {
    let healthy = state.healthy.load(std::sync::atomic::Ordering::Relaxed);
    Json(json!({
        "protocol":"openos.capabilities/v1","runtime":"rust","object":"openagents.worker.capabilities",
        "capabilities": if healthy { json!(["invoke_opencode","git_worktree","test_execution"]) } else { json!([]) },
        "job_types": if healthy { json!(["engineering.opencode"]) } else { json!([]) },
        "runs":{"start":"/v1/runs","status":"/v1/runs/{run_id}","events":"/v1/runs/{run_id}/events","stop":"/v1/runs/{run_id}/stop","approval":"/v1/runs/{run_id}/approval"}
    }))
}

async fn start_run(
    State(state): State<AppState>,
    Json(body): Json<CompatibilityRunRequest>,
) -> Response {
    if !state.healthy.load(std::sync::atomic::Ordering::Relaxed) {
        return error(StatusCode::SERVICE_UNAVAILABLE, "RUNTIME_UNAVAILABLE");
    }
    if body.job.organization_id != state.config.organization_id {
        return error(StatusCode::FORBIDDEN, "ORG_SCOPE_MISMATCH");
    }
    let run_id = Uuid::new_v4();
    state
        .runs
        .insert(RunRecord::new(
            run_id,
            body.job.job_id,
            body.job.ticket_id,
            body.job.correlation_id,
        ))
        .await;
    crate::spawn_job(state, body.job, run_id);
    (
        StatusCode::ACCEPTED,
        Json(json!({"run_id":run_id,"status":"queued","runtime":"rust"})),
    )
        .into_response()
}

async fn get_run(State(state): State<AppState>, Path(id): Path<Uuid>) -> Response {
    match state.runs.get(id).await {
        Some(record) => Json(serde_json::to_value(record).unwrap_or_default()).into_response(),
        None => error(StatusCode::NOT_FOUND, "RUN_NOT_FOUND"),
    }
}

async fn run_events(State(state): State<AppState>, Path(id): Path<Uuid>) -> Response {
    let Some(record) = state.runs.get(id).await else {
        return error(StatusCode::NOT_FOUND, "RUN_NOT_FOUND");
    };
    let historical = tokio_stream::iter(record.events.into_iter().map(Ok::<_, Infallible>));
    let live = BroadcastStream::new(state.runs.subscribe()).filter_map(move |item| match item {
        Ok(event) if event.run_id == id => Some(Ok(event)),
        _ => None,
    });
    let stream = historical.chain(live).map(|item| {
        item.map(|event| {
            let event_name = event.event.clone();
            Event::default()
                .id(event.sequence.to_string())
                .event(event_name)
                .json_data(event)
                .unwrap_or_default()
        })
    });
    Sse::new(stream)
        .keep_alive(KeepAlive::default())
        .into_response()
}

async fn stop_run(State(state): State<AppState>, Path(id): Path<Uuid>) -> Response {
    let Some(record) = state.runs.get(id).await else {
        return error(StatusCode::NOT_FOUND, "RUN_NOT_FOUND");
    };
    if matches!(
        record.status,
        RunStatus::Completed | RunStatus::Failed | RunStatus::Cancelled
    ) {
        return error(StatusCode::CONFLICT, "RUN_ALREADY_TERMINAL");
    }
    state
        .runs
        .update(
            id,
            RunStatus::Cancelled,
            "run.cancelled",
            json!({"requested":true}),
        )
        .await;
    state
        .runs
        .terminal(id, RunStatus::Cancelled, None, Some("cancelled".into()))
        .await;
    Json(json!({"run_id":id,"status":"cancelled"})).into_response()
}

async fn approval(State(state): State<AppState>, Path(id): Path<Uuid>) -> Response {
    if state.runs.get(id).await.is_none() {
        return error(StatusCode::NOT_FOUND, "RUN_NOT_FOUND");
    }
    error(StatusCode::CONFLICT, "NO_APPROVAL_PENDING")
}

async fn cancel_job(
    State(state): State<AppState>,
    Json(envelope): Json<SignedWorkerEnvelope<JobCancellation>>,
) -> Response {
    if let Err(value) = state
        .client
        .verify_control_plane(&envelope, WorkerMessageKind::Cancel)
    {
        return error(StatusCode::UNAUTHORIZED, &value.to_string());
    }
    let Some(record) = state.runs.find_by_job(envelope.payload.job_id).await else {
        return error(StatusCode::NOT_FOUND, "RUN_NOT_FOUND");
    };
    state
        .runs
        .update(
            record.run_id,
            RunStatus::Cancelled,
            "run.cancelled",
            json!({"reason":envelope.payload.reason}),
        )
        .await;
    state
        .runs
        .terminal(
            record.run_id,
            RunStatus::Cancelled,
            None,
            Some(envelope.payload.reason),
        )
        .await;
    Json(json!({"job_id":envelope.payload.job_id,"run_id":record.run_id,"status":"cancelled"}))
        .into_response()
}

fn error(status: StatusCode, message: &str) -> Response {
    (status, Json(json!({"error":message}))).into_response()
}
