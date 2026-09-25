# Routing specific models to a different LLM endpoint

By default every LLM call Poindexter makes goes to one endpoint — the
`api_base` in the LiteLLM provider config (your Ollama instance). The
`model_api_base_overrides` map lets you route _specific models_ somewhere
else, per model, without touching code:

```json
// app_settings key: plugin.llm_provider.litellm
{
  "enabled": true,
  "config": {
    "api_base": "http://host.docker.internal:11434",
    "timeout_seconds": 300,
    "drop_params": true,
    "model_api_base_overrides": {
      "ollama/qwen3-vl:30b": "http://host.docker.internal:11435"
    }
  }
}
```

Any model **not** in the map keeps using the default `api_base`. An empty
or absent map is a no-op — if you run everything on one GPU against one
Ollama, you can ignore this page entirely; nothing about Poindexter
requires a second endpoint.

## When you'd use it

- **Pin an eviction-prone model to a second GPU.** With multiple hot
  models sharing one Ollama, a big writer reload can evict a QA-rail
  model mid-pipeline; the cold reload then times out and the rail skips.
  Running a second Ollama instance pinned to another GPU (see recipe
  below) and routing just that model to it removes the eviction path.
  This is how the reference deployment runs its vision-QA model.
- **Borrow VRAM from another machine.** Point a model at any Ollama you
  can reach — an old gaming PC on the LAN, a box over Tailscale:
  `{"ollama/qwen3-vl:30b": "http://192.168.1.50:11434"}`.
- **Offload a small model to a CPU-only instance** so it never competes
  for GPU memory at all.

## Behavior details

- **Keys match the _resolved_ model name.** Bare names get the default
  prefix first (`qwen3-vl:30b` → `ollama/qwen3-vl:30b`), so key your map
  with the prefix and both spellings match.
- **The value may be a dict or a JSON string** (app_settings TEXT rows
  arrive as strings). Invalid JSON or a wrong type is logged and
  ignored — dispatch falls back to the default `api_base`, never fails.
- **The paid-endpoint policy applies to the effective endpoint.** An
  override pointing at a cloud host (e.g. `https://api.openai.com/v1`)
  is refused unless the paid gate is open — an override can't smuggle a
  paid endpoint past the local-only default. Open the gate by setting
  `plugin.llm_provider.litellm.allow_paid_base_url` to `true` (with
  `poindexter settings set`); the dispatcher folds that flat row into the
  provider config, so you don't hand-edit the nested JSON blob.
- Both streaming and non-streaming calls honor the map. All LLM traffic
  flows through the LiteLLM provider, so the map covers every pipeline
  stage, QA rail, and job.
- **A routed model skips the shared GPU lock.** Poindexter serializes local
  LLM dispatches behind one GPU lock (`gpu_serialize_llm_dispatch`) so a model
  load can't collide with an image/video render. A model routed to a
  _different_ endpoint is exempt: it lives on its own server (and, per the
  recipe below, its own card), the eviction path only ever targets the default
  `api_base`, and renders are pinned to `pipeline_gpu_index` — so the lock
  protects a routed model from nothing while forcing it to queue behind the
  primary instance until `gpu_lock_acquire_timeout_seconds` fires. Because the
  vision rail fails soft on timeout, that showed up as `qa.vision` silently
  "passing open" rather than as an error. Set
  `gpu_pinned_endpoint_skips_lock=false` if your second instance shares one
  physical GPU with the first — two servers on one card genuinely do contend.
- **A routed model runs at one context size.** An instance that holds one
  model (`OLLAMA_MAX_LOADED_MODELS=1`, `KEEP_ALIVE=-1`) holds it at one
  `num_ctx`. Ollama reloads a resident model whenever a request asks for a
  different one, and for a ~20 GB vision judge that is a 10-40 s cold load in
  the middle of a QA rail. So every dispatch routed through this map runs at
  `pinned_llm_endpoint_num_ctx` (default 16384). That overrides the caller's
  `num_ctx` and every per-phase `*_num_ctx` key, including
  `qa_ragas_judge_num_ctx` and `qa_deepeval_judge_num_ctx`, which only size a
  judge that is _not_ pinned. The first time an override changes a size, a
  warning names the phase. `WarmPinnedLlmEndpointsJob` warms at the same size,
  and the brain's Ollama RAM-recycle re-pins at it. Anything outside Poindexter
  that sends no `num_ctx` gets the instance's `OLLAMA_CONTEXT_LENGTH`, so set
  that to the same value in the wrapper (the recipe below does). If the warm
  job finds the model resident at any other size it raises
  `pinned_endpoint_context_mismatch` and leaves the model loaded, because
  re-warming would fight the other caller. Set the key to `0` to go back to
  per-phase sizes. Before this rule the reference judge logged 48 loads in one
  day (2026-09-24), alternating 16384 / 32768 / 8192.

## Recipe: a second Ollama pinned to a specific GPU

Ollama has no per-model GPU affinity — one instance schedules across
every GPU it can see. Hard placement means a second instance that can
only see the target GPU. On Linux the reference implementation is
`scripts/linux/ollama-vision.sh`, run as the `ollama-vision.service`
systemd unit. On Windows it was `scripts/ollama-vision-gpu1.ps1`,
registered as a scheduled task by `scripts/background-services.ps1`:

```powershell
# Resolve the target GPU's UUID (index from `nvidia-smi -L`)
$gpuUuid = (& nvidia-smi --query-gpu=uuid --format=csv,noheader -i 1).Trim()

$env:CUDA_DEVICE_ORDER = "PCI_BUS_ID"
$env:CUDA_VISIBLE_DEVICES = $gpuUuid   # UUID form — bare indices are unreliable
$env:OLLAMA_VULKAN = "false"           # REQUIRED — see gotchas
$env:OLLAMA_HOST = "127.0.0.1:11435"
$env:OLLAMA_KEEP_ALIVE = "-1"          # keep the routed model warm forever
$env:OLLAMA_MAX_LOADED_MODELS = "1"
$env:OLLAMA_CONTEXT_LENGTH = "16384"   # = pinned_llm_endpoint_num_ctx
& "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe" serve
```

Then set the override map to point the model at `:11435`.
`WarmPinnedLlmEndpointsJob` loads it within five minutes. To warm it by hand,
send `options.num_ctx` equal to `pinned_llm_endpoint_num_ctx`, or the first
real call reloads it at that size. With `OLLAMA_KEEP_ALIVE=-1` it stays
resident; the primary instance cannot evict it because it's a separate server
process.

### Gotchas (each of these cost us a debugging round)

1. **`OLLAMA_VULKAN=false` is load-bearing.** Ollama 0.31+ enables a
   Vulkan backend by default whose GPU enumeration ignores
   `CUDA_VISIBLE_DEVICES`. Without this, the scheduler still sees your
   other GPU as `Vulkan0` and will happily place the model there —
   usually on whichever card has more free VRAM, which is exactly the
   card you were trying to avoid.
2. **Pin by GPU UUID, not bare index.** The UUID form
   (`GPU-xxxxxxxx-...`) confines reliably and survives enumeration-order
   changes across driver updates.
3. **Scheduled tasks / services don't inherit your user environment.**
   If your models directory or tuning flags live in user-level env vars
   (`OLLAMA_MODELS`, `OLLAMA_FLASH_ATTENTION`, ...), the wrapper script
   must read them from the registry explicitly or the instance falls
   back to defaults and reports every model as missing.
4. **Loopback bind is enough for Docker.** Docker Desktop proxies
   `host.docker.internal` to the host loopback, so `127.0.0.1:11435`
   is reachable from the worker container with no firewall exposure.
5. **Verify placement with the serve log, not `nvidia-smi` deltas.**
   The instance's `inference compute` / `selecting single GPU` log lines
   are ground truth for which device a model landed on; WDDM memory
   readouts lag and mislead, especially with other GPU consumers active.
6. **A logon-only trigger doesn't come back from a clean exit.**
   `background-services.ps1` registers this (and every background
   service) with both an `AtLogOn` trigger and a repeating 5-min trigger
   (poindexter#860) specifically because Windows' own
   `RestartCount`/`RestartInterval` only fires on a _nonzero_ exit code —
   if `ollama.exe serve` exits 0 (observed during a host crash/reboot
   sequence), the task just goes quiet and, on a box that stays logged in
   for days, doesn't come back until the next interactive logon. That
   left the vision instance dead for 24h+ and starved the `qa.vision` QA
   rail (advisory-only, so it didn't block publishing, but it also wasn't
   running — see GlitchTip issue #877). If you ever see connection
   refusals to `:11435` with sub-10ms latency (an instant refusal, not a
   slow/contended timeout), check `tasklist | grep ollama` for a missing
   second process before assuming GPU contention.
7. **Every context-size change is a full reload.** A pinned judge that
   answers slowly with a tiny decode time is usually reloading, not slow:
   look for a huge `cost_logs.duration_ms` beside a small
   `decode_duration_ms`. The serve log records every load with its size.
   Count them by size (Linux, systemd unit):
   `journalctl -u ollama-vision.service --since today | grep -o 'starting llama-server.* -c [0-9]*' | grep -o ' -c [0-9]*$' | sort | uniq -c`.
   A healthy instance shows one size, loaded once per restart or RAM
   recycle. Two sizes mean a caller is bypassing the dispatcher. Map the
   loads to callers with the `[GIN]` lines that complete right after each
   `starting llama-server` line: their client IP names the container, and
   `::1` is a process on the host.

## Verifying a routed model end-to-end

1. `curl <override-base>/api/version` from inside the worker container
   (`docker exec poindexter-worker curl http://host.docker.internal:11435/api/version`).
2. Dispatch the routed model through the pipeline (or wait for its next
   natural call) and confirm the _default_ instance never loads it
   (`ollama ps` against the default base stays clean).
3. If you pinned it to a GPU, watch that GPU's utilization spike during
   the call.
4. Confirm it stays at one context: `curl <override-base>/api/ps` shows
   `context_length` equal to `pinned_llm_endpoint_num_ctx`, and the serve
   log's load count (gotcha 7) does not grow between recycles.
