#!/usr/bin/env bash
# Global OpenAgents launcher — no venv activation required.
# Linked from ~/.local/bin/openagents by scripts/install-local.sh
set -euo pipefail

resolve_root() {
  if [[ -n "${OPENAGENTS_SOURCE_ROOT:-}" && -x "${OPENAGENTS_SOURCE_ROOT}/venv/bin/openagents" ]]; then
    printf '%s\n' "$OPENAGENTS_SOURCE_ROOT"
    return 0
  fi
  local marker="${HOME}/.openagents/source-install-root"
  if [[ -f "$marker" ]]; then
    local recorded
    recorded="$(tr -d '\n' <"$marker")"
    if [[ -n "$recorded" && -x "$recorded/venv/bin/openagents" ]]; then
      printf '%s\n' "$recorded"
      return 0
    fi
  fi
  return 1
}

ROOT="$(resolve_root)" || {
  echo "OpenAgents source install not found." >&2
  echo "Run: cd OpenAgents && ./scripts/install-local.sh" >&2
  exit 1
}

# Centralize bytecode cache (faster repeat CLI starts; keeps source tree clean)
export PYTHONPYCACHEPREFIX="${PYTHONPYCACHEPREFIX:-${HOME}/.openagents/cache/pycache}"
mkdir -p "$PYTHONPYCACHEPREFIX"

exec "$ROOT/venv/bin/openagents" "$@"
