# Podcast Pipeline — Stage 3 (`podcast_pipeline`)

**Status:** Podcast lane (§3 DISPATCH → STAGE 3 → DISTRIBUTE → FEED) **implemented
2026-06-12**, dormant behind `podcast_pipeline_trigger_enabled` (default off). The
video-side consolidation (drop `video_long`, reconciliation re-dispatch, delete the
backfill jobs) is **deferred to a dedicated change — see §11** (it touches the live,
flag-on YouTube lane and carries a double-upload risk that warrants an attended cutover).
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

## 4. Components

### New files

| File                                           | Purpose                                                                                                                                                                                                                        |
| ---------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `services/podcast_pipeline_spec.py`            | `PODCAST_PIPELINE_GRAPH_DEF` (pure data, no heavy imports).                                                                                                                                                                    |
| `modules/content/atoms/podcast_load_script.py` | `podcast.load_script` — load `podcast_script` + intro path from `pipeline_versions.task_metadata` by `task_id`.                                                                                                                |
| `modules/content/atoms/podcast_render.py`      | `podcast.render` — TTS full read (via `PodcastService.synthesize`) + intro sting + **podcast CTA** outro → temp MP3 + `podcast_duration_s`.                                                                                    |
| `modules/content/atoms/podcast_persist.py`     | `podcast.persist` — durable move + `record_media_asset(type='podcast', task_id, post_id=None, duration_ms)`.                                                                                                                   |
| `services/jobs/dispatch_podcast_pipeline.py`   | `DispatchPodcastPipelineJob` — mirror of `DispatchMediaPipelineJob`, own `podcast_dispatched_at` claim, `thread_id=podcast-{task_id}`.                                                                                         |
| `services/jobs/podcast_distribute.py`          | `PodcastDistributeJob` — link → seed `record_pending` → R2 upload + feed rebuild on approval.                                                                                                                                  |
| migration `*_podcast_pipeline_stage3.py`       | **As shipped:** add `pipeline_tasks.podcast_dispatched_at`; seed `podcast_pipeline` graph_def. (The `video_dispatched_at` column + `video_long`→`video` backfill moved to §11 — unused/unsafe without the video-side cutover.) |

### Changed files

> **Legend:** ✅ shipped with the podcast lane (2026-06-12) · ⏸ deferred to §11
> (video-side cutover). Rows below are the original full design; the ⏸ rows did
> **not** ship in the podcast PR.

| File                                                     | Change                                                                                                                                                                                                                                           |
| -------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| ✅ `services/podcast_service.py`                         | Add a pure `synthesize(script, *, output_path=None, key="") -> (path, duration)` alongside `generate_episode` (reuse, don't duplicate TTS).                                                                                                      |
| ✅ `routes/podcast_routes.py`                            | `podcast_feed` reads `posts ⋈ media_assets(type='podcast') ⋈ media_approvals(podcast,approved)`; drop the `episodes_on_disk` scan; enclosure/length/duration from the asset row. Add `limit`/`offset` to `/episodes` (closes #746 podcast part). |
| ⏸ `services/media_pipeline_spec.py` / `media_persist.py` | Drop `video_long` → write `video` (+ `video_short`). (§11)                                                                                                                                                                                       |
| ⏸ `services/jobs/dispatch_media_pipeline.py`             | Already uses `media_pipeline_dispatched_at` (the de-facto video marker); no rename needed. (§11)                                                                                                                                                 |
| ⏸ `services/jobs/media_distribute.py`                    | Type map → `{video:video, video_short:video_short}`; join `mas.type = ma.medium`; de-dup to avoid double-send. (§11)                                                                                                                             |
| ⏸ `modules/content/atoms/` video render atoms            | Append `media.cta.video` outro as an end beat (own render path — base narration is shared). (§11)                                                                                                                                                |
| ⏸ `services/jobs/media_reconciliation.py`                | Replace `_regen_*`/`_record_media_asset` with per-medium **re-dispatch** (clear marker, capped attempts). Keep drift alert. (§11)                                                                                                                |
| ✅ `services/settings_defaults.py`                       | Seed `podcast_pipeline_trigger_enabled` (+ caps) and `media.cta.{podcast,video,video_short}` (podcast CTA live; video CTAs seeded ahead of their §11 reader).                                                                                    |
| ✅/⏸ `plugins/registry.py`                               | ✅ Register `dispatch_podcast_pipeline` + `podcast_distribute`. ⏸ **deregister** `backfill_podcasts` + `backfill_videos` (§11 — `media_distribute` still imports `backfill_videos` helpers).                                                     |

### Deletions

- ⏸ `services/jobs/backfill_podcasts.py`, `services/jobs/backfill_videos.py` (subsumed — closes #668). Deferred to §11: `media_distribute` imports `backfill_videos._build_youtube_description` / `_parse_seo_keywords`, so the helpers must be extracted first.
- ⏸ `video_long` strings (`media_asset_recorder.py`, etc. — #569 partial, closes #573). Deferred to §11.

## 5. Vocabulary

Target end-state: `media_assets.type ∈ {podcast, video, video_short}`;
`media_approvals.medium` identical. No `video_long`.

**Correction (2026-06-12, from prod data):** `video_long` is NOT 2 stray rows — it
is **10 linked rows, all with a sibling `video` row for the same post**, written by
a _second active producer_. Two producers + two distributors are keyed off the two
names today:

| Producer                   | writes `media_assets.type` | path key        | distributor                            |
| -------------------------- | -------------------------- | --------------- | -------------------------------------- |
| `media.persist` (pipeline) | `video_long`               | `{task_id}.mp4` | `media_distribute` (CASE → video_long) |
| `media_reconciliation`     | `video`                    | `{post_id}.mp4` | `backfill_videos` (disk scan)          |

`media_distribute`'s join `mas.type = CASE ma.medium WHEN 'video' THEN 'video_long'`
is a **routing key**: it makes that job deliver _only_ the pipeline render and skip
reconciliation's `video` rows. Collapsing both names to `video` (the rename) makes
the join match **both** rows per post → with `media_pipeline_trigger_enabled=true`
(live in prod) the next cycle can **double-upload the same video to YouTube**. So the
rename is inseparable from §11's reconciliation + backfill-deletion work, and the
existing 10 dup rows must be de-duplicated (not blindly relabeled). This is why the
video side is deferred — see §11.

## 6. CTAs (per-medium, DB-configurable)

`app_settings`: `media.cta.podcast` (default: "If you enjoyed this, rate and review the
show on Spotify or Apple Podcasts — it genuinely helps."), `media.cta.video` /
`media.cta.video_short` (default: "Like and subscribe for more."). Render atoms append
the medium's outro before TTS / as the end beat. ML-tunable later.

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
- **Deferred to §11 (video-side cutover):** #573 (`video_long` rows), #668 (delete
  backfill jobs), #569 (`video_long` strings).
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

## 11. Deferred — video-side consolidation (separate, attended change)

The video half of #689 is **intentionally not** in the podcast-lane change. Per §5, it
is a coordinated, all-or-nothing cutover on a **live, flag-on** lane with an
outward-facing double-upload risk, so it warrants its own focused (ideally attended)
PR rather than an unattended autonomous pass. It must land as one unit:

1. **Writer** — `media.persist` `_TARGETS`: write `video` (not `video_long`).
2. **Distributor** — `media_distribute`: `_TYPE_TO_MEDIUM` → identity
   `{video, video_short}`; join `mas.type = ma.medium`; and **de-dup** so a post
   never has two `video` assets feeding the approved-undispatched query (else
   double-send). Decide canonical between the reconciliation (`{post_id}.mp4`) and
   pipeline (`{task_id}.mp4`) renders.
3. **Reconciliation** — stop writing `video` assets directly; re-dispatch the video
   medium (clear `media_pipeline_dispatched_at`, capped attempts) so `media.persist`
   is the sole video producer. Keep the drift alert.
4. **Backfill** — delete `backfill_videos` (and `backfill_podcasts`), first extracting
   `_build_youtube_description` / `_parse_seo_keywords` (imported live by
   `media_distribute`) to a shared home.
5. **Data migration** — relabel/de-dup the 10 existing `video_long` rows → one `video`
   row per post.
6. **Video CTA** — append `media.cta.video` as an end beat; the video shares the base
   narration, so a _spoken_ CTA needs its own render path (don't reuse the
   podcast-CTA'd audio).
7. **`recorder` + `cli/posts.py` + reconciliation read-side** — drop the `video_long`
   strings (closes #573).
