# R2 Media Orphan-Reaper — Design

**Date:** 2026-07-11
**Status:** Approved (design), pending implementation plan
**Branch:** `claude/cloudflare-storage-bloat-240bf6`
**Related:** memory `project_cloudflare_r2_media_bloat_2026_07`; precedent
`services/jobs/static_export_orphan_sweep.py`

## Problem

The Cloudflare R2 bucket `gladlabs-media` measured **4,685 objects / 9.6 GB on
2026-07-11 — 96% of R2's 10 GB free-tier ceiling** (a manual reclaim this session
took it to ~9.3 GB). Roughly two-thirds of the bucket is orphaned regeneration
media:

| Prefix                   |     R2 objects | Referenced by posts |       Orphan |
| ------------------------ | -------------: | ------------------: | -----------: |
| `images/inline`          | 3,272 (4.2 GB) |                 144 |         ~96% |
| `images/featured`        | 1,047 (1.4 GB) |                 137 |         ~87% |
| `video/`                 |   ~84 (3.4 GB) |            ~60 live | ~20+ objects |
| `podcast/` (legacy root) |          small |               mixed |         some |

**Root cause:** image and video R2 keys carry a fresh UUID per (re)generation
(`images/featured/{id8}-{uuid8}.jpg`, `images/inline/{uuid12}.png`,
`video/{uuid}.mp4`), so every regeneration orphans the prior object — and **no
cleanup path exists**: all `retention_policies` target DB tables, only
`static_export_orphan_sweep` touches R2 (and only `static/` JSON), and
`media_reconciliation` only _adds_ rows. Inline images are never recorded in
`media_assets` (0 `inline_image` rows), so "live" for them can only be derived by
scanning post bodies.

## Goals / Non-goals

**Goals:** a bounded, dry-run-first scheduled job that safely deletes
unreferenced objects under `images/`, `video/`, and legacy `podcast/`, keeping the
bucket well under the free tier without operator hand-work.

**Non-goals (this spec):** deleting the prior object at regeneration time
(upstream source fix — separate follow-up); `static/` (already swept); `backups/`
(handled manually 2026-07-11); recording inline images in `media_assets`.

## Approach: standalone job (not a retention handler)

The `retention_policies` handler framework is DB-table/TTL-shaped (`table_name`,
`age_column`, `ttl_days`, `filter_sql`) and cannot express object-storage
reference-diffing. `static_export_orphan_sweep` is a standalone job for exactly
this reason and is the proven pattern. New job:
`services/jobs/media_orphan_sweep.py`, registered in `plugins/registry.py`
`_SAMPLES`, config namespace `plugin.job.media_orphan_sweep`.

## The keep-set (correctness core)

An object key is **KEPT** iff its full key OR its basename appears in the union of
these reference sources. Everything else under the swept prefixes is an orphan
candidate.

1. **Non-terminal posts** — `status NOT IN ('rejected','deleted','archived')`
   (published + drafts + awaiting_approval, so in-pipeline work survives). Scan:
   `content`, `featured_image_url`, `cover_image_url`, and `featured_image_data`
   (jsonb, stringified).
2. **`media_assets`** — `url` + `storage_path` (all rows).
3. **R2 feed XML** — fetch `podcast/feed.xml` and `video/feed.xml` object text
   and include verbatim (covers channel art like `podcast/cover.jpg` and episode
   enclosures — proven necessary: `cover.jpg` is referenced _only_ here).

**Matching:** normalize by comparing both the full object key
(`images/inline/abc.webp`) and the basename (`abc.webp`) against a single
concatenated haystack string, so references survive domain variance (the custom
image domain vs the `*.r2.dev` bucket URL) and extension rewrites.

## Scope (swept prefixes)

`images/`, `video/`, `podcast/`. NOT `static/` (own sweep), `backups/`, `sdxl/`,
`diagnostics/`, `smoke-tests/`, `test/`. Current `podcast/v2/*` and post-keyed
videos are kept automatically because they are referenced by `media_assets`/feed.

## Safety mechanisms (all four required)

1. **Grace window** — `grace_days` (default 14). Skip any object whose
   `LastModified` is newer than N days. Protects just-uploaded objects not yet
   linked to a post row/body (in-flight generation).
2. **Per-run cap** — `max_deletes_per_run` (default 500). Bounds blast radius per
   cycle; the truncated remainder is logged, never silently dropped (matches
   `static_export_orphan_sweep`).
3. **Circuit breaker** — if the keep-set is empty, or any keep-set source query
   (posts / media_assets) _failed_, abort the run without deleting. Guards against
   a bad/empty query mass-deleting the bucket. (Feed-fetch failure is non-fatal
   but downgrades to keep-more: on feed fetch error, treat as "cannot verify
   podcast/video" and skip those two prefixes that cycle.)
4. **Hard prefix guard** — the delete call site can only ever target keys whose
   prefix is in the configured swept-prefix allowlist; anything else is skipped
   even if selected upstream.

## Rollout (dry-run first, then auto-arm)

- **Phase 1 — dry-run** (`media_orphan_sweep_armed=false`, the default). Job runs
  on schedule, computes orphans + bytes, emits a `media_orphan_sweep` finding and
  JobResult metrics ("would reclaim X GB / N objects, by prefix"), deletes
  nothing. Operator reviews one cycle.
- **Phase 2 — armed** (`media_orphan_sweep_armed=true`). Job deletes, bounded by
  grace + cap. The ~4k-object backlog clears within a few capped cycles, or via a
  single supervised high-cap pass, then the daily run maintains steady state.
  (Deletes are R2 Class A ops; 4k is trivially within the 1M/mo free tier.)

## Observability

- **Finding** each run (`utils.findings.emit_finding`, kind `media_orphan_sweep`,
  dedup_key stable): dry-run → severity info "would reclaim…"; armed → "reclaimed…".
- **JobResult metrics**: `scanned`, `kept_refs`, `orphans_found`,
  `deleted` (or `would_delete`), `bytes_reclaimed`, per-prefix breakdown,
  `capped_remainder`.
- **Grafana** (feedback_grafana_everything): a panel sourcing bucket total /
  orphan bytes from the job's emitted metrics (finding `extra` or a small stats
  row). Board: Cost & Analytics or Database.

## Config (DB-first, seeded in `settings_defaults.py`)

| Key                                      | Default                   | Meaning                        |
| ---------------------------------------- | ------------------------- | ------------------------------ |
| `media_orphan_sweep_armed`               | `false`                   | dry-run → armed switch         |
| `media_orphan_sweep_grace_days`          | `14`                      | skip objects newer than N days |
| `media_orphan_sweep_max_deletes_per_run` | `500`                     | per-cycle blast-radius cap     |
| `media_orphan_sweep_prefixes`            | `images/,video/,podcast/` | swept prefixes                 |
| schedule                                 | daily                     | via plugin job config          |

## Reused / new seams on `R2UploadService`

- **Reuse** `delete_object(key)` and `_s3_client_and_bucket()` construction.
- **New** `async def list_objects(prefix) -> list[dict]` returning
  `{key, size, last_modified}` (paginated) — `list_keys` returns keys only, but
  the grace window needs `LastModified` and metrics need `Size`.
- **New** `async def get_object_text(key) -> str | None` — fetch feed XML
  (`get_json` only parses JSON).

These are public methods so the job never reaches into internals.

## Testing (unit, model on `test_static_export_orphan_sweep_job.py`)

- keep-set extraction: body regex, feed XML parse, `media_assets`, jsonb column,
  basename vs full-key matching, domain variance.
- grace-window filter (object newer than N days is kept).
- per-run cap truncation + remainder logged.
- circuit breaker: empty keep-set → no deletes; source-query failure → abort;
  feed-fetch failure → podcast/video skipped that cycle.
- dry-run vs armed: `delete_object` called iff armed.
- prefix guard: a selected key outside the allowlist is never deleted.
- Mock S3 (list/delete/get) + fake asyncpg pool.

## Follow-up (separate PR, out of scope here)

Delete-on-regenerate at the source: when `source_featured_image` /
`post_edit_service` replaces an image, delete the prior R2 object so orphans stop
being created. The reaper is the safety net; this is the upstream fix.
