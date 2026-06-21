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
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
# Use the operator stack if available, otherwise the customer stack. Anchor the
# existence check to PROJECT_DIR (derived from $0), NOT the caller's CWD: this
# script is invoked from arbitrary working directories — the deploy-checkout-sync
# Scheduled Task's apply step runs it from C:\Windows\System32 — and a CWD-relative
# check there silently falls back to the customer docker-compose.yml, then
# reconciles the operator project against the wrong topology (tearing down
# operator-only services). COMPOSE_FILE stays a basename; the `docker compose -f`
# call runs after `cd "$PROJECT_DIR"` below, so the basename resolves correctly.
if [ -f "$PROJECT_DIR/docker-compose.local.yml" ]; then
    COMPOSE_FILE="docker-compose.local.yml"
else
    COMPOSE_FILE="docker-compose.yml"
fi

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
    # finding). Glad-Labs/glad-labs-stack#231 — wire-it-once,
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

# Offsite-backup secrets (poindexter#386) — decrypt the three encrypted
# app_settings rows into .poindexter-backup-offsite.env so the backup-offsite
# service's env_file picks up RESTIC_PASSWORD + AWS_* on every up/restart.
# Same pattern + same fail-soft posture as the Grafana token above: the helper
# always emits the full env body (empty assignments when unconfigured), so the
# runner idles loud-inert on an opt-out tier rather than the stack failing.
# The file is git-ignored and only consumed when `poindexter backup setup` has
# populated the secrets; the compose env_file is required:false so a missing
# file is non-fatal too.
if [ -n "$PYTHON_BIN" ] && [ -f "$SCRIPT_DIR/_backup_offsite_secrets.py" ]; then
    _OFFSITE_ENV="$PROJECT_DIR/.poindexter-backup-offsite.env"
    # stdout (the env body) → the file; stderr (WARNINGs) → operator terminal.
    if ! "$PYTHON_BIN" "$SCRIPT_DIR/_backup_offsite_secrets.py" > "$_OFFSITE_ENV"; then
        # The helper is designed never to fail; this is belt-and-suspenders so
        # a crash still leaves a valid (empty) env_file rather than a truncated one.
        printf '%s\n' \
            "# Auto-managed — generation failed, see start-stack.sh output." \
            "RESTIC_PASSWORD=" "AWS_ACCESS_KEY_ID=" "AWS_SECRET_ACCESS_KEY=" \
            > "$_OFFSITE_ENV"
    fi
    chmod 600 "$_OFFSITE_ENV" 2>/dev/null || true
fi

# Default docker compose action
ACTION="${1:-up}"
shift 2>/dev/null || true

# Parallel-stack guard. docker compose infers the project name from the launch
# directory's basename unless COMPOSE_PROJECT_NAME is set. Launching from a second
# directory (e.g. a dedicated deploy checkout) whose basename differs from your
# original checkout then forks a SECOND project that orphans the existing named
# volumes. Pin it in ~/.poindexter/bootstrap.toml (compose_project_name =
# "<your-project>", which the loop above exports as COMPOSE_PROJECT_NAME).
if [ -z "${COMPOSE_PROJECT_NAME:-}" ]; then
    echo "WARNING: COMPOSE_PROJECT_NAME is unset - docker compose will infer it from" >&2
    echo "         '$(basename "$PROJECT_DIR")'. Launching from more than one directory" >&2
    echo "         then forks a parallel stack and orphans your data volumes. Set" >&2
    echo "         compose_project_name in ~/.poindexter/bootstrap.toml." >&2
fi

cd "$PROJECT_DIR"

if [ "$ACTION" = "up" ] && [ $# -eq 0 ]; then
    exec docker compose -f "$COMPOSE_FILE" up -d
else
    exec docker compose -f "$COMPOSE_FILE" "$ACTION" "$@"
fi
