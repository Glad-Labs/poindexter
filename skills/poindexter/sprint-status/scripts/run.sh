#!/bin/bash
# scripts/run.sh — Gitea sprint status (issues + milestones).
#
# Source of truth is the self-hosted Gitea at localhost:3001. GitHub is a
# deployment mirror only — do not point this script at github.com.
#
# Repo defaults to gladlabs/glad-labs-codebase. Override with GITEA_REPO env var
# if you need to point at a different repo temporarily.

set -euo pipefail

GITEA_URL="${GITEA_URL:-http://localhost:3001}"
GITEA_REPO="${GITEA_REPO:-gladlabs/glad-labs-codebase}"
GITEA_TOKEN="${GITEA_TOKEN:-}"

ACTION="${1:-overview}"

# Build auth header once. Gitea's issue API can return private issues only with
# a valid token; without one, some repos 401 and some return empty lists.
if [ -n "$GITEA_TOKEN" ]; then
  AUTH_HEADER="Authorization: token $GITEA_TOKEN"
else
  AUTH_HEADER="X-No-Auth: 1"  # placeholder so curl -H arg stays valid
  echo "(warning: GITEA_TOKEN not set; some reads may fail)" >&2
fi

api() {
  local path="$1"
  curl -s -H "$AUTH_HEADER" -H "Accept: application/json" "${GITEA_URL}${path}"
}

print_milestones() {
  echo "--- Milestones ---"
  api "/api/v1/repos/${GITEA_REPO}/milestones?state=open&limit=20" | python -c "
import sys, json
try:
    rows = json.load(sys.stdin)
except json.JSONDecodeError:
    print('(no JSON in response — check GITEA_TOKEN)')
    sys.exit(0)
if not isinstance(rows, list) or not rows:
    print('(none)')
    sys.exit(0)
for m in rows:
    due = m.get('due_on') or 'no date'
    print(f\"[{m.get('title','?')}] open={m.get('open_issues',0)} closed={m.get('closed_issues',0)} due={due}\")
" 2>/dev/null
  echo ""
}

print_open_issues() {
  echo "--- Open Issues ---"
  api "/api/v1/repos/${GITEA_REPO}/issues?state=open&type=issues&limit=50" | python -c "
import sys, json
try:
    rows = json.load(sys.stdin)
except json.JSONDecodeError:
    print('(no JSON in response — check GITEA_TOKEN)')
    sys.exit(0)
if not isinstance(rows, list) or not rows:
    print('(none)')
    sys.exit(0)
for i in rows:
    ms = (i.get('milestone') or {}).get('title') or 'no milestone'
    print(f\"#{i.get('number')} {i.get('title','')[:80]} [{ms}]\")
" 2>/dev/null
  echo ""
}

print_recent_closed() {
  echo "--- Closed in the last 7 days ---"
  # Gitea API doesn't have a since filter on closed issues directly, so we pull
  # recent closed and filter client-side by closed_at.
  api "/api/v1/repos/${GITEA_REPO}/issues?state=closed&type=issues&limit=50&sort=newest" | python -c "
import sys, json, datetime
try:
    rows = json.load(sys.stdin)
except json.JSONDecodeError:
    print('(no JSON in response — check GITEA_TOKEN)')
    sys.exit(0)
if not isinstance(rows, list) or not rows:
    print('(none)')
    sys.exit(0)
cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=7)
shown = 0
for i in rows:
    closed_at = i.get('closed_at')
    if not closed_at:
        continue
    try:
        dt = datetime.datetime.fromisoformat(closed_at.replace('Z', '+00:00'))
    except ValueError:
        continue
    if dt >= cutoff:
        print(f\"#{i.get('number')} {i.get('title','')[:80]}\")
        shown += 1
if shown == 0:
    print('(none in the last 7 days)')
" 2>/dev/null
  echo ""
}

case "$ACTION" in
  overview|"")
    echo "=== SPRINT STATUS — ${GITEA_REPO} ==="
    echo ""
    print_milestones
    print_open_issues
    print_recent_closed
    ;;
  issues)
    echo "=== OPEN ISSUES — ${GITEA_REPO} ==="
    print_open_issues
    ;;
  milestones)
    echo "=== MILESTONES — ${GITEA_REPO} ==="
    print_milestones
    ;;
  recent)
    echo "=== RECENTLY CLOSED — ${GITEA_REPO} ==="
    print_recent_closed
    ;;
  *)
    echo "Usage: run.sh [overview|issues|milestones|recent]" >&2
    exit 1
    ;;
esac
