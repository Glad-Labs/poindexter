# Podcast Pipeline — Stage 3 (`podcast_pipeline`)

**Status:** Podcast lane (§3 DISPATCH → STAGE 3 → DISTRIBUTE → FEED) **implemented
2026-06-12**, dormant behind `podcast_pipeline_trigger_enabled` (default off). The
video-side consolidation (drop `video_long`, reconciliation re-dispatch, delete the
backfill jobs) **shipped 2026-06-17 as the attended two-PR change #1460 — see §11**
(closes #573, #668, #569).
**Epic:** [Glad-Labs/poindexter#689](https://github.com/Glad-Labs/poindexter/issues/689) (media_pipeline redesign).
**Deviation from #689:** the approved `video-pipeline-redesign.md` keeps podcast as a
parallel branch inside the single `media_pipeline` graph. This design **splits podcast
into its own isolated Stage-3 graph_def** (`podcast_pipeline`) with its own dispatcher
and distribute lane, for hard process-level isolation from the video render lane (a
video-render crash can never touch podcast production). Operator decision, 2026-06-11.

---

## 1. Problem (the regression this fixes)

Podcasts stopped appearing on Spotify ~2026-05-28. Root cause: the podcast RSS feed
gates episodes on `media_approvals(medium='podcast', status='approved')`, but the
current producer (`media_reconciliation` watchdog) writes `media_assets` rows **without
ever seeding `media_approvals`**. The legacy `backfill_podcasts` job that _did_ seed
approvals is dead-on-arrival — the watchdog drops the MP3 on local disk first, so
`PodcastService.episode_exists()` is true and backfill skips before calling
`record_pending`. Net: every podcast generated since 2026-05-28 sits in R2, invisible to
the feed. Video (YouTube) is stalled by the identical seam — `media_approvals` for
`video`/`video_short` is equally frozen at 2026-05-28.

`media_assets` is the canonical file registry (#161). The fix makes podcast production a
first-class atom pipeline that writes `media_assets` **and** seeds `media_approvals`,
and repoints the feed at `media_assets` so it stops depending on a fragile local-disk
scan.

## 2. Goal & principles

- **Composable graph_def, not cron.** Podcast render/QA/persist run as atoms in a
  `podcast_pipeline` graph_def under `TemplateRunner`, with `atom_runs` observability —
  mirroring `canonical_blog` (#355) and `media_pipeline` (#689).
- **Hard isolation from video.** Own graph, own dispatcher, own distribute job, own
  dispatch marker. Podcast ships to Spotify while videos are rejected/regenerated, and
  a video-graph failure cannot halt podcast production.
- **`media_assets` is the single source of truth.** Both production and the feed read
  it. No local-disk episode scan.
- **Human approval, per-asset.** Podcast seeds its own `media_approvals` row via
  `record_pending` (auto-approve tiers apply). Reject → regenerate in isolation.
- **DB-first config.** Per-medium CTA, voice pool, all tunables in `app_settings`.
- **Flip live.** No cutover flag; `media_reconciliation` stays as the re-dispatch net.

## 3. Stage-3 flow

```
STAGE 1 — canonical_blog (existing)
  produces & persists podcast_script (+ intro-sting path, #690) in
  pipeline_versions.task_metadata
          │  Gate 1 (blog approve → publish) — existing
          ▼
DISPATCH — dispatch_podcast_pipeline (NEW job, every 5 min)
  eligible = task status in (approved, published)
           AND persisted podcast_script
           AND podcast_dispatched_at IS NULL
  claim (stamp podcast_dispatched_at) → run podcast_pipeline
          ▼
STAGE 3 — podcast_pipeline (NEW graph_def)
  podcast.load_script → podcast.render → qa.audio → podcast.persist → END
          ▼
DISTRIBUTE — podcast_distribute (NEW job, every 10 min)
  1. link: resolve post via posts.metadata->>'pipeline_task_id', back-stamp media_assets.post_id
  2. seed: record_pending(medium='podcast') for any podcast asset lacking an approval  ← heals backlog
  3. deliver (on approved): ensure R2 upload at podcast/v2/{post_id}.mp3 → rebuild podcast/feed.xml on R2 → record_dispatched
          ▼
FEED — GET /podcast-feed.xml (Next.js) → R2 podcast/feed.xml
  worker /api/podcast/feed.xml now reads media_assets ⋈ media_approvals(approved)

SAFETY NET — media_reconciliation
  drift detected (published podcast-wanting post, no asset) → re-dispatch podcast_pipeline
  (clear podcast_dispatched_at, capped attempts); keep drift alert. No inline regen.
```

> **Interim self-healing seed (shipped 2026-06-14, ahead of §11).** Until the
> §11 re-dispatch refactor lands, `media_reconciliation._record_media_asset`
> seeds the per-medium `media_approvals` gate inline (via `_seed_approval_gate`
> → `record_pending`, idempotent + non-fatal) for **every** stamped podcast /
> video asset. This closes the freeze vector directly: the watchdog can no
> longer stamp a `media_assets` row without also entering the asset into the
> approval queue — regardless of whether `podcast_distribute` is dormant. It's
> the durable fix for the 2026-05-27→06-13 Spotify/Apple freeze
> (`feedback_approval_gate_all_media`).

## 4. Components

### New files

| File                                           | Purpose                                                                                                                                                                                                                                                                                       |
| ---------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `services/podcast_pipeline_spec.py`            | `PODCAST_PIPELINE_GRAPH_DEF` (pure data, no heavy imports).                                                                                                                                                                                                                                   |
| `modules/content/atoms/podcast_load_script.py` | `podcast.load_script` — load `podcast_script` + intro path from `pipeline_versions.task_metadata` by `task_id`.                                                                                                                                                                               |
| `modules/content/atoms/podcast_render.py`      | `podcast.render` — TTS full read (via `PodcastService.synthesize`) + intro sting + **podcast CTA** outro → temp MP3 + `podcast_duration_s`.                                                                                                                                                   |
| `modules/content/atoms/podcast_persist.py`     | `podcast.persist` — durable move + `record_media_asset(type='podcast', task_id, post_id=None, duration_ms)`.                                                                                                                                                                                  |
| `services/jobs/dispatch_podcast_pipeline.py`   | `DispatchPodcastPipelineJob` — mirror of `DispatchMediaPipelineJob`, own `podcast_dispatched_at` claim, `thread_id=podcast-{task_id}`.                                                                                                                                                        |
| `services/jobs/podcast_distribute.py`          | `PodcastDistributeJob` — link → seed `record_pending` → R2 upload + feed rebuild on approval.                                                                                                                                                                                                 |
| migration `*_podcast_pipeline_stage3.py`       | **As shipped:** add `pipeline_tasks.podcast_dispatched_at`; seed `podcast_pipeline` graph_def. (The `video_long`→`video` backfill was deferred to §11, which reuses the existing `media_pipeline_dispatched_at` marker rather than adding a separate `video_dispatched_at` column — see §11.) |

### Changed files

> **Legend:** ✅ shipped with the podcast lane (2026-06-12) · ✅ (#1460) shipped
> in the attended video-side cutover (2026-06-17, §11). The former ⏸ rows did not
> ship in the podcast PR but landed in #1460.

| File                                                  | Change                                                                                                                                                                                                                                           |
| ----------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| ✅ `services/podcast_service.py`                      | Add a pure `synthesize(script, *, output_path=None, key="") -> (path, duration)` alongside `generate_episode` (reuse, don't duplicate TTS).                                                                                                      |
| ✅ `routes/podcast_routes.py`                         | `podcast_feed` reads `posts ⋈ media_assets(type='podcast') ⋈ media_approvals(podcast,approved)`; drop the `episodes_on_disk` scan; enclosure/length/duration from the asset row. Add `limit`/`offset` to `/episodes` (closes #746 podcast part). |
| ✅ (#1460) `modules/content/atoms/media_persist.py`   | `_TARGETS` writes `video` (+ `video_short`), not `video_long`.                                                                                                                                                                                   |
| ✅ (#1460) `services/jobs/dispatch_media_pipeline.py` | Unchanged — already keys off `media_pipeline_dispatched_at`, which reconciliation now NULLs to re-dispatch.                                                                                                                                      |
| ✅ (#1460) `services/jobs/media_distribute.py`        | Identity `_TYPE_TO_MEDIUM`; join `mas.type = ma.medium`; `DISTINCT ON` dispatch de-dup (PR1) + a link-time guard that skips a second render for an already-covered post (`duplicate_video_asset` finding).                                       |
| ✅ `modules/content/atoms/media_render_narration.py`  | Each video lane renders its OWN narration audio (own script + `media.cta.video` / `media.cta.video_short` outro) via the shared `_narration_render` helper — no shared base narration. Shipped #689. (§6)                                        |
| ✅ (#1460) `services/jobs/media_reconciliation.py`    | Video drift re-dispatches Stage-2 (`_redispatch_video` clears the marker, capped by `media_pipeline_redispatch_count`); `_regen_video` removed. Podcast `_record_media_asset` + drift alert kept.                                                |
| ✅ `services/settings_defaults.py`                    | Seed `podcast_pipeline_trigger_enabled` (+ caps) and `media.cta.{podcast,video,video_short}`.                                                                                                                                                    |
| ✅ `plugins/registry.py`                              | Register `dispatch_podcast_pipeline` + `podcast_distribute` (2026-06-12). Deregister `backfill_podcasts` + `backfill_videos` from `_SAMPLES` + pyproject entry-points (#1460).                                                                   |

### Deletions

- ✅ (#1460) `services/jobs/backfill_podcasts.py` + `services/jobs/backfill_videos.py` deleted (closes #668). Their shared YouTube payload helpers (`_build_youtube_description` / `_parse_seo_keywords`) were extracted to `services/jobs/youtube_payload.py` in PR1 first.
- ✅ (#1460) type-valued `video_long` strings dropped (recorder mime map, `cli/posts.py`, `video_routes` feed query, reconciliation read-side; closes #573, #569). State channels `video_long_script` / `long_video_path` untouched.

### Feed rebuild on approval (shipped 2026-06-14)

Media is approved _after_ the post publishes, but R2 feed copies are rebuilt
only at publish time (`publish_service`), so an approval never propagated to
Apple/Spotify/the video feed until some _later_ publish — a second mechanism of
the 2026-05-27→06-13 freeze. Fixed: `media_approval_service.decide()` rebuilds
the matching R2 feed on approve (when `approved` and a `site_config` is passed),
via a shared `services/media_feed_rebuild.py` helper — `rebuild_feed_for_medium`
routes podcast → `podcast/feed.xml`, video → `video/feed.xml`, and `video_short`
→ no-op (shorts have no RSS surface). `podcast_distribute._rebuild_feed` now
delegates to the same helper (one rebuild seam, not two copies). Both the CLI
(`poindexter media approve`) and the HTTP route
(`POST /api/media-approval/{post_id}/{medium}/decide`) load + pass a
`site_config`. Non-fatal + idempotent — a rebuild failure never breaks the
already-committed approval.

## 5. Vocabulary

End-state (shipped #1460): `media_assets.type ∈ {podcast, video, video_short}`;
`media_approvals.medium` identical. No `video_long`.

**Correction (2026-06-12, from prod data):** `video_long` is NOT 2 stray rows — it
is **10 linked rows, all with a sibling `video` row for the same post**, written by
a _second active producer_. Two producers + two distributors are keyed off the two
names today:

| Producer                   | writes `media_assets.type` | path key        | distributor                            |
| -------------------------- | -------------------------- | --------------- | -------------------------------------- |
| `media.persist` (pipeline) | `video_long`               | `{task_id}.mp4` | `media_distribute` (CASE → video_long) |
| `media_reconciliation`     | `video`                    | `{post_id}.mp4` | `backfill_videos` (disk scan)          |

`media_distribute`'s old join `mas.type = CASE ma.medium WHEN 'video' THEN 'video_long'`
was a **routing key**: it made that job deliver _only_ the pipeline render and skip
reconciliation's `video` rows. Collapsing both names to `video` makes the join match
**both** rows per post → with `media_pipeline_trigger_enabled=true` (live in prod) a
naive rename could **double-upload the same video to YouTube**. So the rename shipped
as one atomic unit with the reconciliation re-dispatch + backfill deletion + a de-dup
migration (#1460, §11): the existing dup rows were collapsed to one smart-priority
survivor per post (platform-id > pipeline > newest), then relabeled, and a partial
unique index `uniq_media_assets_post_video_type` now prevents recurrence — see §11.

## 6. CTAs (per-medium, DB-configurable)

`app_settings`: `media.cta.podcast` (default: "If you enjoyed this, rate and review the
show on Spotify or Apple Podcasts — it genuinely helps."), `media.cta.video` /
`media.cta.video_short` (default: "Like and subscribe for more.").

Each medium renders its OWN narration audio from its OWN script with the medium's
CTA appended before TTS — there is **no shared base narration** (#689). The
podcast lane (`podcast.render`) and the two video lanes (`media.render_narration`,
which produces `long_narration_audio_path` + `short_narration_audio_path`) all
delegate to the shared `_narration_render.render_narration(script, cta_key, …)`
helper, so the CTA-append + TTS + fail-soft contract lives in one place.
ML-tunable later.

## 7. Reject → recreate

Clearing a medium's dispatch marker re-runs that medium's graph **fresh** (new render).
Reject → dump = status stays `rejected`, no re-dispatch (optional R2/asset purge).

## 8. Testing (docs + tests default)

- Unit: each new atom (`podcast.render` TTS-mocked → path+duration; `podcast.persist`
  records the row; `podcast.load_script` reads task_metadata), `dispatch_podcast_pipeline`
  claim/skip, `podcast_distribute` link/seed/deliver, feed route media_assets query +
  pagination, CTA append, reconciliation re-dispatch + attempt cap.
- Integration: `podcast_pipeline` end-to-end emits a podcast asset → distribute seeds →
  tier-1 auto-approve → feed lists it (sourced from media_assets).
- Migration smoke + lint (imports stay light).

## 9. Issues

- **Closes with the podcast PR:** #746 (podcast `/episodes` pagination part).
- **Closed by the §11 video-side cutover (#1460):** #573 (`video_long` rows), #668
  (delete backfill jobs), #569 (`video_long` strings).
- **References (deferred):** #685/#686/#687/#688/#669/#1193/#531/#449/#1343.
- New tracking issue under #689 at PR time (code → `glad-labs-stack`).

## 10. One-time backlog heal

**Prod data (2026-06-12):** 78 podcast `media_assets`, all post-linked, **16 with no
`media_approvals` row** — the stuck episodes. `podcast_distribute` Pass 2 calls
`record_pending('podcast')` for each on the first cycle after deploy, so the backlog
heals automatically (no separate data migration). The 59 already-approved episodes
surface immediately via the media_assets-sourced feed (§4 `routes/podcast_routes.py`),
which no longer requires the MP3 to be present on the worker's local disk — the actual
mechanism of the 2026-05-28 Spotify freeze.

## 11. Video-side consolidation (shipped 2026-06-17 — #1460)

The video half of #689 landed as a focused, **attended** two-PR change rather than an
unattended autonomous pass, because it was a coordinated all-or-nothing cutover on a
**live, flag-on** lane with an outward-facing double-upload risk (per §5):

- **PR1 (inert prep, mergeable anytime):** extract the YouTube payload helpers to
  `services/jobs/youtube_payload.py`; add `DISTINCT ON (post_id, medium)` to
  `media_distribute`'s approved-undispatched query (canonical priority: platform id
  > pipeline source > newest) so a post never double-dispatches.
- **PR2 (atomic cutover):** the seven pieces below, landing as one unit. Closes
  #573, #668, #569.

The root-cause fix is a de-dup migration (`*_dedup_and_collapse_video_long.py`) that
keeps one video-family survivor per post by smart priority, relabels
`video_long`→`video`, and adds the partial unique index
`uniq_media_assets_post_video_type` (backup → `media_assets_dedup_backup`; FK-safe);
all producers are conflict-aware so the one-video-per-post invariant holds going forward.

1. ✅ **Writer** — `media.persist` `_TARGETS` writes `video` (not `video_long`).
2. ✅ **Distributor** — `media_distribute`: identity `_TYPE_TO_MEDIUM`
   (`{video, video_short}`); join `mas.type = ma.medium`; `DISTINCT ON` dispatch
   de-dup (PR1) + a link-time guard that leaves a redundant render unlinked and emits
   a `duplicate_video_asset` finding when the post already holds that video type.
3. ✅ **Reconciliation** — stops writing `video` assets directly; on video drift it
   re-dispatches Stage-2 (`_redispatch_video` NULLs `media_pipeline_dispatched_at`,
   capped by `media_pipeline_redispatch_count`) so `media.persist` is the sole video
   producer. Drift alert retained; no-`task_id` posts surface in the finding
   (fail-loud), not silently healed.
4. ✅ **Backfill** — `backfill_videos` + `backfill_podcasts` deleted (closes #668);
   `_build_youtube_description` / `_parse_seo_keywords` extracted to `youtube_payload`
   (PR1) first. Deregistered from `_SAMPLES` + pyproject entry-points.
5. ✅ **Data migration** — `*_dedup_and_collapse_video_long.py` relabels/de-dups the
   existing `video_long` rows → one `video` row per post + the unique guard.
6. ✅ **Video CTA** — shipped (#689). Each video lane renders its OWN narration
   from its OWN script with its OWN spoken CTA (`media.cta.video` /
   `media.cta.video_short`) via `media.render_narration` → the shared
   `_narration_render` helper. (The earlier "video shares the base podcast
   narration" plan is superseded — Stage-2 never carried the podcast audio
   across, which is what left every rendered video silent.)
7. ✅ **`recorder` + `cli/posts.py` + `video_routes` + reconciliation read-side** —
   dropped the type-valued `video_long` strings (closes #573, #569). State-channel
   names (`video_long_script`, `long_video_path`) deliberately untouched.

> **Video-feed approval gate + grandfather (shipped 2026-06-14, ahead of the
> full §11 cutover).** `routes/video_routes.py::video_feed` now mirrors the
> podcast feed — sourced from `media_assets` and gated on
> `media_approvals(medium='video', status='approved')` plus the
> `media_to_generate` niche-policy seam — so un-reviewed video never reaches
> the public RSS feed (`feedback_approval_gate_all_media`). To avoid the
> footgun of freezing the already-live videos when the gate flips, a one-shot
> migration (`*_grandfather_video_media_approvals_for_already_live_videos.py`)
> inserts `status='approved'` rows (`decided_by='auto:grandfather'`) for every
> published post that already has a `video`/`video_long` asset but no `video`
> approval row. It's INSERT-where-absent (`NOT EXISTS` + `ON CONFLICT`), so
> genuine `pending`/`rejected` decisions are untouched and freshly-generated
> un-reviewed video correctly stays off the feed until approved. This gates the
> read-side surface independently of the §11 `video_long`→`video` rename +
> distributor re-dispatch cutover above.
