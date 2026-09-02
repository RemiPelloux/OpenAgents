use std::{
    fs::OpenOptions,
    os::unix::fs::PermissionsExt,
    path::{Component, Path, PathBuf},
    process::Stdio,
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc,
    },
    time::Duration,
};

use anyhow::Context;
use async_trait::async_trait;
use base64::{engine::general_purpose::STANDARD, Engine as _};
use chrono::Utc;
use contract_core::{
    GitEvidence, PullRequestEvidence, SignedWorkerEnvelope, WorkerArtifact, WorkerJob,
    WorkerMessageKind, WorkerResult,
};
use fs2::FileExt;
use reqwest::{Client, StatusCode};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use tokio::{fs, io::AsyncWriteExt, process::Command, time::timeout};
use uuid::Uuid;
use zeroize::{Zeroize, Zeroizing};

use crate::{
    config::Config,
    control_plane::{classify_failure, ControlPlaneClient, ControlPlaneFailure},
    model::{RunStatus, RunStore},
};

const CALLBACK_ATTEMPTS: usize = 4;
const MAX_CONNECTOR_BYTES: u64 = 16 * 1024;
const MAX_GIT_SIGNING_PUBLIC_KEY_B64_BYTES: usize = 256 * 1024;

pub async fn runtime_healthy(config: &Config) -> bool {
    let managed_root = fs::metadata(&config.managed_root)
        .await
        .is_ok_and(|metadata| metadata.is_dir());
    let connector_source = fs::metadata(&config.connector_root)
        .await
        .is_ok_and(|metadata| metadata.is_dir())
        || timeout(
            config.git_timeout,
            Command::new(&config.aws_binary)
                .arg("--version")
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .status(),
        )
        .await
        .is_ok_and(|result| result.is_ok_and(|status| status.success()));
    let git = timeout(
        config.git_timeout,
        Command::new(&config.git_binary)
            .arg("--version")
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status(),
    )
    .await
    .is_ok_and(|result| result.is_ok_and(|status| status.success()));
    let gpg = timeout(
        config.git_timeout,
        Command::new("gpg")
            .arg("--version")
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status(),
    )
    .await
    .is_ok_and(|result| result.is_ok_and(|status| status.success()));
    let trust = prepare_ephemeral_verification_identity(config)
        .await
        .is_ok();
    managed_root && connector_source && git && gpg && trust
}

#[derive(Debug, Deserialize, Serialize)]
struct DeliveryRequest {
    delivery_id: Uuid,
    source_job_id: Uuid,
    plan_id: Uuid,
    task_id: Uuid,
    ticket_id: Uuid,
    repository_id: Uuid,
    base_ref: String,
    remote_branch: String,
    commit_sha: String,
    diff_digest: String,
    candidate_digest: String,
    approval_subject_digest: String,
    workspace: WorkspaceClaim,
    preflight: Value,
    provider: ProviderClaim,
    requirements: DeliveryRequirements,
    callback: CallbackClaim,
}

#[derive(Debug, Deserialize, Serialize)]
struct WorkspaceClaim {
    organization_id: Uuid,
    run_id: Uuid,
    repository_name: String,
    workspace_root: String,
    repository_folder: String,
}

#[derive(Debug, Deserialize, Serialize)]
struct ProviderClaim {
    provider: String,
    external_repository_id: String,
    connector_ref: String,
}

#[derive(Debug, Deserialize, Serialize)]
struct DeliveryRequirements {
    canonical_managed_worktree: bool,
    exact_head: bool,
    clean_index_and_worktree: bool,
    registered_remote_identity: bool,
    draft_pull_request: bool,
    idempotent_branch_and_pull_request: bool,
    credentials_visible_only_inside_trusted_delivery_stage: bool,
}

#[derive(Debug, Deserialize, Serialize)]
struct CallbackClaim {
    method: String,
    path: String,
    required_producer: String,
}

struct ConnectorSecret {
    token: String,
    username: String,
}

impl Drop for ConnectorSecret {
    fn drop(&mut self) {
        self.token.zeroize();
        self.username.zeroize();
    }
}

#[derive(Debug)]
struct VerifiedCandidate {
    worktree: PathBuf,
    local_branch: String,
    remote_url: String,
}

#[derive(Clone, Debug)]
struct DraftPullRequest {
    url: String,
    number: u64,
    base: String,
    head: String,
}

#[async_trait]
trait ConnectorResolver: Send + Sync {
    fn supports(&self, reference: &str) -> bool;
    async fn resolve(
        &self,
        reference: &str,
        organization_id: Uuid,
        provider: &str,
    ) -> anyhow::Result<ConnectorSecret>;
}

struct LocalConnectorResolver {
    root: PathBuf,
}

struct AwsConnectorResolver {
    binary: PathBuf,
    timeout: Duration,
}

#[async_trait]
impl ConnectorResolver for LocalConnectorResolver {
    fn supports(&self, reference: &str) -> bool {
        reference.starts_with("local-connector://")
    }

    async fn resolve(
        &self,
        reference: &str,
        organization_id: Uuid,
        provider: &str,
    ) -> anyhow::Result<ConnectorSecret> {
        let relative = tenant_connector_path(reference, organization_id)?;
        if relative.components().next().and_then(|value| match value {
            Component::Normal(value) => value.to_str(),
            _ => None,
        }) != Some(provider)
        {
            anyhow::bail!("CONNECTOR_PROVIDER_SCOPE_MISMATCH");
        }
        let root = fs::canonicalize(&self.root)
            .await
            .context("CONNECTOR_ROOT_UNAVAILABLE")?;
        let file = fs::canonicalize(root.join(&relative))
            .await
            .context("CONNECTOR_NOT_FOUND")?;
        if !file.starts_with(&root) {
            anyhow::bail!("CONNECTOR_PATH_ESCAPE");
        }
        let metadata = fs::metadata(&file).await?;
        if !metadata.is_file() || metadata.len() == 0 || metadata.len() > MAX_CONNECTOR_BYTES {
            anyhow::bail!("CONNECTOR_FILE_INVALID");
        }
        let bytes = fs::read(file).await?;
        parse_connector_secret(&bytes)
    }
}

#[async_trait]
impl ConnectorResolver for AwsConnectorResolver {
    fn supports(&self, reference: &str) -> bool {
        reference.starts_with("aws-sm://") || reference.starts_with("aws-secrets-manager://")
    }

    async fn resolve(
        &self,
        reference: &str,
        organization_id: Uuid,
        provider: &str,
    ) -> anyhow::Result<ConnectorSecret> {
        let remainder = reference
            .strip_prefix("aws-sm://")
            .or_else(|| reference.strip_prefix("aws-secrets-manager://"))
            .ok_or_else(|| anyhow::anyhow!("CONNECTOR_REFERENCE_INVALID"))?;
        let (region, secret_id) = remainder
            .split_once('/')
            .ok_or_else(|| anyhow::anyhow!("CONNECTOR_REFERENCE_INVALID"))?;
        const EU_REGIONS: &[&str] = &[
            "eu-central-1",
            "eu-central-2",
            "eu-west-1",
            "eu-west-2",
            "eu-west-3",
            "eu-north-1",
            "eu-south-1",
            "eu-south-2",
        ];
        let expected = format!("orgs/{organization_id}/{provider}/");
        if !EU_REGIONS.contains(&region)
            || !secret_id.starts_with(&expected)
            || secret_id.contains(['?', '#'])
            || secret_id.chars().any(char::is_whitespace)
        {
            anyhow::bail!("CONNECTOR_TENANT_SCOPE_MISMATCH");
        }
        let mut command = Command::new(&self.binary);
        command
            .env_clear()
            .env("PATH", "/usr/local/bin:/usr/bin:/bin")
            .env("LANG", "C")
            .env("LC_ALL", "C")
            .env("AWS_REGION", region)
            .env("AWS_DEFAULT_REGION", region)
            .args([
                "secretsmanager",
                "get-secret-value",
                "--region",
                region,
                "--secret-id",
                secret_id,
                "--query",
                "SecretString",
                "--output",
                "text",
                "--no-cli-pager",
            ]);
        for key in [
            "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI",
            "AWS_CONTAINER_CREDENTIALS_FULL_URI",
            "AWS_CONTAINER_AUTHORIZATION_TOKEN",
            "AWS_WEB_IDENTITY_TOKEN_FILE",
            "AWS_ROLE_ARN",
        ] {
            if let Some(value) = std::env::var_os(key) {
                command.env(key, value);
            }
        }
        let output = timeout(self.timeout, command.kill_on_drop(true).output())
            .await
            .map_err(|_| anyhow::anyhow!("CONNECTOR_RESOLUTION_TIMEOUT"))??;
        if !output.status.success() || output.stdout.len() as u64 > MAX_CONNECTOR_BYTES {
            anyhow::bail!("CONNECTOR_RESOLUTION_FAILED");
        }
        parse_connector_secret(&output.stdout)
    }
}

#[async_trait]
trait ProviderAdapter: Send + Sync {
    fn name(&self) -> &'static str;
    fn remote_identity(&self, remote: &str) -> Option<String>;
    fn push_url(&self, external_repository_id: &str) -> anyhow::Result<String>;
    async fn ensure_draft_pull_request(
        &self,
        repository: &str,
        base: &str,
        head: &str,
        ticket_id: Uuid,
        source_run_id: Uuid,
        secret: &ConnectorSecret,
    ) -> anyhow::Result<DraftPullRequest>;
}

struct GithubAdapter {
    http: Client,
    api_url: String,
}

#[async_trait]
impl ProviderAdapter for GithubAdapter {
    fn name(&self) -> &'static str {
        "github"
    }

    fn remote_identity(&self, remote: &str) -> Option<String> {
        github_remote_identity(remote)
    }

    fn push_url(&self, external_repository_id: &str) -> anyhow::Result<String> {
        validate_repository_id(external_repository_id)?;
        Ok(format!("https://github.com/{external_repository_id}.git"))
    }

    async fn ensure_draft_pull_request(
        &self,
        repository: &str,
        base: &str,
        head: &str,
        ticket_id: Uuid,
        source_run_id: Uuid,
        secret: &ConnectorSecret,
    ) -> anyhow::Result<DraftPullRequest> {
        let existing = self
            .list_pull_requests(repository, base, head, secret)
            .await?;
        if !existing.is_empty() {
            return exactly_one_draft(existing, repository, base, head);
        }
        let response = self
            .request(
                reqwest::Method::POST,
                &format!("/repos/{repository}/pulls"),
                secret,
            )
            .json(&json!({
                "title":format!("fix: OpenTicket {ticket_id}"),
                "body":format!("## OpenOS delivery\n\nTicket: `{ticket_id}`\nCandidate run: `{source_run_id}`\n\nThis pull request is a draft and requires human review."),
                "head":head,
                "base":base,
                "draft":true,
            }))
            .send()
            .await?;
        if response.status() != StatusCode::CREATED
            && response.status() != StatusCode::UNPROCESSABLE_ENTITY
        {
            anyhow::bail!("PROVIDER_PULL_REQUEST_CREATE_FAILED: {}", response.status());
        }
        // A concurrent retry can win creation. GitHub's head/base uniqueness plus
        // this authoritative re-read makes both paths converge on one draft PR.
        let final_state = self
            .list_pull_requests(repository, base, head, secret)
            .await?;
        exactly_one_draft(final_state, repository, base, head)
    }
}

impl GithubAdapter {
    fn request(
        &self,
        method: reqwest::Method,
        path: &str,
        secret: &ConnectorSecret,
    ) -> reqwest::RequestBuilder {
        self.http
            .request(method, format!("{}{}", self.api_url, path))
            .header("accept", "application/vnd.github+json")
            .header("x-github-api-version", "2022-11-28")
            .header("user-agent", "OpenAgents-trusted-delivery")
            .bearer_auth(&secret.token)
    }

    async fn list_pull_requests(
        &self,
        repository: &str,
        base: &str,
        head: &str,
        secret: &ConnectorSecret,
    ) -> anyhow::Result<Vec<DraftPullRequest>> {
        let owner = repository
            .split_once('/')
            .map(|(owner, _)| owner)
            .ok_or_else(|| anyhow::anyhow!("PROVIDER_REPOSITORY_INVALID"))?;
        let response = self
            .request(
                reqwest::Method::GET,
                &format!("/repos/{repository}/pulls"),
                secret,
            )
            .query(&[
                ("state", "open"),
                ("base", base),
                ("head", &format!("{owner}:{head}")),
                ("per_page", "100"),
            ])
            .send()
            .await?;
        if !response.status().is_success() {
            anyhow::bail!("PROVIDER_PULL_REQUEST_LOOKUP_FAILED: {}", response.status());
        }
        let values: Vec<Value> = response.json().await?;
        values
            .into_iter()
            .map(|value| parse_github_pull_request(&value, repository, base, head))
            .collect()
    }
}

pub async fn execute(
    job: &WorkerJob,
    run_id: Uuid,
    config: &Config,
    client: &ControlPlaneClient,
    store: &RunStore,
    settlement_started: Arc<AtomicBool>,
) -> anyhow::Result<WorkerResult> {
    validate_delivery_job(job, config)?;
    let envelope: SignedWorkerEnvelope<DeliveryRequest> = serde_json::from_value(
        job.inputs
            .get("request")
            .cloned()
            .ok_or_else(|| anyhow::anyhow!("DELIVERY_CLAIM_MISSING"))?,
    )
    .context("DELIVERY_CLAIM_MALFORMED")?;
    client.verify_control_plane(&envelope, WorkerMessageKind::Claim)?;
    validate_claim_subject(job, &envelope)?;
    let request = &envelope.payload;

    let _lock = acquire_delivery_lock(config, request).await?;
    let candidate = verify_candidate(config, job, request).await?;
    store
        .update(
            run_id,
            RunStatus::Running,
            "delivery.candidate.verified",
            json!({
                "delivery_id":request.delivery_id,
                "commit_sha":request.commit_sha,
                "branch":candidate.local_branch,
                "provider":request.provider.provider,
            }),
        )
        .await;

    let provider = provider_adapter(config, &request.provider.provider)?;
    let actual_identity = provider
        .remote_identity(&candidate.remote_url)
        .ok_or_else(|| anyhow::anyhow!("REGISTERED_REMOTE_INVALID"))?;
    if !actual_identity.eq_ignore_ascii_case(&request.provider.external_repository_id) {
        anyhow::bail!("REGISTERED_REMOTE_IDENTITY_MISMATCH");
    }

    // Credential resolution happens only after every signed subject and local
    // candidate check. No OpenCode or QA process is reachable from this module.
    let connector = resolve_connector(config, request).await?;
    let auth = GitAuth::new(&connector)?;
    let push_url = provider.push_url(&request.provider.external_repository_id)?;
    ensure_remote_branch(
        config,
        &candidate.worktree,
        &push_url,
        &request.remote_branch,
        &request.commit_sha,
        &auth,
    )
    .await?;
    store
        .update(
            run_id,
            RunStatus::Running,
            "delivery.branch.verified",
            json!({"delivery_id":request.delivery_id,"branch":request.remote_branch,"commit_sha":request.commit_sha}),
        )
        .await;

    let pull_request = provider
        .ensure_draft_pull_request(
            &request.provider.external_repository_id,
            &request.base_ref,
            &request.remote_branch,
            request.ticket_id,
            request.workspace.run_id,
            &connector,
        )
        .await?;
    drop(auth);
    drop(connector);
    store
        .update(
            run_id,
            RunStatus::Running,
            "delivery.pull_request.verified",
            json!({"delivery_id":request.delivery_id,"provider":provider.name(),"url":pull_request.url,"number":pull_request.number,"draft":true}),
        )
        .await;

    let callback = json!({
        "job_id":job.job_id,
        "lease_token":job.lease_token,
        "lease_owner":client.worker_id(),
        "repository_id":request.repository_id,
        "base_ref":request.base_ref,
        "branch_name":request.remote_branch,
        "commit_sha":request.commit_sha.to_ascii_lowercase(),
        "diff_digest":request.diff_digest.to_ascii_lowercase(),
        "candidate_digest":request.candidate_digest.to_ascii_lowercase(),
        "approval_subject_digest":request.approval_subject_digest,
        "provider":provider.name(),
        "external_repository_id":request.provider.external_repository_id,
        "pull_request_url":pull_request.url,
        "pull_request_number":pull_request.number,
        "draft":true,
        "status":"open",
        "verification":{
            "canonical_managed_worktree":true,
            "workspace_identity":true,
            "exact_head":true,
            "clean_index_and_worktree":true,
            "registered_remote_identity":true,
            "idempotent_branch":true,
            "single_draft_pull_request":true,
        }
    });
    settlement_started.store(true, Ordering::Release);
    if let Err(error) = submit_callback(client, job, &envelope, callback).await {
        settlement_started.store(false, Ordering::Release);
        return Err(error);
    }

    let artifact = WorkerArtifact {
        kind: "draft_pull_request".into(),
        name: "Trusted delivery draft pull request".into(),
        uri: pull_request.url.clone(),
        sha256: None,
        metadata: json!({
            "delivery_id":request.delivery_id,
            "provider":provider.name(),
            "number":pull_request.number,
            "base_branch":pull_request.base,
            "head_branch":pull_request.head,
            "draft":true,
        }),
    };
    Ok(WorkerResult {
        run_id,
        artifacts: vec![artifact],
        stderr: None,
        exit_status: 0,
        tests: vec![],
        git: Some(GitEvidence {
            repository: request.provider.external_repository_id.clone(),
            worktree: candidate.worktree.display().to_string(),
            branch: request.remote_branch.clone(),
            commit_sha: request.commit_sha.to_ascii_lowercase(),
            clean: true,
            pushed: true,
            remote: Some(candidate.remote_url),
        }),
        engine_session_id: None,
        loaded_skills: vec![],
        acceptance_evidence: vec![],
        cognitive_observations: vec![],
        engineering_workspace: None,
        qa: vec![],
        pull_request: Some(PullRequestEvidence {
            provider: provider.name().into(),
            url: pull_request.url,
            number: Some(pull_request.number),
            base_branch: pull_request.base,
            head_branch: pull_request.head,
            draft: true,
            review_required: true,
            status: "open".into(),
        }),
    })
}

fn validate_delivery_job(job: &WorkerJob, config: &Config) -> anyhow::Result<()> {
    if job.job_type != "engineering.delivery"
        || !job
            .required_capabilities
            .iter()
            .any(|value| value == "engineering.delivery")
    {
        anyhow::bail!("UNSUPPORTED_DELIVERY_JOB");
    }
    if job.organization_id != config.organization_id {
        anyhow::bail!("DELIVERY_TENANT_MISMATCH");
    }
    if job.deadline.is_some_and(|deadline| deadline <= Utc::now()) {
        anyhow::bail!("DELIVERY_JOB_STALE");
    }
    Ok(())
}

fn validate_claim_subject(
    job: &WorkerJob,
    envelope: &SignedWorkerEnvelope<DeliveryRequest>,
) -> anyhow::Result<()> {
    let request = &envelope.payload;
    if envelope.organization_id != job.organization_id
        || envelope.correlation_id != job.correlation_id
        || envelope.idempotency_key != job.idempotency_key
        || request.plan_id != job.plan_id
        || request.task_id != job.task_id
        || request.ticket_id != job.ticket_id
        || request.workspace.organization_id != job.organization_id
        || request.remote_branch != format!("delivery/{}", request.source_job_id)
    {
        anyhow::bail!("DELIVERY_CLAIM_SUBJECT_MISMATCH");
    }
    if job
        .deadline
        .is_some_and(|deadline| envelope.expires_at != deadline)
    {
        anyhow::bail!("DELIVERY_CLAIM_DEADLINE_MISMATCH");
    }
    let expected_candidate = format!(
        "{:x}",
        Sha256::digest(
            format!(
                "{}:{}",
                request.commit_sha.to_ascii_lowercase(),
                request.diff_digest.to_ascii_lowercase()
            )
            .as_bytes()
        )
    );
    if !valid_hex(&request.commit_sha, 40)
        || !valid_hex(&request.diff_digest, 64)
        || !valid_hex(&request.candidate_digest, 64)
        || !valid_hex(&request.approval_subject_digest, 64)
        || request.candidate_digest != expected_candidate
        || request.approval_subject_digest != approval_subject_digest(job.organization_id, request)
    {
        anyhow::bail!("DELIVERY_DIGEST_SUBJECT_MISMATCH");
    }
    if request.callback.method != "POST"
        || request.callback.path != format!("/v1/deliveries/{}/provider", request.delivery_id)
        || request.callback.required_producer != "OpenAgents"
        || request.provider.provider != "github"
        || request.preflight.is_null()
        || !all_requirements_true(&request.requirements)
    {
        anyhow::bail!("DELIVERY_CLAIM_POLICY_MISMATCH");
    }
    validate_repository_id(&request.provider.external_repository_id)?;
    validate_base_ref(&request.base_ref)?;
    Ok(())
}

fn all_requirements_true(value: &DeliveryRequirements) -> bool {
    value.canonical_managed_worktree
        && value.exact_head
        && value.clean_index_and_worktree
        && value.registered_remote_identity
        && value.draft_pull_request
        && value.idempotent_branch_and_pull_request
        && value.credentials_visible_only_inside_trusted_delivery_stage
}

fn approval_subject_digest(organization_id: Uuid, request: &DeliveryRequest) -> String {
    let subject = json!({
        "organization_id":organization_id,
        "delivery_id":request.delivery_id,
        "repository_id":request.repository_id,
        "base_ref":request.base_ref,
        "commit_sha":request.commit_sha.to_ascii_lowercase(),
        "diff_digest":request.diff_digest.to_ascii_lowercase(),
        "candidate_digest":request.candidate_digest.to_ascii_lowercase(),
    });
    format!(
        "{:x}",
        Sha256::digest(serde_json::to_vec(&subject).unwrap_or_default())
    )
}

async fn verify_candidate(
    config: &Config,
    job: &WorkerJob,
    request: &DeliveryRequest,
) -> anyhow::Result<VerifiedCandidate> {
    let managed_root = fs::canonicalize(&config.managed_root)
        .await
        .context("MANAGED_WORKTREE_ROOT_UNAVAILABLE")?;
    let expected_root = fs::canonicalize(
        config
            .managed_root
            .join(job.organization_id.to_string())
            .join(job.plan_id.to_string()),
    )
    .await
    .context("WORKSPACE_ROOT_MISSING")?;
    let claimed_root = fs::canonicalize(&request.workspace.workspace_root)
        .await
        .context("WORKSPACE_ROOT_MISSING")?;
    let worktree = fs::canonicalize(&request.workspace.repository_folder)
        .await
        .context("WORKTREE_MISSING")?;
    let expected_folder =
        expected_root.join(safe_workspace_name(&request.workspace.repository_name)?);
    if expected_root != claimed_root
        || !claimed_root.starts_with(&managed_root)
        || worktree != expected_folder
        || !worktree.starts_with(&claimed_root)
    {
        anyhow::bail!("CANONICAL_WORKTREE_IDENTITY_MISMATCH");
    }
    verify_candidate_git_pointer(&claimed_root, &worktree).await?;
    // Inspect local configuration before commands such as status, diff, or
    // signature verification that can otherwise dispatch configured helpers.
    reject_dangerous_local_git_config(config, &worktree).await?;
    let top = git_text(
        config,
        &worktree,
        &["rev-parse", "--path-format=absolute", "--show-toplevel"],
    )
    .await?;
    if fs::canonicalize(top).await? != worktree {
        anyhow::bail!("GIT_WORKTREE_ROOT_MISMATCH");
    }
    let head = git_text(config, &worktree, &["rev-parse", "HEAD"]).await?;
    if !head.eq_ignore_ascii_case(&request.commit_sha) {
        anyhow::bail!("DELIVERY_HEAD_MISMATCH");
    }
    let local_branch = git_text(
        config,
        &worktree,
        &["symbolic-ref", "--quiet", "--short", "HEAD"],
    )
    .await?;
    let expected_local_branch = format!(
        "openos/{}-{}-{}",
        job.ticket_id,
        safe_workspace_name(&request.workspace.repository_name)?.to_ascii_lowercase(),
        &job.task_id.to_string()[..8],
    );
    if local_branch != expected_local_branch {
        anyhow::bail!("DELIVERY_LOCAL_BRANCH_MISMATCH");
    }
    let status = git_text(
        config,
        &worktree,
        &["status", "--porcelain=v1", "--untracked-files=all"],
    )
    .await?;
    if !status.is_empty() {
        anyhow::bail!("DELIVERY_WORKTREE_DIRTY");
    }
    git_success(config, &worktree, &["diff", "--quiet"]).await?;
    git_success(config, &worktree, &["diff", "--cached", "--quiet"]).await?;
    verify_candidate_signature(config, &worktree, &request.commit_sha).await?;
    let parent = git_text(config, &worktree, &["rev-parse", "HEAD^"]).await?;
    let diff = git_bytes(
        config,
        &worktree,
        &[
            "diff",
            "--no-ext-diff",
            "--unified=3",
            &parent,
            &request.commit_sha,
            "--",
        ],
    )
    .await?;
    if format!("{:x}", Sha256::digest(&diff)) != request.diff_digest.to_ascii_lowercase() {
        anyhow::bail!("DELIVERY_DIFF_DIGEST_MISMATCH");
    }
    let remote_url = git_text(
        config,
        &worktree,
        &["remote", "get-url", &config.git_remote],
    )
    .await?;
    if remote_url.contains('@') && remote_url.starts_with("http") {
        anyhow::bail!("REGISTERED_REMOTE_CONTAINS_CREDENTIALS");
    }
    Ok(VerifiedCandidate {
        worktree,
        local_branch,
        remote_url,
    })
}

async fn verify_candidate_git_pointer(
    workspace_root: &Path,
    worktree: &Path,
) -> anyhow::Result<()> {
    let name = worktree
        .file_name()
        .ok_or_else(|| anyhow::anyhow!("CANDIDATE_GIT_POINTER_INVALID"))?;
    let control_root = workspace_root.join(".openos-control");
    let git_dir = control_root.join(name).with_extension("git");
    let control_metadata = fs::symlink_metadata(&control_root).await?;
    let git_dir_metadata = fs::symlink_metadata(&git_dir).await?;
    let pointer = worktree.join(".git");
    let pointer_metadata = fs::symlink_metadata(&pointer).await?;
    if !control_metadata.is_dir()
        || control_metadata.file_type().is_symlink()
        || !git_dir_metadata.is_dir()
        || git_dir_metadata.file_type().is_symlink()
        || !pointer_metadata.is_file()
        || pointer_metadata.file_type().is_symlink()
        || fs::canonicalize(&git_dir).await? != git_dir
        || fs::read_to_string(&pointer).await? != format!("gitdir: {}\n", git_dir.display())
    {
        anyhow::bail!("CANDIDATE_GIT_POINTER_INVALID");
    }
    Ok(())
}

async fn reject_dangerous_local_git_config(config: &Config, worktree: &Path) -> anyhow::Result<()> {
    let values = git_bytes(config, worktree, &["config", "--local", "--null", "--list"]).await?;
    for entry in values
        .split(|byte| *byte == 0)
        .filter(|entry| !entry.is_empty())
    {
        let key = String::from_utf8_lossy(entry)
            .split(['\n', '='])
            .next()
            .unwrap_or_default()
            .to_ascii_lowercase();
        if key.starts_with("credential.")
            || key.starts_with("http.")
            || key.starts_with("https.")
            || key.starts_with("url.")
            || key.starts_with("include.")
            || key.starts_with("includeif.")
            || key.starts_with("diff.")
            || key.starts_with("filter.")
            || key.starts_with("gpg.")
            || key.starts_with("merge.")
            || key.starts_with("protocol.")
            || matches!(
                key.as_str(),
                "core.sshcommand" | "core.gitproxy" | "core.hookspath" | "core.fsmonitor"
            )
            || (key.starts_with("remote.")
                && (key.ends_with(".proxy")
                    || key.ends_with(".pushurl")
                    || key.ends_with(".receivepack")
                    || key.ends_with(".uploadpack")))
        {
            anyhow::bail!("UNTRUSTED_GIT_CONFIG_REJECTED");
        }
    }
    Ok(())
}

fn provider_adapter(config: &Config, provider: &str) -> anyhow::Result<Arc<dyn ProviderAdapter>> {
    match provider {
        "github" => Ok(Arc::new(GithubAdapter {
            http: Client::builder().timeout(config.request_timeout).build()?,
            api_url: config.github_api_url.clone(),
        })),
        _ => anyhow::bail!("PROVIDER_ADAPTER_UNAVAILABLE"),
    }
}

async fn resolve_connector(
    config: &Config,
    request: &DeliveryRequest,
) -> anyhow::Result<ConnectorSecret> {
    let resolvers: [Box<dyn ConnectorResolver>; 2] = [
        Box::new(LocalConnectorResolver {
            root: config.connector_root.clone(),
        }),
        Box::new(AwsConnectorResolver {
            binary: config.aws_binary.clone(),
            timeout: config.request_timeout,
        }),
    ];
    let resolver = resolvers
        .iter()
        .find(|resolver| resolver.supports(&request.provider.connector_ref))
        .ok_or_else(|| anyhow::anyhow!("CONNECTOR_RESOLVER_UNAVAILABLE"))?;
    resolver
        .resolve(
            &request.provider.connector_ref,
            request.workspace.organization_id,
            &request.provider.provider,
        )
        .await
}

fn tenant_connector_path(reference: &str, organization_id: Uuid) -> anyhow::Result<PathBuf> {
    let path = reference
        .strip_prefix("local-connector://")
        .ok_or_else(|| anyhow::anyhow!("CONNECTOR_REFERENCE_INVALID"))?;
    let prefix = format!("orgs/{organization_id}/");
    let relative = path
        .strip_prefix(&prefix)
        .filter(|value| !value.is_empty())
        .ok_or_else(|| anyhow::anyhow!("CONNECTOR_TENANT_SCOPE_MISMATCH"))?;
    let relative = PathBuf::from(relative);
    if relative.is_absolute()
        || relative
            .components()
            .any(|component| !matches!(component, Component::Normal(_)))
    {
        anyhow::bail!("CONNECTOR_PATH_ESCAPE");
    }
    Ok(relative)
}

fn parse_connector_secret(bytes: &[u8]) -> anyhow::Result<ConnectorSecret> {
    let text = std::str::from_utf8(bytes)?.trim();
    let (token, username) = match serde_json::from_str::<Value>(text) {
        Ok(value) => {
            let token = ["token", "access_token", "password"]
                .iter()
                .find_map(|key| value.get(*key).and_then(Value::as_str))
                .ok_or_else(|| anyhow::anyhow!("CONNECTOR_TOKEN_MISSING"))?;
            let username = value
                .get("username")
                .and_then(Value::as_str)
                .unwrap_or("x-access-token");
            (token.to_string(), username.to_string())
        }
        Err(_) => (text.to_string(), "x-access-token".into()),
    };
    if token.is_empty()
        || token.len() > 8192
        || token.chars().any(char::is_whitespace)
        || username.is_empty()
        || username.chars().any(|value| value.is_control())
    {
        anyhow::bail!("CONNECTOR_SECRET_INVALID");
    }
    Ok(ConnectorSecret { token, username })
}

struct GitAuth {
    _directory: tempfile::TempDir,
    askpass: PathBuf,
    token: String,
    username: String,
}

impl GitAuth {
    fn new(secret: &ConnectorSecret) -> anyhow::Result<Self> {
        let directory = tempfile::Builder::new()
            .prefix("openagents-delivery-")
            .tempdir()?;
        let askpass = directory.path().join("askpass.sh");
        std::fs::write(
            &askpass,
            b"#!/bin/sh\ncase \"$1\" in\n  *Username*) printf '%s' \"$OPENAGENTS_CONNECTOR_USERNAME\" ;;\n  *) printf '%s' \"$OPENAGENTS_CONNECTOR_TOKEN\" ;;\nesac\n",
        )?;
        std::fs::set_permissions(&askpass, std::fs::Permissions::from_mode(0o700))?;
        Ok(Self {
            _directory: directory,
            askpass,
            token: secret.token.clone(),
            username: secret.username.clone(),
        })
    }

    fn apply(&self, command: &mut Command) {
        command
            .env("GIT_TERMINAL_PROMPT", "0")
            .env("GIT_ASKPASS", &self.askpass)
            .env("OPENAGENTS_CONNECTOR_TOKEN", &self.token)
            .env("OPENAGENTS_CONNECTOR_USERNAME", &self.username);
    }
}

impl Drop for GitAuth {
    fn drop(&mut self) {
        self.token.zeroize();
        self.username.zeroize();
    }
}

async fn ensure_remote_branch(
    config: &Config,
    worktree: &Path,
    push_url: &str,
    branch: &str,
    commit: &str,
    auth: &GitAuth,
) -> anyhow::Result<()> {
    validate_delivery_branch(branch)?;
    let existing = remote_branch_head(config, worktree, push_url, branch, auth).await?;
    match remote_branch_action(existing.as_deref(), commit)? {
        RemoteBranchAction::Reuse => return Ok(()),
        RemoteBranchAction::Push => {}
    }
    let refspec = format!("{commit}:refs/heads/{branch}");
    let mut command = delivery_git_command(config, worktree);
    command.args([
        "-c",
        "credential.helper=",
        "-c",
        "core.hooksPath=/dev/null",
        "push",
        "--porcelain",
        "--no-verify",
        push_url,
        &refspec,
    ]);
    auth.apply(&mut command);
    checked_command(&mut command, config.git_timeout, "GIT_PUSH_FAILED").await?;
    let final_head = remote_branch_head(config, worktree, push_url, branch, auth)
        .await?
        .ok_or_else(|| anyhow::anyhow!("REMOTE_BRANCH_MISSING_AFTER_PUSH"))?;
    if !final_head.eq_ignore_ascii_case(commit) {
        anyhow::bail!("REMOTE_BRANCH_COMMIT_MISMATCH");
    }
    Ok(())
}

#[derive(Debug, PartialEq, Eq)]
enum RemoteBranchAction {
    Reuse,
    Push,
}

fn remote_branch_action(
    existing: Option<&str>,
    commit: &str,
) -> anyhow::Result<RemoteBranchAction> {
    match existing {
        Some(existing) if existing.eq_ignore_ascii_case(commit) => Ok(RemoteBranchAction::Reuse),
        Some(_) => anyhow::bail!("REMOTE_BRANCH_CONFLICT"),
        None => Ok(RemoteBranchAction::Push),
    }
}

async fn remote_branch_head(
    config: &Config,
    worktree: &Path,
    url: &str,
    branch: &str,
    auth: &GitAuth,
) -> anyhow::Result<Option<String>> {
    let reference = format!("refs/heads/{branch}");
    let mut command = delivery_git_command(config, worktree);
    command.args([
        "-c",
        "credential.helper=",
        "ls-remote",
        "--heads",
        url,
        &reference,
    ]);
    auth.apply(&mut command);
    let output = timeout(config.git_timeout, command.kill_on_drop(true).output())
        .await
        .map_err(|_| anyhow::anyhow!("REMOTE_BRANCH_LOOKUP_TIMEOUT"))??;
    if !output.status.success() {
        anyhow::bail!("REMOTE_BRANCH_LOOKUP_FAILED");
    }
    let stdout = String::from_utf8(output.stdout)?;
    let heads = stdout
        .lines()
        .filter_map(|line| line.split_once('\t'))
        .filter(|(_, name)| *name == reference)
        .map(|(sha, _)| sha.to_ascii_lowercase())
        .collect::<Vec<_>>();
    if heads.len() > 1 || heads.iter().any(|head| !valid_hex(head, 40)) {
        anyhow::bail!("REMOTE_BRANCH_RESPONSE_INVALID");
    }
    Ok(heads.into_iter().next())
}

async fn submit_callback(
    client: &ControlPlaneClient,
    job: &WorkerJob,
    envelope: &SignedWorkerEnvelope<DeliveryRequest>,
    payload: Value,
) -> anyhow::Result<()> {
    let request = &envelope.payload;
    let idempotency_key = format!(
        "delivery:{}:provider:{}",
        request.delivery_id, request.approval_subject_digest
    );
    for attempt in 0..CALLBACK_ATTEMPTS {
        let now = Utc::now();
        let expires_at = std::cmp::min(envelope.expires_at, now + chrono::Duration::minutes(2));
        match client
            .provider_callback(
                &request.callback.path,
                job.correlation_id,
                &idempotency_key,
                expires_at,
                payload.clone(),
            )
            .await
        {
            Ok(()) => return Ok(()),
            Err(error) => {
                // The callback may have committed before its response was lost.
                // Confirm the exact signed persisted envelope before retrying or
                // treating a lease conflict as failure.
                if client
                    .confirm_provider_callback(
                        request.delivery_id,
                        job.correlation_id,
                        &idempotency_key,
                        &payload,
                    )
                    .await
                    .unwrap_or(false)
                {
                    return Ok(());
                }
                if classify_failure(&error) == ControlPlaneFailure::Transient
                    && attempt + 1 < CALLBACK_ATTEMPTS
                {
                    tokio::time::sleep(Duration::from_millis(250 * (1u64 << attempt))).await;
                    continue;
                }
                return Err(error.context("PROVIDER_CALLBACK_FAILED"));
            }
        }
    }
    unreachable!("bounded callback attempts return")
}

async fn acquire_delivery_lock(
    config: &Config,
    request: &DeliveryRequest,
) -> anyhow::Result<std::fs::File> {
    let directory = config
        .delivery_lock_root
        .join(request.workspace.organization_id.to_string());
    fs::create_dir_all(&directory).await?;
    let path = directory.join(format!("{}.lock", request.delivery_id));
    tokio::task::spawn_blocking(move || {
        let file = OpenOptions::new()
            .create(true)
            .truncate(false)
            .read(true)
            .write(true)
            .open(path)?;
        file.lock_exclusive()?;
        Ok::<_, std::io::Error>(file)
    })
    .await?
    .map_err(Into::into)
}

fn delivery_git_command(config: &Config, worktree: &Path) -> Command {
    let mut command = Command::new(&config.git_binary);
    command
        .env_clear()
        .env("PATH", "/usr/local/bin:/usr/bin:/bin")
        .env("HOME", "/nonexistent")
        .env("LANG", "C")
        .env("LC_ALL", "C")
        .env("GIT_CONFIG_NOSYSTEM", "1")
        .env("GIT_CONFIG_GLOBAL", "/dev/null")
        .arg("-C")
        .arg(worktree)
        .stdin(Stdio::null());
    command
}

struct EphemeralVerificationIdentity {
    home: tempfile::TempDir,
    fingerprint: String,
}

impl Drop for EphemeralVerificationIdentity {
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

async fn prepare_ephemeral_verification_identity(
    config: &Config,
) -> anyhow::Result<EphemeralVerificationIdentity> {
    let encoded = config
        .git_signing_public_key_b64
        .as_deref()
        .context("DELIVERY_GIT_TRUST_REQUIRED")?
        .trim();
    if encoded.is_empty() || encoded.len() > MAX_GIT_SIGNING_PUBLIC_KEY_B64_BYTES {
        anyhow::bail!("DELIVERY_GIT_TRUST_KEY_SIZE_INVALID");
    }
    let expected = normalize_fingerprint(
        config
            .git_signing_fingerprint
            .as_deref()
            .context("DELIVERY_GIT_TRUST_REQUIRED")?,
    )?;
    let key = Zeroizing::new(
        STANDARD
            .decode(encoded)
            .context("DELIVERY_GIT_TRUST_KEY_INVALID")?,
    );
    let home = tempfile::Builder::new()
        .prefix("openagents-delivery-trust-")
        .tempdir()
        .context("DELIVERY_GIT_TRUST_HOME_CREATE_FAILED")?;
    std::fs::set_permissions(home.path(), std::fs::Permissions::from_mode(0o700))?;

    let mut import = Command::new("gpg");
    import
        .env_clear()
        .env("HOME", home.path())
        .env("GNUPGHOME", home.path())
        .env("PATH", "/usr/local/bin:/usr/bin:/bin")
        .args(["--batch", "--no-tty", "--import"])
        .kill_on_drop(true)
        .stdin(Stdio::piped())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    let mut child = import.spawn().context("DELIVERY_GIT_TRUST_IMPORT_FAILED")?;
    let mut stdin = child
        .stdin
        .take()
        .context("DELIVERY_GIT_TRUST_IMPORT_STDIN_MISSING")?;
    stdin.write_all(&key).await?;
    drop(stdin);
    let status = timeout(config.git_timeout, child.wait())
        .await
        .map_err(|_| anyhow::anyhow!("DELIVERY_GIT_TRUST_IMPORT_TIMEOUT"))??;
    if !status.success() {
        anyhow::bail!("DELIVERY_GIT_TRUST_IMPORT_FAILED");
    }

    let listing = gpg_listing(config, home.path(), &["--list-keys"]).await?;
    let fingerprints = primary_fingerprints(&listing);
    if fingerprints.len() != 1 || fingerprints[0] != expected {
        anyhow::bail!("DELIVERY_GIT_TRUST_FINGERPRINT_MISMATCH");
    }
    let secret_listing = gpg_listing(config, home.path(), &["--list-secret-keys"]).await?;
    if secret_listing.lines().any(|line| line.starts_with("sec:")) {
        anyhow::bail!("DELIVERY_GIT_TRUST_CONTAINS_PRIVATE_KEY");
    }
    Ok(EphemeralVerificationIdentity {
        home,
        fingerprint: expected,
    })
}

async fn gpg_listing(config: &Config, home: &Path, args: &[&str]) -> anyhow::Result<String> {
    let mut command = Command::new("gpg");
    command
        .env_clear()
        .env("HOME", home)
        .env("GNUPGHOME", home)
        .env("PATH", "/usr/local/bin:/usr/bin:/bin")
        .args(["--batch", "--no-tty", "--with-colons"])
        .args(args)
        .kill_on_drop(true)
        .stdin(Stdio::null());
    let output = timeout(config.git_timeout, command.output())
        .await
        .map_err(|_| anyhow::anyhow!("DELIVERY_GIT_TRUST_LIST_TIMEOUT"))??;
    if !output.status.success() {
        anyhow::bail!("DELIVERY_GIT_TRUST_LIST_FAILED");
    }
    Ok(String::from_utf8(output.stdout)?)
}

fn primary_fingerprints(listing: &str) -> Vec<String> {
    let mut awaiting_fingerprint = false;
    let mut fingerprints = Vec::new();
    for line in listing.lines() {
        let kind = line.split(':').next().unwrap_or_default();
        if matches!(kind, "pub" | "sec") {
            awaiting_fingerprint = true;
        } else if kind == "fpr" && awaiting_fingerprint {
            if let Some(value) = line.split(':').nth(9) {
                fingerprints.push(value.to_ascii_uppercase());
            }
            awaiting_fingerprint = false;
        }
    }
    fingerprints
}

fn normalize_fingerprint(value: &str) -> anyhow::Result<String> {
    let value = value.trim();
    if !matches!(value.len(), 40 | 64) || !value.bytes().all(|byte| byte.is_ascii_hexdigit()) {
        anyhow::bail!("DELIVERY_GIT_TRUST_FINGERPRINT_INVALID");
    }
    Ok(value.to_ascii_uppercase())
}

async fn verify_candidate_signature(
    config: &Config,
    worktree: &Path,
    commit: &str,
) -> anyhow::Result<()> {
    let identity = prepare_ephemeral_verification_identity(config).await?;
    let mut command = delivery_git_command(config, worktree);
    command
        .env("GNUPGHOME", identity.home.path())
        .args(["-c", "gpg.format=openpgp", "-c", "gpg.program=gpg"])
        .args(["verify-commit", "--raw", commit]);
    let output = timeout(config.git_timeout, command.kill_on_drop(true).output())
        .await
        .map_err(|_| anyhow::anyhow!("DELIVERY_COMMIT_SIGNATURE_TIMEOUT"))??;
    if !output.status.success() {
        anyhow::bail!("DELIVERY_COMMIT_SIGNATURE_INVALID");
    }
    let status = String::from_utf8_lossy(&output.stderr);
    let valid = status
        .lines()
        .filter_map(|line| line.strip_prefix("[GNUPG:] VALIDSIG "))
        .map(|details| details.split_whitespace().collect::<Vec<_>>())
        .collect::<Vec<_>>();
    if valid.len() != 1 {
        anyhow::bail!("DELIVERY_COMMIT_SIGNATURE_STATUS_INVALID");
    }
    let signer = valid[0].first().copied().unwrap_or_default();
    let primary = valid[0].get(9).copied().unwrap_or_default();
    if !signer.eq_ignore_ascii_case(&identity.fingerprint)
        && !primary.eq_ignore_ascii_case(&identity.fingerprint)
    {
        anyhow::bail!("DELIVERY_COMMIT_SIGNER_MISMATCH");
    }
    Ok(())
}

async fn git_text(config: &Config, worktree: &Path, args: &[&str]) -> anyhow::Result<String> {
    Ok(String::from_utf8(git_bytes(config, worktree, args).await?)?
        .trim()
        .to_string())
}

async fn git_bytes(config: &Config, worktree: &Path, args: &[&str]) -> anyhow::Result<Vec<u8>> {
    let mut command = delivery_git_command(config, worktree);
    command.args(args);
    let output = timeout(config.git_timeout, command.kill_on_drop(true).output())
        .await
        .map_err(|_| anyhow::anyhow!("GIT_COMMAND_TIMEOUT"))??;
    if !output.status.success() {
        anyhow::bail!("GIT_COMMAND_FAILED");
    }
    Ok(output.stdout)
}

async fn git_success(config: &Config, worktree: &Path, args: &[&str]) -> anyhow::Result<()> {
    let mut command = delivery_git_command(config, worktree);
    command.args(args);
    checked_command(&mut command, config.git_timeout, "GIT_STATE_CHECK_FAILED").await
}

async fn checked_command(
    command: &mut Command,
    duration: Duration,
    error: &str,
) -> anyhow::Result<()> {
    let output = timeout(duration, command.kill_on_drop(true).output())
        .await
        .map_err(|_| anyhow::anyhow!("{error}: timeout"))??;
    if !output.status.success() {
        anyhow::bail!("{error}");
    }
    Ok(())
}

fn github_remote_identity(value: &str) -> Option<String> {
    let value = value.trim().trim_end_matches('/');
    let path = value
        .strip_prefix("https://github.com/")
        .or_else(|| value.strip_prefix("ssh://git@github.com/"))
        .or_else(|| value.strip_prefix("git@github.com:"))?
        .trim_end_matches(".git");
    validate_repository_id(path).ok().map(|_| path.to_string())
}

fn validate_repository_id(value: &str) -> anyhow::Result<()> {
    let parts = value.split('/').collect::<Vec<_>>();
    if parts.len() != 2
        || parts.iter().any(|part| {
            part.is_empty()
                || matches!(*part, "." | "..")
                || !part
                    .bytes()
                    .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'_' | b'-'))
        })
    {
        anyhow::bail!("PROVIDER_REPOSITORY_INVALID");
    }
    Ok(())
}

fn validate_base_ref(value: &str) -> anyhow::Result<()> {
    if value.is_empty()
        || value.len() > 255
        || value.starts_with('-')
        || value.contains("..")
        || value.contains("@{")
        || value.ends_with(['.', '/'])
        || value.bytes().any(|byte| {
            byte.is_ascii_control()
                || matches!(byte, b' ' | b'~' | b'^' | b':' | b'?' | b'*' | b'[' | b'\\')
        })
    {
        anyhow::bail!("DELIVERY_BASE_REF_INVALID");
    }
    Ok(())
}

fn validate_delivery_branch(value: &str) -> anyhow::Result<()> {
    if !value.starts_with("delivery/") {
        anyhow::bail!("DELIVERY_BRANCH_INVALID");
    }
    validate_base_ref(value)
}

fn valid_hex(value: &str, length: usize) -> bool {
    value.len() == length && value.bytes().all(|byte| byte.is_ascii_hexdigit())
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
    if name.is_empty() || matches!(name.as_str(), "." | "..") {
        anyhow::bail!("REPOSITORY_WORKSPACE_NAME_INVALID");
    }
    Ok(name)
}

fn parse_github_pull_request(
    value: &Value,
    repository: &str,
    expected_base: &str,
    expected_head: &str,
) -> anyhow::Result<DraftPullRequest> {
    let url = value
        .get("html_url")
        .and_then(Value::as_str)
        .filter(|url| url.starts_with(&format!("https://github.com/{repository}/pull/")))
        .ok_or_else(|| anyhow::anyhow!("PULL_REQUEST_URL_INVALID"))?;
    let number = value
        .get("number")
        .and_then(Value::as_u64)
        .ok_or_else(|| anyhow::anyhow!("PULL_REQUEST_NUMBER_MISSING"))?;
    if url != format!("https://github.com/{repository}/pull/{number}") {
        anyhow::bail!("PULL_REQUEST_IDENTITY_MISMATCH");
    }
    let base = value
        .pointer("/base/ref")
        .and_then(Value::as_str)
        .unwrap_or_default();
    let head = value
        .pointer("/head/ref")
        .and_then(Value::as_str)
        .unwrap_or_default();
    let head_repository = value
        .pointer("/head/repo/full_name")
        .and_then(Value::as_str)
        .unwrap_or_default();
    if base != expected_base
        || head != expected_head
        || !head_repository.eq_ignore_ascii_case(repository)
        || value.get("state").and_then(Value::as_str) != Some("open")
        || value.get("draft").and_then(Value::as_bool) != Some(true)
    {
        anyhow::bail!("PULL_REQUEST_SUBJECT_MISMATCH");
    }
    Ok(DraftPullRequest {
        url: url.into(),
        number,
        base: base.into(),
        head: head.into(),
    })
}

fn exactly_one_draft(
    pull_requests: Vec<DraftPullRequest>,
    _repository: &str,
    _base: &str,
    _head: &str,
) -> anyhow::Result<DraftPullRequest> {
    if pull_requests.len() != 1 {
        anyhow::bail!("PULL_REQUEST_CARDINALITY_INVALID");
    }
    Ok(pull_requests.into_iter().next().expect("one pull request"))
}

#[cfg(test)]
mod tests {
    use std::{net::SocketAddr, sync::Arc};

    use axum::{
        extract::State,
        http::StatusCode,
        routing::{get, post},
        Json, Router,
    };
    use chrono::Duration as ChronoDuration;
    use contract_core::{
        dev_private_key, sign_worker_envelope, verify_worker_envelope, IdentityRegistry,
    };
    use tokio::sync::{Barrier, Mutex};

    use super::*;

    fn registry_path() -> PathBuf {
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../../OpenContract/registry")
    }

    fn test_config(root: &Path, orchestrator_url: String) -> Config {
        Config {
            bind: "127.0.0.1:0".parse::<SocketAddr>().unwrap(),
            organization_id: Uuid::nil(),
            worker_id: "delivery-test-worker".into(),
            database_url: "postgres://unused-in-unit-tests".into(),
            role: crate::config::WorkerRole::Delivery,
            orchestrator_url,
            signing_key: dev_private_key("OpenAgents").unwrap().into(),
            identity_registry: registry_path(),
            managed_root: root.to_path_buf(),
            allowed_repositories: vec![],
            opencode_binary: "opencode".into(),
            sandbox_binary: "openagents-sandbox".into(),
            shell_binary: "/bin/sh".into(),
            git_sign_commits: true,
            git_remote: "origin".into(),
            git_binary: "git".into(),
            aws_binary: "aws".into(),
            connector_root: root.join("connectors"),
            delivery_lock_root: root.join("delivery-locks"),
            github_api_url: "https://api.github.com".into(),
            git_signing_key_b64: None,
            git_signing_public_key_b64: Some("invalid-test-public-key".into()),
            git_signing_fingerprint: Some("0".repeat(40)),
            git_timeout: Duration::from_secs(10),
            qa_timeout: Duration::from_secs(10),
            opencode_timeout: Duration::from_secs(10),
            request_timeout: Duration::from_secs(10),
            capacity: 1,
            llm_base_url: "http://invalid".into(),
            llm_api_key: "test".into(),
            llm_model: "test".into(),
            internal_service_key: "internal-test-key".into(),
            openbrain_url: "http://invalid".into(),
            skill_root: root.join("skills"),
        }
    }

    fn test_job() -> WorkerJob {
        let deadline = Utc::now() + ChronoDuration::minutes(5);
        WorkerJob {
            job_id: Uuid::new_v4(),
            organization_id: Uuid::nil(),
            correlation_id: Uuid::new_v4(),
            plan_id: Uuid::new_v4(),
            task_id: Uuid::new_v4(),
            ticket_id: Uuid::new_v4(),
            job_type: "engineering.delivery".into(),
            execution_mode: contract_core::ExecutionMode::Change,
            required_capabilities: vec!["engineering.delivery".into()],
            required_skills: vec![],
            acceptance_criteria: vec![],
            inputs: json!({}),
            priority: 100,
            deadline: Some(deadline),
            attempt: 1,
            max_attempts: 5,
            idempotency_key: "delivery:test".into(),
            lease_token: Uuid::new_v4(),
            leased_until: deadline,
        }
    }

    fn test_request(job: &WorkerJob, workspace_root: &Path, repository: &Path) -> DeliveryRequest {
        let commit_sha = "a".repeat(40);
        let diff_digest = "b".repeat(64);
        let candidate_digest = format!(
            "{:x}",
            Sha256::digest(format!("{commit_sha}:{diff_digest}").as_bytes())
        );
        let mut request = DeliveryRequest {
            delivery_id: Uuid::new_v4(),
            source_job_id: Uuid::new_v4(),
            plan_id: job.plan_id,
            task_id: job.task_id,
            ticket_id: job.ticket_id,
            repository_id: Uuid::new_v4(),
            base_ref: "main".into(),
            remote_branch: String::new(),
            commit_sha,
            diff_digest,
            candidate_digest,
            approval_subject_digest: String::new(),
            workspace: WorkspaceClaim {
                organization_id: job.organization_id,
                run_id: Uuid::new_v4(),
                repository_name: "Repo".into(),
                workspace_root: workspace_root.display().to_string(),
                repository_folder: repository.display().to_string(),
            },
            preflight: json!({"surfaces":[]}),
            provider: ProviderClaim {
                provider: "github".into(),
                external_repository_id: "acme/repo".into(),
                connector_ref: format!("local-connector://orgs/{}/github/app", job.organization_id),
            },
            requirements: DeliveryRequirements {
                canonical_managed_worktree: true,
                exact_head: true,
                clean_index_and_worktree: true,
                registered_remote_identity: true,
                draft_pull_request: true,
                idempotent_branch_and_pull_request: true,
                credentials_visible_only_inside_trusted_delivery_stage: true,
            },
            callback: CallbackClaim {
                method: "POST".into(),
                path: String::new(),
                required_producer: "OpenAgents".into(),
            },
        };
        request.remote_branch = format!("delivery/{}", request.source_job_id);
        request.callback.path = format!("/v1/deliveries/{}/provider", request.delivery_id);
        request.approval_subject_digest = approval_subject_digest(job.organization_id, &request);
        request
    }

    fn signed_request(
        job: &WorkerJob,
        request: DeliveryRequest,
    ) -> SignedWorkerEnvelope<DeliveryRequest> {
        let mut envelope = SignedWorkerEnvelope::new(
            WorkerMessageKind::Claim,
            job.organization_id,
            job.correlation_id,
            &job.idempotency_key,
            "OpenOrchestrator",
            "OpenAgents",
            job.deadline.unwrap(),
            request,
        );
        sign_worker_envelope(
            &mut envelope,
            "OpenOrchestrator",
            dev_private_key("OpenOrchestrator").unwrap(),
        )
        .unwrap();
        envelope
    }

    #[test]
    fn signed_claim_rejects_forgery_staleness_and_wrong_tenant() {
        let temp = tempfile::tempdir().unwrap();
        let job = test_job();
        let envelope = signed_request(&job, test_request(&job, temp.path(), temp.path()));
        let identities = IdentityRegistry::load_dir(&registry_path()).unwrap();
        verify_worker_envelope(&envelope, &identities, Utc::now()).unwrap();
        validate_claim_subject(&job, &envelope).unwrap();
        assert!(
            serde_json::from_value::<SignedWorkerEnvelope<DeliveryRequest>>(json!({
                "protocol_version":"openos.worker/v1",
                "kind":"claim"
            }))
            .is_err()
        );

        let mut forged: SignedWorkerEnvelope<DeliveryRequest> =
            serde_json::from_value(serde_json::to_value(&envelope).unwrap()).unwrap();
        forged.payload.base_ref = "release".into();
        assert!(verify_worker_envelope(&forged, &identities, Utc::now()).is_err());

        let mut stale = signed_request(&job, test_request(&job, temp.path(), temp.path()));
        stale.issued_at = Utc::now() - ChronoDuration::minutes(2);
        stale.expires_at = Utc::now() - ChronoDuration::minutes(1);
        sign_worker_envelope(
            &mut stale,
            "OpenOrchestrator",
            dev_private_key("OpenOrchestrator").unwrap(),
        )
        .unwrap();
        assert!(verify_worker_envelope(&stale, &identities, Utc::now()).is_err());

        let mut wrong_approval = signed_request(&job, test_request(&job, temp.path(), temp.path()));
        wrong_approval.payload.approval_subject_digest = "c".repeat(64);
        sign_worker_envelope(
            &mut wrong_approval,
            "OpenOrchestrator",
            dev_private_key("OpenOrchestrator").unwrap(),
        )
        .unwrap();
        verify_worker_envelope(&wrong_approval, &identities, Utc::now()).unwrap();
        assert!(validate_claim_subject(&job, &wrong_approval).is_err());

        let mut wrong_tenant_job = job.clone();
        wrong_tenant_job.organization_id = Uuid::new_v4();
        let wrong_tenant = signed_request(
            &wrong_tenant_job,
            test_request(&wrong_tenant_job, temp.path(), temp.path()),
        );
        verify_worker_envelope(&wrong_tenant, &identities, Utc::now()).unwrap();
        assert!(validate_claim_subject(&job, &wrong_tenant).is_err());
    }

    fn git(repository: &Path, args: &[&str]) -> String {
        String::from_utf8(git_output(repository, args, None))
            .unwrap()
            .trim()
            .into()
    }

    fn git_output(repository: &Path, args: &[&str], signing_home: Option<&Path>) -> Vec<u8> {
        let mut command = std::process::Command::new("git");
        command.arg("-C").arg(repository).args(args);
        if let Some(home) = signing_home {
            command.env("GNUPGHOME", home);
        }
        let output = command.output().unwrap();
        assert!(output.status.success(), "git {args:?} failed");
        output.stdout
    }

    struct GeneratedSigningIdentity {
        home: tempfile::TempDir,
        fingerprint: String,
        public_key_b64: String,
    }

    fn generated_signing_identity(root: &Path, label: &str) -> GeneratedSigningIdentity {
        let home = tempfile::Builder::new()
            .prefix("delivery-signing-test-")
            .tempdir_in(root)
            .unwrap();
        std::fs::set_permissions(home.path(), std::fs::Permissions::from_mode(0o700)).unwrap();
        let status = std::process::Command::new("gpg")
            .env_clear()
            .env("HOME", home.path())
            .env("GNUPGHOME", home.path())
            .env("PATH", "/usr/local/bin:/usr/bin:/bin")
            .args([
                "--batch",
                "--no-tty",
                "--pinentry-mode",
                "loopback",
                "--passphrase",
                "",
                "--quick-generate-key",
                label,
                "ed25519",
                "sign",
                "0",
            ])
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status()
            .unwrap();
        assert!(status.success(), "test OpenPGP key generation failed");
        let listing = std::process::Command::new("gpg")
            .env_clear()
            .env("HOME", home.path())
            .env("GNUPGHOME", home.path())
            .env("PATH", "/usr/local/bin:/usr/bin:/bin")
            .args(["--batch", "--with-colons", "--list-secret-keys"])
            .output()
            .unwrap();
        assert!(listing.status.success());
        let fingerprint = primary_fingerprints(&String::from_utf8(listing.stdout).unwrap())
            .into_iter()
            .next()
            .unwrap();
        let public_key = std::process::Command::new("gpg")
            .env_clear()
            .env("HOME", home.path())
            .env("GNUPGHOME", home.path())
            .env("PATH", "/usr/local/bin:/usr/bin:/bin")
            .args(["--batch", "--armor", "--export", &fingerprint])
            .output()
            .unwrap();
        assert!(public_key.status.success());
        GeneratedSigningIdentity {
            home,
            fingerprint,
            public_key_b64: STANDARD.encode(public_key.stdout),
        }
    }

    fn signed_candidate_repository(
        root: &Path,
        job: &WorkerJob,
        identity: &GeneratedSigningIdentity,
    ) -> (PathBuf, PathBuf, String, String) {
        let workspace_root = root
            .join(job.organization_id.to_string())
            .join(job.plan_id.to_string());
        let repository = workspace_root.join("Repo");
        std::fs::create_dir_all(&repository).unwrap();
        git(&repository, &["init", "--quiet"]);
        git(&repository, &["config", "user.name", "Test"]);
        git(&repository, &["config", "user.email", "test@example.com"]);
        let branch = format!(
            "openos/{}-repo-{}",
            job.ticket_id,
            &job.task_id.to_string()[..8]
        );
        git(&repository, &["checkout", "-qb", &branch]);
        std::fs::write(repository.join("README.md"), "base\n").unwrap();
        git(&repository, &["add", "README.md"]);
        git(&repository, &["commit", "--quiet", "-m", "base"]);
        std::fs::write(repository.join("README.md"), "candidate\n").unwrap();
        git(&repository, &["add", "README.md"]);
        git_output(
            &repository,
            &[
                "-c",
                "commit.gpgsign=true",
                "-c",
                "gpg.format=openpgp",
                "-c",
                "gpg.program=gpg",
                "-c",
                &format!("user.signingkey={}", identity.fingerprint),
                "commit",
                "--quiet",
                "-m",
                "candidate",
            ],
            Some(identity.home.path()),
        );
        git(
            &repository,
            &[
                "remote",
                "add",
                "origin",
                "https://github.com/acme/repo.git",
            ],
        );
        let head = git(&repository, &["rev-parse", "HEAD"]);
        let parent = git(&repository, &["rev-parse", "HEAD^"]);
        let diff = git_output(
            &repository,
            &["diff", "--no-ext-diff", "--unified=3", &parent, &head, "--"],
            None,
        );
        let diff_digest = format!("{:x}", Sha256::digest(diff));
        let control_root = workspace_root.join(".openos-control");
        std::fs::create_dir(&control_root).unwrap();
        let git_dir = control_root.join("Repo.git");
        std::fs::rename(repository.join(".git"), &git_dir).unwrap();
        let git_dir = std::fs::canonicalize(git_dir).unwrap();
        std::fs::write(
            repository.join(".git"),
            format!("gitdir: {}\n", git_dir.display()),
        )
        .unwrap();
        (workspace_root, repository, head, diff_digest)
    }

    fn candidate_repository(root: &Path, job: &WorkerJob) -> (PathBuf, PathBuf, String) {
        let workspace_root = root
            .join(job.organization_id.to_string())
            .join(job.plan_id.to_string());
        let repository = workspace_root.join("Repo");
        std::fs::create_dir_all(&repository).unwrap();
        git(&repository, &["init", "--quiet"]);
        git(&repository, &["config", "user.name", "Test"]);
        git(&repository, &["config", "user.email", "test@example.com"]);
        let branch = format!(
            "openos/{}-repo-{}",
            job.ticket_id,
            &job.task_id.to_string()[..8]
        );
        git(&repository, &["checkout", "-qb", &branch]);
        std::fs::write(repository.join("README.md"), "candidate\n").unwrap();
        git(&repository, &["add", "README.md"]);
        git(&repository, &["commit", "--quiet", "-m", "candidate"]);
        git(
            &repository,
            &[
                "remote",
                "add",
                "origin",
                "https://github.com/acme/repo.git",
            ],
        );
        let head = git(&repository, &["rev-parse", "HEAD"]);
        let control_root = workspace_root.join(".openos-control");
        std::fs::create_dir(&control_root).unwrap();
        let git_dir = control_root.join("Repo.git");
        std::fs::rename(repository.join(".git"), &git_dir).unwrap();
        let git_dir = std::fs::canonicalize(git_dir).unwrap();
        std::fs::write(
            repository.join(".git"),
            format!("gitdir: {}\n", git_dir.display()),
        )
        .unwrap();
        (workspace_root, repository, head)
    }

    #[tokio::test]
    async fn candidate_checks_reject_path_escape_wrong_head_branch_and_dirty_state() {
        let temp = tempfile::tempdir().unwrap();
        let job = test_job();
        let config = test_config(temp.path(), "http://invalid".into());
        let (workspace_root, repository, head) = candidate_repository(temp.path(), &job);

        let outside = tempfile::tempdir().unwrap();
        let escaped = test_request(&job, &workspace_root, outside.path());
        assert!(verify_candidate(&config, &job, &escaped)
            .await
            .unwrap_err()
            .to_string()
            .contains("CANONICAL_WORKTREE_IDENTITY_MISMATCH"));

        let wrong_head = test_request(&job, &workspace_root, &repository);
        assert!(verify_candidate(&config, &job, &wrong_head)
            .await
            .unwrap_err()
            .to_string()
            .contains("DELIVERY_HEAD_MISMATCH"));

        let mut wrong_branch = test_request(&job, &workspace_root, &repository);
        wrong_branch.commit_sha = head.clone();
        git(&repository, &["checkout", "-qb", "wrong-branch"]);
        assert!(verify_candidate(&config, &job, &wrong_branch)
            .await
            .unwrap_err()
            .to_string()
            .contains("DELIVERY_LOCAL_BRANCH_MISMATCH"));

        let expected_branch = format!(
            "openos/{}-repo-{}",
            job.ticket_id,
            &job.task_id.to_string()[..8]
        );
        git(&repository, &["checkout", &expected_branch]);
        std::fs::write(repository.join("dirty.txt"), "dirty").unwrap();
        let mut dirty = test_request(&job, &workspace_root, &repository);
        dirty.commit_sha = head;
        assert!(verify_candidate(&config, &job, &dirty)
            .await
            .unwrap_err()
            .to_string()
            .contains("DELIVERY_WORKTREE_DIRTY"));
    }

    #[tokio::test]
    async fn candidate_checks_reject_a_redirected_git_pointer() {
        let temp = tempfile::tempdir().unwrap();
        let job = test_job();
        let config = test_config(temp.path(), "http://invalid".into());
        let (workspace_root, repository, head) = candidate_repository(temp.path(), &job);
        let rogue = temp.path().join("rogue.git");
        std::fs::create_dir(&rogue).unwrap();
        std::fs::write(
            repository.join(".git"),
            format!("gitdir: {}\n", rogue.display()),
        )
        .unwrap();
        let mut request = test_request(&job, &workspace_root, &repository);
        request.commit_sha = head;

        let error = verify_candidate(&config, &job, &request).await.unwrap_err();
        assert!(error.to_string().contains("CANDIDATE_GIT_POINTER_INVALID"));
    }

    #[tokio::test]
    async fn candidate_signature_requires_the_configured_public_key_and_fingerprint() {
        if std::process::Command::new("gpg")
            .arg("--version")
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status()
            .is_err()
        {
            return;
        }
        let temp = tempfile::tempdir().unwrap();
        let signer = generated_signing_identity(temp.path(), "OpenAgents candidate signer");
        let wrong = generated_signing_identity(temp.path(), "Untrusted candidate signer");
        let job = test_job();
        let (workspace_root, repository, head, diff_digest) =
            signed_candidate_repository(temp.path(), &job, &signer);
        let mut request = test_request(&job, &workspace_root, &repository);
        request.commit_sha = head;
        request.diff_digest = diff_digest;

        let mut trusted = test_config(temp.path(), "http://invalid".into());
        trusted.git_signing_public_key_b64 = Some(signer.public_key_b64.clone());
        trusted.git_signing_fingerprint = Some(signer.fingerprint.clone());
        verify_candidate(&trusted, &job, &request).await.unwrap();

        let mut missing = trusted.clone();
        missing.git_signing_public_key_b64 = None;
        assert!(verify_candidate(&missing, &job, &request)
            .await
            .unwrap_err()
            .to_string()
            .contains("DELIVERY_GIT_TRUST_REQUIRED"));

        let mut untrusted = trusted;
        untrusted.git_signing_public_key_b64 = Some(wrong.public_key_b64);
        untrusted.git_signing_fingerprint = Some(wrong.fingerprint);
        assert!(verify_candidate(&untrusted, &job, &request)
            .await
            .unwrap_err()
            .to_string()
            .contains("DELIVERY_COMMIT_SIGNATURE_INVALID"));
    }

    #[test]
    fn remote_and_connector_identity_are_strict_and_secret_free() {
        let org = Uuid::new_v4();
        assert_eq!(
            github_remote_identity("https://github.com/acme/repo.git"),
            Some("acme/repo".into())
        );
        assert_eq!(
            github_remote_identity("https://evil.example/acme/repo.git"),
            None
        );
        assert!(
            tenant_connector_path(&format!("local-connector://orgs/{org}/github/app"), org).is_ok()
        );
        assert!(tenant_connector_path(
            "local-connector://orgs/00000000-0000-0000-0000-000000000000/github/app",
            org
        )
        .is_err());
        assert!(
            tenant_connector_path(&format!("local-connector://orgs/{org}/../secret"), org).is_err()
        );
        let secret =
            parse_connector_secret(br#"{"token":"connector-test-value-with-length"}"#).unwrap();
        let auth = GitAuth::new(&secret).unwrap();
        let script = std::fs::read_to_string(&auth.askpass).unwrap();
        assert!(!script.contains(&secret.token));
        assert!(!script.contains("connector-test-value"));
    }

    #[test]
    fn existing_remote_branch_is_reused_only_for_the_exact_commit() {
        let commit = "a".repeat(40);
        assert_eq!(
            remote_branch_action(Some(&commit), &commit).unwrap(),
            RemoteBranchAction::Reuse
        );
        assert_eq!(
            remote_branch_action(None, &commit).unwrap(),
            RemoteBranchAction::Push
        );
        assert!(remote_branch_action(Some(&"b".repeat(40)), &commit).is_err());
    }

    fn slow_executable(root: &Path) -> PathBuf {
        let path = root.join("slow-command.sh");
        std::fs::write(&path, "#!/bin/sh\nsleep 2\n").unwrap();
        let mut permissions = std::fs::metadata(&path).unwrap().permissions();
        permissions.set_mode(0o700);
        std::fs::set_permissions(&path, permissions).unwrap();
        path
    }

    #[tokio::test]
    async fn external_connector_and_remote_lookup_are_bounded() {
        let temp = tempfile::tempdir().unwrap();
        let binary = slow_executable(temp.path());
        let organization_id = Uuid::new_v4();
        let resolver = AwsConnectorResolver {
            binary: binary.clone(),
            timeout: Duration::from_millis(20),
        };
        let connector_error = resolver
            .resolve(
                &format!("aws-sm://eu-west-1/orgs/{organization_id}/github/app"),
                organization_id,
                "github",
            )
            .await
            .err()
            .expect("slow connector must time out");
        assert_eq!(connector_error.to_string(), "CONNECTOR_RESOLUTION_TIMEOUT");

        let mut config = test_config(temp.path(), "http://invalid".into());
        config.git_binary = binary;
        config.git_timeout = Duration::from_millis(20);
        let secret = ConnectorSecret {
            token: "fake-connector-value-with-length".into(),
            username: "x-access-token".into(),
        };
        let auth = GitAuth::new(&secret).unwrap();
        let remote_error = remote_branch_head(
            &config,
            temp.path(),
            "https://github.com/acme/repo.git",
            "delivery/test",
            &auth,
        )
        .await
        .unwrap_err();
        assert_eq!(remote_error.to_string(), "REMOTE_BRANCH_LOOKUP_TIMEOUT");
    }

    #[derive(Default)]
    struct GithubState {
        pull_requests: Vec<Value>,
        creates: usize,
        barrier: Option<Arc<Barrier>>,
    }

    fn pull_request_value() -> Value {
        json!({
            "html_url":"https://github.com/acme/repo/pull/7",
            "number":7,
            "state":"open",
            "draft":true,
            "base":{"ref":"main"},
            "head":{"ref":"delivery/source","repo":{"full_name":"acme/repo"}}
        })
    }

    async fn list_prs(State(state): State<Arc<Mutex<GithubState>>>) -> Json<Vec<Value>> {
        let barrier = state.lock().await.barrier.clone();
        if let Some(barrier) = barrier {
            barrier.wait().await;
        }
        Json(state.lock().await.pull_requests.clone())
    }

    async fn create_pr(State(state): State<Arc<Mutex<GithubState>>>) -> (StatusCode, Json<Value>) {
        let mut state = state.lock().await;
        if state.pull_requests.is_empty() {
            state.creates += 1;
            let value = pull_request_value();
            state.pull_requests.push(value.clone());
            (StatusCode::CREATED, Json(value))
        } else {
            (
                StatusCode::UNPROCESSABLE_ENTITY,
                Json(json!({"message":"exists"})),
            )
        }
    }

    async fn github_server(state: Arc<Mutex<GithubState>>) -> String {
        let app = Router::new()
            .route("/repos/acme/repo/pulls", get(list_prs).post(create_pr))
            .with_state(state);
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = listener.local_addr().unwrap();
        tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
        format!("http://{address}")
    }

    #[tokio::test]
    async fn concurrent_retry_and_existing_pr_reuse_exactly_one_draft() {
        let state = Arc::new(Mutex::new(GithubState {
            barrier: Some(Arc::new(Barrier::new(2))),
            ..Default::default()
        }));
        let adapter = Arc::new(GithubAdapter {
            http: Client::new(),
            api_url: github_server(state.clone()).await,
        });
        let secret = Arc::new(ConnectorSecret {
            token: "not-a-real-token".into(),
            username: "x-access-token".into(),
        });
        let run = Uuid::new_v4();
        let ticket = Uuid::new_v4();
        let first = {
            let adapter = adapter.clone();
            let secret = secret.clone();
            tokio::spawn(async move {
                adapter
                    .ensure_draft_pull_request(
                        "acme/repo",
                        "main",
                        "delivery/source",
                        ticket,
                        run,
                        &secret,
                    )
                    .await
            })
        };
        let second = {
            let adapter = adapter.clone();
            let secret = secret.clone();
            tokio::spawn(async move {
                adapter
                    .ensure_draft_pull_request(
                        "acme/repo",
                        "main",
                        "delivery/source",
                        ticket,
                        run,
                        &secret,
                    )
                    .await
            })
        };
        assert_eq!(first.await.unwrap().unwrap().number, 7);
        assert_eq!(second.await.unwrap().unwrap().number, 7);
        let state = state.lock().await;
        assert_eq!(state.creates, 1);
        assert_eq!(state.pull_requests.len(), 1);
    }

    #[derive(Default)]
    struct CallbackState {
        attempts: usize,
        idempotency_keys: Vec<String>,
        leaked: bool,
    }

    async fn failing_callback(
        State(state): State<Arc<Mutex<CallbackState>>>,
        Json(value): Json<Value>,
    ) -> (StatusCode, Json<Value>) {
        let mut state = state.lock().await;
        state.attempts += 1;
        let serialized = value.to_string().to_ascii_lowercase();
        state.leaked |= ["connector_ref", "authorization", "access_token"]
            .iter()
            .any(|field| serialized.contains(field));
        state.idempotency_keys.push(
            value
                .get("idempotency_key")
                .and_then(Value::as_str)
                .unwrap_or_default()
                .into(),
        );
        (
            StatusCode::SERVICE_UNAVAILABLE,
            Json(json!({"error":"temporary"})),
        )
    }

    #[tokio::test]
    async fn callback_failure_retries_one_signed_idempotency_subject_without_credentials() {
        let state = Arc::new(Mutex::new(CallbackState::default()));
        let app = Router::new()
            .route("/v1/deliveries/{id}/provider", post(failing_callback))
            .with_state(state.clone());
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = listener.local_addr().unwrap();
        tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });

        let temp = tempfile::tempdir().unwrap();
        let config = test_config(temp.path(), format!("http://{address}"));
        let identities = IdentityRegistry::load_dir(&registry_path()).unwrap();
        let client = ControlPlaneClient::new(Client::new(), &config, identities);
        let job = test_job();
        let request = test_request(&job, temp.path(), temp.path());
        let envelope = signed_request(&job, request);
        let error = submit_callback(
            &client,
            &job,
            &envelope,
            json!({"provider":"github","draft":true}),
        )
        .await
        .unwrap_err();
        assert!(error.to_string().contains("PROVIDER_CALLBACK_FAILED"));
        let state = state.lock().await;
        assert_eq!(state.attempts, CALLBACK_ATTEMPTS);
        assert_eq!(
            state
                .idempotency_keys
                .iter()
                .collect::<std::collections::HashSet<_>>()
                .len(),
            1
        );
        assert!(!state.leaked);
    }

    #[derive(Default)]
    struct CommittedCallbackState {
        attempts: usize,
        envelope: Option<Value>,
    }

    async fn commit_then_lose_response(
        State(state): State<Arc<Mutex<CommittedCallbackState>>>,
        Json(value): Json<Value>,
    ) -> (StatusCode, Json<Value>) {
        let mut state = state.lock().await;
        state.attempts += 1;
        state.envelope = Some(value);
        (
            StatusCode::SERVICE_UNAVAILABLE,
            Json(json!({"error":"response_lost"})),
        )
    }

    async fn read_committed_callback(
        State(state): State<Arc<Mutex<CommittedCallbackState>>>,
    ) -> Json<Value> {
        Json(json!({
            "delivery": {
                "status":"pr_open",
                "provider_result":state.lock().await.envelope,
            },
            "approvals":[],
            "dependent_jobs":[],
        }))
    }

    #[tokio::test]
    async fn callback_response_loss_confirms_the_exact_persisted_signed_envelope() {
        let state = Arc::new(Mutex::new(CommittedCallbackState::default()));
        let app = Router::new()
            .route(
                "/v1/deliveries/{id}/provider",
                post(commit_then_lose_response),
            )
            .route("/v1/deliveries/{id}", get(read_committed_callback))
            .with_state(state.clone());
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = listener.local_addr().unwrap();
        tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });

        let temp = tempfile::tempdir().unwrap();
        let config = test_config(temp.path(), format!("http://{address}"));
        let identities = IdentityRegistry::load_dir(&registry_path()).unwrap();
        let client = ControlPlaneClient::new(Client::new(), &config, identities);
        let job = test_job();
        let request = test_request(&job, temp.path(), temp.path());
        let envelope = signed_request(&job, request);
        submit_callback(
            &client,
            &job,
            &envelope,
            json!({"provider":"github","draft":true}),
        )
        .await
        .unwrap();
        assert_eq!(state.lock().await.attempts, 1);
    }
}
