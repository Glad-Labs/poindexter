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
  # Every session runs after 08:00: the box sleeps 01:00-08:00
  # (scripts/linux/night_sleep.py, app_settings host_sleep_*). A fire inside
  # the window would be replayed at wake by Persistent=true, so the morning
  # would start with every session at once on a just-resumed stack.
  "dependency-review|*-*-* 08:20:00"
  "alert-triage|*-*-* 08:40:00"
  "claude-md-sync|*-*-* 09:00:00"
  "test-health|*-*-* 09:20:00"
  "triage-sweep|Mon *-*-* 09:40:00"
  "codebase-audit|Wed *-*-* 10:00:00"
  "doc-sync|Fri *-*-* 10:00:00"
  "pro-freshness|Sun *-*-* 10:30:00"
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
# Stagger: Persistent=true replays every missed fire at boot in the same
# second (nine sessions stampeding a half-started stack — stack#3033). The
# jitter spreads them; run-session.sh's readiness gate does the real
# waiting. Harmless on scheduled fires (sessions are minute-precision).
RandomizedDelaySec=120

[Install]
WantedBy=timers.target
EOF
  sudo systemctl enable --now "poindexter-session@${name}.timer"
  echo "installed timer: ${name} (${cal})"
done

sudo systemctl daemon-reload
echo "done — verify with: systemctl list-timers 'poindexter-session@*'"
