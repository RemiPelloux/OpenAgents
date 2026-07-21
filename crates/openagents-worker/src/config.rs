use std::{net::SocketAddr, path::PathBuf, time::Duration};

use anyhow::Context;
use uuid::Uuid;

#[derive(Debug, Clone)]
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
    pub opencode_timeout: Duration,
    pub request_timeout: Duration,
    pub capacity: u32,
    pub llm_base_url: String,
    pub llm_api_key: String,
    pub llm_model: String,
    pub internal_service_key: String,
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
