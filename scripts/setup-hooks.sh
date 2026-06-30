#!/usr/bin/env bash
# Enable git hooks for this repo (run once after clone).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

mkdir -p .husky
chmod +x .husky/pre-commit 2>/dev/null || true
chmod +x .husky/pre-push 2>/dev/null || true
chmod +x scripts/check-quality.sh 2>/dev/null || true

git config core.hooksPath .husky

echo "✓ Git hooks enabled (core.hooksPath=.husky)"
echo "  pre-commit: quality + type-check + tests"
echo "  pre-push:   type-check + build"
