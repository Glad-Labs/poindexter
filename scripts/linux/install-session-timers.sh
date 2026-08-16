#!/usr/bin/env bash
# install-session-timers.sh — install the session .service template + generate
# and enable the 8 systemd session timers (Linux replacement for
# claude-sessions.ps1 -Install). OnCalendar times are local; systemd honors
# the host timezone (set it to operator_timezone).
#
# Re-run this after any change to infrastructure/systemd/poindexter-session@.service
# — the installed copy under /etc/systemd/system is a render, not a symlink, so
# it does NOT update on its own (run-session.sh self-updates via its ff-only
# pre-flight; the unit file cannot, because writing /etc needs sudo).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
UNIT_DIR="${POINDEXTER_UNIT_DIR:-/etc/systemd/system}"
# The documented invocation is `sudo bash install-session-timers.sh`, so
# `id -un` answers root — SUDO_USER carries the operator login the sessions
# must actually run as (caught live on the first deploy: it rendered
# User=root, which would have run every session against root's HOME with no
# poetry env, no ~/.poindexter, and root-owned artifacts everywhere).
RUN_USER="${SUDO_USER:-$(id -un)}"

# Render the template onto this host: the repo copy ships a generic
# `poindexter` / /home/poindexter placeholder; the live unit must carry the
# operator's login and this checkout's actual path.
sed -e "s|^User=.*|User=${RUN_USER}|" \
    -e "s|^ExecStart=.*|ExecStart=${REPO_ROOT}/scripts/linux/run-session.sh %i|" \
    "$REPO_ROOT/infrastructure/systemd/poindexter-session@.service" \
  | sudo tee "$UNIT_DIR/poindexter-session@.service" >/dev/null
echo "installed unit: poindexter-session@.service (User=${RUN_USER}, repo=${REPO_ROOT})"

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
  # Daytime on purpose: a broken model pin should page while the operator is
  # awake, not surface as a 03:00 session failure (stack#3163).
  "pin-check|*-*-* 12:30:00"
)

for row in "${SCHED[@]}"; do
  name="${row%%|*}"
  cal="${row#*|}"
  sudo tee "$UNIT_DIR/poindexter-session@${name}.timer" >/dev/null <<EOF
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
