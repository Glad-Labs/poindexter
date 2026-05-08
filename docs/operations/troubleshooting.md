# Troubleshooting

Runbook for issues that have bitten us in production. Each entry has a symptom, a root cause, a fix, and a link to the GitHub issue or commit where it was addressed. When you hit something new, add it here instead of just fixing it — the next person (or next-you) will thank you. Use the public `Glad-Labs/poindexter` tracker for product bugs and the private `Glad-Labs/glad-labs-stack` tracker for operator-specific items.

Entries are ordered by frequency of occurrence, not severity.

---

## Post is "Not Found" on the public site immediately after publishing

**Symptom.** You hit approve on a post, the backend logs say it went live, IndexNow and Google sitemap were pinged, but `https://www.gladlabs.io/posts/<slug>` returns a "Post Not Found" page. Refreshing doesn't help for the first 30 seconds to a few minutes.

**Root cause.** The Next.js frontend's `getPostBySlug` helper in `web/public-site/lib/posts.ts` fetches from the R2 static JSON with `next: { revalidate: 300 }`. That's a 5-minute ISR data cache. When you first requested the post URL _before_ the R2 JSON file existed (for example, during pipeline generation, if Vercel pre-rendered anything), the fetch cached a `null` result. `revalidatePath()` — which the backend calls on publish — only invalidates the route cache, not the data cache for that specific fetch URL. So the stale null keeps being served until either the 5-minute TTL expires or a request triggers a background revalidation.

**Fix.** Hit the URL twice in quick succession. The first request sees the stale null, triggers a background fetch from R2, and the second request (and everything after) gets the fresh post. First-click visitors via social media or search bots only hit once and see the not-found page, which is the real cost.

**Real fix.** Switch the frontend to `revalidateTag` keyed on `post:<slug>` so the backend can invalidate exactly that data cache entry when publishing. Tracked in issue #175.

---

## "Vercel deploy is failing" but the test suite looks green locally

**Symptom.** Discord notification fires "CI failed" or "Vercel deploy failed." Matt checks Vercel dashboard — the deploy job is red. But running `npm run test:ci` and `pytest` locally shows everything passing.

**Root cause.** The GitHub Actions workflow at `.github/workflows/ci.yml` declares `deploy: needs: test`. When the `test` job fails, the `deploy` job is _skipped_, not retried, not failed-for-an-unrelated-reason. In the GitHub Actions UI and downstream notifications this shows up as "CI failed AND Vercel didn't deploy." The Vercel failure is a consequence of the upstream test failure, not an independent Vercel issue.

**Fix.** Check the test job first. Look at the specific failing test. If it's a Python unit test failure, reproduce with `docker exec poindexter-worker python -m pytest tests/unit/ -q`. If it's a frontend test, `cd web/public-site && npm run test:ci`. Fix the test, push, CI re-runs, deploy unblocks automatically.

**Debugging anti-pattern.** Do NOT go poking at Vercel settings, env vars, build commands, or the `next.config.js` first. The Vercel deploy step almost certainly never ran.

**Related.** [docs/operations/ci-deploy-chain.md](./ci-deploy-chain) has the full chain diagram.

---

## Pipeline task stuck "in_progress" for more than 10 minutes

**Symptom.** You queued a content task, it shows `status='in_progress'` in `content_tasks`, but there's no progress in the logs. The 15-minute `TASK_TIMEOUT_SECONDS` in `task_executor.py` doesn't fire. The per-stage timeouts in `STAGE_TIMEOUTS` don't fire either.

**Root cause (historical).** Before the timeout hygiene pass on 2026-04-10, several external call sites (`OllamaClient`, SDXL server, nvidia-smi exporter, `DuckDuckGo` via `run_in_executor`) had either no per-call timeout or a very long one (up to 3600s). When the underlying connection hung in a state that didn't yield to asyncio, `asyncio.wait_for` at the stage level couldn't cancel it. One test task this session hung 20+ minutes in multi_model_qa because of this.

**Fix (current).** The timeout hygiene pass commits (`0bfd4389`, `4168f87b`, `f0c6cc0b`, `2bb77afa`, `3690abfd`) added:

1. Explicit `httpx.Timeout(N, connect=M)` on every `httpx.AsyncClient` constructor in the hot path.
2. Explicit per-request `timeout=N` arguments on every `.post()`, `.get()`, `.head()`.
3. `asyncio.wait_for` wrappers around Ollama calls in `multi_model_qa.py` (90s for the main critic, 60s for each gate, 5s for health checks).
4. `asyncio.wait_for` wrappers around the DDGS thread-executor call in `web_research.py` (20s).

After these fixes, the worst-case stall for any external call is its per-call budget. A hung Ollama, SDXL, DDG, or other service surfaces as a timeout error within ~60-180 seconds, not silently forever.

**If it happens anyway.** Clear the stuck row manually:

```sql
UPDATE content_tasks
SET status='failed', error_message='manually cleared — stuck in_progress'
WHERE task_id='<the-uuid>' AND status='in_progress';
```

Then check the worker logs for the last event tagged with that `task_id` to find what hung. If it's a service we already hardened, check that the commits listed above are actually deployed in the worker container (the `docker cp` hot-patch approach we use loses changes on `docker compose down` — #177 tracks the volume mount fix).

---

## `[MULTI_QA] Ollama returned empty response` every single QA run

**Symptom.** Every time multi_model_qa runs, the worker logs a warning:

```
[MULTI_QA] Ollama returned empty response
[MULTI_QA] Primary critic failed, trying fallback model: gemma3:27b
```

The fallback (gemma3:27b) works cleanly. No task is lost. But every primary critic call wastes ~15 seconds of inference electricity on an empty response.

**Root cause.** Not yet investigated. Leading hypotheses:

1. **Thinking model + max_tokens interaction.** `glm-4.7-5090:latest` is a reasoning model that generates internal thoughts before its visible output. If `qa_thinking_model_max_tokens` (default 1500) is consumed by the thinking phase, the visible response is empty.
2. **Prompt length.** The QA_PROMPT after today's grounding, date, and new gate additions may exceed the model's usable context window.
3. **Temperature.** `qa_temperature=0.3` may be suppressing output on a reasoning model.

**Workaround (current).** The fallback chain handles it. Nothing is broken, just noisy and wasteful.

**Real fix.** Tracked in issue #174. First step: reproduce manually with `ollama run glm-4.7-5090:latest` and a copy of a real prompt.

---

## Approval queue is full, pipeline stops generating new content

**Symptom.** You queue a new content task with `topic: "auto"` and get an error "No fresh topics found" or the executor logs `[THROTTLE] Approval queue full (3/3) — skipping generation`. No new content is being produced even though the pipeline looks healthy.

**Root cause.** `max_approval_queue` in `app_settings` is 3 (the default). The executor won't start a new task when the awaiting_approval queue already has that many items. This is intentional — it prevents runaway generation when a human isn't reviewing — but it can surprise you if you forgot you had unreviewed posts.

**Fix.** Drain the queue. Review, approve, or reject each item in awaiting_approval. The executor will resume on its next 5-second poll once the queue count drops below the threshold.

```sql
SELECT task_id, status, quality_score, LEFT(topic, 60) AS topic
FROM content_tasks
WHERE status = 'awaiting_approval'
ORDER BY created_at DESC;
```

**If you want to raise the cap temporarily:**

```sql
UPDATE app_settings SET value='5' WHERE key='max_approval_queue';
```

The cap is read fresh on each executor poll, so no restart needed.

---

## HTCPCP, satire, and off-brand topic rejection

**Symptom.** A satirical or meta-humor topic reaches awaiting_approval, the content is competently written but it's weird brand-wise. Example: "AS' HTCPCP AI Butler™" on 2026-04-10.

**Root cause.** Topic discovery pulls from Hacker News and Dev.to which occasionally surface joke RFCs and satirical projects. The writer doesn't know the topic is a joke and produces a straight-faced technical takedown, which reads as "missed the joke" to any reader.

**Fix.** Reject the task with `allow_revisions=false` and reason `off_brand`. After today's `_auto_retry_failed_tasks` fix (commits `acdd1640` and `bc0450f1`), rejections with `allow_revisions=false` stay permanently rejected — they don't bounce back via auto-retry.

**Prevention.** There's a feedback rule in the Claude memory system: "Don't queue or approve topics requiring straight-faced handling of RFC jokes, parody, or meta-humor; brand isn't mature enough." Future Claude sessions will flag these pre-generation. Topic discovery itself doesn't yet have this filter — a candidate improvement tracked as part of #157.

---

## Auto-retry brings back tasks I explicitly rejected with allow_revisions=false

**Symptom.** You reject a task via the `/reject` endpoint with `allow_revisions: false`. The task's status flips to `rejected`. A few minutes later, the same `task_id` comes back in `awaiting_approval` with regenerated content. The original rejection feedback is ignored.

**Root cause (historical).** The reject endpoint at `approval_routes.py::reject_task` sets `status='failed'` when `allow_revisions=false` and `status='failed_revisions_requested'` when true. The `_auto_retry_failed_tasks` sweep in `task_executor.py` queried `WHERE status='failed'` — which matched both legitimate execution failures AND deliberate `allow_revisions=false` rejections. So "don't retry this" got retried anyway.

**Fix (current).** Two commits shipped 2026-04-10:

1. `acdd1640` added belt-and-suspenders filter excluding `approval_status='rejected'` and `metadata.allow_revisions='false'` from the retry query.
2. `bc0450f1` did the proper semantic fix: the retry query now matches `status IN ('failed', 'failed_revisions_requested')` and uses `metadata.allow_revisions` as the single source of truth for "should this retry." On retry reset, `approval_status` is also cleared back to 'pending' so the new generation isn't judged against a stale rejection flag.

**If it still bounces back after these fixes.** Check `metadata.allow_revisions` in the content_tasks row — it should be `'false'` (string, not boolean). If it's null or 'true' the reject endpoint didn't write it. That would be a regression in `approval_routes.py::reject_task` lines 98-105.

---

## Test suite fails in the worker container but passes in CI

**Symptom.** `docker exec -w /app poindexter-worker python -m pytest tests/unit/` shows 2 failing tests in `test_api_token_auth.py::test_dev_mode_accepts_dev_token` (both the regular and optional variants). CI on GitHub Actions shows the same tests passing.

**Root cause.** The middleware at `api_token_auth.py` evaluates a module-level `_dev_token_blocked` flag at import time, based on `ENVIRONMENT` env var. The worker container runs with `ENVIRONMENT=production` set, so when the module is imported `_dev_token_blocked` becomes `True` permanently. The tests patch `os.environ["DEVELOPMENT_MODE"]="true"` at runtime but that doesn't retroactively unblock the import-time flag. CI runs pytest in an environment without `ENVIRONMENT` set at all, so `_dev_token_blocked` stays False and the tests pass.

**Fix.** Commit `ffa1ec26` switched the tests to patch `middleware.api_token_auth._dev_token_blocked` directly and mock `site_config.get` instead of env vars. Both worker-container and CI now pass.

**Broader lesson.** Module-level state evaluated at import time is a test footgun. Anything that reads environment to make a decision should read at call time, not import time. That's a refactor tracked informally — flag in commit messages if you see similar patterns.

---

## Static export writes succeed but the frontend still shows stale data

**Symptom.** You see `[STATIC_EXPORT] Incremental export complete — N posts` in the worker logs, meaning posts were written to `static/posts/index.json` on R2. But the frontend shows the old post list or 404s new posts.

**Root cause.** Two layers of caching:

1. **R2 itself** caches objects at CloudFlare's edge. Default TTL ~5 minutes.
2. **Next.js ISR data cache** (see "Post is Not Found" above) also has its own 5-minute TTL on the fetch helpers in `web/public-site/lib/posts.ts`.

Both layers clear on their own within 5 minutes.

**Fix.** Either wait, or trigger explicit ISR revalidation via the backend endpoint. For the R2 layer, there's no immediate cache-bust — R2 public buckets don't honor `Cache-Control: no-cache` the way you'd hope. If you need instant visibility, append `?v=<timestamp>` to the fetch URL in the frontend helpers (which bypasses both caches).

---

## SDXL server "degraded" — posts publish with Pexels stock photos

**Symptom.** `curl http://localhost:9836/health` returns `"status":"degraded"` with a `degraded_reason` mentioning `CLIPImageProcessor` or `PEFT backend`. Posts publish with generic Pexels stock images instead of SDXL-generated ones. The pipeline doesn't fail — it silently falls back.

**Root cause.** torch/torchvision version mismatch inside the SDXL container. When torch gets upgraded (e.g., base image update) but torchvision stays pinned at an older version, `torchvision::nms` operator doesn't exist and CLIPImageProcessor fails to import. Similarly, the `peft` package may be missing if the container was rebuilt.

**Fix.**

```bash
docker exec poindexter-sdxl-server pip install --upgrade torchvision peft
docker restart poindexter-sdxl-server
curl http://localhost:9836/health  # should show "status":"idle"
```

**Prevention.** `scripts/Dockerfile.sdxl` now includes both `torchvision` and `peft` in the explicit pip install list, so container rebuilds pick them up.

---

## Wan video server fails on RTX 5090 (Blackwell) with "no kernel image is available"

**Symptom.** `docker logs poindexter-wan-server` shows the model loaded successfully ("Wan 2.1 1.3B ready on NVIDIA GeForce RTX 5090") but the first `/generate` call fails with:

```
RuntimeError: CUDA error: no kernel image is available for execution on the device
```

The error originates in the UMT5 text encoder forward pass.

**Root cause.** PyTorch base images <2.6 don't ship CUDA kernels for sm_120 (Blackwell, the RTX 5090 family). The SDXL sidecar happens to dodge those kernels but Wan's UMT5 hits them. CUDA 12.8 is the first version with sm_120 support, so the base image needs PyTorch 2.7+ on CUDA 12.8.

**Fix.** Already applied in `scripts/Dockerfile.wan` — base image is `pytorch/pytorch:2.8.0-cuda12.8-cudnn9-runtime`. If you see this on a fresh `docker build`, confirm the Dockerfile hasn't been reverted to an older base.

**Prevention.** When adding new GPU-bound containers, check the model's required CUDA capability against your hardware before picking a PyTorch base. Blackwell is sm_120; Ada Lovelace (RTX 4090) is sm_89; Hopper (H100) is sm_90.

---

## Wan video server hangs on first `/generate` after WSL restart

**Symptom.** `curl http://localhost:9840/health` returns `"status":"idle"` but a `/generate` POST seems to hang for 5-10 minutes. CPU is high, VRAM stays at 0 the whole time.

**Root cause.** WSL2 page cache is cold. The Wan model lives at `~/.cache/huggingface/hub/models--Wan-AI--Wan2.1-T2V-1.3B-Diffusers/` (~10 GB on disk) and is bind-mounted into the container. After `wsl --shutdown`, every read has to hit physical disk through the WSL FUSE layer, which is slow. Subsequent loads (within the same WSL session) read from page cache and finish in ~30 s.

**Fix.** Wait it out — the load eventually completes. To pre-warm: `docker exec poindexter-wan-server cat /root/.cache/huggingface/hub/models--Wan-AI--Wan2.1-T2V-1.3B-Diffusers/snapshots/*/model_index.json > /dev/null` after a restart, before the first real `/generate`.

**Prevention.** Don't `wsl --shutdown` while a render is in flight. The 900 s idle-unload timeout on `wan-server` is intentionally large enough that a 14-scene render keeps the model warm; only an explicit shutdown breaks the cache.

---

## Wan video pipeline silently produces a Pexels-only video (cold-load timeout)

**Symptom.** A run of `scripts/run_video_pipeline_sample.py` finishes "successfully" — final MP4 lands in `~/.poindexter/generated-videos/` — but every scene visibly looks like Pexels stock footage. Wall clock matches a Pexels-only run (~5 min) instead of the expected ~12-42 min for Wan. `[Wan21Provider] inference server returned ...` errors do NOT appear; instead each scene logs an `httpx.ReadTimeout` or `httpx.ConnectTimeout` and the strategy falls through to the next provider.

**Root cause.** `Wan21Provider` previously used `_HTTP_TIMEOUT = httpx.Timeout(300.0, connect=10.0)`. A first-call cold load (Hugging Face shard read off WSL2 disk into VRAM) plus 50-step diffusion on 5s @ 16 fps can run 8-10 min on a 5090. The 300 s ceiling fired ~5 min in, the dispatcher caught the exception, and the fall-through to SDXL/Pexels happened transparently for every scene.

**Detection.**

```bash
# Look for ReadTimeout under Wan21Provider in worker logs:
docker logs poindexter-worker 2>&1 | grep -E "Wan21Provider.*(Timeout|ReadTimeout|ConnectTimeout)"

# Confirm each scene fell through (metadata.source != "wan2.1-1.3b"):
SELECT slug, scene_index, metadata->>'source' AS provider
FROM media_assets WHERE post_slug='<slug>' AND kind='video' ORDER BY scene_index;
```

**Fix.** Already applied in commit `427bed44` — `_HTTP_TIMEOUT` bumped to `httpx.Timeout(900.0, connect=10.0)` (15 min). If this regresses, set it back to 900 s. Per-`.post()` `timeout=300` arg in `_generate_to_path` was kept conservative for warm renders but the AsyncClient ceiling now tolerates one cold load per run.

**Prevention.** Pre-warm before any Wan-bound run (see WSL restart entry above). Watch the `X-Elapsed-Seconds` header on successful 200 responses — anything > 180 s on a follow-up call indicates the model unloaded between scenes (idle timeout too short for the run length).

---

## Wan-server up, but pipeline wedges on first GPU stage ("Gaming/external workload detected")

**Symptom.** `docker ps` shows `poindexter-wan-server` healthy. Worker pipeline starts, hits the first ollama-locked Stage (`script_for_video`, `tts_for_video`, or any blog QA stage) and logs:

```
[GPU] Gaming/external workload detected (util=100%) — pausing pipeline
```

…repeatedly, never recovering. No actual game running. Reproduces every time wan-server has the model resident in VRAM (~14 GB, idle).

**Root cause.** `services/gpu_scheduler._wait_for_gaming_clear()` queries `nvidia_gpu_utilization_percent` from the `poindexter-gpu-exporter` Prometheus endpoint. Loaded sidecar VRAM looks identical to a third-party gaming workload from that metric's perspective. Wan-server's idle-keep timeout (900 s) was tuned long enough to span a 14-scene render — and that's exactly the window that trips the detector. See Glad-Labs/poindexter#144 for the full diagnosis.

**Detection.**

```bash
# Is the Wan model actually resident?
curl -s http://localhost:9840/health | jq '.status, .vram_used_mb'
# status=="ready" and vram_used_mb>10000 → confirmed sidecar-VRAM contention

# Confirm gpu_scheduler is the blocker:
docker logs poindexter-worker 2>&1 | grep "Gaming/external workload"
```

**Fix.** Three options, in order of preference:

1. **Set `gpu_gaming_detection_mode=off`** (the post-`c0c463c0` default). Trusts the in-process GPU lock + each sidecar's own VRAM management. Single command:

   ```bash
   poindexter settings set gpu_gaming_detection_mode off
   ```

2. **Pre-unload via the runner workaround** (commit `b83706f3`) — `POST /unload` to wan-server before the pipeline starts. Already wired into `scripts/run_video_pipeline_sample.py`; for ad-hoc triggers run:

   ```bash
   curl -X POST http://localhost:9840/unload
   ```

3. **Hard-stop wan-server** — `docker stop poindexter-wan-server` before any blog generation. Heavy-handed; only use when (1) and (2) aren't options.

**Prevention.** Leave `gpu_gaming_detection_mode` on `off` (default) unless you actively game on the box, in which case use `manual` and flip `pipeline_paused_for_gaming` around game sessions. The legacy `auto` threshold mode is preserved but now defaults to 90% (was 30%) so brief inference bursts don't trip it.

**Related.** Glad-Labs/poindexter#144 (closed by `c0c463c0`); follow-up Glad-Labs/poindexter#160 (cooperative-unload protocol — still open).

---

## Wan-server returns 500 mid-render with `CUDA out of memory` at 1080p

**Symptom.** Operator overrides `width`/`height` to 1280×720 or 1920×1080 (or duration > 10 s) on a `/generate` call; server logs:

```
[wan] generation failed
torch.cuda.OutOfMemoryError: CUDA out of memory. Tried to allocate XX.XX GiB
```

Provider receives HTTP 500 with `detail="OutOfMemoryError: CUDA out of memory..."`. Subsequent calls at the **same** dimensions also fail until the pipeline is unloaded.

**Root cause.** Wan 2.1 1.3B's native res is 832×480 @ 16 fps for 5 s (~80 frames). Peak VRAM at native settings is ~14 GB; doubling pixel count or duration scales memory roughly linearly through the diffusion stack. On a 32 GB 5090 sharing with SDXL/Ollama, anything beyond ~960×540 / 8 s starts hitting OOM. The server enforces an upper-bound `_MAX_FRAMES = 240` (15 s @ 16 fps) but does NOT cap dimensions — the Pydantic model allows up to 1280×1280, which exceeds the model's safe envelope.

**Detection.**

```bash
docker logs poindexter-wan-server 2>&1 | grep -E "OutOfMemoryError|CUDA out of memory"

# What dims did the failed call request? Check the worker side:
docker logs poindexter-worker 2>&1 | grep "Wan21Provider" | tail -20
```

**Fix.**

1. **Force the unload to clear fragmented VRAM**:

   ```bash
   curl -X POST http://localhost:9840/unload
   ```

2. **Resubmit at native dims** — `width=832, height=480, duration_s=5, fps=16`. These are the `Wan21Provider` defaults; if the dispatcher overrode them, drop the override.

3. If 1080p output is genuinely required, generate at 832×480 and upscale via ffmpeg / Real-ESRGAN as a separate stage rather than asking the diffusion model to do it.

**Prevention.** Don't override `width`/`height`/`duration_s` above the defaults in `app_settings.plugin.video_provider.wan2.1-1.3b.*` or per-call config. PLACEHOLDER — needs operator confirmation: a server-side dimension cap (analogous to `_MAX_FRAMES`) would be a low-risk addition; until then, this is enforced by convention in the dispatcher.

---

## Wan-server enters DEGRADED state — `/generate` returns 503 forever

**Symptom.** `curl http://localhost:9840/health` returns:

```json
{"status":"degraded","degraded":true,"degraded_reason":"<exc-type>: <message>",...}
```

Every `/generate` POST returns `503 server degraded: <reason>` immediately (no inference attempted). Container is "running" per `docker ps` but useless until restarted.

**Root cause.** `_ensure_pipeline_loaded()` flips `state.degraded=True` whenever `WanPipeline.from_pretrained(...)` raises. Common triggers:

- **Hugging Face cache corruption** — partial download from an interrupted `wsl --shutdown`. `degraded_reason` mentions `OSError`, `safetensors.SafetensorError`, or "file not found".
- **CUDA not available at startup** — `degraded_reason="CUDA not available"` (set in `on_startup` before any `/generate`).
- **First-load CUDA error** — Blackwell sm_120 mismatch (covered in the separate "no kernel image" entry above) leaves `degraded_reason` like `RuntimeError: CUDA error: ...`.
- **Disk full** on the model cache mount.

**Detection.**

```bash
curl -s http://localhost:9840/health | jq '.degraded, .degraded_reason'
docker logs poindexter-wan-server 2>&1 | grep -E "WanPipeline load failed|degraded" | tail -10
df -h ~/.cache/huggingface  # cache disk space
```

**Fix.** Degraded state is sticky for the life of the process — there's no `/reset` endpoint. To recover:

1. **Restart the container**: `docker restart poindexter-wan-server`. On startup the model lazy-loads on first `/generate`; if the underlying cause is fixed, degraded clears automatically.
2. **If cache corruption**: blow away the partial download and re-fetch on next start:

   ```bash
   rm -rf ~/.cache/huggingface/hub/models--Wan-AI--Wan2.1-T2V-1.3B-Diffusers
   docker restart poindexter-wan-server
   # First /generate after this will redownload (~10 GB, slow on a cold link)
   ```

3. **If disk full**: free space on the cache volume, then restart.

**Prevention.** PLACEHOLDER — needs operator confirmation: ideally `/health` reporting `degraded=true` would alert via Telegram/Discord, but no such alert rule exists yet. Add one: `wan_server_degraded_alert_enabled=true` and a Grafana rule firing when the `/health` probe shows `degraded=true` for > 5 min.

---

## `poindexter-wan-server` container restart-loops every ~30 seconds

**Symptom.** `docker ps` shows the container repeatedly transitioning Up → Restarting. `docker logs poindexter-wan-server -f` shows the server starting, sometimes printing "Wan server starting; GPU=...", then exiting. Healthcheck never goes green.

**Root cause.** Most common causes (in observed-frequency order):

1. **Healthcheck failing during cold load** — first `/generate` hasn't completed yet, but `wget http://localhost:9840/health` should still return 200 since `/health` is non-blocking. If healthcheck is failing it's either uvicorn never started (Python import error) or the container's port mapping is broken.
2. **Python import-time crash** — `diffusers` / `torch` / `transformers` version skew after a base-image bump that didn't cleanly re-pin the pip layers. Logs show a traceback before the FastAPI startup banner.
3. **GPU driver mismatch** — `torch.cuda.is_available()` raises rather than returning False (driver too old for CUDA 12.8). Look for `CUDA driver version is insufficient` in stderr.
4. **OOM-kill at load** — the container hit the host's `--memory` limit (if set) during model load. `dmesg | grep -i oom` on the host shows the kill.

**Detection.**

```bash
# Is the loop happening?
docker ps --filter name=poindexter-wan-server --format "{{.Status}}"

# Catch the crash output before next restart:
docker logs poindexter-wan-server --tail 100 2>&1

# Healthcheck history:
docker inspect poindexter-wan-server --format '{{json .State.Health}}' | jq
```

**Fix.**

1. **Stop the loop first** so logs are readable: `docker stop poindexter-wan-server`.
2. Fix root cause based on traceback:
   - Import error → `docker compose build --no-cache wan-server` to rebuild pip layers from `Dockerfile.wan`.
   - Driver mismatch → update host NVIDIA driver (must support CUDA 12.8+; 555.85 or later on Windows).
   - OOM-kill → drop or raise the container memory limit in `docker-compose.local.yml`.
   - Healthcheck false-positive → confirm port 9840 isn't being used by another process: `Get-NetTCPConnection -LocalPort 9840` (PowerShell).
3. Restart: `docker compose up -d wan-server`.

**Prevention.** Pin `diffusers`, `transformers`, `torch` versions in `scripts/Dockerfile.wan` once the working set is identified (current pins are `>=` ranges). Add `start_period: 600s` to the healthcheck so a long cold load doesn't count failures during model boot.

---

## Wan model unloads mid-render between scenes (idle-timeout edge case)

**Symptom.** A long pipeline run (e.g. 14-scene long-form video) takes radically longer than expected. First scene completes in ~3 min (warm), scene 2 starts and the worker logs show another full ~8-10 min cold-load before the second `/generate` returns. Repeats per scene. Wall clock balloons from ~42 min to ~2.5 h.

**Root cause.** The idle-unload background task in `wan-server.py` (`idle_unloader`) compares `time.time() - state.last_used` to `IDLE_TIMEOUT_S` every 30 s. `state.last_used` is updated on `/generate` **completion**, not start. If the worker spends > `IDLE_TIMEOUT_S` between completing scene N and starting scene N+1 (e.g. doing TTS, scene-stitch prep, or waiting on the GPU lock for SDXL), the model gets unloaded and the next scene pays full cold-load cost. With `IDLE_TIMEOUT_S=120` (the in-code default) this triggers constantly; with `WAN_IDLE_TIMEOUT_S=900` (the compose-file override) it only triggers on long inter-scene gaps.

**Detection.**

```bash
# Look for unload events between scenes:
docker logs poindexter-wan-server 2>&1 | grep -E "Unloading WanPipeline|Wan 2.1 1.3B ready"
# Pattern "Unloading... ready... Unloading... ready..." across a single pipeline run = repeated cold loads.

# Confirm the configured idle timeout matches expectation:
curl -s http://localhost:9840/health | jq '.idle_timeout_s'
```

**Fix.** No mid-run recovery — the cold-load cost is already paid. To prevent the next run from doing the same:

1. **Bump `WAN_IDLE_TIMEOUT_S`** in `docker-compose.local.yml` to span the longest expected inter-scene gap. Current value is 900 s (15 min); raise to 1800 s for very long runs. Restart wan-server to pick up the env change.

   ```bash
   # Edit docker-compose.local.yml (WAN_IDLE_TIMEOUT_S: "1800") then:
   docker compose up -d wan-server
   ```

2. **Pre-warm before each scene** — in the worker, hit `/health` (cheap, doesn't block GPU lock) before each `/generate` to confirm `status=="ready"`. PLACEHOLDER — needs operator confirmation: not currently implemented; would require a Stage-level pre-flight check.

**Prevention.** Treat `WAN_IDLE_TIMEOUT_S` as a runbook-tuned value, not a default. Rule of thumb: `idle_timeout_s >= max(inter_scene_gap_s) * 1.5`. Track via the `Unloading WanPipeline` log line frequency — if it appears more than once per pipeline run, the timeout is too short.

**Related.** This is the same setting tension flagged in the GPU-contention entry above — long timeout helps render throughput, hurts GPU sharing with ollama/SDXL. The current `gpu_gaming_detection_mode=off` default makes the long-timeout side of the trade-off safe.

---

## Voice agent (or any non-interactive Claude session) reports "Permission denied" for an MCP tool the allowlist seems to cover

**Symptom.** The voice-agent-livekit container, a `/schedule` cron Claude Code session, or anything running with `dontAsk` permissions denies a tool call like `mcp__claude_ai_Poindexter__check_health` even though `~/.claude/settings.json` has `"mcp__*"` or `"mcp__claude_ai_*"` in `permissions.allow`. Surfaces to the user as "Sorry, I had trouble talking to Claude Code" or a flat "Permission denied".

**Root cause.** The `*` glob in `permissions.allow` does **NOT** cross `__` boundaries. `__` is the structural separator in `mcp__<server>__<tool>`, and the permission engine treats it as opaque punctuation that wildcards can't span. So `mcp__*` and `mcp__claude_ai_*` silently match nothing.

**Fix.** Replace any cross-`__` wildcard with one entry per MCP server (and use exact tool names if you want even narrower scope):

```json
"allow": [
  "mcp__poindexter__*",
  "mcp__gladlabs__*",
  "mcp__claude_ai_Poindexter__*",
  "mcp__claude_ai_Notion__*",
  "mcp__claude_ai_Sentry__*"
]
```

Full working configuration and discussion in [`docs/operations/claude-code-permissions.md`](claude-code-permissions.md). Closes [Glad-Labs/poindexter#443](https://github.com/Glad-Labs/poindexter/issues/443).

---

## OpenClaw MCP tools fail — "tool execution failed" from Discord

**Symptom.** Using `#reject_post` or other MCP tools from Discord via OpenClaw reports success in the chat message but the action didn't actually happen (e.g., tasks still show `awaiting_approval` in the DB). The LLM fabricates a success response.

**Root cause.** The Claude Desktop MCP config (`claude_desktop_config.json`) has wrong values:

1. `POINDEXTER_API_URL` pointing at a dead Railway URL instead of `http://localhost:8002`
2. The MCP server's OAuth client (`mcp_oauth_client_id` /
   `mcp_oauth_client_secret` in `app_settings`) hasn't been provisioned
   or has been revoked
3. `OPENCLAW_GATEWAY_TOKEN` is missing from the gladlabs MCP env

**Fix.** Edit `%LOCALAPPDATA%\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Roaming\Claude\claude_desktop_config.json`:

- Set `POINDEXTER_API_URL` to `http://localhost:8002`
- Run `poindexter auth migrate-mcp` to (re)provision the MCP OAuth
  client. The static-Bearer fallback (`POINDEXTER_API_TOKEN` /
  `app_settings.api_token`) was removed in
  Glad-Labs/poindexter#249 — there is no env-var path back.
- Add `OPENCLAW_GATEWAY_TOKEN` matching the token in `~/.openclaw/openclaw.json`

Restart Claude Desktop after editing.

---

## Search page returns no results on the live site

**Symptom.** Visiting `https://www.gladlabs.io/search?q=anything` shows "No articles found" or "Failed to search articles."

**Root cause.** The search page was a `'use client'` component calling the FastAPI backend at `localhost:8002` via `fetchAPI()`. On the live Vercel site, visitors' browsers can't reach a local-only server.

**Fix (shipped).** The search page now fetches `posts/index.json` from R2 and filters client-side. No backend dependency. If it breaks again, check that R2 has a current `static/posts/index.json` — trigger a rebuild via `curl -X POST localhost:8002/api/export/rebuild -H "Authorization: Bearer $TOKEN"`.

---

## Topic discovery keeps generating the same rejected topic genre

**Symptom.** The pipeline generates 5+ variations of the same topic (e.g., "Bootstrap Your SaaS") in a 24-hour period. QA rejects all of them for the same reasons (internal consistency, unlinked references). The rejection rate climbs above 80%.

**Root cause.** Topic discovery sources (HN, Dev.to, web search) surface trending topics that map to the same genre. The dedup check prevents exact title matches but not thematic duplicates. The `_should_trigger_discovery` rejection-streak signal fires after 3 consecutive rejections, but if the next discovery pull finds the same trending topic, the cycle repeats.

**Fix.** Check `enabled_topic_sources` — temporarily disable the source producing the repetitive topics:

```bash
curl -X PUT localhost:8002/api/settings/enabled_topic_sources \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"value": "knowledge,codebase,hackernews"}'  # dropped devto,web_search
```

Re-enable after the trend passes. Long-term fix: pgvector-based thematic dedup (GitHub #44).

---

## Approval rate drops to ~0% — every generated task lands in "rejected"

**Symptom.** New blog-post tasks keep firing on schedule (hourly, per the content router cadence), they run to completion, but every single one comes back `status=rejected`. The Prometheus `NoPublishedPostsRecently` alert goes pending, then firing. `pipeline_reviews` shows the rejections concentrated under `reviewer=auto_curator` ("Quality score X below threshold 75.0") and `reviewer=multi_model_qa` ("programmatic_validator @ N: Unlinked citation — possible hallucinated reference"). No genuine operator intervention has changed in this window.

**Root cause.** `app_settings.pipeline_writer_model` silently got flipped off the intended writer (`ollama/glm-4.7-5090:latest`) and onto a smaller budget-tier model like `ollama/gemma3:27b`. The content generator reads that key on every run via `services.ai_content_generator._resolve_model_list` (see `ai_content_generator.py` ~line 636) — **not** `model_role_writer`, which looks like a sibling setting but is orphaned and unused by the generator. With the weaker writer active the content hits fact-fabrication / citation-hallucination patterns that the anti-hallucination gates (`services/content_validator.py` `UNLINKED_CITATION_PATTERNS`, `HALLUCINATED_LINK_PATTERNS`) correctly kill.

Once this flip is in place the approval rate stays at ~0–5% until the setting is reverted. The `pipeline_tasks.model_used` column reflects what was actually used (will show `gemma3:27b` during the bad window, `glm-4.7-5090:latest` before and after).

**Debugging checks.**

```sql
-- Which model is the pipeline USING?
SELECT key, value, updated_at FROM app_settings WHERE key = 'pipeline_writer_model';

-- What model did recent tasks run under? Last successful-published vs current.
SELECT DATE(created_at) AS day, model_used, COUNT(*) FROM pipeline_tasks
WHERE created_at > NOW() - INTERVAL '14d' AND model_used IS NOT NULL
GROUP BY day, model_used ORDER BY day DESC;

-- Approval rate, day-by-day.
SELECT DATE(created_at) AS day,
       SUM(CASE WHEN decision='approved' THEN 1 ELSE 0 END) AS a,
       SUM(CASE WHEN decision='rejected' THEN 1 ELSE 0 END) AS r
FROM pipeline_reviews WHERE reviewer='auto_curator' AND created_at > NOW() - INTERVAL '14d'
GROUP BY day ORDER BY day DESC;
```

**Fix.**

```sql
UPDATE app_settings SET value = 'ollama/glm-4.7-5090:latest', updated_at = NOW()
WHERE key = 'pipeline_writer_model';
```

Write an `audit_log` row so the flip is traceable next time.

**Thinking-model gotcha.** `glm-4.7-5090` is a thinking model — Ollama returns `message.content` AND `message.thinking`, where thinking can eat the full `num_predict` budget on short prompts. The generator already knows about this (see `_is_thinking = any(t in model_name.lower() for t in ("qwen3", "glm-4", "deepseek-r1"))` and the `content_gen_token_mult_thinking` multiplier, default 7.0). If you're testing the model manually with a small `num_predict`, add `"think": false` to the payload or expect empty `content`.

**Related.** Surfaced 2026-04-21. Writer flip happened 2026-04-11. Memory `feedback_model_selection.md` ("Use glm-4.7-5090 for writing"). Fixed via `app_settings` UPDATE + audit_log entry.

---

## Gitleaks CI starts reporting hundreds of "new" leaks right after a pre-commit scrub of `.gitleaks-baseline.json`

**Symptom.** GitHub Actions Security job starts emitting `WRN leaks found: 286` (or similar — roughly the size of the baseline). Every finding IS already in `.gitleaks-baseline.json` — fingerprints match. Both the private and public gitleaks scans start failing loudly.

**Root cause.** Gitleaks 8.x's baseline check does `findingsEqual()` which compares `Commit + File + Line + Author + Email + Secret + Match` — **any** field mismatch and the baseline entry is treated as absent. If you scrub the `Email` field in the baseline (say for a privacy sweep: "replace mattg@x with hello@x"), every commit in the actual git history still has the old email as author, so equality fails for all 285+ entries at once and they re-fire as new findings.

The scanner isn't broken. It's working exactly as documented. The baseline is the artifact that changed.

**Fix.** Revert the `Email` scrub in the baseline file. It's not a real privacy benefit — the email is already public in every commit's author metadata — and the baseline is excluded from the public-sync tree by `scripts/sync-to-github.sh` (so the "leak to the world" angle never applied). Keep the scrub on `pyproject.toml` authors and other non-baseline surfaces.

**Verification after revert.** Gitleaks output drops from `leaks found: 286` back to `leaks found: 1` or similar — whatever genuinely-new findings exist post-baseline. Compare fingerprint sets:

```python
baseline_fps = {f"{e['Commit']}:{e['File']}:{e.get('RuleID',e['Description'])}:{e['StartLine']}"
                for e in json.load(open('.gitleaks-baseline.json'))}
log_fps = [l.split('Fingerprint:',1)[1].strip() for l in log.splitlines() if 'Fingerprint:' in l]
# Expect most log fingerprints to already be in baseline_fps.
```

**Related.** Surfaced 2026-04-22. Regression from commit 8ef90218, fixed by commit 10c6f232.

---

## `scripts/sync-to-github.sh` leaves the repo stuck on a `github-sync-temp-*` branch

**Symptom.** You run `bash scripts/sync-to-github.sh` and the GitHub push appears to succeed, but afterwards `git status` shows you on `github-sync-temp-NNN` with dozens of "untracked" files (CLAUDE.md, docs/, web/storefront/, infrastructure/grafana/dashboards/approval-queue.json, etc.). Next `git commit` or `git checkout` fails or behaves oddly. On the next run the script pushes fine again but also leaves you stuck — and now you have two stale temp branches.

**Root cause.** Historical version of the script ended with `git checkout "$BRANCH" 2>/dev/null && git branch -D "$TEMP_BRANCH" 2>/dev/null`. The temp branch's index has a ton of files removed via `git rm --cached` (they stay on disk — only the index entries were dropped). When the script tries to checkout main, git refuses because main's tree has those files tracked and considers the on-disk copies as "untracked" — refusing to overwrite them on a non-forced checkout. `2>/dev/null` hides the error, `set -e` exits the script, and `git branch -D` never runs. The repo is stranded on the temp.

**Fix.** `git checkout -f main`, then `git branch -D github-sync-temp-<NNN>`. Safe because working-tree content matches main's tree content — only the index was different.

**Permanent fix.** Script now uses `git checkout -f "$BRANCH"` and drops the `2>/dev/null` so any real error surfaces instead of silently stranding the repo. Fixed in commit 5cfe19d7.

---

## Pipeline rejects 100% of content with "unlinked_citation" validator veto

**Symptom.** Every single content task hits `rejected` with the error message `Multi-model QA rejected (score: NN, veto: programmatic_validator @ NN): Unlinked citation — possible hallucinated reference: '<some-random-lowercase-prose>'`. Approval rate is literally 0%. Scores cluster around 60-75 — not rejection because the content is bad, rejection because the validator is firing on everything.

**Root cause.** `services/content_validator._check_patterns()` defaults to `re.IGNORECASE`, which collapses `[A-Z]` in the `UNLINKED_CITATION_PATTERNS` regex set down to `[A-Za-z]`. Patterns intended to match Title-Case citations ("according to MIT Research") then match any lowercase word sequence. Every post has at least one such match.

**Fix.** Commit `e1b8aaed` — `_check_patterns()` now takes a `flags: int` parameter; the unlinked-citation call site passes `flags=0` so `[A-Z]` stays case-sensitive. Inline `(?i:...)` groups inside each pattern handle the keyword case-insensitivity where needed.

**Related.** The false-positive hunt also surfaced three more validator edge classes, each with its own commit:

- `c7df911c` — markdown section headings ("### The Amplifier Effect: Why AI Multiplies...") matched the bare-paper-title pattern. Fix: strip heading lines and list-item leaders before running UNLINKED_CITATION_PATTERNS.
- `9e802e60` — narrative verb regex ("Use API", "adopt Large Language Models") captured plain TitleCase English words as library names. Fix: skip candidates matching `^[A-Z][a-z]+$` unless they're in a known reference list.
- `89768318` — markdown linked citations (`[Title](url)`) still matched because the `(?<!\[)` lookbehind only blocks matches that START at the bracket. Fix: strip the full `[text](url)` construct from the text before running unlinked-citation patterns.

All four fixes have regression tests. See `docs/experiments/pipeline-tuning.md` for the tuning session that uncovered them.

---

## Rejection message says `veto: url_verifier @ 90: 2 verified external citations (+10 bonus)`

**Symptom.** A rejected task's `error_message` names `url_verifier` as the vetoing reviewer, but quotes its feedback as a positive "+10 bonus" note. Makes zero sense as a rejection reason.

**Root cause.** `services/stages/cross_model_qa._build_rejection_reason()` historically fell back to `reviews[-1]` when no reviewer had `approved=False`. url_verifier is typically the last-added reviewer (stage 2f), and when it approves with score 90 ("+bonus"), the fallback still named it as "the veto" — even though the real rejection mode was the score gate (final_score below `qa_final_score_threshold`).

**Fix.** Commits `aa4648ca` + `70297913` — `_build_rejection_reason()` now distinguishes three cases:

1. A reviewer truly vetoed (via the gate logic, not just `approved=False`) → name them
2. No one vetoed but the final_score is below threshold → report `score-gate: below approval threshold, lowest reviewer X @ Y`
3. No reviews at all → explicit "No reviews recorded"

The function also mirrors the special-case gate logic for `internal_consistency` — `approved=False` with score ≥ `qa_consistency_veto_threshold` is advisory, not a veto. Two regression tests pin both branches.

---

## `pgvector`, `LoRA`, `REST`, or `PostgreSQL` flagged as hallucinated library

**Symptom.** A content task rejects at a score that would otherwise pass, with error `Likely hallucinated library/API reference: 'pgvector'` (or `LoRA`, `REST`, `PostgreSQL`, `transformers`, etc.). All of those are real things, just not in the PyPI top-500.

**Root cause.** `services/content_validator._extract_library_candidates()` flags any backticked identifier that isn't in the stdlib / PyPI-top-500 / Ollama-models whitelist. AI/ML acronyms (LoRA, RAG, REST, SDXL), database extensions (pgvector), and product names (PostgreSQL, Redis) aren't in those lists.

**Partial fix.** The plain-TitleCase English-word filter (commit `9e802e60`) catches single-word cases like "Use" or "Large", but not multi-word acronyms or snake_case extensions.

**Full fix — not yet shipped.** Add these to `_HALLUCINATION_WHITELIST`:

```python
# Common AI/ML acronyms that aren't PyPI packages
"lora", "rag", "rest", "sdxl", "llm", "ai", "api",
# Database extensions/products (real but not on PyPI)
"pgvector", "postgresql", "redis", "elasticsearch", "clickhouse",
# Hugging Face org-adjacent names
"transformers", "diffusers", "accelerate",
```

Until shipped, operator workaround: `poindexter settings set content_validator_warning_reject_threshold 8` to raise the promotion threshold so one or two flagged acronyms won't critical-promote the warning. Tracked in the experiments log.

---

## How to add a new entry to this doc

1. You hit an issue that took more than 10 minutes to diagnose.
2. Before you close the issue in your head, write it up here.
3. Template:
   - `## Symptom` (what you saw)
   - `## Root cause` (what was actually wrong)
   - `## Fix` (what you did to unstick it)
   - `## Related` (commit hashes, GitHub issue numbers, memory files)
4. Commit it with a message like `docs(troubleshooting): add <symptom>` so the commit history is searchable.

The point is to stop paying rediscovery tax.
