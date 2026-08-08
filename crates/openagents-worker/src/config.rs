use std::{net::SocketAddr, path::PathBuf, time::Duration};

use anyhow::Context;
use uuid::Uuid;

#[derive(Clone)]
pub struct Config {
    pub bind: SocketAddr,
    pub organization_id: Uuid,
    pub worker_id: String,
    pub orchestrator_url: String,
    pub signing_key: String,
    pub identity_registry: PathBuf,
    pub managed_root: PathBuf,
    pub allowed_repositories: Vec<PathBuf>,
    pub opencode_binary: PathBuf,
    pub shell_binary: PathBuf,
    pub git_sign_commits: bool,
    pub git_remote: String,
    pub git_provider_binary: PathBuf,
    pub git_signing_key_b64: Option<String>,
    pub git_timeout: Duration,
    pub qa_timeout: Duration,
    pub opencode_timeout: Duration,
    pub request_timeout: Duration,
    pub capacity: u32,
    pub llm_base_url: String,
    pub llm_api_key: String,
    pub llm_model: String,
    pub internal_service_key: String,
    pub openbrain_url: String,
    pub skill_root: PathBuf,
}

impl Config {
    pub fn from_env() -> anyhow::Result<Self> {
        let port = parse("PORT", 8080u16)?;
        let managed_root = PathBuf::from(required("OPENOS_MANAGED_WORKTREE_ROOT")?);
        Ok(Self {
            bind: format!("0.0.0.0:{port}").parse()?,
            organization_id: required("OPENOS_ORGANIZATION_ID")?.parse()?,
            worker_id: std::env::var("OPENAGENTS_WORKER_ID")
                .unwrap_or_else(|_| "openagents-rust-1".into()),
            orchestrator_url: required("OPENORCHESTRATOR_API_URL")?
                .trim_end_matches('/')
                .to_string(),
            signing_key: required("OPENCONTRACT_SIGNING_KEY")?,
            identity_registry: PathBuf::from(required("OPENCONTRACT_IDENTITY_REGISTRY")?),
            managed_root,
            allowed_repositories: required("OPENOS_ALLOWED_REPOSITORIES")?
                .split(':')
                .filter(|value| !value.is_empty())
                .map(PathBuf::from)
                .collect(),
            opencode_binary: PathBuf::from(
                std::env::var("OPENOS_OPENCODE_PATH")
                    .unwrap_or_else(|_| "/usr/local/bin/opencode".into()),
            ),
            shell_binary: PathBuf::from(
                std::env::var("OPENAGENTS_SHELL")
                    .or_else(|_| std::env::var("SHELL"))
                    .unwrap_or_else(|_| "/bin/bash".into()),
            ),
            git_sign_commits: parse("OPENAGENTS_GIT_SIGN_COMMITS", true)?,
            git_remote: std::env::var("OPENAGENTS_GIT_REMOTE").unwrap_or_else(|_| "origin".into()),
            git_provider_binary: PathBuf::from(
                std::env::var("OPENAGENTS_GIT_PROVIDER_PATH").unwrap_or_else(|_| "gh".into()),
            ),
            git_signing_key_b64: std::env::var("OPENAGENTS_GIT_SIGNING_KEY_B64")
                .ok()
                .filter(|value| !value.trim().is_empty()),
            git_timeout: Duration::from_secs(parse("OPENAGENTS_GIT_TIMEOUT_SECONDS", 300u64)?),
            qa_timeout: Duration::from_secs(parse("OPENAGENTS_QA_TIMEOUT_SECONDS", 1800u64)?),
            opencode_timeout: Duration::from_secs(parse(
                "OPENOS_OPENCODE_TIMEOUT_SECONDS",
                3600u64,
            )?),
            request_timeout: Duration::from_secs(parse("OPENOS_REQUEST_TIMEOUT_SECONDS", 30u64)?),
            capacity: parse("OPENAGENTS_WORKER_CAPACITY", 2u32)?,
            llm_base_url: required("LLM_BASE_URL")?.trim_end_matches('/').to_string(),
            llm_api_key: required("LLM_API_KEY")?,
            llm_model: required("LLM_MODEL")?,
            internal_service_key: required("INTERNAL_SERVICE_KEY")?,
            openbrain_url: required("OPENBRAIN_URL")?.trim_end_matches('/').to_string(),
            skill_root: PathBuf::from(
                std::env::var("OPENOS_SKILL_ROOT")
                    .unwrap_or_else(|_| "/opt/openagents/skills".into()),
            ),
        })
    }
}

fn required(name: &str) -> anyhow::Result<String> {
    std::env::var(name).with_context(|| format!("{name} is required"))
}

fn parse<T>(name: &str, default: T) -> anyhow::Result<T>
where
    T: std::str::FromStr,
    T::Err: std::fmt::Display,
{
    match std::env::var(name) {
        Ok(value) => value
            .parse()
            .map_err(|error| anyhow::anyhow!("{name}: {error}")),
        Err(_) => Ok(default),
    }
}
