use std::{net::SocketAddr, path::PathBuf, sync::Arc, time::Duration};

use anyhow::Context;
use uuid::Uuid;
use zeroize::Zeroizing;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum WorkerRole {
    Coding,
    Delivery,
}

impl WorkerRole {
    pub fn parse(value: &str) -> anyhow::Result<Self> {
        match value.trim().to_ascii_lowercase().as_str() {
            "coding" => Ok(Self::Coding),
            "delivery" => Ok(Self::Delivery),
            _ => anyhow::bail!("OPENAGENTS_WORKER_ROLE must be coding or delivery"),
        }
    }

    pub fn as_str(self) -> &'static str {
        match self {
            Self::Coding => "coding",
            Self::Delivery => "delivery",
        }
    }

    pub fn job_types(self) -> &'static [&'static str] {
        match self {
            Self::Coding => &[
                "engineering.opencode",
                "engineering.inspect",
                "agent.skill_author",
            ],
            Self::Delivery => &["engineering.delivery"],
        }
    }

    pub fn permits(self, job_type: &str) -> bool {
        self.job_types().contains(&job_type)
    }
}

#[derive(Clone)]
pub struct Config {
    pub bind: SocketAddr,
    pub organization_id: Uuid,
    pub worker_id: String,
    pub database_url: String,
    pub role: WorkerRole,
    pub orchestrator_url: String,
    pub signing_key: String,
    pub identity_registry: PathBuf,
    pub managed_root: PathBuf,
    pub allowed_repositories: Vec<PathBuf>,
    pub opencode_binary: PathBuf,
    pub sandbox_binary: PathBuf,
    pub shell_binary: PathBuf,
    pub git_sign_commits: bool,
    pub git_remote: String,
    pub git_binary: PathBuf,
    pub aws_binary: PathBuf,
    pub connector_root: PathBuf,
    pub delivery_lock_root: PathBuf,
    pub github_api_url: String,
    pub git_signing_key_b64: Option<Arc<Zeroizing<String>>>,
    pub git_signing_public_key_b64: Option<String>,
    pub git_signing_fingerprint: Option<String>,
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
        let role = WorkerRole::parse(
            &std::env::var("OPENAGENTS_WORKER_ROLE").unwrap_or_else(|_| "coding".into()),
        )?;
        let signing = load_git_signing_configuration(role)?;
        Ok(Self {
            bind: format!("0.0.0.0:{port}").parse()?,
            organization_id: required("OPENOS_ORGANIZATION_ID")?.parse()?,
            worker_id: std::env::var("OPENAGENTS_WORKER_ID")
                .unwrap_or_else(|_| "openagents-rust-1".into()),
            database_url: required("OPENAGENTS_DATABASE_URL")?,
            role,
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
            sandbox_binary: PathBuf::from(
                std::env::var("OPENAGENTS_SANDBOX_PATH")
                    .unwrap_or_else(|_| "/usr/local/bin/openagents-sandbox".into()),
            ),
            shell_binary: PathBuf::from(
                std::env::var("OPENAGENTS_SHELL")
                    .or_else(|_| std::env::var("SHELL"))
                    .unwrap_or_else(|_| "/bin/bash".into()),
            ),
            git_sign_commits: parse("OPENAGENTS_GIT_SIGN_COMMITS", true)?,
            git_remote: std::env::var("OPENAGENTS_GIT_REMOTE").unwrap_or_else(|_| "origin".into()),
            git_binary: PathBuf::from(
                std::env::var("OPENAGENTS_GIT_PATH").unwrap_or_else(|_| "git".into()),
            ),
            aws_binary: PathBuf::from(
                std::env::var("OPENAGENTS_AWS_PATH").unwrap_or_else(|_| "aws".into()),
            ),
            connector_root: PathBuf::from(
                std::env::var("OPENAGENTS_CONNECTOR_ROOT")
                    .unwrap_or_else(|_| "/run/openos/connectors".into()),
            ),
            delivery_lock_root: PathBuf::from(
                std::env::var("OPENAGENTS_DELIVERY_LOCK_ROOT")
                    .unwrap_or_else(|_| "/run/openos/delivery-locks".into()),
            ),
            github_api_url: std::env::var("OPENAGENTS_GITHUB_API_URL")
                .unwrap_or_else(|_| "https://api.github.com".into())
                .trim_end_matches('/')
                .to_string(),
            git_signing_key_b64: signing.private_key_b64,
            git_signing_public_key_b64: signing.public_key_b64,
            git_signing_fingerprint: signing.fingerprint,
            git_timeout: Duration::from_secs(parse("OPENAGENTS_GIT_TIMEOUT_SECONDS", 300u64)?),
            qa_timeout: Duration::from_secs(parse("OPENAGENTS_QA_TIMEOUT_SECONDS", 1800u64)?),
            opencode_timeout: Duration::from_secs(parse(
                "OPENOS_OPENCODE_TIMEOUT_SECONDS",
                3600u64,
            )?),
            request_timeout: Duration::from_secs(parse("OPENOS_REQUEST_TIMEOUT_SECONDS", 30u64)?),
            capacity: parse("OPENAGENTS_WORKER_CAPACITY", 2u32)?,
            llm_base_url: required_for_role("LLM_BASE_URL", role, WorkerRole::Coding)?
                .trim_end_matches('/')
                .to_string(),
            llm_api_key: required_for_role("LLM_API_KEY", role, WorkerRole::Coding)?,
            llm_model: required_for_role("LLM_MODEL", role, WorkerRole::Coding)?,
            internal_service_key: required("INTERNAL_SERVICE_KEY")?,
            openbrain_url: required_for_role("OPENBRAIN_URL", role, WorkerRole::Coding)?
                .trim_end_matches('/')
                .to_string(),
            skill_root: PathBuf::from(
                std::env::var("OPENOS_SKILL_ROOT")
                    .unwrap_or_else(|_| "/opt/openagents/skills".into()),
            ),
        })
    }
}

struct GitSigningConfiguration {
    private_key_b64: Option<Arc<Zeroizing<String>>>,
    public_key_b64: Option<String>,
    fingerprint: Option<String>,
}

fn load_git_signing_configuration(role: WorkerRole) -> anyhow::Result<GitSigningConfiguration> {
    let private_key_b64 = take_secret_env("OPENAGENTS_GIT_SIGNING_KEY_B64")?;
    let public_key_b64 = optional_env("OPENAGENTS_GIT_SIGNING_PUBLIC_KEY_B64")?;
    let fingerprint = optional_env("OPENAGENTS_GIT_SIGNING_FINGERPRINT")?;
    validate_git_signing_configuration(role, private_key_b64, public_key_b64, fingerprint)
}

fn validate_git_signing_configuration(
    role: WorkerRole,
    private_key_b64: Option<Arc<Zeroizing<String>>>,
    public_key_b64: Option<String>,
    fingerprint: Option<String>,
) -> anyhow::Result<GitSigningConfiguration> {
    match role {
        WorkerRole::Coding => {
            if public_key_b64.is_some() || fingerprint.is_some() {
                anyhow::bail!("DELIVERY_GIT_TRUST_MUST_NOT_BE_CONFIGURED_FOR_CODING");
            }
        }
        WorkerRole::Delivery => {
            if private_key_b64.is_some() {
                anyhow::bail!("PRIVATE_GIT_SIGNING_KEY_FORBIDDEN_FOR_DELIVERY");
            }
            if public_key_b64.is_none() || fingerprint.is_none() {
                anyhow::bail!("DELIVERY_GIT_TRUST_REQUIRED");
            }
        }
    }
    Ok(GitSigningConfiguration {
        private_key_b64,
        public_key_b64,
        fingerprint,
    })
}

fn take_secret_env(name: &str) -> anyhow::Result<Option<Arc<Zeroizing<String>>>> {
    let value = std::env::var(name);
    std::env::remove_var(name);
    match value {
        Ok(value) if !value.trim().is_empty() => Ok(Some(Arc::new(Zeroizing::new(value)))),
        Ok(_) | Err(std::env::VarError::NotPresent) => Ok(None),
        Err(std::env::VarError::NotUnicode(_)) => anyhow::bail!("{name} must be valid UTF-8"),
    }
}

fn optional_env(name: &str) -> anyhow::Result<Option<String>> {
    match std::env::var(name) {
        Ok(value) if !value.trim().is_empty() => Ok(Some(value)),
        Ok(_) | Err(std::env::VarError::NotPresent) => Ok(None),
        Err(std::env::VarError::NotUnicode(_)) => anyhow::bail!("{name} must be valid UTF-8"),
    }
}

fn required_for_role(
    name: &str,
    role: WorkerRole,
    required_role: WorkerRole,
) -> anyhow::Result<String> {
    if role == required_role {
        required(name)
    } else {
        Ok(std::env::var(name).unwrap_or_default())
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn worker_roles_have_disjoint_job_types() {
        assert!(WorkerRole::Coding.permits("engineering.opencode"));
        assert!(WorkerRole::Coding.permits("engineering.inspect"));
        assert!(WorkerRole::Coding.permits("agent.skill_author"));
        assert!(!WorkerRole::Coding.permits("engineering.delivery"));
        assert!(WorkerRole::Delivery.permits("engineering.delivery"));
        assert!(!WorkerRole::Delivery.permits("engineering.opencode"));
        assert!(WorkerRole::parse("combined").is_err());
    }

    #[test]
    fn signing_material_is_role_separated() {
        let private = || Some(Arc::new(Zeroizing::new("private-key".to_string())));
        assert!(
            validate_git_signing_configuration(WorkerRole::Coding, private(), None, None,).is_ok()
        );
        assert!(validate_git_signing_configuration(
            WorkerRole::Delivery,
            private(),
            Some("public-key".into()),
            Some("fingerprint".into()),
        )
        .err()
        .unwrap()
        .to_string()
        .contains("PRIVATE_GIT_SIGNING_KEY_FORBIDDEN_FOR_DELIVERY"));
        assert!(
            validate_git_signing_configuration(WorkerRole::Delivery, None, None, None)
                .err()
                .unwrap()
                .to_string()
                .contains("DELIVERY_GIT_TRUST_REQUIRED")
        );
        assert!(validate_git_signing_configuration(
            WorkerRole::Coding,
            None,
            Some("public-key".into()),
            Some("fingerprint".into()),
        )
        .is_err());
    }

    #[test]
    fn private_signing_source_is_removed_after_ingestion() {
        const NAME: &str = "OPENAGENTS_TEST_GIT_SIGNING_PRIVATE_KEY";
        std::env::set_var(NAME, "private-key-material");
        let value = take_secret_env(NAME).unwrap().unwrap();
        assert_eq!(value.as_str(), "private-key-material");
        assert!(std::env::var_os(NAME).is_none());
    }
}
