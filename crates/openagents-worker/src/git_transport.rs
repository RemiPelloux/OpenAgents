use std::{
    os::unix::fs::PermissionsExt,
    path::{Component, Path, PathBuf},
    process::Stdio,
};

use anyhow::Context;
use serde_json::Value;
use tokio::{fs, process::Command, time::timeout};
use uuid::Uuid;
use zeroize::Zeroizing;

use crate::config::Config;

const MAX_CONNECTOR_BYTES: u64 = 16 * 1024;

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct CanonicalGitRemote {
    pub(crate) identity: String,
    pub(crate) url: String,
}

pub(crate) fn canonical_git_remote(value: &str) -> anyhow::Result<CanonicalGitRemote> {
    let parsed = url::Url::parse(value.trim()).context("GIT_REMOTE_INVALID")?;
    if parsed.scheme() != "https"
        || !parsed.username().is_empty()
        || parsed.password().is_some()
        || parsed.query().is_some()
        || parsed.fragment().is_some()
        || parsed.host_str().is_none()
        || parsed.port_or_known_default() != Some(443)
    {
        anyhow::bail!("GIT_REMOTE_CREDENTIALS_OR_SCHEME_REJECTED");
    }
    let host = parsed.host_str().unwrap().to_ascii_lowercase();
    let path = parsed.path().trim_matches('/').trim_end_matches(".git");
    if path.is_empty()
        || path
            .split('/')
            .any(|part| part.is_empty() || part == "." || part == "..")
    {
        anyhow::bail!("GIT_REMOTE_PATH_INVALID");
    }
    Ok(CanonicalGitRemote {
        identity: format!("{host}/{path}"),
        url: format!("https://{host}/{path}.git"),
    })
}

pub(crate) struct ConnectorSecret {
    token: Zeroizing<String>,
    username: Zeroizing<String>,
}

impl ConnectorSecret {
    pub(crate) fn token(&self) -> &str {
        self.token.as_str()
    }

    #[cfg(test)]
    pub(crate) fn test_value(token: &str, username: &str) -> Self {
        Self {
            token: Zeroizing::new(token.to_owned()),
            username: Zeroizing::new(username.to_owned()),
        }
    }
}

pub(crate) async fn resolve_connector(
    config: &Config,
    reference: &str,
    organization_id: Uuid,
    provider: &str,
) -> anyhow::Result<ConnectorSecret> {
    if provider.is_empty()
        || !provider
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'_' | b'-'))
    {
        anyhow::bail!("CONNECTOR_PROVIDER_INVALID");
    }
    if reference.starts_with("local-connector://") {
        return resolve_local_connector(
            &config.connector_root,
            reference,
            organization_id,
            provider,
        )
        .await;
    }
    if reference.starts_with("aws-sm://") || reference.starts_with("aws-secrets-manager://") {
        return resolve_aws_connector(config, reference, organization_id, provider).await;
    }
    anyhow::bail!("CONNECTOR_RESOLVER_UNAVAILABLE")
}

async fn resolve_local_connector(
    connector_root: &Path,
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
    let root = fs::canonicalize(connector_root)
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
    parse_connector_secret(&fs::read(file).await?)
}

async fn resolve_aws_connector(
    config: &Config,
    reference: &str,
    organization_id: Uuid,
    provider: &str,
) -> anyhow::Result<ConnectorSecret> {
    let (region, secret_id) = aws_connector_location(reference, organization_id, provider)?;
    let mut command = Command::new(&config.aws_binary);
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
    let output = timeout(config.request_timeout, command.kill_on_drop(true).output())
        .await
        .map_err(|_| anyhow::anyhow!("CONNECTOR_RESOLUTION_TIMEOUT"))??;
    if !output.status.success() || output.stdout.len() as u64 > MAX_CONNECTOR_BYTES {
        anyhow::bail!("CONNECTOR_RESOLUTION_FAILED");
    }
    parse_connector_secret(&output.stdout)
}

fn aws_connector_location<'a>(
    reference: &'a str,
    organization_id: Uuid,
    provider: &str,
) -> anyhow::Result<(&'a str, &'a str)> {
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
    Ok((region, secret_id))
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
            (token.to_owned(), username.to_owned())
        }
        Err(_) => (text.to_owned(), "x-access-token".to_owned()),
    };
    if token.is_empty()
        || token.len() > 8192
        || token.chars().any(char::is_whitespace)
        || username.is_empty()
        || username.chars().any(|value| value.is_control())
    {
        anyhow::bail!("CONNECTOR_SECRET_INVALID");
    }
    Ok(ConnectorSecret {
        token: Zeroizing::new(token),
        username: Zeroizing::new(username),
    })
}

pub(crate) struct GitAuth {
    _directory: tempfile::TempDir,
    askpass: PathBuf,
    token: Zeroizing<String>,
    username: Zeroizing<String>,
}

impl GitAuth {
    pub(crate) fn new(secret: &ConnectorSecret) -> anyhow::Result<Self> {
        let directory = tempfile::Builder::new()
            .prefix("openagents-git-auth-")
            .tempdir()?;
        std::fs::set_permissions(directory.path(), std::fs::Permissions::from_mode(0o700))?;
        let askpass = directory.path().join("askpass.sh");
        std::fs::write(
            &askpass,
            b"#!/bin/sh\ncase \"$1\" in\n  *Username*) printf '%s' \"$OPENAGENTS_CONNECTOR_USERNAME\" ;;\n  *) printf '%s' \"$OPENAGENTS_CONNECTOR_TOKEN\" ;;\nesac\n",
        )?;
        std::fs::set_permissions(&askpass, std::fs::Permissions::from_mode(0o700))?;
        Ok(Self {
            _directory: directory,
            askpass,
            token: Zeroizing::new(secret.token.as_str().to_owned()),
            username: Zeroizing::new(secret.username.as_str().to_owned()),
        })
    }

    pub(crate) fn apply(&self, command: &mut Command) {
        command
            .env("GIT_ASKPASS", &self.askpass)
            .env("OPENAGENTS_CONNECTOR_TOKEN", self.token.as_str())
            .env("OPENAGENTS_CONNECTOR_USERNAME", self.username.as_str());
    }

    #[cfg(test)]
    fn askpass_path(&self) -> &Path {
        &self.askpass
    }
}

pub(crate) fn git_transport_command(git: &Path) -> Command {
    let mut command = Command::new(git);
    command
        .env_clear()
        .env("PATH", "/usr/local/cargo/bin:/usr/local/bin:/usr/bin:/bin")
        .env("HOME", "/nonexistent")
        .env("LANG", "C")
        .env("LC_ALL", "C")
        .env("GIT_CONFIG_NOSYSTEM", "1")
        .env("GIT_CONFIG_GLOBAL", "/dev/null")
        .env("GIT_ATTR_NOSYSTEM", "1")
        .env("GIT_TERMINAL_PROMPT", "0")
        .env("GIT_ASKPASS", "/bin/false")
        .env("SSH_ASKPASS", "/bin/false")
        .env("GIT_EXTERNAL_DIFF", "/bin/false")
        .stdin(Stdio::null())
        .args([
            "-c",
            "credential.helper=",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "core.fsmonitor=false",
            "-c",
            "diff.external=",
        ]);
    command
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn canonical_remotes_are_credential_free_for_distinct_hosts() {
        for (input, expected) in [
            (
                "https://EXAMPLE.test/org/repo",
                "https://example.test/org/repo.git",
            ),
            (
                "https://code.example.net/team/service.git",
                "https://code.example.net/team/service.git",
            ),
        ] {
            assert_eq!(canonical_git_remote(input).unwrap().url, expected);
        }
        for rejected in [
            "https://user:password@example.test/org/repo",
            "ssh://git@example.test/org/repo",
            "https://example.test/org/repo?token=value",
        ] {
            assert!(canonical_git_remote(rejected).is_err());
        }
    }

    #[test]
    fn local_connector_scope_rejects_wrong_tenant() {
        let organization_id = Uuid::new_v4();
        let other = Uuid::new_v4();
        assert!(tenant_connector_path(
            &format!("local-connector://orgs/{organization_id}/github/app"),
            organization_id,
        )
        .is_ok());
        assert!(tenant_connector_path(
            &format!("local-connector://orgs/{other}/github/app"),
            organization_id,
        )
        .is_err());
    }

    #[test]
    fn git_auth_keeps_secret_out_of_argv_and_script() {
        let secret = ConnectorSecret::test_value("synthetic-private-value", "service-user");
        let auth = GitAuth::new(&secret).unwrap();
        let askpass = auth.askpass_path().to_path_buf();
        let script = std::fs::read_to_string(&askpass).unwrap();
        let mut command = git_transport_command(Path::new("git"));
        command.args([
            "clone",
            "https://example.test/acme/repository.git",
            "/tmp/repository",
        ]);
        auth.apply(&mut command);

        let command = command.as_std();
        let argv = command
            .get_args()
            .map(|value| value.to_string_lossy())
            .collect::<Vec<_>>()
            .join(" ");
        assert!(!argv.contains(secret.token()));
        assert!(!argv.contains("service-user"));
        assert!(!script.contains(secret.token()));
        assert!(!script.contains("service-user"));
        assert!(argv.contains("https://example.test/acme/repository.git"));

        drop(auth);
        assert!(!askpass.exists());
    }

    #[tokio::test]
    async fn git_transport_invokes_askpass_without_leaking_secrets() {
        let secret =
            ConnectorSecret::test_value("synthetic-askpass-token-8d7c2a", "synthetic-askpass-user");
        let auth = GitAuth::new(&secret).unwrap();
        let script = std::fs::read_to_string(auth.askpass_path()).unwrap();
        let directory = tempfile::tempdir().unwrap();
        let marker = directory.path().join("askpass-invoked");
        let fake_git = directory.path().join("git");
        std::fs::write(
            &fake_git,
            "#!/bin/sh\n\
             set -eu\n\
             username=\"$(\"$GIT_ASKPASS\" 'Username for https://example.test')\"\n\
             password=\"$(\"$GIT_ASKPASS\" 'Password for https://example.test')\"\n\
             printf 'username=%s\\npassword=%s\\n' \"$username\" \"$password\" > \"$OPENAGENTS_ASKPASS_MARKER\"\n\
             printf 'git shim argv:' >&2\n\
             printf ' <%s>' \"$@\" >&2\n\
             printf '\\n' >&2\n\
             exit 1\n",
        )
        .unwrap();
        std::fs::set_permissions(&fake_git, std::fs::Permissions::from_mode(0o700)).unwrap();

        let mut command = git_transport_command(&fake_git);
        command.env("OPENAGENTS_ASKPASS_MARKER", &marker).args([
            "clone",
            "https://example.test/acme/repository.git",
            "/tmp/repository",
        ]);
        auth.apply(&mut command);
        let command_argv = command
            .as_std()
            .get_args()
            .map(|value| value.to_string_lossy())
            .collect::<Vec<_>>()
            .join(" ");
        assert!(!command_argv.contains(secret.token()));
        assert!(!command_argv.contains("synthetic-askpass-user"));
        let output = command.output().await.unwrap();

        assert!(!output.status.success());
        assert_eq!(
            std::fs::read_to_string(marker).unwrap(),
            "username=synthetic-askpass-user\npassword=synthetic-askpass-token-8d7c2a\n"
        );
        let stderr = String::from_utf8_lossy(&output.stderr);
        assert!(!stderr.contains(secret.token()));
        assert!(!stderr.contains("synthetic-askpass-user"));
        assert!(!script.contains(secret.token()));
        assert!(!script.contains("synthetic-askpass-user"));
        assert!(!stderr.contains("credential.interactive=false"));
    }

    #[tokio::test]
    async fn local_connector_resolution_requires_registered_tenant_and_provider_scope() {
        let root = tempfile::tempdir().unwrap();
        let organization_id = Uuid::new_v4();
        let connector = root.path().join("github/app");
        fs::create_dir_all(connector.parent().unwrap())
            .await
            .unwrap();
        fs::write(
            &connector,
            br#"{"token":"registered-local-token","username":"git-app"}"#,
        )
        .await
        .unwrap();

        let valid = format!("local-connector://orgs/{organization_id}/github/app");
        let secret = resolve_local_connector(root.path(), &valid, organization_id, "github")
            .await
            .unwrap();
        assert_eq!(secret.token(), "registered-local-token");

        let missing = format!("local-connector://orgs/{organization_id}/github/missing");
        assert!(
            resolve_local_connector(root.path(), &missing, organization_id, "github")
                .await
                .is_err()
        );
        assert!(
            resolve_local_connector(root.path(), &valid, organization_id, "gitlab")
                .await
                .is_err()
        );
        assert!(
            resolve_local_connector(root.path(), &valid, Uuid::new_v4(), "github")
                .await
                .is_err()
        );
        assert!(resolve_local_connector(
            root.path(),
            &format!("local-connector://orgs/{organization_id}/github/../app"),
            organization_id,
            "github",
        )
        .await
        .is_err());
    }

    #[test]
    fn aws_connector_references_require_registered_region_tenant_and_provider_scope() {
        let organization_id = Uuid::new_v4();
        let secret_id = format!("orgs/{organization_id}/github/app");
        let reference = format!("aws-sm://eu-west-1/{secret_id}");
        assert_eq!(
            aws_connector_location(&reference, organization_id, "github").unwrap(),
            ("eu-west-1", secret_id.as_str())
        );

        for invalid in [
            format!("aws-sm://us-east-1/orgs/{organization_id}/github/app"),
            format!("aws-sm://eu-west-1/orgs/{}/github/app", Uuid::new_v4()),
            format!("aws-sm://eu-west-1/orgs/{organization_id}/gitlab/app"),
            format!("aws-sm://eu-west-1/orgs/{organization_id}/github/app?version=1"),
            format!("vault://eu-west-1/orgs/{organization_id}/github/app"),
        ] {
            assert!(aws_connector_location(&invalid, organization_id, "github").is_err());
        }
    }

    #[test]
    fn git_transport_starts_from_a_credential_free_environment() {
        let command = git_transport_command(Path::new("git"));
        let environment = command
            .as_std()
            .get_envs()
            .filter_map(|(key, value)| value.map(|value| (key, value)))
            .map(|(key, value)| format!("{}={}", key.to_string_lossy(), value.to_string_lossy()))
            .collect::<Vec<_>>()
            .join("\n");
        for forbidden in [
            "GH_TOKEN=",
            "GITHUB_TOKEN=",
            "OPENAGENTS_CONNECTOR_TOKEN=",
            "OPENAGENTS_CONNECTOR_USERNAME=",
            "AWS_ACCESS_KEY_ID=",
            "AWS_SECRET_ACCESS_KEY=",
        ] {
            assert!(!environment.contains(forbidden));
        }
        assert!(environment.contains("GIT_CONFIG_GLOBAL=/dev/null"));
        assert!(environment.contains("GIT_ASKPASS=/bin/false"));
    }
}
