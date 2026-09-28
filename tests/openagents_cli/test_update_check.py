"""Tests for the update check mechanism in openagents_cli.banner.

Passive checks go through the GitHub REST API — never ``git fetch``. Every CLI, TUI and desktop
start used to fetch; across the install base that was tens of millions of fetch requests a day
and GitHub asked us to poll the API instead. These tests pin that contract plus the cache
policy that keeps the API traffic to one request a day per install.
"""

import json
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

import openagents_cli.banner as banner

SHA_A = "a" * 40
SHA_B = "b" * 40


@pytest.fixture
def git_repo(tmp_path, monkeypatch):
    """A fake checkout the update check resolves to, with git calls stubbed out."""
    repo_dir = tmp_path / "openagents"
    repo_dir.mkdir()
    (repo_dir / ".git").mkdir()
    monkeypatch.setenv("OPENAGENTS_HOME", str(tmp_path))
    monkeypatch.delenv("HERMES_REVISION", raising=False)
    monkeypatch.setattr(banner, "_resolve_repo_dir", lambda: repo_dir)
    monkeypatch.setattr("openagents_cli.config.detect_install_method", lambda root: "git")
    monkeypatch.setattr("openagents_cli.config.get_project_root", lambda: repo_dir)
    return repo_dir


def _stub_git(monkeypatch, *, head=SHA_A, origin="https://github.com/NousResearch/hermes-agent.git"):
    calls = []

<<<<<<< HEAD
    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd == ["git", "remote", "get-url", "origin"]:
            return MagicMock(returncode=0, stdout="git@github.com:RemiPelloux/OpenAgents.git\n")
        if cmd == ["git", "rev-parse", "HEAD"]:
            return MagicMock(returncode=0, stdout="local-sha\n")
        if cmd == [
            "git",
            "ls-remote",
            "https://github.com/RemiPelloux/OpenAgents.git",
            "refs/heads/main",
        ]:
            return MagicMock(returncode=0, stdout="upstream-sha\trefs/heads/main\n")
        raise AssertionError(f"unexpected git command: {cmd!r}")
||||||| cf299e9a01
    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd == ["git", "remote", "get-url", "origin"]:
            return MagicMock(returncode=0, stdout="git@github.com:NousResearch/openagents.git\n")
        if cmd == ["git", "rev-parse", "HEAD"]:
            return MagicMock(returncode=0, stdout="local-sha\n")
        if cmd == [
            "git",
            "ls-remote",
            "https://github.com/NousResearch/openagents.git",
            "refs/heads/main",
        ]:
            return MagicMock(returncode=0, stdout="upstream-sha\trefs/heads/main\n")
        raise AssertionError(f"unexpected git command: {cmd!r}")
=======
    def fake_run(args, **kwargs):
        calls.append(list(args))
        sub = args[1]
        if sub == "rev-parse":
            return MagicMock(returncode=0, stdout=f"{head}\n")
        if sub == "remote":
            return MagicMock(returncode=0, stdout=f"{origin}\n")
        if sub == "merge-base":
            return MagicMock(returncode=1, stdout="")
        raise AssertionError(f"passive check must not run git {sub}: {args}")
>>>>>>> rb/tag

    monkeypatch.setattr(banner.subprocess, "run", fake_run)
    return calls


def test_passive_check_uses_the_api_and_never_fetches(git_repo, monkeypatch):
    """The whole point: no ``git fetch`` / ``ls-remote`` for a GitHub origin, exact count via compare."""
    calls = _stub_git(monkeypatch, head=SHA_A)
    tip = MagicMock(return_value=SHA_B)
    monkeypatch.setattr(banner, "_github_branch_tip", tip)
    monkeypatch.setattr(banner, "_github_compare_behind", lambda cur, tgt: 61)

    assert banner.check_for_updates() == 61
    tip.assert_called_once_with("nousresearch/hermes-agent", "main")
    assert not any(c[1] in {"fetch", "ls-remote"} for c in calls)

<<<<<<< HEAD
    repo_dir = tmp_path / "openagents"
    repo_dir.mkdir()
    (repo_dir / ".git").mkdir()

    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd == ["git", "remote", "get-url", "origin"]:
            return MagicMock(returncode=0, stdout="https://github.com/RemiPelloux/OpenAgents.git\n")
        if cmd == ["git", "rev-parse", "--is-shallow-repository"]:
            return MagicMock(returncode=0, stdout="true\n")
        if cmd[:2] == ["git", "fetch"]:
            return MagicMock(returncode=0, stdout="")
        if cmd == ["git", "rev-parse", "HEAD"]:
            return MagicMock(returncode=0, stdout="local-sha\n")
        if cmd == ["git", "rev-parse", "FETCH_HEAD"]:
            return MagicMock(returncode=0, stdout="upstream-sha\n")
        if cmd[:3] == ["git", "rev-list", "--count"]:
            raise AssertionError("shallow path must not count across the boundary")
        raise AssertionError(f"unexpected git command: {cmd!r}")

    with patch("openagents_cli.banner.subprocess.run", side_effect=fake_run):
        result = banner._check_via_local_git(repo_dir)

    assert result == banner.UPDATE_AVAILABLE_NO_COUNT
    # The shallow fetch must preserve the boundary (--depth 1), not unshallow.
    assert ["git", "fetch", "origin", "--depth", "1", "--quiet"] in calls
||||||| cf299e9a01
    repo_dir = tmp_path / "openagents"
    repo_dir.mkdir()
    (repo_dir / ".git").mkdir()

    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd == ["git", "remote", "get-url", "origin"]:
            return MagicMock(returncode=0, stdout="https://github.com/NousResearch/openagents.git\n")
        if cmd == ["git", "rev-parse", "--is-shallow-repository"]:
            return MagicMock(returncode=0, stdout="true\n")
        if cmd[:2] == ["git", "fetch"]:
            return MagicMock(returncode=0, stdout="")
        if cmd == ["git", "rev-parse", "HEAD"]:
            return MagicMock(returncode=0, stdout="local-sha\n")
        if cmd == ["git", "rev-parse", "FETCH_HEAD"]:
            return MagicMock(returncode=0, stdout="upstream-sha\n")
        if cmd[:3] == ["git", "rev-list", "--count"]:
            raise AssertionError("shallow path must not count across the boundary")
        raise AssertionError(f"unexpected git command: {cmd!r}")

    with patch("openagents_cli.banner.subprocess.run", side_effect=fake_run):
        result = banner._check_via_local_git(repo_dir)

    assert result == banner.UPDATE_AVAILABLE_NO_COUNT
    # The shallow fetch must preserve the boundary (--depth 1), not unshallow.
    assert ["git", "fetch", "origin", "--depth", "1", "--quiet"] in calls
=======
    cached = json.loads((git_repo.parent / ".update_check").read_text())
    assert (cached["head"], cached["target"], cached["behind"]) == (SHA_A, SHA_B, 61)
>>>>>>> rb/tag


def test_cache_is_daily_but_invalidated_when_head_moves(git_repo, monkeypatch):
    """A fresh cache answers without any network; ``hermes update`` moving HEAD busts it at once;
    an inconclusive (None) result is retried after the shorter failure window, not never."""
    from openagents_cli import __version__

    cache_file = git_repo.parent / ".update_check"
    _stub_git(monkeypatch, head=SHA_A)
    tip = MagicMock(return_value=None)
    monkeypatch.setattr(banner, "_github_branch_tip", tip)

<<<<<<< HEAD
    def fake_run(cmd, **kwargs):
        if cmd == ["git", "remote", "get-url", "origin"]:
            return MagicMock(returncode=0, stdout="https://github.com/RemiPelloux/OpenAgents.git\n")
        if cmd == ["git", "rev-parse", "--is-shallow-repository"]:
            return MagicMock(returncode=0, stdout="true\n")
        if cmd[:2] == ["git", "fetch"]:
            return MagicMock(returncode=0, stdout="")
        if cmd == ["git", "rev-parse", "HEAD"]:
            return MagicMock(returncode=0, stdout="same-sha\n")
        if cmd == ["git", "rev-parse", "FETCH_HEAD"]:
            return MagicMock(returncode=0, stdout="same-sha\n")
        raise AssertionError(f"unexpected git command: {cmd!r}")
||||||| cf299e9a01
    def fake_run(cmd, **kwargs):
        if cmd == ["git", "remote", "get-url", "origin"]:
            return MagicMock(returncode=0, stdout="https://github.com/NousResearch/openagents.git\n")
        if cmd == ["git", "rev-parse", "--is-shallow-repository"]:
            return MagicMock(returncode=0, stdout="true\n")
        if cmd[:2] == ["git", "fetch"]:
            return MagicMock(returncode=0, stdout="")
        if cmd == ["git", "rev-parse", "HEAD"]:
            return MagicMock(returncode=0, stdout="same-sha\n")
        if cmd == ["git", "rev-parse", "FETCH_HEAD"]:
            return MagicMock(returncode=0, stdout="same-sha\n")
        raise AssertionError(f"unexpected git command: {cmd!r}")
=======
    def write_cache(*, ts, head, behind):
        cache_file.write_text(json.dumps(
            {"ts": ts, "behind": behind, "rev": None, "ver": __version__, "head": head}))
>>>>>>> rb/tag

    write_cache(ts=time.time() - banner._UPDATE_CHECK_CACHE_SECONDS + 60, head=SHA_A, behind=3)
    assert banner.check_for_updates() == 3
    tip.assert_not_called()

    write_cache(ts=time.time(), head=SHA_B, behind=3)  # cached for a different HEAD
    assert banner.check_for_updates() is None  # API unreachable → inconclusive, re-asked
    tip.assert_called_once()

    tip.reset_mock()
    write_cache(ts=time.time() - banner._UPDATE_CHECK_FAILURE_CACHE_SECONDS + 60, head=SHA_A, behind=None)
    assert banner.check_for_updates() is None
    tip.assert_not_called()

    write_cache(ts=time.time() - banner._UPDATE_CHECK_FAILURE_CACHE_SECONDS - 1, head=SHA_A, behind=None)
    banner.check_for_updates()
    tip.assert_called_once()


<<<<<<< HEAD
def test_check_via_local_git_full_clone_keeps_exact_count(tmp_path):
    """Full (non-shallow) clones keep the exact rev-list count path."""
    import openagents_cli.banner as banner

    repo_dir = tmp_path / "openagents"
    repo_dir.mkdir()
    (repo_dir / ".git").mkdir()

    def fake_run(cmd, **kwargs):
        if cmd == ["git", "remote", "get-url", "origin"]:
            return MagicMock(returncode=0, stdout="https://github.com/RemiPelloux/OpenAgents.git\n")
        if cmd == ["git", "rev-parse", "--is-shallow-repository"]:
            return MagicMock(returncode=0, stdout="false\n")
        if cmd[:2] == ["git", "fetch"]:
            return MagicMock(returncode=0, stdout="")
        if cmd[:3] == ["git", "rev-list", "--count"]:
            return MagicMock(returncode=0, stdout="7\n")
        raise AssertionError(f"unexpected git command: {cmd!r}")

    with patch("openagents_cli.banner.subprocess.run", side_effect=fake_run):
        result = banner._check_via_local_git(repo_dir)

    assert result == 7


def test_check_for_updates_no_git_dir(tmp_path, monkeypatch):
    """Falls back to PyPI check when .git directory doesn't exist anywhere."""
    import openagents_cli.banner as banner

    # Create a fake banner.py so the fallback path also has no .git
    fake_banner = tmp_path / "openagents_cli" / "banner.py"
    fake_banner.parent.mkdir(parents=True, exist_ok=True)
    fake_banner.touch()

    monkeypatch.setattr(banner, "__file__", str(fake_banner))
    monkeypatch.setenv("OPENAGENTS_HOME", str(tmp_path))
    with patch("openagents_cli.banner.subprocess.run") as mock_run:
        with patch("openagents_cli.banner.check_via_pypi", return_value=0):
            result = banner.check_for_updates()
    assert result == 0
    mock_run.assert_not_called()


def test_check_for_updates_fallback_to_project_root(tmp_path, monkeypatch):
    """Dev install: falls back to Path(__file__).parent.parent when OPENAGENTS_HOME has no git repo."""
    import openagents_cli.banner as banner

    project_root = Path(banner.__file__).parent.parent.resolve()
    if not (project_root / ".git").exists():
        pytest.skip("Not running from a git checkout")

    # Point OPENAGENTS_HOME at a temp dir with no openagents/.git
    monkeypatch.setenv("OPENAGENTS_HOME", str(tmp_path))
    with patch("openagents_cli.banner.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="0\n")
        result = banner.check_for_updates()
    # Should have fallen back to project root and run git commands
    assert mock_run.call_count >= 1


def test_check_for_updates_docker_returns_none(tmp_path, monkeypatch):
    """Inside the Docker image, check_for_updates() must short-circuit to None.

    Regression: the published image excludes .git (.dockerignore) and sets no
    HERMES_REVISION (nix-only), so without a docker guard check_for_updates()
    falls through to check_via_pypi(), whose version-mismatch flag (1) gets
    rendered by both the Rich banner and the Ink TUI badge as a phantom
    "1 commit behind" — despite there being no git repo or commit math in the
    container, and `hermes update` correctly refusing to run there. The guard
    must return None (so the > 0 render guards stay false) AND not reach the
    git/pypi probes or write a cache entry.
    """
    import openagents_cli.banner as banner

    monkeypatch.setenv("OPENAGENTS_HOME", str(tmp_path))
    cache_file = tmp_path / ".update_check"

    with patch("openagents_cli.config.detect_install_method", return_value="docker"), \
         patch("openagents_cli.banner.subprocess.run") as mock_run, \
         patch("openagents_cli.banner.check_via_pypi") as mock_pypi:
        result = banner.check_for_updates()

    assert result is None
    # Neither the git probe nor the PyPI probe should have run.
    mock_run.assert_not_called()
    mock_pypi.assert_not_called()
    # And no phantom "behind" count should be cached for the next 6h.
    assert not cache_file.exists()


def test_check_for_updates_non_docker_still_checks(tmp_path, monkeypatch):
    """The docker guard must NOT over-broaden: a pip install still version-checks.

    Invariant guarding against the guard firing for non-docker methods — pip
    installs legitimately reach check_via_pypi() and surface a real update.
    """
    import openagents_cli.banner as banner

    # No local git checkout -> the PyPI (pip-install) path is exercised.
    fake_banner = tmp_path / "openagents_cli" / "banner.py"
    fake_banner.parent.mkdir(parents=True, exist_ok=True)
    fake_banner.touch()
    monkeypatch.setattr(banner, "__file__", str(fake_banner))
    monkeypatch.setenv("OPENAGENTS_HOME", str(tmp_path))
    monkeypatch.delenv("HERMES_REVISION", raising=False)

    with patch("openagents_cli.config.detect_install_method", return_value="pip"), \
         patch("openagents_cli.banner.subprocess.run") as mock_run, \
         patch("openagents_cli.banner.check_via_pypi", return_value=1) as mock_pypi:
        result = banner.check_for_updates()

    assert result == 1
    mock_pypi.assert_called_once()
    mock_run.assert_not_called()


def test_prefetch_non_blocking():
||||||| cf299e9a01
def test_check_via_local_git_full_clone_keeps_exact_count(tmp_path):
    """Full (non-shallow) clones keep the exact rev-list count path."""
    import openagents_cli.banner as banner

    repo_dir = tmp_path / "openagents"
    repo_dir.mkdir()
    (repo_dir / ".git").mkdir()

    def fake_run(cmd, **kwargs):
        if cmd == ["git", "remote", "get-url", "origin"]:
            return MagicMock(returncode=0, stdout="https://github.com/NousResearch/openagents.git\n")
        if cmd == ["git", "rev-parse", "--is-shallow-repository"]:
            return MagicMock(returncode=0, stdout="false\n")
        if cmd[:2] == ["git", "fetch"]:
            return MagicMock(returncode=0, stdout="")
        if cmd[:3] == ["git", "rev-list", "--count"]:
            return MagicMock(returncode=0, stdout="7\n")
        raise AssertionError(f"unexpected git command: {cmd!r}")

    with patch("openagents_cli.banner.subprocess.run", side_effect=fake_run):
        result = banner._check_via_local_git(repo_dir)

    assert result == 7


def test_check_for_updates_no_git_dir(tmp_path, monkeypatch):
    """Falls back to PyPI check when .git directory doesn't exist anywhere."""
    import openagents_cli.banner as banner

    # Create a fake banner.py so the fallback path also has no .git
    fake_banner = tmp_path / "openagents_cli" / "banner.py"
    fake_banner.parent.mkdir(parents=True, exist_ok=True)
    fake_banner.touch()

    monkeypatch.setattr(banner, "__file__", str(fake_banner))
    monkeypatch.setenv("OPENAGENTS_HOME", str(tmp_path))
    with patch("openagents_cli.banner.subprocess.run") as mock_run:
        with patch("openagents_cli.banner.check_via_pypi", return_value=0):
            result = banner.check_for_updates()
    assert result == 0
    mock_run.assert_not_called()


def test_check_for_updates_fallback_to_project_root(tmp_path, monkeypatch):
    """Dev install: falls back to Path(__file__).parent.parent when OPENAGENTS_HOME has no git repo."""
    import openagents_cli.banner as banner

    project_root = Path(banner.__file__).parent.parent.resolve()
    if not (project_root / ".git").exists():
        pytest.skip("Not running from a git checkout")

    # Point OPENAGENTS_HOME at a temp dir with no openagents/.git
    monkeypatch.setenv("OPENAGENTS_HOME", str(tmp_path))
    with patch("openagents_cli.banner.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="0\n")
        result = banner.check_for_updates()
    # Should have fallen back to project root and run git commands
    assert mock_run.call_count >= 1


def test_check_for_updates_docker_returns_none(tmp_path, monkeypatch):
    """Inside the Docker image, check_for_updates() must short-circuit to None.

    Regression: the published image excludes .git (.dockerignore) and sets no
    HERMES_REVISION (nix-only), so without a docker guard check_for_updates()
    falls through to check_via_pypi(), whose version-mismatch flag (1) gets
    rendered by both the Rich banner and the Ink TUI badge as a phantom
    "1 commit behind" — despite there being no git repo or commit math in the
    container, and `hermes update` correctly refusing to run there. The guard
    must return None (so the > 0 render guards stay false) AND not reach the
    git/pypi probes or write a cache entry.
    """
    import openagents_cli.banner as banner

    monkeypatch.setenv("OPENAGENTS_HOME", str(tmp_path))
    cache_file = tmp_path / ".update_check"

    with patch("openagents_cli.config.detect_install_method", return_value="docker"), \
         patch("openagents_cli.banner.subprocess.run") as mock_run, \
         patch("openagents_cli.banner.check_via_pypi") as mock_pypi:
        result = banner.check_for_updates()

    assert result is None
    # Neither the git probe nor the PyPI probe should have run.
    mock_run.assert_not_called()
    mock_pypi.assert_not_called()
    # And no phantom "behind" count should be cached for the next 6h.
    assert not cache_file.exists()


def test_check_for_updates_non_docker_still_checks(tmp_path, monkeypatch):
    """The docker guard must NOT over-broaden: a pip install still version-checks.

    Invariant guarding against the guard firing for non-docker methods — pip
    installs legitimately reach check_via_pypi() and surface a real update.
    """
    import openagents_cli.banner as banner

    # No local git checkout -> the PyPI (pip-install) path is exercised.
    fake_banner = tmp_path / "openagents_cli" / "banner.py"
    fake_banner.parent.mkdir(parents=True, exist_ok=True)
    fake_banner.touch()
    monkeypatch.setattr(banner, "__file__", str(fake_banner))
    monkeypatch.setenv("OPENAGENTS_HOME", str(tmp_path))
    monkeypatch.delenv("HERMES_REVISION", raising=False)

    with patch("openagents_cli.config.detect_install_method", return_value="pip"), \
         patch("openagents_cli.banner.subprocess.run") as mock_run, \
         patch("openagents_cli.banner.check_via_pypi", return_value=1) as mock_pypi:
        result = banner.check_for_updates()

    assert result == 1
    mock_pypi.assert_called_once()
    mock_run.assert_not_called()


def test_prefetch_non_blocking():
=======
def test_prefetch_non_blocking(monkeypatch):
>>>>>>> rb/tag
    """prefetch_update_check() should return immediately without blocking."""
    # Reset module state; force the real (non-pytest) thread path.
    banner._update_result = None
    banner._update_check_done = threading.Event()
    monkeypatch.setattr(banner, "_skip_background_prefetch", lambda: False)

    with patch.object(banner, "check_for_updates", return_value=5):
        start = time.monotonic()
        banner.prefetch_update_check()
        assert time.monotonic() - start < 1.0
        banner._update_check_done.wait(timeout=5)
        assert banner._update_result == 5


def test_prefetch_update_check_is_noop_under_pytest():
    """Under pytest the prefetch must NOT start the git-spawning daemon
    thread: a process-wide ``patch("subprocess.run")`` in an unrelated test
    can capture the thread's ``git fetch``/``rev-parse`` spawns, flaking the
    unrelated test's call_args assertions (seen in
    tests/tui_gateway/test_subprocess_encoding.py and
    test_bot_relay_methods.py on CI, 2026-08-28)."""
    banner._update_result = None
    banner._update_check_done = threading.Event()

    before = {t.ident for t in threading.enumerate()}
    with patch.object(banner, "check_for_updates") as mock_check:
        banner.prefetch_update_check()
        # The done event is set synchronously so get_update_result() callers
        # don't burn their timeout waiting on a check that will never run.
        assert banner._update_check_done.is_set()
        mock_check.assert_not_called()
    after = {t.ident for t in threading.enumerate()}
    assert after <= before, "prefetch_update_check spawned a thread under pytest"


def test_prefetch_banner_data_is_noop_under_pytest(monkeypatch):
    """Same stray-git-spawn class: prefetch_banner_data must not start its
    daemon thread under pytest."""
    monkeypatch.setattr(banner, "_banner_data_prefetch_started", False)
    with patch.object(banner, "get_git_banner_state") as mock_state:
        banner.prefetch_banner_data()
        # Give a hypothetical stray thread a beat to run — nothing should.
        time.sleep(0.05)
        mock_state.assert_not_called()
    assert banner._banner_data_prefetch_started is True


def test_upstream_main_sha_ls_remote_fallback_disables_git_prompts(monkeypatch):
    """When the API is unreachable the HTTPS ls-remote fallback must never inherit the terminal."""
    monkeypatch.setattr(banner, "_github_branch_tip", lambda slug, branch: None)
    completed = MagicMock(returncode=1, stdout="", stderr="auth required")
    run = MagicMock(return_value=completed)
    monkeypatch.setattr(banner.subprocess, "run", run)

    assert banner._upstream_main_sha() is None
    kwargs = run.call_args.kwargs
    assert kwargs["stdin"] is banner.subprocess.DEVNULL
    assert kwargs["env"]["GIT_TERMINAL_PROMPT"] == "0"
    assert kwargs["env"]["GCM_INTERACTIVE"] == "Never"
