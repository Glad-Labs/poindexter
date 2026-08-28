#!/usr/bin/env bash
# install-swap-tiering.sh — size the zram swap fast tier for this host.
#
# WHY: the host swaps into a priority-tiered pool (zram0 prio 1000, dm-crypt
# prio -1). zram fills first, and once full every new page lands on encrypted
# NVMe — the cliff that turns memory pressure into io stall. On 2026-08-28 zram
# read 99.7% full while TOTAL swap read 61%, so neither existing guard
# (PoindexterHostSwapExhausted at 5% pool-free, systemd-oomd SwapUsedLimit=90%)
# was close to reacting. Rationale and the RAM cost are in the config header:
# infrastructure/systemd/zram/pop-zram.
#
# Idempotent: safe to re-run. Re-run after any edit to that config.
#
# BY DEFAULT THIS ONLY WRITES CONFIG. /usr/bin/pop-zram-config begins with
# `test -b /dev/zram0 && exit 0`, so a live host keeps whatever zram it booted
# with; the new size lands at the next reboot. That is deliberate — resizing in
# place means `swapoff` on a device holding many GiB, which faults all of it
# back into RAM at once. That is precisely the pressure spike this whole
# exercise exists to avoid, so it is opt-in (--apply-now) and guarded.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SRC="$REPO_ROOT/infrastructure/systemd/zram/pop-zram"
DEST="${POINDEXTER_ZRAM_CONF:-/etc/pop-zram}"
APPLY_NOW=0
[[ "${1:-}" == "--apply-now" ]] && APPLY_NOW=1

if [[ ! -x /usr/bin/pop-zram-config ]]; then
  echo "FATAL: /usr/bin/pop-zram-config not found — this host does not use" >&2
  echo "       Pop's zram provisioning, so $DEST would be read by nothing." >&2
  echo "       Size zram through whatever this distro uses instead." >&2
  exit 2
fi

sudo install -m 644 "$SRC" "$DEST"
echo "installed: $DEST"

# Resolve what the config asks for the same way pop-zram-config does, so this
# reports the size that will actually be applied rather than the raw MAX_SIZE.
# shellcheck disable=SC1090
set -a; . "$SRC"; set +a
PORTION="${PORTION:-100}"
TOTAL_MB=$(awk -v p="$PORTION" '/MemTotal/ {printf "%.0f", p * $2 / 102400}' /proc/meminfo)
WANT_MB=$(( TOTAL_MB > MAX_SIZE ? MAX_SIZE : TOTAL_MB ))

if [[ -b /dev/zram0 ]]; then
  HAVE_MB=$(( $(cat /sys/block/zram0/disksize) / 1024 / 1024 ))
else
  HAVE_MB=0
fi
echo "zram size: running=${HAVE_MB}MiB configured=${WANT_MB}MiB"

if [[ "$HAVE_MB" == "$WANT_MB" ]]; then
  echo "already the configured size — nothing to do."
  exit 0
fi

if [[ "$APPLY_NOW" != "1" ]]; then
  echo
  echo "Config written. The running zram is unchanged and will stay ${HAVE_MB}MiB"
  echo "until the next reboot (pop-zram-config no-ops while /dev/zram0 exists)."
  echo "To resize in place instead, re-run with --apply-now — it will refuse"
  echo "unless there is RAM to absorb what zram is currently holding."
  exit 0
fi

# --- guarded live resize -------------------------------------------------
# swapoff faults every page zram holds back into RAM. It also frees the RAM
# zram was using to store them compressed, so the true cost is the difference.
# Demand 1.5x that headroom: MemAvailable is an estimate, and anything else
# allocating during the swapoff competes for the same pages.
USED_KB=$(awk '/zram0/ {print $4}' /proc/swaps)
ZRAM_RAM_KB=$(( $(awk '{print $3}' /sys/block/zram0/mm_stat) / 1024 ))
AVAIL_KB=$(awk '/MemAvailable/ {print $2}' /proc/meminfo)
NEED_KB=$(( (USED_KB - ZRAM_RAM_KB) * 3 / 2 ))

printf 'live resize check: zram holds %.1f GiB (costing %.1f GiB RAM); need %.1f GiB available, have %.1f GiB\n' \
  "$(echo "$USED_KB/1048576" | bc -l)" "$(echo "$ZRAM_RAM_KB/1048576" | bc -l)" \
  "$(echo "$NEED_KB/1048576" | bc -l)" "$(echo "$AVAIL_KB/1048576" | bc -l)"

if (( AVAIL_KB < NEED_KB )); then
  echo "REFUSING: not enough available RAM to absorb the swapoff." >&2
  echo "          Reboot to pick up the new size instead — it is free then," >&2
  echo "          because nothing is in zram yet at boot." >&2
  exit 3
fi

echo "resizing zram0 ${HAVE_MB}MiB -> ${WANT_MB}MiB (this pulls pages back into RAM)..."
sudo swapoff /dev/zram0
sudo zramctl --reset /dev/zram0
sudo zramctl --size "${WANT_MB}M" --algorithm "${ALGO:-zstd}" /dev/zram0
sudo mkswap /dev/zram0
sudo swapon -p 1000 /dev/zram0
sudo sysctl -w "vm.page-cluster=${PAGE_CLUSTERS:-0}" "vm.swappiness=${SWAPPINESS:-180}"

# Report what the kernel ended up with, not what we asked for — the whole
# point of the oomctl lesson next door is that config on disk proves nothing.
echo
zramctl /dev/zram0
grep -E 'zram0|Filename' /proc/swaps
