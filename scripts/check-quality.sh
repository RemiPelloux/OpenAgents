#!/usr/bin/env bash
# Pelloux quality gate — runs on staged files only.
# Each check is fail-fast; first violation aborts the commit.
# Portable to bash 3.2 (macOS).

set -uo pipefail

ROOT="$(git rev-parse --show-toplevel 2>/dev/null)"
[ -z "$ROOT" ] && ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

RED="\033[0;31m"
YELLOW="\033[0;33m"
GREEN="\033[0;32m"
RESET="\033[0m"

VIOLATIONS=0
WARNINGS=0
TMP="$(mktemp -t check-quality.XXXXXX)"
DUP_TMP="$(mktemp -t check-quality-dup.XXXXXX)"
trap 'rm -f "$TMP" "$DUP_TMP"' EXIT

fail() {
  printf "${RED}✗ %s${RESET}\n" "$1"
  VIOLATIONS=$((VIOLATIONS + 1))
}

warn() {
  printf "${YELLOW}⚠ %s${RESET}\n" "$1"
  WARNINGS=$((WARNINGS + 1))
}

ok() {
  printf "${GREEN}✓ %s${RESET}\n" "$1"
}

STAGED_ALL="$(git diff --cached --name-only --diff-filter=ACMR)"
STAGED_TSX="$(printf '%s\n' "$STAGED_ALL" | grep -E '\.tsx$' || true)"
STAGED_TS="$(printf '%s\n' "$STAGED_ALL" | grep -E '\.(ts|tsx)$' || true)"
STAGED_PY="$(printf '%s\n' "$STAGED_ALL" | grep -E '\.py$' || true)"
STAGED_LOCALES="$(printf '%s\n' "$STAGED_ALL" | grep -E 'i18n/locales/.*\.ts$' || true)"

is_exempt() {
  local file="$1"
  case "$file" in
    *.test.ts|*.test.tsx|*.spec.ts|*.spec.tsx) return 0 ;;
    */tests/*|*test_*.py|*_test.py) return 0 ;;
    */i18n/locales/*|*/i18n/types.ts) return 0 ;;
    */apiClient.ts|*/api-client.ts) return 0 ;;
    */logger.ts|*/service-worker.*) return 0 ;;
    *) return 1 ;;
  esac
}

# ─── 1. Hardcoded JSX attributes ───────────────────────────────────────
check_hardcoded_jsx() {
  local file
  while IFS= read -r file; do
    [ -z "$file" ] && continue
    is_exempt "$file" && continue
    [ ! -f "$file" ] && continue
    grep -nE '\b(aria-label|placeholder)="[^"{][^"]*"' "$file" 2>/dev/null \
      | grep -vE '(\btype|\brole|\bdata-testid|\balt|\bname)="' > "$TMP" || true
    if [ -s "$TMP" ]; then
      fail "Hardcoded JSX attribute in $file:"
      sed 's/^/  /' "$TMP"
    fi
  done < <(printf '%s\n' "$STAGED_TSX")
}

# ─── 2. console.* calls ────────────────────────────────────────────────
check_console() {
  local file
  while IFS= read -r file; do
    [ -z "$file" ] && continue
    is_exempt "$file" && continue
    [ ! -f "$file" ] && continue
    grep -nE '\bconsole\.(log|warn|error|info|debug)\(' "$file" 2>/dev/null > "$TMP" || true
    if [ -s "$TMP" ]; then
      fail "console.* call in $file (use logger):"
      sed 's/^/  /' "$TMP"
    fi
  done < <(printf '%s\n' "$STAGED_TS")
}

# ─── 3. Type escape hatches ────────────────────────────────────────────
check_type_escape() {
  local file
  while IFS= read -r file; do
    [ -z "$file" ] && continue
    is_exempt "$file" && continue
    [ ! -f "$file" ] && continue
    grep -nE '(\bas any\b|@ts-ignore|@ts-expect-error|: any[^a-zA-Z])' "$file" 2>/dev/null > "$TMP" || true
    if [ -s "$TMP" ]; then
      fail "Type escape hatch in $file:"
      sed 's/^/  /' "$TMP"
    fi
  done < <(printf '%s\n' "$STAGED_TS")
}

# ─── 4. File size limits ───────────────────────────────────────────────
check_file_size() {
  local warn_threshold=300
  local hard_threshold=400
  local file lines
  while IFS= read -r file; do
    [ -z "$file" ] && continue
    is_exempt "$file" && continue
    [ ! -f "$file" ] && continue
    lines=$(wc -l < "$file" | tr -d ' ')
    if [ "$lines" -gt "$hard_threshold" ]; then
      fail "$file is $lines lines (hard limit $hard_threshold). Split it."
    elif [ "$lines" -gt "$warn_threshold" ]; then
      warn "$file is $lines lines (warn $warn_threshold)."
    fi
  done < <(printf '%s\n%s\n' "$STAGED_TS" "$STAGED_PY")
}

# ─── 5. Raw axios imports ──────────────────────────────────────────────
check_raw_axios() {
  local file
  while IFS= read -r file; do
    [ -z "$file" ] && continue
    is_exempt "$file" && continue
    [ ! -f "$file" ] && continue
    grep -nE "^import .* from ['\"]axios['\"]" "$file" 2>/dev/null > "$TMP" || true
    if [ -s "$TMP" ]; then
      fail "Raw axios import in $file (use apiClient):"
      sed 's/^/  /' "$TMP"
    fi
  done < <(printf '%s\n' "$STAGED_TS")
}

# ─── 6. Language-dependent ternaries ───────────────────────────────────
check_lang_ternary() {
  local file
  while IFS= read -r file; do
    [ -z "$file" ] && continue
    is_exempt "$file" && continue
    [ ! -f "$file" ] && continue
    grep -nE "(currentLanguage|\\blanguage\\b|\\blang\\b)[[:space:]]*===[[:space:]]*['\"](fr|en|es|de)['\"][[:space:]]*\\?" "$file" 2>/dev/null > "$TMP" || true
    if [ -s "$TMP" ]; then
      fail "Language-dependent ternary in $file (use t()):"
      sed 's/^/  /' "$TMP"
    fi
  done < <(printf '%s\n' "$STAGED_TS")
}

# ─── 7. Duplicate locale keys ──────────────────────────────────────────
check_duplicate_locale_keys() {
  local file
  while IFS= read -r file; do
    [ -z "$file" ] && continue
    [ ! -f "$file" ] && continue
    awk -F'"' '/^[[:space:]]*"[^"]+":/ {print $2}' "$file" | sort | uniq -d > "$TMP"
    if [ -s "$TMP" ]; then
      fail "Duplicate locale keys in $file:"
      sed 's/^/  /' "$TMP"
    fi
  done < <(printf '%s\n' "$STAGED_LOCALES")
}

# ─── 8. Underscore-prefixed unused props (warning) ─────────────────────
check_unused_underscore_props() {
  local file
  while IFS= read -r file; do
    [ -z "$file" ] && continue
    is_exempt "$file" && continue
    [ ! -f "$file" ] && continue
    grep -nE '\{[[:space:]]*[a-zA-Z]+:[[:space:]]*_[a-zA-Z]' "$file" 2>/dev/null > "$TMP" || true
    if [ -s "$TMP" ]; then
      warn "Underscore-prefixed prop in $file (likely dead code):"
      sed 's/^/  /' "$TMP"
    fi
  done < <(printf '%s\n' "$STAGED_TSX")
}

# ─── 9. Duplicate function bodies across staged files (DRY heuristic) ──
check_duplication() {
  [ -z "$STAGED_TS" ] && return
  > "$DUP_TMP"
  local file
  while IFS= read -r file; do
    [ -z "$file" ] && continue
    is_exempt "$file" && continue
    [ ! -f "$file" ] && continue
    awk -v F="$file" '
      /^(export[[:space:]]+)?(async[[:space:]]+)?function[[:space:]]+/ {
        sig=$0
        getline next1
        getline next2
        body = next1 next2
        gsub(/[[:space:]]+/, " ", body)
        if (length(body) > 60) print body "\t" F ":" NR-2 ":" sig
      }
    ' "$file" >> "$DUP_TMP" 2>/dev/null || true
  done < <(printf '%s\n' "$STAGED_TS")

  if [ -s "$DUP_TMP" ]; then
    sort "$DUP_TMP" | awk -F'\t' '
      prev==$1 { print "  " prevloc " ⇄ " $2 }
      { prev=$1; prevloc=$2 }
    ' > "$TMP" || true
    if [ -s "$TMP" ]; then
      warn "Possible code duplication (extract shared helper):"
      cat "$TMP"
    fi
  fi
}

# ─── 10. Long functions (>25 lines, warning) ───────────────────────────
check_long_functions() {
  local file
  while IFS= read -r file; do
    [ -z "$file" ] && continue
    is_exempt "$file" && continue
    [ ! -f "$file" ] && continue
    awk '
      function emit(   len) {
        if (start) {
          len = NR - start
          if (len > 25) print FILENAME ":" start ": " name " is " len " lines"
        }
      }
      /^(export[[:space:]]+)?(async[[:space:]]+)?(function|def)[[:space:]]+[a-zA-Z_]/ {
        emit()
        start=NR; name=$0; depth=0
      }
      { for (i=1;i<=gsub(/\{/,"{");i++) depth++ }
      { for (i=1;i<=gsub(/\}/,"}");i++) depth-- }
      depth==0 && start && NR>start { emit(); start=0 }
      END { emit() }
    ' "$file" 2>/dev/null > "$TMP" || true
    if [ -s "$TMP" ]; then
      local count
      count=$(wc -l < "$TMP" | tr -d ' ')
      warn "$count long function(s) in $file (>25 lines, first 3):"
      head -3 "$TMP" | sed 's/^/  /'
    fi
  done < <(printf '%s\n%s\n' "$STAGED_TS" "$STAGED_PY")
}

echo "─── Pelloux quality gate ───"
check_hardcoded_jsx
check_console
check_type_escape
check_file_size
check_raw_axios
check_lang_ternary
check_duplicate_locale_keys
check_unused_underscore_props
check_duplication
check_long_functions

if [ "$VIOLATIONS" -gt 0 ]; then
  printf "${RED}✗ %d violation(s), %d warning(s) — commit blocked${RESET}\n" "$VIOLATIONS" "$WARNINGS"
  echo "Bypass with --no-verify only for genuine emergencies."
  exit 1
fi

if [ "$WARNINGS" -gt 0 ]; then
  printf "${YELLOW}⚠ %d warning(s) — commit allowed${RESET}\n" "$WARNINGS"
else
  ok "Quality gate passed"
fi
exit 0
