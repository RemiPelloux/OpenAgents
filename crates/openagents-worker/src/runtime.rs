use std::{
    path::{Component, Path, PathBuf},
    process::Stdio,
};

use anyhow::Context;
use contract_core::{GitEvidence, TestEvidence, WorkerArtifact, WorkerJob, WorkerResult};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use tokio::{
    fs,
    io::{AsyncBufReadExt, BufReader},
    process::Command,
    time::timeout,
};
use uuid::Uuid;

use crate::{
    config::Config,
    model::{RunStatus, RunStore},
};

pub async fn runtime_healthy(config: &Config) -> bool {
    command_ok(Command::new("git").arg("--version")).await
        && command_ok(Command::new(&config.opencode_binary).arg("--version")).await
        && fs::create_dir_all(&config.managed_root).await.is_ok()
}

async fn command_ok(command: &mut Command) -> bool {
    command
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .await
        .is_ok_and(|status| status.success())
}

pub async fn execute(
    job: &WorkerJob,
    run_id: Uuid,
    config: &Config,
    store: &RunStore,
) -> anyhow::Result<WorkerResult> {
    validate_job(job)?;
    let inputs = job.inputs.get("inputs").unwrap_or(&job.inputs);
    let repository = canonical_repository(inputs, &config.allowed_repositories).await?;
    let base_ref = string(inputs, "base_ref")?;
    let tests = string_array(inputs, "test_commands")?;
    let prompt = string(inputs, "prompt").unwrap_or_else(|_| {
        job.inputs
            .get("task")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string()
    });
    if contains_secret(&serde_json::to_string(inputs)?) {
        anyhow::bail!("INPUT_SECRET_REJECTED");
    }
    let (workspace, base_sha) = create_worktree(
        &repository,
        &config.managed_root,
        job.ticket_id,
        run_id,
        &base_ref,
    )
    .await?;
    ensure_not_cancelled(store, run_id).await?;
    store
        .update(
            run_id,
            RunStatus::Running,
            "worktree.created",
            json!({"worktree":workspace,"repository":repository}),
        )
        .await;
    let engine = run_opencode(config, job, run_id, &workspace, &prompt, store).await?;
    if contains_secret(&engine.stdout) || contains_secret(&engine.stderr) {
        anyhow::bail!("ENGINE_SECRET_LEAK_REJECTED");
    }
    ensure_not_cancelled(store, run_id).await?;
    let test_evidence = run_tests(&workspace, &tests, run_id, store).await?;
    if engine.exit_status != 0 || test_evidence.iter().any(|test| test.exit_status != 0) {
        anyhow::bail!("engine or declared tests failed");
    }
    let git = commit_changes(&workspace, &repository, &base_sha, job.ticket_id, run_id).await?;
    let artifact = persist_events(&config.managed_root, run_id, &engine.stdout).await?;
    Ok(WorkerResult {
        run_id,
        artifacts: vec![artifact],
        stderr: nonempty(sanitize(&engine.stderr)),
        exit_status: engine.exit_status,
        tests: test_evidence,
        git: Some(git),
        engine_session_id: engine.session_id,
    })
}

fn validate_job(job: &WorkerJob) -> anyhow::Result<()> {
    if job.job_type != "engineering.opencode"
        || !job
            .required_capabilities
            .iter()
            .any(|value| value == "invoke_opencode")
    {
        anyhow::bail!("UNSUPPORTED_JOB_TYPE_OR_CAPABILITY");
    }
    if job
        .deadline
        .is_some_and(|deadline| deadline <= chrono::Utc::now())
    {
        anyhow::bail!("JOB_DEADLINE_EXPIRED");
    }
    Ok(())
}

async fn canonical_repository(inputs: &Value, allowed: &[PathBuf]) -> anyhow::Result<PathBuf> {
    let requested = PathBuf::from(string(inputs, "repository")?);
    if requested
        .components()
        .any(|component| matches!(component, Component::ParentDir))
    {
        anyhow::bail!("REPOSITORY_PATH_ESCAPE");
    }
    let canonical = fs::canonicalize(&requested)
        .await
        .context("canonicalize repository")?;
    let mut permitted = false;
    for root in allowed {
        if let Ok(root) = fs::canonicalize(root).await {
            permitted |= canonical.starts_with(root);
        }
    }
    if !permitted {
        anyhow::bail!("REPOSITORY_NOT_MANAGED");
    }
    if !fs::try_exists(canonical.join(".git")).await? {
        anyhow::bail!("REPOSITORY_NOT_GIT");
    }
    Ok(canonical)
}

async fn create_worktree(
    repository: &Path,
    managed_root: &Path,
    ticket_id: Uuid,
    run_id: Uuid,
    base_ref: &str,
) -> anyhow::Result<(PathBuf, String)> {
    let workspace = managed_root.join(run_id.to_string()).join("worktree");
    fs::create_dir_all(workspace.parent().expect("run parent")).await?;
    let branch = format!("openos/{ticket_id}-{}", &run_id.to_string()[..8]);
    let base_sha = output_text(
        Command::new("git")
            .arg("-C")
            .arg(repository)
            .args(["rev-parse", base_ref]),
        "git base ref",
    )
    .await?;
    checked(
        Command::new("git")
            .arg("-C")
            .arg(repository)
            .args(["worktree", "add", "-b"])
            .arg(&branch)
            .arg(&workspace)
            .arg(base_ref),
        "git worktree add",
    )
    .await?;
    Ok((workspace, base_sha))
}

struct EngineOutput {
    exit_status: i32,
    stdout: String,
    stderr: String,
    session_id: Option<String>,
}

async fn run_opencode(
    config: &Config,
    job: &WorkerJob,
    run_id: Uuid,
    workspace: &Path,
    prompt: &str,
    store: &RunStore,
) -> anyhow::Result<EngineOutput> {
    let mut child = Command::new(&config.opencode_binary)
        .args([
            "-p",
            "--output-format",
            "stream-json",
            "--verbose",
            "--max-turns",
            "50",
            "--bare",
            "--dangerously-skip-permissions",
        ])
        .arg(prompt)
        .current_dir(workspace)
        .env("OPENCODE_INVOKED_BY", "openagents-rust")
        .env("OPENTICKET_TICKET_ID", job.ticket_id.to_string())
        .env("OPENTICKET_CORRELATION_ID", job.correlation_id.to_string())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()?;
    let stdout = child.stdout.take().context("OpenCode stdout")?;
    let stderr = child.stderr.take().context("OpenCode stderr")?;
    let store_copy = store.clone();
    let stdout_task = tokio::spawn(async move {
        let mut lines = BufReader::new(stdout).lines();
        let mut raw = String::new();
        let mut session = None;
        while let Some(line) = lines.next_line().await? {
            if let Ok(event) = serde_json::from_str::<Value>(&line) {
                session = session.or_else(|| session_id(&event));
                store_copy
                    .update(
                        run_id,
                        RunStatus::Running,
                        "opencode.event",
                        safe_event(&event),
                    )
                    .await;
            }
            raw.push_str(&line);
            raw.push('\n');
        }
        Ok::<_, anyhow::Error>((raw, session))
    });
    let stderr_task = tokio::spawn(async move {
        let mut lines = BufReader::new(stderr).lines();
        let mut raw = String::new();
        while let Some(line) = lines.next_line().await? {
            raw.push_str(&line);
            raw.push('\n');
        }
        Ok::<_, anyhow::Error>(raw)
    });
    let status = tokio::select! {
        result = timeout(config.opencode_timeout, child.wait()) => result
            .map_err(|_| anyhow::anyhow!("OPENCODE_TIMEOUT"))??,
        _ = wait_cancelled(store, run_id) => {
            child.kill().await?;
            anyhow::bail!("RUN_CANCELLED");
        }
    };
    let (stdout, session_id) = stdout_task.await??;
    let stderr = stderr_task.await??;
    Ok(EngineOutput {
        exit_status: status.code().unwrap_or(-1),
        stdout,
        stderr,
        session_id,
    })
}

async fn run_tests(
    workspace: &Path,
    commands: &[String],
    run_id: Uuid,
    store: &RunStore,
) -> anyhow::Result<Vec<TestEvidence>> {
    let mut evidence = Vec::new();
    for command in commands {
        ensure_not_cancelled(store, run_id).await?;
        store
            .update(
                run_id,
                RunStatus::Running,
                "test.started",
                json!({"command":command}),
            )
            .await;
        let output = Command::new("sh")
            .args(["-lc", command])
            .current_dir(workspace)
            .output()
            .await?;
        let path = workspace
            .parent()
            .expect("run root")
            .join(format!("test-{}.log", evidence.len() + 1));
        let mut data = output.stdout;
        data.extend_from_slice(&output.stderr);
        fs::write(&path, &data).await?;
        let exit_status = output.status.code().unwrap_or(-1);
        store
            .update(
                run_id,
                RunStatus::Running,
                "test.completed",
                json!({"command":command,"exit_status":exit_status}),
            )
            .await;
        evidence.push(TestEvidence {
            command: command.clone(),
            exit_status,
            passed: u32::from(exit_status == 0),
            failed: u32::from(exit_status != 0),
            output_uri: Some(file_uri(&path)),
        });
        if exit_status != 0 {
            break;
        }
    }
    Ok(evidence)
}

async fn wait_cancelled(store: &RunStore, run_id: Uuid) {
    loop {
        if store.is_cancelled(run_id).await {
            return;
        }
        tokio::time::sleep(std::time::Duration::from_millis(100)).await;
    }
}

async fn ensure_not_cancelled(store: &RunStore, run_id: Uuid) -> anyhow::Result<()> {
    if store.is_cancelled(run_id).await {
        anyhow::bail!("RUN_CANCELLED");
    }
    Ok(())
}

async fn commit_changes(
    workspace: &Path,
    repository: &Path,
    base_sha: &str,
    ticket_id: Uuid,
    run_id: Uuid,
) -> anyhow::Result<GitEvidence> {
    checked(
        Command::new("git")
            .arg("-C")
            .arg(workspace)
            .args(["add", "-A"]),
        "git add",
    )
    .await?;
    let diff = checked_output(
        Command::new("git")
            .arg("-C")
            .arg(workspace)
            .args(["diff", "--cached", "--quiet"]),
    )
    .await?;
    if !diff.success() {
        checked(
            Command::new("git")
                .arg("-C")
                .arg(workspace)
                .args([
                    "-c",
                    "user.name=OpenAgents",
                    "-c",
                    "user.email=openagents@openos.local",
                    "commit",
                    "-m",
                ])
                .arg(format!(
                    "OpenTicket {ticket_id}: validated worker run {run_id}"
                )),
            "git commit",
        )
        .await?;
    }
    let sha = output_text(
        Command::new("git")
            .arg("-C")
            .arg(workspace)
            .args(["rev-parse", "HEAD"]),
        "git rev-parse",
    )
    .await?;
    if sha == base_sha {
        anyhow::bail!("OPENCODE_PRODUCED_NO_CHANGES");
    }
    let branch = output_text(
        Command::new("git")
            .arg("-C")
            .arg(workspace)
            .args(["branch", "--show-current"]),
        "git branch",
    )
    .await?;
    let status = output_text(
        Command::new("git")
            .arg("-C")
            .arg(workspace)
            .args(["status", "--porcelain"]),
        "git status",
    )
    .await?;
    Ok(GitEvidence {
        repository: repository.display().to_string(),
        worktree: workspace.display().to_string(),
        branch,
        commit_sha: sha,
        clean: status.is_empty(),
    })
}

async fn persist_events(root: &Path, run_id: Uuid, data: &str) -> anyhow::Result<WorkerArtifact> {
    let path = root.join(run_id.to_string()).join("opencode-events.jsonl");
    fs::write(&path, data).await?;
    let hash = format!("{:x}", Sha256::digest(data.as_bytes()));
    Ok(WorkerArtifact {
        kind: "opencode_event_stream".into(),
        name: "OpenCode events".into(),
        uri: file_uri(&path),
        sha256: Some(hash),
        metadata: json!({"bytes":data.len()}),
    })
}

async fn checked(command: &mut Command, name: &str) -> anyhow::Result<()> {
    let output = command.output().await?;
    if !output.status.success() {
        anyhow::bail!(
            "{name}: {}",
            sanitize(&String::from_utf8_lossy(&output.stderr))
        );
    }
    Ok(())
}

async fn checked_output(command: &mut Command) -> anyhow::Result<std::process::ExitStatus> {
    Ok(command
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .await?)
}

async fn output_text(command: &mut Command, name: &str) -> anyhow::Result<String> {
    let output = command.output().await?;
    if !output.status.success() {
        anyhow::bail!("{name} failed");
    }
    Ok(String::from_utf8_lossy(&output.stdout).trim().to_string())
}

fn string(value: &Value, key: &str) -> anyhow::Result<String> {
    value
        .get(key)
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .map(str::to_string)
        .ok_or_else(|| anyhow::anyhow!("INPUT_{key}_REQUIRED"))
}

fn string_array(value: &Value, key: &str) -> anyhow::Result<Vec<String>> {
    let values: Vec<String> = value
        .get(key)
        .cloned()
        .map(serde_json::from_value)
        .transpose()?
        .unwrap_or_default();
    if values.is_empty() {
        anyhow::bail!("INPUT_{key}_REQUIRED");
    }
    Ok(values)
}

fn session_id(value: &Value) -> Option<String> {
    value
        .get("session_id")
        .or_else(|| value.get("sessionId"))
        .or_else(|| {
            value
                .get("message")
                .and_then(|message| message.get("session_id"))
        })
        .and_then(Value::as_str)
        .map(str::to_string)
}

fn safe_event(value: &Value) -> Value {
    json!({"type":value.get("type"),"subtype":value.get("subtype"),"session_id":value.get("session_id")})
}

fn sanitize(value: &str) -> String {
    value
        .lines()
        .take(100)
        .map(|line| {
            if line.to_ascii_lowercase().contains("authorization") || line.contains("sk-") {
                "[redacted]"
            } else {
                line
            }
        })
        .collect::<Vec<_>>()
        .join("\n")
}

fn contains_secret(value: &str) -> bool {
    let lower = value.to_ascii_lowercase();
    ["sk-", "api_key=", "authorization: bearer", "private_key"]
        .iter()
        .any(|needle| lower.contains(needle))
}

fn nonempty(value: String) -> Option<String> {
    (!value.is_empty()).then_some(value)
}
fn file_uri(path: &Path) -> String {
    format!("file://{}", path.display())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn rejects_path_escape_and_unmanaged_repository() {
        let temp = tempfile::tempdir().unwrap();
        let value = json!({"repository":"../outside"});
        assert!(canonical_repository(&value, &[temp.path().into()])
            .await
            .is_err());
    }

    #[test]
    fn strips_secret_bearing_stderr() {
        assert_eq!(
            sanitize("ok\nAuthorization: Bearer abc\nsk-secret"),
            "ok\n[redacted]\n[redacted]"
        );
    }
}
