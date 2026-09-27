# R2 media lifecycle

The media bucket (`storage_bucket`) holds pipeline-produced media: featured and
inline images (`images/featured/`, `images/inline/`), podcast audio
(`podcast/`), video (`video/`), plus the static export index (`static/`).

## Why orphans accumulate

Image object keys carry a fresh UUID per generation, so regenerating a post's
image writes a **new** object and leaves the old one behind. Deterministic keys
overwrite instead: podcast `podcast/{cdn_ver}/{post_id}.mp3`, long-form video
`video/{post_id}.mp4`, and `static/` JSON.

Long-form video reaches the bucket through `media_distribute`'s mirror pass
(`services/video_r2_mirror.py`): each video the RSS feed lists is uploaded to
`video/{post_id}.mp4` and its `media_assets.url` stamped, which also keeps the
object in the sweep's keep-set below. Shorts are not uploaded; they have no RSS
surface. See
[podcast-pipeline-stage3.md §11](podcast-pipeline-stage3.md#the-video-feeds-copy-in-the-bucket-shipped-2026-09-27--poindexter1085).

## Cleanup jobs

| Job                          | Scope                           | What it does                                                                                                           |
| ---------------------------- | ------------------------------- | ---------------------------------------------------------------------------------------------------------------------- |
| `static_export_orphan_sweep` | `static/` JSON                  | Deletes per-post JSON for de-published slugs.                                                                          |
| `media_orphan_sweep`         | `images/`, `video/`, `podcast/` | Deletes objects not referenced by any non-terminal post, `media_assets` row, or feed XML, older than the grace window. |

## `media_orphan_sweep` behaviour

- **Keep-set:** an object is kept if its key or basename appears in any
  non-terminal post (`content`, `featured_image_url`, `cover_image_url`,
  `featured_image_data`), any `media_assets` row (`url`, `storage_path`), or the
  `podcast/feed.xml` / `video/feed.xml` documents.
- **Dry-run first:** with `media_orphan_sweep_armed=false` (default) it reports
  what it would delete via a `media_orphan_sweep` finding and JobResult metrics,
  deleting nothing. Flip `media_orphan_sweep_armed=true` to arm it.
- **Safety:** a grace window (`media_orphan_sweep_grace_days`, default 14) skips
  freshly-uploaded objects; a per-run cap (`media_orphan_sweep_max_deletes_per_run`,
  default 500) bounds blast radius; an empty keep-set aborts the run; and the
  delete call site is guarded to the configured `media_orphan_sweep_prefixes`.
  If the feed XML can't be read, the sweep for that cycle narrows to `images/`
  only, since `video/`/`podcast/` references can live solely in the feed.

## Follow-up

The upstream fix — deleting the prior object when an image is regenerated, so
orphans stop being created — is tracked separately. The reaper is the safety net.
