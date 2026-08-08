use std::{
    io::Read,
    net::{IpAddr, SocketAddr},
    path::{Component, Path, PathBuf},
    process::Stdio,
    sync::atomic::{AtomicBool, Ordering},
    time::Duration,
};

use anyhow::Context;
use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine as _};
use chrono::Utc;
use contract_core::{
    AcceptanceCriterionEvidence, CognitiveEvidenceReference, CognitiveObservation,
    CognitiveObservationType, CognitiveRisk, CognitiveScope, CognitiveScopeType,
    EngineeringWorkspaceEvidence, GitEvidence, PullRequestEvidence, QaEvidence, QaRequirement,
    SkillReference, SkillSource, TestEvidence, WorkerArtifact, WorkerJob, WorkerResult,
};
use reqwest::{header::LOCATION, redirect::Policy};
use scraper::{Html, Selector};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use tokio::{
    fs,
    io::{AsyncBufReadExt, AsyncWriteExt, BufReader},
    process::Command,
    time::{sleep, timeout},
};
use uuid::Uuid;

use crate::{
    config::Config,
    model::{RunStatus, RunStore},
};

const SKILL_AUTHOR_LLM_TIMEOUT: Duration = Duration::from_secs(120);
const SKILL_AUTHOR_WORKFLOW_TIMEOUT: Duration = Duration::from_secs(285);
const MAX_RESEARCH_BODY_BYTES: usize = 2_000_000;
const SKILL_AUTHOR_TRANSPORT_ATTEMPTS: usize = 2;
const SKILL_AUTHOR_CANDIDATE_ATTEMPTS: usize = 3;
const MAX_RESEARCH_SOURCES: usize = 8;
const MAX_CHANGED_FILE_PATCHES: usize = 32;
const MAX_FILE_PATCH_BYTES: usize = 16 * 1024;
const MAX_TOTAL_PATCH_BYTES: usize = 64 * 1024;
const MAX_DIFF_SCAN_BYTES: usize = 4 * 1024 * 1024;
const MAX_REQUIRED_SKILLS: usize = 16;
const MAX_SKILL_BODY_BYTES: usize = 18_000;
const MAX_SKILL_CONTEXT_BYTES: usize = 64 * 1024;
const MAX_ACCEPTANCE_DIFF_BYTES: usize = 64 * 1024;
const MAX_ACCEPTANCE_TEST_BYTES: usize = 32 * 1024;
const MAX_ACCEPTANCE_REPORT_BYTES: usize = 32 * 1024;
const ACCEPTANCE_AUDIT_TIMEOUT: Duration = Duration::from_secs(120);
static GIT_DELIVERY_SETUP_READY: AtomicBool = AtomicBool::new(false);

struct LoadedSkill {
    reference: SkillReference,
    body: String,
}

pub async fn runtime_healthy(config: &Config) -> bool {
    let provider_ready = command_ok(Command::new(&config.git_provider_binary).arg("--version"))
        .await
        && command_ok(Command::new(&config.git_provider_binary).args([
            "auth",
            "status",
            "--hostname",
            "github.com",
        ]))
        .await;
    let git_delivery_setup_ready = if GIT_DELIVERY_SETUP_READY.load(Ordering::Acquire) {
        true
    } else {
        let ready = provider_ready
            && command_ok(Command::new(&config.git_provider_binary).args([
                "auth",
                "setup-git",
                "--hostname",
                "github.com",
            ]))
            .await
            && prepare_git_signing(config).await;
        if ready {
            GIT_DELIVERY_SETUP_READY.store(true, Ordering::Release);
        }
        ready
    };
    command_ok(Command::new("git").arg("--version")).await
        && provider_ready
        && git_delivery_setup_ready
        && command_ok(Command::new(&config.opencode_binary).arg("--version")).await
        && fs::metadata(&config.shell_binary)
            .await
            .is_ok_and(|metadata| metadata.is_file())
        && fs::create_dir_all(&config.managed_root).await.is_ok()
}

async fn prepare_git_signing(config: &Config) -> bool {
    if !config.git_sign_commits {
        return false;
    }
    if let Some(encoded) = config.git_signing_key_b64.as_deref() {
        let Ok(key) = base64::engine::general_purpose::STANDARD.decode(encoded.trim()) else {
            return false;
        };
        let mut child = match Command::new("gpg")
            .args(["--batch", "--import"])
            .stdin(Stdio::piped())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
        {
            Ok(child) => child,
            Err(_) => return false,
        };
        let Some(mut stdin) = child.stdin.take() else {
            return false;
        };
        if stdin.write_all(&key).await.is_err() {
            return false;
        }
        drop(stdin);
        if !matches!(child.wait().await, Ok(status) if status.success()) {
            return false;
        }
    }
    let output = match Command::new("gpg")
        .args(["--batch", "--with-colons", "--list-secret-keys"])
        .output()
        .await
    {
        Ok(output) if output.status.success() => output,
        _ => return false,
    };
    let listing = String::from_utf8_lossy(&output.stdout);
    let Some(fingerprint) = listing
        .lines()
        .find(|line| line.starts_with("fpr:"))
        .and_then(|line| line.split(':').nth(9))
        .filter(|value| !value.is_empty())
    else {
        return false;
    };
    command_ok(Command::new("git").args(["config", "--global", "user.signingkey", fingerprint]))
        .await
        && command_ok(Command::new("git").args(["config", "--global", "gpg.format", "openpgp"]))
            .await
        && command_ok(Command::new("git").args(["config", "--global", "commit.gpgsign", "true"]))
            .await
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
    if job.job_type == "agent.skill_author" {
        return timeout(
            SKILL_AUTHOR_WORKFLOW_TIMEOUT,
            author_skill(job, run_id, config, store),
        )
        .await
        .map_err(|_| anyhow::anyhow!("SKILL_AUTHOR_WORKFLOW_TIMEOUT"))?;
    }
    if job.organization_id != config.organization_id {
        anyhow::bail!("WORKSPACE_TENANT_MISMATCH");
    }
    if !config.git_sign_commits {
        anyhow::bail!("UNSIGNED_ENGINEERING_COMMITS_DISABLED");
    }
    let inputs = job.inputs.get("inputs").unwrap_or(&job.inputs);
    let remote_url = string(inputs, "remote_url")?;
    let repository = canonical_repository(
        inputs,
        &config.allowed_repositories,
        &remote_url,
        &config.git_remote,
        config.git_timeout,
    )
    .await?;
    let repository_name = string(inputs, "repository_name")?;
    let provider = string(inputs, "provider")?;
    let external_repository_id = string(inputs, "external_repository_id")?;
    verify_repository_remote(&repository, &config.git_remote, &remote_url).await?;
    let pull_request_base_branch = string(inputs, "pull_request_base_branch")?;
    if inputs.get("requires_human_review").and_then(Value::as_bool) != Some(true) {
        anyhow::bail!("HUMAN_REVIEW_POLICY_REQUIRED");
    }
    let base_ref = string(inputs, "base_ref")?;
    let tests = string_array(inputs, "test_commands")?;
    let qa_requirements = parse_qa_requirements(inputs, &tests)?;
    let prompt = string(inputs, "prompt").unwrap_or_else(|_| {
        job.inputs
            .get("task")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string()
    });
    let loaded_skills = load_required_skills(job, config).await?;
    store
        .update(
            run_id,
            RunStatus::Running,
            "skills.loaded",
            json!({"skills":loaded_skills.iter().map(|skill| &skill.reference).collect::<Vec<_>>() }),
        )
        .await;
    let prompt = engineering_prompt(&prompt, &loaded_skills, &job.acceptance_criteria)?;
    if contains_secret(&serde_json::to_string(inputs)?) {
        anyhow::bail!("INPUT_SECRET_REJECTED");
    }
    let (workspace_root, workspace, base_sha) = create_worktree(WorktreeSpec {
        repository: &repository,
        managed_root: &config.managed_root,
        organization_id: job.organization_id,
        repository_name: &repository_name,
        ticket_id: job.ticket_id,
        plan_id: job.plan_id,
        task_id: job.task_id,
        base_ref: &base_ref,
        remote: &config.git_remote,
        timeout: config.git_timeout,
    })
    .await?;
    ensure_not_cancelled(store, run_id).await?;
    store
        .update(
            run_id,
            RunStatus::Running,
            "worktree.created",
            json!({
                "workspace_root":workspace_root,
                "worktree":workspace,
                "repository":repository,
                "repository_name":repository_name,
                "organization_id":job.organization_id
            }),
        )
        .await;
    let engine = run_opencode(config, job, run_id, &workspace, &prompt, store).await?;
    if let Some(kind) = detect_secret(&engine.stdout).or_else(|| detect_secret(&engine.stderr)) {
        tracing::warn!(
            run_id = %run_id,
            secret_kind = kind.label(),
            "OpenCode output rejected by credential guard"
        );
        anyhow::bail!("ENGINE_SECRET_LEAK_REJECTED");
    }
    ensure_not_cancelled(store, run_id).await?;
    let test_evidence = run_tests(&workspace, &tests, run_id, store, config.qa_timeout).await?;
    let qa_evidence = qa_evidence(&qa_requirements, &test_evidence)?;
    if engine.exit_status != 0 || test_evidence.iter().any(|test| test.exit_status != 0) {
        anyhow::bail!("engine or declared tests failed");
    }
    let git = commit_changes(
        &workspace,
        &repository,
        &base_sha,
        job.ticket_id,
        run_id,
        true,
        config.git_timeout,
    )
    .await?;
    let changed_files = changed_files_artifact(&workspace, &base_sha, &git.commit_sha)?;
    validate_changed_file_policy(&changed_files, inputs)?;
    store
        .update(
            run_id,
            RunStatus::Running,
            "acceptance.audit.started",
            json!({"criteria_count":job.acceptance_criteria.len(),"provider":"openai-compatible","model":config.llm_model}),
        )
        .await;
    let acceptance_evidence = audit_acceptance_criteria(
        config,
        &workspace,
        &base_sha,
        &git.commit_sha,
        &test_evidence,
        &engine.stdout,
        &job.acceptance_criteria,
    )
    .await?;
    store
        .update(
            run_id,
            RunStatus::Running,
            "acceptance.audit.completed",
            json!({"criteria_count":acceptance_evidence.len(),"passed":true}),
        )
        .await;
    store
        .update(
            run_id,
            RunStatus::Running,
            "git.push.started",
            json!({"remote":config.git_remote,"branch":git.branch}),
        )
        .await;
    let mut git = git;
    push_branch(
        &workspace,
        &config.git_remote,
        &git.branch,
        config.git_timeout,
    )
    .await?;
    git.pushed = true;
    git.remote = Some(config.git_remote.clone());
    store
        .update(
            run_id,
            RunStatus::Running,
            "git.push.completed",
            json!({"remote":config.git_remote,"branch":git.branch,"commit_sha":git.commit_sha}),
        )
        .await;
    store
        .update(
            run_id,
            RunStatus::Running,
            "pull_request.started",
            json!({"provider":provider,"base_branch":pull_request_base_branch,"draft":true,"review_required":true}),
        )
        .await;
    let pull_request = create_or_read_pull_request(PullRequestSpec {
        config,
        workspace: &workspace,
        provider: &provider,
        external_repository_id: &external_repository_id,
        base_branch: &pull_request_base_branch,
        head_branch: &git.branch,
        ticket_id: job.ticket_id,
        run_id,
        qa: &qa_evidence,
    })
    .await?;
    store
        .update(
            run_id,
            RunStatus::Running,
            "pull_request.completed",
            json!({
                "provider":pull_request.provider,
                "url":pull_request.url,
                "number":pull_request.number,
                "draft":pull_request.draft,
                "review_required":pull_request.review_required
            }),
        )
        .await;
    let prompt_sha256 = format!("{:x}", Sha256::digest(prompt.as_bytes()));
    let execution_contract = WorkerArtifact {
        kind: "execution_contract".into(),
        name: "Verified skills and acceptance criteria".into(),
        uri: format!("openagents://runs/{run_id}/execution-contract"),
        sha256: Some(prompt_sha256.clone()),
        metadata: json!({
            "skills":loaded_skills.iter().map(|skill| json!({
                "reference":skill.reference,
                "content_sha256":format!("{:x}", Sha256::digest(skill.body.as_bytes())),
                "content_bytes":skill.body.len(),
            })).collect::<Vec<_>>(),
            "acceptance_criteria":job.acceptance_criteria,
            "prompt_injected":true,
            "prompt_sha256":prompt_sha256,
        }),
    };
    let artifact = persist_events(&workspace_root, run_id, &engine.stdout).await?;
    let cognitive_observations =
        engineering_cognitive_observations(job, run_id, &loaded_skills, &engine.cognitive_events);
    Ok(WorkerResult {
        run_id,
        artifacts: vec![artifact, changed_files, execution_contract],
        stderr: nonempty(sanitize(&engine.stderr)),
        exit_status: engine.exit_status,
        tests: test_evidence,
        git: Some(git),
        engine_session_id: engine.session_id,
        loaded_skills: loaded_skills
            .iter()
            .map(|skill| skill.reference.clone())
            .collect(),
        acceptance_evidence,
        cognitive_observations,
        engineering_workspace: Some(EngineeringWorkspaceEvidence {
            organization_id: job.organization_id,
            run_id,
            repository_name,
            workspace_root: workspace_root.display().to_string(),
            repository_folder: workspace.display().to_string(),
        }),
        qa: qa_evidence,
        pull_request: Some(pull_request),
    })
}

fn engineering_prompt(
    task: &str,
    loaded_skills: &[LoadedSkill],
    acceptance_criteria: &[String],
) -> anyhow::Result<String> {
    let skill_context = loaded_skills
        .iter()
        .map(|skill| {
            format!(
                "## Skill {}@{} ({:?}, sha256={})\n{}",
                skill.reference.id,
                skill.reference.version,
                skill.reference.source,
                skill.reference.sha256,
                skill.body
            )
        })
        .collect::<Vec<_>>()
        .join("\n\n");
    let criteria = serde_json::to_string_pretty(acceptance_criteria)?;
    Ok(format!(
        "{task}\n\nOpenOS required skill instructions (immutable references verified by OpenAgents):\n{skill_context}\n\n\
         OpenOS acceptance criteria (satisfy every item and leave evidence in the diff or declared test output):\n{criteria}\n\n\
         OpenOS worktree isolation contract:\n\
         - The current working directory is the only permitted file-editing and Git target.\n\
         - Treat repository paths from the task as source identity metadata, never as an edit location.\n\
         - Resolve requested paths relative to the current working directory; never edit an absolute repository path or use git -C outside the current working directory.\n\
         - Fail explicitly if a requested target resolves outside the current working directory.\n\n\
         OpenOS generalization contract:\n\
         - Identify facts that vary between valid instances and expose them through existing configuration, function arguments, typed schemas, or discovery outputs.\n\
         - Keep the triggering app name, owner, repository, URL, credential reference, and destination as instance data, not reusable control flow.\n\
         - When a value is unknown, discover it from available evidence and registered tools; fail explicitly when evidence, authorization, or a required real tool is unavailable.\n\
         - Implement the smallest reusable boundary justified by at least two concrete instances; avoid speculative abstractions.\n\
         - Add behavior tests for the triggering instance and a materially different instance whenever the changed behavior is reusable."
    ))
}

async fn load_required_skills(
    job: &WorkerJob,
    config: &Config,
) -> anyhow::Result<Vec<LoadedSkill>> {
    if job.required_skills.len() > MAX_REQUIRED_SKILLS {
        anyhow::bail!("REQUIRED_SKILLS_LIMIT_EXCEEDED");
    }
    let mut seen = std::collections::HashSet::new();
    for reference in &job.required_skills {
        validate_skill_reference(reference)?;
        if !seen.insert((
            reference.id.clone(),
            reference.version.clone(),
            reference.source.clone(),
        )) {
            anyhow::bail!("REQUIRED_SKILL_DUPLICATE");
        }
    }

    let bundled_references = job
        .required_skills
        .iter()
        .filter(|reference| reference.source == SkillSource::Bundled)
        .collect::<Vec<_>>();
    let organization_references = job
        .required_skills
        .iter()
        .filter(|reference| reference.source == SkillSource::Organization)
        .collect::<Vec<_>>();
    let bundled = load_bundled_skills(&config.skill_root, &bundled_references).await?;
    let organization = load_organization_skills(job, config, &organization_references).await?;
    let mut by_reference = bundled
        .into_iter()
        .chain(organization)
        .map(|skill| {
            (
                (
                    skill.reference.id.clone(),
                    skill.reference.version.clone(),
                    skill.reference.source.clone(),
                ),
                skill,
            )
        })
        .collect::<std::collections::HashMap<_, _>>();
    let mut loaded = Vec::with_capacity(job.required_skills.len());
    let mut total_bytes = 0usize;
    for reference in &job.required_skills {
        let key = (
            reference.id.clone(),
            reference.version.clone(),
            reference.source.clone(),
        );
        let skill = by_reference
            .remove(&key)
            .ok_or_else(|| anyhow::anyhow!("REQUIRED_SKILL_NOT_FOUND: {}", reference.id))?;
        total_bytes += skill.body.len();
        if total_bytes > MAX_SKILL_CONTEXT_BYTES {
            anyhow::bail!("REQUIRED_SKILL_CONTEXT_LIMIT_EXCEEDED");
        }
        loaded.push(skill);
    }
    Ok(loaded)
}

fn validate_skill_reference(reference: &SkillReference) -> anyhow::Result<()> {
    let valid_id = !reference.id.is_empty()
        && reference.id.len() <= 96
        && reference
            .id
            .bytes()
            .all(|byte| byte.is_ascii_lowercase() || byte.is_ascii_digit() || byte == b'-');
    let valid_version = !reference.version.trim().is_empty() && reference.version.len() <= 32;
    let valid_hash = reference.sha256.len() == 64
        && reference
            .sha256
            .bytes()
            .all(|byte| byte.is_ascii_hexdigit());
    if !valid_id || !valid_version || !valid_hash {
        anyhow::bail!("REQUIRED_SKILL_REFERENCE_INVALID");
    }
    Ok(())
}

async fn load_bundled_skills(
    root: &Path,
    references: &[&SkillReference],
) -> anyhow::Result<Vec<LoadedSkill>> {
    if references.is_empty() {
        return Ok(Vec::new());
    }
    let root = fs::canonicalize(root)
        .await
        .context("OPENOS_SKILL_ROOT is unavailable")?;
    let mut directories = vec![(root.clone(), 0usize)];
    let mut found = std::collections::HashMap::<String, (String, String)>::new();
    while let Some((directory, depth)) = directories.pop() {
        if depth > 6 {
            continue;
        }
        let mut entries = fs::read_dir(&directory).await?;
        while let Some(entry) = entries.next_entry().await? {
            let file_type = entry.file_type().await?;
            let path = entry.path();
            if file_type.is_dir() {
                if !entry.file_name().to_string_lossy().starts_with('.') {
                    directories.push((path, depth + 1));
                }
            } else if file_type.is_file() && entry.file_name() == "SKILL.md" {
                let canonical = fs::canonicalize(&path).await?;
                if !canonical.starts_with(&root) {
                    anyhow::bail!("BUNDLED_SKILL_PATH_ESCAPE");
                }
                let raw = fs::read_to_string(&canonical).await?;
                let id = skill_frontmatter_value(&raw, "name").unwrap_or_else(|| {
                    canonical
                        .parent()
                        .and_then(Path::file_name)
                        .unwrap_or_default()
                        .to_string_lossy()
                        .into_owned()
                });
                if references.iter().any(|reference| reference.id == id) {
                    let version =
                        skill_frontmatter_value(&raw, "version").unwrap_or_else(|| "1".into());
                    found
                        .entry(id)
                        .or_insert((version, strip_skill_frontmatter(&raw).to_string()));
                }
            }
        }
    }
    references
        .iter()
        .map(|reference| {
            let (version, body) = found.get(&reference.id).ok_or_else(|| {
                anyhow::anyhow!("REQUIRED_BUNDLED_SKILL_NOT_FOUND: {}", reference.id)
            })?;
            verified_loaded_skill(reference, version, body)
        })
        .collect()
}

async fn load_organization_skills(
    job: &WorkerJob,
    config: &Config,
    references: &[&SkillReference],
) -> anyhow::Result<Vec<LoadedSkill>> {
    if references.is_empty() {
        return Ok(Vec::new());
    }
    let response = reqwest::Client::builder()
        .timeout(config.request_timeout)
        .redirect(Policy::none())
        .build()?
        .get(format!(
            "{}/api/v1/internal/runtime-skills/{}",
            config.openbrain_url, job.organization_id
        ))
        .header("X-Internal-Service-Key", &config.internal_service_key)
        .send()
        .await?
        .error_for_status()?
        .json::<Value>()
        .await?;
    let rows = response
        .get("skills")
        .and_then(Value::as_array)
        .ok_or_else(|| anyhow::anyhow!("ORGANIZATION_SKILL_CATALOG_INVALID"))?;
    references
        .iter()
        .map(|reference| {
            let row = rows
                .iter()
                .find(|row| {
                    row.get("slug").and_then(Value::as_str) == Some(&reference.id)
                        && row.get("version").map(value_string).as_deref()
                            == Some(reference.version.as_str())
                })
                .ok_or_else(|| {
                    anyhow::anyhow!("REQUIRED_ORGANIZATION_SKILL_NOT_FOUND: {}", reference.id)
                })?;
            let body = row
                .get("body")
                .and_then(Value::as_str)
                .ok_or_else(|| anyhow::anyhow!("ORGANIZATION_SKILL_BODY_INVALID"))?
                .trim();
            verified_loaded_skill(reference, &reference.version, body)
        })
        .collect()
}

fn value_string(value: &Value) -> String {
    value
        .as_str()
        .map(str::to_owned)
        .unwrap_or_else(|| value.to_string())
}

fn verified_loaded_skill(
    reference: &SkillReference,
    version: &str,
    body: &str,
) -> anyhow::Result<LoadedSkill> {
    if body.is_empty() || body.len() > MAX_SKILL_BODY_BYTES {
        anyhow::bail!("REQUIRED_SKILL_BODY_INVALID: {}", reference.id);
    }
    let digest = format!("{:x}", Sha256::digest(body.as_bytes()));
    if version != reference.version || !digest.eq_ignore_ascii_case(&reference.sha256) {
        anyhow::bail!("REQUIRED_SKILL_INTEGRITY_MISMATCH: {}", reference.id);
    }
    Ok(LoadedSkill {
        reference: reference.clone(),
        body: body.to_string(),
    })
}

fn strip_skill_frontmatter(raw: &str) -> &str {
    let Some(rest) = raw.strip_prefix("---\n") else {
        return raw.trim();
    };
    rest.find("\n---\n")
        .map(|end| rest[end + 5..].trim())
        .unwrap_or_else(|| raw.trim())
}

fn skill_frontmatter_value(raw: &str, key: &str) -> Option<String> {
    let rest = raw.strip_prefix("---\n")?;
    let end = rest.find("\n---\n")?;
    rest[..end].lines().find_map(|line| {
        let (candidate, value) = line.split_once(':')?;
        (candidate.trim() == key).then(|| value.trim().trim_matches(['\'', '"']).to_string())
    })
}

fn changed_files_artifact(
    workspace: &Path,
    base_sha: &str,
    commit_sha: &str,
) -> anyhow::Result<WorkerArtifact> {
    let names_output = std::process::Command::new("git")
        .arg("-C")
        .arg(workspace)
        .args(["diff", "--name-only", base_sha, commit_sha])
        .output()?;
    if !names_output.status.success() {
        anyhow::bail!("CHANGED_FILES_EVIDENCE_FAILED");
    }
    let files: Vec<&str> = std::str::from_utf8(&names_output.stdout)?
        .lines()
        .filter(|value| !value.trim().is_empty())
        .collect();
    if files.is_empty() {
        anyhow::bail!("CHANGED_FILES_EVIDENCE_EMPTY");
    }
    let full_diff = bounded_git_diff(workspace, base_sha, commit_sha, None)?;
    let full_diff_text = std::str::from_utf8(&full_diff)?;
    if detect_secret(full_diff_text).is_some() {
        anyhow::bail!("CHANGED_FILES_PATCH_SECRET_REJECTED");
    }

    let mut patches = Vec::new();
    let mut retained_bytes = 0usize;
    let mut patches_truncated = files.len() > MAX_CHANGED_FILE_PATCHES;
    for path in files.iter().take(MAX_CHANGED_FILE_PATCHES) {
        if retained_bytes >= MAX_TOTAL_PATCH_BYTES {
            patches_truncated = true;
            break;
        }
        let output = bounded_git_diff(workspace, base_sha, commit_sha, Some(path))?;
        let raw = std::str::from_utf8(&output)?;
        let allowance = MAX_FILE_PATCH_BYTES.min(MAX_TOTAL_PATCH_BYTES - retained_bytes);
        let (unified, truncated) = bounded_utf8(raw, allowance);
        let (additions, deletions) = diff_line_counts(raw);
        retained_bytes += unified.len();
        patches_truncated |= truncated;
        patches.push(json!({
            "path": path,
            "unified": unified,
            "additions": additions,
            "deletions": deletions,
            "truncated": truncated,
        }));
    }
    Ok(WorkerArtifact {
        kind: "changed_files".into(),
        name: "Changed files".into(),
        uri: format!("git://{commit_sha}/changed-files"),
        sha256: Some(format!("{:x}", Sha256::digest(&full_diff))),
        metadata: json!({
            "base_sha": base_sha,
            "commit_sha": commit_sha,
            "files": files,
            "patches": patches,
            "patches_truncated": patches_truncated,
        }),
    })
}

fn bounded_git_diff(
    workspace: &Path,
    base_sha: &str,
    commit_sha: &str,
    path: Option<&str>,
) -> anyhow::Result<Vec<u8>> {
    let mut command = std::process::Command::new("git");
    command.arg("-C").arg(workspace).args([
        "diff",
        "--no-ext-diff",
        "--unified=3",
        base_sha,
        commit_sha,
        "--",
    ]);
    if let Some(path) = path {
        command.arg(path);
    }
    let mut child = command.stdout(Stdio::piped()).spawn()?;
    let stdout = child.stdout.take().context("CHANGED_FILES_PATCH_STDOUT")?;
    let mut output = Vec::new();
    stdout
        .take((MAX_DIFF_SCAN_BYTES + 1) as u64)
        .read_to_end(&mut output)?;
    if output.len() > MAX_DIFF_SCAN_BYTES {
        let _ = child.kill();
        let _ = child.wait();
        anyhow::bail!("CHANGED_FILES_PATCH_TOO_LARGE");
    }
    if !child.wait()?.success() {
        anyhow::bail!("CHANGED_FILES_PATCH_FAILED");
    }
    Ok(output)
}

fn bounded_utf8(value: &str, max_bytes: usize) -> (&str, bool) {
    if value.len() <= max_bytes {
        return (value, false);
    }
    let mut end = max_bytes;
    while end > 0 && !value.is_char_boundary(end) {
        end -= 1;
    }
    (&value[..end], true)
}

fn diff_line_counts(diff: &str) -> (usize, usize) {
    diff.lines().fold((0, 0), |(additions, deletions), line| {
        if line.starts_with('+') && !line.starts_with("+++") {
            (additions + 1, deletions)
        } else if line.starts_with('-') && !line.starts_with("---") {
            (additions, deletions + 1)
        } else {
            (additions, deletions)
        }
    })
}

fn validate_job(job: &WorkerJob) -> anyhow::Result<()> {
    let supported = (job.job_type == "engineering.opencode"
        && job
            .required_capabilities
            .iter()
            .any(|value| value == "invoke_opencode"))
        || (job.job_type == "agent.skill_author"
            && ["skill_author", "web_search", "web_extract"]
                .iter()
                .all(|required| {
                    job.required_capabilities
                        .iter()
                        .any(|value| value == required)
                }));
    if !supported {
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

#[derive(Debug)]
struct ResearchSource {
    url: String,
    title: String,
    text: String,
}

struct ValidatedResearchUrl {
    url: url::Url,
    host: String,
    addresses: Vec<SocketAddr>,
    pin_dns: bool,
}

async fn author_skill(
    job: &WorkerJob,
    run_id: Uuid,
    config: &Config,
    store: &RunStore,
) -> anyhow::Result<WorkerResult> {
    let input = job.inputs.get("inputs").unwrap_or(&job.inputs);
    let slug = string(input, "proposed_skill_id")?;
    let query = string(input, "research_query")?;
    let criteria = string_array(input, "success_criteria")?;
    let generalization = input
        .get("generalization")
        .cloned()
        .ok_or_else(|| anyhow::anyhow!("SKILL_GENERALIZATION_REQUIRED"))?;
    validate_generalization_contract(&generalization)?;
    let authoring_criteria = authoring_success_criteria(&criteria);
    store
        .update(
            run_id,
            RunStatus::Running,
            "research.search.started",
            json!({"query":query,"tool":"web_search"}),
        )
        .await;
    let search_http = reqwest::Client::builder()
        .user_agent("OpenAgents skill_author/0.1")
        .timeout(config.request_timeout)
        .build()?;
    let mut candidates = web_search(&search_http, &query).await?;
    rank_search_candidates(&mut candidates, &query);
    store
        .update(
            run_id,
            RunStatus::Running,
            "research.search.completed",
            json!({"tool":"web_search","result_count":candidates.len()}),
        )
        .await;
    let mut sources: Vec<ResearchSource> = Vec::new();
    let mut rejected_sources = Vec::new();
    extract_research_candidates(
        config.request_timeout,
        candidates,
        &mut sources,
        &mut rejected_sources,
    )
    .await;
    let mut hosts = research_hosts(&sources);
    if hosts.len() < 2 {
        let diversified_query = diversify_research_query(&query, &hosts);
        let mut diversified_candidates = web_search(&search_http, &diversified_query)
            .await
            .unwrap_or_default();
        rank_search_candidates(&mut diversified_candidates, &query);
        store
            .update(
                run_id,
                RunStatus::Running,
                "research.search.diversified",
                json!({
                    "tool":"web_search",
                    "query":diversified_query,
                    "excluded_hosts":hosts,
                    "result_count":diversified_candidates.len(),
                }),
            )
            .await;
        extract_research_candidates(
            config.request_timeout,
            diversified_candidates,
            &mut sources,
            &mut rejected_sources,
        )
        .await;
        hosts = research_hosts(&sources);
    }
    if sources.len() < 2 || hosts.len() < 2 {
        store
            .update(
                run_id,
                RunStatus::Running,
                "research.extract.insufficient",
                json!({
                    "tool":"web_extract",
                    "source_count":sources.len(),
                    "independent_hosts":hosts.len(),
                    "rejected":rejected_sources.into_iter().take(8).collect::<Vec<_>>(),
                }),
            )
            .await;
        anyhow::bail!("AUTHORITATIVE_RESEARCH_INSUFFICIENT: fewer than two independent documents were extracted");
    }
    store
        .update(
            run_id,
            RunStatus::Running,
            "research.extract.completed",
            json!({
                "tool":"web_extract",
                "source_count":sources.len(),
                "source_hosts":sources.iter().filter_map(|source| url::Url::parse(&source.url).ok()).filter_map(|value| value.host_str().map(str::to_owned)).collect::<std::collections::HashSet<_>>(),
            }),
        )
        .await;
    store
        .update(
            run_id,
            RunStatus::Running,
            "authoring.started",
            json!({"profile":"skill_author","source_count":sources.len()}),
        )
        .await;
    let (_authored, description, body, source_meta, validation) = author_validated_skill(
        config,
        &slug,
        &query,
        &authoring_criteria,
        &generalization,
        &sources,
        run_id,
        store,
    )
    .await?;
    store
        .update(
            run_id,
            RunStatus::Running,
            "authoring.completed",
            json!({"profile":"skill_author","body_length":body.len()}),
        )
        .await;
    store
        .update(
            run_id,
            RunStatus::Running,
            "validation.started",
            json!({"criteria_count":authoring_criteria.len(),"workflow_criteria_count":criteria.len()-authoring_criteria.len()}),
        )
        .await;
    store
        .update(
            run_id,
            RunStatus::Running,
            "validation.completed",
            json!({"passed":true,"criteria_count":authoring_criteria.len()}),
        )
        .await;
    let skill = json!({
        "slug":slug,"description":description,"skill_md":body,"sources":source_meta,
        "generalization":generalization,
        "validation":validation,
        "provenance":{"research_tools":["web_search","web_extract"],"research_query":query,"provider":"openai-compatible","model":config.llm_model,"source_count":source_meta.len()}
    });
    let serialized = serde_json::to_vec(&skill)?;
    let sha256 = format!("{:x}", Sha256::digest(&serialized));
    Ok(WorkerResult {
        run_id,
        artifacts: vec![WorkerArtifact {
            kind: "organization_skill".into(),
            name: format!("{slug}.json"),
            uri: format!("openagents://runs/{run_id}/skills/{slug}"),
            sha256: Some(sha256),
            metadata: json!({"skill":skill}),
        }],
        stderr: None,
        exit_status: 0,
        tests: vec![TestEvidence {
            command: "skill_author_validation".into(),
            exit_status: 0,
            passed: authoring_criteria.len() as u32 + 6,
            failed: 0,
            output_uri: None,
        }],
        git: None,
        engine_session_id: Some(format!("skill_author:{run_id}")),
        loaded_skills: vec![],
        acceptance_evidence: vec![],
        cognitive_observations: vec![CognitiveObservation {
            id: Uuid::new_v4(),
            organization_id: job.organization_id,
            correlation_id: job.correlation_id,
            run_id: Some(run_id.to_string()),
            session_id: None,
            agent_id: None,
            revision_id: None,
            producer: "openagents".into(),
            sequence: 0,
            scope: CognitiveScope {
                r#type: CognitiveScopeType::Organization,
                id: job.organization_id,
            },
            r#type: CognitiveObservationType::AdaptationProposed,
            confidence: 0.9,
            expected: None,
            observed: Some(
                json!({ "key":"skill.candidate", "value":slug, "validation":validation }),
            ),
            evidence: vec![CognitiveEvidenceReference {
                r#type: "artifact".into(),
                r#ref: format!("openagents://runs/{run_id}/skills/{slug}"),
                summary: Some("Validated read-only skill candidate".into()),
                protected: false,
            }],
            risk: CognitiveRisk::Low,
            observed_at: Utc::now(),
            idempotency_key: format!("openagents:{run_id}:skill-candidate"),
        }],
        engineering_workspace: None,
        qa: vec![],
        pull_request: None,
    })
}

async fn author_validated_skill(
    config: &Config,
    slug: &str,
    query: &str,
    criteria: &[String],
    generalization: &Value,
    sources: &[ResearchSource],
    run_id: Uuid,
    store: &RunStore,
) -> anyhow::Result<(Value, String, String, Vec<Value>, Value)> {
    let mut feedback: Option<String> = None;
    for attempt in 0..SKILL_AUTHOR_CANDIDATE_ATTEMPTS {
        let authored = author_with_llm(
            config,
            slug,
            query,
            criteria,
            generalization,
            sources,
            feedback.as_deref(),
        )
        .await?;
        let description = authored
            .get("description")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .trim()
            .to_string();
        let body = authored
            .get("skill_md")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .trim()
            .to_string();
        let result = if description.len() < 20 || body.len() < 120 || body.len() > 18_000 {
            Err(anyhow::anyhow!("SKILL_AUTHOR_OUTPUT_INVALID"))
        } else {
            select_authoritative_sources(&authored, sources).and_then(|source_meta| {
                validate_authored_skill(
                    &authored,
                    criteria,
                    generalization,
                    &body,
                    &source_meta,
                    sources,
                )
                .map(|validation| {
                    (
                        authored.clone(),
                        description.clone(),
                        body.clone(),
                        source_meta,
                        validation,
                    )
                })
            })
        };
        match result {
            Ok(candidate) => return Ok(candidate),
            Err(error) if attempt + 1 < SKILL_AUTHOR_CANDIDATE_ATTEMPTS => {
                feedback = Some(error.to_string());
                store.update(run_id, RunStatus::Running, "authoring.repairing", json!({
                    "profile":"skill_author","attempt":attempt + 2,"validation_error":error.to_string()
                })).await;
            }
            Err(error) => return Err(error),
        }
    }
    unreachable!("bounded authoring attempts always return")
}

async fn extract_research_candidates(
    request_timeout: Duration,
    candidates: Vec<(String, String)>,
    sources: &mut Vec<ResearchSource>,
    rejected_sources: &mut Vec<Value>,
) {
    for (url, title) in candidates.into_iter().take(24) {
        let (resolved_url, text) = match web_extract(request_timeout, &url).await {
            Ok(value) => value,
            Err(error) => {
                rejected_sources.push(json!({
                    "host":url::Url::parse(&url).ok().and_then(|value| value.host_str().map(str::to_owned)),
                    "error":error.to_string().chars().take(240).collect::<String>(),
                }));
                continue;
            }
        };
        if sources.iter().any(|source| source.url == resolved_url) {
            continue;
        }
        let resolved_host = url::Url::parse(&resolved_url)
            .ok()
            .and_then(|value| value.host_str().map(str::to_owned));
        let same_host_count = resolved_host.as_ref().map_or(0, |host| {
            sources
                .iter()
                .filter(|source| source_host(source).as_ref() == Some(host))
                .count()
        });
        if same_host_count >= 2 {
            continue;
        }
        sources.push(ResearchSource {
            url: resolved_url,
            title,
            text,
        });
        if sources.len() >= MAX_RESEARCH_SOURCES {
            break;
        }
    }
}

fn source_host(source: &ResearchSource) -> Option<String> {
    url::Url::parse(&source.url)
        .ok()
        .and_then(|value| value.host_str().map(str::to_owned))
}

fn research_hosts(sources: &[ResearchSource]) -> std::collections::HashSet<String> {
    sources.iter().filter_map(source_host).collect()
}

fn authoring_success_criteria(criteria: &[String]) -> Vec<String> {
    criteria
        .iter()
        .filter(|criterion| {
            let lower = criterion.to_ascii_lowercase();
            !lower.contains("activated privately")
                && !(lower.contains("activate")
                    && lower.contains("skill")
                    && lower.contains("privately"))
                && !lower.contains("after explicit user confirmation")
                && !(lower.contains("confirmation")
                    && (lower.contains("author") || lower.contains("activat")))
                && !lower.contains("retries the originating")
                && !lower.contains("after successful activation")
        })
        .cloned()
        .collect()
}

fn diversify_research_query(query: &str, hosts: &std::collections::HashSet<String>) -> String {
    let exclusions = hosts
        .iter()
        .take(3)
        .map(|host| format!("-site:{host}"))
        .collect::<Vec<_>>()
        .join(" ");
    format!("{query} official primary source {exclusions}")
}

async fn web_search(http: &reqwest::Client, query: &str) -> anyhow::Result<Vec<(String, String)>> {
    let mut results = Vec::new();
    for search_query in search_query_variants(query) {
        let encoded = urlencoding::encode(&search_query);
        for (endpoint, selector) in [
            (
                format!("https://search.brave.com/search?q={encoded}&source=web"),
                "div.snippet[data-type=\"web\"] a[href]",
            ),
            (
                format!("https://html.duckduckgo.com/html/?q={encoded}"),
                "a.result__a",
            ),
            (
                format!("https://lite.duckduckgo.com/lite/?q={encoded}"),
                "a.result-link",
            ),
            (
                format!("https://www.bing.com/search?q={encoded}"),
                "li.b_algo h2 a",
            ),
        ] {
            let Ok(response) = http.get(endpoint).send().await else {
                continue;
            };
            let Ok(response) = response.error_for_status() else {
                continue;
            };
            let Ok(body) = response.text().await else {
                continue;
            };
            append_search_results(&body, selector, &mut results)?;
        }
    }
    if results.len() < 2 {
        anyhow::bail!("WEB_SEARCH_RETURNED_INSUFFICIENT_RESULTS");
    }
    Ok(results)
}

fn search_query_variants(query: &str) -> Vec<String> {
    const STOP_WORDS: &[&str] = &[
        "using",
        "only",
        "research",
        "current",
        "from",
        "with",
        "into",
        "including",
        "include",
        "applicable",
        "least",
        "sources",
        "source",
        "capture",
        "record",
        "identify",
        "reconcile",
        "such",
        "that",
        "this",
        "then",
        "than",
        "their",
    ];
    let mut seen = std::collections::HashSet::new();
    let concise = query
        .split_whitespace()
        .map(|word| {
            word.trim_matches(|character: char| {
                !character.is_ascii_alphanumeric() && character != '.' && character != '-'
            })
        })
        .filter(|word| word.len() >= 3)
        .filter(|word| !STOP_WORDS.contains(&word.to_ascii_lowercase().as_str()))
        .filter(|word| seen.insert(word.to_ascii_lowercase()))
        .take(20)
        .collect::<Vec<_>>()
        .join(" ");
    if concise.is_empty() || concise == query {
        vec![query.to_string()]
    } else {
        vec![concise, query.to_string()]
    }
}

fn rank_search_candidates(candidates: &mut [(String, String)], query: &str) {
    candidates.sort_by_cached_key(|(url, title)| {
        std::cmp::Reverse(search_candidate_score(url, title, query))
    });
}

fn search_candidate_score(url: &str, title: &str, query: &str) -> usize {
    let searchable = format!("{url} {title}").to_ascii_lowercase();
    let query_terms = search_query_variants(query)
        .into_iter()
        .next()
        .unwrap_or_default()
        .split_whitespace()
        .map(str::to_ascii_lowercase)
        .filter(|term| term.len() >= 4)
        .collect::<std::collections::HashSet<_>>();
    let term_score = query_terms
        .iter()
        .filter(|term| searchable.contains(term.as_str()))
        .count()
        * 4;
    let authority_score = [
        ("docs.", 10),
        ("github.com/", 7),
        ("official", 6),
        ("standard", 5),
        ("specification", 5),
        ("schematron", 5),
        ("/rules", 4),
        ("/schema", 4),
        (".gov/", 8),
        (".eu/", 6),
        (".org/", 4),
    ]
    .iter()
    .filter_map(|(needle, score)| searchable.contains(needle).then_some(score))
    .sum::<usize>();
    term_score + authority_score
}

fn append_search_results(
    body: &str,
    selector: &str,
    results: &mut Vec<(String, String)>,
) -> anyhow::Result<()> {
    let document = Html::parse_document(body);
    let selector = Selector::parse(selector).map_err(|error| anyhow::anyhow!(error.to_string()))?;
    for link in document.select(&selector) {
        let Some(raw) = link.value().attr("href") else {
            continue;
        };
        let Some(url) = normalize_search_result_url(raw) else {
            continue;
        };
        let title = link.text().collect::<Vec<_>>().join(" ").trim().to_string();
        if !results.iter().any(|(existing, _)| existing == &url) {
            results.push((url, title));
        }
    }
    Ok(())
}

fn normalize_search_result_url(raw: &str) -> Option<String> {
    let absolute = if raw.starts_with("//") {
        format!("https:{raw}")
    } else if raw.starts_with('/') {
        format!("https://duckduckgo.com{raw}")
    } else {
        raw.to_string()
    };
    let parsed = url::Url::parse(&absolute).ok()?;
    let host = parsed.host_str()?.to_ascii_lowercase();
    let target = if let Some((_, value)) = parsed.query_pairs().find(|(key, _)| key == "uddg") {
        value.into_owned()
    } else if (host == "bing.com" || host.ends_with(".bing.com"))
        && parsed.path().starts_with("/ck/a")
    {
        let encoded = parsed
            .query_pairs()
            .find(|(key, _)| key == "u")?
            .1
            .into_owned();
        let encoded = encoded.strip_prefix("a1").unwrap_or(&encoded);
        String::from_utf8(URL_SAFE_NO_PAD.decode(encoded).ok()?).ok()?
    } else {
        absolute
    };
    let target = url::Url::parse(&target).ok()?;
    let target_host = target.host_str()?.to_ascii_lowercase();
    if target.scheme() != "https" || is_search_engine_host(&target_host) {
        return None;
    }
    Some(target.to_string())
}

fn is_search_engine_host(host: &str) -> bool {
    ["bing.com", "duckduckgo.com", "search.brave.com"]
        .iter()
        .any(|search_host| host == *search_host || host.ends_with(&format!(".{search_host}")))
}

async fn web_extract(request_timeout: Duration, url: &str) -> anyhow::Result<(String, String)> {
    let mut current = validate_public_https_url(url).await?;
    let mut redirects = 0;
    let response = loop {
        let mut builder = reqwest::Client::builder()
            .user_agent("OpenAgents skill_author/0.1")
            .timeout(request_timeout)
            .redirect(Policy::none());
        if current.pin_dns {
            builder = builder.resolve_to_addrs(&current.host, &current.addresses);
        }
        let response = builder.build()?.get(current.url.clone()).send().await?;
        if !response.status().is_redirection() {
            break response.error_for_status()?;
        }
        redirects += 1;
        if redirects > 5 {
            anyhow::bail!("too many research source redirects");
        }
        let location = response
            .headers()
            .get(LOCATION)
            .and_then(|value| value.to_str().ok())
            .ok_or_else(|| anyhow::anyhow!("redirect missing location"))?;
        current = validate_public_https_url(current.url.join(location)?.as_str()).await?;
    };
    if response
        .content_length()
        .is_some_and(|length| length > MAX_RESEARCH_BODY_BYTES as u64)
    {
        anyhow::bail!("research document exceeds size limit");
    }
    let mut response = response;
    let mut bytes = Vec::new();
    while let Some(chunk) = response.chunk().await? {
        if bytes.len() + chunk.len() > MAX_RESEARCH_BODY_BYTES {
            anyhow::bail!("research document exceeds size limit");
        }
        bytes.extend_from_slice(&chunk);
    }
    if bytes.len() < 200 {
        anyhow::bail!("document too short");
    }
    let raw = String::from_utf8_lossy(&bytes);
    let document = Html::parse_document(&raw);
    let selector = Selector::parse("body").map_err(|error| anyhow::anyhow!(error.to_string()))?;
    let text = document
        .select(&selector)
        .flat_map(|node| node.text())
        .collect::<Vec<_>>()
        .join(" ");
    let normalized = if text.trim().len() >= 200 {
        text
    } else {
        raw.into_owned()
    };
    Ok((
        current.url.to_string(),
        normalized.chars().take(12_000).collect(),
    ))
}

async fn validate_public_https_url(raw: &str) -> anyhow::Result<ValidatedResearchUrl> {
    let parsed = url::Url::parse(raw)?;
    if parsed.scheme() != "https"
        || !parsed.username().is_empty()
        || parsed.password().is_some()
        || parsed.port_or_known_default() != Some(443)
    {
        anyhow::bail!("research source must be credential-free HTTPS on port 443");
    }
    let host = parsed
        .host_str()
        .ok_or_else(|| anyhow::anyhow!("research source has no host"))?
        .trim_end_matches('.')
        .to_ascii_lowercase();
    if host == "localhost"
        || host.ends_with(".localhost")
        || host.ends_with(".local")
        || host.ends_with(".internal")
    {
        anyhow::bail!("research source host is not public");
    }
    if let Ok(ip) = host.parse::<IpAddr>() {
        if !is_public_ip(ip) {
            anyhow::bail!("research source address is not public");
        }
        return Ok(ValidatedResearchUrl {
            url: parsed,
            host,
            addresses: vec![SocketAddr::new(ip, 443)],
            pin_dns: false,
        });
    }
    let resolved = tokio::net::lookup_host((host.as_str(), 443))
        .await?
        .collect::<Vec<_>>();
    if resolved.is_empty() || resolved.iter().any(|address| !is_public_ip(address.ip())) {
        anyhow::bail!("research source resolved to a non-public address");
    }
    let addresses = resolved
        .into_iter()
        .map(|address| SocketAddr::new(address.ip(), 443))
        .collect::<Vec<_>>();
    Ok(ValidatedResearchUrl {
        url: parsed,
        host,
        addresses,
        pin_dns: true,
    })
}

fn is_public_ip(ip: IpAddr) -> bool {
    match ip {
        IpAddr::V4(value) => {
            !(value.is_private()
                || value.is_loopback()
                || value.is_link_local()
                || value.is_broadcast()
                || value.is_documentation()
                || value.is_unspecified()
                || value.is_multicast())
        }
        IpAddr::V6(value) => {
            let segments = value.segments();
            !(value.is_loopback()
                || value.is_unspecified()
                || value.is_multicast()
                || segments[0] & 0xfe00 == 0xfc00
                || segments[0] & 0xffc0 == 0xfe80
                || (segments[0] == 0x2001 && segments[1] == 0x0db8))
        }
    }
}

fn select_authoritative_sources(
    authored: &Value,
    extracted: &[ResearchSource],
) -> anyhow::Result<Vec<Value>> {
    let selected = authored
        .get("sources")
        .and_then(Value::as_array)
        .ok_or_else(|| {
            anyhow::anyhow!("skill_author returned no authoritative source selection")
        })?;
    let mut hosts = std::collections::HashSet::new();
    let mut result = Vec::new();
    for source in selected {
        let url = source
            .get("url")
            .and_then(Value::as_str)
            .unwrap_or_default();
        let authority = source
            .get("authority")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .trim();
        let Some(extracted_source) = extracted.iter().find(|candidate| candidate.url == url) else {
            anyhow::bail!("skill_author selected a source that was not extracted");
        };
        if authority.len() < 12 {
            anyhow::bail!("skill_author did not justify source authority");
        }
        let host = url::Url::parse(url)?
            .host_str()
            .ok_or_else(|| anyhow::anyhow!("selected source has no host"))?
            .to_ascii_lowercase();
        if hosts.insert(host) {
            result.push(json!({
                "url":extracted_source.url,
                "title":extracted_source.title,
                "authority":authority,
            }));
        }
    }
    if result.len() < 2 {
        anyhow::bail!("AUTHORITATIVE_RESEARCH_INSUFFICIENT: fewer than two independently selected primary sources");
    }
    Ok(result)
}

fn generalization_field<'a>(
    generalization: &'a Value,
    snake: &str,
    camel: &str,
) -> Option<&'a Value> {
    generalization
        .get(snake)
        .or_else(|| generalization.get(camel))
}

fn generalization_scenarios(generalization: &Value) -> anyhow::Result<&Vec<Value>> {
    generalization_field(
        generalization,
        "validation_scenarios",
        "validationScenarios",
    )
    .and_then(Value::as_array)
    .filter(|scenarios| scenarios.len() >= 2)
    .ok_or_else(|| anyhow::anyhow!("SKILL_GENERALIZATION_REQUIRES_TWO_SCENARIOS"))
}

fn validate_generalization_contract(generalization: &Value) -> anyhow::Result<()> {
    let capability = generalization
        .get("capability")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|value| value.len() >= 12)
        .ok_or_else(|| anyhow::anyhow!("SKILL_GENERALIZATION_CAPABILITY_INVALID"))?;
    let parameters = generalization
        .get("parameters")
        .and_then(Value::as_array)
        .filter(|values| !values.is_empty())
        .ok_or_else(|| anyhow::anyhow!("SKILL_GENERALIZATION_PARAMETERS_REQUIRED"))?;
    let mut names = std::collections::HashSet::new();
    let mut required = Vec::new();
    for parameter in parameters {
        let name = parameter
            .get("name")
            .and_then(Value::as_str)
            .filter(|name| {
                let mut chars = name.chars();
                chars.next().is_some_and(|value| value.is_ascii_lowercase())
                    && chars.all(|value| {
                        value.is_ascii_lowercase() || value.is_ascii_digit() || value == '_'
                    })
            })
            .ok_or_else(|| anyhow::anyhow!("SKILL_GENERALIZATION_PARAMETER_NAME_INVALID"))?;
        if !names.insert(name) {
            anyhow::bail!("SKILL_GENERALIZATION_PARAMETER_DUPLICATE");
        }
        if parameter
            .get("schema")
            .and_then(|schema| schema.get("type"))
            .and_then(Value::as_str)
            .is_none()
        {
            anyhow::bail!("SKILL_GENERALIZATION_PARAMETER_SCHEMA_REQUIRED");
        }
        if parameter.get("required").and_then(Value::as_bool) == Some(true) {
            required.push(name);
        }
    }
    let discovery = generalization_field(generalization, "discovery_strategy", "discoveryStrategy")
        .and_then(Value::as_array)
        .filter(|values| values.len() >= 2)
        .ok_or_else(|| anyhow::anyhow!("SKILL_GENERALIZATION_DISCOVERY_STRATEGY_REQUIRED"))?;
    if capability.is_empty()
        || discovery
            .iter()
            .any(|value| value.as_str().is_none_or(|step| step.trim().is_empty()))
    {
        anyhow::bail!("SKILL_GENERALIZATION_DISCOVERY_STRATEGY_INVALID");
    }
    let invariants = generalization
        .get("invariants")
        .and_then(Value::as_array)
        .filter(|values| !values.is_empty())
        .ok_or_else(|| anyhow::anyhow!("SKILL_GENERALIZATION_INVARIANTS_REQUIRED"))?;
    if invariants
        .iter()
        .any(|value| value.as_str().is_none_or(|item| item.trim().is_empty()))
    {
        anyhow::bail!("SKILL_GENERALIZATION_INVARIANTS_INVALID");
    }
    let mut scenario_bindings: Vec<&Value> = Vec::new();
    let instance_bindings =
        generalization_field(generalization, "instance_bindings", "instanceBindings")
            .and_then(Value::as_object)
            .ok_or_else(|| anyhow::anyhow!("SKILL_GENERALIZATION_INSTANCE_BINDINGS_REQUIRED"))?;
    if required
        .iter()
        .any(|name| !instance_bindings.contains_key(*name))
    {
        anyhow::bail!("SKILL_GENERALIZATION_INSTANCE_BINDINGS_INCOMPLETE");
    }
    for scenario in generalization_scenarios(generalization)? {
        let bindings = scenario
            .get("bindings")
            .and_then(Value::as_object)
            .ok_or_else(|| anyhow::anyhow!("SKILL_GENERALIZATION_SCENARIO_BINDINGS_REQUIRED"))?;
        if scenario
            .get("name")
            .and_then(Value::as_str)
            .is_none_or(|name| name.trim().len() < 3)
            || required.iter().any(|name| !bindings.contains_key(*name))
        {
            anyhow::bail!("SKILL_GENERALIZATION_SCENARIO_INCOMPLETE");
        }
        let bindings_value = scenario
            .get("bindings")
            .expect("bindings were validated above");
        if scenario_bindings.contains(&bindings_value) {
            anyhow::bail!("SKILL_GENERALIZATION_SCENARIOS_NOT_DISTINCT");
        }
        scenario_bindings.push(bindings_value);
    }
    Ok(())
}

fn validate_parameterized_body(generalization: &Value, body: &str) -> anyhow::Result<()> {
    let lower = body.to_ascii_lowercase();
    if !lower.contains("runtime parameter") {
        anyhow::bail!("SKILL.md must include a Runtime parameters section");
    }
    let parameters = generalization
        .get("parameters")
        .and_then(Value::as_array)
        .ok_or_else(|| anyhow::anyhow!("SKILL_GENERALIZATION_PARAMETERS_REQUIRED"))?;
    for parameter in parameters {
        let name = parameter
            .get("name")
            .and_then(Value::as_str)
            .unwrap_or_default();
        if !body.contains(&format!("`{name}`"))
            && !body.contains(&format!("{{{{{name}}}}}"))
            && !body.contains(&format!("${{{name}}}"))
        {
            anyhow::bail!("SKILL.md does not expose approved runtime parameter {name}");
        }
    }
    if let Some(bindings) =
        generalization_field(generalization, "instance_bindings", "instanceBindings")
            .and_then(Value::as_object)
    {
        for value in bindings.values().filter_map(Value::as_str) {
            if (value.starts_with("https://") || value.starts_with("http://"))
                && body.contains(value)
            {
                anyhow::bail!("SKILL.md hardcodes an instance URL instead of a runtime parameter");
            }
        }
    }
    Ok(())
}

fn validate_authored_skill(
    authored: &Value,
    success_criteria: &[String],
    generalization: &Value,
    body: &str,
    sources: &[Value],
    extracted_sources: &[ResearchSource],
) -> anyhow::Result<Value> {
    let validation = authored
        .get("validation")
        .ok_or_else(|| anyhow::anyhow!("skill_author returned no validation evidence"))?;
    let evidence = validation
        .get("criteria")
        .and_then(Value::as_array)
        .ok_or_else(|| anyhow::anyhow!("skill_author returned no criterion evidence"))?;
    if validation.get("passed").and_then(Value::as_bool) != Some(true) {
        let failed = evidence
            .iter()
            .filter(|item| item.get("passed").and_then(Value::as_bool) != Some(true))
            .filter_map(|item| item.get("criterion").and_then(Value::as_str))
            .take(3)
            .collect::<Vec<_>>()
            .join("; ");
        anyhow::bail!("skill_author validation did not pass: {failed}");
    }
    let normalized_body = normalize_evidence(body);
    for criterion in success_criteria {
        let item = evidence
            .iter()
            .find(|item| item.get("criterion").and_then(Value::as_str) == Some(criterion.as_str()));
        let Some(item) = item else {
            anyhow::bail!("skill_author omitted an approved success criterion");
        };
        let evidence = item
            .get("evidence")
            .and_then(Value::as_str)
            .map(str::trim)
            .filter(|value| value.len() >= 20)
            .ok_or_else(|| anyhow::anyhow!("skill_author supplied weak criterion evidence"))?;
        let normalized_evidence = normalize_evidence(evidence);
        let grounded_in_body = normalized_body.contains(&normalized_evidence);
        let grounded_in_selected_source = extracted_sources.iter().any(|source| {
            sources.iter().any(|selected| {
                selected.get("url").and_then(Value::as_str) == Some(source.url.as_str())
            }) && normalize_evidence(&source.text).contains(&normalized_evidence)
        });
        if item.get("passed").and_then(Value::as_bool) != Some(true)
            || (!grounded_in_body && !grounded_in_selected_source)
        {
            anyhow::bail!("skill_author supplied weak criterion evidence");
        }
    }
    for source in sources {
        let url = source
            .get("url")
            .and_then(Value::as_str)
            .unwrap_or_default();
        if url.is_empty() || !body.contains(url) {
            anyhow::bail!("SKILL.md does not cite every selected authoritative source");
        }
    }
    let generality_scenarios = validation
        .get("generality_scenarios")
        .and_then(Value::as_array)
        .ok_or_else(|| anyhow::anyhow!("skill_author returned no generality scenario evidence"))?;
    let approved_scenarios = generalization_scenarios(generalization)?;
    for approved in approved_scenarios {
        let name = approved
            .get("name")
            .and_then(Value::as_str)
            .unwrap_or_default();
        let result = generality_scenarios
            .iter()
            .find(|candidate| candidate.get("name").and_then(Value::as_str) == Some(name))
            .ok_or_else(|| {
                anyhow::anyhow!("skill_author omitted an approved generality scenario")
            })?;
        if result.get("passed").and_then(Value::as_bool) != Some(true)
            || result
                .get("evidence")
                .and_then(Value::as_str)
                .is_none_or(|value| value.trim().len() < 20)
        {
            anyhow::bail!("skill_author did not pass an approved generality scenario");
        }
    }
    validate_parameterized_body(generalization, body)?;
    Ok(json!({
        "passed":true,
        "checks":[
            "structured_output","source_count","independent_hosts",
            "authority_rationales","extracted_source_membership","body_bounds",
            "approved_success_criteria","grounded_criterion_evidence","source_citations",
            "runtime_parameterization","generality_scenarios"
        ],
        "criteria":evidence,
        "generality_scenarios":generality_scenarios,
    }))
}

fn normalize_evidence(value: &str) -> String {
    value
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ")
        .to_ascii_lowercase()
}

async fn author_with_llm(
    config: &Config,
    slug: &str,
    query: &str,
    criteria: &[String],
    generalization: &Value,
    sources: &[ResearchSource],
    validation_feedback: Option<&str>,
) -> anyhow::Result<Value> {
    let source_context = sources
        .iter()
        .map(|source| json!({
            "url":source.url,
            "host":url::Url::parse(&source.url).ok().and_then(|value| value.host_str().map(str::to_owned)),
            "title":source.title,
            "extract":source.text,
        }))
        .collect::<Vec<_>>();
    let base = config.llm_base_url.trim_end_matches('/');
    let url = if base.ends_with("/v1") {
        format!("{base}/chat/completions")
    } else {
        format!("{base}/v1/chat/completions")
    };
    let llm = reqwest::Client::builder()
        .timeout(SKILL_AUTHOR_LLM_TIMEOUT)
        .redirect(Policy::none())
        .build()?;
    let mut messages = vec![
        json!({"role":"system","content":"You are the approved OpenAgents skill_author. Write concise reusable procedural instructions grounded only in the supplied extracted sources. The approved generalization contract is authoritative: core steps must use its runtime parameter names and discovery branches, never fixed values from instance_bindings. Include a Runtime parameters section and make the procedure executable for every validation scenario. Target-specific values may appear only as clearly labeled examples, never as required constants. Select at least two primary or official sources from different supplied hosts, explain each source's authority, and cite every selected exact URL in skill_md. Evaluate every supplied success criterion verbatim. For each criterion, evidence must be an exact contiguous quotation of at least 20 characters copied from skill_md or from one of the selected supplied extracts; paraphrases are invalid. Return a passed generality_scenarios item with meaningful evidence for every approved scenario. Only set validation.passed=true when every criterion and generality scenario is satisfied. The sources array must contain exact supplied URLs from at least two distinct hosts. If the supplied sources are not authoritative enough, do not submit a skill. Do not claim activation."}),
        json!({"role":"user","content":serde_json::to_string(&json!({"approved_slug":slug,"research_query":query,"success_criteria":criteria,"generalization":generalization,"sources":source_context}))?}),
    ];
    if let Some(feedback) = validation_feedback {
        messages.push(json!({"role":"user","content":format!("The previous candidate failed strict validation: {feedback}. Repair only the candidate. Criterion evidence must be copied verbatim from skill_md or one selected extract; never cite this request as evidence.")}));
    }
    let payload = json!({
        "model":config.llm_model,"temperature":0,
        "messages":messages,
        "tools":[{"type":"function","function":{"name":"submit_skill","description":"Submit the authored skill.","parameters":{
            "type":"object","additionalProperties":false,"required":["description","skill_md","sources","validation"],
            "properties":{"description":{"type":"string","minLength":20},"skill_md":{"type":"string","minLength":120,"maxLength":18000},"sources":{"type":"array","minItems":2,"maxItems":8,"items":{"type":"object","additionalProperties":false,"required":["url","authority"],"properties":{"url":{"type":"string"},"authority":{"type":"string","minLength":12}}}},"validation":{"type":"object","additionalProperties":false,"required":["passed","criteria","generality_scenarios"],"properties":{"passed":{"type":"boolean"},"criteria":{"type":"array","minItems":1,"maxItems":8,"items":{"type":"object","additionalProperties":false,"required":["criterion","passed","evidence"],"properties":{"criterion":{"type":"string"},"passed":{"type":"boolean"},"evidence":{"type":"string","minLength":20}}}},"generality_scenarios":{"type":"array","minItems":2,"maxItems":4,"items":{"type":"object","additionalProperties":false,"required":["name","passed","evidence"],"properties":{"name":{"type":"string"},"passed":{"type":"boolean"},"evidence":{"type":"string","minLength":20}}}}}}}
        }}}],
        "tool_choice":{"type":"function","function":{"name":"submit_skill"}}
    });
    let mut response = None;
    for attempt in 0..SKILL_AUTHOR_TRANSPORT_ATTEMPTS {
        match llm
            .post(&url)
            .bearer_auth(&config.llm_api_key)
            .json(&payload)
            .send()
            .await
        {
            Ok(value) if value.status().is_success() => {
                response = Some(value.json::<Value>().await?);
                break;
            }
            Ok(value)
                if attempt + 1 < SKILL_AUTHOR_TRANSPORT_ATTEMPTS
                    && retryable_author_status(value.status().as_u16()) =>
            {
                sleep(Duration::from_millis(250 * (1 << attempt))).await;
            }
            Ok(value) => {
                return Err(value
                    .error_for_status()
                    .expect_err("non-success response")
                    .into());
            }
            Err(error)
                if attempt + 1 < SKILL_AUTHOR_TRANSPORT_ATTEMPTS
                    && (error.is_timeout() || error.is_connect()) =>
            {
                sleep(Duration::from_millis(250 * (1 << attempt))).await;
            }
            Err(error) => return Err(error.into()),
        }
    }
    let response = response.ok_or_else(|| anyhow::anyhow!("SKILL_AUTHOR_TRANSPORT_EXHAUSTED"))?;
    let arguments = response
        .pointer("/choices/0/message/tool_calls/0/function/arguments")
        .and_then(Value::as_str)
        .ok_or_else(|| anyhow::anyhow!("skill_author LLM returned no submit_skill call"))?;
    Ok(serde_json::from_str(arguments)?)
}

fn retryable_author_status(status: u16) -> bool {
    status == 429 || (500..=599).contains(&status)
}

async fn audit_acceptance_criteria(
    config: &Config,
    workspace: &Path,
    base_sha: &str,
    commit_sha: &str,
    tests: &[TestEvidence],
    engine_stdout: &str,
    criteria: &[String],
) -> anyhow::Result<Vec<AcceptanceCriterionEvidence>> {
    if criteria.is_empty() {
        anyhow::bail!("ACCEPTANCE_CRITERIA_REQUIRED");
    }
    if criteria.len() > 32 || criteria.iter().any(|criterion| criterion.trim().is_empty()) {
        anyhow::bail!("ACCEPTANCE_CRITERIA_INVALID");
    }
    let diff = bounded_git_diff(workspace, base_sha, commit_sha, None)?;
    let diff = std::str::from_utf8(&diff)?;
    let (diff, diff_truncated) = bounded_utf8(diff, MAX_ACCEPTANCE_DIFF_BYTES);
    if detect_secret(diff).is_some() {
        anyhow::bail!("ACCEPTANCE_DIFF_SECRET_REJECTED");
    }
    let mut test_logs = String::new();
    for test in tests {
        let Some(uri) = test.output_uri.as_deref() else {
            continue;
        };
        let Some(path) = uri.strip_prefix("file://") else {
            continue;
        };
        let remaining = MAX_ACCEPTANCE_TEST_BYTES.saturating_sub(test_logs.len());
        if remaining == 0 {
            break;
        }
        let raw = fs::read_to_string(path).await.unwrap_or_default();
        let (bounded, _) = bounded_utf8(&raw, remaining);
        test_logs.push_str(&format!("\n$ {}\n{}", test.command, bounded));
    }
    if detect_secret(&test_logs).is_some() {
        anyhow::bail!("ACCEPTANCE_TEST_LOG_SECRET_REJECTED");
    }
    let execution_report = final_execution_report(engine_stdout)?;
    if detect_secret(&execution_report).is_some() {
        anyhow::bail!("ACCEPTANCE_EXECUTION_REPORT_SECRET_REJECTED");
    }

    let base = config.llm_base_url.trim_end_matches('/');
    let url = if base.ends_with("/v1") {
        format!("{base}/chat/completions")
    } else {
        format!("{base}/v1/chat/completions")
    };
    let payload = json!({
        "model":config.llm_model,
        "temperature":0,
        "messages":[
            {"role":"system","content":"You are the OpenAgents acceptance auditor. Evaluate every supplied criterion verbatim using only the supplied committed Git diff, declared test logs, and terminal OpenCode execution report. For each criterion, quote exact contiguous excerpts from the named sources as evidence. When one excerpt cannot prove every clause, join multiple exact excerpts with a line containing only ---; every excerpt will be checked independently. Use execution_report only for process evidence that cannot exist in a diff or test log. Never infer evidence that is absent, never claim a test that was not run, and set passed=false when the supplied corpus does not prove the criterion. Call submit_acceptance_evidence and do not answer in prose."},
            {"role":"user","content":serde_json::to_string(&json!({
                "criteria":criteria,
                "sources":{
                    "git_diff":{"content":diff,"truncated":diff_truncated},
                    "test_logs":{"content":test_logs,"truncated":test_logs.len() >= MAX_ACCEPTANCE_TEST_BYTES},
                    "execution_report":{"content":execution_report,"truncated":false}
                }
            }))?}
        ],
        "tools":[{"type":"function","function":{
            "name":"submit_acceptance_evidence",
            "description":"Submit grounded evidence for every acceptance criterion.",
            "parameters":{
                "type":"object","additionalProperties":false,"required":["criteria"],
                "properties":{"criteria":{
                    "type":"array","minItems":criteria.len(),"maxItems":criteria.len(),
                    "items":{"type":"object","additionalProperties":false,
                        "required":["criterion","passed","evidence","sources"],
                        "properties":{
                            "criterion":{"type":"string","enum":criteria},
                            "passed":{"type":"boolean"},
                            "evidence":{"type":"string"},
                            "sources":{"type":"array","minItems":1,"uniqueItems":true,"items":{"type":"string","enum":["git_diff","test_logs","execution_report"]}}
                        }
                    }
                }}
            }
        }}],
        "tool_choice":{"type":"function","function":{"name":"submit_acceptance_evidence"}}
    });
    let response = reqwest::Client::builder()
        .timeout(ACCEPTANCE_AUDIT_TIMEOUT)
        .redirect(Policy::none())
        .build()?
        .post(url)
        .bearer_auth(&config.llm_api_key)
        .json(&payload)
        .send()
        .await?
        .error_for_status()?
        .json::<Value>()
        .await?;
    let arguments = response
        .pointer("/choices/0/message/tool_calls/0/function/arguments")
        .and_then(Value::as_str)
        .ok_or_else(|| anyhow::anyhow!("ACCEPTANCE_AUDIT_TOOL_CALL_MISSING"))?;
    let submitted: Value = serde_json::from_str(arguments)?;
    let items = submitted
        .get("criteria")
        .and_then(Value::as_array)
        .ok_or_else(|| anyhow::anyhow!("ACCEPTANCE_AUDIT_RESULT_INVALID"))?;
    if items.len() != criteria.len() {
        anyhow::bail!("ACCEPTANCE_AUDIT_CRITERIA_MISMATCH");
    }
    let source_text = [
        ("git_diff", diff),
        ("test_logs", test_logs.as_str()),
        ("execution_report", execution_report.as_str()),
    ]
    .into_iter()
    .collect::<std::collections::HashMap<_, _>>();
    let mut by_criterion = std::collections::HashMap::new();
    for item in items {
        let criterion = item
            .get("criterion")
            .and_then(Value::as_str)
            .ok_or_else(|| anyhow::anyhow!("ACCEPTANCE_AUDIT_CRITERION_INVALID"))?;
        if !criteria.iter().any(|expected| expected == criterion)
            || by_criterion.contains_key(criterion)
        {
            anyhow::bail!("ACCEPTANCE_AUDIT_CRITERIA_MISMATCH");
        }
        let evidence = item
            .get("evidence")
            .and_then(Value::as_str)
            .map(str::trim)
            .filter(|value| value.len() >= 8)
            .ok_or_else(|| anyhow::anyhow!("ACCEPTANCE_EVIDENCE_MISSING: {criterion}"))?;
        let sources = item
            .get("sources")
            .and_then(Value::as_array)
            .ok_or_else(|| anyhow::anyhow!("ACCEPTANCE_EVIDENCE_SOURCE_MISSING: {criterion}"))?
            .iter()
            .map(|source| {
                source
                    .as_str()
                    .map(str::to_owned)
                    .ok_or_else(|| anyhow::anyhow!("ACCEPTANCE_EVIDENCE_SOURCE_INVALID"))
            })
            .collect::<anyhow::Result<Vec<_>>>()?;
        let grounded = evidence_is_grounded(evidence, &sources, &source_text);
        if item.get("passed").and_then(Value::as_bool) != Some(true) || !grounded {
            anyhow::bail!("ACCEPTANCE_CRITERION_UNPROVEN: {criterion}");
        }
        by_criterion.insert(
            criterion.to_string(),
            AcceptanceCriterionEvidence {
                criterion: criterion.to_string(),
                passed: true,
                evidence: evidence.to_string(),
                sources,
            },
        );
    }
    criteria
        .iter()
        .map(|criterion| {
            by_criterion
                .remove(criterion)
                .ok_or_else(|| anyhow::anyhow!("ACCEPTANCE_AUDIT_CRITERIA_MISMATCH"))
        })
        .collect()
}

fn evidence_is_grounded(
    evidence: &str,
    sources: &[String],
    source_text: &std::collections::HashMap<&str, &str>,
) -> bool {
    let excerpts = evidence
        .split("\n---\n")
        .map(str::trim)
        .filter(|excerpt| excerpt.len() >= 8)
        .collect::<Vec<_>>();
    !excerpts.is_empty()
        && excerpts.iter().all(|excerpt| {
            sources.iter().any(|source| {
                source_text
                    .get(source.as_str())
                    .is_some_and(|content| content.contains(excerpt))
            })
        })
}

fn final_execution_report(stdout: &str) -> anyhow::Result<String> {
    let report = stdout.lines().rev().find_map(|line| {
        let event = serde_json::from_str::<Value>(line).ok()?;
        if event.get("type").and_then(Value::as_str) != Some("result") {
            return None;
        }
        event
            .get("result")
            .and_then(Value::as_str)
            .map(str::trim)
            .filter(|value| !value.is_empty())
            .map(str::to_owned)
    });
    let report = report.ok_or_else(|| anyhow::anyhow!("ACCEPTANCE_EXECUTION_REPORT_MISSING"))?;
    let (report, _) = bounded_utf8(&report, MAX_ACCEPTANCE_REPORT_BYTES);
    Ok(report.to_string())
}

fn parse_qa_requirements(
    inputs: &Value,
    commands: &[String],
) -> anyhow::Result<Vec<QaRequirement>> {
    let requirements = inputs
        .get("qa_requirements")
        .and_then(Value::as_array)
        .ok_or_else(|| anyhow::anyhow!("QA_REQUIREMENTS_MISSING"))?
        .iter()
        .map(|value| serde_json::from_value::<QaRequirement>(value.clone()))
        .collect::<Result<Vec<_>, _>>()
        .context("QA_REQUIREMENTS_INVALID")?;
    if requirements.is_empty()
        || requirements.len() != commands.len()
        || requirements
            .iter()
            .zip(commands)
            .any(|(requirement, command)| requirement.command != *command)
    {
        anyhow::bail!("QA_COMMAND_POLICY_MISMATCH");
    }
    Ok(requirements)
}

fn qa_evidence(
    requirements: &[QaRequirement],
    tests: &[TestEvidence],
) -> anyhow::Result<Vec<QaEvidence>> {
    if requirements.len() != tests.len() {
        anyhow::bail!("QA_EVIDENCE_INCOMPLETE");
    }
    requirements
        .iter()
        .zip(tests)
        .map(|(requirement, test)| {
            if requirement.command != test.command {
                anyhow::bail!("QA_EVIDENCE_COMMAND_MISMATCH");
            }
            Ok(QaEvidence {
                surface: requirement.surface,
                kind: requirement.kind,
                command: test.command.clone(),
                exit_status: test.exit_status,
                passed: test.exit_status == 0 && test.failed == 0,
                output_uri: test.output_uri.clone(),
            })
        })
        .collect()
}

fn validate_changed_file_policy(artifact: &WorkerArtifact, inputs: &Value) -> anyhow::Result<()> {
    let files = string_array(&artifact.metadata, "files")?;
    if files.is_empty() {
        anyhow::bail!("CHANGED_FILES_EVIDENCE_EMPTY");
    }
    let protected_paths = string_array(inputs, "protected_paths")?;
    if files.iter().any(|file| {
        protected_paths
            .iter()
            .any(|path| file == path || file.starts_with(&format!("{path}/")))
    }) {
        anyhow::bail!("PROTECTED_PATH_CHANGED");
    }
    let declared = string_array(inputs, "impact_surfaces")?;
    let surface_paths = inputs
        .get("surface_paths")
        .and_then(Value::as_object)
        .ok_or_else(|| anyhow::anyhow!("SURFACE_PATH_POLICY_MISSING"))?;
    for file in files {
        let actual = surface_paths
            .iter()
            .filter(|(_, prefixes)| {
                prefixes.as_array().is_some_and(|prefixes| {
                    prefixes.iter().filter_map(Value::as_str).any(|prefix| {
                        prefix == "." || file == prefix || file.starts_with(&format!("{prefix}/"))
                    })
                })
            })
            .map(|(surface, _)| surface.as_str())
            .collect::<Vec<_>>();
        if actual.is_empty() {
            anyhow::bail!("CHANGED_FILE_SURFACE_UNCLASSIFIED: {file}");
        }
        if actual
            .iter()
            .any(|surface| !declared.iter().any(|value| value == surface))
        {
            anyhow::bail!("DECLARED_IMPACT_INCOMPLETE: {file}");
        }
    }
    Ok(())
}

async fn canonical_repository(
    inputs: &Value,
    allowed: &[PathBuf],
    remote_url: &str,
    remote: &str,
    git_timeout: Duration,
) -> anyhow::Result<PathBuf> {
    let requested = PathBuf::from(string(inputs, "repository")?);
    if !requested.is_absolute()
        || requested
            .components()
            .any(|component| matches!(component, Component::ParentDir))
    {
        anyhow::bail!("REPOSITORY_PATH_ESCAPE");
    }
    let mut allowed_roots = Vec::new();
    for root in allowed {
        if let Ok(root) = fs::canonicalize(root).await {
            allowed_roots.push(root);
        }
    }
    if !fs::try_exists(&requested).await? {
        let parent = requested
            .parent()
            .ok_or_else(|| anyhow::anyhow!("REPOSITORY_PARENT_MISSING"))?;
        let canonical_parent = fs::canonicalize(parent)
            .await
            .context("canonicalize repository parent")?;
        if !allowed_roots
            .iter()
            .any(|root| canonical_parent.starts_with(root))
        {
            anyhow::bail!("REPOSITORY_PARENT_NOT_MANAGED");
        }
        checked_with_timeout(
            Command::new("git")
                .args(["clone", "--origin", remote, "--no-checkout", remote_url])
                .arg(&requested),
            "git clone managed repository",
            git_timeout,
        )
        .await?;
    }
    let canonical = fs::canonicalize(&requested)
        .await
        .context("canonicalize repository")?;
    let canonical_permitted = allowed_roots.iter().any(|root| canonical.starts_with(root));
    if !canonical_permitted {
        anyhow::bail!("REPOSITORY_NOT_MANAGED");
    }
    if !fs::try_exists(canonical.join(".git")).await? {
        anyhow::bail!("REPOSITORY_NOT_GIT");
    }
    Ok(canonical)
}

async fn verify_repository_remote(
    repository: &Path,
    remote: &str,
    expected_url: &str,
) -> anyhow::Result<()> {
    let actual = output_text(
        Command::new("git")
            .arg("-C")
            .arg(repository)
            .args(["remote", "get-url", remote]),
        "git remote URL",
    )
    .await?;
    if actual.trim() != expected_url.trim() {
        anyhow::bail!("REPOSITORY_REMOTE_POLICY_MISMATCH");
    }
    Ok(())
}

struct WorktreeSpec<'a> {
    repository: &'a Path,
    managed_root: &'a Path,
    organization_id: Uuid,
    repository_name: &'a str,
    ticket_id: Uuid,
    plan_id: Uuid,
    task_id: Uuid,
    base_ref: &'a str,
    remote: &'a str,
    timeout: Duration,
}

async fn create_worktree(spec: WorktreeSpec<'_>) -> anyhow::Result<(PathBuf, PathBuf, String)> {
    let repository_folder = safe_workspace_name(spec.repository_name)?;
    let workspace_root = spec
        .managed_root
        .join(spec.organization_id.to_string())
        .join(spec.plan_id.to_string());
    let workspace = workspace_root.join(&repository_folder);
    fs::create_dir_all(&workspace_root).await?;
    let branch = format!(
        "openos/{}-{}-{}",
        spec.ticket_id,
        repository_folder.to_ascii_lowercase(),
        &spec.task_id.to_string()[..8]
    );
    checked_with_timeout(
        Command::new("git").arg("-C").arg(spec.repository).args([
            "fetch",
            "--prune",
            spec.remote,
            spec.base_ref,
        ]),
        "git fetch base ref",
        spec.timeout,
    )
    .await?;
    let remote_ref = format!("{}/{}", spec.remote, spec.base_ref);
    let base_sha = output_text(
        Command::new("git")
            .arg("-C")
            .arg(spec.repository)
            .args(["rev-parse", &remote_ref]),
        "git base ref",
    )
    .await?;
    if fs::try_exists(&workspace).await? {
        let actual_branch = output_text(
            Command::new("git")
                .arg("-C")
                .arg(&workspace)
                .args(["branch", "--show-current"]),
            "existing worktree branch",
        )
        .await?;
        if actual_branch != branch {
            anyhow::bail!("WORKSPACE_BRANCH_MISMATCH");
        }
    } else {
        checked(
            Command::new("git")
                .arg("-C")
                .arg(spec.repository)
                .args(["worktree", "prune"]),
            "git worktree prune",
        )
        .await?;
        let branch_exists = checked_output(
            Command::new("git")
                .arg("-C")
                .arg(spec.repository)
                .args(["show-ref", "--verify", "--quiet"])
                .arg(format!("refs/heads/{branch}")),
        )
        .await?
        .success();
        let mut command = Command::new("git");
        command
            .arg("-C")
            .arg(spec.repository)
            .args(["worktree", "add"]);
        if branch_exists {
            command.arg(&workspace).arg(&branch);
        } else {
            command
                .arg("-b")
                .arg(&branch)
                .arg(&workspace)
                .arg(&remote_ref);
        }
        checked(&mut command, "git worktree add").await?;
    }
    let canonical_root = fs::canonicalize(&workspace_root).await?;
    let canonical_workspace = fs::canonicalize(&workspace).await?;
    if !canonical_workspace.starts_with(&canonical_root) {
        anyhow::bail!("WORKSPACE_PATH_ESCAPE");
    }
    Ok((canonical_root, canonical_workspace, base_sha))
}

fn safe_workspace_name(value: &str) -> anyhow::Result<String> {
    let mut name = String::with_capacity(value.len().min(64));
    let mut separator = false;
    for character in value.trim().chars() {
        if name.len() >= 64 {
            break;
        }
        if character.is_ascii_alphanumeric() || matches!(character, '.' | '_' | '-') {
            name.push(character);
            separator = false;
        } else if !separator && !name.is_empty() {
            name.push('-');
            separator = true;
        }
    }
    let name = name.trim_matches(['.', '-', '_']).to_string();
    if name.is_empty() || name == "." || name == ".." {
        anyhow::bail!("REPOSITORY_WORKSPACE_NAME_INVALID");
    }
    Ok(name)
}

struct EngineOutput {
    exit_status: i32,
    stdout: String,
    stderr: String,
    session_id: Option<String>,
    cognitive_events: Vec<Value>,
}

fn engineering_cognitive_observations(
    job: &WorkerJob,
    run_id: Uuid,
    loaded_skills: &[LoadedSkill],
    events: &[Value],
) -> Vec<CognitiveObservation> {
    let evidence = || {
        vec![CognitiveEvidenceReference {
            r#type: "run".into(),
            r#ref: run_id.to_string(),
            summary: Some("Verified managed OpenCode execution".into()),
            protected: false,
        }]
    };
    let mut observations = vec![CognitiveObservation {
        id: Uuid::new_v4(),
        organization_id: job.organization_id,
        correlation_id: job.correlation_id,
        run_id: Some(run_id.to_string()),
        session_id: None,
        agent_id: None,
        revision_id: None,
        producer: "openagents".into(),
        sequence: 0,
        scope: CognitiveScope {
            r#type: CognitiveScopeType::Organization,
            id: job.organization_id,
        },
        r#type: CognitiveObservationType::StrategySelected,
        confidence: 1.0,
        expected: None,
        observed: Some(json!({
            "key":"worker.strategy", "value":"managed_opencode", "jobType":job.job_type,
            "loadedSkills":loaded_skills.iter().map(|skill| &skill.reference.id).collect::<Vec<_>>()
        })),
        evidence: evidence(),
        risk: CognitiveRisk::None,
        observed_at: Utc::now(),
        idempotency_key: format!("openagents:{run_id}:strategy"),
    }];
    for (index, event) in events.iter().take(50).enumerate() {
        let event_type = match event.get("type").and_then(Value::as_str) {
            Some("hypothesis_updated") => CognitiveObservationType::HypothesisUpdated,
            Some("expected_observed_mismatch") => {
                CognitiveObservationType::ExpectedObservedMismatch
            }
            _ => continue,
        };
        observations.push(CognitiveObservation {
            id: Uuid::new_v4(),
            organization_id: job.organization_id,
            correlation_id: job.correlation_id,
            run_id: Some(run_id.to_string()),
            session_id: None,
            agent_id: None,
            revision_id: None,
            producer: "opencode-hacn".into(),
            sequence: (index + 1) as u32,
            scope: CognitiveScope {
                r#type: CognitiveScopeType::Organization,
                id: job.organization_id,
            },
            r#type: event_type,
            confidence: event
                .get("confidence")
                .and_then(Value::as_f64)
                .unwrap_or(0.5)
                .clamp(0.0, 1.0),
            expected: event.get("expected").cloned(),
            observed: event.get("observed").cloned(),
            evidence: evidence(),
            risk: CognitiveRisk::None,
            observed_at: event
                .get("observedAt")
                .and_then(Value::as_str)
                .and_then(|value| value.parse().ok())
                .unwrap_or_else(Utc::now),
            idempotency_key: format!("openagents:{run_id}:hacn:{index}"),
        });
    }
    observations
}

async fn run_opencode(
    config: &Config,
    job: &WorkerJob,
    run_id: Uuid,
    workspace: &Path,
    prompt: &str,
    store: &RunStore,
) -> anyhow::Result<EngineOutput> {
    let cognitive_event_path = workspace
        .parent()
        .expect("run parent")
        .join("hacn-cognitive.ndjson");
    let workspace_id = job
        .inputs
        .get("inputs")
        .unwrap_or(&job.inputs)
        .get("repository")
        .and_then(Value::as_str)
        .unwrap_or_else(|| workspace.to_str().unwrap_or("managed"));
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
        .env("SHELL", &config.shell_binary)
        .env("PYTHONDONTWRITEBYTECODE", "1")
        .env("OPENCODE_INVOKED_BY", "openagents-rust")
        .env("OPENCODE_HACN", "1")
        .env("OPENOS_MANAGED_RUNTIME", "1")
        .env("OPENOS_ORGANIZATION_ID", job.organization_id.to_string())
        .env("OPENOS_WORKSPACE_ID", workspace_id)
        .env("OPENOS_SESSION_ID", run_id.to_string())
        .env("OPENOS_HACN_EVENT_FILE", &cognitive_event_path)
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
    let cognitive_events = fs::read_to_string(&cognitive_event_path)
        .await
        .ok()
        .into_iter()
        .flat_map(|content| {
            content
                .lines()
                .filter_map(|line| serde_json::from_str::<Value>(line).ok())
                .collect::<Vec<_>>()
        })
        .collect();
    Ok(EngineOutput {
        exit_status: status.code().unwrap_or(-1),
        stdout,
        stderr,
        session_id,
        cognitive_events,
    })
}

async fn run_tests(
    workspace: &Path,
    commands: &[String],
    run_id: Uuid,
    store: &RunStore,
    qa_timeout: Duration,
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
        let mut process = Command::new("sh");
        process
            .args(["-lc", command])
            .current_dir(workspace)
            .env("PYTHONDONTWRITEBYTECODE", "1");
        let output = match timeout(qa_timeout, process.output()).await {
            Ok(output) => output?,
            Err(_) => {
                store
                    .update(
                        run_id,
                        RunStatus::Failed,
                        "test.failed",
                        json!({"command":command,"error":"QA_COMMAND_TIMEOUT"}),
                    )
                    .await;
                anyhow::bail!("QA_COMMAND_TIMEOUT: {command}");
            }
        };
        let path = workspace
            .parent()
            .expect("run root")
            .join(format!("{run_id}-test-{}.log", evidence.len() + 1));
        let mut data = output.stdout;
        data.extend_from_slice(&output.stderr);
        if detect_secret(&String::from_utf8_lossy(&data)).is_some() {
            store
                .update(
                    run_id,
                    RunStatus::Failed,
                    "test.failed",
                    json!({"command":command,"error":"QA_SECRET_LEAK_REJECTED"}),
                )
                .await;
            anyhow::bail!("QA_SECRET_LEAK_REJECTED");
        }
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
    sign_commit: bool,
    git_timeout: Duration,
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
        checked_with_timeout(
            Command::new("git")
                .arg("-C")
                .arg(workspace)
                .args([
                    "-c",
                    "user.name=OpenAgents",
                    "-c",
                    "user.email=openagents@openos.local",
                    "-c",
                    if sign_commit {
                        "commit.gpgsign=true"
                    } else {
                        "commit.gpgsign=false"
                    },
                    "commit",
                    "-m",
                ])
                .arg(format!(
                    "OpenTicket {ticket_id}: validated worker run {run_id}"
                )),
            "git commit",
            git_timeout,
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
    checked(
        Command::new("git")
            .arg("-C")
            .arg(workspace)
            .args(["verify-commit", "HEAD"]),
        "git verify signed commit",
    )
    .await?;
    Ok(GitEvidence {
        repository: repository.display().to_string(),
        worktree: workspace.display().to_string(),
        branch,
        commit_sha: sha,
        clean: status.is_empty(),
        pushed: false,
        remote: None,
    })
}

async fn push_branch(
    workspace: &Path,
    remote: &str,
    branch: &str,
    git_timeout: Duration,
) -> anyhow::Result<()> {
    if remote.trim().is_empty() || branch.trim().is_empty() {
        anyhow::bail!("GIT_PUSH_POLICY_INVALID");
    }
    checked_with_timeout(
        Command::new("git").arg("-C").arg(workspace).args([
            "push",
            "--set-upstream",
            remote,
            branch,
        ]),
        "git push branch",
        git_timeout,
    )
    .await
}

struct PullRequestSpec<'a> {
    config: &'a Config,
    workspace: &'a Path,
    provider: &'a str,
    external_repository_id: &'a str,
    base_branch: &'a str,
    head_branch: &'a str,
    ticket_id: Uuid,
    run_id: Uuid,
    qa: &'a [QaEvidence],
}

async fn create_or_read_pull_request(
    spec: PullRequestSpec<'_>,
) -> anyhow::Result<PullRequestEvidence> {
    if spec.provider != "github" {
        anyhow::bail!("GIT_PROVIDER_UNSUPPORTED: {}", spec.provider);
    }
    if let Some(existing) = read_pull_request(
        spec.config,
        spec.workspace,
        spec.external_repository_id,
        spec.head_branch,
    )
    .await?
    {
        return Ok(existing);
    }
    let qa_summary = spec
        .qa
        .iter()
        .map(|evidence| {
            format!(
                "- [x] {:?}/{:?}: `{}`",
                evidence.surface, evidence.kind, evidence.command
            )
        })
        .collect::<Vec<_>>()
        .join("\n");
    let title = format!("fix: OpenTicket {}", spec.ticket_id);
    let body = format!(
        "## OpenOS delivery\n\nTicket: `{}`\nRun: `{}`\n\n## Verified QA\n{qa_summary}\n\nThis pull request was created as a draft and requires human review. OpenOS does not merge automatically.",
        spec.ticket_id, spec.run_id
    );
    let mut command = Command::new(&spec.config.git_provider_binary);
    command
        .current_dir(spec.workspace)
        .args([
            "pr",
            "create",
            "--repo",
            spec.external_repository_id,
            "--draft",
            "--base",
            spec.base_branch,
            "--head",
            spec.head_branch,
        ])
        .arg("--title")
        .arg(title)
        .arg("--body")
        .arg(body);
    let output = timeout(spec.config.git_timeout, command.output())
        .await
        .map_err(|_| anyhow::anyhow!("pull request create: timeout"))??;
    if !output.status.success() {
        anyhow::bail!(
            "pull request create: {}",
            sanitize(&String::from_utf8_lossy(&output.stderr))
        );
    }
    read_pull_request(
        spec.config,
        spec.workspace,
        spec.external_repository_id,
        spec.head_branch,
    )
    .await?
    .ok_or_else(|| anyhow::anyhow!("PULL_REQUEST_EVIDENCE_MISSING"))
}

async fn read_pull_request(
    config: &Config,
    workspace: &Path,
    external_repository_id: &str,
    head_branch: &str,
) -> anyhow::Result<Option<PullRequestEvidence>> {
    let mut command = Command::new(&config.git_provider_binary);
    command.current_dir(workspace).args([
        "pr",
        "view",
        head_branch,
        "--repo",
        external_repository_id,
        "--json",
        "url,number,baseRefName,headRefName,isDraft,state",
    ]);
    let output = timeout(config.git_timeout, command.output())
        .await
        .map_err(|_| anyhow::anyhow!("pull request read: timeout"))??;
    if !output.status.success() {
        return Ok(None);
    }
    let value: Value = serde_json::from_slice(&output.stdout).context("pull request JSON")?;
    let url = value
        .get("url")
        .and_then(Value::as_str)
        .filter(|value| value.starts_with("https://"))
        .ok_or_else(|| anyhow::anyhow!("PULL_REQUEST_URL_INVALID"))?;
    let expected_url_prefix = format!("https://github.com/{external_repository_id}/pull/");
    if !url.starts_with(&expected_url_prefix) {
        anyhow::bail!("PULL_REQUEST_REPOSITORY_MISMATCH");
    }
    let base_branch = value
        .get("baseRefName")
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .ok_or_else(|| anyhow::anyhow!("PULL_REQUEST_BASE_MISSING"))?;
    let actual_head = value
        .get("headRefName")
        .and_then(Value::as_str)
        .filter(|value| *value == head_branch)
        .ok_or_else(|| anyhow::anyhow!("PULL_REQUEST_HEAD_MISMATCH"))?;
    Ok(Some(PullRequestEvidence {
        provider: "github".into(),
        url: url.into(),
        number: value.get("number").and_then(Value::as_u64),
        base_branch: base_branch.into(),
        head_branch: actual_head.into(),
        draft: value
            .get("isDraft")
            .and_then(Value::as_bool)
            .unwrap_or(false),
        review_required: true,
        status: value
            .get("state")
            .and_then(Value::as_str)
            .unwrap_or("UNKNOWN")
            .to_ascii_lowercase(),
    }))
}

async fn persist_events(root: &Path, run_id: Uuid, data: &str) -> anyhow::Result<WorkerArtifact> {
    let path = root.join(format!("{run_id}-opencode-events.jsonl"));
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

async fn checked_with_timeout(
    command: &mut Command,
    name: &str,
    duration: Duration,
) -> anyhow::Result<()> {
    let output = timeout(duration, command.output())
        .await
        .map_err(|_| anyhow::anyhow!("{name}: timeout"))??;
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

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum SecretKind {
    ApiKey,
    BearerToken,
    PemPrivateKey,
    ProviderToken,
}

impl SecretKind {
    fn label(self) -> &'static str {
        match self {
            Self::ApiKey => "api_key",
            Self::BearerToken => "bearer_token",
            Self::PemPrivateKey => "pem_private_key",
            Self::ProviderToken => "provider_token",
        }
    }
}

fn contains_secret(value: &str) -> bool {
    detect_secret(value).is_some()
}

fn detect_secret(value: &str) -> Option<SecretKind> {
    let lower = value.to_ascii_lowercase();
    if [
        "-----begin private key-----",
        "-----begin rsa private key-----",
        "-----begin ec private key-----",
        "-----begin openssh private key-----",
    ]
    .iter()
    .any(|marker| lower.contains(marker))
    {
        return Some(SecretKind::PemPrivateKey);
    }
    if ["sk-", "ghp_", "github_pat_", "xoxb-", "xoxp-"]
        .iter()
        .any(|prefix| has_prefixed_token(&lower, prefix, 16))
    {
        return Some(SecretKind::ProviderToken);
    }
    if ["authorization: bearer", "authorization=bearer"]
        .iter()
        .any(|marker| has_assigned_credential(&lower, marker, 20))
    {
        return Some(SecretKind::BearerToken);
    }
    if ["api_key=", "api-key=", "apikey="]
        .iter()
        .any(|marker| has_assigned_credential(&lower, marker, 16))
    {
        return Some(SecretKind::ApiKey);
    }
    None
}

fn has_prefixed_token(value: &str, prefix: &str, min_suffix: usize) -> bool {
    value.match_indices(prefix).any(|(index, _)| {
        if index > 0 {
            let previous = value[..index].chars().next_back().unwrap_or_default();
            if previous.is_ascii_alphanumeric() || matches!(previous, '_' | '-') {
                return false;
            }
        }
        let suffix = &value[index + prefix.len()..];
        credential_token(suffix).chars().count() >= min_suffix
    })
}

fn has_assigned_credential(value: &str, marker: &str, min_length: usize) -> bool {
    value.match_indices(marker).any(|(index, _)| {
        let suffix = &value[index + marker.len()..];
        let token = credential_token(suffix);
        token.chars().count() >= min_length && !is_placeholder(token)
    })
}

fn credential_token(value: &str) -> &str {
    let value = value.trim_start_matches(|character: char| {
        character.is_ascii_whitespace() || matches!(character, '\\' | '\'' | '"' | ':' | '=')
    });
    let end = value
        .find(|character: char| {
            !(character.is_ascii_alphanumeric() || matches!(character, '-' | '_' | '.'))
        })
        .unwrap_or(value.len());
    &value[..end]
}

fn is_placeholder(value: &str) -> bool {
    [
        "changeme",
        "dummy",
        "example",
        "none",
        "null",
        "placeholder",
        "redacted",
        "replace",
        "test",
        "your",
    ]
    .iter()
    .any(|prefix| value.starts_with(prefix))
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
        assert!(canonical_repository(
            &value,
            &[temp.path().into()],
            "https://example.test/repo.git",
            "origin",
            Duration::from_secs(1)
        )
        .await
        .is_err());
    }

    #[tokio::test]
    async fn loads_only_the_exact_versioned_bundled_skill_content() {
        let temp = tempfile::tempdir().unwrap();
        let directory = temp.path().join("open-code");
        std::fs::create_dir(&directory).unwrap();
        let body = "# OpenCode\nUse the isolated worktree and run declared tests.";
        std::fs::write(
            directory.join("SKILL.md"),
            format!("---\nname: open-code\nversion: 7\n---\n{body}\n"),
        )
        .unwrap();
        let reference = SkillReference {
            id: "open-code".into(),
            version: "7".into(),
            sha256: format!("{:x}", Sha256::digest(body.as_bytes())),
            source: SkillSource::Bundled,
        };
        let loaded = load_bundled_skills(temp.path(), &[&reference])
            .await
            .unwrap();
        assert_eq!(loaded.len(), 1);
        assert_eq!(loaded[0].reference, reference);
        assert_eq!(loaded[0].body, body);

        let altered = SkillReference {
            sha256: "0".repeat(64),
            ..reference
        };
        assert!(load_bundled_skills(temp.path(), &[&altered]).await.is_err());
    }

    #[test]
    fn strips_secret_bearing_stderr() {
        assert_eq!(
            sanitize("ok\nAuthorization: Bearer abc\nsk-secret"),
            "ok\n[redacted]\n[redacted]"
        );
    }

    #[test]
    fn credential_guard_ignores_source_identifiers_and_placeholders() {
        for value in [
            "private_key",
            "document the sk- provider prefix",
            "API_KEY=",
            "API_KEY=$OPENAI_API_KEY",
            "API_KEY=placeholder-value-long-enough",
            "Authorization: Bearer <token>",
            "branch openos/task-6899b97b-1fa3-4d0d-b7ed-c68386db6bd4",
            "task_key=acceptance-task-1234567890abcdef",
        ] {
            assert_eq!(detect_secret(value), None, "false positive for {value}");
        }
    }

    #[test]
    fn acceptance_uses_only_the_terminal_opencode_report() {
        let stdout = [
            json!({"type":"assistant","message":{"content":[{"type":"text","text":"intermediate claim"}]}}),
            json!({"type":"result","subtype":"success","result":"Base main at abc123 was clean before implementation."}),
        ]
        .into_iter()
        .map(|event| event.to_string())
        .collect::<Vec<_>>()
        .join("\n");

        assert_eq!(
            final_execution_report(&stdout).unwrap(),
            "Base main at abc123 was clean before implementation."
        );
        assert!(final_execution_report("{\"type\":\"assistant\"}").is_err());
    }

    #[test]
    fn acceptance_grounds_every_composite_evidence_excerpt() {
        let sources = std::collections::HashMap::from([
            ("git_diff", "print('Hello, World!')"),
            ("test_logs", "Ran 1 test\nOK"),
        ]);
        let declared = vec!["git_diff".to_string(), "test_logs".to_string()];

        assert!(evidence_is_grounded(
            "print('Hello, World!')\n---\nRan 1 test\nOK",
            &declared,
            &sources,
        ));
        assert!(!evidence_is_grounded(
            "print('Hello, World!')\n---\n2 tests passed",
            &declared,
            &sources,
        ));
    }

    #[test]
    fn credential_guard_detects_realistic_secret_shapes() {
        assert_eq!(
            detect_secret("-----BEGIN PRIVATE KEY-----\\nbase64-material"),
            Some(SecretKind::PemPrivateKey)
        );
        assert_eq!(
            detect_secret("sk-proj-fakecredentialmaterial123456"),
            Some(SecretKind::ProviderToken)
        );
        assert_eq!(
            detect_secret("API_KEY=fakeCredentialValue123456"),
            Some(SecretKind::ApiKey)
        );
        assert_eq!(
            detect_secret("Authorization: Bearer fake.jwt.token-value-123456"),
            Some(SecretKind::BearerToken)
        );
    }

    #[test]
    fn changed_file_artifact_contains_exact_files_and_digest() {
        let temp = tempfile::tempdir().unwrap();
        let repository = temp.path();
        let git = |args: &[&str]| {
            let output = std::process::Command::new("git")
                .arg("-C")
                .arg(repository)
                .args(args)
                .output()
                .unwrap();
            assert!(output.status.success(), "git {:?} failed", args);
            String::from_utf8(output.stdout).unwrap().trim().to_string()
        };
        git(&["init", "--quiet"]);
        git(&["config", "user.email", "worker-test@openos.local"]);
        git(&["config", "user.name", "OpenAgents Worker Test"]);
        std::fs::write(repository.join("README.md"), "base\n").unwrap();
        git(&["add", "README.md"]);
        git(&["commit", "--quiet", "-m", "base"]);
        let base_sha = git(&["rev-parse", "HEAD"]);

        std::fs::write(repository.join("README.md"), "changed\n").unwrap();
        std::fs::create_dir(repository.join("src")).unwrap();
        std::fs::write(repository.join("src/lib.rs"), "pub fn changed() {}\n").unwrap();
        git(&["add", "README.md", "src/lib.rs"]);
        git(&["commit", "--quiet", "-m", "change"]);
        let commit_sha = git(&["rev-parse", "HEAD"]);

        let artifact = changed_files_artifact(repository, &base_sha, &commit_sha).unwrap();
        let expected_diff = std::process::Command::new("git")
            .arg("-C")
            .arg(repository)
            .args([
                "diff",
                "--no-ext-diff",
                "--unified=3",
                &base_sha,
                &commit_sha,
                "--",
            ])
            .output()
            .unwrap();
        assert!(expected_diff.status.success(), "git diff failed");
        assert_eq!(artifact.kind, "changed_files");
        assert_eq!(artifact.uri, format!("git://{commit_sha}/changed-files"));
        assert_eq!(
            artifact.sha256.as_deref(),
            Some(format!("{:x}", Sha256::digest(&expected_diff.stdout)).as_str())
        );
        assert_eq!(
            artifact.metadata["files"],
            json!(["README.md", "src/lib.rs"])
        );
        let patches = artifact.metadata["patches"].as_array().unwrap();
        assert_eq!(patches.len(), 2);
        assert_eq!(patches[0]["path"], "README.md");
        assert_eq!(patches[0]["additions"], 1);
        assert_eq!(patches[0]["deletions"], 1);
        assert!(patches[0]["unified"].as_str().unwrap().contains("-base"));
        assert!(patches[0]["unified"].as_str().unwrap().contains("+changed"));
        assert_eq!(patches[1]["path"], "src/lib.rs");
        assert_eq!(artifact.metadata["patches_truncated"], false);
    }

    #[test]
    fn changed_file_artifact_rejects_oversized_diffs_before_buffering() {
        let temp = tempfile::tempdir().unwrap();
        let repository = temp.path();
        let git = |args: &[&str]| {
            let status = std::process::Command::new("git")
                .arg("-C")
                .arg(repository)
                .args(args)
                .status()
                .unwrap();
            assert!(status.success(), "git {:?} failed", args);
        };
        git(&["init", "--quiet"]);
        git(&["config", "user.email", "worker-test@openos.local"]);
        git(&["config", "user.name", "OpenAgents Worker Test"]);
        std::fs::write(repository.join("large.txt"), "base\n").unwrap();
        git(&["add", "large.txt"]);
        git(&["commit", "--quiet", "-m", "base"]);
        let base_sha = std::process::Command::new("git")
            .arg("-C")
            .arg(repository)
            .args(["rev-parse", "HEAD"])
            .output()
            .unwrap();
        let base_sha = String::from_utf8(base_sha.stdout)
            .unwrap()
            .trim()
            .to_string();

        std::fs::write(
            repository.join("large.txt"),
            "x".repeat(MAX_DIFF_SCAN_BYTES + 1024),
        )
        .unwrap();
        git(&["add", "large.txt"]);
        git(&["commit", "--quiet", "-m", "large change"]);
        let commit_sha = std::process::Command::new("git")
            .arg("-C")
            .arg(repository)
            .args(["rev-parse", "HEAD"])
            .output()
            .unwrap();
        let commit_sha = String::from_utf8(commit_sha.stdout)
            .unwrap()
            .trim()
            .to_string();

        let error = changed_files_artifact(repository, &base_sha, &commit_sha).unwrap_err();
        assert!(error.to_string().contains("CHANGED_FILES_PATCH_TOO_LARGE"));
    }

    #[tokio::test]
    async fn rejects_private_research_source_addresses() {
        assert!(validate_public_https_url("https://127.0.0.1/private")
            .await
            .is_err());
        assert!(validate_public_https_url("https://[::1]/private")
            .await
            .is_err());
    }

    #[test]
    fn requires_authoritative_selection_from_extracted_sources() {
        let extracted = vec![
            ResearchSource {
                url: "https://docs.example.org/spec".into(),
                title: "Specification".into(),
                text: "x".repeat(200),
            },
            ResearchSource {
                url: "https://standards.example.net/rules".into(),
                title: "Rules".into(),
                text: "y".repeat(200),
            },
        ];
        let authored = json!({"sources":[
            {"url":extracted[0].url,"authority":"Official specification publisher"},
            {"url":extracted[1].url,"authority":"Independent standards authority"}
        ]});
        assert_eq!(
            select_authoritative_sources(&authored, &extracted)
                .unwrap()
                .len(),
            2
        );

        let fabricated = json!({"sources":[
            {"url":"https://unseen.example.com","authority":"Official specification publisher"},
            {"url":extracted[1].url,"authority":"Independent standards authority"}
        ]});
        assert!(select_authoritative_sources(&fabricated, &extracted).is_err());
    }

    #[test]
    fn diversification_excludes_hosts_already_extracted() {
        let hosts = ["docs.example.org".to_string()]
            .into_iter()
            .collect::<std::collections::HashSet<_>>();
        let query = diversify_research_query("invoice validation", &hosts);

        assert!(query.contains("invoice validation official primary source"));
        assert!(query.contains("-site:docs.example.org"));
    }

    #[test]
    fn concise_search_variant_preserves_domain_terms() {
        let variants = search_query_variants(
            "Using authoritative documentation only, research the current Peppol BIS Billing 3.0 validation procedure, prioritizing OpenPeppol Schematron artifacts.",
        );
        assert!(variants[0].contains("Peppol BIS Billing 3.0 validation"));
        assert!(variants[0].contains("OpenPeppol Schematron"));
        assert_eq!(variants.len(), 2);
    }

    #[test]
    fn ranks_query_matching_official_sources_before_commercial_explainers() {
        let mut candidates = vec![
            (
                "https://vendor.example.com/blog/validator".into(),
                "Invoice validator".into(),
            ),
            (
                "https://github.com/OpenPEPPOL/peppol-bis-invoice-3/tree/master/rules".into(),
                "Official rules".into(),
            ),
            (
                "https://docs.peppol.eu/poacc/billing/3.0/".into(),
                "Peppol BIS Billing specification".into(),
            ),
        ];
        rank_search_candidates(
            &mut candidates,
            "Peppol BIS Billing OpenPeppol official rules",
        );
        assert!(
            candidates[0].0.contains("OpenPEPPOL") || candidates[0].0.contains("docs.peppol.eu")
        );
        assert!(!candidates[0].0.contains("vendor.example.com"));
    }

    #[test]
    fn authoring_validation_excludes_control_plane_lifecycle_criteria() {
        let criteria = vec![
            "Defines repeatable invoice checks.".to_string(),
            "Is activated privately only after explicit user confirmation.".to_string(),
            "Authoring occurs only after confirmation.".to_string(),
            "Retries the originating request exactly once after successful activation.".to_string(),
        ];
        assert_eq!(
            authoring_success_criteria(&criteria),
            vec!["Defines repeatable invoice checks."]
        );
    }

    #[test]
    fn retries_only_transient_author_transport_statuses() {
        assert!(retryable_author_status(429));
        assert!(retryable_author_status(502));
        assert!(!retryable_author_status(400));
        assert!(!retryable_author_status(401));
    }

    #[test]
    fn requires_grounded_evidence_for_every_approved_criterion() {
        let criteria = vec!["Validate syntax before business rules".to_string()];
        let generalization = json!({
            "capability":"Validate authoritative business documents",
            "parameters":[{"name":"target","description":"Document system to validate","required":true,"source":"user","schema":{"type":"string"}}],
            "instanceBindings":{"target":"network one"},
            "invariants":["Use authoritative rules"],
            "discoveryStrategy":["Resolve the target owner","Discover independent official rules"],
            "validationScenarios":[
                {"name":"network one","bindings":{"target":"network one"}},
                {"name":"network two","bindings":{"target":"network two"}}
            ]
        });
        let sources = vec![
            json!({"url":"https://docs.example.org/spec"}),
            json!({"url":"https://standards.example.net/rules"}),
        ];
        let authored = json!({"validation":{"passed":true,"criteria":[{
            "criterion":criteria[0],"passed":true,
            "evidence":"The procedure explicitly orders syntax validation before rules."
        }],"generality_scenarios":[
            {"name":"network one","passed":true,"evidence":"The runtime target parameter selects network one rules."},
            {"name":"network two","passed":true,"evidence":"The runtime target parameter selects network two rules."}
        ]}});
        let body = "## Runtime parameters\nUse `target`. The procedure explicitly orders syntax validation before rules. Use https://docs.example.org/spec before applying https://standards.example.net/rules.";
        let extracted = vec![
            ResearchSource {
                url: "https://docs.example.org/spec".into(),
                title: "Specification".into(),
                text: "Official syntax requirements.".into(),
            },
            ResearchSource {
                url: "https://standards.example.net/rules".into(),
                title: "Rules".into(),
                text: "Official business rules.".into(),
            },
        ];
        assert!(validate_authored_skill(
            &authored,
            &criteria,
            &generalization,
            body,
            &sources,
            &extracted
        )
        .is_ok());

        let missing = json!({"validation":{"passed":true,"criteria":[]}});
        assert!(validate_authored_skill(
            &missing,
            &criteria,
            &generalization,
            body,
            &sources,
            &extracted
        )
        .is_err());

        let unsupported = json!({"validation":{"passed":true,"criteria":[{
            "criterion":criteria[0],"passed":true,
            "evidence":"This assertion appears in neither the skill nor its selected sources."
        }]}});
        assert!(validate_authored_skill(
            &unsupported,
            &criteria,
            &generalization,
            body,
            &sources,
            &extracted
        )
        .is_err());
    }

    #[test]
    fn documentation_skill_generalizes_across_repository_and_website_discovery() {
        let generalization = json!({
            "capability":"Discover and ingest authoritative application documentation",
            "parameters":[
                {"name":"target","description":"Application whose docs are required","required":true,"source":"user","schema":{"type":"string"}},
                {"name":"source_hint","description":"Available source discovery hint","required":true,"source":"discovery","schema":{"type":"string"}},
                {"name":"destination","description":"Registered ingestion destination","required":true,"source":"organization_config","schema":{"type":"string"}}
            ],
            "instanceBindings":{"target":"OpenFoo","source_hint":"local_source","destination":"openbrain_docs"},
            "invariants":["Verify provenance and never ingest secrets"],
            "discoveryStrategy":["Inspect source code and Git remotes","Otherwise verify official website ownership"],
            "validationScenarios":[
                {"name":"source repository","bindings":{"target":"OpenFoo","source_hint":"local_source","destination":"openbrain_docs"}},
                {"name":"official website","bindings":{"target":"External SaaS","source_hint":"website","destination":"openbrain_docs"}}
            ]
        });
        let body = "## Runtime parameters\nUse `target`, `source_hint`, and `destination`. If source is available inspect Git remotes; otherwise resolve the official website and owner.";

        assert!(validate_generalization_contract(&generalization).is_ok());
        assert!(validate_parameterized_body(&generalization, body).is_ok());

        let missing_instance_binding = json!({
            "capability":generalization["capability"],
            "parameters":generalization["parameters"],
            "instanceBindings":{"target":"OpenFoo","source_hint":"local_source"},
            "invariants":generalization["invariants"],
            "discoveryStrategy":generalization["discoveryStrategy"],
            "validationScenarios":generalization["validationScenarios"]
        });
        assert!(validate_generalization_contract(&missing_instance_binding).is_err());

        let hardcoded = format!("{body} Always fetch https://vendor.example/docs.");
        let with_url_binding = json!({
            "capability":"Discover and ingest authoritative application documentation",
            "parameters":generalization["parameters"],
            "instanceBindings":{"target":"Vendor","source_hint":"website","destination":"openbrain_docs","website":"https://vendor.example/docs"},
            "invariants":generalization["invariants"],
            "discoveryStrategy":generalization["discoveryStrategy"],
            "validationScenarios":generalization["validationScenarios"]
        });
        assert!(validate_parameterized_body(&with_url_binding, &hardcoded).is_err());
    }

    #[test]
    fn engineering_jobs_receive_bounded_generalization_rules() {
        let skills = [LoadedSkill {
            reference: SkillReference {
                id: "open-code".into(),
                version: "1".into(),
                sha256: "a".repeat(64),
                source: SkillSource::Bundled,
            },
            body: "Use the isolated OpenCode worktree and run declared tests.".into(),
        }];
        let prompt = engineering_prompt(
            "Implement documentation ingestion for OpenFoo.",
            &skills,
            &["The declared test command passes.".into()],
        )
        .unwrap();
        assert!(prompt.contains("open-code@1"));
        assert!(prompt.contains("The declared test command passes."));
        assert!(prompt.contains("current working directory is the only permitted"));
        assert!(prompt.contains("repository paths from the task as source identity metadata"));
        assert!(prompt.contains("never edit an absolute repository path"));
        assert!(prompt.contains("facts that vary between valid instances"));
        assert!(prompt.contains("smallest reusable boundary"));
        assert!(prompt.contains("materially different instance"));
        assert!(!prompt.contains("Always use OpenFoo"));
    }

    #[test]
    fn workspace_names_are_contained_and_project_named() {
        assert_eq!(
            safe_workspace_name("OpenPro Front").unwrap(),
            "OpenPro-Front"
        );
        assert_eq!(safe_workspace_name("../OpenPro").unwrap(), "OpenPro");
        assert_eq!(safe_workspace_name("team/api").unwrap(), "team-api");
        assert!(safe_workspace_name("../../").is_err());
    }

    #[test]
    fn qa_commands_must_exactly_match_orchestrator_policy() {
        let inputs = json!({"qa_requirements":[{
            "surface":"frontend","kind":"responsive","command":"pnpm test:responsive"
        }]});
        assert!(parse_qa_requirements(&inputs, &["pnpm test:responsive".to_string()]).is_ok());
        assert!(parse_qa_requirements(&inputs, &["pnpm test".to_string()]).is_err());
    }

    #[test]
    fn changed_files_are_blocked_before_push_when_impact_or_path_policy_fails() {
        let artifact = WorkerArtifact {
            kind: "changed_files".into(),
            name: "Changed files".into(),
            uri: "git://changed".into(),
            sha256: None,
            metadata: json!({"files":["apps/web/src/page.tsx"]}),
        };
        let valid = json!({
            "protected_paths":[".github"],
            "impact_surfaces":["frontend"],
            "surface_paths":{"frontend":["apps/web"],"backend":["apps/api"]}
        });
        validate_changed_file_policy(&artifact, &valid).unwrap();

        let protected = WorkerArtifact {
            metadata: json!({"files":[".github/workflows/release.yml"]}),
            ..artifact.clone()
        };
        assert_eq!(
            validate_changed_file_policy(&protected, &valid)
                .unwrap_err()
                .to_string(),
            "PROTECTED_PATH_CHANGED"
        );
        let undeclared = WorkerArtifact {
            metadata: json!({"files":["apps/api/src/server.ts"]}),
            ..artifact
        };
        assert_eq!(
            validate_changed_file_policy(&undeclared, &valid)
                .unwrap_err()
                .to_string(),
            "DECLARED_IMPACT_INCOMPLETE: apps/api/src/server.ts"
        );
    }

    #[tokio::test]
    async fn managed_repository_is_cloned_once_below_allowed_root() {
        let root = std::env::temp_dir().join(format!("openagents-clone-{}", Uuid::new_v4()));
        let source = root.join("source.git");
        let managed = root.join("managed");
        std::fs::create_dir_all(&managed).unwrap();
        assert!(std::process::Command::new("git")
            .args(["init", "--bare"])
            .arg(&source)
            .status()
            .unwrap()
            .success());
        let target = managed.join("OpenPro");
        let inputs = json!({"repository":target});
        let cloned = canonical_repository(
            &inputs,
            std::slice::from_ref(&managed),
            source.to_str().unwrap(),
            "origin",
            Duration::from_secs(5),
        )
        .await
        .unwrap();
        assert!(cloned.starts_with(std::fs::canonicalize(&managed).unwrap()));
        assert!(cloned.join(".git").exists());

        let escaped = json!({"repository":managed.join("../escape")});
        assert!(canonical_repository(
            &escaped,
            std::slice::from_ref(&managed),
            source.to_str().unwrap(),
            "origin",
            Duration::from_secs(5)
        )
        .await
        .is_err());
        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn parses_primary_and_lite_search_result_shapes() {
        let mut results = Vec::new();
        append_search_results(
            r#"<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fdocs.example.org%2Fspec">Specification</a>"#,
            "a.result__a",
            &mut results,
        )
        .unwrap();
        append_search_results(
            r#"<a href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fstandards.example.net%2Frules" class="result-link">Rules</a>"#,
            "a.result-link",
            &mut results,
        )
        .unwrap();
        append_search_results(
            r#"<li class="b_algo"><h2><a href="https://primary.example.com/guide">Guide</a></h2></li>"#,
            "li.b_algo h2 a",
            &mut results,
        )
        .unwrap();
        append_search_results(
            r#"<div class="snippet" data-type="web"><a href="https://authority.example.edu/standard">Standard</a></div>"#,
            "div.snippet[data-type=\"web\"] a[href]",
            &mut results,
        )
        .unwrap();
        assert_eq!(results.len(), 4);
        assert_eq!(results[0].0, "https://docs.example.org/spec");
        assert_eq!(results[1].0, "https://standards.example.net/rules");
        assert_eq!(results[2].0, "https://primary.example.com/guide");
        assert_eq!(results[3].0, "https://authority.example.edu/standard");
    }

    #[test]
    fn decodes_bing_tracking_urls_and_rejects_search_pages() {
        let target = "https://docs.example.org/spec";
        let encoded = URL_SAFE_NO_PAD.encode(target);
        let tracked = format!("https://www.bing.com/ck/a?u=a1{encoded}&ntb=1");

        assert_eq!(
            normalize_search_result_url(&tracked).as_deref(),
            Some(target)
        );
        assert!(normalize_search_result_url("https://www.bing.com/search?q=spec").is_none());
    }
}
