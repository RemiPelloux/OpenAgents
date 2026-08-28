"""Runtime smoke test for Docker config-schema migration on boot.

Build the real image and verify: a config.yaml present in $OPENAGENTS_HOME
is migrated by docker_config_migrate.py on boot, running as the hermes
user.
"""
from __future__ import annotations

import subprocess

from tests.docker.conftest import (
    docker_exec,
    docker_exec_sh,
    poll_container,
    start_container,
    wait_for_container_ready,
)


def test_config_migration_runs_on_boot(
    built_image: str, container_name: str,
) -> None:
    """A config.yaml in $OPENAGENTS_HOME must be migrated on boot by
    docker_config_migrate.py, running as the hermes user."""
    # Start container
    start_container(built_image, container_name)

    # Verify config.yaml exists (should be seeded by stage2 if not present)
    r = docker_exec_sh(
        container_name,
        "test -f /opt/data/config.yaml && echo EXISTS || echo MISSING",
        timeout=10,
    )
    assert "EXISTS" in r.stdout, (
        f"config.yaml not found in $OPENAGENTS_HOME: {r.stdout}"
    )

    # Verify the migration script exists in the image
    r = docker_exec_sh(
        container_name,
        "test -f /opt/hermes/scripts/docker_config_migrate.py && "
        "echo SCRIPT_EXISTS || echo SCRIPT_MISSING",
        timeout=10,
    )
    assert "SCRIPT_EXISTS" in r.stdout, (
        f"docker_config_migrate.py not found in image: {r.stdout}"
    )

    # Verify config.yaml is owned by hermes (migration ran as hermes)
    r = docker_exec_sh(
        container_name,
        'stat -c "%U" /opt/data/config.yaml',
        timeout=10,
    )
    assert r.stdout.strip() == "openagents", (
        f"config.yaml not owned by hermes (migration may have run as root): "
        f"{r.stdout.strip()}"
    )


def test_config_migration_opt_out_env_var_respected(
    built_image: str, container_name: str,
) -> None:
    """HERMES_SKIP_CONFIG_MIGRATION=1 must skip the migration."""
    start_container(
        built_image, container_name, "HERMES_SKIP_CONFIG_MIGRATION=1",
    )

    # config.yaml should still be seeded (seeding is separate from migration)
    r = docker_exec_sh(
        container_name,
        "test -f /opt/data/config.yaml && echo EXISTS || echo MISSING",
        timeout=10,
    )
    assert "EXISTS" in r.stdout, (
        f"config.yaml should be seeded even with migration skipped: {r.stdout}"
    )


def test_managed_llm_env_survives_legacy_migration_with_minimal_caps(
    built_image: str, container_name: str,
) -> None:
    """Managed LLM values are restored after migration 12 -> 13 clears them."""
    volume = f"{container_name}-data"
    subprocess.run(
        ["docker", "volume", "create", volume],
        check=True, capture_output=True, timeout=10,
    )
    try:
        subprocess.run(
            [
                "docker", "run", "--rm", "-v", f"{volume}:/opt/data",
                "--entrypoint", "sh", built_image, "-c",
                "printf '_config_version: 12\\n' > /opt/data/config.yaml; "
                "printf 'LLM_MODEL=legacy-model\\n' > /opt/data/.env",
            ],
            check=True, capture_output=True, timeout=30,
        )
        subprocess.run(
            [
                "docker", "run", "-d", "--name", container_name,
                "-v", f"{volume}:/opt/data",
                "--cap-drop", "ALL",
                "--cap-add", "CHOWN",
                "--cap-add", "SETGID",
                "--cap-add", "SETUID",
                "--group-add", "10000",
                "-e", "HERMES_MANAGED_DIR=/etc/hermes",
                "-e", "HERMES_GATEWAY_BOOTSTRAP_STATE=running",
                "-e", "API_SERVER_ENABLED=true",
                "-e", "API_SERVER_HOST=127.0.0.1",
                "-e", "API_SERVER_PORT=8642",
                "-e", "API_SERVER_KEY=test-key",
                "-e", "LLM_PROVIDER=openai-compatible",
                "-e", "LLM_BASE_URL=http://127.0.0.1:9/v1",
                "-e", "LLM_API_KEY=test-llm-key",
                "-e", "LLM_MODEL=managed-model",
                built_image, "sleep", "infinity",
            ],
            check=True, capture_output=True, timeout=60,
        )
        wait_for_container_ready(container_name)

        values = docker_exec(
            container_name,
            "/opt/hermes/.venv/bin/python", "-c",
            "from dotenv import dotenv_values; "
            "v=dotenv_values('/opt/data/.env'); "
            "assert v['LLM_MODEL']=='managed-model'; "
            "assert v['LLM_BASE_URL']=='http://127.0.0.1:9/v1'",
            timeout=10,
        )
        assert values.returncode == 0, values.stderr

        modes = docker_exec_sh(
            container_name,
            "stat -c '%u:%g:%a' /opt/data/.env /opt/data/config.yaml",
            timeout=10,
        )
        assert modes.returncode == 0, modes.stderr
        assert modes.stdout.splitlines() == [
            "10000:10000:600",
            "10000:10000:640",
        ]

        healthy, output = poll_container(
            container_name,
            "curl -fsS http://127.0.0.1:8642/v1/health >/dev/null",
            deadline_s=45,
        )
        assert healthy, f"managed gateway did not become healthy: {output}"
    finally:
        subprocess.run(
            ["docker", "rm", "-f", container_name],
            capture_output=True, timeout=10,
        )
        subprocess.run(
            ["docker", "volume", "rm", "-f", volume],
            capture_output=True, timeout=10,
        )
