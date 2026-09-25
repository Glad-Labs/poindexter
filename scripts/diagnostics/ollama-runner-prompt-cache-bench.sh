#!/usr/bin/env bash
# What does ONE request cost an ollama runner in host memory, by request shape?
#
# The growth this measures is llama-server's host-RAM PROMPT CACHE, not a leak
# (re-measured 2026-09-25; the 2026-08-28 "leak" reading was this cache seen
# over too few requests to reach its cap). When a new task arrives, the idle
# slot's KV state is copied to host memory so a later prompt sharing its prefix
# can restore it instead of re-prefilling (llama.cpp #16391). Ollama 0.32 starts
# llama-server with no --cache-ram, so the upstream default cap applies: 8192
# MiB. Each entry costs (tokens in the saved state) x (KV bytes per token), and
# at the cap the cache evicts oldest-first, so the runner PLATEAUS instead of
# growing forever.
#
# Arms, each N /api/chat requests at the pinned num_ctx:
#   repeat      the SAME short prompt every time. A leak grows on every request;
#               a cache that already holds the prompt does not.
#   text-short  unique ~70-token prompts (the 2026-08-28 bench's shape)
#   text-long   unique ~3,800-token prompts (a QA-rail-sized context)
#   image       unique 1280x720 frames + a short prompt (the qa_shot_vision
#               shape; the runner starts with --image-min-tokens 1024)
#
# The saved state lags one request: the state of request k is written when
# request k+1 arrives. So each arm is measured from AFTER its first request to
# after its last: that window holds exactly the saves of the arm's own first
# N-1 requests (the carry-in from the previous arm lands before it), and those
# saves are read back from the runner's log ("saving prompt with length L,
# total state size = S MiB") beside the /proc delta; "untracked" is the gap
# between the two. "foreign" counts requests
# from other clients inside the arm's window; nonzero means that arm is
# contaminated, so rerun it when the box is quiet.
#
# Side effect: the arms ADD cache entries to the live runner (~2 GiB at the
# defaults). The brain's ollama_runner_ram_watch returns that memory on its
# next recycle; set RESTORE=1 to unload and re-pin at the end instead (one
# 40-65 s reload of the judge, measured over 2026-09-25's recycles).
set -uo pipefail
URL=${URL:-http://localhost:11435}
MODEL=${MODEL:-qwen3-vl:30b-a3b-instruct}
UNIT=${UNIT:-ollama-vision.service}
# MUST equal app_settings.pinned_llm_endpoint_num_ctx. Ollama reloads a resident
# model for any other num_ctx, which starts a new runner and voids the series.
NUM_CTX=${NUM_CTX:-16384}
N=${N:-6}
ARMS=${ARMS:-"repeat text-short text-long image"}
RESTORE=${RESTORE:-0}
# Mixed into every prompt and frame. Without it a second run replays the first
# run's exact requests, the cache already holds them, and each save REPLACES an
# entry instead of adding one: growth reads far below the logged saves. (That
# is the repeat arm's result arriving by accident, not a measurement.)
NONCE=${NONCE:-$(date +%s%N)}
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

runner_pid() {  # the llama-server in $UNIT's cgroup, never a cmdline match
  local p
  for p in $(pgrep -x llama-server 2>/dev/null); do
    grep -q "/$UNIT" "/proc/$p/cgroup" 2>/dev/null && { echo "$p"; return; }
  done
}
anon_mib() { awk '/^RssAnon/{a=$2}/^VmSwap/{s=$2}END{printf "%.1f",(a+s)/1024}' "/proc/$1/status"; }
cursor() { journalctl -u "$UNIT" -n 0 --show-cursor --no-pager 2>/dev/null | sed -n 's/^-- cursor: //p'; }

payload() {  # $1=arm $2=index -> $WORK/req.json
  python3 - "$1" "$2" "$MODEL" "$NUM_CTX" "$WORK/req.json" "$NONCE" <<'PY'
import base64, io, json, sys

arm, i, model, num_ctx, out, nonce = (
    sys.argv[1], int(sys.argv[2]), sys.argv[3], int(sys.argv[4]), sys.argv[5], sys.argv[6])
seed = int(nonce) % 997 + i
msg = {"role": "user"}
if arm == "repeat":
    msg["content"] = f"Summarise in one short sentence why batching matters. Run {nonce}."
elif arm == "text-short":
    msg["content"] = f"Summarise in one short sentence why batching matters. Variant {nonce}-{i}."
elif arm == "text-long":
    rows = [f"Record {nonce}-{i}-{k}: sensor {k * 7 % 97} read {(k * 13 + seed) % 1000} units at step {k}."
            for k in range(160)]
    msg["content"] = "\n".join(rows) + "\nWhich record has the highest reading? Answer with its number only."
elif arm == "image":
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (1280, 720), (seed * 53 % 256, seed * 97 % 256, seed * 151 % 256))
    draw = ImageDraw.Draw(img)
    for k in range(12):
        top = (k * (seed + 3) * 37) % 600
        draw.rectangle([k * 100, top, k * 100 + 80, top + 100],
                       fill=((k * 40 + seed * 20) % 256, 255 - k * 20, seed * 30 % 256))
    draw.text((40, 40), f"frame {nonce}-{i}", fill=(255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    msg["content"] = "Describe this frame in one short sentence."
    msg["images"] = [base64.b64encode(buf.getvalue()).decode()]
else:
    sys.exit(f"unknown arm {arm!r}")
body = {"model": model, "messages": [msg], "stream": False, "keep_alive": -1,
        "options": {"num_ctx": num_ctx, "num_predict": 32, "temperature": 0}}
with open(out, "w") as fh:
    json.dump(body, fh)
PY
}

send() { curl -s --max-time 300 "$URL/api/chat" -H 'Content-Type: application/json' -d @"$WORK/req.json" >/dev/null; }

loaded_ctx=$(curl -s --max-time 10 "$URL/api/ps" | jq -r --arg m "$MODEL" '.models[] | select(.name == $m) | .context_length')
if [ "$loaded_ctx" != "$NUM_CTX" ]; then
  echo "refusing: $MODEL is loaded at context_length=${loaded_ctx:-none} on $URL, but NUM_CTX=$NUM_CTX." >&2
  echo "Sending another size reloads the model and voids the series. Set NUM_CTX to" >&2
  echo "app_settings.pinned_llm_endpoint_num_ctx, or wait for the judge to be re-pinned." >&2
  exit 2
fi
P=$(runner_pid)
[ -n "$P" ] || { echo "no llama-server in $UNIT's cgroup" >&2; exit 2; }
echo "runner pid=$P  anon=$(anon_mib "$P") MiB  model=$MODEL  num_ctx=$NUM_CTX  N=$N per arm"
echo
printf '%-11s %3s %12s %12s %12s %11s %10s %8s\n' arm n "d_anon/req" "logged/req" "untracked" "tokens/req" "KiB/token" foreign

for arm in $ARMS; do
  payload "$arm" 0 && send
  sleep 1  # let journald flush request 0's own lines, or they count as foreign
  before=$(anon_mib "$P")
  cur=$(cursor)
  for i in $(seq 1 $((N - 1))); do payload "$arm" "$i" && send; done
  sleep 1
  after=$(anon_mib "$P")
  if [ "$(runner_pid)" != "$P" ]; then
    echo "  !! runner pid changed during '$arm' (recycled or reloaded) — series void"; exit 1
  fi
  journalctl -u "$UNIT" --after-cursor="$cur" --no-pager -o cat > "$WORK/log" 2>/dev/null
  # Saves logged in this window are the states of this arm's first N-1 requests.
  read -r saves tokens mib < <(grep -o 'saving prompt with length [0-9]*, total state size = [0-9.]* MiB' "$WORK/log" |
    awk '{n++; t+=$5; m+=$10} END{printf "%d %.0f %.1f\n", n, (n ? t/n : 0), (n ? m/n : 0)}')
  served=$(grep -c -E '\[GIN\].*"/api/(chat|generate)"' "$WORK/log")
  foreign=$((served - (N - 1)))
  per_req=$(echo "($after - $before) / ($N - 1)" | bc -l)
  kib_tok=$([ "${tokens:-0}" -gt 0 ] && echo "$mib * 1024 / $tokens" | bc -l || echo 0)
  # untracked = host memory each entry holds OUTSIDE its logged KV state. The
  # cap (--cache-ram) is enforced on the logged size only, so for image entries
  # this rides above the cap (the 2026-09-25 plateau sat ~2.4 GiB over 8 GiB).
  untracked=$(echo "$per_req - $mib" | bc -l)
  printf '%-11s %3d %8.1f MiB %8.1f MiB %8.1f MiB %11s %10.1f %8d   (%d saves)\n' \
    "$arm" "$N" "$per_req" "$mib" "$untracked" "$tokens" "$kib_tok" "$foreign" "$saves"
done

echo
echo "runner anon now: $(anon_mib "$P") MiB"
journalctl -u "$UNIT" --since "-10 min" --no-pager -o cat 2>/dev/null | grep 'cache state:' | tail -1 | sed 's/^.*cache state: /cache state: /'

if [ "$RESTORE" = 1 ]; then
  echo "RESTORE=1: unloading and re-pinning at num_ctx=$NUM_CTX"
  curl -s --max-time 60 "$URL/api/generate" -d "{\"model\":\"$MODEL\",\"keep_alive\":0}" >/dev/null
  curl -s --max-time 420 "$URL/api/generate" \
    -d "{\"model\":\"$MODEL\",\"prompt\":\"\",\"stream\":false,\"keep_alive\":-1,\"options\":{\"num_ctx\":$NUM_CTX}}" >/dev/null
  echo "runner pid=$(runner_pid)  anon=$(anon_mib "$(runner_pid)") MiB"
fi
