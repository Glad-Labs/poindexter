#!/usr/bin/env bash
# Behaviour check for launch-strip.sh. Stubs xrandr / pgrep / conky and drives
# the settle + re-anchor loops at ~20x speed, so it runs anywhere: no X server,
# no conky, no systemd, and it never touches the running strip.
#
#   ./test-launch-strip.sh
set -u

HERE=$(cd "$(dirname "$0")" && pwd)
LAUNCHER=$HERE/launch-strip.sh
TMP=$(mktemp -d)
STUBS=$TMP/stubs
POLL=0.3 # settle/watch interval the launcher runs at under test
FAILURES=0
STATE=""
STATE_N=0
LAUNCHER_PID=""
EXIT_STATUS=""

cleanup_all() {
  [ -n "$LAUNCHER_PID" ] && kill -9 "$LAUNCHER_PID" 2>/dev/null
  local p
  for p in $(cat "$TMP"/state.*/conky-pids 2>/dev/null); do
    kill -9 "$p" 2>/dev/null
  done
  rm -rf "$TMP"
}
trap cleanup_all EXIT

# ---------------------------------------------------------------- stubs ----

mkdir -p "$STUBS"

cat > "$STUBS/pgrep" <<'EOF'
#!/usr/bin/env bash
echo "4242 /usr/bin/Xwayland :1 -rootless -core"
EOF

# Prints $STUB_STATE/current, or -- in churn mode -- a layout that never repeats,
# so the launcher can never collect three identical reads.
cat > "$STUBS/xrandr" <<'EOF'
#!/usr/bin/env bash
echo tick >> "$STUB_STATE/calls"
if [ -f "$STUB_STATE/churn" ]; then
  n=$(wc -l < "$STUB_STATE/calls")
  printf 'Monitors: 2\n 0: +*DP-3 3440/790x1440/350+0+0  DP-3\n 1: +HDMI-A-1 1920/50x480/210+%s+1920  HDMI-A-1\n' "$((4000 + n))"
  exit 0
fi
cat "$STUB_STATE/current" 2>/dev/null
EOF

# Records how it was launched, then parks on the same pid so the launcher's
# liveness check and teardown see a real process.
cat > "$STUBS/conky" <<'EOF'
#!/usr/bin/env bash
echo "$$" >> "$STUB_STATE/conky-pids"
printf '%s\n' "$*" >> "$STUB_STATE/conky-argv"
exec sleep 100000
EOF

chmod +x "$STUBS"/pgrep "$STUBS"/xrandr "$STUBS"/conky

# --------------------------------------------------------------- helpers ----

ok() { printf '  ok    %s\n' "$1"; }
fail() {
  printf '  FAIL  %s\n' "$1"
  FAILURES=$((FAILURES + 1))
}
check() { # check <what> <expected> <actual>
  if [ "$2" = "$3" ]; then ok "$1"; else fail "$1 — expected [$2], got [$3]"; fi
}

pid_alive() {
  local st
  st=$(sed -n 's/^State:[[:space:]]*\([A-Z]\).*/\1/p' "/proc/$1/status" 2>/dev/null)
  case $st in
    "" | Z | X) return 1 ;;
  esac
  return 0
}

new_state() {
  STATE_N=$((STATE_N + 1))
  STATE=$TMP/state.$STATE_N
  mkdir -p "$STATE"
  : > "$STATE/calls"
  : > "$STATE/conky-argv"
  : > "$STATE/conky-pids"
  : > "$STATE/current"
}

set_layout() { # set_layout <m0-x> <m0-y> <strip-x> <strip-y>
  printf 'Monitors: 2\n 0: +*DP-3 3440/790x1440/350+%s+%s  DP-3\n 1: +HDMI-A-1 1920/50x480/210+%s+%s  HDMI-A-1\n' \
    "$1" "$2" "$3" "$4" > "$STATE/current.tmp"
  mv "$STATE/current.tmp" "$STATE/current" # atomic: no half-written read
}

reads_now() { wc -l < "$STATE/calls" | tr -d ' '; }
launch_count() { wc -l < "$STATE/conky-argv" | tr -d ' '; }
launch_args() { sed -n "${1}p" "$STATE/conky-argv"; }
launch_pid() { sed -n "${1}p" "$STATE/conky-pids"; }

wait_reads() { # wait_reads <n> [timeout-s]
  local want deadline
  want=$(($(reads_now) + $1))
  deadline=$((SECONDS + ${2:-15}))
  while [ "$(reads_now)" -lt "$want" ]; do
    [ "$SECONDS" -ge "$deadline" ] && return 1
    sleep 0.02
  done
}

wait_launches() { # wait_launches <n> [timeout-s]
  local deadline=$((SECONDS + ${2:-15}))
  while [ "$(launch_count)" -lt "$1" ]; do
    [ "$SECONDS" -ge "$deadline" ] && return 1
    sleep 0.02
  done
}

await_exit() { # await_exit <pid> <timeout-s> -> EXIT_STATUS
  local deadline=$((SECONDS + $2))
  while pid_alive "$1"; do
    [ "$SECONDS" -ge "$deadline" ] && return 1
    sleep 0.05
  done
  wait "$1" 2>/dev/null
  EXIT_STATUS=$?
}

start_launcher() { # start_launcher [boot-tries]
  CONKY_BIN="$STUBS/conky" CONKY_CONF=/dev/null STRIP_OUTPUT=HDMI-A-1 \
    SETTLE_READS=3 BOOT_POLL=$POLL BOOT_TRIES=${1:-40} WATCH_POLL=$POLL \
    STUB_STATE="$STATE" PATH="$STUBS:$PATH" \
    bash "$LAUNCHER" > "$STATE/log" 2>&1 &
  LAUNCHER_PID=$!
}

stop_launcher() {
  [ -n "$LAUNCHER_PID" ] || return 0
  kill -TERM "$LAUNCHER_PID" 2>/dev/null
  await_exit "$LAUNCHER_PID" 5 || kill -9 "$LAUNCHER_PID" 2>/dev/null
  LAUNCHER_PID=""
}

# ----------------------------------------------------------------- tests ----

# The 2026-07-27 failure: monitors wake staggered, so the first readable layout
# is not the real one.
t_settles_before_anchoring() {
  echo "settles before anchoring"
  new_state
  set_layout 5360 0 8800 0 # transient wake-order layout
  start_launcher
  wait_reads 1 || fail "launcher never read RandR"
  set_layout 0 480 4423 1920 # the real layout: offset 4423 1440
  if wait_launches 1 10; then
    check "anchors on the settled layout" "-c /dev/null -x4423 -y1440" "$(launch_args 1)"
  else
    fail "conky never launched"
  fi
  wait_reads 3
  check "launched exactly once" "1" "$(launch_count)"
  stop_launcher
}

# The 2026-08-05 failure: the layout moved under a running conky, whose -x/-y
# were resolved once at startup.
t_reanchors_when_the_layout_moves() {
  echo "re-anchors when the layout moves"
  new_state
  set_layout 0 480 4423 1920
  start_launcher
  wait_launches 1 10 || {
    fail "conky never launched"
    stop_launcher
    return
  }
  local first
  first=$(launch_pid 1)

  set_layout 0 480 100 200 # rearranged: offset 100 -280
  wait_reads 2
  check "holds until the new offset is stable" "1" "$(launch_count)"

  if wait_launches 2 10; then
    check "re-anchors on the new offset" "-c /dev/null -x100 -y-280" "$(launch_args 2)"
    if pid_alive "$first"; then fail "old conky survived the re-anchor"; else ok "old conky torn down"; fi
    if pid_alive "$(launch_pid 2)"; then ok "new conky running"; else fail "new conky not running"; fi
  else
    fail "never re-anchored after the layout moved"
  fi
  stop_launcher
}

# A monitor asleep/unplugged, or a transient xrandr failure, must not cost a
# working panel.
t_holds_the_anchor_when_randr_is_unreadable() {
  echo "holds the anchor when RandR is unreadable"
  new_state
  set_layout 0 480 4423 1920
  start_launcher
  wait_launches 1 10 || {
    fail "conky never launched"
    stop_launcher
    return
  }
  local pid
  pid=$(launch_pid 1)
  : > "$STATE/current" # xrandr now reports nothing
  wait_reads 4
  check "no relaunch on unreadable reads" "1" "$(launch_count)"
  if pid_alive "$pid"; then ok "panel left running"; else fail "panel torn down on an unreadable read"; fi
  stop_launcher
}

# Degraded-but-visible beats invisible.
t_launches_degraded_when_it_never_settles() {
  echo "launches degraded when the layout never settles"
  new_state
  touch "$STATE/churn"
  start_launcher 6
  if wait_launches 1 15; then
    case "$(launch_args 1)" in
      "-c /dev/null -x40"*" -y1920") ok "anchored on the last readable sample" ;;
      *) fail "unexpected degraded anchor: $(launch_args 1)" ;;
    esac
  else
    fail "never launched despite readable samples"
  fi
  if grep -q "never settled" "$STATE/log"; then ok "logged the degraded anchor"; else fail "degraded anchor not logged"; fi
  stop_launcher
}

# `systemctl --user restart/stop` has to take the panel with it.
t_term_stops_conky_too() {
  echo "SIGTERM stops conky too"
  new_state
  set_layout 0 480 4423 1920
  start_launcher
  wait_launches 1 10 || {
    fail "conky never launched"
    stop_launcher
    return
  }
  local pid
  pid=$(launch_pid 1)
  kill -TERM "$LAUNCHER_PID"
  if await_exit "$LAUNCHER_PID" 5; then
    check "launcher exits cleanly" "0" "$EXIT_STATUS"
  else
    fail "launcher ignored SIGTERM"
  fi
  LAUNCHER_PID=""
  if pid_alive "$pid"; then fail "conky outlived the unit"; else ok "conky stopped with the launcher"; fi
}

# `exec conky` used to make conky's exit the unit's exit; keep that so
# Restart=on-failure still decides.
t_propagates_conky_exit() {
  echo "propagates conky's exit status"
  new_state
  set_layout 0 480 4423 1920
  start_launcher
  wait_launches 1 10 || {
    fail "conky never launched"
    stop_launcher
    return
  }
  kill -TERM "$(launch_pid 1)"
  if await_exit "$LAUNCHER_PID" 10; then
    check "exits with conky's status" "143" "$EXIT_STATUS"
  else
    fail "launcher outlived its conky"
  fi
  LAUNCHER_PID=""
}

# ------------------------------------------------------------------ main ----

bash -n "$LAUNCHER" || {
  echo "launch-strip.sh does not parse"
  exit 1
}

t_settles_before_anchoring
t_reanchors_when_the_layout_moves
t_holds_the_anchor_when_randr_is_unreadable
t_launches_degraded_when_it_never_settles
t_term_stops_conky_too
t_propagates_conky_exit

if [ "$FAILURES" -eq 0 ]; then
  echo "all checks passed"
else
  echo "$FAILURES check(s) failed"
fi
exit $((FAILURES > 0))
