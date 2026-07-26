#!/usr/bin/env bash
# scripts/audit-env-vars.sh — GH-93 guardrail.
#
# Lists every os.getenv/os.environ read in src/cofounder_agent/services/,
# classifies each as bootstrap-allowed or migration-candidate, and exits
# non-zero if any new non-allowed reads appear beyond the known set.
#
# Usage:
#   ./scripts/audit-env-vars.sh           # print report
#   ./scripts/audit-env-vars.sh --strict  # exit 1 if any non-allowed read exists
#
# Allowed reads (grandfathered bootstrap + plugin configs):
#   DATABASE_URL, LOCAL_DATABASE_URL, POINDEXTER_MEMORY_DSN
#   DEPLOYMENT_MODE, ENVIRONMENT
#   OLLAMA_URL, OLLAMA_BASE_URL (see GH-93 P1 commit for rationale)
#   LOG_*, CLAUDE_PROJECTS_DIR, OTEL_*
#
# Everything else should read from app_settings via services.site_config.
set -euo pipefail
cd "$(dirname "$0")/.."

ALLOWED='(DATABASE_URL|LOCAL_DATABASE_URL|POINDEXTER_MEMORY_DSN|DEPLOYMENT_MODE|ENVIRONMENT|OLLAMA_URL|OLLAMA_BASE_URL|LOG_[A-Z_]+|CLAUDE_PROJECTS_DIR|OTEL_[A-Z_]+|CLOUD_DATABASE_URL)'

readers=$(grep -rn 'os\.getenv\|os\.environ' src/cofounder_agent/services/ \
  --include='*.py' | grep -v __pycache__ | grep -Ev '^[^:]+:[0-9]+:[[:space:]]*#' || true)

total=0; allowed=0; flagged=0
while IFS= read -r line; do
  [ -z "$line" ] && continue
  total=$((total+1))
  if echo "$line" | grep -Eq "\"$ALLOWED\"|'$ALLOWED'"; then
    allowed=$((allowed+1))
  elif echo "$line" | grep -Eq 'services/(site_config|settings_service|logger_config|telemetry|taps/|jobs/db_backup|migrations/)'; then
    # Infrastructure modules that define the env-fallback mechanism itself
    # or propagate env to subprocesses. Read but never leak.
    allowed=$((allowed+1))
  else
    echo "FLAG: $line"
    flagged=$((flagged+1))
  fi
done <<< "$readers"

echo ""
echo "services/ env-reads:  $total total  |  $allowed allowed  |  $flagged flagged"

if [ "${1:-}" = "--strict" ] && [ "$flagged" -gt 0 ]; then
  echo "FAIL: $flagged env read(s) should migrate to site_config" >&2
  exit 1
fi
exit 0
