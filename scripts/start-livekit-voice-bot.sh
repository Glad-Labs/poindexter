#!/usr/bin/env bash
# start-livekit-voice-bot.sh — bring the Pipecat-based voice agent into
# a LiveKit room so phones / browsers / other bots can talk to it.
#
# The LiveKit SFU itself is the `poindexter-livekit` Docker container
# (already in docker-compose.local.yml — ports 7880/7881 TCP + 7882 UDP).
# This script starts the BOT process that JOINS the room as a participant
# running the Whisper → LLM → Kokoro pipeline.
#
# Usage:
#   bash scripts/start-livekit-voice-bot.sh                   # default room "matt-test"
#   bash scripts/start-livekit-voice-bot.sh --room standup    # custom room name
#   bash scripts/start-livekit-voice-bot.sh --print-client-token --room <name> --identity phone
#                                                              # mint a token to paste into a phone client

set -euo pipefail

# Default credentials match the dev fallback in docker-compose.local.yml.
# Override via env vars when you rotate to real keys.
export LIVEKIT_URL="${LIVEKIT_URL:-ws://localhost:7880}"
export LIVEKIT_API_KEY="${LIVEKIT_API_KEY:-devkey}"
export LIVEKIT_API_SECRET="${LIVEKIT_API_SECRET:-devsecret_change_me_change_me_change_me}"

echo "==> LiveKit URL:        ${LIVEKIT_URL}"
echo "==> LiveKit API key:    ${LIVEKIT_API_KEY}"
echo "==> LiveKit API secret: ${LIVEKIT_API_SECRET:0:8}…  (truncated)"
echo ""

# Verify the SFU container is up before joining (saves 30s of socket
# hangs when the operator forgot to start it).
if ! docker ps --filter name=poindexter-livekit --filter status=running --format '{{.Names}}' | grep -q poindexter-livekit; then
    echo "ERROR: poindexter-livekit container is not running."
    echo "       Start it: docker compose -f docker-compose.local.yml up -d livekit"
    exit 1
fi

# The bot is poindexter.services.voice_agent_livekit in the worker package.
# Run it from the cofounder_agent/ directory so `-m` finds the poindexter
# package there. The flat `services.` spelling stopped resolving when
# poindexter#1046 deleted the flat roots; scripts/ci/module_launch_paths_lint.py
# checks every `-m` launch string against the tree.
#
# pipecat and livekit are NOT backend poetry dependencies -- only the
# voice-agent image installs them (scripts/Dockerfile.voice-agent). Install
# them into this poetry env first (pins in that Dockerfile), or run the
# containerised voice-agent-livekit service instead.
cd "$(dirname "$0")/../src/cofounder_agent"

exec poetry run python -m poindexter.services.voice_agent_livekit "$@"
