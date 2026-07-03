#!/usr/bin/env bash
# OpenAgents W8 creative mesh E2E — Orch dispatch with goal_class content.creative + optional agent.run trace.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
# shellcheck source=mesh-orch-api.sh
source "$ROOT/scripts/mesh-orch-api.sh"
# shellcheck source=w8-e2e-common.sh
source "$ROOT/OpenBrain/scripts/w8-e2e-common.sh"

ORCH="${ORCHESTRATOR_URL:-http://localhost:3050}"
AGENTS="${OPENAGENTS_API_URL:-http://localhost:8080}"
OPENREC_URL="${OPENREC_URL:-http://localhost:3030}"
CORR="$(uuidgen | tr '[:upper:]' '[:lower:]')"

orch_curl --max-time 5 "$ORCH/v1/health" >/dev/null || w8_fail_or_skip "OpenOrchestrator not reachable at $ORCH"

echo "==> CC-ORCH-004: POST /v1/goals content.creative (OpenBrain source_app)"
TOKEN="$(_orch_bearer || true)"
CURL_ARGS=(-s -w '%{http_code}' -o /tmp/w8-agents-goal.json -X POST "$ORCH/v1/goals"
  -H 'Content-Type: application/json' -H "X-Correlation-Id: $CORR" -H 'X-OpenOS-Plan-Mode: auto')
if [[ -n "$TOKEN" ]]; then CURL_ARGS+=(-H "Authorization: Bearer $TOKEN"); fi
PAYLOAD=$(python3 -c "import json; print(json.dumps({
  'objective': 'Create 2 Instagram images for summer skincare launch',
  'correlation_id': '$CORR',
  'source_app': 'OpenBrain',
  'goal_class': 'content.creative',
  'approval_required': False,
  'brief': {'platform': 'instagram', 'asset_count': 2, 'subject': 'skincare summer launch'},
  'steps': [{
    'goal': 'Generate Instagram image assets',
    'required_skills': ['developer'],
    'depends_on': [],
    'priority': 'high',
  }],
  'secret_refs': [],
}))")
CURL_ARGS+=(-d "$PAYLOAD")
HTTP=$(curl "${CURL_ARGS[@]}" || true)
BODY=$(cat /tmp/w8-agents-goal.json)

[[ "$HTTP" == "201" || "$HTTP" == "200" ]] || {
  echo "FAIL: POST /v1/goals HTTP $HTTP — $BODY" >&2
  exit 1
}

PLAN_ID=$(echo "$BODY" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('plan_id') or (d.get('plan') or {}).get('id',''))")
echo "OK: plan_id=$PLAN_ID correlation=$CORR"

if curl -sf --max-time 3 "$AGENTS/v1/health" >/dev/null 2>&1; then
  echo "==> Poll OpenAgents task dispatch"
  attempts=0
  while [[ "$attempts" -lt 15 ]]; do
    w8_drain_outboxes
    if curl -sf "$OPENREC_URL/v1/traces/$CORR" 2>/dev/null | python3 -c "
import json, sys
events = json.load(sys.stdin).get('events', [])
types = {e.get('type') or e.get('event_type') for e in events}
sys.exit(0 if any(t and t.startswith('agent.run') for t in types) else 1)
" 2>/dev/null; then
      echo "OK: agent.run Rec events for $CORR"
      break
    fi
    TASK_STATUS=$(orch_curl --max-time 5 "$ORCH/v1/plans/$PLAN_ID/tasks" 2>/dev/null | python3 -c "
import json, sys
tasks = json.load(sys.stdin).get('tasks', [])
statuses = {t.get('status') for t in tasks}
print('running' if 'running' in statuses or 'completed' in statuses else 'pending')
" 2>/dev/null || echo "pending")
    if [[ "$TASK_STATUS" == "running" ]]; then
      echo "OK: task running on plan $PLAN_ID"
      break
    fi
    sleep 2
    attempts=$((attempts + 1))
  done
  if [[ "$attempts" -ge 15 ]]; then
    if w8_is_strict && [[ "${W8_REQUIRE_AGENTS_TRACE:-0}" == "1" ]]; then
      echo "FAIL: no agent.run events or running task for $CORR" >&2
      exit 1
    fi
    echo "WARN: OpenAgents dispatch not confirmed (gateway may be offline)"
  fi
else
  echo "WARN: OpenAgents API not reachable at $AGENTS — Orch plan created only"
fi

w8_drain_outboxes
w8_assert_rec_trace "$CORR" 2
echo "w8-creative-mesh-e2e: OK"
