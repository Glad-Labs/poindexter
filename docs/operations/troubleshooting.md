# Troubleshooting

Runbook for issues that have bitten us in production. Each entry has a symptom, a root cause, a fix, and a link to the GitHub issue or commit where it was addressed. When you hit something new, add it here instead of just fixing it — the next person (or next-you) will thank you. Use the public `Glad-Labs/poindexter` tracker for product bugs and the private `Glad-Labs/poindexter` tracker for operator-specific items.

Entries are ordered by frequency of occurrence, not severity.

---

## DbBackupJob fails with `set: pipefail: invalid opt`

**Symptom.** GlitchTip floods with errors from `DbBackupJob` (`http://localhost:8080`, project `poindexter`) every 12 hours. The traceback points at `scripts/db-backup-local.sh` line 17 with bash complaining about `set: pipefail: invalid option name` (often truncated to `set: pipefail: invalid opt` in the log UI because bash's error contains a CR that the renderer eats). Running `bash` interactively inside `poindexter-worker` and typing `set -o pipefail` works fine, so the bug is in the file the script reaches bash with — not in bash itself.

**Root cause.** The script has CRLF line endings inside the container. bash reads line 17 as `set -euo pipefail\r` and parses `pipefail\r` as the argument to `-o`, which isn't a real option name. CRLF can sneak in two ways on a Windows host:

1. The working tree was checked out _before_ `.gitattributes` (`*.sh text eol=lf`) landed and was never re-normalized. The index has the file as LF but the working-tree copy is still CRLF. The worker bind-mounts `./scripts` into `/opt/scripts:ro`, so the container sees the working-tree bytes — CRLF.
2. A contributor's personal `git config --global core.autocrlf=true` overrode the per-file `eol=lf` attribute on their next checkout, re-introducing CRLF. (`.gitattributes` `eol=lf` does win over `autocrlf`, but only after a fresh checkout — existing files stay as-is until git touches them.)

**Fix.** Re-normalize the tracked shell scripts and re-checkout:

```bash
git add --renormalize -- '*.sh' '*.bash'
git commit -m "chore: re-normalize shell scripts to LF"   # no-op if already LF
git rm --cached -r .   # forces git to re-stage everything with the active attrs
git reset --hard       # restores the working tree using the normalized blobs
```

Then verify inside the worker:

```bash
docker exec poindexter-worker bash -c "head -1 /opt/scripts/db-backup-local.sh | od -c | head -1"
```

The output should end in `\n`, not `\r \n`. After the next 12h tick (or a manual `poindexter jobs run db_backup` invocation) GlitchTip should see no new occurrences.

**Regression gate.** The `shell-line-endings` job in `.github/workflows/security.yml` runs `scripts/ci/check-shell-line-endings.py` on every PR. It binary-scans every tracked `*.sh` / `*.bash` file and fails the build the moment any of them contain `\r`. Unit tests in `src/cofounder_agent/tests/unit/scripts/test_check_shell_line_endings.py` pin the contract.

**Windows editor note.** If you're editing `.sh` files on Windows, configure your editor to use LF: VS Code → `"files.eol": "\n"`, Notepad++ → Edit → EOL Conversion → Unix (LF). `.gitattributes` handles normalization at the git layer, but editors that strip-and-rewrite the file (save-as, "convert encoding") can re-introduce CRLF in the working tree between commits — the CI lint catches that next push.

---

## PowerShell script dies with `Unexpected token '}'` (and the brace looks fine)

**Symptom.** Running a repo `.ps1` under Windows PowerShell 5.1 (`powershell.exe`) -- e.g. `./scripts/deploy-worker.ps1` -- aborts at parse time before a single line executes:

```
At C:\...\scripts\deploy-worker.ps1:125 char:1
+ }
+ ~
Unexpected token '}' in expression or statement.
```

The reported brace is balanced and correct. Deleting it does not help (the error just moves or persists), and `git status` shows the file is **unmodified** vs `HEAD`. The same file parses cleanly under PowerShell 7 (`pwsh`).

**Root cause.** The file is UTF-8 **without a BOM** and contains a non-ASCII character -- almost always an em-dash (`U+2014`) in a string or comment. Windows PowerShell 5.1 decodes BOM-less `.ps1` files using the system ANSI code page (Windows-1252), not UTF-8. The em-dash's three UTF-8 bytes (`E2 80 94`) are mis-decoded as three Windows-1252 characters whose last one is `U+201D`, a "smart" closing double-quote. PowerShell accepts smart-quotes as string delimiters, so that phantom quote prematurely terminates the surrounding string. Everything after it re-tokenizes in the wrong context, the brace nesting desyncs, and the parser finally fails on the next `}` it cannot match -- typically a few lines downstream of the real culprit. The reported line is where the parser gave up, not where the bad byte is.

Confirm it is an encoding issue (not a real brace bug):

```powershell
$b=[System.IO.File]::ReadAllBytes($f)
# parses clean as UTF-8 but fails as ANSI == this bug
function P($s){$e=$null;$t=$null;[System.Management.Automation.Language.Parser]::ParseInput($s,[ref]$t,[ref]$e)|Out-Null;if($e){'err'}else{'clean'}}
'utf8 : '+(P ([System.Text.Encoding]::UTF8.GetString($b)))
'ansi : '+(P ([System.Text.Encoding]::GetEncoding(1252).GetString($b)))
```

**Fix.** Replace the non-ASCII characters with ASCII equivalents (em-dash -> `-`, ellipsis -> `...`, smart-quotes -> straight quotes). Reference the char by codepoint so your own tooling does not re-introduce it:

```powershell
$t=[System.IO.File]::ReadAllText($f,[System.Text.Encoding]::UTF8)
$enc=New-Object System.Text.UTF8Encoding($false)  # UTF-8, no BOM (file is now pure ASCII)
[System.IO.File]::WriteAllText($f,$t.Replace([char]0x2014,'-'),$enc)
```

(Re-saving as UTF-8 **with** a BOM also works -- 5.1 honours the BOM -- but plain ASCII is the most portable.) `.gitattributes` keeps `.ps1` at `eol=crlf`; the ReadAllText/Replace/WriteAllText sequence above preserves line endings.

**Immediate unblock.** Run it under PowerShell 7, which reads BOM-less files as UTF-8: `pwsh -File .\scripts\deploy-worker.ps1`. (These scripts already expect `pwsh` -- `deploy-worker.ps1` invokes `& pwsh` internally.)

**Regression gate.** The `shell-line-endings` job in `.github/workflows/security.yml` runs `scripts/ci/check-powershell-encoding.py`, which fails the build if any tracked `*.ps1` / `*.psm1` / `*.psd1` carries non-ASCII bytes without a BOM. Unit tests in `src/cofounder_agent/tests/unit/scripts/test_check_powershell_encoding.py` pin the contract.

**Windows editor note.** If your editor inserts typographic dashes/quotes (Word-style "smart" autocorrect, or paste from a rendered doc), disable it for `.ps1` files. The CI lint catches it next push, but ASCII-only PowerShell sidesteps the whole class.

---

## Post is "Not Found" on the public site immediately after publishing

**Symptom.** You hit approve on a post, the backend logs say it went live, IndexNow and Google sitemap were pinged, but `https://www.gladlabs.io/posts/<slug>` returns a "Post Not Found" page. Refreshing doesn't help for the first 30 seconds to a few minutes.

**Root cause.** The Next.js frontend's `getPostBySlug` helper in `web/public-site/lib/posts.ts` fetches from the R2 static JSON with `next: { revalidate: 300 }`. That's a 5-minute ISR data cache. When you first requested the post URL _before_ the R2 JSON file existed (for example, during pipeline generation, if Vercel pre-rendered anything), the fetch cached a `null` result. `revalidatePath()` — which the backend calls on publish — only invalidates the route cache, not the data cache for that specific fetch URL. So the stale null keeps being served until either the 5-minute TTL expires or a request triggers a background revalidation.

**Fix.** Hit the URL twice in quick succession. The first request sees the stale null, triggers a background fetch from R2, and the second request (and everything after) gets the fresh post. First-click visitors via social media or search bots only hit once and see the not-found page, which is the real cost.

**Real fix.** Switch the frontend to `revalidateTag` keyed on `post:<slug>` so the backend can invalidate exactly that data cache entry when publishing. Tracked in issue #175.

---

## "Vercel deploy is failing" but the test suite looks green locally

**Symptom.** Discord notification fires "CI failed" or "Vercel deploy failed." Matt checks Vercel dashboard — the deploy job is red. But running `npm run test:ci` and `pytest` locally shows everything passing.

**Root cause.** Vercel builds independently of the GitHub Actions test workflows — it watches `Glad-Labs/poindexter` and builds on every push to `main` (see [ci-deploy-chain.md](./ci-deploy-chain)); there is no single `ci.yml` that gates deploy on a `test` job. A red Vercel deploy while local tests pass is therefore a genuine **Vercel-side build failure** — a step that only fails in Vercel's build environment, a missing env var, or a Next.js build error — not a deploy skipped by a failing Python test.

**Fix.** Open the failing deployment in the Vercel dashboard and read its build logs; that's where the real error is. Reproduce the frontend build locally with `cd web/public-site && npm run build`. A red GitHub Actions check (e.g. `unit-tests.yml`) is a separate signal — fix it on its own, but it neither blocks nor unblocks the Vercel deploy.

**Debugging anti-pattern.** Don't assume a green local `pytest` means the deploy is fine — the frontend build runs in Vercel's environment with its own Node version and env vars, so it can fail there while everything passes locally. Start from the Vercel build log, not from guesses about the Python suite.

**Related.** [docs/operations/ci-deploy-chain.md](./ci-deploy-chain) has the full chain diagram.

---

## Pipeline task stuck "in_progress" for more than 10 minutes

**Symptom.** You queued a content task, it shows `status='in_progress'` in `pipeline_tasks`, but there's no progress in the logs. The Prefect stale-task sweep hasn't reclaimed it, and the per-stage timeouts in the LangGraph template haven't fired either.

**Root cause (historical).** Before the timeout hygiene pass on 2026-04-10, several external call sites (`OllamaClient`, SDXL server, nvidia-smi exporter, `DuckDuckGo` via `run_in_executor`) had either no per-call timeout or a very long one (up to 3600s). When the underlying connection hung in a state that didn't yield to asyncio, `asyncio.wait_for` at the stage level couldn't cancel it. One test task this session hung 20+ minutes in multi_model_qa because of this.

**Fix (current).** The timeout hygiene pass commits (`0bfd4389`, `4168f87b`, `f0c6cc0b`, `2bb77afa`, `3690abfd`) added:

1. Explicit `httpx.Timeout(N, connect=M)` on every `httpx.AsyncClient` constructor in the hot path.
2. Explicit per-request `timeout=N` arguments on every `.post()`, `.get()`, `.head()`.
3. `asyncio.wait_for` wrappers around Ollama calls in `multi_model_qa.py` (90s for the main critic, 60s for each gate, 5s for health checks).
4. `asyncio.wait_for` wrappers around the DDGS thread-executor call in `web_research.py` (20s).

After these fixes, the worst-case stall for any external call is its per-call budget. A hung Ollama, SDXL, DDG, or other service surfaces as a timeout error within ~60-180 seconds, not silently forever.

**If it happens anyway.** Clear the stuck row manually:

```sql
UPDATE pipeline_tasks
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
FROM pipeline_tasks
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

**Fix.** Reject the task with `allow_revisions=false` and reason `off_brand`. As of the Prefect cutover Stage 4 (2026-05-16) `task_executor.py` is deleted and the `_auto_retry_failed_tasks` sweep no longer exists — Prefect handles retries natively at the flow level, and a `rejected` task never re-enters the dispatch queue. Pre-Stage-4 the `_auto_retry_failed_tasks` fix (commits `acdd1640` and `bc0450f1`) was what kept `allow_revisions=false` rejections permanently rejected.

**Prevention.** There's a feedback rule in the Claude memory system: "Don't queue or approve topics requiring straight-faced handling of RFC jokes, parody, or meta-humor; brand isn't mature enough." Future Claude sessions will flag these pre-generation. Topic discovery itself doesn't yet have this filter — a candidate improvement tracked as part of #157.

---

## Auto-retry brings back tasks I explicitly rejected with allow_revisions=false

**Symptom.** You reject a task via the `/reject` endpoint with `allow_revisions: false`. The task's status flips to `rejected`. A few minutes later, the same `task_id` comes back in `awaiting_approval` with regenerated content. The original rejection feedback is ignored.

**Root cause (historical, pre-2026-05-16).** Before the Prefect cutover Stage 4 deleted `task_executor.py` outright, the reject endpoint at `approval_routes.py::reject_task` set `status='failed'` when `allow_revisions=false` and `status='failed_revisions_requested'` when true. The `_auto_retry_failed_tasks` sweep in `task_executor.py` queried `WHERE status='failed'` — which matched both legitimate execution failures AND deliberate `allow_revisions=false` rejections.

**Fix (current).** Two commits shipped 2026-04-10:

1. `acdd1640` added belt-and-suspenders filter excluding `approval_status='rejected'` and `metadata.allow_revisions='false'` from the retry query.
2. `bc0450f1` did the proper semantic fix: the retry query now matches `status IN ('failed', 'failed_revisions_requested')` and uses `metadata.allow_revisions` as the single source of truth for "should this retry." On retry reset, `approval_status` is also cleared back to 'pending' so the new generation isn't judged against a stale rejection flag.

**If it still bounces back after these fixes.** Check `metadata.allow_revisions` in the `pipeline_tasks` row — it should be `'false'` (string, not boolean). If it's null or 'true' the reject endpoint didn't write it. That would be a regression in `approval_routes.py::reject_task` lines 98-105.

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

## SDXL server "degraded — the database system is starting up" (boot race)

**Symptom.** `curl http://localhost:9836/health` returns `"status":"degraded"`, `"model":null`, and a `degraded_reason` of `DB read failed for 'image_generation_model': the database system is starting up`. Every `/generate` returns 503, so ALL SDXL image generation is down — blog posts silently fall back to Pexels, but **video generation hard-fails** (`[VIDEO] No images could be generated`) because it has no Pexels fallback for frames. `media_reconciliation` then reports `job_failure` with `media_drift: ... N missing video (regen failures)`. The Docker healthcheck still shows the container "healthy" because `/health` returns HTTP 200 even while degraded.

**Root cause.** A startup race after a host reboot or Docker-daemon restart. `restart: unless-stopped` brings `sdxl-server` and `postgres-local` back up in parallel — compose `depends_on: condition: service_healthy` is honored only by `docker compose up`, NOT by restart-policy restarts. SDXL's `startup()` reads `app_settings.image_generation_model` while Postgres is still initialising (Postgres error `57P03`), calls `mark_degraded()`, and — historically — never retried, so it stayed degraded until a manual `POST /reload`. (Observed 2026-06-04: ~21h silent outage; podcasts reconciled to 0 while video stuck at 18 — the podcast-vs-video differential isolates SDXL, since only video depends on SDXL frames.)

**Immediate fix.**

```bash
curl -X POST http://localhost:9836/reload      # re-reads DB config; clears degraded once Postgres is up
curl http://localhost:9836/health              # should show "status":"idle", "model":"sdxl_lightning"
# the missing-video backlog drains on the next media_reconciliation cycle (hourly)
```

`docker restart poindexter-sdxl-server` also works (re-runs `startup()`), but `/reload` is lighter — no model reload, no VRAM churn.

**Prevention (self-heal).** `scripts/sdxl-server.py` now runs a `degraded_watchdog()` background task that re-runs `reload_config()` while degraded, with exponential backoff (`next_retry_delay`: 5s → 60s cap). A boot-race latch now clears itself within ~5s instead of waiting for a manual `/reload`. Covered by `src/cofounder_agent/tests/unit/scripts/test_sdxl_self_heal.py`. The compose `depends_on` health-gate is kept too, but it only covers `docker compose up`, not reboots — the watchdog is the reliable backstop.

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

## Worker OOMs on 24 GB cards at the inline-image stage (writer + SDXL collide)

**Symptom.** On a 24 GB card (RTX 3090 / 4090) the worker crashes with `CUDA out of memory` somewhere between `quality_evaluation` (stage 5) finishing and `replace_inline_images` (stage 7) starting. On a 32 GB card (RTX 5090) the same boundary briefly hits ~98% VRAM (32003 MiB / 32607 MiB, ~604 MB headroom) and survives, but is a single Chrome tab away from OOMing. Discord/Telegram alerts may fire on `nvidia_gpu_memory_used_bytes` between the two stages.

**Root cause.** The writer LLM (~20 GB for `gemma3:27b`) stays resident from the preceding LLM stages because Ollama's default `keep_alive` is 5 minutes. SDXL Lightning then loads ~12 GB on top before the writer has been evicted. `services/gpu_scheduler` already calls `_unload_ollama_models()` when `gpu.lock("sdxl", ...)` acquires, but Ollama treats `keep_alive: 0` as fire-and-forget — the API call returns immediately and the actual VRAM release is asynchronous. A `/generate` request issued seconds later (the inline-image prompt build) can land before the prior unload has finished. See 2026-05-19 jank-audit finding #4.

**Detection.**

```bash
# Sample VRAM at the transition. Run while a task is in stages 5-7:
watch -n 0.5 'nvidia-smi --query-gpu=memory.used,memory.free,memory.total --format=csv,noheader'

# Confirm both models were resident at once:
docker logs poindexter-worker 2>&1 | grep -E "(generate_content|replace_inline_images).*GPU acquired" | tail -10
```

**Fix.** Default-on as of 2026-05-19: `replace_inline_images.execute()` now calls `services.llm_providers.ollama_unload.maybe_unload_writer_before_sdxl()` at stage entry, which:

1. Walks `/api/ps` to find currently-loaded models.
2. Issues `POST /api/generate` with `keep_alive: 0` for each.
3. Sleeps `pipeline_writer_unload_grace_seconds` (default `2`) so the kernel actually frees the VRAM before the inline-image `/generate` lands.

The log marker to confirm the guard is active:

```
[REPLACE_INLINE_IMAGES] Unloaded writer model gemma3:27b before SDXL phase (grace=2.0s)
```

**Tunables.** Both via `app_settings`:

- `pipeline_explicit_writer_unload_before_sdxl` (bool, default `true`) — flip to `false` on 80+ GB hardware where the ~3-5 s writer-reload tax (when the `qa.critic` rail later needs the LLM back) isn't worth the safety margin.
- `pipeline_writer_unload_grace_seconds` (int, default `2`) — bump on slower hardware if `nvidia-smi` shows VRAM still occupied after the unload returns.

**Prevention.** Don't disable the gate unless you've confirmed your card has enough headroom for writer (~20 GB) + SDXL (~12 GB) + OS overhead in parallel. The default-on path costs one model reload (~3-5 s) per task; the OOM costs the whole task plus a restart.

---

## Too many content flows run at once and pin the GPU at ~98% VRAM

**Symptom.** Multiple `content_generation_flow` runs execute simultaneously, each loading an LLM + SDXL onto the single 5090. VRAM climbs toward ~98% (32.0/32.6 GB) and the GPU VRAM alert fires. (The section above covers the _intra-task_ writer↔SDXL collision; this is the _inter-task_ version — N whole pipelines stacked.)

**Root cause.** The number of simultaneous content flows is the Prefect work-pool `concurrency_limit` on `content-pool`. Per the 2026-05-31 stress test (Glad-Labs/poindexter#578): **3 concurrent** flows sit at a stable ~60% VRAM with healthy headroom; **5 concurrent** pin the GPU at ~98% — no OOM yet (Ollama self-gates model residency and serializes), but one model-load from the edge. Throughput barely improves past ~3 anyway, since each `canonical_blog` task is 6-7 min and the extra flows mostly deepen the Ollama queue rather than run in true parallel.

**Fix (current).** The cap is the native Prefect work-pool concurrency limit, set at deploy time from two DB-configurable settings and enforced fail-loud:

- `prefect_content_flow_concurrency` (int, default `3`) — the work-pool concurrency actually applied. This is the safe production value for the 5090.
- `content_flow_max_concurrency` (int, default `3`) — the hard safety ceiling. `scripts/deploy_content_flow.py` calls `resolve_safe_concurrency()`, which **aborts the deploy with a `ValueError`** if the requested concurrency exceeds this ceiling — so a fat-fingered `5` fails loud instead of silently exhausting VRAM (`feedback_no_silent_defaults`).

```sql
-- Inspect the current cap
SELECT key, value FROM app_settings
WHERE key IN ('prefect_content_flow_concurrency', 'content_flow_max_concurrency');

-- Lower the applied concurrency (then re-run the deploy to apply)
UPDATE app_settings SET value = '2', updated_at = NOW()
WHERE key = 'prefect_content_flow_concurrency';
```

```bash
# Re-apply the deployment so the work pool picks up the new limit:
cd src/cofounder_agent && poetry run python -m scripts.deploy_content_flow
```

**Raising the cap on bigger hardware.** On a GPU with more VRAM, raise the ceiling first, then the requested value — both DB-tunable, neither hardcoded:

```sql
UPDATE app_settings SET value = '6' WHERE key = 'content_flow_max_concurrency';
UPDATE app_settings SET value = '5' WHERE key = 'prefect_content_flow_concurrency';
```

**Prevention.** Leave the defaults at `3/3` on a 5090. The pool default before #578 was `1` (TaskExecutor-serialization parity); `3` is the validated safe production value. Don't raise `prefect_content_flow_concurrency` above `content_flow_max_concurrency` — the deploy will refuse it by design.

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

## Most media-wanting posts show no `media_assets` row even though the MP3/MP4 files exist (Glad-Labs/poindexter#560)

**Symptom.** A query like the one below shows that the bulk of published, media-wanting posts have no asset row, even though the podcast/video files are sitting in `~/.poindexter/podcast/` and `~/.poindexter/video/` (and on R2):

```sql
SELECT type, count(*) FILTER (WHERE post_id IS NOT NULL) AS linked
FROM media_assets GROUP BY type;
-- e.g. podcast 16, video 17, video_short 1  — vs 61 posts wanting media
```

**Root cause.** `media_reconciliation` had two gaps that combined into the coverage hole:

1. **14-day scan window.** The job only scanned posts with `published_at >= now() - 14d`. The vast majority of media-wanting posts are older than that, so they were never reconciled.
2. **Rows were only written as a side-effect of regeneration.** The job HEAD-checked R2, and if the file was present it declared the post "in sync" and did nothing — it never checked whether the **DB row** existed. So a post whose file was on R2 but whose `media_assets` row was missing (the common case after the 2026-04/05 fire-and-forget outages) was invisible to the job. Only when a file was genuinely **absent** did the regen path run and stamp a row.

**Fix (shipped #560).** `media_reconciliation` now runs two passes per cycle:

- **Row-stamp pass (unbounded, cheap, no GPU).** For every media-wanting post whose file IS on R2 but has no `media_assets` row, the deterministic R2 URL is stamped onto a row idempotently. This pass is **not** time-windowed (`config.max_lookback_days = 0` = all-time by default) and **not** capped, because it's a pure DB write. It closes the bulk of the gap in one cycle.
- **Regen pass (capped, GPU-bound).** Genuinely-absent files are still regenerated + uploaded + stamped, but only for posts inside `config.lookback_days` (default 14) so a backlog of old, truly-missing media doesn't pin the GPU.

**Tuning.** Both knobs are DB-configurable via the `plugin.job.media_reconciliation` row's `config` blob:

- `config.max_lookback_days` (default `0` = unbounded) — scan window for the row-stamp pass. Set a positive value only to bound the per-cycle HEAD-check fan-out on very large sites.
- `config.lookback_days` (default `14`) — regen window for genuinely-missing files.

**Detection.** Watch the job's metrics — `stamped_podcast` / `stamped_video` spike on the first cycle after a backlog appears, then settle to 0 once coverage is restored. A persistent non-zero `missing_*` with `regen_*_fail > 0` means the file is genuinely absent AND regen is failing — that's the case to dig into (TTS/video service, R2 upload).

**Note on `video_short`.** Shorts (`{post_id}-short.mp4`) have no R2 upload path or public key convention yet, so the reconciliation job does not HEAD-check or stamp them — that's a separate product decision (see #560 acceptance criteria, "if shorts remain a product").

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

1. `POINDEXTER_API_URL` pointing at a stale URL (e.g. an old hosted-staging address) instead of `http://localhost:8002`
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

## `check_health` shows `API: McpOAuthClient: client_id/client_secret are required` + a FALSE `Worker: offline`

**Symptom.** `mcp__poindexter__check_health` reports
`API: McpOAuthClient: client_id/client_secret are required` (or
`oauth init failed: ...`) and `Worker: offline`, but the worker container
is up and the OAuth client _is_ provisioned — `app_settings.mcp_oauth_client_id`
/ `mcp_oauth_client_secret` exist, encrypted (`enc:v1:`), and decrypt +
mint a valid JWT when you test them by hand.

**Root cause.** Not missing creds — a **timing race**. The MCP/brain
processes decrypt those secret rows with `POINDEXTER_SECRET_KEY`. If the
process made its first worker-API call _before_ its env had a usable
`POINDEXTER_SECRET_KEY`, the credential read decrypted to `""`, and the
OAuth client was built — and cached — with empty creds. The cache was
keyed on `if _oauth is None`, so that creds-less client was pinned for the
whole process lifetime; every later call failed with
`client_id/client_secret are required` even after the key was available.
A restart fixed it, which made it look like a stale-cache / down-worker
problem (see also the `reference_mcp_oauth_stale_cache` operator note).

**Fix (self-healing as of Glad-Labs/poindexter#243 follow-up).** Two
defense-in-depth changes make this recover on its own — no restart:

1. `mcp-server/server.py` + `mcp-server-gladlabs/server.py` rebuild the
   OAuth client when the cached one isn't usable
   (`if _oauth is None or not _oauth.using_oauth`), so a transiently
   creds-less client self-heals on a later call once the env/creds are good.
2. The secret readers (`mcp-server/oauth_client.py`,
   `mcp-server-gladlabs/oauth_client.py`, `brain/secret_reader.py`) fall
   back to reading `poindexter_secret_key` from `~/.poindexter/bootstrap.toml`
   (plaintext, where `poindexter setup` writes it) when
   `POINDEXTER_SECRET_KEY` is absent from the launch env — the same
   fallback the CLI already uses.

`check_health` also now reports `Worker: unknown (auth: ...)` instead of a
flat `Worker: offline` when the probe fails for an auth reason, so a
transient credential issue no longer masquerades as a downed worker.

If it recurs without self-healing, confirm the key is present and plaintext:

```bash
python -c "import tomllib,os;d=tomllib.load(open(os.path.expanduser('~/.poindexter/bootstrap.toml'),'rb'));print('present' if d.get('poindexter_secret_key') else 'MISSING')"
```

then restart the affected MCP/brain process to force a clean rebuild.

---

## OpenClaw watchdog can't restart the gateway — "port 18789 is still busy before restart" (false positive)

**Symptom.** `scripts/openclaw-watchdog.ps1` (Scheduled Task, every 2 min) keeps firing but never restores the gateway. `~/.openclaw/logs/watchdog.log` fills with `Gateway recovery FAILED after 3 attempts`. Running `openclaw gateway start` (or `restart`) by hand prints:

```
Gateway start failed: Error: gateway port 18789 is still busy before restart
```

But `Get-NetTCPConnection -LocalPort 18789` returns nothing and `curl http://localhost:18789/` is refused — the port really is free.

**Root cause.** Upstream bug in OpenClaw's Windows Scheduled-Task installer (`node_modules/openclaw/dist/schtasks-*.js`). Its `waitForGatewayPortRelease` helper polls `inspectPortUsage`, which shells out to `Get-CimInstance Win32_Process` with a 1.5 s timeout. When that snapshot times out (common on a busy machine) it returns `"unknown"` instead of `"free"`, the polling loop never sees `"free"`, and the CLI throws `port … is still busy before restart` even though the port is empty. Both the `start` and `restart` subcommands of `openclaw gateway` go through this path.

The bare gateway entry — `node node_modules/openclaw/dist/index.js gateway --port 18789` — does NOT contain `waitForGatewayPortRelease` and is unaffected. OpenClaw itself writes this exact invocation into `~/.openclaw/gateway.cmd` (it's what the OpenClaw Scheduled Task launches). The fix bypasses the broken wrapper by invoking `gateway.cmd` directly.

**Fix.** Patch `scripts/openclaw-watchdog.ps1` (the script is intentionally gitignored — Matt-specific operator state, see `.gitignore`) so `Start-Gateway`/`Restart-Gateway` skip `openclaw gateway start/restart` and drive `gateway.cmd` instead. Three changes:

1. Add a `$GATEWAY_CMD` constant alongside `$GATEWAY_PORT`:

   ```powershell
   $GATEWAY_CMD = "$env:USERPROFILE\.openclaw\gateway.cmd"
   ```

2. Add `Stop-PortListeners` — authoritative kill of whatever owns the gateway port via `Get-NetTCPConnection` (no PowerShell-snapshot timeout, no false positives):

   ```powershell
   function Stop-PortListeners {
       param([int]$Port = $GATEWAY_PORT)
       $listeners = Get-NetTCPConnection -LocalPort $Port -State Listen `
           -ErrorAction SilentlyContinue
       if (-not $listeners) { return }
       $ownerPids = @($listeners |
           Select-Object -ExpandProperty OwningProcess -ErrorAction SilentlyContinue |
           Where-Object { $_ -is [int] -and $_ -gt 0 } |
           Sort-Object -Unique)
       foreach ($ownerPid in $ownerPids) {
           Write-Log "INFO" "Stopping PID $ownerPid (listener on port $Port)"
           Stop-Process -Id $ownerPid -Force -ErrorAction SilentlyContinue
       }
       if ($ownerPids.Count -gt 0) { Start-Sleep -Seconds 2 }
   }
   ```

3. Rewrite `Start-Gateway` to prefer `gateway.cmd`, and rewrite `Restart-Gateway` to skip the broken CLI restart and drive `Start-Gateway` after killing the listener:

   ```powershell
   function Start-Gateway {
       Write-Log "INFO" "Starting OpenClaw gateway..."
       Stop-StaleOpenClawProcesses
       try {
           if (Test-Path $GATEWAY_CMD) {
               Start-Process -FilePath "cmd.exe" `
                   -ArgumentList "/c", "`"$GATEWAY_CMD`"" `
                   -WindowStyle Hidden
           } else {
               Write-Log "WARN" "$GATEWAY_CMD not found; falling back to 'openclaw gateway start' (may trip upstream port-busy false positive)"
               Start-Process -FilePath "powershell.exe" `
                   -ArgumentList "-NoProfile", "-Command", "openclaw gateway start" `
                   -WindowStyle Hidden
           }
           Start-Sleep -Seconds 10
           return (Test-GatewayHTTP)
       } catch {
           Write-Log "ERROR" "Failed to start gateway: $_"
           return $false
       }
   }

   function Restart-Gateway {
       Write-Log "INFO" "Restarting OpenClaw gateway (kill listener + cold start; bypasses upstream busy-check bug)..."
       Stop-PortListeners
       Stop-StaleOpenClawProcesses
       return (Start-Gateway)
   }
   ```

After saving, the next Scheduled Task fire (within 2 min) picks up the new logic — no service restart needed; the script is re-read on each run.

**Verify.** Tail `~/.openclaw/logs/watchdog.log` and confirm the next cycle ends with `Gateway HTTP responding` instead of `Gateway recovery FAILED`. Once stable, follow up by reversing the silencing migration that Glad-Labs/poindexter#600 added to `operator_url_probe_skip_keys` so the brain probe pages on real openclaw outages again.

If after applying the patch the watchdog still fails — and a direct `node node_modules/openclaw/dist/index.js gateway --port 18789` invocation logs `Gateway failed to start: failed to bind gateway socket on ws://127.0.0.1:18789: Error: listen EACCES: permission denied 127.0.0.1:18789` — see the **EACCES on port 18789** entry below. That's a separate (Windows-OS) problem: the upstream CLI's misleading "still busy" error was masking a port that Windows had reserved.

Closes Glad-Labs/poindexter#519.

---

## OpenClaw gateway can't bind — `listen EACCES: permission denied 127.0.0.1:18789`

**Symptom.** After applying the watchdog patch above, the gateway still doesn't come up. Running the bare `gateway.cmd` invocation by hand prints (after a few seconds of `loading configuration… / resolving authentication… / starting… / starting HTTP server…`):

```
Gateway failed to start: failed to bind gateway socket on ws://127.0.0.1:18789: Error: listen EACCES: permission denied 127.0.0.1:18789
```

…and the node process exits. `Get-NetTCPConnection -LocalPort 18789 -State Listen` returns nothing — the port is "free" from the listener-table view, but `bind()` is refused.

**Root cause.** Windows reserves dynamic TCP port ranges for Hyper-V / WSL2 / WinNAT / Docker on boot. The reservation is randomized, and 18789 lands inside an excluded range some of the time. Confirm with:

```powershell
netsh int ipv4 show excludedportrange protocol=tcp
```

If a range like `18736 - 18835` (or anything spanning 18789) appears in the output, that's the block. The upstream OpenClaw CLI doesn't distinguish `EACCES` from `EADDRINUSE` cleanly — its busy-check helper sees the bind fail and reports `port is still busy`, which is the misleading error that originally surfaced in Glad-Labs/poindexter#519.

**Fix.** Restart the Windows NAT driver to force the OS to re-randomize the exclusion ranges (requires Administrator):

```powershell
net stop winnat   # may pause Docker / WSL2 / Hyper-V briefly
net start winnat
netsh int ipv4 show excludedportrange protocol=tcp   # confirm 18789 no longer covered
```

Then let the watchdog's next cycle bring the gateway up, or kick it manually with `cmd.exe /c "%USERPROFILE%\.openclaw\gateway.cmd"`.

**Until upstream lets us pick a different port.** OpenClaw currently hardcodes 18789 in `~/.openclaw/gateway.cmd` (via `OPENCLAW_GATEWAY_PORT=18789`). Hand-editing the file to a port outside the typical Hyper-V dynamic range (e.g. 8789 or 28789) survives until the next OpenClaw upgrade overwrites the script. A persistent fix needs to land upstream — track in a follow-up.

---

## Search page returns no results on the live site

**Symptom.** Visiting `https://www.gladlabs.io/search?q=anything` shows "No articles found" or "Failed to search articles."

**Root cause.** The search page was a `'use client'` component calling the FastAPI backend at `localhost:8002` via `fetchAPI()`. On the live Vercel site, visitors' browsers can't reach a local-only server.

**Fix (shipped).** The search page now fetches `posts/index.json` from R2 and filters client-side. No backend dependency. If it breaks again, check that R2 has a current `static/posts/index.json` — trigger a rebuild via `curl -X POST localhost:8002/api/export/rebuild -H "Authorization: Bearer $TOKEN"`.

---

## `rebuild_static_export` reports "Export failed:" but the export actually succeeded

**Symptom.** The `rebuild_static_export` MCP tool (or `POST /api/export/rebuild` driven through the MCP server) returns `Export failed:` with an empty or `ReadTimeout` error, yet the worker log for the same request shows the export completing and the R2 files are current:

```
[STATIC_EXPORT] Full rebuild complete — 92 posts, 6 categories, 2 authors, 0 errors
POST /api/export/rebuild took 34996ms (status: 200)
```

**Root cause.** A full rebuild runs ~35s at current post volume, but the MCP server's `_api` helper defaulted to a 15s httpx read timeout. The client aborted with `ReadTimeout` before the worker's 200 came back, and `_api` flattens that into `{"error": ...}` → a false `Export failed:` on a successful export. Dangerous for operators — a takedown/rebuild looks failed and may be retried or assumed not to have worked (Glad-Labs/poindexter#657).

**Fix (shipped).** `_api` now takes a per-call `timeout` and `rebuild_static_export` passes `120.0s` — comfortable headroom over the ~35s worst case while still failing fast for everything else (the 15s default is unchanged for other tools). If the export ever legitimately grows past ~2 min, raise that constant in `mcp-server/server.py`. A genuine non-2xx / transport error still surfaces as `Export failed:`.

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

**Root cause.** `app_settings.pipeline_writer_model` silently got flipped off the intended writer (`ollama/glm-4.7-5090:latest`) and onto a smaller budget-tier model like `ollama/gemma3:27b`. The content generator reads that key on every run via `services.ai_content_generator._resolve_model_list` (see `ai_content_generator.py` ~line 636) — **not** `model_role_writer`, which looks like a sibling setting but is orphaned and unused by the generator. With the weaker writer active the content hits fact-fabrication / citation-hallucination patterns that the anti-hallucination gates (`modules/content/content_validator.py` `UNLINKED_CITATION_PATTERNS`, `HALLUCINATED_LINK_PATTERNS`) correctly kill.

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

All four fixes have regression tests.

---

## Rejection message says `veto: url_verifier @ 90: 2 verified external citations (+10 bonus)`

**Symptom.** A rejected task's `error_message` names `url_verifier` as the vetoing reviewer, but quotes its feedback as a positive "+10 bonus" note. Makes zero sense as a rejection reason.

**Root cause.** `services/stages/cross_model_qa._build_rejection_reason()` historically fell back to `reviews[-1]` when no reviewer had `approved=False`. url_verifier is typically the last-added reviewer (stage 2f), and when it approves with score 90 ("+bonus"), the fallback still named it as "the veto" — even though the real rejection mode was the score gate (final_score below `qa_final_score_threshold`).

**Fix.** Commits `aa4648ca` + `70297913` — `_build_rejection_reason()` now distinguishes three cases:

1. A reviewer truly vetoed (via the gate logic, not just `approved=False`) → name them
2. No one vetoed but the final_score is below threshold → report `score-gate: below approval threshold, lowest reviewer X @ Y`
3. No reviews at all → explicit "No reviews recorded"

The function also mirrors the special-case gate logic for `internal_consistency` — `approved=False` with score ≥ `qa_consistency_veto_threshold` is advisory, not a veto. Two regression tests pin both branches.

**Note (#355).** `cross_model_qa` was later retired; rejection-reason building now lives in the `qa.aggregate` atom (`modules/content/atoms/`). This entry is the original postmortem on the retired stage — if the symptom recurs on the current graph_def path, check `qa.aggregate`.

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

## `brain.smart_monitor` is silent on a host without smartmontools

**Symptom.** You're running the brain on a host that doesn't have `smartmontools` installed (e.g. a fresh Windows PC, a minimal Linux VM). You expected SMART monitoring to be active but you don't see any operator pages from `brain.smart_monitor`, and the brain's probe summary shows `status='skipped'` every cycle.

**Root cause.** This is intentional. The probe runs `smartctl --scan-open --json` to enumerate drives, and `shutil.which("smartctl")` returning `None` is treated as "optional dependency missing, not a probe failure." The probe writes one info-level log line and one `audit_log` row on the first cycle that notices, then short-circuits silently on every subsequent cycle (no Telegram, no Discord, no per-cycle `notify_operator()` page). Installing smartmontools is a separate operator decision — there's nothing for the operator to do in the moment a "smartctl missing" page arrives, so paging would be unactionable noise. See `feedback_telegram_vs_discord`: Telegram is critical-alert-only, Discord is the routine-progress lane; "your host doesn't have an optional tool installed" doesn't fit either bucket.

**Fix.** If you want SMART monitoring, install smartmontools and the probe self-activates on the next cycle.

- Linux: `apt install smartmontools` (or your distro's equivalent)
- macOS: `brew install smartmontools`
- Windows: install the smartmontools MSI from <https://www.smartmontools.org/> and make sure the install location is on `PATH` (or set `app_settings.smart_monitor_smartctl_path` to the absolute path of `smartctl.exe`)

Verify the probe is now reading drives by querying `audit_log` for the next probe cycle:

```sql
SELECT created_at, event_type, details
FROM audit_log
WHERE source = 'brain.smart_monitor'
ORDER BY created_at DESC
LIMIT 5;
```

**Real SMART failures still page.** Once smartctl is on PATH, reallocated/pending sectors and overall SMART self-test failures continue to write firing `alert_events` rows at warning or critical severity — the dispatcher routes those through the existing Telegram + Discord path. The skip-with-no-page behavior is scoped specifically to the "tool not installed" branch.

**Related.** `brain/smart_monitor.py` (the `_smartctl_missing_notified` short-circuit at the top of `run_smart_monitor_probe`), `feedback_telegram_vs_discord` (channel discipline), `feedback_no_silent_defaults` (why we still emit an info log + audit row instead of fully silent skip).

---

## Operator Discord (or Telegram) alerts stop landing — "no webhook URL"

**Symptom.** GlitchTip fills with `ValueError: discord_post: no webhook URL — set secret_key_ref (preferred) or row.url on the dispatcher row` (or the Telegram-side equivalent). The `webhook_endpoints.discord_ops` row is correctly configured with `secret_key_ref='discord_ops_webhook_url'` and `url=NULL`; the `app_settings.discord_ops_webhook_url` row has the `enc:v1:` ciphertext prefix and the right length (262 chars for the redacted Discord webhook URL); `POINDEXTER_SECRET_KEY` is set in the worker env and decrypts the ciphertext correctly under `pgp_sym_decrypt`. Worker logs show a smoking-gun WARNING immediately before the failure:

```
services.integrations.secret_resolver WARNING - resolve_secret: row 'discord_ops'
  references 'discord_ops_webhook_url' but no site_config in scope — treating as unconfigured
```

**Root cause.** The integrations framework's `shared_context.py` module is supposed to expose a `set_site_config()` / `get_site_config()` setter/getter pair so the lifespan-bound SiteConfig is reachable from `operator_notify._resolve_site_config()`. If those symbols are missing, `_resolve_site_config()`'s `from services.integrations.shared_context import get_site_config` raises `ImportError`. The historical `except Exception: return None` swallowed the error silently, `notify_operator` then forwarded `site_config=None` to `outbound_dispatcher.deliver`, and `resolve_secret(row, None)` short-circuited with "no site_config in scope" and returned `None`. The handler then raised "no webhook URL" — the misleading error mask. This was the 2026-05-26 regression (PR #514 reference, missing wiring).

**Diagnostic playbook (in order).** Before assuming the worst:

1. Confirm the `webhook_endpoints` row is healthy:

   ```bash
   docker exec poindexter-postgres-local psql -U poindexter -d poindexter_brain -c \
     "SELECT name, direction, url, secret_key_ref, last_error FROM webhook_endpoints WHERE name = 'discord_ops';"
   ```

   `url` should be NULL, `secret_key_ref='discord_ops_webhook_url'`, `last_error` will pinpoint the handler exception.

2. Confirm the `app_settings` row is correctly encrypted (length 262 for the Discord webhook URL shape):

   ```bash
   docker exec poindexter-postgres-local psql -U poindexter -d poindexter_brain -c \
     "SELECT LEFT(value, 20), LENGTH(value), is_secret FROM app_settings WHERE key = 'discord_ops_webhook_url';"
   ```

   `LEFT` should be `enc:v1:` + the first ~13 base64 chars; `is_secret = t`.

3. Confirm `POINDEXTER_SECRET_KEY` decrypts the ciphertext:

   ```bash
   KEY=$(docker exec poindexter-worker printenv POINDEXTER_SECRET_KEY)
   docker exec poindexter-postgres-local psql -U poindexter -d poindexter_brain -c \
     "SELECT pgp_sym_decrypt(decode(SUBSTRING(value FROM 8), 'base64'), '$KEY')::text \
        FROM app_settings WHERE key = 'discord_ops_webhook_url';"
   ```

   If this returns the Discord URL, the encryption layer is fine. If it raises, the key was rotated and the ciphertext needs re-encryption with the current key (or the operator lost the original key — see `poindexter setup --rotate-secrets`).

4. Grep the worker log for the secret_resolver warning. The presence of "no site_config in scope" pins the DI seam as the broken link, NOT the encryption layer:
   ```bash
   docker logs poindexter-worker --since 1h 2>&1 | grep "no site_config in scope"
   ```

**Fix.** Restore the `set_site_config` / `get_site_config` pair in `services/integrations/shared_context.py`, wire `main.py`'s lifespan (via `services/di_wiring.py::wire_site_config_modules`) to call `set_site_config(_site_cfg)` after `_site_cfg.load(pool)` returns, and confirm the regression test at `tests/unit/services/integrations/test_shared_context_site_config.py` (which exercises the real import path, not a mock) passes. The previous pin in `test_operator_notify_site_config_fallback.py` mocked over `_resolve_site_config` itself, so the broken import never tripped — keep that test for the no-clobber invariant, but the new test is the one that guards this class of regression.

**Related.** PR #514 (2026-05-20) introduced the original missing-import; the 2026-05-26 fix branch restored the wiring + added the test.

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
