#!/usr/bin/env bash
# install-oomd.sh — install + enable systemd-oomd with the swap-kill policy
# that keeps a swap-exhaustion livelock from taking the whole box down.
#
# WHY: on 2026-08-27 the host froze hard (no I/O, hard power cycle required)
# with ~28 GiB of swap held by four DORMANT model sidecars while MemAvailable
# still read ~20 GiB. The kernel OOM killer never fired — there was no
# allocation failure, only endless direct reclaim against a 100%-full swap
# device — and nothing else was watching, so there was no way out.
#
# Re-run after any change to infrastructure/systemd/oomd/** — the installed
# copies under /etc are renders, not symlinks, so they do NOT update on their
# own. Idempotent: safe to re-run.
#
# Verify afterwards with `sudo oomctl`, NOT by reading the files back: the
# config being on disk says nothing about whether oomd parsed it. Two things
# must be true in that output — a "Swap Monitored CGroups" entry for
# /system.slice, and "Dry Run: no".
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SRC="$REPO_ROOT/infrastructure/systemd/oomd"
UNIT_DIR="${POINDEXTER_UNIT_DIR:-/etc/systemd/system}"
OOMD_CONF="${POINDEXTER_OOMD_CONF:-/etc/systemd/oomd.conf}"

if ! command -v oomctl >/dev/null 2>&1; then
  echo "installing systemd-oomd..."
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y systemd-oomd
fi

# cgroup v2 is a hard requirement — oomd reads memory.swap.current per cgroup,
# which cgroup v1 does not expose. Fail loud rather than install a no-op.
if [[ "$(stat -fc %T /sys/fs/cgroup)" != "cgroup2fs" ]]; then
  echo "FATAL: /sys/fs/cgroup is not cgroup2fs — systemd-oomd cannot see" >&2
  echo "       per-cgroup swap usage, so this policy would be inert." >&2
  exit 2
fi

sudo install -m 644 "$SRC/oomd.conf" "$OOMD_CONF"
echo "installed: $OOMD_CONF"

# system.slice carries every docker-<id>.scope AND ollama-{primary,vision}, so
# opting it in makes each a separate kill candidate instead of one blob.
# docker/containerd get `avoid` because killing either cascades to all ~47
# containers — strictly worse than losing the one sidecar causing the pressure.
for d in system.slice.d docker.service.d containerd.service.d; do
  sudo mkdir -p "$UNIT_DIR/$d"
  for f in "$SRC/$d"/*.conf; do
    sudo install -m 644 "$f" "$UNIT_DIR/$d/$(basename "$f")"
    echo "installed: $UNIT_DIR/$d/$(basename "$f")"
  done
done

sudo systemctl daemon-reload
sudo systemctl enable --now systemd-oomd
sudo systemctl restart systemd-oomd

# Assert the policy actually took. `systemctl show` would only echo the file
# back; oomctl reports what the running daemon resolved, which is the only
# statement worth trusting here.
sleep 2
if ! sudo oomctl | grep -qE '^\s+Path: /system\.slice$'; then
  echo "FATAL: systemd-oomd is running but is NOT monitoring /system.slice." >&2
  echo "       The swap-kill policy is inert. Check 'sudo oomctl'." >&2
  exit 1
fi
echo "OK: systemd-oomd is monitoring /system.slice for swap exhaustion."
sudo oomctl | sed -n '1,10p'
