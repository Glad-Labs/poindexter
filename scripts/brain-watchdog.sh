#!/usr/bin/env bash
# Brain daemon watchdog — Layer 1 of Poindexter's redundancy model.
#
# Monitors the brain daemon's heartbeat file. If stale (>15 min), restarts
# the brain and optionally sends a Telegram alert.
#
# Usage:
#   ./brain-watchdog.sh              # Run once (check heartbeat)
#   ./brain-watchdog.sh --install    # Add to crontab (every 10 minutes)
#   ./brain-watchdog.sh --uninstall  # Remove from crontab
#
# Works on Linux and macOS. Zero dependencies beyond bash + curl.

set -euo pipefail

HEARTBEAT_FILE="$HOME/.poindexter/heartbeat"
BRAIN_SCRIPT="$(cd "$(dirname "$0")/.." && pwd)/brain/brain_daemon.py"
LOG_DIR="$HOME/.poindexter/logs"
LOG_FILE="$LOG_DIR/watchdog.log"
MAX_STALE_SECONDS=900  # 15 minutes

mkdir -p "$LOG_DIR"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $1" >> "$LOG_FILE"; }

# --- Install / Uninstall ---

if [[ "${1:-}" == "--install" ]]; then
    SCRIPT_PATH="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
    chmod +x "$SCRIPT_PATH"
    # Remove existing entry, then add
    (crontab -l 2>/dev/null | grep -v "brain-watchdog" || true; \
     echo "*/10 * * * * $SCRIPT_PATH") | crontab -
    echo "Installed cron job: brain-watchdog (every 10 minutes)"
    exit 0
fi

if [[ "${1:-}" == "--uninstall" ]]; then
    (crontab -l 2>/dev/null | grep -v "brain-watchdog" || true) | crontab -
    echo "Removed cron job: brain-watchdog"
    exit 0
fi

# --- Heartbeat Check ---

if [[ ! -f "$HEARTBEAT_FILE" ]]; then
    log "WARN: No heartbeat file — brain may have never started"
    if [[ -f "$BRAIN_SCRIPT" ]]; then
        log "ACTION: Starting brain daemon"
        nohup python3 "$BRAIN_SCRIPT" > /dev/null 2>&1 &
        log "OK: Brain daemon started (PID $!)"
    else
        log "ERROR: Brain script not found at $BRAIN_SCRIPT"
    fi
    exit 0
fi

# Parse heartbeat timestamp (handles both JSON and legacy plain-number format)
if command -v python3 &>/dev/null; then
    HEARTBEAT_TS=$(python3 -c "
import json, sys
try:
    data = json.load(open('$HEARTBEAT_FILE'))
    print(int(data['ts']))
except:
    print(int(float(open('$HEARTBEAT_FILE').read().strip())))
" 2>/dev/null || echo 0)
else
    # Fallback: file modification time
    HEARTBEAT_TS=$(stat -c %Y "$HEARTBEAT_FILE" 2>/dev/null || stat -f %m "$HEARTBEAT_FILE" 2>/dev/null || echo 0)
fi

NOW=$(date +%s)
AGE=$(( NOW - HEARTBEAT_TS ))

if [[ $AGE -lt $MAX_STALE_SECONDS ]]; then
    # Brain is alive
    exit 0
fi

AGE_MIN=$(( AGE / 60 ))
log "ALERT: Brain heartbeat is ${AGE_MIN} minutes old (threshold: $(( MAX_STALE_SECONDS / 60 )))"

# Kill stale brain processes
pkill -f "brain_daemon.py" 2>/dev/null && log "ACTION: Killed stale brain process" || true

# Restart
if [[ -f "$BRAIN_SCRIPT" ]]; then
    nohup python3 "$BRAIN_SCRIPT" > /dev/null 2>&1 &
    log "ACTION: Brain daemon restarted (PID $!)"
else
    log "ERROR: Brain script not found at $BRAIN_SCRIPT"
fi

# Send Telegram alert
TELEGRAM_BOT_TOKEN="${TELEGRAM_BOT_TOKEN:-}"
TELEGRAM_CHAT_ID="${TELEGRAM_CHAT_ID:-}"

# Try loading from OpenClaw .env
if [[ -z "$TELEGRAM_BOT_TOKEN" && -f "$HOME/.openclaw/workspace/.env" ]]; then
    eval "$(grep -E '^TELEGRAM_(BOT_TOKEN|CHAT_ID)=' "$HOME/.openclaw/workspace/.env")"
fi

if [[ -n "$TELEGRAM_BOT_TOKEN" && -n "$TELEGRAM_CHAT_ID" ]]; then
    MSG="Brain Watchdog: daemon was unresponsive for ${AGE_MIN} minutes. Restarted automatically."
    curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
        -d chat_id="$TELEGRAM_CHAT_ID" \
        -d text="$MSG" > /dev/null 2>&1 \
        && log "OK: Telegram alert sent" \
        || log "ERROR: Telegram alert failed"
else
    log "WARN: No Telegram credentials — alert not sent"
fi
