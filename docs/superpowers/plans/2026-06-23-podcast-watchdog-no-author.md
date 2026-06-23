# Podcast Watchdog — Detect + Gated Re-dispatch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax. TDD throughout.

**Goal:** Stop `media_reconciliation` from authoring duplicate, Gate-2-bypassing podcasts; make it detect drift and re-dispatch the gated pipeline (or re-deliver an already-approved file) instead.

**Architecture:** Reconciliation's podcast "missing" decision moves from an R2 HEAD (delivery check) to a DB-row check (existence check), mirroring the video lane. The direct `generate_podcast_episode` author path is deleted. A genuine miss (no row) clears `podcast_dispatched_at` so `dispatch_podcast_pipeline` re-renders through QA + Gate-2; a delivered-then-lost R2 file is re-uploaded from the durable local copy (no re-render).

**Tech Stack:** Python 3.12, asyncpg, pytest. Spec: `docs/superpowers/specs/2026-06-23-podcast-watchdog-no-author.md`.

## Global Constraints

- Reconciliation MUST NEVER call `generate_podcast_episode` or otherwise author podcast content (regression-tested).
- New podcast re-dispatch is capped by `pipeline_tasks.podcast_redispatch_count < podcast_redispatch_max` (default `"3"`), mirroring video's `media_pipeline_redispatch_count` / `media_pipeline_redispatch_max`.
- `app_settings` defaults go in `settings_defaults.py`, never a migration (`feedback_seed_data_in_baseline`).
- Schema DDL goes in a timestamped migration via `python scripts/new-migration.py "..."`.
- Tests-first; one behavior per test; commit per task. PR off `origin/main`.
- Test runner (host venv): `PYTHONPATH=<worktree>/src/cofounder_agent <py> -m pytest <test> -q -p no:cacheprovider --no-header` where `<py>` = the borrowed `poindexter-backend-*` venv python.

---

## File Structure

- **Create** `src/cofounder_agent/services/migrations/<ts>_add_podcast_redispatch_count.py` — `ADD COLUMN podcast_redispatch_count INTEGER NOT NULL DEFAULT 0` on `pipeline_tasks` (idempotent `IF NOT EXISTS`).
- **Modify** `src/cofounder_agent/services/settings_defaults.py` — add `"podcast_redispatch_max": "3"`.
- **Modify** `src/cofounder_agent/services/jobs/media_reconciliation.py`:
  - the `media_assets` lookup query → also select `storage_provider`, `storage_path`, `url` per `(post_id,'podcast')`.
  - `_check_post_media` → `podcast_missing` from DB row; add `podcast_delivered_gone` flag.
  - new `_PODCAST_RESOLVE_TASK_SQL` / `_PODCAST_CLEAR_MARKER_SQL`; new `_redispatch_podcast`, `_redeliver_podcast`; delete `_regen_podcast` body + `_promote_existing_podcast`.
  - `run()` Pass-2 podcast branch → call the new remedies; tally; finding.
- **Modify** `src/cofounder_agent/tests/unit/services/jobs/test_media_reconciliation_job.py` — branch tests + never-author regression.

---

## Task 1: Schema column + cap setting

**Files:**

- Create: `src/cofounder_agent/services/migrations/<ts>_add_podcast_redispatch_count.py`
- Modify: `src/cofounder_agent/services/settings_defaults.py`
- Test: `src/cofounder_agent/tests/unit/services/test_settings_defaults.py` (or the existing defaults test)

- [ ] **Step 1:** Generate the migration: `python scripts/new-migration.py "add podcast_redispatch_count to pipeline_tasks"`. Implement `up()` as:
  ```python
  await conn.execute(
      "ALTER TABLE pipeline_tasks "
      "ADD COLUMN IF NOT EXISTS podcast_redispatch_count INTEGER NOT NULL DEFAULT 0"
  )
  ```
- [ ] **Step 2:** Add `"podcast_redispatch_max": "3"` to `DEFAULTS` in `settings_defaults.py` (next to `media_pipeline_redispatch_max`).
- [ ] **Step 3:** Run migration lint + smoke: `python scripts/ci/migrations_lint.py` then `python scripts/ci/migrations_smoke.py`. Expected: green, column present on fresh DB.
- [ ] **Step 4:** Commit: `feat(media): add podcast_redispatch_count column + podcast_redispatch_max default`.

---

## Task 2: Podcast existence = DB row; add delivery-integrity flag

**Files:**

- Modify: `src/cofounder_agent/services/jobs/media_reconciliation.py` (the `media_assets` lookup query + `_check_post_media`)
- Test: `tests/unit/services/jobs/test_media_reconciliation_job.py`

**Interfaces — Produces:** `_check_post_media` returns the row augmented with:

- `podcast_missing: bool` = wants_podcast AND `(post_id,'podcast') not in existing_pairs` (DB row, **not** R2 HEAD).
- `podcast_delivered_gone: bool` = wants_podcast AND a delivered row exists (`storage_provider=='cloudflare_r2'`) AND the R2 HEAD 404s.
- Existing `podcast_exists` (R2 HEAD) is retained for the unchanged Pass-1 row-stamp.
  The `(post_id,type)` lookup is enriched to a dict `existing_assets[(post_id,'podcast')] = {storage_provider, storage_path, url}`.

- [ ] **Step 1 (test, no-row → missing):** post wants podcast, no podcast row → `podcast_missing is True`, `podcast_delivered_gone is False`.
- [ ] **Step 2 (test, local pending → NOT missing):** post wants podcast, row with `storage_provider='local'` present → `podcast_missing is False` and `podcast_delivered_gone is False`. _(the dedup pin — this used to be True via R2 HEAD)_
- [ ] **Step 3 (test, delivered + R2 gone):** row `storage_provider='cloudflare_r2'`, R2 HEAD 404 (mock `_exists`→False) → `podcast_missing is False`, `podcast_delivered_gone is True`.
- [ ] **Step 4 (test, delivered + R2 present → healthy):** row `storage_provider='cloudflare_r2'`, R2 HEAD 200 (mock `_exists`→True) → `podcast_missing is False` AND `podcast_delivered_gone is False`. _(no action branch — spec state-machine row 4)_
- [ ] **Step 5:** Run the four → FAIL.
- [ ] **Step 6:** Implement: enrich the asset-lookup SQL (`SELECT post_id::text, type, storage_provider, storage_path, url ...`) into `existing_assets`; set the three flags in `_check_post_media`. Keep `podcast_exists` (R2 HEAD) for stamp pass.
- [ ] **Step 7:** Run → PASS. Commit: `fix(media): podcast presence keyed on DB row, not R2 HEAD`.

---

## Task 3: Re-dispatch on genuine miss; delete the direct author

**Files:**

- Modify: `media_reconciliation.py` (add podcast SQL + `_redispatch_podcast`; delete `_regen_podcast` author body + `_promote_existing_podcast`; rewire `run()` Pass-2 podcast branch)
- Test: `tests/unit/services/jobs/test_media_reconciliation_job.py`

**Interfaces — Produces:**

- `_PODCAST_RESOLVE_TASK_SQL` (post_id → `task_id`, `podcast_redispatch_count`) and `_PODCAST_CLEAR_MARKER_SQL` (`SET podcast_dispatched_at=NULL, podcast_redispatch_count=podcast_redispatch_count+1 WHERE task_id=$1 AND podcast_redispatch_count<$2`), mirroring the video pair.
- `async def _redispatch_podcast(self, pool, post_row) -> bool` — mirrors `_redispatch_video`: resolves task, caps on `podcast_redispatch_max`, clears marker; returns True iff cleared.

- [ ] **Step 1 (test, no-row → re-dispatch):** missing podcast with a resolvable task and count<cap → `_redispatch_podcast` clears `podcast_dispatched_at`, bumps count; returns True. Assert `generate_podcast_episode` is NOT called (monkeypatch it to raise).
- [ ] **Step 2 (test, capped):** count==cap → returns False, marker untouched.
- [ ] **Step 3 (test, no task seam):** post with no `pipeline_task_id` → returns False (finding only).
- [ ] **Step 4:** Run → FAIL.
- [ ] **Step 5:** Implement `_redispatch_podcast` + the two SQL consts. In `run()` Pass-2, replace the `_regen_podcast` loop with a `_redispatch_podcast` loop over `regen_podcast` candidates (rename to `redispatch_podcast`). Delete `_regen_podcast`'s author body and `_promote_existing_podcast` entirely. Keep the drift finding.
- [ ] **Step 6:** Run → PASS. Run the full file: `pytest tests/unit/services/jobs/test_media_reconciliation_job.py -q`. Expected: green.
- [ ] **Step 7:** Commit: `fix(media): reconciliation re-dispatches podcast pipeline, never authors`.

---

## Task 4: Re-deliver a delivered-then-lost episode (no re-render)

**Files:**

- Modify: `media_reconciliation.py` (`_redeliver_podcast`; wire into `run()` Pass-2)
- Test: `tests/unit/services/jobs/test_media_reconciliation_job.py`

**Interfaces — Produces:** `async def _redeliver_podcast(self, pool, post_row) -> bool` — reads the post's podcast `storage_path` from `existing_assets`; if the local file exists, `R2UploadService.upload_to_r2(storage_path, f"podcast/{cdn_ver}/{post_id}.mp3", "audio/mpeg")` and return True; if local file gone, return False (caller falls back to `_redispatch_podcast`).

- [ ] **Step 1 (test, redeliver):** `podcast_delivered_gone` post, local file present → `_redeliver_podcast` calls `upload_to_r2` with the post-keyed key, returns True; `generate_podcast_episode` NOT called.
- [ ] **Step 2 (test, local gone → fallback):** `podcast_delivered_gone` post, local file absent → `_redeliver_podcast` returns False and `run()` then calls `_redispatch_podcast`.
- [ ] **Step 3:** Run → FAIL.
- [ ] **Step 4:** Implement `_redeliver_podcast`; in `run()` Pass-2, route `podcast_delivered_gone` posts to `_redeliver_podcast` (fallback `_redispatch_podcast`).
- [ ] **Step 5:** Run → PASS. Commit: `feat(media): reconciliation re-delivers an approved podcast whose R2 file vanished`.

---

## Task 5: Never-author regression + full-file green

**Files:**

- Test: `tests/unit/services/jobs/test_media_reconciliation_job.py`

- [ ] **Step 1 (test):** Drive a full `MediaReconciliationJob.run()` over a fixture set covering all four states with `generate_podcast_episode` and `upload_podcast_episode` monkeypatched to raise `AssertionError`. Assert the run completes (the author functions are never reached) and the missing/redispatch tallies are correct.
- [ ] **Step 2:** Run → it should already PASS (guard test). If it fails, a stray author call remains — fix it.
- [ ] **Step 3:** Run the full reconciliation test file + the settings/migration tests. Expected: green.
- [ ] **Step 4:** Commit: `test(media): reconciliation never authors podcast content (regression)`.

---

## Out of scope (per spec)

- Cleanup of the ~25 redundant local-pending rows and re-pushing the 65 grandfathered R2 episodes through Gate-2.
- Any change to `podcast_pipeline`, `podcast_distribute`, or the video lane.
