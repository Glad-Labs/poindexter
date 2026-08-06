#!/usr/bin/env bash
# Boot-robust launcher for the sensor-strip conky (8.8" 1920x480, HDMI-A-1).
# Xwayland's display number and monitor order can change between boots, and the
# layout can be rearranged long after login, so:
#  1. discover the session's Xwayland display,
#  2. wait for RandR to settle, then anchor conky relative to X monitor 0
#     (conky's default anchor head),
#  3. keep watching RandR and re-anchor when the strip moves out from under it.
set -u

# Defaults are what production runs; the env overrides let test-launch-strip.sh
# drive the same logic fast against stub binaries.
CONKY_BIN=${CONKY_BIN:-/usr/bin/conky}
CONKY_CONF=${CONKY_CONF:-$HOME/.config/conky/sensor-strip.conf}
STRIP_OUTPUT=${STRIP_OUTPUT:-HDMI-A-1}
SETTLE_READS=${SETTLE_READS:-3} # identical consecutive reads before a layout is trusted
BOOT_POLL=${BOOT_POLL:-2}       # seconds between reads while waiting to anchor
BOOT_TRIES=${BOOT_TRIES:-60}
WATCH_POLL=${WATCH_POLL:-5} # seconds between reads once conky is up

CONKY_PID=""
SLEEP_PID=""
OFFSET=""  # offset conky is currently anchored to, "<x> <y>"
PENDING="" # offset from the most recent readable sample
STABLE=0   # consecutive reads that have agreed with $PENDING
LAST=""    # most recent readable sample, settled or not

log() { printf 'launch-strip: %s\n' "$*" >&2; }

# Sleep in a child and wait on it: bash defers traps until the foreground
# command finishes, and `systemctl --user stop` shouldn't have to wait out a
# poll interval.
nap() {
  sleep "$1" &
  SLEEP_PID=$!
  wait "$SLEEP_PID" 2>/dev/null
  SLEEP_PID=""
}

# Echo "<x> <y>" — where the strip sits relative to X monitor 0. Silent when
# RandR is unreadable or either monitor is missing from the layout, which the
# callers treat as "hold whatever is on screen".
read_offset() {
  xrandr --listmonitors 2>/dev/null | awk -v out="$STRIP_OUTPUT" '
    $1 ~ /^[0-9]+:$/ {
      # $3 is WIDTH/mmxHEIGHT/mm+X+Y, e.g. 1920/50x480/210+4423+1920
      n = split($3, g, "+")
      if (n < 3 || g[2] == "" || g[3] == "") next
      if ($1 == "0:") { px = g[2]; py = g[3]; have0 = 1 }
      if ($NF == out) { sx = g[2]; sy = g[3]; have_strip = 1 }
    }
    END { if (have0 && have_strip) print sx - px, sy - py }
  '
}

# One settle step: fold a fresh read into PENDING/STABLE/LAST, and succeed once
# SETTLE_READS consecutive reads have agreed on the same offset. Waiting for the
# layout to STOP CHANGING (rather than merely be readable) is what keeps a
# staggered monitor wake from baking a wrong anchor in — seen 2026-07-27, when
# head 0 sampled as HDMI-A-3 @+5360 instead of DP-3 @+1920 and the strip drew
# 3440px off-target, running but invisible for the whole session.
settle_step() {
  local cur
  cur=$(read_offset)
  if [ -z "$cur" ]; then
    STABLE=0
    return 1
  fi
  LAST=$cur
  if [ "$cur" = "$PENDING" ]; then
    STABLE=$((STABLE + 1))
  else
    PENDING=$cur
    STABLE=1
  fi
  [ "$STABLE" -ge "$SETTLE_READS" ]
}

start_conky() {
  local x=${OFFSET%% *}
  local y=${OFFSET##* }
  "$CONKY_BIN" -c "$CONKY_CONF" -x"$x" -y"$y" &
  CONKY_PID=$!
  log "anchored at x=$x y=$y relative to monitor 0 (pid $CONKY_PID)"
}

# A dead-but-unreaped child still answers `kill -0`, so read the process state.
conky_alive() {
  local state
  state=$(sed -n 's/^State:[[:space:]]*\([A-Z]\).*/\1/p' "/proc/${CONKY_PID:-0}/status" 2>/dev/null)
  case $state in
    "" | Z | X) return 1 ;;
  esac
  return 0
}

stop_conky() {
  [ -n "$CONKY_PID" ] || return 0
  kill "$CONKY_PID" 2>/dev/null
  for _ in $(seq 1 40); do
    conky_alive || break
    sleep 0.1
  done
  conky_alive && kill -9 "$CONKY_PID" 2>/dev/null
  wait "$CONKY_PID" 2>/dev/null
  CONKY_PID=""
}

# `systemctl --user restart/stop` TERMs the whole cgroup, but take conky down
# explicitly so the child can never outlive the unit or get re-anchored
# mid-shutdown.
on_term() {
  trap - TERM INT
  [ -n "$SLEEP_PID" ] && kill "$SLEEP_PID" 2>/dev/null
  stop_conky
  exit 0
}
trap on_term TERM INT

# 1. Xwayland display — the number varies per boot.
D=""
for _ in $(seq 1 90); do
  D=$(pgrep -a Xwayland 2>/dev/null | grep -oE ' :[0-9]+' | head -1 | tr -d ' ')
  [ -n "$D" ] && break
  nap 2
done
[ -n "$D" ] || { log "no Xwayland display appeared; giving up"; exit 1; }
export DISPLAY=$D

# 2. Settle, then anchor.
for _ in $(seq 1 "$BOOT_TRIES"); do
  settle_step && break
  nap "$BOOT_POLL"
done
if [ "$STABLE" -ge "$SETTLE_READS" ]; then
  OFFSET=$PENDING
else
  # Degraded-but-visible beats invisible: if it never settled, go with the last
  # readable sample rather than exiting and leaving the strip blank.
  [ -n "$LAST" ] || { log "RandR never reported both monitor 0 and $STRIP_OUTPUT"; exit 1; }
  log "layout never settled; anchoring on the last readable sample"
  OFFSET=$LAST
fi
start_conky

# 3. Keep watching. conky's -x/-y are resolved once at startup, so a COSMIC
# display rearrange (or a monitor waking late) strands the panel at its old
# absolute position — seen 2026-08-05, drawn across the Acer while the strip had
# moved to +4423+1920, and only a manual unit restart fixed it. Re-anchor when
# the offset changes and then holds still. Keying on the offset rather than the
# raw layout means unrelated churn (another monitor's mode change, a game
# switching resolution on a head that doesn't move us) costs nothing: a relaunch
# resets the panel's graph history, so only tear one down when it has to be.
while :; do
  nap "$WATCH_POLL"

  # conky gone (crash, X connection lost): surface its status the way the old
  # `exec conky` did, so the unit's Restart=on-failure still decides what next.
  if ! conky_alive; then
    wait "$CONKY_PID" 2>/dev/null
    rc=$?
    log "conky exited ($rc); handing the restart decision to systemd"
    exit "$rc"
  fi

  settle_step || continue
  [ "$PENDING" = "$OFFSET" ] && continue
  log "layout moved: re-anchoring from ($OFFSET) to ($PENDING)"
  stop_conky
  OFFSET=$PENDING
  start_conky
done
