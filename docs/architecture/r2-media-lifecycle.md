# R2 media lifecycle

The media bucket (`storage_bucket`) holds pipeline-produced media: featured and
inline images (`images/featured/`, `images/inline/`), podcast audio
(`podcast/`), video (`video/`), plus the static export index (`static/`).

## Why orphans accumulate

Image and video object keys carry a fresh UUID per generation, so regenerating
a post's image or video writes a **new** object and leaves the old one behind.
Deterministic keys (podcast `{post_id}` paths, `static/` JSON) overwrite instead.

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
