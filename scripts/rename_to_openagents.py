#!/usr/bin/env python3
"""Re-apply Hermes → OpenAgents rebrand after merging Hermes Agent upstream.

Run from the repo root after ``git merge upstream/main``::

    python scripts/rename_to_openagents.py

Preserves upstream GitHub URLs (NousResearch/Hermes-agent), the Hermes tool-call
parser module, migration scripts, and plugin paths that intentionally keep
``hermes`` in the name.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "dist",
    "build",
    ".turbo",
}

SKIP_FILE_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".ico",
    ".pdf",
    ".woff",
    ".woff2",
    ".ttf",
    ".eot",
    ".zip",
    ".gz",
    ".tar",
    ".sqlite",
    ".db",
    ".pyc",
    ".so",
    ".dylib",
    ".dll",
    ".wasm",
    ".mp3",
    ".wav",
    ".mp4",
}

# Paths that must keep ``hermes`` in the filename or content semantics.
SKIP_PATH_PARTS = {
    "environments/tool_call_parsers/hermes_parser.py",
    "optional-skills/migration/openclaw-migration/scripts/openclaw_to_hermes.py",
    "scripts/rename_to_openagents.py",
    "scripts/sync_from_hermes.sh",
    "openagents_fork.py",
}

# Git renames applied before text substitution (source still uses Hermes names).
GIT_MV_MAP = [
    ("hermes_cli", "openagents_cli"),
    ("tests/hermes_cli", "tests/openagents_cli"),
    ("tests/hermes_state", "tests/openagents_state"),
    ("hermes_constants.py", "openagents_constants.py"),
    ("hermes_state.py", "openagents_state.py"),
    ("hermes_logging.py", "openagents_logging.py"),
    ("hermes_time.py", "openagents_time.py"),
    ("hermes_bootstrap.py", "openagents_bootstrap.py"),
    ("hermes", "openagents"),
    ("tests/test_hermes_constants.py", "tests/test_openagents_constants.py"),
    ("tests/test_hermes_state.py", "tests/test_openagents_state.py"),
    ("tests/test_hermes_logging.py", "tests/test_openagents_logging.py"),
    ("tests/test_hermes_bootstrap.py", "tests/test_openagents_bootstrap.py"),
    (
        "tests/test_hermes_state_wal_fallback.py",
        "tests/test_openagents_state_wal_fallback.py",
    ),
    (
        "tests/test_hermes_state_compression_locks.py",
        "tests/test_openagents_state_compression_locks.py",
    ),
    (
        "tests/test_hermes_home_profile_warning.py",
        "tests/test_openagents_home_profile_warning.py",
    ),
    ("environments/hermes_base_env.py", "environments/openagents_base_env.py"),
    ("environments/hermes_swe_env", "environments/openagents_swe_env"),
    ("setup-hermes.sh", "setup-openagents.sh"),
    ("hermes-already-has-routines.md", "openagents-already-has-routines.md"),
]

# Longest-first literal replacements (Hermes identity → OpenAgents).
TEXT_REPLACEMENTS = [
    ("get_hermes_home_override", "get_openagents_home_override"),
    ("set_hermes_home_override", "set_openagents_home_override"),
    ("reset_hermes_home_override", "reset_openagents_home_override"),
    ("_get_platform_default_hermes_home", "_get_platform_default_openagents_home"),
    ("get_default_hermes_root", "get_default_openagents_root"),
    ("display_hermes_home", "display_openagents_home"),
    ("load_hermes_dotenv", "load_openagents_dotenv"),
    ("get_hermes_dir", "get_openagents_dir"),
    ("get_hermes_home", "get_openagents_home"),
    ("_HERMES_HOME_OVERRIDE", "_OPENAGENTS_HOME_OVERRIDE"),
    ("HERMES_OPTIONAL_MCPS", "OPENAGENTS_OPTIONAL_MCPS"),
    ("HERMES_OPTIONAL_SKILLS", "OPENAGENTS_OPTIONAL_SKILLS"),
    ("HERMES_REVISION", "OPENAGENTS_REVISION"),
    ("HERMES_QUIET", "OPENAGENTS_QUIET"),
    ("Hermes_HOME", "OPENAGENTS_HOME"),
    ("HERMES_HOME", "OPENAGENTS_HOME"),
    ("hermes-agent", "openagents"),
    ("hermes_cli", "openagents_cli"),
    ("hermes_constants", "openagents_constants"),
    ("hermes_state", "openagents_state"),
    ("hermes_logging", "openagents_logging"),
    ("hermes_time", "openagents_time"),
    ("hermes_bootstrap", "openagents_bootstrap"),
    ("hermes_base_env", "openagents_base_env"),
    ("hermes_swe_env", "openagents_swe_env"),
    ("hermes-acp", "openagents-acp"),
    ("hermes-run", "openagents-run"),
    ("setup-hermes.sh", "setup-openagents.sh"),
    ("ai.hermes.", "ai.openagents."),
    ("hermes-gateway", "openagents-gateway"),
    ("Hermes-gateway", "OpenAgents-gateway"),
    ("~/.Hermes", "~/.openagents"),
    ("~/.hermes", "~/.openagents"),
    ("/.Hermes", "/.openagents"),
    ("/.hermes", "/.openagents"),
    (".Hermes/", ".openagents/"),
    (".hermes/", ".openagents/"),
    ("Hermes Agent", "OpenAgents"),
    ("Hermes Gateway", "OpenAgents Gateway"),
    ("Hermes gateway", "OpenAgents gateway"),
    ("Hermes CLI", "OpenAgents CLI"),
    ("Hermes setup", "OpenAgents setup"),
    ("Hermes update", "OpenAgents update"),
    ("Hermes doctor", "OpenAgents doctor"),
    ("Hermes model", "OpenAgents model"),
    ("Hermes config", "OpenAgents config"),
    ("Hermes tools", "OpenAgents tools"),
    ("Hermes skills", "OpenAgents skills"),
    ("Hermes cron", "OpenAgents cron"),
    ("Hermes backup", "OpenAgents backup"),
    ("Hermes uninstall", "OpenAgents uninstall"),
    ("Hermes version", "OpenAgents version"),
    ("Hermes profile", "OpenAgents profile"),
    ("Hermes dashboard", "OpenAgents dashboard"),
    ("Hermes web", "OpenAgents web"),
    ("Hermes claw", "OpenAgents claw"),
    ("Hermes honcho", "OpenAgents honcho"),
    ("Hermes debug", "OpenAgents debug"),
    ("Hermes sessions", "OpenAgents sessions"),
    ("Hermes plugins", "OpenAgents plugins"),
    ("Hermes mcp", "OpenAgents mcp"),
    ("Hermes chat", "OpenAgents chat"),
    ("Hermes status", "OpenAgents status"),
    ("Hermes import", "OpenAgents import"),
    ("`hermes update`", "`openagents update`"),
    ("`hermes doctor`", "`openagents doctor`"),
    ("`hermes model`", "`openagents model`"),
    ("`hermes gateway`", "`openagents gateway`"),
    ("`hermes setup`", "`openagents setup`"),
    ("``hermes update``", "``openagents update``"),
    ("``hermes doctor``", "``openagents doctor``"),
    ("``hermes model``", "``openagents model``"),
    ("``hermes gateway``", "``openagents gateway``"),
    ("``hermes setup``", "``openagents setup``"),
    ('"hermes-cli"', '"openagents"'),
    ('"hermes_cli"', '"openagents_cli"'),
    ("NousResearch/hermes-agent", "NousResearch/Hermes-agent"),  # normalize casing
]

PROTECTED_LITERALS = [
    "https://github.com/NousResearch/Hermes-agent",
    "git@github.com:NousResearch/Hermes-agent",
    "github.com/NousResearch/Hermes-agent",
    "NousResearch/Hermes-agent",
    "hermes_parser",
    "hermes-achievements",
    "hermes_tools_mcp",
    "openclaw_to_hermes",
    "Nous Hermes",
]

COMPAT_BLOCK = """

# ---------------------------------------------------------------------------
# Backward compatibility (Hermes → OpenAgents migration)
# ---------------------------------------------------------------------------

get_hermes_home = get_openagents_home
get_default_hermes_root = get_default_openagents_root
display_hermes_home = display_openagents_home
get_hermes_dir = get_openagents_dir
set_hermes_home_override = set_openagents_home_override
reset_hermes_home_override = reset_openagents_home_override
get_hermes_home_override = get_openagents_home_override
"""


def should_skip(path: Path) -> bool:
    rel = path.relative_to(ROOT).as_posix()
    if rel in SKIP_PATH_PARTS:
        return True
    if any(part.startswith(".!") for part in path.parts):
        return True
    if path.suffix.lower() in SKIP_FILE_SUFFIXES:
        return True
    for part in path.parts:
        if part in SKIP_DIRS:
            return True
    return False


def git_mv(old: str, new: str) -> None:
    old_path = ROOT / old
    new_path = ROOT / new
    if not old_path.exists():
        return
    if new_path.exists():
        print(f"skip mv (target exists): {old} -> {new}")
        return
    new_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "mv", old, new], cwd=ROOT, check=True)
    print(f"git mv {old} -> {new}")


def protect_upstream_urls(content: str) -> tuple[str, dict[str, str]]:
    tokens: dict[str, str] = {}
    for index, literal in enumerate(PROTECTED_LITERALS):
        token = f"__OPENAGENTS_PROTECT_{index}__"
        if literal in content:
            tokens[token] = literal
            content = content.replace(literal, token)
    return content, tokens


def restore_protected(content: str, tokens: dict[str, str]) -> str:
    for token, literal in tokens.items():
        content = content.replace(token, literal)
    return content


def patch_pyproject(content: str) -> str:
    content = content.replace('name = "hermes-agent"', 'name = "openagents"')
    content = content.replace('name = "openagents"', 'name = "openagents"')
    content = content.replace(
        'hermes = "hermes_cli.main:main"',
        'openagents = "openagents_cli.main:main"\nhermes = "openagents_cli.main:main"  # deprecated alias',
    )
    content = content.replace(
        'hermes-run = "run_agent:main"', 'openagents-run = "run_agent:main"'
    )
    content = content.replace(
        'hermes-acp = "acp_adapter.entry:main"',
        'openagents-acp = "acp_adapter.entry:main"',
    )
    content = content.replace('"hermes_cli"', '"openagents_cli"')
    content = content.replace('"hermes_cli.*"', '"openagents_cli.*"')
    content = content.replace("hermes_constants", "openagents_constants")
    content = content.replace("hermes_state", "openagents_state")
    content = content.replace("hermes_logging", "openagents_logging")
    content = content.replace("hermes_time", "openagents_time")
    if 'openagents = "openagents_cli.main:main"' not in content:
        content = re.sub(
            r'^openagents = "openagents_cli\.main:main"$',
            'openagents = "openagents_cli.main:main"\nhermes = "openagents_cli.main:main"  # deprecated alias',
            content,
            flags=re.MULTILINE,
        )
    return content


def patch_package_json(content: str) -> str:
    content = content.replace('"name": "hermes-agent"', '"name": "openagents"')
    return content


def apply_replacements(content: str, path: Path) -> str:
    content, tokens = protect_upstream_urls(content)
    for old, new in TEXT_REPLACEMENTS:
        content = content.replace(old, new)
    # User-facing CLI command (keep ``hermes`` only as deprecated alias in pyproject).
    content = re.sub(r"(?<![\w./-])\bhermes\b(?![\w-])", "openagents", content)
    content = restore_protected(content, tokens)
    rel = path.relative_to(ROOT).as_posix()
    if rel == "pyproject.toml":
        content = patch_pyproject(content)
    if rel == "package.json":
        content = patch_package_json(content)
    return content


def patch_constants_compat(path: Path) -> None:
    if path.name != "openagents_constants.py":
        return
    text = path.read_text(encoding="utf-8")
    if "get_hermes_home = get_openagents_home" in text:
        return
    if "def get_openagents_home" not in text:
        return
    text = text.rstrip() + COMPAT_BLOCK + "\n"
    path.write_text(text, encoding="utf-8")


def main() -> int:
    os.chdir(ROOT)

    for old, new in GIT_MV_MAP:
        git_mv(old, new)

    changed = 0
    for path in ROOT.rglob("*"):
        if not path.is_file() or should_skip(path):
            continue
        try:
            original = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        updated = apply_replacements(original, path)
        if updated != original:
            path.write_text(updated, encoding="utf-8")
            changed += 1

    patch_constants_compat(ROOT / "openagents_constants.py")
    print(f"updated {changed} text files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
