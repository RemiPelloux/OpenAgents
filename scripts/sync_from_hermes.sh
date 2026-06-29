#!/usr/bin/env bash
# Merge latest Hermes Agent upstream and re-apply the OpenAgents rebrand.
#
# Maintainer workflow (run from repo root):
#   ./scripts/sync_from_hermes.sh
#   ./scripts/sync_from_hermes.sh --push
#
# End users should NOT run this — they use: openagents update
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

HERMES_UPSTREAM="${HERMES_UPSTREAM:-https://github.com/NousResearch/Hermes-agent.git}"
PUSH=false
RUN_TESTS=true

for arg in "$@"; do
  case "$arg" in
    --push) PUSH=true ;;
    --no-tests) RUN_TESTS=false ;;
    -h|--help)
      cat <<'EOF'
Usage: ./scripts/sync_from_hermes.sh [--push] [--no-tests]

  1. fetch NousResearch/Hermes-agent (upstream)
  2. merge upstream/main into the current branch
  3. run scripts/rename_to_openagents.py
  4. refresh uv.lock
  5. optional smoke tests

Options:
  --push       push current branch to origin after a successful sync
  --no-tests   skip pytest smoke checks
EOF
      exit 0
      ;;
    *)
      echo "Unknown option: $arg" >&2
      exit 2
      ;;
  esac
done

if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "✗ Working tree is dirty. Commit or stash changes before syncing." >&2
  exit 1
fi

if git remote get-url upstream >/dev/null 2>&1; then
  current="$(git remote get-url upstream)"
  if [[ "$current" != *Hermes-agent* ]]; then
    echo "⚠ upstream remote is: $current"
    echo "  Expected Hermes-agent. Set HERMES_UPSTREAM or fix the remote."
    exit 1
  fi
else
  echo "→ Adding upstream remote ($HERMES_UPSTREAM)..."
  git remote add upstream "$HERMES_UPSTREAM"
fi

echo "→ Fetching upstream/main..."
git fetch upstream main

echo "→ Merging upstream/main..."
if ! git merge --no-edit upstream/main; then
  echo "✗ Merge conflicts. Resolve them, then run:" >&2
  echo "    python scripts/rename_to_openagents.py" >&2
  exit 1
fi

echo "→ Applying OpenAgents rebrand..."
python3 scripts/rename_to_openagents.py

if command -v uv >/dev/null 2>&1; then
  echo "→ Refreshing uv.lock..."
  uv lock
fi

if $RUN_TESTS; then
  echo "→ Smoke tests..."
  if [[ -f venv/bin/activate ]]; then
    # shellcheck disable=SC1091
    source venv/bin/activate
  fi
  python3 -m pytest tests/test_openagents_constants.py tests/openagents_cli/test_update_check.py -q
fi

echo ""
echo "✓ Sync complete."
echo "  Review: git status"
echo "  Commit: git commit -am 'Sync Hermes upstream and reapply OpenAgents rebrand.'"
if $PUSH; then
  branch="$(git branch --show-current)"
  echo "→ Pushing to origin/$branch..."
  git push origin "$branch"
fi
