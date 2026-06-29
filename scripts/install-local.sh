#!/usr/bin/env bash
# Local dev install for OpenAgents (macOS/Linux) — Pelloux fork defaults.
#
# Usage:
#   ./scripts/install-local.sh              # reuse venv, install [all], link CLI
#   ./scripts/install-local.sh --link-only  # refresh ~/.local/bin links only
#   ./scripts/install-local.sh --recreate     # rebuild venv from scratch
#   ./scripts/install-local.sh --dev          # include dev extras (pytest, ruff, …)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PYTHON="${PYTHON:-python3.11}"
LINK_ONLY=false
RECREATE=false
INSTALL_DEV=false

for arg in "$@"; do
  case "$arg" in
    --link-only) LINK_ONLY=true ;;
    --recreate) RECREATE=true ;;
    --dev) INSTALL_DEV=true ;;
    -h|--help)
      sed -n '2,8p' "$0"
      exit 0
      ;;
    *)
      echo "Unknown option: $arg" >&2
      exit 2
      ;;
  esac
done

LOCAL_BIN="${HOME}/.local/bin"
LAUNCHER="$ROOT/scripts/openagents-launch.sh"
MARKER="${HOME}/.openagents/source-install-root"

link_cli_commands() {
  mkdir -p "$LOCAL_BIN"
  chmod +x "$LAUNCHER"
  ln -sf "$LAUNCHER" "$LOCAL_BIN/openagents"
  ln -sf "$LAUNCHER" "$LOCAL_BIN/hermes"
  echo "  ✓ $LOCAL_BIN/openagents → openagents-launch.sh"
  echo "  ✓ $LOCAL_BIN/hermes → openagents-launch.sh"
  for name in openagents-run openagents-acp; do
    target="$ROOT/venv/bin/$name"
    if [[ -x "$target" ]]; then
      ln -sf "$target" "$LOCAL_BIN/$name"
      echo "  ✓ $LOCAL_BIN/$name"
    fi
  done
  mkdir -p "$HOME/.openagents"
  printf '%s\n' "$ROOT" >"$MARKER"
}

ensure_path_hint() {
  if [[ ":$PATH:" == *":$LOCAL_BIN:"* ]]; then
    return 0
  fi
  echo ""
  echo "⚠  $LOCAL_BIN is not on your PATH yet."
  echo "   Add to ~/.zshrc or ~/.bash_profile:"
  echo '   export PATH="$HOME/.local/bin:$PATH"'
}

write_default_config() {
  mkdir -p "$HOME/.openagents"
  if [[ -f "$HOME/.openagents/config.yaml" ]]; then
    return 0
  fi
  cat >"$HOME/.openagents/config.yaml" <<'EOF'
# OpenAgents — Pelloux fork defaults (scripts/install-local.sh)
display:
  skin: opencode
model:
  provider: auto
updates:
  pre_update_backup: false
EOF
}

if ! command -v "$PYTHON" >/dev/null 2>&1; then
  PYTHON=python3
fi

if $LINK_ONLY; then
  echo "→ Refreshing global CLI links..."
  link_cli_commands
  ensure_path_hint
  echo "✓ CLI links updated."
  exit 0
fi

if $RECREATE && [[ -d venv ]]; then
  echo "→ Removing existing venv..."
  rm -rf venv
fi

EXTRAS="all"
if $INSTALL_DEV; then
  EXTRAS="all,dev"
fi

if [[ -x venv/bin/python && -x venv/bin/openagents ]]; then
  echo "→ Reusing existing venv (use --recreate to rebuild)..."
  # shellcheck disable=SC1091
  source venv/bin/activate
else
  echo "→ Creating venv with $PYTHON..."
  if command -v uv >/dev/null 2>&1; then
    uv venv venv --python "$PYTHON"
  else
    "$PYTHON" -m venv venv
  fi
  # shellcheck disable=SC1091
  source venv/bin/activate
fi

echo "→ Installing OpenAgents (.[${EXTRAS}])..."
export UV_COMPILE_BYTECODE=1
if command -v uv >/dev/null 2>&1; then
  uv pip install -e ".[${EXTRAS}]"
else
  pip install -U pip wheel
  pip install -e ".[${EXTRAS}]"
fi

echo "→ Linking global CLI (no source venv/bin/activate)..."
link_cli_commands
write_default_config
ensure_path_hint

echo ""
echo "✓ Install complete — run from any directory:"
echo ""
echo "  openagents --version"
echo "  openagents doctor"
echo "  openagents auth add openai-codex"
echo "  openagents model"
echo "  openagents"
