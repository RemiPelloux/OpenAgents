"""Tests for optional-mcps wheel packaging."""

from __future__ import annotations

from pathlib import Path
import tomllib

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _optional_mcp_names() -> list[str]:
    root = REPO_ROOT / "optional-mcps"
    return sorted(
        p.name for p in root.iterdir() if p.is_dir() and (p / "manifest.yaml").is_file()
    )


def test_every_optional_mcp_has_pyproject_data_files_entry():
    """Each optional-mcps/<name>/manifest.yaml must ship in the wheel."""
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    data_files = data["tool"]["setuptools"].get("data-files", {})

    missing = []
    for name in _optional_mcp_names():
        key = f"optional-mcps/{name}"
        if key not in data_files:
            missing.append(name)

    assert not missing, (
        "Add pyproject.toml [tool.setuptools.data-files] entries for: "
        + ", ".join(missing)
    )
