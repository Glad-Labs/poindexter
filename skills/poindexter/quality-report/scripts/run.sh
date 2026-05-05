#!/bin/bash
# scripts/run.sh — Quality report for tasks that cleared the pipeline.
#
# The old "completed" status was removed in the 2026-04 refactor. Terminal
# states are now:
#   - awaiting_approval  (passed QA, in human review queue)
#   - published          (approved and live on R2)
#
# Both carry a quality_score populated by multi_model_qa after all reviewers
# (programmatic_validator, ollama_critic, topic_delivery, internal_consistency,
# image_relevance, rendered_preview) finish.

set -o pipefail

FASTAPI_URL="${FASTAPI_URL:-http://localhost:8002}"

# OAuth helper (Glad-Labs/poindexter#246).
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
. "${SCRIPT_DIR}/../../_lib/get_token.sh"
POINDEXTER_TOKEN="$(get_poindexter_token)" || exit 1

MODE="${1:-all}"
LIMIT="${2:-10}"

fetch_tasks() {
  local status="$1"
  local limit="$2"
  curl -s -w "\n%{http_code}" -X GET "${FASTAPI_URL}/api/tasks?status=${status}&limit=${limit}" \
    -H "Authorization: Bearer ${POINDEXTER_TOKEN}" \
    -H "Content-Type: application/json"
}

print_tasks() {
  local label="$1"
  local status="$2"
  local limit="$3"
  local resp http body
  resp=$(fetch_tasks "$status" "$limit")
  http=$(echo "$resp" | tail -1)
  body=$(echo "$resp" | sed '$d')

  if [ "$http" -ge 200 ] && [ "$http" -lt 300 ]; then
    echo "=== $label ($status) ==="
    echo "$body" | python -c "
import sys, json
try:
    d = json.load(sys.stdin)
except json.JSONDecodeError:
    print('(no JSON in response)')
    sys.exit(0)
tasks = d.get('tasks', []) if isinstance(d, dict) else []
if not tasks:
    print('(no tasks)')
for t in tasks:
    meta = t.get('metadata') or {}
    score = t.get('quality_score') or meta.get('quality_score', 'N/A')
    title = t.get('title') or t.get('task_name') or t.get('topic') or '?'
    print(f\"  {str(t.get('id','?'))[:8]}  Q:{score:<5}  {title[:72]}\")
" 2>/dev/null || echo "$body"
    echo ""
  else
    echo "Error fetching $status tasks: HTTP $http" >&2
    echo "$body" >&2
    return 1
  fi
}

case "$MODE" in
  awaiting)
    print_tasks "Awaiting Approval" "awaiting_approval" "$LIMIT"
    ;;
  published)
    print_tasks "Published" "published" "$LIMIT"
    ;;
  all|"")
    print_tasks "Awaiting Approval" "awaiting_approval" "$LIMIT"
    print_tasks "Published" "published" "$LIMIT"
    ;;
  *)
    echo "Usage: run.sh [awaiting|published|all] [limit]" >&2
    exit 1
    ;;
esac
