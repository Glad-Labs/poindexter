#!/usr/bin/env bash
# start-stack.sh — launch the Poindexter Docker stack without .env files.
#
# Reads ~/.poindexter/bootstrap.toml (written by `poindexter setup`),
# exports every key as an uppercase env var, then runs docker compose.
#
# Usage:
#   bash scripts/start-stack.sh              # docker compose up -d
#   bash scripts/start-stack.sh up -d        # same
#   bash scripts/start-stack.sh down         # stop
#   bash scripts/start-stack.sh logs -f      # follow logs
set -euo pipefail

BOOTSTRAP="${USERPROFILE:-$HOME}/.poindexter/bootstrap.toml"
# Use the operator stack if available, otherwise the customer stack
if [ -f "docker-compose.local.yml" ]; then
    COMPOSE_FILE="docker-compose.local.yml"
else
    COMPOSE_FILE="docker-compose.yml"
fi
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

if [ ! -f "$BOOTSTRAP" ]; then
    echo "ERROR: $BOOTSTRAP not found."
    echo ""
    echo "Run 'poindexter setup' first — it generates secrets and writes"
    echo "the bootstrap file. No .env needed."
    exit 1
fi

# Parse bootstrap.toml: extract key = "value" lines, export as
# UPPER_CASE env vars. Handles both quoted and unquoted values.
while IFS= read -r line; do
    # Skip comments and blank lines
    [[ "$line" =~ ^[[:space:]]*# ]] && continue
    [[ -z "${line// }" ]] && continue

    # Match: key = "value" or key = value
    if [[ "$line" =~ ^[[:space:]]*([a-z_]+)[[:space:]]*=[[:space:]]*\"?([^\"]*)\"? ]]; then
        key="${BASH_REMATCH[1]}"
        value="${BASH_REMATCH[2]}"
        # Uppercase for env var convention
        env_key="${key^^}"
        export "$env_key"="$value"
    fi
done < "$BOOTSTRAP"

# Grafana webhook JWT — decrypt from app_settings.grafana_webhook_oauth_jwt
# so contact-points.yml can substitute it into the Poindexter Webhook
# receiver's Authorization header. Without this Grafana posts
# unauthenticated and the worker rejects every alert with 401 (finding
# #2 from the 2026-05-19 jank-audit stress test). Best-effort: failures
# (missing key, Postgres not up yet, etc.) emit a WARNING to stderr and
# leave the var empty — Grafana boots fine, worker logs the 401 loudly
# per `feedback_no_silent_defaults`. The operator runs
# `poindexter auth mint-grafana-token --persist` to provision the JWT;
# subsequent start-stack runs pick it up automatically.
if command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN=python3
elif command -v python >/dev/null 2>&1; then
    PYTHON_BIN=python
else
    PYTHON_BIN=""
fi
if [ -n "$PYTHON_BIN" ] && [ -f "$SCRIPT_DIR/_grafana_webhook_token.py" ]; then
    # Capture stdout (the JWT) only; let stderr pass through to the
    # operator's terminal so missing-config warnings are visible.
    GRAFANA_WEBHOOK_TOKEN="$("$PYTHON_BIN" "$SCRIPT_DIR/_grafana_webhook_token.py" || true)"
    export GRAFANA_WEBHOOK_TOKEN
    # Persist the decrypted token to a runtime env file so plain
    # ``docker restart poindexter-grafana`` and ``docker compose up
    # -d`` (run outside ``start-stack.sh``) still pick up the JWT.
    # Without this, restarting Grafana directly leaves the
    # contact-point's Bearer credential empty and the worker logs
    # ~200 401s/day on the Alertmanager webhook (2026-05-27 audit
    # finding). Glad-Labs/poindexter#231 — wire-it-once,
    # survive-every-restart hardening.
    #
    # Writes to ``.poindexter-grafana.env`` next to docker-compose.local.yml;
    # the grafana service is wired to load it via ``env_file:`` so the
    # token lands in the container's env on every up/restart.
    #
    # ``.poindexter-grafana.env`` is git-ignored (operator-specific
    # runtime secret). Empty-token writes are still safe — the file
    # carries an explicit empty assignment which mirrors the
    # docker-compose env-var default and keeps the loud-401 failure
    # mode (per ``feedback_no_silent_defaults``).
    _RUNTIME_ENV="$PROJECT_DIR/.poindexter-grafana.env"
    {
        echo "# Auto-managed by scripts/start-stack.sh — DO NOT EDIT BY HAND."
        echo "# Regenerated on every start-stack invocation from"
        echo "# app_settings.grafana_webhook_oauth_jwt (encrypted at rest)."
        echo "GRAFANA_WEBHOOK_TOKEN=$GRAFANA_WEBHOOK_TOKEN"
    } > "$_RUNTIME_ENV"
    chmod 600 "$_RUNTIME_ENV" 2>/dev/null || true
fi

# Default docker compose action
ACTION="${1:-up}"
shift 2>/dev/null || true

cd "$PROJECT_DIR"

if [ "$ACTION" = "up" ] && [ $# -eq 0 ]; then
    exec docker compose -f "$COMPOSE_FILE" up -d
else
    exec docker compose -f "$COMPOSE_FILE" "$ACTION" "$@"
fi
