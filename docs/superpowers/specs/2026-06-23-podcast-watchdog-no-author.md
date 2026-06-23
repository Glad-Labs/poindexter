# Podcast Watchdog: Detect + Gated Re-dispatch, Never Author

**Date:** 2026-06-23
**Status:** Approved (design)
**Author:** Claude (with operator)

## Problem

Every published post that wants a podcast ends up with **two** `media_assets`
podcast rows from two independent producers that cannot see each other:

1. **Normal pipeline** (`dispatch_podcast_pipeline` → `podcast_pipeline` →
   `podcast.persist`): renders `{task_id}.mp3` **locally**, records a row with
   `post_id=None` (back-stamped later), `task_id` set, `storage_provider='local'`,
   `url=NULL`. It is **not** uploaded to R2 until an operator approves Gate-2 and
   `podcast_distribute` Pass 3 ships it to `podcast/{cdn_ver}/{post_id}.mp3`.
2. **`media_reconciliation`** decides "podcast missing" with an **R2 HEAD** on
   that post-keyed path (`_check_post_media`: `podcast_missing = wants_podcast and
not podcast_exists`). Because the normal podcast is local-and-pending (not on
   R2 yet), the HEAD 404s, so reconciliation **generates a second podcast from
   scratch** (`_regen_podcast` → `generate_podcast_episode` +
   `upload_podcast_episode`) and uploads it **straight to R2, bypassing Gate-2**.

### Evidence (prod, 2026-06-23)

- Post `003fff16`: two podcast rows — `a98b54e8` (`storage_provider='local'`,
  `url=NULL`, correctly pending Gate-2) and `4fd76522` (`task_id=NULL`,
  `storage_provider='cloudflare_r2'`, on R2 — Gate-2 bypassed).
- Global: **69** podcast assets on R2 via reconciliation (task-NULL) vs **4** that
  went through the proper approve→deliver path; 25 normal podcasts sit local,
  never shipped. The podcast Gate-2 gate is a no-op in practice.
- The live `podcast/feed.xml` is **deduped** (65 items, 0 duplicate titles/guids),
  so subscribers see no duplicates — the damage is duplicate DB rows, a doubled
  TTS render per post, and the Gate-2 bypass.

### Why the earlier fix (#32) did not stop it

#32 added `_promote_existing_podcast` ("promote an on-disk render instead of
re-rendering"), but that only helps when a render is already present to find. It
never corrected the wrong _missing_ signal (R2 HEAD), so reconciliation still
fires and still double-produces whenever the normal podcast hasn't been delivered.

### Root cause (one line)

Reconciliation answers an **existence** question ("does a podcast exist for this
post?") with a **delivery** check ("is the post-keyed R2 file present?"). The two
states differ for every pre-approval podcast, so reconciliation treats
rendered-but-pending podcasts as missing and authors duplicates outside the gate.
The video lane never had this bug because it checks the **DB row**
(`(post_id,'video') in existing_pairs`) and **re-dispatches** the gated pipeline
rather than producing video directly.

## Decisions

1. **Podcasts are Gate-2 gated** (operator decision). The `podcast_pipeline` is
   the sole producer; nothing ships to the feed without approval.
2. **The watchdog detects and gated-retries; it never authors content.** A
   watchdog may re-run the gated pipeline (retry) or re-upload an already-approved
   artifact (redeliver). It may never generate new content. (Same class of bug as
   the disabled `regenerate_stock_images` job, which overrode approved images.)

## Design

### Podcast watchdog state machine

For each published post whose `media_to_generate` includes `podcast`, key the
decision on its podcast `media_assets` row — **not** an R2 HEAD:

| Row state                                                        | Action                                                                                                                                                                                                                                                                                                                                            |
| ---------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **No podcast row**                                               | Genuine miss. Resolve the source task (`posts.metadata->>'pipeline_task_id'`); clear `podcast_dispatched_at` (capped by `podcast_redispatch_count < podcast_redispatch_max`) so `dispatch_podcast_pipeline` re-renders through QA + Gate-2. Emit `podcast_missing` finding. No task seam → finding only (fail-loud, not healed), mirroring video. |
| **Row, `storage_provider='local'`** (rendered, pending approval) | **No action.** Correctly waiting in the Gate-2 queue. _(this is the dedup)_                                                                                                                                                                                                                                                                       |
| **Row, `storage_provider='cloudflare_r2'`, R2 HEAD 404**         | Delivered then lost. Re-deliver: re-upload the durable local `{task_id}.mp3` to the same key via `R2UploadService.upload_to_r2`. If the local file is also gone, fall back to re-dispatch. Emit finding.                                                                                                                                          |
| **Row, on R2, file present**                                     | Healthy. No action.                                                                                                                                                                                                                                                                                                                               |

`storage_provider` already encodes delivery state, so no `media_approvals` join is
needed. The R2 HEAD is retained **only** for its legitimate purpose — delivery
integrity of episodes that were already delivered.

### Deletions

- `_regen_podcast`'s direct `generate_podcast_episode` + `upload_podcast_episode`
  path — the gate-bypassing author.
- `_promote_existing_podcast` — obsolete once the only miss-remedy is re-dispatch.

The podcast row-stamp pass (Pass 1) is **kept** — it is harmless (a no-op once
post-keyed R2 podcasts all have rows) and orthogonal to the dedup fix. Removing it
is out of scope here.

### Schema + settings

- New column `pipeline_tasks.podcast_redispatch_count INTEGER NOT NULL DEFAULT 0`
  (mirrors `media_pipeline_redispatch_count`), via a timestamped migration.
- New default `podcast_redispatch_max` (default `"3"`) in `settings_defaults.py`
  (the per-task re-dispatch cap). Not a migration — seeded every boot.

### Video lane

Unchanged. It already detects via the DB row and re-dispatches the gated pipeline;
it never regenerates. This change makes podcast symmetric with it.

### Existing data

- **Grandfather** the 65 live feed episodes (the post-keyed R2 files). Removing
  them would drop episodes and re-trigger Apple/Spotify re-ingest (platforms cache
  by URL). Leave them serving the feed.
- The ~25 redundant local-pending rows are harmless cruft. Optional later sweep;
  **no migration** in this change.

## Testing

- Unit tests extending `tests/unit/services/jobs/test_media_reconciliation_job.py`,
  one per state-machine branch:
  - no row → clears `podcast_dispatched_at`, bumps count, respects cap, never
    calls `generate_podcast_episode`.
  - local/pending row → no action (no re-dispatch, no regen).
  - R2 row + HEAD 404 → re-delivers via `upload_to_r2` (no re-render); local-gone
    → falls back to re-dispatch.
  - R2 row + HEAD 200 → no action.
- A regression test asserting reconciliation **never** imports/calls
  `generate_podcast_episode` on any podcast path.
- Migration: `python scripts/ci/migrations_smoke.py` (fresh-DB) green; the new
  column present and defaulted.

## Out of scope

- Backfill/cleanup of the 25 redundant local rows (separate optional sweep).
- Re-pushing the 65 grandfathered episodes through Gate-2 (they stay as-is).
- Any change to the video lane or to `podcast_distribute` / `podcast_pipeline`
  themselves — this change is confined to `media_reconciliation` + the one column.
