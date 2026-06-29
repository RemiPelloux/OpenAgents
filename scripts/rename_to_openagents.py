#!/usr/bin/env python3
"""One-shot OpenAgents → OpenAgents rename for this fork.

Renames packages/modules, updates imports, and refreshes user-facing identifiers.
Preserves upstream GitHub URLs and the OpenAgents tool-call parser module name.
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
    ".lock",  # uv.lock updated separately if needed
}

GIT_MV_MAP = [
    ("openagents_cli", "openagents_cli"),
    ("tests/openagents_cli", "tests/openagents_cli"),
    ("openagents_constants.py", "openagents_constants.py"),
    ("openagents_state.py", "openagents_state.py"),
    ("openagents_logging.py", "openagents_logging.py"),
    ("openagents_time.py", "openagents_time.py"),
    ("tests/test_openagents_constants.py", "tests/test_openagents_constants.py"),
    ("tests/test_openagents_state.py", "tests/test_openagents_state.py"),
    ("tests/test_openagents_logging.py", "tests/test_openagents_logging.py"),
    ("tests/test_openagents_bootstrap.py", "tests/test_openagents_bootstrap.py"),
    ("tests/test_openagents_state_wal_fallback.py", "tests/test_openagents_state_wal_fallback.py"),
    ("tests/test_hermes_home_profile_warning.py", "tests/test_openagents_home_profile_warning.py"),
    ("environments/openagents_base_env.py", "environments/openagents_base_env.py"),
    ("environments/openagents_swe_env", "environments/openagents_swe_env"),
]

# Longest-first literal replacements inside text files.
TEXT_REPLACEMENTS = [
    ("get_openagents_home_override", "get_openagents_home_override"),
    ("set_openagents_home_override", "set_openagents_home_override"),
    ("reset_openagents_home_override", "reset_openagents_home_override"),
    ("_get_platform_default_openagents_home", "_get_platform_default_openagents_home"),
    ("get_default_openagents_root", "get_default_openagents_root"),
    ("display_openagents_home", "display_openagents_home"),
    ("load_openagents_dotenv", "load_openagents_dotenv"),
    ("get_openagents_dir", "get_openagents_dir"),
    ("get_openagents_home", "get_openagents_home"),
    ("_OPENAGENTS_HOME_OVERRIDE", "_OPENAGENTS_HOME_OVERRIDE"),
    ("OPENAGENTS_OPTIONAL_MCPS", "OPENAGENTS_OPTIONAL_MCPS"),
    ("OPENAGENTS_OPTIONAL_SKILLS", "OPENAGENTS_OPTIONAL_SKILLS"),
    ("OPENAGENTS_QUIET", "OPENAGENTS_QUIET"),
    ("OPENAGENTS_HOME", "OPENAGENTS_HOME"),
    ("openagents_cli", "openagents_cli"),
    ("openagents_constants", "openagents_constants"),
    ("openagents_state", "openagents_state"),
    ("openagents_logging", "openagents_logging"),
    ("openagents_time", "openagents_time"),
    ("openagents_base_env", "openagents_base_env"),
    ("openagents_swe_env", "openagents_swe_env"),
    ("openagents-acp", "openagents-acp"),
    ("openagents", "openagents"),
    ("OpenAgents", "OpenAgents"),
    ("OpenAgents", "OpenAgents"),
    ("OpenAgents CLI", "OpenAgents CLI"),
    ("OpenAgents Gateway", "OpenAgents Gateway"),
    ("OpenAgents gateway", "OpenAgents gateway"),
    ("~/.openagents", "~/.openagents"),
    ("~/.openagents", "~/.openagents"),
    (".openagents/", ".openagents/"),
    ("/.openagents", "/.openagents"),
    ("ai.openagents.", "ai.openagents."),
    ("openagents-gateway", "openagents-gateway"),
    ("openagents-gateway", "openagents-gateway"),
    ("OpenAgents setup", "OpenAgents setup"),
    ("OpenAgents update", "OpenAgents update"),
    ("OpenAgents doctor", "OpenAgents doctor"),
    ("OpenAgents model", "OpenAgents model"),
    ("OpenAgents config", "OpenAgents config"),
    ("OpenAgents tools", "OpenAgents tools"),
    ("OpenAgents skills", "OpenAgents skills"),
    ("OpenAgents gateway", "OpenAgents gateway"),
    ("OpenAgents cron", "OpenAgents cron"),
    ("OpenAgents backup", "OpenAgents backup"),
    ("OpenAgents uninstall", "OpenAgents uninstall"),
    ("OpenAgents version", "OpenAgents version"),
    ("OpenAgents profile", "OpenAgents profile"),
    ("OpenAgents dashboard", "OpenAgents dashboard"),
    ("OpenAgents web", "OpenAgents web"),
    ("OpenAgents claw", "OpenAgents claw"),
    ("OpenAgents honcho", "OpenAgents honcho"),
    ("OpenAgents debug", "OpenAgents debug"),
    ("OpenAgents sessions", "OpenAgents sessions"),
    ("OpenAgents plugins", "OpenAgents plugins"),
    ("OpenAgents mcp", "OpenAgents mcp"),
    ("OpenAgents chat", "OpenAgents chat"),
    ("OpenAgents status", "OpenAgents status"),
    ("OpenAgents import", "OpenAgents import"),
    ("OpenAgents -p", "OpenAgents -p"),
    ("OpenAgents -c", "OpenAgents -c"),
    ("OpenAgents -w", "OpenAgents -w"),
    ("OpenAgents --", "OpenAgents --"),
    ("`openagents`", "`openagents`"),
    (" OpenAgents ", " OpenAgents "),
    ("\nHermes ", "\nOpenAgents "),
    ("# OpenAgents", "# OpenAgents"),
    ('"openagents"', '"openagents"'),
    ("'openagents'", "'openagents'"),
    ("openagents = ", "openagents = "),
    ('include = ["agent", "tools", "tools.*", "openagents_cli"', 'include = ["agent", "tools", "tools.*", "openagents_cli"'),
    ("py-modules = [", "py-modules = ["),  # placeholder — patched below
]

PYPROJECT_PATCHES = {
    'name = "openagents"': 'name = "openagents"',
    'openagents_cli = ["web_dist/**/*"': 'openagents_cli = ["web_dist/**/*"',
    '"openagents[': '"openagents[',
    "openagents[": "openagents[",
    'openagents = "openagents_cli.main:main"': 'openagents = "openagents_cli.main:main"',
    'openagents = "run_agent:main"': 'openagents-run = "run_agent:main"',
    'openagents-acp = "acp_adapter.entry:main"': 'openagents-acp = "acp_adapter.entry:main"',
    '"openagents_cli"': '"openagents_cli"',
    '"openagents_cli.*"': '"openagents_cli.*"',
    "real_concurrent_gate: opt out of the autouse stub that disables _detect_concurrent_hermes_instances":
        "real_concurrent_gate: opt out of the autouse stub that disables _detect_concurrent_openagents_instances",
    "openagents_cli/main.py": "openagents_cli/main.py",
    "openagents_logging.py": "openagents_logging.py",
    "openagents_constants.py": "openagents_constants.py",
    "openagents_state.py": "openagents_state.py",
    "openagents_time.py": "openagents_time.py",
    "setup-hermes.sh": "setup-openagents.sh",
}

PACKAGE_JSON_PATCHES = {
    '"name": "openagents"': '"name": "openagents"',
    "NousResearch/Hermes-agent": "NousResearch/Hermes-agent",  # keep upstream URL
}

SKIP_PATH_PARTS = {
    "environments/tool_call_parsers/hermes_parser.py",
    "optional-skills/migration/openclaw-migration/scripts/openclaw_to_hermes.py",
}


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


def apply_replacements(content: str, path: Path) -> str:
    rel = path.relative_to(ROOT).as_posix()
    for old, new in TEXT_REPLACEMENTS:
        content = content.replace(old, new)
    if rel == "pyproject.toml":
        for old, new in PYPROJECT_PATCHES.items():
            content = content.replace(old, new)
        content = content.replace(
            'py-modules = ["run_agent", "model_tools", "toolsets", "batch_runner",',
            'py-modules = ["run_agent", "model_tools", "toolsets", "batch_runner",',
        )
        content = re.sub(
            r'\bopenagents_constants\b', "openagents_constants", content
        )
        content = re.sub(
            r'\bopenagents_state\b', "openagents_state", content
        )
        content = re.sub(
            r'\bopenagents_logging\b', "openagents_logging", content
        )
        content = re.sub(
            r'\bopenagents_time\b', "openagents_time", content
        )
    if rel == "package.json":
        for old, new in PACKAGE_JSON_PATCHES.items():
            content = content.replace(old, new)
    return content


def patch_constants_compat(path: Path) -> None:
    if path.name != "openagents_constants.py":
        return
    text = path.read_text(encoding="utf-8")
    if "def get_openagents_home" not in text:
        return
    compat = '''

# ---------------------------------------------------------------------------
# Backward compatibility (Hermes → OpenAgents migration)
# ---------------------------------------------------------------------------

get_openagents_home = get_openagents_home
get_default_openagents_root = get_default_openagents_root
display_openagents_home = display_openagents_home
get_openagents_dir = get_openagents_dir
set_openagents_home_override = set_openagents_home_override
reset_openagents_home_override = reset_openagents_home_override
get_openagents_home_override = get_openagents_home_override
'''
    if "get_openagents_home = get_openagents_home" not in text:
        text = text.rstrip() + compat + "\n"
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
    print(f"updated {changed} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
