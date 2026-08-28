#!/usr/bin/env bash
# Does the vision runner's anonymous footprint grow PER REQUEST?
# The mmap A/B showed a fresh runner at 0.30 GiB and a 5.5h-old one at 9.35 GiB,
# so the shadow accumulates during service, not at load.
set -uo pipefail
URL=http://localhost:11435
MODEL=qwen3-vl:30b
pid() { for p in $(pgrep -x llama-server 2>/dev/null); do
  tr '\0' ' ' < /proc/$p/cmdline 2>/dev/null | grep -q mmproj && { echo "$p"; return; }; done; }
anon() { local p=$1; awk '/^RssAnon/{a=$2}/^VmSwap/{s=$2}END{printf "%.3f",(a+s)/1048576}' /proc/$p/status; }

P=$(pid); echo "runner pid=$P  start_anon=$(anon $P) GiB"
echo
printf '%-8s %-12s %s\n' "reqs" "anon_GiB" "delta_from_start"
START=$(anon $P)
for batch in 1 2 3 4 5 6; do
  for i in $(seq 1 5); do
    curl -s --max-time 120 "$URL/api/generate" \
      -d "{\"model\":\"$MODEL\",\"prompt\":\"Summarise in one short sentence why batching matters. Iteration $batch-$i.\",\"stream\":false,\"keep_alive\":-1,\"options\":{\"num_predict\":48}}" \
      >/dev/null 2>&1
  done
  A=$(anon $P)
  printf '%-8s %-12s %+.3f\n' "$((batch*5))" "$A" "$(echo "$A - $START" | bc -l)"
  # a runner restart would invalidate the series
  [ "$(pid)" != "$P" ] && { echo "  !! runner pid changed — series invalid"; break; }
done
echo
echo "final smaps_rollup:"
awk '/^Anonymous:|^Swap:|^Rss:/{printf "  %-12s %7.2f GiB\n",$1,$2/1048576}' /proc/$P/smaps_rollup
