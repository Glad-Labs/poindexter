#!/usr/bin/env bash
# docker-watchdog.sh — minimal Linux liveness watchdog for the Poindexter stack.
# Bare-metal replacement for the Windows docker-watchdog.ps1: on native Docker
# there is no WSL VM, so the whole `wsl --shutdown` big-hammer path is gone.
# If the engine is down, restart it; if the stack isn't up, bring it up.
# Intended to run every ~5 min via a systemd timer.
set -euo pipefail
REPO="${POINDEXTER_REPO:-$HOME/glad-labs-website}"

# 1. Engine alive?
if ! docker info >/dev/null 2>&1; then
  echo "docker engine down — restarting"
  sudo systemctl restart docker
  sleep 10
fi

# 2. Worker healthy? (the canonical up signal)
if curl -sf --max-time 5 localhost:8002/api/health >/dev/null 2>&1; then
  exit 0
fi

echo "worker unhealthy — bringing the stack up"
cd "$REPO"
bash scripts/start-stack.sh up -d
