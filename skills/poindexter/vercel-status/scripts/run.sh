#!/bin/bash
# scripts/run.sh — Vercel status via the REST API.
#
# Does NOT call the vercel CLI. The CLI may or may not be installed in the
# openclaw container; this skill works anywhere curl + python exist. Project
# and team IDs come from .vercel/project.json at repo root. Auth is via
# VERCEL_TOKEN env var (fails loud if unset — no anonymous fallback).

set -euo pipefail

VERCEL_API="https://api.vercel.com"
ACTION="${1:-overview}"
LIMIT_ARG="${2:-5}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"
PROJECT_JSON="${REPO_ROOT}/.vercel/project.json"

if [ ! -f "$PROJECT_JSON" ]; then
  echo "Error: $PROJECT_JSON not found. Run 'vercel link' once to create it." >&2
  exit 1
fi

PROJECT_ID=$(python -c "import json,sys; print(json.load(open(sys.argv[1]))['projectId'])" "$PROJECT_JSON")
TEAM_ID=$(python -c "import json,sys; print(json.load(open(sys.argv[1]))['orgId'])" "$PROJECT_JSON")

if [ -z "${VERCEL_TOKEN:-}" ]; then
  echo "Error: VERCEL_TOKEN env var is not set." >&2
  echo "Generate at https://vercel.com/account/settings/tokens and export it." >&2
  exit 1
fi

api() {
  local path="$1"
  curl -s -w "\n%{http_code}" -H "Authorization: Bearer ${VERCEL_TOKEN}" "${VERCEL_API}${path}"
}

parse_json_or_fail() {
  local body="$1"
  local http="$2"
  if [ "$http" -ge 200 ] && [ "$http" -lt 300 ]; then
    echo "$body"
    return 0
  fi
  echo "Vercel API error: HTTP $http" >&2
  echo "$body" >&2
  return 1
}

show_deployments() {
  local n="${1:-5}"
  local target_filter="${2:-}"
  local query="projectId=${PROJECT_ID}&teamId=${TEAM_ID}&limit=${n}"
  if [ -n "$target_filter" ]; then
    query="${query}&target=${target_filter}"
  fi

  local resp http body
  resp=$(api "/v6/deployments?${query}")
  http=$(echo "$resp" | tail -1)
  body=$(echo "$resp" | sed '$d')
  body=$(parse_json_or_fail "$body" "$http") || return 1

  echo "$body" | python -c "
import json, sys, datetime
d = json.load(sys.stdin)
deployments = d.get('deployments', [])
if not deployments:
    print('(no deployments)')
    sys.exit(0)
for dep in deployments:
    url = dep.get('url') or '(no url)'
    state = dep.get('state') or dep.get('readyState') or '?'
    target = dep.get('target') or 'preview'
    created = dep.get('created') or dep.get('createdAt')
    if created:
        ts = datetime.datetime.fromtimestamp(created / 1000, tz=datetime.timezone.utc).isoformat()
    else:
        ts = '?'
    commit = (dep.get('meta') or {}).get('githubCommitSha', '')[:7]
    print(f\"  {state:<10} [{target:<10}] https://{url}  {ts}  {commit}\")
" 2>/dev/null
}

show_domains() {
  local resp http body
  resp=$(api "/v9/projects/${PROJECT_ID}/domains?teamId=${TEAM_ID}")
  http=$(echo "$resp" | tail -1)
  body=$(echo "$resp" | sed '$d')
  body=$(parse_json_or_fail "$body" "$http") || return 1

  echo "$body" | python -c "
import json, sys
d = json.load(sys.stdin)
doms = d.get('domains', [])
if not doms:
    print('(no domains attached)')
    sys.exit(0)
for dom in doms:
    name = dom.get('name', '?')
    verified = dom.get('verified')
    git_branch = dom.get('gitBranch') or ''
    redirect = dom.get('redirect') or ''
    verified_str = 'verified' if verified else 'UNVERIFIED'
    extras = []
    if git_branch:
        extras.append(f'branch={git_branch}')
    if redirect:
        extras.append(f'redirect→{redirect}')
    extra_str = (' ' + ' '.join(extras)) if extras else ''
    print(f\"  {name}  [{verified_str}]{extra_str}\")
" 2>/dev/null
}

case "$ACTION" in
  overview|"")
    echo "=== Latest Production Deployment ==="
    show_deployments 1 production
    echo ""
    echo "=== Recent Deployments (last 5) ==="
    show_deployments 5
    echo ""
    echo "=== Domains ==="
    show_domains
    ;;
  deployments)
    echo "=== Recent Deployments (last ${LIMIT_ARG}) ==="
    show_deployments "$LIMIT_ARG"
    ;;
  production)
    echo "=== Latest Production Deployment ==="
    show_deployments 1 production
    ;;
  domains)
    echo "=== Domains ==="
    show_domains
    ;;
  *)
    echo "Usage: run.sh [overview|deployments [n]|production|domains]" >&2
    exit 1
    ;;
esac
