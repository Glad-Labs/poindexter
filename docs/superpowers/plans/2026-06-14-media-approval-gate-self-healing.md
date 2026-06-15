# Self-Healing Media Approval Gate — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the per-medium approval gate cover ALL media (podcast, video, reconciliation-generated) before any public surface, and make it self-healing — so the 2026-05-27→06-13 podcast-feed freeze cannot recur.

**Architecture:** Three independent changes, shipped as a 3-PR series against `Glad-Labs/glad-labs-stack` (never `main`):

1. **Seed at asset-creation** — every reconciliation-created `media_asset` seeds its `media_approvals` row inline, so gating no longer depends on the dormant `podcast_distribute` job.
2. **Gate the video feed (+ grandfather)** — the video RSS feed requires an approved row like the podcast feed already does; a one-shot migration grandfathers the already-live videos so the gate flip doesn't freeze them.
3. **Rebuild feeds on approval** — approving a medium (which happens _after_ publish) rebuilds the matching R2 feed immediately, instead of waiting for the next publish.

**Tech Stack:** Python 3 / FastAPI / asyncpg / pytest. Migration runner (`services/migrations/`). R2 (S3-compatible) via `R2UploadService`. httpx.

## Root cause (recorded)

`routes/podcast_routes.py::podcast_feed` requires `media_approvals.status='approved'` (medium='podcast'). The ONLY seeder of podcast approval rows was `podcast_distribute`, gated `podcast_pipeline_trigger_enabled=false` (dormant). Reconciliation-generated podcasts (`services/jobs/media_reconciliation.py`) got `media_assets` rows but no approval rows → excluded from the feed → frozen. Note: `podcast_distribute` _already_ has a backlog-heal pass for exactly this, but it never runs because the whole job is dormant — which is precisely why the seed must move to asset-creation time.

## Verified data shapes (prod, read-only survey)

- `media_approvals.medium` distinct values = `{podcast, video, video_short}`. **No `video_long` medium ever** — it's a `media_assets.type` that maps to medium `video` (`media_distribute._TYPE_TO_MEDIUM`). So the long-form video-feed gate is `medium='video'`, NOT the spec's literal `video_long`.
- Published posts with a video asset but no approved video row ⇒ the gate flip would freeze ~a dozen live videos without the grandfather. Grandfather scope (published + video/video_long asset + no `video` approval row) ≈ a single-digit row count.
- Sourcing the video feed from `media_assets` (like the podcast feed) drops **0** currently-approved videos — strictly safe.
- All currently-approved videos have `video`/`video_long` in `media_to_generate` — the niche-policy filter is safe to include (0 dropped).
- The only `pending` video rows are recent, never-decided pipeline output — the grandfather must skip them (`ON CONFLICT DO NOTHING`).

## Deviations from the literal spec (data-justified)

1. **Video feed gate column** = `medium='video'` (not `video_long`) — `video_long` is never a `media_approvals.medium`. Including it would be dead SQL.
2. **Video feed sourced from `media_assets`** (mirroring the podcast feed) instead of bolting a gate onto the disk-glob — verified 0 regressions, makes the feed/grandfather scopes identical, and correctly handles Stage-2 task-keyed videos. This is the most faithful reading of "like the podcast feed."

---

## PR 1 — Seed approval at asset-creation (`media_reconciliation`)

**Files:**

- Modify: `src/cofounder_agent/services/jobs/media_reconciliation.py` (`_record_media_asset`, + new `_seed_approval_gate`)
- Test: `src/cofounder_agent/tests/unit/services/jobs/test_media_reconciliation_job.py`
- Docs: `docs/architecture/podcast-pipeline-stage3.md` (note the asset-creation seed)

- [ ] **Step 1 — Failing test: stamp seeds a pending approval row.** Assert `_record_media_asset(pool, post_id=PID, asset_type='podcast', url=URL)` calls `media_approval_service.record_pending(pool, PID, 'podcast')`. Patch `services.media_approval_service.record_pending` with an `AsyncMock`; use the existing pool/conn fixture in this test file.
- [ ] **Step 2 — Run it, expect FAIL** (`record_pending` not called).
- [ ] **Step 3 — Implement.** After the stamp succeeds in `_record_media_asset`, call a new `_seed_approval_gate(pool, post_id, asset_type)` that lazy-imports `record_pending` and calls it. `return` early in the stamp `except` so a failed stamp does not seed. Map is identity (`podcast→podcast`, `video→video`); both are valid media verbatim. Wrap the seed in its own `try/except` (non-fatal, `noqa: BLE001`) with a distinct warning so a seed failure is not mislabeled as a stamp failure.
- [ ] **Step 4 — Run it, expect PASS.**
- [ ] **Step 5 — Failing test: `asset_type='video'` seeds medium `video`.** Expect PASS after step 3 (same code path).
- [ ] **Step 6 — Failing test: seed failure is non-fatal.** `record_pending` raises → `_record_media_asset` returns without raising; warning logged.
- [ ] **Step 7 — Failing test: no seed when stamp fails.** `pool.acquire` raises → `record_pending` NOT called.
- [ ] **Step 8 — Run the full file** `pytest tests/unit/services/jobs/test_media_reconciliation_job.py -q`. Expect PASS.
- [ ] **Step 9 — Docs + module docstring.** Add a line to the `media_reconciliation.py` "Fail-loud contract" docstring section noting every stamped asset now seeds its approval gate; add a sentence to `podcast-pipeline-stage3.md`.
- [ ] **Step 10 — Commit** `feat(media): seed approval gate at reconciliation asset-creation (self-healing) (Glad-Labs/glad-labs-stack)`.

## PR 2 — Gate the video feed + grandfather backfill

**Files:**

- Modify: `src/cofounder_agent/routes/video_routes.py` (`video_feed` query → `media_assets`-sourced + approval gate)
- Create: `src/cofounder_agent/services/migrations/<UTC>_grandfather_video_media_approvals.py`
- Test: `src/cofounder_agent/tests/unit/routes/test_video_routes.py`, + a migration SQL-shape test
- Docs: `docs/architecture/podcast-pipeline-stage3.md` (video-feed gate + grandfather)

- [ ] **Step 1 — Generate migration** `python scripts/new-migration.py "grandfather video media_approvals for already-live videos"`.
- [ ] **Step 2 — Failing test: video feed excludes posts without an approved row.** Mirror `test_podcast_routes`/existing `test_video_routes` fixture: mock `get_services().get_database()` pool `fetch` to return rows; assert the SQL the handler runs joins `media_approvals … status='approved' … medium='video'` and the rendered RSS contains only approved post slugs.
- [ ] **Step 3 — Run it, expect FAIL** (current handler globs disk, no gate).
- [ ] **Step 4 — Implement the feed.** Replace the disk-glob body of `video_feed` with a `media_assets`-sourced query mirroring `podcast_feed`:
  ```sql
  SELECT DISTINCT ON (p.id)
         p.id::text AS post_id, p.title, p.slug, p.excerpt, p.published_at,
         mas.url, mas.file_size_bytes
  FROM posts p
  JOIN media_assets mas
    ON mas.post_id = p.id AND mas.type IN ('video','video_long')
  JOIN media_approvals ma
    ON ma.post_id = p.id AND ma.medium = 'video' AND ma.status = 'approved'
  WHERE p.status = 'published'
    AND ('video' = ANY(media_to_generate) OR 'video_long' = ANY(media_to_generate))
  ORDER BY p.id, mas.created_at DESC NULLS LAST
  ```
  Build items from rows (enclosure falls back to `f"{_r2}/video/{post_id}.mp4"` when `url` is empty, like podcast). Keep the empty-feed fallback. Sort newest-first by `published_at` after `DISTINCT ON`.
- [ ] **Step 5 — Run it, expect PASS.**
- [ ] **Step 6 — Failing test: empty feed when nothing approved.** `fetch` returns `[]` → minimal `<rss>` shell, 200.
- [ ] **Step 7 — Implement the grandfather migration** (`up`/`down`, light stdlib imports only):
  ```sql
  -- up
  INSERT INTO media_approvals (post_id, medium, status, decided_at, decided_by)
  SELECT DISTINCT p.id, 'video', 'approved', NOW(), 'auto:grandfather'
  FROM posts p
  JOIN media_assets mas ON mas.post_id = p.id AND mas.type IN ('video','video_long')
  WHERE p.status = 'published'
    AND NOT EXISTS (SELECT 1 FROM media_approvals ma WHERE ma.post_id = p.id AND ma.medium = 'video')
  ON CONFLICT (post_id, medium) DO NOTHING;
  -- down
  DELETE FROM media_approvals WHERE medium='video' AND decided_by='auto:grandfather';
  ```
  Idempotent: `NOT EXISTS` + `ON CONFLICT` both guard; on a fresh migrations-smoke DB it inserts 0 rows (no posts). Skips the recent `pending` rows by construction.
- [ ] **Step 8 — Migration SQL-shape test.** Assert the module's `up` SQL string contains `'auto:grandfather'`, `'approved'`, `type IN ('video','video_long')`, `NOT EXISTS`, `ON CONFLICT`.
- [ ] **Step 9 — Verify migration tooling:** `python scripts/ci/migrations_lint.py` and `python scripts/ci/migrations_smoke.py` (against a fresh/baseline DB — NOT prod; see the bootstrap-precedence gotcha).
- [ ] **Step 10 — Run** `pytest tests/unit/routes/test_video_routes.py -q`. Expect PASS.
- [ ] **Step 11 — Docs + module docstring.**
- [ ] **Step 12 — Commit** `feat(media): gate video RSS feed on approval + grandfather live videos`.

## PR 3 — Rebuild feeds on approval (not just on publish)

**Files:**

- Create: `src/cofounder_agent/services/media_feed_rebuild.py` (`rebuild_podcast_feed`, `rebuild_video_feed`, `rebuild_feed_for_medium`)
- Modify: `src/cofounder_agent/services/jobs/podcast_distribute.py` (`_rebuild_feed` delegates to the shared helper — DRY)
- Modify: `src/cofounder_agent/services/media_approval_service.py` (`decide` gains keyword-only `site_config=None`; rebuilds the matching feed on approve)
- Modify: `src/cofounder_agent/poindexter/cli/media.py` (`_decide` builds `site_config` and passes it)
- Modify: `src/cofounder_agent/routes/media_approval_routes.py` (`decide` route injects `site_config` via DI and passes it)
- Test: `tests/unit/services/test_media_feed_rebuild.py`, `tests/unit/services/test_media_approval_service.py`
- Docs: `docs/architecture/podcast-pipeline-stage3.md`

- [ ] **Step 1 — Failing test: `rebuild_feed_for_medium` routing.** `podcast`→`rebuild_podcast_feed`; `video`→`rebuild_video_feed`; `video_short`→neither (no RSS surface). Patch the two rebuild fns.
- [ ] **Step 2 — Run it, expect FAIL** (module doesn't exist).
- [ ] **Step 3 — Implement `media_feed_rebuild.py`** by lifting the `podcast_distribute._rebuild_feed` pattern: GET `{internal_api_base_url}/api/{podcast|video}/feed.xml` → `R2UploadService.upload_to_r2(tmp, "{podcast|video}/feed.xml", "application/rss+xml")`. Each fn is its own try/except (non-fatal). `rebuild_feed_for_medium` dispatches by medium.
- [ ] **Step 4 — Run it, expect PASS.**
- [ ] **Step 5 — DRY: refactor `podcast_distribute._rebuild_feed`** to `await rebuild_podcast_feed(site_config)`; run `pytest tests/unit/services/jobs/test_podcast_distribute.py -q` (expect still PASS).
- [ ] **Step 6 — Failing test: `decide(approved=True, medium='podcast', site_config=stub)` triggers `rebuild_feed_for_medium`.** Patch it; assert called with `(stub, 'podcast')`.
- [ ] **Step 7 — Implement** the `decide` hook (keyword-only `site_config=None`; only on `approved and site_config is not None`; lazy import; non-fatal outer guard). Backcompat: existing callers without the kwarg are unaffected (`feedback_backcompat_now_required`).
- [ ] **Step 8 — Failing tests:** reject → no rebuild; `site_config=None` → no rebuild; rebuild raising → `decide` still succeeds.
- [ ] **Step 9 — Wire callers.** CLI `_decide`: build `site_config` via the `publish_approval._make_site_config(pool)` pattern (`SiteConfig(pool=pool)` + `await load`) and pass it. HTTP route `decide`: add `site_config = Depends(get_site_config_dependency)` and pass it.
- [ ] **Step 10 — Run** `pytest tests/unit/services/test_media_approval_service.py tests/unit/services/test_media_feed_rebuild.py -q`. Expect PASS.
- [ ] **Step 11 — Docs + docstrings.**
- [ ] **Step 12 — Commit** `feat(media): rebuild matching R2 feed on media approval (self-healing propagation)`.

## Cross-cutting verification

- [ ] Per-PR: `poetry run pytest tests/unit/ -q` for touched paths green.
- [ ] PR2: `migrations_lint.py` + `migrations_smoke.py` green.
- [ ] No new route/router added (only handler bodies changed) → the `_WORKER_ROUTES` count guard + `services.md` doc-drift do NOT apply.
- [ ] Each PR opened against `Glad-Labs/glad-labs-stack`; CI green = merge (`feedback_ci_is_the_review_gate`).
- [ ] Operator heads-up: 2 recent un-approved videos drop from the public video feed until `poindexter media approve <id> video`.

## Self-review

- Spec coverage: step 1 → PR1; step 2 (gate + grandfather footgun) → PR2; step 3 (rebuild on approval, reuse `_rebuild_feed`, add video equivalent, non-fatal + idempotent) → PR3. ✓
- Confirm Stage-2 `media_distribute` already seeds `video`/`video_short` via `record_pending` → confirmed (`_TYPE_TO_MEDIUM`), no change needed. ✓
- Backcompat: `decide` new arg keyword-only w/ default → no breakage. ✓
- No placeholders; every code step shows the code. ✓
