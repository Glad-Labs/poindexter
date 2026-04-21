# Troubleshooting

Runbook for issues that have bitten us in production. Each entry has a symptom, a root cause, a fix, and a link to the Gitea issue or commit where it was addressed. When you hit something new, add it here instead of just fixing it — the next person (or next-you) will thank you. Link to GitHub issues for product bugs, Gitea for operator-specific items.

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

**Related.** [docs/operations/ci-deploy-chain.md](./ci-deploy-chain.md) has the full chain diagram.

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

## OpenClaw MCP tools fail — "tool execution failed" from Discord

**Symptom.** Using `#reject_post` or other MCP tools from Discord via OpenClaw reports success in the chat message but the action didn't actually happen (e.g., tasks still show `awaiting_approval` in the DB). The LLM fabricates a success response.

**Root cause.** The Claude Desktop MCP config (`claude_desktop_config.json`) has wrong values:

1. `POINDEXTER_API_URL` pointing at a dead Railway URL instead of `http://localhost:8002`
2. `POINDEXTER_API_TOKEN` is stale (doesn't match the current `api_token` in bootstrap.toml)
3. `OPENCLAW_GATEWAY_TOKEN` is missing from the gladlabs MCP env

**Fix.** Edit `%LOCALAPPDATA%\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Roaming\Claude\claude_desktop_config.json`:

- Set `POINDEXTER_API_URL` to `http://localhost:8002`
- Set `POINDEXTER_API_TOKEN` to the value from `~/.poindexter/bootstrap.toml`
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
