"""Tests for /company workspace scaffolding."""

import os
from pathlib import Path

import pytest

from openagents_cli.company_cmd import (
    MANIFEST_NAME,
    find_company_root,
    handle_company_command,
    load_manifest,
    scaffold_company,
)


@pytest.fixture
def company_env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_scaffold_startup_layout(company_env):
    root = company_env / "acme"
    scaffold_company(root, name="Acme Corp", template="startup", register_project=False)

    assert (root / MANIFEST_NAME).is_file()
    assert (root / "COMPANY.md").is_file()
    assert (root / "AGENTS.md").is_file()
    assert (root / "roles" / "ceo.yaml").is_file()
    assert (root / "agents" / "engineer" / "SOUL.md").is_file()
    assert (root / "skills" / "assignments.yaml").is_file()
    assert (root / "workspace").is_dir()

    manifest = load_manifest(root)
    role_ids = [r["id"] for r in manifest["roles"]]
    assert "ceo" in role_ids
    assert "engineer" in role_ids
    assert manifest["template"] == "startup"


def test_find_company_root_walks_up(company_env):
    root = company_env / "co"
    scaffold_company(root, name="Co", register_project=False)
    nested = root / "workspace" / "deep"
    nested.mkdir(parents=True)
    assert find_company_root(nested) == root


def test_handle_init_command(company_env):
    res = handle_company_command('init "Test Co" ./test-co template=minimal')
    assert res.agent_seed is None
    assert "Created company" in res.text
    assert (company_env / "test-co" / MANIFEST_NAME).is_file()
    manifest = load_manifest(company_env / "test-co")
    assert len(manifest["roles"]) == 2


def test_handle_delegate_seeds_agent(company_env):
    root = company_env / "co"
    scaffold_company(root, name="Co", register_project=False)
    os.chdir(root)
    res = handle_company_command("delegate engineer Fix the auth module")
    assert res.agent_seed is not None
    assert "engineer" in res.agent_seed.lower()
    assert "Fix the auth module" in res.agent_seed


def test_handle_roles_unknown(company_env):
    root = company_env / "co"
    scaffold_company(root, name="Co", register_project=False)
    os.chdir(root)
    res = handle_company_command("roles nosuch")
    assert "Unknown role" in res.text


def test_scaffold_rejects_nonempty_dir(company_env):
    root = company_env / "busy"
    root.mkdir()
    (root / "existing.txt").write_text("x", encoding="utf-8")
    with pytest.raises(FileExistsError):
        scaffold_company(root, name="Busy", register_project=False)
