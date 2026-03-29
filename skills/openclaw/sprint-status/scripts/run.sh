#!/bin/bash
# scripts/run.sh — Show sprint status from GitHub

REPO="Glad-Labs/glad-labs-codebase"
ACTION="${1:-overview}"

case "$ACTION" in
  overview)
    echo "=== SPRINT STATUS ==="
    echo ""
    echo "--- Milestones ---"
    gh api "repos/${REPO}/milestones?state=open" --jq '.[] | "[\(.title)] \(.open_issues) open / \(.closed_issues) closed | Due: \(.due_on // "no date")"'
    echo ""
    echo "--- Open Issues ---"
    gh issue list --repo "$REPO" --state open --json number,title,labels,milestone --jq '.[] | "#\(.number) \(.title) [\(.milestone.title // "no milestone")]"'
    echo ""
    echo "--- Recently Closed (7 days) ---"
    gh issue list --repo "$REPO" --state closed --json number,title,closedAt --jq '[.[] | select(.closedAt > (now - 604800 | strftime("%Y-%m-%dT%H:%M:%SZ")))] | .[:10][] | "#\(.number) \(.title)"'
    ;;

  issues)
    echo "=== OPEN ISSUES ==="
    gh issue list --repo "$REPO" --state open --json number,title,labels,milestone --jq '.[] | "#\(.number) [\(.milestone.title // "none")] \(.title)"'
    ;;

  milestones)
    echo "=== MILESTONES ==="
    gh api "repos/${REPO}/milestones?state=all" --jq '.[] | "\(.state | ascii_upcase) [\(.title)] \(.open_issues) open / \(.closed_issues) closed | Due: \(.due_on // "no date")"'
    ;;

  recent)
    echo "=== RECENTLY CLOSED (7 days) ==="
    gh issue list --repo "$REPO" --state closed --limit 20 --json number,title,closedAt --jq '[.[] | select(.closedAt > (now - 604800 | strftime("%Y-%m-%dT%H:%M:%SZ")))] | .[] | "#\(.number) \(.title)"'
    ;;

  *)
    echo "Usage: run.sh [overview|issues|milestones|recent]"
    exit 1
    ;;
esac
