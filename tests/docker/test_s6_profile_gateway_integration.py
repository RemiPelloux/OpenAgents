"""Harness: in-container integration tests for S6ServiceManager.

The unit tests in tests/openagents_cli/test_service_manager.py exercise the
class against a tmp-path scandir with a stubbed ``subprocess.run``.
These tests run the real class inside a real container against the
real s6-svc / s6-svscanctl binaries, validating end-to-end.

Phase 3 only registers the service slot — it doesn't depend on the
gateway actually starting (the binary will refuse to start without a
valid profile config). The full register → start → supervised-restart
→ unregister cycle is covered by Phase 4 once profile create/delete
hooks land.

Every ``docker exec`` here runs as the unprivileged ``hermes`` user
(via :func:`docker_exec` in conftest); see the conftest module
docstring. ``/run/openagents-services`` is owned by hermes and watched by a
nested scanner that also runs as hermes, so registration cannot publish a
root service.
"""
from __future__ import annotations

from tests.docker.conftest import docker_exec, poll_container, start_container


_REGISTER_SCRIPT = """
import sys
sys.path.insert(0, "/opt/hermes")
from openagents_cli.service_manager import S6ServiceManager
S6ServiceManager().register_profile_gateway("phase3test")
# Don't worry about whether the gateway actually starts — we only care
# that the supervision slot was created. The gateway run script will
# likely error out (no profile config exists) but that's expected.
print("REGISTERED")
"""

_UNREGISTER_SCRIPT = """
import sys
sys.path.insert(0, "/opt/hermes")
from openagents_cli.service_manager import S6ServiceManager
S6ServiceManager().unregister_profile_gateway("phase3test")
print("UNREGISTERED")
"""


def test_s6_register_creates_service_dir_in_live_container(
    built_image: str, container_name: str,
) -> None:
    """S6ServiceManager.register_profile_gateway must create
    ``/run/openagents-services/gateway-<profile>/`` and trigger s6-svscan rescan
    against the real s6 supervision tree."""
    start_container(built_image, container_name, cmd="sleep 120")

    r = docker_exec(container_name, "python3", "-c", _REGISTER_SCRIPT, timeout=30)
    assert "REGISTERED" in r.stdout, (
        f"register failed: stderr={r.stderr!r} stdout={r.stdout!r}"
    )

    # Service directory exists with the expected structure.
    r = docker_exec(container_name, "test", "-d", "/run/openagents-services/gateway-phase3test")
    assert r.returncode == 0, "service directory not created"

    r = docker_exec(container_name, "test", "-f", "/run/openagents-services/gateway-phase3test/run")
    assert r.returncode == 0, "run script not created"

    r = docker_exec(container_name, "test", "-f",
              "/run/openagents-services/gateway-phase3test/log/run")
    assert r.returncode == 0, "log/run script not created"

    # s6-svscan picked it up — s6-svstat works against the dir.
    # `docker exec` doesn't put /command/ on PATH (only the supervision
    # tree does), so call s6-svstat by absolute path.
    r = docker_exec(container_name, "/command/s6-svstat",
              "/run/openagents-services/gateway-phase3test")
    assert r.returncode == 0, f"s6-svstat failed: {r.stderr or r.stdout}"

    # list_profile_gateways picks it up.
    r = docker_exec(container_name, "python3", "-c", (
        "from openagents_cli.service_manager import S6ServiceManager;"
        "print(S6ServiceManager().list_profile_gateways())"
    ))
    assert "phase3test" in r.stdout, f"list output: {r.stdout!r}"


def test_s6_unregister_removes_service_dir_in_live_container(
    built_image: str, container_name: str,
) -> None:
    """unregister_profile_gateway must stop the service, remove the
    directory, and trigger s6-svscan rescan so the supervise process
    is dropped."""
    start_container(built_image, container_name, cmd="sleep 120")

    # First register so we have something to unregister.
    r = docker_exec(container_name, "python3", "-c", _REGISTER_SCRIPT, timeout=30)
    assert "REGISTERED" in r.stdout

    # Then unregister.
    r = docker_exec(container_name, "python3", "-c", _UNREGISTER_SCRIPT, timeout=30)
    assert "UNREGISTERED" in r.stdout, (
        f"unregister failed: stderr={r.stderr!r} stdout={r.stdout!r}"
    )

    # Directory is gone.
    r = docker_exec(container_name, "test", "-d", "/run/openagents-services/gateway-phase3test")
    assert r.returncode != 0, "service directory still exists after unregister"

    # list_profile_gateways no longer includes it.
    r = docker_exec(container_name, "python3", "-c", (
        "from openagents_cli.service_manager import S6ServiceManager;"
        "print(S6ServiceManager().list_profile_gateways())"
    ))
    assert "phase3test" not in r.stdout


def test_dynamic_scanner_cannot_execute_a_published_service_as_root(
    built_image: str, container_name: str,
) -> None:
    """A hostile hermes-owned service directory remains UID 10000."""
    start_container(built_image, container_name, cmd="sleep 120")

    script = """
from pathlib import Path
import subprocess

scandir = Path('/run/openagents-services')
service = scandir / 'hostile-publication'
service.mkdir()
run = service / 'run'
run.write_text(
    '#!/bin/sh\\n'
    'id -u > /opt/data/hostile-publication.uid\\n'
    'exec sleep 60\\n'
)
run.chmod(0o755)
subprocess.run(
    ['/command/s6-svscanctl', '-a', str(scandir)],
    check=True,
)
"""
    published = docker_exec(
        container_name, "python3", "-c", script, timeout=30,
    )
    assert published.returncode == 0, published.stderr

    ready, output = poll_container(
        container_name,
        "test \"$(cat /opt/data/hostile-publication.uid 2>/dev/null)\" = 10000",
        deadline_s=15,
    )
    assert ready, f"published service escaped hermes UID: {output}"


def test_hermes_cannot_publish_to_or_signal_root_scandir(
    built_image: str, container_name: str,
) -> None:
    """The root scanner remains outside the hermes writable boundary."""
    start_container(built_image, container_name, cmd="sleep 120")

    create = docker_exec(
        container_name,
        "mkdir", "/run/service/hostile-publication",
        timeout=10,
    )
    assert create.returncode != 0, "hermes unexpectedly wrote to /run/service"

    signal = docker_exec(
        container_name,
        "/command/s6-svscanctl", "-a", "/run/service",
        timeout=10,
    )
    assert signal.returncode != 0, "hermes unexpectedly signalled root s6-svscan"
