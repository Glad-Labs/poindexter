#!/usr/bin/env bash
# Install the nightly sleep window (poindexter-night-sleep.{service,timer}).
#
# Run from the checkout the unit should execute, normally the deploy clone
# (~/.poindexter/deploy/glad-labs-stack), so a merged change to
# night_sleep.py runs on the next tick without a reinstall. The window itself
# lives in app_settings: nothing sleeps until host_sleep_enabled is true.
#
#   bash scripts/linux/install-night-sleep.sh            # install + enable
#   sudo python3 scripts/linux/night_sleep.py --dry-run  # what it would do now
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RUN_USER="${SUDO_USER:-$USER}"
RUN_HOME="$(getent passwd "$RUN_USER" | cut -d: -f6)"
UNIT_DIR=/etc/systemd/system

sed -e "s|REPO_ROOT|${REPO_ROOT}|g" -e "s|RUN_HOME|${RUN_HOME}|g" \
  "$REPO_ROOT/infrastructure/systemd/poindexter-night-sleep.service" \
  | sudo tee "$UNIT_DIR/poindexter-night-sleep.service" >/dev/null
sudo install -m 0644 "$REPO_ROOT/infrastructure/systemd/poindexter-night-sleep.timer" \
  "$UNIT_DIR/poindexter-night-sleep.timer"
sudo systemctl daemon-reload
sudo systemctl enable --now poindexter-night-sleep.timer
echo "installed poindexter-night-sleep (repo=${REPO_ROOT}, home=${RUN_HOME})"
echo "the window is app_settings host_sleep_*; check with: sudo python3 ${REPO_ROOT}/scripts/linux/night_sleep.py --dry-run"
