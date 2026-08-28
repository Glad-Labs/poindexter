#!/usr/bin/env bash
# docker-watchdog.sh — minimal Linux liveness watchdog for the Poindexter stack.
# Bare-metal replacement for the Windows docker-watchdog.ps1: on native Docker
# there is no WSL VM, so the whole `wsl --shutdown` big-hammer path is gone.
# If the engine is down, restart it; if the stack isn't up, bring it up.
# Intended to run every ~5 min via a systemd timer.
#
# Two properties below are load-bearing and were both learned the hard way on
# 2026-08-27. Read the comments before simplifying either.
set -euo pipefail

# Bring the stack up from the checkout it was DEPLOYED from — the deploy clone
# — not from the dev checkout.
#
# Why: compose bind mounts are relative (`./infrastructure/prometheus/...`), so
# they resolve to different ABSOLUTE paths per checkout, and the resolved paths
# land in compose's config hash. Running `up -d` from the dev checkout therefore
# computes a different hash than the running containers carry, and recreates
# them EVERY time — by construction, not occasionally. Measured on 2026-08-27:
#
#   running prometheus     f48993c79091cae4...
#   deploy clone computes  f48993c79091cae4...   -> "Running", no-op
#   dev checkout computes  d8da2e5e76fb82d9...   -> "Recreate", every pass
#
# That churned prometheus/grafana/alertmanager/brain-daemon/worker ~10x in 7
# days. Each Prometheus restart discards every pending alert `for:` clock (see
# docs/operations/alert-rule-authoring.md), which is how the host-swap alert
# came to sit `pending` for 8h15m through a hard-reboot freeze. Recreating a
# depended-on service is also what strands dependents in `created` (#3407).
#
# Falls back to the dev checkout so a fresh install with no deploy clone yet
# still self-heals.
DEPLOY_ROOT="${POINDEXTER_DEPLOY_ROOT:-$HOME/.poindexter/deploy/glad-labs-stack}"
if [[ -f "$DEPLOY_ROOT/docker-compose.local.yml" ]]; then
  REPO="${POINDEXTER_REPO:-$DEPLOY_ROOT}"
else
  REPO="${POINDEXTER_REPO:-$HOME/glad-labs-website}"
fi

# How hard to confirm before treating an unhealthy worker as a dead stack.
# The worker bounces constantly on ordinary deploys — 36 down-windows in 12h,
# median 30s — and this watchdog polls every 5 min, currently phase-locked
# ~23s behind poindexter-deploy-sync. Acting on the first failed probe meant
# 15 of 19 activations landed within 10 min of a deploy (12 within ONE min):
# the watchdog was reacting to the deploy's own restart, not to a fault.
# 3 x 30s covers 30 of those 36 observed windows.
CONFIRM_ATTEMPTS="${POINDEXTER_WATCHDOG_CONFIRM_ATTEMPTS:-3}"
CONFIRM_INTERVAL="${POINDEXTER_WATCHDOG_CONFIRM_INTERVAL:-30}"
HEALTH_URL="${POINDEXTER_WATCHDOG_HEALTH_URL:-localhost:8002/api/health}"

worker_healthy() {
  curl -sf --max-time 5 "$HEALTH_URL" >/dev/null 2>&1
}

# 1. Engine alive?
if ! docker info >/dev/null 2>&1; then
  echo "docker engine down — restarting"
  sudo systemctl restart docker
  sleep 10
fi

# 2. Worker healthy? (the canonical up signal)
if worker_healthy; then
  exit 0
fi

# 2b. Confirm. A single failed probe is far likelier to be a deploy restart
# than a dead stack, and the remedy here (`up -d` on the whole stack) is much
# more disruptive than the symptom.
for attempt in $(seq 1 "$CONFIRM_ATTEMPTS"); do
  echo "worker probe failed (${attempt}/${CONFIRM_ATTEMPTS}) — re-checking in ${CONFIRM_INTERVAL}s"
  sleep "$CONFIRM_INTERVAL"
  if worker_healthy; then
    echo "worker recovered on its own — no action (likely a deploy restart)"
    exit 0
  fi
done

echo "worker unhealthy after ${CONFIRM_ATTEMPTS} confirmations — bringing the stack up from $REPO"
cd "$REPO"
bash scripts/start-stack.sh up -d
