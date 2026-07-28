#!/usr/bin/env bash
# Boot-robust launcher for the sensor-strip conky (8.8" 1920x480, HDMI-A-1).
# Xwayland's display number and monitor order can change between boots, so:
#  1. discover the session's Xwayland display,
#  2. wait for the strip output to appear in RandR,
#  3. position conky relative to X monitor 0 (conky's default anchor head).
set -u

D=""
for _ in $(seq 1 90); do
  D=$(pgrep -a Xwayland 2>/dev/null | grep -oE ' :[0-9]+' | head -1 | tr -d ' ')
  [ -n "$D" ] && break
  sleep 2
done
[ -n "$D" ] || exit 1
export DISPLAY=$D

# Wait for the layout to STOP CHANGING, not merely to be readable. Monitors
# wake staggered at login, so monitor 0 is transiently whichever output came up
# first -- sampling mid-settle bakes a wrong anchor in for the whole session
# (seen 2026-07-27: sampled head 0 = HDMI-A-3 @+5360, so the strip drew 3440px
# off-target until a manual restart resampled it as DP-3 @+1920).
STRIP="" M0="" PREV="" STABLE=0
for _ in $(seq 1 60); do
  MON=$(xrandr --listmonitors 2>/dev/null)
  STRIP=$(printf '%s\n' "$MON" | awk '$NF=="HDMI-A-1"{print $3}')
  M0=$(printf '%s\n' "$MON" | awk '$1=="0:"{print $3}')
  if [ -n "$STRIP" ] && [ -n "$M0" ] && [ "$MON" = "$PREV" ]; then
    STABLE=$((STABLE + 1))
    [ "$STABLE" -ge 3 ] && break
  else
    STABLE=0
  fi
  PREV=$MON
  sleep 2
done
# Degraded-but-visible beats invisible: if it never settled, go with the last
# readable sample rather than exiting and leaving the strip blank.
[ -n "$STRIP" ] && [ -n "$M0" ] || exit 1

SX=$(printf '%s' "$STRIP" | grep -oE '\+[0-9]+\+[0-9]+$' | cut -d+ -f2)
SY=$(printf '%s' "$STRIP" | grep -oE '\+[0-9]+\+[0-9]+$' | cut -d+ -f3)
PX=$(printf '%s' "$M0" | grep -oE '\+[0-9]+\+[0-9]+$' | cut -d+ -f2)
PY=$(printf '%s' "$M0" | grep -oE '\+[0-9]+\+[0-9]+$' | cut -d+ -f3)

exec /usr/bin/conky -c "$HOME/.config/conky/sensor-strip.conf" -x"$((SX-PX))" -y"$((SY-PY))"
