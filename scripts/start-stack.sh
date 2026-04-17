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

# Default docker compose action
ACTION="${1:-up}"
shift 2>/dev/null || true

cd "$PROJECT_DIR"

if [ "$ACTION" = "up" ] && [ $# -eq 0 ]; then
    exec docker compose -f "$COMPOSE_FILE" up -d
else
    exec docker compose -f "$COMPOSE_FILE" "$ACTION" "$@"
fi
