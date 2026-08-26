use std::{
    future::pending,
    io::Read,
    net::{IpAddr, SocketAddr},
    path::{Component, Path, PathBuf},
    process::{ExitStatus, Stdio},
    time::Duration,
};

use anyhow::Context;
use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine as _};
use chrono::Utc;
use contract_core::{
    AcceptanceCriterionEvidence, CognitiveEvidenceReference, CognitiveObservation,
    CognitiveObservationType, CognitiveRisk, CognitiveScope, CognitiveScopeType,
    EngineeringWorkspaceEvidence, GitEvidence, QaEvidence, QaRequirement, SkillReference,
    SkillSource, TestEvidence, WorkerArtifact, WorkerJob, WorkerResult,
};
use reqwest::{header::LOCATION, redirect::Policy};
use scraper::{Html, Selector};
use serde::Deserialize;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use tokio::{
    fs,
    io::{AsyncRead, AsyncReadExt, AsyncWriteExt},
    process::{Child, Command},
    sync::mpsc,
    task::JoinHandle,
    time::{sleep, timeout},
};
use uuid::Uuid;
use zeroize::Zeroizing;

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
const ACCEPTANCE_AUDIT_TRANSPORT_ATTEMPTS: usize = 2;
const MAX_COGNITIVE_EVENT_FILE_BYTES: u64 = 64 * 1024;
const MAX_COGNITIVE_EVENT_BYTES: usize = 16 * 1024;
const MAX_COGNITIVE_EVENTS: usize = 50;
const MAX_OPENCODE_EVIDENCE_EVENTS: usize = 1_000;
const MAX_OPENCODE_STDOUT_BYTES: usize = 4 * 1024 * 1024;
const MAX_OPENCODE_STDERR_BYTES: usize = 512 * 1024;
const MAX_QA_STDOUT_BYTES: usize = 2 * 1024 * 1024;
const MAX_QA_STDERR_BYTES: usize = 512 * 1024;
const PROCESS_DRAIN_TIMEOUT: Duration = Duration::from_secs(5);
const SANDBOX_INIT_FAILURE_EXIT_CODE: i32 = 125;
const CANDIDATE_CONTROL_DIRECTORY: &str = ".openos-control";

struct LoadedSkill {
    reference: SkillReference,
    body: String,
}

#[derive(Debug, Deserialize)]
struct WorkspaceDependency {
    name: String,
    repository: String,
    provider: String,
    external_repository_id: String,
    remote_url: String,
    base_ref: String,
    source_path: String,
    destination: String,
}

pub async fn runtime_healthy(config: &Config) -> bool {
    command_ok(Command::new("git").arg("--version")).await
        && signing_configuration_ready(config).await
        && command_ok(Command::new(&config.sandbox_binary).arg("--check")).await
        && command_ok(Command::new(&config.opencode_binary).arg("--version")).await
        && fs::metadata(&config.shell_binary)
            .await
            .is_ok_and(|metadata| metadata.is_file())
        && fs::create_dir_all(&config.managed_root).await.is_ok()
}

async fn signing_configuration_ready(config: &Config) -> bool {
    let Some(encoded) = config
        .git_signing_key_b64
        .as_ref()
        .map(|encoded| encoded.trim())
    else {
        return false;
    };
    if !config.git_sign_commits
        || encoded.is_empty()
        || encoded.len() % 4 != 0
        || !encoded
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'+' | b'/' | b'='))
    {
        return false;
    }
    command_ok(Command::new("gpg").arg("--version")).await
}

async fn command_ok(command: &mut Command) -> bool {
    command
        .kill_on_drop(true)
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
    let external_repository_id = string(inputs, "external_repository_id")?;
    validate_repository_remote(&remote_url, &external_repository_id)?;
    let repository = canonical_repository(
        inputs,
        &config.allowed_repositories,
        &remote_url,
        &config.git_remote,
        config.git_timeout,
    )
    .await?;
    let repository_name = string(inputs, "repository_name")?;
    verify_repository_remote(&repository, &config.git_remote, &remote_url).await?;
    if inputs.get("requires_human_review").and_then(Value::as_bool) != Some(true) {
        anyhow::bail!("HUMAN_REVIEW_POLICY_REQUIRED");
    }
    let base_ref = string(inputs, "base_ref")?;
    let tests = string_array(inputs, "test_commands")?;
    let qa_requirements = parse_qa_requirements(inputs, &tests)?;
    let prompt = string(inputs, "prompt")?;
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
    let (workspace_root, workspace, git_dir, base_sha, expected_branch) =
        create_worktree(WorktreeSpec {
            git_binary: &config.git_binary,
            repository: &repository,
            managed_root: &config.managed_root,
            organization_id: job.organization_id,
            repository_name: &repository_name,
            ticket_id: job.ticket_id,
            plan_id: job.plan_id,
            task_id: job.task_id,
            base_ref: &base_ref,
            remote: &config.git_remote,
            remote_url: &remote_url,
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
    let workspace_dependencies = parse_workspace_dependencies(inputs)?;
    let materialized_dependencies = materialize_workspace_dependencies(
        &workspace_dependencies,
        config,
        &workspace_root,
        &workspace,
        job.task_id,
    )
    .await?;
    if !workspace_dependencies.is_empty() {
        store
            .update(
                run_id,
                RunStatus::Running,
                "workspace.dependencies.materialized",
                json!({"count":workspace_dependencies.len()}),
            )
            .await;
    }
    let engine = run_opencode(OpenCodeSpec {
        config,
        job,
        run_id,
        workspace: &workspace,
        git_dir: &git_dir,
        repository: &repository,
        workspace_id: &external_repository_id,
        workspace_dependencies: &materialized_dependencies,
        prompt: &prompt,
        store,
    })
    .await?;
    verify_workspace_dependencies_clean(&config.git_binary, &materialized_dependencies).await?;
    let terminal_report = final_execution_report(&engine.stdout)?;
    if let Some(kind) = post_opencode_secret_kind(&terminal_report, &engine.stderr) {
        tracing::warn!(
            run_id = %run_id,
            secret_kind = kind.label(),
            "OpenCode output rejected by credential guard"
        );
        anyhow::bail!("ENGINE_SECRET_LEAK_REJECTED");
    }
    if engine.exit_status != 0 {
        anyhow::bail!(
            "OPENCODE_FAILED: exit_status={}; terminal={}; stderr={}",
            engine.exit_status,
            opencode_terminal_status(&engine.stdout),
            bounded_diagnostic(&engine.stderr),
        );
    }
    ensure_not_cancelled(store, run_id).await?;
    let git = commit_changes(CommitSpec {
        workspace: &workspace,
        git_dir: &git_dir,
        repository: &repository,
        base_sha: &base_sha,
        expected_branch: &expected_branch,
        ticket_id: job.ticket_id,
        run_id,
        sign_commit: true,
        git_binary: &config.git_binary,
        signing_key_b64: config
            .git_signing_key_b64
            .as_ref()
            .map(|encoded| encoded.as_str()),
        git_timeout: config.git_timeout,
    })
    .await?;
    let changed_files = changed_files_artifact(
        &config.git_binary,
        &workspace,
        &git_dir,
        &base_sha,
        &git.evidence.commit_sha,
    )?;
    validate_changed_file_policy(&changed_files, inputs)?;
    let test_evidence = run_tests(QaExecutionSpec {
        workspace: &workspace,
        git_dir: &git_dir,
        dependencies: &materialized_dependencies,
        commands: &tests,
        run_id,
        store,
        sandbox_binary: &config.sandbox_binary,
        shell_binary: &config.shell_binary,
        timeout: config.qa_timeout,
    })
    .await?;
    verify_workspace_dependencies_clean(&config.git_binary, &materialized_dependencies).await?;
    verify_committed_delivery_unchanged(
        &config.git_binary,
        &workspace,
        &git_dir,
        &git.evidence.commit_sha,
    )
    .await?;
    let qa_evidence = qa_evidence(&qa_requirements, &test_evidence)?;
    let failed_tests = test_evidence
        .iter()
        .enumerate()
        .filter(|(_, test)| test.exit_status != 0)
        .map(|(index, test)| format!("{index}:{}", test.exit_status))
        .collect::<Vec<_>>();
    if !failed_tests.is_empty() {
        anyhow::bail!(
            "QA_FAILED: failed_test_exit_statuses={}",
            if failed_tests.is_empty() {
                "none".into()
            } else {
                failed_tests.join(",")
            }
        );
    }
    verify_local_candidate(&git)?;
    store
        .update(
            run_id,
            RunStatus::Running,
            "candidate.local.completed",
            json!({
                "branch":git.evidence.branch,
                "commit_sha":git.evidence.commit_sha,
                "signed":true,
                "pushed":false,
                "pull_request_created":false
            }),
        )
        .await;
    store
        .update(
            run_id,
            RunStatus::Running,
            "acceptance.audit.started",
            json!({"criteria_count":job.acceptance_criteria.len(),"provider":"openai-compatible","model":config.llm_model}),
        )
        .await;
    let acceptance_evidence = audit_acceptance_criteria(AcceptanceAuditSpec {
        config,
        workspace: &workspace,
        git_dir: &git_dir,
        git_binary: &config.git_binary,
        base_sha: &base_sha,
        commit_sha: &git.evidence.commit_sha,
        tests: &test_evidence,
        engine_stdout: &engine.stdout,
        git: &git,
        changed_files: &changed_files,
        organization_id: job.organization_id,
        repository_name: &repository_name,
        workspace_root: &workspace_root,
        base_ref: &base_ref,
        criteria: &job.acceptance_criteria,
    })
    .await?;
    store
        .update(
            run_id,
            RunStatus::Running,
            "acceptance.audit.completed",
            json!({"criteria_count":acceptance_evidence.len(),"passed":true}),
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
    let artifact = persist_opencode_evidence(&workspace_root, run_id, &engine.stdout).await?;
    let cognitive_observations =
        engineering_cognitive_observations(job, run_id, &loaded_skills, &engine.cognitive_events);
    ensure_not_cancelled(store, run_id).await?;
    Ok(WorkerResult {
        run_id,
        artifacts: vec![artifact, changed_files, execution_contract],
        stderr: nonempty(sanitize(&engine.stderr)),
        exit_status: engine.exit_status,
        tests: test_evidence,
        git: Some(git.evidence),
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
        pull_request: None,
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
         OpenOS delivery ownership contract:\n\
         - OpenAgents exclusively owns the branch and local commit-signing lifecycle.\n\
         - This coding phase produces a signed, tested local candidate only; it never pushes or creates a pull request.\n\
         - Do not create, switch, rename, delete, commit, push, merge, rebase, or modify Git branches, refs, remotes, hooks, configuration, or files under .git.\n\
         - Leave the requested source and test changes uncommitted in the current branch for OpenAgents to validate and deliver.\n\n\
         - OpenAgents executes every authoritative registered QA command after it creates the signed commit. Do not install dependencies or run build, typecheck, lint, or test commands inside OpenCode.\n\n\
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
    git_binary: &Path,
    workspace: &Path,
    git_dir: &Path,
    base_sha: &str,
    commit_sha: &str,
) -> anyhow::Result<WorkerArtifact> {
    let names_output = trusted_std_git_command(git_binary, workspace, git_dir)
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
    let full_diff = bounded_git_diff(git_binary, workspace, git_dir, base_sha, commit_sha, None)?;
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
        let output = bounded_git_diff(
            git_binary,
            workspace,
            git_dir,
            base_sha,
            commit_sha,
            Some(path),
        )?;
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
    git_binary: &Path,
    workspace: &Path,
    git_dir: &Path,
    base_sha: &str,
    commit_sha: &str,
    path: Option<&str>,
) -> anyhow::Result<Vec<u8>> {
    let mut command = trusted_std_git_command(git_binary, workspace, git_dir);
    command.args([
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

fn trusted_std_git_command(
    git_binary: &Path,
    workspace: &Path,
    git_dir: &Path,
) -> std::process::Command {
    let mut command = std::process::Command::new(git_binary);
    command
        .env_clear()
        .env("HOME", "/nonexistent")
        .env("PATH", "/usr/local/bin:/usr/bin:/bin")
        .env("LANG", "C.UTF-8")
        .env("GIT_CONFIG_NOSYSTEM", "1")
        .env("GIT_CONFIG_GLOBAL", "/dev/null")
        .env("GIT_ATTR_NOSYSTEM", "1")
        .env("GIT_TERMINAL_PROMPT", "0")
        .env("GIT_ASKPASS", "/bin/false")
        .env("SSH_ASKPASS", "/bin/false")
        .env("GIT_EDITOR", "true")
        .env("GIT_SEQUENCE_EDITOR", "true")
        .env("GIT_PAGER", "cat")
        .env("PAGER", "cat")
        .env("GIT_EXTERNAL_DIFF", "/bin/false")
        .arg("--git-dir")
        .arg(git_dir)
        .arg("--work-tree")
        .arg(workspace)
        .args([
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "core.fsmonitor=false",
            "-c",
            "credential.helper=",
            "-c",
            "credential.interactive=false",
            "-c",
            "diff.external=",
        ]);
    command
}

fn trusted_git_command(git_binary: &Path, workspace: &Path, git_dir: &Path) -> Command {
    let mut command = Command::new(git_binary);
    command
        .env_clear()
        .env("HOME", "/nonexistent")
        .env("PATH", "/usr/local/bin:/usr/bin:/bin")
        .env("LANG", "C.UTF-8")
        .env("GIT_CONFIG_NOSYSTEM", "1")
        .env("GIT_CONFIG_GLOBAL", "/dev/null")
        .env("GIT_ATTR_NOSYSTEM", "1")
        .env("GIT_TERMINAL_PROMPT", "0")
        .env("GIT_ASKPASS", "/bin/false")
        .env("SSH_ASKPASS", "/bin/false")
        .env("GIT_EDITOR", "true")
        .env("GIT_SEQUENCE_EDITOR", "true")
        .env("GIT_PAGER", "cat")
        .env("PAGER", "cat")
        .env("GIT_EXTERNAL_DIFF", "/bin/false")
        .arg("--git-dir")
        .arg(git_dir)
        .arg("--work-tree")
        .arg(workspace)
        .args([
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "core.fsmonitor=false",
            "-c",
            "credential.helper=",
            "-c",
            "credential.interactive=false",
            "-c",
            "diff.external=",
        ]);
    command
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

#[allow(clippy::too_many_arguments)]
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
            let excluded = lower.contains("activated privately")
                || (lower.contains("activate")
                    && lower.contains("skill")
                    && lower.contains("privately"))
                || lower.contains("after explicit user confirmation")
                || (lower.contains("confirmation")
                    && (lower.contains("author") || lower.contains("activat")))
                || lower.contains("retries the originating")
                || lower.contains("after successful activation");
            !excluded
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

struct AcceptanceAuditSpec<'a> {
    config: &'a Config,
    workspace: &'a Path,
    git_dir: &'a Path,
    git_binary: &'a Path,
    base_sha: &'a str,
    commit_sha: &'a str,
    tests: &'a [TestEvidence],
    engine_stdout: &'a str,
    git: &'a VerifiedGitDelivery,
    changed_files: &'a WorkerArtifact,
    organization_id: Uuid,
    repository_name: &'a str,
    workspace_root: &'a Path,
    base_ref: &'a str,
    criteria: &'a [String],
}

async fn audit_acceptance_criteria(
    spec: AcceptanceAuditSpec<'_>,
) -> anyhow::Result<Vec<AcceptanceCriterionEvidence>> {
    let AcceptanceAuditSpec {
        config,
        workspace,
        git_dir,
        git_binary,
        base_sha,
        commit_sha,
        tests,
        engine_stdout,
        git,
        changed_files,
        organization_id,
        repository_name,
        workspace_root,
        base_ref,
        criteria,
    } = spec;
    if criteria.is_empty() {
        anyhow::bail!("ACCEPTANCE_CRITERIA_REQUIRED");
    }
    if criteria.len() > 32 || criteria.iter().any(|criterion| criterion.trim().is_empty()) {
        anyhow::bail!("ACCEPTANCE_CRITERIA_INVALID");
    }
    let diff = bounded_git_diff(git_binary, workspace, git_dir, base_sha, commit_sha, None)?;
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
    let candidate_evidence =
        serde_json::to_string_pretty(&candidate_evidence(CandidateEvidenceSpec {
            git,
            changed_files,
            organization_id,
            repository_name,
            workspace_root,
            base_ref,
            base_sha,
        }))?;

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
            {"role":"system","content":"You are the OpenAgents acceptance auditor. Evaluate every supplied criterion verbatim using only the supplied committed Git diff, declared test logs, terminal OpenCode execution report, and structured local-candidate evidence. For each criterion, quote exact contiguous excerpts from the named sources as evidence. When one excerpt cannot prove every clause, join multiple exact excerpts with a line containing only ---; every excerpt will be checked independently. Use execution_report only for process evidence that cannot exist in a diff or test log, and candidate_evidence only for the verified signed local commit and explicit absence of push or pull-request delivery. Never infer evidence that is absent, never claim a test that was not run, and set passed=false when the supplied corpus does not prove the criterion. Call submit_acceptance_evidence and do not answer in prose."},
            {"role":"user","content":serde_json::to_string(&json!({
                "criteria":criteria,
                "sources":{
                    "git_diff":{"content":diff,"truncated":diff_truncated},
                    "test_logs":{"content":test_logs,"truncated":test_logs.len() >= MAX_ACCEPTANCE_TEST_BYTES},
                    "execution_report":{"content":execution_report,"truncated":false},
                    "candidate_evidence":{"content":candidate_evidence,"truncated":false}
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
                            "sources":{"type":"array","minItems":1,"uniqueItems":true,"items":{"type":"string","enum":["git_diff","test_logs","execution_report","candidate_evidence"]}}
                        }
                    }
                }}
            }
        }}],
        "tool_choice":{"type":"function","function":{"name":"submit_acceptance_evidence"}}
    });
    let client = reqwest::Client::builder()
        .timeout(ACCEPTANCE_AUDIT_TIMEOUT)
        .redirect(Policy::none())
        .build()?;
    let mut response = None;
    for attempt in 0..ACCEPTANCE_AUDIT_TRANSPORT_ATTEMPTS {
        match client
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
                if attempt + 1 < ACCEPTANCE_AUDIT_TRANSPORT_ATTEMPTS
                    && retryable_author_status(value.status().as_u16()) =>
            {
                sleep(Duration::from_millis(250 * (1 << attempt))).await;
            }
            Ok(value) => return Err(value.error_for_status().expect_err("non-success").into()),
            Err(error)
                if attempt + 1 < ACCEPTANCE_AUDIT_TRANSPORT_ATTEMPTS
                    && (error.is_timeout() || error.is_connect()) =>
            {
                sleep(Duration::from_millis(250 * (1 << attempt))).await;
            }
            Err(error) => return Err(error.into()),
        }
    }
    let response =
        response.ok_or_else(|| anyhow::anyhow!("ACCEPTANCE_AUDIT_TRANSPORT_EXHAUSTED"))?;
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
        ("candidate_evidence", candidate_evidence.as_str()),
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

struct CandidateEvidenceSpec<'a> {
    git: &'a VerifiedGitDelivery,
    changed_files: &'a WorkerArtifact,
    organization_id: Uuid,
    repository_name: &'a str,
    workspace_root: &'a Path,
    base_ref: &'a str,
    base_sha: &'a str,
}

fn verify_local_candidate(git: &VerifiedGitDelivery) -> anyhow::Result<()> {
    if git.evidence.pushed || git.evidence.remote.is_some() {
        anyhow::bail!("LOCAL_CANDIDATE_HAS_DELIVERY_STATE");
    }
    if git.signed_commits.is_empty()
        || !git
            .signed_commits
            .iter()
            .any(|commit| commit == &git.evidence.commit_sha)
    {
        anyhow::bail!("LOCAL_CANDIDATE_SIGNATURE_EVIDENCE_MISSING");
    }
    Ok(())
}

fn candidate_evidence(spec: CandidateEvidenceSpec<'_>) -> Value {
    let CandidateEvidenceSpec {
        git,
        changed_files,
        organization_id,
        repository_name,
        workspace_root,
        base_ref,
        base_sha,
    } = spec;
    json!({
        "schema":"openos.local-candidate/v1",
        "workspace":{
            "organization_id":organization_id,
            "root":workspace_root,
            "repository_name":repository_name,
            "repository_folder":git.evidence.worktree,
            "path_isolation":{
                "managed_root_containment":"verified",
                "dedicated_git_worktree":"verified"
            },
            "process_sandbox":"linux_landlock_verified_at_worker_health"
        },
        "scope":{
            "changed_files":changed_files.metadata.get("files").cloned().unwrap_or_else(|| json!([])),
            "changed_file_policy":"verified"
        },
        "git":{
            "repository":git.evidence.repository,
            "branch":git.evidence.branch,
            "branch_policy":"verified",
            "base_ref":base_ref,
            "base_sha":base_sha,
            "commit_sha":git.evidence.commit_sha,
            "commit_count":git.signed_commits.len(),
            "signature_verifier":"git verify-commit",
            "signature_verified_commits":git.signed_commits,
            "clean":git.evidence.clean,
            "pushed":git.evidence.pushed,
            "remote":git.evidence.remote
        },
        "delivery":{
            "mode":"local_candidate_only",
            "push_performed":false,
            "pull_request_created":false,
            "human_review_required":true
        }
    })
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

fn post_opencode_secret_kind(terminal_report: &str, stderr: &str) -> Option<SecretKind> {
    detect_secret(terminal_report).or_else(|| detect_secret(stderr))
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
    if parsed_remote_repository(&actual) != parsed_remote_repository(expected_url) {
        anyhow::bail!("REPOSITORY_REMOTE_POLICY_MISMATCH");
    }
    Ok(())
}

fn validate_repository_remote(
    remote_url: &str,
    external_repository_id: &str,
) -> anyhow::Result<()> {
    let identity = remote_repository_identity(remote_url)
        .ok_or_else(|| anyhow::anyhow!("REPOSITORY_REMOTE_URL_INVALID"))?;
    if !identity.eq_ignore_ascii_case(external_repository_id.trim().trim_end_matches(".git")) {
        anyhow::bail!("REMOTE_REPOSITORY_IDENTITY_MISMATCH");
    }
    Ok(())
}

fn remote_repository_identity(value: &str) -> Option<String> {
    parsed_remote_repository(value).map(|(_, path)| path)
}

fn parsed_remote_repository(value: &str) -> Option<(String, String)> {
    let value = value.trim().trim_end_matches('/');
    let (host, path) = if let Ok(url) = url::Url::parse(value) {
        let identity_is_safe = match url.scheme() {
            "https" => url.username().is_empty() && url.password().is_none(),
            "ssh" => url.username() == "git" && url.password().is_none(),
            _ => false,
        };
        if !identity_is_safe
            || url.host_str().is_none()
            || url.query().is_some()
            || url.fragment().is_some()
        {
            return None;
        }
        (
            url.host_str()?.to_ascii_lowercase(),
            url.path().trim_start_matches('/').to_string(),
        )
    } else {
        let (authority, path) = value.split_once(':')?;
        let (user, host) = authority.split_once('@')?;
        if user != "git" || host.contains('@') || authority.contains('/') || path.starts_with('/') {
            return None;
        }
        if host.is_empty()
            || !host
                .bytes()
                .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'-'))
        {
            return None;
        }
        (host.to_ascii_lowercase(), path.to_string())
    };
    let path = path.trim_end_matches(".git");
    let parts = path.split('/').collect::<Vec<_>>();
    if parts.len() < 2
        || parts.iter().any(|part| {
            part.is_empty()
                || matches!(*part, "." | "..")
                || !part
                    .bytes()
                    .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'_' | b'-'))
        })
    {
        return None;
    }
    Some((host, parts.join("/")))
}

struct WorktreeSpec<'a> {
    git_binary: &'a Path,
    repository: &'a Path,
    managed_root: &'a Path,
    organization_id: Uuid,
    repository_name: &'a str,
    ticket_id: Uuid,
    plan_id: Uuid,
    task_id: Uuid,
    base_ref: &'a str,
    remote: &'a str,
    remote_url: &'a str,
    timeout: Duration,
}

async fn create_worktree(
    spec: WorktreeSpec<'_>,
) -> anyhow::Result<(PathBuf, PathBuf, PathBuf, String, String)> {
    let repository_folder = safe_workspace_name(spec.repository_name)?;
    let workspace_root = spec
        .managed_root
        .join(spec.organization_id.to_string())
        .join(spec.plan_id.to_string());
    let workspace = workspace_root.join(&repository_folder);
    fs::create_dir_all(&workspace_root).await?;
    let canonical_root = fs::canonicalize(&workspace_root).await?;
    let control_root = workspace_root.join(CANDIDATE_CONTROL_DIRECTORY);
    match fs::symlink_metadata(&control_root).await {
        Ok(metadata) if !metadata.is_dir() || metadata.file_type().is_symlink() => {
            anyhow::bail!("CANDIDATE_GIT_CONTROL_ROOT_INVALID");
        }
        Ok(_) => {}
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            fs::create_dir(&control_root).await?;
        }
        Err(error) => return Err(error.into()),
    }
    let canonical_control_root = fs::canonicalize(&control_root).await?;
    if !canonical_control_root.starts_with(&canonical_root) {
        anyhow::bail!("CANDIDATE_GIT_CONTROL_ROOT_ESCAPE");
    }
    let branch = format!(
        "openos/{}-{}-{}",
        spec.ticket_id,
        repository_folder.to_ascii_lowercase(),
        &spec.task_id.to_string()[..8]
    );
    let fetched_base_ref = format!("refs/openos/fetched-base/{}", spec.task_id);
    let pinned_base_ref = format!("refs/openos/base/{}", spec.task_id);
    let fetch_refspec = format!("+refs/heads/{}:{fetched_base_ref}", spec.base_ref);
    checked_with_timeout(
        Command::new("git").arg("-C").arg(spec.repository).args([
            "fetch",
            "--no-tags",
            spec.remote,
            &fetch_refspec,
        ]),
        "git fetch base ref",
        spec.timeout,
    )
    .await?;
    let base_sha = output_text(
        Command::new("git")
            .arg("-C")
            .arg(spec.repository)
            .args(["rev-parse", &fetched_base_ref]),
        "git base ref",
    )
    .await?;
    let base_sha = pinned_base_revision(spec.repository, &pinned_base_ref, &base_sha).await?;
    let existing_workspace = fs::try_exists(&workspace).await?;
    let git_dir = canonical_control_root
        .join(&repository_folder)
        .with_extension("git");
    initialize_candidate_repository(CandidateInitSpec {
        git_binary: spec.git_binary,
        source_repository: spec.repository,
        workspace: &workspace,
        git_dir: &git_dir,
        remote: spec.remote,
        remote_url: spec.remote_url,
        base_sha: &base_sha,
        branch: &branch,
        preserve_worktree: existing_workspace,
        git_timeout: spec.timeout,
    })
    .await?;
    let canonical_workspace = fs::canonicalize(&workspace).await?;
    if !canonical_workspace.starts_with(&canonical_root) {
        anyhow::bail!("WORKSPACE_PATH_ESCAPE");
    }
    let canonical_git_dir = fs::canonicalize(&git_dir).await?;
    if !canonical_git_dir.starts_with(&canonical_root)
        || canonical_git_dir.starts_with(&canonical_workspace)
    {
        anyhow::bail!("CANDIDATE_GIT_CONTROL_PATH_ESCAPE");
    }
    Ok((
        canonical_root,
        canonical_workspace,
        canonical_git_dir,
        base_sha,
        branch,
    ))
}

async fn pinned_base_revision(
    repository: &Path,
    pin_reference: &str,
    fetched_sha: &str,
) -> anyhow::Result<String> {
    let pin_exists = checked_output(Command::new("git").arg("-C").arg(repository).args([
        "show-ref",
        "--verify",
        "--quiet",
        pin_reference,
    ]))
    .await?
    .success();
    if !pin_exists {
        let created = checked_output(Command::new("git").arg("-C").arg(repository).args([
            "update-ref",
            pin_reference,
            fetched_sha,
            "0000000000000000000000000000000000000000",
        ]))
        .await?
        .success();
        if !created
            && !checked_output(Command::new("git").arg("-C").arg(repository).args([
                "show-ref",
                "--verify",
                "--quiet",
                pin_reference,
            ]))
            .await?
            .success()
        {
            anyhow::bail!("BASE_REVISION_PIN_CREATE_FAILED");
        }
    }
    let pinned_sha = output_text(
        Command::new("git")
            .arg("-C")
            .arg(repository)
            .args(["rev-parse", pin_reference]),
        "git pinned base ref",
    )
    .await?;
    if pinned_sha != fetched_sha
        && !checked_output(Command::new("git").arg("-C").arg(repository).args([
            "merge-base",
            "--is-ancestor",
            &pinned_sha,
            fetched_sha,
        ]))
        .await?
        .success()
    {
        anyhow::bail!("BASE_REVISION_REWRITTEN");
    }
    Ok(pinned_sha)
}

struct CandidateInitSpec<'a> {
    git_binary: &'a Path,
    source_repository: &'a Path,
    workspace: &'a Path,
    git_dir: &'a Path,
    remote: &'a str,
    remote_url: &'a str,
    base_sha: &'a str,
    branch: &'a str,
    preserve_worktree: bool,
    git_timeout: Duration,
}

async fn initialize_candidate_repository(spec: CandidateInitSpec<'_>) -> anyhow::Result<()> {
    let CandidateInitSpec {
        git_binary,
        source_repository,
        workspace,
        git_dir,
        remote,
        remote_url,
        base_sha,
        branch,
        preserve_worktree,
        git_timeout,
    } = spec;
    if preserve_worktree {
        if !fs::metadata(workspace)
            .await
            .is_ok_and(|metadata| metadata.is_dir())
        {
            anyhow::bail!("CANDIDATE_WORKTREE_INVALID");
        }
    } else {
        fs::create_dir_all(workspace).await?;
    }
    remove_path_without_following(git_dir).await?;
    remove_path_without_following(&workspace.join(".git")).await?;
    fs::create_dir_all(
        git_dir
            .parent()
            .ok_or_else(|| anyhow::anyhow!("CANDIDATE_GIT_CONTROL_PARENT_MISSING"))?,
    )
    .await?;
    checked_with_timeout(
        Command::new(git_binary)
            .args(["clone", "--bare", "--no-hardlinks"])
            .arg(source_repository)
            .arg(git_dir),
        "git clone candidate control repository",
        git_timeout,
    )
    .await?;
    let hooks = git_dir.join("hooks");
    remove_path_without_following(&hooks).await?;
    fs::create_dir(&hooks).await?;
    for (key, value) in [
        ("core.bare", "false"),
        ("core.hooksPath", "/dev/null"),
        ("core.fsmonitor", "false"),
        ("credential.helper", ""),
        ("credential.interactive", "false"),
        ("diff.external", ""),
    ] {
        checked(
            trusted_git_command(git_binary, workspace, git_dir)
                .args(["config", "--local", key, value]),
            "git configure candidate control repository",
        )
        .await?;
    }
    checked(
        trusted_git_command(git_binary, workspace, git_dir)
            .args(["config", "--local", "core.worktree"])
            .arg(workspace),
        "git configure candidate worktree",
    )
    .await?;
    checked(
        trusted_git_command(git_binary, workspace, git_dir)
            .args(["remote", "set-url", remote, remote_url]),
        "git set candidate remote",
    )
    .await?;
    checked(
        trusted_git_command(git_binary, workspace, git_dir).args([
            "update-ref",
            &format!("refs/heads/{branch}"),
            base_sha,
        ]),
        "git set candidate branch",
    )
    .await?;
    checked(
        trusted_git_command(git_binary, workspace, git_dir).args([
            "symbolic-ref",
            "HEAD",
            &format!("refs/heads/{branch}"),
        ]),
        "git select candidate branch",
    )
    .await?;
    checked(
        trusted_git_command(git_binary, workspace, git_dir).args(["read-tree", base_sha]),
        "git initialize candidate index",
    )
    .await?;
    if !preserve_worktree {
        checked(
            trusted_git_command(git_binary, workspace, git_dir).args(["checkout-index", "-a"]),
            "git checkout candidate worktree",
        )
        .await?;
    }
    restore_candidate_git_pointer(workspace, git_dir).await?;
    verify_candidate_repository(
        git_binary, workspace, git_dir, remote, remote_url, base_sha, branch,
    )
    .await
}

async fn verify_candidate_repository(
    git_binary: &Path,
    workspace: &Path,
    git_dir: &Path,
    remote: &str,
    remote_url: &str,
    base_sha: &str,
    branch: &str,
) -> anyhow::Result<()> {
    if !fs::metadata(git_dir)
        .await
        .is_ok_and(|metadata| metadata.is_dir())
        || fs::try_exists(git_dir.join("objects/info/alternates")).await?
    {
        anyhow::bail!("CANDIDATE_REPOSITORY_NOT_SELF_CONTAINED");
    }
    let actual_remote = output_text(
        trusted_git_command(git_binary, workspace, git_dir).args(["remote", "get-url", remote]),
        "candidate remote URL",
    )
    .await?;
    if parsed_remote_repository(&actual_remote) != parsed_remote_repository(remote_url) {
        anyhow::bail!("CANDIDATE_REMOTE_IDENTITY_MISMATCH");
    }
    let actual_branch = output_text(
        trusted_git_command(git_binary, workspace, git_dir).args(["branch", "--show-current"]),
        "candidate branch",
    )
    .await?;
    if actual_branch != branch {
        anyhow::bail!("WORKSPACE_BRANCH_MISMATCH");
    }
    if !checked_output(trusted_git_command(git_binary, workspace, git_dir).args([
        "merge-base",
        "--is-ancestor",
        base_sha,
        "HEAD",
    ]))
    .await?
    .success()
    {
        anyhow::bail!("CANDIDATE_BASE_NOT_ANCESTOR");
    }
    Ok(())
}

#[cfg(test)]
fn candidate_git_dir(workspace: &Path) -> anyhow::Result<PathBuf> {
    let root = workspace
        .parent()
        .ok_or_else(|| anyhow::anyhow!("CANDIDATE_WORKTREE_PARENT_MISSING"))?;
    let name = workspace
        .file_name()
        .ok_or_else(|| anyhow::anyhow!("CANDIDATE_WORKTREE_NAME_MISSING"))?;
    Ok(root
        .join(CANDIDATE_CONTROL_DIRECTORY)
        .join(name)
        .with_extension("git"))
}

async fn remove_path_without_following(path: &Path) -> anyhow::Result<()> {
    let Ok(metadata) = fs::symlink_metadata(path).await else {
        return Ok(());
    };
    if metadata.is_dir() && !metadata.file_type().is_symlink() {
        fs::remove_dir_all(path).await?;
    } else {
        fs::remove_file(path).await?;
    }
    Ok(())
}

async fn restore_candidate_git_pointer(workspace: &Path, git_dir: &Path) -> anyhow::Result<()> {
    let pointer = workspace.join(".git");
    remove_path_without_following(&pointer).await?;
    fs::write(&pointer, format!("gitdir: {}\n", git_dir.display())).await?;
    Ok(())
}

fn valid_git_branch_name(value: &str) -> bool {
    !value.is_empty()
        && !value.contains("..")
        && !value.contains("//")
        && !value.contains("@{")
        && !value.split('/').any(|component| component.starts_with('.'))
        && !value.ends_with('.')
        && !value.ends_with('/')
        && !value.ends_with(".lock")
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'/' | b'.' | b'_' | b'-'))
}

fn parse_workspace_dependencies(inputs: &Value) -> anyhow::Result<Vec<WorkspaceDependency>> {
    let dependencies = inputs
        .get("workspace_dependencies")
        .cloned()
        .unwrap_or_else(|| json!([]));
    let dependencies = serde_json::from_value::<Vec<WorkspaceDependency>>(dependencies)
        .context("WORKSPACE_DEPENDENCIES_INVALID")?;
    if dependencies.len() > 8 {
        anyhow::bail!("WORKSPACE_DEPENDENCY_LIMIT_EXCEEDED");
    }
    let mut destinations = std::collections::HashSet::new();
    let mut names = std::collections::HashSet::new();
    for dependency in &dependencies {
        validate_repository_remote(&dependency.remote_url, &dependency.external_repository_id)?;
        if dependency.provider.trim().is_empty()
            || !dependency
                .provider
                .bytes()
                .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'_' | b'-'))
            || safe_workspace_name(&dependency.name)? != dependency.name
            || dependency.base_ref.trim().is_empty()
            || !valid_relative_path(&dependency.source_path, true)
            || !valid_relative_path(&dependency.destination, false)
            || [
                CANDIDATE_CONTROL_DIRECTORY,
                ".openos-support",
                "qa-home",
                "qa-tmp",
            ]
            .iter()
            .any(|reserved| {
                dependency.destination == *reserved
                    || dependency.destination.starts_with(&format!("{reserved}/"))
            })
            || !names.insert(dependency.name.clone())
            || !destinations.insert(dependency.destination.clone())
        {
            anyhow::bail!("WORKSPACE_DEPENDENCY_POLICY_INVALID");
        }
    }
    Ok(dependencies)
}

fn valid_relative_path(value: &str, allow_current: bool) -> bool {
    let path = Path::new(value);
    !value.trim().is_empty()
        && !path.is_absolute()
        && (allow_current || value != ".")
        && path.components().all(|component| {
            matches!(component, Component::Normal(_))
                || (allow_current && matches!(component, Component::CurDir))
        })
}

async fn materialize_workspace_dependencies(
    dependencies: &[WorkspaceDependency],
    config: &Config,
    workspace_root: &Path,
    target_workspace: &Path,
    task_id: Uuid,
) -> anyhow::Result<Vec<(PathBuf, String)>> {
    let mut materialized = Vec::with_capacity(dependencies.len());
    for (index, dependency) in dependencies.iter().enumerate() {
        let repository = canonical_repository(
            &json!({"repository":dependency.repository}),
            &config.allowed_repositories,
            &dependency.remote_url,
            &config.git_remote,
            config.git_timeout,
        )
        .await?;
        verify_repository_remote(&repository, &config.git_remote, &dependency.remote_url).await?;
        let support = workspace_root
            .join(".openos-support")
            .join(safe_workspace_name(&dependency.name)?);
        let reference = format!("refs/openos/support/{task_id}/{index}");
        let pin_reference = format!("refs/openos/support-pins/{task_id}/{index}");
        let refspec = format!("+refs/heads/{}:{reference}", dependency.base_ref);
        checked_with_timeout(
            Command::new("git").arg("-C").arg(&repository).args([
                "fetch",
                "--no-tags",
                &dependency.remote_url,
                &refspec,
            ]),
            "git fetch workspace dependency",
            config.git_timeout,
        )
        .await?;
        let remote_head_sha = output_text(
            Command::new("git")
                .arg("-C")
                .arg(&repository)
                .args(["rev-parse", &reference]),
            "git workspace dependency ref",
        )
        .await?;
        let expected_sha =
            pinned_dependency_revision(&repository, &pin_reference, &remote_head_sha).await?;
        if fs::try_exists(&support).await? {
            verify_workspace_dependencies_clean(
                &config.git_binary,
                &[(support.clone(), expected_sha.clone())],
            )
            .await?;
        } else {
            fs::create_dir_all(support.parent().expect("support root")).await?;
            checked(
                Command::new("git")
                    .arg("-C")
                    .arg(&repository)
                    .args(["worktree", "add", "--detach"])
                    .arg(&support)
                    .arg(&pin_reference),
                "git workspace dependency add",
            )
            .await?;
            verify_workspace_dependencies_clean(
                &config.git_binary,
                &[(support.clone(), expected_sha.clone())],
            )
            .await?;
        }
        link_workspace_dependency(
            workspace_root,
            target_workspace,
            &support,
            &dependency.source_path,
            &dependency.destination,
        )
        .await?;
        materialized.push((support, expected_sha));
    }
    Ok(materialized)
}

async fn pinned_dependency_revision(
    repository: &Path,
    pin_reference: &str,
    remote_head_sha: &str,
) -> anyhow::Result<String> {
    let pin_exists = checked_output(Command::new("git").arg("-C").arg(repository).args([
        "show-ref",
        "--verify",
        "--quiet",
        pin_reference,
    ]))
    .await?
    .success();
    if !pin_exists {
        let zero_sha = "0000000000000000000000000000000000000000";
        let created = checked_output(Command::new("git").arg("-C").arg(repository).args([
            "update-ref",
            pin_reference,
            remote_head_sha,
            zero_sha,
        ]))
        .await?
        .success();
        if !created
            && !checked_output(Command::new("git").arg("-C").arg(repository).args([
                "show-ref",
                "--verify",
                "--quiet",
                pin_reference,
            ]))
            .await?
            .success()
        {
            anyhow::bail!("WORKSPACE_DEPENDENCY_PIN_CREATE_FAILED");
        }
    }
    let pinned_sha = output_text(
        Command::new("git")
            .arg("-C")
            .arg(repository)
            .args(["rev-parse", pin_reference]),
        "git workspace dependency pin",
    )
    .await?;
    if pinned_sha == remote_head_sha {
        return Ok(pinned_sha);
    }
    let ancestor = checked_output(Command::new("git").arg("-C").arg(repository).args([
        "merge-base",
        "--is-ancestor",
        &pinned_sha,
        remote_head_sha,
    ]))
    .await?;
    if !ancestor.success() {
        anyhow::bail!("WORKSPACE_DEPENDENCY_REVISION_MISMATCH");
    }
    Ok(pinned_sha)
}

async fn verify_workspace_dependencies_clean(
    git_binary: &Path,
    dependencies: &[(PathBuf, String)],
) -> anyhow::Result<()> {
    for (repository, expected_sha) in dependencies {
        let actual_sha = output_text(
            hardened_repository_git_command(git_binary, repository).args(["rev-parse", "HEAD"]),
            "workspace dependency revision",
        )
        .await?;
        let status = output_text(
            hardened_repository_git_command(git_binary, repository).args([
                "status",
                "--porcelain",
                "--untracked-files=all",
            ]),
            "workspace dependency status",
        )
        .await?;
        if actual_sha != *expected_sha || !status.is_empty() {
            anyhow::bail!("WORKSPACE_DEPENDENCY_MUTATED");
        }
    }
    Ok(())
}

fn hardened_repository_git_command(git_binary: &Path, repository: &Path) -> Command {
    let mut command = Command::new(git_binary);
    command
        .env_clear()
        .env("HOME", "/nonexistent")
        .env("PATH", "/usr/local/bin:/usr/bin:/bin")
        .env("LANG", "C.UTF-8")
        .env("GIT_CONFIG_NOSYSTEM", "1")
        .env("GIT_CONFIG_GLOBAL", "/dev/null")
        .env("GIT_ATTR_NOSYSTEM", "1")
        .env("GIT_TERMINAL_PROMPT", "0")
        .env("GIT_ASKPASS", "/bin/false")
        .env("SSH_ASKPASS", "/bin/false")
        .env("GIT_EDITOR", "true")
        .env("GIT_SEQUENCE_EDITOR", "true")
        .env("GIT_PAGER", "cat")
        .env("PAGER", "cat")
        .env("GIT_EXTERNAL_DIFF", "/bin/false")
        .arg("-C")
        .arg(repository)
        .args([
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "core.fsmonitor=false",
            "-c",
            "credential.helper=",
            "-c",
            "credential.interactive=false",
            "-c",
            "diff.external=",
        ]);
    command
}

async fn link_workspace_dependency(
    workspace_root: &Path,
    target_workspace: &Path,
    support: &Path,
    source_path: &str,
    destination: &str,
) -> anyhow::Result<()> {
    let source = fs::canonicalize(support.join(source_path))
        .await
        .context("WORKSPACE_DEPENDENCY_SOURCE_MISSING")?;
    let support = fs::canonicalize(support).await?;
    if !source.starts_with(&support) {
        anyhow::bail!("WORKSPACE_DEPENDENCY_SOURCE_ESCAPE");
    }
    let destination = workspace_root.join(destination);
    if destination == target_workspace || destination.starts_with(target_workspace) {
        anyhow::bail!("WORKSPACE_DEPENDENCY_TARGET_COLLISION");
    }
    if fs::symlink_metadata(&destination).await.is_ok() {
        if fs::canonicalize(&destination).await? != source {
            anyhow::bail!("WORKSPACE_DEPENDENCY_DESTINATION_COLLISION");
        }
        return Ok(());
    }
    let parent = destination
        .parent()
        .ok_or_else(|| anyhow::anyhow!("WORKSPACE_DEPENDENCY_DESTINATION_INVALID"))?;
    fs::create_dir_all(parent).await?;
    let canonical_root = fs::canonicalize(workspace_root).await?;
    let canonical_parent = fs::canonicalize(parent).await?;
    if !canonical_parent.starts_with(&canonical_root) {
        anyhow::bail!("WORKSPACE_DEPENDENCY_DESTINATION_ESCAPE");
    }
    fs::symlink(&source, &destination).await?;
    Ok(())
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

struct EphemeralDirectory(PathBuf);

impl Drop for EphemeralDirectory {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
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
            session_id: Some(job.task_id),
            agent_id: None,
            revision_id: None,
            producer: "opencode-hacn".into(),
            sequence: (index + 1) as u32,
            scope: CognitiveScope {
                r#type: CognitiveScopeType::Session,
                id: job.task_id,
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

struct OpenCodeSpec<'a> {
    config: &'a Config,
    job: &'a WorkerJob,
    run_id: Uuid,
    workspace: &'a Path,
    git_dir: &'a Path,
    repository: &'a Path,
    workspace_id: &'a str,
    workspace_dependencies: &'a [(PathBuf, String)],
    prompt: &'a str,
    store: &'a RunStore,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum CapturedStreamName {
    Stdout,
    Stderr,
}

impl CapturedStreamName {
    fn error_suffix(self) -> &'static str {
        match self {
            Self::Stdout => "STDOUT",
            Self::Stderr => "STDERR",
        }
    }
}

struct CapturedStream {
    data: Vec<u8>,
    overflowed: bool,
}

#[derive(Debug)]
struct BoundedProcessOutput {
    status: ExitStatus,
    stdout: Vec<u8>,
    stderr: Vec<u8>,
}

fn require_sandbox_initialized(label: &str, output: &BoundedProcessOutput) -> anyhow::Result<()> {
    if output.status.code() == Some(SANDBOX_INIT_FAILURE_EXIT_CODE)
        && String::from_utf8_lossy(&output.stderr).contains("OPENAGENTS_SANDBOX_INIT_FAILED:")
    {
        anyhow::bail!("{label}_SANDBOX_INIT_FAILED");
    }
    Ok(())
}

async fn drain_bounded_stream<R: AsyncRead + Unpin>(
    mut reader: R,
    limit: usize,
    stream: CapturedStreamName,
    overflow: mpsc::Sender<CapturedStreamName>,
) -> std::io::Result<CapturedStream> {
    let mut data = Vec::with_capacity(limit.min(64 * 1024));
    let mut buffer = [0_u8; 8192];
    let mut overflowed = false;
    loop {
        let count = reader.read(&mut buffer).await?;
        if count == 0 {
            break;
        }
        let remaining = limit.saturating_sub(data.len());
        data.extend_from_slice(&buffer[..count.min(remaining)]);
        if count > remaining && !overflowed {
            overflowed = true;
            let _ = overflow.try_send(stream);
        }
    }
    Ok(CapturedStream { data, overflowed })
}

async fn finish_stream_capture(
    mut task: JoinHandle<std::io::Result<CapturedStream>>,
    label: &str,
) -> anyhow::Result<CapturedStream> {
    match timeout(PROCESS_DRAIN_TIMEOUT, &mut task).await {
        Ok(result) => Ok(result.context("bounded stream task failed")??),
        Err(_) => {
            task.abort();
            let _ = task.await;
            anyhow::bail!("{label}_PIPE_DRAIN_TIMEOUT");
        }
    }
}

fn spawn_isolated_process(command: &mut Command) -> std::io::Result<Child> {
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt as _;
        command.as_std_mut().process_group(0);
    }
    command.spawn()
}

fn terminate_process_group(process_group: u32) {
    #[cfg(unix)]
    {
        if let Ok(process_group) = i32::try_from(process_group) {
            // The child is created as its own process-group leader. A negative
            // PID targets every descendant that has not deliberately escaped.
            unsafe {
                libc::kill(-process_group, libc::SIGKILL);
            }
        }
    }
    #[cfg(not(unix))]
    let _ = process_group;
}

async fn bounded_child_output(
    mut child: Child,
    label: &str,
    duration: Duration,
    stdout_limit: usize,
    stderr_limit: usize,
    cancellation: Option<(&RunStore, Uuid)>,
) -> anyhow::Result<BoundedProcessOutput> {
    let process_group = child.id().context("bounded child pid missing")?;
    let stdout = child
        .stdout
        .take()
        .context("bounded child stdout missing")?;
    let stderr = child
        .stderr
        .take()
        .context("bounded child stderr missing")?;
    let (overflow_tx, mut overflow_rx) = mpsc::channel(2);
    let stdout_task = tokio::spawn(drain_bounded_stream(
        stdout,
        stdout_limit,
        CapturedStreamName::Stdout,
        overflow_tx.clone(),
    ));
    let stderr_task = tokio::spawn(drain_bounded_stream(
        stderr,
        stderr_limit,
        CapturedStreamName::Stderr,
        overflow_tx,
    ));
    enum Completion {
        Exited(std::io::Result<ExitStatus>),
        Timeout,
        Cancelled,
        Overflow(CapturedStreamName),
    }
    let cancellation_wait = async {
        if let Some((store, run_id)) = cancellation {
            wait_cancelled(store, run_id).await;
        } else {
            pending::<()>().await;
        }
    };
    let overflow_wait = async {
        match overflow_rx.recv().await {
            Some(stream) => stream,
            None => pending::<CapturedStreamName>().await,
        }
    };
    let completion = tokio::select! {
        status = child.wait() => Completion::Exited(status),
        _ = sleep(duration) => Completion::Timeout,
        _ = cancellation_wait => Completion::Cancelled,
        stream = overflow_wait => Completion::Overflow(stream),
    };
    let failure = match completion {
        Completion::Exited(_) => None,
        Completion::Timeout => Some(format!("{label}_TIMEOUT")),
        Completion::Cancelled => Some("RUN_CANCELLED".to_string()),
        Completion::Overflow(stream) => {
            Some(format!("{label}_{}_LIMIT_EXCEEDED", stream.error_suffix()))
        }
    };
    if failure.is_some() {
        terminate_process_group(process_group);
        let _ = child.start_kill();
        let _ = timeout(PROCESS_DRAIN_TIMEOUT, child.wait()).await;
    } else {
        // A successful direct child may have daemonized descendants that still
        // hold inherited pipes. No untrusted process survives its command.
        terminate_process_group(process_group);
    }
    let (stdout, stderr) = tokio::join!(
        finish_stream_capture(stdout_task, label),
        finish_stream_capture(stderr_task, label)
    );
    if let Some(failure) = failure {
        // Both capture tasks were drained or aborted above. Preserve the primary
        // timeout/cancellation/overflow reason even if a descendant held a pipe.
        let _ = stdout;
        let _ = stderr;
        anyhow::bail!(failure);
    }
    let stdout = stdout?;
    let stderr = stderr?;
    let status = match completion {
        Completion::Exited(status) => status?,
        _ => unreachable!("failure completions return above"),
    };
    if stdout.overflowed {
        anyhow::bail!("{label}_STDOUT_LIMIT_EXCEEDED");
    }
    if stderr.overflowed {
        anyhow::bail!("{label}_STDERR_LIMIT_EXCEEDED");
    }
    Ok(BoundedProcessOutput {
        status,
        stdout: stdout.data,
        stderr: stderr.data,
    })
}

async fn run_opencode(spec: OpenCodeSpec<'_>) -> anyhow::Result<EngineOutput> {
    let OpenCodeSpec {
        config,
        job,
        run_id,
        workspace,
        git_dir,
        repository,
        workspace_id,
        workspace_dependencies,
        prompt,
        store,
    } = spec;
    restore_candidate_git_pointer(workspace, git_dir).await?;
    let runtime_home = prepare_opencode_runtime_home(run_id, workspace_dependencies).await?;
    let hacn_store_root = managed_hacn_store_root(
        &config.managed_root,
        job.organization_id,
        workspace_id,
        job.task_id,
    );
    fs::create_dir_all(&hacn_store_root)
        .await
        .context("HACN_STORE_ROOT_CREATE_FAILED")?;
    let cognitive_event_path = runtime_home.0.join("hacn-cognitive.ndjson");
    fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&cognitive_event_path)
        .await
        .context("HACN_EVENT_FILE_CREATE_FAILED")?;
    let mut command = Command::new(&config.sandbox_binary);
    for path in ["/usr", "/bin", "/lib", "/etc"] {
        command.args(["--ro", path]);
    }
    command.arg("--ro").arg(repository);
    command.arg("--ro").arg(git_dir);
    for (path, _) in workspace_dependencies {
        command.arg("--ro").arg(path);
    }
    command
        .args(["--rw", "/dev"])
        .arg("--rw")
        .arg(workspace)
        .arg("--rw")
        .arg(&runtime_home.0)
        .arg("--rw")
        .arg(&hacn_store_root)
        .arg("--")
        .arg(&config.opencode_binary)
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
        .arg(prompt);
    command
        .current_dir(workspace)
        .env_clear()
        .env("HOME", &runtime_home.0)
        .env("TMPDIR", runtime_home.0.join("tmp"))
        // OpenCode uses this explicit base for its per-UID shell directory on Unix.
        .env("CLAUDE_CODE_TMPDIR", runtime_home.0.join("tmp"))
        .env("PATH", "/usr/local/bin:/usr/bin:/bin")
        .env("SHELL", &config.shell_binary)
        .env("LANG", "C.UTF-8")
        .env("NO_COLOR", "1")
        .env("PYTHONDONTWRITEBYTECODE", "1")
        .env("OPENCODE_BASE_URL", &config.llm_base_url)
        .env("OPENCODE_API_KEY", &config.llm_api_key)
        .env("OPENCODE_MODEL", &config.llm_model)
        .env("OPENCODE_INVOKED_BY", "openagents-rust")
        .env("OPENCODE_HACN", "1")
        .env("CLAUDE_CODE_DISABLE_BACKGROUND_TASKS", "1")
        .env("OPENOS_MANAGED_RUNTIME", "1")
        .env("OPENOS_ORGANIZATION_ID", job.organization_id.to_string())
        .env("OPENOS_WORKSPACE_ID", workspace_id)
        .env("OPENOS_SESSION_ID", job.task_id.to_string())
        .env("OPENOS_HACN_STORE_DIR", &hacn_store_root)
        .env("OPENOS_HACN_EVENT_FILE", &cognitive_event_path)
        .env("OPENTICKET_TICKET_ID", job.ticket_id.to_string())
        .env("OPENTICKET_CORRELATION_ID", job.correlation_id.to_string())
        .kill_on_drop(true)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    let child = spawn_isolated_process(&mut command)?;
    let output = bounded_child_output(
        child,
        "OPENCODE",
        config.opencode_timeout,
        MAX_OPENCODE_STDOUT_BYTES,
        MAX_OPENCODE_STDERR_BYTES,
        Some((store, run_id)),
    )
    .await?;
    require_sandbox_initialized("OPENCODE", &output)?;
    let stdout = String::from_utf8_lossy(&output.stdout).into_owned();
    let stderr = String::from_utf8_lossy(&output.stderr).into_owned();
    let mut session = None;
    for line in stdout.lines().take(MAX_OPENCODE_EVIDENCE_EVENTS) {
        if let Ok(event) = serde_json::from_str::<Value>(line) {
            session = session.or_else(|| session_id(&event));
            store
                .update(
                    run_id,
                    RunStatus::Running,
                    "opencode.event",
                    safe_event(&event),
                )
                .await;
        }
    }
    let cognitive_events = read_cognitive_events(&cognitive_event_path).await?;
    Ok(EngineOutput {
        exit_status: output.status.code().unwrap_or(-1),
        stdout,
        stderr,
        session_id: session,
        cognitive_events,
    })
}

fn managed_hacn_store_root(
    managed_root: &Path,
    organization_id: Uuid,
    workspace_id: &str,
    session_id: Uuid,
) -> PathBuf {
    let namespace = format!("{organization_id}\0{workspace_id}\0{session_id}");
    let digest = format!("{:x}", Sha256::digest(namespace.as_bytes()));
    managed_root.join(".hacn").join(digest)
}

async fn prepare_opencode_runtime_home(
    run_id: Uuid,
    workspace_dependencies: &[(PathBuf, String)],
) -> anyhow::Result<EphemeralDirectory> {
    let home = std::env::temp_dir().join(format!("openagents-opencode-{run_id}"));
    fs::create_dir(&home)
        .await
        .context("OPENCODE_RUNTIME_HOME_CREATE_FAILED")?;
    let guard = EphemeralDirectory(home.clone());
    let settings_directory = home.join(".opencode");
    fs::create_dir(&settings_directory).await?;
    fs::create_dir(home.join("tmp")).await?;

    let mut additional_directories = Vec::with_capacity(workspace_dependencies.len());
    let mut denied_dependency_edits = Vec::with_capacity(workspace_dependencies.len());
    for (path, _) in workspace_dependencies {
        let path = path.display().to_string();
        additional_directories.push(path.clone());
        denied_dependency_edits.push(format!("Edit({path}/**)",));
    }
    let settings = json!({
        "autoMemoryEnabled": false,
        "permissions": {
            "additionalDirectories": additional_directories,
            "deny": denied_dependency_edits
        }
    });
    fs::write(
        settings_directory.join("settings.json"),
        serde_json::to_vec(&settings)?,
    )
    .await?;
    Ok(guard)
}

async fn read_cognitive_events(path: &Path) -> anyhow::Result<Vec<Value>> {
    let result = async {
        let metadata = fs::metadata(path).await?;
        if metadata.len() > MAX_COGNITIVE_EVENT_FILE_BYTES {
            anyhow::bail!("HACN_EVENT_FILE_TOO_LARGE");
        }
        let content = fs::read_to_string(path).await?;
        let mut events = Vec::new();
        for line in content.lines() {
            if events.len() >= MAX_COGNITIVE_EVENTS {
                break;
            }
            if line.len() > MAX_COGNITIVE_EVENT_BYTES {
                anyhow::bail!("HACN_EVENT_TOO_LARGE");
            }
            let Ok(event) = serde_json::from_str::<Value>(line) else {
                continue;
            };
            if let Some(event) = sanitized_cognitive_event(&event)? {
                events.push(event);
            }
        }
        Ok(events)
    }
    .await;
    let _ = fs::remove_file(path).await;
    result
}

fn sanitized_cognitive_event(event: &Value) -> anyhow::Result<Option<Value>> {
    let Some(event_type @ ("hypothesis_updated" | "expected_observed_mismatch")) =
        event.get("type").and_then(Value::as_str)
    else {
        return Ok(None);
    };
    let expected = event.get("expected").and_then(sanitized_cognitive_payload);
    let observed = event.get("observed").and_then(sanitized_cognitive_payload);
    if expected.is_none() && observed.is_none() {
        return Ok(None);
    }
    let sanitized = json!({
        "type":event_type,
        "confidence":event.get("confidence").and_then(Value::as_f64).unwrap_or(0.5).clamp(0.0, 1.0),
        "expected":expected,
        "observed":observed,
        "observedAt":event.get("observedAt").and_then(Value::as_str)
    });
    let serialized = serde_json::to_string(&sanitized)?;
    if serialized.len() > MAX_COGNITIVE_EVENT_BYTES {
        anyhow::bail!("HACN_EVENT_TOO_LARGE");
    }
    if detect_secret(&serialized).is_some() {
        anyhow::bail!("HACN_EVENT_SECRET_REJECTED");
    }
    Ok(Some(sanitized))
}

fn sanitized_cognitive_payload(value: &Value) -> Option<Value> {
    let source = value.as_object()?;
    let mut safe = serde_json::Map::new();
    if let Some(key) = source.get("key").and_then(Value::as_str) {
        let (prefix, identity) = key.split_once(':')?;
        if matches!(prefix, "hacn.relevance" | "hacn.prediction") && !identity.is_empty() {
            let digest = format!("{:x}", Sha256::digest(identity.as_bytes()));
            safe.insert(
                "key".into(),
                Value::String(format!("{prefix}:sha256:{}", &digest[..24])),
            );
        }
    }
    if let Some(surfaced) = source.get("surfaced").and_then(Value::as_bool) {
        safe.insert("surfaced".into(), Value::Bool(surfaced));
    }
    if let Some(details) = source.get("value").and_then(Value::as_object) {
        let mut safe_details = serde_json::Map::new();
        for key in ["fromState", "toState", "fromWeight", "toWeight"] {
            if let Some(number) = details.get(key).and_then(Value::as_f64) {
                if let Some(number) = serde_json::Number::from_f64(number) {
                    safe_details.insert(key.into(), Value::Number(number));
                }
            }
        }
        if !safe_details.is_empty() {
            safe.insert("value".into(), Value::Object(safe_details));
        }
    }
    (!safe.is_empty()).then_some(Value::Object(safe))
}

struct QaExecutionSpec<'a> {
    workspace: &'a Path,
    git_dir: &'a Path,
    dependencies: &'a [(PathBuf, String)],
    commands: &'a [String],
    run_id: Uuid,
    store: &'a RunStore,
    sandbox_binary: &'a Path,
    shell_binary: &'a Path,
    timeout: Duration,
}

async fn run_tests(spec: QaExecutionSpec<'_>) -> anyhow::Result<Vec<TestEvidence>> {
    let QaExecutionSpec {
        workspace,
        git_dir,
        dependencies,
        commands,
        run_id,
        store,
        sandbox_binary,
        shell_binary,
        timeout: qa_timeout,
    } = spec;
    let mut evidence = Vec::new();
    let qa_home = workspace.parent().expect("run root").join("qa-home");
    let qa_tmp = workspace.parent().expect("run root").join("qa-tmp");
    fs::create_dir_all(&qa_home).await?;
    fs::create_dir_all(&qa_tmp).await?;
    for command in commands {
        ensure_not_cancelled(store, run_id).await?;
        restore_candidate_git_pointer(workspace, git_dir).await?;
        store
            .update(
                run_id,
                RunStatus::Running,
                "test.started",
                json!({"command":command}),
            )
            .await;
        let mut process = Command::new(sandbox_binary);
        for path in ["/usr", "/bin", "/lib", "/etc"] {
            process.args(["--ro", path]);
        }
        process.arg("--ro").arg(git_dir);
        for (path, _) in dependencies {
            process.arg("--ro").arg(path);
        }
        process
            .args(["--rw", "/dev"])
            .arg("--rw")
            .arg(workspace)
            .arg("--rw")
            .arg(&qa_home)
            .arg("--rw")
            .arg(&qa_tmp)
            .arg("--")
            .arg(shell_binary)
            .arg("-lc")
            .arg(command)
            .current_dir(workspace)
            .env_clear()
            .env("PATH", "/usr/local/bin:/usr/bin:/bin")
            .env("HOME", &qa_home)
            .env("TMPDIR", &qa_tmp)
            .env("SHELL", shell_binary)
            .env("CI", "1")
            .env("NO_COLOR", "1")
            .env("COREPACK_ENABLE_DOWNLOAD_PROMPT", "0")
            .env("PYTHONDONTWRITEBYTECODE", "1")
            .kill_on_drop(true)
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        let child = spawn_isolated_process(&mut process)?;
        let output = match bounded_child_output(
            child,
            "QA",
            qa_timeout,
            MAX_QA_STDOUT_BYTES,
            MAX_QA_STDERR_BYTES,
            Some((store, run_id)),
        )
        .await
        {
            Ok(output) => output,
            Err(error) => {
                let failure = error.to_string();
                if failure == "RUN_CANCELLED" {
                    return Err(error);
                }
                store
                    .update(
                        run_id,
                        RunStatus::Failed,
                        "test.failed",
                        json!({"command":command,"error":failure}),
                    )
                    .await;
                let path = workspace
                    .parent()
                    .expect("run root")
                    .join(format!("{run_id}-test-{}.log", evidence.len() + 1));
                fs::write(&path, format!("{failure}\n")).await?;
                evidence.push(TestEvidence {
                    command: command.clone(),
                    exit_status: -1,
                    passed: 0,
                    failed: 1,
                    output_uri: Some(file_uri(&path)),
                });
                continue;
            }
        };
        require_sandbox_initialized("QA", &output)?;
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
    }
    restore_candidate_git_pointer(workspace, git_dir).await?;
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

#[derive(Debug)]
struct VerifiedGitDelivery {
    evidence: GitEvidence,
    signed_commits: Vec<String>,
}

struct CommitSpec<'a> {
    workspace: &'a Path,
    git_dir: &'a Path,
    repository: &'a Path,
    base_sha: &'a str,
    expected_branch: &'a str,
    ticket_id: Uuid,
    run_id: Uuid,
    sign_commit: bool,
    git_binary: &'a Path,
    signing_key_b64: Option<&'a str>,
    git_timeout: Duration,
}

struct EphemeralSigningIdentity {
    home: tempfile::TempDir,
    fingerprint: String,
}

impl Drop for EphemeralSigningIdentity {
    fn drop(&mut self) {
        let _ = std::process::Command::new("gpgconf")
            .env_clear()
            .env("HOME", self.home.path())
            .env("GNUPGHOME", self.home.path())
            .env("PATH", "/usr/local/bin:/usr/bin:/bin")
            .args(["--kill", "gpg-agent"])
            .status();
    }
}

async fn prepare_ephemeral_signing_identity(
    encoded: &str,
    duration: Duration,
) -> anyhow::Result<EphemeralSigningIdentity> {
    let key = Zeroizing::new(
        base64::engine::general_purpose::STANDARD
            .decode(encoded.trim())
            .context("GIT_SIGNING_KEY_INVALID")?,
    );
    let home = tempfile::Builder::new()
        .prefix("openagents-signing-")
        .tempdir()
        .context("GIT_SIGNING_HOME_CREATE_FAILED")?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt as _;
        std::fs::set_permissions(home.path(), std::fs::Permissions::from_mode(0o700))?;
    }
    let mut command = Command::new("gpg");
    command
        .env_clear()
        .env("HOME", home.path())
        .env("GNUPGHOME", home.path())
        .env("PATH", "/usr/local/bin:/usr/bin:/bin")
        .args(["--batch", "--no-tty", "--import"])
        .kill_on_drop(true)
        .stdin(Stdio::piped())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    let mut child = command.spawn()?;
    let mut stdin = child
        .stdin
        .take()
        .context("GIT_SIGNING_IMPORT_STDIN_MISSING")?;
    stdin.write_all(&key).await?;
    drop(stdin);
    let status = timeout(duration, child.wait())
        .await
        .map_err(|_| anyhow::anyhow!("GIT_SIGNING_IMPORT_TIMEOUT"))??;
    if !status.success() {
        anyhow::bail!("GIT_SIGNING_IMPORT_FAILED");
    }
    let mut listing = Command::new("gpg");
    listing
        .env_clear()
        .env("HOME", home.path())
        .env("GNUPGHOME", home.path())
        .env("PATH", "/usr/local/bin:/usr/bin:/bin")
        .args(["--batch", "--no-tty", "--with-colons", "--list-secret-keys"])
        .kill_on_drop(true);
    let output = timeout(duration, listing.output())
        .await
        .map_err(|_| anyhow::anyhow!("GIT_SIGNING_LIST_TIMEOUT"))??;
    if !output.status.success() {
        anyhow::bail!("GIT_SIGNING_LIST_FAILED");
    }
    let listing = String::from_utf8_lossy(&output.stdout);
    let fingerprint = listing
        .lines()
        .find(|line| line.starts_with("fpr:"))
        .and_then(|line| line.split(':').nth(9))
        .filter(|value| !value.is_empty())
        .context("GIT_SIGNING_FINGERPRINT_MISSING")?
        .to_owned();
    Ok(EphemeralSigningIdentity { home, fingerprint })
}

async fn commit_changes(spec: CommitSpec<'_>) -> anyhow::Result<VerifiedGitDelivery> {
    let CommitSpec {
        workspace,
        git_dir,
        repository,
        base_sha,
        expected_branch,
        ticket_id,
        run_id,
        sign_commit,
        git_binary,
        signing_key_b64,
        git_timeout,
    } = spec;
    let branch = output_text(
        trusted_git_command(git_binary, workspace, git_dir).args(["branch", "--show-current"]),
        "git branch",
    )
    .await?;
    if branch != expected_branch || !valid_git_branch_name(&branch) {
        anyhow::bail!("GIT_DELIVERY_BRANCH_MISMATCH");
    }
    if !checked_output(trusted_git_command(git_binary, workspace, git_dir).args([
        "merge-base",
        "--is-ancestor",
        base_sha,
        "HEAD",
    ]))
    .await?
    .success()
    {
        anyhow::bail!("GIT_BASE_NOT_ANCESTOR");
    }
    let commits_before = commits_since(git_binary, workspace, git_dir, base_sha).await?;
    checked(
        trusted_git_command(git_binary, workspace, git_dir).args(["add", "-A"]),
        "git add",
    )
    .await?;
    let diff = checked_output(
        trusted_git_command(git_binary, workspace, git_dir).args(["diff", "--cached", "--quiet"]),
    )
    .await?;
    if !diff.success() && !commits_before.is_empty() {
        anyhow::bail!("GIT_COMMIT_TOPOLOGY_DIRTY_AFTER_COMMIT");
    }
    let mut signing = None;
    if !diff.success() {
        if sign_commit {
            signing = Some(
                prepare_ephemeral_signing_identity(
                    signing_key_b64.context("GIT_SIGNING_KEY_REQUIRED")?,
                    git_timeout,
                )
                .await?,
            );
        }
        let mut command = trusted_git_command(git_binary, workspace, git_dir);
        if let Some(identity) = signing.as_ref() {
            command.env("GNUPGHOME", identity.home.path());
        }
        checked_with_timeout(
            command
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
                    "-c",
                    "gpg.format=openpgp",
                    "-c",
                    "gpg.program=gpg",
                ])
                .arg("-c")
                .arg(format!(
                    "user.signingkey={}",
                    signing
                        .as_ref()
                        .map(|identity| identity.fingerprint.as_str())
                        .unwrap_or("")
                ))
                .args(["commit", "-m"])
                .arg(format!("OpenOS delivery {ticket_id} ({run_id})")),
            "git commit",
            git_timeout,
        )
        .await?;
    }
    let sha = output_text(
        trusted_git_command(git_binary, workspace, git_dir).args(["rev-parse", "HEAD"]),
        "git rev-parse",
    )
    .await?;
    if sha == base_sha {
        anyhow::bail!("OPENCODE_PRODUCED_NO_CHANGES");
    }
    let commits = commits_since(git_binary, workspace, git_dir, base_sha).await?;
    if commits.len() != 1 || commits[0] != sha {
        anyhow::bail!("GIT_COMMIT_TOPOLOGY_INVALID");
    }
    let parent = output_text(
        trusted_git_command(git_binary, workspace, git_dir).args(["rev-parse", "HEAD^"]),
        "git commit parent",
    )
    .await?;
    if parent != base_sha {
        anyhow::bail!("GIT_COMMIT_PARENT_MISMATCH");
    }
    let status = output_text(
        trusted_git_command(git_binary, workspace, git_dir).args(["status", "--porcelain"]),
        "git status",
    )
    .await?;
    if !status.is_empty() {
        anyhow::bail!("GIT_WORKTREE_DIRTY_AFTER_COMMIT");
    }
    if signing.is_none() && sign_commit {
        signing = Some(
            prepare_ephemeral_signing_identity(
                signing_key_b64.context("GIT_SIGNING_KEY_REQUIRED")?,
                git_timeout,
            )
            .await?,
        );
    }
    for commit in &commits {
        let mut command = trusted_git_command(git_binary, workspace, git_dir);
        command
            .env(
                "GNUPGHOME",
                signing
                    .as_ref()
                    .context("GIT_SIGNING_IDENTITY_REQUIRED")?
                    .home
                    .path(),
            )
            .args(["-c", "gpg.format=openpgp", "-c", "gpg.program=gpg"]);
        checked(
            command.args(["verify-commit", commit]),
            "git verify signed commit",
        )
        .await?;
    }
    Ok(VerifiedGitDelivery {
        evidence: GitEvidence {
            repository: repository.display().to_string(),
            worktree: workspace.display().to_string(),
            branch,
            commit_sha: sha,
            clean: true,
            pushed: false,
            remote: None,
        },
        signed_commits: commits,
    })
}

async fn verify_committed_delivery_unchanged(
    git_binary: &Path,
    workspace: &Path,
    git_dir: &Path,
    expected_commit: &str,
) -> anyhow::Result<()> {
    let head = output_text(
        trusted_git_command(git_binary, workspace, git_dir).args(["rev-parse", "HEAD"]),
        "git rev-parse after QA",
    )
    .await?;
    if head != expected_commit {
        anyhow::bail!("QA_CHANGED_DELIVERY_COMMIT");
    }
    let status = output_text(
        trusted_git_command(git_binary, workspace, git_dir).args(["status", "--porcelain"]),
        "git status after QA",
    )
    .await?;
    if !status.is_empty() {
        anyhow::bail!("QA_MUTATED_DELIVERY_WORKTREE");
    }
    Ok(())
}

async fn commits_since(
    git_binary: &Path,
    workspace: &Path,
    git_dir: &Path,
    base_sha: &str,
) -> anyhow::Result<Vec<String>> {
    let range = format!("{base_sha}..HEAD");
    let output = output_text(
        trusted_git_command(git_binary, workspace, git_dir).args(["rev-list", "--reverse", &range]),
        "git commit range",
    )
    .await?;
    Ok(output
        .lines()
        .map(str::trim)
        .filter(|line| !line.is_empty())
        .map(str::to_owned)
        .collect())
}

async fn persist_opencode_evidence(
    root: &Path,
    run_id: Uuid,
    data: &str,
) -> anyhow::Result<WorkerArtifact> {
    let events = data
        .lines()
        .filter_map(|line| serde_json::from_str::<Value>(line).ok())
        .take(MAX_OPENCODE_EVIDENCE_EVENTS)
        .map(|event| safe_event(&event))
        .collect::<Vec<_>>();
    let event_count = events.len();
    let evidence = json!({
        "schema":"openos.opencode-execution-evidence/v1",
        "run_id":run_id,
        "events":events,
        "truncated":data.lines().count() > MAX_OPENCODE_EVIDENCE_EVENTS
    });
    let serialized = serde_json::to_vec_pretty(&evidence)?;
    let path = root.join(format!("{run_id}-opencode-evidence-v1.json"));
    fs::write(&path, &serialized).await?;
    let hash = format!("{:x}", Sha256::digest(&serialized));
    Ok(WorkerArtifact {
        kind: "opencode_execution_evidence".into(),
        name: "Redacted OpenCode execution evidence v1".into(),
        uri: file_uri(&path),
        sha256: Some(hash),
        metadata: json!({
            "schema":"openos.opencode-execution-evidence/v1",
            "bytes":serialized.len(),
            "event_count":event_count,
            "raw_jsonl_persisted":false
        }),
    })
}

async fn checked(command: &mut Command, name: &str) -> anyhow::Result<()> {
    let output = command.kill_on_drop(true).output().await?;
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
    let output = timeout(duration, command.kill_on_drop(true).output())
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
        .kill_on_drop(true)
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .await?)
}

async fn output_text(command: &mut Command, name: &str) -> anyhow::Result<String> {
    let output = command.kill_on_drop(true).output().await?;
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
        .and_then(safe_metadata_string)
}

fn safe_event(value: &Value) -> Value {
    json!({
        "schema":"openos.opencode-event/v1",
        "type":value.get("type").and_then(Value::as_str).and_then(safe_metadata_string),
        "subtype":value.get("subtype").and_then(Value::as_str).and_then(safe_metadata_string),
        "session_id":session_id(value)
    })
}

fn safe_metadata_string(value: &str) -> Option<String> {
    let value = value.trim();
    if value.is_empty() {
        return None;
    }
    if value.len() > 128
        || detect_secret(value).is_some()
        || !value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'_' | b'-' | b':'))
    {
        return Some("[redacted]".into());
    }
    Some(value.into())
}

fn opencode_terminal_status(stdout: &str) -> String {
    stdout
        .lines()
        .rev()
        .filter_map(|line| serde_json::from_str::<Value>(line).ok())
        .find(|event| event.get("type").and_then(Value::as_str) == Some("result"))
        .map(|event| {
            format!(
                "subtype={};is_error={}",
                event
                    .get("subtype")
                    .and_then(Value::as_str)
                    .unwrap_or("unknown"),
                event
                    .get("is_error")
                    .and_then(Value::as_bool)
                    .unwrap_or(true),
            )
        })
        .unwrap_or_else(|| "missing".into())
}

fn bounded_diagnostic(value: &str) -> String {
    let sanitized = sanitize(value);
    if sanitized.is_empty() {
        "none".into()
    } else {
        sanitized.chars().take(4096).collect()
    }
}

fn sanitize(value: &str) -> String {
    value
        .lines()
        .take(100)
        .map(|line| {
            let lower = line.trim().to_ascii_lowercase();
            if detect_secret(line).is_some()
                || lower.starts_with("authorization: bearer")
                || lower.starts_with("authorization=bearer")
                || lower.starts_with("sk-")
            {
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
    AssignedSecret,
    BearerToken,
    CloudAccessKey,
    Jwt,
    PemPrivateKey,
    ProviderToken,
    Webhook,
}

impl SecretKind {
    fn label(self) -> &'static str {
        match self {
            Self::ApiKey => "api_key",
            Self::AssignedSecret => "assigned_secret",
            Self::BearerToken => "bearer_token",
            Self::CloudAccessKey => "cloud_access_key",
            Self::Jwt => "jwt",
            Self::PemPrivateKey => "pem_private_key",
            Self::ProviderToken => "provider_token",
            Self::Webhook => "webhook",
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
    if ["sk-", "ghp_", "github_pat_", "glpat-", "xoxb-", "xoxp-"]
        .iter()
        .any(|prefix| has_prefixed_token(&lower, prefix, 16))
    {
        return Some(SecretKind::ProviderToken);
    }
    if ["akia", "asia"]
        .iter()
        .any(|prefix| has_prefixed_token(&lower, prefix, 16))
    {
        return Some(SecretKind::CloudAccessKey);
    }
    if contains_jwt(value) {
        return Some(SecretKind::Jwt);
    }
    if ["hooks.slack.com/services/", "discord.com/api/webhooks/"]
        .iter()
        .any(|marker| lower.contains(marker))
    {
        return Some(SecretKind::Webhook);
    }
    if ["authorization: bearer", "authorization=bearer"]
        .iter()
        .any(|marker| has_assigned_credential(&lower, marker, 1))
    {
        return Some(SecretKind::BearerToken);
    }
    if ["api_key=", "api-key=", "apikey="]
        .iter()
        .any(|marker| has_assigned_credential(&lower, marker, 16))
    {
        return Some(SecretKind::ApiKey);
    }
    if [
        "password=",
        "password:",
        "passwd=",
        "client_secret=",
        "client-secret=",
        "access_token=",
        "refresh_token=",
        "secret=",
        "secret_b64=",
        "token=",
        "token_b64=",
        "private_key_b64=",
    ]
    .iter()
    .any(|marker| has_assigned_credential(&lower, marker, 12))
    {
        return Some(SecretKind::AssignedSecret);
    }
    None
}

fn contains_jwt(value: &str) -> bool {
    value
        .split(|character: char| {
            !(character.is_ascii_alphanumeric() || matches!(character, '-' | '_' | '.'))
        })
        .any(|token| {
            let segments = token.split('.').collect::<Vec<_>>();
            segments.len() == 3
                && segments[0].starts_with("eyJ")
                && segments.iter().all(|segment| segment.len() >= 8)
        })
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

    fn passthrough_sandbox(root: &Path) -> PathBuf {
        let path = root.join("test-sandbox.sh");
        std::fs::write(
            &path,
            "#!/bin/sh\nwhile [ \"$1\" != \"--\" ]; do shift 2; done\nshift\nexec \"$@\"\n",
        )
        .unwrap();
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt as _;
            std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o700)).unwrap();
        }
        path
    }

    async fn run_test_qa(
        workspace: &Path,
        commands: &[String],
        run_id: Uuid,
        store: &RunStore,
        duration: Duration,
    ) -> anyhow::Result<Vec<TestEvidence>> {
        let root = workspace.parent().unwrap();
        let git_dir = root.join("test-git-control");
        fs::create_dir_all(&git_dir).await?;
        let sandbox = passthrough_sandbox(root);
        run_tests(QaExecutionSpec {
            workspace,
            git_dir: &git_dir,
            dependencies: &[],
            commands,
            run_id,
            store,
            sandbox_binary: &sandbox,
            shell_binary: Path::new("/bin/sh"),
            timeout: duration,
        })
        .await
    }

    fn externalize_test_git_dir(repository: &Path) -> PathBuf {
        let git_dir = repository.parent().unwrap().join(format!(
            "{}-git-control",
            repository.file_name().unwrap_or_default().to_string_lossy()
        ));
        std::fs::rename(repository.join(".git"), &git_dir).unwrap();
        std::fs::write(
            repository.join(".git"),
            format!("gitdir: {}\n", git_dir.display()),
        )
        .unwrap();
        git_dir
    }

    #[cfg(target_os = "linux")]
    fn test_signing_key() -> String {
        use std::os::unix::fs::PermissionsExt as _;

        let home = tempfile::tempdir().unwrap();
        std::fs::set_permissions(home.path(), std::fs::Permissions::from_mode(0o700)).unwrap();
        let status = std::process::Command::new("gpg")
            .env_clear()
            .env("HOME", home.path())
            .env("GNUPGHOME", home.path())
            .env("PATH", "/usr/local/bin:/usr/bin:/bin")
            .args([
                "--batch",
                "--passphrase",
                "",
                "--quick-generate-key",
                "OpenAgents Boundary Test <boundary@openos.local>",
                "rsa2048",
                "sign",
                "0",
            ])
            .status()
            .unwrap();
        assert!(status.success(), "failed to create test signing key");
        let output = std::process::Command::new("gpg")
            .env_clear()
            .env("HOME", home.path())
            .env("GNUPGHOME", home.path())
            .env("PATH", "/usr/local/bin:/usr/bin:/bin")
            .args(["--batch", "--passphrase", "", "--export-secret-keys"])
            .output()
            .unwrap();
        assert!(output.status.success());
        base64::engine::general_purpose::STANDARD.encode(output.stdout)
    }

    #[cfg(unix)]
    fn process_running(pid: i32) -> bool {
        #[cfg(target_os = "linux")]
        if let Ok(stat) = std::fs::read_to_string(format!("/proc/{pid}/stat")) {
            if stat
                .rsplit_once(") ")
                .and_then(|(_, fields)| fields.chars().next())
                == Some('Z')
            {
                return false;
            }
        }
        unsafe { libc::kill(pid, 0) == 0 }
    }

    #[cfg(unix)]
    async fn assert_process_stopped(pid_file: &Path) {
        let pid = timeout(Duration::from_secs(2), async {
            loop {
                if let Ok(value) = fs::read_to_string(pid_file).await {
                    if let Ok(pid) = value.trim().parse::<i32>() {
                        break pid;
                    }
                }
                sleep(Duration::from_millis(20)).await;
            }
        })
        .await
        .expect("descendant pid was not recorded");
        timeout(Duration::from_secs(2), async {
            while process_running(pid) {
                sleep(Duration::from_millis(20)).await;
            }
        })
        .await
        .expect("descendant survived process-group termination");
    }

    #[tokio::test]
    async fn qa_runs_every_declared_command_after_a_failure() {
        let temp = tempfile::tempdir().unwrap();
        let workspace = temp.path().join("repository");
        fs::create_dir(&workspace).await.unwrap();
        let evidence = run_test_qa(
            &workspace,
            &["exit 7".into(), "printf second-gate".into()],
            Uuid::new_v4(),
            &RunStore::new(),
            Duration::from_secs(5),
        )
        .await
        .unwrap();

        assert_eq!(evidence.len(), 2);
        assert_eq!(evidence[0].exit_status, 7);
        assert_eq!(evidence[1].exit_status, 0);
    }

    #[tokio::test]
    async fn qa_uses_scrubbed_environment_and_continues_after_timeout() {
        let temp = tempfile::tempdir().unwrap();
        let workspace = temp.path().join("repository");
        fs::create_dir(&workspace).await.unwrap();
        let expected_home = temp.path().join("qa-home");
        let environment_check = format!(
            "test \"$HOME\" = '{}' && test -z \"${{GH_TOKEN:-}}\" && test -n \"$PATH\"",
            expected_home.display()
        );
        let evidence = run_test_qa(
            &workspace,
            &["sleep 2".into(), environment_check],
            Uuid::new_v4(),
            &RunStore::new(),
            Duration::from_secs(1),
        )
        .await
        .unwrap();

        assert_eq!(evidence.len(), 2);
        assert_eq!(evidence[0].exit_status, -1);
        assert_eq!(evidence[1].exit_status, 0);
    }

    #[tokio::test]
    async fn qa_output_overflow_is_bounded_failed_and_does_not_block_later_gates() {
        let temp = tempfile::tempdir().unwrap();
        let workspace = temp.path().join("repository");
        fs::create_dir(&workspace).await.unwrap();
        let oversized = format!(
            "dd if=/dev/zero bs={} count=1 2>/dev/null",
            MAX_QA_STDOUT_BYTES + 1
        );
        let evidence = run_test_qa(
            &workspace,
            &[oversized, "printf later-gate".into()],
            Uuid::new_v4(),
            &RunStore::new(),
            Duration::from_secs(5),
        )
        .await
        .unwrap();

        assert_eq!(evidence.len(), 2);
        assert_eq!(evidence[0].exit_status, -1);
        assert_eq!(evidence[1].exit_status, 0);
        let failure_log = evidence[0]
            .output_uri
            .as_deref()
            .unwrap()
            .strip_prefix("file://")
            .unwrap();
        let failure_log = fs::read_to_string(failure_log).await.unwrap();
        assert_eq!(failure_log, "QA_STDOUT_LIMIT_EXCEEDED\n");
    }

    #[tokio::test]
    async fn qa_sandbox_initialization_failure_is_terminal() {
        let temp = tempfile::tempdir().unwrap();
        let workspace = temp.path().join("repository");
        let git_dir = temp.path().join("git-control");
        fs::create_dir(&workspace).await.unwrap();
        fs::create_dir(&git_dir).await.unwrap();
        let sandbox = temp.path().join("broken-sandbox.sh");
        std::fs::write(
            &sandbox,
            "#!/bin/sh\necho 'OPENAGENTS_SANDBOX_INIT_FAILED: test' >&2\nexit 125\n",
        )
        .unwrap();
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt as _;
            std::fs::set_permissions(&sandbox, std::fs::Permissions::from_mode(0o700)).unwrap();
        }
        let later = temp.path().join("later-ran");
        let commands = vec![
            "printf unreachable".into(),
            format!("touch {}", later.display()),
        ];
        let error = run_tests(QaExecutionSpec {
            workspace: &workspace,
            git_dir: &git_dir,
            dependencies: &[],
            commands: &commands,
            run_id: Uuid::new_v4(),
            store: &RunStore::new(),
            sandbox_binary: &sandbox,
            shell_binary: Path::new("/bin/sh"),
            timeout: Duration::from_secs(2),
        })
        .await
        .unwrap_err();
        assert_eq!(error.to_string(), "QA_SANDBOX_INIT_FAILED");
        assert!(!later.exists());
    }

    #[tokio::test]
    async fn opencode_sandbox_initialization_failure_is_terminal() {
        let mut command = Command::new("sh");
        command
            .args([
                "-c",
                "echo 'OPENAGENTS_SANDBOX_INIT_FAILED: test' >&2; exit 125",
            ])
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        let output = bounded_child_output(
            spawn_isolated_process(&mut command).unwrap(),
            "OPENCODE",
            Duration::from_secs(2),
            1024,
            1024,
            None,
        )
        .await
        .unwrap();
        assert_eq!(
            require_sandbox_initialized("OPENCODE", &output)
                .unwrap_err()
                .to_string(),
            "OPENCODE_SANDBOX_INIT_FAILED"
        );
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn qa_cancellation_is_terminal_and_skips_later_commands() {
        let temp = tempfile::tempdir().unwrap();
        let workspace = temp.path().join("repository");
        fs::create_dir(&workspace).await.unwrap();
        let pid_file = temp.path().join("qa-cancel.pid");
        let later = temp.path().join("later-ran");
        let commands = vec![
            format!("sleep 30 & echo $! > '{}'; wait", pid_file.display()),
            format!("touch '{}'", later.display()),
        ];
        let run_id = Uuid::new_v4();
        let store = RunStore::new();
        store
            .insert(crate::model::RunRecord::new(
                run_id,
                Uuid::new_v4(),
                Uuid::new_v4(),
                Uuid::new_v4(),
                "qa-cancellation".into(),
            ))
            .await;
        let cancel_store = store.clone();
        tokio::spawn(async move {
            sleep(Duration::from_millis(750)).await;
            cancel_store
                .terminal(run_id, RunStatus::Cancelled, None, None)
                .await;
        });
        let error = run_test_qa(
            &workspace,
            &commands,
            run_id,
            &store,
            Duration::from_secs(5),
        )
        .await
        .unwrap_err();
        assert_eq!(error.to_string(), "RUN_CANCELLED");
        assert!(!later.exists());
        assert_process_stopped(&pid_file).await;
    }

    #[tokio::test]
    async fn opencode_capture_kills_and_drains_on_stdout_overflow() {
        let mut command = Command::new("sh");
        command
            .args(["-c", "while :; do printf 0123456789; done"])
            .kill_on_drop(true)
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        let error = timeout(
            Duration::from_secs(3),
            bounded_child_output(
                spawn_isolated_process(&mut command).unwrap(),
                "OPENCODE",
                Duration::from_secs(30),
                1024,
                1024,
                None,
            ),
        )
        .await
        .expect("overflow capture must not deadlock")
        .unwrap_err();

        assert_eq!(error.to_string(), "OPENCODE_STDOUT_LIMIT_EXCEEDED");
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn timeout_kills_descendant_process_group() {
        let temp = tempfile::tempdir().unwrap();
        let pid_file = temp.path().join("timeout.pid");
        let mut command = Command::new("sh");
        command
            .arg("-c")
            .arg("sleep 30 & echo $! > \"$1\"; wait")
            .arg("sh")
            .arg(&pid_file)
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        let error = bounded_child_output(
            spawn_isolated_process(&mut command).unwrap(),
            "QA",
            Duration::from_millis(200),
            1024,
            1024,
            None,
        )
        .await
        .unwrap_err();
        assert_eq!(error.to_string(), "QA_TIMEOUT");
        assert_process_stopped(&pid_file).await;
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn output_overflow_kills_descendant_process_group() {
        let temp = tempfile::tempdir().unwrap();
        let pid_file = temp.path().join("overflow.pid");
        let mut command = Command::new("sh");
        command
            .arg("-c")
            .arg("sleep 30 & echo $! > \"$1\"; while :; do printf 0123456789; done")
            .arg("sh")
            .arg(&pid_file)
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        let error = bounded_child_output(
            spawn_isolated_process(&mut command).unwrap(),
            "QA",
            Duration::from_secs(5),
            1024,
            1024,
            None,
        )
        .await
        .unwrap_err();
        assert_eq!(error.to_string(), "QA_STDOUT_LIMIT_EXCEEDED");
        assert_process_stopped(&pid_file).await;
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn cancellation_kills_descendant_process_group() {
        let temp = tempfile::tempdir().unwrap();
        let pid_file = temp.path().join("cancel.pid");
        let run_id = Uuid::new_v4();
        let store = RunStore::new();
        store
            .insert(crate::model::RunRecord::new(
                run_id,
                Uuid::new_v4(),
                Uuid::new_v4(),
                Uuid::new_v4(),
                "process-group-cancellation".into(),
            ))
            .await;
        let cancel_store = store.clone();
        tokio::spawn(async move {
            sleep(Duration::from_millis(200)).await;
            cancel_store
                .terminal(run_id, RunStatus::Cancelled, None, None)
                .await;
        });
        let mut command = Command::new("sh");
        command
            .arg("-c")
            .arg("sleep 30 & echo $! > \"$1\"; wait")
            .arg("sh")
            .arg(&pid_file)
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        let error = bounded_child_output(
            spawn_isolated_process(&mut command).unwrap(),
            "OPENCODE",
            Duration::from_secs(5),
            1024,
            1024,
            Some((&store, run_id)),
        )
        .await
        .unwrap_err();
        assert_eq!(error.to_string(), "RUN_CANCELLED");
        assert_process_stopped(&pid_file).await;
    }

    #[tokio::test]
    async fn qa_evidence_is_rejected_when_the_committed_worktree_changes() {
        let temp = tempfile::tempdir().unwrap();
        let workspace = temp.path().join("repository");
        fs::create_dir(&workspace).await.unwrap();
        checked(
            Command::new("git").arg("-C").arg(&workspace).arg("init"),
            "git init",
        )
        .await
        .unwrap();
        fs::write(workspace.join("tracked.txt"), b"original\n")
            .await
            .unwrap();
        checked(
            Command::new("git")
                .arg("-C")
                .arg(&workspace)
                .args(["add", "tracked.txt"]),
            "git add",
        )
        .await
        .unwrap();
        checked(
            Command::new("git").arg("-C").arg(&workspace).args([
                "-c",
                "user.name=OpenAgents Test",
                "-c",
                "user.email=openagents-test@openos.local",
                "commit",
                "-m",
                "test commit",
            ]),
            "git commit",
        )
        .await
        .unwrap();
        let head = output_text(
            Command::new("git")
                .arg("-C")
                .arg(&workspace)
                .args(["rev-parse", "HEAD"]),
            "git head",
        )
        .await
        .unwrap();
        let git_dir = externalize_test_git_dir(&workspace);

        verify_committed_delivery_unchanged(Path::new("git"), &workspace, &git_dir, &head)
            .await
            .unwrap();
        fs::write(workspace.join("tracked.txt"), b"mutated by QA\n")
            .await
            .unwrap();
        let error =
            verify_committed_delivery_unchanged(Path::new("git"), &workspace, &git_dir, &head)
                .await
                .unwrap_err();
        assert!(error.to_string().contains("QA_MUTATED_DELIVERY_WORKTREE"));
    }

    #[tokio::test]
    async fn qa_cannot_persist_a_redirected_candidate_git_pointer() {
        let temp = tempfile::tempdir().unwrap();
        let workspace = temp.path().join("repository");
        fs::create_dir(&workspace).await.unwrap();
        let run_id = Uuid::new_v4();
        let store = RunStore::new();

        run_test_qa(
            &workspace,
            &["printf 'gitdir: /tmp/attacker.git\\n' > .git".into()],
            run_id,
            &store,
            Duration::from_secs(2),
        )
        .await
        .unwrap();

        let expected = format!(
            "gitdir: {}\n",
            temp.path().join("test-git-control").display()
        );
        assert_eq!(
            fs::read_to_string(workspace.join(".git")).await.unwrap(),
            expected
        );
    }

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

    #[test]
    fn accepts_provider_neutral_https_and_ssh_repository_identities() {
        assert_eq!(
            remote_repository_identity("git@example.com:RemiPelloux/OpenBrain.git"),
            Some("RemiPelloux/OpenBrain".into())
        );
        validate_repository_remote(
            "https://code.example.com/RemiPelloux/OpenBrain.git",
            "remipelloux/openbrain",
        )
        .unwrap();
        assert_eq!(
            remote_repository_identity("ssh://git@code.example.net/team/platform/OpenBrain.git"),
            Some("team/platform/OpenBrain".into())
        );
        assert!(validate_repository_remote(
            "https://code.example.com/RemiPelloux/OpenBrain.git",
            "RemiPelloux/OpenAgents"
        )
        .is_err());
        assert!(remote_repository_identity("https://example.com/OpenBrain.git").is_none());
        for unsafe_remote in [
            "https://token@code.example.com/a/b.git",
            "https://user:secret@code.example.com/a/b.git",
            "ssh://token@code.example.com/a/b.git",
            "ssh://git:secret@code.example.com/a/b.git",
            "token@code.example.com:a/b.git",
            "git@token@code.example.com:a/b.git",
        ] {
            assert_eq!(
                remote_repository_identity(unsafe_remote),
                None,
                "accepted unsafe remote {unsafe_remote}"
            );
        }
        assert_ne!(
            parsed_remote_repository("https://code.example.com/team/repository.git"),
            parsed_remote_repository("https://mirror.example.net/team/repository.git")
        );
    }

    #[tokio::test]
    async fn candidate_repository_is_self_contained_after_source_cache_disappears() {
        let temp = tempfile::tempdir().unwrap();
        let source = temp.path().join("repositories/source");
        let candidate = temp.path().join("worktrees/candidate");
        fs::create_dir_all(&source).await.unwrap();
        checked(
            Command::new("git").arg("-C").arg(&source).arg("init"),
            "git init source",
        )
        .await
        .unwrap();
        fs::write(source.join("tracked.txt"), b"base\n")
            .await
            .unwrap();
        checked(
            Command::new("git")
                .arg("-C")
                .arg(&source)
                .args(["add", "tracked.txt"]),
            "git add source",
        )
        .await
        .unwrap();
        checked(
            Command::new("git").arg("-C").arg(&source).args([
                "-c",
                "user.name=OpenAgents Test",
                "-c",
                "user.email=openagents-test@openos.local",
                "-c",
                "commit.gpgsign=false",
                "commit",
                "-m",
                "base",
            ]),
            "git commit source",
        )
        .await
        .unwrap();
        let base_sha = output_text(
            Command::new("git")
                .arg("-C")
                .arg(&source)
                .args(["rev-parse", "HEAD"]),
            "source head",
        )
        .await
        .unwrap();
        let remote_url = "https://code.example.com/team/repository.git";
        let git_dir = candidate_git_dir(&candidate).unwrap();
        initialize_candidate_repository(CandidateInitSpec {
            git_binary: Path::new("git"),
            source_repository: &source,
            workspace: &candidate,
            git_dir: &git_dir,
            remote: "origin",
            remote_url,
            base_sha: &base_sha,
            branch: "openos/task-stable",
            preserve_worktree: false,
            git_timeout: Duration::from_secs(5),
        })
        .await
        .unwrap();

        assert!(candidate.join(".git").is_file());
        assert!(git_dir.is_dir());
        assert!(!git_dir.join("objects/info/alternates").exists());
        std::fs::remove_dir_all(source.parent().unwrap()).unwrap();
        verify_candidate_repository(
            Path::new("git"),
            &candidate,
            &git_dir,
            "origin",
            remote_url,
            &base_sha,
            "openos/task-stable",
        )
        .await
        .unwrap();
        verify_committed_delivery_unchanged(Path::new("git"), &candidate, &git_dir, &base_sha)
            .await
            .unwrap();
        checked(
            Command::new("git")
                .arg("-C")
                .arg(&candidate)
                .args(["fsck", "--full"]),
            "git fsck candidate",
        )
        .await
        .unwrap();
    }

    #[tokio::test]
    async fn task_base_pin_survives_fast_forward_retries() {
        let temp = tempfile::tempdir().unwrap();
        let repository = temp.path();
        checked(
            Command::new("git").arg("-C").arg(repository).arg("init"),
            "git init",
        )
        .await
        .unwrap();
        fs::write(repository.join("tracked.txt"), b"first\n")
            .await
            .unwrap();
        checked(
            Command::new("git")
                .arg("-C")
                .arg(repository)
                .args(["add", "tracked.txt"]),
            "git add",
        )
        .await
        .unwrap();
        let commit = |message: &'static str| {
            let mut command = Command::new("git");
            command.arg("-C").arg(repository).args([
                "-c",
                "user.name=OpenAgents Test",
                "-c",
                "user.email=openagents-test@openos.local",
                "-c",
                "commit.gpgsign=false",
                "commit",
                "-am",
                message,
            ]);
            command
        };
        checked(&mut commit("first"), "first commit").await.unwrap();
        let first = output_text(
            Command::new("git")
                .arg("-C")
                .arg(repository)
                .args(["rev-parse", "HEAD"]),
            "first head",
        )
        .await
        .unwrap();
        let pin = "refs/openos/base/test";
        assert_eq!(
            pinned_base_revision(repository, pin, &first).await.unwrap(),
            first
        );
        fs::write(repository.join("tracked.txt"), b"second\n")
            .await
            .unwrap();
        checked(&mut commit("second"), "second commit")
            .await
            .unwrap();
        let second = output_text(
            Command::new("git")
                .arg("-C")
                .arg(repository)
                .args(["rev-parse", "HEAD"]),
            "second head",
        )
        .await
        .unwrap();
        assert_eq!(
            pinned_base_revision(repository, pin, &second)
                .await
                .unwrap(),
            first
        );
    }

    #[test]
    fn workspace_dependencies_reject_escapes_reserved_and_duplicate_destinations() {
        let dependency = json!({
            "name":"OpenAgents",
            "repository":"/repositories/OpenAgents",
            "provider":"github",
            "external_repository_id":"RemiPelloux/OpenAgents",
            "remote_url":"https://github.com/RemiPelloux/OpenAgents.git",
            "base_ref":"main",
            "source_path":".",
            "destination":"OpenAgents"
        });
        assert_eq!(
            parse_workspace_dependencies(&json!({"workspace_dependencies":[dependency.clone()]}))
                .unwrap()
                .len(),
            1
        );
        for invalid in ["../OpenAgents", ".", ".openos-support/cache"] {
            let mut value = dependency.clone();
            value["destination"] = json!(invalid);
            assert!(
                parse_workspace_dependencies(&json!({"workspace_dependencies":[value]})).is_err()
            );
        }
        assert!(parse_workspace_dependencies(&json!({
            "workspace_dependencies":[dependency.clone(), dependency]
        }))
        .is_err());
    }

    #[tokio::test]
    async fn workspace_dependency_retry_preserves_pin_and_rejects_local_mutation() {
        let temp = tempfile::tempdir().unwrap();
        let repository = temp.path();
        let git = |path: &Path, arguments: &[&str]| {
            let output = std::process::Command::new("git")
                .arg("-C")
                .arg(path)
                .args(arguments)
                .output()
                .unwrap();
            assert!(output.status.success(), "git {:?} failed", arguments);
            String::from_utf8_lossy(&output.stdout).trim().to_string()
        };
        git(repository, &["init", "-q"]);
        git(repository, &["config", "user.name", "OpenOS Test"]);
        git(
            repository,
            &["config", "user.email", "openos-test@example.invalid"],
        );
        std::fs::write(repository.join("dependency.txt"), "first\n").unwrap();
        git(repository, &["add", "dependency.txt"]);
        git(
            repository,
            &["-c", "commit.gpgsign=false", "commit", "-qm", "first"],
        );
        let ancestor = git(repository, &["rev-parse", "HEAD"]);
        std::fs::write(repository.join("dependency.txt"), "second\n").unwrap();
        git(repository, &["add", "dependency.txt"]);
        git(
            repository,
            &["-c", "commit.gpgsign=false", "commit", "-qm", "second"],
        );
        let pinned = git(repository, &["rev-parse", "HEAD"]);
        let pin_reference = "refs/openos/support-pins/test/0";

        assert_eq!(
            pinned_dependency_revision(repository, pin_reference, &pinned)
                .await
                .unwrap(),
            pinned,
        );
        let support = temp.path().join("support");
        git(
            repository,
            &[
                "worktree",
                "add",
                "--detach",
                support.to_str().unwrap(),
                pin_reference,
            ],
        );

        std::fs::write(repository.join("dependency.txt"), "third\n").unwrap();
        git(repository, &["add", "dependency.txt"]);
        git(
            repository,
            &["-c", "commit.gpgsign=false", "commit", "-qm", "third"],
        );
        let advanced_remote = git(repository, &["rev-parse", "HEAD"]);
        assert_eq!(
            pinned_dependency_revision(repository, pin_reference, &advanced_remote)
                .await
                .unwrap(),
            pinned,
        );
        verify_workspace_dependencies_clean(Path::new("git"), &[(support.clone(), pinned.clone())])
            .await
            .unwrap();

        git(&support, &["checkout", "--detach", &ancestor]);
        assert_eq!(
            verify_workspace_dependencies_clean(
                Path::new("git"),
                &[(support.clone(), pinned.clone())],
            )
            .await
            .unwrap_err()
            .to_string(),
            "WORKSPACE_DEPENDENCY_MUTATED"
        );
        git(&support, &["checkout", "--detach", &pinned]);
        std::fs::write(support.join("local.txt"), "untrusted\n").unwrap();
        assert_eq!(
            verify_workspace_dependencies_clean(Path::new("git"), &[(support, pinned)])
                .await
                .unwrap_err()
                .to_string(),
            "WORKSPACE_DEPENDENCY_MUTATED"
        );

        git(repository, &["checkout", "--detach", &ancestor]);
        std::fs::write(repository.join("dependency.txt"), "diverged\n").unwrap();
        git(repository, &["add", "dependency.txt"]);
        git(
            repository,
            &["-c", "commit.gpgsign=false", "commit", "-qm", "diverged"],
        );
        let divergent_remote = git(repository, &["rev-parse", "HEAD"]);
        assert_eq!(
            pinned_dependency_revision(repository, pin_reference, &divergent_remote)
                .await
                .unwrap_err()
                .to_string(),
            "WORKSPACE_DEPENDENCY_REVISION_MISMATCH"
        );
    }

    #[test]
    fn delivery_branch_names_are_strictly_bounded() {
        assert!(valid_git_branch_name("openos/delivery-123"));
        for invalid in [
            "",
            "openos/../main",
            "openos/topic/",
            "openos/topic.lock",
            "openos/topic$",
        ] {
            assert!(!valid_git_branch_name(invalid), "accepted {invalid}");
        }
    }

    #[tokio::test]
    async fn delivery_rejects_a_branch_changed_by_the_agent() {
        let temp = tempfile::tempdir().unwrap();
        let repository = temp.path();
        let git = |arguments: &[&str]| {
            let output = std::process::Command::new("git")
                .arg("-C")
                .arg(repository)
                .args(arguments)
                .output()
                .unwrap();
            assert!(
                output.status.success(),
                "git {:?}: {}",
                arguments,
                String::from_utf8_lossy(&output.stderr)
            );
            String::from_utf8_lossy(&output.stdout).trim().to_string()
        };
        git(&["init", "-q"]);
        git(&["config", "user.name", "OpenOS Test"]);
        git(&["config", "user.email", "openos-test@example.invalid"]);
        std::fs::write(repository.join("base.txt"), "base\n").unwrap();
        git(&["add", "base.txt"]);
        git(&["-c", "commit.gpgsign=false", "commit", "-qm", "base"]);
        let base_sha = git(&["rev-parse", "HEAD"]);
        git(&["checkout", "-qb", "unexpected/topic"]);
        let git_dir = externalize_test_git_dir(repository);
        let error = commit_changes(CommitSpec {
            workspace: repository,
            git_dir: &git_dir,
            repository,
            base_sha: &base_sha,
            expected_branch: "openos/expected",
            ticket_id: Uuid::new_v4(),
            run_id: Uuid::new_v4(),
            sign_commit: true,
            git_binary: Path::new("git"),
            signing_key_b64: None,
            git_timeout: Duration::from_secs(5),
        })
        .await
        .unwrap_err();
        assert_eq!(error.to_string(), "GIT_DELIVERY_BRANCH_MISMATCH");
    }

    #[tokio::test]
    async fn delivery_rejects_multiple_commits_above_the_base() {
        let temp = tempfile::tempdir().unwrap();
        let repository = temp.path();
        let git = |arguments: &[&str]| {
            let output = std::process::Command::new("git")
                .arg("-C")
                .arg(repository)
                .args(arguments)
                .output()
                .unwrap();
            assert!(output.status.success(), "git {:?} failed", arguments);
            String::from_utf8_lossy(&output.stdout).trim().to_string()
        };
        git(&["init", "-q"]);
        git(&["config", "user.name", "OpenOS Test"]);
        git(&["config", "user.email", "openos-test@example.invalid"]);
        std::fs::write(repository.join("base.txt"), "base\n").unwrap();
        git(&["add", "base.txt"]);
        git(&["-c", "commit.gpgsign=false", "commit", "-qm", "base"]);
        let base_sha = git(&["rev-parse", "HEAD"]);
        git(&["checkout", "-qb", "openos/expected"]);
        for index in 1..=2 {
            std::fs::write(repository.join(format!("change-{index}.txt")), "change\n").unwrap();
            git(&["add", "."]);
            git(&[
                "-c",
                "commit.gpgsign=false",
                "commit",
                "-qm",
                &format!("change {index}"),
            ]);
        }
        let git_dir = externalize_test_git_dir(repository);
        assert_eq!(
            commit_changes(CommitSpec {
                workspace: repository,
                git_dir: &git_dir,
                repository,
                base_sha: &base_sha,
                expected_branch: "openos/expected",
                ticket_id: Uuid::new_v4(),
                run_id: Uuid::new_v4(),
                sign_commit: true,
                git_binary: Path::new("git"),
                signing_key_b64: None,
                git_timeout: Duration::from_secs(5),
            })
            .await
            .unwrap_err()
            .to_string(),
            "GIT_COMMIT_TOPOLOGY_INVALID"
        );
    }

    #[cfg(target_os = "linux")]
    #[tokio::test]
    async fn trusted_git_ignores_generated_control_and_signs_one_commit() {
        use std::os::unix::fs::PermissionsExt as _;

        let temp = tempfile::tempdir().unwrap();
        let repository = temp.path().join("candidate");
        std::fs::create_dir(&repository).unwrap();
        let git = |arguments: &[&str]| {
            let output = std::process::Command::new("git")
                .arg("-C")
                .arg(&repository)
                .args(arguments)
                .output()
                .unwrap();
            assert!(
                output.status.success(),
                "git {:?}: {}",
                arguments,
                String::from_utf8_lossy(&output.stderr)
            );
            String::from_utf8_lossy(&output.stdout).trim().to_string()
        };
        git(&["init", "-q"]);
        git(&["config", "user.name", "OpenOS Test"]);
        git(&["config", "user.email", "openos-test@example.invalid"]);
        std::fs::write(repository.join("tracked.txt"), "base\n").unwrap();
        git(&["add", "tracked.txt"]);
        git(&["-c", "commit.gpgsign=false", "commit", "-qm", "base"]);
        let base_sha = git(&["rev-parse", "HEAD"]);
        git(&["checkout", "-qb", "openos/expected"]);
        let git_dir = externalize_test_git_dir(&repository);

        let attacker_git = temp.path().join("attacker.git");
        assert!(std::process::Command::new("git")
            .args(["init", "--bare", "--quiet"])
            .arg(&attacker_git)
            .status()
            .unwrap()
            .success());
        let marker = temp.path().join("executable-surface-ran");
        let executable = temp.path().join("attacker-helper.sh");
        std::fs::write(
            &executable,
            format!("#!/bin/sh\ntouch '{}'\ncat\n", marker.display()),
        )
        .unwrap();
        std::fs::set_permissions(&executable, std::fs::Permissions::from_mode(0o700)).unwrap();
        for (key, value) in [
            ("core.hooksPath", executable.to_str().unwrap()),
            ("core.fsmonitor", executable.to_str().unwrap()),
            ("filter.evil.clean", executable.to_str().unwrap()),
            ("credential.helper", executable.to_str().unwrap()),
            ("diff.external", executable.to_str().unwrap()),
            ("gpg.program", executable.to_str().unwrap()),
        ] {
            assert!(std::process::Command::new("git")
                .arg("--git-dir")
                .arg(&attacker_git)
                .args(["config", key, value])
                .status()
                .unwrap()
                .success());
        }
        std::fs::write(
            repository.join(".git"),
            format!("gitdir: {}\n", attacker_git.display()),
        )
        .unwrap();
        std::fs::write(
            repository.join(".gitattributes"),
            "*.txt filter=evil diff=evil\n",
        )
        .unwrap();
        std::fs::write(repository.join("tracked.txt"), "candidate\n").unwrap();

        let signing_key = test_signing_key();
        let result = commit_changes(CommitSpec {
            workspace: &repository,
            git_dir: &git_dir,
            repository: &repository,
            base_sha: &base_sha,
            expected_branch: "openos/expected",
            ticket_id: Uuid::new_v4(),
            run_id: Uuid::new_v4(),
            sign_commit: true,
            git_binary: Path::new("git"),
            signing_key_b64: Some(&signing_key),
            git_timeout: Duration::from_secs(10),
        })
        .await
        .unwrap();

        assert_eq!(result.signed_commits.len(), 1);
        assert!(!marker.exists(), "untrusted Git executable surface ran");
        assert_eq!(
            output_text(
                trusted_git_command(Path::new("git"), &repository, &git_dir).args([
                    "rev-list",
                    "--count",
                    &format!("{base_sha}..HEAD")
                ]),
                "signed commit count",
            )
            .await
            .unwrap(),
            "1"
        );
    }

    #[test]
    fn candidate_evidence_reports_signed_local_only_state() {
        let git = VerifiedGitDelivery {
            evidence: GitEvidence {
                repository: "/repositories/Repository".into(),
                worktree: "/worktrees/org/plan/Repository".into(),
                branch: "openos/task".into(),
                commit_sha: "a".repeat(40),
                clean: true,
                pushed: false,
                remote: None,
            },
            signed_commits: vec!["a".repeat(40)],
        };
        let changed_files = WorkerArtifact {
            kind: "changed_files".into(),
            name: "Changed files".into(),
            uri: "git://commit/changed-files".into(),
            sha256: None,
            metadata: json!({"files":["src/lib.rs"]}),
        };
        let base_sha = "b".repeat(40);
        verify_local_candidate(&git).unwrap();
        let evidence = candidate_evidence(CandidateEvidenceSpec {
            git: &git,
            changed_files: &changed_files,
            organization_id: Uuid::new_v4(),
            repository_name: "Repository",
            workspace_root: Path::new("/worktrees/org/plan"),
            base_ref: "main",
            base_sha: &base_sha,
        });
        assert_eq!(
            evidence["workspace"]["process_sandbox"],
            "linux_landlock_verified_at_worker_health"
        );
        assert!(evidence["workspace"].get("isolated").is_none());
        assert_eq!(evidence["git"]["commit_count"], 1);
        assert_eq!(evidence["git"]["pushed"], false);
        assert_eq!(evidence["delivery"]["mode"], "local_candidate_only");
        assert_eq!(evidence["delivery"]["pull_request_created"], false);

        let delivered = VerifiedGitDelivery {
            evidence: GitEvidence {
                pushed: true,
                remote: Some("https://code.example.com/owner/repository.git".into()),
                ..git.evidence.clone()
            },
            signed_commits: git.signed_commits.clone(),
        };
        assert_eq!(
            verify_local_candidate(&delivered).unwrap_err().to_string(),
            "LOCAL_CANDIDATE_HAS_DELIVERY_STATE"
        );
    }

    #[tokio::test]
    async fn hacn_events_are_bounded_projected_and_removed() {
        let temp = tempfile::tempdir().unwrap();
        let path = temp.path().join("hacn-run.ndjson");
        std::fs::write(
            &path,
            format!(
                "{}\n{}\n",
                json!({
                    "type":"hypothesis_updated",
                    "confidence":0.75,
                    "expected":{"key":"hacn.prediction:/private/user/path.md","surfaced":true},
                    "observed":{"key":"hacn.prediction:/private/user/path.md","surfaced":false},
                    "reasoning":"must not be persisted"
                }),
                json!({"type":"irrelevant","observed":{"state":"ignored"}})
            ),
        )
        .unwrap();
        let events = read_cognitive_events(&path).await.unwrap();
        assert_eq!(events.len(), 1);
        assert!(events[0].get("reasoning").is_none());
        let serialized = events[0].to_string();
        assert!(!serialized.contains("/private/user/path.md"));
        assert!(serialized.contains("hacn.prediction:sha256:"));
        assert!(!path.exists());

        let private_path = temp.path().join("hacn-private.ndjson");
        std::fs::write(
            &private_path,
            json!({
                "type":"expected_observed_mismatch",
                "observed":{"private_reasoning":"not persistable"}
            })
            .to_string(),
        )
        .unwrap();
        assert!(read_cognitive_events(&private_path)
            .await
            .unwrap()
            .is_empty());
        assert!(!private_path.exists());

        let secret_path = temp.path().join("hacn-secret.ndjson");
        std::fs::write(
            &secret_path,
            json!({
                "type":"hypothesis_updated",
                "observed":{
                    "key":"hacn.relevance:sk-proj-secretmaterial1234567890",
                    "value":{"fromState":1,"toState":2,"api_key":"must-not-persist"}
                }
            })
            .to_string(),
        )
        .unwrap();
        let events = read_cognitive_events(&secret_path).await.unwrap();
        let serialized = serde_json::to_string(&events).unwrap();
        assert!(!serialized.contains("sk-proj"));
        assert!(!serialized.contains("api_key"));
        assert!(!serialized.contains("must-not-persist"));
        assert!(!secret_path.exists());
    }

    #[tokio::test]
    async fn workspace_dependency_link_is_idempotent_and_cannot_overlap_target() {
        let temp = tempfile::tempdir().unwrap();
        let root = temp.path().join("run");
        let support = root.join(".openos-support/OpenOS");
        let source = support.join("OpenContract");
        let target = root.join("OpenBrain");
        std::fs::create_dir_all(&source).unwrap();
        std::fs::create_dir_all(&target).unwrap();

        link_workspace_dependency(&root, &target, &support, "OpenContract", "OpenContract")
            .await
            .unwrap();
        link_workspace_dependency(&root, &target, &support, "OpenContract", "OpenContract")
            .await
            .unwrap();
        assert_eq!(
            std::fs::canonicalize(root.join("OpenContract")).unwrap(),
            std::fs::canonicalize(source).unwrap()
        );
        assert!(link_workspace_dependency(
            &root,
            &target,
            &support,
            "OpenContract",
            "OpenBrain/dependency"
        )
        .await
        .is_err());
    }

    #[tokio::test]
    async fn workspace_dependency_mutation_is_detected_before_delivery() {
        let temp = tempfile::tempdir().unwrap();
        let repository = temp.path();
        let git = |arguments: &[&str]| {
            let output = std::process::Command::new("git")
                .arg("-C")
                .arg(repository)
                .args(arguments)
                .output()
                .unwrap();
            assert!(output.status.success());
            String::from_utf8_lossy(&output.stdout).trim().to_string()
        };
        git(&["init", "-q"]);
        git(&["config", "user.name", "OpenOS Test"]);
        git(&["config", "user.email", "openos-test@example.invalid"]);
        std::fs::write(repository.join("dependency.txt"), "base\n").unwrap();
        git(&["add", "dependency.txt"]);
        git(&["-c", "commit.gpgsign=false", "commit", "-qm", "base"]);
        let expected_sha = git(&["rev-parse", "HEAD"]);
        let dependencies = vec![(repository.to_path_buf(), expected_sha)];

        verify_workspace_dependencies_clean(Path::new("git"), &dependencies)
            .await
            .unwrap();
        std::fs::write(repository.join("dependency.txt"), "mutated\n").unwrap();
        assert_eq!(
            verify_workspace_dependencies_clean(Path::new("git"), &dependencies)
                .await
                .unwrap_err()
                .to_string(),
            "WORKSPACE_DEPENDENCY_MUTATED"
        );
    }

    #[tokio::test]
    async fn opencode_settings_keep_support_repositories_read_only() {
        let temp = tempfile::tempdir().unwrap();
        let dependency = temp.path().join("SupportRepository");
        std::fs::create_dir(&dependency).unwrap();
        let run_id = Uuid::new_v4();
        let runtime =
            prepare_opencode_runtime_home(run_id, &[(dependency.clone(), "a".repeat(40))])
                .await
                .unwrap();
        let settings: Value = serde_json::from_slice(
            &std::fs::read(runtime.0.join(".opencode/settings.json")).unwrap(),
        )
        .unwrap();
        let dependency = dependency.display().to_string();
        assert_eq!(
            settings["permissions"]["additionalDirectories"],
            json!([dependency])
        );
        assert_eq!(
            settings["permissions"]["deny"],
            json!([format!("Edit({dependency}/**)")])
        );
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
            sanitize("ok\nAuthorization: Bearer abc\nsk-secret\npassword=fake-password-value"),
            "ok\n[redacted]\n[redacted]\n[redacted]"
        );
    }

    #[test]
    fn reports_bounded_opencode_failure_metadata_without_result_text() {
        let stdout = json!({
            "type":"result",
            "subtype":"error_during_execution",
            "is_error":true,
            "result":"private model output must not be persisted"
        })
        .to_string();
        assert_eq!(
            opencode_terminal_status(&stdout),
            "subtype=error_during_execution;is_error=true"
        );
        assert_eq!(bounded_diagnostic(""), "none");
        assert_eq!(bounded_diagnostic(&"x".repeat(5000)).len(), 4096);
    }

    #[tokio::test]
    async fn persists_only_versioned_allowlisted_opencode_evidence() {
        let temp = tempfile::tempdir().unwrap();
        let run_id = Uuid::new_v4();
        let private_text = "private model output must not be persisted";
        let credential = "sk-proj-fakecredentialmaterial123456";
        let stream = [
            json!({
                "type":"assistant",
                "message":{"content":[{"type":"text","text":private_text}]},
                "session_id":credential
            }),
            json!({
                "type":"result",
                "subtype":"success",
                "is_error":false,
                "result":private_text
            }),
        ]
        .into_iter()
        .map(|event| event.to_string())
        .collect::<Vec<_>>()
        .join("\n");

        let artifact = persist_opencode_evidence(temp.path(), run_id, &stream)
            .await
            .unwrap();
        let path = PathBuf::from(artifact.uri.strip_prefix("file://").unwrap());
        let persisted = fs::read_to_string(path).await.unwrap();
        assert!(persisted.contains("openos.opencode-execution-evidence/v1"));
        assert!(persisted.contains("[redacted]"));
        assert!(!persisted.contains(private_text));
        assert!(!persisted.contains(credential));
        assert!(!artifact.uri.ends_with(".jsonl"));
        assert_eq!(artifact.metadata["raw_jsonl_persisted"], false);
    }

    #[tokio::test]
    async fn post_opencode_guard_ignores_internal_tool_content_and_does_not_persist_it() {
        let temp = tempfile::tempdir().unwrap();
        let run_id = Uuid::new_v4();
        let credential = [
            "sk", "-", "proj", "-", "runtime", "credential", "material", "123456",
        ]
        .concat();
        let stream = [
            json!({
                "type":"tool_result",
                "tool_name":"read",
                "content":credential
            }),
            json!({
                "type":"result",
                "subtype":"success",
                "is_error":false,
                "result":"Updated terminal-only output validation."
            }),
        ]
        .into_iter()
        .map(|event| event.to_string())
        .collect::<Vec<_>>()
        .join("\n");

        let terminal_report = final_execution_report(&stream).unwrap();
        assert_eq!(post_opencode_secret_kind(&terminal_report, ""), None);

        let artifact = persist_opencode_evidence(temp.path(), run_id, &stream)
            .await
            .unwrap();
        let path = PathBuf::from(artifact.uri.strip_prefix("file://").unwrap());
        let persisted = fs::read_to_string(path).await.unwrap();
        assert!(!persisted.contains(&credential));
        let evidence: Value = serde_json::from_str(&persisted).unwrap();
        assert_eq!(
            evidence["events"][0],
            json!({
                "schema":"openos.opencode-event/v1",
                "type":"tool_result",
                "subtype":null,
                "session_id":null
            })
        );
        assert_eq!(artifact.metadata["raw_jsonl_persisted"], false);
    }

    #[test]
    fn post_opencode_guard_rejects_terminal_result_credential() {
        let credential = [
            "sk", "-", "proj", "-", "terminal", "credential", "material", "123456",
        ]
        .concat();
        let terminal_report = format!("report: {credential}");

        assert_eq!(
            post_opencode_secret_kind(&terminal_report, ""),
            Some(SecretKind::ProviderToken)
        );
    }

    #[test]
    fn post_opencode_guard_rejects_stderr_credential() {
        let credential = [
            "sk", "-", "proj", "-", "stderr", "credential", "material", "123456",
        ]
        .concat();
        assert_eq!(
            post_opencode_secret_kind("Safe terminal report.", &credential),
            Some(SecretKind::ProviderToken)
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
        for value in [
            "password=fake-password-value",
            "client_secret=fake-client-secret-value",
            "token_b64=ZmFrZS1lbmNvZGVkLXNlY3JldA==",
        ] {
            assert_eq!(detect_secret(value), Some(SecretKind::AssignedSecret));
        }
        assert_eq!(
            detect_secret("AKIAEXAMPLE1234567890"),
            Some(SecretKind::CloudAccessKey)
        );
        assert_eq!(
            detect_secret("eyJhbGciOiJub25lIn0.eyJzdWIiOiJ0ZXN0In0.c2lnbmF0dXJl"),
            Some(SecretKind::Jwt)
        );
        assert_eq!(
            detect_secret("https://hooks.slack.com/services/AAA/BBB/fake-webhook-value"),
            Some(SecretKind::Webhook)
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
        let git_dir = externalize_test_git_dir(repository);

        let artifact = changed_files_artifact(
            Path::new("git"),
            repository,
            &git_dir,
            &base_sha,
            &commit_sha,
        )
        .unwrap();
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
        let git_dir = externalize_test_git_dir(repository);

        let error = changed_files_artifact(
            Path::new("git"),
            repository,
            &git_dir,
            &base_sha,
            &commit_sha,
        )
        .unwrap_err();
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
        assert!(prompt.contains("OpenAgents exclusively owns the branch"));
        assert!(prompt.contains("Do not create, switch, rename, delete, commit, push"));
        assert!(prompt.contains("Leave the requested source and test changes uncommitted"));
        assert!(prompt.contains("OpenAgents executes every authoritative registered QA command"));
        assert!(prompt.contains("Do not install dependencies or run build"));
        assert!(prompt.contains("facts that vary between valid instances"));
        assert!(prompt.contains("smallest reusable boundary"));
        assert!(prompt.contains("materially different instance"));
        assert!(!prompt.contains("Always use OpenFoo"));
    }

    #[tokio::test]
    async fn opencode_runtime_home_is_ephemeral_and_denies_dependency_edits() {
        let run_id = Uuid::new_v4();
        let dependency = PathBuf::from("/worktrees/org/plan/.openos-support/OpenContract");
        let home = prepare_opencode_runtime_home(run_id, &[(dependency.clone(), "a".repeat(40))])
            .await
            .unwrap();
        let path = home.0.clone();
        let settings = serde_json::from_slice::<Value>(
            &fs::read(path.join(".opencode/settings.json"))
                .await
                .unwrap(),
        )
        .unwrap();
        assert_eq!(settings["autoMemoryEnabled"], false);
        assert_eq!(
            settings["permissions"]["additionalDirectories"][0],
            dependency.display().to_string()
        );
        assert_eq!(
            settings["permissions"]["deny"][0],
            format!("Edit({}/**)", dependency.display())
        );
        assert!(settings.get("sandbox").is_none());
        drop(home);
        assert!(!path.exists());
    }

    #[test]
    fn managed_hacn_namespaces_are_durable_isolated_and_redacted() {
        let root = Path::new("/managed");
        let organization = Uuid::new_v4();
        let session = Uuid::new_v4();
        let first = managed_hacn_store_root(root, organization, "owner/repository", session);
        let retry = managed_hacn_store_root(root, organization, "owner/repository", session);
        let other_workspace = managed_hacn_store_root(root, organization, "owner/other", session);
        let other_session =
            managed_hacn_store_root(root, organization, "owner/repository", Uuid::new_v4());

        assert_eq!(first, retry);
        assert_ne!(first, other_workspace);
        assert_ne!(first, other_session);
        let rendered = first.display().to_string();
        assert!(!rendered.contains(&organization.to_string()));
        assert!(!rendered.contains("owner/repository"));
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
