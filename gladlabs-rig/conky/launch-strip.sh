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

STRIP="" M0=""
for _ in $(seq 1 30); do
  MON=$(xrandr --listmonitors 2>/dev/null)
  STRIP=$(printf '%s\n' "$MON" | awk '$NF=="HDMI-A-1"{print $3}')
  M0=$(printf '%s\n' "$MON" | awk '$1=="0:"{print $3}')
  [ -n "$STRIP" ] && [ -n "$M0" ] && break
  sleep 2
done
[ -n "$STRIP" ] && [ -n "$M0" ] || exit 1

SX=$(printf '%s' "$STRIP" | grep -oE '\+[0-9]+\+[0-9]+$' | cut -d+ -f2)
SY=$(printf '%s' "$STRIP" | grep -oE '\+[0-9]+\+[0-9]+$' | cut -d+ -f3)
PX=$(printf '%s' "$M0" | grep -oE '\+[0-9]+\+[0-9]+$' | cut -d+ -f2)
PY=$(printf '%s' "$M0" | grep -oE '\+[0-9]+\+[0-9]+$' | cut -d+ -f3)

exec conky -c "$HOME/.config/conky/sensor-strip.conf" -x"$((SX-PX))" -y"$((SY-PY))"
