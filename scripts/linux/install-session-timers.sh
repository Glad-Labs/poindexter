#!/usr/bin/env bash
# install-session-timers.sh — generate + enable the 8 systemd session timers
# (Linux replacement for claude-sessions.ps1 -Install). OnCalendar times are
# local; systemd honors the host timezone (set it to operator_timezone).
set -euo pipefail

# name|OnCalendar  (mirrors the Windows $Sessions schedule table)
SCHED=(
  "dependency-review|*-*-* 06:30:00"
  "codebase-audit|Wed *-*-* 02:00:00"
  "doc-sync|Fri *-*-* 05:00:00"
  "claude-md-sync|*-*-* 02:30:00"
  "triage-sweep|Mon *-*-* 07:00:00"
  "alert-triage|*-*-* 01:00:00"
  "test-health|*-*-* 03:00:00"
  "pro-freshness|Sun *-*-* 04:30:00"
)

for row in "${SCHED[@]}"; do
  name="${row%%|*}"
  cal="${row#*|}"
  sudo tee "/etc/systemd/system/poindexter-session@${name}.timer" >/dev/null <<EOF
[Unit]
Description=Timer for Poindexter ops session ${name}

[Timer]
OnCalendar=${cal}
Persistent=true

[Install]
WantedBy=timers.target
EOF
  sudo systemctl enable --now "poindexter-session@${name}.timer"
  echo "installed timer: ${name} (${cal})"
done

sudo systemctl daemon-reload
echo "done — verify with: systemctl list-timers 'poindexter-session@*'"
