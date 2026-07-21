use std::convert::Infallible;

use axum::{
    extract::{Path, State},
    http::{HeaderMap, StatusCode},
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
        "protocol":"openos.capabilities/v1","app":"OpenAgents","version":env!("CARGO_PKG_VERSION"),"runtime":"rust","object":"openagents.worker.capabilities",
        "tools": if healthy { json!([
            {
                "name":"openagents_invoke_opencode","description":"Run an approved engineering job in an isolated managed Git worktree and return real test and commit evidence.",
                "input_schema":{"type":"object","required":["job"],"properties":{"job":{"type":"object"}},"additionalProperties":false},
                "mutates":true,"requires_confirmation":true,"operation":{"method":"POST","path":"/v1/runs"}
            },
            {
                "name":"openagents_get_run","description":"Read the real status and artifacts for an OpenAgents worker run.",
                "input_schema":{"type":"object","required":["run_id"],"properties":{"run_id":{"type":"string","format":"uuid"}},"additionalProperties":false},
                "mutates":false,"requires_confirmation":false,"operation":{"method":"GET","path":"/v1/runs/{run_id}"}
            }
        ]) } else { json!([]) },
        "profiles": if healthy { json!({"catalog":["skill_author"],"available":["skill_author"]}) } else { json!({"catalog":["skill_author"],"available":[]}) },
        "capabilities": if healthy { json!(["invoke_opencode","git_worktree","test_execution","skill_author","web_search","web_extract"]) } else { json!([]) },
        "job_types": if healthy { json!(["engineering.opencode","agent.skill_author"]) } else { json!([]) },
        "runs":{"start":"/v1/runs","status":"/v1/runs/{run_id}","events":"/v1/runs/{run_id}/events","stop":"/v1/runs/{run_id}/stop","approval":"/v1/runs/{run_id}/approval"}
    }))
}

async fn start_run(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<CompatibilityRunRequest>,
) -> Response {
    if let Err(response) = require_internal_service(&state, &headers) {
        return response;
    }
    if !state.healthy.load(std::sync::atomic::Ordering::Relaxed) {
        return error(StatusCode::SERVICE_UNAVAILABLE, "RUNTIME_UNAVAILABLE");
    }
    if body.job.organization_id != state.config.organization_id {
        return error(StatusCode::FORBIDDEN, "ORG_SCOPE_MISMATCH");
    }
    if body.job.idempotency_key.trim().is_empty() {
        return error(StatusCode::BAD_REQUEST, "IDEMPOTENCY_KEY_REQUIRED");
    }
    let run_id = Uuid::new_v4();
    let (record, inserted) = state
        .runs
        .insert_idempotent(RunRecord::new(
            run_id,
            body.job.job_id,
            body.job.ticket_id,
            body.job.correlation_id,
            body.job.idempotency_key.clone(),
        ))
        .await;
    if !inserted {
        if record.job_id != body.job.job_id || record.correlation_id != body.job.correlation_id {
            return error(StatusCode::CONFLICT, "IDEMPOTENCY_KEY_REUSED");
        }
        return Json(json!({
            "run_id":record.run_id,
            "status":record.status,
            "runtime":"rust",
            "idempotent_replay":true
        }))
        .into_response();
    }
    crate::spawn_job(state, body.job, run_id);
    (
        StatusCode::ACCEPTED,
        Json(json!({"run_id":run_id,"status":"queued","runtime":"rust"})),
    )
        .into_response()
}

async fn get_run(
    State(state): State<AppState>,
    Path(id): Path<Uuid>,
    headers: HeaderMap,
) -> Response {
    if let Err(response) = require_internal_service(&state, &headers) {
        return response;
    }
    match state.runs.get(id).await {
        Some(record) => Json(serde_json::to_value(record).unwrap_or_default()).into_response(),
        None => error(StatusCode::NOT_FOUND, "RUN_NOT_FOUND"),
    }
}

async fn run_events(
    State(state): State<AppState>,
    Path(id): Path<Uuid>,
    headers: HeaderMap,
) -> Response {
    if let Err(response) = require_internal_service(&state, &headers) {
        return response;
    }
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

async fn stop_run(
    State(state): State<AppState>,
    Path(id): Path<Uuid>,
    headers: HeaderMap,
) -> Response {
    if let Err(response) = require_internal_service(&state, &headers) {
        return response;
    }
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

async fn approval(
    State(state): State<AppState>,
    Path(id): Path<Uuid>,
    headers: HeaderMap,
) -> Response {
    if let Err(response) = require_internal_service(&state, &headers) {
        return response;
    }
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

fn require_internal_service(state: &AppState, headers: &HeaderMap) -> Result<(), Response> {
    let supplied = headers
        .get("x-internal-service-key")
        .and_then(|value| value.to_str().ok())
        .unwrap_or_default()
        .as_bytes();
    let expected = state.config.internal_service_key.as_bytes();
    let mut difference = supplied.len() ^ expected.len();
    for index in 0..supplied.len().max(expected.len()) {
        difference |= usize::from(
            supplied.get(index).copied().unwrap_or_default()
                ^ expected.get(index).copied().unwrap_or_default(),
        );
    }
    if difference == 0 {
        Ok(())
    } else {
        Err(error(StatusCode::UNAUTHORIZED, "SERVICE_AUTH_REQUIRED"))
    }
}
