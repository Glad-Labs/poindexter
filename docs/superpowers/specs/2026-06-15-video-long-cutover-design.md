# Video-side cutover — drop `video_long` (design)

**Issue:** [Glad-Labs/glad-labs-stack#1460](https://github.com/Glad-Labs/glad-labs-stack/issues/1460)
· **Epic:** [poindexter#689](https://github.com/Glad-Labs/poindexter/issues/689)
· **Closes:** #573, #668, #569
· **Status:** design approved 2026-06-15 · **Posture:** ATTENDED-ONLY (live, flag-on YouTube lane; outward-facing double-upload risk)

This is the deferred video half of the media-pipeline atomization. The reference
for the original scope is `docs/architecture/podcast-pipeline-stage3.md` §11. This
spec supersedes the loose §11 sketch with prod-verified data and three locked
decisions.

---

## 1. Problem

`media_assets.type='video_long'` is **not** a naming inconsistency — it is a
load-bearing routing key separating two producers and two distributors:

| Producer                   | writes `type` | path key        | distributor                                   |
| -------------------------- | ------------- | --------------- | --------------------------------------------- |
| `media.persist` (pipeline) | `video_long`  | `{task_id}.mp4` | `media_distribute` (join CASE → `video_long`) |
| `media_reconciliation`     | `video`       | `{post_id}.mp4` | `backfill_videos` (disk scan)                 |

`media_distribute`'s join `mas.type = CASE ma.medium WHEN 'video' THEN 'video_long' …`
makes that job deliver **only** the pipeline render. Collapsing both names to
`video` makes the join match **both** rows per post → with
`media_pipeline_trigger_enabled=true` (live) the next cycle could **double-upload
the same video to YouTube**. The rename is therefore inseparable from the
distributor join change, the reconciliation rewrite, and a de-dup data migration.
It must land as a coordinated, attended cutover.

## 2. Prod-verified facts (2026-06-15, `poindexter_brain`)

These ground the migration and correct the stale "10 stray rows" framing:

- **Video-family `media_assets`:** `video_long`/pipeline = 10; `video`/reconciliation
  = 71 (41 with a non-empty `storage_path`); `video`/pipeline = 1; `video_short`/pipeline = 9.
- **Per-post dup shape:** one test post (`…-custom-wa-6b1f93d8`, "steam-engine") has
  **18 video assets** (8 `video_long` + 1 `video` + 9 `video_short`); 8 posts have
  **2 `video` rows each** (duplicate reconciliation stamps, unrelated to `video_long`);
  the 2 "clean" `video_long` posts each have **the pipeline render already on YouTube**
  (`platform_video_ids` set on the `video_long` row, _not_ the reconciliation sibling);
  53 posts have exactly 1 `video`.
- **Existing rows are currently upload-safe:** every video approval is already
  `dispatched_at IS NOT NULL` (`video` 63/63, `video_short` 59/59) and mostly
  grandfathered (`video` 61/63, `video_short` 59/59). `media_distribute` filters
  `dispatched_at IS NULL AND decided_by NOT LIKE '%grandfather%'`, so it returns
  **zero** rows for everything live. **The double-upload risk is going-forward, not
  latent under existing rows.**
- **`storage_path` is an unreliable secondary router:** 41/71 reconciliation `video`
  rows have a `storage_path`, so the `storage_path <> ''` filter cannot be relied on
  to exclude reconciliation rows post-collapse. De-dup must be explicit.
- **`video_long` is dead in the policy vocabulary:** 0 niches and 0 posts use it in
  `media_to_generate` (they use `video`/`video_short`). Dropping it from the CLI
  vocabulary is safe.
- **No CHECK constraint on `media_assets.type`** — the relabel is unconstrained.
- **Flags:** `media_pipeline_trigger_enabled=true`, `podcast_pipeline_trigger_enabled=false`.
- **Video CTAs already seeded** (`media.cta.video`, `media.cta.video_short`).

## 3. Decisions

1. **De-dup canonical rule = smart priority.** When a post has >1 video-family asset,
   keep the row by precedence: (a) non-empty `platform_video_ids` → (b) `source='pipeline'`
   → (c) newest `created_at`. Delete the rest. This preserves existing YouTube uploads
   (both clean `video_long` posts carry the id on the pipeline row), and also cleans the
   18-asset test post and the 8 double-reconciliation posts.
2. **Close the root cause with a unique guard.** After de-dup, add a partial unique
   index on `(post_id, type)` for the video families; make producers/linkers
   conflict-aware. Duplicate rows become structurally impossible (today nothing guards
   them — the actual root cause).
3. **Sequence as prep PR → atomic cutover.** PR1 is behaviorally inert and mergeable
   anytime; PR2 is the all-or-nothing unit. (The unique index lives in PR2, immediately
   after the de-dup migration, because it requires de-duped data.)

## 4. Non-goals

- **Podcast paths are out of the blast radius.** `podcast_distribute` is dormant
  (flag off) and the podcast feed already reads `media_assets ⋈ media_approvals`.
  We delete the dead `backfill_podcasts` job (closes #668) but change no live podcast
  behavior.
- **No flag flips, no prod dispatch, no triggering uploads.** Verification is
  in-process only (re-dispatching a published post risks a real YouTube re-upload).
- **State-channel renames are explicitly excluded.** `video_long_script`,
  `long_video_path`, `long_narration_audio_path`, etc. are PipelineState channels, not
  `media_assets.type`. They stay. Only type-valued `video_long` occurrences are dropped.

## 5. PR1 — Safe prep (behaviorally inert)

5.1 **Extract YouTube payload helpers** into a new `services/jobs/youtube_payload.py`
(sibling to `dispatch_handles.py`): `_build_youtube_description`, `_parse_seo_keywords`,
`_strip_markup`, and the `_YOUTUBE_DESCRIPTION_BUDGET` / `_YOUTUBE_MAX_TAGS` /
`_YOUTUBE_TAGS_JOINED_LIMIT` constants. `media_distribute` and (still-living)
`backfill_videos` both import from the new home. Pure relocation; helper unit tests
move with it.

5.2 **Dispatch de-dup guard.** Rewrite `_APPROVED_UNDISPATCHED_SQL` to
`SELECT DISTINCT ON (ma.post_id, ma.medium) …` ordered by the canonical priority
(`platform_video_ids` present DESC → `source='pipeline'` DESC → `created_at` DESC),
wrapped in an outer `SELECT … ORDER BY created_at ASC LIMIT $2` so fairness + cap are
preserved. Inert today (all approvals dispatched), and correct both before the rename
(CASE join) and after (identity join).

**PR1 is correct independent of PR2 and reduces risk if PR2 slips.**

## 6. PR2 — Atomic cutover

6.1 **Writer** — `modules/content/atoms/media_persist.py`: `_TARGETS` long-form tuple
`video_long` → `video`. Filenames unchanged (`{task_id}.mp4` long, `{task_id}_short.mp4`
short). `video_short` unchanged.

6.2 **Distributor** — `services/jobs/media_distribute.py`:

- `_TYPE_TO_MEDIUM` → identity `{"video": "video", "video_short": "video_short"}`.
- `_APPROVED_UNDISPATCHED_SQL` join: CASE → `mas.type = ma.medium`.
- Update the `backfill_videos` deconfliction comment (that job is deleted).

  6.3 **Data migration** — one timestamped file, in this order, **light-env safe**
  (no heavy imports), idempotent, no-op on a fresh baseline DB (no `video_long` rows):

1. Archive every row that will be deleted into a `media_assets_dedup_backup` table
   (destructive + hard-to-reverse → recoverable).
2. Per post, collapse the long-form family `{video, video_long}` to one survivor by the
   §3 smart rule; collapse `{video_short}` to one survivor by the same rule; delete the
   losers. Re-point or archive any `pipeline_distributions` rows that reference a deleted
   asset onto the survivor (verify the FK first).
3. Relabel surviving `video_long` → `video`.
4. `CREATE UNIQUE INDEX … ON media_assets (post_id, type) WHERE post_id IS NOT NULL AND
type IN ('video','video_short')`.

6.4 **Recurrence-safe producers:**

- `services/media_asset_recorder.record_media_asset`: for video-family rows with a
  non-null `post_id`, INSERT `… ON CONFLICT (post_id, type) WHERE type IN ('video','video_short')
DO UPDATE` (refresh `storage_path`/`url`/dims/etc.). Pipeline writes at `post_id=NULL`
  (no conflict at write time); the conflict surface is the link-time back-stamp.
- `media_distribute` link step: before back-stamping `post_id`, skip (and emit a finding)
  when the post already has a video-family asset of that type, so a second task-keyed
  render never loops forever on the unique violation.

  6.5 **Reconciliation** — `services/jobs/media_reconciliation.py`:

- Remove direct `video` production: delete `_regen_video`'s `generate_video_for_post`
  path and the `_record_media_asset(type='video')` row-stamp for video.
- On video drift (published video-wanting post with no video-family `media_assets` row),
  **re-dispatch** Stage-2: clear the task's `media_pipeline_dispatched_at`, capped
  attempts via a new `app_settings` cap (mirrors the §3 podcast safety-net pattern).
- Keep the `media_drift` finding (severity escalates if re-dispatch can't make progress).
- **No-task_id posts:** re-dispatch needs the source `posts.metadata->>'pipeline_task_id'`.
  A published video-wanting post without one (legacy/manual) can't be re-dispatched and no
  longer gets the retired direct-generation fallback — it surfaces in the `media_drift`
  finding (fail-loud, operator-visible) rather than being silently healed. Acceptable:
  canonical_blog always stamps the seam, and existing legacy videos are already
  live/grandfathered.
- **Podcast passes unchanged.**

  6.6 **Delete backfill jobs** — remove `services/jobs/backfill_videos.py` and
  `services/jobs/backfill_podcasts.py`; deregister both from `plugins/registry.py`
  `_SAMPLES`. Helpers already extracted in PR1, so no live import breaks. Closes #668.

  6.7 **Vocabulary cleanup (closes #573/#569)** — drop only type-valued `video_long`:

- `poindexter/cli/posts.py`: remove from `CANONICAL_MEDIA_NAMES` + `--media` help text
  (verified 0 policy usage).
- `services/media_asset_recorder.py`: drop from `_DEFAULT_MIME_TYPES` + docstring
  conventions (keep `video`/`video_short`).
- `services/jobs/media_reconciliation.py` + `routes/video_routes.py`: drop `video_long`
  from the read-side type sets (post-relabel there are no such rows).
- Audit each remaining `video_long` grep hit; leave PipelineState channel names intact.

  6.8 **Video CTA (issue step 6)** — already shipped in #1621 (`media.render_narration` +
  `media.cta.video`/`media.cta.video_short`). No work.

## 7. Data flow after cutover

`media.persist` writes one `video`/`video_short` row (`post_id=NULL`) →
`media_distribute` links it to the post (unique-guarded), seeds one `media_approvals`
row, and on approve dispatches exactly one asset per `(post, medium)`. Reconciliation
no longer produces video — it only re-pokes Stage-2 on drift. **One producer, one row,
one upload.**

## 8. Testing (docs + tests default)

- **PR1:** relocated `youtube_payload` helper tests; a `media_distribute` test proving a
  post with two `video` assets yields one dispatch (DISTINCT ON picks the canonical row).
- **PR2:** writer records `video` (not `video_long`); identity map + identity join; de-dup
  canonical selection across all three shapes (YT-id wins, pipeline wins, newest wins);
  migration test (keeps canonical, relabels, index holds, idempotent, backup populated,
  FK re-point); `record_media_asset` `ON CONFLICT` upsert; link-time skip-on-existing;
  reconciliation re-dispatch + attempt cap + drift finding intact + podcast untouched;
  vocabulary (`CANONICAL_MEDIA_NAMES`).
- Gates: `migrations_lint` + `migrations_smoke`; the `modules/content` CI guards
  (module-purity / silent-except ratchet / `services.md` regen) for any touched atom;
  the migration-adjacency tests under `tests/unit/services/migrations/` if a spec import
  ripples.

## 9. Verification & rollback (attended)

- **Verify in-process — never via prod dispatch.** Apply the migration to a DB copy and
  assert `video_long → 0` and `≤1 video-family row per post`; run `media.persist`
  in-process and assert a `video` row.
- **Attended cutover (operator):** merge PR2 → apply migration → restart workers
  (`poindexter-prefect-worker` registers the atom; `poindexter-worker` runs the jobs) →
  watch one `media_distribute` cycle, expecting **zero new uploads** (everything live is
  dispatched + grandfathered).
- **Rollback:** PR1 reverts trivially. PR2 code reverts cleanly; the row deletion is
  recoverable from `media_assets_dedup_backup`.

## 10. Open items to resolve during planning

- Exact `pipeline_distributions` FK behavior toward deleted `media_assets` rows (re-point
  vs archive).
- Name + default for the reconciliation re-dispatch attempt-cap setting; where the
  attempt counter lives (`pipeline_tasks` column vs task metadata key).
- Whether `routes/video_routes.py` still needs `video_long` transitionally (it should not
  post-relabel, but confirm no read runs before the migration in a mixed state).
