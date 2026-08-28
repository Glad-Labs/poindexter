#!/usr/bin/env bash
# Bench: what holds ollama-vision's ~9.3 GiB host-side anonymous shadow, and
# does use_mmap change it? Metric is TOTAL ANONYMOUS = RssAnon + VmSwap, which
# is invariant to whether the kernel has swapped it out yet (RSS alone drops to
# ~0 within minutes and would read as "no shadow").
set -uo pipefail
URL=http://localhost:11435
MODEL=qwen3-vl:30b

runner_pid() { for p in $(pgrep -x llama-server 2>/dev/null); do
    grep -q "$(systemctl show ollama-vision.service -p MainPID --value)" /proc/$p/status 2>/dev/null
    tr '\0' ' ' < /proc/$p/cmdline 2>/dev/null | grep -q 'mmproj' && { echo "$p"; return; }
  done; }

measure() { # $1 = label
  local p; p=$(runner_pid)
  if [ -z "$p" ]; then printf '%-26s runner=NONE\n' "$1"; return; fi
  awk -v L="$1" -v P="$p" '
    /^RssAnon/{a=$2} /^VmSwap/{s=$2} /^VmRSS/{r=$2} /^VmHWM/{h=$2}
    END{printf "%-26s pid=%-8s anon_total=%6.2f GiB (rss_anon=%5.2f + swap=%5.2f)  peak_rss=%5.2f\n",
        L,P,(a+s)/1048576,a/1048576,s/1048576,h/1048576}' /proc/$p/status
}

flags() { local p; p=$(runner_pid); [ -n "$p" ] && \
  tr '\0' ' ' < /proc/$p/cmdline | grep -oE '\-\-no-mmap' || echo "(mmap on)"; }

unload() { curl -s --max-time 60 "$URL/api/generate" \
  -d "{\"model\":\"$MODEL\",\"keep_alive\":0}" >/dev/null 2>&1
  for _ in $(seq 1 30); do [ -z "$(runner_pid)" ] && break; sleep 2; done; }

load() { # $1 = extra options json  $2 = keep_alive
  curl -s --max-time 400 "$URL/api/generate" \
    -d "{\"model\":\"$MODEL\",\"prompt\":\"hi\",\"stream\":false,\"keep_alive\":$2,\"options\":{$1}}" \
    >/dev/null 2>&1; }

restore() { echo; echo "--- RESTORING pinned production state ---"
  unload; load "" -1; sleep 3; measure "RESTORED (mmap default)"; echo "  flags: $(flags)"; }
trap restore EXIT

echo "=== PHASE 0: baseline (as found) ==="
measure "baseline"; echo "  flags: $(flags)"

echo; echo "=== PHASE 1: unload — does the shadow belong to the runner? ==="
unload; sleep 2; measure "after unload"

echo; echo "=== PHASE 2: load, mmap ON (default) ==="
load "" -1
for t in 0 60 150; do [ "$t" -gt 0 ] && sleep $((t==60?60:90)); measure "mmap=on  t+${t}s"; done
echo "  flags: $(flags)"

echo; echo "=== PHASE 3: reload with use_mmap=false ==="
unload; sleep 2
load "\"use_mmap\":false" -1
for t in 0 60 150; do [ "$t" -gt 0 ] && sleep $((t==60?60:90)); measure "mmap=off t+${t}s"; done
echo "  flags: $(flags)   <-- must show --no-mmap or the arm is INVALID"
